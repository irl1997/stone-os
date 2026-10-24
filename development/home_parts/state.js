/* ---------- State ---------- */
const reduceOS = matchMedia('(prefers-reduced-motion: reduce)').matches;
const S = {
  screen: 'transition', running: null, profile: 1, tech: false, sound: true, vol: 0.7,
  hc: false, rm: reduceOS, ts: 0, safe: 5, section: 0, filter: 0, from: 'home',
  prof: 'balanced', lkg: 'balanced', adv: false, advAck: false, idle: 2,
  caps: null, actual: null, safeMode: false, game: null, provisional: false, pending: null,
  info: null, libReady: false, libFound: true, launching: null, st: null
};
/* Performance profiles: all values stay inside the range documented as stable on every BC-250 (≤ 2000 MHz at 1000 mV, ≤ 85 °C).
   stoned holds the same table (profiles.py) and is the one that applies it; these are for display. */
const FACT = {
  silent: { n: 'SILENT', max: 1500, min: 1000, temp: 75, resp: 0, d: 'Mínimo ruido, temperatura y consumo. Multimedia, emulación y juegos ligeros.', fps: '30 o 40 FPS' },
  balanced: { n: 'BALANCED', max: 1850, min: 1000, temp: 85, resp: 1, d: 'El mejor equilibrio entre rendimiento, frametime, temperatura y ruido. Recomendado.', fps: '60 FPS' },
  performance: { n: 'PERFORMANCE', max: 2000, min: 1000, temp: 85, resp: 1, d: 'Máximo rendimiento dentro del rango estable de STONE MACHINE.', fps: 'Sin límite con VRR' }
};
const RESP = ['Suave', 'Normal', 'Rápida'];
const IDLE = ['Nunca', '15 min', '30 min', '60 min'];
const IDLE_MIN = [0, 15, 30, 60];
const slots = [null, null, null];
const slotId = k => 'c' + (k + 1);
function profOf(id) {
  if (FACT[id]) return FACT[id];
  const k = +String(id).slice(1) - 1, sl = slots[k];
  return sl ? { n: 'CUSTOM 0' + (k + 1), ...sl } : FACT.balanced;
}
const usable = () => ['silent', 'balanced', 'performance', ...slots.map((sl, k) => sl && sl.state !== 'failed' ? slotId(k) : null).filter(Boolean)];
/* what is applied right now, as reported by stoned */
function effective() { return (S.game && S.game.profile) || S.prof; }
/* Text from Steam's files goes into the page as text, never as markup. */
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
