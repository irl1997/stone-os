"""STONE DISPLAY: the display model, the output policy and the game environment, from real sources.

Five resolutions, never mixed:
  A physical   the display's native mode (EDID preferred timing, else the kernel's first mode)
  B output     what Gamescope sends now (Steam's current mode for that display)
  C game env   the largest resolution a game can choose (Steam's per-game size, applied by
               Gamescope to the games' Xwayland screen)
  D internal   what a game actually renders: the game's own setting (STONE GRAPHICS, later)
  E final      what reaches the display after scaling: equals B
STONE DISPLAY reports A, B, C and E and sets the policy for B and C. It never chooses D.

Sources, in this order of trust for each fact:
  kernel (drm.py, EDID)  identity, native mode, what the display supports (VRR range, HDR)
  Steam  (steam.py)      current output mode, modes validated on this link, VRR/HDR active,
                         the size games get by default
  Xwayland (xwayland.py) the screen sizes Gamescope gave right now
Anything no source states is None (shown as UNKNOWN). Nothing is invented.
"""

import hashlib

from . import link

POLICY_MAX_HZ = 144          # above this a console gains nothing and links get fragile


def _area(m):
    return (m or {}).get("w", 0) * (m or {}).get("h", 0)


def _res(m):
    return f"{m['w']}x{m['h']}" if m and m.get("w") and m.get("h") else None


def display_id(conn):
    """Stable id across reconnections and ports: maker, product and name (never the serial)."""
    e = conn.get("edid") or {}
    key = f"{e.get('vendor')}-{e.get('product_code')}-{e.get('name')}" if e else f"noedid-{conn['connector']}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _edid_refreshes(e, w, h):
    rates = set()
    for t in (e or {}).get("timings", []):
        if t["w"] == w and t["h"] == h:
            rates.add(round(t["hz"]))
    for v in (e or {}).get("vics", []):
        if v["w"] == w and v["h"] == h:
            rates.add(v["hz"])
    return sorted(rates)


def _match_steam(conn, steam_displays, connector_names=()):
    """The Steam display that is this connector, or None. A Steam display named after another
    connector (Steam names displays by connector, e.g. "HDMI-A-1") is never this one."""
    if not steam_displays:
        return None
    others = {n.lower() for n in connector_names if n != conn["connector"]}
    steam_displays = [d for d in steam_displays if (d.get("name") or "").lower() not in others]
    if not steam_displays:
        return None
    enabled = [d for d in steam_displays if d.get("is_enabled") or d.get("current_mode_id", -1) >= 0]
    name = ((conn.get("edid") or {}).get("name") or "").lower()
    for d in steam_displays:
        n = (d.get("name") or "").lower()
        if n and (n == conn["connector"].lower() or (name and name in n)):
            return d
    if len(enabled) == 1:
        return enabled[0]
    prim = [d for d in steam_displays if d.get("is_primary")]
    return prim[0] if len(prim) == 1 else None


