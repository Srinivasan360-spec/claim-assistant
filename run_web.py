#!/usr/bin/env python3
"""Web entry point - run from the claim-assistant folder:
    python run_web.py            # http://127.0.0.1:5000
    python run_web.py --port 5001
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from claim_assistant.web import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
