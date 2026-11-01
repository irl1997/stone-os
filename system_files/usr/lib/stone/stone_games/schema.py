"""STONE GAME PROFILE: what STONE knows and decides for one game.

    {
      "schema": 1,                       PROFILE_VERSION (format of this document)
      "id": "steam:<AppID>",             launcher:id (steam today; heroic/gog/epic later)
      "appid": "<AppID>", "name": "...",
      "status": UNKNOWN | AUTO_GENERATED | RECOMMENDED | USER_MODIFIED | HARDWARE_VERIFIED,
      "revision": 3,                     bumps on every change of this profile
      "game": {                          facts (built-in DB and detection; None = unknown)
        "api": "dx12" | "dx11" | "vulkan" | "opengl" | None,
        "native_upscalers": ["fsr3.1", "dlss", "xess", ...],
        "anti_cheat": True | False | None, "online_only": True | False | None,
        "benchmark": "in-game benchmark (Ajustes → Gráficos → Benchmark)" | None},
      "modes": {"recommended"|"quality"|"performance"|"custom": CONFIG},
      "invalidation": {...},             what the measurements were made with (optimizer.environment)
      "measurements": [...],             metrics + score of each measured configuration
      "failures": {"g:<graphics>": n, "p:<perf profile>": n}   abnormal exits, counted apart
      "safety": {"next_safe": "<graphics>"|None, "next_safe_perf": "<profile>"|None}
                                         the next launch avoids these (one abnormal exit)
      "verified": {...} | None           HARDWARE_VERIFIED: what the person confirmed, and when
    }

CONFIG (one way to play the game):
    {"graphics": {"path": native | game_upscaler | helixsr | optiscaler | gamescope,
                  "upscaler_preset": quality | balanced | performance | ultra_performance | native_aa | None,
                  "internal": {"strategy": native | upscaled, "scale": 0.33..1.0},
                  "frame_generation": False, "game_settings": "text for the person (in-game menu)"},
     "performance": {"profile": silent | balanced | performance},
     "frame_target": {"fps": 30..240 | None, "vrr": True | False},
     "gamescope": {"scaling_filter": None | fsr | nis | linear | integer}}

HARDWARE_VERIFIED is set only by a person confirming on the machine (optimizer.verify), never by
a test or a built-in profile; there is no "STONE VERIFIED".
"""

import copy

PROFILE_VERSION = 1
STATUSES = ("UNKNOWN", "AUTO_GENERATED", "RECOMMENDED", "USER_MODIFIED", "HARDWARE_VERIFIED")
MODES = ("recommended", "quality", "performance", "custom")
PATHS = ("native", "game_upscaler", "helixsr", "optiscaler", "gamescope")
PRESETS = (None, "native_aa", "quality", "balanced", "performance", "ultra_performance")
PERF = ("silent", "balanced", "performance")
FILTERS = (None, "fsr", "nis", "linear", "integer")
APIS = (None, "dx12", "dx11", "vulkan", "opengl", "native")
# How much of the output each preset renders internally (per axis), the usual FSR/DLSS ratios.
PRESET_SCALE = {"native_aa": 1.0, "quality": 1 / 1.5, "balanced": 1 / 1.7, "performance": 0.5, "ultra_performance": 1 / 3}


class ProfileError(ValueError):
    pass


def default_config():
    return {"graphics": {"path": "native", "upscaler_preset": None, "internal": {"strategy": "native", "scale": 1.0},
                         "frame_generation": False, "game_settings": None},
            "performance": {"profile": "balanced"},
            "frame_target": {"fps": None, "vrr": False},
            "gamescope": {"scaling_filter": None}}


def _choice(v, allowed, what):
    if v not in allowed:
        raise ProfileError(f"{what}: {v!r} not one of {allowed}")
    return v


