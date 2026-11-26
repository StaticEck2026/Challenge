// Rasterise Modern-UI SVG frames to PNG with headless Chromium (Playwright).
//
//   node tools/modern/rasterize.js <out_dir> a.svg b.svg ...
//
// Each SVG is placed in a 1920x1080 page that declares the bundled fonts
// (godot/fonts: Inter, JetBrains Mono), so text renders with the same fonts
// Godot uses. Writes <out_dir>/<name>.png.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..', '..');
const fonts = path.join(root, 'godot', 'fonts');
const face = (family, file, weight) =>
  `@font-face{font-family:'${family}';src:url('file://${path.join(fonts, file)}');font-weight:${weight};}`;
const css = [
  face('Inter', 'Inter-Medium.otf', 500),
  face('Inter', 'Inter-Bold.otf', 700),
  face('JetBrains Mono', 'JetBrainsMono-Medium.ttf', 500),
  face('JetBrains Mono', 'JetBrainsMono-Medium.ttf', 700),
  'html,body{margin:0;padding:0;background:#0b0f14;}svg{display:block;}',
].join('\n');

(async () => {
  const [outDir, ...files] = process.argv.slice(2);
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  for (const f of files) {
    const svg = fs.readFileSync(f, 'utf8');
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body>${svg}</body></html>`;
    const tmp = path.join(outDir, '.page.html');
    fs.writeFileSync(tmp, html);
    await page.goto('file://' + tmp);
    await page.evaluate(() => document.fonts.ready);
    const out = path.join(outDir, path.basename(f).replace(/\.svg$/, '.png'));
    await page.screenshot({ path: out, clip: { x: 0, y: 0, width: 1920, height: 1080 } });
    console.log('[png] ' + out);
  }
  await browser.close();
})();
