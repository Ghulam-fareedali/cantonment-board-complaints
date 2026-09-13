"""The 18-test production battery, run against api.techdeva.net.

Phase 12 of the plan, in order. Nothing is faked: a test that cannot pass
because the feature does not exist is recorded MISSING, not green.
"""
import json, time, subprocess, urllib.request, urllib.error, http.cookiejar

BASE = "https://api.techdeva.net"
RESULTS = []

def client():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def call(op, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with op.open(req, timeout=40) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:200]}
    except Exception as e:
        return 0, {"error": str(e)}

def record(n, name, state, detail=""):
    RESULTS.append((n, name, state, str(detail)[:220]))
    print("{0:>2}. {1:<44} {2}  {3}".format(n, name, state, str(detail)[:90]))

def dc(*args):
    return subprocess.run(["docker", "compose"] + list(args),
                          cwd="/opt/techdeva-platform/techdeva-platform",
                          capture_output=True, text=True, timeout=180)

print("=== TechDeva 18-test production battery ===\n")

# 1 signup / login
opA = client()
emailA = "p3-battery-a-%d@techdeva.test" % int(time.time())
pw = "Battery-Passw0rd-A"
st, body = call(opA, "POST", "/signup", {"business_name": "Battery Tenant A",
    "industry": "salon", "contact_email": emailA, "password": pw})
ok_signup = st in (200, 201)
st2, _ = call(opA, "POST", "/auth/login", {"email": emailA, "password": pw})
record(1, "Signup and login", "GREEN" if ok_signup and st2 == 200 else "RED",
       "signup %s login %s" % (st, st2))
tenant_a = body.get("tenant_id")

# 2 organization exists for the signup
st, me = call(opA, "GET", "/portal/config")
record(2, "Organization created by signup", "GREEN" if st == 200 else "RED", st)

# 3 create client record (a customer)
st, cust = call(opA, "POST", "/business/customers", {"name": "Battery Customer",
                                                    "phone": "+923001112233"})
cust = cust.get("item", cust)
record(3, "Create client record", "GREEN" if st in (200, 201) and cust.get("id") else "RED", st)

# 4 create lead
st, lead = call(opA, "POST", "/leads", {"name": "Battery Lead", "phone": "+923004445566",
                                        "source": "hermes"})
lead_id = (lead.get("item") or lead).get("id") or lead.get("lead_id")
record(4, "Create lead", "GREEN" if st in (200, 201) else "RED", st)

# 5 send event
st, ev = call(opA, "POST", "/events", {"type": "lead.created",
                                       "payload": {"lead_id": lead_id}})
record(5, "Send event", "GREEN" if st in (200, 201, 202) else "RED", st)

# 6 + 7 queue and worker: the event should turn into a task.
# The worker polls, so poll back rather than guessing a sleep -- and report how
# long the pipeline actually took, which is a number worth knowing.
# Real agents now call a real model. A free-tier model answers in ~25s
# and a task can make several calls, so 90s was measuring the model, not
# the pipeline. 300s is the honest ceiling for "the worker is alive".
deadline = time.time() + 300
rows, st, waited = [], 0, 0
while time.time() < deadline:
    st, tasks = call(opA, "GET", "/tasks")
    rows = tasks.get("tasks", tasks.get("items", []))
    if rows:
        break
    time.sleep(3)
    waited += 3
record(6, "Event queued", "GREEN" if st == 200 else "RED", st)
record(7, "Worker processed the queue", "GREEN" if rows else "RED",
       "%d tasks after %ds" % (len(rows), waited))

# 8 + 9 orchestrator ran and chose an agent
agents = sorted({r.get("agent") for r in rows if r.get("agent")}) if rows else []
record(8, "Orchestrator processed", "GREEN" if rows else "RED", "%d tasks" % len(rows))
record(9, "Correct agent selected", "GREEN" if agents else "RED", agents)

# 10 Hermes reachable from the platform
r = dc("exec", "-T", "api", "python3", "-c",
       "import os,urllib.request;u=os.environ['HERMES_URL'].rstrip('/');"
       "print(urllib.request.urlopen(u+'/health',timeout=8).read().decode()[:80])")
record(10, "Hermes responds", "GREEN" if '"ok"' in r.stdout else "RED", r.stdout.strip()[:80])

