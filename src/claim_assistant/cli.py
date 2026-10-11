"""Command-line interface (deliverable: "basic web interface or CLI").

Usage:
    python run_cli.py                       # interactive chat
    python run_cli.py -q "Explain CLM-1001" # one-shot query, exit 0
    python run_cli.py --claims              # list demo claims
    python run_cli.py --provider rule       # force offline generator

Interactive commands: /claims, /claim <id>, /help, /quit
"""

from __future__ import annotations

import argparse
import sys

from .agent import ClaimAgent
from .config import Config

def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if sys.stdout.isatty() else text


def _print_card(view: dict) -> None:
    if not view:
        return
    money = f"{view.get('currency', 'INR')} {view.get('claimed_amount'):,.0f}" \
        if isinstance(view.get("claimed_amount"), (int, float)) else "n/a"
    approved = view.get("approved_amount")
    approved_txt = (f"{view.get('currency', 'INR')} {approved:,.0f}"
                    if isinstance(approved, (int, float)) else "not decided yet")
    print(_bold(f"  {view.get('claim_id')}  [{view.get('status_label')}]"))
    print(f"  {view.get('policy_type', '?')} cover | {view.get('coverage_type', '?')}")
    print(f"  claimed {money} | approved {approved_txt}")
    if view.get("next_action"):
        actor = "you" if view.get("next_action_who") == "customer" else "us"
        print(f"  next: {actor} - {view['next_action']}")
    print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claim-assistant",
        description="Insurance Claim Explanation Assistant (CLI)")
    p.add_argument("-q", "--question", help="ask a single question and exit")
    p.add_argument("--claim", help="start the session on this claim id")
    p.add_argument("--claims", action="store_true", help="list demo claims and exit")
    p.add_argument("--provider", choices=["auto", "ollama", "rule"],
                   default=None, help="explanation backend (default: env or auto)")
    p.add_argument("--api-url", default=None,
                   help="mock status API base URL (default: in-process)")
    p.add_argument("--no-color", action="store_true", help="disable ANSI styling")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config()
    if args.provider:
        cfg.provider = args.provider
    if args.api_url:
        cfg.mock_api_url = args.api_url

    agent = ClaimAgent(cfg)

    if args.claims:
        for c in agent.list_claims():
            print(f"{c['claim_id']}  {c.get('policy_type', ''):7} "
                  f"{c.get('coverage_type', ''):24} {c.get('status_code', '')}")
        return 0

    session: dict = {"history": [], "claim_id": None}
    if args.claim:
        resp = agent.handle(f"Explain claim {args.claim}", session)
        _print_card(resp.get("claim_view") or {})
        print(resp["reply"] + "\n")

    if args.question:
        resp = agent.handle(args.question, session)
        _print_card(resp.get("claim_view") or {})
        print(resp["reply"])
        return 0

    print(_bold("Insurance Claim Explanation Assistant") +
          f"  ({_dim(cfg.describe())})")
    print("Type a claim ID or a question. /claims to list, /quit to exit.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            return 0
        if not line:
            continue
        if line.lower() in ("/quit", "/exit", "/q"):
            print("Goodbye!")
            return 0
        if line.lower() == "/help":
            print(HELP_LINE)
            continue
        if line.lower() == "/claims":
            for c in agent.list_claims():
                print(f"  {c['claim_id']}  {c.get('coverage_type', '')} "
                      f"({c.get('status_code', '')})")
            continue
        if line.lower().startswith("/claim "):
            line = f"Explain claim {line.split(None, 1)[1]}"
        resp = agent.handle(line, session)
        _print_card(resp.get("claim_view") or {})
        print(resp["reply"] + "\n")
        if resp.get("note") and resp.get("provider") != "ollama":
            print(_dim(f"[{resp['provider']}: {resp['note']}]"))


HELP_LINE = ('Commands: type any claim ID (e.g. CLM-1001), ask about a term '
             '("what is a deductible"), /claims, /claim <id>, /help, /quit')

if __name__ == "__main__":
    sys.exit(main())
