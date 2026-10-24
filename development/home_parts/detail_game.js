/* ---------- DETAIL ---------- */
let curG = 0;
function enterDetail(i, opts = {}) {
  curG = i; const g = G[i], running = S.running === i;
  S.screen = 'detail';
  const prim = running ? 'REANUDAR' : 'JUGAR';
  const sec = [[g.fav ? 'Quitar de favoritos' : 'Favorito', 'fav'], [`Rendimiento: ${g.perf ? profOf(g.perf).n : 'Global'}`, 'perf'], ['Propiedades', 'opt']];
  $('#detail').innerHTML = `
    <div class="col"><div class="origin">${esc(g.o)}</div><h1>${esc(g.n.toUpperCase())}</h1>
      <div class="stats">${playText(g)} · Última sesión: ${running ? 'ahora' : g.last}</div></div>
    <div class="primary txt-f" data-f data-act="primary" id="dprim">${prim}</div>
    <div class="second">${sec.map(([s, a]) => `<span class="txt-f" data-f data-act="${a}">${s}</span>`).join('')}</div>
    <div class="info"><div><span>Tamaño</span><b>${g.sz}</b></div><div><span>Origen</span><b>${esc(g.o)}</b></div><div><span>Estado</span><b>${g.upd ? 'Actualizando en Steam' : g.inst ? 'Instalado' : 'Pendiente en Steam'}</b></div><div><span>Al iniciarse</span><b>${g.perf ? profOf(g.perf).n : 'Perfil global'}${S.safeMode && g.perf ? ' (en pausa: Safe Mode)' : ''}</b></div></div>`;
  showScreen('detail'); chrome(true, false);
  bgGame(g, true, 'side');
  const d = $('#detail');
  NAV = mkNav([[d.querySelector('#dprim')], [...d.querySelectorAll('.second [data-f]')]], {
    r: opts.r || 0, c: opts.c || 0,
    place: (el, b, r) => ({ x: b.x, y: b.y + b.h + (r === 0 ? 16 : 12), w: b.w })
  });
  NAV.id = 'detail';
  requestAnimationFrame(() => applyFocus());
  hints([['A', prim.charAt(0) + prim.slice(1).toLowerCase()], ['B', 'Volver']]);
}
function detailA(el) {
  const g = G[curG], a = el.dataset.act;
  if (a === 'primary') { play('select'); return S.running === g.i ? resumeGame() : launch(g); }
  if (a === 'opt') { play('select'); return toast('Propiedades, logros y compatibilidad: en la ficha del juego en Steam', false); }
  if (a === 'fav') {
    play('select'); g.fav = !g.fav; if (g.fav) favs.add(g.appid); else favs.delete(g.appid); savePrefs();
    enterDetail(curG, { r: 1, c: 0 }); return toast(g.fav ? 'Añadido a favoritos' : 'Quitado de favoritos', false);
  }
  if (a === 'perf') {
    play('nav'); const list = [null, ...usable()], k = list.indexOf(g.perf || null), next = list[(k + 1) % list.length];
    return api('setGameProfile', g.appid, next || 'global').then(st => {
      if (S.screen === 'detail') enterDetail(curG, { r: 1, c: 1 });
      if (st) toast(g.perf ? `${g.n} usará ${profOf(g.perf).n} al iniciarse` : `${g.n} usará el perfil global`, false);
    });
  }
}

/* ---------- GAME ----------
   Steam starts the game; Gamescope puts it in front of STONE Home. stoned sees the session through
   Steam's reaper process and reports it in its state, which is what this screen follows: when the
   game ends, Home comes back. */
