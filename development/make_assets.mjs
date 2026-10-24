// Renders STONE Home's icon and the Steam library artwork of the STONE shortcut from HTML,
// with the bundled fonts, into system_files/usr/share/stone-home. Run after design changes:
//   (cd tests/e2e && npm ci) && node development/make_assets.mjs
import { chromium } from '../tests/e2e/node_modules/playwright/index.mjs';
import { mkdirSync, writeFileSync, rmSync, mkdtempSync } from 'node:fs';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const ui = path.join(here, '..', 'system_files', 'usr', 'share', 'stone-home');
mkdirSync(path.join(ui, 'grid'), { recursive: true });
const font = f => `url(file://${path.join(ui, 'fonts', f)})`;
const css = `@font-face{font-family:Jost;font-weight:200;src:${font('jost-latin-200-normal.woff2')}}
html,body{margin:0;height:100%;background:transparent}
.s{position:relative;width:100%;height:100%;overflow:hidden;font-family:Jost;font-weight:200;color:#ede9e3}
.bg{background:radial-gradient(ellipse at 50% 120%,#2b2c2f 0%,#141517 45%,#0a0a0b 80%)}
.w{position:absolute;left:0;right:0;text-align:center;letter-spacing:.45em;padding-left:.45em}
.l{position:absolute;height:var(--lh);background:#fff;border-radius:2px;box-shadow:0 0 18px rgba(255,255,255,.35)}`;
const items = [
  { file: 'icon.png', w: 256, h: 256, body: `<div class="s bg" style="border-radius:44px"><div class="l" style="--lh:8px;left:56px;right:56px;top:160px"></div></div>` },
  { file: 'grid/capsule.png', w: 600, h: 900, body: `<div class="s bg"><div class="w" style="top:330px;font-size:64px">STONE</div><div class="l" style="--lh:4px;left:150px;right:150px;top:560px"></div></div>` },
  { file: 'grid/hero.png', w: 1920, h: 620, body: `<div class="s bg"><div class="l" style="--lh:4px;left:720px;right:720px;top:420px"></div></div>` },
  { file: 'grid/header.png', w: 920, h: 430, body: `<div class="s bg"><div class="w" style="top:140px;font-size:72px">STONE</div><div class="l" style="--lh:4px;left:310px;right:310px;top:300px"></div></div>` },
  { file: 'grid/logo.png', w: 1280, h: 360, body: `<div class="s"><div class="w" style="top:110px;font-size:120px">STONE</div></div>` }
];
const browser = await chromium.launch();
for (const it of items) {
  const page = await browser.newPage({ viewport: { width: it.w, height: it.h } });
  const tmp = path.join(mkdtempSync(path.join(os.tmpdir(), 'stone-asset-')), 'a.html');   // file:// so the font loads
  writeFileSync(tmp, `<!doctype html><style>${css}</style>${it.body}`);
  await page.goto('file://' + tmp);
  rmSync(path.dirname(tmp), { recursive: true });
  await page.evaluate(async () => { await document.fonts.load('200 64px Jost'); await document.fonts.ready; });
  await page.screenshot({ path: path.join(ui, it.file), omitBackground: true });
  await page.close();
  console.log('wrote', it.file);
}
await browser.close();