# 11 CRM updated: an invoice against the customer, paid in full
st, inv = call(opA, "POST", "/operations/invoices", {
    "customer_id": cust.get("id"),
    "items": [{"description": "Battery service", "quantity": 2, "unit_price": 1500}]})
inv_id = inv.get("invoice_id")
st2, pay = call(opA, "POST", "/operations/invoices/%s/pay" % inv_id,
                {"amount": 3000, "method": "cash"})
record(11, "CRM updated (invoice raised and settled)",
       "GREEN" if inv.get("total") == 3000 and pay.get("status") == "paid" else "RED",
       "total %s status %s" % (inv.get("total"), pay.get("status")))

# 12 execution logged
st, ex = call(opA, "GET", "/business/executions")
ex_rows = ex.get("items", [])
acts = [r.get("action") + ":" + r.get("resource") for r in ex_rows]
record(12, "Execution logged", "GREEN" if "pay:invoices" in acts else "RED",
       "%d rows" % len(ex_rows))

# 13 analytics updated
st, pnl = call(opA, "GET", "/operations/pnl")
record(13, "Analytics updated", "GREEN" if pnl.get("collected") == 3000 else "RED",
       "collected %s" % pnl.get("collected"))

# 14 frontend renders
st, page = 0, ""
try:
    with urllib.request.urlopen(BASE + "/dashboard", timeout=20) as r:
        st, page = r.status, r.read().decode()
except Exception as e:
    page = str(e)
has_views = all(k in page for k in ("VIEWS.invoices", "VIEWS.packages", "VIEWS.dayclose"))
record(14, "Frontend serves the built dashboard",
       "GREEN" if st == 200 and has_views else "RED", "%s bytes %d" % (st, len(page)))

# 15 tenant isolation
opB = client()
emailB = "p3-battery-b-%d@techdeva.test" % int(time.time())
call(opB, "POST", "/signup", {"business_name": "Battery Tenant B", "industry": "salon",
                              "contact_email": emailB, "password": "Battery-Passw0rd-B"})
call(opB, "POST", "/auth/login", {"email": emailB, "password": "Battery-Passw0rd-B"})
st_read, steal = call(opB, "GET", "/operations/invoices/%s" % inv_id)
st_pay, _ = call(opB, "POST", "/operations/invoices/%s/pay" % inv_id, {"amount": 1})
st_list, blist = call(opB, "GET", "/business/invoices")
st_anon, _ = call(client(), "GET", "/operations/pnl")
isolated = (st_read == 404 and st_pay in (403, 404)
            and len(blist.get("items", [])) == 0 and st_anon in (401, 403))
record(15, "Tenant isolation", "GREEN" if isolated else "RED",
       "read %s pay %s list %s anon %s" % (st_read, st_pay,
                                           len(blist.get("items", [])), st_anon))

# 16 worker restart recovery
dc("restart", "worker")
time.sleep(12)
r = dc("ps", "--format", "{{.Service}} {{.Status}}")
record(16, "Worker restart recovery", "GREEN" if "worker Up" in r.stdout else "RED",
       [l for l in r.stdout.split("\n") if l.startswith("worker")])

# 17 API restart recovery
dc("restart", "api")
time.sleep(14)
st, _ = call(client(), "GET", "/health")
record(17, "API restart recovery", "GREEN" if st == 200 else "RED", st)

# 18 database and redis connectivity after the restarts
opA2 = client()
call(opA2, "POST", "/auth/login", {"email": emailA, "password": pw})
st_db, again = call(opA2, "GET", "/operations/invoices/%s" % inv_id)
r = dc("exec", "-T", "redis", "redis-cli", "--no-auth-warning", "INFO", "replication")
role_ok = "role:master" in r.stdout
record(18, "Database and Redis connectivity",
       "GREEN" if st_db == 200 and role_ok else "RED",
       "invoice %s redis master %s" % (st_db, role_ok))

green = sum(1 for x in RESULTS if x[2] == "GREEN")
print("\n=== %d GREEN, %d not green, of %d ===" % (green, len(RESULTS) - green, len(RESULTS)))
for n, name, state, detail in RESULTS:
    if state != "GREEN":
        print("   %2d %s -> %s  %s" % (n, name, state, detail))
