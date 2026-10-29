"""One-click self-update from GitHub Releases (the build workflow publishes each build as a release)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import build_info

UA = {"User-Agent": "ValorInventoryAgent"}


def _get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)


def check():
    if not build_info.REPO:
        return {"ok": False, "error": "This copy wasn't built by GitHub, so it can't self-update."}
    try:
        rel = json.load(_get(f"https://api.github.com/repos/{build_info.REPO}/releases/latest"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": False, "error": "No release found. The repository must be public and the build must have finished."}
        return {"ok": False, "error": f"GitHub said {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"Couldn't reach GitHub: {e}"}
    tag = rel.get("tag_name", "")
    try:
        latest = int(tag.split("-")[-1])
    except ValueError:
        return {"ok": False, "error": f"Unrecognised release tag {tag}"}
    asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(".exe")), None)
    return {"ok": True, "current": build_info.BUILD, "latest": latest, "update": latest > build_info.BUILD and bool(asset),
            "url": asset["browser_download_url"] if asset else "", "notes": rel.get("body", "")}


def install(url):
    """Download the new installer, run it silently (it closes and restarts this app)."""
    if not url.startswith("https://github.com/"):
        return {"ok": False, "error": "Refusing to download from an unexpected address."}
    if os.name != "nt":
        return {"ok": False, "error": "Auto-install only works on Windows."}
    path = os.path.join(tempfile.gettempdir(), "ValorInventoryAgent-Setup.exe")
    try:
        with _get(url, timeout=120) as r, open(path, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        return {"ok": False, "error": f"Download failed: {e}"}
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"])
    threading.Thread(target=lambda: (time.sleep(2), os._exit(0)), daemon=True).start()
    return {"ok": True}
