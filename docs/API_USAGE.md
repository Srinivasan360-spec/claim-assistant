# API Usage Documentation

Layers served by one process: **pages/auth** (personalisation), the **simulated
claim status API** (data layer), the **chat API** (agent layer), and the
**glossary** + **ratings** helpers for the web UI.

Start everything:

```bash
py -3.12 run_web.py            # http://127.0.0.1:5000
```

## 0. Pages and auth (personalisation)

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | chat UI; redirects to `/login` when signed out; `profile_name`, `rating_pct`, `rating_total` template vars |
| `/signup` | GET/POST | sign-up form: name, email, phone, policy number, preferred style (simple/detailed), password + confirm |
| `/login` | GET/POST | email + password; wrong credentials re-render with an error |
| `/logout` | POST | clears the session |
| `/guest` | POST | continue without an account (no personalisation) |

Accounts live in `data/users.json` with per-user **salted SHA-256** hashes
(demo-grade, local only; plaintext passwords are never stored). The Flask
session cookie persists across restarts via `data/.flask_secret`.

**Personalisation flow:** the signed-in profile is attached to the chat
session, producing a `CUSTOMER PROFILE` block in the prompt — replies open
with `Hi <first name>,` and honour the chosen style (detailed adds the claim
timeline). Verified by `test_chat_uses_signed_in_profile` and
`test_chat_detailed_style_adds_timeline`.

---

## 1. Simulated claim status API

All responses are JSON. Claim records returned over HTTP are **anonymized at
the edge** (names, IDs, phone, email, registration numbers masked).

### `GET /api/health`

```json
{ "ok": true, "service": "claim-status-api", "claims": 8 }
```

### `GET /api/claims`

List of compact claim summaries (no PII).

```json
{ "claims": [
    { "claim_id": "CLM-1001", "policy_type": "health",
      "coverage_type": "Hospitalization",
      "status_code": "UNDER_REVIEW", "submitted_at": "2026-10-05T09:15:00" }
] }
```

### `GET /api/claims/{claim_id}`

Full claim record, identity masked (`404` if unknown):

```json
{ "claim": {
    "claim_id": "CLM-1005",
    "claimant": { "name": "P. N***", "customer_id": "******3390",
                  "phone": "**********344", "email": "***@example.com" },
    "amounts": { "claimed": 95000.0, "approved": 78250.0,
                 "deductible": 5000.0, "copay_percent": 10, "currency": "INR" },
    "anonymized": true
} }
```

### `GET /api/claims/{claim_id}/status` — the real-time endpoint

Status is recomputed from the clock: claims with `simulate_progression: true`
advance one stage every `stage_hours` since submission, clamped to the end of
their progression. Call it today and tomorrow — the answer changes.

```json
{ "claim_id": "CLM-1001", "code": "UNDER_REVIEW",
  "label": "Under review",
  "plain": "A claims specialist is checking your bills against your policy terms.",
  "meaning": "We are verifying that the claim is covered and the amounts are correct. No action is needed from you right now.",
  "who_acts": "insurer",
  "next_steps": ["Nothing needed from you at this stage",
                 "Reply promptly if we ask for any clarification"],
  "sla_days": 3, "simulated": true,
  "as_of": "2026-10-08T13:51:39", "updated_at": "2026-10-07T14:30:00" }
```

Unknown claim → `404`:

```json
{ "error": "claim_not_found", "claim_id": "CLM-9999" }
```

curl examples:

```bash
curl http://127.0.0.1:5000/api/claims
curl http://127.0.0.1:5000/api/claims/CLM-1001/status
curl -s http://127.0.0.1:5000/api/claims/CLM-1007 | python -m json.tool
```

Data may equally be served from **CSV**: `ClaimStore("data/claims.csv")`
loads the same eight records (statement: "JSON or CSV format"), verified by
`test_csv_store_matches_json_and_simulates`.

---

## 2. Chat API (the agent)

### `POST /api/chat`

Request:

