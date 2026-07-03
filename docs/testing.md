# Testing notes

## Server checks

```bash
cd server
python3 server.py
curl -s http://127.0.0.1:8765/health
curl -s -X POST http://127.0.0.1:8765/api/suggest \
  -H 'content-type: application/json' \
  -d '{"repo":"WordPress/wordpress-playground","type":"issue","number":3845}'
```

## Chrome DevTools MCP fixture test

The local server exposes GitHub-shaped fixture pages for testing without mutating GitHub:

- Detail fixture: `http://127.0.0.1:8765/WordPress/wordpress-playground/issues/3845`
- List fixture: `http://127.0.0.1:8765/WordPress/wordpress-playground/issues`

In Chrome DevTools MCP, open the fixture page and inject the extension assets:

```js
async () => {
  const css = await fetch('/extension/content.css').then(r => r.text());
  document.head.append(Object.assign(document.createElement('style'), { textContent: css }));
  const js = await fetch('/extension/content.js').then(r => r.text());
  (0, eval)(js);
  await new Promise(resolve => setTimeout(resolve, 2000));
  return {
    hasPanel: !!document.querySelector('#codex-triage-detail-panel'),
    panelText: document.querySelector('#codex-triage-detail-panel')?.innerText
  };
}
```

Expected result: the detail page shows a compact Triage panel with the proposed next action, private rationale directly under the action title, label add/remove chips above the inline editable draft reply, and an action-menu arrow for swapping to a different stewardship action. Submitting mutations still requires `TRIAGE_ALLOW_APPLY=1`.

The list fixture should show compact action pills beside each issue/PR title, for example **Ask proof**, **Fast merge**, **Medium**, **Proposal**, **Plan first**, and **Close**.

## Live GitHub test performed

The extension was tested against the real public GitHub pages, not only fixtures:

- `https://github.com/WordPress/wordpress-playground/issues`
- `https://github.com/WordPress/wordpress-playground/issues/3845`
- `https://github.com/WordPress/wordpress-playground/pulls`
- one live PR detail page selected from the PR list

A first direct page-injection attempt failed on GitHub because page-context `fetch()` to `127.0.0.1` is blocked by the browser private-network/CORS model. The extension now routes API requests through an MV3 background service worker (`extension/background.js`), which has localhost host permission and can communicate with the companion server.

Screenshots from the live test are saved in the Codex outputs folder:

- `triage-copilot-live-github-list.png`
- `triage-copilot-live-github-detail.png`
- `triage-copilot-live-github-pr-list.png`
- `triage-copilot-live-github-pr-detail.png`

## Scenario screenshot set

The local scenario fixture covers the stewardship states discussed in the report/conversation:

- needs proof before review
- needs design/proposal acceptance
- fast merge
- medium review with a clear review budget
- accepted direction that still needs an execution plan
- waiting after a maintainer already asked for follow-up
- close quickly after author timeout
- aligned work with no current maintainer capacity
- no action for already-closed work

Run:

```bash
NODE_PATH=/Users/cloudnik/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules \
/Users/cloudnik/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node test-scenarios.cjs
```

Screenshots are saved as `triage-copilot-scenarios-*.png` and `triage-copilot-scenario-*.png` in the Codex outputs folder.
