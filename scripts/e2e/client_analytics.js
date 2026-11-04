// E2E (Playwright). Usage: node scripts/e2e/client_analytics.js <screenshot_dir>
// Requires backend on :8000 and Streamlit on :8501 (see scripts/e2e/README.md).
const { chromium } = require('playwright');
(async () => {
  const [out] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 5200 } });
  const errors = []; p.on('pageerror', e => errors.push(String(e)));
  await p.goto('http://localhost:8501/client_analytics', { waitUntil: 'networkidle' });
  await p.getByText('Retention rate').first().waitFor({ timeout: 30000 });
  await p.waitForTimeout(2500);
  const t = await p.locator('body').innerText();
  for (const s of ['Active in 2025', 'Active in 2026', 'Retention rate', 'Existing active', 'returning ÷', 'Year-to-date'])
    console.log(s, t.includes(s));
  await p.screenshot({ path: `${out}/ca_full.png`, fullPage: true });
  await p.setViewportSize({ width: 420, height: 900 });
  await p.waitForTimeout(1500);
  await p.screenshot({ path: `${out}/ca_mobile.png`, fullPage: false });
  console.log('page errors', errors.length, errors.slice(0, 3));
  await b.close();
})().catch(e => { console.error('FAILED', e.message); process.exit(1); });
