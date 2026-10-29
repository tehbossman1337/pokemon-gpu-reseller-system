"""Package tracking.

Tracking numbers are pulled from order / shipping emails (and from carrier emails such as UPS, USPS and FedEx),
stored, linked to the order, and followed until delivered by:
  1. later emails (out for delivery, delivered, delay ...),
  2. optional live lookups through the 17TRACK API (add your free key in Settings),
  3. manual status changes.
Every important change goes through notify.notify().
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import core
import notify

STATUSES = ["label_created", "shipped", "in_transit", "pickup", "out_for_delivery", "exception", "delivered"]
LABELS = {"label_created": "Label created", "shipped": "Shipped", "in_transit": "In transit", "pickup": "Ready for pickup",
          "out_for_delivery": "Out for delivery", "exception": "Delivery problem", "delivered": "Delivered"}
RANK = {"label_created": 0, "shipped": 1, "in_transit": 2, "exception": 2, "pickup": 3, "out_for_delivery": 3, "delivered": 9}
NOTIFY_KIND = {"shipped": "pkg_shipped", "label_created": "pkg_shipped", "out_for_delivery": "pkg_out", "pickup": "pkg_out",
               "delivered": "pkg_delivered", "exception": "pkg_exception"}
RECENT_DAYS = 3        # don't notify about old emails found while reading history
ACTIVE_DAYS = 60       # stop following a package that has not moved in this long
CARRIER_DOMAINS = {"UPS": ["ups.com"], "USPS": ["usps.com", "usps.gov"], "FedEx": ["fedex.com"], "DHL": ["dhl.com"],
                   "OnTrac": ["ontrac.com"], "LaserShip": ["lasership.com"], "Amazon": ["amazon.com"]}

URLS = {"UPS": "https://www.ups.com/track?tracknum={}", "USPS": "https://tools.usps.com/go/TrackConfirmAction?tLabels={}",
        "FedEx": "https://www.fedex.com/fedextrack/?trknbr={}", "DHL": "https://www.dhl.com/en/express/tracking.html?AWB={}",
        "OnTrac": "https://www.ontrac.com/tracking/?number={}", "LaserShip": "https://www.lasership.com/track/{}"}
GENERIC_URL = "https://t.17track.net/en#nums={}"


def track_url(carrier, number):
    return URLS.get(carrier, GENERIC_URL).format(number)


# ------------------------------------------------------------------ finding tracking numbers in text
_UPS = r"1Z[0-9A-Z]{16}"
_USPS = r"(?:420\d{5})?9[2-5]\d{18,22}|[A-Z]{2}\d{9}US"
_AMZ = r"TBA\d{9,15}"
_ONTRAC = r"[CD]\d{14}"
_LASER = r"1LS\d{12}"
KNOWN = [("UPS", re.compile(rf"^{_UPS}$")), ("USPS", re.compile(rf"^(?:{_USPS})$")), ("Amazon", re.compile(rf"^{_AMZ}$")),
         ("LaserShip", re.compile(rf"^{_LASER}$")), ("OnTrac", re.compile(rf"^{_ONTRAC}$"))]
SCAN = re.compile(rf"\b({_UPS}|{_USPS}|{_AMZ}|{_LASER})\b")
PARAM = re.compile(r"(?:trknbr|tracknum|tracknumbers?|trackingnumbers?|tracking_number|tlabels|inquirynumber\d|awb|trackingnum|nums?)=([A-Za-z0-9]{8,35})", re.I)
LABELED = re.compile(r"tracking\s*(?:number|no\.?|#|id|code)?\s*(?:is)?\s*[:#]?\s*([A-Z0-9]{10,34})\b", re.I)


def detect_carrier(number, hint=""):
    for name, rx in KNOWN:
        if rx.match(number):
            return name
    h = hint.lower()
    if re.match(r"^(\d{12}|\d{15}|96\d{20})$", number) and "fedex" in h:
        return "FedEx"
    if re.match(r"^\d{10}$", number) and "dhl" in h:
        return "DHL"
    if re.match(r"^\d{12}$|^\d{15}$", number):
        return "FedEx" if "fedex" in h else ""
    return ""


def _plausible(n):
    n = n.upper()
    if any(rx.match(n) for _, rx in KNOWN):
        return True
    return sum(ch.isdigit() for ch in n) >= 10 and len(n) <= 30 and not re.match(r"^(19|20)\d{6}", n)


def extract(text):
    """Return [{'tracking','carrier'}] found in an email, most reliable first."""
    found, seen = [], set()
    low = text.lower()

    def add(num, pos):
        num = re.sub(r"[\s-]", "", num).upper()
        if num in seen or not _plausible(num):
            return
        seen.add(num)
        found.append({"tracking": num, "carrier": detect_carrier(num, low[max(0, pos - 160):pos + 160] + (" fedex" if "fedex.com" in low[max(0, pos - 160):pos + 160] else ""))})
    for m in SCAN.finditer(text):
        add(m.group(1), m.start())
    for m in PARAM.finditer(text):
        add(m.group(1), m.start())
    for m in LABELED.finditer(text):
        add(m.group(1), m.start())
    return found


# ------------------------------------------------------------------ carrier / status emails
STATUS_RULES = [
    ("delivered", r"(?<!to be )(?<!will be )(?<!un)(?<!not )\bdelivered\b|delivery complete|left (at|with) (your|the)"),
    ("out_for_delivery", r"out for delivery|arriving today|delivery today|on (the|a) (vehicle|truck) for delivery|will be delivered today"),
    ("pickup", r"ready for pick ?up|available for pick ?up|waiting for pick ?up"),
    ("exception", r"\bdelay(ed)?\b|exception|unable to deliver|delivery attempt|attempted|undeliverable|returned to sender|address (issue|problem)|action required"),
    ("in_transit", r"in transit|on the way|on its way|departed|arrived at|shipment received|picked up|processed at"),
    ("label_created", r"label created|shipping label|pre-shipment|shipment information received|order processed"),
    ("shipped", r"\bshipped\b|has shipped"),
]


def classify(text):
    for status, rx in STATUS_RULES:
        if re.search(rx, text, re.I):
            return status
    return None


def carrier_of_sender(sender):
    s = (sender or "").lower()
    for name, doms in CARRIER_DOMAINS.items():
        if any(d in s for d in doms):
            return name
    return None


def _iso_date(date_header):
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_header).replace(tzinfo=None).isoformat(timespec="seconds")
    except Exception:
        return core.now()


def process_carrier_email(c, sender, subject, bodies, date_header):
    """Handle a UPS/USPS/FedEx/DHL/Amazon notification. Returns (outcome, carrier) or None if it isn't one."""
    carrier = carrier_of_sender(sender)
    if not carrier:
        return None
    bodies = bodies if isinstance(bodies, list) else [bodies]
    text = subject + "\n" + "\n".join(b[:3000] for b in bodies)
    nums = extract(text)
    if not nums:
        return "skipped (carrier email without tracking number)", carrier
    status = classify(subject + "\n" + bodies[0][:1500]) or "in_transit"
    when = _iso_date(date_header)
    changed = []
    for n in nums[:5]:
        r = set_or_create(c, n["tracking"], n["carrier"] or carrier, status, subject[:200], when, source=f"{carrier} email")
        if r:
            changed.append(r)
    return (f"package {nums[0]['tracking']}: {LABELS[status]}" if changed else f"package {nums[0]['tracking']}: no change"), carrier


