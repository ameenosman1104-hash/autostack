"""Isolated, review-only scanner trial. Never import the production startup wrapper.

python scripts/local_invoice_trial.py --setup-only
python scripts/local_invoice_trial.py --serve
"""
import argparse
import base64
import copy
import getpass
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time
from io import BytesIO

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
DEFAULT_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home()/"AppData/Local"))/"AutoStack"/"InvoiceTrial"
TENANT_ID = 10
USERNAME = "invoice_tester"
PASSWORD = "Local-Scanner-Only-2026!"
MODEL = "gpt-5.4-nano"
MAX_CALLS = 5
MAX_INPUT = 20000
MAX_OUTPUT = 6000
RESERVATION_USD = 0.02
BUDGET_USD = 0.10


class TrialError(Exception):
    """Messages must contain no provider response, credentials, or invoice content."""


def local_configuration():
    """Read only test configuration. Never read .env.production or existing tenant data."""
    allowed={"OPENAI_API_KEY","AUTOSTACK_INVOICE_MODEL","OPENAI_INVOICE_MODEL","OPENAI_MODEL"}
    config={}
    path=PROJECT/".env"
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line=raw.strip().removeprefix("export ")
            if not line or line.startswith("#") or "=" not in line:continue
            key,value=line.split("=",1)
            if key.strip() not in allowed:continue
            value=value.strip()
            config[key.strip()]=value[1:-1] if len(value)>1 and value[0]==value[-1] and value[0] in (chr(34),chr(39)) else value.split(" #",1)[0].strip()
    key=os.environ.get("OPENAI_API_KEY") or config.get("OPENAI_API_KEY")
    model=os.environ.get("OPENAI_INVOICE_MODEL") or config.get("OPENAI_INVOICE_MODEL") or os.environ.get("AUTOSTACK_INVOICE_MODEL") or config.get("AUTOSTACK_INVOICE_MODEL") or os.environ.get("OPENAI_MODEL") or config.get("OPENAI_MODEL") or MODEL
    # Allow gpt-4.1, gpt-4.1-mini, gpt-5.4-nano for isolated testing
    if not model or not (model in ("gpt-4.1","gpt-4.1-mini","gpt-5.4-nano") or model.endswith(("-nano","-mini"))):
        raise TrialError(f"This trial supports structured extraction models only. Requested: {model}")
    return key,model


def configure_isolation(root,model=None):
    root=Path(root).resolve()
    if root==Path(root.anchor) or root==PROJECT or root.is_relative_to(PROJECT) or PROJECT.is_relative_to(root):
        raise TrialError("Test data must be in a separate directory outside the project")
    marker=root/".invoice-trial"
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise TrialError("Refusing to use an existing directory without the isolated-trial marker")
    root.mkdir(parents=True,exist_ok=True)
    marker.write_text("AutoStack isolated invoice trial v1",encoding="utf-8")
    # No production startup restoration, inherited data roots, integrations, or external sync.
    for key in ("RENDER","AUTOSTACK_INVOICE_MOUNT","SECRET_KEY","OPENAI_LOG","OPENAI_BASE_URL","OPENAI_ORG_ID","OPENAI_PROJECT_ID"):
        os.environ.pop(key,None)
    os.environ.update(AUTOSTACK_DATA_DIR=str(root),AUTOSTACK_ENV="development",
                      AUTOSTACK_INVOICE_DIR=str(root/"invoices"),AUTOSTACK_INVOICE_MODEL=model or MODEL)
    logging.disable(logging.CRITICAL)
    return root


