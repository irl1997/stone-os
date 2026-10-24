function openQuick() {
  if (S.screen !== 'game') return;
  S.screen = 'quick'; play('select');
  const q = $('#quick .panel'), g = G[S.running];
  q.innerHTML = `<div class="top"><span class="brand">STONE</span><span>${$('#clock').textContent}</span></div>
    <div class="main txt-f" data-f data-act="resume">REANUDAR</div>
    <div class="it txt-f" data-f data-act="home">Volver a STONE</div>
    <div class="seg"><span class="lbl txt-f" data-f data-act="profile">Rendimiento · ${profOf(effective()).n}${String(effective()).startsWith('c') && profOf(effective()).state !== 'validated' ? ' (provisional)' : ''}${S.safeMode ? ' · SAFE MODE' : ''}</span>
      <span id="qread" style="font-size:18px;color:var(--text-2);font-variant-numeric:tabular-nums">${S.actual && !S.actual.error ? `GPU hasta ${S.actual.max} MHz · ${S.actual.temp} °C` : noLimitText()} · ${quickReadings(S.metrics)}</span>
      <div class="opts">${['silent', 'balanced', 'performance', 'custom'].map(p => { const cur = p === 'custom' ? String(effective()).startsWith('c') : effective() === p; const label = p === 'custom' ? (String(effective()).startsWith('c') ? profOf(effective()).n : 'CUSTOM') : FACT[p].n; return `<span class="${cur ? 'cur' : ''}" style="font-size:15px;letter-spacing:.06em">${label}</span>`; }).join('')}</div></div>
    <div class="rule"></div>
    <div class="steam"><span class="cap">MANDO, AUDIO, BLUETOOTH Y RED: BOTÓN ··· DE STEAM</span>
      <span class="it txt-f" data-f data-act="tech">Modo técnico <span class="tog">${S.tech ? 'activado' : 'desactivado'}</span></span>
      <span class="it txt-f" data-f data-act="power">Apagar</span></div>
    ${g ? `<div style="font-size:18px;color:var(--text-2)">${esc(g.n)} sigue abierto</div>` : ''}`;
  $('#quick').classList.add('on');
  PerfAPI.getMetrics().then(m => { S.metrics = m; const q = $('#qread'); if (q && S.actual && !S.actual.error) q.textContent = `GPU hasta ${S.actual.max} MHz · ${S.actual.temp} °C · ${quickReadings(m)}`; }).catch(() => {});
  const items = [...q.querySelectorAll('[data-f]')];
  NAV = mkNav(items.map(e => [e]), { r: 0, place: (el, b) => ({ x: b.x, y: b.y + b.h + 8, w: b.w }) });
  NAV.id = 'quick';
  setTimeout(() => { line.classList.remove('off'); applyFocus(); }, S.rm ? 60 : 260);
  hints([['A', 'Elegir'], ['B', 'Cerrar']]);
}
function quickReadings(m) { return m ? `GPU ${nv(m.gpu_temp_c, ' °C')} · uso ${nv(m.gpu_busy_pct, ' %')}` : 'leyendo…'; }
function closeQuick(silent) {
  $('#quick').classList.remove('on'); line.classList.add('off');
  setTimeout(() => { if (!$('#quick').classList.contains('on')) $('#quick .panel').innerHTML = ''; }, 700);
  if (!silent) { play('back'); S.screen = 'game'; NAV = null; $('#hints').classList.add('hide'); }
}
function quickA(el) {
  const a = el.dataset.act;
  if (a === 'resume') { play('select'); return resumeGame(); }
  if (a === 'home') { play('select'); return returnHome(); }
  if (a === 'profile') { play('select'); return api('setSessionProfile', nextSession(1)).then(() => { if (S.screen === 'quick') refreshQuick(); }); }
  if (a === 'tech') { play('select'); S.tech = !S.tech; savePrefs(); setMetrics(S.tech); return refreshQuick(); }
  if (a === 'power') { play('select'); closeQuick(true); S.screen = 'game'; return openPower(); }
}
