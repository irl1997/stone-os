/* ---------- STONE OS simulation: the host side, played in the page ----------
   Replaces Bridge (transport to stone_home/host.py) so the real interface runs with no console.
   Same method names, arguments and answer shapes as host.py. EVERYTHING here is simulated:
   the library, the display, Steam's settings, the downloads. There are no measurements and no
   frame rates: the numbers shown are the ones the rules in stone_display/link.py would give
   for each connection, nothing else. Built into the simulation page by development/build_simulation.py. */
const SimPanel = { adapter: 'passive' };      /* the connection: passive DP→HDMI (≈300 MHz) or active DP 1.4→HDMI 2.0 */
const Bridge = (() => {
  const listeners = {};
  const emit = (ev, d) => (listeners[ev] || []).forEach(f => { try { f(d); } catch (e) { console.error(e); } });
  window.__stoneEvent = emit;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const ok = v => JSON.parse(JSON.stringify(v));
  const bad = (code, msg) => { throw new PerfError(code, msg); };

  /* ---- preferences ---- */
  let prefs = { sound: true, vol: 0.7, hc: false, rm: false, ts: 0, safe: 5, idle: 2, tech: false, adv_ack: false, favorites: ['1091500'],
    engine: 0, game_res: 0, display_goal: 'fluid', optimized: 1 };

  /* ---- library (simulated; Steam's art is not available here, STONE paints its own covers) ---- */
  const DAY = 86400, now = Date.now() / 1000;
  const GAMES = [
    { appid: '1091500', name: 'Cyberpunk 2077', source: 'steam', installed: true, last: now - 3600 * 5, min: 5400, size: 71e9, kind: 'upscaler' },
    { appid: '1245620', name: 'ELDEN RING', source: 'steam', installed: true, last: now - DAY * 2, min: 7800, size: 49e9, kind: 'anticheat' },
    { appid: '1551360', name: 'Forza Horizon 5', source: 'steam', installed: true, last: now - DAY * 4, min: 1900, size: 110e9, kind: 'upscaler' },
    { appid: '1145350', name: 'Hades II', source: 'steam', installed: true, last: now - DAY * 6, min: 2200, size: 12e9, kind: 'plain' },
    { appid: '1174180', name: 'Red Dead Redemption 2', source: 'steam', installed: true, last: now - DAY * 12, min: 3600, size: 119e9, kind: 'upscaler' },
    { appid: '413150', name: 'Stardew Valley', source: 'steam', installed: true, last: now - DAY * 20, min: 6100, size: 0.6e9, kind: 'plain' },
    { appid: '730', name: 'Counter-Strike 2', source: 'steam', installed: true, last: now - DAY * 30, min: 900, size: 33e9, kind: 'online' },
    { appid: '367520', name: 'Hollow Knight', source: 'steam', installed: true, last: now - DAY * 45, min: 1500, size: 2.1e9, kind: 'plain' },
    { appid: '1086940', name: "Baldur's Gate 3", source: 'steam', installed: false, last: 0, min: 0, size: 150e9, kind: 'upscaler' },
    { appid: '620', name: 'Portal 2', source: 'steam', installed: false, last: now - DAY * 200, min: 600, size: 12e9, kind: 'plain' }
  ];
  const gameOf = appid => GAMES.find(g => g.appid === appid) || bad('InvalidValue', 'unknown game');

  /* ---- display: what stone_display/link.py concludes for each adapter ---- */
  const hzTxt = m => m;
  function displayStatus() {
    const passive = SimPanel.adapter === 'passive', max = prefs.display_goal === 'max_res';
    const f60 = passive ? { w: 2560, h: 1440, hz: 60 } : { w: 3840, h: 2160, hz: 60 };
    const mres = passive ? { w: 3840, h: 2160, hz: 30 } : { w: 3840, h: 2160, hz: 60 };
    const target = max ? mres : f60;
    return {
      connected: true, name: 'TV 4K (simulado)', connection: 'HDMI', connector: 'HDMI-A-1', native: { w: 3840, h: 2160, hz: 60 },
      output: target, preferred: { w: 3840, h: 2160, hz: 60 }, max_hz: 60, rates: [24, 30, 50, 60],
      vrr: { supported: false, range: null, active: false }, hdr: { supported: true, active: false },
      game_env: prefs.game_res === 1 ? { w: 1280, h: 800 } : { w: target.w, h: target.h }, steam_default: { w: 1280, h: 800 },
      policy: prefs.game_res === 1 ? 'steam' : 'native', safe: { w: 1920, h: 1080, hz: 60 }, steam: true, steam_error: null,
      findings: passive ? [{ level: 'warn', code: 'LINK_LIMITS_MODES', text: 'El adaptador DisplayPort→HDMI no lleva 4K a 60 Hz: STONE usa 2560 × 1440 a 60 Hz (o 4K a 30 Hz si eliges máxima resolución). Con un adaptador activo DP 1.4 → HDMI 2.0 (4K 60 Hz) llegaría a 4K a 60 Hz.' }] : [],
      link: { best_fluid: f60, best_max_res: mres, target, reaches_4k60: !passive, display_offers_4k60: true, limited_by_link: passive,
        unreachable: [], goal: max ? 'max_res' : 'fluid',
        advice: passive ? 'Esta conexión (≈300 MHz de reloj de píxel) no alcanza 4K a 60 Hz. Para 4K a 60 Hz: un adaptador activo DisplayPort 1.4 → HDMI 2.0 («4K 60 Hz»). Simulación del cálculo de STONE DISPLAY.'
          : 'La conexión lleva 4K a 60 Hz. Para 120 Hz el televisor tendría que ofrecer ese modo.' },
      goal: max ? 'max_res' : 'fluid'
    };
  }

  /* ---- Steam's own settings, as the discovery layer would list them for one Steam (names split into words) ---- */
  const SS_ES = { 'Enable Hdr': 'Activar HDR', 'Use Native Resolution': 'Usar la resolución nativa', 'Screen Brightness': 'Brillo de pantalla', 'Game Volume': 'Volumen de los juegos',
    'Mute On Minimize': 'Silenciar al minimizar', 'Enable Xbox Support': 'Soporte para mandos Xbox', 'Enable Playstation Support': 'Soporte para mandos PlayStation', 'Haptics Enabled': 'Vibración háptica',
    'Enable Wifi Power Save': 'Ahorro de energía del Wi-Fi', 'Allow Downloads During Gameplay': 'Descargar mientras se juega', 'Download Region': 'Región de descarga', 'Show Fps Counter': 'Mostrar contador de FPS',
    'Enable Animations': 'Animaciones', 'Enable Steam Play': 'Activar Steam Play', 'Enable Developer Mode': 'Modo desarrollador', 'Show Toast Notifications': 'Mostrar avisos emergentes' };
  const SS_AREA = { Display: 'Pantalla', Audio: 'Audio', Controller: 'Mandos', Network: 'Red', Downloads: 'Descargas', Interface: 'Interfaz', Compatibility: 'Compatibilidad', System: 'Sistema', Notifications: 'Notificaciones' };
  const STEAMSET = [
    { id: 'Settings.Display.EnableHdr', label: 'Enable Hdr', area: 'Display', value: false, writable: true },
    { id: 'Settings.Display.UseNativeResolution', label: 'Use Native Resolution', area: 'Display', value: true, writable: true },
    { id: 'Settings.Display.ScreenBrightness', label: 'Screen Brightness', area: 'Display', value: 0.8, writable: false },
    { id: 'Settings.Audio.GameVolume', label: 'Game Volume', area: 'Audio', value: 0.7, writable: false },
    { id: 'Settings.Audio.MuteOnMinimize', label: 'Mute On Minimize', area: 'Audio', value: false, writable: true },
    { id: 'Settings.Controller.EnableXboxSupport', label: 'Enable Xbox Support', area: 'Controller', value: true, writable: true },
    { id: 'Settings.Controller.EnablePlaystationSupport', label: 'Enable Playstation Support', area: 'Controller', value: true, writable: true },
    { id: 'Settings.Controller.HapticsEnabled', label: 'Haptics Enabled', area: 'Controller', value: true, writable: true },
    { id: 'Settings.Network.EnableWifiPowerSave', label: 'Enable Wifi Power Save', area: 'Network', value: false, writable: true },
    { id: 'Settings.Downloads.AllowDownloadsDuringGameplay', label: 'Allow Downloads During Gameplay', area: 'Downloads', value: false, writable: true },
    { id: 'Settings.Downloads.DownloadRegion', label: 'Download Region', area: 'Downloads', value: 'Chile', writable: false },
    { id: 'Settings.Interface.ShowFpsCounter', label: 'Show Fps Counter', area: 'Interface', value: false, writable: true },
    { id: 'Settings.Interface.EnableAnimations', label: 'Enable Animations', area: 'Interface', value: true, writable: true },
    { id: 'Settings.Compatibility.EnableSteamPlay', label: 'Enable Steam Play', area: 'Compatibility', value: true, writable: true },
    { id: 'Settings.System.EnableDeveloperMode', label: 'Enable Developer Mode', area: 'System', value: false, writable: true },
    { id: 'Settings.Notifications.ShowToastNotifications', label: 'Show Toast Notifications', area: 'Notifications', value: true, writable: true }
  ].map(x => Object.assign({}, x, { label_en: x.label, label: SS_ES[x.label] || x.label, area: SS_AREA[x.area] || x.area })).map(x => Object.assign({ type: typeof x.value === 'boolean' ? 'bool' : typeof x.value === 'number' ? 'number' : 'text' }, x));

  /* ---- image enhancement: components, per-game choices, install, optimization ---- */
  const comps = { helixsr: null, optiscaler: null };            /* what STONE has downloaded */
  const gs = {};                                                /* per game: { mode, path, preset, opti (installed by STONE), helix, done } */
  const st = appid => gs[appid] || (gs[appid] = { mode: 'recommended', path: null, preset: null, opti: false, helix: false, done: 0 });
  const LABEL = { native: 'Sin mejora (nativo)', game_upscaler: 'FSR del juego', gamescope: 'FSR de pantalla (Gamescope)', helixsr: 'HelixSR', optiscaler: 'OptiScaler' };
  const PRESETS = ['quality', 'balanced', 'performance', 'ultra_performance'];
  const PRESET_ES = { quality: 'Calidad', balanced: 'Equilibrado', performance: 'Rendimiento', ultra_performance: 'Ultra rendimiento' };
  const protectedGame = g => g.kind === 'anticheat' || g.kind === 'online';
  function avail(g) {
    const s = st(g.appid), prot = protectedGame(g);
    const a = { native: { available: true }, gamescope: { available: true, guidance_only: true, reason: 'Solo orientación: se activa desde Steam' } };
    a.game_upscaler = g.kind === 'upscaler' ? { available: true } : { available: false, reason: 'este juego no trae FSR propio' };
    if (prot) { a.helixsr = a.optiscaler = { available: false, reason: g.kind === 'online' ? 'solo en línea: STONE no inyecta nada' : 'tiene anti-cheat: STONE no inyecta nada' }; }
    else {
      a.optiscaler = s.opti ? { available: true } : { available: false, reason: 'not installed' };
      a.helixsr = s.helix ? { available: true } : { available: false, reason: 'not installed' };
    }
    return a;
  }
  const recPath = g => g.kind === 'upscaler' ? 'game_upscaler' : 'native';
  const cur = g => { const s = st(g.appid); return { path: s.mode === 'custom' ? s.path : recPath(g), preset: s.mode === 'custom' ? s.preset : (g.kind === 'upscaler' ? 'quality' : null) }; };
  const summary = g => {
    const c = cur(g), d = displayStatus(), t = d.link.target;
    const how = c.path === 'native' ? 'Nativo' : `${LABEL[c.path]} · ${PRESET_ES[c.preset] || 'Calidad'}`;
    return `${how} · salida ${t.w} × ${t.h} a ${t.hz} Hz`;
  };
  const MATRIX = g => g.kind === 'upscaler' ? 9 : g.kind === 'plain' ? 4 : 3;

  const M = {
    library: () => ({ root_found: true, owned_known: true, games: GAMES.slice().sort((a, b) => b.installed - a.installed || b.last - a.last).map(g => ({
      appid: g.appid, name: g.name, source: g.source, installed: g.installed, updating: false, playtime_min: g.min, last_played: g.last,
      size_bytes: g.size, art: { capsule: true, hero: true }, launch: g.installed ? 'steam://rungameid/' + g.appid : null })) }),
    launch: async ([appid]) => { gameOf(appid); SimStoned.sim.steamLaunch(appid); SimPanel.running = appid; SimPanel.refresh && SimPanel.refresh(); return { ok: true }; },
    install: async () => ({ ok: true }),
    openSteam: async () => ({ ok: true }), openSteamSettings: async () => ({ ok: true }), restart: async () => ({ ok: true }),
    power: async ([a]) => { toast(`Simulación: «${a === 'off' ? 'Apagar' : 'Reiniciar'}» no hace nada aquí`, false); return { ok: true }; },
    systemInfo: () => ({ release: { STONE_VERSION: '0.1.0-rc23 (simulación)', STONE_BASE_IMAGE: 'bazzite-deck', STONE_KERNEL: 'simulado', STONE_MESA: 'simulado', STONE_GOVERNOR_VERSION: 'simulado' },
      boot_time: Math.round(now - 3600), deployments: { booted: { image: 'simulación', version: '0.1.0-rc23', timestamp: now }, staged: null, rollback: null } }),
    'prefs.get': () => prefs,
    'prefs.set': ([p]) => (prefs = Object.assign({}, prefs, p)),
    emulators: () => ['RetroDECK|Todo en uno: la mayoría de consolas clásicas en una sola app|1', 'RetroArch|Consolas clásicas con núcleos de libretro|0', 'Dolphin|GameCube y Wii|0',
      'PCSX2|PlayStation 2|0', 'PPSSPP|PlayStation Portable|0', 'RPCS3|PlayStation 3|0', 'Cemu|Wii U|0', 'Azahar|Nintendo 3DS|0'].map(s => { const [name, plays, i] = s.split('|'); return { key: name.toLowerCase(), name, plays, installed: i === '1', busy: false }; }),
    emuInstall: async ([key]) => { setTimeout(() => emit('emulator', { key, state: 'done', text: 'Simulación: instalación terminada' }), 1800); return { ok: true }; },

    'display.status': () => displayStatus(),
    'display.gamePolicy': ([p]) => { prefs.game_res = p; return { ok: true, changed: 10, error: null }; },
    'display.goal': ([g]) => { prefs.display_goal = g ? 'max_res' : 'fluid'; return { ok: true, goal: prefs.display_goal, error: null }; },

    'steam.settings': async ([refresh]) => { await wait(refresh ? 500 : 250); return { available: true, settings: ok(STEAMSET), count: STEAMSET.length, error: null }; },
    'steam.setSetting': async ([id, v]) => { const x = STEAMSET.find(s => s.id === id); if (!x || !x.writable) bad('InvalidValue', 'unknown, read-only or not a true/false setting'); await wait(200); x.value = v; return { ok: true, id, value: v }; },

    'game.status': () => ({ active: !!SimPanel.running }),
    'game.recommended': ([appid]) => {
      const g = gameOf(appid), s = st(appid);
      const meas = s.done > 0;
      return { mode: s.mode, label: s.mode === 'custom' ? 'TU CONFIGURACIÓN' : 'STONE RECOMMENDED', summary: summary(g),
        why: 'Simulación: reglas de STONE para este juego y esta conexión', settings: summary(g),
        status: s.mode === 'custom' ? 'USER_MODIFIED' : meas ? 'AUTO_GENERATED' : 'RECOMMENDED',
        status_text: s.mode === 'custom' ? 'Tu configuración' : meas ? 'Medido en esta consola' : 'Recomendado por STONE',
        target: displayStatus().link.target, output: displayStatus().output, internal: null, trail: [], stale: false };
    },
    'game.modes': ([appid]) => ({ chosen: st(appid).mode, modes: ['recommended', 'custom'] }),
    'game.profile': ([appid]) => ({ id: appid, appid, name: gameOf(appid).name, status: 'RECOMMENDED', availability: avail(gameOf(appid)) }),
    'game.apply': async ([appid, mode]) => { st(appid).mode = mode; return { ok: true }; },
    'game.image': ([appid]) => {
      const g = gameOf(appid), s = st(appid), a = avail(g), c = cur(g), out = [{ key: 'auto', label: 'STONE decide', available: true, reason: null, selected: s.mode !== 'custom' }];
      for (const key of ['native', 'game_upscaler', 'gamescope', 'helixsr', 'optiscaler']) {
        const x = a[key];
        if ((key === 'helixsr' || key === 'optiscaler') && !x.available && /not installed/.test(x.reason || '')) continue;
        out.push({ key, label: LABEL[key], available: !!x.available, reason: x.reason || null, guidance_only: !!x.guidance_only, selected: s.mode === 'custom' && c.path === key });
      }
      return { mode: s.mode, path: c.path, preset: c.preset, choices: out, by_stone: s.opti, presets: PRESETS };
    },
    'game.setImage': async ([appid, key, preset]) => {
      const g = gameOf(appid), s = st(appid);
      if (key === 'auto') { s.mode = 'recommended'; s.path = s.preset = null; return { ok: true }; }
      const a = avail(g)[key]; if (!a || !a.available) bad('InvalidValue', `${key}: ${(a && a.reason) || 'no disponible'}`);
      s.mode = 'custom'; s.path = key; s.preset = key === 'native' ? null : (preset || s.preset || 'quality'); return { ok: true };
    },
    components: async ([kind]) => {
      if (!kind) return { helixsr: comps.helixsr, optiscaler: comps.optiscaler };
      await wait(1600); comps[kind] = { tag: 'versión simulada', verified: 'sha256 simulado' }; return { ok: true, kind, tag: comps[kind].tag };
    },
    'game.installPlan': async ([appid, kind]) => {
      const g = gameOf(appid);
      if (protectedGame(g)) return { ok: false, error: g.kind === 'online' ? 'Este juego es solo en línea: STONE no instala nada dentro' : 'Este juego tiene anti-cheat: STONE no instala nada dentro' };
      if (!comps[kind]) return { ok: false, needs_download: true, error: 'primero hay que descargar el componente' };
      return { ok: true, needs_download: false, tag: comps[kind].tag, component: { tag: comps[kind].tag }, folder: `…/steamapps/common/${g.name}/`, files: kind === 'optiscaler' ? 14 : 6 };
    },
    'game.install': async ([appid]) => { await wait(900); st(appid).opti = true; return { ok: true }; },
    'game.uninstallImage': async ([appid]) => { const s = st(appid); s.opti = false; if (s.path === 'optiscaler') { s.mode = 'recommended'; s.path = s.preset = null; } return { ok: true }; },
    'game.optimizeStatus': ([appid]) => {
      const g = gameOf(appid), s = st(appid), total = MATRIX(g), done = Math.min(s.done, total);
      const names = ['Nativo', 'FSR del juego · Calidad', 'FSR del juego · Equilibrado', 'FSR del juego · Rendimiento', 'OptiScaler · Calidad', 'HelixSR · Calidad', 'FSR de pantalla', 'Calidad ultra rendimiento', 'Nativo con tope de FPS'];
      return { done, total, next: done < total ? names[done % names.length] : null, benchmark: g.kind === 'upscaler' ? 'Ajustes → Gráficos → Benchmark' : null, stale: false,
        recommended: summary(g), status: done ? 'AUTO_GENERATED' : 'RECOMMENDED', measured: done };
    },
    'game.optimizeStart': async ([appid]) => {
      const g = gameOf(appid), s = st(appid); s.done += 1; SimStoned.sim.steamLaunch(appid); SimPanel.running = appid; SimPanel.refresh && SimPanel.refresh();
      return { ok: true, settings: summary(g), summary: summary(g) };
    },
    'game.restoreRecommended': async ([appid]) => { const s = st(appid); s.mode = 'recommended'; s.path = s.preset = null; return { ok: true }; }
  };

  /* covers: generated here (Steam's own artwork is not available in the simulation) */
  const artCache = {};
  function artOf(appid, kind) {
    const k = appid + kind; if (artCache[k]) return artCache[k];
    const g = GAMES.find(x => x.appid === appid), w = kind === 'hero' ? 960 : 300, h = kind === 'hero' ? 310 : 450;
    let hsh = 0; for (const c of appid) hsh = (hsh * 31 + c.charCodeAt(0)) % 360;
    const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d');
    const gr = x.createLinearGradient(0, 0, w, h); gr.addColorStop(0, `hsl(${hsh},38%,26%)`); gr.addColorStop(1, `hsl(${(hsh + 50) % 360},34%,9%)`);
    x.fillStyle = gr; x.fillRect(0, 0, w, h);
    x.fillStyle = 'rgba(255,255,255,.07)'; x.beginPath(); x.arc(w * .78, h * .3, h * .55, 0, 7); x.fill();
    x.fillStyle = 'rgba(237,233,227,.9)'; x.font = `300 ${Math.round(h * (kind === 'hero' ? .13 : .075))}px Jost, sans-serif`; x.textBaseline = 'bottom'; if (kind === 'hero') x.textAlign = 'center';
    const words = (g ? g.name : appid).toUpperCase().split(' '); let line = '', y = h - h * .08; const lines = [];
    for (const wd of words) { const tt = line ? line + ' ' + wd : wd; if (x.measureText(tt).width > w * .8 && line) { lines.push(line); line = wd; } else line = tt; }
    lines.push(line); const lh = h * (kind === 'hero' ? .15 : .085); y -= (lines.length - 1) * lh;
    lines.forEach((l, i) => x.fillText(l, kind === 'hero' ? w * .5 : w * .07, y + i * lh));
    return (artCache[k] = c.toDataURL('image/jpeg', .85));
  }
  async function call(method, ...args) {
    if (method.startsWith('perf.')) {
      const f = SimStoned[method.slice(5)];
      if (!f) throw new PerfError('Unsupported', method);
      return f(...args);
    }
    const f = M[method];
    if (!f) throw new PerfError('InvalidValue', 'método desconocido: ' + method);
    return f(args);
  }
  return { call, on: (ev, f) => { SimStoned.on(ev, f); (listeners[ev] = listeners[ev] || []).push(f); }, artUrl: (appid, kind) => artOf(appid, kind) };
})();
const PerfAPI = SimStoned;
window.SimPanel = SimPanel; window.SimStoned = SimStoned;
