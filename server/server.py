#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("TRIAGE_DB", ROOT / "triage.sqlite3"))
ACTIONS_PATH = Path(os.environ.get("TRIAGE_ACTIONS", ROOT / "config" / "actions.json"))
SCHEMA_PATH = ROOT / "schema.json"
HOST = os.environ.get("TRIAGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("TRIAGE_PORT", "8765"))
PROVIDER = os.environ.get("TRIAGE_PROVIDER", "heuristic")
ALLOW_APPLY = os.environ.get("TRIAGE_ALLOW_APPLY") == "1"
ALLOWED_REPO = "WordPress/wordpress-playground"
FIXTURE_REPO = "local/scenarios"


def main() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Triage companion listening on http://{HOST}:{PORT}")
    print(f"Provider: {PROVIDER}; apply enabled: {ALLOW_APPLY}; db: {DB_PATH}")
    server.serve_forever()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS suggestions (
                repo TEXT NOT NULL,
                item_type TEXT NOT NULL,
                number INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                title TEXT NOT NULL,
                source_json TEXT NOT NULL,
                suggestion_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (repo, item_type, number, fingerprint)
            );
            CREATE TABLE IF NOT EXISTS applied_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                item_type TEXT NOT NULL,
                number INTEGER NOT NULL,
                fingerprint TEXT,
                suggestion_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )


