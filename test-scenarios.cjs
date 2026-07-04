const { chromium } = require('playwright');
const path = require('node:path');

const root = process.cwd();
const outputs = path.resolve(root, '../../outputs');

async function injectExtension(page) {
  await page.addStyleTag({ path: path.join(root, 'extension/content.css') });
  await page.addScriptTag({ path: path.join(root, 'extension/content.js') });
}

async function waitForList(page, expected) {
  await page.waitForFunction((count) => document.querySelectorAll('.codex-triage-list-button[data-state="ready"]').length >= count, expected, { timeout: 15000 });
}

async function waitForPanel(page) {
  await page.waitForFunction(() => {
    const panel = document.querySelector('#codex-triage-detail-panel');
    const title = panel?.querySelector('[data-role="summary-title"]');
    return panel && title && title.textContent !== 'Checking current thread…';
  }, null, { timeout: 15000 });
}

(async () => {
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage({ viewport: { width: 1360, height: 980 } });
  const base = 'http://127.0.0.1:8765';

  try {
    await page.goto(`${base}/local/scenarios/issues`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await injectExtension(page);
    await waitForList(page, 10);
    await page.screenshot({ path: path.join(outputs, 'triage-copilot-scenarios-list.png'), fullPage: true });

    await page.click('.codex-triage-list-button[data-action="needs-design"]');
    await page.waitForFunction(() => document.querySelector('.codex-triage-popover')?.dataset.action === 'needs-design', null, { timeout: 10000 });
    await page.screenshot({ path: path.join(outputs, 'triage-copilot-scenarios-popover.png'), fullPage: true });

    const scenarios = [
      ['needs-proof', '/local/scenarios/issues/101'],
      ['needs-design', '/local/scenarios/issues/102'],
      ['fast-merge', '/local/scenarios/pull/103'],
      ['needs-execution-plan', '/local/scenarios/pull/104'],
      ['medium-review', '/local/scenarios/pull/105'],
      ['close-not-actionable', '/local/scenarios/issues/106'],
      ['waiting-author', '/local/scenarios/issues/108'],
      ['no-capacity', '/local/scenarios/issues/109'],
      ['duplicate-of', '/local/scenarios/issues/110'],
      ['close-solved', '/local/scenarios/issues/111'],
      ['candidate-pr-review', '/local/scenarios/issues/112'],
      ['candidate-pr-wait', '/local/scenarios/issues/113'],
      ['no-action', '/local/scenarios/issues/107'],
    ];

    for (const [name, route] of scenarios) {
      await page.goto(`${base}${route}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await injectExtension(page);
      await waitForPanel(page);
      await page.locator('#codex-triage-detail-panel').screenshot({ path: path.join(outputs, `triage-copilot-scenario-${name}.png`) });
    }

    await page.goto(`${base}/local/scenarios/issues/101`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await injectExtension(page);
    await waitForPanel(page);
    await page.fill('#codex-triage-detail-panel [data-role="comment"]', '@bug-reporter Thanks — I edited this draft inline before inserting it.');
    await page.locator('#codex-triage-detail-panel').screenshot({ path: path.join(outputs, 'triage-copilot-scenario-editable-draft.png') });

    await page.goto(`${base}/local/scenarios/pull/104`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await injectExtension(page);
    await waitForPanel(page);
    await page.click('#codex-triage-detail-panel [data-role="action-menu-button"]');
    await page.locator('#codex-triage-detail-panel').screenshot({ path: path.join(outputs, 'triage-copilot-scenario-action-menu.png') });
  } finally {
    await browser.close();
  }
})();
