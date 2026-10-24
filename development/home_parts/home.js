/* ---------- HOME ---------- */
let metaT = 0;
const runTag = i => S.running === i ? '<span class="run">EN CURSO</span>' : '';
function homeHTML() {
  let cards = '';
  if (S.section === 0) {
    if (G.length) {
      const g0 = G[recent[0]], hu = heroUrl(g0);
      cards += `<div class="card hero" data-f data-g="${g0.i}" style="background-image:${hu ? `url(&quot;${hu}&quot;)` : 'none'};background-color:var(--shale)">${hu ? '' : nameTag(g0)}${runTag(g0.i)}</div>`;
      recent.slice(1).forEach(i => { const g = G[i]; cards += `<div class="card cap" data-f data-g="${i}" style="background-image:${capUrl(g).replace(/"/g, '&quot;')}">${g.art.capsule ? '' : nameTag(g)}${runTag(i)}</div>`; });
    } else {
      cards += `<div class="card wide" data-f data-act="steam" data-k="store" data-name="STEAM STORE"><b>${S.libReady ? 'SIN JUEGOS' : 'BIBLIOTECA'}</b><span>${S.libReady ? 'Instala juegos desde la Tienda de Steam' : 'Leyendo la biblioteca de Steam…'}</span></div>`;
    }
    cards += `<div class="card tile" data-f data-act="library">BIBLIOTECA</div>`;
  } else if (S.section === 2) {
    [['STEAM STORE', 'Tienda de Steam', 'store'], ['DESCARGAS', 'Descargas y actualizaciones de juegos', 'downloads']]
      .forEach(([t, s, k]) => { cards += `<div class="card wide" data-f data-act="steam" data-k="${k}" data-name="${t}"><b>${t}</b><span>${s}</span></div>`; });
  } else {
    cards += `<div class="card wide" data-f data-act="steam" data-k="screenshots" data-name="CAPTURAS"><b>CAPTURAS</b><span>Capturas guardadas por Steam</span></div>`;
  }
  const ico = {
    lib: '<svg width="26" height="26" viewBox="0 0 26 26" fill="none" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="6" height="18"/><rect x="11" y="4" width="6" height="18"/><path d="M19 5l4 1-3 16-4-1"/></svg>',
    exp: '<svg width="26" height="26" viewBox="0 0 26 26" fill="none" stroke-width="2" aria-hidden="true"><circle cx="13" cy="13" r="9"/><path d="M16.5 9.5l-2 5-5 2 2-5z"/></svg>',
    set: '<svg width="26" height="26" viewBox="0 0 26 26" fill="none" stroke-width="2" aria-hidden="true"><path d="M4 7h18M4 13h18M4 19h18"/><rect x="8" y="5" width="4" height="4" fill="#141517"/><rect x="15" y="11" width="4" height="4" fill="#141517"/><rect x="6" y="17" width="4" height="4" fill="#141517"/></svg>',
    pow: '<svg width="26" height="26" viewBox="0 0 26 26" fill="none" stroke-width="2" aria-hidden="true"><path d="M13 3v9"/><path d="M7.5 7a8 8 0 1 0 11 0"/></svg>'
  };
  return `<div class="clip"><div class="track" data-sx="0">${cards}</div></div>
    <div class="horizon"></div>
    <div class="ctx"><div class="kicker" id="hk"></div><div class="ctx-title" id="ht"></div><div class="meta" id="hm"></div></div>
    <div class="sys">
      <span class="txt-f" data-f data-act="library">${ico.lib}Biblioteca</span>
      <span class="txt-f" data-f data-act="explore">${ico.exp}Explorar</span>
      <span class="txt-f" data-f data-act="settings">${ico.set}Ajustes</span>
      <span class="txt-f" data-f data-act="power">${ico.pow}Apagar</span>
    </div>`;
}
function homeContext(el) {
  const hk = $('#hk'), ht = $('#ht'), hm = $('#hm');
  hm.classList.remove('on'); clearTimeout(metaT);
  const gi = el.dataset.g;
  if (gi !== undefined) {
    const g = G[+gi], first = el.classList.contains('hero');
    hk.innerHTML = first ? `CONTINUAR${S.running === g.i ? ' · <span class="now">EN CURSO</span>' : ''}` : 'RECIENTE';
    ht.textContent = g.n;
    hm.textContent = `${S.running === g.i ? 'Abierto ahora' : g.last} · ${playText(g)}${g.o !== 'Steam' ? ' · ' + g.o : ''}`;
    metaT = setTimeout(() => hm.classList.add('on'), S.rm ? 150 : 600);
    bgGame(g, false, '');
    [NAV && NAV.rows[0][NAV.c + 1], NAV && NAV.rows[0][NAV.c - 1]].forEach(nx => { if (nx && nx.dataset.g !== undefined) blurOf(G[+nx.dataset.g]); });
  } else {
    const a = el.dataset.act;
    const label = { library: ['BIBLIOTECA', S.libReady ? `${G.length} juego${G.length === 1 ? '' : 's'}` : 'Leyendo…'], explore: ['EXPLORAR', 'Tienda y descargas de Steam'], settings: ['AJUSTES', 'Pantalla, audio, rendimiento y más'], power: ['APAGAR', 'Apagar o reiniciar'], steam: [el.dataset.name, 'Se abre en Steam'] }[a] || ['', ''];
    hk.textContent = S.section === 2 ? 'EXPLORAR' : S.section === 3 ? 'MEDIA' : 'SISTEMA';
    ht.textContent = label[0]; hm.textContent = label[1];
    metaT = setTimeout(() => hm.classList.add('on'), S.rm ? 150 : 600);
    if (el.closest('.sys') == null || S.section !== 0) setBg(neutralBg, '');
  }
}
function enterHome(opts = {}) {
  S.screen = 'home'; S.from = 'home';
  if (S.section === 1) S.section = 0;
  const h = $('#home'); h.innerHTML = homeHTML();
  showScreen('home'); chrome(true, true);
  const track = h.querySelector('.track');
  const rows = [[...track.children], [...h.querySelectorAll('.sys [data-f]')]];
  const prev = NAV && NAV.id === 'home' ? NAV : null;
  NAV = mkNav(rows, {
    r: opts.r ?? (prev ? prev.r : 0), c: opts.c ?? (prev ? prev.c : 0),
    place: (el, b, r) => r === 0 ? { x: b.x, y: 668, w: b.w * (S.rm ? 1 : 1.06) } : { x: b.x, y: b.y + b.h + 14, w: b.w },
    scroll: el => {
      if (!el.closest('.track')) return;
      let sx = +track.dataset.sx; const l = el.offsetLeft, r = l + el.offsetWidth * 1.06, view = 1728;
      if (l + sx < 0) sx = -l; else if (r + sx > view) sx = view - r;
      track.dataset.sx = sx; track.style.transform = `translateX(${sx}px)`;
    },
    onFocus: homeContext
  });
  NAV.id = 'home';
  if (NAV.c >= rows[NAV.r].length) NAV.c = rows[NAV.r].length - 1;
  requestAnimationFrame(() => applyFocus({ slow: opts.slow }));
  hints([['A', S.section === 0 ? 'Abrir' : 'Elegir'], ['LB RB', 'Sección']]);
}
function homeA(el) {
  const a = el.dataset.act;
  if (el.dataset.g !== undefined) {
    const g = G[+el.dataset.g];
    if (S.running === g.i && el.classList.contains('hero')) { play('select'); return resumeGame(); }
    play('select'); return enterDetail(g.i);
  }
  if (a === 'library') { play('select'); S.section = 1; return enterLibrary(); }
  if (a === 'explore') { play('select'); S.section = 2; return enterHome({ r: 0, c: 0 }); }
  if (a === 'settings') { play('select'); return enterSettings(); }
  if (a === 'power') { play('select'); return openPower(); }
  if (a === 'steam') { play('select'); return sys('openSteam', el.dataset.k).then(r => { if (r) toast(`${el.dataset.name}: se abre en Steam`, false); }); }
}
function section(d) {
  const n = S.section + d;
  if (n < 0 || n > 3) return bump(d);
  play('nav'); S.section = n;
  if (n === 1) enterLibrary(); else { if (NAV) NAV.id = ''; enterHome({ r: 0, c: 0 }); }
}
