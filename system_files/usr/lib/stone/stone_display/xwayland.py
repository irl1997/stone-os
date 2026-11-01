"""The X screens Gamescope gives to apps (its Xwayland servers :0, :1, ...).

In Steam Game Mode, Steam (and STONE Home) use one Xwayland server and games another. Gamescope
sizes each server to what Steam asked for that app (GAMESCOPE_XWAYLAND_MODE_CONTROL), so the
size of the games' server while a game runs is the largest resolution that game can offer.
Read with libX11 (a query, no window, nothing changed); None when there is no such server.
"""

import ctypes
import ctypes.util
import os

MAX_SERVERS = 4


def _lib():
    name = ctypes.util.find_library("X11") or "libX11.so.6"
    try:
        x = ctypes.CDLL(name)
    except OSError:
        return None
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XDefaultScreen.argtypes = [ctypes.c_void_p]
    x.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    return x


def screens():
    """{":0": {"w", "h"}, ...} for the Xwayland servers that answer."""
    if os.environ.get("STONE_XWAYLAND_FAKE"):             # tests only: JSON {":1": [w, h]}
        import json
        return {k: {"w": v[0], "h": v[1]} for k, v in json.loads(os.environ["STONE_XWAYLAND_FAKE"]).items()}
    x = _lib()
    if x is None:
        return {}
    out = {}
    for n in range(MAX_SERVERS):
        name = f":{n}"
        dpy = x.XOpenDisplay(name.encode())
        if not dpy:
            continue
        try:
            s = x.XDefaultScreen(dpy)
            out[name] = {"w": int(x.XDisplayWidth(dpy, s)), "h": int(x.XDisplayHeight(dpy, s))}
        finally:
            x.XCloseDisplay(dpy)
    return out
