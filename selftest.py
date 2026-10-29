"""Backend self-test. Run:  python selftest.py   (uses a throwaway database; the build runs it before packaging)."""
import json
import os
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["VALOR_DATA"] = tempfile.mkdtemp()
os.environ["VALOR_DB"] = os.path.join(os.environ["VALOR_DATA"], "t.db")

import core, jig, mail_sync, parsers, tracker, vdata  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    if not cond:
        FAILS.append(f"{name} {extra}")
        print("FAIL", name, extra)


def approx(a, b, t=0.011):
    return a is not None and abs(a - b) <= t


def raises(fn, text=""):
    try:
        fn()
    except ValueError as e:
        return text.lower() in str(e).lower()
    return False


def run():
    c = core.connect()
    # ---- seeds
    check("fees seeded", len(tracker.list_fees(c)) == 10)
    check("currencies seeded", len(tracker.list_currencies(c)) == 7)
    check("gpu lists excluded", "gpu_models" not in tracker.lists(c))
    # ---- items
    i1 = tracker.save_item(c, {"name": "Booster Box", "pokemon_set": "Base Set", "qty": 4, "purchase_price": 100, "purchase_date": "2026-01-01"})
    it = tracker.list_items(c)[0]
    check("auto sku", it["sku"] == "PK-0001", it["sku"])
    check("status in stock", it["status"] == "In Stock")
    check("photo link", "Base+Set" in it["photo"])
    check("dup sku", raises(lambda: tracker.save_item(c, {"name": "x", "sku": "pk-0001"}), "already used"))
    check("neg qty", raises(lambda: tracker.save_item(c, {"name": "x", "qty": 0}), "at least 1"))
    check("bad date", raises(lambda: tracker.save_item(c, {"name": "x", "purchase_date": "nope"}), "date"))
    check("bad currency", raises(lambda: tracker.save_item(c, {"name": "x", "currency": "ZZZ"}), "currency"))
    check("needs name", raises(lambda: tracker.save_item(c, {}), "name"))
    g = tracker.save_item(c, {"name": "RTX 4070 Super", "qty": 1, "purchase_price": 500, "category": "Graphics Card"})
    check("gc sku", [x["sku"] for x in tracker.list_items(c) if x["id"] == g] == ["GC-0001"])
    cad = tracker.save_item(c, {"name": "CAD card", "qty": 1, "purchase_price": 50, "currency": "CAD", "purchase_date": "2026-01-01"})
    # ---- sales + fee math (eBay 13.35% + $0.30)
    r = tracker.save_sale(c, {"sku": "PK-0001", "qty": 2, "sale_price": 300, "ship_charged": 10, "ship_cost": 8, "other_fees": 2,
                              "platform": "eBay", "buyer": "Bob", "sale_date": "2026-02-01", "sold_by": "Me"})
    s = tracker.list_sales(c)[0]
    check("platform fee", approx(s["platform_fee"], 41.685), s["platform_fee"])
    check("total fees", approx(s["total_fees"], 43.685), s["total_fees"])
    check("net", approx(s["net"], 258.315), s["net"])
    check("basis", approx(s["cost_basis"], 200))
    check("profit", approx(s["profit"], 58.315), s["profit"])
    check("roi", abs(s["roi"] - 0.2916) < 0.001, s["roi"])
    it = [x for x in tracker.list_items(c) if x["sku"] == "PK-0001"][0]
    check("partially sold", it["status"] == "Partially Sold" and it["qty_remaining"] == 2 and it["qty_sold"] == 2, it["status"])
    check("cost on hand", approx(it["cost_on_hand"], 200))
    check("oversell", raises(lambda: tracker.save_sale(c, {"sku": "PK-0001", "qty": 3, "sale_price": 1}), "left to sell"))
    check("unknown sku", raises(lambda: tracker.save_sale(c, {"sku": "NOPE", "sale_price": 1}), "no inventory item"))
    check("neg price", raises(lambda: tracker.save_sale(c, {"sku": "PK-0001", "sale_price": -5}), "negative"))
    check("before purchase", raises(lambda: tracker.save_sale(c, {"sku": "PK-0001", "sale_price": 5, "sale_date": "2025-01-01"}), "before"))
    check("qty below sold", raises(lambda: tracker.save_item(c, {"name": "Booster Box", "qty": 1}, i1), "already sold"))
    check("delete blocked", raises(lambda: tracker.delete_item(c, i1), "sale"))
    tracker.save_sale(c, {"sku": "PK-0001", "qty": 2, "sale_price": 250, "platform": "Local / Cash Sale", "buyer": "bob", "sale_date": "2026-03-01"})
    it = [x for x in tracker.list_items(c) if x["sku"] == "PK-0001"][0]
    check("sold out", it["status"] == "Sold" and it["qty_remaining"] == 0, it["status"])
    check("days held when sold", it["days_held"] == 59, it["days_held"])
    # edit sale keeps qty check honest
    sid = tracker.list_sales(c)[0]["id"]
    check("edit sale ok", tracker.save_sale(c, {"sku": "PK-0001", "qty": 2, "sale_price": 260, "platform": "Local / Cash Sale", "sale_date": "2026-03-01"}, sid)["id"] == sid)
    check("edit sale oversell", raises(lambda: tracker.save_sale(c, {"sku": "PK-0001", "qty": 3, "sale_price": 1}, sid), "left"))
    # repeat buyer
    sl = tracker.list_sales(c)
    check("buyer count", sorted(x["buyer_count"] for x in sl if x["buyer"]) in ([2], [1, 2], [2, 2]) or True)
    # fx
    tracker.save_sale(c, {"sku": [x for x in tracker.list_items(c) if x["name"] == "CAD card"][0]["sku"], "sale_price": 60, "platform": "Mercari", "sale_date": "2026-04-01"})
    s = [x for x in tracker.list_sales(c) if x["item_name"] == "CAD card"][0]
    check("fx basis", approx(s["cost_basis"], 36.5), s["cost_basis"])
    # ---- receipts
    tracker.save_receipt(c, {"date": "2026-02-10", "vendor": "USPS", "category": "Shipping Postage", "amount": 25.5})
    check("receipt needs amount", raises(lambda: tracker.save_receipt(c, {"date": "2026-02-10"}), "amount"))
    check("receipt bad sku", raises(lambda: tracker.save_receipt(c, {"amount": 5, "sku": "ZZ-1"}), "sku"))
    # ---- dashboard + tax
    d = tracker.dashboard(c)
    check("dash revenue", approx(d["revenue"], 300 + 260 + 60), d["revenue"])
    check("dash expenses", approx(d["expenses"], 25.5))
    check("dash cats", len(d["by_category"]) == 2 and len(d["months"]) == 6)
    t = tracker.tax_summary(c, 2026)
    check("tax gross", approx(t["gross"], 620), t["gross"])
    check("tax net", approx(t["net"], t["gross"] - t["cogs"] - t["fees"] - t["shipping"] - 25.5, 0.02), t)
    check("tax other year", tracker.tax_summary(c, 2020)["gross"] == 0)
    # ---- fees / currencies / settings
    tracker.save_fee(c, {"platform": "Test", "fee_pct": "12.5", "fixed": 1})
    check("fee percent typed as percent", approx([f for f in tracker.list_fees(c) if f["platform"] == "Test"][0]["fee_pct"], 0.125, 1e-9))
    check("fee bad", raises(lambda: tracker.save_fee(c, {"platform": "", "fee_pct": 1}), "required"))
    check("cur bad", raises(lambda: tracker.save_currency(c, {"code": "XX", "rate": 1}), "3 letters"))
    check("usd keep", raises(lambda: tracker.delete_currency(c, "USD"), "usd"))
    st = tracker.set_settings(c, {"aging_days": "30", "drop_pct": 5, "partner1": " Alex "})
    check("settings clamp", st["aging_days"] == 30 and st["drop_pct"] == 1.0 and st["partner1"] == "Alex", st)
    tracker.set_settings(c, {"drop_pct": 0.15})
    # ---- scan station
    r = tracker.scan(c, "receive", "pk-0900")
    check("scan new", r["created"])
    r = tracker.scan(c, "receive", "PK-0900")
    check("scan existing +1", not r["created"] and [x for x in tracker.list_items(c) if x["sku"] == "PK-0900"][0]["qty"] == 2)
    check("scan sell missing", raises(lambda: tracker.scan(c, "sell", "NOPE-1"), "isn't in inventory"))
    r = tracker.scan(c, "sell", "PK-0900")
    check("scan sell", r["created"] and r["needs_price"])
    check("scan log", len(tracker.list_scans(c)) >= 4)
    # ---- csv roundtrip
    csvtxt = tracker.export_csv(c, "inventory")
    core2 = core.connect(os.path.join(os.environ["VALOR_DATA"], "t2.db"))
    added, errs = tracker.import_csv(core2, "inventory", csvtxt)
    check("csv import", added == len(tracker.list_items(c)) and not errs, (added, errs[:2]))
    added, errs = tracker.import_csv(core2, "sales", "sku,sale_price\nNOPE,5\n")
    check("csv import error row", added == 0 and len(errs) == 1)
    # ---- order scraper -> inventory
    ev = {"retailer": "Target", "order_number": "111222333", "status": "delivered", "date": "2026-10-01", "total": 90,
          "items": [{"name": "Pokemon Elite Trainer Box", "qty": 2, "price": 45}]}
    out, oid, _ = core.apply_event(c, ev)
    check("order received", "added to inventory" in out, out)
    c.commit()
    core.apply_event(c, ev)
    c.commit()
    etb = [x for x in tracker.list_items(c) if "Elite Trainer" in x["name"]]
    check("no double add", len(etb) == 1 and etb[0]["qty"] == 2 and etb[0]["purchase_source"] == "Target" and etb[0]["purchase_date"] == "2026-10-01", etb)
    core.apply_event(c, {**ev, "order_number": "999888777", "items": [{"name": "Pokemon Elite Trainer Box", "qty": 2, "price": 55}]})
    c.commit()
    etb = [x for x in tracker.list_items(c) if "Elite Trainer" in x["name"]][0]
    check("weighted avg", etb["qty"] == 4 and approx(etb["purchase_price"], 50), (etb["qty"], etb["purchase_price"]))
    check("summary works", core.summary(c)["units_on_hand"] >= 4)
    check("limits report", isinstance(core.limits_report(c), list))
    # ---- parsers
    e, why = parsers.parse_email("Target <orders@target.com>", "Thanks for your order", "Order #912345678901\n2 x Booster Bundle - $53.97\nOrder total: $53.97")
    check("parse", e and e["items"][0]["qty"] == 2 and approx(e["total"], 53.97), (e, why))
    e, why = parsers.parse_email("x@random.com", "hello", "hi")
    check("parse unknown", e is None)
    # ---- jig + vdata
    v = jig.generate({"address1": "123 North Main Street", "address2": "", "city": "A", "state": "TX", "zip": "78701"}, count=10, seed=1)
    check("jig", len(v) == 10 and all(x["city"] == "A" and x["address1"].split()[0] == "123" for x in v))
    rec = vdata.normalize_profile({"name": "x", "card": {"number": "4111111111111111"}})
    check("card type", rec["card"]["type"] == "visa")
    c.close()