class Budget:
    """Persist attempts before network I/O; restarts never reset this app-local allowance."""
    def __init__(self,root):
        self.path=Path(root)/"trial_usage.db"
        self.lock=threading.Lock()
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY, started REAL NOT NULL, outcome TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, estimated_usd REAL)""")

    def reserve(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            count,last=conn.execute("SELECT COUNT(*),MAX(started) FROM attempts").fetchone()
            if count>=MAX_CALLS or (count+1)*RESERVATION_USD>BUDGET_USD+0.0000001:
                raise TrialError("Local trial usage allowance exhausted. No AI request was sent.")
            if last and time.time()-last<10:
                raise TrialError("Please wait 10 seconds between AI attempts")
            return conn.execute("INSERT INTO attempts(started,outcome) VALUES(?,'reserved')",(time.time(),)).lastrowid

    def finish(self,attempt,outcome,inputs=None,outputs=None):
        estimate=(inputs*0.20+outputs*1.25)/1000000 if inputs is not None and outputs is not None else None
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE attempts SET outcome=?,input_tokens=?,output_tokens=?,estimated_usd=? WHERE id=?",
                         (outcome,inputs,outputs,estimate,attempt))

    def summary(self):
        with sqlite3.connect(self.path) as conn:
            used=conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        return {"attempts_used":used,"attempt_limit":MAX_CALLS,"reserved_usd":round(used*RESERVATION_USD,2),"application_budget_usd":BUDGET_USD}


def strict_schema(value):
    """Count the same strict JSON schema used by the SDK's structured parser."""
    if isinstance(value,list):return [strict_schema(item) for item in value]
    if not isinstance(value,dict):return value
    result={key:strict_schema(item) for key,item in value.items() if key!="default"}
    if result.get("type")=="object":
        result["additionalProperties"]=False
        result["required"]=list(result.get("properties",{}))
    return result


def install_bounded_client(extraction,key,budget,model=MODEL,client_factory=None):
    import openai
    real_factory=client_factory or openai.OpenAI
    class Responses:
        def __init__(self,raw):self.raw=raw
        def parse(self,**kwargs):
            if kwargs.get("model")!=model or kwargs.get("store") is not False or kwargs.get("max_output_tokens")!=MAX_OUTPUT:
                raise TrialError(f"Unexpected AI request configuration; nothing was sent (model: {kwargs.get('model')} vs {model})")
            if not key:raise TrialError("Enter a local test API key before extraction")
            if not budget.lock.acquire(blocking=False):raise TrialError("An AI request is already running")
            attempt=None
            try:
                attempt=budget.reserve()
                # Limit real PDF trials to five pages before either token counting or generation.
                for message in kwargs.get("input",[]):
                    for part in message.get("content",[]):
                        if part.get("type")=="input_file":
                            from pypdf import PdfReader
                            content=base64.b64decode(part["file_data"].split(",",1)[1],validate=True)
                            if len(PdfReader(BytesIO(content)).pages)>5:
                                raise TrialError("Use at most five PDF pages in this controlled trial")
                schema=kwargs["text_format"]
                counted=self.raw.responses.input_tokens.count(model=model,input=kwargs["input"],
                    instructions=kwargs.get("instructions"),
                    text={"format":{"type":"json_schema","name":schema.__name__,"strict":True,"schema":strict_schema(schema.model_json_schema())}})
                if type(counted.input_tokens) is not int or counted.input_tokens<0 or counted.input_tokens+1024>MAX_INPUT:
                    raise TrialError("This invoice exceeds the local input-token allowance. Use fewer pages or a smaller image.")
                result=self.raw.responses.parse(**kwargs)
                usage=result.usage
                budget.finish(attempt,"completed",usage.input_tokens,usage.output_tokens)
                return result
            except TrialError:
                if attempt is not None:budget.finish(attempt,"blocked")
                raise
            except Exception:
                if attempt is not None:budget.finish(attempt,"failed")
                raise TrialError("The AI request failed. No provider response or invoice contents were logged. Check the test key, account access, billing, and model support.") from None
            finally:
                budget.lock.release()
    class Client:
        def __init__(self,*args,**kwargs):
            if not key:raise TrialError("No local test API key configured")
            # Only the official OpenAI endpoint; disable inherited proxy configuration.
            self.raw=real_factory(api_key=key,base_url="https://api.openai.com/v1",timeout=60,max_retries=0,
                                  http_client=openai.DefaultHttpxClient(trust_env=False))
            self.responses=Responses(self.raw)
        def close(self):self.raw.close()
    extraction.openai.OpenAI=Client


