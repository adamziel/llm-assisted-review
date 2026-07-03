#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
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
            elif route == "/api/reproduce":
                self._json(handle_reproduce(payload))
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
        "evidence": evidence_rows(item_type, source, suggestion),
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


def handle_reproduce(payload: dict) -> dict:
    repo = ensure_supported_repo(require(payload, "repo"))
    item_type = normalize_type(require(payload, "type"))
    number = int(require(payload, "number"))
    source = fetch_item(repo, item_type, number, payload)
    workdir = Path(tempfile.mkdtemp(prefix=f"playground-repro-{number}-"))
    prompt_path = workdir / "prompt.md"
    readme_path = workdir / "README.md"
    script_path = workdir / "run-codex-repro.command"
    prompt_path.write_text(reproduction_prompt(repo, item_type, number, source), encoding="utf-8")
    readme_path.write_text(reproduction_readme(repo, item_type, number, prompt_path), encoding="utf-8")
    script_path.write_text(reproduction_script(workdir, prompt_path, repo, item_type, number), encoding="utf-8")
    script_path.chmod(0o755)
    launch = open_codex_reproduction(workdir, script_path)
    return {
        "ok": True,
        "message": launch.get("message") or f"Opened {launch['surface']} for {repo}#{number}.",
        "launch": launch,
        "workdir": str(workdir),
        "promptPath": str(prompt_path),
        "readmePath": str(readme_path),
        "scriptPath": str(script_path),
    }


def fetch_item(repo: str, item_type: str, number: int, page_payload: dict) -> dict:
    if repo == "local/scenarios":
        return scenario_source(item_type, number, page_payload)

    json_fields = ["number", "title", "body", "labels", "state", "author", "createdAt", "updatedAt", "comments", "url"]
    if item_type == "pr":
        json_fields += ["isDraft", "additions", "deletions", "changedFiles", "headRefOid", "reviews", "commits"]
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
            "createdAt": page_payload.get("createdAt") or "",
            "updatedAt": page_payload.get("updatedAt") or "",
            "comments": [],
            "url": page_payload.get("url") or f"https://github.com/{repo}/{item_type == 'pr' and 'pull' or 'issues'}/{number}",
        }
    item["_page"] = {k: page_payload.get(k) for k in ["title", "labels", "url"] if page_payload.get(k) is not None}
    enrich_source(repo, item_type, item)
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
            "labels": ["[Type] Bug"],
            "state": "OPEN",
            "author": {"login": "bug-reporter"},
            "updatedAt": "2026-07-03T08:00:00Z",
        },
        102: {
            "type": "issue",
            "title": "Public API proposal for Blueprint schema imports",
            "caption": "Needs design acceptance before implementation",
            "body": "This proposes a new public API and schema contract for importing Blueprint resources across packages.",
            "labels": ["[Type] Enhancement"],
            "state": "OPEN",
            "author": {"login": "api-proposer"},
            "updatedAt": "2026-07-03T08:01:00Z",
        },
        103: {
            "type": "pr",
            "title": "Docs: correct Blueprint example typo",
            "caption": "Fast merge",
            "body": "Corrects one copied Blueprint example and updates the adjacent comment. No runtime behavior changes.",
            "labels": ["[Type] Documentation"],
            "state": "OPEN",
            "author": {"login": "docs-contributor"},
            "updatedAt": "2026-07-03T08:02:00Z",
            "isDraft": False,
            "additions": 12,
            "deletions": 4,
            "changedFiles": 1,
            "headRefOid": "scenario-fast-merge",
            "reviews": [],
        },
        104: {
            "type": "pr",
            "title": "Accepted proposal: rewrite storage sync implementation",
            "caption": "Accepted direction, but too large for one review",
            "body": "Implements the accepted storage sync proposal in one large branch touching runtime, docs, tests, and browser integration.",
            "labels": ["[Feature] Storage"],
            "state": "OPEN",
            "author": {"login": "storage-contributor"},
            "updatedAt": "2026-07-03T08:03:00Z",
            "isDraft": False,
            "additions": 2600,
            "deletions": 725,
            "changedFiles": 38,
            "headRefOid": "scenario-needs-execution-plan",
            "reviews": [],
        },
        105: {
            "type": "pr",
            "title": "Add project-specific server headers for local previews",
            "caption": "Medium review with a clear review budget",
            "body": "Implements project-specific server headers for advanced local previews. This is in scope and concrete, but it should stay to one behavior change with tests, manual verification, and rollback notes.",
            "labels": ["[Type] Enhancement"],
            "state": "OPEN",
            "author": {"login": "preview-builder"},
            "updatedAt": "2026-07-03T08:04:00Z",
            "isDraft": False,
            "additions": 290,
            "deletions": 80,
            "changedFiles": 3,
            "headRefOid": "scenario-medium-review",
            "reviews": [],
        },
        106: {
            "type": "issue",
            "title": "Old report still marked needs author's reply",
            "caption": "Close quickly after author timeout",
            "body": "The original report is missing details and has been waiting for the author's reply.",
            "labels": ["Needs Author's Reply", "[Status] Awaits reporter response"],
            "state": "OPEN",
            "author": {"login": "stale-reporter"},
            "updatedAt": "2026-06-10T08:05:00Z",
        },
        108: {
            "type": "issue",
            "title": "Recently asked reporter for reproduction details",
            "caption": "Waiting; no public action yet",
            "body": "The maintainer already asked for a Playground URL and browser details. This is waiting on contributor follow-up.",
            "labels": ["Needs Author's Reply", "[Status] Awaits reporter response"],
            "state": "OPEN",
            "author": {"login": "waiting-reporter"},
            "updatedAt": "2026-07-02T08:05:00Z",
        },
        109: {
            "type": "issue",
            "title": "Useful import workflow but no reviewer capacity",
            "caption": "Aligned, but no maintainer capacity",
            "body": "This workflow sounds useful and likely aligned, but there is no maintainer capacity or current owner to carry review right now.",
            "labels": ["[Type] Enhancement"],
            "state": "OPEN",
            "author": {"login": "workflow-builder"},
            "updatedAt": "2026-07-03T08:07:00Z",
        },
        110: {
            "type": "issue",
            "title": "File browser covers the top navigation on mobile",
            "caption": "Likely duplicate of an older tracked issue",
            "body": "The file browser overlaps the top navigation when the viewport is narrow.",
            "labels": ["[Type] Bug"],
            "state": "OPEN",
            "author": {"login": "mobile-reporter"},
            "updatedAt": "2026-07-03T08:08:00Z",
            "possibleDuplicates": [
                {
                    "number": 3813,
                    "title": "[website] File browser covering the navigation",
                    "url": "https://github.com/WordPress/wordpress-playground/issues/3813",
                    "state": "OPEN",
                    "updatedAt": "2026-06-24T13:59:06Z",
                    "overlap": 4,
                }
            ],
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
        "createdAt": source.get("createdAt"),
        "updatedAt": source.get("updatedAt"),
        "isDraft": source.get("isDraft"),
        "linesChanged": int(source.get("additions") or 0) + int(source.get("deletions") or 0),
        "changedFiles": source.get("changedFiles"),
        "candidatePRs": [
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "url": pr.get("url"),
                "state": pr.get("state"),
                "linesChanged": pr.get("linesChanged"),
                "changedFiles": pr.get("changedFiles"),
                "reviewState": pr.get("reviewState"),
            }
            for pr in source.get("candidatePRs", [])
        ],
        "possibleDuplicates": [
            {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "url": issue.get("url"),
                "state": issue.get("state"),
                "overlap": issue.get("overlap"),
            }
            for issue in source.get("possibleDuplicates", [])
        ],
        "reviewState": source.get("reviewState"),
    }


