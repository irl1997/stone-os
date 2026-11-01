"""STONE dynamic performance controller: how much of the validated range a game gets right now.

The GPU governor (cyan-skillfish-governor-smu) already moves the clock with the load between
its minimum and the ceiling STONE sets, so a game that needs 1500 MHz stays near 1500 MHz even
under PERFORMANCE. This controller only moves the ceiling, inside [baseline, profile maximum],
and only for reasons the governor cannot see:

  thermal headroom   GPU within HOT_MARGIN_C of the limit for HOT_TICKS samples -> one step down;
                     at least COOL_MARGIN_C under it for COOL_TICKS samples -> one step back up.
                     (Hardware and governor thermal protections stay on; this is an upper layer.
                     The thermal watch still drops to SILENT on alerts.)
  frame target       when a frame-time source exists (a future STONE GAME PROFILE / Gamescope
                     stats) and the game holds its target with room to spare at a low GPU load,
                     one step down: the same frames for less power and noise. Below target at a
                     high load, one step back up. Without frame times this input is ignored.

Pure logic (decide) plus a small state object; the service calls it once per thermal tick
while a game runs. All values in MHz / C / ms.
"""

STEP_MHZ = 50
HOT_MARGIN_C = 3
HOT_TICKS = 3
COOL_MARGIN_C = 10
COOL_TICKS = 6
FRAME_SPARE = 0.85            # frame time under 85 % of the target's = room to spare
LOAD_LOW, LOAD_HIGH = 70.0, 95.0


class TrimState:
    def __init__(self):
        self.trim = 0             # MHz below the profile's maximum
        self.hot = 0
        self.cool = 0
        self.spare = 0

    def reset(self):
        self.__init__()


def decide(state, profile_max, baseline, temp_c, temp_limit_c, busy_pct=None, frame_ms=None, target_fps=None):
    """Update state.trim; return the ceiling to use (MHz) and why ("hot", "cool", "spare",
    "short", or None when nothing changed)."""
    floor = min(baseline, profile_max)
    room = profile_max - floor
    why = None
    if temp_c is not None and temp_c >= temp_limit_c - HOT_MARGIN_C:
        state.hot, state.cool = state.hot + 1, 0
        if state.hot >= HOT_TICKS and state.trim < room:
            state.trim = min(room, state.trim + STEP_MHZ)
            state.hot, why = 0, "hot"
    elif temp_c is not None and temp_c <= temp_limit_c - COOL_MARGIN_C:
        state.cool, state.hot = state.cool + 1, 0
    else:
        state.hot = state.cool = 0
    if why is None and frame_ms and target_fps:
        budget = 1000.0 / target_fps
        if frame_ms <= budget * FRAME_SPARE and busy_pct is not None and busy_pct < LOAD_LOW:
            state.spare += 1
            if state.spare >= COOL_TICKS and state.trim < room:
                state.trim = min(room, state.trim + STEP_MHZ)
                state.spare, why = 0, "spare"
        elif frame_ms > budget and (busy_pct is None or busy_pct >= LOAD_HIGH) and state.trim > 0 \
                and (temp_c is None or temp_c < temp_limit_c - HOT_MARGIN_C):
            state.trim = max(0, state.trim - STEP_MHZ)
            state.spare, why = 0, "short"
        else:
            state.spare = 0
    if why is None and state.cool >= COOL_TICKS and state.trim > 0 and not (frame_ms and target_fps):
        state.trim = max(0, state.trim - STEP_MHZ)
        state.cool, why = 0, "cool"
    return profile_max - state.trim, why