def prepare_app(root,key=None,model=None,client_factory=None):
    root=configure_isolation(root,model)
    import app.main_db as main_db
    main_db.MAIN_DB=str(root/"main.db")
    from app.services import auto_sync
    auto_sync.start_background_sync=lambda app:None
    from app import create_app
    app=create_app()
    app.config.update(TESTING=True,DEBUG=False,PROPAGATE_EXCEPTIONS=False,WTF_CSRF_ENABLED=True)
    app.jinja_env.auto_reload=True
    # Allow the existing development storage validator, without changing production code.
    app.logger.disabled=True
    from werkzeug.security import generate_password_hash
    conn=main_db.get_conn()
    conn.execute("UPDATE tenants SET is_active=0 WHERE is_admin=1")
    conn.execute("""INSERT OR IGNORE INTO tenants(id,business_name,username,password_hash)
        VALUES(10,'ISOLATED INVOICE TEST - SAMPLE STOCK ONLY',?,?)""",(USERNAME,generate_password_hash(PASSWORD)))
    conn.commit();conn.close()
    from app.tenant_db import get_conn
    conn=get_conn(TENANT_ID)
    for code,name,qty,cost,sell,brand,size,pattern in (
        ("DEMO-2055516","Demo Touring 205/55R16",10,700,1000,"DEMO","205/55R16","Touring"),
        ("DEMO-1956515","Demo Eco 195/65R15",8,600,900,"DEMO","195/65R15","Eco"),
        ("DEMO-2254517","Demo Sport 225/45R17",6,900,1350,"DEMO","225/45R17","Sport"),
        ("DEMO-2656517","Demo All Terrain 265/65R17",4,1500,2100,"DEMO","265/65R17","All Terrain"),
    ):
        conn.execute("""INSERT OR IGNORE INTO products(code,name,category,unit,current_stock,last_cost_price,
            previous_cost_price,selling_price,brand,tyre_size,pattern,condition,extra_data)
            VALUES(?,?,'Tyres','PCS',?,?,?,?,?,?,?,'new',?)""",
            (code,name,qty,cost,cost,sell,brand,size,pattern,json.dumps({"brand":brand,"tyre_size":size,"pattern":pattern})))
    conn.commit();conn.close()
    budget=Budget(root)
    from app.services import supplier_invoice_extraction as extraction
    install_bounded_client(extraction,key,budget,model,client_factory)
    app.config["TRIAL_API_READY"]=False
    from flask import request,jsonify
    from flask_login import current_user
    @app.before_request
    def extraction_only_trial():
        endpoint=request.endpoint or ""
        if endpoint=="static":return None
        if endpoint in {"auth.login","auth.logout","sw","manifest"}:return None
        if current_user.is_authenticated and current_user.tenant_id!=TENANT_ID:
            return jsonify(error="This trial permits only the isolated test tenant"),403
        if endpoint=="supplier_invoices.confirm_supplier_invoice":
            return jsonify(error="Stock confirmation is disabled during the extraction-only trial"),403
        if endpoint.startswith("supplier_invoices."):
            if request.method=="GET":return None
            if endpoint not in {"supplier_invoices.upload_invoice","supplier_invoices.extract_invoice","supplier_invoices.save_review_draft"}:
                return jsonify(error="Action disabled in the isolated trial"),403
            if endpoint=="supplier_invoices.extract_invoice" and not app.config["TRIAL_API_READY"]:
                return jsonify(error="Synthetic API preflight has not passed. Configure a local test key first."),503
            return None
        if endpoint in {"dashboard.index","inventory.index"} and request.method=="GET":return None
        return jsonify(error="Unrelated actions and integrations are disabled in this local trial"),403
    @app.after_request
    def trial_notice(response):
        if response.mimetype=="text/html" and not response.direct_passthrough:
            text=response.get_data(as_text=True)
            start=text.find("<body")
            end=text.find(">",start) if start>=0 else -1
            if end>=0:
                state="API preflight passed" if app.config["TRIAL_API_READY"] else "API preflight pending - no live extraction verified"
                banner='<div role="status" style="position:relative;z-index:9999;background:#403400;color:#fff;padding:12px;text-align:center">ISOLATED LOCAL TEST | '+state+' | Stock confirmation disabled | Extraction sends the selected document to OpenAI; document contents are not logged.</div>'
                response.set_data(text[:end+1]+banner+text[end+1:])
        return response
    return app,budget


