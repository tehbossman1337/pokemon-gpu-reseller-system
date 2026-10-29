"""Phone scanner pairing.

A tiny second web server (opt-in) that exposes ONLY barcode scanning, protected by a secret token, so a phone on the same
Wi-Fi can send scans to the Scan Station.  The main app (accounts, profiles, sales...) is never exposed to the network.
Works with the phone page itself, or any scanner app that can open a URL / send an HTTP request with the scanned code.
"""
import hmac
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import core
import qr
import tracker

_SRV = {"srv": None, "port": 0}
_LOCK = threading.Lock()


def _get(c, k, d=""):
    r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r else d


def _set(c, k, v):
    c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))
    c.commit()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def token(c):
    t = _get(c, "phone.token")
    if not t:
        t = secrets.token_urlsafe(12)
        _set(c, "phone.token", t)
    return t


def status(c):
    on = _get(c, "phone.enabled") == "1"
    out = {"enabled": on, "running": bool(_SRV["srv"]), "mode": _get(c, "phone.mode", "receive")}
    if on and _SRV["srv"]:
        url = f"http://{lan_ip()}:{_SRV['port']}/?t={token(c)}"
        out.update(url=url, qr=qr.svg(url, scale=5), scan_url=f"http://{lan_ip()}:{_SRV['port']}/s?t={token(c)}&code=")
    return out


def set_mode(c, mode):
    if mode in ("receive", "sell"):
        _set(c, "phone.mode", mode)


def regenerate(c):
    _set(c, "phone.token", secrets.token_urlsafe(12))


