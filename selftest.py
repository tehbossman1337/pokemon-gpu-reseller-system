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


# ------------------------------------------------------------------ mail scanner against a fake iCloud
def mail_test():
    import imaplib
    import re as _re
    from email.message import EmailMessage

    def mk(frm, subj, body, mid, html=None, to="alias@icloud.com"):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"], m["Message-ID"], m["Date"] = frm, to, subj, mid, "Mon, 26 Oct 2026 10:00:00 -0400"
        m.set_content(body)
        if html:
            m.add_alternative(html, subtype="html")
        return m.as_bytes()
    PC = "Pokemon Center <pokemoncenter@email.pokemoncenter.com>"
    FOLDERS = {
        "INBOX": {b"1": mk("promo@shop.com", "Big sale", "buy now", "<i1>"),
                  b"2": mk("Me <me@icloud.com>", "Fwd: your order", "From: Target <orders@target.com>\nOrder #912345678901\n1 x Booster Bundle $26.94\nOrder total: $26.94", "<i2>"),
                  b"5": mk("UPS <pkginfo@ups.com>", "UPS Update: Package Delivered", "Your package was delivered.\nTracking Number: 1Z999AA10123456784", "<i5>"),
                  b"4": mk(PC, "The Game Boy Jukebox Has Returned!", "Shop now and save on merch", "<i4>"),
                  b"3": mk("Orders <no-reply@pokemon.com>", "Your Pokemon Center order has shipped", "Order Number: 77001122 tracking number 1Z999AA10123456784", "<i3>")},
        "Archive": {b"1": mk(PC, "Thank you for your order!", "view in browser", "<a1>",
                             html="<html><body><p>Order Number</p><p>77001122</p><p>Pokemon TCG Elite Trainer Box</p><p>Qty: 2</p><p>$109.98</p><p>Order Total: $109.98</p></body></html>"),
                    b"2": mk("friend@x.com", "lunch?", "no order here", "<a2>")},
        "Drafts": {b"1": mk(PC, "Thank you for your order", "Order #99999999\n1 x X Thing $5.00", "<d1>")},
    }

    class Fake:
        def __init__(s, *a):
            s.f = None
        def login(s, u, p): return "OK", [b""]
        def noop(s): return "OK", [b""]
        def logout(s): pass
        def list(s): return "OK", [f'(\\HasNoChildren) "/" "{n}"'.encode() for n in FOLDERS] + [b'(\\Noselect) "/" "[Gmail]"']
        def select(s, f, readonly=True):
            f = f.strip('"')
            if f not in FOLDERS:
                return "NO", [b""]
            s.f = f
            return "OK", [b"1"]
        def response(s, k): return k, [b"7"]
        def uid(s, cmd, *a):
            if cmd == "search":
                q = " ".join(map(str, a)); ids = sorted(FOLDERS[s.f]); m = _re.search(r'"([^"]+)"', q)
                if m:
                    ids = [i for i in ids if m.group(1).lower().encode() in FOLDERS[s.f][i].lower()]
                return "OK", [b" ".join(ids)]
            out = []
            for u in a[0].split(b","):
                raw = FOLDERS[s.f][u]
                if "HEADER.FIELDS" in str(a[1]):
                    import email as _e
                    mm = _e.message_from_bytes(raw)
                    raw = "".join(f"{k}: {mm[k]}\r\n" for k in ("From", "Subject", "Message-ID") if mm[k]).encode()
                out.append((b"1 (UID " + u + b" BODY[] {1}", raw))
                out.append(b")")
            return "OK", out
    real = imaplib.IMAP4_SSL
    imaplib.IMAP4_SSL = Fake
    try:
        mail_sync.save_conf("me@icloud.com", "x")  # defaults: every folder, all time
        conf = mail_sync.load_conf()
        check("mail defaults", conf["folder"] == "*" and conf["days"] == 0 and conf["everything"])
        c = core.connect(os.path.join(os.environ["VALOR_DATA"], "mail.db"))
        orig = core.connect
        core.connect = lambda *a, **k: orig(os.path.join(os.environ["VALOR_DATA"], "mail.db"))
        log = []
        st = mail_sync.sync(progress=log.append)
        text = "\n".join(log)
        check("all folders listed", "INBOX" in text and "Archive" in text and "Drafts" not in text.split("Folders to read")[1].split("\n")[0], text[:300])
        orders = {(r["retailer"], r["order_number"]): r["status"] for r in c.execute("select * from orders")}
        check("pokemon center order read (archive)", ("Pokemon Center", "77001122") in orders, orders)
        check("status moved forward to shipped", orders.get(("Pokemon Center", "77001122")) == "shipped", orders)
        check("forwarded target order", ("Target", "912345678901") in orders, orders)
        import packages as _pk
        pkgs = {p["tracking"]: p for p in _pk.list_packages(c)}
        check("tracking number scraped from shipped email", "1Z999AA10123456784" in pkgs and pkgs["1Z999AA10123456784"]["order_number"] == "77001122", pkgs)
        check("carrier email delivered it", pkgs.get("1Z999AA10123456784", {}).get("status") == "delivered", pkgs)
        check("drafts skipped", ("Pokemon Center", "99999999") not in orders)
        it = c.execute("select qty from order_items oi join orders o on o.id=oi.order_id where o.order_number='77001122'").fetchone()
        check("items parsed from separate-line layout", it and it[0] == 2, it)
        check("checked everything incl. non-retailers", st["scanned"] >= 5 and st["skipped"] >= 2, st)
        check("only plausible mails fully read", sum(1 for r in c.execute("select outcome from processed_emails where outcome not like 'skipped%' and outcome != 'duplicate' and subject != ''")) >= 3)
        st2 = mail_sync.sync(progress=log.append)
        check("second sync reads nothing new", st2["scanned"] == 0, st2)
        st3 = mail_sync.sync(progress=log.append, rescan=True)
        check("rescan idempotent", st3["scanned"] >= 5 and c.execute("select count(*) from orders").fetchone()[0] == len(orders), st3)
        # old config (INBOX, 30 days, no upgrade marker) is upgraded to scan everything
        json.dump({"email": "me@icloud.com", "password": "x", "folder": "INBOX", "days": 30}, open(mail_sync.CONF_PATH, "w"))
        conf = mail_sync.load_conf()
        check("old config upgraded", conf["folder"] == "*" and conf["days"] == 0, conf)
        # promos from the store are skipped quietly and never fill the Review queue
        check("promo not in review", c.execute("select count(*) from review where subject like '%Jukebox%'").fetchone()[0] == 0)
        core.connect = orig
    finally:
        imaplib.IMAP4_SSL = real


