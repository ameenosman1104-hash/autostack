"""Review and matching security, draft validation and no-stock-write regressions."""
import copy
from decimal import Decimal
import json
import re
import sqlite3
from io import BytesIO
from pathlib import Path

import pytest

from tests.test_supplier_invoices_phase1 import app, client, auth_headers
from tests.test_supplier_invoices_phase2 import document, output, PDF
from app.tenant_db import get_conn
from app.db_migrations import migrate_tenant_db, LATEST_SCHEMA_VERSION


@pytest.fixture
def invoice(client, auth_headers):
    iid = document(client)
    data = output()
    data["items"][0]["confidence"] = "low"
    data["items"][0]["warnings"] = ["Check the blurred tyre size"]
    data["review_flags"] = ["items[0].uncertain"]
    conn = get_conn(10)
    conn.execute("UPDATE supplier_invoices SET status='extracted',extracted_json=? WHERE id=?", (json.dumps(data), iid))
    conn.execute("""INSERT INTO products (id,code,name,current_stock,extra_data) VALUES
        (11,'T1','Brand Touring 205/55R16',9,'{"brand":"Brand","pattern":"Touring","size":"205/55R16"}'),
        (12,'OTHER','Other tyre 195/65R15',20,'{}'),
        (13,'DELETED','Deleted Brand 205/55R16',1,'{}')""")
    conn.execute("UPDATE products SET deleted=1 WHERE id=13")
    conn.commit();conn.close()
    return iid


def review(client, iid):
    response=client.get(f"/suppliers/invoices/{iid}/review-data")
    assert response.status_code == 200, response.json
    return response.json


def save(client, iid, draft):
    return client.post(f"/suppliers/invoices/{iid}/review", json=draft)


