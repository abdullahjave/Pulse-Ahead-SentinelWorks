"""Serverless/hosted deployment behaviour: stateless signed sessions, read-only demo mode, disabled back-test
rerun, role scoping preserved, and the packaged static layout."""
import json
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel import auth, config as C, store
from sentinel.api import app

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(app)
PHO, HSE, ANA = "pho-north", "hse-emp-a", "policy-analyst"
Q = "scenario=heatwave&as_of=2026-07-28"


@pytest.fixture
def hosted(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SENTINEL_SESSION_SECRET", "x" * 32)
    for k in ("SENTINEL_DB_PATH", "SENTINEL_READ_ONLY", "SENTINEL_VALIDATION_RERUN"):
        monkeypatch.delenv(k, raising=False)
    def no_sqlite(*a, **k):
        raise AssertionError("hosted read-only mode must not open SQLite")
    monkeypatch.setattr(store.sqlite3, "connect", no_sqlite)
    return tmp_path


def H(user):
    r = client.post("/api/auth/login", json={"user_id": user})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_tokens_are_stateless_across_instances(hosted, monkeypatch):
    tok = auth.issue_token(HSE)
    monkeypatch.setattr(auth, "_EPHEMERAL_SECRET", b"a different process")  # a fresh instance shares nothing but the env secret
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/auth/me", headers=h).json()["role"] == "hse_lead"


def test_token_tamper_expiry_and_wrong_secret_rejected(hosted, monkeypatch):
    tok = auth.issue_token(ANA)
    body, sig = tok.split(".")
    bad = [f"{body}.{sig[:-2]}AA", f"{auth._b64(json.dumps({'u': PHO, 'exp': int(time.time()) + 999}).encode())}.{sig}", "garbage", ""]
    for t in bad:
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401
    monkeypatch.setattr(auth, "SESSION_TTL_SECONDS", -1)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {auth.issue_token(ANA)}"}).status_code == 401
    monkeypatch.setattr(auth, "SESSION_TTL_SECONDS", 3600)
    monkeypatch.setenv("SENTINEL_SESSION_SECRET", "y" * 32)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_hosted_without_secret_fails_closed(hosted, monkeypatch):
    monkeypatch.delenv("SENTINEL_SESSION_SECRET")
    assert client.post("/api/auth/login", json={"user_id": PHO}).status_code == 503
    assert client.get("/api/health").json()["session_configured"] is False


def test_role_is_never_taken_from_client(hosted):
    h = H(ANA)
    assert client.get("/api/auth/me", headers={**h, "X-Role": "public_health_officer"}).json()["role"] == "policy_analyst"
    assert client.get("/api/export?kind=sites", headers=h).status_code == 403


def test_read_only_mode_incident_detail_and_no_db_touch(hosted):
    h = H(PHO)
    incs = client.get(f"/api/incidents?{Q}", headers=h).json()["incidents"]
    assert incs and client.get(f"/api/overview?{Q}", headers=h).status_code == 200
    d = client.get(f"/api/incidents/{incs[0]['id']}?{Q}", headers=h)
    assert d.status_code == 200
    j = d.json()
    assert j["recording_enabled"] is False and j["decisions"] == [] and "read-only" in j["recording_unavailable_reason"]
    body = {"scenario": "heatwave", "as_of": "2026-07-28", "status": "acknowledged"}
    r = client.post(f"/api/incidents/{incs[0]['id']}/decision", json=body, headers=h)
    assert r.status_code == 503 and "read-only" in r.json()["detail"]
    assert client.post("/api/demo/reset", headers=h).status_code == 503
    assert client.post("/api/demo/reset", headers=H(ANA)).status_code == 403  # role check still comes first
    assert client.get("/api/meta", headers=h).json()["capabilities"]["recording"] is False


def test_hosted_incident_role_scoping_preserved(hosted):
    pho, hse = H(PHO), H(HSE)
    all_ids = {i["id"]: i["zone"]["id"] for i in client.get(f"/api/incidents?{Q}", headers=pho).json()["incidents"]}
    mine = {i["id"] for i in client.get(f"/api/incidents?{Q}", headers=hse).json()["incidents"]}
    scope = set(client.get("/api/auth/me", headers=hse).json()["zones_in_scope"])
    assert mine <= set(all_ids) and {all_ids[i] for i in mine} <= scope
    outside = [i for i, z in all_ids.items() if z not in scope]
    assert outside, "scenario should have incidents outside the HSE scope"
    assert client.get(f"/api/incidents/{outside[0]}?{Q}", headers=hse).status_code == 403
    assert client.get("/api/export?kind=zone_daily", headers=hse).status_code == 403
    csv_text = client.get(f"/api/export?kind=sites&{Q}", headers=hse).text
    assert "EMP_A" in csv_text and "EMP_B" not in csv_text
    assert client.get(f"/api/export?kind=sites&{Q}", headers=H(ANA)).status_code == 403


def test_validation_precomputed_and_rerun_disabled_hosted(hosted):
    h = H(PHO)
    v = client.get("/api/validation", headers=h)
    assert v.status_code == 200
    j = v.json()
    assert j["rerun"]["available"] is False and "hosted demo" in j["rerun"]["reason"] and j["holdout"]["conditions"]["standard"]
    r = client.post("/api/validation/rerun", headers=h)
    assert r.status_code == 503
    assert client.post("/api/validation/rerun", headers=H(ANA)).status_code == 403


def test_local_mode_unchanged(monkeypatch, tmp_path):
    for k in ("VERCEL", "SENTINEL_HOSTED", "SENTINEL_READ_ONLY", "SENTINEL_VALIDATION_RERUN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "s.db"))
    assert store.recording_enabled() and store.decisions("k") == [] and not (tmp_path / "s.db").exists()
    store.add_decision("k", "I1", "acknowledged", "n", "hse_lead", "u", "2026-07-28")
    assert store.decisions("k", "I1")[0]["note"] == "n"
    assert store.reset() == 1
    assert client.get("/api/meta", headers=H(PHO)).json()["capabilities"]["validation_rerun"] is True


def test_packaged_layout_static_assets_and_data():
    for d in (ROOT / "public", ROOT / "frontend" / "dist"):
        html = (d / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="(/[^"]+)"', html)
        assert any(r.startswith("/assets/") for r in refs)
        for r in refs:
            assert (d / r.lstrip("/")).is_file(), f"{d}: {r} missing"
    assert (ROOT / "public" / "index.html").read_text(encoding="utf-8") == (ROOT / "frontend" / "dist" / "index.html").read_text(encoding="utf-8")
    entry = (ROOT / "app.py").read_text()
    assert "from sentinel.api import app" in entry
    assert json.loads((ROOT / "backend" / "data" / "validation_results.json").read_text())["params"] == C.detection_params()
    assert {"fastapi", "numpy", "pydantic"} <= {l.split(">")[0].strip() for l in (ROOT / "requirements.txt").read_text().splitlines() if l and not l.startswith("#")}
    vj = json.loads((ROOT / "vercel.json").read_text())
    assert "app.py" in vj["functions"]


def test_entrypoint_imports_as_vercel_would():
    import subprocess
    r = subprocess.run([sys.executable, "-c", "import app; print(app.app.title)"], cwd=ROOT, capture_output=True, text=True,
                       env={**__import__('os').environ, "PYTHONPATH": ""})
    assert r.returncode == 0 and "SentinelWorks" in r.stdout, r.stderr
