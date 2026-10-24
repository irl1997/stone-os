// STONE Home end to end, driven like the console: keyboard (the same actions as the gamepad),
// against the production page, the real host (devserver) and a real stoned on a private bus.
// Started by tests/e2e_home.py, which prepares everything and checks the governor afterwards.
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';

const URL = process.env.STONE_HOME_URL;
const errors = [];
const log = m => console.log(m);
const fail = m => { throw new Error(m); };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on('pageerror', e => errors.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });

const key = async (k, n = 1, pause = 260) => { for (let i = 0; i < n; i++) { await page.keyboard.press(k); await page.waitForTimeout(pause); } };
const text = sel => page.$eval(sel, e => e.textContent);
const waitText = (sel, re, timeout = 15000) => page.waitForFunction(([s, r]) => {
  const e = document.querySelector(s); return e && new RegExp(r).test(e.textContent);
}, [sel, re.source], { timeout });
// Move along the focused row until the focused element reads `re` (the line follows focus).
const focusTo = async re => {
  await key('ArrowLeft', 5, 120);
  for (let i = 0; i < 8; i++) {
    const t = await page.$eval('.f', e => e.textContent.trim()).catch(() => '');
    if (re.test(t)) return;
    await key('ArrowRight');
  }
  fail(`could not focus ${re}`);
};
const shot = async name => { if (process.env.STONE_E2E_SHOTS) await page.screenshot({ path: `${process.env.STONE_E2E_SHOTS}/${name}.png` }); };
const dbus = (method, ...args) => execFileSync('dbus-send', ['--session', '--print-reply', '--dest=org.stonemachine.Performance1',
  '/org/stonemachine/Performance1', `org.stonemachine.Performance1.${method}`, ...args], { env: process.env });

try {
  await page.goto(URL);
  // 1. Boot -> Home with the Steam library (most recent first), artwork from Steam's cache
  await page.waitForSelector('#home.on .card.hero', { timeout: 20000 });
  await waitText('#ht', /^Marea Lenta$/);
  const heroBg = await page.$eval('#home .card.hero', e => e.style.backgroundImage);
  if (!heroBg.includes('/art/1001/hero')) fail(`hero art not from the library: ${heroBg}`);
  await page.waitForTimeout(1200); await shot('home');
  log('PASS  boot -> Home, library from Steam files, artwork');

  // 2. Names from Steam's files are text, never markup
  await key('ArrowRight');
  await waitText('#ht', /^Basalto <b>&<\/b>$/);
  if (await page.$('#home .card b, #home .ctx b')) fail('markup from a game name reached the page');
  log('PASS  game names are escaped');

  // 3. Settings -> Rendimiento -> Performance Center -> PERFORMANCE, shown from the governor's answer
  await key('ArrowDown'); await focusTo(/^Ajustes$/); await key('Enter');
  await page.waitForSelector('#settings.on');
  await key('ArrowDown', 6);
  await waitText('#settings .cats .cur', /^Rendimiento$/);
  await key('ArrowRight'); await key('Enter');
  await page.waitForSelector('#perf.on');
  await waitText('#perf .pstatus', /GPU hasta 1850 MHz/);
  await waitText('#pread', /GPU 55(\.0)? °C · CPU 48(\.0)? °C · 1500 MHz/, 8000);   // live readings from stoned's sensors
  await key('ArrowRight'); await key('Enter');
  await waitText('#toast', /PERFORMANCE activo · GPU hasta 2000 MHz/);
  await waitText('#perf .pstatus', /Activo: PERFORMANCE.*GPU hasta 2000 MHz/);
  await shot('performance-center');
  log('PASS  Performance Center applies PERFORMANCE and shows what the governor reports');

  // 4. Per-game profile from the game's page, then launch through Steam
  await key('h');
  await page.waitForSelector('#home.on');
  await key('Enter');
  await page.waitForSelector('#detail.on');
  await key('ArrowDown'); await focusTo(/^Rendimiento: Global$/); await key('Enter');
  await waitText('#toast', /Marea Lenta usará SILENT al iniciarse/);
  await key('ArrowUp'); await key('Enter');
  await page.waitForSelector('#game.on', { timeout: 15000 });
  await waitText('#game .tag', /Marea Lenta · en curso/);
  log('PASS  JUGAR asks Steam to start the game; the game screen follows');

  // 5. stoned saw the game (fake Steam announced it): quick menu shows the per-game profile
  await page.waitForTimeout(800);
  await key('Tab');
  await page.waitForSelector('#quick.on');
  await waitText('#quick .seg .lbl', /Rendimiento · SILENT/);
  await page.waitForTimeout(800); await shot('quick-menu');
  await key('Escape');
  log('PASS  per-game profile active during the game (from stoned state)');

  // 6. The game ends (Steam's reaper gone): Home comes back on its own
  dbus('GameEnded', 'string:1001', 'boolean:false');
  await page.waitForSelector('#home.on', { timeout: 15000 });
  log('PASS  game end returns to Home');

  // 7. Power off goes to logind (recorded by the dev server)
  await key('ArrowDown'); await focusTo(/^Apagar$/); await key('Enter');
  await page.waitForSelector('#power.on');
  await key('Enter');
  await waitText('#offmsg', /Apagando/, 20000);
  log('PASS  APAGAR requests a power off');

  if (errors.length) fail('page errors: ' + errors.join(' | '));
  log('E2E OK');
} catch (e) {
  await page.screenshot({ path: process.env.STONE_E2E_SHOT || '/tmp/stone-e2e-fail.png' }).catch(() => {});
  console.error('E2E FAIL: ' + e.message);
  if (errors.length) console.error('page errors: ' + errors.join(' | '));
  process.exitCode = 1;
} finally {
  await browser.close();
}
