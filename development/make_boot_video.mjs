// Renders STONE's Steam startup movie (system_files/usr/share/stone-home/boot/stone-startup.webm).
// It continues the Plymouth boot screen frame for frame: black, the white line at 82 % of the
// height and 25 % of the width (stone.script), then the STONE wordmark (0-4 s), then the line
// alone, breathing like Plymouth while Steam finishes loading (4-10 s). Its last frame is STONE
// Home's first frame (black and the full line), so whether STONE Home appears during the tail
// or after it, nothing jumps. Silent: STONE Home plays the startup sound.
//   (cd tests/e2e && npm ci) && node development/make_boot_video.mjs      (needs ffmpeg with libvpx-vp9)
import { chromium } from '../tests/e2e/node_modules/playwright/index.mjs';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const ui = path.join(here, '..', 'system_files', 'usr', 'share', 'stone-home');
const out = path.join(ui, 'boot', 'stone-startup.webm');
const W = 1920, H = 1080, FPS = 60, SECONDS = 10.0;
const tmp = mkdtempSync(path.join(os.tmpdir(), 'stone-boot-'));
mkdirSync(path.dirname(out), { recursive: true });

const html = `<!doctype html><style>@font-face{font-family:Jost;font-weight:200;src:url(file://${path.join(ui, 'fonts', 'jost-latin-200-normal.woff2')})}
html,body{margin:0;background:#000}</style><canvas id=c width=${W} height=${H}></canvas>
<script>
const c = document.getElementById('c'), x = c.getContext('2d');
const ease = t => t <= 0 ? 0 : t >= 1 ? 1 : 1 - Math.pow(1 - t, 3);          // no bounce, like STONE MOTION
const ramp = (t, a, b) => ease((t - a) / (b - a));
const lineY = Math.round(${H} * 0.82), thick = Math.max(2, Math.round(${H} / 540)), lineW = Math.round(${W} * 0.25);
function spaced(text, cx, y, track) {
  const ch = [...text], ws = ch.map(k => x.measureText(k).width), total = ws.reduce((a, b) => a + b, 0) + track * (ch.length - 1);
  let px = cx - total / 2; ch.forEach((k, i) => { x.fillText(k, px, y); px += ws[i] + track; });
}
window.draw = t => {
  x.fillStyle = '#000'; x.fillRect(0, 0, ${W}, ${H});
  // the line: picks up Plymouth's breathing level (~0.9) and settles at full brightness
  // tail: Plymouth's breathing (0.78-1.0, same period), easing in from 4 s and back to full by the end
  const breathe = t < 4 ? 0 : 0.11 * (1 - Math.cos(2 * Math.PI * (t - 4) / 3.0)) * (1 - ramp(t, 9.0, 10.0));
  const op = 0.9 + 0.1 * ramp(t, 0, 0.6) - breathe;
  const glow = 14 * (ramp(t, 0.4, 1.4) - 0.6 * ramp(t, 3.0, 3.7));
  x.save(); x.shadowColor = 'rgba(255,255,255,0.35)'; x.shadowBlur = glow;
  x.fillStyle = 'rgba(255,255,255,' + op + ')';
  x.fillRect(${W} / 2 - lineW / 2, lineY, lineW, thick); x.restore();
  // the wordmark, where STONE Home shows it on arrival
  const a = ramp(t, 1.0, 2.0) * (1 - ramp(t, 3.0, 3.7));
  if (a > 0) {
    x.font = '200 64px Jost'; x.textBaseline = 'alphabetic'; x.fillStyle = 'rgba(237,233,227,' + a + ')';
    spaced('STONE', ${W} / 2, 488, 64 * 0.45);
  }
  return c.toDataURL('image/png');
};
</script>`;
const page_file = path.join(tmp, 'boot.html');
writeFileSync(page_file, html);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: W, height: H } });
await page.goto('file://' + page_file);
await page.evaluate(async () => { await document.fonts.load('200 64px Jost'); });
const n = Math.round(FPS * SECONDS);
for (let i = 0; i < n; i++) {
  const url = await page.evaluate(t => window.draw(t), i / FPS);
  writeFileSync(path.join(tmp, `f${String(i).padStart(4, '0')}.png`), Buffer.from(url.split(',')[1], 'base64'));
}
await browser.close();
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(FPS), '-i', path.join(tmp, 'f%04d.png'),
  '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuv420p', '-crf', '28', '-b:v', '0', '-row-mt', '1', '-an', out], { stdio: 'inherit' });
rmSync(tmp, { recursive: true });
console.log('wrote', path.relative(path.join(here, '..'), out), `${W}x${H} ${FPS} fps ${SECONDS} s`);
