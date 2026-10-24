const FILTERS = ['Todos', 'Steam', 'Accesos directos', 'Favoritos'];
function libList() {
  const f = S.filter;
  return G.filter(g => f === 0 || (f === 1 && g.src === 'steam') || (f === 2 && g.src === 'shortcut') || (f === 3 && g.fav));
}
function emptyText() {
  if (!S.libFound) return 'No se encontró la biblioteca de Steam. Inicia sesión en Steam y vuelve a STONE.';
  if (S.filter === 3) return 'Aún no hay favoritos. Márcalos desde la ficha de cada juego.';
  if (S.filter === 2) return 'Sin accesos directos. Los lanzadores y emuladores que añaden sus juegos a Steam aparecen aquí.';
  return G.length ? 'No hay juegos con este filtro.' : 'Aún no hay juegos instalados. Instálalos desde la Tienda de Steam.';
}
