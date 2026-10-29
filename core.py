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
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, sku TEXT NOT NULL UNIQUE, category TEXT DEFAULT 'Pokemon Card', name TEXT NOT NULL,
  pokemon_set TEXT DEFAULT '', card_no TEXT DEFAULT '', condition TEXT DEFAULT '', grading TEXT DEFAULT '',
  qty INTEGER DEFAULT 1, added_by TEXT DEFAULT '', purchase_date TEXT DEFAULT '', purchase_price REAL DEFAULT 0,
  purchase_source TEXT DEFAULT '', listing_platform TEXT DEFAULT '', listed_price REAL DEFAULT 0, currency TEXT DEFAULT 'USD',
  market_price REAL DEFAULT 0, location TEXT DEFAULT '', notes TEXT DEFAULT '', created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tsales(
  id INTEGER PRIMARY KEY, sku TEXT NOT NULL, sale_date TEXT, sold_by TEXT DEFAULT '', platform TEXT DEFAULT '', buyer TEXT DEFAULT '',
  sale_price REAL DEFAULT 0, ship_charged REAL DEFAULT 0, ship_cost REAL DEFAULT 0, other_fees REAL DEFAULT 0,
  qty INTEGER DEFAULT 1, notes TEXT DEFAULT '', created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tsales_sku ON tsales(sku);
CREATE TABLE IF NOT EXISTS receipts(
  id INTEGER PRIMARY KEY, date TEXT, vendor TEXT DEFAULT '', category TEXT DEFAULT '', description TEXT DEFAULT '', sku TEXT DEFAULT '',
  amount REAL DEFAULT 0, method TEXT DEFAULT '', ref TEXT DEFAULT '', notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fees(platform TEXT PRIMARY KEY, fee_pct REAL DEFAULT 0, fixed REAL DEFAULT 0, processing TEXT DEFAULT '', notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS currencies(code TEXT PRIMARY KEY, name TEXT, rate REAL DEFAULT 1);
CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY, ts TEXT, action TEXT, sku TEXT, result TEXT);
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
    c = sqlite3.connect(path or DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    import tracker  # local import: tracker needs this module's helpers
    tracker.ensure_seed(c)
    tracker.migrate_legacy(c)
    return c


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"pok[eé]mon|trading card game|\btcg\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


# ---------------------------------------------------------------- inventory (SKU lots, see tracker.py)
def find_item(c, sku, name):
    if sku:
        r = c.execute("SELECT * FROM items WHERE sku=?", (sku.upper(),)).fetchone()
        if r:
            return r
    n = norm(name)
    for r in c.execute("SELECT * FROM items"):
        if norm(r["name"]) == n:
            return r
    return None


def add_to_inventory(c, sku, name, qty, unit_cost, source="", when=""):
    """Add received units. Same SKU/name -> bigger lot with a weighted-average purchase price."""
    import tracker
    qty = max(1, int(qty or 1))
    unit_cost = float(unit_cost or 0)
    r = find_item(c, sku, name)
    if r:
        oq, op = r["qty"], r["purchase_price"] or 0
        avg = unit_cost if (not op) else (oq * op + qty * unit_cost) / (oq + qty) if unit_cost else op
        c.execute("UPDATE items SET qty=?, purchase_price=?, updated_at=? WHERE id=?", (oq + qty, round(avg, 4), now(), r["id"]))
        return r["id"]
    cat = tracker.infer_category(name)
    sku = (sku or "").upper()
    if not sku or c.execute("SELECT 1 FROM items WHERE sku=?", (sku,)).fetchone():
        sku = tracker.next_sku(c, cat)
    for attempt in range(6):
        try:
            return c.execute(
                "INSERT INTO items(sku,category,name,qty,purchase_date,purchase_price,purchase_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (sku, cat, name, qty, (when or now())[:10], unit_cost, source, now(), now())).lastrowid
        except sqlite3.IntegrityError:  # another thread took this SKU a moment ago
            sku = tracker.next_sku(c, cat)
    raise ValueError("Could not pick a free SKU")


def receive_order(c, order_id):
    """Move an order's items into inventory (once)."""
    o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o or o["received"] or o["status"] == "cancelled":
        return False
    for it in c.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall():
        add_to_inventory(c, it["sku"], it["name"], it["qty"], it["unit_price"], o["retailer"], o["order_date"] or "")
    c.execute("UPDATE orders SET received=1, updated_at=? WHERE id=?", (now(), order_id))
    return True


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
    import tracker
    d = tracker.dashboard(c)
    incoming = c.execute(
        "SELECT COALESCE(SUM(i.qty),0) FROM order_items i JOIN orders o ON o.id=i.order_id "
        "WHERE o.status IN ('confirmed','shipped') AND o.received=0").fetchone()[0]
    open_orders = c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('confirmed','shipped')").fetchone()[0]
    cut = (datetime.now() - timedelta(days=30)).date().isoformat()
    p30 = sum(s["profit"] or 0 for s in tracker.list_sales(c) if (s["sale_date"] or "") >= cut)
    return {"units_on_hand": d["in_stock"], "inventory_cost": d["cost_on_hand"], "inventory_market": d["market_value"],
            "incoming_units": incoming, "open_orders": open_orders, "revenue": d["revenue"], "profit": d["net_profit"],
            "profit_30d": round(p30, 2), "review_count": c.execute("SELECT COUNT(*) FROM review WHERE resolved=0").fetchone()[0]}


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
