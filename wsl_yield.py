"""Make way for the person at a Windows PC: inside its WSL, pause this user's jobs while the Windows desktop is used
and resume them when it is not (stdlib only, Python 3.9+).

    python3 wsl_yield.py   # keeps going; cron restarts it when it is not running (README)

Windows is "in use" while a program of ALWAYS runs there (a driving simulator, used with a wheel and not the keyboard)
or while its desktop had keyboard or mouse input (on the PC or over remote desktop) in at least ACTIVE of the last 30
20-second slots (10 min): a remote desktop window left open elsewhere sends a stray mouse move now and then, someone
working fills most slots. Without the slot count (desktop_idle.pyw not running): input in the last IDLE_MIN minutes.
The jobs resume once it has not been in use for RESUME_MIN minutes. What Windows does comes from the workstations
dashboard (agent.py's "desktop" of the Windows host, polled every minute), as WSL started by a background task cannot
run Windows programs itself. When the dashboard cannot be read, nothing changes.

The jobs are this user's processes whose command line matches JOBS (python, ffmpeg, yt-dlp, rsync, ...), except the
dashboard's agent and this script. Paused with SIGSTOP and resumed with SIGCONT, so they lose nothing; a job started
while Windows is in use is paused at the next check. STATE (~/.wsl_yield.json) says what is paused and why, for the
pipeline status page.

The crowd workers pause too (2026-11-10: pause them when Windows is used, not just the jaywalking analysis; from
2026-10-22 they kept going). To let them keep going again, put their folders back in KEEP_GOING: the jobs whose working
folder is one of KEEP_GOING (a crowd worker and what it starts) keep going while Windows is used and only the others
pause. STATE's "keeps" lists those folders. A pause longer than 45 min makes run_channels.py stop the batch it was
waiting on (its silence check counts the paused time) and start it again later.

A simulation (an ALWAYS program, or FLAG made by "CARLA - free GPU.bat" on the Windows desktop before starting one,
2026-11-10) frees the GPU instead of pausing: a paused process keeps its GPU memory, so each crowd worker gets its
run_channels.py pause file (made here, removed again with the resume) and its batch with the GPU models in it
(propose_segments.py and its "python -c" helpers) is ended; run_channels.py waits while the pause file is there and
does the batch again later. FLAG lasts FLAG_H hours, or until the simulation it was made for has ended.

Work on the Windows side (2026-11-14: the coupled-carla project's CARLA build in C:/cc58, run by scheduled tasks
and over SSH) pauses at the same moments: this writes WIN_DECISION every EVERY_S, and win_yield.pyw on Windows pauses
and resumes that work by it. win_yield.pyw writes WIN_JOBS with the programs running outside that work, which this
uses in place of the dashboard's for ALWAYS, so a CARLA server of the background work does not count as a simulation
(someone at the PC). In WSL, CARLA servers and SUMO pause like the other jobs (JOBS).

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
ALWAYS = {'UXDEngine-Win64-Shipping.exe', 'SCANeRstudio_Visual.exe',  # SCANeR's 3D engine: a simulation is running
          'CarlaUnreal-Win64-Shipping.exe'}  # CARLA 0.10.0 server (user, 2026-11-09: pause while CARLA runs)
# ACTIVE 12 (user, 2026-10-26; was 6): with nobody at win5090, only a RustDesk viewer connected, single stray inputs
# came every 2-4 min, 6 slots in 10 min now and then, and the jaywalking analysis was ended 9 times in an evening
IDLE_MIN, RESUME_MIN, EVERY_S, ACTIVE = 10, 10, 30, 12
# also CARLA's Linux servers and SUMO (the coupled-carla project in ~/coupled-carla, 2026-11-14)
JOBS = re.compile(r'(^|/)(python3?(\.\d+)?|ffmpeg|ffprobe|yt-dlp|rsync|node'
                  r'|CarlaUE4-Linux-Shipping|CarlaUnreal-Linux-Shipping|sumo|sumo-gui|netconvert)( |$)')
KEEP = re.compile(r'compute-dash/agent\.py|wsl_yield\.py|\.claude/|sync_segmentation\.py')  # the last: a push
STATE = os.path.expanduser('~/.wsl_yield.json')
FLAG, FLAG_H = '/mnt/c/Users/Public/carla_soon', 4  # "CARLA - free GPU.bat" (Public Desktop): a simulation is coming
WIN_DECISION = '/mnt/c/Users/Public/wsl_yield.json'  # for win_yield.pyw: {"t", "paused", "why"}
WIN_JOBS, WIN_FRESH_S = '/mnt/c/Users/Public/win_yield.json', 120  # win_yield.pyw's: its "apps", "jobs" and "held"
GPU_JOB = re.compile(r'propose_segments\.py| -c ')  # a crowd worker's batch and its GPU models (YOLO, OCR, CLIP)
# the folders of jobs that keep going while Windows is used: none since 2026-11-10 (was ~/crowd-routine and
# ~/crowd-routine-2, the crowd workers)
KEEP_GOING = []
WORKERS = [os.path.expanduser(f) for f in ('~/crowd-routine', '~/crowd-routine-2')]  # the crowd workers' folders
PAUSE_FILES = [os.path.join(w, '_output/proposals/pause') for w in WORKERS]  # run_channels.py waits while one is there
# {tmux session: (its job's process, the command it is started again with)}: the jaywalking analysis Shadab and the
# user run held most of the 5090's memory while paused (user, 2026-10-22); it saves each segment and skips the saved
# ones when started again, and its run_six.sh pushes them once the analysis has ended
# since 2026-11-09 the 16-bit run (Shadab, 2026-11-10; was alam-crowd with run_six.sh >> kv.log)
RESTART = {'alam-crowd16': (re.compile(r'scripts/crowd/run_crowd_analysis\.py'),
                            '~/alam/run_16bit.sh >> ~/alam/run16.log 2>&1')}


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


def simulation(status, s, now):
    """(True/False, why): a simulation runs on HOST or FLAG says one is coming. FLAG is removed once the simulation
    seen since it was made has ended, or after FLAG_H hours. Notes in s when an ALWAYS program was last seen."""
    m = next((m for m in status or [] if m.get('name') == HOST), None)
    running = sorted(ALWAYS & set(((m or {}).get('desktop') or {}).get('apps') or []))
    if running:
        s['sim_seen'] = now
        return True, f'{", ".join(running)} running on {HOST}'
    try:
        made = os.path.getmtime(FLAG)
    except OSError:
        return False, ''
    if now - made > FLAG_H * 3600 or s.get('sim_seen', 0) > made:
        try:
            os.remove(FLAG)
        except OSError:
            pass
        return False, ''
    return True, f'a simulation is coming on {HOST} (CARLA - free GPU.bat)'


def pause_workers():
    """Makes the crowd workers' pause files that are not there yet; the ones made."""
    made = []
    for f in PAUSE_FILES:
        if os.path.isdir(os.path.dirname(f)) and not os.path.exists(f):
            with open(f, 'w') as fh:
                fh.write('wsl_yield.py: a simulation on win5090\n')
            made.append(f)
    return made


