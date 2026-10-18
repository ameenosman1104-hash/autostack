"""Isolated test-launcher safeguards. All provider calls here are mocked."""
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

spec=importlib.util.spec_from_file_location("local_invoice_trial",Path(__file__).parents[1]/"scripts/local_invoice_trial.py")
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)


@pytest.fixture
def provider(tmp_path,monkeypatch):
    monkeypatch.setenv("AUTOSTACK_DATA_DIR",str(tmp_path))
    from app.services import supplier_invoice_extraction as extraction
    original=extraction.openai.OpenAI
    monkeypatch.setattr(extraction.openai,"OpenAI",original)
    counted=Mock(return_value=SimpleNamespace(input_tokens=1000))
    parsed=Mock(return_value=SimpleNamespace(usage=SimpleNamespace(input_tokens=1000,output_tokens=200)))
    raw=SimpleNamespace(responses=SimpleNamespace(input_tokens=SimpleNamespace(count=counted),parse=parsed),close=Mock())
    def factory(**kwargs):
        assert kwargs["base_url"]=="https://api.openai.com/v1"
        assert kwargs["max_retries"]==0 and kwargs["timeout"]==60
        kwargs["http_client"].close()
        return raw
    budget=trial.Budget(tmp_path)
    trial.install_bounded_client(extraction,"fake-test-key",budget,client_factory=factory)
    client=extraction.openai.OpenAI()
    params={"model":trial.MODEL,"store":False,"max_output_tokens":trial.MAX_OUTPUT,
            "input":[{"role":"user","content":[{"type":"input_text","text":"synthetic"}]}],
            "instructions":"Read synthetic content only","text_format":extraction.InvoiceExtraction}
    yield client,budget,counted,parsed,params
    client.close()


def test_persistent_attempt_and_budget_cap(tmp_path):
    budget=trial.Budget(tmp_path)
    for _ in range(5):
        budget.reserve()
        with sqlite3.connect(budget.path) as conn:conn.execute("UPDATE attempts SET started=0")
    with pytest.raises(trial.TrialError,match="exhausted"):trial.Budget(tmp_path).reserve()
    assert budget.summary()=={"attempts_used":5,"attempt_limit":5,"reserved_usd":0.1,"application_budget_usd":0.1}


def test_rate_limit_and_one_in_flight(provider):
    client,budget,counted,parsed,params=provider
    budget.reserve()
    with pytest.raises(trial.TrialError,match="10 seconds"):client.responses.parse(**params)
    assert not counted.called and not parsed.called
    budget.lock.acquire()
    try:
        with pytest.raises(trial.TrialError,match="already running"):client.responses.parse(**params)
    finally:budget.lock.release()


def test_token_count_before_generation_and_private_ledger(provider):
    client,budget,counted,parsed,params=provider
    client.responses.parse(**params)
    assert counted.call_count==1 and parsed.call_count==1
    with sqlite3.connect(budget.path) as conn:
        row=conn.execute("SELECT outcome,input_tokens,output_tokens,estimated_usd FROM attempts").fetchone()
    assert row[:3]==("completed",1000,200)
    assert row[3]==pytest.approx(0.00045)
    assert b"fake-test-key" not in budget.path.read_bytes()
    schema=counted.call_args.kwargs["text"]["format"]["schema"]
    assert set(schema["required"])==set(schema["properties"])


def test_oversized_input_blocks_generation(provider):
    client,budget,counted,parsed,params=provider
    counted.return_value=SimpleNamespace(input_tokens=20000)
    with pytest.raises(trial.TrialError,match="input-token allowance"):client.responses.parse(**params)
    parsed.assert_not_called()


@pytest.mark.parametrize("change",[{"model":"other-model"},{"max_output_tokens":99999},{"store":True}])
def test_unexpected_request_rejected_without_network(provider,change):
    client,budget,counted,parsed,params=provider
    params.update(change)
    with pytest.raises(trial.TrialError,match="Unexpected"):client.responses.parse(**params)
    counted.assert_not_called();parsed.assert_not_called()
    assert budget.summary()["attempts_used"]==0


