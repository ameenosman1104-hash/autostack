"""Atomic supplier receiving, explicit approvals, isolation and retry safety."""
import copy
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from tests.test_supplier_invoices_phase1 import app, client, auth_headers
from tests.test_supplier_invoices_phase3 import invoice, review, save, snapshot
from tests.test_supplier_invoices_phase2 import document
from app.tenant_db import get_conn
from app.services.supplier_invoice_confirmation import confirmation_preview, confirm_invoice
from app.services.supplier_invoice_review import ReviewError


@pytest.fixture
def ready(client, invoice):
    draft=review(client,invoice)["draft"]
    for item in draft["items"]:
        item.update(match_type="existing",matched_product_id=11)
    assert save(client,invoice,draft).status_code==200
    return invoice


def preview(app,iid):
    with app.app_context():
        return confirmation_preview(10,iid)


def payload(app,iid):
    state=preview(app,iid)
    return {"token":state["token"],"confirmed":True,"approved_new_lines":[line["index"] for line in state["lines"] if line["new"]]}


def confirm(client,iid,data):
    return client.post(f"/suppliers/invoices/{iid}/confirm",json=data)


def dbrows(table):
    conn=get_conn(10)
    result=[dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    conn.close()
    return result


def test_existing_stock_cost_history_and_receipt(app,client,ready):
    conn=get_conn(10)
    conn.execute("UPDATE products SET selling_price=250,last_cost_price=80,previous_cost_price=70 WHERE id=11")
    conn.commit();conn.close()
    assert client.get(f"/suppliers/invoices/{ready}/confirm").status_code==200
    before=snapshot()
    data=payload(app,ready)
    assert snapshot()==before
    result=confirm(client,ready,data)
    assert result.status_code==200,result.json
    assert result.json["products_updated"]==1
    product=next(p for p in dbrows("products") if p["id"]==11)
    assert product["current_stock"]==13 and product["selling_price"]==250
    assert product["last_cost_price"]==100 and product["previous_cost_price"]==100
    history=dbrows("stock_history")
    assert [(r["qty_before"],r["qty_after"],r["change_by"]) for r in history]==[(9,11,2),(11,13,2)]
    assert all(r["change_type"]=="supplier_invoice" and f"#{ready}" in r["notes"] and "INV1" in r["notes"] and "testuser" in r["notes"] for r in history)
    assert dbrows("supplier_invoices")[0]["status"]=="processed"
    assert dbrows("supplier_invoices")[0]["confirmation_date"]
    assert len(dbrows("supplier_invoice_confirmations"))==1
    assert client.get(result.json["receipt_url"]).status_code==200
    assert result.json["invoice_total"]=="460" or Decimal(result.json["invoice_total"])==460
    assert client.get(f"/suppliers/invoices/{ready}/review").status_code==409
    for table in ("invoices","invoice_items","debtors","customers","suppliers"):
        assert [tuple(row.values()) for row in dbrows(table)]==before[table]


@pytest.mark.parametrize("mixed",[False,True])
def test_approved_new_and_mixed(app,client,ready,mixed):
    draft=review(client,ready)["draft"]
    for index,item in enumerate(draft["items"]):
        if not mixed or index==0:
            item.update(match_type="new",matched_product_id=None,product_code=f"NEW{index}")
    assert save(client,ready,draft).status_code==200
    data=payload(app,ready)
    result=confirm(client,ready,data)
    assert result.status_code==200,result.json
    assert result.json["products_updated"]==2
    new=[p for p in dbrows("products") if p["code"].startswith("NEW")]
    assert len(new)==(1 if mixed else 2)
    assert all(p["current_stock"]==2 and p["last_cost_price"]==100 and p["selling_price"] is None and p["condition"]=="new" for p in new)
    assert json.loads(new[0]["extra_data"])["tyre_size"]=="205/55R16"
    assert new[0]["brand"]=="Brand" and new[0]["tyre_size"]=="205/55R16" and new[0]["pattern"]=="Touring"
    after=snapshot()
    assert confirm(client,ready,data).json["already_processed"] is True
    assert snapshot()==after


def test_approval_required(app,client,ready):
    draft=review(client,ready)["draft"]
    draft["items"][0].update(match_type="new",matched_product_id=None,product_code="NEW")
    save(client,ready,draft)
    data=payload(app,ready);data["approved_new_lines"]=[]
    before=snapshot()
    assert confirm(client,ready,data).status_code==400
    assert snapshot()==before


def test_double_click_and_repeated_request(app,client,ready):
    data=payload(app,ready)
    first=confirm(client,ready,data)
    assert first.status_code==200
    before=snapshot()
    for _ in range(3):
        result=confirm(client,ready,data)
        assert result.status_code==200 and result.json["already_processed"]
    assert snapshot()==before


def test_concurrent_confirmation_exactly_once(app,client,ready):
    data=payload(app,ready)
    barrier=threading.Barrier(2)
    def worker():
        with app.app_context():
            barrier.wait()
            return confirm_invoice(10,ready,data,"testuser")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:worker(),range(2)))
    assert sorted(r["already_processed"] for r in results)==[False,True]
    assert next(p for p in dbrows("products") if p["id"]==11)["current_stock"]==13
    assert len(dbrows("stock_history"))==2


