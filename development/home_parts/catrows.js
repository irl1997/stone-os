/* Steam's own settings section for each STONE category (opened in Steam's interface). */
const STEAM_SEC = { 'Sistema': 'System', 'Pantalla': 'Display', 'Audio': 'Audio', 'Mandos': 'Controller', 'Red': 'Internet',
  'Bluetooth': 'Bluetooth', 'Almacenamiento': 'Storage', 'Energía': 'Power', 'Actualizaciones': 'System', 'Accesibilidad': 'Accessibility' };
/* How STONE draws inside Game Mode (read at start by /usr/libexec/stone-home). GPU: Chromium,
   at the screen's own resolution (4K on a 4K TV), the same engine as Steam's interface. Sin GPU:
   the WebKit shell drawing in software, the fallback (smooth only at low resolutions). */
const RENDER = ['GPU', 'Sin GPU'];
const RENDER_TXT = ['Chromium con la GPU, a la resolución del televisor. Recomendado',
  'Respaldo sin GPU (WebKit). Úsalo sólo si GPU falla: a 4K va lento'];
/* The resolution STONE is drawn at, in real pixels. */
const drawRes = () => `${Math.round(innerWidth * devicePixelRatio)} × ${Math.round(innerHeight * devicePixelRatio)}`;
/* Emulators: fixed catalog in the host (stone_home/emulators.py), installed from Flathub. */
const EMU_STATE = {};
function loadEmulators() {
  S.emuLoading = true;
  Sys.emulators().then(r => { S.emu = r; }, () => { S.emu = []; }).then(() => {
    S.emuLoading = false;
    if (S.screen === 'settings' && CATS[cat] === 'Emuladores') enterSettings({ pane: !!NAV.pane, ri: NAV.pane ? NAV.c : 0 });
  });
}
Bridge.on('emulator', d => {
  EMU_STATE[d.key] = d;
  toast(d.text, false);
  if (d.state === 'done') { loadEmulators(); loadLibrary(); }
  else if (S.screen === 'settings' && CATS[cat] === 'Emuladores') enterSettings({ pane: !!NAV.pane, ri: NAV.pane ? NAV.c : 0 });
});
function emuA(r) {
  const e = r.emu;
  if (e.installed) { play('select'); return toast(`${e.name} está en tu biblioteca, como acceso directo`, false); }
  if (EMU_STATE[e.key] && EMU_STATE[e.key].state === 'installing') { play('error'); return toast(`${e.name} se está instalando`, false); }
  play('select');
  EMU_STATE[e.key] = { state: 'installing', text: `Instalando ${e.name}…` };
  enterSettings({ pane: true, ri: NAV.c });
  return sys('emuInstall', e.key);
}
/* Smoothness, measured on the console: STONE moves through Home by itself for a few seconds and
   records the time between frames (requestAnimationFrame). Logged to home.log for diagnosis. */
