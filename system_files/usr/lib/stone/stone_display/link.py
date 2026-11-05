"""STONE DISPLAY link budget: the best picture this connection can actually carry.

The display may accept 4K60 and the link (DisplayPort -> HDMI adapter, cable) may not. This
module ranks the modes the display offers against what the link can carry and answers:

  best_fluid     the highest resolution at 60 Hz or more that fits  (what games should use)
  best_max_res   the highest resolution at any refresh that fits    (e.g. 4K30: not good for games)
  reaches_4k60   True only if a >=3840 wide mode at >=50 Hz fits
  limited_by_link  the display offers a mode the link cannot carry
  target         best_fluid or best_max_res, by the person's goal ("fluid" | "max_res")

The budget (pixel clock the link carries) comes from, in this order:
  - Steam's own list of modes: it already validated them on this link  -> no budget needed
  - the HDMI sink's maximum TMDS clock / FRL rate from the EDID
  - nothing known (native DisplayPort, no EDID data)                    -> the kernel's list decides
Pixel clocks are ESTIMATES (CEA table for the common modes, reduced-blanking formula otherwise,
with a 3 % margin) and are labelled as such. Nothing here changes the display.
"""

POLICY_MAX_HZ = 144
MARGIN = 1.03

# Exact CEA-861 pixel clocks (MHz) of the modes where blanking is standardised.
CEA_CLOCK = {
    (1920, 1080, 24): 74.25, (1920, 1080, 30): 74.25, (1920, 1080, 50): 148.5, (1920, 1080, 60): 148.5,
    (1920, 1080, 100): 297.0, (1920, 1080, 120): 297.0,
    (3840, 2160, 24): 297.0, (3840, 2160, 25): 297.0, (3840, 2160, 30): 297.0, (3840, 2160, 50): 594.0,
    (3840, 2160, 60): 594.0, (3840, 2160, 100): 1188.0, (3840, 2160, 120): 1188.0,
    (1280, 720, 60): 74.25, (1280, 720, 120): 148.5,
}
FRL_FULL_GBPS = 18.0          # an 18 Gbps FRL link carries what 600 MHz of TMDS does


def pixel_clock_mhz(w, h, hz):
    """Estimated pixel clock of a mode, in MHz (before the safety margin)."""
    k = CEA_CLOCK.get((w, h, int(round(hz))))
    if k:
        return k
    return (w + 80) * (h + 46) * hz / 1e6          # reduced blanking, close to CVT-RB v2


def need_mhz(m):
    """Clock a mode needs: exact for the standard CEA modes, with a safety margin for the estimated ones."""
    exact = (m["w"], m["h"], int(round(m["hz"]))) in CEA_CLOCK
    return pixel_clock_mhz(m["w"], m["h"], m["hz"]) * (1.0 if exact else MARGIN)


def budget(cap):
    """(MHz or None, source). None = no figure: do not filter by clock."""
    if cap.get("modes_source") == "steam":
        return None, "validado por Steam"
    frl = cap.get("hdmi_frl_gbps")
    if frl:
        return 600.0 * frl / FRL_FULL_GBPS * 0.95, f"HDMI FRL {frl} Gbps (estimado)"
    tmds = cap.get("hdmi_max_tmds_mhz")
    if tmds:
        return float(tmds), f"HDMI hasta {tmds} MHz TMDS"
    return None, "sin dato de la conexión (se usa la lista del kernel)"


def _area(m):
    return m["w"] * m["h"]


def plan(cap, goal="fluid"):
    """The link plan for one display capability dict (model.capabilities)."""
    modes = []
    for m in cap.get("available_modes") or []:
        if not (m.get("w") and m.get("h")):
            continue
        if m.get("hz"):
            if m["hz"] <= POLICY_MAX_HZ:
                modes.append(m)
        elif cap.get("modes_source") == "kernel+edid":
            # The kernel accepts the size but the EDID names no rate for it: 60 Hz is the standard rate, flagged as assumed.
            modes.append({"w": m["w"], "h": m["h"], "hz": 60, "hz_assumed": True})
    if not modes:
        return None
    bud, src = budget(cap)
    fits = [m for m in modes if bud is None or need_mhz(m) <= bud]
    out = {"budget_mhz": round(bud) if bud else None, "budget_source": src, "estimated": cap.get("modes_source") != "steam"}

    def best(ms):
        if not ms:
            return None
        m = max(ms, key=lambda x: (_area(x), x["hz"]))
        return {"w": m["w"], "h": m["h"], "hz": m["hz"], "need_mhz": round(need_mhz(m)), "hz_assumed": bool(m.get("hz_assumed"))}
    out["best_fluid"] = best([m for m in fits if m["hz"] >= 59])
    out["best_max_res"] = best(fits)
    wants = [m for m in modes if m["w"] >= 3840 and m["hz"] >= 50]
    out["display_offers_4k60"] = bool(wants)
    out["reaches_4k60"] = any(m["w"] >= 3840 and m["hz"] >= 50 for m in fits)
    lost = [m for m in modes if m not in fits]
    out["limited_by_link"] = bool(lost) and bud is not None
    out["unreachable"] = [{"w": m["w"], "h": m["h"], "hz": m["hz"], "need_mhz": round(need_mhz(m))}
                          for m in sorted(lost, key=lambda x: (-_area(x), -x["hz"]))[:6]]
    out["target"] = out["best_max_res"] if goal == "max_res" else (out["best_fluid"] or out["best_max_res"])
    out["goal"] = "max_res" if goal == "max_res" else "fluid"
    out["advice"] = advice(out, bud)
    return out


def advice(p, bud):
    if p["display_offers_4k60"] and not p["reaches_4k60"] and bud is not None:
        return (f"La pantalla acepta 4K a 60 Hz, pero esta conexión solo lleva {round(bud)} MHz y 4K60 necesita unos 600. "
                "Para 4K a 60 Hz usa un adaptador ACTIVO DisplayPort 1.4 → HDMI 2.0 (que diga «4K 60 Hz») y un cable HDMI de alta velocidad. "
                "Mientras tanto STONE usa " + (_fmt(p["best_fluid"]) or "la mejor salida disponible") + ".")
    if p["limited_by_link"] and not p["reaches_4k60"]:
        return "Algunos modos de la pantalla no caben en esta conexión; STONE usa solo los que sí caben."
    return None


def _fmt(m):
    return f"{m['w']}x{m['h']}@{m['hz']}" if m else None


def reachable(p, w, h):
    """True if a resolution this size (or larger) can be driven: it is not above the best the link carries."""
    t = (p or {}).get("best_max_res")
    return True if not t else w * h <= t["w"] * t["h"]
