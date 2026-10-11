"""Preprocessing: status-code mapping, PII anonymization, prompt conditioning.

Implements the statement's "preprocessing to map status codes to
human-readable terms and extract key claim parameters for AI prompt
conditioning" plus privacy compliance via anonymization.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .config import DATA_DIR, load_json

UNKNOWN_STATUS_FALLBACK = {
    "label": "Status update",
    "plain": "Your claim is progressing through our system.",
    "meaning": "The system has not returned a familiar status label for this stage yet.",
    "who_acts": "insurer",
    "next_steps": ["Keep your claim ID handy for any support call"],
    "sla_days": None,
}

_STATUS_MAP: Optional[dict] = None


def _status_map() -> dict:
    global _STATUS_MAP
    if _STATUS_MAP is None:
        _STATUS_MAP = load_json(DATA_DIR / "status_codes.json", {})
    return _STATUS_MAP


def map_status(code: str) -> dict:
    """Map a machine status code to human-readable terms (never raises)."""
    entry = _status_map().get((code or "").upper())
    if not entry:
        out = dict(UNKNOWN_STATUS_FALLBACK)
        out["code"] = (code or "UNKNOWN").upper()
        return out
    return {"code": (code or "").upper(), **entry}


def mask_name(name: str) -> str:
    """'Ananya Sharma' -> 'A. S*****' (keeps shape, drops identity)."""
    if not name:
        return "Customer"
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if not parts:
        return "Customer"
    masked = [parts[0][0].upper() + "."]
    for surname in parts[1:]:
        masked.append(surname[0].upper() + "*" * max(len(surname) - 1, 2))
    return " ".join(masked)


def mask_id(value: str) -> str:
    """Keep only the last 4 characters: 'POL-H-77821' -> '*******7821'."""
    if not value:
        return ""
    tail = value[-4:]
    return "*" * max(len(value) - 4, 1) + tail


def mask_contact(value: str) -> str:
    """Phone/email -> last 3-4 chars only."""
    if not value or "@" in value:
        local, _, domain = (value or "").partition("@")
        if not domain:
            return ""
        return f"***@{domain}"
    digits = re.sub(r"\D", "", value)
    return ("*" * max(len(digits) - 3, 0) + digits[-3:]) if digits else "***"


def anonymize_claim(claim: dict) -> dict:
    """Return a privacy-safe copy: claimant identity masked, claim facts kept."""
    out = json.loads(json.dumps(claim))  # deep copy
    c = out.get("claimant") or {}
    c["name"] = mask_name(c.get("name", ""))
    c["customer_id"] = mask_id(c.get("customer_id", ""))
    c["phone"] = mask_contact(c.get("phone", ""))
    c["email"] = mask_contact(c.get("email", ""))
    vehicle = (out.get("incident") or {}).get("vehicle")
    if vehicle and vehicle.get("reg_no"):
        vehicle["reg_no"] = mask_id(vehicle["reg_no"])
    out["anonymized"] = True
    return out


def money(amount: Optional[float], currency: str = "INR") -> str:
    if amount is None:
        return "not decided yet"
    return f"{currency} {amount:,.0f}"


def settlement_note(amounts: dict) -> str:
    """Precomputed money reconciliation so the model never fumbles arithmetic.

    E.g. claimed 64,500 - approved 59,500 == deductible 5,000 means the
    deductible was already adjusted in this settlement.
    """
    claimed = amounts.get("claimed")
    approved = amounts.get("approved")
    deductible = amounts.get("deductible") or 0.0
    currency = amounts.get("currency", "INR")
    if approved is None:
        return "Settlement not decided yet - no amount has been fixed."
    if approved == 0:
        return "Nothing is payable on this claim as it was not approved."
    if claimed is None:
        return "Approved amount is fixed; claimed amount not recorded."
    diff = claimed - approved
    if deductible and abs(diff - deductible) < 1.0:
        return (f"Reconciliation: approved = claimed - deductible. The deductible "
                f"of {currency} {deductible:,.0f} has already been adjusted inside "
                f"this settlement, so no further deduction is expected.")
    if diff <= 0:
        return (f"Approved meets the full claim (difference {currency} "
                f"{abs(diff):,.0f}).")
    return (f"Approved is {currency} {diff:,.0f} less than claimed; the decision "
            "letter lists the breakup (sub-limits, deductible, co-pay, "
            "non-medical items).")


def extract_key_params(claim: dict, status: dict) -> dict:
    """Flatten the claim into the parameters that condition the AI prompt."""
    amounts = claim.get("amounts", {}) or {}
    incident = claim.get("incident", {}) or {}
    action = claim.get("next_action", {}) or {}
    return {
        "claim_id": claim.get("claim_id"),
        "policy_type": claim.get("policy_type"),
        "product": claim.get("product"),
        "coverage_type": claim.get("coverage_type"),
        "status": status.get("label"),
        "status_code": status.get("code"),
        "status_explained": status.get("plain"),
        "submitted_at": claim.get("submitted_at"),
        "updated_at": claim.get("updated_at"),
        "incident_date": incident.get("date"),
        "incident_summary": incident.get("description"),
        "claimed_amount": money(amounts.get("claimed"), amounts.get("currency", "INR")),
        "approved_amount": money(amounts.get("approved"), amounts.get("currency", "INR")),
        "deductible": money(amounts.get("deductible"), amounts.get("currency", "INR")),
        "co_pay_percent": amounts.get("copay_percent"),
        "settlement_note": settlement_note(amounts),
        "estimated_days_left": status.get("sla_days"),
        "next_action_who": action.get("who"),
        "next_action_what": action.get("what"),
        "next_action_due": action.get("due"),
    }


def format_history(claim: dict, limit: int = 8) -> list[str]:
    """Human-readable claim history bullets, newest last, capped."""
    events = claim.get("history") or []
    lines = []
    for item in events[-limit:]:
        when = str(item.get("at", ""))[:16].replace("T", " ")
        lines.append(f"{when} - {item.get('event', 'update').replace('_', ' ')}: {item.get('note', '')}")
    return lines


def anonymized_view(claim: dict) -> dict:
    """Status card data for the UI: anonymized identity + mapped status."""
    view = anonymize_claim(claim)
    view["status"] = map_status(claim.get("status_code", ""))
    return view
