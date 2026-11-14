"""Windows side of wsl_yield.py: pause background work on the Windows PC itself while the PC is in use, and resume it
when wsl_yield.py resumes its WSL jobs (Python 3.9+ with psutil).

    pyw -3.13 win_yield.pyw   # keeps going; a scheduled task starts it (README)

The work paused is every process of a background session (session 0: scheduled tasks, SSH) whose program or command
line is in one of the PROJECTS folders, and everything those started (a build's msbuild, cl.exe and git, a CARLA
server); what someone starts at the desk is never paused. Paused with NtSuspendProcess (psutil's suspend) and resumed
with psutil's resume, so nothing is lost, though a paused program keeps its memory (also GPU memory). A job started
while the PC is in use is paused at the next check (EVERY_S).

When is decided by wsl_yield.py (in WSL, which pauses its own jobs at the same moments): it writes DECISION every 30 s.
When DECISION is older than STALE_S (WSL down), the work is resumed: nobody else would ever resume it. OUT says what is
paused, for people, and lists the programs running outside the PROJECTS' work: wsl_yield.py's ALWAYS (a simulation:
someone at the PC) must not count a CARLA server of this background work as someone driving.

Runs elevated (scheduled task "Run with highest privileges"), as the account the background work runs as: a process
started with highest privileges can only be paused by one that has them too.
"""
import ctypes
import json
import os
import time

import psutil

# the folders of the background work to pause (user, 2026-11-14: the coupled-carla project's CARLA/UE5 build)
PROJECTS = [r'C:\cc58']
DECISION = r'C:\Users\Public\wsl_yield.json'  # written by wsl_yield.py: {"t", "paused", "why"}
OUT = r'C:\Users\Public\win_yield.json'  # this script's: {"t", "paused", "why", "jobs", "apps"}
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'win_yield.log')
EVERY_S, STALE_S = 20, 600
NEVER = {'conhost.exe'}  # the console window of a paused job: left alone


def log(*args):
    with open(LOG, 'a') as f:
        print(time.strftime('%Y-%m-%d %H:%M:%S'), *args, file=f)


def session(pid):
    """The Windows session a process runs in (0: background), None when it cannot be told."""
    sid = ctypes.c_ulong()
    return sid.value if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid)) else None


def in_project(info):
    text = ' '.join([info.get('exe') or ''] + list(info.get('cmdline') or [])).lower().replace('/', '\\')
    return any(p.lower().rstrip('\\') + '\\' in text for p in PROJECTS)


def work(procs, sessions):
    """{pid: (name, create_time)} of the PROJECTS' background work, out of procs ({pid: info}) and sessions
    ({pid: session}): the processes of session 0 in a PROJECTS folder and all they started."""
    kids = {}
    for pid, i in procs.items():
        kids.setdefault(i.get('ppid'), []).append(pid)
    todo = [p for p, i in procs.items() if sessions.get(p) == 0 and in_project(i)]
    found = {}
    while todo:
        pid = todo.pop()
        if pid in found or pid == os.getpid():
            continue
        found[pid] = procs[pid]
        # a child is younger than its parent: an older one has a reused number of a parent that has ended
        born = procs[pid].get('create_time') or 0
        todo += [k for k in kids.get(pid, []) if (procs[k].get('create_time') or 0) >= born]
    return {p: (i.get('name') or '?', i.get('create_time')) for p, i in found.items()
            if (i.get('name') or '').lower() not in NEVER}


def decision(now):
    """(paused, why) from wsl_yield.py's DECISION; not paused when it is missing or too old."""
    try:
        with open(DECISION) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False, 'no word from wsl_yield.py'
    if now - d.get('t', 0) > STALE_S:
        return False, f'no word from wsl_yield.py for {int((now - d.get("t", 0)) // 60)} min'
    return bool(d.get('paused')), d.get('why') or ''