```json
{ "message": "What happens next for CLM-1002?",
  "session_id": "optional-uuid-from-a-previous-response" }
```

Response (stable key set on every turn):

```json
{ "reply": "**What is happening now**\n...",
  "claim_id": "CLM-1002",
  "claim_view": { "claim_id": "CLM-1002", "status_label": "Awaiting documents",
                  "status_code": "DOCS_PENDING", "policy_type": "motor",
                  "coverage_type": "Own Damage", "claimed_amount": 38500.0,
                  "approved_amount": null, "currency": "INR",
                  "next_action": "Upload the garage repair estimate...",
                  "next_action_who": "customer", "simulated": false },
  "provider": "ollama", "note": "model=llama3:latest",
  "api": "in-process store",
  "session_id": "0f2c..." }
```

| Field | Meaning |
|---|---|
| `reply` | the plain-language explanation (markdown-ish: `**bold**`, `- ` bullets) |
| `claim_id` | claim the turn resolved to (also carried by follow-ups); `null` for glossary-only turns |
| `claim_view` | compact status card for UI rendering; `null` when no claim |
| `provider` | `ollama`, `rule-based`, or `retrieval` (glossary/help turns that needed no generation) — `note` explains fallbacks |
| `api` | claim source actually used; `null` when no claim was fetched |
| `session_id` | pass it back to keep conversation context (current claim, history, profile) |

Behaviour notes:

- **Intent-aware**: the question is classified (why / money / timeline /
  documents / next / coverage) and answered first — see `docs/PROMPT_DESIGN.md`.
- **Session continuity**: omit the claim ID in follow-ups and the agent uses
  the session's current claim.
- **Personalised when signed in**: profile flows into the reply (greeting +
  style) via the session set by the server.
- **Never fails**: unknown claim → guidance echoing your question; malformed
  JSON → help text; backend errors → apology message. The key set above is
  always present.

curl example:

```bash
curl -s -X POST http://127.0.0.1:5000/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"Explain claim CLM-1005\"}"
```

---

## 3. Glossary box API

### `GET /api/glossary`

Every coverage term for the in-UI glossary box (search, expand, "Ask the
assistant") and for making terms inside replies clickable:

```json
{ "terms": [
    { "term": "deductible",
      "definition": "The fixed amount you pay out of your own pocket before the insurer starts paying.",
      "plain": "Think of it as your share of the bill that comes first.",
      "example": "If your deductible is INR 5,000 ..." }
] }
```

25 terms, sorted; verified by `test_glossary_endpoint`.

## 4. Clarity rating API (85% success metric)

### `GET /api/ratings`

```json
{ "yes": 12, "no": 2, "total": 14, "percent": 86 }
```

`percent` is `null` until the first vote.

### `POST /api/ratings`

```json
{ "helpful": true }
```

Increments the tally and returns the same object. The web UI posts one vote
per answer via the **"Was this clear? 👍/👎"** widget; the header shows the
running percentage. Invalid payloads → `400`. Verified by
`test_ratings_flow`.

---

## 5. Consuming it from Python (in-process, no server)

```python
import sys; sys.path.insert(0, "src")
from claim_assistant.agent import ClaimAgent
from claim_assistant.config import Config

agent = ClaimAgent(Config())          # provider=auto: ollama → rule fallback
resp = agent.handle("Explain claim CLM-1001")
print(resp["reply"], resp["provider"], resp["claim_view"]["status_label"])
```

CLI equivalents:

```bash
py -3.12 run_cli.py -q "Explain claim CLM-1001"
py -3.12 run_cli.py --claim CLM-1006            # seed session, then chat
py -3.12 run_cli.py --api-url http://host:5000  # agent reads a remote status API
```

## 6. Pointing the agent at an external API

```bash
set MOCK_API_URL=http://localhost:5000     # or export in .env
py -3.12 run_web.py
```

The agent then fetches over HTTP; if the server becomes unreachable it falls
back to the local store automatically and keeps answering (the response's
`api` field shows the source actually used).
