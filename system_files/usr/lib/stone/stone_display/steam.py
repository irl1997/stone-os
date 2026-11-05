"""What Steam (and through it, Gamescope) uses: Steam's own display manager and per-game resolution.

Read through Steam's interface (stone_home/steamcef.py, the local CEF debugger Steam opens in
Game Mode). Nothing here reads Linux directly.

  display_state()   SteamClient.System.DisplayManager.GetState(): CMsgSystemDisplayManagerState
                    (SteamDatabase/Protobufs steammessages_client_objects.proto):
                      displays[]: id, name, is_primary, current_mode_id, modes[] (w, h, refresh),
                                  refresh range, VRR capable/active, HDR capable/active
                      game_resolution_override_default: the size a game gets when its own
                                  "Game resolution" is Default  <- the size games see
                      game_resolution_override_native:  the size behind "Native"
  app_resolution(appid)          SteamClient.Apps.GetResolutionOverrideForApp -> "Default",
                                 "Native" or "WxH"
  set_app_resolution(appid, v)   SteamClient.Apps.SetAppResolutionOverride (only "Default"/"Native")
  set_default_expression(w, h)   SteamClient.System.DisplayManager.SetGamescopeInternalResolution:
                                 what Steam calls the size games get by default. Its effect is not
                                 documented, so STONE always reads the state back afterwards and
                                 puts the old value back if game_resolution_override_default did
                                 not become (w, h) (__init__.GameEnvironment.ensure_default).

Gamescope applies the chosen size to the game's Xwayland screen (GAMESCOPE_XWAYLAND_MODE_CONTROL):
that screen is the largest resolution the game can offer in its menus.
"""

import base64
import json

from stone_home import steamcef

STATE_EXPRESSION = (
    "(async()=>{const S=window.SteamClient&&SteamClient.System&&SteamClient.System.DisplayManager;"
    "if(!S||!S.GetState)return JSON.stringify({error:'no DisplayManager'});"
    "let s=await S.GetState();if(s instanceof ArrayBuffer)s=new Uint8Array(s);"
    "if(s&&s.BYTES_PER_ELEMENT===1&&typeof s.length==='number'){let b='';for(let i=0;i<s.length;i++)b+=String.fromCharCode(s[i]);"
    "return JSON.stringify({b64:btoa(b)});}"
    "if(typeof s==='string')return JSON.stringify({str:s});"
    "if(s&&typeof s.serializeBinary==='function'){const u=s.serializeBinary();let b='';for(let i=0;i<u.length;i++)b+=String.fromCharCode(u[i]);return JSON.stringify({b64:btoa(b)});}"
    "if(s&&typeof s.toObject==='function')return JSON.stringify({obj:s.toObject()});"
    "return JSON.stringify({obj:s});})()")

ALLOWED_OVERRIDES = ("Default", "Native")


# ---- minimal protobuf reader (wire format only; no schema library) -----------------------
def _varint(buf, i):
    shift = val = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError("bad varint")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def fields(buf):
    """{field_number: [values]}; varints as int, length-delimited as bytes, fixed as int."""
    out, i = {}, 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 2:
            n, i = _varint(buf, i)
            if i + n > len(buf):
                raise ValueError("truncated")
            v, i = bytes(buf[i:i + n]), i + n
        elif wt == 5:
            v, i = int.from_bytes(buf[i:i + 4], "little"), i + 4
        elif wt == 1:
            v, i = int.from_bytes(buf[i:i + 8], "little"), i + 8
        else:
            raise ValueError(f"wire type {wt}")
        out.setdefault(num, []).append(v)
    return out


def _i32(v):
    v &= 0xFFFFFFFFFFFFFFFF
    return v - (1 << 64) if v >= 1 << 63 else v


def _one(f, n, default=None):
    v = f.get(n)
    return v[0] if v else default


def _ints(f, n):
    out = []
    for v in f.get(n, []):
        if isinstance(v, bytes):              # packed repeated
            i = 0
            while i < len(v):
                x, i = _varint(v, i)
                out.append(_i32(x))
        else:
            out.append(_i32(v))
    return out


def _res(raw):
    if not isinstance(raw, bytes):
        return None
    f = fields(raw)
    w, h = _one(f, 1), _one(f, 2)
    return {"w": w, "h": h} if w and h else None