def clean_config(c):
    """A well-formed CONFIG or ProfileError. Unknown keys are dropped."""
    if not isinstance(c, dict):
        raise ProfileError("config must be an object")
    out = default_config()
    g = c.get("graphics") or {}
    out["graphics"]["path"] = _choice(g.get("path", "native"), PATHS, "graphics.path")
    out["graphics"]["upscaler_preset"] = _choice(g.get("upscaler_preset"), PRESETS, "upscaler_preset")
    i = g.get("internal") or {}
    strategy = _choice(i.get("strategy", "native"), ("native", "upscaled"), "internal.strategy")
    scale = i.get("scale", 1.0 if strategy == "native" else PRESET_SCALE.get(out["graphics"]["upscaler_preset"] or "quality"))
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0.33 <= scale <= 1.0:
        raise ProfileError("internal.scale must be 0.33..1.0")
    if strategy == "native" and out["graphics"]["path"] in ("helixsr", "optiscaler", "gamescope") and scale >= 1.0 \
            and out["graphics"]["upscaler_preset"] not in (None, "native_aa"):
        raise ProfileError("an upscaler preset needs an upscaled internal resolution")
    out["graphics"]["internal"] = {"strategy": strategy, "scale": round(float(scale), 3)}
    fg = g.get("frame_generation", False)
    if not isinstance(fg, bool):
        raise ProfileError("frame_generation must be true/false")
    out["graphics"]["frame_generation"] = fg
    gs = g.get("game_settings")
    out["graphics"]["game_settings"] = gs[:300] if isinstance(gs, str) else None
    out["performance"]["profile"] = _choice((c.get("performance") or {}).get("profile", "balanced"), PERF, "performance.profile")
    ft = c.get("frame_target") or {}
    fps = ft.get("fps")
    if fps is not None and (isinstance(fps, bool) or not isinstance(fps, int) or not 24 <= fps <= 240):
        raise ProfileError("frame_target.fps must be 24..240 or null")
    out["frame_target"] = {"fps": fps, "vrr": bool(ft.get("vrr", False))}
    out["gamescope"]["scaling_filter"] = _choice((c.get("gamescope") or {}).get("scaling_filter"), FILTERS, "scaling_filter")
    return out


def config_key(c):
    """Stable short key of a CONFIG (for measurements and failure counts)."""
    g = c["graphics"]
    return (f"{g['path']}:{g['upscaler_preset'] or '-'}:{g['internal']['scale']}:fg{int(g['frame_generation'])}:"
            f"{c['performance']['profile']}:{c['frame_target']['fps'] or '-'}:{c['gamescope']['scaling_filter'] or '-'}")


def clean_profile(p, source):
    """A well-formed profile or ProfileError (built-in, auto and user files alike)."""
    if not isinstance(p, dict):
        raise ProfileError("profile must be an object")
    if p.get("schema") != PROFILE_VERSION:
        raise ProfileError(f"profile schema {p.get('schema')!r} is not {PROFILE_VERSION}")
    appid = str(p.get("appid", ""))
    if not appid.isdigit() or len(appid) > 10:
        raise ProfileError("appid")
    status = _choice(p.get("status", "UNKNOWN"), STATUSES, "status")
    if source == "builtin" and status in ("AUTO_GENERATED", "USER_MODIFIED", "HARDWARE_VERIFIED"):
        raise ProfileError("a built-in profile cannot claim a local status")
    game = p.get("game") or {}
    ups = game.get("native_upscalers") or []
    if not isinstance(ups, list) or not all(isinstance(u, str) and len(u) < 24 for u in ups):
        raise ProfileError("game.native_upscalers")
    out = {
        "schema": PROFILE_VERSION, "id": f"steam:{appid}", "appid": appid,
        "name": str(p.get("name", ""))[:200], "status": status, "source": source,
        "revision": int(p.get("revision", 1)) if str(p.get("revision", 1)).isdigit() else 1,
        "game": {"api": _choice(game.get("api"), APIS, "game.api"), "native_upscalers": ups[:8],
                 "anti_cheat": game.get("anti_cheat") if isinstance(game.get("anti_cheat"), bool) else None,
                 "online_only": game.get("online_only") if isinstance(game.get("online_only"), bool) else None,
                 "benchmark": str(game["benchmark"])[:200] if isinstance(game.get("benchmark"), str) else None,
                 "notes": str(game["notes"])[:500] if isinstance(game.get("notes"), str) else None},
        "modes": {}, "invalidation": p.get("invalidation") if isinstance(p.get("invalidation"), dict) else {},
        "measurements": [m for m in (p.get("measurements") or []) if isinstance(m, dict)][-40:],
        "failures": {str(k): int(v) for k, v in (p.get("failures") or {}).items() if str(v).isdigit()},
        "candidates": [],
        "safety": {k: (v[:120] if isinstance(v, str) else None)
                   for k, v in ((k, (p.get("safety") or {}).get(k)) for k in ("next_safe", "next_safe_perf"))},
        "verified": p.get("verified") if isinstance(p.get("verified"), dict) and status == "HARDWARE_VERIFIED" else None,
    }
    for m, c in (p.get("modes") or {}).items():
        if m in MODES and c is not None:
            out["modes"][m] = clean_config(c)
    for c in (p.get("candidates") or [])[:24]:
        out["candidates"].append(clean_config(c))
    return out


def empty_profile(appid, name=""):
    return {"schema": PROFILE_VERSION, "id": f"steam:{appid}", "appid": str(appid), "name": name, "status": "UNKNOWN",
            "source": "none", "revision": 0,
            "game": {"api": None, "native_upscalers": [], "anti_cheat": None, "online_only": None, "benchmark": None, "notes": None},
            "modes": {}, "invalidation": {}, "measurements": [], "failures": {}, "candidates": [],
            "safety": {"next_safe": None, "next_safe_perf": None}, "verified": None}


def copy_profile(p):
    return copy.deepcopy(p)
