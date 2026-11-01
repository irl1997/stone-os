"""STONE OPTIMIZED: STONE GAME PROFILE, STONE GRAPHICS and STONE OPTIMIZER.

    SELECT GAME -> DETECT DISPLAY -> LOAD GAME PROFILE -> VALIDATE -> APPLY GRAPHICS ->
    APPLY PERFORMANCE -> VERIFY -> LAUNCH -> MONITOR -> EXIT: SAVE METRICS, RESTORE, CLEAN

Modules (see STONE_OPTIMIZED.md):
  schema.py    the game profile: fields, validation, versions, statuses
  store.py     profile layers: built-in (image) < auto-generated (measured here) < user
  detect.py    facts about an installed game read from disk: install folder, version, anti-cheat,
               injected upscalers present (HelixSR, OptiScaler), Proton tool
  graphics.py  STONE GRAPHICS: which image paths really exist for a game, the launch options each
               needs, applying them reversibly and cleaning them afterwards
  metrics.py   frame metrics from MangoHud's CSV log (real frames only; nothing estimated)
  score.py     STONE EXPERIENCE SCORE
  optimizer.py experience targets, candidate matrix, measurement sessions, recommendation,
               crash handling

Runs inside STONE Home's host (the console user's session): it needs the user's Steam (launch
options through Steam's interface), the games' folders and their logs. GPU performance goes
through stoned (per-game profile), never directly. Mocks exist only in tests.
"""
