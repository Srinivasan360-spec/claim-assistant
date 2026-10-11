# Compliance Matrix — problem statement sheet → implementation → proof

Every requirement quoted from the TCS Technology Day sheet ("Insurance Claim
Explanation Assistant"), with the file that implements it and the test that
proves it. Test names run with `py -3.12 -m pytest` (65 tests, all green).

## Problem statement

| Statement requirement | Where implemented | Verified by |
|---|---|---|
| "customers want clear, personalized explanations of claim status, coverage details, and next steps" | `llm.py` output structure (*What is happening now / What it means for you / Next steps / Good to know*) + `agent.py` | `test_explain_claim_end_to_end`, `test_rule_*` |
| "support teams... delayed responses and inconsistent explanations" (consistency) | single system prompt + shared status map for every answer | `test_system_prompt_sets_ground_rules`, `test_map_status_known_code` |
| "solution that leverages generative AI to interpret claim data" | `llm.py` Ollama provider (real local model) + `prompting.py` | `test_generate_auto_falls_back_when_ollama_down`, live CLI/web runs |

## Data considerations

| Statement requirement | Where implemented | Verified by |
|---|---|---|
| "Structured claim data including claim ID, status codes, coverage types, and claim history in **JSON or CSV** format" | `data/claims.json` **and** `data/claims.csv`; `ClaimStore` loads either | `test_csv_store_matches_json_and_simulates` |
| "**Simulated claim status API endpoints** providing real-time updates" | `mock_api.py` blueprint; status recomputed from the clock (one stage per `stage_hours`) | `test_live_status_advances_with_time`, `test_http_endpoints_via_flask_client`, live curl |
| "**Policy documents and glossary terms** for coverage explanation, ideally as text files" | `data/policy_docs/*.txt` (4 text files incl. real-world insurer/IRDAI docs), `data/glossary.json` | `test_real_world_reference_docs_present`, `test_glossary_lookup_exact_and_fuzzy` |
| "**Synthetic claim records** representing diverse scenarios" | 8 synthetic claims: health + motor, all 8 statuses (submitted → rejected → closed), varied amounts/deductibles | `test_store_loads_all_claims`, scenario tests in `test_agent.py` |
| "**Privacy compliance ensured by anonymizing claimant details**" | `preprocess.anonymize_claim` masks name/customer id/phone/email/vehicle reg; applied at the API edge *and* before prompt assembly | `test_anonymize_claim_masks_identity_keeps_facts`, `test_http_endpoints_via_flask_client` (asserts masked name), `test_personalised_greeting_via_session_profile` (real name never appears) |
| "**Preprocessing to map status codes to human-readable terms** and extract key claim parameters for AI prompt conditioning" | `preprocess.map_status`, `extract_key_params`, `settlement_note` | `test_map_status_known_code`, `test_extract_key_params_and_history`, `test_settlement_note_*` |
| "synthetic or anonymized data... data quality and relevance" | all records synthetic (documented in every policy doc footer); JSON validated + 65-test suite | JSON validation, `pytest` |

## Solution expectations

| Statement requirement | Where implemented | Verified by |
|---|---|---|
| "**single-agent** conversational system" | one `ClaimAgent` owns fetch → preprocess → retrieve → generate; sessions carry context | `test_follow_up_uses_session_claim`, `test_chat_session_continuity` |
| "takes **claim identifiers or metadata**" | claim ID (`CLM-1001`), policy ID (`POL-H-77821`), session's current claim, free-text questions | `test_explain_claim_end_to_end`, `_find_claim` paths |
| "fetches **simulated status updates**" | `ClaimSource` (in-process or HTTP), `live_status()` | `test_get_status_payload_shape`, `test_csv_store_*` |
| "generates clear, personalized explanations of **claim progress and next steps**" | structured generation + intent routing (why/money/timeline/documents/next/coverage) | `test_rule_*_question`, `test_detect_intent_classification` |
| "demonstrate understanding of **insurance terminology** and produce **plain language** responses" | 25-term glossary injected in prompt + glossary box in UI; system prompt enforces 8th-grade plain English with per-term glosses | `test_glossary_terms_in_text`, `test_glossary_endpoint`, `test_build_context_*` |
| "Success metrics include at least **85% positive clarity rating** from demo users" | "Was this clear?" widget → `POST /api/ratings`, live tally in header, survey procedure in `PLAN.md` | `test_ratings_flow` |
| "integration with **mock APIs** for real-time data" | same-process blueprint; `MOCK_API_URL` for a remote API with automatic local fallback | `test_api_routes_mounted_on_same_app`, `HttpClaimSource` graceful-degradation design |
| "Deliverables include a basic **web interface or CLI**" | **both**: Flask chat UI (`/`) and `run_cli.py` | `test_index_renders_for_guest`, CLI verified live (`run_cli.py -q ...`) |
| "**documentation** on API usage and prompt design" | `docs/API_USAGE.md`, `docs/PROMPT_DESIGN.md` (+ this matrix) | reviewed in repo |
| "a **demo video** showcasing explanation generation for varied claim scenarios" | shot-by-shot script in `PLAN.md` (recording is a human step) | `PLAN.md` → Demo video script |
| "GenAI capabilities focus on **document understanding**" | policy/practice text retrieval conditions every answer | `test_policy_docs_scoped_by_claim_type`, `test_real_world_reference_docs_present` |
| "**natural language generation**" | Ollama `llama3:latest` NLG with deterministic fallback | live runs logged; `test_generate_*` |
| "**domain-specific terminology handling**" | glossary + status-code vocabulary + clickable glossary box | `test_glossary_search_handles_typos`, `test_glossary_endpoint` |
| "Feel free to explore creative solutions" | auth personalization, intent-aware answers, clarity widget, real-world reference docs | this document |

## Feedback round 1 (implemented in this build)

| Feedback | Fix | Verified by |
|---|---|---|
| "it provides the related default answers for every question" | intent detection reshapes rule answers; no-claim fallback echoes the question instead of static help | `test_detect_intent_classification`, `test_rule_*_question`, `test_unknown_question_echoed_not_static_help` |
| "the suggested questions aren't working if we click on it" | click handlers on the hint chips | template assertion in `test_index_renders_for_guest`, browser click-through |
| "link some real world insurance companies' data... simple tone" | `health_practices.txt` / `motor_practices.txt` (real insurers, IRDAI timelines, CSR ranges, sources) | `test_real_world_reference_docs_present` |
| "glossary terms for coverage explanation box" | Glossary panel + clickable terms inside replies | `test_glossary_endpoint`, template hooks, browser verification |
| "login and sign up page... personalised experience" | `/signup` `/login` `/logout` + guest; profile (name/style/policy) conditions every answer | `test_signup_*`, `test_chat_uses_signed_in_profile`, `test_chat_detailed_style_adds_timeline` |
| "meet each and every solution expectations and data considerations" | this matrix; gaps filled: CSV format, clarity widget, real-world docs | all rows above |
