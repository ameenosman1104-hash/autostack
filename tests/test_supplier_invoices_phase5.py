"""Full scanner workflow and read-only management, using isolated tenants and mocked AI."""
import hashlib
import json
import re
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.datastructures import MultiDict

from tests.test_supplier_invoices_phase1 import app, client, auth_headers
from tests.test_supplier_invoices_phase2 import ai, document, output, PDF
from tests.test_supplier_invoices_phase3 import invoice, review, save, snapshot
from tests.test_supplier_invoices_phase4 import ready, payload, confirm, dbrows
from app.tenant_db import get_conn
from app.services.supplier_invoice_history import history, invoice_details


def image_bytes(kind):
    stream=BytesIO();Image.new("RGB",(80,80),"white").save(stream,format=kind)
    return stream.getvalue()


def ids(response):
    assert response.status_code==200,response.text[:200]
    return [int(value) for value in re.findall(r'data-invoice-id="(\d+)"',response.text)]


@pytest.mark.parametrize("ext,content",[("pdf",PDF),("png",image_bytes("PNG")),("jpg",image_bytes("JPEG"))])
def test_all_five_phases_complete_workflow(app,client,auth_headers,ai,ext,content):
    conn=get_conn(10)
    conn.execute("INSERT INTO products(id,code,name,current_stock,last_cost_price,selling_price) VALUES(11,'T1','Existing tyre',9,80,250)")
    conn.commit();conn.close()
    original_hash=hashlib.sha256(content).hexdigest()
    iid=document(client,ext=ext,content=content)
    assert dbrows("products")[0]["current_stock"]==9
    extracted=client.post(f"/suppliers/invoices/{iid}/extract")
    assert extracted.status_code==200,extracted.json
    state=review(client,iid)
    assert state["previews"][0]["suggestions"][0]["id"]==11
    assert client.get("/suppliers/invoices/products?q=T1").json["products"][0]["id"]==11
    draft=state["draft"]
    draft.update(supplier_name="Corrected Supplier",invoice_number="CORRECTED-16",invoice_date="2026-10-16")
    draft["items"][0].update(match_type="existing",matched_product_id=11)
    draft["items"][1].update(match_type="new",matched_product_id=None,product_code="NEW205",product_description="Approved touring tyre")
    saved=save(client,iid,draft)
    assert saved.status_code==200,saved.json
    assert len(dbrows("products"))==1 and dbrows("products")[0]["current_stock"]==9
    data=payload(app,iid)
    without_approval={**data,"approved_new_lines":[]}
    assert confirm(client,iid,without_approval).status_code==400
    assert len(dbrows("products"))==1
    result=confirm(client,iid,data)
    assert result.status_code==200,result.json
    products=dbrows("products")
    assert products[0]["current_stock"]==11 and products[0]["last_cost_price"]==100
    assert products[0]["previous_cost_price"]==80 and products[0]["selling_price"]==250
    assert products[1]["code"]=="NEW205" and products[1]["current_stock"]==2
    assert len(dbrows("stock_history"))==2
    assert result.json["products_updated"]==2
    after=snapshot()
    assert confirm(client,iid,data).json["already_processed"] is True
    assert snapshot()==after
    assert ids(client.get("/suppliers/invoices/?q=CORRECTED-16&status=processed"))==[iid]
    details=client.get(result.json["details_url"])
    assert details.status_code==200,details.text[:300]
    for text in ("Corrected Supplier","CORRECTED-16","2026-10-16","Original AI extraction","Created","Updated","testuser","Linked stock movements"):
        assert text in details.text
    assert f'movement-{result.json["lines"][0]["stock_history_id"]}' in details.text
    assert client.post(f"/suppliers/invoices/{iid}/review",json=saved.json["draft"]).status_code==409
    assert client.get(f"/suppliers/invoices/{iid}/review").status_code==409
    assert client.delete(f"/suppliers/invoices/{iid}").status_code==405
    for suffix in ("original","download"):
        response=client.get(f"/suppliers/invoices/{iid}/{suffix}")
        assert response.status_code==200,response.text[:200] if response.mimetype=="text/html" else ""
        assert response.data==content
        assert hashlib.sha256(response.data).hexdigest()==original_hash
        assert "no-store" in response.headers["Cache-Control"]
        assert response.headers["X-Content-Type-Options"]=="nosniff"
        assert ("attachment" if suffix=="download" else "inline") in response.headers["Content-Disposition"]
        response.close()
    row=dbrows("supplier_invoices")[0]
    assert Path(row["file_path"]).read_bytes()==content
    assert row["extraction_sha256"]==original_hash
    assert snapshot()==after
    assert ai.call_count==1


