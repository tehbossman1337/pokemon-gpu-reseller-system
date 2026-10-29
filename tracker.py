"""Reselling tracker logic (inventory, sales, receipts, dashboard, tax, scan station).

Mirrors the "Pokemon & Graphics Card Reselling Tracker" workbook:
  * Inventory rows are SKU lots (qty purchased); sold/remaining/status/profit/ROI are derived from Sales.
  * A sale's fees come from the Fees table by platform:  (price + shipping charged) * fee% + fixed fee,
    plus any other fees.  Net = price + shipping charged - fees - your shipping cost.
    Profit = net - cost basis (qty * purchase price * FX rate).
"""
import csv
import io
import json
import re
import threading
import urllib.request
from datetime import date, datetime, timedelta

import lists_data

CATEGORIES = ["Pokemon Card", "Graphics Card"]
DEFAULT_SETTINGS = {"partner1": "Me", "partner2": "Business Partner", "aging_days": 90, "drop_pct": 0.15,
                    "monthly_target": 0, "big_sale": 0, "slack_webhook": "", "auto_update": 1, "tax_year": 0}

GPU_WORDS = re.compile(r"\b(rtx|gtx|radeon|geforce|gpu|graphics card|video card|nvidia)\b", re.I)


SKU_LOCK = threading.RLock()  # serialises anything that picks a new SKU so concurrent requests can't collide


def locked(fn):
    def w(*a, **k):
        with SKU_LOCK:
            return fn(*a, **k)
    w.__name__ = fn.__name__
    w.__doc__ = fn.__doc__
    return w


def today():
    return date.today().isoformat()


def now():
    return datetime.now().isoformat(timespec="seconds")


