"""The client's logo, and the console that changes their dashboard and teaches
their sales agent. Against production.
"""
import base64, http.cookiejar, json, subprocess, time, urllib.error, urllib.request

BASE = "https://api.techdeva.net"
PASS = "P4-Console-Test-Pass"
ok = bad = 0

# A one-pixel PNG is a real PNG. It exercises every byte of the path.
PNG = ("data:image/png;base64,"
       "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def check(name, good, note=""):
    global ok, bad
    if good: ok += 1; print("PASS ", name, note)
    else:    bad += 1; print("FAIL ", name, note)


def call(op, method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with op.open(req, timeout=180) as r:   # free models are slow
            blob = r.read()
            return r.status, (blob if raw else json.loads(blob.decode() or "{}")), r.headers
    except urllib.error.HTTPError as e:
        blob = e.read()
        try:
            return e.code, (blob if raw else json.loads(blob.decode() or "{}")), e.headers
        except Exception:
            return e.code, {}, e.headers


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres",
                           "-d", "techdeva", "-tAc", sql],
                          cwd="/opt/techdeva-platform/techdeva-platform",
                          capture_output=True, text=True).stdout.strip()


def tenant(tag):
    stamp = int(time.time())
    business = "P4 Console %s %d" % (tag, stamp)
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    email = "p4con-%s-%d@techdeva.test" % (tag, stamp)
    st, _, _ = call(op, "POST", "/signup", {"business_name": business, "industry": "salon",
                                            "contact_email": email, "password": PASS})
    assert st in (200, 201), "signup -> %s" % st
    st, _, _ = call(op, "POST", "/auth/login", {"email": email, "password": PASS})
    assert st == 200, "login -> %s" % st
    return op, psql("select id from tenants where name = '%s'" % business), business


A, tid_a, name_a = tenant("a")

# ---- the logo ----------------------------------------------------------
st, body, _ = call(A, "GET", "/brand")
check("a new business has no logo yet", st == 200 and body.get("has_logo") is False, str(st))

st, body, _ = call(A, "POST", "/brand/logo", {"image": PNG})
check("the owner can upload a logo", st == 200 and body.get("mime") == "image/png", str(st))

st, blob, headers = call(A, "GET", "/brand/logo", raw=True)
check("the logo comes back as the image itself",
      st == 200 and headers.get("content-type") == "image/png" and blob[:8] == b"\x89PNG\r\n\x1a\n",
      str(st))

st, body, _ = call(A, "GET", "/brand")
check("the dashboard is told there is a logo to use", body.get("has_logo") is True)

st, body, _ = call(A, "POST", "/brand/logo", {"image": "data:text/html;base64,PGgxPmhp"})
check("a non-image is refused", st == 400, str(st))

st, body, _ = call(A, "POST", "/brand/logo",
                   {"image": "data:image/png;base64," + base64.b64encode(b"x" * 1_200_000).decode()})
check("an oversized image is refused", st == 413, str(st))

# ---- one business never sees another's mark ----------------------------
B, tid_b, name_b = tenant("b")
st, body, _ = call(B, "GET", "/brand/logo", raw=True)
check("another business cannot fetch that logo", st == 404, str(st))

# ---- the console -------------------------------------------------------
st, body, _ = call(A, "POST", "/console",
                   {"message": "Teach my WhatsApp agent that hair PRP is 12000 rupees "
                               "per session and three sessions are 30000."})
taught = st == 200 and body.get("kind") == "teach" and body.get("lesson")
check("teaching the agent is understood and stored", taught,
      "%s %s" % (st, str(body.get("lesson") or body.get("detail"))[:70]))

st, body, _ = call(A, "GET", "/console/lessons")
lessons = body.get("lessons") or []
check("the lesson is readable afterwards", st == 200 and len(lessons) >= 1, str(len(lessons)))

if lessons:
    got = psql("select count(*) from agent_lessons where tenant_id='%s' and active" % tid_a)
    check("and it is a row of its own, not a blob", got == "1", got)

st, body, _ = call(B, "GET", "/console/lessons")
check("another business cannot read those lessons",
      st == 200 and not (body.get("lessons") or []), str(len(body.get("lessons") or [])))

st, body, _ = call(A, "POST", "/console",
                   {"message": "Hide the inventory section and set my brand colour to #C9A227."})
prefs = (body.get("prefs") or {}) if st == 200 else {}
check("a dashboard instruction changes the dashboard",
      st == 200 and body.get("kind") == "dashboard" and
      (prefs.get("accent") or "").lower() == "#c9a227",
      "%s %s" % (st, json.dumps(prefs)[:80]))

st, body, _ = call(A, "GET", "/console/prefs")
check("the change is remembered", (body.get("prefs") or {}).get("accent", "").lower() == "#c9a227",
      json.dumps(body.get("prefs"))[:80])

if lessons:
    st, body, _ = call(A, "DELETE", "/console/lessons/" + lessons[0]["id"])
    check("a wrong lesson can be removed", st == 200, str(st))
    st, body, _ = call(A, "GET", "/console/lessons")
    check("and it stops being taught", not (body.get("lessons") or []))

for t in (tid_a, tid_b):
    psql("update tenants set is_test = true where id = '%s'" % t)

print("\n=== %d passed, %d failed ===" % (ok, bad))
