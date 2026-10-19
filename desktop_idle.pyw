"""Windows, in the desktop session: every 20 s, write how long ago the last keyboard or mouse input was (at the PC or
over remote desktop) and in how many of the last 30 such 20 s slots (10 min) there was any, to desktop_idle.json next
to this file, for agent.py. A remote desktop window left open elsewhere sends a stray mouse move now and then (Faye's
RustDesk, 2026-10-18): one slot, where someone working fills most of them. Only a program inside the desktop session
can tell (GetLastInputInfo); quser shows the console session as "none" idle all the time. Started by a scheduled task
at logon, "only when the user is logged on" (README); pythonw, so it has no window. Standard library only.
"""
import collections
import ctypes
import json
import os
import time


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint32)]


OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'desktop_idle.json')
ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint32


def last_input():
    """(GetLastInputInfo's tick of the last input, s since then)."""
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return info.dwTime, ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000  # wraps: 49.7 d


slots, before = collections.deque(maxlen=30), None  # one per 20 s: any input since the one before
while True:
    tick, idle = last_input()
    if before is not None:
        slots.append(tick != before)
    before = tick
    with open(OUT + '.tmp', 'w') as f:
        json.dump(dict(t=time.time(), idle_s=round(idle), active=sum(slots), slots=len(slots)), f)
    for _ in range(10):  # agent.py may be reading it just then: Windows refuses to replace an open file
        try:
            os.replace(OUT + '.tmp', OUT)
            break
        except PermissionError:
            time.sleep(0.2)
    time.sleep(20)