@pytest.mark.parametrize("target",["history","status","receipt"])
def test_late_failure_rolls_everything_back(app,client,ready,target):
    draft=review(client,ready)["draft"]
    draft["items"][0].update(match_type="new",matched_product_id=None,product_code="NEW")
    save(client,ready,draft)
    data=payload(app,ready)
    conn=get_conn(10)
    trigger={"history":"BEFORE INSERT ON stock_history WHEN NEW.product_id=11", "status":"BEFORE UPDATE OF status ON supplier_invoices WHEN NEW.status='processed'", "receipt":"BEFORE UPDATE ON supplier_invoice_confirmations"}[target]
    conn.execute(f"CREATE TRIGGER fail_receiving {trigger} BEGIN SELECT RAISE(ABORT,'forced failure'); END")
    conn.commit();conn.close()
    before=snapshot()
    response=confirm(client,ready,data)
    assert response.status_code==409,response.json
    assert snapshot()==before and dbrows("supplier_invoice_confirmations")==[]
    conn=get_conn(10);conn.execute("DROP TRIGGER fail_receiving");conn.commit();conn.close()
    assert confirm(client,ready,data).status_code==200


@pytest.mark.parametrize("change",["version","mapping","code","name","deleted"])
def test_stale_review_or_changed_products_rejected(app,client,ready,change):
    data=payload(app,ready)
    if change in {"version","mapping"}:
        draft=review(client,ready)["draft"]
        if change=="mapping":draft["items"][0]["matched_product_id"]=12
        save(client,ready,draft)
    else:
        conn=get_conn(10)
        field,value={"code":("code","CHANGED"),"name":("name","Changed name"),"deleted":("deleted",1)}[change]
        conn.execute(f"UPDATE products SET {field}=? WHERE id=11",(value,));conn.commit();conn.close()
    before=snapshot()
    assert confirm(client,ready,data).status_code==409
    assert snapshot()==before


def test_uses_actual_balances_and_preserves_latest_selling_price(app,client,ready):
    data=payload(app,ready)
    conn=get_conn(10);conn.execute("UPDATE products SET current_stock=30,selling_price=777 WHERE id=11");conn.commit();conn.close()
    result=confirm(client,ready,data)
    assert result.status_code==200
    product=next(p for p in dbrows("products") if p["id"]==11)
    assert product["current_stock"]==34 and product["selling_price"]==777


@pytest.mark.parametrize("change",[
    {"confirmed":False},{"confirmed":1},{"token":"tampered"},{"current_stock":999},
    {"selling_price":1},{"tenant_id":20},{"approved_new_lines":[True]},
    {"approved_new_lines":[0,0]},{"approved_new_lines":[-1]},{"approved_new_lines":[999]}])
def test_invalid_confirmation_payload(app,client,ready,change):
    data=payload(app,ready);data.update(change)
    before=snapshot()
    assert confirm(client,ready,data).status_code in {400,409}
    assert snapshot()==before


@pytest.mark.parametrize("field,value",[("supplier_name",None),("invoice_date",None),("invoice_number",None),("subtotal",None),("vat",None),("invoice_total",None),("line_totals_include_vat",None),("subtotal","999"),("vat","999"),("invoice_total","999")])
def test_incomplete_or_inconsistent_totals_block(app,client,ready,field,value):
    draft=review(client,ready)["draft"];draft[field]=value
    assert save(client,ready,draft).status_code==200
    before=snapshot()
    assert client.get(f"/suppliers/invoices/{ready}/confirm").status_code==400
    assert snapshot()==before


@pytest.mark.parametrize("field,value",[("quantity",None),("unit_cost",None),("line_total",None),("vat",None),("product_description",None),("match_type","unresolved"),("line_total","201"),("vat","31")])
def test_missing_line_values_block(app,client,ready,field,value):
    draft=review(client,ready)["draft"];draft["items"][0][field]=value
    if field=="match_type":draft["items"][0]["matched_product_id"]=None
    assert save(client,ready,draft).status_code==200
    before=snapshot()
    assert client.get(f"/suppliers/invoices/{ready}/confirm").status_code==400
    assert snapshot()==before