def decode_state(raw):
    """CMsgSystemDisplayManagerState bytes -> normalized dict."""
    f = fields(raw)
    displays = []
    for d in f.get(1, []):
        df = fields(d)
        modes = []
        for m in df.get(11, []):
            mf = fields(m)
            modes.append({"id": _i32(_one(mf, 1, 0)), "w": _i32(_one(mf, 2, 0)), "h": _i32(_one(mf, 3, 0)),
                          "hz": _i32(_one(mf, 4, 0))})
        name = _one(df, 2)
        desc = _one(df, 3)
        displays.append({
            "id": _i32(_one(df, 1, 0)), "name": name.decode("utf-8", "replace") if name else None,
            "description": desc.decode("utf-8", "replace") if desc else None,
            "is_primary": bool(_one(df, 4, 0)), "is_enabled": bool(_one(df, 5, 0)),
            "is_internal": bool(_one(df, 6, 0)), "has_mode_override": bool(_one(df, 7, 0)),
            "current_mode_id": _i32(_one(df, 10, -1)), "modes": modes,
            "refresh_min": _one(df, 12), "refresh_max": _one(df, 13),
            "vrr_capable": bool(_one(df, 14, 0)), "vrr_active": bool(_one(df, 15, 0)),
            "hdr_capable": bool(_one(df, 16, 0)), "hdr_active": bool(_one(df, 17, 0)),
            "refresh_rates": _ints(df, 18)})
    return {"displays": displays, "mode_switching": bool(_one(f, 2, 0)),
            "game_native": _res(_one(f, 4)), "game_default": _res(_one(f, 5))}


def _norm(k):
    return k.replace("_", "").lower()


def from_object(o):
    """The same state when Steam hands back a plain object (camelCase or snake_case keys)."""
    def g(d, name, default=None):
        if not isinstance(d, dict):
            return default
        for k, v in d.items():
            if _norm(k) in (_norm(name), _norm(name) + "list"):
                return v
        return default

    def res(r):
        w, h = g(r, "width"), g(r, "height")
        return {"w": int(w), "h": int(h)} if w and h else None

    displays = []
    for d in g(o, "displays", []) or []:
        modes = [{"id": int(g(m, "id", 0) or 0), "w": int(g(m, "width", 0) or 0), "h": int(g(m, "height", 0) or 0),
                  "hz": int(g(m, "refresh_hz", 0) or 0)} for m in (g(d, "modes", []) or [])]
        displays.append({
            "id": int(g(d, "id", 0) or 0), "name": g(d, "name"), "description": g(d, "description"),
            "is_primary": bool(g(d, "is_primary")), "is_enabled": bool(g(d, "is_enabled")),
            "is_internal": bool(g(d, "is_internal")), "has_mode_override": bool(g(d, "has_mode_override")),
            "current_mode_id": int(g(d, "current_mode_id", -1) if g(d, "current_mode_id") is not None else -1),
            "modes": modes, "refresh_min": g(d, "refresh_rate_min"), "refresh_max": g(d, "refresh_rate_max"),
            "vrr_capable": bool(g(d, "is_vrr_capable")), "vrr_active": bool(g(d, "is_vrr_output_active")),
            "hdr_capable": bool(g(d, "is_hdr_capable")), "hdr_active": bool(g(d, "is_hdr_output_active")),
            "refresh_rates": [int(x) for x in (g(d, "supported_refresh_rates", []) or [])]})
    return {"displays": displays, "mode_switching": bool(g(o, "is_mode_switching_supported")),
            "game_native": res(g(o, "game_resolution_override_native")),
            "game_default": res(g(o, "game_resolution_override_default"))}


def parse_answer(raw):
    """The JSON string STATE_EXPRESSION returns -> (state or None, error or None)."""
    if not isinstance(raw, str):
        return None, "Steam's interface did not answer"
    try:
        a = json.loads(raw)
    except ValueError:
        return None, "unreadable answer"
    if not isinstance(a, dict):
        return None, "unreadable answer"
    try:
        if "error" in a:
            return None, str(a["error"])
        if "b64" in a:
            return decode_state(base64.b64decode(a["b64"])), None
        if "str" in a:
            try:
                return decode_state(base64.b64decode(a["str"], validate=True)), None
            except ValueError:
                return from_object(json.loads(a["str"])), None
        if "obj" in a and isinstance(a["obj"], dict):
            return from_object(a["obj"]), None
    except (ValueError, TypeError) as e:
        return None, f"could not decode Steam's display state ({e})"
    return None, "unexpected answer"


