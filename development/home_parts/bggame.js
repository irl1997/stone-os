function bgGame(g, sharp, veil) {
  S.bgFor = { i: g.i, sharp, veil };
  const u = heroUrl(g);
  if (!u) return setBg(neutralBg, veil, true);
  if (sharp) return setBg(`url("${u}")`, veil, true);
  const b = blurOf(g, () => { if (S.bgFor && S.bgFor.i === g.i && !S.bgFor.sharp) setBg(`url(${BLUR.get(g.appid)})`, S.bgFor.veil, true); });
  if (b) return setBg(`url(${b})`, veil, true);
  $('#veil').className = veil;
}
