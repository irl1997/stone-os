function catRows() {
  /* Settings Steam already handles stay in Steam; STONE says exactly where. */
  const steam = (t, d, where) => ({ l: t, s: d, v: 'En Steam', act: 'steam', where });
  const rel = (S.info && S.info.release) || {}, dep = (S.info && S.info.deployments) || null;
  switch (CATS[cat]) {
    case 'Pantalla': return { d: 'Ajusta cómo se ve STONE en tu televisor.', rows: [
      { l: 'Área segura', s: 'Usa ← → hasta que las cuatro esquinas se vean completas', v: `${S.safe} %`, key: 'safe' },
      steam('Resolución y frecuencia', 'Las fija Steam para todo el sistema', 'Botón STEAM → Ajustes → Pantalla'),
      steam('HDR', 'Según el televisor y el juego', 'Botón STEAM → Ajustes → Pantalla'),
      { l: 'Tamaño del texto', s: 'Se aplica a STONE Home', v: SIZES[S.ts], key: 'ts' }] };
    case 'Audio': return { d: 'Sonidos del sistema y volumen de la interfaz.', rows: [
      { l: 'Sonidos del sistema', s: 'Botones, encendido y apagado', v: S.sound ? 'Activados' : 'Desactivados', key: 'sound' },
      { l: 'Volumen de la interfaz', s: 'Independiente del volumen de los juegos', v: Math.round(S.vol * 100) + ' %', key: 'vol' },
      steam('Salida de audio', 'Altavoces, auriculares, HDMI o DisplayPort', 'Botón ··· → Audio, o botón STEAM → Ajustes → Audio')] };
    case 'Rendimiento': return { d: `Perfil activo: ${profOf(S.prof).n}. Elige entre silencio y rendimiento, o ajusta tu propio perfil.`, rows: [
      { l: 'Performance Center', s: 'SILENT, BALANCED, PERFORMANCE y CUSTOM', v: 'Abrir', act: 'perf' },
      { l: 'Modo técnico', s: 'Temperaturas, frecuencia y consumo en el menú de STONE durante el juego', v: S.tech ? 'Activado' : 'Desactivado', key: 'tech' },
      { l: 'Safe Mode', s: S.safeMode ? `Activo: ${SAFE_REASON[S.safeReason] || 'valores de fábrica'}` : 'Sólo valores de fábrica: sin CUSTOM, sin perfiles por juego ni pruebas', v: S.safeMode ? 'Salir' : 'Activar', act: 'safe' }] };
    case 'Energía': return { d: 'STONE MACHINE no tiene modo de reposo. Apagarla cuando no se usa ahorra entre 55 y 105 W.', rows: [
      { l: 'Apagar tras inactividad', s: 'Sólo en STONE Home, nunca con un juego abierto', v: IDLE[S.idle], key: 'idle' }] };
    case 'Accesibilidad': return { d: 'Haz que STONE sea más fácil de leer y más tranquilo.', rows: [
      { l: 'Alto contraste', s: 'Línea de 4 px y texto secundario más claro', v: S.hc ? 'Activado' : 'Desactivado', key: 'hc' },
      { l: 'Reducir movimiento', s: 'Sin escalas ni desplazamientos de fondo', v: S.rm ? 'Activado' : 'Desactivado', key: 'rm' },
      { l: 'Tamaño del texto', s: 'Se aplica a STONE Home', v: SIZES[S.ts], key: 'ts' }] };
    case 'Acerca de': return { d: 'STONE MACHINE · STONE OS', rows: [
      { l: 'Versión', s: rel.STONE_VERSION ? `STONE OS ${rel.STONE_VERSION}${rel.STONE_COMMIT ? ' · ' + rel.STONE_COMMIT.slice(0, 7) : ''}` : 'Leyendo…', v: '' },
      { l: 'Hardware', s: S.caps ? (S.caps.board ? 'AMD BC-250 detectada · Zen 2 6 núcleos · GPU 24 CU · 16 GB GDDR6' : 'No se detectó una AMD BC-250: el ajuste de rendimiento está desactivado') : 'Leyendo…', v: '' },
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