# ------------------------------------------------------------------ store
def _norm(n):
    return re.sub(r"[\s-]", "", str(n or "")).upper()


def _recent(when):
    try:
        return datetime.now() - datetime.fromisoformat(str(when)[:19]) <= timedelta(days=RECENT_DAYS)
    except Exception:
        return True


def _describe(c, order_id):
    if not order_id:
        return ""
    items = c.execute("SELECT name, qty FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    return ", ".join(f"{i['qty']}x {i['name']}" for i in items)[:300]


def _push(c, pkg, status, when, detail=""):
    kind = NOTIFY_KIND.get(status)
    if not kind or not _recent(when):
        return
    who = pkg["retailer"] or pkg["carrier"] or "Package"
    title = f"{who}: {LABELS[status].lower()}"
    bits = [pkg["description"] or (f"Order {pkg['order_number']}" if pkg["order_number"] else ""), f"{pkg['carrier']} {pkg['tracking']}".strip(), detail]
    notify.notify(c, kind, title, "\n".join(b for b in bits if b), dedupe=f"pkg:{pkg['tracking']}:{status}", link="packages")


def set_or_create(c, tracking, carrier="", status="shipped", detail="", when="", source="email", retailer="", order_id=None,
                  order_number="", description="", location="", eta="", events=None, quiet=False):
    """Create the package or move it forward. Returns 'new' / 'updated' / None (nothing changed)."""
    tn = _norm(tracking)
    if not tn:
        return None
    when = when or core.now()
    row = c.execute("SELECT * FROM packages WHERE tracking=?", (tn,)).fetchone()
    if row is None:
        carrier = carrier or detect_carrier(tn)
        desc = description or _describe(c, order_id)
        c.execute("INSERT INTO packages(tracking,carrier,retailer,order_id,order_number,description,status,detail,location,eta,shipped_at,"
                  "delivered_at,last_change,events,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (tn, carrier, retailer, order_id, order_number, desc, status, detail, location, eta, when[:19],
                   when[:19] if status == "delivered" else "", when[:19], json.dumps(events or []), source, core.now()))
        c.commit()
        if not quiet:
            _push(c, c.execute("SELECT * FROM packages WHERE tracking=?", (tn,)).fetchone(), status, when, detail)
        return "new"
    changed = False
    upd = {}
    for k, v in (("carrier", carrier or detect_carrier(tn)), ("retailer", retailer), ("order_number", order_number),
                 ("description", description or _describe(c, order_id or row["order_id"]))):
        if v and not row[k]:
            upd[k] = v
    if order_id and not row["order_id"]:
        upd["order_id"] = order_id
    cur = row["status"]
    move = cur != "delivered" and cur != status and not (RANK[status] < RANK[cur] and status != "exception")
    if move:
        upd.update(status=status, detail=detail or row["detail"], last_change=when[:19])
        if status == "delivered":
            upd["delivered_at"] = when[:19]
        changed = True
    elif cur == status and detail and detail != row["detail"] and cur != "delivered":
        upd["detail"] = detail
    if location:
        upd["location"] = location
    if eta:
        upd["eta"] = eta
    if events:
        upd["events"] = json.dumps(events)
    if upd:
        c.execute(f"UPDATE packages SET {','.join(k + '=?' for k in upd)} WHERE id=?", list(upd.values()) + [row["id"]])
        c.commit()
    if changed and not quiet:
        _push(c, c.execute("SELECT * FROM packages WHERE id=?", (row["id"],)).fetchone(), status, when, detail)
    return "updated" if (changed or upd) else None


