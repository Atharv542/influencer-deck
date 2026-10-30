
from __future__ import annotations

import logging
import time

import requests

from .config import env

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
    "ollama": "llama3.2",
}
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY",
            "openrouter": "OPENROUTER_API_KEY", "ollama": None}
OPENAI_STYLE_URLS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}


def provider() -> str | None:
    p = (env("LLM_PROVIDER") or "").strip().lower()
    if p:
        return p
    return "anthropic" if env("ANTHROPIC_API_KEY") else None


def model_name(p: str) -> str:
    return env("LLM_MODEL") or (env("ANTHROPIC_MODEL") if p == "anthropic" else None) or DEFAULT_MODELS.get(p, "")


def is_configured() -> bool:
    p = provider()
    if p not in DEFAULT_MODELS:
        if p:
            log.warning("Unknown LLM_PROVIDER %r (use one of %s)", p, ", ".join(DEFAULT_MODELS))
        return False
    key_var = KEY_VARS[p]
    return key_var is None or bool(env(key_var))


def _post(url: str, retries: int = 4, **kw) -> dict:
    last = ""
    for attempt in range(retries):
        r = requests.post(url, timeout=60, **kw)
        if r.status_code == 200:
            return r.json()
        last = f"{r.status_code}: {r.text[:300]}"
        if r.status_code in (429, 500, 502, 503):      # free tiers rate-limit: back off and retry
            time.sleep(6 * (attempt + 1))
            continue
        break
    raise RuntimeError(f"LLM request failed ({last})")


def complete(system: str, prompt: str) -> tuple[str, str]:
    """Returns (text, 'provider/model')."""
    p, model = provider(), None
    model = model_name(p)
    label = f"{p}/{model}"
    if p == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
        resp = client.messages.create(model=model, max_tokens=800, system=system,
                                      messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text"), label
    if p == "gemini":
        data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                     params={"key": env("GEMINI_API_KEY")},
                     json={"systemInstruction": {"parts": [{"text": system}]},
                           "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}})
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(x.get("text", "") for x in parts), label
    
    url = OPENAI_STYLE_URLS.get(p) or f"{env('OLLAMA_HOST', 'http://localhost:11434')}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {env(KEY_VARS[p])}"} if KEY_VARS[p] else {}
    data = _post(url, headers=headers,
                 json={"model": model, "temperature": 0.8,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
    return data["choices"][0]["message"]["content"], label