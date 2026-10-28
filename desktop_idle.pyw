"""Windows, in the desktop session: every 20 s, write how long ago the last keyboard or mouse input was (at the PC or
over remote desktop) and in how many of the last 30 such 20 s slots (10 min) there was any, to desktop_idle.json next
to this file, for agent.py. A remote desktop window left open elsewhere sends a stray mouse move now and then (Faye's
RustDesk, 2026-10-18): one slot, where someone working fills most of them. Only a program inside the desktop session
can tell; quser shows the console session as "none" idle all the time. Started by a scheduled task at logon, "only
when the user is logged on" (README); pythonw, so it has no window. Standard library only.

Keyboard and mouse only, from raw input (a remote desktop's input comes as a device-less one): GetLastInputInfo also
counts game controllers, and the simulator's racing wheel (a Logitech G25 at win5090) sent ~16 reports a second with
nobody at it, so the PC looked used all day (Shadab, 2026-10-28). A simulation is seen by its program (wsl_yield.py's
ALWAYS) instead.
"""
import collections
import ctypes
import ctypes.wintypes as w
import json
import os
import threading
import time


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint32)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [('page', ctypes.c_ushort), ('usage', ctypes.c_ushort), ('flags', w.DWORD), ('target', w.HWND)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, ctypes.c_uint, w.WPARAM, w.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [('style', ctypes.c_uint), ('proc', WNDPROC), ('extra', ctypes.c_int), ('wextra', ctypes.c_int),
                ('inst', w.HINSTANCE), ('icon', w.HANDLE), ('cursor', w.HANDLE), ('bg', w.HANDLE),
                ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]


OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'desktop_idle.json')
user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
kernel32.GetTickCount.restype = ctypes.c_uint32
user32.DefWindowProcW.argtypes = [w.HWND, ctypes.c_uint, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.restype = w.HWND
WM_INPUT, HWND_MESSAGE, RIDEV_INPUTSINK = 0x00FF, -3, 0x100


def idle_at_start():
    """s since the last input of any kind (GetLastInputInfo), as a start before the first keyboard or mouse event."""
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    user32.GetLastInputInfo(ctypes.byref(info))
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000  # wraps: 49.7 d


last = [time.time() - idle_at_start()]  # time of the last keyboard or mouse input


def on_message(hwnd, msg, wparam, lparam):
    if msg == WM_INPUT:
        last[0] = time.time()
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def write():
    slots, before = collections.deque(maxlen=30), None  # one per 20 s: any input since the one before
    while True:
        if before is not None:
            slots.append(last[0] != before)
        before = last[0]
        with open(OUT + '.tmp', 'w') as f:
            json.dump(dict(t=time.time(), idle_s=round(time.time() - last[0]), active=sum(slots), slots=len(slots)),
                      f)
        for _ in range(10):  # agent.py may be reading it just then: Windows refuses to replace an open file
            try:
                os.replace(OUT + '.tmp', OUT)
                break
            except PermissionError:
                time.sleep(0.2)
        time.sleep(20)


proc = WNDPROC(on_message)
cls = WNDCLASS(0, proc, 0, 0, kernel32.GetModuleHandleW(None), None, None, None, None, 'desktop_idle')
user32.RegisterClassW(ctypes.byref(cls))
window = user32.CreateWindowExW(0, 'desktop_idle', 'desktop_idle', 0, 0, 0, 0, 0, w.HWND(HWND_MESSAGE), None,
                                cls.inst, None)
devices = (RAWINPUTDEVICE * 2)(RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK, window),  # mouse, also when not in front
                               RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, window))  # keyboard
if not user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE)):
    raise OSError('RegisterRawInputDevices failed')
threading.Thread(target=write, daemon=True).start()
msg = w.MSG()
while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    user32.DispatchMessageW(ctypes.byref(msg))
