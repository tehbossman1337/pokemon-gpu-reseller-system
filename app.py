"""Desktop entry point. Opens the dashboard in its own window (Edge WebView2 on Windows 11).
If the native window can't start, falls back to your browser plus a small control window."""
import sys
import threading
import time
import traceback
import webbrowser

import core
import server


def _log(msg):
    try:
        with open(core.DATA_DIR + "/app.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def fallback_window(url):
    webbrowser.open(url)
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title("Valor Inventory Agent")
        root.geometry("340x130")
        tk.Label(root, text="Valor Inventory Agent is running.\nKeep this window open. Close it to quit.", pady=14).pack()
        tk.Button(root, text="Open dashboard", command=lambda: webbrowser.open(url)).pack()
        root.mainloop()
    except Exception:
        while True:  # no GUI toolkit at all: just keep serving
            time.sleep(3600)


def main():
    _log("starting")
    srv, port = server.make_server(8765)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    import urllib.request
    for _ in range(50):  # wait until the page is really being served before opening the window
        try:
            urllib.request.urlopen(url + "/api/version", timeout=2).read()
            break
        except Exception as e:
            err = repr(e)
            time.sleep(0.2)
    else:
        _log("server did not answer: " + err)
    _log("serving at " + url)
    if "--browser" in sys.argv:
        return fallback_window(url)
    try:
        import webview
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:
            pass
        webview.create_window("Valor Inventory Agent", url, width=1280, height=820, min_size=(900, 600))
        webview.start()
    except Exception:
        _log("native window failed:\n" + traceback.format_exc())
        fallback_window(url)


if __name__ == "__main__":
    main()
