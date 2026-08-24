"""The model adapter — one place the platform talks to a language model.

Provider is CONFIGURABLE, never hard-coded. `AI_PROVIDER` selects it and
`AI_MODEL` overrides the model; the default is Anthropic (Claude). A tenant on a
different model must never be silently swapped onto another one, so nothing
outside this file names a vendor.

    AI_PROVIDER=anthropic   ANTHROPIC_API_KEY=...   (default)
    AI_PROVIDER=gemini      GEMINI_API_KEY=...
    AI_PROVIDER=openai      OPENAI_API_KEY=...
    AI_PROVIDER=none        generation disabled (agents park for a human)

--------------------------------------------------------------------------
THE RULE THIS FILE EXISTS TO ENFORCE
--------------------------------------------------------------------------
No key, no network, or an unparseable answer => `AIUnavailable`. The calling
agent then parks the task as `needs_human` and raises an `ops.alert`. It NEVER
falls back to canned copy, and it never ships a half-parsed answer. A client
reading a placeholder caption in their dashboard is worse than an empty slot with
an honest reason next to it.

No new dependencies: `requests` is already pinned in app/requirements.txt.
"""
from __future__ import annotations

import json
import os
import re

import requests

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o",
}
AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "60"))
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "4000"))

# Base URLs are overridable so the platform can be pointed at a gateway, a proxy,
# a self-hosted endpoint, or a stub server during an end-to-end test — without
# any test-only branch existing in production code.
BASE_URLS = {
    "anthropic": os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
    "gemini": os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"),
    "openai": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com"),
}


def base_url(p: str) -> str:
    return os.environ.get(f"{p.upper()}_BASE_URL", BASE_URLS[p]).rstrip("/")


class AIUnavailable(Exception):
    """Generation could not happen honestly. Carries a reason fit to show a human."""


def provider() -> str:
    return os.environ.get("AI_PROVIDER", "anthropic").strip().lower()


def model_name() -> str:
    return os.environ.get("AI_MODEL") or DEFAULT_MODELS.get(provider(), "")


def _key() -> str:
    env = {"anthropic": "ANTHROPIC_API_KEY",
           "gemini": "GEMINI_API_KEY",
           "openai": "OPENAI_API_KEY"}.get(provider())
    return os.environ.get(env, "").strip() if env else ""


def available() -> bool:
    """True when a real generation call could actually be made."""
    return provider() in DEFAULT_MODELS and bool(_key())


# ---------------------------------------------------------------- transport
def _call_anthropic(system: str, prompt: str) -> str:
    r = requests.post(
        base_url("anthropic") + "/v1/messages",
        headers={"x-api-key": _key(), "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model_name(), "max_tokens": AI_MAX_TOKENS, "system": system,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=AI_TIMEOUT)
    if r.status_code >= 400:
        raise AIUnavailable(f"anthropic_{r.status_code}: {r.text[:200]}")
    blocks = r.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _call_gemini(system: str, prompt: str) -> str:
    url = (f'{base_url("gemini")}/v1beta/models/'
           f"{model_name()}:generateContent")
    r = requests.post(url, params={"key": _key()},
                      json={"system_instruction": {"parts": [{"text": system}]},
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {"maxOutputTokens": AI_MAX_TOKENS}},
                      timeout=AI_TIMEOUT)
    if r.status_code >= 400:
        raise AIUnavailable(f"gemini_{r.status_code}: {r.text[:200]}")
    cands = r.json().get("candidates", [])
    if not cands:
        raise AIUnavailable("gemini returned no candidates")
    return "".join(p.get("text", "") for p in cands[0]["content"]["parts"])


def _call_openai(system: str, prompt: str) -> str:
    r = requests.post(
        base_url("openai") + "/v1/chat/completions",
        headers={"Authorization": "Bearer " + _key()},
        json={"model": model_name(), "max_tokens": AI_MAX_TOKENS,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": prompt}]},
        timeout=AI_TIMEOUT)
    if r.status_code >= 400:
        raise AIUnavailable(f"openai_{r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


_TRANSPORT = {"anthropic": _call_anthropic, "gemini": _call_gemini, "openai": _call_openai}


def generate(system: str, prompt: str) -> str:
    """Raw text generation. Raises AIUnavailable rather than returning anything fake."""
    p = provider()
    if p == "none":
        raise AIUnavailable("generation disabled (AI_PROVIDER=none)")
    if p not in _TRANSPORT:
        raise AIUnavailable(f"unknown AI_PROVIDER '{p}'")
    if not _key():
        raise AIUnavailable(f"{p} API key not configured")
    try:
        text = _TRANSPORT[p](system, prompt)
    except requests.RequestException as e:
        raise AIUnavailable(f"{p} unreachable: {type(e).__name__}")
    if not (text or "").strip():
        raise AIUnavailable(f"{p} returned an empty response")
    return text


# ------------------------------------------------------------- JSON output
def _extract_json(text: str):
    """Pull the JSON object out of a reply, tolerating ```json fences and prose."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise AIUnavailable("model did not return parseable JSON")


def generate_json(system: str, prompt: str, required_keys: tuple = (), _retry=True) -> dict:
    """Generation that must come back as a JSON object with the expected shape.

    One corrective retry, then give up honestly. Half-understood output is never
    passed on to a client — the agent parks it for a human instead.
    """
    text = generate(system, prompt)
    try:
        data = _extract_json(text)
        if not isinstance(data, dict):
            raise AIUnavailable("model returned JSON that is not an object")
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise AIUnavailable(f"model response missing keys: {', '.join(missing)}")
        return data
    except AIUnavailable:
        if not _retry:
            raise
        return generate_json(
            system,
            prompt + "\n\nYour previous reply could not be parsed. Reply with ONLY a "
                     "single valid JSON object and no other text, no markdown fences.",
            required_keys, _retry=False)
