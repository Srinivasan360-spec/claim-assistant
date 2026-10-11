"""Retrieval over the glossary and synthetic policy documents.

Deliberately dependency-free (keyword scoring) so the demo runs offline;
the interface is small enough to swap in embeddings later if desired.
"""

from __future__ import annotations

import difflib
import re
from typing import Optional

from .config import DATA_DIR, POLICY_DOCS_DIR, load_json


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9%]+", (text or "").lower())


class Glossary:
    def __init__(self, path=None):
        self.terms: dict = load_json(path or (DATA_DIR / "glossary.json"), {})

    def lookup(self, term: str) -> Optional[dict]:
        """Exact -> case-insensitive -> substring match; never raises."""
        if not term:
            return None
        key = term.strip().lower()
        if key in self.terms:
            return {"term": key, **self.terms[key]}
        for k, v in self.terms.items():
            if k.lower() == key:
                return {"term": k, **v}
        for k, v in self.terms.items():
            if key in k.lower() or k.lower() in key:
                return {"term": k, **v}
        return None

    def terms_in(self, text: str) -> list[dict]:
        """Glossary terms mentioned in a piece of text (for prompt conditioning)."""
        found, seen = [], set()
        low = (text or "").lower()
        for term in self.terms:
            if re.search(r"\b" + re.escape(term) + r"\b", low) and term not in seen:
                seen.add(term)
                found.append({"term": term, **self.terms[term]})
        return found

    def search(self, question: str) -> Optional[dict]:
        """Best glossary entry for a free-text question (token + typo matching)."""
        q_tokens = [t for t in _tokens(question) if len(t) > 2]
        if not q_tokens:
            return None
        best, best_score = None, 0
        for term in self.terms:
            t_tokens = _tokens(term)
            score = sum(1 for t in t_tokens if t in q_tokens)
            if term in (question or "").lower():
                score += 2
            if score > best_score:
                best, best_score = term, score
        if best and best_score >= 1:
            return {"term": best, **self.terms[best]}
        # typo tolerance: "dedcutible" -> "deductible"
        matches = difflib.get_close_matches((question or "").lower(),
                                             list(self.terms), n=1, cutoff=0.75)
        if not matches:
            word_matches = []
            for tok in q_tokens:
                word_matches += difflib.get_close_matches(
                    tok, list(self.terms), n=1, cutoff=0.8)
            matches = word_matches
        if matches:
            return {"term": matches[0], **self.terms[matches[0]]}
        return None


class PolicyDocs:
    """Sentence-level keyword scorer over the synthetic policy texts."""

    def __init__(self, docs_dir=None):
        self.passages: list[dict] = []
        root = docs_dir or POLICY_DOCS_DIR
        try:
            files = sorted(root.glob("*.txt"))
        except OSError:
            files = []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            for para in text.split("\n\n"):
                for sentence in re.split(r"(?<=[.!?])\s+", para.strip()):
                    sentence = sentence.strip()
                    if len(sentence) > 40:
                        self.passages.append({"doc": f.stem, "text": sentence})
        self._token_sets = [(p, set(_tokens(p["text"]))) for p in self.passages]

    def _relevant(self, claim: dict) -> list[dict]:
        """Passages from the claim's own policy document, health/motor first."""
        ptype = (claim.get("policy_type") or "").lower()
        wanted = [p for p in self.passages if p["doc"].startswith(ptype)]
        return wanted or self.passages

    def find(self, query: str, claim: Optional[dict] = None, top_k: int = 2) -> list[dict]:
        q_tokens = set(_tokens(query))
        if claim:
            q_tokens |= set(_tokens(claim.get("coverage_type") or ""))
            q_tokens |= set(_tokens(claim.get("product") or ""))
        candidates = self._relevant(claim) if claim else self.passages
        scored = []
        for p in candidates:
            p_tokens = set(_tokens(p["text"]))
            score = len(q_tokens & p_tokens)
            if score:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:top_k]]


def build_context(question: str, claim: Optional[dict], glossary: Glossary,
                  policy: PolicyDocs) -> dict:
    """Assemble retrieval context: glossary hits + relevant policy passages.

    Priority: terms the customer actually wrote > money terms from the claim
    itself > fuzzy search extras (which can match generic words like 'claim').
    """
    glossary_hits = glossary.terms_in(question)
    if claim:
        wanted = ["deductible"]
        if claim.get("amounts", {}).get("copay_percent"):
            wanted.append("co-pay")
        for term in wanted:
            hit = glossary.lookup(term)
            if hit and hit["term"] not in {h["term"] for h in glossary_hits}:
                glossary_hits.append(hit)
    extra = glossary.search(question)
    if extra and extra["term"] not in {h["term"] for h in glossary_hits}:
        glossary_hits.append(extra)
    passages = policy.find(question, claim=claim)
    return {"glossary": glossary_hits[:5], "policy_passages": passages}
