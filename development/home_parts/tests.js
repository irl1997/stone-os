let testUI = null, lastTests = [], progT = 0;
const lastBaseline = () => [...lastTests].reverse().find(t => t.kind === 'baseline' && t.passed);
const mmss = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
/* Progress comes from stoned's own clock (state.testing); readings from its metrics, every 0.25 s during a test. */
function startProgress(st) {
  const t = st && st.testing; if (!t || !testUI) return;
  const t0 = performance.now() - (t.seconds || 0) * 1000, dur = (t.duration || 300) * 1000;
  clearInterval(progT);
  const tick = () => {
    if (!testUI || !$('#tLoad')) { clearInterval(progT); return; }
    const el = performance.now() - t0, p = Math.min(1, el / dur);
    setLine(testUI.bx.x, testUI.bx.y - 0.5, testUI.bx.w * p);
    $('#tLeft').textContent = p < 1 ? `Quedan ${mmss((dur - el) / 1000)}` : 'Terminando…';
    const m = S.metrics; if (!m) return;
    $('#tLoad').textContent = nv(m.gpu_busy_pct, ' %'); $('#tClk').textContent = nv(m.gpu_clock_mhz, ' MHz');
    $('#tTemp').textContent = nv(m.gpu_temp_c, ' °C'); $('#tPow').textContent = nv(m.gpu_power_w, ' W');
  };
  tick(); progT = setInterval(tick, 500);
}
const liveBox = () => `<div class="live"><div><span>Carga GPU</span><b id="tLoad">—</b></div><div><span>Frecuencia</span><b id="tClk">—</b></div><div><span>Temperatura</span><b id="tTemp">—</b></div><div><span>Consumo GPU</span><b id="tPow">—</b></div></div>`;
function runBaseline(ri) {
  api('measureBaseline', 'quick').then(st => {
    if (!st) return enterTuning({ ri });
    openDlg(`<h2>MEDICIÓN DE REFERENCIA</h2><p>Misma carga que la prueba rápida (5 minutos) sobre ${profOf(effective()).n}, sin cambiar nada</p>
    ${liveBox()}<div class="ptrack" id="tTrack"></div><p id="tMsg">Sin cambios en el regulador${st.actual && !st.actual.error ? `: GPU hasta ${st.actual.max} MHz` : ''} · <span id="tLeft"></span></p>`, { onA: () => {}, onB: () => {
      testUI = null; clearInterval(progT); api('cancelTest').then(() => { closeDlg(); play('back'); toast('Medición cancelada', false); enterTuning({ ri }); });
    } }, true);
    const bx = stageBox($('#tTrack')); line.classList.remove('off'); setLine(bx.x, bx.y - 0.5, 0);
    hints([['B', 'Cancelar']]);
    testUI = { ri, bx, baseline: true };
    startProgress(st);
  });
}
function baselineDone(ri, r) {
  play('notify');
  openDlg(`<h2>REFERENCIA GUARDADA</h2><p>${profOf(r.profile).n}: ${r.median_clock_mhz} MHz sostenidos, ${r.max_temp_c} °C como máximo, ${nv(r.avg_power_w, ' W')} de GPU.</p><p>Las próximas pruebas rápidas se compararán con esta medición.</p>
    <div class="btns"><span class="txt-f" data-f data-b="ok">ENTENDIDO</span></div>`, { onA: () => { closeDlg(); play('back'); enterTuning({ ri }); }, onB: () => { closeDlg(); play('back'); enterTuning({ ri }); } });
}
function comparisonText(c) {
  if (!c) return '';
  if (c.verdict === 'insufficient') return 'Para saber si mejora, mide antes el perfil actual con «Medir el perfil actual».';
  const base = profOf(c.baseline_profile).n, sg = v => (v > 0 ? '+' : '') + String(v).replace('.', ',');
  if (c.verdict === 'better') return `Frente a ${base}: ${sg(c.clock_gain_pct)} % de frecuencia sostenida, ${sg(c.temp_delta_c)} °C, ${sg(c.power_delta_w)} W.`;
  if (c.verdict === 'no_gain') return `Frente a ${base}: la frecuencia sostenida apenas cambia (${sg(c.clock_gain_pct)} %). Más techo no mejora el rendimiento en esta consola.`;
  return `Frente a ${base}: la GPU pasa más tiempo limitada por temperatura (${sg(c.throttle_delta_pts)} puntos) y sostiene ${sg(c.clock_gain_pct)} % de frecuencia. Aquí más frecuencia rinde peor.`;
}
function runTest(ri, level) {
  const vals = { max: D.max, min: D.min, temp: D.temp, resp: D.resp };
  api('startTest', D.slot, vals, level).then(st => {
    if (!st) { closeDlg(); return enterTuning({ ri }); }
    openDlg(`<h2>STONE TESTING</h2><p>Prueba ${level === 'quick' ? 'rápida (5 minutos)' : 'estándar (30 minutos)'} de GPU · CUSTOM 0${D.slot + 1}</p>
    ${liveBox()}<div class="ptrack" id="tTrack"></div><p id="tMsg">Aplicado en el regulador: GPU hasta ${st.actual.max} MHz · límite ${st.actual.temp} °C · <span id="tLeft"></span></p>`, { onA: () => {}, onB: () => {
      testUI = null; clearInterval(progT);
      api('cancelTest').then(() => { closeDlg(); play('back'); toast(`Prueba cancelada; no se guardó nada. Activo: ${profOf(S.prof).n}`, false); enterTuning({ ri }); });
    } }, true);
    const tr = $('#tTrack'), bx = stageBox(tr);
    line.classList.remove('off'); setLine(bx.x, bx.y - 0.5, 0);
    hints([['B', 'Cancelar prueba']]);
    testUI = { ri, bx, slot: D.slot };
    startProgress(st);
  });
}
PerfAPI.on('testFinished', r => {
  const t = testUI; testUI = null; clearInterval(progT);
  if (!t) return;
  api('getState').then(() => {
    if (r.kind === 'baseline') return baselineDone(t.ri, r);
    r.passed ? testPassed(t.ri, r) : testFailed(t.ri, r);
  });
});
