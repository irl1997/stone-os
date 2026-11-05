/* ---------- Transport to STONE Home's host process ----------
   WebKit shell (stone_home/app.py, the no-GPU fallback): its message handler, answered asynchronously.
   STONE OS (Chromium shell) and development: the same requests over local HTTP, events over
   Server-Sent Events (stone_home/server.py). Paths are relative: the console serves the page
   under a per-start secret prefix. Requests and answers are identical in both. */
const Bridge = (() => {
  const wk = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.stone;
  const http = !wk && location.protocol.startsWith('http');
  const listeners = {};
  const emit = (ev, d) => (listeners[ev] || []).forEach(f => { try { f(d); } catch (e) { console.error(e); } });
  window.__stoneEvent = (ev, d) => emit(ev, d);
  if (http) {
    const es = new EventSource('events');
    es.onmessage = m => { try { const { name, data } = JSON.parse(m.data); emit(name, data); } catch (e) { /* ignore a bad frame */ } };
  }
  async function call(method, ...args) {
    let r;
    try {
      if (wk) r = await wk.postMessage({ method, args });
      else if (http) r = await (await fetch('api', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method, args }) })).json();
      else throw new Error('STONE Home host not found');
    } catch (e) { throw new PerfError('ServiceUnavailable', String(e)); }
    if (!r || !r.ok) throw new PerfError((r && r.code) || 'ServiceUnavailable', r && r.message);
    return r.result;
  }
  const artUrl = (appid, kind) => http ? `art/${appid}/${kind}` : `stone://art/${appid}/${kind}`;
  return { call, on: (ev, f) => { (listeners[ev] = listeners[ev] || []).push(f); }, artUrl };
})();
const PerfAPI = { on: Bridge.on };
['getCapabilities', 'getState', 'getMetrics', 'homeReady', 'setProfile', 'setSessionProfile', 'startTest', 'cancelTest',
  'measureBaseline', 'saveTestResult', 'discardTestResult', 'restoreDefaults', 'enterSafeMode', 'exitSafeMode',
  'setGameProfile', 'subscribe', 'unsubscribe', 'getCalibration', 'startCalibration', 'cancelCalibration'].forEach(m => { PerfAPI[m] = (...a) => Bridge.call('perf.' + m, ...a); });
/* Everything else the console needs from the system: Steam library and launching, power, versions, settings file. */
const Sys = {
  library: () => Bridge.call('library'), launch: appid => Bridge.call('launch', appid), openSteam: k => Bridge.call('openSteam', k),
  power: a => Bridge.call('power', a), info: () => Bridge.call('systemInfo'),
  prefsGet: () => Bridge.call('prefs.get'), prefsSet: p => Bridge.call('prefs.set', p),
  openSteamSettings: s => Bridge.call('openSteamSettings', s), restart: () => Bridge.call('restart'),
  install: appid => Bridge.call('install', appid), emulators: () => Bridge.call('emulators'), emuInstall: k => Bridge.call('emuInstall', k),
  display: fresh => Bridge.call('display.status', fresh), displayPolicy: p => Bridge.call('display.gamePolicy', p), displayGoal: g => Bridge.call('display.goal', g),
  /* STONE OPTIMIZED (stone_games/): the game profile behind JUGAR */
  gameRecommended: (appid, mode) => Bridge.call('game.recommended', ...(mode ? [appid, mode] : [appid])),
  gameProfile: appid => Bridge.call('game.profile', appid), gameModes: appid => Bridge.call('game.modes', appid),
  gameApply: (appid, mode, cfg) => Bridge.call('game.apply', ...(cfg ? [appid, mode, cfg] : [appid, mode])),
  gameImage: appid => Bridge.call('game.image', appid), gameSetImage: (appid, key, preset) => Bridge.call('game.setImage', ...(preset ? [appid, key, preset] : [appid, key])),
  components: kind => Bridge.call('components', ...(kind ? [kind] : [])), gamePlan: (appid, kind) => Bridge.call('game.installPlan', appid, kind),
  gameInstall: appid => Bridge.call('game.install', appid), gameUninstall: appid => Bridge.call('game.uninstallImage', appid),
  gameOptStatus: appid => Bridge.call('game.optimizeStatus', appid), gameOptStart: appid => Bridge.call('game.optimizeStart', appid),
  gameStatus: () => Bridge.call('game.status'), gameRestore: appid => Bridge.call('game.restoreRecommended', appid)
};
async function sys(method, ...a) {
  try { return await Sys[method](...a); }
  catch (e) { play('error'); toast(ERR_TEXT[e.code] || e.message, false); return null; }
}