function measureSmoothness() {
  if (S.measuring) return;
  S.measuring = true;
  const ri = NAV.c, d = [];
  let last = performance.now(), run = true;
  const tick = t => { d.push(t - last); last = t; if (run) requestAnimationFrame(tick); };
  toast('Midiendo la fluidez: STONE se mueve sola unos segundos', false);
  enterHome({ r: 0, c: 0 });
  requestAnimationFrame(t => { last = t; requestAnimationFrame(tick); });
  const steps = ['right', 'right', 'right', 'left', 'left', 'left', 'right', 'right', 'left', 'left', 'right', 'left'];
  let k = 0;
  const iv = setInterval(() => {
    if (k < steps.length) { act(steps[k++]); return; }
    clearInterval(iv); run = false; S.measuring = false;
    const s = [...d].sort((a, b) => a - b), avg = d.reduce((a, b) => a + b, 0) / Math.max(1, d.length);
    S.smooth = { fps: Math.round(1000 / avg), p99: Math.round(s[Math.floor(s.length * .99)] || 0), slow: d.filter(x => x > 25).length, n: d.length, mode: S.render, res: drawRes() };
    console.info(`[stone] smoothness mode=${RENDER[S.render]} res=${S.smooth.res} fps=${S.smooth.fps} p99=${S.smooth.p99}ms slow=${S.smooth.slow}/${S.smooth.n}`);
    cat = CATS.indexOf('Pantalla'); enterSettings({ pane: true, ri });
    toast(`${RENDER[S.render]}: ${S.smooth.fps} cuadros por segundo de media; el más lento, ${S.smooth.p99} ms`, false);
  }, 450);
}
/* Opens the matching section of Steam's settings over STONE; if Steam cannot be reached, says where it is. */
function openSteamSettings(r) {
  if (!r.sec) return toast(r.where, false);
  return Sys.openSteamSettings(r.sec).then(res => { if (!res || !res.ok) toast(r.where, false); }, () => toast(r.where, false));
}
/* A new drawing mode applies when STONE Home starts: save, say so, restart. */
function applyRender() {
  clearTimeout(saveT);
  toast(`Modo de imagen: ${RENDER[S.render]}. STONE se reinicia…`, false);
  Sys.prefsSet({ sound: S.sound, vol: S.vol, hc: S.hc, rm: S.rm, ts: S.ts, safe: S.safe, idle: S.idle, tech: S.tech,
    adv_ack: S.advAck, engine: S.render, favorites: [...favs] })
    .then(() => setTimeout(() => Sys.restart().catch(() => toast('Se aplica la próxima vez que abras STONE', false)), 900))
    .catch(() => toast('No se pudo guardar el modo de imagen', false));
  enterSettings({ pane: true, ri: NAV.c });
}
function catRows() {
  /* Settings Steam already handles open in Steam's own settings; if that fails, STONE says where. */
  const steam = (t, d, where) => ({ l: t, s: d, v: 'Abrir en Steam', act: 'steam', where, sec: STEAM_SEC[CATS[cat]] });
  const rel = (S.info && S.info.release) || {}, dep = (S.info && S.info.deployments) || null;
  switch (CATS[cat]) {
    case 'Pantalla': return { d: 'Ajusta cómo se ve STONE en tu televisor.', rows: [
      { l: 'Área segura', s: 'Usa ← → hasta que las cuatro esquinas se vean completas', v: `${S.safe} %`, key: 'safe' },
      steam('Resolución y frecuencia', 'Las fija Steam para todo el sistema', 'Botón STEAM → Ajustes → Pantalla'),
      steam('HDR', 'Según el televisor y el juego', 'Botón STEAM → Ajustes → Pantalla'),
      { l: 'Tamaño del texto', s: 'Se aplica a STONE Home', v: SIZES[S.ts], key: 'ts' },
      { l: 'Modo de imagen', s: RENDER_TXT[S.render], v: RENDER[S.render], key: 'render' },
      { l: 'Resolución de STONE', s: devicePixelRatio > 1.5 || innerWidth * devicePixelRatio >= 3800 ? 'Dibujada a la resolución del televisor'
        : 'Si tu televisor es 4K y aquí no dice 3840 × 2160, mira «Cómo»', v: drawRes(), act: 'info',
        where: 'Steam decide el tamaño de cada app. Botón STEAM → Biblioteca → STONE → ⚙ → Propiedades → General → Resolución del juego: Nativa. Luego reinicia STONE' },
      { l: 'Medir fluidez', s: S.smooth ? `Última medida, ${RENDER[S.smooth.mode]} a ${S.smooth.res}: ${S.smooth.fps} FPS · cuadro más lento ${S.smooth.p99} ms · ${S.smooth.slow} de ${S.smooth.n} cuadros lentos`
        : 'STONE recorre el inicio unos segundos y mide los cuadros por segundo del modo actual', v: 'Medir', act: 'measure' }] };
    case 'Audio': return { d: 'Sonidos del sistema y volumen de la interfaz.', rows: [
      { l: 'Sonidos del sistema', s: 'Botones, encendido y apagado', v: S.sound ? 'Activados' : 'Desactivados', key: 'sound' },
      { l: 'Volumen de la interfaz', s: 'Independiente del volumen de los juegos', v: Math.round(S.vol * 100) + ' %', key: 'vol' },
      steam('Salida de audio', 'Altavoces, auriculares, HDMI o DisplayPort', 'Botón ··· → Audio, o botón STEAM → Ajustes → Audio')] };
    case 'Emuladores': {
      if (!S.emu) { if (!S.emuLoading) loadEmulators(); return { d: 'Emuladores de otras consolas, desde Flathub.', rows: [{ l: 'Leyendo…', s: 'Buscando qué emuladores están instalados', v: '', ro: true }] }; }
      return { d: 'Se instalan desde Flathub y aparecen en tu biblioteca como accesos directos. Los juegos y BIOS los pones tú. RetroDECK reúne la mayoría de consolas en una sola app.',
        rows: S.emu.map(e => { const st = EMU_STATE[e.key]; const busy = st && st.state === 'installing';
          return { l: e.name, s: busy ? st.text : e.plays, v: e.installed ? 'Instalado' : busy ? 'Instalando…' : 'Instalar', act: 'emu', emu: e }; }) };
    }
    case 'Rendimiento': return { d: `Perfil activo: ${profOf(S.prof).n}. Elige entre silencio y rendimiento, o ajusta tu propio perfil.`, rows: [
      { l: 'Performance Center', s: 'SILENT, BALANCED, PERFORMANCE y CUSTOM', v: 'Abrir', act: 'perf' },
      { l: 'Modo técnico', s: 'Temperaturas, frecuencia y consumo en el menú de STONE durante el juego', v: S.tech ? 'Activado' : 'Desactivado', key: 'tech' },
      cu40Row(),
      { l: 'Safe Mode', s: S.safeMode ? `Activo: ${SAFE_REASON[S.safeReason] || 'valores de fábrica'}` : 'Sólo valores de fábrica: sin CUSTOM, sin perfiles por juego ni pruebas', v: S.safeMode ? 'Salir' : 'Activar', act: 'safe' }] };
    case 'Energía': return { d: 'STONE MACHINE no tiene modo de reposo. Apagarla cuando no se usa ahorra entre 55 y 105 W.', rows: [
      { l: 'Apagar tras inactividad', s: 'Sólo en STONE Home, nunca con un juego abierto', v: IDLE[S.idle], key: 'idle' }] };
    case 'Accesibilidad': return { d: 'Haz que STONE sea más fácil de leer y más tranquilo.', rows: [
      { l: 'Alto contraste', s: 'Línea de 4 px y texto secundario más claro', v: S.hc ? 'Activado' : 'Desactivado', key: 'hc' },
      { l: 'Reducir movimiento', s: 'Sin escalas ni desplazamientos de fondo', v: S.rm ? 'Activado' : 'Desactivado', key: 'rm' },
      { l: 'Tamaño del texto', s: 'Se aplica a STONE Home', v: SIZES[S.ts], key: 'ts' }] };
    case 'Acerca de': return { d: 'STONE MACHINE · STONE OS', rows: [
      { l: 'Versión', s: rel.STONE_VERSION ? `STONE OS ${rel.STONE_VERSION}${rel.STONE_COMMIT ? ' · ' + rel.STONE_COMMIT.slice(0, 7) : ''}` : 'Leyendo…', v: '' },
      { l: 'Hardware', s: S.caps ? (S.caps.board ? `AMD BC-250 detectada · Zen 2 ${cores()} núcleos · GPU ${cu40Top() ? 40 : 24} CU · 16 GB GDDR6` : 'No se detectó una AMD BC-250: el ajuste de rendimiento está desactivado') : 'Leyendo…', v: '' },
      { l: 'Sistema base', s: rel.STONE_BASE_IMAGE ? `Bazzite · ${rel.STONE_BASE_IMAGE}${rel.STONE_BASE_DIGEST ? ' @ ' + rel.STONE_BASE_DIGEST.slice(7, 19) : ''}` : 'Bazzite (imagen derivada de bazzite-deck)', v: '' },
      { l: 'Componentes', s: [rel.STONE_KERNEL && `kernel ${rel.STONE_KERNEL}`, rel.STONE_MESA && `Mesa ${rel.STONE_MESA}`, rel.STONE_GOVERNOR_VERSION && `regulador ${rel.STONE_GOVERNOR_VERSION}`].filter(Boolean).join(' · ') || '—', v: '' },
      { l: 'Modo escritorio', s: 'Para usuarios avanzados', v: 'En Steam', act: 'info', where: 'Botón STEAM → Energía → Cambiar a escritorio' }] };
    case 'Actualizaciones': return { d: 'Las actualizaciones llegan como una imagen completa y se aplican al reiniciar. Si una falla, la anterior sigue disponible.', rows: [
      { l: 'Estado', s: !dep ? 'No se pudo leer el estado del sistema' : dep.staged ? `Actualización lista: ${dep.staged.version || 'nueva versión'}. Se aplica al reiniciar` : `Instalada: ${(dep.booted && dep.booted.version) || rel.STONE_VERSION || '—'}`, v: '' },
      steam('Buscar actualizaciones', 'Sistema y Steam se actualizan desde Steam', 'Botón STEAM → Ajustes → Sistema → Buscar actualizaciones'),
      { l: 'Volver a la versión anterior', s: dep && dep.rollback ? `Guardada: ${dep.rollback.version || 'versión anterior'}` : 'No hay una versión anterior guardada todavía', v: 'Cómo', act: 'info', where: 'Reinicia y, en el menú de arranque, elige la segunda entrada. STONE OS arranca con la versión anterior' }] };
    default: return { d: `${CATS[cat]}: lo gestiona Steam.`, rows: [steam(`Abrir ${CATS[cat].toLowerCase()}`, 'Panel nativo de Steam', `Botón STEAM → Ajustes → ${CATS[cat]}`)] };
  }
}