@pytest.mark.parametrize("code",["T1","t1","DELETED","NEW!","", "bad\\code"])
def test_duplicate_or_invalid_new_code(app,client,ready,code):
    draft=review(client,ready)["draft"]
    draft["items"][0].update(match_type="new",matched_product_id=None,product_code=code)
    assert save(client,ready,draft).status_code==200
    before=snapshot()
    assert client.get(f"/suppliers/invoices/{ready}/confirm").status_code in {400,409}
    assert snapshot()==before


def test_new_code_created_after_preview_blocks(app,client,ready):
    draft=review(client,ready)["draft"]
    draft["items"][0].update(match_type="new",matched_product_id=None,product_code="NEW")
    save(client,ready,draft);data=payload(app,ready)
    conn=get_conn(10);conn.execute("INSERT INTO products(code,name) VALUES('new','Concurrent product')");conn.commit();conn.close()
    before=snapshot()
    assert confirm(client,ready,data).status_code==409
    assert snapshot()==before


def test_duplicate_supplier_identity_across_drafts(app,client,ready):
    assert confirm(client,ready,payload(app,ready)).status_code==200
    iid=document(client,number="OTHER")
    conn=get_conn(10)
    source=conn.execute("SELECT extracted_json FROM supplier_invoices WHERE id=?",(ready,)).fetchone()[0]
    conn.execute("UPDATE supplier_invoices SET status='extracted',extracted_json=? WHERE id=?",(source,iid));conn.commit();conn.close()
    draft=review(client,iid)["draft"]
    for item in draft["items"]:item.update(match_type="existing",matched_product_id=11)
    draft["supplier_name"]="SUPPLIER";draft["invoice_number"]="INV-1"
    save(client,iid,draft)
    before=snapshot()
    assert client.get(f"/suppliers/invoices/{iid}/confirm").status_code==409
    assert snapshot()==before


def test_tenant_isolation(app,client,ready):
    data=payload(app,ready)
    # A signed token cannot be moved to another tenant even with the same invoice ID.
    with app.app_context(),pytest.raises(ReviewError) as error:
        confirm_invoice(20,ready,data,"other")
    assert error.value.status_code==403
    from app.main_db import get_conn as main_conn
    conn=main_conn();conn.execute("INSERT INTO tenants(id,business_name,username,password_hash) VALUES(20,'Other','other','unused')");conn.commit();conn.close()
    with client.session_transaction() as session:session["_user_id"]="20"
    assert client.get(f"/suppliers/invoices/{ready}/confirm").status_code==404
    assert client.get(f"/suppliers/invoices/{ready}/processed").status_code==404
    assert confirm(client,ready,data).status_code==403
    assert next(p for p in dbrows("products") if p["id"]==11)["current_stock"]==9


def test_csrf_and_anonymous(app,client,ready):
    data=payload(app,ready)
    app.config["WTF_CSRF_ENABLED"]=True
    assert confirm(client,ready,data).status_code==400
    app.config["WTF_CSRF_ENABLED"]=False
    with client.session_transaction() as session:session.clear()
    assert confirm(client,ready,data).status_code in {302,401}
    assert client.get(f"/suppliers/invoices/{ready}/confirm").status_code in {302,401}


def test_gross_line_totals(app,client,ready):
    draft=review(client,ready)["draft"]
    draft["line_totals_include_vat"]=True
    for item in draft["items"]:item["line_total"]="230"
    save(client,ready,draft)
    result=confirm(client,ready,payload(app,ready))
    assert result.status_code==200,result.json


def test_receipt_database_constraints(app,client,ready):
    assert confirm(client,ready,payload(app,ready)).status_code==200
    conn=get_conn(10)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO supplier_invoice_confirmations SELECT * FROM supplier_invoice_confirmations")
    conn.rollback()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0]==1
    assert conn.execute("PRAGMA foreign_key_check").fetchall()==[]
    conn.close()


def test_migration_existing_tenant_preserves_data(app,client,ready):
    from app.db_migrations import migrate_tenant_db
    from app.tenant_db import _db_path
    conn=get_conn(10)
    conn.execute("DROP TABLE supplier_invoice_confirmations")
    conn.execute("DELETE FROM schema_migrations WHERE version=24")
    conn.commit();conn.close()
    before=snapshot_without_migration()
    migrate_tenant_db(10,_db_path(10))
    migrate_tenant_db(10,_db_path(10))
    assert snapshot()==before
    assert dbrows("supplier_invoice_confirmations")==[]


