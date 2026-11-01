"""STONE GRAPHICS: which image paths really exist for a game, and switching them reversibly.

PATHS (schema.PATHS)
  native         the game renders at the output resolution, no upscaler (its own TAA)
  game_upscaler  the game's own FSR / XeSS / DLSS-path chosen in its menu (no injection)
  helixsr        HelixSR (OptiScaler-based, made on a BC-250) installed by the person in the game folder
  optiscaler     OptiScaler installed by the person, without HelixSR
  gamescope      Gamescope's spatial filter (FSR 1 / NIS) on a lower game resolution: guidance
                 only in rc12 (Steam owns Gamescope's settings in Game Mode)

WHAT STONE SWITCHES, AND HOW (everything goes through Steam's own per-game "launch options",
backed up before and put back exactly afterwards):
  - HelixSR/OptiScaler installed under a switchable proxy name (winmm.dll, version.dll, ...) is
    only loaded by Wine when the launch options say WINEDLLOVERRIDES="<name>=n,b". Without that
    the files sit unused, so the injected path is ON for one launch and OFF for the next with
    nothing left behind. This is the installation STONE asks for.
  - Installed as dxgi.dll it is ALWAYS loaded (no option needed, none turns it off without
    breaking DXVK), so STONE cannot offer a clean native / game-FSR path for that game and says
    so; it never moves or deletes the person's files.
  - Frame target: DXVK_FRAME_RATE / VKD3D_FRAME_RATE (the game's own D3D translation layer caps
    the frame rate). [VERIFICAR en placa]
  - Measurement only: MangoHud CSV logging without overlay (metrics.py).
  - The person's own launch options are kept: STONE's variables go in front; a variable the person
    already set wins and STONE says so (and never stacks a second WINEDLLOVERRIDES).

ANTI-CHEAT / ONLINE: helixsr and optiscaler are never offered for a game with anti-cheat files or
marked online-only (DLL injection can get an account banned). The game's own paths remain.
STONE never installs HelixSR or OptiScaler (license: not redistributable) and never forces them.
"""

import json
import os
import re
import shlex
import time

from . import schema

MANAGED = ("WINEDLLOVERRIDES", "DXVK_FRAME_RATE", "VKD3D_FRAME_RATE", "MANGOHUD", "MANGOHUD_CONFIG", "STONE_OPTIMIZED")
INJECTED = ("helixsr", "optiscaler")


def state_dir():
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state"), "stone")


# ---- launch options --------------------------------------------------------------------------
def env_in(options, key):
    """The value the launch options give `key` (VAR=value before %command%), or None."""
    m = re.search(r"(?:^|\s)" + re.escape(key) + r"=(\"[^\"]*\"|'[^']*'|\S*)", options or "")
    if not m:
        return None
    v = m.group(1)
    return v[1:-1] if v[:1] in "\"'" and v[-1:] == v[:1] else v


def compose(original, env):
    """The person's options with STONE's variables in front. env: ordered {VAR: value}."""
    original = (original or "").strip()
    env = {k: v for k, v in env.items() if v is not None and env_in(original, k) is None}
    if not env:
        return original
    head = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
    if "%command%" in original:
        return f"{head} {original}"
    return f"{head} %command% {original}".strip()


def get_options_expression(appid):
    a = int(appid)
    return ("(async()=>{const A=window.SteamClient&&SteamClient.Apps;if(!A||!A.RegisterForAppDetails)"
            "return JSON.stringify({error:'no RegisterForAppDetails'});"
            "return await new Promise(res=>{let h=null,done=false;const fin=v=>{if(done)return;done=true;"
            "setTimeout(()=>{try{h&&h.unregister();}catch(e){}},0);res(JSON.stringify(v));};"
            "setTimeout(()=>fin({error:'timeout'}),4000);"
            f"h=A.RegisterForAppDetails({a},d=>fin({{options:String(d&&d.strLaunchOptions!=null?d.strLaunchOptions:'')}}));}});}})()")


def set_options_expression(appid, options):
    a = int(appid)
    if len(options) > 4000 or "\n" in options:
        raise ValueError("launch options")
    return ("(async()=>{const A=window.SteamClient&&SteamClient.Apps;if(!A||!A.SetAppLaunchOptions)"
            f"return JSON.stringify({{error:'no SetAppLaunchOptions'}});A.SetAppLaunchOptions({a},{json.dumps(options)});"
            "return JSON.stringify({ok:true});})()")


class LaunchOptions:
    """Steam's per-game launch options through Steam's interface (cef.ui_value)."""

    def __init__(self, cef):
        self.cef = cef

    def _run(self, expr):
        raw = self.cef.ui_value(expr)
        if not isinstance(raw, str):
            return None, "Steam's interface did not answer"
        try:
            a = json.loads(raw)
        except ValueError:
            return None, "unreadable answer"
        if not isinstance(a, dict) or "error" in a:
            return None, a.get("error", "unexpected answer") if isinstance(a, dict) else "unexpected answer"
        return a, None

    def get(self, appid):
        a, err = self._run(get_options_expression(appid))
        return (a.get("options", ""), None) if a else (None, err)

    def set(self, appid, options, settle=lambda: time.sleep(0.3)):
        """APPLY -> VERIFY (read back). (True, None) or (False, why)."""
        a, err = self._run(set_options_expression(appid, options))
        if a is None:
            return False, err
        settle()
        now, err = self.get(appid)
        if now is None:
            return False, f"not verified ({err})"
        return (True, None) if now.strip() == options.strip() else (False, "Steam kept other launch options")


