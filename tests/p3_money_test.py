import json, time, urllib.request, urllib.error, http.cookiejar

BASE = "https://api.techdeva.net"
PASS = 0
FAIL = 0

def client():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def call(op, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with op.open(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:200]}

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + "   " + str(detail)[:300])

def signup(tag):
    op = client()
    email = "p3-{0}-{1}@techdeva.test".format(tag, int(time.time()))
    st, body = call(op, "POST", "/signup", {
        "business_name": "P3 Test " + tag, "industry": "salon",
        "contact_email": email, "password": "Test-Passw0rd-" + tag})
    if st not in (200, 201):
        raise SystemExit("signup failed {0} {1}".format(st, body))
    st, lg = call(op, "POST", "/auth/login",
                  {"email": email, "password": "Test-Passw0rd-" + tag})
    if st not in (200, 201):
        raise SystemExit("login failed {0} {1}".format(st, lg))
    return op, body

print("=== Part 3 money layer, end to end, against production ===")
op, who = signup("a")
print("tenant A: " + json.dumps(who)[:200])

st, cust = call(op, "POST", "/business/customers",
                {"name": "Ayesha Khan", "phone": "+923001234567"})
cust = cust.get("item", cust)
check("create customer", st in (200, 201) and cust.get("id"), (st, cust))
st, svc = call(op, "POST", "/business/services",
               {"name": "Hydrafacial", "price": 5000, "duration_minutes": 60})
svc = svc.get("item", svc)
check("create service", st in (200, 201) and svc.get("id"), (st, svc))

st, inv = call(op, "POST", "/operations/invoices", {
    "customer_id": cust.get("id"),
    "items": [{"service_id": svc.get("id"), "description": "Hydrafacial",
               "quantity": 1, "unit_price": 5000},
              {"description": "Serum add-on", "quantity": 2, "unit_price": 750}],
    "discount": 500, "tax": 0})
check("create invoice", st in (200, 201) and inv.get("invoice_id"), (st, inv))
check("invoice number is INV-0001", inv.get("number") == "INV-0001", inv)
check("total = 5000 + 1500 - 500 = 6000", abs(float(inv.get("total", 0)) - 6000) < 0.01, inv)
inv_id = inv.get("invoice_id")

st, got = call(op, "GET", "/operations/invoices/" + str(inv_id))
check("read invoice back", st == 200, (st, got))
check("two line items", len(got.get("items", [])) == 2, got.get("items"))
check("balance = full total before payment", abs(got.get("balance", -1) - 6000) < 0.01, got.get("balance"))
check("status is issued, not paid", got["invoice"].get("status") == "issued", got["invoice"].get("status"))
check("customer name joined", got["invoice"].get("customer_name") == "Ayesha Khan", got["invoice"].get("customer_name"))

st, over = call(op, "POST", "/operations/invoices/{0}/pay".format(inv_id),
                {"amount": 9999, "method": "cash"})
check("overpayment refused", st == 409, (st, over))

st, p1 = call(op, "POST", "/operations/invoices/{0}/pay".format(inv_id),
              {"amount": 2000, "method": "cash"})
check("partial payment accepted", st in (200, 201), (st, p1))
check("status becomes partial", p1.get("status") == "partial", p1)
check("balance 4000 after partial", abs(p1.get("balance", -1) - 4000) < 0.01, p1)

st, bad = call(op, "POST", "/operations/invoices/{0}/void".format(inv_id),
               {"reason": "changed my mind"})
check("void refused while payments exist", st == 409, (st, bad))

st, p2 = call(op, "POST", "/operations/invoices/{0}/pay".format(inv_id),
              {"amount": 4000, "method": "card"})
check("final payment accepted", st in (200, 201), (st, p2))
check("status becomes paid", p2.get("status") == "paid", p2)
check("balance zero", abs(p2.get("balance", -1)) < 0.01, p2)

st, pnl = call(op, "GET", "/operations/pnl")
check("pnl reachable", st == 200, (st, pnl))
check("collected = 6000", abs(pnl.get("collected", -1) - 6000) < 0.01, pnl)
check("invoiced = 6000", abs(pnl.get("invoiced", -1) - 6000) < 0.01, pnl)
check("net = collected - expenses", abs(pnl.get("net", -1) - 6000) < 0.01, pnl)

st, exp = call(op, "POST", "/business/expenses",
               {"category": "supplies", "description": "Serum stock", "amount": 1200})
check("create expense", st in (200, 201), (st, exp))
st, pnl2 = call(op, "GET", "/operations/pnl")
check("expenses reflected in pnl", abs(pnl2.get("expenses", -1) - 1200) < 0.01, pnl2)
check("net drops to 4800", abs(pnl2.get("net", -1) - 4800) < 0.01, pnl2)

st, prev = call(op, "GET", "/operations/closing/preview")
check("closing preview", st == 200 and abs(prev.get("expected_total", -1) - 6000) < 0.01, (st, prev))
check("preview splits by method", prev.get("by_method", {}).get("cash") == 2000, prev.get("by_method"))
check("preview not already closed", prev.get("already_closed") is None, prev.get("already_closed"))

st, close = call(op, "POST", "/operations/closing",
                 {"cash_counted": 1900, "card_counted": 4000, "notes": "100 short in cash"})
check("day closes", st in (200, 201), (st, close))
check("variance = -100", abs(close.get("variance", 999) + 100) < 0.01, close)
st, again = call(op, "POST", "/operations/closing", {"cash_counted": 1900, "card_counted": 4000})
check("second close of same day refused", st == 409, (st, again))

st, execs = call(op, "GET", "/business/executions")
rows = execs.get("items", execs if isinstance(execs, list) else [])
acts = [r.get("action") + ":" + r.get("resource") for r in rows] if isinstance(rows, list) else []
check("audit trail recorded", st == 200 and len(acts) >= 5, (st, acts[:12]))
check("payments audited", "pay:invoices" in acts, acts[:12])
check("closing audited", "close:daily_closing" in acts, acts[:12])

opB, whoB = signup("b")
st, bInv = call(opB, "GET", "/business/invoices")
bRows = bInv.get("items", bInv if isinstance(bInv, list) else [])
check("tenant B sees no invoices of tenant A", st == 200 and len(bRows) == 0, (st, bRows))
st, steal = call(opB, "GET", "/operations/invoices/" + str(inv_id))
check("tenant B cannot read tenant A invoice by id", st == 404, (st, steal))
st, stealpay = call(opB, "POST", "/operations/invoices/{0}/pay".format(inv_id), {"amount": 1})
check("tenant B cannot pay tenant A invoice", stealpay and st in (403, 404), (st, stealpay))
st, bpnl = call(opB, "GET", "/operations/pnl")
check("tenant B pnl is zero", st == 200 and bpnl.get("collected") == 0, (st, bpnl))

st, anon = call(client(), "GET", "/operations/pnl")
check("no credential is refused", st in (401, 403), (st, anon))

print("")
print("=== {0} passed, {1} failed ===".format(PASS, FAIL))
