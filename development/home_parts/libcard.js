function libCard(g, row, col) {
  const name = g.art.capsule ? '' : nameTag(g);
  return `<div class="card${g.inst ? '' : ' dim'}" data-f data-g="${g.i}" data-row="${row}" style="left:${(col * (LIB_COL_W + 40)).toFixed(2)}px;top:${row * LIB_ROW_H}px;background-image:${capUrl(g).replace(/"/g, '&quot;')}">${name}${runTag(g.i)}</div>`;
}
