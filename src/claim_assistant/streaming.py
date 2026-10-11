"""Streaming helpers for the web chat fast-path (provider=auto).

Why: even the 3B local model needs ~13-14 s to WRITE a full structured
answer before a normal POST can return it. Streaming moves the first
words to ~1-2 s when the model is warm.

Cold start (model not loaded in Ollama):
  -> probe_warm() fails in ~0.4 s, so the UI gets the instant rule-based
     answer immediately while the model loads in the background
     (keep_alive keeps it warm for the next question).

Warm:
  -> POST /api/chat/stream says "stream" and the UI renders tokens live.

The deterministic rule path stays available (provider=rule / offline
demo). Nothing here raises - every failure degrades to the rule answer.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request


def probe_model_warm(base_url: str, model: str, keep_alive: str,
                     warm_timeout: float = 1.5) -> tuple[bool, str]:
    """Try to get a 1-token reply within warm_timeout.

    Returns (is_warm, info). A completed fast reply means the model is
    loaded; a slow reply means it is loading right now - the probe call
    itself keeps the load going, so the NEXT question will be warm.
    Never raises.
    """
    box: dict = {}

    def job() -> None:
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/chat",
                data=json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False, "keep_alive": keep_alive,
                    "options": {"temperature": 0.0, "num_predict": 1},
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                json.loads(resp.read().decode("utf-8"))
            box["secs"] = time.perf_counter() - t0
        except Exception:  # noqa: BLE001 - the demo must not crash
            box["secs"] = time.perf_counter() - t0
            box["error"] = True

    t = threading.Thread(target=job, daemon=True)
    t.start()
    t.join(warm_timeout)
    if box.get("error"):
        return False, "ollama unreachable"
    if box.get("secs") is not None:
        return True, f"model ready ({box['secs']:.1f}s probe)"
    return False, f"{model} is loading in the background"


def ollama_token_stream(base_url: str, model: str, keep_alive: str,
                        system_prompt: str, user_prompt: str,
                        num_predict: int = 350):
    """Yield ('token', chunk) pairs from Ollama /api/chat (stream=True).

    The final item is ('done', stats_dict). On any error the first item
    is ('error', message) and the generator stops - never raises.
    """
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": True,
        "keep_alive": keep_alive,
        "options": {"temperature": 0.3, "num_predict": num_predict},
    }).encode("utf-8")
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/chat", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                try:
                    chunk = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                tok = (chunk.get("message") or {}).get("content")
                if tok:
                    yield "token", tok
                if chunk.get("done"):
                    break
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
            OSError) as exc:
        yield "error", f"ollama: {exc}"
        return
    yield "done", {"secs": time.perf_counter() - t0}


def sse_event(data: dict) -> str:
    """One Server-Sent-Event frame as a text string."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
