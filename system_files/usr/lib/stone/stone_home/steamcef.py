"""Ask Steam's own interface to start a game, as Decky Loader does in Game Mode.

On the board (RC3, 25-10-2026) Steam logged `ExecuteSteamURL: "steam://rungameid/<id>"` for
every request with the right id and still never started the STONE shortcut: in Game Mode
steam://rungameid does not start non-Steam shortcuts. Steam's interface does, through
SteamClient.Apps.RunGame, reachable over Chromium's remote debugging protocol, which Steam
exposes on 127.0.0.1:8080 when `<Steam>/.cef-enable-remote-debugging` exists at its start.

Only the standard library is used: one HTTP GET for the list of pages and a minimal
WebSocket client for a single Runtime.evaluate call.
"""

import base64
import json
import os
import socket
import struct
import urllib.parse
import urllib.request

PORT = int(os.environ.get("STONE_STEAM_CEF_PORT", "8080"))
FLAG = ".cef-enable-remote-debugging"
# Pages of Steam's interface that carry the SteamClient object, most suitable first.
PREFERRED_TITLES = ("SharedJSContext", "Steam Shared Context presented by Valve™", "Steam Big Picture Mode", "Steam")


def enable(steam_root):
    """Create the flag file Steam reads at start. Returns True if it is there."""
    path = os.path.join(steam_root, FLAG)
    if os.path.exists(path):
        return True
    try:
        with open(path, "w"):
            pass
        return True
    except OSError:
        return False


def targets(port=PORT, timeout=2.0):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return data if isinstance(data, list) else []


def pick(pages):
    """The page to run SteamClient calls in, or None."""
    usable = [p for p in pages if isinstance(p, dict) and p.get("webSocketDebuggerUrl")]
    for title in PREFERRED_TITLES:
        for p in usable:
            if p.get("title") == title:
                return p
    for p in usable:
        if "steamloopback.host" in str(p.get("url", "")):
            return p
    return None


def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def _send_text(s, text):
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    head = bytearray([0x81])
    n = len(payload)
    if n < 126:
        head.append(0x80 | n)
    elif n < 65536:
        head.append(0x80 | 126)
        head += struct.pack(">H", n)
    else:
        head.append(0x80 | 127)
        head += struct.pack(">Q", n)
    s.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _recv_message(s):
    """One complete text message (fragments joined); control frames skipped."""
    parts = []
    while True:
        b0, b1 = _recv_exact(s, 2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", _recv_exact(s, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _recv_exact(s, 8))[0]
        mask = _recv_exact(s, 4) if b1 & 0x80 else None
        data = _recv_exact(s, n)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        op = b0 & 0x0F
        if op == 0x8:
            raise ConnectionError("closed by Steam")
        if op in (0x9, 0xA):          # ping / pong
            continue
        parts.append(data)
        if b0 & 0x80:
            return b"".join(parts).decode("utf-8", "replace")


def evaluate(ws_url, expression, timeout=5.0):
    """Runtime.evaluate in one page; returns the result value (or raises)."""
    u = urllib.parse.urlparse(ws_url)
    if u.scheme != "ws" or u.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"refusing non-local debugger url {ws_url}")
    s = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = s.recv(1024)
            if not chunk:
                raise ConnectionError("no handshake answer")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        _send_text(s, json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": expression, "awaitPromise": True, "returnByValue": True}}))
        while True:
            msg = json.loads(_recv_message(s))
            if msg.get("id") == 1:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "error"))
                res = msg.get("result", {})
                if res.get("exceptionDetails"):
                    raise RuntimeError(res["exceptionDetails"].get("text", "exception"))
                return res.get("result", {}).get("value")
    finally:
        s.close()


def run_game_expression(game_id):
    gid = str(int(game_id))           # digits only: nothing else reaches Steam's interface
    return ("(async()=>{if(!window.SteamClient||!SteamClient.Apps||!SteamClient.Apps.RunGame)"
            "return 'no SteamClient';"
            f"SteamClient.Apps.RunGame('{gid}','',-1,100);return 'ok';}})()")


def ui_eval(expression, port=PORT):
    """Run one fixed expression in Steam's interface. Returns a short status, never raises."""
    try:
        page = pick(targets(port))
    except (OSError, ValueError) as e:
        return f"cef unavailable ({e.__class__.__name__})"
    if not page:
        return "cef: no Steam page yet"
    try:
        return f"cef[{page.get('title', '?')}]: {evaluate(page['webSocketDebuggerUrl'], expression)}"
    except (OSError, ValueError, RuntimeError) as e:
        return f"cef error: {e}"


def run_game(game_id, port=PORT):
    """Ask Steam's interface to start game_id (a Steam app id or a shortcut's 64-bit game id)."""
    return ui_eval(run_game_expression(game_id), port)


# Sections of Steam's own settings (SettingsSection_t in decky-frontend-lib) STONE may open.
SETTINGS_SECTIONS = ("Display", "Audio", "Controller", "Internet", "Bluetooth", "Storage", "System",
                     "Power", "Accessibility", "Keyboard", "Downloads")
PAGES = ("Downloads",)

# Steam's interface: the focused window (or the Game Mode main window) and its navigator.
# The main menu is opened first so Steam's interface comes over the running app (STONE is
# itself the "game" Steam is showing), then menus are closed after navigating, as Decky does.
_NAV = ("const S=window.SteamUIStore;if(!S)return 'no SteamUIStore';let w=null;"
        "try{w=S.GetFocusedWindowInstance();}catch(e){}"
        "const main=S.WindowStore&&S.WindowStore.GamepadUIMainWindowInstance;"
        "if(!w||!(w.Navigator||w.m_Navigator))w=main||w;"
        "const n=w&&(w.Navigator||w.m_Navigator);if(!n)return 'no Navigator';"
        "const m=w.MenuStore;try{m&&m.OpenMainMenu&&m.OpenMainMenu();}catch(e){}")


def open_settings_expression(section):
    if section not in SETTINGS_SECTIONS:
        raise ValueError("unknown settings section")
    return ("(async()=>{" + _NAV + f"if(!n.Settings)return 'no Settings';n.Settings('{section}');"
            "await new Promise(r=>setTimeout(r,150));try{m&&m.CloseSideMenus&&m.CloseSideMenus();}catch(e){}"
            "return 'ok';})()")


def open_page_expression(page):
    if page not in PAGES:
        raise ValueError("unknown page")
    return ("(async()=>{" + _NAV + f"if(!n.{page})return 'no {page}';n.{page}();"
            "await new Promise(r=>setTimeout(r,150));try{m&&m.CloseSideMenus&&m.CloseSideMenus();}catch(e){}"
            "return 'ok';})()")


def open_settings(section, port=PORT):
    return ui_eval(open_settings_expression(section), port)


def open_page(page, port=PORT):
    return ui_eval(open_page_expression(page), port)