def on_order_event(c, ev, order_id):
    """Called for every parsed retailer email: remember its tracking numbers and follow the order's status."""
    status = {"shipped": "shipped", "delivered": "delivered", "confirmed": "shipped"}.get(ev["status"])
    when = (ev.get("date") or core.now()[:10]) + "T12:00:00" if len(str(ev.get("date") or "")) == 10 else (ev.get("date") or core.now())
    order = c.execute("SELECT order_number FROM orders WHERE id=?", (order_id,)).fetchone()
    onum = order["order_number"] if order else ev.get("order_number", "")
    pk = ev.get("packages") or ([{"tracking": ev["tracking"], "carrier": ""}] if ev.get("tracking") else [])
    if status:
        for p in pk:
            if ev["status"] == "confirmed" and not p.get("tracking"):
                continue
            set_or_create(c, p["tracking"], p.get("carrier", ""), status, f"{ev['retailer']} email", when, source="order email",
                          retailer=ev["retailer"], order_id=order_id, order_number=onum)
    if ev["status"] == "delivered":  # the store says it arrived: close every package of this order
        for r in c.execute("SELECT tracking FROM packages WHERE order_id=? AND status!='delivered'", (order_id,)).fetchall():
            set_or_create(c, r["tracking"], status="delivered", detail=f"{ev['retailer']} says delivered", when=when, source="order email")
    if ev["status"] == "cancelled" and _recent(when):
        notify.notify(c, "order_cancelled", f"{ev['retailer']} order cancelled", f"Order {onum}", dedupe=f"cancel:{order_id}", link="orders")


# ------------------------------------------------------------------ list / manual edits
def is_active(r):
    if r["status"] == "delivered":
        return False
    try:
        return datetime.now() - datetime.fromisoformat((r["last_change"] or r["created_at"])[:19]) <= timedelta(days=ACTIVE_DAYS)
    except Exception:
        return True


def list_packages(c):
    out = []
    for r in c.execute("SELECT * FROM packages ORDER BY (status='delivered'), COALESCE(NULLIF(last_change,''), created_at) DESC"):
        d = dict(r)
        d["events"] = json.loads(d["events"] or "[]")
        d["label"] = LABELS.get(d["status"], d["status"])
        d["url"] = track_url(d["carrier"], d["tracking"])
        d["active"] = is_active(r)
        out.append(d)
    return out


