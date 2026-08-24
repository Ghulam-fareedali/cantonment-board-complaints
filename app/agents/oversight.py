"""Oversight agents — 12 Quality Control and 13 Campaign Optimizer.

These are the two agents the workflow diagram promised and the code did not have.
Both are *oversight*: they judge work rather than produce it. Neither one is
allowed to take an irreversible action on its own.

------------------------------------------------------------------------------
12  QUALITY CONTROL
------------------------------------------------------------------------------
Sits between the agents that GENERATE (content 02, design 03) and the agents that
SPEND or PUBLISH (ads 06, publishing 05). Until now the only thing standing there
was a human reading a dashboard card. This agent does the mechanical part of that
read first, so the human sees a shortlist of real problems instead of everything.

What it actually checks — all of it verifiable, none of it taste:
  1. Unbacked claims  — reuses guards.scan_plan, the same function the creative
     agents use on themselves. Checking twice is the point: this catches work
     that reached the pipeline by any other route.
  2. Artwork safety   — reuses guards.sanitize_svg. Anything that would render in
     a logged-in dashboard must be inert.
  3. Completeness     — empty or placeholder deliverables never reach a client.
  4. Brand consistency — only where the tenant's config actually states a brand
     rule. No config rule => no finding. It does not invent a style guide.
  5. Learning history — if humans have been rejecting this agent's work lately,
     say so on the card so the reviewer knows to look harder.

Verdicts: `pass` (advance), `flag` (advance, with named concerns attached), and
`block` (never advances — parks as needs_human). Anything touching money, public
posting, safety, or a fabricated fact is a BLOCK, never a flag.

------------------------------------------------------------------------------
13  CAMPAIGN OPTIMIZER
------------------------------------------------------------------------------
Reads real campaign performance and proposes what to change. It is the agent that
turns "we can see the numbers" into "something acts on the numbers".

The single most important property, and the reason this file is careful:
**an optimizer that can change budgets is an AI with a credit card.** So:

  * It NEVER executes. It produces a proposal. Every proposal that would move
    money declares human_review=True and parks exactly like the ads agent.
  * It NEVER invents a metric. No spend rows, no leads, no revenue => it does not
    guess a CPL. It returns needs_human with an ops.alert naming what is missing.
    A confident number computed from nothing is worse than no number.
  * A null denominator is null, not zero. Zero leads on real spend is a genuine
    finding; zero leads with no recorded spend is an absence of data. The two are
    never collapsed.
  * Recommendations are ranked by evidence, and each carries the numbers it was
    derived from, so a human can check the arithmetic rather than trust it.
"""
from __future__ import annotations

from .contract import Agent, Task, Context, AgentResult
from . import guards
from ..learning import guidance_for, should_review_harder

# ---------------------------------------------------------------------------
# tunables — conservative on purpose; a human still approves every change
# ---------------------------------------------------------------------------
MIN_LEADS_FOR_JUDGEMENT = 5        # below this, a CPL is noise, not a signal
MIN_SPEND_FOR_JUDGEMENT = 1000     # in the tenant's own currency
SCALE_ROAS = 3.0                   # earning this well => propose more budget
CUT_CPL_MULTIPLE = 2.0             # this far above the account average => propose a cut
MAX_BUDGET_STEP = 0.30             # never propose more than a 30% move in one go

PLACEHOLDER_MARKERS = ("lorem ipsum", "tbd", "todo", "xxx", "placeholder",
                       "your text here", "[insert", "sample text")


