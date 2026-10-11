"""Web interface (deliverable: "basic web interface or CLI").

Flask app serving from one port:
  * /                 chat UI (redirects to /login when signed out)
  * /signup /login /logout   local demo auth with profile capture
  * POST /api/chat    conversational endpoint (session-scoped, profile-aware)
  * GET  /api/glossary  all glossary terms for the in-UI glossary box
  * GET/POST /api/ratings  "was this clear?" tally for the 85% success metric
  * /api/claims...    the simulated status API (same process = no setup)

Auth is demo-grade and local: users live in data/users.json with per-user
salted SHA-256 password hashes (never plaintext). Guest mode works without
an account. Personalisation: the signed-in profile (name, style, policy)
flows into every generated explanation.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session as flask_session, stream_with_context,
                   url_for)

from .agent import (CLAIM_ID_RE, ClaimAgent, _is_small_talk,
                    anonymized_view_for, sanitize)
from .config import Config, DATA_DIR, TEMPLATES_DIR
from .llm import rule_based_explanation
from .mock_api import ClaimStore, create_api_blueprint
from .preprocess import extract_key_params, format_history, map_status
from .prompting import SYSTEM_PROMPT, build_user_prompt
from .retrieval import Glossary, PolicyDocs, build_context
from .streaming import ollama_token_stream, sse_event

_SESSIONS: dict = {}
_USERS_FILE = DATA_DIR / "users.json"
_RATINGS_FILE = DATA_DIR / "ratings.json"
_SECRET_FILE = DATA_DIR / ".flask_secret"


def _load_json_file(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save_json_file(path, data) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    except OSError:
        pass  # demo must not crash on a read-only data dir


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def _tally() -> dict:
    data = _load_json_file(_RATINGS_FILE, {"yes": 0, "no": 0})
    try:
        yes, no = int(data.get("yes", 0)), int(data.get("no", 0))
    except (TypeError, ValueError):
        yes = no = 0
    total = yes + no
    percent = round(100.0 * yes / total) if total else None
    return {"yes": yes, "no": no, "total": total, "percent": percent}


def _free_fast_answer(glossary_terms: list, passages: list,
                      question: str) -> str:
    """Instant no-claim answer shaped by the actual question text (used only
    seconds while the local AI model cold-loads in the background)."""
    parts = [f'**Your question:** "{question}"']
    if glossary_terms:
        out = ["", "**In plain terms**"]
        for g in glossary_terms[:3]:
            out.append(f"- {g['term']}: {g.get('plain') or g.get('definition', '')}")
        if len(glossary_terms) > 3:
            out.append(f"(- and {len(glossary_terms) - 3} more covered terms - ask "
                       "me about any of them by name)")
        parts.extend(out)
    if passages:
        parts.append("")
        parts.append("**From the insurer's practice guidelines**")
        for p in passages[:2]:
            parts.append(f"- [{p['doc']}] {p['text'][:220]}")
    parts.append("")
    parts.append("The full AI answer is loading right now - ask me again in a "
                 "few seconds and I will give you the personalised reply. "
                 "Or name a claim (e.g. CLM-1001) to connect this to real "
                 "amounts and stages.")
    return "\n".join(parts)


def _warm_background(base_url: str, model: str, keep_alive: str) -> None:
    """Best-effort model load so Ollama has the weights resident; never raises."""
    import threading
    import urllib.request

    def job() -> None:
        try:
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/generate",
                data=b'{"model": ' + json.dumps(model).encode()
                     + b', "prompt": "", "keep_alive": '
                     + json.dumps(keep_alive).encode() + b'}',
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=300).read()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=job, daemon=True).start()


def _fast_chat_events(agent: ClaimAgent, policy: PolicyDocs, cfg: Config,
                      message: str, chat_session: dict, profile: dict | None):
    """Yield SSE frames for one fast chat turn. Committing session history is
    the exact parity job done at the end of each successful path."""
    def commit(final_text: str, provider: str, note: str,
               claim_view: dict | None, claim_id: str | None, api):
        chat_session["history"] = (list(chat_session.get("history", []))
                                   + [("user", message),
                                      ("assistant", sanitize(final_text))])
        if claim_id:
            chat_session["claim_id"] = claim_id
        return {"reply": sanitize(final_text),
                "claim_id": claim_id or chat_session.get("claim_id"),
                "claim_view": claim_view,
                "provider": provider, "note": note, "api": api}

    def finish(reply_dict, provider, note):
        yield sse_event({"t": "done", "provider": provider, "note": note,
                         **{k: v for k, v in reply_dict.items()
                            if k in ("claim_id", "claim_view", "api")}})

    def preset(text: str, provider: str, note: str,
               claim_view: dict | None = None, claim_id: str | None = None,
               session_commit: bool = False):
        final = (commit(text, provider, note, claim_view, claim_id,
                        agent.source.name) if session_commit
                 else {"reply": sanitize(text),
                       "claim_id": claim_id or chat_session.get("claim_id"),
                       "claim_view": claim_view, "provider": provider,
                       "note": note, "api": None})
        yield sse_event({"t": "rule", "text": text})
        yield from finish(final, provider, note)

    # ----- resolve what kind of turn this is (mirrors agent.handle)  ------
    if not message:
        yield from preset("I can explain any claim in plain language. "
                          "Ask me about a claim ID like CLM-1001, or ask what "
                          "an insurance term means.",
                          "retrieval", "help text - no LLM needed")
        return
    if _is_small_talk(message):
        r = agent._small_talk(message, chat_session)
        yield from preset(r["reply"], "retrieval", r["note"])
        return

    mention = CLAIM_ID_RE.search(message)
    fetched = None
    try:
        if mention:
            fetched = agent._fetch(mention.group(1).upper())
        if fetched is None and chat_session.get("claim_id"):
            fetched = agent._fetch(chat_session["claim_id"])
    except Exception:  # noqa: BLE001
        fetched = None

    if mention and fetched is None:
        known = ", ".join(c["claim_id"] for c in agent.list_claims()[:3])
        yield from preset(
            f"I could not find claim {mention.group(1).upper()} in the demo "
            f"data. Try one of these: {known} - or type /claims for the full list.",
            "retrieval", "claim not found in demo data")
        return

    if fetched:
        claim, status = fetched
        claim_id = claim["claim_id"]
        view = anonymized_view_for(claim, status)
        params = extract_key_params(claim, status)
        history_lines = format_history(claim)
        retrieval = build_context(message or "explain this claim", claim,
                                  agent.glossary, policy)
        user_prompt = build_user_prompt(
            params, status, history_lines, retrieval,
            message or "Explain this claim and its next steps.",
            [(r, c[:300]) for r, c in chat_session["history"][-4:]],
            profile=profile)
        fast_text = None
        fast_meta = None
    else:
        hit = agent.glossary.search(message)
        # Deterministic glossary answer ONLY when the question is a short,
        # single-term lookup ("what is a sub-limit?"). Anything richer -
        # discharge costs, claim decisions, procedures - deserves the real
        # AI reasoning over the practice docs, not a canned definition.
        questionish = (any(w in message.lower() for w in
                           ("what is", "meaning of", "define", "explain the term",
                            "difference between"))
                       or (hit and len(message.split()) <= 6
                           and message.strip().endswith("?")))
        if hit and questionish:
            yield from preset(
                f"**{hit['term'].title()}** - {hit['definition']}\n\n"
                f"In plain words: {hit['plain']}\n\n"
                f"Example: {hit['example']}\n\n"
                "Applied to your situation? Name the claim (for example: "
                "Explain claim CLM-1001) and I will connect the term to "
                "real amounts and stages.",
                "retrieval", "answered from glossary - no LLM call needed")
            return
        # Glossary term mentioned mid-question still gets included as a hint
        # for the AI prompt via retrieval below.
        retrieval = build_context(message, None, agent.glossary, policy)
        params = {"claim_id": None, "coverage_type": None,
                  "product": "general insurance (no specific claim selected)",
                  "claimed_amount": "n/a", "approved_amount": "not decided yet",
                  "deductible": "not decided yet", "co_pay_percent": None,
                  "currency": "INR", "status": "not applicable",
                  "status_code": None,
                  "status_explained": "The customer asked a general insurance "
                                      "question; no claim is attached to it.",
                  "next_action_who": "customer", "next_action_what": "n/a",
                  "next_action_due": None, "estimated_days_left": None,
                  "updated_at": None, "settlement_note": None}
        status = {"meaning": "General insurance question - no claim stage is "
                             "attached.", "who_acts": "customer",
                  "next_steps": []}
        user_prompt = build_user_prompt(
            params, status, [], retrieval, message,
            [(r, c[:300]) for r, c in chat_session["history"][-4:]],
            profile=profile)
        claim_id = None
        view = None
        fast_text = _free_fast_answer(retrieval.get("glossary") or [],
                                      retrieval.get("policy_passages") or [],
                                      message)
        fast_meta = None
    # ----------------------------------------------------------------------
    # Ship the instant deterministic answer FIRST - the user always has text
    # on screen in well under a second. Then try to upgrade in place with the
    # AI: a background worker streams Ollama while the pump enforces a HARD
    # time budget - if the first AI token does not arrive in time (cold
    # model load takes ~5-20 s), we stop waiting, keep the instant answer,
    # and trigger a quiet background load so the NEXT question gets AI text.
    # Once tokens ARE flowing (warm model ~1-2 s), the pump waits until the
    # end so the answer is never cut mid-sentence.
    # ----------------------------------------------------------------------
    import queue as _queue
    import time as _time

    quick = fast_text or rule_based_explanation(user_prompt)
    api_name = agent.source.name
    keep_alive = getattr(cfg, "ollama_keep_alive", "10m")
    first_token_budget_s = 6.0

    yield sse_event({"t": "rule", "text": quick})

    q: _queue.Queue = _queue.Queue()
    _FINISH = object()

    def pump() -> None:
        try:
            for kind, chunk in ollama_token_stream(
                    cfg.ollama_base_url, cfg.ollama_model, keep_alive,
                    SYSTEM_PROMPT, user_prompt,
                    num_predict=getattr(cfg, "llm_max_tokens", 350)):
                q.put((kind, chunk))
        except Exception as exc:  # noqa: BLE001 - pump must never die noisy
            q.put(("error", f"stream pump: {exc}"))
        finally:
            q.put((_FINISH, None))

    import threading as _threading
    _threading.Thread(target=pump, daemon=True).start()

    deadline = _time.monotonic() + first_token_budget_s
    acc: list = []
    errored: str | None = None
    seen_first_token = False
    streaming = False
    while True:
        remaining = deadline - _time.monotonic()
        if not streaming and remaining <= 0:
            break                      # budget blown - keep instant answer
        try:
            kind, chunk = q.get(timeout=remaining if remaining > 0 else 0.05)
            if kind is _FINISH:
                break
            if kind == "token":
                if not seen_first_token:
                    seen_first_token = True
                    streaming = True
                    yield sse_event({"t": "status",
                                     "text": "AI answer streaming..."})
                acc.append(chunk)
                yield sse_event({"t": "token", "text": chunk})
            elif kind == "error":
                errored = str(chunk)
                break
        except _queue.Empty:
            if not streaming:
                break                  # nothing within budget
                # (streaming case: queue.Empty just means between tokens)

    text = "".join(acc).strip()
    if text:
        note = (f"model={cfg.ollama_model}"
                + (f" (stream cut: {errored})" if errored else ""))
        final = commit(text, "ollama", note, view, claim_id, api_name)
        yield from finish(final, "ollama", note)
        return

    if not seen_first_token:
        _warm_background(cfg.ollama_base_url, cfg.ollama_model, keep_alive)
    provider = "rule-based"
    note = (f"instant answer - {errored or cfg.ollama_model + ' still loading'}; "
            "next questions get the full AI reply")
    final = (commit(quick, provider, note, view, claim_id, api_name)
             if fetched else
             {"reply": sanitize(quick), "claim_id": None,
              "claim_view": None, "provider": provider, "note": note,
              "api": None})
    yield from finish(final, provider, note)


def _secret_key() -> str:
    """Persisted so login survives server restarts during a demo."""
    try:
        existing = _SECRET_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
        token = secrets.token_hex(16)
        _SECRET_FILE.write_text(token, encoding="utf-8")
        return token
    except OSError:
        return secrets.token_hex(16)


def create_app(cfg: Config = None) -> Flask:
    cfg = cfg or Config()
    app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
    app.config["JSON_AS_ASCII"] = False
    app.secret_key = _secret_key()

    agent = ClaimAgent(cfg)
    store = ClaimStore()
    glossary = Glossary()
    app.register_blueprint(create_api_blueprint(store))
    policy_docs = PolicyDocs()

    def current_profile():
        """Signed-in user's profile for personalisation (guest -> None)."""
        email = flask_session.get("user")
        if not email:
            return None
        user = _load_json_file(_USERS_FILE, {}).get(email)
        if not user:
            return None
        style = user.get("style", "simple")
        return {"name": user.get("name") or "Customer",
                "style": style if style in ("simple", "detailed") else "simple",
                "policy_no": user.get("policy_no", "")}

    def signed_in() -> bool:
        return bool(flask_session.get("user") or flask_session.get("guest"))

    # ------------------------------------------------------------- pages --
    @app.get("/")
    def index():
        if not signed_in():
            return redirect(url_for("login"))
        profile = current_profile()
        tally = _tally()
        return render_template(
            "index.html", claims=agent.list_claims(), config=cfg.describe(),
            profile_name=(profile or {}).get("name"),
            rating_pct=tally["percent"], rating_total=tally["total"])

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        error = ""
        form = {"name": "", "email": "", "policy_no": "", "style": "simple"}
        if request.method == "POST":
            form = {k: (request.form.get(k) or "").strip()
                    for k in ("name", "email", "phone", "policy_no", "style")}
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm") or ""
            if not form["name"] or not form["email"] or not password:
                error = "Name, email and password are required."
            elif "@" not in form["email"] or "." not in form["email"].split("@")[-1]:
                error = "Please enter a valid email address."
            elif len(password) < 4:
                error = "Password must be at least 4 characters."
            elif password != confirm:
                error = "The two passwords do not match."
            else:
                email = form["email"].lower()
                users = _load_json_file(_USERS_FILE, {})
                if email in users:
                    error = "An account with this email already exists - log in instead."
                else:
                    salt = secrets.token_hex(8)
                    users[email] = {
                        "name": form["name"], "email": email,
                        "phone": form.get("phone", ""),
                        "policy_no": form.get("policy_no", ""),
                        "style": form["style"] if form["style"] in ("simple", "detailed")
                                 else "simple",
                        "salt": salt,
                        "password": _hash_password(password, salt),
                    }
                    _save_json_file(_USERS_FILE, users)
                    flask_session.clear()
                    flask_session["user"] = email
                    return redirect(url_for("index"))
        return render_template("signup.html", error=error, form=form)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = ""
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            user = _load_json_file(_USERS_FILE, {}).get(email)
            if user and user.get("password") == _hash_password(
                    password, user.get("salt", "")):
                flask_session.clear()
                flask_session["user"] = email
                return redirect(url_for("index"))
            error = "Email or password is incorrect."
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        flask_session.clear()
        return redirect(url_for("login"))

    @app.post("/guest")
    def guest():
        flask_session.clear()
        flask_session["guest"] = True
        return redirect(url_for("index"))

    # ------------------------------------------------------------ APIs --
    @app.post("/api/chat")
    def chat():
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message") or "")
        session_id = str(payload.get("session_id") or "") or uuid.uuid4().hex
        chat_session = _SESSIONS.setdefault(session_id,
                                            {"history": [], "claim_id": None})
        profile = current_profile()
        if profile:
            chat_session["profile"] = profile
        resp = agent.handle(message, chat_session)
        resp["session_id"] = session_id
        return jsonify(resp)

    @app.post("/api/chat/stream")
    def chat_stream():
        """Fast-path chat: SSE.

        Events (all as `data: {json}\n\n`):
          {"t":"status","text":...}      - note shown as a small badge
          {"t":"token","text":...}       - reply delta, append in order
          {"t":"rule","text":...}        - full instant rule-based reply
                                            (shown as one block)
          {"t":"done","provider":...}    - final provider + note

        Cold model: emits rule immediately (UI shows it in <1s) while the
        model loads in the background. Warm: streams the AI tokens, first
        words in ~1-2s. The session history is updated exactly once, from
        the final text that ended up on screen.
        """
        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message") or "").strip()
        session_id = str(payload.get("session_id") or "") or uuid.uuid4().hex
        chat_session = _SESSIONS.setdefault(
            session_id, {"history": [], "claim_id": None})
        profile = current_profile()
        if profile:
            chat_session["profile"] = profile

        try:
            resp = Response(
                stream_with_context(
                    _fast_chat_events(agent, policy_docs, cfg, message,
                                      chat_session, profile)),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache",
                         "X-Accel-Buffering": "no",
                         "Connection": "close"})
            # Werkzeug needs direct_passthrough (or a Content-Length-less
            # response) so it does not buffer a streaming body chunk-by-chunk.
            resp.is_streamed = True
            return resp
        except Exception:  # noqa: BLE001 - streaming must never 500
            return jsonify(agent.handle(message, chat_session)), 200

    @app.get("/api/glossary")
    def glossary_endpoint():
        terms = [{"term": t, **body} for t, body in sorted(glossary.terms.items())]
        return jsonify({"terms": terms})

    @app.get("/api/ratings")
    def ratings_get():
        return jsonify(_tally())

    @app.post("/api/ratings")
    def ratings_post():
        payload = request.get_json(silent=True) or {}
        helpful = payload.get("helpful")
        if helpful not in (True, False):
            return jsonify({"error": "helpful must be true or false"}), 400
        data = _load_json_file(_RATINGS_FILE, {"yes": 0, "no": 0})
        try:
            yes, no = int(data.get("yes", 0)), int(data.get("no", 0))
        except (TypeError, ValueError):
            yes = no = 0
        if helpful:
            yes += 1
        else:
            no += 1
        _save_json_file(_RATINGS_FILE, {"yes": yes, "no": no})
        return jsonify(_tally())

    return app


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="claim-web",
                                     description="Web UI for the claim assistant")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--provider", choices=["auto", "ollama", "rule"],
                        default=None)
    args = parser.parse_args(argv)

    cfg = Config()
    if args.provider:
        cfg.provider = args.provider
    port = args.port or cfg.web_port

    app = create_app(cfg)
    print(f"Claim assistant web UI -> http://{args.host}:{port}  ({cfg.describe()})")
    try:
        app.run(host=args.host, port=port, debug=False, threaded=True)
    except OSError as exc:
        print(f"Port {port} is busy ({exc}). Try: python run_web.py --port 5001")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