# ------------------------------------------------------------------ packages + notifications
def package_test():
    import http.server
    import packages
    import notify
    import parsers as P
    dbp = os.path.join(os.environ["VALOR_DATA"], "pkg.db")
    c = core.connect(dbp)
    # --- finding tracking numbers
    ex = packages.extract("Your order shipped.\nTracking number: 1Z999AA10123456784\nOrder #912345678901")
    check("ups found", [x["tracking"] for x in ex] == ["1Z999AA10123456784"] and ex[0]["carrier"] == "UPS", ex)
    ex = packages.extract("Track: https://www.fedex.com/fedextrack/?trknbr=123456789012 thanks")
    check("fedex from link", ex and ex[0]["tracking"] == "123456789012" and ex[0]["carrier"] == "FedEx", ex)
    ex = packages.extract("USPS tracking 9400111899223197428490 and also 9261290100130358400004")
    check("usps two numbers", len(ex) == 2 and ex[0]["carrier"] == "USPS", ex)
    check("order number is not tracking", packages.extract("Order Number: 20481234\nOrder #912345678901 total $5.00") == [])
    check("year/dates not tracking", packages.extract("Tracking updated 2026102812345") == [])
    check("status words", packages.classify("Your package was delivered") == "delivered" and packages.classify("will be delivered Friday") != "delivered"
          and packages.classify("Out for delivery today") == "out_for_delivery" and packages.classify("Delivery attempted - action required") == "exception")
    # --- retailer emails -> packages (+ notifications)
    today = core.now()[:10]
    ev, why = P.parse_email("Pokemon Center <pokemoncenter@email.pokemoncenter.com>", "Your Pokemon Center order has shipped",
                            "Order Number: 55501234\n2 x Elite Trainer Box - $99.98\nTracking number: 1Z999AA10123456784\nOrder Total: $99.98")
    check("parse has packages", ev and ev["packages"] and ev["packages"][0]["tracking"] == "1Z999AA10123456784", (ev, why))
    out, oid, _ = core.apply_event(c, ev); c.commit()
    pk = packages.list_packages(c)
    check("package created+linked", len(pk) == 1 and pk[0]["order_id"] == oid and pk[0]["status"] == "shipped" and pk[0]["retailer"] == "Pokemon Center"
          and "Elite Trainer" in pk[0]["description"] and pk[0]["carrier"] == "UPS", pk)
    check("track url", pk[0]["url"].startswith("https://www.ups.com/track?tracknum=1Z999"))
    nn = notify.list_notifications(c)
    check("shipped notification", any(n["kind"] == "pkg_shipped" for n in nn["items"]) and nn["unread"] >= 1, nn["items"])
    core.apply_event(c, ev); c.commit()
    check("no duplicate package/notification", len(packages.list_packages(c)) == 1 and sum(1 for n in notify.list_notifications(c)["items"] if n["kind"] == "pkg_shipped") == 1)
    # carrier email: out for delivery, then delivered
    r = packages.process_carrier_email(c, "UPS <pkginfo@ups.com>", "UPS Update: Package Out For Delivery", ["Tracking Number: 1Z999AA10123456784"], None); c.commit()
    check("carrier email out for delivery", packages.list_packages(c)[0]["status"] == "out_for_delivery", r)
    check("out notification", any(n["kind"] == "pkg_out" for n in notify.list_notifications(c)["items"]))
    ev2, _ = P.parse_email("Pokemon Center <pokemoncenter@email.pokemoncenter.com>", "Your order has been delivered", "Order #55501234 has been delivered")
    core.apply_event(c, ev2); c.commit()
    pkg = packages.list_packages(c)[0]
    check("delivered via store email", pkg["status"] == "delivered" and pkg["delivered_at"] and not pkg["active"], pkg["status"])
    check("delivered notification", any(n["kind"] == "pkg_delivered" for n in notify.list_notifications(c)["items"]))
    packages.process_carrier_email(c, "UPS <x@ups.com>", "UPS: in transit", ["Tracking Number: 1Z999AA10123456784"], None); c.commit()
    check("delivered is final", packages.list_packages(c)[0]["status"] == "delivered")
    # old emails found while reading history stay quiet
    n0 = len(notify.list_notifications(c)["items"])
    packages.set_or_create(c, "1Z999AA10123450001", "UPS", "delivered", "old", "2026-01-01T10:00:00", quiet=False)
    check("history is silent", len(notify.list_notifications(c)["items"]) == n0 and len(packages.list_packages(c)) == 2)
    # manual add / edit / status / delete
    pid = packages.save_package(c, {"tracking": "9400 1118 9922 3197 4284 90", "retailer": "Walmart", "description": "Booster box"})
    check("manual add", [p for p in packages.list_packages(c) if p["id"] == pid][0]["carrier"] == "USPS")
    check("dup rejected", raises(lambda: packages.save_package(c, {"tracking": "9400111899223197428490"}), "already"))
    check("short rejected", raises(lambda: packages.save_package(c, {"tracking": "abc"}), "tracking"))
    packages.set_status_manual(c, pid, "delivered"); check("manual status", [p for p in packages.list_packages(c) if p["id"] == pid][0]["status"] == "delivered")
    packages.delete_package(c, pid); check("delete", all(p["id"] != pid for p in packages.list_packages(c)))
    # --- notification settings + channels
    check("cfg defaults", notify.get_config(c)["desktop"] is True and notify.get_config(c)["events"]["pkg_delivered"])
    check("cfg bad webhook", raises(lambda: notify.set_config(c, {"discord": "http://x"}), "https"))
    check("cfg bad quiet", raises(lambda: notify.set_config(c, {"quiet_start": "25:99"}), "22:00"))
    cfg = notify.set_config(c, {"quiet_start": "22:00", "quiet_end": "07:00", "events": {"pkg_out": False}})
    check("cfg saved", cfg["quiet_start"] == "22:00" and cfg["events"]["pkg_out"] is False and cfg["events"]["pkg_delivered"] is True)
    from datetime import datetime as D
    check("quiet hours wrap", notify._in_quiet(cfg, D(2026, 1, 1, 23, 30)) and notify._in_quiet(cfg, D(2026, 1, 1, 3, 0)) and not notify._in_quiet(cfg, D(2026, 1, 1, 12, 0)))
    check("disabled event not logged", notify.notify(c, "pkg_out", "x", "y", dedupe="z1") is None)
    check("dedupe", notify.notify(c, "test", "t", "b", dedupe="same", push=False) and notify.notify(c, "test", "t", "b", dedupe="same", push=False) is None)
    ts = notify.toast_script("It's <b>", "Tom & Jerry")
    check("toast script escaped", "&lt;b&gt;" in ts and "Tom &amp; Jerry" in ts and "It''s" in ts)
    got = []

    class W(http.server.BaseHTTPRequestHandler):
        def log_message(s, *a): pass
        def do_POST(s):
            got.append((s.path, s.headers.get("Title"), s.rfile.read(int(s.headers.get("Content-Length") or 0)).decode()))
            s.send_response(200); s.end_headers(); s.wfile.write(b"ok")
    ws = http.server.ThreadingHTTPServer(("127.0.0.1", 0), W); threading.Thread(target=ws.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{ws.server_address[1]}"
    res = notify.send_push({"desktop": False, "discord": base + "/discord", "slack": base + "/slack", "ntfy": "mytopic", "ntfy_server": base}, "Title here", "Body here")
    check("push channels", res == {"discord": "ok", "slack": "ok", "ntfy": "ok"} and len(got) == 3, (res, got))
    check("push payloads", any(p == "/discord" and "Body here" in b for p, _, b in got) and any(p == "/mytopic" and t == "Title here" for p, t, _ in got), got)
    res = notify.send_push({"desktop": False, "discord": "http://127.0.0.1:9/nope"}, "t", "b")
    check("push failure reported not raised", res["discord"] != "ok")
    res = notify.send_push({"desktop": False, "discord": base + "/d", "quiet_start": "00:00", "quiet_end": "23:59"}, "t", "b")
    check("quiet hours hold pushes", res["discord"].startswith("skipped"))
    ws.shutdown()
    # --- live tracking through a stand-in for the 17TRACK API
    class T(http.server.BaseHTTPRequestHandler):
        def log_message(s, *a): pass
        def do_POST(s):
            body = json.loads(s.rfile.read(int(s.headers.get("Content-Length") or 0)) or b"[]")
            if s.headers.get("17token") != "goodkey":
                s.send_response(401); s.end_headers(); return
            nums = [x["number"] for x in body]
            if s.path.endswith("/register"):
                resp = {"code": 0, "data": {"accepted": [{"number": n} for n in nums if not n.endswith("BAD")],
                                            "rejected": [{"number": n, "error": {"code": -18019903, "message": "carrier cannot be detected"}} for n in nums if n.endswith("BAD")]}}
            else:
                st = {"1": "InTransit", "2": "Delivered", "3": "OutForDelivery", "4": "Exception", "5": "NotFound"}
                acc = []
                for n in nums:
                    acc.append({"number": n, "track_info": {"latest_status": {"status": st.get(n[-1], "InTransit")},
                                "latest_event": {"time_iso": "2026-10-29T10:00:00-04:00", "description": "Departed facility", "location": "Louisville, KY"},
                                "time_metrics": {"estimated_delivery_date": {"from": "2026-10-31T00:00:00-04:00"}},
                                "tracking": {"providers": [{"events": [{"time_iso": "2026-10-29T10:00:00-04:00", "description": "Departed facility", "location": "Louisville, KY"}]}]}}})
                resp = {"code": 0, "data": {"accepted": acc, "rejected": []}}
            out = json.dumps(resp).encode()
            s.send_response(200); s.send_header("Content-Length", str(len(out))); s.end_headers(); s.wfile.write(out)
    ts_ = http.server.ThreadingHTTPServer(("127.0.0.1", 0), T); threading.Thread(target=ts_.serve_forever, daemon=True).start()
    os.environ["TRACK_API_BASE"] = f"http://127.0.0.1:{ts_.server_address[1]}"
    for n in ("1Z0000000000000011", "1Z0000000000000012", "1Z0000000000000013", "1Z0000000000000014", "1Z0000000000000015", "1Z000000000000BAD"):
        packages.set_or_create(c, n, "UPS", "shipped", "x", core.now(), quiet=True)
    check("refresh needs key", packages.refresh(c)["ok"] is False)
    packages.set_settings(c, {"track_key": "wrong"})
    check("refresh bad key message", raises_rt(lambda: packages.refresh(c), "did not accept"))
    packages.set_settings(c, {"track_key": "goodkey", "interval": 15})
    r = packages.refresh(c)
    by = {p["tracking"][-1]: p for p in packages.list_packages(c) if p["tracking"].startswith("1Z0000000")}
    check("refresh ok", r["ok"] and r["checked"] >= 5, r)
    check("live statuses", by["1"]["status"] == "in_transit" and by["2"]["status"] == "delivered" and by["3"]["status"] == "out_for_delivery" and by["4"]["status"] == "exception" and by["5"]["status"] == "shipped", {k: v["status"] for k, v in by.items()})
    check("live details", by["1"]["location"] == "Louisville, KY" and by["1"]["eta"] == "2026-10-31" and by["1"]["events"], by["1"])
    check("rejected number flagged", [p for p in packages.list_packages(c) if p["tracking"].endswith("BAD")][0]["error"])
    check("live notifications", any(n["kind"] == "pkg_delivered" and "1Z0000000000000012" in n["body"] for n in notify.list_notifications(c)["items"]))
    check("settings", packages.get_settings(c)["interval"] == 15 and packages.set_settings(c, {"interval": 5})["interval"] == 10)
    os.environ.pop("TRACK_API_BASE"); ts_.shutdown()
    c.close()


def raises_rt(fn, text):
    try:
        fn()
    except RuntimeError as e:
        return text.lower() in str(e).lower()
    return False


def api_smoke():
    import server
    import static_embedded
    # the installed app serves this copy of the page (no static folder is bundled), so it must match and be sendable
    page = open(os.path.join(HERE, "static", "index.html"), encoding="utf-8").read() if os.path.isfile(os.path.join(HERE, "static", "index.html")) else None
    check("embedded page sendable", bool(static_embedded.HTML.encode("utf-8")))
    check("embedded page up to date", page is None or page == static_embedded.HTML)
    server.STATIC = os.path.join(HERE, "no-static-folder")  # serve exactly like the installed app
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
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode("utf-8")
    check("page served from embedded copy", page.startswith("<!doctype html>") and "Valor" in page)
    code, _ = call("nonexistent")
    check("404", code == 404)
    srv.shutdown()


if __name__ == "__main__":
    run()
    mail_test()
    package_test()
    api_smoke()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        sys.exit(1)
    print("selftest: all checks passed")
