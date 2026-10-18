"""Make way for the person at a Windows PC: inside its WSL, pause this user's jobs while the Windows desktop is used
and resume them when it is not (stdlib only, Python 3.9+).

    python3 wsl_yield.py   # keeps going; cron restarts it when it is not running (README)

Windows is "in use" while a program of ALWAYS runs there (a driving simulator, used with a wheel and not the keyboard)
or while its desktop had keyboard or mouse input (on the PC or over remote desktop) in at least ACTIVE of the last 30
20-second slots (10 min): a remote desktop window left open elsewhere sends a stray mouse move now and then, someone
working fills most slots. Without the slot count (desktop_idle.pyw not running): input in the last IDLE_MIN minutes. The
jobs resume once it has not been in use for RESUME_MIN minutes. What Windows does comes from the workstations
dashboard (agent.py's "desktop" of the Windows host, polled every minute), as WSL started by a background task cannot
run Windows programs itself. When the dashboard cannot be read, nothing changes.

The jobs are this user's processes whose command line matches JOBS (python, ffmpeg, yt-dlp, rsync, ...), except the
dashboard's agent and this script. Paused with SIGSTOP and resumed with SIGCONT, so they lose nothing; a job started
while Windows is in use is paused at the next check. STATE (~/.wsl_yield.json) says what is paused and why, for the
pipeline status page.
"""
import json
import os
import re
import signal
import time
import urllib.request

DASH = os.environ.get('DASH', 'http://131.155.233.55:8090')
HOST = os.environ.get('YIELD_HOST', 'win5090')                   # the Windows machine in the dashboard
ALWAYS = {'UXDEngine-Win64-Shipping.exe', 'SCANeRstudio_Visual.exe'}  # SCANeR's 3D engine: a simulation is running
IDLE_MIN, RESUME_MIN, EVERY_S, ACTIVE = 10, 10, 30, 6
JOBS = re.compile(r'(^|/)(python3?(\.\d+)?|ffmpeg|ffprobe|yt-dlp|rsync|node)( |$)')
KEEP = re.compile(r'compute-dash/agent\.py|wsl_yield\.py|\.claude/')
STATE = os.path.expanduser('~/.wsl_yield.json')


def in_use(status):
    """(True/False, why) for the Windows host in the dashboard's status, or (None, why) when it cannot tell."""
    m = next((m for m in status if m.get('name') == HOST), None)
    if not m or not m.get('ok') or time.time() - m.get('polled', 0) > 300 or m.get('desktop') is None:
        return None, f'no recent desktop status of {HOST}'
    d = m['desktop']
    running = sorted(ALWAYS & set(d.get('apps') or []))
    if running:
        return True, f'{", ".join(running)} running on {HOST}'
    if d.get('active') is not None:
        if d['active'] >= ACTIVE:
            return True, f'{HOST} desktop used: input in {d["active"]} of the last {d.get("slots")} 20 s slots'
        return False, f'{HOST} desktop hardly used: input in {d["active"]} of the last {d.get("slots")} 20 s slots'
    if d.get('idle_min') is not None and d['idle_min'] < IDLE_MIN:
        return True, f'{HOST} desktop used {d["idle_min"]} min ago'
    if d.get('idle_min') is None:
        return False, f'nobody on {HOST}'
    return False, f'{HOST} desktop not used for {IDLE_MIN} min'


def jobs():
    """{pid: command} of this user's job processes."""
    out, me = {}, os.getpid()
    for pid in filter(str.isdigit, os.listdir('/proc')):
        try:
            if int(pid) == me or os.stat(f'/proc/{pid}').st_uid != os.getuid():
                continue
            cmd = open(f'/proc/{pid}/cmdline', 'rb').read().replace(b'\0', b' ').decode(errors='replace').strip()
        except OSError:
            continue
        if JOBS.search(cmd.split(' -c ')[0][:200]) and not KEEP.search(cmd):
            out[int(pid)] = cmd[:120]
    return out


