"""Tests for the web interface: auth, chat, glossary box, ratings, API routes."""

import pytest

flask = pytest.importorskip("flask", reason="flask not installed")

from claim_assistant import web  # noqa: E402
from claim_assistant.config import Config  # noqa: E402
from claim_assistant.web import create_app  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    """App whose user/rating files live in tmp - never touches demo data."""
    monkeypatch.setattr(web, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(web, "_RATINGS_FILE", tmp_path / "ratings.json")
    monkeypatch.setattr(web, "_SECRET_FILE", tmp_path / "secret.txt")
    cfg = Config()
    cfg.provider = "rule"
    app = create_app(cfg)
    app.testing = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _guest(client):
    client.post("/guest")
    return client


# ------------------------------------------------------------------ pages --
def test_index_redirects_when_signed_out(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")


def test_index_renders_for_guest(client):
    _guest(client)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Insurance Claim Explanation Assistant" in html
    assert "CLM-1001" in html
    # template hooks for the fixed feedback items
    assert ".hints code" in html          # suggestion chips are wired up
    assert "gterm" in html                # clickable glossary terms
    assert "/api/glossary" in html        # glossary box data source
    assert "Was this clear?" in html      # clarity rating widget
    assert "Log out" in html


def test_login_page_and_guest_button(client):
    html = client.get("/login").get_data(as_text=True)
    assert "Welcome back" in html
    assert 'action="/guest"' in html
    assert "/signup" in html


# --------------------------------------------------------------- sign-up --
def _signup(client, name="Priya Tester", email="priya@example.com",
            password="secret123", style="simple", policy_no="POL-H-77821",
            confirm=None):
    if confirm is None:
        confirm = password
    return client.post("/signup", data={
        "name": name, "email": email, "phone": "9876543210",
        "policy_no": policy_no, "style": style,
        "password": password, "confirm": confirm})


def test_signup_signs_in_and_greets(client):
    resp = _signup(client)
    assert resp.status_code == 302
    page = client.get("/").get_data(as_text=True)
    assert "Hi, Priya" in page
    assert "Log out" in page


def test_signup_validation_errors(client):
    assert "required" in _signup(client, name="", email="",
                                 password="").get_data(as_text=True)
    assert "valid email" in _signup(client, email="nope").get_data(as_text=True)
    assert "do not match" in _signup(client, password="abcd",
                                     confirm="abce").get_data(as_text=True)
    # duplicate email
    _signup(client)
    client.post("/logout")
    assert "already exists" in _signup(client).get_data(as_text=True)


def test_login_wrong_password_then_correct(client):
    _signup(client)
    client.post("/logout")
    bad = client.post("/login", data={"email": "priya@example.com",
                                      "password": "wrong"})
    assert "incorrect" in bad.get_data(as_text=True)
    good = client.post("/login", data={"email": "priya@example.com",
                                       "password": "secret123"})
    assert good.status_code == 302
    assert "Hi, Priya" in client.get("/").get_data(as_text=True)


def test_logout_to_login_page(client):
    _signup(client)
    client.post("/logout")
    assert client.get("/").status_code == 302
    assert "Welcome back" in client.get("/login").get_data(as_text=True)


def test_password_never_stored_plaintext(client, tmp_path, monkeypatch):
    users_file = tmp_path / "users.json"
    monkeypatch.setattr(web, "_USERS_FILE", users_file)
    _signup(client, password="hunter2000")
    stored = users_file.read_text(encoding="utf-8")
    assert "hunter2000" not in stored
    import json
    record = json.loads(stored)["priya@example.com"]
    assert record["password"] != "hunter2000" and len(record["password"]) == 64
    assert record["salt"]


# ---------------------------------------------------------- personalisation --
def test_chat_uses_signed_in_profile(client):
    _signup(client, name="Priya Tester", style="simple")
    resp = client.post("/api/chat", json={"message": "Explain claim CLM-1001"})
    data = resp.get_json()
    assert data["reply"].startswith("Hi Priya,")
    assert "Ananya" not in data["reply"]          # claimant still anonymised


def test_chat_detailed_style_adds_timeline(client):
    _signup(client, name="Rahul Verma", style="detailed")
    data = client.post("/api/chat",
                       json={"message": "Explain claim CLM-1005"}).get_json()
    assert data["reply"].startswith("Hi Rahul,")
    assert "**Recent updates**" in data["reply"]  # style preference honoured


def test_guest_gets_no_personalisation(client):
    _guest(client)
    data = client.post("/api/chat", json={"message": "Explain claim CLM-1001"}).get_json()
    assert not data["reply"].startswith("Hi ")


# ------------------------------------------------------------- glossary --
def test_glossary_endpoint(client):
    data = client.get("/api/glossary").get_json()
    terms = data["terms"]
    assert len(terms) >= 20
    ded = next(t for t in terms if t["term"] == "deductible")
    assert ded["definition"] and ded["plain"] and ded["example"]


# --------------------------------------------------------------- ratings --
def test_ratings_flow(client):
    assert client.get("/api/ratings").get_json() == {
        "yes": 0, "no": 0, "total": 0, "percent": None}
    first = client.post("/api/ratings", json={"helpful": True}).get_json()
    assert first == {"yes": 1, "no": 0, "total": 1, "percent": 100}
    client.post("/api/ratings", json={"helpful": False})
    tally = client.get("/api/ratings").get_json()
    assert tally["total"] == 2 and tally["percent"] == 50
    assert client.post("/api/ratings", json={}).status_code == 400


# ------------------------------------------------------------------ chat --
def test_chat_endpoint_explains_claim(client):
    resp = client.post("/api/chat", json={"message": "Explain claim CLM-1006"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["claim_id"] == "CLM-1006"
    assert data["session_id"]
    assert "**Next steps**" in data["reply"]
    assert data["claim_view"]["status_label"] == "Payment processing"


def test_chat_session_continuity(client):
    r1 = client.post("/api/chat", json={"message": "What's up with CLM-1003?"})
    sid = r1.get_json()["session_id"]
    r2 = client.post("/api/chat", json={"message": "and what does deductible mean?",
                                        "session_id": sid})
    assert r2.get_json()["claim_id"] == "CLM-1003"


def test_chat_handles_garbage_and_empty(client):
    assert client.post("/api/chat", json={}).get_json()["reply"]
    assert client.post("/api/chat", json={"message": 12345}).get_json()["reply"]
    assert client.post("/api/chat", data="not json",
                       content_type="text/plain").status_code == 200


def test_api_routes_mounted_on_same_app(client):
    assert client.get("/api/health").get_json()["ok"] is True
    assert len(client.get("/api/claims").get_json()["claims"]) == 8
    assert client.get("/api/claims/CLM-1004/status").status_code == 200
    assert client.get("/api/claims/NOPE").status_code == 404
