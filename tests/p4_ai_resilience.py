"""P4.7 - what the AI layer does when a provider says no. Runs in the container.

The failure this is written against actually happened: the account ran out of
credit, every agent failed, and nothing anywhere said why.
"""
import os, sys
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY") or "test-key-a"
os.environ["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY") or "test-key-g"
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY") or "test-key-o"
os.environ["AI_PROVIDER"] = "anthropic"
os.environ["AI_PROVIDERS"] = "anthropic,gemini,openai"
# The real deployment sets a model-level chain; pin the test to its own so it
# is testing the code and not whatever the container happens to be configured with.
os.environ["AI_MODEL_CHAIN"] = "anthropic,gemini,openai"

from app import ai

ok = bad = 0
def check(name, good, note=""):
    global ok, bad
    if good: ok += 1; print("PASS ", name, note)
    else:    bad += 1; print("FAIL ", name, note)

calls = []

def broke_credit(system, prompt):
    calls.append("anthropic")
    raise ai.AIUnavailable("anthropic_400: your credit balance is too low")

def works(system, prompt):
    calls.append("gemini")
    return "the fallback answered"

def also_broken(system, prompt):
    calls.append("openai")
    raise ai.AIUnavailable("openai_429: rate limit exceeded")

ai._TRANSPORT.update({"anthropic": broke_credit, "gemini": works, "openai": also_broken})
ai._DOWN.clear()

# 1. A dead first provider does not stop the workforce.
out = ai.generate("s", "p")
check("work continues on the next provider", out == "the fallback answered", out)
check("the dead provider was actually tried first", calls[:2] == ["anthropic", "gemini"], str(calls))

# 2. The reason is understood, not just recorded as 'error'.
# The breaker is keyed by provider AND model, because one model can be rate
# limited upstream while the next on the same provider is fine.
check("an exhausted account is recognised as a credit problem",
      ai._DOWN.get("anthropic:default", {}).get("reason") == "credit",
      str(ai._DOWN.get("anthropic:default", {}).get("reason")))

# 3. It is not asked again straight away.
calls.clear()
ai.generate("s", "p")
check("a provider that just said no is left alone", "anthropic" not in calls, str(calls))

# 4. Health is honest while degraded.
h = ai.health()
check("health reports degraded-but-working", h["status"] == "ok" and h["usable"] >= 1,
      "%s usable=%s" % (h["status"], h["usable"]))
check("and names the resting provider and why",
      any(c["provider"] == "anthropic" and c["state"] == "resting"
          and c["reason"] == "credit" for c in h["chain"]))

# 5. When everything is down, the error says what was tried.
ai._TRANSPORT["gemini"] = broke_credit
ai._DOWN.clear()
try:
    ai.generate("s", "p")
    check("all providers down raises", False, "it returned instead")
except ai.AIUnavailable as e:
    msg = str(e)
    check("all providers down raises with the detail",
          "every AI provider refused" in msg and "credit" in msg, msg[:90])

# 6. A fallback provider is never handed the configured provider's model name.
os.environ["AI_MODEL"] = "claude-sonnet-4-5"
ai._ACTIVE = "gemini"
check("the fallback uses its own model, not AI_MODEL",
      ai.model_name() == ai.DEFAULT_MODELS["gemini"], ai.model_name())
ai._ACTIVE = None

print("\n=== %d passed, %d failed ===" % (ok, bad))
sys.exit(1 if bad else 0)
