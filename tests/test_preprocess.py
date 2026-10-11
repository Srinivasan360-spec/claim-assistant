"""Tests for preprocessing: status mapping, anonymization, key params."""

from claim_assistant.preprocess import (anonymize_claim, extract_key_params,
                                        format_history, map_status, mask_contact,
                                        mask_id, mask_name, settlement_note)


def test_map_status_known_code():
    s = map_status("UNDER_REVIEW")
    assert s["code"] == "UNDER_REVIEW"
    assert s["label"] == "Under review"
    assert "specialist" in s["plain"]
    assert s["who_acts"] in ("insurer", "customer", "none")
    assert isinstance(s["next_steps"], list) and s["next_steps"]


def test_map_status_unknown_code_never_raises():
    s = map_status("SOMETHING_NEW")
    assert s["code"] == "SOMETHING_NEW"
    assert s["label"] == "Status update"  # generic fallback
    s2 = map_status(None)
    assert s2["code"] == "UNKNOWN"


def test_mask_name():
    assert mask_name("Ananya Sharma") == "A. S*****"
    assert mask_name("Fatima Sheikh") == "F. S*****"
    assert mask_name("Dev") == "D."
    assert mask_name("") == "Customer"


def test_mask_id_and_contact():
    # keeps length, keeps only the last 4 characters
    assert mask_id("POL-H-77821") == "*******7821"
    assert "POL" not in mask_id("POL-H-77821")
    assert mask_contact("+91 9876541172").endswith("172")
    assert mask_contact("ananya.sharma@example.com") == "***@example.com"
    assert mask_contact("") == ""


def test_anonymize_claim_masks_identity_keeps_facts():
    claim = {
        "claim_id": "CLM-1001",
        "claimant": {"name": "Ananya Sharma", "customer_id": "CUST-4417",
                     "phone": "+91 9876541172", "email": "a@example.com"},
        "amounts": {"claimed": 145000.0},
        "incident": {"vehicle": {"reg_no": "MH 04 AB 1234"}},
    }
    out = anonymize_claim(claim)
    assert out["claimant"]["name"] == "A. S*****"
    assert "CUST" not in out["claimant"]["customer_id"]  # prefix masked
    assert out["claimant"]["customer_id"].endswith("4417")  # last 4 kept
    assert "98765" not in out["claimant"]["phone"]
    assert "ananya" not in out["claimant"]["email"]
    assert out["incident"]["vehicle"]["reg_no"].endswith("1234")
    assert out["claim_id"] == "CLM-1001"          # facts preserved
    assert out["amounts"]["claimed"] == 145000.0
    assert out["anonymized"] is True
    # original untouched
    assert claim["claimant"]["name"] == "Ananya Sharma"


def test_extract_key_params_and_history():
    claim = {
        "claim_id": "CLM-1001", "policy_type": "health",
        "product": "Family Health Optimizer", "coverage_type": "Hospitalization",
        "submitted_at": "2026-10-05T09:15:00", "updated_at": "2026-10-07T14:30:00",
        "amounts": {"claimed": 145000.0, "approved": None, "deductible": 5000.0,
                    "copay_percent": 10, "currency": "INR"},
        "incident": {"date": "2026-10-03", "description": "surgery"},
        "next_action": {"who": "insurer", "what": "review", "due": "2026-10-09"},
        "history": [{"at": "2026-10-05T09:15:00", "event": "claim_received",
                     "note": "registered"}],
    }
    status = map_status("UNDER_REVIEW")
    p = extract_key_params(claim, status)
    assert p["claimed_amount"] == "INR 145,000"
    assert p["approved_amount"] == "not decided yet"
    assert p["deductible"] == "INR 5,000"
    assert p["co_pay_percent"] == 10
    assert p["next_action_who"] == "insurer"
    assert p["status"] == "Under review"
    assert p["status_code"] == "UNDER_REVIEW"

    lines = format_history(claim)
    assert len(lines) == 1
    assert "claim received" in lines[0]


def test_settlement_note_reconciles_deductible():
    # 64,500 - 59,500 == 5,000 deductible -> precomputed reconciliation
    note = settlement_note({"claimed": 64500.0, "approved": 59500.0,
                            "deductible": 5000.0, "currency": "INR"})
    assert "deductible" in note.lower()
    assert "5,000" in note and "adjusted" in note


def test_settlement_note_partial_and_rejected_and_undecided():
    partial = settlement_note({"claimed": 95000.0, "approved": 78250.0,
                               "deductible": 5000.0, "currency": "INR"})
    assert "16,750" in partial and "decision letter" in partial

    rejected = settlement_note({"claimed": 72000.0, "approved": 0.0,
                                "deductible": 1000.0, "currency": "INR"})
    assert "not approved" in rejected

    undecided = settlement_note({"claimed": 145000.0, "approved": None,
                                 "deductible": 5000.0, "currency": "INR"})
    assert "not decided" in undecided

    # parameter flows into the conditioning dict
    claim = {"amounts": {"claimed": 64500.0, "approved": 59500.0,
                          "deductible": 5000.0, "currency": "INR"}}
    p = extract_key_params(claim, map_status("PAYMENT_PROCESSING"))
    assert "already been adjusted" in p["settlement_note"]
