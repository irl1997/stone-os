/* ---------- Self-test (stone-home --selftest, CI and first check on the board) ---------- */
async function selfTest() {
  const r = { pass: false, checks: {} }, c = r.checks;
  const t = async (k, f) => { try { c[k] = await f(); } catch (e) { c[k] = 'error: ' + (e.code || e.message); } };
  await t('state', async () => { const st = await PerfAPI.getState(); return !!st && typeof st.profile === 'string'; });
  await t('capabilities', async () => { const cp = await PerfAPI.getCapabilities(); return { board: cp.board, governor: cp.governor, advanced: cp.advanced }; });
  await t('library', async () => (await Sys.library()).games.length);
  await t('prefs', async () => typeof (await Sys.prefsGet()).sound === 'boolean');
  await t('info', async () => !!(await Sys.info()).release);
  c.audio = !!(window.AudioContext || window.webkitAudioContext);
  c.gamepad_api = typeof navigator.getGamepads === 'function';
  await (document.fonts ? document.fonts.load('300 20px Jost') : Promise.resolve());
  c.font = document.fonts ? document.fonts.check('300 20px Jost') : false;
  r.pass = c.state === true && c.prefs === true && typeof c.library === 'number' && c.info === true && c.font === true;
  await Bridge.call('selftest', r);
}

/* ---------- Start ---------- */
applyPrefs();
const ready = document.fonts && document.fonts.ready ? Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 2500))]) : Promise.resolve();
ready.then(() => {
  stage.focus({ preventScroll: true });
  touched(); pollPads(); if (padOn) ensureLoop();
  if (location.hash === '#selftest') return selfTest();
  audio(); boot();
});
