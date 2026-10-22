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

Half, not all (user, 2026-10-22: "use 50% of the GPU"): while the desktop is used, the jobs whose working folder is
KEEP_GOING (the first crowd worker and what it starts) keep going and only the others (the second worker) pause; a
GPU cannot be capped per process in WSL, so one of the two workers is about half. A running simulation (ALWAYS) alike
(user, 2026-10-22). STATE's "keeps" names the folder that keeps going.

Ended, not paused: a job of RESTART is ended while Windows is used, as a paused process keeps its GPU memory, and
started again in its tmux session once Windows is free (and that session has closed); STATE's "restart" lists those.
"""
import json
import os
import re
import signal
import subprocess
import time
import urllib.request

DASH = os.environ.get('DASH', 'http://131.155.233.55:8090')
HOST = os.environ.get('YIELD_HOST', 'win5090')                   # the Windows machine in the dashboard
ALWAYS = {'UXDEngine-Win64-Shipping.exe', 'SCANeRstudio_Visual.exe'}  # SCANeR's 3D engine: a simulation is running
IDLE_MIN, RESUME_MIN, EVERY_S, ACTIVE = 10, 10, 30, 6
JOBS = re.compile(r'(^|/)(python3?(\.\d+)?|ffmpeg|ffprobe|yt-dlp|rsync|node)( |$)')
KEEP = re.compile(r'compute-dash/agent\.py|wsl_yield\.py|\.claude/|sync_segmentation\.py')  # the last: a push
STATE = os.path.expanduser('~/.wsl_yield.json')
KEEP_GOING = os.path.expanduser('~/crowd-routine')  # the worker that keeps going while the desktop is used
# {tmux session: (its job's process, the command it is started again with)}: the jaywalking analysis Shadab and the
# user run held most of the 5090's memory while paused (user, 2026-10-22); it saves each segment and skips the saved
# ones when started again, and its run_six.sh pushes them once the analysis has ended
RESTART = {'alam-crowd': (re.compile(r'scripts/crowd/run_crowd_analysis\.py'),
                          '~/alam/run_six.sh >> ~/alam/kv.log 2>&1')}


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


def folder(pid):
    """The working folder of a process ('' when gone)."""
    try:
        return os.readlink(f'/proc/{pid}/cwd')
    except OSError:
        return ''


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


def tmux(*args):
    """tmux's exit code (1 when it cannot be run)."""
    try:
        return subprocess.run(['tmux', *args], capture_output=True, timeout=20).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def restarted(session):
    """Whether a RESTART job's session was started again (False while its old one is still closing)."""
    if session not in RESTART:
        return True
    if tmux('has-session', '-t', session) == 0:
        return False
    return tmux('new-session', '-d', '-s', session, RESTART[session][1]) == 0


def step(s, use, why, now):
    """The next state; pauses or resumes jobs as needed."""
    if use is None:  # ponytail: jobs stay paused while the dashboard is down; resume by hand if that lasts (README)
        return dict(s, checked=now)
    if use:
        found = jobs()
        end = {p for p, c in found.items() if any(job.search(c) for job, _ in RESTART.values())}
        ended = {n for n, (job, _) in RESTART.items() if any(job.search(found[p]) for p in end)}
        signal_all(end, signal.SIGTERM)
        signal_all(end, signal.SIGCONT)  # a paused one ends too
        found = {p: c for p, c in found.items() if p not in end}
        keep = {p for p in found if folder(p) == KEEP_GOING}
        signal_all(keep, signal.SIGCONT)  # paused by the pause-all of before: going again
        signal_all(set(found) - keep, signal.SIGSTOP)
        pids = sorted((set(s.get('pids', [])) | set(found)) - keep - end)
        return dict(s, paused=True, pids=pids, last_used=now, keeps=KEEP_GOING,
                    restart=sorted(set(s.get('restart', [])) | ended),
                    why=f'{why}; {KEEP_GOING} keeps going (half the GPU)',
                    since=s.get('since') if s.get('paused') else now, checked=now)
    if s.get('restart') and now - s.get('last_used', 0) >= RESUME_MIN * 60:
        s = dict(s, restart=[n for n in s['restart'] if not restarted(n)])
    if (s.get('paused') or s.get('pids')) and now - s.get('last_used', 0) >= RESUME_MIN * 60:
        signal_all(s.get('pids', []), signal.SIGCONT)
        return dict(s, paused=False, pids=[], keeps=None, why=why, since=None, checked=now)
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
    # Windows used (desktop or a simulation): the first worker keeps going (and goes on if paused), the second pauses
    # ... and a RESTART job is ended, then started again once Windows is free and its old session has closed
    global jobs, folder, signal_all, tmux
    real_jobs, real_folder, real_signal, real_tmux = jobs, folder, signal_all, tmux
    sent, calls, alive = [], [], [True]
    jobs = lambda: {1: 'python first', 2: 'python second',  # noqa: E731
                    3: '/home/crowd/alam/crowd-jaywalking/.venv/bin/python -u scripts/crowd/run_crowd_analysis.py'}
    folder = lambda p: KEEP_GOING if p == 1 else KEEP_GOING + '-2'  # noqa: E731
    signal_all = lambda pids, sig: sent.append((sorted(pids), sig))  # noqa: E731
    tmux = lambda *a: calls.append(a) or (0 if a[0] == 'new-session' or alive[0] else 1)  # noqa: E731
    try:
        new = step(dict(paused=True, pids=[1, 2, 3]), True, 'desktop used', now)
        assert sent == [([3], signal.SIGTERM), ([3], signal.SIGCONT), ([1], signal.SIGCONT), ([2], signal.SIGSTOP)]
        assert new['pids'] == [2] and new['keeps'] and new['restart'] == ['alam-crowd'], new
        later = step(new, False, 'free', now + 600)
        assert later['restart'] == ['alam-crowd'] and not any(c[0] == 'new-session' for c in calls)  # still closing
        alive[0] = False
        later = step(later, False, 'free', now + 630)
        assert later['restart'] == [] and calls[-1][:4] == ('new-session', '-d', '-s', 'alam-crowd')
    finally:
        jobs, folder, signal_all, tmux = real_jobs, real_folder, real_signal, real_tmux
    assert JOBS.search('/home/crowd/crowd-routine/.venv/bin/python -u run_channels.py')
    assert JOBS.search('rsync -a src/ dst/') and not JOBS.search('bash -c cd ~/checks')
    assert KEEP.search('/home/crowd/compute-dash/.venv/bin/python compute-dash/agent.py')


if __name__ == '__main__':
    _check()
    main()