def snapshot_without_migration():
    from app.tenant_db import _db_path
    conn=sqlite3.connect(_db_path(10))
    result={table:[tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in
            ("products","stock_history","invoices","invoice_items","suppliers","debtors","customers","supplier_invoice_items")}
    result["extracted_json"]=[tuple(row) for row in conn.execute("SELECT extracted_json,status,confirmation_date FROM supplier_invoices")]
    conn.close()
    return result


@pytest.mark.parametrize("field,value",[("quantity",-1),("quantity",0),("quantity","NaN"),("unit_cost",-1),("unit_cost","Infinity"),("line_total",-1)])
def test_tampered_saved_draft_revalidated(app,client,ready,field,value):
    data=payload(app,ready)
    conn=get_conn(10)
    row=conn.execute("SELECT draft_json FROM supplier_invoice_reviews WHERE supplier_invoice_id=?",(ready,)).fetchone()
    draft=json.loads(row[0]);draft["items"][0][field]=value
    conn.execute("UPDATE supplier_invoice_reviews SET draft_json=? WHERE supplier_invoice_id=?",(json.dumps(draft),ready));conn.commit();conn.close()
    before=snapshot()
    assert confirm(client,ready,data).status_code==400
    assert snapshot()==before


def test_distinct_existing_products_keep_previous_cost(app,client,ready):
    draft=review(client,ready)["draft"]
    draft["items"][1]["matched_product_id"]=12
    save(client,ready,draft)
    conn=get_conn(10);conn.execute("UPDATE products SET last_cost_price=80,selling_price=220 WHERE id IN (11,12)");conn.commit();conn.close()
    result=confirm(client,ready,payload(app,ready))
    assert result.status_code==200 and result.json["products_updated"]==2
    products=[p for p in dbrows("products") if p["id"] in (11,12)]
    assert all(p["previous_cost_price"]==80 and p["last_cost_price"]==100 and p["selling_price"]==220 for p in products)
    assert [p["current_stock"] for p in products]==[11,22]


def test_same_code_two_new_lines_rejected(app,client,ready):
    draft=review(client,ready)["draft"]
    for item in draft["items"]:item.update(match_type="new",matched_product_id=None,product_code="NEW")
    save(client,ready,draft)
    before=snapshot()
    assert client.get(f"/suppliers/invoices/{ready}/confirm").status_code==409
    assert snapshot()==before


def test_confirmation_unknown_invoice_and_invalid_content(client,auth_headers):
    assert client.get("/suppliers/invoices/999/confirm").status_code==404
    assert client.post("/suppliers/invoices/999/confirm",data="bad").status_code==400


def test_confirmation_valid_csrf_and_ignores_tenant_header(app,client,ready):
    import re
    data=payload(app,ready)
    app.config["WTF_CSRF_ENABLED"]=True
    html=client.get(f"/suppliers/invoices/{ready}/confirm").text
    token=re.search(r'name="csrf_token" value="([^"]+)"',html).group(1)
    response=client.post(f"/suppliers/invoices/{ready}/confirm",json=data,headers={"X-CSRFToken":token,"X-Tenant-ID":"20"})
    assert response.status_code==200,response.json
    assert next(p for p in dbrows("products") if p["id"]==11)["current_stock"]==13


def test_concurrent_duplicate_invoice_identities(app,client,ready):
    first=payload(app,ready)
    iid=document(client,number="OTHER")
    conn=get_conn(10)
    source=conn.execute("SELECT extracted_json FROM supplier_invoices WHERE id=?",(ready,)).fetchone()[0]
    conn.execute("UPDATE supplier_invoices SET status='extracted',extracted_json=? WHERE id=?",(source,iid));conn.commit();conn.close()
    draft=review(client,iid)["draft"]
    for item in draft["items"]:item.update(match_type="existing",matched_product_id=11)
    save(client,iid,draft)
    second=payload(app,iid)
    barrier=threading.Barrier(2)
    def worker(pair):
        with app.app_context():
            barrier.wait()
            try:
                return confirm_invoice(10,pair[0],pair[1],"testuser")["success"]
            except ReviewError as exc:
                return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,[(ready,first),(iid,second)]))
    assert results.count(True)==1 and results.count(409)==1
    assert len(dbrows("supplier_invoice_confirmations"))==1
    assert next(p for p in dbrows("products") if p["id"]==11)["current_stock"]==13