@pytest.fixture
def listed(client,auth_headers):
    conn=get_conn(10)
    for n in range(1,31):
        source={"supplier_name":"Alpha Tyres" if n%2 else "Beta Parts", "invoice_number":f"INV-{n:03}",
                "invoice_date":f"2026-10-{(n-1)%28+1:02}", "invoice_total":n*10}
        conn.execute("""INSERT INTO supplier_invoices(id,supplier_name,invoice_number,invoice_date,
            file_name,file_path,created_by,status,upload_date,extracted_json)
            VALUES(?,?,?,?,?,'/not-displayed',?,?,?,?)""",
            (n,source["supplier_name"],source["invoice_number"],source["invoice_date"],"invoice.pdf",
             "uploader", "processed" if n%3==0 else "uploaded",f"2026-10-{(n-1)%28+1:02} 10:00:00",json.dumps(source)))
    conn.commit();conn.close()


def test_history_pagination_and_sorting(client,listed):
    first=ids(client.get("/suppliers/invoices/?sort=invoice_number&direction=asc&per_page=10"))
    second_response=client.get("/suppliers/invoices/?sort=invoice_number&direction=asc&per_page=10&page=2")
    second=ids(second_response)
    third=ids(client.get("/suppliers/invoices/?sort=invoice_number&direction=asc&per_page=10&page=3"))
    assert first==list(range(1,11)) and second==list(range(11,21)) and third==list(range(21,31))
    assert "Page 2 of 3" in second_response.text and 'rel="prev"' in second_response.text and 'rel="next"' in second_response.text
    assert ids(client.get("/suppliers/invoices/?sort=total&direction=desc&per_page=10"))==list(range(30,20,-1))
    assert ids(client.get("/suppliers/invoices/?sort=invoice_number&direction=asc&per_page=10&page=999"))==third


@pytest.mark.parametrize("query,expected",[
    ("supplier=alpha&per_page=50&sort=invoice_number&direction=desc",list(range(29,0,-2))),
    ("invoice_number=INV-015",[15]),("q=inv-009",[9]),
    ("status=processed&sort=invoice_number&direction=asc",list(range(3,31,3))),
    ("date_from=2026-10-10&date_to=2026-10-12&sort=invoice_date&direction=asc",[10,11,12]),
    ("supplier=alpha&status=processed&date_from=2026-10-10&date_to=2026-10-25",[21,15]),
    ("q=%25",[]),("q=_",[]),("q=%27%20OR%201%3D1--",[]),
])
def test_history_search_and_filters(client,listed,query,expected):
    assert ids(client.get("/suppliers/invoices/?"+query))==expected


@pytest.mark.parametrize("query",["sort=DROP%20TABLE%20products","direction=sideways","page=0","page=-1","page=abc","per_page=100000","status=unknown","date_from=2026-02-30","date_from=20261016","date_from=2026-10-20&date_to=2026-10-01","tenant_id=20","q=a&q=b","q="+"a"*201])
def test_invalid_filters_explained(client,auth_headers,query):
    response=client.get("/suppliers/invoices/?"+query)
    assert response.status_code==400
    assert "Clear filters and return to history" in response.text
    assert "Traceback" not in response.text


