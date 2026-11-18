#!/usr/bin/env python3
"""Mini API (Render) : GET /api/data relance la relève Betclic + build et renvoie data.json avec CORS.
Aucun secret. Cache 2 min pour les cotes, 60 min pour le contexte ESPN."""
import json, os, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get("WORK_DIR", "/tmp/pdj")
NOTES_URL = "https://raw.githubusercontent.com/William6500/paris-du-jour/main/notes.json"
ODDS_TTL, CTX_TTL = 120, 3600
os.makedirs(WORK, exist_ok=True)
lock = threading.Lock()
state = {"t": 0, "data": None, "ctx_t": 0, "err": None}


def run(script):
    r = subprocess.run([sys.executable, os.path.join(HERE, script)], cwd=WORK, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"{script}: {(r.stderr or r.stdout)[-300:]}")
    return r.stdout.strip()


def refresh():
    with lock:
        if state["data"] and time.time() - state["t"] < ODDS_TTL:
            return state["data"]
        try:
            with urllib.request.urlopen(NOTES_URL, timeout=10) as r:
                open(os.path.join(WORK, "notes.json"), "wb").write(r.read())
        except Exception:
            src = os.path.join(HERE, "..", "notes.json")
            if os.path.exists(src):
                open(os.path.join(WORK, "notes.json"), "wb").write(open(src, "rb").read())
        log = [run("fetch_odds.py")]
        if time.time() - state["ctx_t"] > CTX_TTL or not os.path.exists(os.path.join(WORK, "context.json")):
            try:
                log.append(run("enrich_espn.py")); state["ctx_t"] = time.time()
            except Exception as e:
                log.append(f"ESPN ignoré : {e}")
        log.append(run("build.py"))
        data = json.load(open(os.path.join(WORK, "data.json"), encoding="utf-8"))
        data["live"] = True
        data["log"] = log
        state.update(t=time.time(), data=data)
        return data


class H(BaseHTTPRequestHandler):
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self):
        self.send_response(204); self.cors(); self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            body, code = b'{"ok":true}', 200
        elif self.path.startswith("/api/data"):
            try:
                body, code = json.dumps(refresh(), ensure_ascii=False).encode(), 200
            except Exception as e:
                body, code = json.dumps({"error": str(e)[:400]}).encode(), 502
        else:
            body, code = b'{"service":"paris-du-jour","endpoints":["/api/data","/health"]}', 200
        self.send_response(code); self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"paris-du-jour API sur le port {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
