"""Startup gate: no requests served and no databases created until verified restoration is marked."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = """
import io, json, os, sys
sys.path.insert(0, os.getcwd())
import run
env = {}
out = {}
def start_response(status, headers):
    out["status"] = status
body = b"".join(run.app({"REQUEST_METHOD": "GET", "PATH_INFO": "/", "SERVER_NAME": "x",
                         "SERVER_PORT": "80", "wsgi.url_scheme": "http", "wsgi.input": io.BytesIO(),
                         "wsgi.errors": sys.stderr, "QUERY_STRING": "", "SERVER_PROTOCOL": "HTTP/1.1"},
                        start_response))
print(json.dumps({"status": out["status"], "body": body.decode()}))
"""


def run_probe(data_dir, gate, marker):
    env = {**os.environ, "AUTOSTACK_DATA_DIR": str(data_dir), "AUTOSTACK_ENV": "test"}
    env.pop("AUTOSTACK_REQUIRE_RESTORED_DATA", None)
    if gate:
        env["AUTOSTACK_REQUIRE_RESTORED_DATA"] = "1"
    if marker:
        (Path(data_dir) / ".restore_verified").write_text("verified")
    return subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


def test_gate_closed_returns_503_and_creates_no_database(tmp_path):
    result = run_probe(tmp_path, gate=True, marker=False)
    assert result.returncode == 0, result.stderr
    assert '"503 Service Unavailable"' in result.stdout
    assert "paused until verified database restoration" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_gate_open_with_marker_starts_application(tmp_path):
    result = run_probe(tmp_path, gate=True, marker=True)
    assert result.returncode == 0, result.stderr
    assert "503" not in result.stdout
    assert (tmp_path / "main.db").exists()


def test_gate_disabled_keeps_normal_startup(tmp_path):
    result = run_probe(tmp_path, gate=False, marker=False)
    assert result.returncode == 0, result.stderr
    assert "503" not in result.stdout
    assert (tmp_path / "main.db").exists()
