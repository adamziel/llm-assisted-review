# Release checklist

This project can be published as a normal public GitHub repository, for example `adamziel/llm-assisted-review`.

## What to include

- `extension/` — unpacked MV3 Chrome extension.
- `server/` — local Python companion service.
- `server/config/actions.json` — editable stewardship actions and draft templates.
- `README.md` — setup and operating instructions.
- `docs/testing.md` — manual and automated test notes.

Do not include local runtime files:

- `server/triage.sqlite3`
- `__pycache__/`
- packaged `.zip` files committed to the repo

The included `.gitignore` excludes those files.

## Suggested first release assets

Attach a zip containing the source tree, excluding local runtime state:

```bash
rm -f triage-copilot-mvp.zip
zip -qr triage-copilot-mvp.zip triage-copilot \
  -x '*/__pycache__/*' '*.sqlite3' '*.zip'
```

Release notes should include:

1. Install `gh` and authenticate with `gh auth login` if you want richer GitHub context or apply mode.
2. Start the companion:
   ```bash
   cd server
   python3 server.py
   ```
3. Open `chrome://extensions`, enable Developer mode, click **Load unpacked**, and select `extension/`.
4. Open a GitHub issue or PR list/detail page.
5. Optional Codex mode:
   ```bash
   TRIAGE_PROVIDER=codex python3 server.py
   ```
6. Optional mutation mode, preferably only on a test repo first:
   ```bash
   TRIAGE_ALLOW_APPLY=1 python3 server.py
   ```

## Unreachable states

If the companion is not running, the extension shows:

- **Start server** on GitHub issue/PR list buttons.
- **Start local companion** on issue/PR detail panels.

Start `python3 server.py` from `server/`, then refresh GitHub.

If Codex mode is enabled but `codex` is unavailable, the companion returns the error to the extension. Restart without `TRIAGE_PROVIDER=codex` to use deterministic local heuristics.

## Secret handling

No secrets should be committed. The code does not embed API keys or tokens. Credentials stay in the user's local tools:

- GitHub authentication stays in the local `gh` CLI credential store.
- Codex authentication stays wherever the local Codex CLI stores it.
- Suggestions and applied-action audit logs stay in local SQLite unless the user shares them.