def save_package(c, d, pid=None):
    tn = _norm(d.get("tracking"))
    if not tn or len(tn) < 8:
        raise ValueError("Enter a tracking number (at least 8 characters)")
    carrier = str(d.get("carrier") or "").strip() or detect_carrier(tn)
    if pid:
        c.execute("UPDATE packages SET tracking=?,carrier=?,retailer=?,description=?,notes=? WHERE id=?",
                  (tn, carrier, str(d.get("retailer") or "").strip(), str(d.get("description") or "").strip(), str(d.get("notes") or "").strip(), pid))
        c.commit()
        return pid
    if c.execute("SELECT 1 FROM packages WHERE tracking=?", (tn,)).fetchone():
        raise ValueError("That tracking number is already being tracked")
    set_or_create(c, tn, carrier, "shipped", "Added manually", core.now(), source="manual", retailer=str(d.get("retailer") or "").strip(),
                  description=str(d.get("description") or "").strip(), quiet=True)
    if d.get("notes"):
        c.execute("UPDATE packages SET notes=? WHERE tracking=?", (str(d["notes"]).strip(), tn))
        c.commit()
    return c.execute("SELECT id FROM packages WHERE tracking=?", (tn,)).fetchone()[0]


def set_status_manual(c, pid, status):
    if status not in STATUSES:
        raise ValueError("Unknown status")
    r = c.execute("SELECT * FROM packages WHERE id=?", (pid,)).fetchone()
    if not r:
        raise ValueError("Package not found")
    c.execute("UPDATE packages SET status=?, last_change=?, delivered_at=? WHERE id=?",
              (status, core.now(), core.now() if status == "delivered" else "", pid))
    c.commit()


def delete_package(c, pid):
    c.execute("DELETE FROM packages WHERE id=?", (pid,))
    c.commit()


# ------------------------------------------------------------------ live tracking (17TRACK, optional)
def get_settings(c):
    g = lambda k, d="": (c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone() or [d])[0]  # noqa: E731
    try:
        interval = max(10, int(g("pkg.interval", "30")))
    except ValueError:
        interval = 30
    return {"track_key": g("pkg.track_key"), "interval": interval, "auto": g("pkg.auto", "1") != "0"}


def set_settings(c, d):
    if "track_key" in d and d["track_key"] != "__keep__":
        c.execute("INSERT OR REPLACE INTO settings VALUES('pkg.track_key',?)", (str(d["track_key"]).strip(),))
    if "interval" in d:
        try:
            iv = max(10, min(1440, int(d["interval"])))
        except (TypeError, ValueError):
            raise ValueError("Check interval must be a number of minutes (10 or more)")
        c.execute("INSERT OR REPLACE INTO settings VALUES('pkg.interval',?)", (str(iv),))
    if "auto" in d:
        c.execute("INSERT OR REPLACE INTO settings VALUES('pkg.auto',?)", ("1" if d["auto"] else "0",))
    c.commit()
    return get_settings(c)


STATE = {"running": False, "last_run": "", "last_result": "", "error": ""}
MAP17 = {"InfoReceived": "label_created", "InTransit": "in_transit", "OutForDelivery": "out_for_delivery", "AvailableForPickup": "pickup",
         "DeliveryFailure": "exception", "Exception": "exception", "Delivered": "delivered", "Expired": "exception"}


