"""Local dashboard server (stdlib only). Run: python server.py  ->  http://localhost:8765"""
import json
import mimetypes
import sys
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import re
import subprocess
import core
import mail_sync
import tracker
import parsers
import updater
import static_embedded
import vdata

STATIC = os.path.join(core.RES_DIR, "static")
SYNC = {"running": False, "log": [], "stats": None, "error": None}
try:
    import build_info
    BUILD = build_info.BUILD
except Exception:
    BUILD = 0


def exports_dir():
    d = os.path.join(os.path.expanduser("~"), "Documents", "Valor Inventory Agent Exports")
    os.makedirs(d, exist_ok=True)
    return d


def save_export(filename, text):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(filename or "valor_export.json")) or "valor_export.json"
    path = os.path.join(exports_dir(), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def rows(c, q, args=()):
    return [dict(r) for r in c.execute(q, args)]


def get_token(c):
    r = c.execute("SELECT value FROM settings WHERE key='github_token'").fetchone()
    return r[0] if r else ""


def run_sync(rescan=False):
    SYNC.update(running=True, log=[], stats=None, error=None)
    try:
        SYNC["stats"] = mail_sync.sync(progress=lambda m: SYNC["log"].append(m), rescan=rescan)
    except Exception as e:  # surfaced in the UI
        SYNC["error"] = str(e)
    finally:
        SYNC["running"] = False


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._crash()

    def do_POST(self):
        try:
            self._post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._crash()

    def _crash(self):
        import traceback
        tb = traceback.format_exc()
        try:
            with open(os.path.join(core.DATA_DIR, "app.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + self.path + "\n" + tb + "\n")
        except Exception:
            pass
        try:
            self.send({"error": "Internal error: " + tb.strip().splitlines()[-1]}, 500)
        except Exception:
            pass

    def _get(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            f = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            p = os.path.normpath(os.path.join(STATIC, f))
            if f == "index.html" and not os.path.isfile(p):
                data = static_embedded.HTML.encode("utf-8")
                ctype = "text/html; charset=utf-8"
            elif p.startswith(STATIC) and os.path.isfile(p):
                data = open(p, "rb").read()
                ctype = mimetypes.guess_type(p)[0] or "text/plain"
            else:
                return self.send({"error": "not found"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        c = core.connect()
        try:
            if u.path == "/api/summary":
                return self.send(core.summary(c))
            if u.path == "/api/orders":
                orders = rows(c, "SELECT * FROM orders ORDER BY COALESCE(order_date,'') DESC, id DESC")
                for o in orders:
                    o["items"] = rows(c, "SELECT * FROM order_items WHERE order_id=?", (o["id"],))
                return self.send(orders)
            if u.path == "/api/items":
                return self.send(tracker.list_items(c))
            if u.path == "/api/tsales":
                return self.send(tracker.list_sales(c))
            if u.path == "/api/receipts":
                return self.send(tracker.list_receipts(c))
            if u.path == "/api/dashboard":
                return self.send(tracker.dashboard(c))
            if u.path == "/api/sku":
                return self.send({"item": tracker.sku_lookup(c, parse_qs(u.query).get("sku", [""])[0])})
            if u.path == "/api/tax":
                y = parse_qs(u.query).get("year", [""])[0]
                return self.send(tracker.tax_summary(c, int(y) if y.isdigit() else tracker.date.today().year))
            if u.path == "/api/tsettings":
                return self.send({"settings": tracker.get_settings(c), "fees": tracker.list_fees(c), "currencies": tracker.list_currencies(c)})
            if u.path == "/api/lists":
                return self.send(tracker.lists(c))
            if u.path == "/api/scans":
                return self.send(tracker.list_scans(c))
            if u.path == "/api/review":
                return self.send(rows(c, "SELECT * FROM review WHERE resolved=0 ORDER BY id DESC"))
            if u.path == "/api/vaccounts":
                return self.send(vdata.list_accounts(c))
            if u.path == "/api/vprofiles":
                return self.send(vdata.list_profiles(c))
            if u.path == "/api/version":
                return self.send({"build": BUILD})
            if u.path == "/api/update/check":
                return self.send(updater.check(get_token(c)))
            if u.path == "/api/update/status":
                return self.send({**updater.STATE, "build": BUILD, "has_token": bool(get_token(c))})
            if u.path == "/api/inbox/list":
                qs = parse_qs(u.query)
                try:
                    return self.send({"ok": True, **mail_sync.inbox_list(qs.get("folder", ["INBOX"])[0], int(qs.get("limit", ["50"])[0]), qs.get("q", [""])[0])})
                except Exception as e:
                    return self.send({"ok": False, "error": str(e)})
            if u.path == "/api/inbox/message":
                qs = parse_qs(u.query)
                try:
                    return self.send({"ok": True, **mail_sync.inbox_message(qs.get("folder", ["INBOX"])[0], qs["uid"][0])})
                except Exception as e:
                    return self.send({"ok": False, "error": str(e)})
            if u.path == "/api/mail/folders":
                try:
                    return self.send({"ok": True, "folders": mail_sync.list_folders()})
                except Exception as e:
                    return self.send({"ok": False, "error": str(e)})
            if u.path == "/api/limits":
                return self.send(core.limits_report(c))
            if u.path == "/api/sync":
                return self.send(SYNC)
            if u.path == "/api/settings":
                conf = mail_sync.load_conf()
                return self.send({"email": conf["email"], "has_password": bool(conf["password"]),
                                  "folder": conf["folder"], "days": conf["days"], "everything": bool(conf.get("everything", True))})
            self.send({"error": "not found"}, 404)
        finally:
            c.close()

    def _post(self):
        u = urlparse(self.path)
        b = self.body()
        c = core.connect()
        try:
            if u.path == "/api/sync":
                if SYNC["running"]:
                    return self.send({"error": "already running"}, 409)
                threading.Thread(target=run_sync, kwargs={"rescan": bool(b.get("rescan"))}, daemon=True).start()
                return self.send({"ok": True})
            if u.path == "/api/settings":
                old = mail_sync.load_conf()
                mail_sync.save_conf(b.get("email", old["email"]), b.get("password") or old["password"],
                                    b.get("folder", "*") or "*", int(b["days"]) if str(b.get("days", "")).strip().isdigit() else 0,
                                    bool(b.get("everything")))
                return self.send({"ok": True})
            if u.path == "/api/items/save":
                return self.send({"ok": True, "id": tracker.save_item(c, b, b.get("id"))})
            if u.path == "/api/items/delete":
                tracker.delete_item(c, b["id"])
                return self.send({"ok": True})
            if u.path == "/api/tsales/save":
                return self.send({"ok": True, **tracker.save_sale(c, b, b.get("id"))})
            if u.path == "/api/tsales/delete":
                tracker.delete_sale(c, b["id"])
                return self.send({"ok": True})
            if u.path == "/api/receipts/save":
                tracker.save_receipt(c, b, b.get("id"))
                return self.send({"ok": True})
            if u.path == "/api/receipts/delete":
                tracker.delete_receipt(c, b["id"])
                return self.send({"ok": True})
            if u.path == "/api/tsettings":
                return self.send({"ok": True, "settings": tracker.set_settings(c, b)})
            if u.path == "/api/fees/save":
                tracker.save_fee(c, b, b.get("old"))
                return self.send({"ok": True})
            if u.path == "/api/fees/delete":
                tracker.delete_fee(c, b["platform"])
                return self.send({"ok": True})
            if u.path == "/api/currencies/save":
                tracker.save_currency(c, b)
                return self.send({"ok": True})
            if u.path == "/api/currencies/delete":
                tracker.delete_currency(c, b["code"])
                return self.send({"ok": True})
            if u.path == "/api/scan":
                return self.send({"ok": True, **tracker.scan(c, b.get("mode"), b.get("sku"), b.get("sold_by", ""))})
            if u.path == "/api/export":
                text = tracker.export_csv(c, b["name"])
                fn = f"{b['name']}_{tracker.today()}.csv"
                return self.send({"ok": True, "csv": text, "filename": fn, "saved_to": save_export(fn, text)})
            if u.path == "/api/import":
                added, errors = tracker.import_csv(c, b["name"], b.get("csv", ""))
                return self.send({"ok": True, "added": added, "errors": errors[:20], "error_count": len(errors)})
            if u.path == "/api/orders":
                o = core.apply_event(c, {
                    "retailer": b["retailer"], "order_number": b["order_number"].upper(),
                    "status": b.get("status", "confirmed"), "date": b.get("date"), "total": b.get("total"),
                    "items": b.get("items", [])}, auto_receive=False)
                c.execute("UPDATE orders SET source='manual' WHERE id=?", (o[1],))
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/orders/status":
                if b["status"] not in core.STATUSES:
                    return self.send({"error": "bad status"}, 400)
                c.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (b["status"], core.now(), b["id"]))
                if b["status"] == "delivered":
                    core.receive_order(c, b["id"])
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/orders/items":
                c.execute("DELETE FROM order_items WHERE order_id=? AND ?", (b["id"], not c.execute(
                    "SELECT received FROM orders WHERE id=?", (b["id"],)).fetchone()["received"]))
                for it in b["items"]:
                    c.execute("INSERT INTO order_items(order_id,sku,name,qty,unit_price) VALUES(?,?,?,?,?)",
                              (b["id"], it.get("sku", ""), it["name"], int(it.get("qty", 1)), float(it.get("price", 0))))
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/orders/receive":
                ok = core.receive_order(c, b["id"])
                c.commit()
                return self.send({"ok": ok})
            if u.path == "/api/orders/delete":
                c.execute("DELETE FROM orders WHERE id=?", (b["id"],))
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/vprofiles/save":
                site = (b.get("site") or "").strip()
                if not site or not (b.get("rec") or {}).get("name"):
                    return self.send({"error": "Website and profile name are required"}, 400)
                vdata.save_profile(c, site, b["rec"], pid=b.get("id"), group_id=b.get("group_id"))
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/vprofiles/delete":
                for i in b["ids"]:
                    c.execute("DELETE FROM vprofiles WHERE id=? OR group_id=?", (i, i))
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/vprofiles/patch":
                n = vdata.patch_profiles(c, b["ids"], b["changes"])
                c.commit()
                return self.send({"ok": True, "updated": n})
            if u.path == "/api/vprofiles/import":
                site = (b.get("site") or "").strip()
                if not site:
                    return self.send({"error": "Choose which website these profiles are for"}, 400)
                r = vdata.import_profiles(c, site, b["text"], bool(b.get("replace")))
                c.commit()
                return self.send({"ok": True, **r})
            if u.path == "/api/vprofiles/export":
                out = vdata.export_profiles(c, b.get("site") or None, bool(b.get("selected_only")),
                                            b.get("include_originals", True), b.get("include_jigs", True))
                if not out:
                    return self.send({"error": "No profiles match that selection"}, 400)
                text = json.dumps(out, indent=2)
                return self.send({"ok": True, "json": text, "count": len(out), "saved_to": save_export(b.get("filename"), text)})
            if u.path == "/api/vprofiles/jig":
                v = vdata.jig_profile(c, b["id"], max(1, min(int(b.get("count", 10)), 200)), bool(b.get("use_units", True)),
                                      b.get("seed"), bool(b.get("save")), bool(b.get("replace", True)))
                if not v:
                    return self.send({"error": "Couldn't generate variants for that address"}, 400)
                c.commit()
                return self.send({"ok": True, "variants": v})
            if u.path == "/api/vprofiles/bulk_address":
                a = b["address"]
                if not (a.get("addressLine1") and a.get("city") and a.get("state") and a.get("zipCode")):
                    return self.send({"error": "Address line 1, city, state and ZIP are required"}, 400)
                r = vdata.bulk_address(c, b["site"], b.get("ids"), a, bool(b.get("unique")), b.get("seed"), bool(b.get("use_units", True)),
                                     b.get("target", "both") if b.get("target") in ("both", "shipping", "billing") else "both",
                                     [u.strip() for u in (b.get("units") or []) if u.strip()] or None)
                c.commit()
                return self.send({"ok": True, **r})
            if u.path == "/api/vaccounts/save":
                site = (b.get("site") or "").strip()
                if not site:
                    return self.send({"error": "Website is required"}, 400)
                rec, old = b["rec"], b.get("old_id")
                if old and rec.get("id") == old and old == b.get("old_email") and rec.get("email") != old:
                    rec["id"] = rec["email"].strip()
                if old is None:
                    rec["id"] = rec.get("id") or rec.get("email", "").strip()
                vdata.save_account(c, site, rec, old_id=old)
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/vaccounts/delete":
                for i in b["ids"]:
                    c.execute("DELETE FROM vaccounts WHERE site=? AND id=?", (b["site"], i))
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/vaccounts/patch":
                n = vdata.patch_accounts(c, b["site"], b["ids"], b["changes"])
                c.commit()
                return self.send({"ok": True, "updated": n})
            if u.path == "/api/vaccounts/import":
                r = vdata.import_accounts(c, b["text"], (b.get("site") or "").strip() or None, bool(b.get("replace")))
                c.commit()
                return self.send({"ok": True, **r})
            if u.path == "/api/vaccounts/export":
                if not b.get("site"):
                    return self.send({"error": "Choose a website"}, 400)
                out = vdata.export_accounts(c, b["site"], bool(b.get("selected_only")))
                if not out["accounts"]:
                    return self.send({"error": "No accounts match that selection"}, 400)
                text = json.dumps(out, indent=2)
                return self.send({"ok": True, "json": text, "count": len(out["accounts"]), "saved_to": save_export(b.get("filename"), text)})
            if u.path == "/api/open_url":
                url = str(b.get("url", ""))
                if url.startswith(("https://", "http://")):
                    import webbrowser
                    webbrowser.open(url)
                    return self.send({"ok": True})
                return self.send({"error": "Only web links can be opened"}, 400)
            if u.path == "/api/open_exports":
                d = exports_dir()
                try:
                    if os.name == "nt":
                        os.startfile(d)  # noqa
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", d])
                except Exception:
                    pass
                return self.send({"ok": True, "path": d})
            if u.path == "/api/inbox/apply":
                try:
                    return self.send({"ok": True, **mail_sync.inbox_apply(b.get("folder", "INBOX"), str(b["uid"]))})
                except Exception as e:
                    return self.send({"ok": False, "error": str(e)})
            if u.path == "/api/update/download":
                return self.send(updater.start_download(b.get("url", ""), get_token(c), int(b.get("size") or 0)))
            if u.path == "/api/update/install":
                return self.send(updater.install())
            if u.path == "/api/update/token":
                c.execute("INSERT OR REPLACE INTO settings VALUES('github_token',?)", ((b.get("token") or "").strip(),))
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/parse_test":
                ev, why = parsers.parse_email(b.get("sender", ""), b.get("subject", ""), b.get("body", ""))
                return self.send({"ok": True, "event": ev, "reason": why})
            if u.path == "/api/limits/save":
                if not b.get("retailer") or not b.get("product") or int(b.get("max_qty") or 0) < 1:
                    return self.send({"error": "Retailer, product and a limit of at least 1 are required"}, 400)
                if b.get("id"):
                    c.execute("UPDATE limits SET retailer=?,product=?,max_qty=?,window_days=? WHERE id=?",
                              (b["retailer"].strip(), b["product"].strip(), int(b["max_qty"]), int(b.get("window_days") or 0), b["id"]))
                else:
                    c.execute("INSERT INTO limits(retailer,product,max_qty,window_days) VALUES(?,?,?,?)",
                              (b["retailer"].strip(), b["product"].strip(), int(b["max_qty"]), int(b.get("window_days") or 0)))
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/limits/delete":
                c.execute("DELETE FROM limits WHERE id=?", (b["id"],))
                c.commit()
                return self.send({"ok": True})
            if u.path == "/api/review/resolve":
                c.execute("UPDATE review SET resolved=1 WHERE id=?", (b["id"],))
                c.commit()
                return self.send({"ok": True})
            self.send({"error": "not found"}, 404)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
            self.send({"error": f"{type(e).__name__}: {e}"}, 400)
        finally:
            c.close()


def make_server(port=8765):
    core.connect().close()  # create/upgrade the database before any request arrives
    for p in range(port, port + 20):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), H), p
        except OSError:
            continue
    raise RuntimeError("No free port found")


if __name__ == "__main__":
    srv, port = make_server(int(os.environ.get("PORT", 8765)))
    print(f"Valor Inventory Agent running at http://localhost:{port}")
    srv.serve_forever()