def display_state(cef=steamcef):
    """(state, error). Steam has several interface pages and only some answer GetState, so each
    candidate page is tried until one gives a readable state; the error names what each said."""
    each = getattr(cef, "ui_values", None)
    if each is None:
        return parse_answer(cef.ui_value(STATE_EXPRESSION))
    notes = []
    for title, value, err in each(STATE_EXPRESSION):
        if err:
            notes.append(f"{title}: {err}")
            continue
        state, perr = parse_answer(value)
        if state is not None:
            return state, None
        notes.append(f"{title}: {perr}")
    return None, "; ".join(notes) or "Steam's interface did not answer"


def _appid(appid):
    a = int(appid)
    if not 0 < a < 1 << 32:
        raise ValueError("not an app id")
    return a


def app_resolution(appid, cef=steamcef):
    a = _appid(appid)
    v = cef.ui_value("(async()=>{if(!window.SteamClient||!SteamClient.Apps||!SteamClient.Apps.GetResolutionOverrideForApp)"
                     f"return null;return String(await SteamClient.Apps.GetResolutionOverrideForApp({a}));}})()")
    return v if isinstance(v, str) and len(v) <= 32 else None


def set_app_resolution(appid, value, cef=steamcef):
    a = _appid(appid)
    if value not in ALLOWED_OVERRIDES:
        raise ValueError("only Default or Native")
    v = cef.ui_value("(async()=>{if(!window.SteamClient||!SteamClient.Apps||!SteamClient.Apps.SetAppResolutionOverride)"
                     f"return 'no';SteamClient.Apps.SetAppResolutionOverride({a},'{value}');return 'ok';}})()")
    return v == "ok"


def ensure_native_expression(appids):
    """One call for many games: every game whose Game resolution is "Default" gets "Native".
    Explicit choices ("Native" or a size the person picked) are left as they are. Returns the
    ids changed and how many were already set."""
    ids = sorted({_appid(a) for a in appids})
    return ("(async()=>{const A=window.SteamClient&&SteamClient.Apps;if(!A||!A.GetResolutionOverrideForApp||!A.SetAppResolutionOverride)"
            "return JSON.stringify({error:'no SteamClient.Apps resolution API'});const ch=[],kept={};"
            f"for(const id of {json.dumps(ids)}){{let v=null;try{{v=String(await A.GetResolutionOverrideForApp(id));}}catch(e){{continue;}}"
            "if(v==='Default'){A.SetAppResolutionOverride(id,'Native');ch.push(id);}else{kept[id]=v;}}"
            "return JSON.stringify({changed:ch,kept:kept});})()")


def restore_default_expression(appids):
    """Back to "Default" for games STONE set to "Native" (and that are still "Native")."""
    ids = sorted({_appid(a) for a in appids})
    return ("(async()=>{const A=window.SteamClient&&SteamClient.Apps;if(!A||!A.SetAppResolutionOverride)"
            "return JSON.stringify({error:'no SteamClient.Apps resolution API'});const ch=[];"
            f"for(const id of {json.dumps(ids)}){{let v=null;try{{v=String(await A.GetResolutionOverrideForApp(id));}}catch(e){{continue;}}"
            "if(v==='Native'){A.SetAppResolutionOverride(id,'Default');ch.push(id);}}"
            "return JSON.stringify({changed:ch});})()")


def set_default_expression(w, h):
    w, h = int(w), int(h)
    if not (320 <= w <= 7680 and 200 <= h <= 4320):
        raise ValueError("size out of range")
    return ("(async()=>{const D=window.SteamClient&&SteamClient.System&&SteamClient.System.DisplayManager;"
            "if(!D||!D.SetGamescopeInternalResolution)return JSON.stringify({error:'no SetGamescopeInternalResolution'});"
            f"await D.SetGamescopeInternalResolution({w},{h});return JSON.stringify({{ok:true}});}})()")


def run_batch(expression, cef=steamcef):
    raw = cef.ui_value(expression)
    if not isinstance(raw, str):
        return None, "Steam's interface did not answer"
    try:
        a = json.loads(raw)
    except ValueError:
        return None, "unreadable answer"
    if not isinstance(a, dict) or "error" in a:
        return None, (a or {}).get("error", "unexpected answer") if isinstance(a, dict) else "unexpected answer"
    return a, None
