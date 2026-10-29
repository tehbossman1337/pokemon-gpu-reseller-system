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
    conf.setdefault("folder", "INBOX")
    conf.setdefault("days", 30)
    return conf


def save_conf(email_addr, password, folder="INBOX", days=30):
    with open(CONF_PATH, "w") as f:
        json.dump({"email": email_addr, "password": password, "folder": folder, "days": days}, f)
    os.chmod(CONF_PATH, 0o600)


def _dec(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _body(msg):
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
    if plain.strip():
        return plain
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", html)
    html = re.sub(r"(?i)</t[dh]>", "  ", html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    return "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in html.splitlines() if l.strip())


def process_message(c, message_id, sender, subject, body, date_header):
    """Parse one message and apply it. Returns (outcome, retailer)."""
    retailer = parsers.detect_retailer(sender)
    if not retailer:
        return "skipped (not a retailer)", None
    ev, why = parsers.parse_email(sender, subject, body, date_header)
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


def sync(progress=print):
    conf = load_conf()
    if not conf["email"] or not conf["password"]:
        raise RuntimeError("iCloud credentials not set. Add them in Settings or via env vars.")
    c = core.connect()
    seen = {r[0] for r in c.execute("SELECT message_id FROM processed_emails")}
    M = imaplib.IMAP4_SSL(HOST, 993)
    try:
        M.login(conf["email"], conf["password"])
    except imaplib.IMAP4.error as e:
        raise RuntimeError(f"iCloud login failed ({e}). Use an app-specific password, not your Apple ID password.")
    M.select(f'"{conf["folder"]}"', readonly=True)
    since = (datetime.now() - timedelta(days=int(conf["days"]))).strftime("%d-%b-%Y")
    uids = []
    for doms in parsers.RETAILERS.values():
        for d in doms:
            typ, data = M.search(None, f'(SINCE {since} FROM "{d}")')
            if typ == "OK" and data[0]:
                uids += data[0].split()
    uids = sorted(set(uids), key=int)
    stats = {"scanned": 0, "new": 0, "updated": 0, "review": 0, "skipped": 0}
    for uid in uids:
        typ, data = M.fetch(uid, "(BODY.PEEK[])")
        if typ != "OK" or not data or not data[0]:
            continue
        msg = email.message_from_bytes(data[0][1])
        mid = (msg.get("Message-ID") or f"uid-{uid.decode()}").strip()
        if mid in seen:
            continue
        stats["scanned"] += 1
        subject, sender = _dec(msg.get("Subject")), _dec(msg.get("From"))
        outcome, retailer = process_message(c, mid, sender, subject, _body(msg), msg.get("Date"))
        c.execute("INSERT OR IGNORE INTO processed_emails VALUES(?,?,?,?,?)",
                  (mid, subject, retailer, outcome, core.now()))
        c.commit()
        key = ("review" if "review" in outcome else "skipped" if outcome.startswith("skipped")
               else "new" if outcome.startswith("new") else "updated")
        stats[key] += 1
        progress(f"[{retailer or '-'}] {subject[:60]} -> {outcome}")
    M.logout()
    c.close()
    return stats
