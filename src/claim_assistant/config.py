"""Runtime configuration for the claim explanation assistant.

Everything is environment-driven with safe defaults, so a fresh clone runs
with zero configuration:
    LLM_PROVIDER      auto | ollama | rule     (default: auto)
    OLLAMA_MODEL      model name               (default: llama3.2:3b, fast on CPU)
    OLLAMA_BASE_URL                               (default: http://localhost:11434)
    OLLAMA_KEEP_ALIVE how long the model stays loaded (default: 10m)
    MOCK_API_URL      remote mock API base     (default: empty = in-process store)
    CLAIMS_PORT       web UI + API port        (default: 5000)
    LLM_TIMEOUT       max seconds for a full answer       (default: 120)
    LLM_COLD_TIMEOUT  seconds before auto mode gives up waiting
                      for the FIRST answer while the model is still cold
                      (default: 12 - then an instant rule-based answer)
    LLM_MAX_TOKENS    max output tokens per answer        (default: 350)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
POLICY_DOCS_DIR = DATA_DIR / "policy_docs"
TEMPLATES_DIR = PROJECT_ROOT / "templates"


def _load_dotenv() -> None:
    """Minimal .env loader (stdlib only); real env vars always win."""
    env_file = Path(".env")
    if not env_file.exists():
        env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass  # never fail startup because of a dotenv file


_load_dotenv()


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass
class Config:
    provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "llama3.2:3b"))
    ollama_base_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_keep_alive: str = field(default_factory=lambda: _env("OLLAMA_KEEP_ALIVE", "10m"))
    mock_api_url: str = field(default_factory=lambda: _env("MOCK_API_URL", ""))
    web_port: int = field(default_factory=lambda: int(_env("CLAIMS_PORT", "5000")))
    llm_timeout: int = field(default_factory=lambda: int(_env("LLM_TIMEOUT", "120")))
    llm_cold_timeout: int = field(default_factory=lambda: int(_env("LLM_COLD_TIMEOUT", "12")))
    llm_max_tokens: int = field(default_factory=lambda: int(_env("LLM_MAX_TOKENS", "350")))

    def describe(self) -> str:
        source = self.mock_api_url or "in-process store"
        return (f"provider={self.provider} model={self.ollama_model} "
                f"ollama={self.ollama_base_url} tokens<={self.llm_max_tokens} "
                f"data={source}")


def load_json(path: Path, default):
    """Read JSON, returning `default` instead of raising (never crash the demo)."""
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
