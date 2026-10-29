"""Notifications: an in-app inbox (bell) plus optional pushes to the Windows desktop, Discord, Slack and ntfy.

notify(c, kind, title, body) records the notification and sends it to every channel you switched on.
Pushes run on a background thread so a slow webhook can never stall the app or an email sync.
"""
import base64
import json
import os
import subprocess
import threading
import urllib.request
from datetime import datetime
from xml.sax.saxutils import escape

import core

KINDS = {
    "pkg_shipped": "Package shipped",
    "pkg_out": "Out for delivery",
    "pkg_delivered": "Package delivered",
    "pkg_exception": "Delivery problem",
    "order_cancelled": "Order cancelled",
    "big_sale": "Big sale",
    "review": "Emails need review",
    "test": "Test",
}
DEFAULTS = {"desktop": True, "discord": "", "slack": "", "ntfy": "", "ntfy_server": "https://ntfy.sh",
            "quiet_start": "", "quiet_end": "", "events": {k: True for k in KINDS}}
_LAST_ERRORS = {}


def get_config(c):
    r = c.execute("SELECT value FROM settings WHERE key='notify'").fetchone()
    cfg = json.loads(r[0]) if r else {}
    out = {**DEFAULTS, **{k: v for k, v in cfg.items() if k != "events"}}
    out["events"] = {**DEFAULTS["events"], **(cfg.get("events") or {})}
    if not out["slack"]:  # the older big-sale Slack setting keeps working
        try:
            import tracker
            out["slack"] = tracker.get_settings(c).get("slack_webhook", "")
        except Exception:
            pass
    return out


def set_config(c, d):
    cur = get_config(c)
    cfg = {"desktop": bool(d.get("desktop", cur["desktop"])),
           "discord": str(d.get("discord", cur["discord"]) or "").strip(),
           "slack": str(d.get("slack", cur["slack"]) or "").strip(),
           "ntfy": str(d.get("ntfy", cur["ntfy"]) or "").strip(),
           "ntfy_server": str(d.get("ntfy_server", cur["ntfy_server"]) or "https://ntfy.sh").strip().rstrip("/"),
           "quiet_start": str(d.get("quiet_start", cur["quiet_start"]) or "").strip(),
           "quiet_end": str(d.get("quiet_end", cur["quiet_end"]) or "").strip(),
           "events": {k: bool((d.get("events") or {}).get(k, cur["events"].get(k, True))) for k in KINDS}}
    for k in ("discord", "slack"):
        if cfg[k] and not cfg[k].startswith("https://"):
            raise ValueError(f"The {k.title()} webhook must start with https://")
    if cfg["ntfy"] and not (cfg["ntfy"].startswith("https://") or "/" not in cfg["ntfy"]):
        raise ValueError("ntfy: enter just a topic name (e.g. my-valor-alerts) or a full https:// topic URL")
    for k in ("quiet_start", "quiet_end"):
        if cfg[k]:
            try:
                datetime.strptime(cfg[k], "%H:%M")
            except ValueError:
                raise ValueError("Quiet hours must look like 22:00")
    c.execute("INSERT OR REPLACE INTO settings VALUES('notify',?)", (json.dumps(cfg),))
    c.commit()
    return cfg


def _in_quiet(cfg, now=None):
    a, b = cfg.get("quiet_start"), cfg.get("quiet_end")
    if not a or not b:
        return False
    t = (now or datetime.now()).strftime("%H:%M")
    return (a <= t < b) if a <= b else (t >= a or t < b)


# ------------------------------------------------------------------ channels
def toast_script(title, body):
    """PowerShell that shows a native Windows toast (no extra software needed)."""
    xml = f"<toast><visual><binding template=\"ToastGeneric\"><text>{escape(title)}</text><text>{escape(body)}</text></binding></visual></toast>"
    return ("$ErrorActionPreference='Stop';"
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
            "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
            f"$x.LoadXml('{xml.replace(chr(39), chr(39) * 2)}');"
            "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($t)")


def _desktop(title, body):
    if os.name != "nt":
        return
    enc = base64.b64encode(toast_script(title, body).encode("utf-16-le")).decode()
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", enc],
                     creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _post(url, data, headers=None, timeout=15):
    h = {"Content-Type": "application/json", "User-Agent": "ValorInventoryAgent"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data if isinstance(data, bytes) else json.dumps(data).encode(), headers=h, method="POST")
    urllib.request.urlopen(req, timeout=timeout).read()


def send_push(cfg, title, body, force=False):
    """Send to every enabled channel. Returns {channel: 'ok' | error text}."""
    res = {}
    quiet = _in_quiet(cfg) and not force
    plan = []
    if cfg.get("desktop"):
        plan.append(("desktop", lambda: _desktop(title, body)))
    if cfg.get("discord"):
        plan.append(("discord", lambda: _post(cfg["discord"], {"username": "Valor Inventory Agent", "content": f"**{title}**\n{body}"[:1900]})))
    if cfg.get("slack"):
        plan.append(("slack", lambda: _post(cfg["slack"], {"text": f"*{title}*\n{body}"})))
    if cfg.get("ntfy"):
        t = cfg["ntfy"]
        url = t if t.startswith("https://") else f"{cfg.get('ntfy_server') or 'https://ntfy.sh'}/{t}"
        plan.append(("ntfy", lambda: _post(url, body.encode("utf-8"), {"Title": title.encode("ascii", "replace").decode(), "Content-Type": "text/plain"})))
    for name, fn in plan:
        if quiet:
            res[name] = "skipped (quiet hours)"
            continue
        try:
            fn()
            res[name] = "ok"
            _LAST_ERRORS.pop(name, None)
        except Exception as e:
            res[name] = f"{type(e).__name__}: {e}"[:200]
            _LAST_ERRORS[name] = res[name]
    return res


# ------------------------------------------------------------------ public API
def notify(c, kind, title, body="", dedupe=None, link="", sync=False, push=True):
    """Record a notification (once per dedupe key) and push it. Returns the id, or None if skipped."""
    cfg = get_config(c)
    if not cfg["events"].get(kind, True):
        return None
    if dedupe and c.execute("SELECT 1 FROM notifications WHERE dedupe=?", (dedupe,)).fetchone():
        return None
    try:
        nid = c.execute("INSERT INTO notifications(ts,kind,title,body,link,dedupe) VALUES(?,?,?,?,?,?)",
                        (core.now(), kind, title, body, link, dedupe)).lastrowid
    except Exception:
        return None  # lost a race on the dedupe key
    c.commit()
    if not push:
        return nid
    job = lambda: send_push(cfg, title, body)  # noqa: E731
    if sync:
        job()
    else:
        threading.Thread(target=job, daemon=True).start()
    return nid


def list_notifications(c, limit=60):
    rows = [dict(r) for r in c.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]
    unread = c.execute("SELECT COUNT(*) FROM notifications WHERE read=0").fetchone()[0]
    return {"items": rows, "unread": unread, "errors": dict(_LAST_ERRORS)}


def mark_read(c, ids=None):
    if ids:
        c.execute(f"UPDATE notifications SET read=1 WHERE id IN ({','.join('?' * len(ids))})", [int(i) for i in ids])
    else:
        c.execute("UPDATE notifications SET read=1")
    c.commit()


def clear(c):
    c.execute("DELETE FROM notifications")
    c.commit()
