"""iCloud IMAP sync: pull retailer emails, parse, update orders.

Setup: create an app-specific password at https://account.apple.com (Sign-In and Security),
then set ICLOUD_EMAIL and ICLOUD_APP_PASSWORD (or save them from the dashboard Settings).
Read-only: messages are fetched with BODY.PEEK and never modified or deleted.
"""
import email
import imaplib
import json
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header

import core
import parsers

HOST = "imap.mail.me.com"
CONF_PATH = os.path.join(core.BASE_DIR, "config.json")


def load_conf():
    conf = {}
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH) as f:
            conf = json.load(f)
    conf["email"] = os.environ.get("ICLOUD_EMAIL", conf.get("email", ""))
    conf["password"] = os.environ.get("ICLOUD_APP_PASSWORD", conf.get("password", ""))
    if os.path.exists(CONF_PATH) and conf.get("upgraded", 0) < 3 and conf.get("email"):
        conf["folder"], conf["days"], conf["everything"] = "*", 0, True  # one-time: scan every folder, all time
        try:
            with open(CONF_PATH, "w") as f:
                json.dump({**conf, "upgraded": 3}, f)
        except Exception:
            pass
    conf.setdefault("folder", "*")      # * = every folder
    conf.setdefault("days", 0)          # 0 = all time
    conf.setdefault("everything", True)  # read every email instead of only ones that look like orders
    return conf


def save_conf(email_addr, password, folder="*", days=0, everything=None):
    old = load_conf() if os.path.exists(CONF_PATH) else {}
    ev = old.get("everything", True) if everything is None else bool(everything)
    with open(CONF_PATH, "w") as f:
        json.dump({"email": email_addr, "password": password, "folder": folder, "days": days, "everything": ev,
                   "upgraded": 3}, f)
    os.chmod(CONF_PATH, 0o600)


PARSER_VERSION = 4  # bump when the email reader improves: everything is re-read once, safely
SKIP_FOLDERS = re.compile(r"^(drafts?|trash|deleted (messages|items)|\[gmail\]/(drafts|trash))$", re.I)


