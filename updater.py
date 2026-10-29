"""In-app updates from GitHub Releases. The build workflow publishes every build as a release;
this checks for a newer one, downloads it with progress, and runs the installer silently so it
overwrites the installed program files (your data in %APPDATA% is never touched)."""
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import build_info

API = os.environ.get("VALOR_API_BASE", "https://api.github.com")
STATE = {"phase": "idle", "done": 0, "total": 0, "error": "", "path": ""}


class _NoAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub redirects asset downloads to a signed storage URL, which must not receive our token."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).netloc != urllib.parse.urlparse(req.full_url).netloc:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def _open(url, token="", accept="application/vnd.github+json", timeout=30):
    h = {"User-Agent": "ValorInventoryAgent", "Accept": accept}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return urllib.request.build_opener(_NoAuthOnRedirect).open(urllib.request.Request(url, headers=h), timeout=timeout)


def check(token=""):
    if not build_info.REPO:
        return {"ok": False, "error": "This copy wasn't built by GitHub, so it can't self-update. Install the Setup file from your repo's Releases page once."}
    try:
        rel = json.load(_open(f"{API}/repos/{build_info.REPO}/releases/latest", token))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "error": "No release found. If your repo is private, add a GitHub token below; otherwise wait for the build to finish."}
        if e.code in (401, 403):
            return {"ok": False, "error": "GitHub refused the request. Check your token (it needs read access to this repo's contents)."}
        return {"ok": False, "error": f"GitHub said {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"Couldn't reach GitHub: {e}"}
    tag = rel.get("tag_name", "")
    try:
        latest = int(tag.split("-")[-1])
    except ValueError:
        return {"ok": False, "error": f"Unrecognised release tag {tag}"}
    asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(".exe")), None)
    return {"ok": True, "current": build_info.BUILD, "latest": latest, "tag": tag,
            "update": latest > build_info.BUILD and bool(asset), "url": asset["url"] if asset else "",
            "size": asset["size"] if asset else 0, "notes": rel.get("body", ""), "published": rel.get("published_at", "")}


def _download(url, token, size):
    STATE.update(phase="downloading", done=0, total=size, error="")
    path = os.path.join(tempfile.gettempdir(), "ValorInventoryAgent-Setup.exe")
    try:
        with _open(url, token, accept="application/octet-stream", timeout=120) as r, open(path, "wb") as f:
            STATE["total"] = int(r.headers.get("Content-Length") or size or 0)
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                STATE["done"] += len(chunk)
        if STATE["total"] and STATE["done"] < STATE["total"]:
            raise IOError("download was cut short")
        STATE.update(phase="ready", path=path)
    except Exception as e:
        STATE.update(phase="error", error=f"Download failed: {e}")


def start_download(url, token="", size=0):
    if STATE["phase"] == "downloading":
        return {"ok": False, "error": "Already downloading"}
    if not url.startswith(API + "/repos/"):
        return {"ok": False, "error": "Refusing to download from an unexpected address."}
    threading.Thread(target=_download, args=(url, token, size), daemon=True).start()
    return {"ok": True}


def launch_installer(path):
    # /SILENT shows only a progress bar; it overwrites the installed files and relaunches the app.
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS", "/NORESTART"])


def install():
    if STATE["phase"] != "ready" or not os.path.isfile(STATE["path"]):
        return {"ok": False, "error": "Nothing downloaded yet."}
    if os.name != "nt":
        return {"ok": False, "error": "Installing only works on Windows. The file is at " + STATE["path"]}
    STATE["phase"] = "installing"
    launch_installer(STATE["path"])
    threading.Thread(target=lambda: (time.sleep(2), os._exit(0)), daemon=True).start()
    return {"ok": True}
