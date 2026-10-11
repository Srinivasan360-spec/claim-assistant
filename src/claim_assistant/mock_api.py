"""Simulated claim status API (data consideration: "Simulated claim status
API endpoints providing real-time updates").

One ClaimStore powers three surfaces:
  * LocalClaimSource  - in-process, used when MOCK_API_URL is empty (zero-setup demo)
  * api blueprint      - HTTP endpoints served by the web app (real API integration)
  * HttpClaimSource   - the agent's client when MOCK_API_URL points at a server

"Real-time" behaviour: claims flagged simulate_progression advance through
their progression list one stage per `stage_hours` of elapsed time since
submission, so the same claim reports a different status tomorrow.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

from .config import DATA_DIR, load_json
from .preprocess import map_status

_TIME_FMT = "%Y-%m-%dT%H:%M:%S"


class ClaimStore:
    """Loads the synthetic claim records (JSON or CSV) and computes simulated live status."""

    def __init__(self, path=None):
        path = path or (DATA_DIR / "claims.json")
        if str(path).lower().endswith(".csv"):
            self.claims = self._load_csv(path)
        else:
            data = load_json(path, {})
            self.claims = {c["claim_id"]: c for c in data.get("claims", [])
                           if c.get("claim_id")}

    @staticmethod
    def _load_csv(path) -> dict:
        """CSV mirror of claims.json (statement: 'JSON or CSV format')."""
        import csv as csvmod
        claims = {}
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                rows = list(csvmod.DictReader(fh))
        except (OSError, csvmod.Error):
            return claims

        def num(value, cast=float):
            try:
                return cast(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                return None

        for row in rows:
            cid = row.get("claim_id")
            if not cid:
                continue
            claims[cid] = {
                "claim_id": cid,
                "policy_id": row.get("policy_id"),
                "policy_type": row.get("policy_type"),
                "product": row.get("product"),
                "coverage_type": row.get("coverage_type"),
                "status_code": row.get("status_code"),
                "submitted_at": row.get("submitted_at"),
                "updated_at": row.get("updated_at"),
                "claimant": {
                    "name": row.get("claimant_name") or "",
                    "customer_id": row.get("claimant_customer_id") or "",
                    "phone": row.get("claimant_phone") or "",
                    "email": row.get("claimant_email") or "",
                },
                "amounts": {
                    "claimed": num(row.get("claimed_amount")),
                    "approved": num(row.get("approved_amount")),
                    "deductible": num(row.get("deductible")) or 0.0,
                    "copay_percent": num(row.get("copay_percent"), int) or 0,
                    "currency": row.get("currency") or "INR",
                },
                "incident": {
                    "date": row.get("incident_date"),
                    "description": row.get("incident_description"),
                },
                "history": [],
                "next_action": {
                    "who": row.get("next_action_who") or "insurer",
                    "what": row.get("next_action_what"),
                    "due": row.get("next_action_due") or None,
                },
                "simulate_progression":
                    (row.get("simulate_progression") or "").lower() == "true",
                "stage_hours": num(row.get("stage_hours"), int) or 24,
                "progression": [s for s in
                                (row.get("progression") or "").split("|") if s],
            }
        return claims

    def list_claims(self) -> list[dict]:
        """Compact summaries for listings (safe to expose: no PII beyond IDs)."""
        out = []
        for c in self.claims.values():
            out.append({
                "claim_id": c.get("claim_id"),
                "policy_type": c.get("policy_type"),
                "coverage_type": c.get("coverage_type"),
                "status_code": self.live_status(c)["code"],
                "submitted_at": c.get("submitted_at"),
            })
        return sorted(out, key=lambda x: x["claim_id"])

    def get_claim(self, claim_id: str) -> Optional[dict]:
        return self.claims.get((claim_id or "").strip().upper())

    def live_status(self, claim: dict, now: Optional[datetime] = None) -> dict:
        """Stored status, advanced through the progression for simulated claims."""
        code = (claim.get("status_code") or "SUBMITTED").upper()
        simulated = bool(claim.get("simulate_progression"))
        if simulated:
            try:
                submitted = datetime.strptime(claim.get("submitted_at", ""), _TIME_FMT)
                now = now or datetime.now()
                elapsed_h = max((now - submitted).total_seconds() / 3600.0, 0.0)
                stages = claim.get("progression") or [code]
                step_h = float(claim.get("stage_hours") or 24)
                idx = min(int(elapsed_h // step_h), len(stages) - 1)
                # Never move backwards past what the stored history already recorded
                stored_idx = stages.index(code) if code in stages else 0
                code = stages[max(idx, stored_idx)]
            except (ValueError, TypeError):
                pass  # bad timestamps -> keep stored status, never raise
        return {"code": code, "simulated": simulated, **map_status(code)}

    def get_status(self, claim_id: str, now: Optional[datetime] = None) -> Optional[dict]:
        claim = self.get_claim(claim_id)
        if not claim:
            return None
        return {
            "claim_id": claim["claim_id"],
            "as_of": (now or datetime.now()).isoformat(timespec="seconds"),
            "updated_at": claim.get("updated_at"),
            **self.live_status(claim, now=now),
        }

    def find_by_policy(self, policy_id: str) -> Optional[dict]:
        pid = (policy_id or "").strip().upper()
        for c in self.claims.values():
            if (c.get("policy_id") or "").upper() == pid:
                return c
        return None


class LocalClaimSource:
    """In-process claim source (no server required)."""

    name = "in-process store"

    def __init__(self, store: Optional[ClaimStore] = None):
        self.store = store or ClaimStore()

    def list_claims(self) -> list[dict]:
        return self.store.list_claims()

    def get_claim(self, claim_id: str) -> Optional[dict]:
        return self.store.get_claim(claim_id)

    def get_status(self, claim_id: str) -> Optional[dict]:
        return self.store.get_status(claim_id)


class HttpClaimSource:
    """Client for the simulated HTTP API; degrades gracefully, never raises."""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    @property
    def name(self) -> str:
        return self.base_url

    def _get(self, path: str):
        try:
            req = urllib.request.Request(
                f"{self.base_url}{path}", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
            return None  # server down or bad payload -> caller falls back locally

    def list_claims(self) -> list[dict]:
        data = self._get("/api/claims")
        return (data or {}).get("claims", [])

    def get_claim(self, claim_id: str) -> Optional[dict]:
        data = self._get(f"/api/claims/{claim_id}")
        return (data or {}).get("claim")

    def get_status(self, claim_id: str) -> Optional[dict]:
        return self._get(f"/api/claims/{claim_id}/status")


def make_source(cfg) -> object:
    """HTTP source when MOCK_API_URL is set, otherwise in-process.

    If the HTTP server stops responding the agent falls back to the local
    store transparently (see agent.py) so the demo never dead-ends.
    """
    if cfg.mock_api_url:
        return HttpClaimSource(cfg.mock_api_url)
    return LocalClaimSource()


# ---------------------------------------------------------------- blueprint --
def create_api_blueprint(store: Optional[ClaimStore] = None):
    """Flask blueprint with the simulated status endpoints."""
    from flask import Blueprint, jsonify

    store = store or ClaimStore()
    bp = Blueprint("claims_api", __name__)

    @bp.get("/api/health")
    def health():
        return jsonify({"ok": True, "service": "claim-status-api",
                        "claims": len(store.claims)})

    @bp.get("/api/claims")
    def list_claims():
        return jsonify({"claims": store.list_claims()})

    @bp.get("/api/claims/<claim_id>")
    def get_claim(claim_id):
        claim = store.get_claim(claim_id)
        if not claim:
            return jsonify({"error": "claim_not_found", "claim_id": claim_id.upper()}), 404
        # The HTTP API is also privacy-safe: identity masked at the edge
        from .preprocess import anonymize_claim
        return jsonify({"claim": anonymize_claim(claim)})

    @bp.get("/api/claims/<claim_id>/status")
    def get_status(claim_id):
        status = store.get_status(claim_id)
        if not status:
            return jsonify({"error": "claim_not_found", "claim_id": claim_id.upper()}), 404
        return jsonify(status)

    return bp
