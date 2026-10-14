"""Workstations dashboard: polls agent.py on each machine of machines.json every INTERVAL s and serves the page (/),
/status.json (the machines' latest numbers) and /config.json (the title and the machines' addresses and notes).

    python3 server.py   # http://<this machine>:8090
"""
import json, subprocess, sys, threading, time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT, INTERVAL, HISTORY = 8090, 60, 180  # 180 samples x 60 s = 3 h of sparkline
CONFIG = json.loads((HERE / "machines.json").read_text())  # see machines.example.json
SSH = ["ssh", "-T", "-i", str(Path.home() / ".ssh/id_dash"), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
       "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]
# "local": agent.py runs here; else ssh with the machine's "ssh" arguments, to a key that is restricted to a forced
# command running agent.py there (README), so no remote command is sent
MACHINES = {m["name"]: [sys.executable, str(HERE / "agent.py")] if m.get("local") else SSH + m["ssh"]
            for m in CONFIG["machines"]}
PUBLIC = json.dumps({**{k: v for k, v in CONFIG.items() if k != "machines"},  # never the logins
                     "machines": [{k: v for k, v in m.items() if k not in ("ssh", "local")} for m in CONFIG["machines"]]})
STATE = {}


def poll(name, cmd):
    hist = deque(maxlen=HISTORY)
    while True:
        t, r = time.time(), None
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=45, stdin=subprocess.DEVNULL)
            d = json.loads(r.stdout)
        except Exception as e:
            msg = (r.stderr.strip().splitlines() or [""])[-1] if r else ""
            d = dict(STATE.get(name, {}), ok=False, error=(msg or str(e))[:200])  # keep last good numbers
        else:
            g = d["gpus"][0] if d["gpus"] else {}
            hist.append([round(t), d["cpu"], g.get("util"), round(100 - 100 * d["mem_avail"] / d["mem_total"])])
            d["last_ok"] = t
        d.update(name=name, polled=t, interval=INTERVAL, hist=list(hist))
        STATE[name] = d
        time.sleep(max(5, INTERVAL - (time.time() - t)))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/status.json":
            body, ctype = json.dumps([STATE[n] for n in MACHINES if n in STATE]).encode(), "application/json"
        elif path == "/config.json":
            body, ctype = PUBLIC.encode(), "application/json"
        elif path in ("/", "/index.html"):
            body, ctype = (HERE / "index.html").read_bytes(), "text/html; charset=utf-8"
        else:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    for n, c in MACHINES.items():
        threading.Thread(target=poll, args=(n, c), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