def snapshot():
    conn=get_conn(10)
    result={table:[tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in
            ("products","stock_history","invoices","invoice_items","suppliers","debtors","customers","supplier_invoice_items")}
    result["extracted_json"]=conn.execute("SELECT extracted_json,status,confirmation_date FROM supplier_invoices").fetchall()
    result["extracted_json"]=[tuple(row) for row in result["extracted_json"]]
    conn.close()
    return result


def test_review_page_and_original_render(client, invoice):
    response=client.get(f"/suppliers/invoices/{invoice}/review")
    assert response.status_code==200
    html=response.text
    for text in ("Original invoice", "Save Draft", "Cancel", "Review only", "review-line-template", "csrf_token", "supplier-invoice-review.js"):
        assert text in html
    original=client.get(f"/suppliers/invoices/{invoice}/original")
    assert original.status_code==200 and original.data==PDF
    assert original.mimetype=="application/pdf"
    assert "no-store" in original.headers["Cache-Control"]
    assert original.headers["X-Content-Type-Options"]=="nosniff"
    original.close()


def test_save_reload_repeat_and_no_inventory_writes(client, invoice):
    before=snapshot()
    state=review(client,invoice)
    assert state["previews"][0]["uncertain"] is True
    draft=state["draft"]
    draft.update(supplier_name="Corrected supplier",invoice_number="CORRECTED",invoice_date="2026-10-15",subtotal="600.00",vat="90.00",invoice_total="690.00")
    draft["items"][0].update(product_description="Corrected tyre",brand="Corrected brand",tyre_size="225/45R17",pattern_model="Sport",quantity="3",unit_cost="100",line_total="300",vat="45",match_type="existing",matched_product_id=11)
    response=save(client,invoice,draft)
    assert response.status_code==200,response.json
    assert response.json["draft"]["version"]==1
    loaded=review(client,invoice)
    assert loaded["draft"]["supplier_name"]=="Corrected supplier"
    assert loaded["draft"]["items"][0]["matched_product_id"]==11
    loaded["draft"]["items"][0]["quantity"]="4"
    assert save(client,invoice,loaded["draft"]).json["draft"]["version"]==2
    assert snapshot()==before
    conn=get_conn(10)
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_review_events").fetchone()[0]==2
    conn.close()


def test_matching_suggestions_are_not_selected(client,invoice):
    state=review(client,invoice)
    first=state["previews"][0]
    assert first["suggestions"][0]["id"]==11
    assert set(first["suggestions"][0]["reasons"])=={"Product code","Tyre size","Brand","Pattern"}
    assert state["draft"]["items"][0]["match_type"]=="unresolved"
    assert first["selected_product"] is None and first["projected_stock"] is None
    result=client.get("/suppliers/invoices/products?q=205%2055R16").json["products"]
    assert [p["id"] for p in result]==[11]
    assert all(p["id"]!=13 for p in client.get("/suppliers/invoices/products").json["products"])


def test_aggregate_stock_preview_for_same_selected_product(client,invoice):
    draft=review(client,invoice)["draft"]
    for index,item in enumerate(draft["items"]):
        item.update(match_type="existing",matched_product_id=11,quantity=str(index+2))
    result=save(client,invoice,draft).json
    assert all(p["current_stock"]==9 for p in result["previews"])
    assert all(Decimal(p["projected_stock"])==14 for p in result["previews"])
    assert result["estimated_purchase_cost"]=="500.00"
    conn=get_conn(10)
    assert conn.execute("SELECT current_stock FROM products WHERE id=11").fetchone()[0]==9
    conn.close()


def test_new_product_is_only_a_proposal(client,invoice):
    before=snapshot()
    draft=review(client,invoice)["draft"]
    draft["items"][0].update(match_type="new",matched_product_id=None,product_code="PROPOSED",product_description="Proposed tyre")
    response=save(client,invoice,draft)
    assert response.status_code==200
    assert response.json["previews"][0]["current_stock"]==0
    assert response.json["previews"][0]["projected_stock"]=="2.0" or response.json["previews"][0]["projected_stock"]=="2"
    assert snapshot()==before


@pytest.mark.parametrize("field,value",[("quantity",0),("quantity",-1),("quantity",True),("quantity","NaN"),("quantity","Infinity"),("quantity","1000001"),("quantity","1.0001"),("unit_cost",-1),("unit_cost","abc"),("unit_cost","NaN"),("unit_cost",True),("unit_cost","0.00001"),("line_total",-1),("vat",-1),("vat","1.001"),("product_description",["wrong"]),("brand","x"*101)])
def test_invalid_line_values_rejected_without_changes(client,invoice,field,value):
    before=snapshot()
    draft=review(client,invoice)["draft"]
    draft["items"][0][field]=value
    assert save(client,invoice,draft).status_code==400
    assert review(client,invoice)["draft"]["version"]==0
    assert snapshot()==before


@pytest.mark.parametrize("field,value",[("version",True),("version",-1),("version","0"),("invoice_date","2026-02-30"),("invoice_date","15/10/2026"),("invoice_total",-1),("vat",True),("supplier_name",["bad"]),("supplier_name","x"*201),("tenant_id",20),("status","processed")])
def test_invalid_header_or_untrusted_keys(client,invoice,field,value):
    draft=review(client,invoice)["draft"]
    draft[field]=value
    assert save(client,invoice,draft).status_code==400


def test_incomplete_draft_allowed_with_warnings(client,invoice):
    draft=review(client,invoice)["draft"]
    draft.update(supplier_name=None,invoice_number=None,invoice_date=None,vat=None)
    draft["items"][0].update(quantity=None,unit_cost=None)
    response=save(client,invoice,draft)
    assert response.status_code==200
    assert "missing:supplier_name" in response.json["warnings"]
    assert response.json["cost_is_complete"] is False


def test_stale_review_cannot_overwrite_newer_edits(client,invoice):
    first=review(client,invoice)["draft"]
    second=copy.deepcopy(first)
    first["supplier_name"]="First editor"
    assert save(client,invoice,first).status_code==200
    second["supplier_name"]="Stale second editor"
    response=save(client,invoice,second)
    assert response.status_code==409 and "newer draft" in response.json["error"]
    assert review(client,invoice)["draft"]["supplier_name"]=="First editor"


def test_duplicate_draft_warning_without_mutating_original_identity(client,invoice):
    other=document(client,number="OTHER")
    draft=review(client,invoice)["draft"]
    draft.update(supplier_name=" Supplier ",invoice_number="other")
    response=save(client,invoice,draft)
    assert response.status_code==200
    assert other in response.json["duplicate_ids"]
    assert any("duplicate" in warning for warning in response.json["warnings"])
    conn=get_conn(10)
    assert conn.execute("SELECT invoice_number FROM supplier_invoices WHERE id=?",(invoice,)).fetchone()[0]=="INV1"
    conn.close()


@pytest.mark.parametrize("pid",[999,13])
def test_unavailable_or_deleted_product_rejected(client,invoice,pid):
    draft=review(client,invoice)["draft"]
    draft["items"][0].update(match_type="existing",matched_product_id=pid)
    assert save(client,invoice,draft).status_code==400


def test_cannot_smuggle_product_id_as_proposal(client,invoice):
    draft=review(client,invoice)["draft"]
    draft["items"][0].update(match_type="new",matched_product_id=11)
    assert save(client,invoice,draft).status_code==400


def test_tenant_isolation_on_every_route_and_product_lookup(client,invoice):
    from app.main_db import get_conn as main_conn
    conn=main_conn()
    conn.execute("INSERT INTO tenants(id,business_name,username,password_hash) VALUES(20,'Other','other','hash')")
    conn.commit();conn.close()
    conn=get_conn(20)
    conn.execute("INSERT INTO products(id,code,name,current_stock) VALUES(99,'PRIVATE','Other shop',12)")
    conn.commit();conn.close()
    draft=review(client,invoice)["draft"]
    draft["items"][0].update(match_type="existing",matched_product_id=99)
    assert save(client,invoice,draft).status_code==400
    assert not client.get("/suppliers/invoices/products?q=PRIVATE").json["products"]
    with client.session_transaction() as session:session["_user_id"]="20"
    for path in ("review","review-data","original"):
        assert client.get(f"/suppliers/invoices/{invoice}/{path}",headers={"X-Tenant-ID":"10"}).status_code==404
    assert save(client,invoice,draft).status_code==404
    assert "Supplier / invoice" not in client.get("/suppliers/invoices/").text
    assert [p["id"] for p in client.get("/suppliers/invoices/products").json["products"]]==[99]


def test_file_path_outside_tenant_denied(client,invoice,tmp_path):
    outside=tmp_path/"outside.pdf";outside.write_bytes(PDF)
    conn=get_conn(10)
    conn.execute("UPDATE supplier_invoices SET file_path=?",(str(outside),))
    conn.commit();conn.close()
    assert client.get(f"/suppliers/invoices/{invoice}/original").status_code==404


def test_csrf_required_and_valid_token_accepted(client,app,invoice):
    draft=review(client,invoice)["draft"]
    app.config["WTF_CSRF_ENABLED"]=True
    assert save(client,invoice,draft).status_code==400
    html=client.get(f"/suppliers/invoices/{invoice}/review").text
    token=re.search(r'name="csrf_token" value="([^"]+)"',html).group(1)
    assert client.post(f"/suppliers/invoices/{invoice}/review",json=draft,headers={"X-CSRFToken":token}).status_code==200


def test_unauthenticated_review_and_original_denied(client):
    for path in ("1/review","1/original","products",""):
        assert client.get("/suppliers/invoices/"+path).status_code==302
    assert client.post("/suppliers/invoices/1/review",json={}).status_code==302


def test_processed_or_unextracted_invoice_cannot_be_reviewed(client,invoice):
    draft=review(client,invoice)["draft"]
    conn=get_conn(10)
    conn.execute("UPDATE supplier_invoices SET status='processed'")
    conn.commit();conn.close()
    assert save(client,invoice,draft).status_code==409
    assert client.get(f"/suppliers/invoices/{invoice}/review").status_code==409


def test_search_input_validation(client,invoice):
    assert client.get("/suppliers/invoices/products?tenant_id=20").status_code==400
    assert client.get("/suppliers/invoices/products?q="+"x"*121).status_code==400
    assert client.get("/suppliers/invoices/products?q=a&q=b").status_code==400


def test_migration_23_preserves_existing_extraction_and_is_repeatable(client,invoice):
    before=snapshot()
    conn=get_conn(10)
    conn.execute("DROP TABLE IF EXISTS quotation_events")
    conn.execute("DROP TABLE IF EXISTS quotation_items")
    conn.execute("DROP TABLE IF EXISTS quotations")
    conn.execute("DROP TABLE supplier_invoice_confirmations")
    conn.execute("DROP TABLE supplier_invoice_review_events")
    conn.execute("DROP TABLE supplier_invoice_reviews")
    conn.execute("DELETE FROM schema_migrations WHERE version>=23")
    conn.commit();conn.close()
    for _ in range(2):
        conn=get_conn(10)
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]==LATEST_SCHEMA_VERSION
        conn.close()
    assert snapshot()==before


