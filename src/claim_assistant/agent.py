"""Single-agent orchestration: fetch -> preprocess -> retrieve -> generate.

One agent handles the whole conversation: claim lookups, follow-up
questions in the context of a claim, standalone glossary questions, and
free-form insurance questions answered by the local LLM.
`handle()` never raises - every failure path returns a helpful message,
so the demo cannot crash.
"""

from __future__ import annotations

import re
import traceback
from typing import Optional

from .config import Config
from .llm import generate
from .mock_api import LocalClaimSource, make_source
from .preprocess import extract_key_params, format_history, map_status
from .retrieval import Glossary, PolicyDocs, build_context

CLAIM_ID_RE = re.compile(r"\b(CLM-\d{3,})\b", re.IGNORECASE)
POLICY_ID_RE = re.compile(r"\b(POL-[A-Z]-\d{3,})\b", re.IGNORECASE)

# LLMs sometimes answer with fancy bullets/emoji that break Windows terminals
_BULLET_MAP = str.maketrans({
    "•": "-", "‣": "-", "◦": "-", "▪": "-", "●": "-", "○": "-",
    "➤": "-", "→": "-", "►": "-", "▸": "-", "·": "-",
    "​": "", "﻿": "", " ": " ",
})


def sanitize(text: str) -> str:
    """Normalize generator output so every terminal can render it."""
    text = (text or "").translate(_BULLET_MAP)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


HELP_TEXT = """I can explain any claim in plain language. Try one of these:
- "Explain claim CLM-1001"
- "What happens next for CLM-1002?"
- "Why was my amount reduced?" (after naming a claim)
- "What is a deductible?" (any insurance term)
Type /claims to see every claim in the demo data."""

# Small talk that must not be stuffed into a claim-conditioned prompt.
_SMALL_TALK_RE = re.compile(
    r"^(hi+|hello+|hey+|thanks?|thank you|ok(?:ay)?|cool|bye+"
    r"|good (?:morning|evening|afternoon)|who are you|what is your name"
    r"|how are you)[\s!.?]*$", re.I)


def _is_small_talk(message: str) -> bool:
    return bool(_SMALL_TALK_RE.match((message or "").strip()))


