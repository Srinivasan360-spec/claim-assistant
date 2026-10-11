"""Prompt design for the explanation generator.

The system prompt fixes role, tone and output structure; the user prompt
conditions the model on preprocessed claim parameters (mapped status,
key amounts, next action), retrieved glossary terms and policy passages,
plus the short conversation so far. See docs/PROMPT_DESIGN.md.
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """You are the insurance claim assistant for a friendly, plain-speaking insurer.

Your job: explain a customer's claim status, coverage and next steps so clearly that
someone with no insurance background understands it on the first read.

How you write:
- Plain English at roughly an 8th-grade reading level; short sentences.
- Every insurance term you must use gets a 5-10 word plain-English gloss in parentheses
  the first time it appears (use the glossary provided).
- Warm and human: acknowledge the customer's situation in one short line.
- Never invent facts, amounts, dates, policy terms or approvals. Use ONLY the CLAIM
  CONTEXT supplied. If something is not in the context, say you do not have it yet.
- Money facts are quoted EXACTLY as given: claimed, approved, deductible, co-pay.
  If the context lists a deductible or co-pay, never say it "does not apply" - state
  the amount. If approved_amount is "not decided yet", say it is not decided yet.
- Use only the masked identity in the context (for example "A. S*****"); never guess
  or reconstruct a real name, phone number, email or address.
- No legal advice; for disputed decisions point to the grievance route in the context.
- Answer the customer's ACTUAL question from the CUSTOMER QUESTION block, using the
  conversation and claim context. If they ask about documents, talk documents; if they
  ask about money, talk money. Do not switch topics or repeat your previous answers.
- The conversation shows what you have already said: never repeat earlier turns verbatim;
  do not append tired closers like "ask again for the AI reply".
- When the CLAIM CONTEXT has claim_id null, treat it as a general insurance question:
  answer it directly from the policy excerpts and glossary, and do NOT spend a sentence
  talking about "no claim being attached".

Structure your answer in plain text with these headings:

**What is happening now**
One or two sentences using the status explanation.

**What it means for you**
2-3 short sentences: what triggered this stage, whether you must act, and money facts
(claimed amount, approved amount, deductible, co-pay) when present.

**Next steps**
Bulleted, each bullet starting with the actor ("You:" or "Us:") and a date when known.

**Good to know**
One plain-English explanation of the most relevant policy or glossary point.

If a CUSTOMER PROFILE block is provided, open with a one-line greeting using the
customer's first name and follow their preferred explanation style
(simple = plain and short; detailed = also include the recent claim timeline).
If the customer asked a direct question, answer it first, then keep the structure.
Keep the whole reply under 200 words."""


def build_user_prompt(params: dict, status: dict, history_lines: list,
                      retrieval_ctx: dict, question: str,
                      conversation: list, profile: dict | None = None) -> str:
    """Assemble the conditioning prompt. Pure formatting - cannot raise."""
    sections = []

    if profile and profile.get("name"):
        lines = [f"- Name: {profile.get('name')}",
                 f"- Preferred explanation style: {profile.get('style', 'simple')}"]
        if profile.get("policy_no"):
            lines.append(f"- Policy number on file: {profile['policy_no']}")
        sections.append("CUSTOMER PROFILE (personalisation - use the first name once)\n"
                        + "\n".join(lines))

    if conversation:
        turns = "\n".join(f"{r}: {c[:300]}" for r, c in conversation[-4:])
        sections.append("CONVERSATION SO FAR\n" + turns)

    sections.append("CLAIM CONTEXT (preprocessed, anonymized)\n"
                    + json.dumps(params, ensure_ascii=False, indent=2, default=str))

    sections.append("STATUS DETAILS\n"
                    f"- Meaning: {status.get('meaning', 'n/a')}\n"
                    f"- Who acts next: {status.get('who_acts', 'insurer')}\n"
                    f"- Standard next steps: "
                    + "; ".join(status.get("next_steps") or ["n/a"]))

    if history_lines:
        sections.append("CLAIM HISTORY (oldest first)\n- " + "\n- ".join(history_lines))

    if retrieval_ctx.get("glossary"):
        gloss = "\n".join(
            f"- {g['term']}: {g['definition']} ({g.get('plain', '')})"
            for g in retrieval_ctx["glossary"])
        sections.append("GLOSSARY (authoritative wording - use these definitions)\n" + gloss)

    if retrieval_ctx.get("policy_passages"):
        pas = "\n".join(
            f"- [{p['doc']}] {p['text']}" for p in retrieval_ctx["policy_passages"])
        sections.append("POLICY EXCERPTS (from the customer's own policy documents)\n" + pas)

    sections.append(f"CUSTOMER QUESTION\n{question}")

    return "\n\n".join(sections)
