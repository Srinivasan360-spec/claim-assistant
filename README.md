# Insurance Claim Explanation Assistant

Single-agent conversational system for the TCS Technology Day problem
statement: a customer names a claim, the agent fetches its simulated live
status, and explains — in plain English — what is happening, what it means,
what happens next, and why, using the customer's own policy documents and a
25-term insurance glossary.

## Quick start (zero configuration)

```bash
# 1. install the one dependency (Python 3.12.8)
py -3.12 -m pip install -r requirements.txt

# 2. web interface  →  http://127.0.0.1:5000
py -3.12 run_web.py      # opens on the login page: sign up, or continue as guest

# 3. or the CLI
py -3.12 run_cli.py                 # interactive
py -3.12 run_cli.py -q "Explain claim CLM-1001"
py -3.12 run_cli.py --claims        # list the 8 demo claims
```

Sign-up collects name, email, phone, policy number and preferred explanation
style - replies then greet you by name and follow the chosen style (simple vs.
detailed-with-timeline). Guest mode works with no account.

Explanations come from your local **Ollama** model (`llama3:latest`, no API
key needed). If Ollama is not running, a deterministic rule-based generator
answers the same structure instantly — the reply's badge always says which
one ran, so **the demo cannot fail**.

```bash
py -3.12 run_cli.py --provider rule   # force offline mode (instant answers)
py -3.12 run_web.py --port 5001       # port taken? move it
```

## Try these queries

| Query | Shows |
|---|---|
| `Explain claim CLM-1001` | under review, health, amounts + timeline |
| `What happens next for CLM-1002?` | customer action: docs pending |
| `Why was my amount reduced?` on CLM-1005 | partial approval, sub-limit, co-pay |
| `Explain claim CLM-1007` | rejected claim → empathy + grievance route |
| `What is a deductible?` | standalone glossary answer, no claim needed |
| `Why was my amount reduced?` on CLM-1005 | intent-routed: reason + timeline + money |
| `which documents are pending?` on CLM-1002 | intent-routed: customer to-do list |
| follow-up: `and what do I need to do?` | session context retention |

In the web UI: suggestion chips are clickable, glossary terms inside answers
open the **Glossary box**, and every answer carries a **"Was this clear?"**
button whose tally feeds the 85% success metric.

## Tests

```bash
py -3.12 -m pip install pytest
py -3.12 -m pytest          # 65 passed
```

Covers preprocessing, anonymization, retrieval (incl. real-world insurer
reference docs), JSON+CSV data loading, the live-status simulation, HTTP
endpoints, prompt construction, intent routing, fallback behaviour, auth and
personalisation, glossary/ratings endpoints, end-to-end agent turns (including
a deliberately broken source/LLM) and the web routes.

## Layout

```
claim-assistant/
├── run_cli.py / run_web.py     entry points
├── PLAN.md                     execution plan, requirement map, demo script
├── src/claim_assistant/
│   ├── config.py               env-driven settings + .env loader
│   ├── preprocess.py           status→plain terms, PII masking, key params
│   ├── retrieval.py            glossary + policy passage retrieval
│   ├── mock_api.py             simulated live status API (store + HTTP)
│   ├── prompting.py            system prompt + conditioning template
│   ├── llm.py                  Ollama client + rule-based fallback
│   ├── agent.py                single-agent orchestration (never raises)
│   ├── cli.py  web.py          interfaces
├── data/                       claims (JSON + CSV), status codes, glossary, policy docs
├── templates/                  chat UI + login/signup pages
├── docs/API_USAGE.md           endpoint + client documentation
├── docs/PROMPT_DESIGN.md       prompt engineering documentation
├── docs/COMPLIANCE.md          problem-statement → code → test matrix
└── tests/                      65 pytest cases
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `auto` | `auto` / `ollama` / `rule` |
| `OLLAMA_MODEL` | `llama3:latest` | any model you have pulled |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `MOCK_API_URL` | *(empty)* | point at an external status API; empty = in-process, and HTTP failures fall back to local |
| `CLAIMS_PORT` | `5000` | web UI port |

Documentation: [docs/API_USAGE.md](docs/API_USAGE.md) ·
[docs/PROMPT_DESIGN.md](docs/PROMPT_DESIGN.md) ·
[docs/COMPLIANCE.md](docs/COMPLIANCE.md) · [PLAN.md](PLAN.md)
