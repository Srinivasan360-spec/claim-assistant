"""Tests for the simulated claim status API (store, live progression, HTTP)."""

from datetime import datetime, timedelta

from claim_assistant.mock_api import ClaimStore, create_api_blueprint

FMT = "%Y-%m-%dT%H:%M:%S"


def test_store_loads_all_claims():
    store = ClaimStore()
    assert len(store.claims) == 8
    assert store.get_claim("CLM-1001")["policy_type"] == "health"
    assert store.get_claim("clm-1001")  # case-insensitive
    assert store.get_claim("CLM-9999") is None
    ids = [c["claim_id"] for c in store.list_claims()]
    assert ids == sorted(ids)
    assert "CLM-1008" in ids


def test_live_status_advances_with_time():
    store = ClaimStore()
    claim = store.get_claim("CLM-1003")  # submitted 2026-10-08, stage 24h, sim on
    submitted = datetime.strptime(claim["submitted_at"], FMT)

    early = store.live_status(claim, now=submitted + timedelta(hours=1))
    late = store.live_status(claim, now=submitted + timedelta(hours=24 * 4))
    assert early["code"] == "SUBMITTED"
    assert late["code"] != early["code"]          # real-time progression
    stages = claim["progression"]
    assert stages.index(late["code"]) > stages.index(early["code"])
    # clamped at the end of the progression, never beyond
    far = store.live_status(claim, now=submitted + timedelta(hours=24 * 99))
    assert far["code"] == stages[-1]
    assert far["simulated"] is True


def test_live_status_static_claim_never_moves():
    store = ClaimStore()
    claim = store.get_claim("CLM-1002")  # simulate_progression = false
    now = datetime.strptime(claim["submitted_at"], FMT) + timedelta(days=30)
    assert store.live_status(claim, now=now)["code"] == "DOCS_PENDING"


def test_get_status_payload_shape():
    store = ClaimStore()
    st = store.get_status("CLM-1001")
    assert st["claim_id"] == "CLM-1001"
    assert st["code"] and st["label"] and st["meaning"]
    assert "as_of" in st and "simulated" in st
    assert store.get_status("CLM-0000") is None


def test_find_by_policy():
    store = ClaimStore()
    hit = store.find_by_policy("POL-H-77821")
    assert hit and hit["claim_id"] == "CLM-1001"
    assert store.find_by_policy("POL-Z-00000") is None


def test_http_endpoints_via_flask_client():
    flask = __import__("pytest").importorskip("flask")
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(create_api_blueprint())

    client = app.test_client()
    assert client.get("/api/health").get_json()["ok"] is True

    data = client.get("/api/claims").get_json()
    assert len(data["claims"]) == 8

    resp = client.get("/api/claims/CLM-1005")
    assert resp.status_code == 200
    claim = resp.get_json()["claim"]
    assert claim["anonymized"] is True              # PII masked at the edge
    assert claim["claimant"]["name"] == "P. N***"   # 'Priya Nair' masked
    assert "Priya" not in str(claim)

    missing = client.get("/api/claims/CLM-9999")
    assert missing.status_code == 404
    assert missing.get_json()["error"] == "claim_not_found"

    status = client.get("/api/claims/CLM-1001/status")
    assert status.status_code == 200
    assert status.get_json()["code"] in (
        "SUBMITTED", "DOCS_PENDING", "UNDER_REVIEW", "ADJUDICATION",
        "APPROVED", "PAYMENT_PROCESSING", "CLOSED")


def test_csv_store_matches_json_and_simulates():
    """Statement requires claim data as 'JSON or CSV' - both must work."""
    from claim_assistant.config import DATA_DIR

    csv_store = ClaimStore(DATA_DIR / "claims.csv")
    json_store = ClaimStore()
    assert set(csv_store.claims) == set(json_store.claims)
    assert len(csv_store.claims) == 8

    c = csv_store.get_claim("CLM-1005")
    assert c["amounts"]["claimed"] == 95000.0
    assert c["amounts"]["approved"] == 78250.0
    assert c["simulate_progression"] is False
    assert c["progression"][0] == "SUBMITTED"
    assert c["claimant"]["name"] == "Priya Nair"  # anonymized at the edge

    # time-driven progression works from CSV too
    claim = csv_store.get_claim("CLM-1003")
    submitted = datetime.strptime(claim["submitted_at"], FMT)
    assert csv_store.live_status(claim, now=submitted)["code"] == "SUBMITTED"
    later = csv_store.live_status(claim, now=submitted + timedelta(hours=100))
    assert later["code"] == "APPROVED"

    st = csv_store.get_status("CLM-1001")
    assert st["code"] in ("UNDER_REVIEW", "ADJUDICATION", "APPROVED",
                          "PAYMENT_PROCESSING", "CLOSED")
    assert csv_store.get_status("CLM-0000") is None