def _dec(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _html_to_text(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", html)
    html = re.sub(r"(?i)</t[dh]>", "  ", html)
    html = re.sub(r"<[^>]+>", " ", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&#36;", "$"), ("&dollar;", "$"), ("&#39;", "'"), ("&quot;", '"')):
        html = html.replace(a, b)
    return "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in html.splitlines() if l.strip())


def _bodies(msg):
    """Return candidate texts: the plain-text part and the HTML part (as text). Retailers often put a
    stub like 'view in browser' in the plain part, so both get tried."""
    plain, html = "", ""
    for part in msg.walk():
        ct = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ct == "text/plain":
            plain += txt
        elif ct == "text/html":
            html += txt
    out = [t for t in (plain.strip(), _html_to_text(html).strip() if html else "") if t]
    return out or [""]


def _body(msg):
    return _bodies(msg)[0]


def process_message(c, message_id, sender, subject, body, date_header, extra="", default=None):
    """Parse one message and apply it. Returns (outcome, retailer)."""
    bodies = body if isinstance(body, list) else [body]
    head = " ".join(b[:800] for b in bodies)
    extra = extra or f"{subject}\n{head}"
    retailer = parsers.detect_retailer(sender, extra, default)
    if not retailer:
        return "skipped (not a retailer)", None
    ev, why, body = None, "", bodies[0]
    for cand in bodies:  # prefer a version that yields items; else any that parses
        e2, w2 = parsers.parse_email(sender, subject, cand, date_header, extra, default)
        if e2 and (e2["items"] or e2["status"] == "cancelled"):
            ev, why, body = e2, "", cand
            break
        if e2 and not ev:
            ev, why, body = e2, "", cand
        elif not e2 and not ev:
            why = w2
    if not ev and not why:
        why = "could not read this email"
    if not ev and why in ("could not tell order status", "no order number found"):
        return "skipped (not an order email)", retailer  # newsletters / promos from the store
    if not ev:
        c.execute(
            "INSERT INTO review(message_id,retailer,subject,reason,snippet,created_at) VALUES(?,?,?,?,?,?)",
            (message_id, retailer, subject, why, body[:400], core.now()),
        )
        return f"needs review: {why}", retailer
    outcome, oid, has_items = core.apply_event(c, ev)
    if not has_items and ev["status"] != "cancelled":
        c.execute(
            "INSERT INTO review(message_id,retailer,subject,reason,snippet,created_at) VALUES(?,?,?,?,?,?)",
            (message_id, retailer, subject,
             f"order {ev['order_number']} has no items parsed - add items manually so inventory can update", body[:400],
             core.now()),
        )
    return outcome, retailer


def _login(conf):
    M = imaplib.IMAP4_SSL(HOST, 993)
    try:
        M.login(conf["email"], conf["password"])
    except imaplib.IMAP4.error as e:
        raise RuntimeError(f"iCloud login failed ({e}). Use an app-specific password from account.apple.com, not your Apple ID password, and the @icloud.com address.")
    return M


def list_folders():
    conf = load_conf()
    if not conf["email"] or not conf["password"]:
        raise RuntimeError("Save your iCloud email and app-specific password first.")
    M = _login(conf)
    try:
        out = []
        for line in M.list()[1] or []:
            txt = line.decode(errors="replace")
            m = re.match(r'\((?P<flags>[^)]*)\)\s+"(?P<sep>[^"]*)"\s+(?P<name>.+)$', txt)
            if m and "\\Noselect" not in m.group("flags"):
                out.append(m.group("name").strip().strip('"'))
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _brand_from_folder(name):
    n = name.lower()
    for brand, words in parsers.BRAND_WORDS.items():
        if any(w.replace(".com", "") in n for w in words) or brand.lower() in n:
            return brand
    return None


def _all_folders(M):
    out = []
    for line in M.list()[1] or []:
        txt = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+"(?P<sep>[^"]*)"\s+(?P<name>.+)$', txt)
        if m and "\\Noselect" not in m.group("flags"):
            n = m.group("name").strip().strip('"')
            if not SKIP_FOLDERS.match(n):
                out.append(n)
    return out


def _uids(M, crit):
    typ, data = M.uid("search", None, crit)
    return set(data[0].split()) if typ == "OK" and data and data[0] else set()


ORDER_WORDS = re.compile(r"order|ship|deliver|cancel|receipt|confirm|tracking|pick ?up|purchase|package|parcel|refund|invoice|payment|thank(s| you)|pre-?order", re.I)
RELAYS = ("privaterelay.appleid.com", "icloud.com", "me.com", "mac.com")


def _fetch_many(M, uids, spec):
    """UID FETCH several messages in one round trip. Returns {uid(bytes): raw bytes}."""
    out = {}
    if not uids:
        return out
    typ, data = M.uid("fetch", b",".join(uids), spec)
    if typ != "OK":
        return out
    for item in data or []:
        if isinstance(item, tuple):
            m = re.search(rb"UID (\d+)", item[0])
            if m:
                out[m.group(1)] = item[1]
    return out


def _looks_like_order(msg, mine, whole):
    """Cheap decision from the headers alone, so only plausible order emails are downloaded in full."""
    if whole:
        return True
    sender, subject = _dec(msg.get("From")), _dec(msg.get("Subject"))
    hints = f"{_dec(msg.get('Reply-To'))} {_dec(msg.get('X-Original-From'))} {subject}"
    if not ORDER_WORDS.search(subject):
        return False
    if parsers.detect_retailer(sender, hints):
        return True
    s = sender.lower()
    return bool(mine and mine in s) or any(r in s for r in RELAYS)  # forwards / Hide-My-Email


def sync(progress=print, rescan=False):
    conf = load_conf()
    if not conf["email"] or not conf["password"]:
        raise RuntimeError("iCloud credentials not set. Click Settings and add your iCloud email and app-specific password.")
    c = core.connect()
    pv = c.execute("SELECT value FROM settings WHERE key='parser_version'").fetchone()
    if rescan or not pv or str(pv[0]) != str(PARSER_VERSION):
        # re-read everything: safe, order statuses only ever move forward and stock is only added once
        c.execute("DELETE FROM processed_emails")
        c.execute("DELETE FROM review WHERE resolved=0")
        c.execute("INSERT OR REPLACE INTO settings VALUES('parser_version',?)", (str(PARSER_VERSION),))
        c.commit()
        progress("Re-reading every email (first run with the improved reader)" if not rescan else "Re-scan: forgetting which emails were already read")
    seen = {r[0] for r in c.execute("SELECT message_id FROM processed_emails")}
    progress(f"Connecting to iCloud as {conf['email']} ...")
    M = _login(conf)
    progress("Logged in.")
    days = int(conf["days"] or 0)
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y") if days else ""
    stats = {"scanned": 0, "new": 0, "updated": 0, "review": 0, "skipped": 0, "errors": 0}
    wanted = [f.strip() for f in str(conf["folder"]).split(",") if f.strip()] or ["*"]
    folders = []
    for f in wanted:
        if f in ("*", "ALL", "all", "All folders"):
            folders += [x for x in _all_folders(M) if x not in folders]
        elif f not in folders:
            folders.append(f)
    progress(f"Folders to read ({len(folders)}): " + ", ".join(folders))
    by_retailer = {}
    for folder in folders:
        typ, _ = M.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            progress(f"!! Folder '{folder}' not found. Use Settings > Load my folders to pick the exact name.")
            continue
        validity = ""
        try:
            validity = (M.response("UIDVALIDITY")[1] or [b""])[0].decode()
        except Exception:
            pass
        whole = folder.upper() != "INBOX" and _brand_from_folder(folder) is not None  # a folder named after a store
        default = _brand_from_folder(folder) if whole else None
        crit = f"SINCE {since}" if since else "ALL"
        uids = _uids(M, f"({crit})")
        todo = [u for u in sorted(uids, key=int) if f"uid:{folder}:{validity}:{u.decode()}" not in seen]
        progress(f"'{folder}': {len(uids)} emails, {len(todo)} not checked yet" + (f", treating them as {default}" if default else ""))
        mine = conf["email"].lower()
        cands = []
        for i in range(0, len(todo), 300):  # pass 1: headers only, 300 at a time
            chunk = todo[i:i + 300]
            try:
                heads = _fetch_many(M, chunk, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT REPLY-TO X-ORIGINAL-FROM MESSAGE-ID)])")
            except Exception as e:
                progress(f"!! header batch failed in '{folder}': {e}")
                heads = {}
            for uid in chunk:
                raw = heads.get(uid)
                if raw is None or _looks_like_order(email.message_from_bytes(raw), mine, whole):
                    cands.append(uid)
                else:
                    c.execute("INSERT OR IGNORE INTO processed_emails VALUES(?,?,?,?,?)",
                              (f"uid:{folder}:{validity}:{uid.decode()}", "", None, "skipped (not an order email)", core.now()))
                    stats["skipped"] += 1
                    stats["scanned"] += 1
            c.commit()
            progress(f"'{folder}': checked {min(i + 300, len(todo))} of {len(todo)} ...")
        if todo:
            progress(f"'{folder}': {len(cands)} look like orders - reading them now")
        for i in range(0, len(cands), 10):  # pass 2: full text of likely orders, 10 per round trip
            chunk = cands[i:i + 10]
            try:
                raws = _fetch_many(M, chunk, "(BODY.PEEK[])")
            except Exception as e:
                progress(f"!! could not download a batch in '{folder}': {e}")
                stats["errors"] += len(chunk)
                continue
            for uid in chunk:
                key = f"uid:{folder}:{validity}:{uid.decode()}"
                try:
                    raw = raws.get(uid)
                    if raw is None:
                        continue
                    msg = email.message_from_bytes(raw)
                    mid = (msg.get("Message-ID") or key).strip()
                    dup = mid in seen and mid != key
                    seen.add(key)
                    if dup:  # same email filed in two folders
                        c.execute("INSERT OR IGNORE INTO processed_emails VALUES(?,?,?,?,?)", (key, "", None, "duplicate", core.now()))
                        continue
                    seen.add(mid)
                    stats["scanned"] += 1
                    subject, sender = _dec(msg.get("Subject")), _dec(msg.get("From"))
                    body = _bodies(msg)
                    extra = f"{_dec(msg.get('Reply-To'))} {_dec(msg.get('X-Original-From'))} {subject}\n{body[0][:800]}"
                    outcome, retailer = process_message(c, mid, sender, subject, body, msg.get("Date"), extra, default)
                    c.execute("INSERT OR IGNORE INTO processed_emails VALUES(?,?,?,?,?)", (mid, subject, retailer, outcome, core.now()))
                    c.execute("INSERT OR IGNORE INTO processed_emails VALUES(?,?,?,?,?)", (key, subject, retailer, outcome, core.now()))
                    c.commit()
                except Exception as e:  # one unreadable email must never stop the scan
                    stats["errors"] += 1
                    progress(f"!! could not read email {uid.decode()} in '{folder}': {e}")
                    continue
                k = ("review" if "review" in outcome else "skipped" if outcome.startswith("skipped")
                     else "new" if outcome.startswith("new") else "updated")
                stats[k] += 1
                if retailer and not outcome.startswith("skipped"):
                    by_retailer[retailer] = by_retailer.get(retailer, 0) + 1
                    progress(f"[{retailer}] {subject[:60]} -> {outcome}")
    try:
        M.logout()
    except Exception:
        pass
    progress("Order emails found: " + (", ".join(f"{k} {v}" for k, v in by_retailer.items()) or "none"))
    c.close()
    return stats


