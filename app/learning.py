"""The learning loop — the READ half.

`learning_signals` has been written to since Phase C (every human override,
every edited reply, every takeover). Nothing ever read it back, so the platform
recorded its mistakes and then made them again. This module closes that loop.

Design rules it inherits and must not break:

  * NEVER INVENT. A signal is a real recorded human correction or it does not
    exist. This module summarises rows; it does not infer, extrapolate, or
    generate advice from an empty table. No signals => `{}` => agents behave
    exactly as they did before. Silence is honest.
  * ADVISORY, NOT AUTHORITATIVE. Learning shifts a *default*, never a hard rule.
    It can make the Quality Control agent stricter; it can never make the ads or
    publishing human gate open by itself. A system that could learn its way out
    of asking permission is the exact failure the Golden Rule forbids.
  * READ-ONLY AND CHEAP. Pure functions over rows already fetched. No I/O here,
    so it is trivially testable and cannot slow the worker down.

Shape of what agents receive, under `ctx.store['learning']`:

    {
      "content": {
        "signals": 7,
        "corrections": 5,          # human edited/replaced the agent's output
        "rejections": 2,           # human threw it away
        "reject_rate": 0.29,
        "caution": True,           # enough rejections to warrant stricter review
        "themes": ["price", "tone"],
        "last_reason": "too pushy, softened the CTA"
      },
      ...
    }
"""
from __future__ import annotations

from collections import Counter

# Signal types that mean "a human changed what the agent produced".
CORRECTION_TYPES = {"edited_reply", "edited_output", "status_change", "override"}
# Signal types that mean "a human threw the agent's work away".
REJECTION_TYPES = {"rejected", "ignored", "discarded", "human_took_over"}

# How many recent signals to consider, and the reject rate above which an agent
# is flagged for stricter review. Deliberately conservative: one bad week should
# not permanently brand an agent.
WINDOW = 50
CAUTION_RATE = 0.34
MIN_FOR_CAUTION = 3

# Recurring themes worth naming back to the agent. Matched against the human's
# free-text `reason`, which is the only place the *why* is ever recorded.
THEMES = {
    "price": ("price", "cost", "rate", "expensive", "cheap", "discount", "rs", "pkr"),
    "tone": ("tone", "pushy", "aggressive", "rude", "formal", "casual", "salesy"),
    "accuracy": ("wrong", "incorrect", "inaccurate", "false", "not true", "we don't"),
    "language": ("urdu", "english", "language", "translate", "script"),
    "timing": ("too soon", "too early", "too late", "timing", "spam"),
    "brand": ("brand", "logo", "colour", "color", "font", "style guide"),
}


def _themes_from(reasons: list[str]) -> list[str]:
    """Name the recurring themes in the humans' own words. Never invents one."""
    hits: Counter = Counter()
    for reason in reasons:
        low = (reason or "").lower()
        for theme, words in THEMES.items():
            if any(w in low for w in words):
                hits[theme] += 1
    return [t for t, _ in hits.most_common(3)]


def summarise(signals: list[dict]) -> dict:
    """Group raw learning_signals rows by agent into per-agent guidance.

    `signals` is a list of dicts with at least `agent`, `signal_type`, and
    optionally `reason`. Rows missing an agent are dropped rather than guessed.
    """
    if not signals:
        return {}

    by_agent: dict[str, list[dict]] = {}
    for s in signals[:WINDOW]:
        agent = (s or {}).get("agent")
        if agent:
            by_agent.setdefault(agent, []).append(s)

    out: dict[str, dict] = {}
    for agent, rows in by_agent.items():
        corrections = sum(1 for r in rows if r.get("signal_type") in CORRECTION_TYPES)
        rejections = sum(1 for r in rows if r.get("signal_type") in REJECTION_TYPES)
        total = len(rows)
        rate = round(rejections / total, 2) if total else 0.0
        reasons = [r.get("reason") for r in rows if r.get("reason")]

        out[agent] = {
            "signals": total,
            "corrections": corrections,
            "rejections": rejections,
            "reject_rate": rate,
            # Caution needs BOTH a meaningful rate and enough rows to mean
            # anything. One rejection out of one is not a pattern.
            "caution": rejections >= MIN_FOR_CAUTION and rate >= CAUTION_RATE,
            "themes": _themes_from(reasons),
            "last_reason": reasons[0] if reasons else None,
        }
    return out


def guidance_for(store: dict, agent_name: str) -> dict:
    """What one agent should know about its own past corrections.

    Returns {} when there is nothing recorded — which must leave the agent's
    behaviour completely unchanged.
    """
    return ((store or {}).get("learning") or {}).get(agent_name, {}) or {}


def should_review_harder(store: dict, agent_name: str) -> bool:
    """True when humans have rejected this agent's work often enough recently
    that its output deserves a stricter pass before it reaches a client."""
    return bool(guidance_for(store, agent_name).get("caution"))