def capabilities(conn, sd=None):
    """DisplayCapabilities for one connector (sd: the matching Steam display, if known)."""
    e = conn.get("edid") or {}
    connected = conn.get("status") == "connected"
    native = None
    if e.get("preferred"):
        native = {"w": e["preferred"]["w"], "h": e["preferred"]["h"], "hz": round(e["preferred"]["hz"])}
    elif conn.get("modes"):
        native = {"w": conn["modes"][0]["w"], "h": conn["modes"][0]["h"], "hz": None}
    kind = conn.get("kind")
    if kind == "DisplayPort" and e.get("is_hdmi"):
        kind = "DisplayPort → HDMI (adapter)"
    modes, source = [], None
    if sd and sd.get("modes"):
        modes = sorted({(m["w"], m["h"], m["hz"]) for m in sd["modes"] if m.get("w") and m.get("h")},
                       key=lambda x: (-x[0] * x[1], -x[2]))
        modes = [{"w": w, "h": h, "hz": hz} for w, h, hz in modes]
        source = "steam"
    elif conn.get("modes"):
        for m in conn["modes"]:
            if m.get("interlaced"):
                continue
            rates = _edid_refreshes(e, m["w"], m["h"])
            for hz in rates or [None]:
                modes.append({"w": m["w"], "h": m["h"], "hz": hz})
        source = "kernel+edid"
    current = None
    if sd and sd.get("current_mode_id", -1) >= 0:
        cm = next((m for m in sd.get("modes", []) if m.get("id") == sd["current_mode_id"]), None)
        if cm:
            current = {"w": cm["w"], "h": cm["h"], "hz": cm["hz"]}
    at_native = sorted({m["hz"] for m in modes if native and m["w"] == native["w"] and m["h"] == native["h"] and m["hz"]})
    vrr = e.get("vrr")
    vrr_supported = True if (vrr or (sd and sd.get("vrr_capable"))) else (False if e else None)
    hdr = e.get("hdr")
    hdr_supported = None
    if hdr is not None:
        hdr_supported = "HDR10 (PQ)" in hdr.get("eotfs", []) or "HLG" in hdr.get("eotfs", [])
    if sd and sd.get("hdr_capable"):
        hdr_supported = True
    if hdr_supported is None and e:
        hdr_supported = False if e.get("vics") or e.get("is_hdmi") else None
    return {
        "connected": connected, "display_id": display_id(conn) if connected else None,
        "display_name": e.get("name") or (sd or {}).get("name"), "maker": e.get("vendor"),
        "connector": conn["connector"], "card": conn.get("card"), "connection_type": kind,
        "native_resolution": native, "current_resolution": current, "available_modes": modes,
        "modes_source": source, "refresh_rates": at_native,
        "vrr_supported": vrr_supported,
        "vrr_range": {"min": vrr["min"], "max": vrr["max"], "source": vrr["source"]} if vrr else (
            {"min": sd["refresh_min"], "max": sd["refresh_max"], "source": "steam"}
            if sd and sd.get("vrr_capable") and sd.get("refresh_min") and sd.get("refresh_max") else None),
        "vrr_active": bool(sd.get("vrr_active")) if sd else None,
        "hdr_supported": hdr_supported,
        "hdr": hdr,
        "hdr_active": bool(sd.get("hdr_active")) if sd else None,
        "hdmi_max_tmds_mhz": e.get("hdmi_max_tmds_mhz"), "hdmi_frl_gbps": e.get("hdmi_frl_gbps"),
        "edid_ok": bool(e), "steam_display": sd is not None,
    }


def preferred_mode(cap):
    """Output policy. The display's native resolution; among the refresh rates proven to work on
    this link (Steam's list; else what the kernel accepts with the EDID's rates), the highest up
    to POLICY_MAX_HZ. Never a lower resolution for a higher refresh, never 4K30 over 4K60."""
    n = cap.get("native_resolution")
    if not n:
        return None
    rates = [r for r in cap.get("refresh_rates") or [] if r and r <= POLICY_MAX_HZ]
    if rates:
        return {"w": n["w"], "h": n["h"], "hz": max(rates), "confidence": cap.get("modes_source")}
    return {"w": n["w"], "h": n["h"], "hz": n.get("hz"), "confidence": "edid" if n.get("hz") else None}


def primary(caps):
    """The gaming display: the one Steam shows on; else the only connected one; else the
    connected external one with the largest native mode."""
    conn = [c for c in caps if c["connected"]]
    if not conn:
        return None
    with_current = [c for c in conn if c.get("current_resolution")]
    if len(with_current) == 1:
        return with_current[0]
    if len(conn) == 1:
        return conn[0]
    ext = [c for c in conn if c["connection_type"] != "internal panel"] or conn
    return max(ext, key=lambda c: _area(c.get("native_resolution")))