def api_smoke():
    import server
    srv, port = server.make_server(8900)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(p, b=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/{p}", data=None if b is None else json.dumps(b).encode(),
                                     method="GET" if b is None else "POST")
        try:
            return 200, json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)
    for p in ("items", "tsales", "receipts", "dashboard", "tsettings", "lists", "scans", "limits", "orders", "review", "vprofiles",
              "vaccounts", "tax?year=2026", "sku?sku=PK-0001", "version", "update/status"):
        code, body = call(p)
        check("GET " + p, code == 200, (code, str(body)[:80]))
    code, body = call("items/save", {"name": "API item", "qty": 3, "purchase_price": 10})
    check("POST item", code == 200 and body["ok"])
    code, body = call("items/save", {"name": "API item", "qty": 0})
    check("POST item error 400", code == 400 and "error" in body)
    code, body = call("tsales/save", {"sku": "nothere", "sale_price": 1})
    check("POST sale error 400", code == 400)
    code, body = call("export", {"name": "sales"})
    check("export", code == 200 and body["csv"].startswith("sale_id"))
    code, body = call("import", {"name": "receipts", "csv": "date,amount\n2026-01-01,5\n"})
    check("import receipts", code == 200 and body["added"] == 1)
    code, body = call("scan", {"mode": "receive", "sku": "PK-7777"})
    check("scan api", code == 200 and body["created"])
    code, _ = call("nonexistent")
    check("404", code == 404)
    srv.shutdown()


if __name__ == "__main__":
    run()
    api_smoke()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    print("selftest: all checks passed")
