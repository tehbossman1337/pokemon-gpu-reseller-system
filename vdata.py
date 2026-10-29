"""Valor-native accounts and profiles.

Formats (taken from real Valor exports):
  Profiles file : {"<uuid>": {name,email,phoneNumber,billingSameAsShipping,oneCheckout,quickTask,
                   card{holder,number,expiration,cvv,type}, shipping{...}, billing{...}, id,totalSpent,selected,
                   [personalCustomsCode,pinCode,idNumber,birthday]}, ...}
  Accounts file : {"version":1,"site":"Pokemon Center US","accounts":{"<id>":{id,email,password,proxy,session,selected}},
                   "sessions":{}}
Whole records are stored as JSON so unknown fields survive import -> export unchanged.
"""
import copy
import json
import re
import uuid

import jig

ADDR_FIELDS = ["firstName", "lastName", "addressLine1", "addressLine2", "city", "countryName", "countryCode", "state", "zipCode"]
CARD_FIELDS = ["holder", "number", "expiration", "cvv", "type"]
EXTRA_FIELDS = ["personalCustomsCode", "pinCode", "idNumber", "birthday"]
COUNTRIES = {"US": "United States", "CA": "Canada", "GB": "United Kingdom", "AU": "Australia", "DE": "Germany",
             "FR": "France", "JP": "Japan", "MX": "Mexico"}


def blank_addr():
    return {"firstName": "", "lastName": "", "addressLine1": "", "addressLine2": "", "city": "",
            "countryName": "United States", "countryCode": "US", "state": "", "zipCode": ""}


def blank_profile():
    return {"name": "", "email": "", "phoneNumber": "", "billingSameAsShipping": True, "oneCheckout": False,
            "quickTask": False, "card": {"holder": "", "number": "", "expiration": "", "cvv": "", "type": ""},
            "shipping": blank_addr(), "billing": blank_addr(), "id": "", "totalSpent": 0, "selected": False}


def card_type(number):
    n = re.sub(r"\D", "", str(number or ""))
    if re.match(r"^4", n):
        return "visa"
    if re.match(r"^(5[1-5]|2(2[2-9]|[3-6]\d|7[01]|720))", n):
        return "mastercard"
    if re.match(r"^3[47]", n):
        return "amex"
    if re.match(r"^(6011|65|64[4-9])", n):
        return "discover"
    return ""


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def normalize_profile(rec, keep_id=True):
    r = _merge(blank_profile(), rec if isinstance(rec, dict) else {})
    if not (keep_id and r.get("id")):
        r["id"] = str(uuid.uuid4())
    if r["card"].get("number") and not r["card"].get("type"):
        r["card"]["type"] = card_type(r["card"]["number"])
    for k in ("billingSameAsShipping", "oneCheckout", "quickTask", "selected"):
        r[k] = bool(r[k])
    try:
        r["totalSpent"] = float(r["totalSpent"]) if "." in str(r["totalSpent"]) else int(r["totalSpent"] or 0)
    except (TypeError, ValueError):
        r["totalSpent"] = 0
    return r


def set_path(rec, dotted, value):
    parts = dotted.split(".")
    o = rec
    for p in parts[:-1]:
        o = o.setdefault(p, {})
    o[parts[-1]] = value


# ---------------------------------------------------------------- profiles
def save_profile(c, site, rec, group_id=None, pid=None, raw=False):
    rec = rec if raw else normalize_profile(rec)
    if pid:
        rec["id"] = pid
    c.execute("INSERT INTO vprofiles(id,site,group_id,name,email,data,created_at) VALUES(?,?,?,?,?,?,datetime('now')) "
              "ON CONFLICT(id) DO UPDATE SET site=excluded.site,name=excluded.name,email=excluded.email,data=excluded.data",
              (rec["id"], site, group_id, rec["name"], rec["email"], json.dumps(rec)))
    return rec["id"]