def evaluate(p, pref, game_default, game_native, xw, game_policy):
    """Findings, each {"code","level","text"}; level: ok | warn | info."""
    out = []
    if p is None:
        return [{"code": "DISPLAY_DISCONNECTED", "level": "warn", "text": "No hay ninguna pantalla conectada"}]
    cur, nat = p.get("current_resolution"), p.get("native_resolution")
    if not p.get("edid_ok"):
        out.append({"code": "EDID_UNKNOWN", "level": "warn",
                    "text": "La pantalla no entrega su EDID: STONE no puede saber su resolución nativa (modo seguro: se deja la salida actual)"})
    if cur and nat and _area(cur) < _area(nat):
        out.append({"code": "OUTPUT_BELOW_NATIVE", "level": "warn",
                    "text": f"Salida {_res(cur)} por debajo de la nativa {_res(nat)}: Steam → Ajustes → Pantalla → Resolución"})
    elif cur and pref and _res(cur) == _res(pref) and pref.get("hz") and cur.get("hz") and cur["hz"] < pref["hz"] - 1:
        out.append({"code": "REFRESH_BELOW_BEST", "level": "info",
                    "text": f"Salida a {cur['hz']} Hz; esta conexión admite {pref['hz']} Hz a la misma resolución"})
    elif cur and nat:
        out.append({"code": "OUTPUT_NATIVE", "level": "ok", "text": f"Salida a la resolución nativa {_res(cur)} @ {cur['hz']} Hz"})
    if nat and _area(nat) >= 3840 * 2160 and not any(m["w"] >= 3840 and (m["hz"] or 0) >= 50 for m in p.get("available_modes", [])) \
            and p.get("modes_source") == "steam":
        out.append({"code": "LINK_LIMITS_4K60", "level": "warn",
                    "text": "La conexión no admite 4K a 60 Hz (cable o adaptador DisplayPort → HDMI sin HDMI 2.0)"})
    if game_default and nat:
        if _area(game_default) < _area(nat):
            out.append({"code": "STEAM_GAME_DEFAULT_BELOW_NATIVE", "level": "warn" if game_policy == "steam" else "info",
                        "text": f"Steam da a los juegos {_res(game_default)} por defecto (pantalla {_res(nat)})"
                                + (": STONE pone «Nativa» en cada juego" if game_policy == "native" else "")})
        else:
            out.append({"code": "STEAM_GAME_DEFAULT_NATIVE", "level": "ok",
                        "text": f"Steam da a los juegos la resolución de la pantalla ({_res(game_default)})"})
    for name, s in sorted((xw or {}).items()):
        if nat and cur and _area(s) < _area(cur) and name != ":0":
            out.append({"code": "GAME_SCREEN_BELOW_OUTPUT", "level": "info",
                        "text": f"Pantalla de juegos {name}: {_res(s)} (salida {_res(cur)})"})
    return out


def snapshot(kernel, steam_state=None, xw=None, game_policy="native", goal="fluid"):
    """The whole STONE DISPLAY view from already gathered sources (pure: tests feed it mocks)."""
    sds = (steam_state or {}).get("displays") or []
    names = [c["connector"] for c in kernel]
    caps = [capabilities(c, _match_steam(c, sds, names) if c.get("status") == "connected" else None) for c in kernel]
    p = primary(caps)
    pref = preferred_mode(p) if p else None
    gd = (steam_state or {}).get("game_default")
    gn = (steam_state or {}).get("game_native")
    findings = evaluate(p, pref, gd, gn, xw, game_policy)
    lk = link.plan(p, goal) if p else None
    if lk and lk.get("advice"):
        findings.append({"code": "LINK_LIMITS_MODES", "level": "warn", "text": lk["advice"]})
    safe = p is None or not p.get("edid_ok")
    nat = (p or {}).get("native_resolution")
    # The size games see: the display's native one, unless the link cannot carry it; then the best the link can.
    tgt = nat
    if lk and lk.get("target"):
        t = lk["target"]
        if lk.get("goal") == "max_res" or (nat and (not link.reachable(lk, nat["w"], nat["h"]) or
                                                     (lk.get("best_fluid") and _area(nat) > _area(lk["best_fluid"])))):
            tgt = t
    if game_policy == "native" and tgt:
        game_env = {"w": tgt["w"], "h": tgt["h"], "source": "policy: native" if tgt is nat else "policy: best the link carries"}
    elif gd:
        game_env = {"w": gd["w"], "h": gd["h"], "source": "steam default"}
    else:
        game_env = None
    return {
        "displays": caps, "primary": p, "preferred_mode": pref,
        "output": (p or {}).get("current_resolution"), "final_output": (p or {}).get("current_resolution"),
        "game_environment": game_env, "steam_game_default": gd, "steam_game_native": gn,
        "xwayland": xw or {}, "game_policy": game_policy, "link": lk, "goal": goal, "safe_display_mode": safe,
        "steam_available": steam_state is not None, "findings": findings,
    }


def signature(snap):
    """What counts as a display change for logging and events."""
    p = snap.get("primary") or {}
    return (p.get("display_id"), _res(p.get("native_resolution")), _res(p.get("current_resolution")),
            (p.get("current_resolution") or {}).get("hz"), len([d for d in snap["displays"] if d["connected"]]))
