function act(a) {
  if (S.screen === 'transition') return;
  if (S.screen === 'game') {
    if (a === 'home' || a === 'menu') return openQuick();
    if (a === 'a') { play('select'); return resumeGame(); }
    if (a === 'b') { play('back'); return returnHome(); }
    return;
  }
  showHints();