# ===========================================================================
# 12 — QUALITY CONTROL
# ===========================================================================
class QualityControlAgent(Agent):
    name = "quality"                                    # Agent 12

    def handles(self, t):
        return t in {"quality_check", "review_output"}

    # -- individual checks ---------------------------------------------------

    def _check_claims(self, subject: dict, ctx: Context) -> list[dict]:
        """Every unsourced price, offer or promise. Always blocking."""
        return [{"severity": "block", "check": "unbacked_claim", "detail": c}
                for c in guards.scan_plan(subject, ctx.config)]

    def _check_artwork(self, subject) -> list[dict]:
        """Artwork renders inside a logged-in dashboard, so it must be inert."""
        findings: list[dict] = []
        for svg in _collect_svgs(subject):
            try:
                _, removed = guards.sanitize_svg(svg)
            except guards.UnsafeSVG as e:
                findings.append({"severity": "block", "check": "artwork_unsafe",
                                 "detail": str(e)})
                continue
            if removed:
                findings.append({"severity": "block", "check": "artwork_sanitised",
                                 "detail": f"active content removed: {', '.join(removed)}"})
        return findings

    def _check_complete(self, subject: dict) -> list[dict]:
        """Nothing empty or half-written reaches a client."""
        findings: list[dict] = []
        items = subject.get("deliverables") or []
        if not items:
            findings.append({"severity": "flag", "check": "no_deliverables",
                             "detail": "nothing was produced to review"})
        for item in items:
            title = (item.get("title") or "").strip()
            body = item.get("body")
            if not title:
                findings.append({"severity": "block", "check": "missing_title",
                                 "detail": "deliverable has no title"})
            if body in (None, "", {}, []):
                findings.append({"severity": "block", "check": "empty_body",
                                 "detail": f"'{title or 'untitled'}' has no content"})
            for marker in PLACEHOLDER_MARKERS:
                if marker in _text_of(item).lower():
                    findings.append({"severity": "block", "check": "placeholder_text",
                                     "detail": f"'{title or 'untitled'}' still contains "
                                               f"placeholder text ({marker})"})
                    break
        return findings

    def _check_brand(self, subject, ctx: Context) -> list[dict]:
        """Only checks rules the tenant actually stated. No config => no finding.

        This is where a lesser version of this agent would invent a style guide
        and start rejecting work for violating rules nobody wrote down.
        """
        brand = ctx.config.get("brand") or {}
        findings: list[dict] = []
        blob = _text_of(subject)

        banned = [w for w in (brand.get("banned_words") or []) if w]
        for word in banned:
            if word.lower() in blob.lower():
                findings.append({"severity": "block", "check": "banned_word",
                                 "detail": f"brand config forbids '{word}'"})

        required = [w for w in (brand.get("required_mentions") or []) if w]
        for word in required:
            if word.lower() not in blob.lower():
                findings.append({"severity": "flag", "check": "missing_required_mention",
                                 "detail": f"brand config expects '{word}' to appear"})
        return findings

    # -- the contract --------------------------------------------------------

    def run(self, task: Task, ctx: Context) -> AgentResult:
        subject = task.payload.get("subject") or task.payload.get("result") or {}
        produced_by = task.payload.get("produced_by") or "unknown"

        if not subject:
            # Fail closed: nothing to review is not the same as reviewed and fine.
            return AgentResult(
                status="needs_human",
                result={"verdict": "block", "findings": [{
                    "severity": "block", "check": "nothing_to_review",
                    "detail": "quality_check ran with no subject attached"}]},
                events=[{"type": "ops.alert", "payload": {
                    "provider": "platform",
                    "message": "Quality check received no output to review — "
                               "the upstream task produced nothing."}}])

        findings: list[dict] = []
        findings += self._check_claims(subject, ctx)
        findings += self._check_artwork(subject)
        findings += self._check_complete(subject)
        findings += self._check_brand(subject, ctx)

        # The learning loop, applied: an agent humans keep rejecting gets a
        # stricter pass. Advisory only — it raises attention, never lowers a gate.
        if should_review_harder(ctx.store, produced_by):
            g = guidance_for(ctx.store, produced_by)
            findings.append({
                "severity": "flag", "check": "history",
                "detail": f"humans rejected {g.get('rejections')} of "
                          f"{g.get('signals')} recent outputs from {produced_by}"
                          + (f" (themes: {', '.join(g['themes'])})" if g.get("themes") else "")})

        blocks = [f for f in findings if f["severity"] == "block"]
        flags = [f for f in findings if f["severity"] == "flag"]
        verdict = "block" if blocks else ("flag" if flags else "pass")

        summary = {"verdict": verdict, "produced_by": produced_by,
                   "findings": findings, "blocking": len(blocks), "advisory": len(flags)}

        if blocks:
            # Never advances. A human decides what to do with broken work.
            return AgentResult(
                status="needs_human", human_review=True,
                result={**summary, "deliverables": [{
                    "kind": "quality_report",
                    "title": f"Quality check FAILED — {produced_by} "
                             f"({len(blocks)} blocking)",
                    "body": summary}]},
                events=[{"type": "quality.blocked",
                         "payload": {"produced_by": produced_by,
                                     "findings": blocks}}])

        return AgentResult(
            status="done",
            result=summary,
            context_writes={"last_quality_check": summary},
            events=[{"type": "quality.passed",
                     "payload": {"produced_by": produced_by, "verdict": verdict,
                                 "advisory": len(flags)}}])


