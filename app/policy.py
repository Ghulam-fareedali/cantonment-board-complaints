"""Which actions need a human, and who is allowed to change that.

The human gate used to depend on an agent setting `human_review` on its own
result. That works right up until an agent forgets, or a new agent is written by
someone who never knew the convention -- and the failure is silent and lands on
the client's customers. The orchestrator now asks this module instead, so the
gate is a property of the action rather than of the agent's memory.
"""

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

# An action nobody has classified is not automatically safe. Anything in one of
# these namespaces reaches the outside world -- a customer, a public account, an
# ad account, a price -- so an unrecognised verb inside them is treated as high
# risk until a person says otherwise. Everything else unknown is internal work
# and runs, because parking a lead-qualification behind an approval helps nobody.
REACHES_THE_WORLD = ("publish.", "ads.", "pricing.", "offer.", "refund.",
                     "invoice.", "whatsapp.", "integration.", "customer.")

RISKS = ("low", "medium", "high")


def decide(row, action):
    """Return {risk, requires_approval, reason, source} for one action."""
    if row:
        return {"action": action, "risk": row["risk"],
                "requires_approval": bool(row["requires_approval"]),
                "reason": row["reason"], "source": "policy"}
    if action and action.startswith(REACHES_THE_WORLD):
        return {"action": action, "risk": "high", "requires_approval": True,
                "reason": "No policy for this action yet, and it reaches the "
                          "outside world. Approve it once and it can be classified.",
                "source": "unclassified-external"}
    return {"action": action, "risk": "low", "requires_approval": False,
            "reason": "Internal work with no policy on file.",
            "source": "unclassified-internal"}


def _identity(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role, uid


def make_policy_router():
    router = APIRouter(prefix="/policies", tags=["policy"])

    @router.get("")
    def list_policies(x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The effective policy: the tenant's own row wins over the default."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT DISTINCT ON (action)
                                  action, risk, requires_approval, reason,
                                  (tenant_id IS NOT NULL) AS is_override
                             FROM risk_policies
                            WHERE tenant_id = %s OR tenant_id IS NULL
                            ORDER BY action, tenant_id NULLS LAST""", (tid,))
            rows = cur.fetchall()
        return {"policies": [dict(r) for r in rows]}

    @router.put("/{action}")
    def set_policy(action: str,
                   body: dict = Body(default={}),
                   x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """A business may tighten a gate. It may not loosen one.

        Letting a client turn off the approval on "spend my money" or "post as
        me" would make the gate decorative, and the first bad post is the one
        that costs the account.
        """
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can change approval policy.")

        risk = (body.get("risk") or "").strip().lower()
        if risk not in RISKS:
            raise HTTPException(400, "risk must be one of " + ", ".join(RISKS))
        requires = body.get("requires_approval")
        if requires is None:
            requires = risk in ("medium", "high")
        requires = bool(requires)
        if risk == "high" and not requires:
            raise HTTPException(400, "A high-risk action always needs approval.")

        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT risk, requires_approval, reason FROM risk_policies
                            WHERE action = %s AND tenant_id IS NULL""", (action,))
            default = cur.fetchone()
            if default and role != "techdeva_admin":
                if bool(default["requires_approval"]) and not requires:
                    raise HTTPException(
                        403,
                        "TechDeva requires approval for %s: %s. You can make it "
                        "stricter, not looser." % (action, default["reason"]))
                if RISKS.index(risk) < RISKS.index(default["risk"]):
                    raise HTTPException(
                        403,
                        "%s is rated %s by TechDeva and cannot be rated lower."
                        % (action, default["risk"]))

            reason = (body.get("reason") or "").strip() or "Set by the business owner."
            cur.execute("""INSERT INTO risk_policies
                             (tenant_id, action, risk, requires_approval, reason)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (tenant_id, action) WHERE tenant_id IS NOT NULL
                           DO UPDATE SET risk = EXCLUDED.risk,
                                         requires_approval = EXCLUDED.requires_approval,
                                         reason = EXCLUDED.reason,
                                         updated_at = now()
                           RETURNING action, risk, requires_approval, reason""",
                        (tid, action, risk, requires, reason))
            return dict(cur.fetchone())

    return router