def list_profiles(c):
    return [{"id": r["id"], "site": r["site"], "group_id": r["group_id"], "rec": json.loads(r["data"])}
            for r in c.execute("SELECT * FROM vprofiles ORDER BY site, COALESCE(group_id,id), rowid")]


def import_profiles(c, site, text, replace=False):
    root = json.loads(text)
    if isinstance(root, dict) and all(isinstance(v, dict) for v in root.values()) and root:
        recs = list(root.values())
    elif isinstance(root, list) and all(isinstance(v, dict) for v in root):
        recs = root
    elif isinstance(root, dict) and "shipping" in root:
        recs = [root]
    else:
        raise ValueError("This doesn't look like a Valor profiles file")
    if not any("shipping" in r or "card" in r for r in recs):
        raise ValueError("This doesn't look like a Valor profiles file (no shipping/card fields)")
    if replace:
        c.execute("DELETE FROM vprofiles WHERE site=?", (site,))
    added = updated = 0
    for r in recs:
        norm = _merge_keep_order(r)
        exists = c.execute("SELECT 1 FROM vprofiles WHERE id=?", (norm.get("id"),)).fetchone() if norm.get("id") else None
        if not norm.get("id"):
            norm["id"] = str(uuid.uuid4())
        save_profile(c, site, norm, raw=True)
        updated += bool(exists)
        added += not exists
    return {"added": added, "updated": updated, "total": len(recs)}


def _merge_keep_order(r):
    """Keep every original key (including unknown ones) while filling any missing standard ones."""
    base = blank_profile()
    out = copy.deepcopy(r)
    for k, v in base.items():
        if k not in out:
            out[k] = v
        elif isinstance(v, dict) and isinstance(out[k], dict):
            for kk, vv in v.items():
                out[k].setdefault(kk, vv)
    return out


def export_profiles(c, site=None, selected_only=False, include_originals=True, include_jigs=True, ids=None):
    where, args = [], []
    if site:
        where.append("site=?")
        args.append(site)
    if not include_originals:
        where.append("group_id IS NOT NULL")
    if not include_jigs:
        where.append("group_id IS NULL")
    q = "SELECT data FROM vprofiles" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY site, COALESCE(group_id,id), rowid"
    out = {}
    for r in c.execute(q, args):
        rec = json.loads(r["data"])
        if selected_only and not rec.get("selected"):
            continue
        if ids and rec["id"] not in ids:
            continue
        out[rec["id"]] = rec
    return out


def jig_profile(c, pid, count=10, use_units=True, seed=None, save=False, replace=True):
    row = c.execute("SELECT * FROM vprofiles WHERE id=?", (pid,)).fetchone()
    if not row or row["group_id"]:
        raise ValueError("Pick an original (non-rejigged) profile")
    src = json.loads(row["data"])
    sh = src["shipping"]
    if not sh.get("addressLine1"):
        raise ValueError("That profile has no street address to vary")
    base = {"address1": sh["addressLine1"], "address2": sh.get("addressLine2", ""), "city": sh["city"], "state": sh["state"], "zip": sh["zipCode"]}
    variants = jig.generate(base, count=count, seed=seed or None, use_units=use_units, keep_original=False)
    if save:
        if replace:
            c.execute("DELETE FROM vprofiles WHERE group_id=?", (pid,))
        for i, v in enumerate(variants, 1):
            cp = copy.deepcopy(src)
            cp["id"] = str(uuid.uuid4())
            cp["name"] = f"{src['name']}-J{i}"
            cp["selected"] = False
            cp["shipping"]["addressLine1"], cp["shipping"]["addressLine2"] = v["address1"], v["address2"]
            if cp.get("billingSameAsShipping"):
                cp["billing"]["addressLine1"], cp["billing"]["addressLine2"] = v["address1"], v["address2"]
            save_profile(c, row["site"], cp, group_id=pid)
    return [{"addressLine1": v["address1"], "addressLine2": v["address2"]} for v in variants]


