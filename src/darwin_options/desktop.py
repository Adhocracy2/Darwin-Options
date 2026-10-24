"""Minimize IB Gateway's windows on AI1's desktop (X11 / Xwayland, via libX11 and ctypes; no extra
packages). GNOME ignores the gateway's own minimize request, so this does it from outside.

    python -m darwin_options.desktop list            # every named window
    python -m darwin_options.desktop minimize gateway|messages
    python -m darwin_options.desktop watch           # keep minimizing new gateway windows (after restarts)

The dashboard calls `minimize()` in a subprocess with the desktop's DISPLAY and XAUTHORITY (`desktop_env`).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import glob
import os
import subprocess
import sys
import time

# which windows each target means (matched on the window title, lower case)
TARGETS = {
    "gateway": lambda n: n in ("ibkr gateway", "ibcalpha-ibc-ibcgateway"),
    "messages": lambda n: n == "login messages",
}


def desktop_env() -> dict:
    """Environment for reaching the logged-in desktop (GNOME on Wayland runs X apps on :0)."""
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    if "XAUTHORITY" not in os.environ:
        auth = sorted(glob.glob(f"/run/user/{os.getuid()}/.mutter-Xwaylandauth.*"), key=os.path.getmtime)
        if auth:
            env["XAUTHORITY"] = auth[-1]
    return env


class _X:
    def __init__(self):
        lib = ctypes.util.find_library("X11")
        if not lib:
            raise RuntimeError("libX11 not found")
        x = self.x = ctypes.cdll.LoadLibrary(lib)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                                 ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
        x.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)]
        x.XIconifyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
        x.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x.XFlush.argtypes = [ctypes.c_void_p]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.d = x.XOpenDisplay(None)
        if not self.d:
            raise RuntimeError("can't open the desktop display")

    def windows(self) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []

        def walk(w):
            name = ctypes.c_char_p()
            if self.x.XFetchName(self.d, w, ctypes.byref(name)) and name.value:
                out.append((w, name.value.decode(errors="replace")))
            r, p, kids, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
            if self.x.XQueryTree(self.d, w, ctypes.byref(r), ctypes.byref(p), ctypes.byref(kids), ctypes.byref(n)):
                for i in range(n.value):
                    walk(kids[i])
        walk(self.x.XDefaultRootWindow(self.d))
        return out

    def iconify(self, w: int) -> None:
        self.x.XIconifyWindow(self.d, w, self.x.XDefaultScreen(self.d))
        self.x.XFlush(self.d)

    def close(self):
        self.x.XCloseDisplay(self.d)


def minimize(target: str) -> list[str]:
    """Minimize the windows for `target` ("gateway" or "messages"); returns their titles."""
    match = TARGETS[target]
    x = _X()
    try:
        done = []
        for w, name in x.windows():
            if match(name.lower()):
                x.iconify(w)
                done.append(name)
        time.sleep(0.2)
        return done
    finally:
        x.close()


def minimize_in_desktop(target: str, timeout: float = 15) -> list[str]:
    """Run `minimize` in a subprocess attached to the desktop (for the dashboard service)."""
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}")
    r = subprocess.run([sys.executable, "-m", "darwin_options.desktop", "minimize", target], env=desktop_env(),
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else "failed")
    return [l for l in r.stdout.splitlines() if l]


def watch(interval: float = 20) -> None:
    """Minimize each new IB Gateway main window once (it reappears after IBC's daily restart)."""
    seen: set[int] = set()
    while True:
        try:
            x = _X()
            for w, name in x.windows():
                if TARGETS["gateway"](name.lower()) and w not in seen:
                    time.sleep(5)  # let the login finish first
                    x.iconify(w)
                    seen.add(w)
            x.close()
        except Exception as e:  # desktop not ready yet; try again
            print("watch:", e, file=sys.stderr)
        time.sleep(interval)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        x = _X()
        for w, n in x.windows():
            print(hex(w), n)
    elif cmd == "minimize":
        for n in minimize(sys.argv[2]):
            print(n)
    elif cmd == "watch":
        watch()
