# Prompt Design Documentation

How the explanation prompt is constructed, why each block exists, and how the
system degrades gracefully. Source of truth:
`src/claim_assistant/prompting.py` (system prompt + template),
`src/claim_assistant/llm.py` (provider ladder).

## Goals

1. **Plain language first** — a customer with zero insurance background
   understands status, coverage and next steps on first read.
2. **Grounded, never invented** — every fact in the answer comes from the
   preprocessed claim or retrieved documents; the model may not add facts.
3. **Consistent shape** — same four sections every time, so answers are
   comparable and support agents can reuse them (this is what makes the
   ≥85% clarity metric achievable).

## System prompt (`SYSTEM_PROMPT`)

| Rule | Why |
|---|---|
| 8th-grade reading level, short sentences | the core clarity requirement |
| Gloss every insurance term in parentheses on first use | domain terminology → plain language |
| **Use ONLY the CLAIM CONTEXT; say when data is missing** | hallucination control — the model cannot invent an approval or amount |
| **Quote money facts exactly; never deny a listed deductible/co-pay** | caught a real drift where the model claimed "no deductible" while the context listed INR 5,000 — now pinned by the prompt *and* by `test_partial_approval_shows_money` |
| Only the masked identity (`A. S*****`) | privacy compliance even if the model tries to be helpful |
| No legal advice; point to grievance route for disputes | compliance / escalation safety |
| Fixed structure: *What is happening now / What it means for you / Next steps / Good to know* | predictable, scannable answers |
| Under 200 words | support-call replacement, not an essay |

Temperature 0.3, `num_predict` 600 — factual register, bounded length.

## User prompt: conditioning blocks (in order)

| Block | Source | Purpose |
|---|---|---|
| `CUSTOMER PROFILE` | signed-in account (`/signup` fields) | personalisation: first-name greeting + preferred style (simple vs. detailed-with-timeline); only name/style/policy reach the prompt — contact details are never sent to the model |
| _(followed by)_ | | the blocks below in order |
| `CONVERSATION SO FAR` | session history (last 4 turns, truncated) | follow-ups like "and what do I need to do?" resolve against the current claim |
| `CLAIM CONTEXT` | `preprocess.extract_key_params` — claim id, policy type, coverage, **mapped status in plain terms**, dates, claimed/approved/deductible/co-pay, next action + due date | the facts; status codes are mapped *before* prompting so the model never guesses what `DOCS_PENDING` means |
| `STATUS DETAILS` | `status_codes.json` (meaning, who acts, standard steps, SLA) | authoritative wording per stage; keeps explanations consistent across agents/operators |
| `CLAIM HISTORY` | claim timeline bullets | personalization: "we received your bills on 5 Oct" instead of generic prose |
| `GLOSSARY` | `retrieval.build_context` — terms the customer wrote + money terms from the claim + typo-tolerant search | the model quotes *our* definitions, so wording never drifts |
| `POLICY EXCERPTS` | sentence-level retrieval scoped to the claim's own document (health/motor) | document understanding: answers cite the customer's actual policy |
| `CUSTOMER QUESTION` | user input | last, so it stays salient |

Retrieval order matters: terms the customer actually typed → the claim's own
money terms (deductible/co-pay) → fuzzy extras. (An earlier version led with
fuzzy matches and pulled "cashless claim" for generic words like "claim".)

## The grounding/privacy pipeline (before the prompt ever exists)

```
raw claim ──▶ map_status()        status code → plain terms
          ──▶ anonymize_claim()   name/id/phone/email/reg masked
          ──▶ extract_key_params()   flat conditioning dict
          ──▶ build_context()        glossary + policy passages
          ──▶ build_user_prompt()    structured text
```

PII never enters the prompt: the HTTP API masks at the edge too
(tests: `test_anonymize_claim_masks_identity_keeps_facts`,
`test_http_endpoints_via_flask_client`).

## Intent routing (rule path - no more template answers)

`llm.detect_intent()` classifies the CUSTOMER QUESTION before rendering, so
the deterministic fallback answers *the question asked* instead of always
printing the same four sections:

| Intent | Trigger words (examples) | Reply opens with |
|---|---|---|
| `documents` | document, upload, pending, missing | who must submit what, by when + policy document rules |
| `why` | why, reason, rejected, reduced | the status meaning + money position + recent timeline as evidence |
| `money` | how much, amount, paid, deductible, reduced | claimed vs. approved + the precomputed settlement reconciliation |
| `timeline` | when, how long, status, updated | stage, last update timestamp, typical stage duration |
| `next` | next, what do I do, steps | the next action with actor and due date |
| `coverage` | cover, include, exclusion | which cover applies + the matching policy passage |
| `default` | everything else ("Explain CLM-1001") | the standard four-section structure |

Every intent still ends with **Next steps** and **Good to know**, so answers
stay structured while the opening line directly answers the customer.
The LLM path handles the same variety natively (the question is the last
prompt block and the system prompt says "answer it first").

## Provider ladder (why the demo cannot error)

| `LLM_PROVIDER` | Behaviour |
|---|---|
| `auto` (default) | Ollama → on failure, rule-based with note *"Ollama unavailable (...); used rule-based fallback"* |
| `ollama` | Ollama → on failure, still falls back rather than crash (note says *"Ollama failed"*) |
| `rule` | deterministic template only — used by the test-suite and for instant offline demos |

The **rule-based generator** renders the *same* conditioning context through
the *same* output structure (it parses the prompt's sections back out). It is
not a language model — it is the floor under the experience: correct status,
correct amounts, correct next steps, zero latency, zero dependencies.

Every reply carries a badge (`provider` + `note`) so judges can see which
path ran — honesty about the fallback is part of the demo.

## Evaluation hooks

- `tests/test_llm.py` pins the structure (`**Next steps**` present, due dates
  surfaced, amounts correct) for both generators.
- `tests/test_agent.py` pins end-to-end content per scenario (rejected claim
  mentions grievance; partial approval shows the reduced amount; real names
  never appear; a signed-in customer is greeted by name; unknown questions are
  echoed back instead of a static help dump).
- `tests/test_llm.py` pins each intent's opening line and the profile
  greeting/style behaviour.
- For the human clarity survey (≥85% metric) see `PLAN.md` — five scenarios
  chosen to span the status range.