def unpause_workers(made):
    for f in made:
        try:
            os.remove(f)
        except OSError:
            pass


def windows_side(status, now, path=WIN_JOBS):
    """Puts win_yield.pyw's programs outside its background work in place of the HOST's apps in status (when it wrote
    them in the last WIN_FRESH_S); win_yield.pyw's state, {} when there is none."""
    try:
        with open(path) as f:
            w = json.load(f)
    except (OSError, ValueError):
        return {}
    if now - w.get('t', 0) > WIN_FRESH_S:
        return {}
    m = next((m for m in status or [] if m.get('name') == HOST), None)
    if m and m.get('desktop') is not None and w.get('apps') is not None:
        m['desktop'] = dict(m['desktop'], apps=w['apps'])
    return w


def tell_windows(s, now):
    """WIN_DECISION for win_yield.pyw: paused or not, as the jobs here."""
    try:
        with open(WIN_DECISION + '.tmp', 'w') as f:
            json.dump(dict(t=now, paused=bool(s.get('paused')), why=s.get('why') or ''), f)
        os.replace(WIN_DECISION + '.tmp', WIN_DECISION)
    except OSError:  # win_yield.pyw reading it just then: next time
        pass


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


def step(s, use, why, now, sim=False):
    """The next state; pauses or resumes jobs as needed. sim: a simulation, which frees the GPU (see above)."""
    if use is None:  # ponytail: jobs stay paused while the dashboard is down; resume by hand if that lasts (README)
        return dict(s, checked=now)
    if use:
        found = jobs()
        end = {p for p, c in found.items() if any(job.search(c) for job, _ in RESTART.values())}
        ended = {n for n, (job, _) in RESTART.items() if any(job.search(found[p]) for p in end)}
        signal_all(end, signal.SIGTERM)
        signal_all(end, signal.SIGCONT)  # a paused one ends too
        found = {p: c for p, c in found.items() if p not in end}
        keep = {p for p in found if folder(p) in KEEP_GOING}
        made = list(s.get('made_pause') or [])
        if sim:  # the workers wait on their pause files, their batches end: the GPU memory is freed
            made += pause_workers()
            workers = {p for p in found if folder(p) in WORKERS}
            gpu = {p for p in workers if GPU_JOB.search(found[p])}
            keep |= workers - gpu
            signal_all(gpu, signal.SIGTERM)
            signal_all(gpu, signal.SIGCONT)
            found = {p: c for p, c in found.items() if p not in gpu}
        signal_all(keep, signal.SIGCONT)  # paused by the pause-all of before: going again
        signal_all(set(found) - keep, signal.SIGSTOP)
        pids = sorted((set(s.get('pids', [])) | set(found)) - keep - end)
        return dict(s, paused=True, pids=pids, last_used=now, keeps=KEEP_GOING,
                    restart=sorted(set(s.get('restart', [])) | ended),
                    why=(f'{why}; the crowd workers wait (pause file), their GPU freed' if sim
                         else f'{why}; the crowd workers keep going' if KEEP_GOING else why),
                    sim=sim, made_pause=made,
                    since=s.get('since') if s.get('paused') else now, checked=now)
    if s.get('restart') and now - s.get('last_used', 0) >= RESUME_MIN * 60:
        s = dict(s, restart=[n for n in s['restart'] if not restarted(n)])
    if s.get('made_pause') and now - s.get('last_used', 0) >= RESUME_MIN * 60:
        unpause_workers(s['made_pause'])
        s = dict(s, made_pause=[], sim=False)
    if (s.get('paused') or s.get('pids')) and now - s.get('last_used', 0) >= RESUME_MIN * 60:
        signal_all(s.get('pids', []), signal.SIGCONT)
        return dict(s, paused=False, pids=[], keeps=None, why=why, since=None, checked=now, sim=False)
    return dict(s, why=why if not s.get('paused') else s.get('why'), checked=now)