def bulk_address(c, site, ids, addr, unique=False, seed=None, use_units=True, target="both", units=None):
    """Apply one address to many profiles; unique=True gives each profile its own spelling of it."""
    q = "SELECT * FROM vprofiles WHERE site=?"
    rows = c.execute(q, (site,)).fetchall()
    if ids:
        rows = [r for r in rows if r["id"] in set(ids)]
    if not rows:
        raise ValueError("No profiles to update")
    code = (addr.get("countryCode") or "US").upper()
    name = addr.get("countryName") or COUNTRIES.get(code, code)
    variants = [{"address1": addr["addressLine1"], "address2": addr.get("addressLine2", "")}] * len(rows)
    note = ""
    if unique:
        base = {"address1": addr["addressLine1"], "address2": addr.get("addressLine2", ""), "city": addr["city"],
                "state": addr["state"], "zip": addr["zipCode"]}
        vs = jig.generate(base, count=len(rows), seed=seed or None, use_units=use_units, keep_original=True)
        if len(vs) < len(rows):
            note = f"Only {len(vs)} distinct spellings exist for this address, so some profiles share one."
        variants = [{"address1": vs[i % len(vs)]["address1"], "address2": vs[i % len(vs)]["address2"]} for i in range(len(rows))]
    keys = ("addressLine1", "addressLine2", "city", "state", "zipCode", "countryCode", "countryName")
    if units:  # real unit numbers supplied by the user, handed out in rotation
        variants = [{"address1": addr["addressLine1"], "address2": units[i % len(units)]} for i in range(len(rows))]
        note = ""
    for r, v in zip(rows, variants):
        rec = json.loads(r["data"])
        vals = {"addressLine1": v["address1"], "addressLine2": v["address2"], "city": addr["city"], "state": addr["state"],
                "zipCode": addr["zipCode"], "countryCode": code, "countryName": name}
        if target in ("both", "shipping"):
            rec["shipping"].update(vals)
        if target == "billing":
            rec["billing"].update(vals)
            rec["billingSameAsShipping"] = False  # billing now differs on purpose
        elif target == "both" and rec.get("billingSameAsShipping"):
            for k in keys:
                rec["billing"][k] = rec["shipping"][k]
        c.execute("UPDATE vprofiles SET data=? WHERE id=?", (json.dumps(rec), r["id"]))
    return {"updated": len(rows), "note": note}


def patch_profiles(c, ids, changes):
    n = 0
    for pid in ids:
        row = c.execute("SELECT data FROM vprofiles WHERE id=?", (pid,)).fetchone()
        if not row:
            continue
        rec = json.loads(row["data"])
        for k, v in changes.items():
            set_path(rec, k, v)
        rec = normalize_profile(rec)
        c.execute("UPDATE vprofiles SET data=?, name=?, email=? WHERE id=?", (json.dumps(rec), rec["name"], rec["email"], pid))
        n += 1
    return n


# ---------------------------------------------------------------- accounts
def blank_account(email=""):
    return {"id": email, "email": email, "password": "", "proxy": "", "session": False, "selected": False}


def save_account(c, site, rec, old_id=None):
    rec = {**blank_account(), **rec}
    rec["email"] = (rec["email"] or "").strip()
    if not rec["email"]:
        raise ValueError("Email is required")
    rec["id"] = rec.get("id") or rec["email"]
    if old_id and old_id != rec["id"]:
        c.execute("DELETE FROM vaccounts WHERE site=? AND id=?", (site, old_id))
    c.execute("INSERT INTO vaccounts(site,id,email,data) VALUES(?,?,?,?) ON CONFLICT(site,id) DO UPDATE SET "
              "email=excluded.email,data=excluded.data", (site, rec["id"], rec["email"], json.dumps(rec)))
    return rec["id"]


def list_accounts(c):
    return [{"site": r["site"], "id": r["id"], "rec": json.loads(r["data"])}
            for r in c.execute("SELECT * FROM vaccounts ORDER BY site, email")]


