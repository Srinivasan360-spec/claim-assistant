"""Tests for retrieval: glossary lookup/search and policy passage scoring."""

from claim_assistant.retrieval import Glossary, PolicyDocs, build_context


def test_glossary_lookup_exact_and_fuzzy():
    g = Glossary()
    hit = g.lookup("deductible")
    assert hit and "own pocket" in hit["definition"]
    assert g.lookup("DEDUCTIBLE")  # case-insensitive
    assert g.lookup("deductibles")  # substring tolerance
    assert g.lookup("") is None


def test_glossary_terms_in_text():
    g = Glossary()
    hits = g.terms_in("Do I still pay the deductible and what is co-pay?")
    terms = {h["term"] for h in hits}
    assert "deductible" in terms
    assert "co-pay" in terms


def test_glossary_search_handles_typos():
    g = Glossary()
    hit = g.search("what does dedcutible mean?")
    assert hit and hit["term"] == "deductible"
    assert g.search("") is None


def test_policy_docs_scoped_by_claim_type():
    p = PolicyDocs()
    assert len(p.passages) > 10
    health_claim = {"policy_type": "health", "coverage_type": "Hospitalization"}
    hits = p.find("room rent sub-limit hospitalization", claim=health_claim)
    assert hits
    assert all(h["doc"].startswith("health") for h in hits)
    assert any("sub-limit" in h["text"] or "room rent" in h["text"].lower()
               for h in hits)


def test_build_context_assembles_glossary_and_passages():
    g, p = Glossary(), PolicyDocs()
    claim = {"policy_type": "motor", "coverage_type": "Own Damage",
             "amounts": {"copay_percent": 0}}
    ctx = build_context("What is a deductible in my own damage claim?",
                        claim, g, p)
    assert any(h["term"] == "deductible" for h in ctx["glossary"])
    assert ctx["policy_passages"]
    assert all(x["doc"].startswith("motor") for x in ctx["policy_passages"])


def test_build_context_without_claim():
    g, p = Glossary(), PolicyDocs()
    ctx = build_context("explain waiting period", None, g, p)
    assert ctx["glossary"]
    assert isinstance(ctx["policy_passages"], list)


def test_real_world_reference_docs_present():
    """Real-world insurer/IRDAI reference docs are loaded and retrievable."""
    p = PolicyDocs()
    docs = {x["doc"] for x in p.passages}
    assert "health_practices" in docs and "motor_practices" in docs

    claim = {"policy_type": "health", "coverage_type": "Hospitalization",
             "amounts": {}}
    hits = p.find("irdai cashless timeline", claim=claim, top_k=3)
    assert hits and all(h["doc"].startswith("health") for h in hits)
    assert any("irdai" in h["text"].lower() for h in hits)

    stars = p.find("Star Health claim settlement ratio", claim=claim, top_k=3)
    assert any("Star Health" in h["text"] for h in stars)


def test_motor_reference_doc_retrievable():
    p = PolicyDocs()
    claim = {"policy_type": "motor", "coverage_type": "Own Damage",
             "amounts": {}}
    hits = p.find("cashless garage network depreciation", claim=claim, top_k=3)
    assert hits and all(h["doc"].startswith("motor") for h in hits)
    assert any("garage" in h["text"].lower() for h in hits)
