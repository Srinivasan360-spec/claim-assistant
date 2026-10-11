"""Explanation generation: Ollama (local, no API key) with a deterministic
rule-based fallback so the demo can never error.

Provider resolution (LLM_PROVIDER env / --provider flag):
    auto    try Ollama, fall back to rule-based with an explicit note  (default)
    ollama  Ollama first, still falls back rather than crash
    rule    deterministic template only (offline / tests)

The rule-based path is intent-aware: it classifies the question (why / money /
timeline / documents / next / coverage) and answers that question first
instead of always printing the same template.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .prompting import SYSTEM_PROMPT, build_user_prompt


@dataclass
class GenerationResult:
    text: str
    provider: str
    note: str = ""


class LLMError(Exception):
    """Provider failed (connection, timeout, bad payload)."""


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: int = 120,
                 keep_alive: str = "10m", max_tokens: int = 350):
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.model = model
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.max_tokens = max_tokens

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
                OSError) as exc:
            raise LLMError(f"ollama unreachable: {exc}") from exc

    def warm(self) -> float:
        """Pre-load the model with a 1-token prompt so the next real answer is fast."""
        data = self._post({"model": self.model, "messages": [{
            "role": "user", "content": "ping"}], "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0.0, "num_predict": 1}})
        content = ((data or {}).get("message") or {}).get("content", "").strip()
        if not content:
            raise LLMError("ollama returned an empty message")
        return float((data or {}).get("total_duration") or 0) / 1e9

    def generate(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0.3, "num_predict": self.max_tokens},
        }
        data = self._post(payload)
        content = ((data or {}).get("message") or {}).get("content", "").strip()
        if not content:
            raise LLMError("ollama returned an empty message")
        return content


class RuleBasedProvider:
    """Deterministic template built from the same conditioning context.

    Not a language model - it guarantees a correct, plain-English explanation
    even with no model loaded, and doubles as the offline test path.
    """
    name = "rule-based"

    def generate(self, system: str, user: str) -> str:  # system unused by design
        return rule_based_explanation(user)


_WARM_FLAG: list = []
"""Set after one successful Ollama answer: the model is loaded and warm.
A list is used as a mutable global; tests can clear it to simulate cold."""


def _looks_like_header(line: str) -> bool:
    """Section headers are ALL-CAPS first words on their own line (not bullets/JSON)."""
    s = line.strip()
    if not s or s.startswith(("-", "{", "}", '"')):
        return False
    first = s.split()[0].rstrip(":")
    return len(first) > 1 and first.isupper() and first.isalpha()


def _section(user_prompt: str, header: str) -> str:
    """Return the body under `header`, up to the next section header."""
    try:
        start = user_prompt.index(header)
    except ValueError:
        return ""
    lines = user_prompt[start + len(header):].splitlines()
    out = []
    for i, line in enumerate(lines):
        if i > 0 and _looks_like_header(line):
            break
        out.append(line)
    return "\n".join(out).strip()


def detect_intent(question: str) -> str:
    """Classify what the customer is actually asking; drives the reply shape."""
    ql = (question or "").lower()
    if any(w in ql for w in ("document", "docs", "upload", "papers",
                             "checklist", "missing")):
        return "documents"
    if ql.startswith("why") or any(w in ql for w in (
            " why", "reason", "rejected", "denied", "refused", "declined",
            "not approved")):
        return "why"
    if any(w in ql for w in (
            "how much", "amount", "money", "settlement", "reduced",
            "deductible", "co-pay", "copay", "cost", "refund", "credit",
            "payable", " paid", "pay ", "get for")):
        return "money"
    if any(w in ql for w in (
            "when", "how long", "days", "time", "status", "progress",
            "update", "track", "eta")):
        return "timeline"
    if any(w in ql for w in (
            "next", "do i need", "what should i", "what do i do",
            "action", "steps")):
        return "next"
    if any(w in ql for w in (
            "cover", "coverage", "include", "exclusion", "waiting period")):
        return "coverage"
    return "default"


def _parse_kv(section: str) -> dict:
    """'- Key: value' lines -> dict (keys lower-cased)."""
    out = {}
    for ln in section.splitlines():
        if ln.startswith("- ") and ": " in ln:
            k, _, v = ln[2:].partition(": ")
            out[k.strip().lower()] = v.strip()
    return out


def _bullets(section: str) -> list:
    return [ln[2:].strip() for ln in section.splitlines() if ln.startswith("- ")]


def rule_based_explanation(user_prompt: str) -> str:
    """Render the conditioning context as the system prompt's structure,
    shaped by what the customer actually asked (intent-aware)."""
    params = {}
    block = _section(user_prompt, "CLAIM CONTEXT (preprocessed, anonymized)")
    if block:
        try:
            params = json.loads(block)
        except json.JSONDecodeError:
            params = {}

    kv = _parse_kv(_section(user_prompt, "STATUS DETAILS"))
    meaning = kv.get("meaning", "We are working on your claim.")
    who_acts = (kv.get("who acts next") or "insurer").lower()
    std_steps = [s.strip() for s in
                 (kv.get("standard next steps") or "").split(";") if s.strip()]

    glossary = _bullets(_section(
        user_prompt, "GLOSSARY (authoritative wording - use these definitions)"))
    history = _bullets(_section(user_prompt, "CLAIM HISTORY (oldest first)"))
    passages = _bullets(_section(
        user_prompt, "POLICY EXCERPTS (from the customer's own policy documents)"))
    question = _section(user_prompt, "CUSTOMER QUESTION")
    prof = _parse_kv(_section(user_prompt, "CUSTOMER PROFILE"))

    who = {"customer": "You", "insurer": "Us", "none": "Nobody"}
    who_text = who.get(str(params.get("next_action_who") or "insurer").lower(), "Us")
    status_code = str(params.get("status_code") or "")
    approved = params.get("approved_amount")
    decided = approved not in (None, "", "not decided yet")
    settlement = str(params.get("settlement_note") or "")
    claimed = params.get("claimed_amount", "the claimed amount")
    what = params.get("next_action_what") or "We continue processing your claim."
    due = params.get("next_action_due")
    days = params.get("estimated_days_left")
    action_line = f"- {who_text}: {what}" + (f" (by {due})" if due else "")

    name_parts = (prof.get("name") or "").split()
    greeting = f"Hi {name_parts[0]}," if name_parts else None
    detailed = "detail" in (prof.get("preferred explanation style") or "simple").lower()

    def next_steps_section() -> list:
        out = ["**Next steps**", action_line]
        if days and who_text == "Us" and status_code not in ("REJECTED", "CLOSED"):
            out.append(f"- Us: expected progress within about {days} days of this stage.")
        return out

    def history_section() -> list:
        if not history:
            return []
        tail = history if detailed else history[-3:]
        return ["", "**Recent updates**"] + [f"- {h}" for h in tail]

    def good_to_know(intent: str) -> list:
        out = ["", "**Good to know**"]
        pick = None
        if intent in ("money", "why"):
            for g in glossary:
                if g.lower().startswith(("deductible", "co-pay", "copay")):
                    pick = g
                    break
        if pick is None and glossary:
            pick = glossary[0]
        if intent == "coverage" and passages:
            out.append(passages[0])
        elif pick:
            out.append(pick)
        else:
            out.append("Your claim follows the stages in your policy document; "
                       "each stage has a fixed service window.")
        return out

    intent = detect_intent(question)
    lines = []

    if intent == "default":
        lines.append("**What is happening now**")
        lines.append(str(params.get("status_explained")
                         or "Your claim is moving through our system."))
        lines.append("")
        lines.append("**What it means for you**")
        lines.append(f"{meaning} Your claim {params.get('claim_id') or ''} "
                     f"({params.get('coverage_type') or 'coverage'} cover) was "
                     f"{claimed}.")
        if decided and status_code != "REJECTED":
            lines.append(f"Approved so far: {approved}.")
        ded = params.get("deductible")
        if ded and ded != "not decided yet":
            lines.append(f"Deductible applies: {ded}.")
        copay = params.get("co_pay_percent")
        if copay:
            lines.append(f"Co-pay: {copay}% of the payable amount.")
        if settlement:
            lines.append(settlement)
        lines.append("")
        lines.extend(next_steps_section())
        lines.extend(good_to_know(intent))
        lines.extend(history_section() if detailed else [])
    else:
        lead = ""
        detail = []
        if intent == "money":
            lead = f"You asked about the money: {claimed} was claimed, "
            if decided and status_code != "REJECTED":
                lead += f"{approved} is approved."
            elif status_code == "REJECTED":
                lead += "nothing is payable because the claim was not approved."
            else:
                lead += "the payable amount is not decided yet."
            if settlement:
                lead += " " + settlement
            detail = [meaning]
            ded = params.get("deductible")
            if ded and ded != "not decided yet":
                detail.append(f"Deductible: {ded}.")
            copay = params.get("co_pay_percent")
            if copay:
                detail.append(f"Co-pay: {copay}% of the payable amount.")
        elif intent == "why":
            lead = meaning
            if decided:
                lead += f" On money: {claimed} was claimed, {approved} is approved."
            if settlement:
                lead += " " + settlement
            detail = []
            if status_code in ("REJECTED", "PARTIALLY_APPROVED"):
                detail = [f"- {s}" for s in std_steps]
            if not detail and not history:
                detail = ["- No timeline events recorded for this claim yet."]
        elif intent == "timeline":
            lead = (f"Stage: {params.get('status')}. "
                    f"{params.get('status_explained') or 'Your claim is progressing.'}")
            if params.get("updated_at"):
                lead += (" Last updated "
                         f"{str(params['updated_at'])[:16].replace('T', ' ') }.")
            if days:
                lead += f" This stage normally takes about {days} days."
            detail = [f"- {s}" for s in std_steps]
            if not detail and not history:
                detail = ["- No timeline events recorded for this claim yet."]
        elif intent == "documents":
            lead = f"{who_text}: {what}" + (f" (by {due})" if due else "")
            status_line = params.get("status_explained")
            if status_line:
                lead += ". " + str(status_line)
            detail = [f"- {s}" for s in std_steps]
            doc_passages = [p for p in passages if any(w in p.lower() for w in
                            ("document", "intimate", "required", "submit",
                             "upload", "original"))]
            if doc_passages:
                detail.append(doc_passages[0])
            elif passages:
                detail.append(passages[0])
        elif intent == "next":
            lead = f"{who_text}: {what}" + (f" (by {due})." if due else ".")
            detail = [meaning] + [f"- {s}" for s in std_steps]
        else:  # coverage
            lead = (f"Your {params.get('coverage_type') or 'policy'} cover under "
                    f"{params.get('product') or 'your policy'} applies here.")
            detail = passages[:2] or [meaning]

        lines.append("**Short answer**")
        lines.append(lead.strip())
        lines.append("")
        if detail:
            lines.append("**Details**")
            lines.extend(detail)
        lines.extend(history_section())
        lines.append("")
        lines.extend(next_steps_section())
        lines.extend(good_to_know(intent))

    if greeting:
        lines.insert(0, greeting)
        lines.insert(1, "")
    return "\n".join(lines)


def _run_llm(cfg, ollama: OllamaProvider, user: str, fast: str,
             provider_pref: str) -> GenerationResult:
    """Shared auto / explicit-ollama execution ladder. Never raises."""
    def llm_job(out: dict) -> None:
        t0 = time.perf_counter()
        try:
            out["text"] = ollama.generate(SYSTEM_PROMPT, user)
            out["secs"] = time.perf_counter() - t0
        except LLMError as exc:
            out["error"] = str(exc)
            out["secs"] = time.perf_counter() - t0

    if provider_pref != "auto":  # explicit "ollama": wait for the full answer
        box: dict = {}
        llm_job(box)
        if box.get("text"):
            return GenerationResult(box["text"], "ollama",
                                    f"model={cfg.ollama_model} ({box['secs']:.1f}s)")
        return GenerationResult(fast, "rule-based",
                                f"Ollama failed ({box.get('error')}); "
                                "used rule-based fallback")

    # auto mode: never make the user wait for a cold model. If the model is
    # already warm in Ollama memory (keep_alive), wait the short cold-budget
    # window for the real AI answer. Otherwise ship the instant rule-based
    # answer immediately while Ollama loads in the background - the next turn
    # is then answered by the model within the same window.
    box: dict = {}
    warm = bool(_WARM_FLAG)
    budget = cfg.llm_cold_timeout if warm else 0.5
    t = threading.Thread(target=llm_job, kwargs={"out": box}, daemon=True)
    t.start()
    t.join(min(budget, cfg.llm_timeout))
    if box.get("text"):
        _WARM_FLAG[:] = [True]
        return GenerationResult(box["text"], "ollama",
                                f"model={cfg.ollama_model} ({box['secs']:.1f}s)")
    if box.get("error"):
        _WARM_FLAG.clear()
        return GenerationResult(fast, "rule-based",
                                f"Ollama unavailable ({box['error']}); "
                                "used rule-based fallback")
    reason = ("model replied slower than the instant-answer window"
              if warm else
              f"{cfg.ollama_model} is loading in the background")
    return GenerationResult(fast, "rule-based",
                            f"instant answer - {reason}; ask again for the AI reply")


def generate_raw(cfg, user_prompt: str) -> GenerationResult:
    """Same auto/ollama/rule ladder as generate(), on a prebuilt prompt used
    by the free-question path (agent._explain_free_question)."""
    provider_pref = (cfg.provider or "auto").lower()

    if provider_pref == "rule":
        return GenerationResult(rule_based_explanation(user_prompt), "rule-based",
                                "deterministic mode requested")

    ollama = OllamaProvider(cfg.ollama_base_url, cfg.ollama_model,
                            timeout=cfg.llm_timeout,
                            keep_alive=getattr(cfg, "ollama_keep_alive", "10m"),
                            max_tokens=getattr(cfg, "llm_max_tokens", 350))
    fast = rule_based_explanation(user_prompt)
    return _run_llm(cfg, ollama, user_prompt, fast, provider_pref)


def generate(cfg, params: dict, status: dict, history_lines: list,
             retrieval_ctx: dict, question: str, conversation: list,
             profile: dict | None = None) -> GenerationResult:
    """Generate the explanation. NEVER raises - always returns usable text.

    Speed contract: with provider=auto the reply is always produced within a
    small fixed budget. If the model is still warming (first call after
    startup), the instant rule-based answer ships now and the model answer
    replaces it once it lands - so the user never waits for cold-start.
    """
    user = build_user_prompt(params, status, history_lines, retrieval_ctx,
                             question, conversation, profile=profile)
    return generate_raw(cfg, user)
