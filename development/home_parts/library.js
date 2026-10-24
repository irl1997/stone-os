/* ---------- Library: Steam's installed games and shortcuts, read by the host ----------
   The host sorts by last played. Artwork comes from Steam's own cache through stone://art. */
let G = [], recent = [];
const favs = new Set();
const SRC = { steam: 'Steam', shortcut: 'Acceso directo' };
function ago(t) {
  if (!t) return 'Nunca';
  const s = Date.now() / 1000 - t, d = Math.round(s / 86400), pl = (n, a, b) => `Hace ${n} ${n === 1 ? a : b}`;
  if (s < 3600) return pl(Math.max(1, Math.round(s / 60)), 'min', 'min');
  if (s < 86400) return pl(Math.round(s / 3600), 'hora', 'horas');
  if (d <= 1) return 'Ayer';
  if (d < 7) return pl(d, 'día', 'días');
  if (d < 30) return pl(Math.round(d / 7), 'semana', 'semanas');
  if (d < 365) return pl(Math.round(d / 30), 'mes', 'meses');
  return pl(Math.round(d / 365), 'año', 'años');
}
const sizeText = b => !b ? '—' : b >= 1e9 ? (b / 1e9).toFixed(b >= 1e10 ? 0 : 1).replace('.', ',') + ' GB' : Math.max(1, Math.round(b / 1e6)) + ' MB';
const playText = g => !g.min ? 'Sin jugar' : g.min < 60 ? `${g.min} min jugados` : `${g.h} h jugadas`;
async function loadLibrary() {
  let r = null;
  try { r = await Sys.library(); } catch (e) { r = null; }
  const list = r ? r.games : [];
  G = list.map((x, i) => ({ i, appid: x.appid, n: x.name, o: SRC[x.source] || 'Steam', src: x.source, min: x.playtime_min,
    h: Math.round(x.playtime_min / 60), lastT: x.last_played, last: ago(x.last_played), sz: sizeText(x.size_bytes),
    inst: x.installed, upd: x.updating, art: x.art || {}, fav: favs.has(x.appid), perf: null }));
  recent = G.slice(0, 6).map(g => g.i);
  S.libReady = true; S.libFound = r ? r.root_found : false;
  if (S.st) adopt(S.st);
}
const capUrl = g => g.art.capsule ? `url("${Bridge.artUrl(g.appid, 'capsule')}")` : 'none';
const heroUrl = g => g.art.hero || g.art.capsule ? Bridge.artUrl(g.appid, g.art.hero ? 'hero' : 'capsule') : null;
const nameTag = g => `<span style="position:absolute;left:20px;right:20px;bottom:24px;font-family:var(--f-display);font-weight:300;letter-spacing:.2em;font-size:18px;line-height:1.3;color:var(--warm)">${esc(g.n.toUpperCase())}</span>`;
/* Background blur from a 64×36 copy of the hero: one tiny image scaled up, no live blur filter.
   A small LRU keeps the last ones; game mode empties it. */
const BLUR = new Map(), BLUR_MAX = 24;
function blurOf(g, cb) {
  if (BLUR.has(g.appid)) { const v = BLUR.get(g.appid); BLUR.delete(g.appid); BLUR.set(g.appid, v); return v; }
  const u = heroUrl(g); if (!u) return null;
  const img = new Image(); img.decoding = 'async';
  img.onload = () => {
    try {
      const c = document.createElement('canvas'); c.width = 64; c.height = 36;
      c.getContext('2d').drawImage(img, 0, 0, 64, 36);
      BLUR.set(g.appid, c.toDataURL('image/png'));
      while (BLUR.size > BLUR_MAX) BLUR.delete(BLUR.keys().next().value);
      if (cb) cb();
    } catch (e) { /* artwork that cannot be read stays sharp-only */ }
  };
  img.src = u; return null;
}
function trimArt() { BLUR.clear(); }
