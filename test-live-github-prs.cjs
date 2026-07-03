const { chromium } = require('playwright');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');

(async () => {
  const root = process.cwd();
  const extensionPath = path.join(root, 'extension');
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'triage-copilot-chrome-'));
  const listShot = path.resolve(root, '../../outputs/triage-copilot-live-github-pr-list.png');
  const detailShot = path.resolve(root, '../../outputs/triage-copilot-live-github-pr-detail.png');
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: false,
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`, '--no-first-run'],
    viewport: { width: 1440, height: 1100 },
  });
  try {
    const page = await context.newPage();
    page.on('console', (message) => console.log(`[console:${message.type()}] ${message.text()}`));
    await page.goto('https://github.com/WordPress/wordpress-playground/pulls', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(12000);
    const listResult = await page.evaluate(() => ({
      url: location.href,
      title: document.title,
      buttonCount: document.querySelectorAll('.codex-triage-list-button').length,
      buttons: Array.from(document.querySelectorAll('.codex-triage-list-button')).slice(0, 12).map((el) => ({ text: el.textContent, title: el.getAttribute('title') })),
    }));
    await page.screenshot({ path: listShot, fullPage: true });
    const firstPrHref = await page.evaluate(() => Array.from(document.querySelectorAll('a[href*="/pull/"]')).map(a => a.href).find(h => /\/pull\/\d+$/.test(new URL(h).pathname)) || 'https://github.com/WordPress/wordpress-playground/pull/3725');
    await page.goto(firstPrHref, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(8000);
    const detailResult = await page.evaluate(() => ({
      url: location.href,
      title: document.title,
      hasPanel: !!document.querySelector('#codex-triage-detail-panel'),
      panelText: document.querySelector('#codex-triage-detail-panel')?.innerText.slice(0, 1200) || null,
      commentPreview: document.querySelector('#codex-triage-detail-panel textarea')?.value.slice(0, 500) || null,
    }));
    await page.screenshot({ path: detailShot, fullPage: true });
    console.log(JSON.stringify({ listResult, detailResult, listShot, detailShot }, null, 2));
  } finally {
    await context.close();
  }
})();