class ClaimAgent:
    def __init__(self, cfg: Optional[Config] = None, source=None,
                 glossary: Optional[Glossary] = None,
                 policy: Optional[PolicyDocs] = None):
        self.cfg = cfg or Config()
        self.source = source or make_source(self.cfg)
        self.glossary = glossary or Glossary()
        self.policy = policy or PolicyDocs()
        self._local = LocalClaimSource()  # fallback when a remote API dies

    # ------------------------------------------------------------- fetching --
    def list_claims(self) -> list[dict]:
        claims = self.source.list_claims()
        return claims if claims else self._local.list_claims()

    def _fetch(self, claim_id: str) -> Optional[tuple[dict, dict]]:
        """(claim, live_status) via the configured source, local fallback."""
        claim = self.source.get_claim(claim_id)
        status = self.source.get_status(claim_id)
        if claim is None or status is None:
            local_claim = self._local.get_claim(claim_id)
            local_status = self._local.get_status(claim_id)
            if local_claim is not None:
                return local_claim, local_status or map_status(local_claim.get("status_code"))
            return None
        if status is None:
            status = {"code": claim.get("status_code", "SUBMITTED"),
                      **map_status(claim.get("status_code"))}
        return claim, status

    def _find_claim(self, message: str) -> Optional[dict]:
        """Resolve a claim from id in text, policy id, or the fallback store."""
        m = CLAIM_ID_RE.search(message)
        if m:
            return self._fetch(m.group(1).upper())
        m = POLICY_ID_RE.search(message)
        if m:
            found = None
            if hasattr(self._local.store, "find_by_policy"):
                found = self._local.store.find_by_policy(m.group(1))
            if found:
                return self._fetch(found["claim_id"])
        return None

    # ------------------------------------------------------------- handling --
    def handle(self, message: str, session: Optional[dict] = None) -> dict:
        """Process one conversational turn. Returns a response dict, never raises."""
        session = session if session is not None else {}
        session.setdefault("history", [])
        message = (message or "").strip()
        try:
            if not message:
                return self._reply(HELP_TEXT, session)
            if _is_small_talk(message):
                return self._small_talk(message, session)
            fetched = self._find_claim(message)
            if fetched is None and session.get("claim_id"):
                fetched = self._fetch(session["claim_id"])  # follow-up in context
            if fetched is None:
                if CLAIM_ID_RE.search(message):
                    mentioned = CLAIM_ID_RE.search(message).group(1).upper()
                    known = ", ".join(c["claim_id"] for c in self.list_claims()[:3])
                    return self._reply(
                        f"I could not find claim {mentioned} in the demo data. "
                        f"Try one of these: {known} - or type /claims for the full list.",
                        session)
                return self._no_claim(message, session)
            return self._explain(fetched, message, session)
        except Exception:  # noqa: BLE001 - a demo must never show a traceback
            traceback.print_exc()
            return self._reply(
                "Sorry - something went wrong on my side. Please try that again, "
                "or type /claims to pick a claim.", session)

    def _small_talk(self, message: str, session: dict) -> dict:
        """Friendly one-liner; keeps the helpful persona alive."""
        profile = session.get("profile") if isinstance(session.get("profile"), dict) else None
        first_name = str((profile or {}).get("name") or "").split()
        you = f", {first_name[0]}" if first_name else ""
        known = ", ".join(c["claim_id"] for c in self.list_claims()[:3])
        return self._reply(
            f"Hi{you}! I explain insurance claims in plain language. "
            f"Name a claim (for example {known}) and I will walk you through "
            "the money, next steps and anything you must do - or ask me what "
            "any insurance term means.", session)

    def _no_claim(self, message: str, session: dict) -> dict:
        """No claim in play: glossary hit, LLM free-question, else a guide."""
        hit = self.glossary.search(message)
        questionish = any(w in message.lower() for w in
                          ("what", "meaning", "mean", "explain", "define",
                           "difference", "coverage", "cover")) \
            or message.strip().endswith("?")
        if hit and questionish:
            text = (f"**{hit['term'].title()}** - {hit['definition']}\n\n"
                    f"In plain words: {hit['plain']}\n\n"
                    f"Example: {hit['example']}\n\n"
                    "Want this applied to a claim? Name the claim "
                    "(for example: Explain claim CLM-1001) and I will connect "
                    "the term to your coverage, deductible and next steps.")
            return self._reply(text, session)

        profile = session.get("profile") if isinstance(session.get("profile"), dict) else None
        if profile is not None or self.cfg.provider.lower() != "rule":
            return self._explain_free_question(message, session, profile)

        # Offline rule mode: echo the actual question in a helpful guide
        # instead of one static help message for everything.
        first_name = str((profile or {}).get("name") or "").split()
        greeting = f"Hi {first_name[0]}, " if first_name else ""
        known = ", ".join(c["claim_id"] for c in self.list_claims()[:3])
        text = (f'{greeting}I do not have a claim or glossary match for '
                f'"{message[:90]}". Here is what I can do with it:\n'
                f"- Explain a claim - try: {known}\n"
                "- Explain why an amount changed - e.g. 'Why was my amount "
                "reduced?' after naming a claim\n"
                "- Ask when it will be paid, which documents are pending, or "
                "what happens next\n"
                "- Ask what a term means - e.g. 'What is a sub-limit?'\n\n"
                "Type /claims to see every demo claim.")
        return self._reply(text, session)

    def _explain_free_question(self, message: str, session: dict,
                               profile: Optional[dict]) -> dict:
        """Free-form insurance question with no claim in play: answer the
        ACTUAL question with the local LLM, grounded in the insurer practice
        docs and glossary - personalised, never a canned template."""
        from .llm import build_user_prompt  # local import avoids a cycle

        retrieval = build_context(message, None, self.glossary, self.policy)
        status = map_status("SUBMITTED")
        params = {"claim_id": None, "coverage_type": None,
                  "product": "general insurance (no specific claim selected)",
                  "claimed_amount": "n/a", "approved_amount": "not decided yet",
                  "deductible": "not decided yet", "co_pay_percent": None,
                  "currency": "INR", "status": "not applicable",
                  "status_code": None,
                  "status_explained": "The customer asked a general insurance "
                                      "question; no claim is attached to it.",
                  "next_action_who": "customer", "next_action_what": "n/a",
                  "next_action_due": None, "estimated_days_left": None,
                  "updated_at": None, "settlement_note": None,
                  "next_action_who_text": "You"}
        params["status_explained"] = params["status_explained"]
        user_prompt = build_user_prompt(
            params, {"meaning": "General insurance question - no claim stage "
                                "is attached.", "who_acts": "customer",
                     "next_steps": []},
            [], retrieval, message,
            [(r, c[:300]) for r, c in session["history"][-4:]],
            profile=profile)
        result = generate_from_prompt(self.cfg, user_prompt)

        session["history"].append(("user", message))
        session["history"].append(("assistant", sanitize(result.text)))
        return self._reply(result.text, session,
                           provider=result.provider, note=result.note)

    def _explain(self, fetched: tuple, message: str, session: dict) -> dict:
        claim, status = fetched
        session["claim_id"] = claim["claim_id"]
        profile = session.get("profile") if isinstance(session.get("profile"), dict) else None

        params = extract_key_params(claim, status)
        history_lines = format_history(claim)
        retrieval = build_context(message or "explain this claim", claim,
                                  self.glossary, self.policy)
        conversation = [(r, c[:300]) for r, c in session["history"][-4:]]

        result = generate(self.cfg, params, status, history_lines, retrieval,
                          message or "Explain this claim and its next steps.",
                          conversation, profile=profile)

        view = anonymized_view_for(claim, status)
        session["history"].append(("user", message))
        session["history"].append(("assistant", sanitize(result.text)))
        return self._reply(result.text, session,
                           claim_id=claim["claim_id"],
                           claim_view=view,
                           provider=result.provider,
                           note=result.note,
                           api=self.source.name)

    @staticmethod
    def _reply(text: str, session: dict, **extra) -> dict:
        """Stable response schema: every documented key is always present."""
        out = {
            "reply": sanitize(text),
            "claim_id": session.get("claim_id"),
            "claim_view": None,
            "provider": "retrieval",
            "note": "answered from glossary/help - no LLM call needed",
            "api": None,
        }
        out.update(extra)
        return out


def generate_from_prompt(cfg, user_prompt: str):
    """Run the generate() ladder on an already-built user prompt."""
    from . import llm as llm_mod
    return llm_mod.generate_raw(cfg, user_prompt)


def anonymized_view_for(claim: dict, status: dict) -> dict:
    """Compact status card sent to the UI alongside the prose."""
    amounts = claim.get("amounts") or {}
    action = claim.get("next_action") or {}
    return {
        "claim_id": claim.get("claim_id"),
        "status_code": status.get("code"),
        "status_label": status.get("label"),
        "status_meaning": status.get("meaning"),
        "policy_type": claim.get("policy_type"),
        "coverage_type": claim.get("coverage_type"),
        "claimed_amount": amounts.get("claimed"),
        "approved_amount": amounts.get("approved"),
        "currency": amounts.get("currency", "INR"),
        "next_action": action.get("what"),
        "next_action_who": action.get("who"),
        "simulated": status.get("simulated", False),
    }
