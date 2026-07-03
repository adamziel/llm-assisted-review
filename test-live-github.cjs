const { chromium } = require('playwright');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');

(async () => {
  const root = process.cwd();
  const extensionPath = path.join(root, 'extension');
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'triage-copilot-chrome-'));
  const listShot = path.resolve(root, '../../outputs/triage-copilot-live-github-list.png');
  const detailShot = path.resolve(root, '../../outputs/triage-copilot-live-github-detail.png');

  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: false,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
      '--no-first-run',
      '--no-default-browser-check',
    ],
    viewport: { width: 1440, height: 1100 },
  });

  try {
    const page = await context.newPage();
    page.on('console', (message) => console.log(`[console:${message.type()}] ${message.text()}`));
    page.on('pageerror', (error) => console.log(`[pageerror] ${error.message}`));

    await page.goto('https://github.com/WordPress/wordpress-playground/issues', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(12000);
    const listResult = await page.evaluate(() => ({
      url: location.href,
      title: document.title,
      buttonCount: document.querySelectorAll('.codex-triage-list-button').length,
      buttons: Array.from(document.querySelectorAll('.codex-triage-list-button')).slice(0, 12).map((el) => ({ text: el.textContent, title: el.getAttribute('title') })),
      firstRows: Array.from(document.querySelectorAll('.Box-row, [data-testid="list-row"], div[id^="issue_"]')).slice(0, 8).map((el) => el.innerText.slice(0, 240)),
    }));
    await page.screenshot({ path: listShot, fullPage: true });

    await page.goto('https://github.com/WordPress/wordpress-playground/issues/3845', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(8000);
    const detailResult = await page.evaluate(() => ({
      url: location.href,
      title: document.title,
      hasPanel: !!document.querySelector('#codex-triage-detail-panel'),
      panelText: document.querySelector('#codex-triage-detail-panel')?.innerText.slice(0, 1400) || null,
      commentPreview: document.querySelector('#codex-triage-detail-panel textarea')?.value.slice(0, 500) || null,
    }));
    await page.screenshot({ path: detailShot, fullPage: true });

    console.log(JSON.stringify({ listResult, detailResult, listShot, detailShot }, null, 2));
  } finally {
    await context.close();
  }
})();