def synthetic_preflight(extraction,root,key):
    """Two actual API calls using only generated content. Never a genuine supplier invoice."""
    from PIL import Image,ImageDraw
    key_hash=hashlib.sha256(key.encode()).hexdigest()
    report_path=Path(root)/"api_preflight.json"
    if report_path.exists():
        report=json.loads(report_path.read_text())
        if report.get("passed") and report.get("model")==MODEL and report.get("key_sha256")==key_hash:
            return {key:value for key,value in report.items() if key!="key_sha256"}
    image=Image.new("RGB",(1000,700),"white")
    ImageDraw.Draw(image).multiline_text((35,35),
        "SYNTHETIC TEST SUPPLIER\nInvoice SYNTH-001     Date 2026-10-16\n\nTEST205  DEMO Touring 205/55R16\nQty 2   Unit cost 100.00 excluding VAT\nLine net 200.00   Line VAT 30.00\nSubtotal 200.00   VAT 30.00   Total ZAR 230.00",
        fill="black",font_size=25,spacing=18)
    checks=[]
    for index,(kind,mime) in enumerate((("PNG","image/png"),("PDF","application/pdf"))):
        if index:time.sleep(10)
        stream=BytesIO();image.save(stream,format=kind)
        url="data:"+mime+";base64,"+base64.b64encode(stream.getvalue()).decode()
        part={"type":"input_image","image_url":url,"detail":"high"} if kind=="PNG" else {"type":"input_file","filename":"synthetic.pdf","file_data":url}
        data,model=extraction._call_ai(part)
        valid=(data.invoice_number=="SYNTH-001" and data.invoice_total==230 and len(data.items)==1 and data.items[0].quantity==2)
        checks.append({"format":kind,"structured_response":True,"synthetic_fields_correct":valid})
        if not valid:raise TrialError("Synthetic extraction did not match the known test fields. Real-invoice testing remains blocked.")
    report={"passed":True,"model":MODEL,"checked_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"checks":checks,"key_sha256":key_hash}
    report_path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    return {key:value for key,value in report.items() if key!="key_sha256"}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",type=Path,default=DEFAULT_ROOT)
    parser.add_argument("--port",type=int,default=51653)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--setup-only",action="store_true")
    mode.add_argument("--serve",action="store_true")
    mode.add_argument("--serve-without-key",action="store_true")
    args=parser.parse_args(argv)
    if not 1024<=args.port<=65535:raise TrialError("Use a local port between 1024 and 65535")
    key,model=local_configuration()
    if args.serve and not key:
        key=getpass.getpass("Enter a LOCAL TEST OpenAI API key (hidden; not saved): ").strip()
        if not key:raise TrialError("No key entered. Live API verification has not run.")
    app,budget=prepare_app(args.data_dir,key,model)
    root=Path(args.data_dir).resolve()
    print("Isolated local data: "+str(root))
    print("Test tenant: "+USERNAME+" | Password: "+PASSWORD)
    print("AI allowance: 5 attempts total, including 2 synthetic checks; $0.10 app-local reservation budget; no automatic retries.")
    print(json.dumps(budget.summary()))
    if args.setup_only:
        print("Setup complete. API key, live model access and genuine extraction NOT verified.")
        return 0
    if args.serve:
        from app.services import supplier_invoice_extraction as extraction
        report=synthetic_preflight(extraction,root,key)
        app.config["TRIAL_API_READY"]=True
        print(json.dumps(report))
        print("Synthetic image/PDF checks passed. Genuine invoice extraction has NOT been tested.")
    else:
        print("NO KEY MODE: API preflight pending; extraction blocked.")
    from werkzeug.serving import make_server,WSGIRequestHandler
    class QuietHandler(WSGIRequestHandler):
        def log(self,*args,**kwargs):pass
        def log_request(self,*args,**kwargs):pass
        def log_error(self,*args,**kwargs):pass
    server=make_server("127.0.0.1",args.port,app,threaded=True,request_handler=QuietHandler)
    print(f"Open http://127.0.0.1:{args.port}/suppliers/invoices/new | Ctrl+C stops this server.",flush=True)
    try:server.serve_forever()
    finally:server.server_close()
    return 0


if __name__=="__main__":
    try:raise SystemExit(main())
    except KeyboardInterrupt:print("Local trial server stopped.")
    except Exception as exc:
        # Never print arbitrary exception messages: SDK errors can contain request data.
        print(str(exc) if isinstance(exc,TrialError) else "Local trial setup failed safely. No real extraction success is claimed.")
        raise SystemExit(1)
