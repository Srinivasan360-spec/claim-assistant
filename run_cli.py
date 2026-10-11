#!/usr/bin/env python3
"""CLI entry point - run from the claim-assistant folder:
    python run_cli.py -q "Explain claim CLM-1001"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from claim_assistant.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