def write(s):
    with open(OUT + '.tmp', 'w') as f:
        json.dump(s, f)
    for _ in range(10):  # wsl_yield.py may be reading it just then: Windows refuses to replace an open file
        try:
            os.replace(OUT + '.tmp', OUT)
            return
        except PermissionError:
            time.sleep(0.2)


def same(pid, ctime):
    """The psutil Process of pid if it is still the one started at ctime, else None."""
    try:
        p = psutil.Process(pid)
        return p if abs(p.create_time() - ctime) < 1 else None
    except psutil.Error:
        return None


def suspend(pid, ctime, how):
    p = same(pid, ctime)
    try:
        if p:
            getattr(p, how)()
            return True
    except psutil.Error:
        pass
    return False


def tick(held, was):
    """One check: pauses or resumes the work as wsl_yield.py says. held: {pid: [name, create_time]} paused by this;
    was: paused at the check before. The new held and paused."""
    now = time.time()
    fields = ['pid', 'ppid', 'name', 'exe', 'cmdline', 'create_time']
    procs = {p.info['pid']: p.info for p in psutil.process_iter(fields)}
    jobs = work(procs, {pid: session(pid) for pid in procs})
    paused, why = decision(now)
    if paused:
        for pid, (name, ctime) in jobs.items():
            if pid not in held and suspend(pid, ctime, 'suspend'):
                held[pid] = [name, ctime]
        held = {p: v for p, v in held.items() if same(p, v[1])}
    elif held:
        for pid, (name, ctime) in held.items():
            suspend(pid, ctime, 'resume')
        held = {}
    if paused != was:
        log('paused' if paused else 'resumed', len(held) if paused else len(jobs), 'jobs:', why)
    write(dict(t=now, paused=paused, why=why, held=held, jobs=sorted({n for n, _ in jobs.values()}),
               apps=sorted({i.get('name') for p, i in procs.items() if p not in jobs and i.get('name')})))
    return held, paused


def main():
    try:
        with open(OUT) as f:
            held = {int(k): v for k, v in json.load(f).get('held', {}).items()}  # paused before a restart of this
    except (OSError, ValueError):
        held = {}
    was = None
    while True:
        held, was = tick(held, was)
        time.sleep(EVERY_S)


def _check():
    procs = {1: dict(ppid=0, name='svchost.exe', exe=r'C:\Windows\system32\svchost.exe', cmdline=[], create_time=1),
             2: dict(ppid=1, name='cmd.exe', exe=r'C:\Windows\system32\cmd.exe',
                     cmdline=['cmd.exe', '/c', r'C:\cc58\engine_build.cmd'], create_time=2),
             3: dict(ppid=2, name='MSBuild.exe', exe=r'C:\VS\MSBuild.exe', cmdline=['msbuild', 'UE5.vcxproj'],
                     create_time=3),
             4: dict(ppid=3, name='cl.exe', exe=r'C:\VS\cl.exe', cmdline=None, create_time=4),
             5: dict(ppid=2, name='conhost.exe', exe=r'C:\Windows\system32\conhost.exe', cmdline=None, create_time=3),
             6: dict(ppid=3, name='old.exe', exe=r'C:\x\old.exe', cmdline=None, create_time=0.5),  # a reused number
             7: dict(ppid=1, name='UnrealEditor.exe', exe=r'C:\cc58\UnrealEngine5_carla\UnrealEditor.exe',
                     cmdline=None, create_time=5),  # started at the desk
             8: dict(ppid=1, name='python.exe', exe=r'C:\py\python.exe', cmdline=['python', 'C:/cc58/carla/run.py'],
                     create_time=6)}
    sessions = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 1, 8: 0}
    assert sorted(work(procs, sessions)) == [2, 3, 4, 8], work(procs, sessions)
    assert not in_project(dict(exe=r'C:\cc580\x.exe')) and in_project(dict(cmdline=['x', 'c:/CC58/y']))


if __name__ == '__main__':
    _check()
    main()