let tagT = 0, launchT = 0;
async function launch(g) {
  if (S.launching !== null) return;
  const ok = await sys('launch', g.appid);
  if (!ok) return;
  S.launching = g.i; S.running = g.i; S.screen = 'transition';
  recent = [g.i, ...recent.filter(i => i !== g.i)].slice(0, 6);
  const b = stageBox(focusEl());
  chrome(false, false); $('#hints').classList.add('hide');
  $('#black').classList.remove('fast'); $('#black').classList.add('on');
  setLine(b.x + b.w / 2, b.y + b.h + 16, 0, { slow: true });
  await wait(1200);
  line.classList.add('off');
  showGame(g, true);
  /* If stoned does not see the game within 90 s (Steam updating it first, or stoned not running),
     STONE stops waiting; B on this screen always goes back to Home. */
  clearTimeout(launchT); launchT = setTimeout(() => { S.launching = null; }, 90000);
}
/* Called from adopt(): stoned's view of the running game is the truth. */
function gameSync(st, before) {
  const appid = st.game ? st.game.appid : null;
  const i = appid ? G.findIndex(g => g.appid === appid) : -1;
  if (appid) {
    if (i >= 0) { S.running = i; if (S.launching === i) { S.launching = null; clearTimeout(launchT); } }
    return;
  }
  if (S.running !== null && S.launching === null) gameEnded(before);
}
function gameEnded(before) {
  const g = G[S.running]; S.running = null; setMetrics(false);
  const k = before ? slots.findIndex((sl, i) => sl && sl.state === 'validated' && before[i] !== 'validated') : -1;
  const back = ['game', 'quick'].includes(S.screen) || (S.screen === 'power' && powerFrom === 'game');
  if (back) {
    $('#quick').classList.remove('on'); $('#power').classList.remove('on');
    returnHome();
  }
  if (k >= 0) setTimeout(() => toast(`${g ? g.n + ' terminó. ' : ''}CUSTOM 0${k + 1} validado: la partida terminó sin errores de GPU`), back ? 1200 : 0);
  loadLibrary().then(() => { if (S.screen === 'home') enterHome(); });     /* play time and order changed */
}
function returnHome() {
  closeQuick(true);
  $('#black').classList.add('fast', 'on');
  setTimeout(() => {
    S.section = 0; if (NAV) NAV.id = ''; NAV = null;
    if (AC && AC.state !== 'running' && S.sound) AC.resume();
    enterHome({ r: 0, c: 0 }); $('#black').classList.remove('on');
  }, S.rm ? 120 : 450);
}
/* GAME MODE RESOURCE STATE: menu screens are unmounted, full-screen backgrounds released,
   the blur cache emptied, input polling slowed and metrics only if the overlay is on. */
function gameMode() {
  ['home', 'library', 'detail', 'settings', 'perf', 'tuning'].forEach(id => { $('#' + id).innerHTML = ''; });
  spreadEls = [];
  setBg('', 'none'); $('#bgA').style.backgroundImage = 'none'; $('#bgB').style.backgroundImage = 'none'; bgCur = '';
  trimArt();
  setTimeout(() => { if (S.screen === 'game') audioSleep(); }, 2500);
}
function showGame(g, fresh) {
  S.screen = 'game'; NAV = null;
  const gm = $('#game'), hu = heroUrl(g);
  gm.querySelector('.art').style.backgroundImage = hu ? `url("${hu}")` : neutralBg;
  const tag = gm.querySelector('.tag'); tag.textContent = `${g.n} · en curso. A: volver al juego · ☰: menú de STONE · B: inicio`;
  tag.classList.remove('gone'); clearTimeout(tagT); tagT = setTimeout(() => tag.classList.add('gone'), 6000);
  showScreen('game'); setBg('', 'none'); chrome(false, false);
  $('#hints').classList.add('hide');
  line.classList.add('off');
  setTimeout(() => $('#black').classList.remove('on'), fresh ? 200 : 0);
  setMetrics(S.tech);
  gameMode();
}
/* Steam brings a running game back to the front when it is asked to start it again. [VERIFICAR EN PLACA] */
function resumeGame() {
  closeQuick(true);
  const g = G[S.running]; if (!g) return returnHome();
  sys('launch', g.appid);
  $('#black').classList.add('fast', 'on');
  setTimeout(() => showGame(g, true), S.rm ? 120 : 450);
}
function metricsTick() {
  if (!S.tech || $('#metrics').hidden) return;
  const id = effective(), m = S.metrics;
  const lim = S.actual && !S.actual.error ? `Límite ${S.actual.max} MHz · ${S.actual.temp} °C (regulador)` : noLimitText();
  $('#metrics').textContent = `STONE ${profOf(id).n}\n${lim}\n` + (m ? `GPU ${nv(m.gpu_temp_c, ' °C')}   ${nv(m.gpu_clock_mhz, ' MHz')}   uso ${nv(m.gpu_busy_pct, ' %')}\nCPU ${nv(m.cpu_temp_c, ' °C')}   uso ${nv(m.cpu_pct, ' %')}   GPU ${nv(m.gpu_power_w, ' W')}\nVentilador ${nv(m.fan_rpm, ' RPM')} · curva de la BIOS\n` : 'Leyendo…\n')
    + 'FPS y frametime: botón ··· → Rendimiento (Steam)';
}
function setMetrics(on) {
  $('#metrics').hidden = !on;
  if (on) metricsTick();
  telemetrySync();        /* the overlay subscribes at 0.5 s; closed, nothing is read */
}