def test_provider_error_is_redacted_and_counts_attempt(provider,capsys):
    client,budget,counted,parsed,params=provider
    parsed.side_effect=RuntimeError("PRIVATE SUPPLIER invoice secret key")
    with pytest.raises(trial.TrialError) as caught:client.responses.parse(**params)
    assert "PRIVATE" not in str(caught.value) and "secret key" not in str(caught.value)
    captured=capsys.readouterr()
    assert "PRIVATE" not in captured.out+captured.err
    assert budget.summary()["attempts_used"]==1


def test_unmarked_or_project_directory_refused(tmp_path):
    existing=tmp_path/"existing";existing.mkdir();(existing/"untouched.txt").write_text("keep")
    with pytest.raises(trial.TrialError,match="existing directory"):trial.configure_isolation(existing)
    assert (existing/"untouched.txt").read_text()=="keep"
    with pytest.raises(trial.TrialError,match="outside the project"):trial.configure_isolation(trial.PROJECT/"data")


def test_isolated_seed_review_only_and_csrf(tmp_path,monkeypatch):
    root=tmp_path/"trial"
    monkeypatch.setenv("AUTOSTACK_DATA_DIR",str(root))
    monkeypatch.setenv("AUTOSTACK_ENV","test")
    for key in ("AUTOSTACK_DATA_DIR","AUTOSTACK_ENV","AUTOSTACK_INVOICE_DIR","AUTOSTACK_INVOICE_MODEL","RENDER","SECRET_KEY"):
        monkeypatch.setenv(key,__import__('os').environ.get(key,""))
    trial.configure_isolation(root)
    import app.main_db as main_db
    monkeypatch.setattr(main_db,"MAIN_DB",str(root/"main.db"))
    from app.services import auto_sync
    monkeypatch.setattr(auto_sync,"start_background_sync",lambda app:None)
    from app.services import supplier_invoice_extraction as extraction
    monkeypatch.setattr(extraction.openai,"OpenAI",extraction.openai.OpenAI)
    logging_level=logging.root.manager.disable
    try:
        app,budget=trial.prepare_app(root)
        assert app.config["WTF_CSRF_ENABLED"] and not app.debug
        conn=sqlite3.connect(root/"tenants/10.db")
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]==4
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]==0
        assert conn.execute("SELECT SUM(current_stock) FROM products").fetchone()[0]==28
        conn.close()
        client=app.test_client()
        with client.session_transaction() as session:session["_user_id"]="10"
        for suffix in ("confirm",):
            assert client.get(f"/suppliers/invoices/1/{suffix}").status_code==403
        assert client.get("/api/ai/stock-insights").status_code==403
        assert client.get("/suppliers/invoices/new").status_code==200
        assert "API preflight pending" in client.get("/suppliers/invoices/new").text
        assert client.post("/suppliers/invoices/1/extract").status_code==400
        import re
        csrf=re.search(r'name="csrf_token" value="([^"]+)"',client.get("/suppliers/invoices/new").text).group(1)
        assert client.post("/suppliers/invoices/1/confirm",json={},headers={"X-CSRFToken":csrf}).status_code==403
        assert client.post("/suppliers/invoices/1/extract",headers={"X-CSRFToken":csrf}).status_code==503
        assert budget.summary()["attempts_used"]==0
    finally:logging.disable(logging_level)


def test_synthetic_probe_is_explicit_and_cached_without_key_disclosure(tmp_path,monkeypatch):
    monkeypatch.setattr(trial.time,"sleep",lambda value:None)
    parsed=SimpleNamespace(invoice_number="SYNTH-001",invoice_total=230,items=[SimpleNamespace(quantity=2)])
    call=Mock(return_value=(parsed,trial.MODEL))
    extraction=SimpleNamespace(_call_ai=call)
    report=trial.synthetic_preflight(extraction,tmp_path,"fake-test-key")
    assert report["passed"] and call.call_count==2
    assert [check["format"] for check in report["checks"]]==["PNG","PDF"]
    assert "fake-test-key" not in json.dumps(report)
    assert "key_sha256" not in report
    assert trial.synthetic_preflight(extraction,tmp_path,"fake-test-key")["passed"]
    assert call.call_count==2


def test_failed_probe_never_claims_success(tmp_path):
    extraction=SimpleNamespace(_call_ai=Mock(side_effect=trial.TrialError("Synthetic check blocked")))
    with pytest.raises(trial.TrialError):trial.synthetic_preflight(extraction,tmp_path,"fake-test-key")
    assert not (tmp_path/"api_preflight.json").exists()
