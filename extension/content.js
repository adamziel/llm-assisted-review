(() => {
  const API = 'http://127.0.0.1:8765';
  const seenRows = new WeakSet();
  const suggestionCache = new Map();

  function boot() {
    decoratePage();
    const observer = new MutationObserver(() => schedule(decoratePage));
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('popstate', () => setTimeout(decoratePage, 250));
  }

  let scheduled = false;
  function schedule(fn) {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => { scheduled = false; fn(); }, 350);
  }

  function decoratePage() {
    const route = parseRoute(location.href);
    if (!route) return;
    if (route.number) {
      renderDetailPanel(route);
      return;
    }
    decorateList(route);
  }

  function parseRoute(url) {
    const parsed = new URL(url);
    const parts = parsed.pathname.split('/').filter(Boolean);
    if (parts.length < 3) return null;
    const [owner, name, area, number] = parts;
    if (!owner || !name) return null;
    if (!['issues', 'pull', 'pulls'].includes(area)) return null;
    return {
      repo: `${owner}/${name}`,
      type: area === 'pull' || area === 'pulls' ? 'pr' : 'issue',
      number: number && /^\d+$/.test(number) ? Number(number) : null,
      area,
    };
  }

  function decorateList(route) {
    const main = document.querySelector('main') || document.body;
    const titleLinks = Array.from(main.querySelectorAll('[data-testid="issue-pr-title-link"]'));
    const fallbackLinks = titleLinks.length ? [] : Array.from(main.querySelectorAll('a[href*="/issues/"], a[href*="/pull/"]'));
    const links = titleLinks.length ? titleLinks : fallbackLinks;
    let decorated = 0;

    for (const link of links) {
      const item = parseRoute(link.href);
      if (!item || item.repo !== route.repo || !item.number) continue;
      const row = closestRow(link);
      if (!row) continue;
      if (seenRows.has(row)) {
        const existing = row.querySelector('.codex-triage-list-button');
        if (existing) placeListButton(row, existing, link);
        continue;
      }
      seenRows.add(row);

      const title = cleanup(link.textContent || '');
      if (!title || title.startsWith('#')) continue;

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'codex-triage-list-button';
      button.dataset.state = 'loading';
      button.textContent = 'Triage…';
      button.title = 'Load local triage suggestion';
      button.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        showFloatingPanel({ ...item, title }, button);
      });
      placeListButton(row, button, link);

      loadSuggestion({ ...item, title }).then((result) => {
        const p = presentationFor(result.suggestion);
        button.dataset.state = 'ready';
        button.dataset.action = result.suggestion.actionId || '';
        button.textContent = p.list;
        button.setAttribute('aria-label', `${p.long}: ${result.suggestion.justification || ''}`);
        button.title = `${p.long}
${result.suggestion.justification || ''}`;
      }).catch((error) => {
        button.dataset.state = 'error';
        button.textContent = 'Start server';
        button.setAttribute('aria-label', 'Start local companion to load triage suggestions');
        button.title = `Local companion unavailable. From the project folder, run: cd server && python3 server.py.

${error.message}`;
      });

      decorated++;
      if (decorated >= 30) break;
    }
  }

  function closestRow(link) {
    return link.closest('[role="listitem"], [data-testid="list-row"], .Box-row, div.js-issue-row, div[id^="issue_"]') || link.parentElement;
  }

  function placeListButton(row, button, link) {
    row.classList.add('codex-triage-list-row');
    const assignees = row.querySelector('[data-testid="list-row-assignees"]');
    if (assignees) {
      assignees.classList.add('codex-triage-list-button-slot');
      assignees.parentElement?.classList.add('codex-triage-list-metadata');
      if (button.parentElement !== assignees) assignees.appendChild(button);
      return;
    }

    const comments = row.querySelector('[data-testid="list-row-comments"]');
    if (comments) {
      comments.classList.add('codex-triage-list-button-slot');
      comments.parentElement?.classList.add('codex-triage-list-metadata');
      if (button.parentElement !== comments) comments.appendChild(button);
      return;
    }

    const metadata = row.querySelector('[class*="MetadataContainer"]')
      || row.querySelector('[data-testid="list-row-linked-pull-requests"]')?.parentElement;
    if (metadata) {
      metadata.classList.add('codex-triage-list-metadata');
      metadata.appendChild(button);
      return;
    }

    const titleContainer = row.querySelector('[data-listview-item-title-container="true"]')
      || link.closest('h3')
      || link.parentElement;
    titleContainer.appendChild(button);
  }

  function renderDetailPanel(route) {
    if (document.querySelector('#codex-triage-detail-panel')) return;
    const mount = detailPanelMount();
    if (!mount) return;
    const panel = buildPanel(route, { detail: true });
    panel.id = 'codex-triage-detail-panel';
    panel.dataset.surface = 'detail';
    mount.insertAdjacentElement('afterend', panel);
    refreshPanel(panel, route);
  }

  function detailPanelMount() {
    const legacyHeader = document.querySelector('#partial-discussion-header') || document.querySelector('[data-testid="issue-header"]');
    if (legacyHeader) return legacyHeader;
    const title = document.querySelector('main h1');
    if (title) return title.closest('header') || title.closest('[class*="PageHeader"]') || title.parentElement;
    return document.querySelector('main > *');
  }

  function showFloatingPanel(item, anchor) {
    document.querySelectorAll('.codex-triage-popover').forEach((el) => el.remove());
    const panel = buildPanel(item, { detail: false });
    panel.classList.add('codex-triage-popover');
    panel.dataset.surface = 'popover';
    document.body.appendChild(panel);
    positionFloatingPanel(panel, anchor);
    refreshPanel(panel, item).then(() => positionFloatingPanel(panel, anchor));
  }

  function positionFloatingPanel(panel, anchor) {
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(560, window.innerWidth - 36);
    const measuredHeight = Math.min(panel.offsetHeight || 360, window.innerHeight - 36);
    const left = Math.max(18, Math.min(window.innerWidth - width - 18, rect.right - width));
    const top = Math.max(18, Math.min(window.innerHeight - measuredHeight - 18, rect.bottom + 8));
    panel.style.width = `${width}px`;
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
  }

  function buildPanel(item, opts) {
    const uid = `codex-triage-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const panel = document.createElement('section');
    panel.className = 'codex-triage-panel';
    panel.innerHTML = `
      <button type="button" data-role="close" class="codex-triage-icon-button" title="Close panel">×</button>
      <div class="codex-triage-panel__body">
        <div class="codex-triage-summary">
          <div class="codex-triage-title-wrap">
            <div class="codex-triage-title-line">
              <span class="codex-triage-summary-title" data-role="summary-title">Checking current thread…</span>
              <button type="button" data-role="action-menu-button" class="codex-triage-action-button" aria-label="Change suggested action" aria-expanded="false">⌄</button>
              <div class="codex-triage-action-menu" data-role="action-menu" hidden></div>
            </div>
            <div class="codex-triage-why" data-role="justification">Asking local companion.</div>
          </div>
        </div>

        <div class="codex-triage-field" data-role="field">
          <div class="codex-triage-label-row" data-role="label-row" hidden>
            <span class="codex-triage-field-label">Will do</span>
            <span class="codex-triage-label-chips" data-role="label-chips"></span>
          </div>
          <div class="codex-triage-ops" data-role="operations" hidden></div>
          <div class="codex-triage-draft-section" data-role="draft-section">
            <label class="codex-triage-field-label codex-triage-draft-label" for="${uid}-comment">Draft reply</label>
            <div class="codex-triage-draft-box">
              <textarea id="${uid}-comment" data-role="comment" name="codex-triage-comment" aria-label="Editable suggested public comment" placeholder="Suggested public comment will appear here"></textarea>
            </div>
          </div>
          <div class="codex-triage-draft-footer">
            <button type="button" data-role="apply" class="primary">Submit</button>
          </div>
        </div>

        <div class="codex-triage-statusline" data-role="status"></div>
      </div>
    `;

    panel.querySelector('[data-role="close"]').addEventListener('click', () => panel.remove());
    const actionButton = panel.querySelector('[data-role="action-menu-button"]');
    const actionMenu = panel.querySelector('[data-role="action-menu"]');
    actionButton.addEventListener('click', (event) => {
      event.preventDefault();
      const isOpen = !actionMenu.hidden;
      actionMenu.hidden = isOpen;
      actionButton.setAttribute('aria-expanded', String(!isOpen));
    });
    panel.querySelector('[data-role="comment"]').addEventListener('input', () => updateInsertButton(panel));
    panel.querySelector('[data-role="apply"]').addEventListener('click', () => {
      if (panel.dataset.applyEnabled === '1') applySelected(panel, item);
      else insertComment(panel);
    });
    return panel;
  }

  async function refreshPanel(panel, item, options = {}) {
    setStatus(panel, 'Loading…');
    panel.dataset.item = JSON.stringify(item);
    try {
      const result = await loadSuggestion(item, options);
      panel.dataset.suggestion = JSON.stringify(result);
      renderSuggestion(panel, result);
      setStatus(panel, '');
    } catch (error) {
      renderUnavailable(panel, error);
    }
  }


  function renderUnavailable(panel, error) {
    const title = panel.querySelector('[data-role="summary-title"]');
    const why = panel.querySelector('[data-role="justification"]');
    const field = panel.querySelector('[data-role="field"]');
    const actionButton = panel.querySelector('[data-role="action-menu-button"]');
    const menu = panel.querySelector('[data-role="action-menu"]');
    panel.dataset.action = 'unavailable';
    panel.dataset.applyEnabled = '0';
    if (title) title.textContent = 'Start local companion';
    if (why) {
      why.textContent = `The extension cannot reach ${API}. From the project folder, run: cd server && python3 server.py. Then refresh GitHub.`;
    }
    if (field) field.hidden = true;
    if (actionButton) actionButton.hidden = true;
    if (menu) menu.hidden = true;
    setStatus(panel, error?.message ? `Connection detail: ${error.message}` : 'Companion is not reachable.');
  }

  function renderSuggestion(panel, result) {
    const suggestion = result.suggestion;
    const actions = result.actions || [];
    const presentation = presentationFor(suggestion);

    panel.dataset.actions = JSON.stringify(actions);
    panel.dataset.existingLabels = JSON.stringify(result.source?.labels || []);
    panel.dataset.applyEnabled = result.applyEnabled ? '1' : '0';
    panel.dataset.action = suggestion.actionId || '';
    panel.querySelector('[data-role="summary-title"]').textContent = actionTitleFor(suggestion.actionId, suggestion.shortTitle || presentation.long);
    panel.querySelector('[data-role="justification"]').textContent = suggestion.justification || 'No private rationale provided.';
    setCommentValue(panel, suggestion.publicComment || '');
    renderOperations(panel, suggestion.operations || []);
    renderActionMenu(panel, actions, suggestion.actionId);
    updateInsertButton(panel);
  }

  function renderActionMenu(panel, actions, selectedActionId) {
    const menu = panel.querySelector('[data-role="action-menu"]');
    menu.innerHTML = '';
    for (const action of actions) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'codex-triage-action-menu__item';
      item.dataset.action = action.id;
      item.dataset.selected = action.id === selectedActionId ? 'true' : 'false';
      const text = actionMenuText(action);
      item.innerHTML = `<span>${escapeHtml(text.title)}</span><small>${escapeHtml(text.description)}</small>`;
      item.addEventListener('click', () => {
        const variant = (action.variants || [])[0];
        applyActionVariant(panel, action, variant);
        menu.hidden = true;
        panel.querySelector('[data-role="action-menu-button"]').setAttribute('aria-expanded', 'false');
      });
      menu.appendChild(item);
    }
  }

  function applyActionVariant(panel, action, variant) {
    if (!action || !variant) return;
    const ops = operationsForAction(panel, action, variant);
    const presentation = presentationFor({ actionId: action.id });
    setCommentValue(panel, variant.comment || '');
    panel.querySelector('[data-role="summary-title"]').textContent = actionTitleFor(action.id, action.title || 'Custom action');
    panel.dataset.action = action.id || '';
    panel.querySelector('[data-role="justification"]').textContent = `Manually switched to ${presentation.long.toLowerCase()}.`;
    renderOperations(panel, ops);
    updateInsertButton(panel);
    const actions = JSON.parse(panel.dataset.actions || '[]');
    renderActionMenu(panel, actions, action.id);
  }

  function operationsForAction(panel, action, variant) {
    const desired = action.statusLabel;
    const existingLabels = JSON.parse(panel.dataset.existingLabels || '[]');
    const statusPrefix = '[Status]';
    const ops = (action.operations || [])
      .filter((op) => op.type !== 'addLabel' && op.type !== 'removeLabel')
      .map((op) => {
        const item = { ...op };
        if (item.type === 'comment') item.body = variant.comment;
        return item;
      });

    if (desired) {
      for (const label of existingLabels) {
        if (String(label).startsWith(statusPrefix) && label !== desired) {
          ops.push({ type: 'removeLabel', label });
        }
      }
    }

    const wantsStatus = (action.operations || []).some((op) => op.type === 'addLabel' && op.label === desired);
    if (desired && wantsStatus && !existingLabels.includes(desired)) {
      ops.push({ type: 'addLabel', label: desired });
    }
    return ops;
  }

  function renderOperations(panel, operations) {
    const root = panel.querySelector('[data-role="operations"]');
    root.dataset.operations = JSON.stringify(operations || []);
    renderWorkChips(panel, operations || []);
  }

  function renderWorkChips(panel, operations) {
    const row = panel.querySelector('[data-role="label-row"]');
    const chips = panel.querySelector('[data-role="label-chips"]');
    const visibleOps = operations
      .filter((op) => op.type !== 'comment')
      .sort((a, b) => operationSort(a) - operationSort(b));
    row.hidden = !visibleOps.length;
    chips.innerHTML = '';
    for (const op of visibleOps) {
      const chip = document.createElement('span');
      if (op.type === 'addLabel' || op.type === 'removeLabel') {
        chip.className = 'codex-triage-label-chip';
        chip.dataset.op = op.type === 'addLabel' ? 'add' : 'remove';
        chip.textContent = `${op.type === 'addLabel' ? '+' : '−'} ${op.label}`;
      } else {
        chip.className = 'codex-triage-operation-chip';
        chip.dataset.op = op.type;
        chip.textContent = operationText(op);
      }
      chips.appendChild(chip);
    }
  }

  function actionTitleFor(actionId, fallback) {
    const titles = {
      'needs-proof': 'Ask for reproduction',
      'waiting-author': 'Waiting on contributor',
      'needs-design': 'Move to proposal/design',
      'ready-review': 'Review normally',
      'needs-slicing': 'Accepted direction, split first',
      'needs-owner': 'Find an owner',
      'no-capacity': 'Useful, no capacity',
      'close-not-actionable': 'Decline / close',
      'no-action': 'No action',
    };
    return titles[actionId] || fallback || 'Suggested action';
  }

  function actionMenuText(action) {
    const copy = {
      'needs-proof': ['Ask for reproduction', 'Need steps, logs, or benchmark'],
      'waiting-author': ['Waiting on contributor', 'Already asked; no public action yet'],
      'needs-design': ['Move to proposal/design', 'Agree on shape before code review'],
      'ready-review': ['Review normally', 'Small enough for the regular queue'],
      'needs-slicing': ['Split into smaller PRs', 'Direction may be accepted, review size is not'],
      'needs-owner': ['Find an owner', 'Needs someone accountable before review'],
      'no-capacity': ['Useful, no capacity', 'Aligned, but no reviewer capacity now'],
      'close-not-actionable': ['Decline / close', 'Out of scope, stale, or not actionable'],
      'no-action': ['No action', 'Already handled or no mutation needed'],
    };
    const [title, description] = copy[action.id] || [action.title || 'Suggested action', 'Switch to this action'];
    return { title, description };
  }

  function operationSort(op) {
    if (op.type === 'removeLabel') return 0;
    if (op.type === 'addLabel') return 1;
    return 2;
  }

  function operationText(op) {
    if (op.type === 'comment') return 'Post edited comment';
    if (op.type === 'addLabel') return `Add label: ${op.label}`;
    if (op.type === 'removeLabel') return `Remove label: ${op.label}`;
    if (op.type === 'close') return 'Will close issue/PR';
    return op.type;
  }

  function presentationFor(suggestion) {
    const map = {
      'needs-proof': ['Details', 'Ask details', 'Needs details'],
      'waiting-author': ['Waiting', 'Waiting', 'Waiting on contributor'],
      'needs-design': ['Design', 'Proposal', 'Needs design'],
      'ready-review': ['Review', 'Review', 'Ready for review'],
      'needs-slicing': ['Split', 'Split first', 'Needs slicing'],
      'needs-owner': ['Owner', 'Find owner', 'Needs owner'],
      'no-capacity': ['Capacity', 'No capacity', 'Useful, no capacity'],
      'close-not-actionable': ['Close', 'Close', 'Decline / close'],
      'no-action': ['Done', 'No action', 'No action'],
    };
    const [short, list, long] = map[suggestion?.actionId] || ['Triage', 'Triage', suggestion?.status || 'Suggested triage'];
    return { short, list, long };
  }

  async function apiRequest(path, body) {
    if (typeof chrome !== 'undefined' && chrome.runtime?.sendMessage) {
      const response = await chrome.runtime.sendMessage({ type: 'triage-api', path, body });
      if (!response?.ok) throw new Error(response?.body?.message || response?.statusText || 'Local companion request failed');
      return response.body;
    }
    const response = await fetch(`${API}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const result = await response.json();
    if (result.error) throw new Error(result.message || result.error);
    return result;
  }

  async function loadSuggestion(item, options = {}) {
    const key = `${item.repo}:${item.type}:${item.number}:${options.force ? 'force' : 'cache'}`;
    if (!options.force && suggestionCache.has(key)) return suggestionCache.get(key);
    const result = await apiRequest('/api/suggest', { ...item, force: !!options.force, url: location.href });
    suggestionCache.set(key, result);
    return result;
  }

  function selectedOperations(panel) {
    const root = panel.querySelector('[data-role="operations"]');
    return JSON.parse(root?.dataset.operations || '[]');
  }

  async function applySelected(panel, item) {
    const operations = selectedOperations(panel);
    const comment = commentValue(panel);
    const result = JSON.parse(panel.dataset.suggestion || '{}');
    const summary = operations.map(operationText).join('\n');
    if (!confirm(`Submit these operations via local gh?\n\n${summary || '(no operations selected)'}`)) return;
    const body = await apiRequest('/api/apply', { ...item, fingerprint: result.fingerprint, comment, operations });
    setStatus(panel, body.ok ? 'Submitted selected operations.' : (body.message || 'Submit returned errors. See console.'));
    console.log('[GitHub Triage Copilot] apply result', body);
  }

  function insertComment(panel) {
    const value = commentValue(panel);
    const textarea = findCommentTextarea();
    if (!textarea) {
      setStatus(panel, 'Could not find GitHub comment box. Copied instead.');
      navigator.clipboard.writeText(value);
      return;
    }
    textarea.focus();
    textarea.value = value;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.dispatchEvent(new Event('change', { bubbles: true }));
    setStatus(panel, 'Inserted into GitHub comment box. Review and submit in GitHub.');
  }

  function findCommentTextarea() {
    const candidates = Array.from(document.querySelectorAll('textarea')).filter((el) => !el.closest('.codex-triage-panel'));
    return candidates.find((el) => /comment/i.test(el.getAttribute('placeholder') || '') || /new_comment/i.test(el.name || '') || el.closest('form')) || candidates[0];
  }

  function commentValue(panel) {
    return panel.querySelector('[data-role="comment"]').value || '';
  }

  function setCommentValue(panel, value) {
    panel.querySelector('[data-role="comment"]').value = value || '';
  }

  function updateInsertButton(panel) {
    const hasComment = !!commentValue(panel).trim();
    const hasOperations = selectedOperations(panel).length > 0;
    const field = panel.querySelector('[data-role="field"]');
    const draftSection = panel.querySelector('[data-role="draft-section"]');
    const button = panel.querySelector('[data-role="apply"]');
    if (field) field.hidden = !hasComment && !hasOperations;
    if (draftSection) draftSection.hidden = !hasComment;
    button.hidden = !hasComment && !hasOperations;
    button.disabled = !hasComment && !hasOperations;
  }

  function setStatus(panel, text) {
    const el = panel.querySelector('[data-role="status"]');
    if (el) el.textContent = text || '';
  }

  function cleanup(text) { return text.replace(/\s+/g, ' ').trim(); }
  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\'': '&#39;', '"': '&quot;' }[char]));
  }

  boot();
})();
