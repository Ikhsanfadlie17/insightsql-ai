// E2E (Playwright): screenshot every page at desktop and phone width and report
// browser errors and Streamlit exceptions. Usage:
//   node scripts/e2e/all_pages.js <screenshot_dir> [base_url]
// Requires the backend and Streamlit running with a dataset imported and mapped
// (see scripts/e2e/README.md). Prints one `check` line per page.
const { chromium } = require('playwright');

const PAGES = [
  ['', 'Overview'], // the default page lives at /
  ['data_sources', 'Datasets'],
  ['ai_analyst', 'AI Analyst'],
  ['client_analytics', 'Retention rate'],
  ['query_history', 'Query History'],
  ['settings', 'AI usage and cost'],
];

(async () => {
  const [out, base = 'http://localhost:8501'] = process.argv.slice(2);
  const browser = await chromium.launch();
  let failures = 0;
  for (const [width, height, suffix] of [[1440, 2600, 'desktop'], [390, 2600, 'mobile']]) {
    const page = await browser.newPage({ viewport: { width, height } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    for (const [path, marker] of PAGES) {
      await page.goto(`${base}/${path}`, { waitUntil: 'networkidle' });
      let found = true;
      try {
        await page.getByText(marker).first().waitFor({ timeout: 30000 });
      } catch {
        found = false;
      }
      await page.waitForTimeout(2000);
      const exceptions = await page.locator('[data-testid="stException"]').count();
      const notFound = await page.getByText('Page not found').count();
      // Something does not fit: the page scrolls sideways, or a block is wider than the
      // main column (bordered containers clip it instead of scrolling).
      const overflow = await page.evaluate(() => {
        if (document.documentElement.scrollWidth > window.innerWidth + 1) return true;
        const main = document.querySelector('[data-testid="stMainBlockContainer"]');
        if (!main) return false;
        const limit = main.getBoundingClientRect().right + 1;
        return [...main.querySelectorAll('table, [data-testid="stMetric"], button')].some(
          (el) => el.getBoundingClientRect().right > limit && getComputedStyle(el).display !== 'block',
        );
      });
      await page.screenshot({ path: `${out}/${path || 'overview'}-${suffix}.png`, fullPage: true });
      const ok = found && exceptions === 0 && !overflow && notFound === 0;
      if (!ok) failures += 1;
      console.log('check', suffix, path || 'overview', ok, { found, exceptions, overflow, notFound });
    }
    console.log('page errors', suffix, errors.length, errors.slice(0, 3));
    failures += errors.length;
    await page.close();
  }
  await browser.close();
  process.exitCode = failures ? 1 : 0;
})();