def test_review_text_cannot_inject_script(client,invoice):
    draft=review(client,invoice)["draft"]
    draft["supplier_name"]="</script><script>alert(1)</script>"
    draft["items"][0]["product_description"]="<img src=x onerror=alert(1)>"
    assert save(client,invoice,draft).status_code==200
    html=client.get(f"/suppliers/invoices/{invoice}/review").text
    assert "</script><script>alert(1)</script>" not in html
    assert "<img src=x onerror=alert(1)>" not in html


def test_private_invoice_paths_bypass_service_worker_cache():
    script=(Path(__file__).parents[1]/"app/static/sw.js").read_text()
    assert script.index("url.pathname.startsWith('/suppliers/invoices')")<script.index("// Only handle GET requests")
    assert "cache: 'no-store'" in script


def test_review_save_rolls_back_if_audit_write_fails(client,invoice):
    draft=review(client,invoice)["draft"]
    conn=get_conn(10)
    conn.execute("CREATE TRIGGER reject_review_audit BEFORE INSERT ON supplier_invoice_review_events BEGIN SELECT RAISE(ABORT,'test audit failure'); END")
    conn.commit();conn.close()
    with pytest.raises(sqlite3.IntegrityError):
        save(client,invoice,draft)
    conn=get_conn(10)
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_reviews").fetchone()[0]==0
    conn.close()


def test_preview_uses_latest_stock_without_overwriting_it(client,invoice):
    draft=review(client,invoice)["draft"]
    draft["items"][0].update(match_type="existing",matched_product_id=11,quantity="4")
    conn=get_conn(10)
    conn.execute("UPDATE products SET current_stock=25 WHERE id=11")
    conn.commit();conn.close()
    result=save(client,invoice,draft)
    assert result.status_code==200
    assert Decimal(result.json["previews"][0]["projected_stock"])==29
    conn=get_conn(10)
    assert conn.execute("SELECT current_stock FROM products WHERE id=11").fetchone()[0]==25
    conn.close()


def test_cancel_and_review_gets_do_not_write_drafts_or_stock(client,invoice):
    before=snapshot()
    client.get(f"/suppliers/invoices/{invoice}/review")
    client.get("/suppliers/invoices/")
    assert snapshot()==before
    conn=get_conn(10)
    assert conn.execute("SELECT COUNT(*) FROM supplier_invoice_reviews").fetchone()[0]==0
    conn.close()
