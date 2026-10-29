"""Core data layer: SQLite schema, inventory logic, order state machine."""
import os
import re
import sqlite3
from datetime import datetime, timedelta

import sys

# RES_DIR: bundled read-only files (the web UI). DATA_DIR: where the user's data lives.
# When packaged as an exe, data sits next to the exe so it survives updates.
if getattr(sys, "frozen", False):
    RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    if os.name == "nt":
        DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ValorInventoryAgent")
    else:
        DATA_DIR = os.path.join(os.path.dirname(sys.executable), "ValorData")
else:
    RES_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.environ.get("VALOR_DATA", os.path.join(RES_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
BASE_DIR = DATA_DIR  # backwards compat
DB_PATH = os.environ.get("VALOR_DB", os.path.join(DATA_DIR, "inventory.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory(
  id INTEGER PRIMARY KEY,
  sku TEXT DEFAULT '',
  name TEXT NOT NULL,
  category TEXT DEFAULT '',
  qty INTEGER DEFAULT 0,
  avg_cost REAL DEFAULT 0,
  market_price REAL DEFAULT 0,
  location TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY,
  retailer TEXT NOT NULL,
  order_number TEXT NOT NULL,
  status TEXT NOT NULL,
  order_date TEXT,
  total REAL,
  tracking TEXT DEFAULT '',
  received INTEGER DEFAULT 0,
  source TEXT DEFAULT 'email',
  updated_at TEXT,
  UNIQUE(retailer, order_number)
);
CREATE TABLE IF NOT EXISTS order_items(
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  sku TEXT DEFAULT '',
  name TEXT NOT NULL,
  qty INTEGER DEFAULT 1,
  unit_price REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sales(
  id INTEGER PRIMARY KEY,
  inventory_id INTEGER,
  item_name TEXT,
  qty INTEGER,
  sale_price REAL,
  fees REAL DEFAULT 0,
  shipping REAL DEFAULT 0,
  cost_basis REAL DEFAULT 0,
  platform TEXT DEFAULT '',
  sold_at TEXT
);
CREATE TABLE IF NOT EXISTS processed_emails(
  message_id TEXT PRIMARY KEY,
  subject TEXT,
  retailer TEXT,
  outcome TEXT,
  processed_at TEXT
);
CREATE TABLE IF NOT EXISTS vaccounts(
  site TEXT NOT NULL, id TEXT NOT NULL, email TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY(site, id)
);
CREATE TABLE IF NOT EXISTS vprofiles(
  id TEXT PRIMARY KEY, site TEXT NOT NULL, group_id TEXT, name TEXT, email TEXT, data TEXT NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS limits(
  id INTEGER PRIMARY KEY, retailer TEXT NOT NULL, product TEXT NOT NULL, max_qty INTEGER NOT NULL, window_days INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS review(
  id INTEGER PRIMARY KEY,
  message_id TEXT,
  retailer TEXT,
  subject TEXT,
  reason TEXT,
  snippet TEXT,
  created_at TEXT,
  resolved INTEGER DEFAULT 0
);
"""

RANK = {"confirmed": 1, "shipped": 2, "delivered": 3, "cancelled": 9}
STATUSES = list(RANK)


def now():
    return datetime.now().isoformat(timespec="seconds")


def connect(path=None):
    c = sqlite3.connect(path or DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    return c


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"pok[eé]mon|trading card game|\btcg\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


# ---------------------------------------------------------------- inventory
def find_inventory(c, sku, name):
    if sku:
        r = c.execute("SELECT * FROM inventory WHERE sku=?", (sku,)).fetchone()
        if r:
            return r
    n = norm(name)
    for r in c.execute("SELECT * FROM inventory"):
        if norm(r["name"]) == n:
            return r
    return None


def add_to_inventory(c, sku, name, qty, unit_cost):
    r = find_inventory(c, sku, name)
    t = now()
    if r:
        old_q, old_c = r["qty"], r["avg_cost"] or 0
        new_q = old_q + qty
        if old_q <= 0 or not old_c:
            avg = unit_cost or old_c
        else:
            avg = (old_q * old_c + qty * unit_cost) / new_q if new_q else old_c
        c.execute(
            "UPDATE inventory SET qty=?, avg_cost=?, sku=COALESCE(NULLIF(sku,''),?), updated_at=? WHERE id=?",
            (new_q, round(avg, 4), sku or "", t, r["id"]),
        )
        return r["id"]
    cur = c.execute(
        "INSERT INTO inventory(sku,name,qty,avg_cost,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (sku or "", name, qty, unit_cost or 0, t, t),
    )
    return cur.lastrowid


def receive_order(c, order_id):
    """Move an order's items into on-hand inventory (once)."""
    o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o or o["received"] or o["status"] == "cancelled":
        return False
    for it in c.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall():
        add_to_inventory(c, it["sku"], it["name"], it["qty"], it["unit_price"])
    c.execute("UPDATE orders SET received=1, updated_at=? WHERE id=?", (now(), order_id))
    return True


def record_sale(c, inventory_id, qty, sale_price, fees=0, shipping=0, platform="", sold_at=None):
    r = c.execute("SELECT * FROM inventory WHERE id=?", (inventory_id,)).fetchone()
    if not r:
        raise ValueError("Inventory item not found")
    if qty < 1 or qty > r["qty"]:
        raise ValueError(f"Only {r['qty']} on hand")
    c.execute(
        "INSERT INTO sales(inventory_id,item_name,qty,sale_price,fees,shipping,cost_basis,platform,sold_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (inventory_id, r["name"], qty, sale_price, fees, shipping, r["avg_cost"] or 0, platform, sold_at or now()),
    )
    c.execute("UPDATE inventory SET qty=qty-?, updated_at=? WHERE id=?", (qty, now(), inventory_id))


# ------------------------------------------------------------------- orders
def apply_event(c, ev, auto_receive=True):
    """Apply one parsed email event to the orders table. Returns an outcome string."""
    row = c.execute(
        "SELECT * FROM orders WHERE retailer=? AND order_number=?",
        (ev["retailer"], ev["order_number"]),
    ).fetchone()
    t = now()
    outcome = ""
    if row is None:
        cur = c.execute(
            "INSERT INTO orders(retailer,order_number,status,order_date,total,tracking,source,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (ev["retailer"], ev["order_number"], ev["status"], ev.get("date"), ev.get("total"),
             ev.get("tracking") or "", "email", t),
        )
        oid = cur.lastrowid
        outcome = f"new order ({ev['status']})"
    else:
        oid = row["id"]
        new, old = ev["status"], row["status"]
        if new == "cancelled" and row["received"]:
            outcome = "cancel email ignored (already received)"
        elif RANK[new] > RANK[old]:
            c.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (new, t, oid))
            outcome = f"{old} -> {new}"
        else:
            outcome = f"no change ({old})"
        if ev.get("total") and not row["total"]:
            c.execute("UPDATE orders SET total=? WHERE id=?", (ev["total"], oid))
        if ev.get("tracking") and not row["tracking"]:
            c.execute("UPDATE orders SET tracking=? WHERE id=?", (ev["tracking"], oid))
        if ev.get("date") and (not row["order_date"] or ev["status"] == "confirmed"):
            c.execute("UPDATE orders SET order_date=? WHERE id=?", (ev["date"], oid))

    has_items = c.execute("SELECT COUNT(*) FROM order_items WHERE order_id=?", (oid,)).fetchone()[0]
    if ev.get("items") and not has_items:
        for it in ev["items"]:
            c.execute(
                "INSERT INTO order_items(order_id,sku,name,qty,unit_price) VALUES(?,?,?,?,?)",
                (oid, it.get("sku", ""), it["name"], it.get("qty", 1), it.get("price", 0)),
            )
        has_items = len(ev["items"])

    cur_status = c.execute("SELECT status, received FROM orders WHERE id=?", (oid,)).fetchone()
    if auto_receive and cur_status["status"] == "delivered" and not cur_status["received"] and has_items:
        if receive_order(c, oid):
            outcome += "; added to inventory"
    return outcome, oid, bool(has_items)


# ------------------------------------------------------------------ summary
def summary(c):
    inv = c.execute(
        "SELECT COALESCE(SUM(qty),0) u, COALESCE(SUM(qty*avg_cost),0) cost, COALESCE(SUM(qty*market_price),0) mkt FROM inventory"
    ).fetchone()
    incoming = c.execute(
        "SELECT COALESCE(SUM(i.qty),0) FROM order_items i JOIN orders o ON o.id=i.order_id "
        "WHERE o.status IN ('confirmed','shipped') AND o.received=0"
    ).fetchone()[0]
    open_orders = c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('confirmed','shipped')").fetchone()[0]
    sales = c.execute("SELECT * FROM sales").fetchall()

    def profit(s):
        return s["sale_price"] * s["qty"] - (s["fees"] or 0) - (s["shipping"] or 0) - s["cost_basis"] * s["qty"]

    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    return {
        "units_on_hand": inv["u"],
        "inventory_cost": round(inv["cost"], 2),
        "inventory_market": round(inv["mkt"], 2),
        "incoming_units": incoming,
        "open_orders": open_orders,
        "revenue": round(sum(s["sale_price"] * s["qty"] for s in sales), 2),
        "profit": round(sum(profit(s) for s in sales), 2),
        "profit_30d": round(sum(profit(s) for s in sales if (s["sold_at"] or "") >= cutoff), 2),
        "review_count": c.execute("SELECT COUNT(*) FROM review WHERE resolved=0").fetchone()[0],
    }


def limits_report(c):
    """For each saved limit, count units bought (non-cancelled orders) and what's left."""
    out = []
    for lim in c.execute("SELECT * FROM limits ORDER BY retailer, product").fetchall():
        since = (datetime.now() - timedelta(days=lim["window_days"])).date().isoformat() if lim["window_days"] else ""
        key = norm(lim["product"])
        bought = 0
        q = ("SELECT i.name, i.qty, o.order_date FROM order_items i JOIN orders o ON o.id=i.order_id "
             "WHERE lower(o.retailer)=lower(?) AND o.status!='cancelled'")
        for r in c.execute(q, (lim["retailer"],)):
            if since and (r["order_date"] or "") < since:
                continue
            if key and key in norm(r["name"]):
                bought += r["qty"]
        out.append({**dict(lim), "bought": bought, "remaining": max(lim["max_qty"] - bought, 0), "over": bought > lim["max_qty"]})
    return out
