"""Windows, in the desktop session: every 20 s, write how long ago the last keyboard or mouse input was (at the PC or
over remote desktop) to desktop_idle.json next to this file, for agent.py. Only a program inside the desktop session
can tell (GetLastInputInfo); quser shows the console session as "none" idle all the time. Started by a scheduled task
at logon, "only when the user is logged on" (README); pythonw, so it has no window. Standard library only.
"""
import ctypes
import json
import os
import time


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint32)]


OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'desktop_idle.json')
ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint32


def idle_s():
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000  # both wrap after 49.7 days


while True:
    with open(OUT + '.tmp', 'w') as f:
        json.dump(dict(t=time.time(), idle_s=round(idle_s())), f)
    os.replace(OUT + '.tmp', OUT)
    time.sleep(20)