class Handler(BaseHTTPRequestHandler):
    server_version = "TriageCompanion/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/health":
            self._json({"ok": True, "provider": PROVIDER, "applyEnabled": ALLOW_APPLY})
        elif route == "/api/actions":
            self._json(load_actions())
        elif route.startswith("/extension/"):
            self._static_extension(route.removeprefix("/extension/"))
        elif self._maybe_fixture(route):
            return
        else:
            self._json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            payload = self._read_json()
            if route == "/api/suggest":
                self._json(handle_suggest(payload))
            elif route == "/api/apply":
                self._json(handle_apply(payload))
            else:
                self._json({"error": "not_found"}, status=404)
        except Exception as exc:
            self._json({"error": type(exc).__name__, "message": str(exc)}, status=500)

    def _read_json(self) -> dict:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _json(self, data: dict, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _static_extension(self, name: str) -> None:
        if name not in {"content.js", "content.css", "manifest.json"}:
            self._json({"error": "not_found"}, status=404)
            return
        path = ROOT.parent / "extension" / name
        raw = path.read_bytes()
        content_type = "application/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "application/json"
        self._bytes(raw, content_type)

    def _maybe_fixture(self, route: str) -> bool:
        parts = [part for part in route.split("/") if part]
        if len(parts) < 3:
            return False
        owner, repo_name, area = parts[:3]
        if area not in {"issues", "pull", "pulls"}:
            return False
        repo = f"{owner}/{repo_name}"
        if repo == "local/scenarios":
            if len(parts) >= 4 and parts[3].isdigit():
                self._bytes(scenario_detail_html(owner, repo_name, area, int(parts[3])).encode(), "text/html")
                return True
            self._bytes(scenario_list_html(owner, repo_name).encode(), "text/html")
            return True
        if len(parts) >= 4 and parts[3].isdigit():
            number = parts[3]
            kind = "pull" if area in {"pull", "pulls"} else "issue"
            title = f"Fixture {kind} #{number}"
            html = f"""<!doctype html><meta charset="utf-8"><title>{title}</title>
            <main style="max-width:980px;margin:40px auto;font-family:Arial,sans-serif">
              <div id="partial-discussion-header"><h1><span>{title}</span></h1><p>Local GitHub-shaped fixture for {repo}.</p></div>
              <div class="timeline-comment"><textarea name="comment" aria-label="Leave a comment" placeholder="Leave a comment" style="width:100%;height:120px"></textarea></div>
            </main>"""
            self._bytes(html.encode(), "text/html")
            return True
        html = f"""<!doctype html><meta charset="utf-8"><title>Fixture list</title>
        <main style="max-width:980px;margin:40px auto;font-family:Arial,sans-serif">
          <h1>{repo} {area}</h1>
          <div class="Box-row"><a href="/{owner}/{repo_name}/issues/3845">Ooops! WordPress Playground had a hiccup!</a></div>
          <div class="Box-row"><a href="/{owner}/{repo_name}/issues/3815">Autosave progress announcements emitted constantly for screen readers</a></div>
          <div class="Box-row"><a href="/{owner}/{repo_name}/pull/3725">Draft native TypeScript Blueprint v2 runner</a></div>
        </main>"""
        self._bytes(html.encode(), "text/html")
        return True

    def _bytes(self, raw: bytes, content_type: str) -> None:
        self.send_response(200)
        self._cors()
        self.send_header("content-type", content_type + "; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _cors(self) -> None:
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type")
        self.send_header("access-control-allow-private-network", "true")

    def log_message(self, fmt: str, *args) -> None:  # quieter local server logs
        print(f"{self.address_string()} - {fmt % args}")


def handle_suggest(payload: dict) -> dict:
    repo = ensure_supported_repo(require(payload, "repo"), allow_fixture=True)
    item_type = normalize_type(require(payload, "type"))
    number = int(require(payload, "number"))
    force = bool(payload.get("force"))

    source = fetch_item(repo, item_type, number, payload)
    fingerprint = compute_fingerprint(repo, item_type, number, source)

    if not force:
        cached = get_cached(repo, item_type, number, fingerprint)
        if cached:
            cached["cached"] = True
            return cached

    suggestion = generate_suggestion(repo, item_type, number, source)
    response = {
        "repo": repo,
        "type": item_type,
        "number": number,
        "fingerprint": fingerprint,
        "cached": False,
        "source": summarize_source(source),
        "suggestion": suggestion,
        "actions": load_actions()["actions"],
        "applyEnabled": ALLOW_APPLY,
    }
    put_cached(repo, item_type, number, fingerprint, source, response)
    return response


def handle_apply(payload: dict) -> dict:
    repo = ensure_supported_repo(require(payload, "repo"))

    if not ALLOW_APPLY:
        return {"ok": False, "dryRun": True, "message": "Submitting is disabled. Restart with TRIAGE_ALLOW_APPLY=1.", "operations": payload.get("operations", [])}

    item_type = normalize_type(require(payload, "type"))
    number = int(require(payload, "number"))
    comment = (payload.get("comment") or "").strip()
    operations = payload.get("operations") or []
    results = []

    for op in operations:
        op_type = op.get("type")
        if op_type == "comment":
            if comment:
                cmd = ["gh", "pr" if item_type == "pr" else "issue", "comment", str(number), "--repo", repo, "--body-file", "-"]
                results.append(run_gh(cmd, input_text=comment))
            else:
                results.append({"operation": op, "skipped": True, "reason": "empty comment"})
        elif op_type == "addLabel":
            label = op.get("label")
            if label:
                results.append(run_gh(["gh", "issue", "edit", str(number), "--repo", repo, "--add-label", label]))
        elif op_type == "removeLabel":
            label = op.get("label")
            if label:
                results.append(run_gh(["gh", "issue", "edit", str(number), "--repo", repo, "--remove-label", label]))
        elif op_type == "close":
            results.append(run_gh(["gh", "pr" if item_type == "pr" else "issue", "close", str(number), "--repo", repo]))
        else:
            results.append({"operation": op, "skipped": True, "reason": "unsupported operation in MVP"})

    result = {"ok": all(r.get("ok") or r.get("skipped") for r in results), "results": results}
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT INTO applied_actions(repo,item_type,number,fingerprint,suggestion_json,result_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (repo, item_type, number, payload.get("fingerprint"), json.dumps(payload, ensure_ascii=False), json.dumps(result, ensure_ascii=False), int(time.time())),
        )
    return result


def fetch_item(repo: str, item_type: str, number: int, page_payload: dict) -> dict:
    if repo == "local/scenarios":
        return scenario_source(item_type, number, page_payload)

    json_fields = ["number", "title", "body", "labels", "state", "author", "updatedAt", "comments", "url"]
    if item_type == "pr":
        json_fields += ["isDraft", "additions", "deletions", "changedFiles", "headRefOid", "reviews"]
        cmd = ["gh", "pr", "view", str(number), "--repo", repo, "--json", ",".join(json_fields)]
    else:
        cmd = ["gh", "issue", "view", str(number), "--repo", repo, "--json", ",".join(json_fields)]
    try:
        item = json.loads(subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL))
    except Exception:
        item = {
            "number": number,
            "title": page_payload.get("title") or f"#{number}",
            "body": page_payload.get("bodyText") or "",
            "labels": [{"name": label} for label in page_payload.get("labels", [])],
            "state": "OPEN",
            "updatedAt": page_payload.get("updatedAt") or "",
            "comments": [],
            "url": page_payload.get("url") or f"https://github.com/{repo}/{item_type == 'pr' and 'pull' or 'issues'}/{number}",
        }
    item["_page"] = {k: page_payload.get(k) for k in ["title", "labels", "url"] if page_payload.get(k) is not None}
    return item


def scenario_list_html(owner: str, repo_name: str) -> str:
    rows = []
    for number, data in scenario_catalog().items():
        path_area = "pull" if data["type"] == "pr" else "issues"
        kind = "Pull request" if data["type"] == "pr" else "Issue"
        rows.append(f"""
          <div class="Box-row scenario-row">
            <a data-testid="issue-pr-title-link" href="/{owner}/{repo_name}/{path_area}/{number}">{escape_text(data["title"])}</a>
            <div class="scenario-meta">#{number} · {kind} · {escape_text(data["caption"])}</div>
          </div>
        """)
    return f"""<!doctype html><meta charset="utf-8"><title>Triage scenario list</title>
    <style>
      body {{ margin:0; color:#24292f; background:#f6f8fa; font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
      main {{ max-width:1040px; margin:36px auto; padding:0 24px; }}
      h1 {{ margin:0 0 8px; font-size:28px; }}
      .intro {{ color:#57606a; margin:0 0 24px; }}
      .Box-row {{ padding:14px 18px; border:1px solid #d0d7de; border-bottom:0; background:white; }}
      .Box-row:first-of-type {{ border-radius:10px 10px 0 0; }}
      .Box-row:last-of-type {{ border-bottom:1px solid #d0d7de; border-radius:0 0 10px 10px; }}
      a {{ color:#24292f; font-weight:700; text-decoration:none; }}
      a:hover {{ color:#0969da; }}
      .scenario-meta {{ margin-top:4px; color:#57606a; font-size:12px; }}
    </style>
    <main>
      <h1>Stewardship triage scenarios</h1>
      <p class="intro">Local GitHub-shaped fixture for capturing every default decision state without touching GitHub.</p>
      {''.join(rows)}
    </main>"""


def scenario_detail_html(owner: str, repo_name: str, area: str, number: int) -> str:
    data = scenario_catalog().get(number) or scenario_catalog()[101]
    status = "Merged" if data["state"] != "OPEN" and data["type"] == "pr" else "Closed" if data["state"] != "OPEN" else "Open"
    return f"""<!doctype html><meta charset="utf-8"><title>{escape_text(data["title"])}</title>
    <style>
      body {{ margin:0; color:#24292f; background:#fff; font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
      main {{ max-width:1040px; margin:34px auto; padding:0 24px; }}
      header {{ border-bottom:1px solid #d8dee4; padding-bottom:16px; }}
      h1 {{ margin:0 0 10px; font-size:30px; font-weight:600; }}
      .state {{ display:inline-flex; border-radius:999px; padding:5px 10px; background:#1a7f37; color:white; font-weight:700; }}
      .state.closed {{ background:#8250df; }}
      .layout {{ display:grid; grid-template-columns:minmax(0, 1fr) 240px; gap:28px; margin-top:24px; }}
      .comment {{ border:1px solid #d0d7de; border-radius:8px; overflow:hidden; background:#fff; }}
      .comment-head {{ background:#f6f8fa; border-bottom:1px solid #d8dee4; padding:10px 14px; font-weight:700; }}
      .comment-body {{ padding:18px; line-height:1.55; white-space:pre-wrap; }}
      textarea {{ width:100%; min-height:120px; border:1px solid #d0d7de; border-radius:8px; margin-top:18px; padding:10px; font:inherit; }}
      aside {{ color:#57606a; font-size:12px; }}
      aside h2 {{ color:#57606a; font-size:14px; margin-top:0; }}
    </style>
    <main>
      <header>
        <h1>{escape_text(data["title"])} <span style="color:#57606a">#{number}</span></h1>
        <span class="state {'closed' if data["state"] != "OPEN" else ''}">{status}</span>
      </header>
      <div class="layout">
        <section>
          <div class="comment">
            <div class="comment-head">Description</div>
            <div class="comment-body">{escape_text(data["body"])}</div>
          </div>
          <textarea name="comment" aria-label="Leave a comment" placeholder="Leave a comment"></textarea>
        </section>
        <aside>
          <h2>Scenario</h2>
          <p>{escape_text(data["caption"])}</p>
        </aside>
      </div>
    </main>"""


def scenario_source(item_type: str, number: int, page_payload: dict) -> dict:
    data = dict(scenario_catalog().get(number) or scenario_catalog()[101])
    data["number"] = number
    data["url"] = page_payload.get("url") or f"http://127.0.0.1:8765/local/scenarios/{'pull' if data['type'] == 'pr' else 'issues'}/{number}"
    data["labels"] = [{"name": label} for label in data.get("labels", [])]
    data["comments"] = data.get("comments", [])
    data["_page"] = {k: page_payload.get(k) for k in ["title", "labels", "url"] if page_payload.get(k) is not None}
    return data


def scenario_catalog() -> dict[int, dict]:
    return {
        101: {
            "type": "issue",
            "title": "Crash report with no reproduction",
            "caption": "Needs proof before review",
            "body": "Playground crashes. See developer tools for details.",
            "labels": ["[Type] Bug", "[Status] Needs Owner"],
            "state": "OPEN",
            "author": {"login": "bug-reporter"},
            "updatedAt": "2026-07-03T08:00:00Z",
        },
        102: {
            "type": "issue",
            "title": "Public API proposal for Blueprint schema imports",
            "caption": "Needs design acceptance before implementation",
            "body": "This proposes a new public API and schema contract for importing Blueprint resources across packages.",
            "labels": ["[Type] Enhancement", "[Status] Needs Proof"],
            "state": "OPEN",
            "author": {"login": "api-proposer"},
            "updatedAt": "2026-07-03T08:01:00Z",
        },
        103: {
            "type": "pr",
            "title": "Docs: correct Blueprint example typo",
            "caption": "Fast merge / normal review",
            "body": "Corrects one copied Blueprint example and updates the adjacent comment. No runtime behavior changes.",
            "labels": ["[Type] Documentation", "[Status] Needs Design"],
            "state": "OPEN",
            "author": {"login": "docs-contributor"},
            "updatedAt": "2026-07-03T08:02:00Z",
            "isDraft": False,
            "additions": 12,
            "deletions": 4,
            "changedFiles": 1,
            "headRefOid": "scenario-ready-review",
            "reviews": [],
        },
        104: {
            "type": "pr",
            "title": "Accepted proposal: rewrite storage sync implementation",
            "caption": "Accepted direction, but too large for one review",
            "body": "Implements the accepted storage sync proposal in one large branch touching runtime, docs, tests, and browser integration.",
            "labels": ["[Feature] Storage", "[Status] Needs Proof"],
            "state": "OPEN",
            "author": {"login": "storage-contributor"},
            "updatedAt": "2026-07-03T08:03:00Z",
            "isDraft": False,
            "additions": 2600,
            "deletions": 725,
            "changedFiles": 38,
            "headRefOid": "scenario-needs-slicing",
            "reviews": [],
        },
        105: {
            "type": "issue",
            "title": "Medium-sized enhancement needs a clear owner",
            "caption": "Plausible work, unclear reviewer/owner",
            "body": "Support project-specific server headers for advanced local previews. This is useful but needs someone to own the smallest slice through review.",
            "labels": ["[Type] Enhancement", "[Status] Ready for Review"],
            "state": "OPEN",
            "author": {"login": "preview-builder"},
            "updatedAt": "2026-07-03T08:04:00Z",
        },
        106: {
            "type": "issue",
            "title": "Old report still marked needs author's reply",
            "caption": "Close quickly after author timeout",
            "body": "The original report is missing details and has been waiting for the author's reply.",
            "labels": ["Needs Author's Reply", "[Status] Needs Proof"],
            "state": "OPEN",
            "author": {"login": "stale-reporter"},
            "updatedAt": "2026-06-10T08:05:00Z",
        },
        108: {
            "type": "issue",
            "title": "Recently asked reporter for reproduction details",
            "caption": "Waiting; no public action yet",
            "body": "The maintainer already asked for a Playground URL and browser details. This is waiting on contributor follow-up.",
            "labels": ["Needs Author's Reply", "[Status] Needs Proof"],
            "state": "OPEN",
            "author": {"login": "waiting-reporter"},
            "updatedAt": "2026-07-02T08:05:00Z",
        },
        109: {
            "type": "issue",
            "title": "Useful import workflow but no reviewer capacity",
            "caption": "Aligned, but no maintainer capacity",
            "body": "This workflow sounds useful and likely aligned, but there is no maintainer capacity or current owner to carry review right now.",
            "labels": ["[Type] Enhancement", "[Status] Needs Owner"],
            "state": "OPEN",
            "author": {"login": "workflow-builder"},
            "updatedAt": "2026-07-03T08:07:00Z",
        },
        107: {
            "type": "issue",
            "title": "Already closed duplicate",
            "caption": "No action needed",
            "body": "Closed as a duplicate of the current tracked issue.",
            "labels": ["duplicate"],
            "state": "CLOSED",
            "author": {"login": "duplicate-reporter"},
            "updatedAt": "2026-07-03T08:06:00Z",
        },
    }


def escape_text(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def summarize_source(source: dict) -> dict:
    labels = [label_name(l) for l in source.get("labels", [])]
    return {
        "title": source.get("title"),
        "url": source.get("url"),
        "state": source.get("state"),
        "author": (source.get("author") or {}).get("login") if isinstance(source.get("author"), dict) else source.get("author"),
        "labels": labels,
        "updatedAt": source.get("updatedAt"),
        "isDraft": source.get("isDraft"),
        "linesChanged": int(source.get("additions") or 0) + int(source.get("deletions") or 0),
        "changedFiles": source.get("changedFiles"),
    }


def compute_fingerprint(repo: str, item_type: str, number: int, source: dict) -> str:
    relevant = {
        "repo": repo,
        "type": item_type,
        "number": number,
        "title": source.get("title"),
        "body": source.get("body"),
        "state": source.get("state"),
        "updatedAt": source.get("updatedAt"),
        "labels": sorted(label_name(l) for l in source.get("labels", [])),
        "isDraft": source.get("isDraft"),
        "headRefOid": source.get("headRefOid"),
        "additions": source.get("additions"),
        "deletions": source.get("deletions"),
        "changedFiles": source.get("changedFiles"),
        "recentComments": tail_comments(source.get("comments", []), 6),
        "recentReviews": tail_reviews(source.get("reviews", []), 6),
    }
    blob = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def tail_comments(comments: list, n: int) -> list:
    compact = []
    for c in comments[-n:]:
        compact.append({
            "author": (c.get("author") or {}).get("login") if isinstance(c.get("author"), dict) else None,
            "createdAt": c.get("createdAt"),
            "body": (c.get("body") or "")[:2000],
        })
    return compact


def tail_reviews(reviews: list, n: int) -> list:
    compact = []
    for r in reviews[-n:]:
        compact.append({
            "author": (r.get("author") or {}).get("login") if isinstance(r.get("author"), dict) else None,
            "submittedAt": r.get("submittedAt"),
            "state": r.get("state"),
            "body": (r.get("body") or "")[:2000],
        })
    return compact


def generate_suggestion(repo: str, item_type: str, number: int, source: dict) -> dict:
    if PROVIDER == "codex":
        try:
            return codex_suggestion(repo, item_type, number, source)
        except Exception as exc:
            fallback = heuristic_suggestion(item_type, source)
            fallback["justification"] += f" Codex provider failed, so heuristic fallback was used: {type(exc).__name__}: {exc}"
            return fallback
    return heuristic_suggestion(item_type, source)


def codex_suggestion(repo: str, item_type: str, number: int, source: dict) -> dict:
    actions = load_actions()["actions"]
    prompt = {
        "instructions": "You are proposing one stewardship action for a GitHub issue or PR. Return only JSON matching the schema. Choose from the provided action ids and variants. Public comment should be ready to post, concise, respectful, and specific to this issue/PR rather than generic. Mention concrete details from the title/body, and include an @mention of the reporter/PR author when that would feel natural. Justification is private for maintainers.",
        "repo": repo,
        "type": item_type,
        "number": number,
        "source": compact_for_prompt(source),
        "availableActions": actions,
    }
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as out:
        out_path = out.name
    cmd = [
        "codex", "exec",
        "--skip-git-repo-check",
        "--sandbox", "read-only",
        "--output-schema", str(SCHEMA_PATH),
        "--output-last-message", out_path,
        "-",
    ]
    subprocess.run(cmd, input=json.dumps(prompt, ensure_ascii=False), text=True, check=True, cwd=str(ROOT), timeout=180)
    raw = Path(out_path).read_text()
    try:
        Path(out_path).unlink(missing_ok=True)
    except Exception:
        pass
    return normalize_suggestion(json.loads(raw), source)


def compact_for_prompt(source: dict) -> dict:
    data = summarize_source(source)
    data["body"] = (source.get("body") or "")[:6000]
    data["recentComments"] = tail_comments(source.get("comments", []), 5)
    data["recentReviews"] = tail_reviews(source.get("reviews", []), 5)
    return data


def heuristic_suggestion(item_type: str, source: dict) -> dict:
    title = source.get("title") or ""
    body = source.get("body") or ""
    labels = [label_name(l) for l in source.get("labels", [])]
    text = " ".join([title, body, " ".join(labels)]).lower()
    lines = int(source.get("additions") or 0) + int(source.get("deletions") or 0)
    is_draft = bool(source.get("isDraft"))

    def choose(action_id: str, variant_id: str, status: str, justification: str) -> dict:
        return personalize_suggestion(action_from_catalog(action_id, variant_id, status, justification), item_type, source)

    state = (source.get("state") or "").upper()
    if state and state not in {"OPEN"}:
        return choose("no-action", "already-closed", "No action", f"The item is already {state.lower()}, so no triage mutation is needed.")
    if any(term in text for term in ["needs author's reply", "waiting on author", "waiting on contributor", "needs reporter reply"]):
        days = age_in_days(source.get("updatedAt"))
        if days is not None and days >= 14:
            return choose("close-not-actionable", "stale-waiting", "Close after follow-up timeout", f"We asked for follow-up about {days} days ago; closing now keeps the queue honest while leaving a path back.")
        if days is None:
            return choose("waiting-author", "recently-asked", "Waiting on contributor", "We already asked for follow-up; no public maintainer action is needed yet.")
        remaining = max(0, 14 - days)
        return choose("waiting-author", "recently-asked", "Waiting on contributor", f"We asked for follow-up about {days} days ago; wait roughly {remaining} more day{'s' if remaining != 1 else ''} before considering closure.")
    if item_type == "pr" and (is_draft or lines > 3000):
        return choose("needs-slicing", "large-pr", "Split before review", f"This PR is {'draft and ' if is_draft else ''}{lines} changed lines, so full review would create a large maintainer obligation.")
    if item_type == "pr" and lines <= 250:
        return choose("ready-review", "small-scoped", "Small scoped PR", "The change is small enough for normal review once CI and tests are checked.")
    if "documentation" in text or "docs" in text:
        return choose("ready-review", "small-scoped", "Docs review", "Documentation work is usually safe to review directly if accurate and scoped.")
    if any(word in text for word in ["oops", "hiccup", "crash", "fails", "failed", "broken", "error", "bug", "missing dependency", "without declaring"]):
        has_detail = len(body.strip()) > 500 or "```" in body or "steps" in text or "reproduce" in text or "summary" in text or "problem" in text
        if not has_detail:
            return choose("needs-proof", "reproduction", "Needs reproduction", "The report looks actionable only after exact reproduction details or logs are available.")
        return choose("ready-review", "small-scoped", "Reproducible bug candidate", "The report includes enough detail to route to an owner or a narrow test/fix.")
    design_terms = ["design", "explore", "exploration", "strategy", "epic", "public api", "schema", "sync", "oauth", "sentry", "proposal", "storage model", "persistent storage"]
    if any(word in text for word in design_terms):
        variant = "public-api" if any(word in text for word in ["api", "schema", "sync", "oauth", "storage model", "persistent storage"]) else "product-direction"
        return choose("needs-design", variant, "Needs design first", "This appears to decide product, architecture, or public contract shape before implementation can be reviewed safely.")
    if item_type == "pr" and lines > 800:
        return choose("needs-slicing", "large-pr", "Split before review", f"The PR changes {lines} lines; asking for slices reduces review risk.")
    if any(term in text for term in ["no capacity", "reviewer capacity", "maintainer capacity", "no current owner", "no clear owner"]):
        return choose("no-capacity", "no-reviewer-capacity", "Useful, no capacity", "The work appears plausible, but the project should not imply maintainer review capacity without an owner.")
    return choose("needs-owner", "no-capacity", "Needs owner", "The item may be valid, but the next owner/reviewer is not obvious from the current context.")


def action_from_catalog(action_id: str, variant_id: str, status: str, justification: str) -> dict:
    catalog = load_actions()
    action = next(a for a in catalog["actions"] if a["id"] == action_id)
    variant = next(v for v in action["variants"] if v["id"] == variant_id)
    operations = []
    for op in action.get("operations", []):
        item = dict(op)
        if item.get("type") == "comment":
            item["body"] = variant["comment"]
        operations.append(item)
    return {
        "actionId": action_id,
        "variantId": variant_id,
        "status": status,
        "shortTitle": action["title"],
        "justification": justification,
        "publicComment": variant["comment"],
        "operations": operations,
    }


def personalize_suggestion(suggestion: dict, item_type: str, source: dict) -> dict:
    action_id = suggestion.get("actionId")
    if action_id in {"no-action", "waiting-author"}:
        return suggestion

    mention = author_mention(source)
    prefix = f"{mention} " if mention else ""
    title = source.get("title") or "this"
    subject = short_subject(title)
    lines = int(source.get("additions") or 0) + int(source.get("deletions") or 0)
    files = source.get("changedFiles")
    is_draft = bool(source.get("isDraft"))

    if action_id == "needs-proof":
        if item_type == "pr":
            comment = (
                f"{prefix}Thanks for working on “{subject}”. Before reviewing the change, "
                "we need the proof that anchors it: the exact Playground URL or Blueprint, browser/runtime version, "
                "steps to reproduce, expected behavior, actual behavior, and any console/error output."
            )
        else:
            comment = (
                f"{prefix}Thanks for reporting “{subject}”. To investigate this, "
                "we need a minimal reproduction: the exact Playground URL or Blueprint, browser/runtime version, "
                "steps to reproduce, expected behavior, actual behavior, and any console/error output."
            )
    elif action_id == "needs-design":
        comment = (
            f"{prefix}This looks directionally useful, but “{subject}” changes product/design shape or a public Playground contract. "
            "Please move it to a proposal or Discussion first with the user problem, non-goals, affected APIs, compatibility risks, "
            "alternatives considered, and the smallest first slice that would validate the idea."
        )
    elif action_id == "ready-review" and item_type == "pr":
        comment = (
            f"{prefix}This looks scoped to “{subject}” and reviewable as-is. Next step: maintainer review once CI is green "
            "and the stated tests or manual verification are confirmed."
        )
    elif action_id == "ready-review":
        comment = (
            f"{prefix}This report has enough detail to route a focused fix/test for “{subject}”. "
            "Next step: maintainer review of the reproduction and the smallest safe fix."
        )
    elif action_id == "needs-slicing":
        size = f"{lines} changed lines" if lines else "a broad change"
        if files:
            size += f" across {files} files"
        draft = "draft and " if is_draft else ""
        comment = (
            f"{prefix}The direction in “{subject}” may be useful, but this PR is {draft}{size}, which is too much to review safely in one pass. "
            "Please split it into a short implementation plan and the smallest first PR with standalone value; we can resume code review from that slice."
        )
    elif action_id == "no-capacity":
        comment = (
            f"{prefix}“{subject}” looks useful and aligned, but we do not currently have maintainer capacity to review or carry it. "
            "Leaving this marked as needing capacity; the path back is for someone to volunteer as owner, propose the smallest reviewable slice, and stay with it through follow-up."
        )
    elif action_id == "needs-owner":
        comment = (
            f"{prefix}“{subject}” looks like a plausible area of work, but it needs a clear owner before maintainers spend review time on it. "
            "A good next step would be for someone to volunteer for the smallest owned slice, list the files/API surfaces affected, and stay with it through review."
        )
    elif action_id == "close-not-actionable":
        if suggestion.get("variantId") == "stale-waiting":
            comment = (
                f"{prefix}Closing this for now because we asked for more information on “{subject}” and do not have enough detail to move it forward. "
                "If someone can still reproduce it in current Playground, please open a fresh issue with the exact URL or Blueprint, steps, expected behavior, actual behavior, and console/error output."
            )
        else:
            comment = (
                f"{prefix}Closing this for now because “{subject}” is not actionable with the current information. "
                "If someone can reproduce it in current Playground, please open a fresh issue with the exact URL or Blueprint, steps, expected behavior, actual behavior, and console/error output."
            )
    else:
        return suggestion

    suggestion["publicComment"] = comment
    for op in suggestion.get("operations", []):
        if op.get("type") == "comment":
            op["body"] = comment
    sync_status_label_operations(suggestion, source)
    return suggestion


def sync_status_label_operations(suggestion: dict, source: dict) -> None:
    catalog = load_actions()
    prefix = catalog.get("labels", {}).get("statusPrefix", "[Status]")
    action = next((a for a in catalog["actions"] if a["id"] == suggestion.get("actionId")), None)
    desired = action.get("statusLabel") if action else None
    existing = [label_name(label) for label in source.get("labels", [])]
    operations = [op for op in suggestion.get("operations", []) if op.get("type") not in {"addLabel", "removeLabel"}]

    if desired:
        for label in existing:
            if label.startswith(prefix) and label != desired:
                operations.append({"type": "removeLabel", "label": label})

    wants_status_label = any(op.get("type") == "addLabel" and op.get("label") == desired for op in suggestion.get("operations", []))
    if desired and wants_status_label and desired not in existing:
        operations.append({"type": "addLabel", "label": desired})

    suggestion["operations"] = operations


def age_in_days(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds() // 86400))


def author_mention(source: dict) -> str:
    author = source.get("author")
    login = author.get("login") if isinstance(author, dict) else author
    if not login:
        return ""
    login = str(login).strip().lstrip("@")
    if not login:
        return ""
    return f"@{login}"


def short_subject(title: str) -> str:
    compact = " ".join(str(title).split())
    if len(compact) <= 96:
        return compact
    return compact[:93].rstrip() + "…"


def normalize_suggestion(suggestion: dict, source: dict) -> dict:
    suggestion.setdefault("publicComment", "")
    suggestion.setdefault("operations", [])
    suggestion.setdefault("justification", "No justification provided.")
    suggestion.setdefault("shortTitle", suggestion.get("status") or suggestion.get("actionId") or "Suggested action")
    return suggestion


def load_actions() -> dict:
    return json.loads(ACTIONS_PATH.read_text())


def get_cached(repo: str, item_type: str, number: int, fingerprint: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT suggestion_json FROM suggestions WHERE repo=? AND item_type=? AND number=? AND fingerprint=?",
            (repo, item_type, number, fingerprint),
        ).fetchone()
    return json.loads(row[0]) if row else None


def put_cached(repo: str, item_type: str, number: int, fingerprint: str, source: dict, response: dict) -> None:
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT OR REPLACE INTO suggestions(repo,item_type,number,fingerprint,title,source_json,suggestion_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (repo, item_type, number, fingerprint, source.get("title") or "", json.dumps(source, ensure_ascii=False), json.dumps(response, ensure_ascii=False), now, now),
        )


def run_gh(cmd: list[str], input_text: str | None = None) -> dict:
    proc = subprocess.run(cmd, input=input_text, text=True, capture_output=True)
    return {
        "ok": proc.returncode == 0,
        "command": cmd,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "returncode": proc.returncode,
    }


def require(payload: dict, key: str):
    value = payload.get(key)
    if value in (None, ""):
        raise ValueError(f"missing required field: {key}")
    return value


def ensure_supported_repo(repo: str, allow_fixture: bool = False) -> str:
    if repo.lower() == ALLOWED_REPO.lower():
        return ALLOWED_REPO
    if allow_fixture and repo == FIXTURE_REPO:
        return repo
    raise ValueError(f"this companion is currently restricted to {ALLOWED_REPO}")


def normalize_type(value: str) -> str:
    value = value.lower()
    if value in ("pull", "pulls", "pr"):
        return "pr"
    if value in ("issue", "issues"):
        return "issue"
    raise ValueError(f"unsupported item type: {value}")


def label_name(label) -> str:
    return label.get("name") if isinstance(label, dict) else str(label)


if __name__ == "__main__":
    main()
