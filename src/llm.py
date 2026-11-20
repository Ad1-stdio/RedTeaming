import hashlib
import json
import time
from pathlib import Path

from ollama import Client

from .config import CFG

client = Client(host=CFG.ollama_base_url, timeout=CFG.request_timeout)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "llm"


def _cache_path(messages: list[dict], temperature: float) -> Path:
    """Same model + messages + temperature always maps to the same file."""
    key = json.dumps([CFG.local_model, messages, temperature], sort_keys=True)
    return CACHE_DIR / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def chat(messages: list[dict], temperature: float = 0.2, use_cache: bool | None = None) -> str:
    """Send a list of {"role", "content"} messages to the model and return the reply text.

    The cache returns identical output for identical input. That saves time while you
    develop, but it hides run-to-run variation, so switch it off (use_cache=False or
    USE_CACHE=0 in .env) when you measure repeat runs.
    """
    use_cache = CFG.use_cache if use_cache is None else use_cache
    path = _cache_path(messages, temperature)
    if use_cache and path.exists():
        return json.loads(path.read_text())["reply"]

    last_error = None
    for attempt in range(CFG.max_retries + 1):
        try:
            response = client.chat(
                model=CFG.local_model,
                messages=messages,
                options={"temperature": temperature},
            )
            reply = response.message.content
            if use_cache:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"reply": reply}))
            return reply
        except Exception as e:  # network hiccup, Ollama busy or not running
            last_error = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed after {CFG.max_retries + 1} attempts: {last_error}")


def generate_response(prompt: str) -> str:
    """Single-prompt convenience wrapper around chat()."""
    return chat([{"role": "system", "content": CFG.system_prompt},
                {"role": "user", "content": prompt}])