def stopped(pid):
    try:
        return open(f'/proc/{pid}/stat').read().rsplit(')', 1)[1].split()[0] in ('T', 't')
    except OSError:
        return False


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):  # first start: jobs paused by hand are taken over, to be resumed with the rest
        return dict(paused=False, pids=[p for p in jobs() if stopped(p)], last_used=0, why='')


def save(s):
    with open(STATE + '.tmp', 'w') as f:
        json.dump(s, f)
    os.replace(STATE + '.tmp', STATE)


def signal_all(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def step(s, use, why, now):
    """The next state; pauses or resumes jobs as needed."""
    if use is None:  # ponytail: jobs stay paused while the dashboard is down; resume by hand if that lasts (README)
        return dict(s, checked=now)
    if use:
        found = jobs()
        signal_all(found, signal.SIGSTOP)
        pids = sorted(set(s.get('pids', [])) | set(found))
        return dict(s, paused=True, pids=pids, last_used=now, why=why, since=s.get('since') if s.get('paused') else now,
                    checked=now)
    if (s.get('paused') or s.get('pids')) and now - s.get('last_used', 0) >= RESUME_MIN * 60:
        signal_all(s.get('pids', []), signal.SIGCONT)
        return dict(s, paused=False, pids=[], why=why, since=None, checked=now)
    return dict(s, why=why if not s.get('paused') else s.get('why'), checked=now)


def main():
    s = load()
    while True:
        try:
            with urllib.request.urlopen(DASH + '/status.json', timeout=20) as r:
                use, why = in_use(json.load(r))
        except (OSError, ValueError) as e:
            use, why = None, f'dashboard not readable: {e}'
        new = step(s, use, why, time.time())
        if new.get('paused') != s.get('paused'):
            print(time.strftime('%Y-%m-%d %H:%M:%S'), 'paused' if new['paused'] else 'resumed',
                  len(new['pids'] or s.get('pids', [])), 'jobs:', why, flush=True)
        s = new
        save(s)
        time.sleep(EVERY_S)


def _check():
    now = 1_000_000.0
    st = [dict(name=HOST, ok=True, polled=now, desktop=dict(idle_min=None, apps=['x.exe']))]
    real = time.time
    time.time = lambda: now
    try:
        assert in_use(st)[0] is False  # nobody logged in
        st[0]['desktop'] = dict(idle_min=3, apps=[])
        assert in_use(st)[0] is True  # used 3 min ago
        st[0]['desktop'] = dict(idle_min=40, apps=['UXDEngine-Win64-Shipping.exe'])
        assert in_use(st)[0] is True  # a simulation, with nobody at the keyboard
        st[0]['desktop'] = dict(idle_min=40, apps=['SCANeRstudio.exe'])
        assert in_use(st)[0] is False  # the editor left open
        st[0]['desktop'] = dict(idle_min=0, active=2, slots=30, apps=[])
        assert in_use(st)[0] is False  # a stray mouse move over a remote desktop window just now
        st[0]['desktop'] = dict(idle_min=0, active=25, slots=30, apps=[])
        assert in_use(st)[0] is True  # someone working
        st[0]['polled'] = now - 600
        assert in_use(st)[0] is None  # the dashboard's news is too old
    finally:
        time.time = real
    s = dict(paused=True, pids=[], last_used=now - 300)
    assert step(s, False, 'free', now)['paused'] is True  # not yet RESUME_MIN
    assert step(dict(s, pids=[999999999]), False, 'free', now + 600)['paused'] is False
    assert step(s, False, 'free', now + 600)['paused'] is False  # its jobs ended meanwhile: not paused any more
    assert step(s, None, '?', now) == dict(s, checked=now)
    assert JOBS.search('/home/crowd/crowd-routine/.venv/bin/python -u run_channels.py')
    assert JOBS.search('rsync -a src/ dst/') and not JOBS.search('bash -c cd ~/checks')
    assert KEEP.search('/home/crowd/compute-dash/.venv/bin/python compute-dash/agent.py')


if __name__ == '__main__':
    _check()
    main()
