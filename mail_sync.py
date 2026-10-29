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


def process_message(c, message_id, sender, subject, body, date_header, extra=""):
    """Parse one message and apply it. Returns (outcome, retailer)."""
    bodies = body if isinstance(body, list) else [body]
    head = " ".join(b[:800] for b in bodies)
    extra = extra or f"{subject}\n{head}"
    retailer = parsers.detect_retailer(sender, extra)
    if not retailer:
        return "skipped (not a retailer)", None
    ev, why, body = None, "", bodies[0]
    for cand in bodies:  # prefer a version that yields items; else any that parses
        e2, w2 = parsers.parse_email(sender, subject, cand, date_header, extra)
        if e2 and (e2["items"] or e2["status"] == "cancelled"):
            ev, why, body = e2, "", cand
            break
        if e2 and not ev:
            ev, why, body = e2, "", cand
        elif not e2 and not ev:
            why = w2
    if not ev and not why:
        why = "could not read this email"
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


def sync(progress=print, rescan=False):
    conf = load_conf()
    if not conf["email"] or not conf["password"]:
        raise RuntimeError("iCloud credentials not set. Click Settings and add your iCloud email and app-specific password.")
    c = core.connect()
    if rescan:  # re-read everything: safe, order statuses only ever move forward and stock is only added once
        c.execute("DELETE FROM processed_emails")
        c.execute("DELETE FROM review WHERE resolved=0")
        c.commit()
        progress("Re-scan: forgetting which emails were already read")
    seen = {r[0] for r in c.execute("SELECT message_id FROM processed_emails")}
    progress(f"Connecting to iCloud as {conf['email']} ...")
    M = imaplib.IMAP4_SSL(HOST, 993)
    try:
        M.login(conf["email"], conf["password"])
    except imaplib.IMAP4.error as e:
        raise RuntimeError(f"iCloud login failed ({e}). Use an app-specific password from account.apple.com, not your Apple ID password, and the @icloud.com address.")
    progress("Logged in.")
    typ, _ = M.select(f'"{conf["folder"]}"', readonly=True)
    if typ != "OK":
        names = [l.decode(errors="replace") for l in (M.list()[1] or [])]
        raise RuntimeError(f"Mail folder '{conf['folder']}' not found. Folders on your account: " + "; ".join(n.split('"/"')[-1].strip() for n in names))
    since = (datetime.now() - timedelta(days=int(conf["days"]))).strftime("%d-%b-%Y")
    uids, per = set(), {}
    for name, doms in parsers.RETAILERS.items():
        for d in doms:
            typ, data = M.search(None, f'(SINCE {since} FROM "{d}")')
            if typ == "OK" and data[0]:
                found = data[0].split()
                per[name] = per.get(name, 0) + len(found)
                uids.update(found)
    # forwarded / Hide-My-Email / re-sent mail may not show the retailer as sender: also look at order-like subjects
    for word in ("order", "shipped", "delivered", "canceled", "cancelled"):
        typ, data = M.search(None, f'(SINCE {since} SUBJECT "{word}")')
        if typ == "OK" and data[0]:
            uids.update(data[0].split())
    progress("Emails from retailers in the last %s days: %s" % (conf["days"], ", ".join(f"{k} {v}" for k, v in per.items()) or "none found"))
    uids = sorted(uids, key=int)
    progress(f"{len(uids)} candidate emails to check")
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
        body = _bodies(msg)
        extra = f"{_dec(msg.get('Reply-To'))} {_dec(msg.get('X-Original-From'))} {subject}\n{body[0][:800]}"
        outcome, retailer = process_message(c, mid, sender, subject, body, msg.get("Date"), extra)
        c.execute("INSERT OR IGNORE INTO processed_emails VALUES(?,?,?,?,?)", (mid, subject, retailer, outcome, core.now()))
        c.commit()
        key = ("review" if "review" in outcome else "skipped" if outcome.startswith("skipped")
               else "new" if outcome.startswith("new") else "updated")
        stats[key] += 1
        if key != "skipped":
            progress(f"[{retailer or '-'}] {subject[:60]} -> {outcome}")
    M.logout()
    c.close()
    return stats