# ---- availability ----------------------------------------------------------------------------
def safe_reason(game, facts):
    """Why injection is not allowed for this game, or None."""
    if facts.get("anti_cheat"):
        return "anti-cheat: " + ", ".join(facts["anti_cheat"])
    if game.get("anti_cheat"):
        return "anti-cheat (STONE profile database)"
    if game.get("online_only"):
        return "online game (STONE profile database)"
    return None


def availability(game, facts, original_options=""):
    """{path: {"available": bool, "reason": str|None, ...}} from REAL facts only.
    game: the profile's "game" facts; facts: detect.facts(); original_options: the person's."""
    inj = facts.get("injected") or {}
    proxy = inj.get("proxy")
    always_on = bool(inj) and proxy in (None, "dxgi", "d3d12")
    user_overrides = env_in(original_options, "WINEDLLOVERRIDES") or ""
    user_enables = bool(proxy) and re.search(r"(?:^|[;,])" + re.escape(proxy) + r"=n", user_overrides) is not None
    safe = safe_reason(game, facts)
    out = {}
    blocked = None
    if always_on:
        blocked = f"{inj['kind']} installed as {proxy or '?'}.dll is always loaded: reinstall it as winmm.dll"
    elif user_enables:
        blocked = f"your launch options load {proxy}.dll ({inj['kind']}) on every start"
    out["native"] = {"available": blocked is None, "reason": blocked}
    ups = game.get("native_upscalers") or []
    if blocked:
        out["game_upscaler"] = {"available": False, "reason": blocked}
    elif ups:
        out["game_upscaler"] = {"available": True, "reason": None, "upscalers": ups}
    else:
        out["game_upscaler"] = {"available": False, "reason": "no upscaler of the game known (STONE profile database)"}
    for kind in INJECTED:
        if inj.get("kind") != kind:
            other = inj.get("kind")
            out[kind] = {"available": False, "reason": f"{other} installed instead" if other else "not installed in the game folder"}
        elif safe:
            out[kind] = {"available": False, "reason": safe}
        elif user_overrides and not user_enables and not always_on:
            out[kind] = {"available": False, "reason": "your launch options already set WINEDLLOVERRIDES"}
        elif not ups and game.get("api") is not None:
            out[kind] = {"available": False, "reason": "the game has no DLSS/FSR/XeSS input to replace"}
        else:
            out[kind] = {"available": True, "reason": None, "proxy": proxy, "always_on": always_on,
                         "fingerprint": inj.get("fingerprint")}
    out["gamescope"] = {"available": True, "reason": None, "guidance_only": True}
    return out


def env_for(config, facts, measure_env=None):
    """STONE's variables for one config (None = not possible). measure_env: MangoHud logging."""
    g = config["graphics"]
    inj = facts.get("injected") or {}
    env = {}
    if g["path"] in INJECTED:
        if inj.get("kind") != g["path"]:
            return None
        if inj.get("proxy") not in (None, "dxgi", "d3d12"):
            env["WINEDLLOVERRIDES"] = f"{inj['proxy']}=n,b"
    fps = config["frame_target"]["fps"]
    if fps:
        env["DXVK_FRAME_RATE"] = str(fps)
        env["VKD3D_FRAME_RATE"] = str(fps)
    if measure_env:
        env.update(measure_env)
    if env:                                   # marks STONE's options (nothing at all when none are needed)
        env["STONE_OPTIMIZED"] = schema.config_key(config).replace(":", "_")[:80]
    return env


# ---- the applied state (crash-proof restore) ------------------------------------------------
class Overrides:
    """Records what STONE changed before changing it, so a crash of anything (the game, STONE
    Home, the console) is followed by putting the person's options back."""

    def __init__(self, lo, path=None):
        self.lo = lo
        self.path = path or os.path.join(state_dir(), "game-graphics.json")

    def _doc(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, d):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.path)

    def pending(self):
        return {k: v for k, v in self._doc().items() if isinstance(v, dict)}

    def apply(self, appid, env, key):
        """(applied options, None) or (None, why). The person's options are recorded first."""
        appid = str(appid)
        doc = self._doc()
        if appid in doc:                         # left over: put the original back first
            self.restore(appid)
            doc = self._doc()
        original, err = self.lo.get(appid)
        if original is None:
            return None, err
        new = compose(original, env)
        if new == original.strip():
            return original, None
        doc[appid] = {"original": original, "applied": new, "config": key, "time": int(time.time())}
        self._write(doc)
        ok, err = self.lo.set(appid, new)
        if not ok:
            self.restore(appid)
            return None, err
        return new, None

    def restore(self, appid):
        """Put the person's options back. "restored" | "nothing" | "kept (changed by the person)" | error."""
        appid = str(appid)
        doc = self._doc()
        rec = doc.get(appid)
        if not rec:
            return "nothing"
        now, err = self.lo.get(appid)
        if now is None:
            return f"error: {err}"                # keep the record: retried at the next start
        if now.strip() != rec["applied"].strip() and now.strip() != rec["original"].strip():
            result = "kept (changed by the person)"
        elif now.strip() == rec["original"].strip():
            result = "restored"
        else:
            ok, err = self.lo.set(appid, rec["original"])
            if not ok:
                return f"error: {err}"
            result = "restored"
        doc.pop(appid, None)
        self._write(doc)
        return result

    def restore_all(self):
        return {a: self.restore(a) for a in list(self.pending())}