def main():
    s = load()
    while True:
        status = None
        try:
            with urllib.request.urlopen(DASH + '/status.json', timeout=20) as r:
                status = json.load(r)
            win = windows_side(status, time.time())
            use, why = in_use(status)
        except (OSError, ValueError) as e:
            use, why, win = None, f'dashboard not readable: {e}', {}
        sim, swhy = simulation(status, s, time.time())
        if sim:
            use, why = True, swhy
        new = step(s, use, why, time.time(), sim)
        if new.get('sim') and not s.get('sim'):
            print(time.strftime('%Y-%m-%d %H:%M:%S'), 'freeing the GPU:', why, flush=True)
        if new.get('paused') != s.get('paused'):
            print(time.strftime('%Y-%m-%d %H:%M:%S'), 'paused' if new['paused'] else 'resumed',
                  len(new['pids'] or s.get('pids', [])), 'jobs:', why, flush=True)
        s = dict(new, windows=dict(held=len(win.get('held') or {}), jobs=win.get('jobs') or []) if win else None)
        save(s)
        tell_windows(s, time.time())
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
        st[0]['desktop'] = dict(idle_min=0, active=6, slots=30, apps=[])
        assert in_use(st)[0] is False  # stray inputs every 2-4 min with only a RustDesk viewer (2026-10-26)
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
    # Windows used (desktop or a simulation): both crowd workers pause like any other job (since 2026-11-10)
    # ... and a RESTART job is ended, then started again once Windows is free and its old session has closed
    global jobs, folder, signal_all, tmux
    real_jobs, real_folder, real_signal, real_tmux = jobs, folder, signal_all, tmux
    sent, calls, alive = [], [], [True]
    jobs = lambda: {1: 'python first', 2: 'python second', 4: 'python a check',  # noqa: E731
                    3: '/home/crowd/alam/crowd-jaywalking/.venv/bin/python -u scripts/crowd/run_crowd_analysis.py'}
    folder = lambda p: {1: WORKERS[0], 2: WORKERS[1]}.get(p, '/home/crowd/checks')  # noqa: E731
    signal_all = lambda pids, sig: sent.append((sorted(pids), sig))  # noqa: E731
    tmux = lambda *a: calls.append(a) or (0 if a[0] == 'new-session' or alive[0] else 1)  # noqa: E731
    try:
        new = step(dict(paused=True, pids=[1, 2, 3]), True, 'desktop used', now)
        assert sent == [([3], signal.SIGTERM), ([3], signal.SIGCONT), ([], signal.SIGCONT),
                        ([1, 2, 4], signal.SIGSTOP)]
        assert new['pids'] == [1, 2, 4] and new['keeps'] == [] and new['restart'] == ['alam-crowd16'], new
        assert 'keep going' not in new['why'], new
        later = step(new, False, 'free', now + 600)
        assert later['restart'] == ['alam-crowd16'] and not any(c[0] == 'new-session' for c in calls)  # still closing
        alive[0] = False
        later = step(later, False, 'free', now + 630)
        assert later['restart'] == [] and calls[-1][:4] == ('new-session', '-d', '-s', 'alam-crowd16')
    finally:
        jobs, folder, signal_all, tmux = real_jobs, real_folder, real_signal, real_tmux
    # a simulation: the workers' batches and GPU models end, their runners wait on pause files, removed with the resume
    global pause_workers, unpause_workers
    real_pw, real_uw = pause_workers, unpause_workers
    sent, removed = [], []
    jobs = lambda: {1: 'python -u run_channels.py', 2: 'python -u propose_segments.py --channel x',  # noqa: E731
                    3: '/x/python -c  import json, sys, easyocr, torch', 4: 'python a check'}
    folder = lambda p: WORKERS[0] if p < 4 else '/home/crowd/checks'  # noqa: E731
    signal_all = lambda pids, sig: sent.append((sorted(pids), sig))  # noqa: E731
    pause_workers = lambda: ['/w/pause']  # noqa: E731
    unpause_workers = lambda made: removed.extend(made)  # noqa: E731
    try:
        new = step(dict(paused=False, pids=[]), True, 'CARLA', now, sim=True)
        assert ([2, 3], signal.SIGTERM) in sent and ([1], signal.SIGCONT) in sent and ([4], signal.SIGSTOP) in sent
        assert new['pids'] == [4] and new['made_pause'] == ['/w/pause'] and new['sim'], new
        assert step(new, False, 'free', now + 300)['made_pause'] == ['/w/pause'] and not removed  # not yet
        later = step(new, False, 'free', now + 600)
        assert removed == ['/w/pause'] and later['made_pause'] == [] and not later['paused'], later
    finally:
        jobs, folder, signal_all = real_jobs, real_folder, real_signal
        pause_workers, unpause_workers = real_pw, real_uw
    assert GPU_JOB.search('/x/python -c  import sys, time, cv2') and not GPU_JOB.search('python -u run_channels.py')
    assert JOBS.search('/home/crowd/crowd-routine/.venv/bin/python -u run_channels.py')
    assert JOBS.search('rsync -a src/ dst/') and not JOBS.search('bash -c cd ~/checks')
    assert JOBS.search('/home/crowd/carlaCache/0.9.16/Carla/CarlaUE4/Binaries/Linux/CarlaUE4-Linux-Shipping CarlaUE4')
    assert JOBS.search('/usr/bin/sumo -c city.sumocfg') and not JOBS.search('/usr/bin/sumoxyz')
    st = [dict(name=HOST, desktop=dict(apps=['CarlaUnreal-Win64-Shipping.exe']))]
    assert windows_side(st, now, '/nonexistent') == {}
    assert st[0]['desktop']['apps'] == ['CarlaUnreal-Win64-Shipping.exe']
    assert KEEP.search('/home/crowd/compute-dash/.venv/bin/python compute-dash/agent.py')


if __name__ == '__main__':
    _check()
    main()