def test_corrected_values_drive_history(app,client,invoice):
    draft=review(client,invoice)["draft"]
    draft.update(supplier_name="Corrected shop",invoice_number="COR-123",invoice_date="2026-10-16",invoice_total="123.45")
    assert save(client,invoice,draft).status_code==200
    response=client.get("/suppliers/invoices/?supplier=corrected&invoice_number=COR-123&date_from=2026-10-16")
    assert ids(response)==[invoice] and "123.45" in response.text
    assert client.get(f"/suppliers/invoices/{invoice}/details").status_code==200


def test_upload_only_details_show_metadata(client,auth_headers):
    iid=document(client,number="UPLOAD-ONLY")
    response=client.get(f"/suppliers/invoices/{iid}/details")
    assert response.status_code==200
    assert "Uploaded invoice information" in response.text
    assert "UPLOAD-ONLY" in response.text and "Original AI extraction" not in response.text


def test_processed_history_is_immutable(app,client,ready):
    result=confirm(client,ready,payload(app,ready))
    assert result.status_code==200
    before=snapshot()
    for path in ("",f"{ready}/details",f"{ready}/processed",f"{ready}/original",f"{ready}/download"):
        response=client.get("/suppliers/invoices/"+path)
        assert response.status_code==200
        if response.mimetype=="text/html":
            assert 'class="btn btn-outline-primary extract-invoice"' not in response.text
        response.close()
    assert snapshot()==before
    for method in (client.post,client.put,client.patch,client.delete):
        assert method(f"/suppliers/invoices/{ready}/details",json={"current_stock":999}).status_code==405
    assert snapshot()==before


@pytest.mark.parametrize("damage",["missing","truncated","wrong_size","wrong_hash","outside","wrong_mime"])
def test_unavailable_original_graceful_and_no_paths(client,invoice,tmp_path,damage):
    conn=get_conn(10);row=conn.execute("SELECT * FROM supplier_invoices WHERE id=?",(invoice,)).fetchone()
    path=Path(row["file_path"])
    if damage=="missing":path.unlink()
    elif damage=="truncated":path.write_bytes(b"%PDF-broken")
    elif damage=="wrong_size":conn.execute("UPDATE supplier_invoices SET file_size=1 WHERE id=?",(invoice,))
    elif damage=="wrong_hash":conn.execute("UPDATE supplier_invoices SET extraction_sha256=? WHERE id=?",("0"*64,invoice))
    elif damage=="outside":
        other=tmp_path/"private.pdf";other.write_bytes(PDF)
        conn.execute("UPDATE supplier_invoices SET file_path=? WHERE id=?",(str(other),invoice))
    else:conn.execute("UPDATE supplier_invoices SET mime_type='text/html' WHERE id=?",(invoice,))
    conn.commit();conn.close()
    response=client.get(f"/suppliers/invoices/{invoice}/details")
    assert response.status_code==200 and "unavailable or damaged" in response.text
    assert str(path) not in response.text and str(tmp_path) not in response.text
    for suffix in ("original","download"):
        response=client.get(f"/suppliers/invoices/{invoice}/{suffix}")
        assert response.status_code==404 and "unavailable or damaged" in response.text
        assert str(path) not in response.text


@pytest.mark.parametrize("kind,content",[("png",b"\x89PNG\r\n\x1a\nfixture"),("jpg",b"\xff\xd8\xfffixture"),("pdf",b"%PDF-malformed")])
def test_damaged_format_not_served(client,auth_headers,kind,content):
    iid=document(client,ext=kind,content=content)
    assert client.get(f"/suppliers/invoices/{iid}/download").status_code==404


def test_tenant_isolation_history_details_and_documents(app,client,invoice):
    from app.main_db import get_conn as main_conn
    conn=main_conn();conn.execute("INSERT INTO tenants(id,business_name,username,password_hash) VALUES(20,'Other','other','unused')");conn.commit();conn.close()
    with client.session_transaction() as session:session["_user_id"]="20"
    assert ids(client.get("/suppliers/invoices/"))==[]
    for path in ("details","original","download","processed","review","confirm","extraction"):
        response=client.get(f"/suppliers/invoices/{invoice}/{path}",headers={"X-Tenant-ID":"10"})
        assert response.status_code==404,(path,response.status_code)
    assert client.post(f"/suppliers/invoices/{invoice}/extract").status_code==404


