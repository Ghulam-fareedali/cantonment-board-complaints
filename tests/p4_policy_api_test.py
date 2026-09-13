"""P4.5 - the approval policy API, against production.

The question: can a business turn off a gate that protects its own customers?
It must not be able to, and it must be able to add gates of its own.
"""
import http.cookiejar, json, subprocess, time, urllib.error, urllib.request

BASE = "https://api.techdeva.net"
PASS = "P4-Policy-Test-Pass"
ok = bad = 0


def check(name, good, note=""):
    global ok, bad
    if good:
        ok += 1; print("PASS ", name, note)
    else:
        bad += 1; print("FAIL ", name, note)


def call(op, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with op.open(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


stamp = int(time.time())
business = "P4 Policy %d" % stamp
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
st, _ = call(op, "POST", "/signup", {"business_name": business, "industry": "salon",
                                     "contact_email": "p4pol-%d@techdeva.test" % stamp,
                                     "password": PASS})
assert st in (200, 201), "signup -> %s" % st
st, _ = call(op, "POST", "/auth/login", {"email": "p4pol-%d@techdeva.test" % stamp,
                                         "password": PASS})
assert st == 200, "login -> %s" % st

st, body = call(op, "GET", "/policies")
pol = {p["action"]: p for p in body.get("policies", [])}
check("the platform defaults are visible to the business", st == 200 and len(pol) > 20,
      "%s actions" % len(pol))
check("publishing in the business name needs approval",
      pol.get("schedule_posts", {}).get("requires_approval") is True)
check("spending the client's money needs approval",
      pol.get("build_campaign", {}).get("requires_approval") is True)
check("internal work does not", pol.get("qualify", {}).get("requires_approval") is False)

# The one that matters: a client cannot switch off a gate.
st, body = call(op, "PUT", "/policies/schedule_posts",
                {"risk": "low", "requires_approval": False})
check("a business cannot switch off an approval gate", st == 403,
      "%s %s" % (st, body.get("detail", "")[:60]))

st, body = call(op, "PUT", "/policies/build_campaign", {"risk": "medium"})
check("nor quietly downgrade its risk", st == 403, str(st))

# But it can be stricter than TechDeva requires.
st, body = call(op, "PUT", "/policies/make_creative",
                {"risk": "high", "reason": "We review every creative before it is used"})
check("a business can add a gate of its own", st == 200 and body.get("requires_approval") is True,
      str(st))

st, body = call(op, "GET", "/policies")
after = {p["action"]: p for p in body.get("policies", [])}
check("the stricter rule is what the platform now uses",
      after.get("make_creative", {}).get("requires_approval") is True
      and after.get("make_creative", {}).get("is_override") is True)

subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres",
                "-d", "techdeva", "-tAc",
                "update tenants set is_test = true where name = '%s'" % business],
               cwd="/opt/techdeva-platform/techdeva-platform", capture_output=True)

print("\n=== %d passed, %d failed ===" % (ok, bad))
