"""Small widgets for this Mac's screen, served on 127.0.0.1:8091 (stdlib only, Python 3.9+):

    /          the web pages this user runs on localhost: link, description, start time, a stop button
    /machines  a compact view of the workstations dashboard (DASH, its /status.json and /config.json passed through)
    /active    whether anyone can see the screen (this user's session on screen, unlocked, display on)
    /layout    where each widget's window is (POST); /above?name=X: where the widget before X is, so X docks below it
    /dock.js   the docking code the widgets share

More widgets, data to pass through and names for local pages come from widgets.json (widgets.example.json); the
environment variables DASH and REFRESH override it.

    python3 widget.py   # widgets.sh starts it and opens the widgets as small app windows at the top-left
"""
import html, json, os, plistlib, re, signal, subprocess, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 8091
CONFIG = json.loads((HERE / 'widgets.json').read_text()) if (HERE / 'widgets.json').exists() else {}
DASH = os.environ.get('DASH') or CONFIG.get('dash', 'http://127.0.0.1:8090')
REFRESH = int(os.environ.get('REFRESH') or CONFIG.get('refresh', 3600))  # seconds; 3600 on a laptop (battery)
LAYOUT = {}  # widget name -> x, y, h of its window: each docks below the one before it
# top to bottom: widgets.json "order", else the local pages, the machines, then the "windows" of widgets.json
ORDER = CONFIG.get('order') or ['pages', 'machines', *[w[0] for w in CONFIG.get('windows', [])]]
PAGES = {'/': 'widget_pages.html', '/machines': 'widget_machines.html', **CONFIG.get('pages', {})}
PROXIES = {'/status.json': DASH + '/status.json', '/config.json': DASH + '/config.json', **CONFIG.get('proxies', {})}
NAMES = [  # command-line pattern -> description (before the page's <title>, which can be the same for many)
    (re.escape(str(HERE)) + r'/server\.py', 'Workstations dashboard'),
    (re.escape(str(HERE)) + r'/widget\.py', 'These widgets'),
    *[tuple(n) for n in CONFIG.get('names', [])],
]


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout


def probe(port):
    """(title, is_html) of the page on this port, or None when it does not answer HTTP."""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=0.8) as r:
            body = r.read(20000).decode('utf-8', 'replace')
            ok = r.status < 400 and 'html' in r.headers.get('Content-Type', '')
    except Exception:
        return None
    m = re.search(r'<title>(.*?)</title>', body, re.S | re.I)
    return (html.unescape(m.group(1)).strip() if m else ''), ok


def pages():
    """This user's listening TCP ports that serve a web page, with process, start time and description."""
    out, seen = [], set()
    pid = None
    for line in run('lsof', '-nP', '-iTCP', '-sTCP:LISTEN', '-a', '-u', str(os.getuid()), '-Fpn').splitlines():
        if line.startswith('p'):
            pid = int(line[1:])
        elif line.startswith('n'):
            port = int(line.rsplit(':', 1)[1])
            if port in seen or port == PORT:
                continue
            seen.add(port)
            got = probe(port)
            if not got or not got[1]:
                continue
            cmd = run('ps', '-o', 'command=', '-p', str(pid)).strip()
            started = run('ps', '-o', 'lstart=', '-p', str(pid)).strip()
            desc = next((m.expand(name) for pat, name in NAMES for m in [re.search(pat, cmd)] if m), got[0])
            out.append(dict(port=port, pid=pid, desc=desc or cmd.split('/')[-1][:60], cmd=cmd[:300],
                            started=time.mktime(time.strptime(started, '%a %b %d %H:%M:%S %Y')) if started else None))
    return sorted(out, key=lambda p: p['started'] or 0, reverse=True)


_active = [0.0, True]


def screen_active():
    """True when this user's session is on the screen, logged in and unlocked, and the display is on (macOS ioreg;
    an unknown answer counts as yes, e.g. no IODisplayWrangler). Cached for 2 s."""
    if time.time() - _active[0] < 2:
        return _active[1]
    on = True
    try:
        users = plistlib.loads(subprocess.run(['ioreg', '-n', 'Root', '-d1', '-a'], capture_output=True,
                                              timeout=5).stdout).get('IOConsoleUsers', [])
        me = next((u for u in users if u.get('kCGSSessionUserIDKey') == os.getuid()), None)
        on = bool(me and me.get('kCGSSessionOnConsoleKey') and me.get('kCGSessionLoginDoneKey', True)
                  and not me.get('CGSSessionScreenIsLocked'))
    except Exception:
        pass
    m = re.search(r'"CurrentPowerState"=(\d)', run('ioreg', '-n', 'IODisplayWrangler', '-r', '-d1'))
    on = on and (not m or int(m[1]) >= 4)  # 4: on; lower: dimmed or asleep
    _active[:] = [time.time(), on]
    return on


class Handler(BaseHTTPRequestHandler):
    def send(self, body, ctype='application/json', code=200):
        body = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in PAGES:  # a file here, or a URL (a page another server serves, e.g. the globe)
            src = PAGES[path]
            try:
                if src.startswith('http'):
                    with urllib.request.urlopen(src, timeout=20) as r:
                        page = r.read().decode('utf-8')
                else:
                    page = (HERE / src).read_text()
            except Exception as e:
                return self.send(f'{src}: {e}', 'text/plain', 502)
            for k, v in {'__REFRESH__': str(REFRESH), '__DASH__': DASH, **CONFIG.get('vars', {})}.items():
                page = page.replace(k, v)
            return self.send(page, 'text/html; charset=utf-8')
        if path == '/above':  # ?name=machines: where the widget before it is ({} for the first)
            name = self.path.partition('name=')[2]
            i = ORDER.index(name) if name in ORDER else 0
            return self.send(json.dumps(LAYOUT.get(ORDER[i - 1], {}) if i else {}))
        if path == '/dock.js':
            return self.send((HERE / 'dock.js').read_text(), 'text/javascript')
        if path == '/active':
            return self.send(json.dumps(dict(active=screen_active())))
        if path == '/pages.json':
            return self.send(json.dumps(pages()))
        if path in PROXIES:
            try:
                with urllib.request.urlopen(PROXIES[path], timeout=20) as r:
                    return self.send(r.read())
            except Exception as e:
                return self.send(json.dumps(dict(error=str(e))), code=502)
        self.send('not found', 'text/plain', 404)

    def do_POST(self):
        if self.path.startswith('/layout'):
            LAYOUT[self.path.partition('name=')[2]] = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            return self.send('{}')
        if self.path != '/stop':
            return self.send('not found', 'text/plain', 404)
        d = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        # only a page still listed (this user's, same pid on the same port) can be stopped
        # ponytail: any local account can reach 127.0.0.1:8091; check the caller's uid if others log in to this Mac
        if not any(p['pid'] == d.get('pid') and p['port'] == d.get('port') for p in pages()):
            return self.send(json.dumps(dict(error='that page is no longer running')), code=409)
        os.kill(d['pid'], signal.SIGTERM)
        return self.send(json.dumps(dict(ok=True)))

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