def _do_scan(code, mode, sold_by):
    c = core.connect()
    try:
        if mode in ("receive", "sell"):
            set_mode(c, mode)
        mode = _get(c, "phone.mode", "receive")
        try:
            r = tracker.scan(c, mode, code, sold_by)
            return {"ok": True, "mode": mode, "message": r["message"], "created": r.get("created", False) and mode == "receive",
                    "needs_price": r.get("needs_price", False)}
        except ValueError as e:
            return {"ok": False, "mode": mode, "error": str(e)}
    finally:
        c.close()


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Inventory Agent scanner</title><style>
body{font:17px system-ui;margin:0;background:#0d0f13;color:#eceef2}main{max-width:520px;margin:auto;padding:16px}
h1{font-size:19px}.seg{display:flex;gap:8px;margin:12px 0}.seg button{flex:1;padding:14px;border-radius:10px;border:1px solid #444;background:#171a20;color:#eceef2;font-size:17px}
.seg button.on{background:#ef5350;border-color:#ef5350;color:#fff}input{width:100%;box-sizing:border-box;padding:16px;font-size:20px;border-radius:10px;border:1px solid #555;background:#171a20;color:#fff}
button.go{width:100%;padding:16px;margin-top:10px;border-radius:10px;border:0;background:#1d4ed8;color:#fff;font-size:18px}
#msg{margin:14px 0;padding:12px;border-radius:10px;min-height:24px}.ok{background:#14532d}.bad{background:#7f1d1d}
#vid{width:100%;border-radius:10px;display:none;margin-top:10px}li{margin:6px 0;color:#9aa1ad;font-size:14px}</style></head><body><main>
<h1>Inventory Agent scanner</h1><div class="seg"><button id="m_receive" onclick="setMode('receive')">Receive stock</button><button id="m_sell" onclick="setMode('sell')">Sell</button></div>
<input id="code" placeholder="Scan or type a barcode / SKU" autocomplete="off" autocapitalize="off" inputmode="text" autofocus>
<button class="go" onclick="send()">Send</button><button class="go" id="camb" style="display:none;background:#374151" onclick="cam()">Use camera</button>
<video id="vid" playsinline muted></video><div id="msg"></div><ul id="hist"></ul></main><script>
const T=new URLSearchParams(location.search).get('t');let mode=%MODE%;
function setMode(m){mode=m;document.getElementById('m_receive').className=m=='receive'?'on':'';document.getElementById('m_sell').className=m=='sell'?'on':''}
setMode(mode);
async function send(code){const el=document.getElementById('code');code=(code||el.value).trim();if(!code)return;el.value='';
 const r=await fetch('/scan',{method:'POST',body:JSON.stringify({t:T,code,mode})}).then(r=>r.json()).catch(e=>({ok:false,error:'Cannot reach the PC. Same Wi-Fi?'}));
 const m=document.getElementById('msg');m.className=r.ok?'ok':'bad';m.textContent=r.ok?r.message+(r.created?' — finish the details on your PC.':''):r.error;
 const li=document.createElement('li');li.textContent=code+' → '+(r.ok?'ok':r.error);document.getElementById('hist').prepend(li);if(navigator.vibrate)navigator.vibrate(r.ok?60:[80,60,80]);el.focus()}
document.getElementById('code').addEventListener('keydown',e=>{if(e.key=='Enter')send()});
let stream,det,busy=false;
if('BarcodeDetector' in window&&navigator.mediaDevices&&window.isSecureContext)document.getElementById('camb').style.display='block';
async function cam(){try{det=new BarcodeDetector();stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});const v=document.getElementById('vid');v.srcObject=stream;v.style.display='block';await v.play();
 const tick=async()=>{if(!stream)return;if(!busy){busy=true;try{const b=await det.detect(v);if(b.length){await send(b[0].rawValue);await new Promise(r=>setTimeout(r,1500))}}catch(e){}busy=false}requestAnimationFrame(tick)};tick()}catch(e){document.getElementById('msg').className='bad';document.getElementById('msg').textContent='Camera not available here. Type the code, or use a scanner app.'}}
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _tok_ok(self, t):
        c = core.connect()
        try:
            return bool(t) and hmac.compare_digest(str(t), token(c))
        finally:
            c.close()

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        t = (q.get("t") or [""])[0]
        if not self._tok_ok(t):
            return self._send(403, "Not paired. Open the link shown in Inventory Agent -> Scan Station.", "text/plain")
        if u.path == "/":
            c = core.connect()
            try:
                mode = _get(c, "phone.mode", "receive")
            finally:
                c.close()
            return self._send(200, PAGE.replace("%MODE%", json.dumps(mode)), "text/html")
        if u.path == "/s":  # for scanner apps that open a URL with the scanned code appended
            code = (q.get("code") or [""])[0]
            r = _do_scan(code, (q.get("mode") or [""])[0], "")
            return self._send(200, f"<meta name=viewport content='width=device-width'><body style='font:20px system-ui;padding:20px'>"
                                   f"<b>{'OK' if r['ok'] else 'Problem'}</b><br>{(r.get('message') or r.get('error') or '').replace('<', '&lt;')}</body>", "text/html")
        self._send(404, "Not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/scan":
            return self._send(404, "{}")
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 4096)
            b = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, json.dumps({"ok": False, "error": "Bad request"}))
        if not self._tok_ok(b.get("t")):
            return self._send(403, json.dumps({"ok": False, "error": "Not paired - re-open the link from the PC"}))
        self._send(200, json.dumps(_do_scan(str(b.get("code") or ""), str(b.get("mode") or ""), "")))


def start(c, base=8766):
    with _LOCK:
        if _SRV["srv"]:
            return True
        for p in range(base, base + 20):
            try:
                srv = ThreadingHTTPServer(("0.0.0.0", p), H)
            except OSError:
                continue
            _SRV.update(srv=srv, port=p)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            return True
    return False


def stop():
    with _LOCK:
        if _SRV["srv"]:
            _SRV["srv"].shutdown()
            _SRV["srv"].server_close()
            _SRV.update(srv=None, port=0)


def enable(c, on):
    _set(c, "phone.enabled", "1" if on else "0")
    if on:
        token(c)
        if not start(c):
            raise ValueError("Could not open a network port for the phone scanner")
    else:
        stop()


def autostart():
    c = core.connect()
    try:
        if _get(c, "phone.enabled") == "1":
            start(c)
    finally:
        c.close()