def num(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def parse_date(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def r2(x):
    return None if x is None else round(x + 0.0, 2)


# ---------------------------------------------------------------- seed / settings
def ensure_seed(c):
    if c.execute("SELECT COUNT(*) FROM fees").fetchone()[0] == 0:
        for f in lists_data.DATA["fees"]:
            c.execute("INSERT INTO fees VALUES(?,?,?,?,?)", (f["platform"], f["fee_pct"], f["fixed"], f["processing"], f["notes"]))
    if c.execute("SELECT COUNT(*) FROM currencies").fetchone()[0] == 0:
        for f in lists_data.DATA["currencies"]:
            c.execute("INSERT INTO currencies VALUES(?,?,?)", (f["code"], f["name"], f["rate"]))
    c.commit()


def get_settings(c):
    out = dict(DEFAULT_SETTINGS)
    for r in c.execute("SELECT key,value FROM settings WHERE key LIKE 't.%'"):
        k = r["key"][2:]
        if k in out:
            try:
                out[k] = type(DEFAULT_SETTINGS[k])(json.loads(r["value"]))
            except Exception:
                pass
    return out


def set_settings(c, d):
    cur = get_settings(c)
    for k, dv in DEFAULT_SETTINGS.items():
        if k not in d:
            continue
        v = d[k]
        if isinstance(dv, str):
            v = str(v or "").strip()
        elif isinstance(dv, float):
            v = max(0.0, min(num(v, dv), 1.0))
        else:
            v = max(0, int(num(v, dv)))
        cur[k] = v
        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", ("t." + k, json.dumps(v)))
    c.commit()
    return cur


def team(c):
    s = get_settings(c)
    return [s["partner1"] or "Me", s["partner2"] or "Business Partner", "Other / Unassigned"]


def lists(c):
    d = dict(lists_data.DATA["lists"])
    d["team"] = team(c)
    d["platforms"] = [r["platform"] for r in c.execute("SELECT platform FROM fees ORDER BY rowid")]
    d["currencies"] = [r["code"] for r in c.execute("SELECT code FROM currencies ORDER BY rowid")]
    d["visual"] = lists_data.DATA["visual"]
    d["skus"] = [r["sku"] for r in c.execute("SELECT sku FROM items ORDER BY sku")]
    return d


# ---------------------------------------------------------------- fees & currencies
def list_fees(c):
    return [dict(r) for r in c.execute("SELECT * FROM fees ORDER BY rowid")]


def save_fee(c, d, old=None):
    p = str(d.get("platform") or "").strip()
    if not p:
        raise ValueError("Platform name is required")
    pct = num(d.get("fee_pct"))
    if pct > 1:  # allow "13.35" typed as a percent
        pct /= 100.0
    if pct < 0 or pct > 1:
        raise ValueError("Fee % must be between 0 and 100")
    fixed = max(0.0, num(d.get("fixed")))
    if old and old != p:
        c.execute("DELETE FROM fees WHERE platform=?", (old,))
        c.execute("UPDATE tsales SET platform=? WHERE platform=?", (p, old))
    c.execute("INSERT OR REPLACE INTO fees VALUES(?,?,?,?,?)", (p, pct, fixed, str(d.get("processing") or ""), str(d.get("notes") or "")))
    c.commit()


def delete_fee(c, platform):
    c.execute("DELETE FROM fees WHERE platform=?", (platform,))
    c.commit()


def list_currencies(c):
    return [dict(r) for r in c.execute("SELECT * FROM currencies ORDER BY rowid")]


def save_currency(c, d):
    code = str(d.get("code") or "").strip().upper()
    rate = num(d.get("rate"), -1)
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError("Currency code must be 3 letters, like CAD")
    if rate <= 0:
        raise ValueError("Rate must be greater than 0")
    c.execute("INSERT OR REPLACE INTO currencies VALUES(?,?,?)", (code, str(d.get("name") or code), rate))
    c.commit()


def delete_currency(c, code):
    if code == "USD":
        raise ValueError("USD can't be removed")
    c.execute("DELETE FROM currencies WHERE code=?", (code,))
    c.commit()


# ---------------------------------------------------------------- items
def infer_category(name):
    return "Graphics Card" if GPU_WORDS.search(name or "") else "Pokemon Card"


def next_sku(c, category):
    prefix = "GC" if category == "Graphics Card" else "PK"
    top = 0
    for r in c.execute("SELECT sku FROM items WHERE sku LIKE ?", (prefix + "-%",)):
        m = re.fullmatch(prefix + r"-(\d+)", r["sku"])
        if m:
            top = max(top, int(m.group(1)))
    return f"{prefix}-{top + 1:04d}"


def _fx(c, code):
    r = c.execute("SELECT rate FROM currencies WHERE code=?", (code or "USD",)).fetchone()
    return r["rate"] if r else 1.0


ITEM_COLS = ["sku", "category", "name", "pokemon_set", "card_no", "condition", "grading", "qty", "added_by",
             "purchase_date", "purchase_price", "purchase_source", "listing_platform", "listed_price", "currency", "market_price",
             "location", "notes", "barcode"]


def _clean_item(c, d, old=None):
    d = dict(d)
    cat = d.get("category") or (old["category"] if old else "")
    name = str(d.get("name") or "").strip()
    if cat not in CATEGORIES:
        cat = infer_category(name) if name else "Pokemon Card"
    qty = d.get("qty")
    qty = 1 if qty in (None, "") else int(num(qty, -1))
    if qty < 1:
        raise ValueError("Quantity must be at least 1")
    pp = num(d.get("purchase_price"), 0)
    if pp < 0:
        raise ValueError("Purchase price can't be negative")
    lp = num(d.get("listed_price"), 0)
    if lp < 0:
        raise ValueError("Listed price can't be negative")
    pd = str(d.get("purchase_date") or "").strip()
    if pd and not parse_date(pd):
        raise ValueError("Purchase date must look like 2026-10-28")
    cur = str(d.get("currency") or "USD").upper()
    if not c.execute("SELECT 1 FROM currencies WHERE code=?", (cur,)).fetchone():
        raise ValueError(f"Unknown currency {cur}. Add it in Settings first.")
    sku = str(d.get("sku") or "").strip().upper()
    if not name and not d.get("pokemon_set"):
        raise ValueError("Give the item a name")
    if not name:
        name = d.get("pokemon_set")
    out = {"category": cat, "name": name, "qty": qty, "purchase_price": pp, "listed_price": lp, "purchase_date": pd, "currency": cur,
           "market_price": max(0.0, num(d.get("market_price"), 0))}
    for k in ("pokemon_set", "card_no", "condition", "grading", "added_by", "purchase_source", "listing_platform",
              "location", "notes", "barcode"):
        out[k] = str(d.get(k) or "").strip()
    if out["barcode"]:
        dup = c.execute("SELECT sku FROM items WHERE barcode=? AND id<>?", (out["barcode"], old["id"] if old else -1)).fetchone()
        if dup:
            raise ValueError(f"Barcode {out['barcode']} already belongs to {dup['sku']}")
    out["sku"] = sku or (old["sku"] if old else next_sku(c, cat))
    return out


@locked
def save_item(c, d, item_id=None):
    old = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone() if item_id else None
    if item_id and not old:
        raise ValueError("That item no longer exists")
    v = _clean_item(c, d, old)
    dup = c.execute("SELECT id FROM items WHERE sku=?", (v["sku"],)).fetchone()
    if dup and dup["id"] != item_id:
        raise ValueError(f"SKU {v['sku']} is already used. Pick a different one.")
    if old:
        sold = c.execute("SELECT COALESCE(SUM(qty),0) FROM tsales WHERE sku=?", (old["sku"],)).fetchone()[0]
        if v["qty"] < sold:
            raise ValueError(f"{sold} already sold, so quantity can't go below {sold}.")
        if v["sku"] != old["sku"]:
            c.execute("UPDATE tsales SET sku=? WHERE sku=?", (v["sku"], old["sku"]))
            c.execute("UPDATE receipts SET sku=? WHERE sku=?", (v["sku"], old["sku"]))
        sets = ",".join(k + "=?" for k in ITEM_COLS)
        c.execute(f"UPDATE items SET {sets}, updated_at=? WHERE id=?", [v[k] for k in ITEM_COLS] + [now(), item_id])
        c.commit()
        return item_id
    cur = c.execute(f"INSERT INTO items({','.join(ITEM_COLS)},created_at,updated_at) VALUES({','.join('?' * len(ITEM_COLS))},?,?)",
                    [v[k] for k in ITEM_COLS] + [now(), now()])
    c.commit()
    return cur.lastrowid


def delete_item(c, item_id):
    r = c.execute("SELECT sku FROM items WHERE id=?", (item_id,)).fetchone()
    if not r:
        return
    n = c.execute("SELECT COUNT(*) FROM tsales WHERE sku=?", (r["sku"],)).fetchone()[0]
    if n:
        raise ValueError(f"{r['sku']} has {n} sale(s) logged. Delete those sales first.")
    c.execute("DELETE FROM items WHERE id=?", (item_id,))
    c.commit()


def _sale_calc(s, item, fees, fx):
    """Return the derived money fields for one sale row (dicts in, dict out)."""
    price, ship_c, ship_k, other, qty = s["sale_price"], s["ship_charged"], s["ship_cost"], s["other_fees"], s["qty"]
    f = fees.get(s["platform"])
    pct = f["fee_pct"] if f else 0.0
    fixed = f["fixed"] if f else 0.0
    pfee = (price + ship_c) * pct + fixed
    total = pfee + other
    net = price + ship_c - total - ship_k
    basis = qty * item["purchase_price"] * fx if item and item["purchase_price"] else None
    profit = None if basis is None else net - basis
    roi = None if basis in (None, 0) else profit / basis
    return {"fee_pct": pct, "platform_fee": r2(pfee), "total_fees": r2(total), "net": r2(net), "cost_basis": r2(basis),
            "profit": r2(profit), "roi": None if roi is None else round(roi, 4)}


def _load(c):
    fees = {r["platform"]: dict(r) for r in c.execute("SELECT * FROM fees")}
    fxs = {r["code"]: r["rate"] for r in c.execute("SELECT * FROM currencies")}
    items = {r["sku"]: dict(r) for r in c.execute("SELECT * FROM items")}
    return fees, fxs, items


def list_sales(c):
    fees, fxs, items = _load(c)
    rows = [dict(r) for r in c.execute("SELECT * FROM tsales ORDER BY sale_date, id")]
    buyers = {}
    for r in rows:
        b = (r["buyer"] or "").strip().lower()
        if b:
            buyers[b] = buyers.get(b, 0) + 1
    seen = set()
    for r in rows:
        it = items.get(r["sku"])
        r["sale_id"] = f"S-{r['id']:04d}"
        r["item_name"] = it["name"] if it else "(item deleted)"
        r["category"] = it["category"] if it else ""
        r.update(_sale_calc(r, it, fees, fxs.get(it["currency"], 1.0) if it else 1.0))
        b = (r["buyer"] or "").strip().lower()
        r["buyer_count"] = buyers.get(b) if b else None
        r["repeat_first"] = buyers[b] if b and b not in seen and buyers[b] > 1 else None
        seen.add(b)
    rows.sort(key=lambda r: (r["sale_date"] or "", r["id"]), reverse=True)
    return rows


def list_items(c, sales=None):
    fees, fxs, items = _load(c)
    sales = sales if sales is not None else list_sales(c)
    agg = {}
    for s in sales:
        a = agg.setdefault(s["sku"], {"qty": 0, "price": 0.0, "fees": 0.0, "net": 0.0, "profit": 0.0, "date": "", "has_profit": False})
        a["qty"] += s["qty"]
        a["price"] += s["sale_price"]
        a["fees"] += s["total_fees"] or 0
        a["net"] += s["net"] or 0
        if s["profit"] is not None:
            a["profit"] += s["profit"]
            a["has_profit"] = True
        a["date"] = max(a["date"], s["sale_date"] or "")
    out = []
    t = date.today()
    for it in sorted(items.values(), key=lambda x: x["sku"]):
        a = agg.get(it["sku"])
        sold = a["qty"] if a else 0
        remaining = max(0, it["qty"] - sold)
        fx = fxs.get(it["currency"], 1.0)
        status = ("Listed for Sale" if it["listed_price"] else "In Stock") if sold == 0 else ("Sold" if remaining <= 0 else "Partially Sold")
        pdte = parse_date(it["purchase_date"])
        days = None
        if pdte:
            end = parse_date(a["date"]) if (remaining <= 0 and a and a["date"]) else t
            days = (end - pdte).days
        profit = a["profit"] if a and a["has_profit"] else None
        basis_sold = sold * it["purchase_price"] * fx
        q = (it["pokemon_set"] if it["category"] == "Pokemon Card" else it["name"]) or ""
        photo = ("https://www.google.com/search?tbm=isch&q=" + re.sub(r"\s+", "+", q.strip()) +
                 ("+pokemon+card+set" if it["category"] == "Pokemon Card" else "")) if q and q != "Other / Not Listed" else ""
        it.update({"qty_sold": sold, "qty_remaining": remaining, "status": status, "fx": fx,
                   "cost_on_hand": r2(remaining * it["purchase_price"] * fx), "total_cost": r2(it["qty"] * it["purchase_price"] * fx),
                   "sale_date": a["date"] if a else "", "sale_total": r2(a["price"]) if a else None, "fees_total": r2(a["fees"]) if a else None,
                   "net_total": r2(a["net"]) if a else None, "profit": r2(profit),
                   "roi": round(profit / basis_sold, 4) if profit is not None and basis_sold > 0 else None,
                   "days_held": days, "days_in_stock": days if remaining > 0 else None, "photo": photo})
        out.append(it)
    return out


# ---------------------------------------------------------------- sales
def _post_slack(url, text):
    def go():
        try:
            req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def save_sale(c, d, sale_id=None):
    sku = str(d.get("sku") or "").strip().upper()
    it = c.execute("SELECT * FROM items WHERE sku=?", (sku,)).fetchone()
    if not it:
        raise ValueError(f"No inventory item with SKU '{sku}'. Add it on the Inventory tab first.")
    old = c.execute("SELECT * FROM tsales WHERE id=?", (sale_id,)).fetchone() if sale_id else None
    if sale_id and not old:
        raise ValueError("That sale no longer exists")
    qty = 1 if d.get("qty") in (None, "") else int(num(d.get("qty"), -1))
    if qty < 1:
        raise ValueError("Quantity sold must be at least 1")
    sold = c.execute("SELECT COALESCE(SUM(qty),0) FROM tsales WHERE sku=? AND id IS NOT ?", (sku, sale_id)).fetchone()[0]
    if qty + sold > it["qty"]:
        raise ValueError(f"Only {max(0, it['qty'] - sold)} of {sku} left to sell.")
    for k, label in (("sale_price", "Sale price"), ("ship_charged", "Shipping charged"), ("ship_cost", "Shipping cost"), ("other_fees", "Other fees")):
        if num(d.get(k)) < 0:
            raise ValueError(f"{label} can't be negative")
    sd = str(d.get("sale_date") or today()).strip()
    if not parse_date(sd):
        raise ValueError("Sale date must look like 2026-10-28")
    if parse_date(it["purchase_date"]) and parse_date(sd) < parse_date(it["purchase_date"]):
        raise ValueError("Sale date is before the purchase date")
    vals = (sku, sd, str(d.get("sold_by") or "").strip(), str(d.get("platform") or "").strip(), str(d.get("buyer") or "").strip(),
            num(d.get("sale_price")), num(d.get("ship_charged")), num(d.get("ship_cost")), num(d.get("other_fees")), qty,
            str(d.get("notes") or "").strip())
    cols = "sku,sale_date,sold_by,platform,buyer,sale_price,ship_charged,ship_cost,other_fees,qty,notes"
    if old:
        c.execute(f"UPDATE tsales SET {','.join(k + '=?' for k in cols.split(','))} WHERE id=?", vals + (sale_id,))
        sid = sale_id
    else:
        sid = c.execute(f"INSERT INTO tsales({cols},created_at) VALUES({','.join('?' * 12)})", vals + (now(),)).lastrowid
    c.commit()
    alert = None
    thr = get_settings(c)["big_sale"]
    if not old and thr and vals[5] >= thr:
        alert = f"Big sale: ${vals[5]:,.2f} for {it['name']} ({sku}) on {vals[3] or 'unknown platform'}"
        try:
            import notify
            notify.notify(c, "big_sale", "Big sale", alert, dedupe=f"sale:{sid}")
        except Exception:
            pass
    return {"id": sid, "alert": alert}


def delete_sale(c, sale_id):
    c.execute("DELETE FROM tsales WHERE id=?", (sale_id,))
    c.commit()


# ---------------------------------------------------------------- receipts
def list_receipts(c):
    return [dict(r) for r in c.execute("SELECT * FROM receipts ORDER BY date DESC, id DESC")]


def save_receipt(c, d, rid=None):
    dt = str(d.get("date") or today()).strip()
    if not parse_date(dt):
        raise ValueError("Date must look like 2026-10-28")
    amt = num(d.get("amount"), None)
    if amt is None or amt == 0 and str(d.get("amount")).strip() == "":
        raise ValueError("Amount is required")
    sku = str(d.get("sku") or "").strip().upper()
    if sku and not c.execute("SELECT 1 FROM items WHERE sku=?", (sku,)).fetchone():
        raise ValueError(f"No inventory item with SKU '{sku}' (leave it blank if the receipt isn't for one item)")
    vals = (dt, str(d.get("vendor") or "").strip(), str(d.get("category") or "").strip(), str(d.get("description") or "").strip(), sku,
            amt, str(d.get("method") or "").strip(), str(d.get("ref") or "").strip(), str(d.get("notes") or "").strip())
    cols = "date,vendor,category,description,sku,amount,method,ref,notes"
    if rid:
        c.execute(f"UPDATE receipts SET {','.join(k + '=?' for k in cols.split(','))} WHERE id=?", vals + (rid,))
    else:
        c.execute(f"INSERT INTO receipts({cols}) VALUES({','.join('?' * 9)})", vals)
    c.commit()


def delete_receipt(c, rid):
    c.execute("DELETE FROM receipts WHERE id=?", (rid,))
    c.commit()


# ---------------------------------------------------------------- dashboard / tax
def _month_start(d, back):
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def dashboard(c):
    s = get_settings(c)
    sales = list_sales(c)
    items = list_items(c, sales)
    exp = sum(r["amount"] for r in c.execute("SELECT amount FROM receipts"))
    rev = sum(x["sale_price"] for x in sales)
    fees = sum(x["total_fees"] or 0 for x in sales)
    prof = sum(x["profit"] or 0 for x in sales)
    basis = sum(x["cost_basis"] or 0 for x in sales)
    sold_units = sum(x["qty_sold"] for x in items)
    net_profit = prof - exp
    t = date.today()
    out = {
        "in_stock": sum(x["qty_remaining"] for x in items), "sold": sold_units,
        "cost_on_hand": r2(sum(x["cost_on_hand"] or 0 for x in items)), "revenue": r2(rev), "fees": r2(fees),
        "expenses": r2(exp), "net_profit": r2(net_profit), "roi": round(net_profit / basis, 4) if basis else None,
        "market_value": r2(sum(x["qty_remaining"] * x["market_price"] for x in items)),
    }
    out["by_category"] = [{"category": cat, "units": sum(x["qty_sold"] for x in items if x["category"] == cat),
                           "revenue": r2(sum(x["sale_total"] or 0 for x in items if x["category"] == cat)),
                           "profit": r2(sum(x["profit"] or 0 for x in items if x["category"] == cat))} for cat in CATEGORIES]
    out["by_member"] = [{"name": n, "sales": sum(1 for x in sales if x["sold_by"] == n), "revenue": r2(sum(x["sale_price"] for x in sales if x["sold_by"] == n)),
                         "profit": r2(sum(x["profit"] or 0 for x in sales if x["sold_by"] == n))} for n in team(c)]
    aging = [x for x in items if x["qty_remaining"] > 0 and x["days_held"] is not None and x["days_held"] >= s["aging_days"]]
    out["aging_count"] = len(aging)
    out["aging_days"] = s["aging_days"]
    withp = sorted([x for x in sales if x["profit"] is not None], key=lambda x: x["profit"], reverse=True)[:5]
    out["top_sales"] = [{"item": x["item_name"], "sku": x["sku"], "profit": x["profit"], "roi": x["roi"]} for x in withp]
    months = []
    for back in range(5, -1, -1):
        ms = _month_start(t, back)
        key = ms.strftime("%Y-%m")
        ms_sales = [x for x in sales if (x["sale_date"] or "").startswith(key)]
        months.append({"month": key, "label": ms.strftime("%b %Y"), "revenue": r2(sum(x["sale_price"] for x in ms_sales)),
                       "profit": r2(sum(x["profit"] or 0 for x in ms_sales))})
    out["months"] = months
    cur_profit = months[-1]["profit"]
    avg = (net_profit / sold_units) if sold_units else 0
    out["target"] = {"target": s["monthly_target"], "so_far": cur_profit, "remaining": r2(max(0, s["monthly_target"] - cur_profit)),
                     "avg_profit": r2(avg),
                     "more_sales": (None if not s["monthly_target"] or avg <= 0 else int(-(-max(0, s["monthly_target"] - cur_profit) // avg)))}
    cut = (t - timedelta(days=30)).isoformat()
    reorder = []
    for cat in CATEGORIES:
        sold30 = sum(x["qty"] for x in sales if x["category"] == cat and (x["sale_date"] or "") >= cut)
        stock = sum(x["qty_remaining"] for x in items if x["category"] == cat)
        reorder.append({"category": cat, "sold_30d": sold30, "in_stock": stock,
                        "suggestion": "Restock soon" if sold30 >= 3 and stock <= 1 else "Selling well" if sold30 >= 3 else "OK"})
    out["reorder"] = reorder
    oldest = sorted([x for x in items if x["qty_remaining"] > 0 and x["days_held"] is not None], key=lambda x: x["days_held"], reverse=True)[:5]
    out["price_drops"] = [{"item": x["name"], "sku": x["sku"], "days": x["days_held"], "listed": x["listed_price"] or None,
                           "suggested": r2(x["listed_price"] * (1 - s["drop_pct"])) if x["listed_price"] else None} for x in oldest]
    rep = {}
    for x in sales:
        b = (x["buyer"] or "").strip().lower()
        if b:
            rep.setdefault(b, []).append(x)
    reps = [v for v in rep.values() if len(v) > 1]
    out["repeat_buyers"] = {"count": len(reps), "revenue": r2(sum(x["sale_price"] for v in reps for x in v))}
    try:
        pk = c.execute("SELECT status, COUNT(*) n FROM packages GROUP BY status").fetchall()
        cnt = {r["status"]: r["n"] for r in pk}
        out["packages"] = {"active": sum(n for s, n in cnt.items() if s != "delivered"), "out_for_delivery": cnt.get("out_for_delivery", 0),
                           "exception": cnt.get("exception", 0), "delivered": cnt.get("delivered", 0)}
    except Exception:
        out["packages"] = {"active": 0, "out_for_delivery": 0, "exception": 0, "delivered": 0}
    out["recent_sales"] = [{"sale_id": x["sale_id"], "item": x["item_name"], "date": x["sale_date"], "price": x["sale_price"], "profit": x["profit"]} for x in sales[:6]]
    return out


def sku_lookup(c, sku):
    sku = (sku or "").strip().upper()
    for it in list_items(c):
        if it["sku"] == sku:
            return it
    return None


def tax_summary(c, year):
    year = int(year)
    sales = [x for x in list_sales(c) if (x["sale_date"] or "").startswith(str(year))]
    rec = sum(r["amount"] for r in c.execute("SELECT amount,date FROM receipts") if (r["date"] or "").startswith(str(year)))
    gross = sum(x["sale_price"] for x in sales)
    cogs = sum(x["cost_basis"] or 0 for x in sales)
    fees = sum(x["total_fees"] or 0 for x in sales)
    ship = sum(x["ship_cost"] for x in sales)
    missing = sum(1 for x in sales if x["cost_basis"] is None)
    years = sorted({int(x["sale_date"][:4]) for x in list_sales(c) if x["sale_date"]} |
                   {int(r["date"][:4]) for r in c.execute("SELECT date FROM receipts") if r["date"]} | {date.today().year}, reverse=True)
    return {"year": year, "years": years, "gross": r2(gross), "cogs": r2(cogs), "fees": r2(fees), "shipping": r2(ship), "expenses": r2(rec),
            "net": r2(gross - cogs - fees - ship - rec), "missing_basis": missing}


# ---------------------------------------------------------------- scan station
def log_scan(c, action, sku, result):
    c.execute("INSERT INTO scans(ts,action,sku,result) VALUES(?,?,?,?)", (now(), action, sku, result))
    c.commit()


SKU_RE = re.compile(r"^[A-Z]{2,4}-\d{1,6}$")


def _find_scanned(c, code):
    """A scan can be our own SKU label or the product's printed barcode (UPC/EAN/QR text)."""
    it = c.execute("SELECT * FROM items WHERE sku=?", (code.upper(),)).fetchone()
    if not it:
        it = c.execute("SELECT * FROM items WHERE barcode=? AND barcode<>''", (code,)).fetchone()
    return it


@locked
def scan(c, mode, sku, sold_by=""):
    code = (sku or "").strip()
    if not code:
        raise ValueError("Nothing scanned")
    it = _find_scanned(c, code)
    if mode == "receive":
        if it:
            c.execute("UPDATE items SET qty=qty+1, updated_at=? WHERE id=?", (now(), it["id"]))
            c.commit()
            log_scan(c, "Receive", it["sku"], f"Added 1 more to {it['name']} (now {it['qty'] + 1})")
            return {"id": it["id"], "sku": it["sku"], "created": False, "message": f"{it['sku']} {it['name']}: quantity is now {it['qty'] + 1}"}
        if SKU_RE.match(code.upper()):
            new_sku, barcode, cat = code.upper(), "", ("Graphics Card" if code.upper().startswith("GC") else "Pokemon Card")
        else:  # a product barcode we haven't seen: make a row and remember the barcode for next time
            cat, barcode = "Pokemon Card", code
            new_sku = next_sku(c, cat)
        iid = c.execute("INSERT INTO items(sku,category,name,qty,purchase_date,barcode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (new_sku, cat, "(new item - fill in details)", 1, today(), barcode, now(), now())).lastrowid
        c.commit()
        log_scan(c, "Receive", new_sku, "Created new inventory row" + (f" for barcode {barcode}" if barcode else ""))
        return {"id": iid, "sku": new_sku, "created": True, "message": f"{new_sku}: new inventory row created. Fill in the details."}
    if mode == "sell":
        full = next((x for x in list_items(c) if it and x["sku"] == it["sku"]), None)
        if not full:
            log_scan(c, "Sell", code, "ERROR: not found in inventory")
            raise ValueError(f"{code} isn't in inventory")
        if full["qty_remaining"] <= 0:
            log_scan(c, "Sell", full["sku"], "ERROR: none left")
            raise ValueError(f"{full['sku']} {full['name']} is already sold out")
        r = save_sale(c, {"sku": full["sku"], "qty": 1, "sale_price": full["listed_price"] or 0, "sale_date": today(), "sold_by": sold_by,
                          "platform": full["listing_platform"]})
        log_scan(c, "Sell", full["sku"], "Sale row created" + ("" if full["listed_price"] else " (price still needed)"))
        return {"sale_id": r["id"], "sku": full["sku"], "created": True, "needs_price": not full["listed_price"], "alert": r.get("alert"),
                "message": f"{full['sku']} {full['name']}: sale logged" + ("" if full["listed_price"] else ". Enter the sale price.")}
    raise ValueError("Unknown scan mode")


def list_scans(c, limit=40):
    return [dict(r) for r in c.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- csv export / import
EXPORTS = {
    "inventory": (lambda c: list_items(c), ["sku", "category", "name", "pokemon_set", "card_no", "condition", "grading", "qty",
                                            "added_by", "purchase_date", "purchase_price", "purchase_source", "listing_platform",
                                            "listed_price", "currency", "barcode", "status", "qty_sold", "qty_remaining", "sale_date", "sale_total",
                                            "fees_total", "net_total", "profit", "roi", "days_held", "market_price", "location", "notes"]),
    "sales": (lambda c: list_sales(c), ["sale_id", "sku", "item_name", "sale_date", "sold_by", "platform", "buyer", "sale_price", "ship_charged",
                                        "ship_cost", "fee_pct", "platform_fee", "other_fees", "total_fees", "net", "cost_basis", "profit",
                                        "roi", "qty", "notes"]),
    "receipts": (lambda c: list_receipts(c), ["date", "vendor", "category", "description", "sku", "amount", "method", "ref", "notes"]),
}


def export_csv(c, name):
    if name not in EXPORTS:
        raise ValueError("Unknown export")
    fn, cols = EXPORTS[name]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in fn(c):
        w.writerow(["" if r.get(k) is None else r.get(k) for k in cols])
    return buf.getvalue()


def import_csv(c, name, text):
    """Add rows from a CSV whose header uses the same column names as the export. Returns (added, errors)."""
    rdr = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not rdr.fieldnames:
        raise ValueError("The file is empty")
    added, errors = 0, []
    for i, row in enumerate(rdr, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not any(row.values()):
            continue
        try:
            if name == "inventory":
                existing = c.execute("SELECT id FROM items WHERE sku=?", (row.get("sku", "").upper(),)).fetchone() if row.get("sku") else None
                save_item(c, row, existing["id"] if existing else None)
            elif name == "sales":
                save_sale(c, row)
            elif name == "receipts":
                save_receipt(c, row)
            else:
                raise ValueError("Unknown import")
            added += 1
        except ValueError as e:
            errors.append(f"Row {i}: {e}")
    return added, errors


# ---------------------------------------------------------------- legacy data (older versions of this app)
@locked
def migrate_legacy(c):
    if c.execute("SELECT 1 FROM settings WHERE key='legacy_migrated'").fetchone():
        return
    try:
        old_items = c.execute("SELECT * FROM inventory").fetchall()
        old_sales = c.execute("SELECT * FROM sales").fetchall()
    except Exception:
        old_items, old_sales = [], []
    sold_by_id = {}
    for s in old_sales:
        sold_by_id[s["inventory_id"]] = sold_by_id.get(s["inventory_id"], 0) + (s["qty"] or 0)
    sku_of = {}
    for r in old_items:
        cat = infer_category(r["name"])
        sku = (r["sku"] or "").upper()
        if not sku or c.execute("SELECT 1 FROM items WHERE sku=?", (sku,)).fetchone():
            sku = next_sku(c, cat)
        qty = max(1, (r["qty"] or 0) + sold_by_id.get(r["id"], 0))
        c.execute("INSERT INTO items(sku,category,name,qty,purchase_price,market_price,location,notes,purchase_date,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (sku, cat, r["name"], qty, r["avg_cost"] or 0, r["market_price"] or 0, r["location"] or "",
                                                  r["notes"] or "", (r["created_at"] or today())[:10], now(), now()))
        sku_of[r["id"]] = sku
    for s in old_sales:
        sku = sku_of.get(s["inventory_id"])
        if sku:
            c.execute("INSERT INTO tsales(sku,sale_date,platform,sale_price,ship_cost,other_fees,qty,created_at,notes) VALUES(?,?,?,?,?,?,?,?,?)",
                      (sku, (s["sold_at"] or today())[:10], "", (s["sale_price"] or 0) * (s["qty"] or 1), s["shipping"] or 0,
                       s["fees"] or 0, s["qty"] or 1, now(), ("Imported from the older version. Platform: " + s["platform"]) if s["platform"] else "Imported from the older version"))
    c.execute("INSERT OR REPLACE INTO settings VALUES('legacy_migrated','1')")
    c.commit()
