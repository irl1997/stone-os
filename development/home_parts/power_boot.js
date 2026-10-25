/* ---------- POWER ---------- */
let powerFrom = 'home';
function openPower() {
  powerFrom = S.screen === 'game' ? 'game' : S.screen;
  S.screen = 'power';
  const box = $('#power .box');
  box.innerHTML = `<div class="h">ENERGÍA</div>
    <div class="main txt-f" data-f data-act="off">APAGAR</div>
    <div class="txt-f" style="font-size:26px" data-f data-act="reboot">Reiniciar</div>
    ${S.running !== null && G[S.running] ? `<div class="warn">${esc(G[S.running].n)} se cerrará al apagar o reiniciar.</div>` : ''}`;
  $('#power').classList.add('on'); line.classList.remove('off');
  const items = [...box.querySelectorAll('[data-f]')];
  NAV = mkNav(items.map(e => [e]), { place: (el, b) => ({ x: b.x, y: b.y + b.h + 8, w: b.w }) });
  NAV.id = 'power';
  setTimeout(() => applyFocus(), S.rm ? 60 : 200);
  hints([['A', 'Elegir'], ['B', 'Cerrar']]);
}
function closePower() {
  play('back'); $('#power').classList.remove('on');
  if (powerFrom === 'game') { S.screen = 'game'; NAV = null; line.classList.add('off'); $('#hints').classList.add('hide'); return; }
  if (powerFrom === 'settings') return enterSettings();
  if (NAV) NAV.id = '';
  enterHome({ r: 1, c: 3 });
}
async function powerA(el) {
  const a = el.dataset.act; play('select');
  $('#power').classList.remove('on');
  if (a === 'off') return shutdown(false);
  if (a === 'reboot') return shutdown(true);
}
/* The animation plays, then logind powers off. stoned's clean-shutdown marker is written by
   systemd when its unit stops, whatever started the shutdown. */
async function shutdown(reboot) {
  S.screen = 'transition'; $('#hints').classList.add('hide'); play('stop');
  chrome(false, false);
  $('#black').classList.remove('fast'); $('#black').classList.add('on');
  setLine(720, 886, 480, { slow: true }); await wait(1000);
  setLine(960, 886, 0, { slow: true }); await wait(1300);
  line.classList.add('off');
  try {
    await Sys.power(reboot ? 'reboot' : 'off');
    setMetrics(false); NAV = null;
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('on')); setBg('', 'none');
    $('#offmsg').textContent = reboot ? 'Reiniciando…' : 'Apagando…'; $('#offmsg').classList.add('on');
  } catch (e) {
    $('#black').classList.remove('on'); line.classList.remove('off');
    S.section = 0; if (NAV) NAV.id = ''; enterHome({ r: 1, c: 3 });
    play('error'); toast(`No se pudo ${reboot ? 'reiniciar' : 'apagar'} (${ERR_TEXT[e.code] || e.message}). Usa el botón STEAM → Energía`, false);
  }
}
/* Apagar tras inactividad: only on STONE's own screens, never with a game open or a test running. */
let idleOffT = 0;
function armIdleOff() {
  clearTimeout(idleOffT);
  const min = IDLE_MIN[S.idle]; if (!min) return;
  idleOffT = setTimeout(() => {
    if (S.running !== null || S.game || testUI || ['game', 'quick', 'transition', 'dlg'].includes(S.screen)) return armIdleOff();
    shutdown(false);
  }, min * 60000);
}
let saveT = 0;
function savePrefs() {
  clearTimeout(saveT);
  saveT = setTimeout(() => Sys.prefsSet({ sound: S.sound, vol: S.vol, hc: S.hc, rm: S.rm, ts: S.ts, safe: S.safe, idle: S.idle,
    tech: S.tech, adv_ack: S.advAck, render: S.render, favorites: [...favs] }).catch(() => {}), 400);
}
function loadPrefs(p) {
  if (!p) return;
  S.sound = p.sound; S.vol = p.vol; S.hc = p.hc; S.rm = p.rm || reduceOS; S.ts = p.ts; S.safe = p.safe; S.idle = p.idle;
  S.tech = p.tech; S.advAck = p.adv_ack; S.render = p.render || 0; favs.clear(); (p.favorites || []).forEach(a => favs.add(a));
  applyPrefs();
}

/* ---------- BOOT ----------
   Plymouth and Steam already showed the line while the machine started; this is STONE Home
   arriving. The animation runs while the library, settings and stoned answer, so it adds
   no waiting of its own; Home appears as soon as both are done. */
async function boot() {
  S.screen = 'transition';
  $('#black').classList.add('on'); line.classList.add('boot');
  const prefs = Sys.prefsGet().then(loadPrefs).catch(() => {});
  const prep = Promise.all([prefs, refreshCaps(), Sys.info().then(i => { S.info = i; }).catch(() => {}), prefs.then(loadLibrary)]);
  /* Home is about to be on screen: only now may stoned apply the saved profile (factory or validated) */
  const home = prep.then(() => PerfAPI.homeReady()).then(st => { adopt(st); return st; })
    .catch(e => { if (e && e.code === 'ServiceUnavailable') S.actual = { error: 'ServiceUnavailable' }; return null; });
  /* The frame before this one (Plymouth, then STONE's Steam startup movie) is black with the full
     line at 82 % of the height and 25 % of the width: STONE Home starts on that same frame. */
  setLine(720, 886, 480); line.classList.remove('off');
  play('start');
  await wait(1000);
  $('#bootword').classList.add('on');
  await Promise.race([prep, new Promise(r => setTimeout(r, 8000))]).then(() => wait(700));
  $('#bootword').classList.remove('on');
  await wait(300);
  $('#black').classList.remove('on');
  S.section = 0; if (NAV) NAV.id = ''; NAV = null;
  if (S.running !== null && S.game) { showGame(G[S.running], true); }
  else enterHome({ r: 0, c: 0, slow: true });
  setTimeout(() => line.classList.remove('boot'), 1300);
  armIdleOff();
  home.then(st => { if (st) setTimeout(() => bootNotices(st), 1400); });
}
