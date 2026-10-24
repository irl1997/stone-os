function adopt(st) {
  if (!st || st.profile === undefined) return;
  const before = slots.map(s => s && s.state);
  S.st = st;
  S.prof = st.profile; S.lkg = st.last_known_good; S.safeMode = st.safe_mode; S.actual = st.actual; S.game = st.game || null;
  S.provisional = st.provisional; S.pending = st.pending_result || null; S.thermal = st.thermal || null;
  S.safeReason = st.safe_reason || null;
  if (st.tests) lastTests = st.tests;
  const sb = document.getElementById('safeBadge'); if (sb) sb.hidden = !st.safe_mode;
  st.slots.forEach((s, k) => { slots[k] = s.state === 'empty' ? null : { max: s.max, min: s.min, temp: s.temp, resp: s.resp, state: s.state, test_level: s.test_level }; });
  G.forEach(g => { g.perf = st.per_game[g.appid] || null; });
  gameSync(st, before);
}
async function api(method, ...args) {
  try { const st = await PerfAPI[method](...args); adopt(st); return st; }
  catch (e) {
    if (!(e instanceof PerfError)) throw e;
    if (e.code === 'ServiceUnavailable') S.actual = { error: 'ServiceUnavailable' };
    play('error'); toast(ERR_TEXT[e.code] || e.message, false);
    return null;
  }
}
/* stoned restarted (or came up late): it has forgotten that Home is on screen and who was reading metrics. */
Bridge.on('service', d => {
  if (!d.up) { S.actual = { error: 'ServiceUnavailable' }; if (['perf', 'tuning'].includes(S.screen)) $(`#${S.screen} .pstatus`).innerHTML = statusLine(S.prof); return; }
  telCtx = null;
  refreshCaps().then(() => api('homeReady')).then(() => { telemetrySync(); if (['perf', 'tuning'].includes(S.screen)) $(`#${S.screen} .pstatus`).innerHTML = statusLine(S.prof); });
});
