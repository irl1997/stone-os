/* ---------- Transport to STONE Home's host process ----------
   STONE OS: WebKit message handler of stone-home (stone_home/app.py), answered asynchronously.
   Development and CI: the same requests over HTTP, events over Server-Sent Events
   (development/stone_home_devserver.py). Requests and answers are identical in both. */
const Bridge = (() => {
  const wk = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.stone;
  const http = !wk && location.protocol.startsWith('http');
  const listeners = {};
  const emit = (ev, d) => (listeners[ev] || []).forEach(f => { try { f(d); } catch (e) { console.error(e); } });
  window.__stoneEvent = (ev, d) => emit(ev, d);
  if (http) {
    const es = new EventSource('/events');
    es.onmessage = m => { try { const { name, data } = JSON.parse(m.data); emit(name, data); } catch (e) { /* ignore a bad frame */ } };
  }
  async function call(method, ...args) {
    let r;
    try {
      if (wk) r = await wk.postMessage({ method, args });
      else if (http) r = await (await fetch('/api', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ method, args }) })).json();
      else throw new Error('STONE Home host not found');
    } catch (e) { throw new PerfError('ServiceUnavailable', String(e)); }
    if (!r || !r.ok) throw new PerfError((r && r.code) || 'ServiceUnavailable', r && r.message);
    return r.result;
  }
  const artUrl = (appid, kind) => http ? `/art/${appid}/${kind}` : `stone://art/${appid}/${kind}`;
  return { call, on: (ev, f) => { (listeners[ev] = listeners[ev] || []).push(f); }, artUrl };
})();
const PerfAPI = { on: Bridge.on };
['getCapabilities', 'getState', 'getMetrics', 'homeReady', 'setProfile', 'setSessionProfile', 'startTest', 'cancelTest',
  'measureBaseline', 'saveTestResult', 'discardTestResult', 'restoreDefaults', 'enterSafeMode', 'exitSafeMode',
  'setGameProfile', 'subscribe', 'unsubscribe'].forEach(m => { PerfAPI[m] = (...a) => Bridge.call('perf.' + m, ...a); });
/* Everything else the console needs from the system: Steam library and launching, power, versions, settings file. */
const Sys = {
  library: () => Bridge.call('library'), launch: appid => Bridge.call('launch', appid), openSteam: k => Bridge.call('openSteam', k),
  power: a => Bridge.call('power', a), info: () => Bridge.call('systemInfo'),
  prefsGet: () => Bridge.call('prefs.get'), prefsSet: p => Bridge.call('prefs.set', p),
  openSteamSettings: s => Bridge.call('openSteamSettings', s), restart: () => Bridge.call('restart'),
  install: appid => Bridge.call('install', appid), emulators: () => Bridge.call('emulators'), emuInstall: k => Bridge.call('emuInstall', k)
};
async function sys(method, ...a) {
  try { return await Sys[method](...a); }
  catch (e) { play('error'); toast(ERR_TEXT[e.code] || e.message, false); return null; }
}
