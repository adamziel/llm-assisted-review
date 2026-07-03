# GitHub Triage Copilot MVP

Local-first GitHub triage assistant for WordPress Playground-style issue/PR stewardship.

It has two pieces:

- **Chrome extension** (`extension/`) that injects triage UI into GitHub issue/PR lists and detail pages.
- **Local companion service** (`server/`) that stores suggestions in SQLite, fetches current GitHub context through `gh`, and optionally asks Codex for a structured triage proposal.

The extension never mutates GitHub directly. Submitting actions goes through the local server and is disabled unless `TRIAGE_ALLOW_APPLY=1` is set.

## Quick start

```bash
cd server
python3 server.py
```

If the extension cannot reach the local companion, GitHub will show **Start local companion** on detail pages and **Start server** on list rows. Start the command above and refresh GitHub.

Then load the unpacked extension from:

```text
extension/
```

Open a GitHub issue or PR page. A **Triage** panel should appear.

The default UI is intentionally compact: list pages show a subtle next-action marker, and detail pages show the proposed action, a short private rationale, and an inline editable draft reply. The small arrow beside the action title opens a menu for swapping to a different stewardship action.

## Screenshot

![Decline / close triage workflow](docs/assets/triage-copilot-scenario-close-not-actionable.png)

## Stewardship actions covered

- **Review normally**: small/scoped, ready for the usual review queue.
- **Ask for reproduction**: needs steps, logs, benchmark, or proof before review.
- **Waiting on contributor**: a maintainer already asked for something; no new public action yet, just wait until the follow-up window expires.
- **Move to proposal/design**: public API, product direction, or architecture needs agreement before implementation review.
- **Accepted direction, split first**: the idea is acceptable, but the PR is too large to review safely as one unit.
- **Find an owner**: plausible work, but no clear accountable owner/reviewer yet.
- **Useful, no capacity**: aligned work, but maintainers should not imply available review capacity without an owner.
- **Decline / close**: stale after follow-up, out of scope, insufficient information, or otherwise not actionable.
- **No action**: already handled, closed, merged, or no mutation needed.

## What the local companion does

The Chrome extension cannot run local tools by itself. The companion is a small Python HTTP service on `127.0.0.1:8765` that:

- receives the current GitHub repo, issue/PR number, and page context from the extension;
- fetches fuller issue/PR context with the local `gh` CLI when available;
- stores suggestion results in `server/triage.sqlite3` so refreshes do not recompute unchanged items;
- returns the suggested action, private rationale, draft public comment, and proposed operations;
- optionally invokes `codex exec` when `TRIAGE_PROVIDER=codex` is set;
- optionally applies operations through `gh` only when `TRIAGE_ALLOW_APPLY=1` is set.

By default it uses the deterministic heuristic provider and does not need Codex or any API key.

## Publishing and secrets

This repository is safe to publish as source code. It should not contain tokens, API keys, cookies, GitHub credentials, or Codex credentials. Runtime state such as `server/triage.sqlite3`, `__pycache__/`, and zip artifacts are ignored by `.gitignore` and excluded from the packaged release zip.

The extension requests host access only for GitHub and the local companion URL (`http://127.0.0.1:8765/*`). GitHub mutations are disabled by default and require starting the companion with `TRIAGE_ALLOW_APPLY=1`.

## Optional Codex provider

By default the server uses a deterministic local heuristic provider so the UI can be tested without spending tokens.

To use Codex CLI suggestions:

```bash
TRIAGE_PROVIDER=codex python3 server.py
```

The Codex provider runs `codex exec` with a JSON output schema and stores results in SQLite keyed by the current item fingerprint.

## Optional apply mode

To actually post comments, add labels, or close issues/PRs through `gh`:

```bash
TRIAGE_ALLOW_APPLY=1 python3 server.py
```

The extension shows the exact operations before applying. Move-to-discussion remains manual in this MVP.

## Local data

SQLite database:

```text
server/triage.sqlite3
```

Cache keys include the repo, item type, number, state, labels, updated timestamp, current body, recent comments/reviews, PR draft state, and PR head SHA when available.
