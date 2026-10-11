"""Tests for the generation layer: rule output, Ollama fallback, prompt build."""

from claim_assistant.config import Config
from claim_assistant.llm import (OllamaProvider, detect_intent, generate,
                                rule_based_explanation)
from claim_assistant.prompting import SYSTEM_PROMPT, build_user_prompt
from claim_assistant.preprocess import extract_key_params, format_history, map_status


def _ctx():
    claim = {"claim_id": "CLM-1001", "policy_type": "health",
             "product": "Family Health Optimizer", "coverage_type": "Hospitalization",
             "submitted_at": "2026-10-05T09:15:00", "updated_at": "2026-10-07T14:30:00",
             "amounts": {"claimed": 145000.0, "approved": None, "deductible": 5000.0,
                         "copay_percent": 10, "currency": "INR"},
             "incident": {"date": "2026-10-03", "description": "surgery"},
             "next_action": {"who": "insurer", "what": "medical review",
                             "due": "2026-10-09"},
             "history": [{"at": "2026-10-05T09:15:00", "event": "claim_received",
                          "note": "registered"}]}
    status = map_status("UNDER_REVIEW")
    params = extract_key_params(claim, status)
    retrieval = {"glossary": [{"term": "deductible",
                               "definition": "amount you pay first",
                               "plain": "your share", "example": "e.g. 5000"}],
                 "policy_passages": [{"doc": "health",
                                      "text": "A deductible of INR 5,000 applies per policy year."}]}
    return claim, status, params, retrieval


def test_system_prompt_sets_ground_rules():
    assert "Never invent facts" in SYSTEM_PROMPT
    assert "masked identity" in SYSTEM_PROMPT
    assert "**Next steps**" in SYSTEM_PROMPT


def test_build_user_prompt_contains_all_conditioning_blocks():
    claim, status, params, retrieval = _ctx()
    prompt = build_user_prompt(params, status, format_history(claim), retrieval,
                               "Explain this claim", [("user", "hi")])
    for block in ("CONVERSATION SO FAR", "CLAIM CONTEXT", "STATUS DETAILS",
                  "CLAIM HISTORY", "GLOSSARY", "POLICY EXCERPTS", "CUSTOMER QUESTION"):
        assert block in prompt
    assert "CLM-1001" in prompt and "INR 145,000" in prompt


def test_rule_based_explanation_structure():
    claim, status, params, retrieval = _ctx()
    prompt = build_user_prompt(params, status, format_history(claim), retrieval,
                               "Explain this claim", [])
    text = rule_based_explanation(prompt)
    assert "**What is happening now**" in text
    assert "**What it means for you**" in text
    assert "**Next steps**" in text
    assert "**Good to know**" in text
    assert "INR 145,000" in text
    assert "deductible" in text.lower()
    assert "2026-10-09" in text          # next-step due date surfaced


def test_generate_rule_mode_never_touches_network():
    claim, status, params, retrieval = _ctx()
    cfg = Config()
    cfg.provider = "rule"
    out = generate(cfg, status=status, params=params, history_lines=[],
                   retrieval_ctx=retrieval, question="Explain", conversation=[])
    assert out.provider == "rule-based"
    assert out.text and len(out.text) > 100


def test_generate_auto_falls_back_when_ollama_down():
    claim, status, params, retrieval = _ctx()
    cfg = Config()
    cfg.provider = "auto"
    cfg.ollama_base_url = "http://127.0.0.1:1"  # guaranteed dead endpoint
    cfg.llm_timeout = 1
    from claim_assistant import llm as llm_mod
    llm_mod._WARM_FLAG.clear()  # simulate a cold start for this test
    out = generate(cfg, status=status, params=params, history_lines=[],
                   retrieval_ctx=retrieval, question="Explain", conversation=[])
    assert out.provider == "rule-based"
    assert "instant answer" in out.note   # never blocks on a dead model
    assert "**Next steps**" in out.text    # still a complete explanation


def test_generate_ollama_mode_still_falls_back():
    claim, status, params, retrieval = _ctx()
    cfg = Config()
    cfg.provider = "ollama"
    cfg.ollama_base_url = "http://127.0.0.1:1"
    cfg.llm_timeout = 1
    out = generate(cfg, status=status, params=params, history_lines=[],
                   retrieval_ctx=retrieval, question="Explain", conversation=[])
    assert out.provider == "rule-based"     # never raises, degrades instead
    assert "Ollama failed" in out.note


def test_ollama_provider_bad_payload_raises_llm_error():
    import pytest
    p = OllamaProvider("http://127.0.0.1:1", "llama3:latest", timeout=1)
    from claim_assistant.llm import LLMError
    with pytest.raises(LLMError):
        p.generate("sys", "user")


# ------------------------------------------------------------- intents ---
def _prompt_for(question):
    claim, status, params, retrieval = _ctx()
    return build_user_prompt(params, status, format_history(claim), retrieval,
                             question, [])


def test_detect_intent_classification():
    assert detect_intent("Why was my amount reduced?") == "why"
    assert detect_intent("when will I get paid") == "money"
    assert detect_intent("which documents are pending") == "documents"
    assert detect_intent("what happens next") == "next"
    assert detect_intent("does my cover include this") == "coverage"
    assert detect_intent("when will this be updated") == "timeline"
    assert detect_intent("Explain claim CLM-1001") == "default"


def test_rule_money_question_leads_with_money():
    text = rule_based_explanation(_prompt_for("How much will I get?"))
    assert text.startswith("**Short answer**")
    assert "You asked about the money" in text
    assert "INR 145,000" in text and "not decided yet" in text
    assert "**Next steps**" in text            # structure still present


def test_rule_why_question_shows_reason_and_timeline():
    text = rule_based_explanation(_prompt_for("Why is this taking so long?"))
    assert text.startswith("**Short answer**")
    assert "2026-10-05" in text                 # claim history as evidence
    assert "**Recent updates**" in text


def test_rule_documents_question():
    text = rule_based_explanation(_prompt_for("which documents are pending?"))
    assert text.startswith("**Short answer**")
    assert "medical review" in text             # insurer's action shown
    assert "**Details**" in text


def test_rule_timeline_question():
    text = rule_based_explanation(_prompt_for("when will this be updated?"))
    assert "Stage: Under review" in text
    assert "Last updated 2026-10-07" in text


def test_rule_coverage_question():
    text = rule_based_explanation(_prompt_for("does my policy cover this?"))
    assert ("Your Hospitalization cover under Family Health Optimizer "
            "applies here.") in text


def test_profile_greeting_and_style():
    claim, status, params, retrieval = _ctx()
    prof = {"name": "Priya Tester", "style": "simple", "policy_no": "POL-H-77821"}
    prompt = build_user_prompt(params, status, format_history(claim), retrieval,
                               "Explain this claim", [], profile=prof)
    assert "CUSTOMER PROFILE" in prompt and "Priya Tester" in prompt
    assert rule_based_explanation(prompt).startswith("Hi Priya,")

    prof["style"] = "detailed"
    prompt = build_user_prompt(params, status, format_history(claim), retrieval,
                               "Explain this claim", [], profile=prof)
    text = rule_based_explanation(prompt)
    assert text.startswith("Hi Priya,")
    assert "**Recent updates**" in text        # style preference honoured
