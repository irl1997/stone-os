/* After boot, Home tells stoned it is on screen; stoned answers with what it decided at boot
   (stoned --recover). Failures recorded during this boot's recovery are reported once. */
function bootNotices(st) {
  if (st.notice) toast(ERR_TEXT[st.notice] || st.notice, false);
  if (st.safe_mode && st.safe_reason === 'failures') return safeModeDlg();
  if (st.safe_mode) return toast(`STONE arrancó en Safe Mode (${SAFE_REASON[st.safe_reason]}). Para salir: Ajustes → Rendimiento`, false);
  const f = st.last_failure, bt = S.info && S.info.boot_time;
  if (!f || !bt || f.time < bt - 5 || f.time > bt + 900) return;
  if (f.during === 'test') toast(`La prueba no terminó. STONE volvió a la última configuración buena: ${profOf(S.prof).n}.`);
  else if (f.during === 'provisional') toast(`${f.profile ? 'CUSTOM 0' + f.profile.slice(1) : 'La ranura'} falló y quedó desactivada. Activo: ${profOf(S.prof).n}.`);
  else if (f.during === 'boot') toast(`STONE MACHINE no se apagó bien. Activo: ${profOf(S.prof).n}.`, false);
}
