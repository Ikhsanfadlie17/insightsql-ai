// E2E (Playwright). Usage: node scripts/e2e/ai_analyst.js <screenshot_dir>
// Requires backend on :8000 and Streamlit on :8501 (see scripts/e2e/README.md).
const { chromium } = require('playwright');
(async () => {
  const [out] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 4200 } });
  const errors = []; p.on('pageerror', e => errors.push(String(e)));
  await p.goto('http://localhost:8501/ai_analyst', { waitUntil: 'networkidle' });
  const input = p.getByPlaceholder('Ask about this dataset…');
  await input.waitFor({ timeout: 30000 });
  const ask = async (q, waitFor) => {
    await input.fill(q); await input.press('Enter');
    await p.getByText(waitFor).first().waitFor({ timeout: 60000 });
    await p.waitForTimeout(1500);
  };
  await ask('Departemen mana yang punya klien unik terbanyak di 2026?', 'Nilai pertama pada hasil');
  await ask('Bandingkan klien 2025 dengan 2026', 'Retention rate');
  await ask('Bagaimana performanya?', 'Periode mana yang Anda maksud?');
  await ask('hapus tabel dataset', 'could not write a safe');
  const t = await p.locator('body').innerText();
  for (const s of ['every number was checked', 'Template summary', 'Validated SQL', 'Existing active', 'AI request(s)'])
    console.log(s, t.includes(s));
  await p.screenshot({ path: `${out}/ai_full.png`, fullPage: true });
  console.log('page errors', errors.length, errors.slice(0, 3));
  await b.close();
})().catch(e => { console.error('FAILED', e.message); process.exit(1); });