/* CPU cores the system sees (threads / 2: Zen 2 has 2 threads per core). Boards sold
   unlocked report 8; the BC-250's stock firmware 6. */
function cores() { const t = navigator.hardwareConcurrency || 12; return Math.max(1, Math.round(t / 2)); }

/* 40 CU: shown, never switched from here. It changes how the kernel starts, so it is done by
   hand with sudo stone-cu40 (confirmation, GPU test, automatic way back). */
function cu40Row() {
  const c = S.caps && S.caps.cu40;
  const how = 'En una terminal (Konsole o Ctrl+Alt+F3): sudo stone-cu40 enable → reinicia → sudo stone-cu40 test. '
    + 'Si la prueba falla, STONE vuelve sola a 24 CU. Para apagarlo: sudo stone-cu40 disable';
  if (!S.caps) return { l: 'GPU · unidades de cálculo', s: 'Leyendo…', v: '' };
  if (!c || !c.available) return { l: 'GPU · unidades de cálculo', s: 'Esta versión de STONE OS no trae el desbloqueo de 40 CU', v: '24 CU' };
  if (c.active) return { l: 'GPU · unidades de cálculo', s: `40 CU en uso · STONE limita la GPU a ${c.ceiling_mhz} MHz en todos los perfiles`, v: '40 CU', act: 'info', where: 'Estado y prueba: sudo stone-cu40 status · sudo stone-cu40 test. Para apagarlo: sudo stone-cu40 disable' };
  return { l: 'GPU · unidades de cálculo', s: '24 CU en uso · 40 CU disponible, se activa a mano con prueba de estabilidad', v: '24 CU', act: 'info', where: how };
}
