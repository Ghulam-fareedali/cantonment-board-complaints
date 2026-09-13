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
import time
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


# The provider currently being attempted. generate() sets it while it walks the
# chain, so model_name() and _key() pick the right model and key for whichever
# provider is being tried rather than for the one named in AI_PROVIDER.
_ACTIVE = None
_ACTIVE_MODEL = None


def configured_provider() -> str:
    return os.environ.get("AI_PROVIDER", "anthropic").strip().lower()


def provider() -> str:
    return _ACTIVE or configured_provider()


def model_name() -> str:
    """AI_MODEL names a model on the configured provider, so it must not be
    handed to a fallback provider that has never heard of it."""
    if _ACTIVE_MODEL:
        return _ACTIVE_MODEL
    p = provider()
    per_provider = os.environ.get("AI_MODEL_" + p.upper())
    if per_provider:
        return per_provider.strip()
    if p == configured_provider():
        return os.environ.get("AI_MODEL") or DEFAULT_MODELS.get(p, "")
    return DEFAULT_MODELS.get(p, "")


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


# ---------------------------------------------------------------- resilience
# One provider is a single point of failure for the entire AI workforce, and the
# most common way it fails is the dullest: the account runs out of credit. When
# that happened here, every agent failed, retried, dead-lettered, and nothing
# said why. The chain below fails over instead, and when it cannot, it says what
# it tried and what each one answered.

_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY"}

# How long to stop asking a provider that just told us no. An exhausted account
# will still be exhausted in a second; hammering it wastes the whole task's time
# budget on a question already answered.
_COOLDOWN = {"credit": 900, "auth": 900, "rate": 60, "unreachable": 30, "bad_response": 10}

_DOWN = {}          # provider -> {"until": ts, "why": str, "reason": str}


def _key_for(p: str) -> str:
    return os.environ.get(_KEY_ENV.get(p, ""), "").strip()


def classify(message: str) -> str:
    """What kind of no was that? The answer decides how long to wait."""
    m = (message or "").lower()
    if any(w in m for w in ("credit", "quota", "billing", "insufficient",
                            "exceeded your current", "payment")):
        return "credit"
    if any(w in m for w in ("_401", "_403", "unauthorized", "invalid api key",
                            "invalid x-api-key", "permission")):
        return "auth"
    if "_429" in m or "rate limit" in m or "overloaded" in m:
        return "rate"
    if any(w in m for w in ("unreachable", "timeout", "_500", "_502", "_503", "_504")):
        return "unreachable"
    return "bad_response"


def providers() -> list:
    """The order to try, keys permitting.

    AI_PROVIDERS is explicit. Otherwise the configured provider goes first and
    every other provider that has a key follows it, because a key that exists is
    a fallback somebody already paid for.
    """
    raw = os.environ.get("AI_PROVIDERS", "").strip()
    if raw:
        chain = [x.strip().lower() for x in raw.split(",") if x.strip()]
    else:
        first = configured_provider()
        chain = [first] + [x for x in DEFAULT_MODELS if x != first]
    seen, out = set(), []
    for x in chain:
        if x in _TRANSPORT and x not in seen and _key_for(x):
            seen.add(x)
            out.append(x)
    return out


def chain_entries() -> list:
    """The (provider, model) pairs to try, in order.

    A provider is not the only thing that goes out of action -- one model can be
    rate limited upstream while the next is fine, which is the normal state of
    the free tiers. AI_MODEL_CHAIN spells the order out:

        AI_MODEL_CHAIN=openai=gpt-4o,openai=google/gemma-4-31b-it:free

    With no chain set this falls back to one entry per provider that has a key.
    """
    raw = os.environ.get("AI_MODEL_CHAIN", "").strip()
    if not raw:
        return [(x, None) for x in providers()]
    out = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        prov, _, model = item.partition("=")
        prov = prov.strip().lower()
        if prov in _TRANSPORT and _key_for(prov):
            out.append((prov, model.strip() or None))
    return out


def _mark_down(key: str, why: str):
    reason = classify(why)
    _DOWN[key] = {"until": time.time() + _COOLDOWN.get(reason, 30),
                  "why": why[:200], "reason": reason}


def _resting(key: str):
    d = _DOWN.get(key)
    if d and d["until"] > time.time():
        return d
    _DOWN.pop(key, None)
    return None


def health() -> dict:
    """What the AI layer can honestly say about itself right now."""
    entries = chain_entries()
    out = []
    for p, m in entries:
        key = "%s:%s" % (p, m or "default")
        rest = _resting(key)
        out.append({"provider": p, "model": m or DEFAULT_MODELS.get(p),
                    "state": "resting" if rest else "ready",
                    "reason": rest["reason"] if rest else None,
                    "last_error": rest["why"] if rest else None,
                    "retry_in_seconds": int(rest["until"] - time.time()) if rest else 0})
    usable = [x for x in out if x["state"] == "ready"]
    return {"configured": configured_provider(),
            "chain": out,
            "usable": len(usable),
            "status": "ok" if usable else ("degraded" if out else "unconfigured")}


_TRANSPORT = {"anthropic": _call_anthropic, "gemini": _call_gemini, "openai": _call_openai}


def generate(system: str, prompt: str) -> str:
    """Raw text generation. Raises AIUnavailable rather than returning anything fake."""
    global _ACTIVE
    if configured_provider() == "none":
        raise AIUnavailable("generation disabled (AI_PROVIDER=none)")

    global _ACTIVE_MODEL
    chain = chain_entries()
    if not chain:
        raise AIUnavailable("no AI provider has an API key configured")

    tried, skipped = [], []
    for p, m in chain:
        key = "%s:%s" % (p, m or "default")
        rest = _resting(key)
        if rest:
            skipped.append(f"{key} resting ({rest['reason']}, {int(rest['until'] - time.time())}s)")
            continue
        _ACTIVE, _ACTIVE_MODEL = p, m
        try:
            text = _TRANSPORT[p](system, prompt)
            if not (text or "").strip():
                raise AIUnavailable(f"{key} returned an empty response")
            if (p, m) != chain[0]:
                print(f"[ai] served by fallback {key}", flush=True)
            return text
        except requests.RequestException as e:
            why = f"{key} unreachable: {type(e).__name__}"
            _mark_down(key, why)
            tried.append(why)
        except AIUnavailable as e:
            _mark_down(key, str(e))
            tried.append(str(e))
        finally:
            _ACTIVE, _ACTIVE_MODEL = None, None

    # Every provider said no. Say which, and what each one said: an agent that
    # dies with "AI unavailable" and no reason is how a 22-day outage happens.
    detail = "; ".join(tried + skipped) or "no provider was in a state to try"
    raise AIUnavailable("every AI provider refused: " + detail)


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
