"""End-to-end tests: the agent's full pipeline with the offline (rule) provider.

These prove the "no error" property: every path returns a structured reply
and never raises, even with a broken source or an unreachable LLM.
"""

import pytest

from claim_assistant.agent import ClaimAgent
from claim_assistant.config import Config


@pytest.fixture
def agent():
    cfg = Config()
    cfg.provider = "rule"
    return ClaimAgent(cfg)


def test_explain_claim_end_to_end(agent):
    resp = agent.handle("Explain claim CLM-1001")
    assert resp["reply"]
    assert resp["claim_id"] == "CLM-1001"
    assert resp["provider"] == "rule-based"
    assert resp["claim_view"]["status_label"] in (
        "Claim submitted", "Awaiting documents", "Under review",
        "Decision in progress", "Approved", "Partially approved",
        "Not approved", "Payment processing", "Claim closed")
    # core deliverable content: status, meaning, next steps, money facts
    reply = resp["reply"]
    assert "**What is happening now**" in reply
    assert "**What it means for you**" in reply
    assert "**Next steps**" in reply
    assert "INR 145,000" in reply
    # privacy: no real claimant name anywhere in the output
    assert "Ananya" not in reply


def test_follow_up_uses_session_claim(agent):
    session = {"history": [], "claim_id": None}
    first = agent.handle("What is happening with CLM-1002?", session)
    assert first["claim_id"] == "CLM-1002"
    second = agent.handle("and what do I need to do?", session)  # no claim id
    assert second["claim_id"] == "CLM-1002"
    assert second["reply"]


def test_glossary_question_without_claim(agent):
    resp = agent.handle("What is a deductible?")
    assert "own pocket" in resp["reply"]
    # stable schema: documented keys present on every response shape
    assert set(resp) >= {"reply", "claim_id", "claim_view", "provider", "note", "api"}
    assert resp["claim_view"] is None
    assert resp["provider"] == "retrieval"        # no LLM call was needed


def test_unknown_claim_id_gives_guidance(agent):
    resp = agent.handle("Explain claim CLM-9999")
    assert "CLM-9999" in resp["reply"]
    assert "CLM-1001" in resp["reply"]        # suggests real ids
    assert resp.get("claim_view") is None


def test_empty_and_irrelevant_messages(agent):
    assert agent.handle("")["reply"]
    assert agent.handle("   ")["reply"]
    assert agent.handle("hello there")["reply"]


def test_list_claims(agent):
    ids = [c["claim_id"] for c in agent.list_claims()]
    assert len(ids) == 8 and "CLM-1007" in ids


def test_agent_survives_broken_source_and_llm():
    """Even when both the data source and LLM explode, no exception escapes."""
    class BrokenSource:
        name = "broken"
        def list_claims(self): return []
        def get_claim(self, cid): raise RuntimeError("boom")
        def get_status(self, cid): raise RuntimeError("boom")

    cfg = Config()
    cfg.provider = "auto"
    cfg.ollama_base_url = "http://127.0.0.1:1"   # nothing listens there
    cfg.llm_timeout = 1
    agent = ClaimAgent(cfg, source=BrokenSource())
    # fetch raises inside _fetch -> caught by handle's blanket guard
    resp = agent.handle("Explain claim CLM-1001")
    assert isinstance(resp["reply"], str) and resp["reply"]
    assert "sorry" in resp["reply"].lower()


def test_rejected_claim_explanation_is_empathetic_and_grievance_aware(agent):
    resp = agent.handle("Explain claim CLM-1007")
    reply = resp["reply"]
    assert resp["claim_view"]["status_label"] == "Not approved"
    assert "grievance" in reply.lower() or "Next steps" in reply


def test_partial_approval_shows_money(agent):
    resp = agent.handle("Why did I get less money on CLM-1005?")
    assert "INR 78,250" in resp["reply"] or "78,250" in resp["reply"]


def test_personalised_greeting_via_session_profile(agent):
    session = {"history": [], "claim_id": None,
               "profile": {"name": "Ananya Rao", "style": "simple",
                           "policy_no": "POL-H-77821"}}
    resp = agent.handle("Explain claim CLM-1001", session)
    assert resp["reply"].startswith("Hi Ananya,")
    assert "Ananya Sharma" not in resp["reply"]   # claimant stays masked


def test_unknown_question_echoed_not_static_help(agent):
    resp = agent.handle("can you dance for me?")
    reply = resp["reply"]
    assert "dance for me" in reply              # echoes the actual question
    assert "CLM-1001" in reply                  # still shows what it can do
    assert "CLM-1003" in reply