# ------------------------------------------------------------------ inbox viewer (read-only)
import threading
from email.utils import parsedate_to_datetime

_LOCK = threading.Lock()
_CONN = {"M": None, "sig": None, "folder": None}


def _conn(folder):
    """Reuse one IMAP connection between clicks; reconnect if it dropped or settings changed."""
    conf = load_conf()
    if not conf["email"] or not conf["password"]:
        raise RuntimeError("Add your iCloud email and app-specific password in Settings first.")
    sig = (conf["email"], conf["password"])
    M = _CONN["M"]
    if M is not None and _CONN["sig"] == sig:
        try:
            M.noop()
        except Exception:
            M = None
    else:
        M = None
    if M is None:
        M = _login(conf)
        _CONN.update(M=M, sig=sig, folder=None)
    if _CONN["folder"] != folder:
        typ, _ = M.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Folder '{folder}' not found")
        _CONN["folder"] = folder
    return M


def _iso(d):
    try:
        return parsedate_to_datetime(d).astimezone().isoformat(timespec="minutes")
    except Exception:
        return d or ""


def inbox_list(folder="INBOX", limit=50, q=""):
    limit = max(1, min(int(limit), 200))
    with _LOCK:
        M = _conn(folder)
        if q:
            q = q.replace('"', "")
            typ, data = M.uid("search", None, "OR", "OR", "SUBJECT", f'"{q}"', "FROM", f'"{q}"', "BODY", f'"{q}"')
        else:
            typ, data = M.uid("search", None, "ALL")
        uids = (data[0].split() if typ == "OK" and data and data[0] else [])
        total = len(uids)
        out = []
        for uid in reversed(uids[-limit:]):
            typ, d = M.uid("fetch", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not d or not d[0] or not isinstance(d[0], tuple):
                continue
            msg = email.message_from_bytes(d[0][1])
            flags = d[0][0].decode(errors="replace")
            out.append({"uid": uid.decode(), "from": _dec(msg.get("From")), "subject": _dec(msg.get("Subject")) or "(no subject)",
                        "date": _iso(msg.get("Date")), "unread": "\\Seen" not in flags})
        return {"folder": folder, "total": total, "messages": out}


def inbox_message(folder, uid):
    with _LOCK:
        M = _conn(folder)
        typ, d = M.uid("fetch", uid, "(BODY.PEEK[])")
        if typ != "OK" or not d or not d[0]:
            raise RuntimeError("Couldn't open that email")
        msg = email.message_from_bytes(d[0][1])
    plain, html = "", ""
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            txt = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if part.get_content_type() == "text/plain":
            plain += txt
        elif part.get_content_type() == "text/html":
            html += txt
    subject, sender = _dec(msg.get("Subject")), _dec(msg.get("From"))
    bodies = [t for t in (plain.strip(), _html_to_text(html).strip() if html else "") if t] or [""]
    extra = f"{_dec(msg.get('Reply-To'))} {_dec(msg.get('X-Original-From'))} {subject}\n{bodies[0][:800]}"
    default = _brand_from_folder(folder) if folder.upper() != "INBOX" else None
    ev, why = None, "not a recognised retailer email"
    if parsers.detect_retailer(sender, extra, default):
        for cand in bodies:
            ev, why = parsers.parse_email(sender, subject, cand, msg.get("Date"), extra, default)
            if ev and ev["items"]:
                break
    return {"uid": uid, "folder": folder, "from": sender, "to": _dec(msg.get("To")), "subject": subject, "date": _iso(msg.get("Date")),
            "text": plain.strip() or bodies[0], "html": html, "parsed": ev, "reason": why if not ev else ""}


def inbox_apply(folder, uid):
    """Import one email into orders on demand (same logic as a sync)."""
    with _LOCK:
        M = _conn(folder)
        typ, d = M.uid("fetch", uid, "(BODY.PEEK[])")
        if typ != "OK" or not d or not d[0]:
            raise RuntimeError("Couldn't open that email")
        msg = email.message_from_bytes(d[0][1])
    c = core.connect()
    try:
        subject, sender = _dec(msg.get("Subject")), _dec(msg.get("From"))
        bodies = _bodies(msg)
        extra = f"{_dec(msg.get('Reply-To'))} {_dec(msg.get('X-Original-From'))} {subject}\n{bodies[0][:800]}"
        default = _brand_from_folder(folder) if folder.upper() != "INBOX" else None
        mid = (msg.get("Message-ID") or f"{folder}-uid-{uid}").strip()
        outcome, retailer = process_message(c, mid, sender, subject, bodies, msg.get("Date"), extra, default)
        c.execute("INSERT OR REPLACE INTO processed_emails VALUES(?,?,?,?,?)", (mid, subject, retailer, outcome, core.now()))
        c.commit()
        return {"outcome": outcome, "retailer": retailer}
    finally:
        c.close()
