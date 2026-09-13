"""P4.3 - WhatsApp self-service, checked against production.

The question this answers is not "do the endpoints return 200". It is whether
one business can see or touch another business's WhatsApp number, and whether
the platform ever claims a connection it cannot prove.
"""
import json, time, urllib.error, urllib.request, subprocess

BASE = "https://api.techdeva.net"
PASS = "P4-Wa-Test-Pass"
ok = bad = 0


def call(opener, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def record(name, good, note=""):
    global ok, bad
    if good:
        ok += 1
        print("PASS ", name, note)
    else:
        bad += 1
        print("FAIL ", name, note)


def psql(sql):
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres",
         "-d", "techdeva", "-tAc", sql],
        cwd="/opt/techdeva-platform/techdeva-platform",
        capture_output=True, text=True).stdout.strip()


def tenant(name):
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    stamp = int(time.time())
    email = "p4wa-%s-%d@techdeva.test" % (name, stamp)
    # The tenant row carries no email -- that lives on the user -- so the
    # business name is what ties the signup back to a tenant id here.
    business = "P4 WA %s %d" % (name, stamp)
    st, _ = call(op, "POST", "/signup", {"business_name": business,
                                         "industry": "salon",
                                         "contact_email": email, "password": PASS})
    assert st in (200, 201), "signup %s -> %s" % (name, st)
    st, _ = call(op, "POST", "/auth/login", {"email": email, "password": PASS})
    assert st == 200, "login %s -> %s" % (name, st)
    tid = psql("select id from tenants where name = '%s'" % business)
    assert tid, "could not find the tenant created for %s" % business
    return op, tid


print("=== P4.3 WhatsApp self-service ===")
A, tid_a = tenant("a")
B, tid_b = tenant("b")

# 1. A tenant with no agent assigned is told so, not given a 500 or a blank page.
st, body = call(A, "GET", "/whatsapp")
record("unassigned tenant gets a clear 409", st == 409 and "assigned" in body.get("detail", ""),
       "%s %s" % (st, body.get("detail", "")[:40]))

# 2. Assign A an agent, the way TechDeva would. B is untouched.
psql("insert into whatsapp_connections (tenant_id, agent_key, pairctl_url) "
     "values ('%s', 'clinic', 'http://pairctl-clinic:3010')" % tid_a)

st, body = call(A, "GET", "/whatsapp")
record("assigned tenant sees its own row", st == 200 and body.get("status") == "never_connected",
       "%s %s" % (st, body.get("status")))
record("no connection is claimed without proof", body.get("connected") is False)
record("the control URL is never sent to the client", "pairctl_url" not in body)

# 3. The isolation that matters.
st, body = call(B, "GET", "/whatsapp")
record("tenant B cannot see tenant A's connection", st == 409, str(st))

st, body = call(B, "POST", "/whatsapp/connect")
record("tenant B cannot start pairing on it", st == 409, str(st))

# 4. The agent may be down. That is a 503 with a sentence, not a stack trace.
st, body = call(A, "POST", "/whatsapp/connect")
record("an unreachable agent answers 503, not 500", st in (503, 200),
       "%s %s" % (st, body.get("detail", body.get("status", ""))[:40]))

# 5. The database refuses a connection with nothing behind it.
err = subprocess.run(
    ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "techdeva",
     "-c", "update whatsapp_connections set status='connected' where tenant_id='%s'" % tid_a],
    cwd="/opt/techdeva-platform/techdeva-platform", capture_output=True, text=True)
record("the database refuses a connection with no number",
       "whatsapp_connections_proof_ck" in (err.stderr + err.stdout))

# clean up
psql("delete from whatsapp_connections where tenant_id in ('%s','%s')" % (tid_a, tid_b))
psql("update tenants set is_test = true where id in ('%s','%s')" % (tid_a, tid_b))

print("\n=== %d passed, %d failed ===" % (ok, bad))