def import_accounts(c, text, site_override=None, replace=False):
    root = json.loads(text)
    if not isinstance(root, dict) or "accounts" not in root:
        raise ValueError("This doesn't look like a Valor accounts file (no \"accounts\" section)")
    site = (site_override or root.get("site") or "").strip()
    if not site:
        raise ValueError("The file has no site name - enter one")
    if replace:
        c.execute("DELETE FROM vaccounts WHERE site=?", (site,))
    accts = root["accounts"]
    accts = accts.values() if isinstance(accts, dict) else accts
    added = updated = 0
    for a in accts:
        a = {**blank_account(a.get("email", "")), **a}
        exists = c.execute("SELECT 1 FROM vaccounts WHERE site=? AND id=?", (site, a.get("id") or a["email"])).fetchone()
        save_account(c, site, a)
        updated += bool(exists)
        added += not exists
    if root.get("sessions"):
        c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (f"sessions:{site}", json.dumps(root["sessions"])))
    return {"site": site, "added": added, "updated": updated}


def export_accounts(c, site, selected_only=False):
    out = {}
    for r in c.execute("SELECT data FROM vaccounts WHERE site=? ORDER BY email", (site,)):
        rec = json.loads(r["data"])
        if selected_only and not rec.get("selected"):
            continue
        out[rec["id"]] = rec
    s = c.execute("SELECT value FROM settings WHERE key=?", (f"sessions:{site}",)).fetchone()
    return {"version": 1, "site": site, "accounts": out, "sessions": json.loads(s[0]) if s else {}}


def patch_accounts(c, site, ids, changes):
    n = 0
    for aid in ids:
        row = c.execute("SELECT data FROM vaccounts WHERE site=? AND id=?", (site, aid)).fetchone()
        if not row:
            continue
        rec = {**json.loads(row["data"]), **changes}
        c.execute("UPDATE vaccounts SET data=? WHERE site=? AND id=?", (json.dumps(rec), site, aid))
        n += 1
    return n


# ---------------------------------------------------------------- groups (a group = a website; may be empty)
def _gkey(kind):
    if kind not in ("acc", "prof"):
        raise ValueError("Unknown group type")
    return "vgroups:" + kind


def list_groups(c):
    out = {}
    for kind in ("acc", "prof"):
        r = c.execute("SELECT value FROM settings WHERE key=?", (_gkey(kind),)).fetchone()
        try:
            out[kind] = [str(x) for x in json.loads(r[0])] if r else []
        except ValueError:
            out[kind] = []
    return out


def _set_groups(c, kind, names):
    c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (_gkey(kind), json.dumps(sorted(set(names), key=str.lower))))


def add_group(c, kind, name):
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the group a name")
    if len(name) > 60:
        raise ValueError("Group names can be up to 60 characters")
    table = "vaccounts" if kind == "acc" else "vprofiles"
    existing = {r[0].lower() for r in c.execute(f"SELECT DISTINCT site FROM {table}")} | {n.lower() for n in list_groups(c)[kind]}
    if name.lower() in existing:
        raise ValueError(f"A group named {name} already exists")
    _set_groups(c, kind, list_groups(c)[kind] + [name])
    return name


def remove_group_name(c, kind, name):
    _set_groups(c, kind, [n for n in list_groups(c)[kind] if n != name])


def rename_group(c, kind, old, new):
    new = (new or "").strip()
    if not new:
        raise ValueError("Give the group a name")
    if new == old:
        return new
    table = "vaccounts" if kind == "acc" else "vprofiles"
    taken = {r[0].lower() for r in c.execute(f"SELECT DISTINCT site FROM {table}")} | {n.lower() for n in list_groups(c)[kind]}
    if new.lower() in taken:
        raise ValueError(f"A group named {new} already exists")
    c.execute(f"UPDATE {table} SET site=? WHERE site=?", (new, old))
    if kind == "acc":
        c.execute("UPDATE settings SET key=? WHERE key=?", (f"sessions:{new}", f"sessions:{old}"))
    _set_groups(c, kind, [new if n == old else n for n in list_groups(c)[kind]])
    return new
