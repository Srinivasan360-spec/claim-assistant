# Execution Plan — Insurance Claim Explanation Assistant

TCS Technology Day problem statement. Single-agent conversational system that
takes claim identifiers, fetches simulated status updates, and generates clear,
personalized plain-language explanations.

## Architecture

```
                 ┌──────────────────────────────────────────────┐
 user question → │  ClaimAgent (single agent)                   │
                 │   1. resolve claim id (regex / session)      │
                 │   2. fetch  ← ClaimSource                    │
                 │        • in-process store (default, zero-    │
                 │          setup)  • HTTP mock API (MOCK_API_  │
                 │          URL) with local fallback            │
                 │   3. preprocess (status→plain terms, PII     │
                 │      anonymization, key-param extraction)    │
                 │   4. retrieve (glossary + policy passages)   │
                 │   5. generate (Ollama → rule-based fallback) │
                 └──────────────┬───────────────────────────────┘
                                ▼
             reply + status card + provider badge
        (CLI  ·  Web UI  ·  POST /api/chat)
```

## Phases (build order — everything below is DONE)

| # | Phase | Deliverable | Status |
|---|-------|-------------|--------|
| 1 | Data | `data/` — 8 synthetic claims across 8 statuses (JSON + CSV), 4 policy/reference text docs, 25-term glossary, status-code map | ✅ |
| 2 | Preprocessing | status→human terms, PII masking, key-param extraction (`preprocess.py`) | ✅ |
| 3 | Retrieval | glossary + policy passage scoring, dependency-free (`retrieval.py`) | ✅ |
| 4 | Mock API | live-simulated status endpoints + HTTP/in-process sources (`mock_api.py`) | ✅ |
| 5 | Prompting | system prompt + conditioning template (`prompting.py`) | ✅ |
| 6 | Generation | Ollama provider + rule-based fallback, never raises (`llm.py`) | ✅ |
| 7 | Agent | orchestration, sessions, follow-ups, guardrails (`agent.py`) | ✅ |
| 8 | Interfaces | CLI (`cli.py`) + Web UI (`web.py`, `templates/index.html`) | ✅ |
| 9 | Tests | 65 pytest cases — pipeline, API, fallbacks, privacy, auth, intents | ✅ |
| 10 | Docs | `docs/API_USAGE.md`, `docs/PROMPT_DESIGN.md`, `docs/COMPLIANCE.md`, README | ✅ |
| 11 | Demo video | script below — record with any screen recorder, save as `claim-assistant/docs/demo.mp4` | ⬜ |
| 12 | Feedback round 1: chips + intent-aware answers | clickable suggestion chips; why/money/timeline/documents/next/coverage routing | ✅ |
| 13 | Feedback round 1: real-world data | `health_practices.txt` / `motor_practices.txt` — real insurers, IRDAI timelines, CSR ranges | ✅ |
| 14 | Feedback round 1: glossary box | `/api/glossary` + searchable panel + clickable terms in replies | ✅ |
| 15 | Feedback round 1: auth + personalisation | `/signup` `/login` `/guest`; profile → prompt → greeted, style-honoured replies | ✅ |
| 16 | Feedback round 1: statement gap-fill | `claims.csv` (+CSV loader), clarity-rating widget, `docs/COMPLIANCE.md` matrix | ✅ |

## How each requirement is met

| Statement requirement | Where |
|---|---|
| Takes claim identifiers **or metadata** | claim ID (`CLM-1001`), policy ID, or session follow-up |
| Fetches simulated status updates | `/api/claims/<id>/status`, time-driven progression |
| Clear personalized explanations | structured reply: status / meaning / next steps / glossary |
| Insurance terminology → plain language | 25-term glossary injected into prompt + policy excerpts + in-UI glossary box |
| Mock API integration for real-time data | same-process Flask blueprint, or `MOCK_API_URL` |
| Privacy compliance | anonymization at the edge (`/api/claims` returns masked) |
| Preprocessing / prompt conditioning | `preprocess.extract_key_params` → `prompting.build_user_prompt` |
| Web interface **or** CLI | both |
| Documentation on API usage & prompt design | `docs/API_USAGE.md`, `docs/PROMPT_DESIGN.md` |
| Demo video for varied scenarios | script below |

## Success metric: ≥85% positive clarity rating

Built into the design, not measured automatically:

1. **Plain-language rules** in the system prompt (8th-grade level, short
   sentences, every term glossed) — see `docs/PROMPT_DESIGN.md`.
2. **Deterministic structure** so every answer covers *what is happening /
   what it means / next steps / good to know* — reviewers rate a fixed shape.
3. **Clarity survey for the demo**: show 5 varied claims
   (CLM-1001 under review, CLM-1002 docs pending, CLM-1005 partial approval,
   CLM-1007 rejected, CLM-1008 closed) to demo users and ask one question:
   *"Did you clearly understand what is happening with your claim and what to
   do next?"* — Yes/No. Target ≥85% Yes. Record the tally on screen at the
   end of the demo video.

## Demo video script (3–5 min)

Record with Win+G / OBS and **save the file as `claim-assistant/docs/demo.mp4`**
so code, docs and video ship together in one submission folder.

1. **Problem** (20 s): confusing claim communication, support calls.
2. **Data** (20 s): show `claims.json` + status codes + glossary + policy txt.
3. **Live API** (30 s): `curl /api/claims/CLM-1001/status` twice a day apart —
   status advanced automatically (show `simulated: true`).
4. **Web demo** (120 s): sign up (name + style) → run `python run_web.py`,
   click CLM-1007 (rejected — empathetic + grievance route), ask
   "Why was my amount reduced?" on CLM-1005 (intent-routed: reason + money),
   click a suggested-question chip, click an underlined glossary term in a
   reply to open the Glossary box, ask "What is a deductible?" with no claim,
   then hit "Was this clear? 👍" and show the header tally.
5. **CLI** (30 s): `python run_cli.py -q "Explain claim CLM-1006"`.
6. **Honesty/fallback** (30 s): stop Ollama → same answer from rule-based
   fallback with the `[rule-based]` badge; restart → `[ollama: llama3:latest]`.
7. **Tests** (20 s): `python -m pytest` → 65 passed.
8. **Docs** (10 s): `docs/` folder + clarity-survey tally.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Model not installed / laptop offline | rule-based fallback, badge shows which path ran |
| LLM invents an amount | prompt forbids it; only preprocessed fields are in context |
| PII leak into prompt | anonymization happens *before* prompt assembly; tested |
| Port busy, API down, bad JSON | every layer returns structured errors, `handle()` never raises |
| Windows terminal encoding | `sanitize()` normalizes bullets/emoji |

## Extension ideas (post-demo)

- Embeddings (gte-large) for retrieval instead of keyword scoring — the
  `Glossary`/`PolicyDocs` interface is deliberately small.
- Cloud LLM providers (`.env` keys from the hackathon checklist) as another
  rung in the fallback ladder.
- Clarification loop: agent asks a question back when a claim has missing docs.
