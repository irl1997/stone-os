/* ---------- STONE PERFORMANCE API ----------
   The UI's only door to performance: stoned (D-Bus org.stonemachine.Performance1, docs/STONED.md),
   reached through STONE Home's host process (stone_home/bridge.py). The host accepts a fixed list
   of methods with typed arguments; stoned validates every value again and answers with the state
   read back from the GPU governor. The UI renders the state that comes back, never what it asked for. */
