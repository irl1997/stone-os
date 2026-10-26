/* ---------- Asleep while a game is in front ----------
   STONE stays open behind the game. While a game runs and STONE's window is not the focused one
   (Gamescope gives focus to the game), STONE draws nothing and reads no controller: animations
   and transitions are paused, the controller loop stops, the interface sound is suspended and
   live readings are unsubscribed. Chromium then produces no frames at all, so the GPU and CPU
   are the game's. Focus back (game closed, or STONE chosen from Steam) wakes it at once. */
function dormantCheck() {
  const on = S.running !== null && S.running !== undefined && !document.hasFocus();
  if (!!S.dormant === on) return;
  S.dormant = on;
  document.body.classList.toggle('dormant', on);
  console.info(`[stone] ${on ? 'asleep: game in front' : 'awake'}`);
  if (on) {
    if (loopH) { cancelAnimationFrame(loopH); clearTimeout(loopH); loopH = 0; }
    stopHold();
    audioSleep();
    if (telCtx) { telCtx = null; PerfAPI.unsubscribe().catch(() => {}); }
  } else {
    pollPads(); ensureLoop(); telemetrySync();
  }
}
window.addEventListener('blur', dormantCheck);
window.addEventListener('focus', dormantCheck);

/* One line in home.log at start: the resolution STONE is drawn at and the GPU drawing it
   ("SwiftShader" or "llvmpipe" would mean no GPU). */
(function drawProbe() {
  try {
    const c = document.createElement('canvas'), gl = c.getContext('webgl');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    const gpu = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : (gl ? 'webgl' : 'none');
    console.info(`[stone] draw: ${Math.round(innerWidth * devicePixelRatio)}x${Math.round(innerHeight * devicePixelRatio)} dpr=${devicePixelRatio} gpu=${gpu}`);
    const lose = gl && gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
  } catch (e) { console.warn('[stone] draw: ' + e); }
})();