def enrich_source(repo: str, item_type: str, source: dict) -> None:
    if item_type == "issue":
        source["candidatePRs"] = candidate_prs_for_issue(repo, source)
        source["possibleDuplicates"] = possible_duplicates_for_issue(repo, source)
    elif item_type == "pr":
        source["reviewState"] = review_state_for_pr(source)


def candidate_prs_for_issue(repo: str, source: dict) -> list[dict]:
    direct_numbers = extract_pr_numbers(source)
    candidates: dict[int, dict] = {}
    for number in direct_numbers:
        summary = fetch_pr_summary(repo, number)
        if summary:
            candidates[number] = summary

    if candidates:
        for number in search_related_pr_numbers(repo, source):
            if number not in candidates:
                summary = fetch_pr_summary(repo, number)
                if summary and candidate_recent_enough(source, summary) and candidate_overlap(source, summary) >= 2:
                    candidates[number] = summary

    return sorted(candidates.values(), key=lambda pr: (pr.get("linesChanged") or 0, pr.get("number") or 0))


def extract_pr_numbers(source: dict) -> list[int]:
    text = "\n".join([
        source.get("body") or "",
        "\n".join((comment.get("body") or "") for comment in source.get("comments", [])),
    ])
    numbers = {int(match) for match in re.findall(r"github\.com/WordPress/wordpress-playground/pull/(\d+)", text, re.I)}
    return sorted(numbers)