def test_anonymous_management_access_denied(client):
    for path in ("","new","1/details","1/original","1/download"):
        assert client.get("/suppliers/invoices/"+path).status_code==302


def test_audit_movements_match_receipt_not_current_stock(app,client,ready):
    result=confirm(client,ready,payload(app,ready)).json
    conn=get_conn(10);conn.execute("UPDATE products SET current_stock=999 WHERE id=11");conn.commit();conn.close()
    with app.app_context():state=invoice_details(10,ready)
    assert [row["id"] for row in state["movements"]]==[line["stock_history_id"] for line in result["lines"]]
    assert [(row["qty_before"],row["qty_after"],row["change_by"]) for row in state["movements"]]==[(9,11,2),(11,13,2)]
    assert state["confirmed_by"]=="testuser" and state["confirmed_at"]
    assert state["receipt"]["lines"][0]["projected_stock"]=="11.0" or state["receipt"]["lines"][0]["projected_stock"]=="11"


def test_html_escape_and_no_internal_paths(client,invoice):
    draft=review(client,invoice)["draft"]
    draft["supplier_name"]='<script>alert("x")</script>'
    save(client,invoice,draft)
    conn=get_conn(10);internal=conn.execute("SELECT file_path FROM supplier_invoices WHERE id=?",(invoice,)).fetchone()[0];conn.close()
    for path in ("",f"{invoice}/details"):
        response=client.get("/suppliers/invoices/"+path)
        assert response.status_code==200
        assert '<script>alert("x")</script>' not in response.text and internal not in response.text


def test_scan_page_and_responsive_assets(client,auth_headers):
    response=client.get("/suppliers/invoices/new")
    assert response.status_code==200
    for text in ('capture="environment"','accept=".jpg,.jpeg,.png,.pdf','name="csrf_token"','Upload &amp; Extract','do not create products'):
        assert text in response.text
    assert "Supplier Invoice History" in client.get("/suppliers/invoices/").text
    root=Path(__file__).parents[1]
    css=(root/"app/static/css/supplier-invoice-history.css").read_text()
    assert "@media(max-width:700px)" in css and "data-label" in css
    assert "minmax(0,1fr)" in css and "overflow:auto" in css


def test_csrf_remains_required_on_upload_and_extraction(app,client,auth_headers):
    iid=document(client)
    app.config["WTF_CSRF_ENABLED"]=True
    assert client.post(f"/suppliers/invoices/{iid}/extract").status_code==400
    assert client.post("/suppliers/invoices/upload",data={}).status_code==400


def test_same_original_cannot_add_stock_under_changed_references(app,client,auth_headers,ai):
    conn=get_conn(10)
    conn.execute("INSERT INTO products(id,code,name,current_stock) VALUES(11,'T1','Tyre',9)")
    conn.commit();conn.close()
    first=document(client,number="FIRST")
    assert client.post(f"/suppliers/invoices/{first}/extract").status_code==200
    draft=review(client,first)["draft"]
    for item in draft["items"]:item.update(match_type="existing",matched_product_id=11)
    assert save(client,first,draft).status_code==200
    first_confirmation=payload(app,first)
    second=document(client,number="SECOND")
    assert client.post(f"/suppliers/invoices/{second}/extract").status_code==200
    draft=review(client,second)["draft"]
    draft.update(supplier_name="Renamed supplier",invoice_number="RENAMED-REFERENCE")
    for item in draft["items"]:item.update(match_type="existing",matched_product_id=11)
    assert save(client,second,draft).status_code==200
    second_confirmation=payload(app,second)
    assert confirm(client,first,first_confirmation).status_code==200
    before=snapshot()
    denied=confirm(client,second,second_confirmation)
    assert denied.status_code==409 and "original document" in denied.json["error"]
    response=client.get(f"/suppliers/invoices/{second}/confirm")
    assert response.status_code==409
    assert "original document" in response.text
    assert snapshot()==before