def _api(path, body, key):
    base = os.environ.get("TRACK_API_BASE", "https://api.17track.net/track/v2.2")
    req = urllib.request.Request(f"{base}/{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"17token": key, "Content-Type": "application/json", "User-Agent": "ValorInventoryAgent"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=25))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("17TRACK did not accept the key - check it in Settings")
        if e.code == 429:
            raise RuntimeError("17TRACK is rate limiting - will retry later")
        raise RuntimeError(f"17TRACK answered {e.code}")
    except Exception as e:
        raise RuntimeError(f"Couldn't reach 17TRACK: {e}")


def parse_info(a):
    ti = a.get("track_info") or {}
    ls = ti.get("latest_status") or {}
    le = ti.get("latest_event") or {}
    eta = (ti.get("time_metrics") or {}).get("estimated_delivery_date") or {}
    events = []
    for p in (ti.get("tracking") or {}).get("providers") or []:
        for e in p.get("events") or []:
            events.append({"time": e.get("time_iso") or e.get("time_utc") or "", "desc": e.get("description") or "", "loc": e.get("location") or ""})
    events.sort(key=lambda e: e["time"], reverse=True)
    return {"status": MAP17.get(ls.get("status")), "raw": ls.get("status"), "detail": le.get("description") or "", "location": le.get("location") or "",
            "when": (le.get("time_iso") or le.get("time_utc") or "")[:19], "eta": str(eta.get("from") or eta.get("to") or "")[:10], "events": events[:25]}


def refresh(c, only_ids=None):
    """Ask 17TRACK about every package still on its way. Returns a summary dict."""
    key = get_settings(c)["track_key"]
    if not key:
        return {"ok": False, "error": "Add your 17TRACK key in Settings → Package tracking to enable live tracking."}
    rows = [r for r in c.execute("SELECT * FROM packages WHERE status!='delivered'") if is_active(r) and (not only_ids or r["id"] in only_ids)]
    if not rows:
        return {"ok": True, "checked": 0, "changed": 0}
    changed = 0
    by_num = {r["tracking"]: r for r in rows}
    todo = [r["tracking"] for r in rows if not r["registered"]]
    for i in range(0, len(todo), 40):
        resp = _api("register", [{"number": n} for n in todo[i:i + 40]], key)
        data = resp.get("data") or {}
        ok_nums = [a["number"] for a in data.get("accepted") or []]
        for rj in data.get("rejected") or []:
            code = (rj.get("error") or {}).get("code")
            if code == -18019901:  # already registered
                ok_nums.append(rj["number"])
            else:
                c.execute("UPDATE packages SET error=?, last_checked=? WHERE tracking=?", ((rj.get("error") or {}).get("message", "rejected")[:200], core.now(), rj["number"]))
        for n in ok_nums:
            c.execute("UPDATE packages SET registered=1, error='' WHERE tracking=?", (n,))
        c.commit()
        time.sleep(0.4 if os.environ.get("TRACK_API_BASE") is None else 0)
    nums = [r["tracking"] for r in c.execute("SELECT tracking FROM packages WHERE status!='delivered' AND registered=1") if r["tracking"] in by_num]
    for i in range(0, len(nums), 40):
        resp = _api("gettrackinfo", [{"number": n} for n in nums[i:i + 40]], key)
        data = resp.get("data") or {}
        for a in data.get("accepted") or []:
            n = a.get("number")
            info = parse_info(a)
            c.execute("UPDATE packages SET last_checked=?, error='' WHERE tracking=?", (core.now(), n))
            if info["status"]:
                r = set_or_create(c, n, "", info["status"], info["detail"], info["when"] or core.now(), source="17TRACK",
                                  location=info["location"], eta=info["eta"], events=info["events"])
                changed += 1 if r else 0
            elif info["events"] or info["eta"]:
                set_or_create(c, n, "", by_num[n]["status"], info["detail"], core.now(), source="17TRACK", location=info["location"],
                              eta=info["eta"], events=info["events"])
        for rj in data.get("rejected") or []:
            c.execute("UPDATE packages SET registered=0, error=?, last_checked=? WHERE tracking=?",
                      ((rj.get("error") or {}).get("message", "not found yet")[:200], core.now(), rj.get("number")))
        c.commit()
        time.sleep(0.4 if os.environ.get("TRACK_API_BASE") is None else 0)
    return {"ok": True, "checked": len(nums), "changed": changed}


def run_refresh(only_ids=None):
    """Thread-safe wrapper that records the outcome for the UI."""
    if STATE["running"]:
        return {"ok": False, "error": "A check is already running"}
    STATE["running"] = True
    c = core.connect()
    try:
        r = refresh(c, only_ids)
        STATE.update(last_run=core.now(), last_result=("Checked %s, %s updated" % (r.get("checked"), r.get("changed"))) if r.get("ok") else r.get("error", ""),
                     error="" if r.get("ok") else r.get("error", ""))
        return r
    except Exception as e:
        STATE.update(last_run=core.now(), last_result=str(e), error=str(e))
        return {"ok": False, "error": str(e)}
    finally:
        STATE["running"] = False
        c.close()


_POLLER = {"thread": None}


def start_poller():
    """Background loop: re-check packages every few minutes (only when a 17TRACK key is saved)."""
    if _POLLER["thread"] is not None:
        return

    def loop():
        last = 0.0
        while True:
            time.sleep(20)
            try:
                c = core.connect()
                s = get_settings(c)
                c.close()
                if s["track_key"] and s["auto"] and time.time() - last >= s["interval"] * 60:
                    last = time.time()
                    run_refresh()
            except Exception:
                pass
    t = threading.Thread(target=loop, daemon=True)
    _POLLER["thread"] = t
    t.start()