def search_related_pr_numbers(repo: str, source: dict) -> list[int]:
    query = related_pr_query(source.get("title") or "")
    if not query:
        return []
    try:
        raw = subprocess.check_output(
            ["gh", "search", "prs", query, "--repo", repo, "--state", "open", "--json", "number", "--limit", "8"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return [int(item["number"]) for item in json.loads(raw)]
    except Exception:
        return []


def possible_duplicates_for_issue(repo: str, source: dict) -> list[dict]:
    query = related_issue_query(source.get("title") or "")
    if not query:
        return []
    current_number = int(source.get("number") or 0)
    found: dict[int, dict] = {}
    for state in ["open", "closed"]:
        try:
            raw = subprocess.check_output(
                [
                    "gh", "search", "issues", query,
                    "--repo", repo,
                    "--state", state,
                    "--match", "title",
                    "--json", "number,title,url,state,createdAt,updatedAt,labels,commentsCount",
                    "--limit", "8",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            continue
        for issue in json.loads(raw):
            number = int(issue.get("number") or 0)
            if not number or number == current_number:
                continue
            overlap = candidate_overlap(source, issue)
            if overlap < 2:
                continue
            issue["overlap"] = overlap
            found[number] = issue
    return sorted(found.values(), key=lambda issue: (-int(issue.get("overlap") or 0), str(issue.get("state") or "").upper() != "OPEN", issue.get("number") or 0))[:3]


def related_issue_query(title: str) -> str:
    words = issue_keywords(title)
    if len(words) < 2:
        return ""
    return " ".join(words[:4])


def related_pr_query(title: str) -> str:
    words = issue_keywords(title)
    if len(words) < 2:
        return ""
    return " ".join(words[:2])


def candidate_overlap(source: dict, candidate: dict) -> int:
    issue_words = set(issue_keywords(source.get("title") or ""))
    pr_words = set(issue_keywords(candidate.get("title") or ""))
    return len(issue_words & pr_words)


def candidate_recent_enough(source: dict, candidate: dict) -> bool:
    created = timestamp_value(source.get("createdAt"))
    updated = timestamp_value(candidate.get("updatedAt"))
    if not created or not updated:
        return True
    return updated >= created - (7 * 24 * 60 * 60)


def issue_keywords(text: str) -> list[str]:
    cleaned = re.sub(r"\[[^\]]+\]", " ", text.lower())
    words = re.findall(r"[a-z0-9]+", cleaned)
    stop = {"the", "with", "from", "into", "when", "using", "fixed", "fix", "issue", "bug", "website"}
    return [word for word in words if len(word) >= 4 and word not in stop]


def fetch_pr_summary(repo: str, number: int) -> dict | None:
    fields = ["number", "title", "url", "state", "author", "createdAt", "updatedAt", "isDraft", "additions", "deletions", "changedFiles", "reviews", "comments", "commits"]
    try:
        raw = subprocess.check_output(
            ["gh", "pr", "view", str(number), "--repo", repo, "--json", ",".join(fields)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        pr = json.loads(raw)
    except Exception:
        return None
    pr["linesChanged"] = int(pr.get("additions") or 0) + int(pr.get("deletions") or 0)
    pr["authorLogin"] = (pr.get("author") or {}).get("login") if isinstance(pr.get("author"), dict) else pr.get("author")
    pr["reviewState"] = review_state_for_pr(pr)
    return pr


def review_state_for_pr(source: dict) -> dict:
    author = (source.get("author") or {}).get("login") if isinstance(source.get("author"), dict) else source.get("author")
    latest_feedback = latest_reviewer_feedback(source, author)
    latest_author = latest_author_activity(source, author)
    return {
        "latestReviewer": latest_feedback,
        "latestAuthorActivity": latest_author,
        "needsRereview": bool(latest_feedback and latest_author and timestamp_value(latest_author.get("at")) > timestamp_value(latest_feedback.get("at"))),
    }


def latest_reviewer_feedback(source: dict, author: str | None) -> dict | None:
    events = []
    for review in source.get("reviews") or []:
        reviewer = (review.get("author") or {}).get("login") if isinstance(review.get("author"), dict) else None
        if reviewer and reviewer != author and review.get("state") in {"CHANGES_REQUESTED", "COMMENTED"}:
            events.append({"kind": review.get("state"), "author": reviewer, "at": review.get("submittedAt"), "body": review.get("body") or ""})
    for comment in source.get("comments") or []:
        commenter = (comment.get("author") or {}).get("login") if isinstance(comment.get("author"), dict) else None
        if commenter and commenter != author:
            events.append({"kind": "COMMENTED", "author": commenter, "at": comment.get("createdAt"), "body": comment.get("body") or ""})
    return latest_event(events)


def latest_author_activity(source: dict, author: str | None) -> dict | None:
    events = []
    for comment in source.get("comments") or []:
        commenter = (comment.get("author") or {}).get("login") if isinstance(comment.get("author"), dict) else None
        if commenter and commenter == author:
            events.append({"kind": "AUTHOR_COMMENT", "author": commenter, "at": comment.get("createdAt"), "body": comment.get("body") or ""})
    for commit in source.get("commits") or []:
        at = commit.get("committedDate") or commit.get("authoredDate")
        events.append({"kind": "COMMIT", "author": author, "at": at, "body": commit.get("messageHeadline") or ""})
    return latest_event(events)


def latest_event(events: list[dict]) -> dict | None:
    events = [event for event in events if event.get("at")]
    if not events:
        return None
    return max(events, key=lambda event: timestamp_value(event.get("at")))


def timestamp_value(value: str | None) -> float:
    if not value:
        return 0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def evidence_rows(item_type: str, source: dict, suggestion: dict) -> list[dict]:
    if item_type == "issue":
        return issue_evidence_rows(source, suggestion)
    if item_type == "pr":
        return pr_evidence_rows(source, suggestion)
    return []


def issue_evidence_rows(source: dict, suggestion: dict) -> list[dict]:
    rows = []
    candidates = source.get("candidatePRs") or []
    duplicates = source.get("possibleDuplicates") or []
    if not candidates:
        rows.append({"label": "Change size", "value": "issue only; no files or lines changed"})
        if duplicates:
            rows.append({"label": "Possible duplicate", "value": duplicate_summary(duplicates)})
        rows.append({"label": "Suggested action", "value": action_summary(suggestion, candidates)})
        return rows
    if candidates:
        rows.append({"label": "Issue patch", "value": "none attached; candidate PR sizes below"})
        rows.append({"label": "Issue has reproduction", "value": "yes" if has_reproduction(source) else "not clear"})
        linked = ", ".join(f"#{pr['number']}" for pr in candidates)
        rows.append({"label": "Candidate PRs", "value": linked})
        smallest = candidates[0]
        rows.append({"label": "Smallest candidate", "value": f"#{smallest['number']}, {change_size_text(smallest)}"})
        broader = [pr for pr in candidates[1:] if (pr.get("linesChanged") or 0) > (smallest.get("linesChanged") or 0)]
        if broader:
            rows.append({"label": "Broader candidate", "value": ", ".join(f"#{pr['number']}, {change_size_text(pr)}" for pr in broader[:2])})
        review = candidate_review_summary(candidates)
        if review:
            rows.append({"label": "Review state", "value": review})
        rows.append({"label": "Suggested action", "value": action_summary(suggestion, candidates)})
    return rows


def duplicate_summary(duplicates: list[dict]) -> str:
    parts = []
    for issue in duplicates[:3]:
        state = str(issue.get("state") or "").lower()
        suffix = f" {state}" if state else ""
        parts.append(f"#{issue.get('number')}{suffix} — {issue.get('title')}")
    return "; ".join(parts)


def pr_evidence_rows(source: dict, suggestion: dict) -> list[dict]:
    return [{"label": "Change size", "value": change_size_text(source)}]


def change_size_text(source: dict) -> str:
    additions = int(source.get("additions") or 0)
    deletions = int(source.get("deletions") or 0)
    lines = int(source.get("linesChanged") or additions + deletions)
    files = source.get("changedFiles")
    if additions or deletions:
        line_text = f"+{additions} −{deletions} lines"
    else:
        line_text = f"{lines} changed lines"
    if files:
        try:
            file_count = int(files)
        except (TypeError, ValueError):
            file_count = 0
        noun = "file" if file_count == 1 else "files"
        return f"{line_text}, {files} {noun}"
    return line_text


def candidate_review_summary(candidates: list[dict]) -> str:
    parts = []
    for pr in candidates[:2]:
        review = pr.get("reviewState") or {}
        feedback = review.get("latestReviewer")
        if not feedback or feedback.get("kind") != "CHANGES_REQUESTED":
            continue
        if review.get("needsRereview"):
            parts.append(f"#{pr['number']} has author follow-up after {feedback.get('kind', '').lower().replace('_', ' ')}")
        else:
            parts.append(f"#{pr['number']} latest feedback is {feedback.get('kind', '').lower().replace('_', ' ')} from @{feedback.get('author')}")
    return "; ".join(parts)


def action_summary(suggestion: dict, candidates: list[dict]) -> str:
    action_id = suggestion.get("actionId")
    if action_id == "fast-merge":
        return "Small, low-risk, and ready for the fast review lane."
    if action_id == "medium-review":
        return "In scope, but needs a focused review budget and verification path."
    if action_id == "needs-execution-plan":
        return "Direction may be accepted, but implementation review needs slices, owners, tests, and rollback boundaries."
    if action_id == "duplicate-of":
        return "Close this issue while pointing to the canonical issue."
    if action_id == "narrow-fast-path" and len(candidates) >= 2:
        return f"Use #{candidates[0]['number']} as the fast path if it fully resolves the report; otherwise evaluate or split broader follow-ups."
    if action_id == "competing-prs":
        return "Choose one implementation path before asking for more review."
    if action_id == "has-candidate-pr":
        return "Route review through the existing candidate PR instead of asking the reporter for more process."
    if action_id == "needs-rereview":
        return "Ask the previous reviewer or area maintainer to re-test the updated PR."
    if action_id == "needs-owner":
        return "No patch is attached; someone needs to own reproduction and the smallest fix/test path."
    return suggestion.get("shortTitle") or suggestion.get("status") or "Suggested action"


def likely_duplicate_issue(source: dict) -> bool:
    duplicates = source.get("possibleDuplicates") or []
    if not duplicates:
        return False
    source_title = normalized_issue_title(source.get("title") or "")
    duplicate = duplicates[0]
    duplicate_title = normalized_issue_title(duplicate.get("title") or "")
    if source_title and source_title == duplicate_title:
        return True
    keywords = issue_keywords(source.get("title") or "")
    overlap = int(duplicate.get("overlap") or 0)
    return bool(keywords) and overlap >= min(4, len(keywords))


def normalized_issue_title(title: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]", " ", title.lower())
    return " ".join(re.findall(r"[a-z0-9]+", cleaned))


def event_summary(event: dict) -> str:
    author = f"@{event.get('author')}" if event.get("author") else "unknown"
    kind = str(event.get("kind") or "activity").lower().replace("_", " ")
    return f"{kind} by {author}"


def has_reproduction(source: dict) -> bool:
    text = " ".join([source.get("body") or "", source.get("title") or ""]).lower()
    return "steps to reproduce" in text or "user-attachments" in text or "<img" in text or "screenshot" in text or "reproduce" in text


def compute_fingerprint(repo: str, item_type: str, number: int, source: dict) -> str:
    relevant = {
        "triageVersion": 5,
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
        "candidatePRs": [
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "linesChanged": pr.get("linesChanged"),
                "reviewState": pr.get("reviewState"),
            }
            for pr in source.get("candidatePRs", [])
        ],
        "possibleDuplicates": [
            {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "state": issue.get("state"),
                "updatedAt": issue.get("updatedAt"),
                "overlap": issue.get("overlap"),
            }
            for issue in source.get("possibleDuplicates", [])
        ],
        "reviewState": source.get("reviewState"),
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


def reproduction_prompt(repo: str, item_type: str, number: int, source: dict) -> str:
    kind = "pull request" if item_type == "pr" else "issue"
    return f"""You are helping a WordPress Playground maintainer reproduce GitHub {kind} {repo}#{number}.

Goal:
- Determine whether the reported behavior or proposed change is reproducible/verifiable from the available public information.
- Work only locally in this temporary directory.
- Do not post GitHub comments, add/remove labels, close issues, push branches, or open pull requests.
- When the issue is about browser behavior, UI, Blueprints, WordPress loading, or a user-visible Playground workflow, try reproducing first on https://playground.wordpress.net if that is the smallest useful path. Use a local checkout only when the live site cannot exercise the reported path, when the candidate PR must be tested, or when source-level inspection is necessary.
- If you need source code, clone {repo} into this directory or use the local gh CLI to inspect the {kind}.
- Keep the session focused on reproduction/verification. Do not drift into a full implementation unless a tiny test-only proof is necessary.

When finished, report:
1. Reproduced / not reproduced / inconclusive.
2. Exact commands and environment used.
3. Observed output or error.
4. What maintainer action this evidence supports next.

GitHub context:
{json.dumps(compact_for_prompt(source), ensure_ascii=False, indent=2)}
"""


def reproduction_readme(repo: str, item_type: str, number: int, prompt_path: Path) -> str:
    kind = "PR" if item_type == "pr" else "issue"
    return f"""# Codex reproduction workspace

GitHub target: `{repo}` {kind} #{number}

Start with `prompt.md`. If this opened in Codex Desktop, paste the prompt into a new local task from this workspace.

For browser/UI/Blueprint reports, prefer `https://playground.wordpress.net` when it is enough to reproduce or disprove the report. Use a local checkout when the live site cannot exercise the path or a candidate PR needs to be tested.

Prompt path:

```text
{prompt_path}
```
"""


def reproduction_script(workdir: Path, prompt_path: Path, repo: str, item_type: str, number: int) -> str:
    codex = shutil.which("codex") or "codex"
    kind = "PR" if item_type == "pr" else "issue"
    return f"""#!/bin/zsh
set -e
cd {shlex.quote(str(workdir))}
echo "Starting Codex reproduction session for {repo} {kind} #{number}"
echo "Working directory: {workdir}"
echo
{shlex.quote(codex)} --cd {shlex.quote(str(workdir))} --sandbox workspace-write --ask-for-approval on-request --no-alt-screen "$(cat {shlex.quote(str(prompt_path))})"
echo
echo "Codex session ended. Files remain in: {workdir}"
echo "Press Return to close this terminal."
read -r _
"""


def open_codex_reproduction(workdir: Path, script_path: Path) -> dict:
    desktop_opened = open_codex_desktop(workdir)
    open_terminal(script_path)
    if desktop_opened:
        return {
            "surface": "Codex Desktop + Terminal Codex session",
            "desktopOpened": True,
            "fallback": False,
            "message": "Opened Codex Desktop with the workspace and started a Terminal Codex session with the reproduction prompt.",
        }
    return {
        "surface": "Terminal Codex session",
        "desktopOpened": False,
        "fallback": True,
        "message": "Opened a Terminal Codex session with the reproduction prompt.",
    }


def open_codex_desktop(workdir: Path) -> bool:
    if codex_desktop_available():
        codex = shutil.which("codex")
        if codex:
            try:
                result = subprocess.run([codex, "app", str(workdir)], text=True, capture_output=True, timeout=10)
                if result.returncode == 0:
                    return True
            except Exception:
                pass
        try:
            result = subprocess.run(["open", "-a", "Codex", str(workdir)], text=True, capture_output=True, timeout=10)
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False


def codex_desktop_available() -> bool:
    if sys.platform != "darwin":
        return False
    return any(path.exists() for path in [
        Path("/Applications/Codex.app"),
        Path.home() / "Applications" / "Codex.app",
    ])


def open_terminal(script_path: Path) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Opening Codex reproduction sessions is currently implemented for macOS.")
    subprocess.Popen(["open", "-a", "Terminal", str(script_path)])


def heuristic_suggestion(item_type: str, source: dict) -> dict:
    title = source.get("title") or ""
    body = source.get("body") or ""
    labels = [label_name(l) for l in source.get("labels", [])]
    text = " ".join([title, body, " ".join(labels)]).lower()
    lines = int(source.get("additions") or 0) + int(source.get("deletions") or 0)
    files = int(source.get("changedFiles") or 0)
    is_draft = bool(source.get("isDraft"))

    design_terms = ["design", "explore", "exploration", "strategy", "epic", "public api", "schema", "sync", "oauth", "sentry", "storage model", "persistent storage"]
    accepted_terms = ["accepted", "approved", "proposal accepted", "direction accepted", "agreed direction", "rfc accepted", "accepted proposal"]
    bug_terms = ["oops", "hiccup", "crash", "fails", "failed", "broken", "error", "bug", "missing dependency", "without declaring"]
    capacity_terms = ["no capacity", "reviewer capacity", "maintainer capacity", "no current owner", "no clear owner"]
    wait_terms = ["needs author's reply", "awaits reporter response", "waiting on author", "waiting on contributor", "needs reporter reply"]

    def choose(action_id: str, variant_id: str, status: str, justification: str) -> dict:
        return personalize_suggestion(action_from_catalog(action_id, variant_id, status, justification), item_type, source)

    state = (source.get("state") or "").upper()
    if state and state not in {"OPEN"}:
        return choose("no-action", "already-closed", "No action", f"The item is already {state.lower()}, so no triage mutation is needed.")

    if item_type == "issue" and source.get("candidatePRs"):
        candidates = source["candidatePRs"]
        smallest = candidates[0]
        broader = [pr for pr in candidates[1:] if (pr.get("linesChanged") or 0) >= max(80, (smallest.get("linesChanged") or 0) * 4)]
        if broader:
            return choose("narrow-fast-path", "small-vs-broad", "Use narrow candidate first", f"The issue has reproduction and candidate PRs; #{smallest['number']} is the smallest path while broader PRs change more surface area.")
        if len(candidates) > 1:
            return choose("competing-prs", "choose-path", "Choose implementation path", "Multiple candidate PRs address the report, so maintainers should choose a path before asking for more review.")
        return choose("has-candidate-pr", "issue-has-pr", "Has candidate PR", f"The issue already has candidate PR #{smallest['number']}; asking the reporter for an owner would add process instead of moving review forward.")

    if item_type == "issue" and likely_duplicate_issue(source):
        duplicate = source["possibleDuplicates"][0]
        return choose("duplicate-of", "duplicate-issue", "Duplicate of", f"Possible duplicate #{duplicate['number']} has a very similar title, so close only if that issue is the canonical place to track this.")

    if item_type == "pr" and (source.get("reviewState") or {}).get("needsRereview"):
        return choose("needs-rereview", "author-followed-up", "Needs re-review", "The PR author responded after reviewer feedback, so the next action is re-test/re-review rather than fresh triage.")

    if any(term in text for term in wait_terms):
        days = age_in_days(source.get("updatedAt"))
        if days is not None and days >= 14:
            return choose("close-not-actionable", "stale-waiting", "Close after follow-up timeout", f"We asked for follow-up about {days} days ago; closing now keeps the queue honest while leaving a path back.")
        if days is None:
            return choose("waiting-author", "recently-asked", "Waiting on contributor", "We already asked for follow-up; no public maintainer action is needed yet.")
        remaining = max(0, 14 - days)
        return choose("waiting-author", "recently-asked", "Waiting on contributor", f"We asked for follow-up about {days} days ago; wait roughly {remaining} more day{'s' if remaining != 1 else ''} before considering closure.")

    has_design_risk = any(word in text for word in design_terms)
    has_accepted_direction = any(term in text for term in accepted_terms)

    if item_type == "pr" and has_accepted_direction and (is_draft or lines > 250 or files > 3):
        return choose("needs-execution-plan", "large-accepted-work", "Needs execution plan", "The direction appears accepted, but implementation review still needs slices, owners, tests, and rollback boundaries.")

    if item_type == "pr" and has_design_risk and not has_accepted_direction:
        variant = "public-api" if any(word in text for word in ["api", "schema", "sync", "oauth", "storage model", "persistent storage"]) else "product-direction"
        return choose("needs-design", variant, "Needs design first", "This appears to decide product, architecture, or public contract shape before implementation can be reviewed safely.")

    if item_type == "pr" and (lines > 800 or files > 10):
        return choose("needs-execution-plan", "large-accepted-work", "Needs execution plan", f"The PR changes {lines} lines across {files or 'multiple'} files; implementation review needs a slice plan before maintainers take on that obligation.")

    if item_type == "pr" and lines <= 250 and not is_draft:
        return choose("fast-merge", "small-tested", "Fast merge", "The change is small enough for the fast lane once CI and the stated verification are checked.")

    if item_type == "pr" and (lines <= 800 or files <= 6):
        return choose("medium-review", "make-reviewable", "Medium review", "The change is concrete and reviewable, but larger than fast-track; it needs an explicit review budget and verification path.")

    if item_type == "pr" and ("documentation" in text or "docs" in text):
        return choose("medium-review", "make-reviewable", "Medium review", "Documentation work is in scope, but this should still be kept to a reviewable slice with clear verification.")

    if any(word in text for word in bug_terms):
        has_detail = len(body.strip()) > 500 or "```" in body or "steps" in text or "reproduce" in text or "summary" in text or "problem" in text
        if not has_detail:
            return choose("needs-proof", "reproduction", "Needs reproduction", "The report looks actionable only after exact reproduction details or logs are available.")
        if item_type == "issue":
            return choose("needs-owner", "no-capacity", "Needs owner", "The report has enough signal to investigate, but there is no patch attached; the next step is an owner for reproduction and the smallest fix/test.")
        return choose("medium-review", "make-reviewable", "Medium review", "The change includes enough detail for a focused review path, but it still needs scoped verification.")

    if has_design_risk:
        variant = "public-api" if any(word in text for word in ["api", "schema", "sync", "oauth", "storage model", "persistent storage"]) else "product-direction"
        return choose("needs-design", variant, "Needs design first", "This appears to decide product, architecture, or public contract shape before implementation can be reviewed safely.")

    if any(term in text for term in capacity_terms):
        return choose("no-capacity", "no-reviewer-capacity", "Useful, no capacity", "The work appears plausible, but the project should not imply maintainer review capacity without an owner.")

    if any(term in text for term in ["owner", "sponsor", "maintainer"]):
        return choose("needs-owner", "no-capacity", "Needs owner", "The item may be valid, but the next owner/reviewer is not obvious from the current context.")

    if item_type == "issue":
        return choose("needs-owner", "no-capacity", "Needs owner", "There is no patch attached, so the next step is an owner for reproduction, scope, and the smallest fix/test path.")

    return choose("medium-review", "make-reviewable", "Medium review", "The item appears concrete enough for the normal stewardship queue, but it needs reviewable scope and verification before maintainers spend review time.")

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
        filter_known_label_operations(suggestion)
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
    elif action_id == "fast-merge":
        comment = ""
    elif action_id == "medium-review":
        if item_type == "pr":
            size = f"{lines} changed lines" if lines else "a medium-sized change"
            if files:
                size += f" across {files} files"
            draft = "draft and " if is_draft else ""
            comment = (
                f"{prefix}This looks in scope for “{subject}”, but it is {draft}{size}, so it is not a fast-track review. "
                "Please keep it to one behavior change, add tests or manual verification, include screenshots or a Playground link if user-visible, "
                "and note the rollback path. Then it can go through focused review."
            )
        else:
            comment = (
                f"{prefix}“{subject}” looks in scope, but it needs a reviewable shape before maintainers spend review time. "
                "Please narrow it to one behavior change, describe the smallest useful slice, add proof or manual verification steps, "
                "and include screenshots or a Playground link if user-visible."
            )
    elif action_id == "has-candidate-pr":
        candidate = (source.get("candidatePRs") or [{}])[0]
        pr_number = candidate.get("number")
        pr_text = f"#{pr_number}" if pr_number else "the linked PR"
        comment = (
            f"{prefix}Thanks for the clear report. This already has a candidate fix in {pr_text}, so the next step is to review and test that PR against the reproduction here."
        )
    elif action_id == "needs-rereview":
        comment = (
            f"{prefix}Thanks for following up on “{subject}”. The next step is a re-test/re-review from the previous reviewer or area maintainer to confirm whether the latest update resolves the reported issue."
        )
    elif action_id == "competing-prs":
        candidates = source.get("candidatePRs") or []
        linked = " and ".join(f"#{pr['number']}" for pr in candidates[:2])
        comment = (
            f"{prefix}Thanks for the clear reproduction. There are multiple candidate fixes ({linked}), so the next step is to choose one implementation path before asking for more review. "
            "Let's use the smallest change that fully resolves the reported overlap and split broader layout improvements into follow-ups if needed."
        )
    elif action_id == "narrow-fast-path":
        candidates = source.get("candidatePRs") or []
        smallest = candidates[0] if candidates else {}
        broader = candidates[1] if len(candidates) > 1 else {}
        small_text = f"#{smallest.get('number')}" if smallest.get("number") else "the narrow PR"
        broad_text = f"#{broader.get('number')}" if broader.get("number") else "the broader PR"
        comment = (
            f"{prefix}Thanks for the clear reproduction. There are two candidate fixes: {small_text} is the narrow CSS fix, and {broad_text} is a broader layout pass. "
            f"I’d treat {small_text} as the fast path if it fully resolves the overlap. If it still fails, we can evaluate {broad_text} or split its broader layout changes into follow-ups."
        )
    elif action_id == "needs-execution-plan":
        size = f"{lines} changed lines" if lines else "a broad change"
        if files:
            size += f" across {files} files"
        draft = "draft and " if is_draft else ""
        comment = (
            f"{prefix}The direction in “{subject}” may be accepted or useful, but this PR is {draft}{size}, which is too much to review safely in one pass. "
            "Please turn it into an implementation plan with the smallest mergeable slice, review owners, compatibility risks, tests, rollback plan, and follow-up sequence. "
            "We can resume implementation review from the first slice."
        )
    elif action_id == "no-capacity":
        comment = (
            f"{prefix}“{subject}” looks useful and aligned, but we do not currently have maintainer capacity to review or carry it. "
            "The path back is for someone to volunteer as owner, propose the smallest reviewable slice, and stay with it through follow-up."
        )
    elif action_id == "needs-owner":
        if item_type == "issue":
            comment = (
                f"{prefix}Thanks for reporting “{subject}”. This has enough signal to investigate, but there is no patch to review yet. "
                "The next useful step is for someone to own reproduction and propose the smallest fix or test that verifies the problem."
            )
        else:
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
    elif action_id == "duplicate-of":
        duplicate = (source.get("possibleDuplicates") or [{}])[0]
        number = duplicate.get("number")
        duplicate_subject = short_subject(duplicate.get("title") or "the existing issue")
        if number:
            comment = (
                f"{prefix}Closing this as a duplicate of #{number}, where “{duplicate_subject}” is already tracked. "
                "Please continue the discussion there so context stays in one place."
            )
        else:
            comment = (
                f"{prefix}Closing this as a duplicate of #____, where the same issue is already tracked. "
                "Please continue the discussion there so context stays in one place."
            )
    else:
        filter_known_label_operations(suggestion)
        return suggestion

    suggestion["publicComment"] = comment
    for op in suggestion.get("operations", []):
        if op.get("type") == "comment":
            op["body"] = comment
    add_candidate_issue_labels(suggestion, source, item_type)
    sync_status_label_operations(suggestion, source)
    filter_known_label_operations(suggestion)
    return suggestion

def add_candidate_issue_labels(suggestion: dict, source: dict, item_type: str) -> None:
    if item_type != "issue" or suggestion.get("actionId") not in {"has-candidate-pr", "competing-prs", "narrow-fast-path"}:
        return
    existing = [label_name(label) for label in source.get("labels", [])]
    text = " ".join([source.get("title") or "", source.get("body") or "", " ".join(existing)]).lower()
    operations = suggestion.setdefault("operations", [])

    if "[Type] Enhancement" in existing and any(term in text for term in ["covering", "broken", "bug", "fails", "error", "regression"]):
        operations.append({"type": "removeLabel", "label": "[Type] Enhancement"})
    if any(term in text for term in ["covering", "broken", "bug", "fails", "error", "regression"]) and "[Type] Bug" not in existing:
        operations.append({"type": "addLabel", "label": "[Type] Bug"})
    if "website" in text:
        if "[Aspect] Website" not in existing:
            operations.append({"type": "addLabel", "label": "[Aspect] Website"})
        if "[Package][@wp-playground] Website" not in existing:
            operations.append({"type": "addLabel", "label": "[Package][@wp-playground] Website"})


def sync_status_label_operations(suggestion: dict, source: dict) -> None:
    catalog = load_actions()
    prefix = catalog.get("labels", {}).get("statusPrefix", "[Status]")
    action = next((a for a in catalog["actions"] if a["id"] == suggestion.get("actionId")), None)
    desired = action.get("statusLabel") if action else None
    existing = [label_name(label) for label in source.get("labels", [])]
    operations = [
        op for op in suggestion.get("operations", [])
        if op.get("type") not in {"addLabel", "removeLabel"} or not str(op.get("label", "")).startswith(prefix)
    ]

    if desired:
        for label in existing:
            if label.startswith(prefix) and label != desired:
                operations.append({"type": "removeLabel", "label": label})

    wants_status_label = any(op.get("type") == "addLabel" and op.get("label") == desired for op in suggestion.get("operations", []))
    if desired and wants_status_label and desired not in existing:
        operations.append({"type": "addLabel", "label": desired})

    suggestion["operations"] = operations


def filter_known_label_operations(suggestion: dict) -> None:
    known = set(load_actions().get("labels", {}).get("knownMutationLabels", []))
    if not known:
        return
    suggestion["operations"] = [
        op for op in suggestion.get("operations", [])
        if op.get("type") not in {"addLabel", "removeLabel"} or op.get("label") in known
    ]


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
    filter_known_label_operations(suggestion)
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