# ===========================================================================
# 13 — CAMPAIGN OPTIMIZER
# ===========================================================================
class CampaignOptimizerAgent(Agent):
    name = "optimizer"                                  # Agent 13

    def handles(self, t):
        return t in {"optimize_campaign", "review_performance"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        perf = (task.payload.get("performance")
                or ctx.store.get("campaign_performance") or {})
        campaigns = perf.get("campaigns") or []

        # ---- honest degradation: no real numbers => no recommendations ----
        if not campaigns:
            return AgentResult(
                status="needs_human",
                result={"blocked": "no_performance_data",
                        "detail": "no campaign spend or lead data available"},
                events=[{"type": "ops.alert", "payload": {
                    "provider": "meta",
                    "message": "Optimizer has no performance data to work from. "
                               "Connect the ad account and let spend sync before "
                               "asking for optimisation — recommendations without "
                               "real numbers would be guesses."}}])

        analysed, skipped = [], []
        for c in campaigns:
            row = _analyse_campaign(c)
            (skipped if row.get("insufficient") else analysed).append(row)

        if not analysed:
            return AgentResult(
                status="needs_human",
                result={"blocked": "insufficient_volume",
                        "reviewed": len(skipped), "campaigns": skipped},
                events=[{"type": "ops.alert", "payload": {
                    "provider": "meta",
                    "message": f"{len(skipped)} campaign(s) found but none has enough "
                               f"volume to judge (need {MIN_LEADS_FOR_JUDGEMENT}+ leads "
                               f"or {MIN_SPEND_FOR_JUDGEMENT}+ spend). Letting them run "
                               f"is the correct action; changing them now would be noise."}}])

        # Account average CPL, computed only over campaigns that actually have one.
        cpls = [c["cpl"] for c in analysed if c.get("cpl") is not None]
        avg_cpl = round(sum(cpls) / len(cpls), 2) if cpls else None

        recommendations = []
        for c in analysed:
            recommendations += _recommend(c, avg_cpl)
        recommendations.sort(key=lambda r: r.get("confidence", 0), reverse=True)

        report = {
            "reviewed": len(analysed),
            "skipped_low_volume": len(skipped),
            "account_avg_cpl": avg_cpl,
            "campaigns": analysed,
            "recommendations": recommendations,
            # Provenance so nobody trusts a stale number by accident.
            "data_as_of": perf.get("as_of"),
            "currency": perf.get("currency"),
        }

        if not recommendations:
            # A real and useful answer: nothing needs changing.
            return AgentResult(
                status="done",
                result={**report, "verdict": "no_change_recommended"},
                context_writes={"last_optimization": report},
                events=[{"type": "optimization.completed",
                         "payload": {"changes_proposed": 0}}])

        # Anything here moves money. It parks. Always.
        return AgentResult(
            status="needs_human", human_review=True,
            result={**report, "verdict": "changes_proposed",
                    "deliverables": [{
                        "kind": "optimization",
                        "title": f"Budget recommendations — {len(recommendations)} "
                                 f"change(s) for your approval",
                        "body": report}]},
            events=[{"type": "optimization.proposed",
                     "payload": {"changes_proposed": len(recommendations),
                                 "account_avg_cpl": avg_cpl}}])


# ---------------------------------------------------------------------------
# helpers — pure, so every branch is testable without a database
# ---------------------------------------------------------------------------
def _num(v):
    """Coerce to float, or None. A missing metric must never become 0."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _analyse_campaign(c: dict) -> dict:
    spend = _num(c.get("spend"))
    leads = _num(c.get("leads"))
    revenue = _num(c.get("revenue"))
    name = c.get("name") or c.get("id") or "unnamed campaign"

    out = {"campaign": name, "id": c.get("id"), "platform": c.get("platform"),
           "spend": spend, "leads": leads, "revenue": revenue,
           "cpl": None, "roas": None}

    # Null denominator => null metric. Never 0, never a fabricated ratio.
    if spend is not None and leads:
        out["cpl"] = round(spend / leads, 2)
    if spend and revenue is not None:
        out["roas"] = round(revenue / spend, 2)

    enough = ((leads or 0) >= MIN_LEADS_FOR_JUDGEMENT
              or (spend or 0) >= MIN_SPEND_FOR_JUDGEMENT)
    if not enough:
        out["insufficient"] = True
        out["reason"] = "not enough volume to judge yet"
    return out


def _recommend(c: dict, avg_cpl: float | None) -> list[dict]:
    """Turn one campaign's real numbers into concrete, checkable proposals."""
    recs: list[dict] = []
    name, cpl, roas = c["campaign"], c.get("cpl"), c.get("roas")

    # Earning well -> propose more budget, capped.
    if roas is not None and roas >= SCALE_ROAS:
        recs.append({
            "action": "increase_budget",
            "campaign": name, "campaign_id": c.get("id"),
            "change_pct": int(MAX_BUDGET_STEP * 100),
            "because": f"ROAS {roas} is at or above the {SCALE_ROAS} scaling "
                       f"threshold on {c.get('spend')} spend",
            "evidence": {"spend": c.get("spend"), "revenue": c.get("revenue"),
                         "roas": roas},
            "confidence": 0.8 if (c.get("leads") or 0) >= MIN_LEADS_FOR_JUDGEMENT * 2 else 0.6,
            "irreversible": True,
        })

    # Paying far over the odds per lead -> propose a cut, not a kill.
    if cpl is not None and avg_cpl and cpl >= avg_cpl * CUT_CPL_MULTIPLE:
        recs.append({
            "action": "reduce_budget",
            "campaign": name, "campaign_id": c.get("id"),
            "change_pct": -int(MAX_BUDGET_STEP * 100),
            "because": f"CPL {cpl} is {round(cpl / avg_cpl, 1)}x the account "
                       f"average of {avg_cpl}",
            "evidence": {"cpl": cpl, "account_avg_cpl": avg_cpl,
                         "leads": c.get("leads"), "spend": c.get("spend")},
            "confidence": 0.7,
            "irreversible": True,
        })

    # Real spend, zero leads. A genuine finding — but a human pauses it, not us.
    if (c.get("spend") or 0) >= MIN_SPEND_FOR_JUDGEMENT and c.get("leads") == 0:
        recs.append({
            "action": "review_for_pause",
            "campaign": name, "campaign_id": c.get("id"),
            "because": f"{c.get('spend')} spent with no recorded leads",
            "evidence": {"spend": c.get("spend"), "leads": 0},
            "confidence": 0.9,
            "irreversible": True,
        })
    return recs


def _text_of(obj) -> str:
    """Flatten any nested structure into searchable text."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_text_of(v) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(_text_of(v) for v in obj)
    return str(obj) if obj is not None else ""


def _collect_svgs(obj) -> list[str]:
    """Find every SVG document anywhere in a generated structure."""
    found: list[str] = []
    if isinstance(obj, str):
        if "<svg" in obj.lower():
            found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            found += _collect_svgs(v)
    elif isinstance(obj, list):
        for v in obj:
            found += _collect_svgs(v)
    return found
