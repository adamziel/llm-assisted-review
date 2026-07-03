(() => {
  const API = 'http://127.0.0.1:8765';
  const ALLOWED_REPO = 'WordPress/wordpress-playground';
  const FIXTURE_REPO = 'local/scenarios';
  const MENU_ACTION_IDS = ['fast-merge', 'medium-review', 'needs-proof', 'needs-design', 'needs-execution-plan', 'close-not-actionable', 'duplicate-of'];
  const seenRows = new WeakSet();
  const suggestionCache = new Map();
  const dismissedDetailPanels = new Set();
  const minimizedDetailPanels = new Set();
  let outsidePopoverListener = null;

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
    if (!route) {
      document.querySelector('#codex-triage-detail-panel')?.remove();
      document.querySelectorAll('.codex-triage-popover').forEach((el) => el.remove());
      return;
    }
    if (route.number) {
      renderDetailPanel(route);
      return;
    }
    document.querySelector('#codex-triage-detail-panel')?.remove();
    decorateList(route);
  }

  function parseRoute(url) {
    const parsed = new URL(url);
    const parts = parsed.pathname.split('/').filter(Boolean);
    if (parts.length < 3) return null;
    const [owner, name, area, number] = parts;
    if (!owner || !name) return null;
    if (!['issues', 'pull', 'pulls'].includes(area)) return null;
    const parsedRepo = `${owner}/${name}`;
    const repo = parsedRepo.toLowerCase() === ALLOWED_REPO.toLowerCase() ? ALLOWED_REPO : parsedRepo;
    if (repo !== ALLOWED_REPO && repo !== FIXTURE_REPO) return null;
    return {
      repo,
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
      button.textContent = 'Next action…';
      button.title = 'Load local next-action suggestion';
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
        row.classList.toggle('codex-triage-list-row-muted', isQuietSuggestion(result.suggestion));
        button.innerHTML = `<span class="codex-triage-list-prefix">Next action: </span><span class="codex-triage-list-action">${escapeHtml(p.list)}</span>`;
        button.setAttribute('aria-label', `Open next-action preview: ${p.long}. ${result.suggestion.justification || ''}`);
        button.title = `Open next-action preview: ${p.long}
${result.suggestion.justification || ''}

No GitHub action happens until you review and submit.`;
      }).catch((error) => {
        row.classList.remove('codex-triage-list-row-muted');
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
    const routeKey = itemKey(route);
    const existing = document.querySelector('#codex-triage-detail-panel');
    if (existing?.dataset.routeKey === routeKey) return;
    if (existing) existing.remove();
    if (isDetailPanelDismissed(route)) return;
    const mount = detailPanelMount();
    if (!mount) return;
    const panel = buildPanel(route, { detail: true });
    panel.id = 'codex-triage-detail-panel';
    panel.dataset.surface = 'detail';
    panel.dataset.routeKey = routeKey;
    setPanelMinimized(panel, isDetailPanelMinimized(route));
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
    panel.dataset.routeKey = itemKey(item);
    document.body.appendChild(panel);
    enableOutsidePopoverClose();
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
      <div class="codex-triage-panel-controls">
        <button type="button" data-role="reconsider" class="codex-triage-control-button codex-triage-reconsider-button" title="Get a fresh recommendation">Reconsider</button>
        <button type="button" data-role="minimize" class="codex-triage-control-button" title="Minimize panel" aria-label="Minimize panel" aria-expanded="true">−</button>
        <button type="button" data-role="close" class="codex-triage-control-button" title="Close panel" aria-label="Close panel">×</button>
      </div>
      <div class="codex-triage-panel__body">
        <div class="codex-triage-summary">
          <div class="codex-triage-title-wrap">
            <div class="codex-triage-title-line">
              <button type="button" data-role="action-menu-button" class="codex-triage-action-button" aria-label="Change suggested action" aria-expanded="false">
                <span class="codex-triage-action-prefix">Next action:</span>
                <span class="codex-triage-summary-title" data-role="summary-title">Checking current thread…</span>
                <span class="codex-triage-action-chevron" aria-hidden="true"></span>
              </button>
              <div class="codex-triage-action-menu" data-role="action-menu" hidden></div>
            </div>
            <div class="codex-triage-why" data-role="justification">Asking local companion.</div>
            <div class="codex-triage-evidence" data-role="evidence" hidden></div>
          </div>
        </div>
        <div class="codex-triage-links" data-role="links" hidden></div>

        <div class="codex-triage-field" data-role="field">
          <div class="codex-triage-label-row" data-role="label-row" hidden>
            <span class="codex-triage-field-label">Will do</span>
            <span class="codex-triage-label-chips" data-role="label-chips"></span>
          </div>
          <div class="codex-triage-ops" data-role="operations" hidden></div>
          <div class="codex-triage-local-actions" data-role="local-actions" hidden>
            <button type="button" data-role="reproduce">Ask Codex to reproduce</button>
          </div>
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

    panel.querySelector('[data-role="close"]').addEventListener('click', () => {
      if (panel.dataset.surface === 'detail') dismissDetailPanel(item);
      panel.remove();
    });
    panel.querySelector('[data-role="minimize"]').addEventListener('click', () => {
      const minimized = panel.dataset.minimized !== 'true';
      setPanelMinimized(panel, minimized);
      if (panel.dataset.surface === 'detail') saveDetailPanelMinimized(item, minimized);
    });
    panel.querySelector('[data-role="reconsider"]').addEventListener('click', () => reconsiderSuggestion(panel, item));
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
    panel.querySelector('[data-role="reproduce"]').addEventListener('click', () => askCodexToReproduce(panel, item));
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
    const evidence = panel.querySelector('[data-role="evidence"]');
    const links = panel.querySelector('[data-role="links"]');
    panel.dataset.action = 'unavailable';
    panel.dataset.applyEnabled = '0';
    if (title) title.textContent = 'Start local companion';
    if (why) {
      why.textContent = `The extension cannot reach ${API}. From the project folder, run: cd server && python3 server.py. Then refresh GitHub.`;
    }
    if (field) field.hidden = true;
    if (actionButton) actionButton.hidden = true;
    if (menu) menu.hidden = true;
    if (evidence) evidence.hidden = true;
    if (links) links.hidden = true;
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
    panel.querySelector('[data-role="summary-title"]').textContent = presentation.long;
    panel.querySelector('[data-role="justification"]').textContent = suggestion.justification || 'No private rationale provided.';
    renderEvidence(panel, result.evidence || []);
    renderActionLinks(panel, result.links || []);
    setCommentValue(panel, suggestion.publicComment || '');
    renderOperations(panel, suggestion.operations || []);
    renderActionMenu(panel, actions, suggestion.actionId);
    updateLocalActions(panel);
    updateInsertButton(panel);
  }

  function renderEvidence(panel, rows) {
    const root = panel.querySelector('[data-role="evidence"]');
    root.hidden = !rows.length;
    root.innerHTML = '';
    for (const row of rows) {
      const item = document.createElement('div');
      item.className = 'codex-triage-evidence__row';
      let value;
      if (Array.isArray(row.links) && row.links.length) {
        value = `<div class="codex-triage-evidence-links">${row.links.map(evidenceLinkHtml).join('')}</div>`;
      } else {
        value = row.href
          ? `<a href="${escapeHtml(row.href)}" target="_blank" rel="noreferrer">${escapeHtml(row.value)}</a>`
          : `<strong>${escapeHtml(row.value)}</strong>`;
      }
      item.innerHTML = `<span>${escapeHtml(row.label)}</span>${value}`;
      root.appendChild(item);
    }
  }

  function evidenceLinkHtml(link) {
    const meta = link.meta ? `<small>${escapeHtml(link.meta)}</small>` : '';
    const title = link.title || link.meta || link.label || '';
    if (link.href) {
      return `<a class="codex-triage-evidence-link" href="${escapeHtml(link.href)}" target="_blank" rel="noreferrer" title="${escapeHtml(title)}"><b>${escapeHtml(link.label || link.href)}</b>${meta}</a>`;
    }
    return `<span class="codex-triage-evidence-link"><b>${escapeHtml(link.label || '')}</b>${meta}</span>`;
  }

  function renderActionLinks(panel, links) {
    const root = panel.querySelector('[data-role="links"]');
    if (!root) return;
    root.hidden = !links.length;
    root.innerHTML = '';
    for (const link of links) {
      const anchor = document.createElement('a');
      anchor.className = 'codex-triage-link-card';
      anchor.href = link.href;
      anchor.target = '_blank';
      anchor.rel = 'noreferrer';
      anchor.innerHTML = `
        <span class="codex-triage-link-card__label">${escapeHtml(link.label || 'Open link')}</span>
        <span class="codex-triage-link-card__meta">${escapeHtml(link.meta || '')}</span>
      `;
      root.appendChild(anchor);
    }
  }

  function renderActionMenu(panel, actions, selectedActionId) {
    const menu = panel.querySelector('[data-role="action-menu"]');
    menu.innerHTML = '';
    for (const action of menuActions(actions, selectedActionId)) {
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

  function menuActions(actions, selectedActionId) {
    const byId = new Map(actions.map((action) => [action.id, action]));
    const visible = MENU_ACTION_IDS.map((id) => byId.get(id)).filter(Boolean);
    if (selectedActionId && !MENU_ACTION_IDS.includes(selectedActionId) && byId.has(selectedActionId)) {
      return [byId.get(selectedActionId), ...visible];
    }
    return visible;
  }

  function applyActionVariant(panel, action, variant) {
    if (!action || !variant) return;
    const ops = operationsForAction(panel, action, variant);
    const presentation = presentationFor({ actionId: action.id, variantId: variant?.id, status: action.title });
    setCommentValue(panel, variant.comment || '');
    panel.querySelector('[data-role="summary-title"]').textContent = presentation.long;
    panel.dataset.action = action.id || '';
    panel.querySelector('[data-role="justification"]').textContent = `Manually switched to ${presentation.long.toLowerCase()}.`;
    renderOperations(panel, ops);
    updateLocalActions(panel);
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

  function actionMenuText(action) {
    const copy = {
      'fast-merge': ['Fast-review PR', 'Small, tested, low-risk'],
      'medium-review': ['Review with budget', 'In scope, but not fast-track'],
      'needs-proof': ['Ask for details', 'Need steps, logs, or benchmark'],
      'waiting-author': ['Wait for response', 'Already asked; no public action yet'],
      'needs-design': ['Move to proposal', 'Agree on shape before code review'],
      'has-candidate-pr': ['Review the PR', 'Route work through the existing PR'],
      'needs-rereview': ['Re-review PR', 'Author followed up after feedback'],
      'competing-prs': ['Choose PR path', 'Multiple PRs address the same issue'],
      'narrow-fast-path': ['Review narrow PR', 'Prefer the smallest sufficient patch'],
      'needs-execution-plan': ['Ask for plan', 'Accepted direction still needs slices'],
      'needs-owner': ['Find owner', 'Needs someone accountable before review'],
      'no-capacity': ['Defer—no capacity', 'Aligned, but no reviewer capacity now'],
      'close-not-actionable': ['Close issue', 'Out of scope, stale, or not actionable'],
      'duplicate-of': ['Close as duplicate', 'Point to the canonical issue'],
      'no-action': ['None needed', 'Already handled or no mutation needed'],
    };
    const [title, description] = copy[action.id] || [action.title || 'Suggested action', 'Switch to this action'];
    return { title, description };
  }

  function operationSort(op) {
    if (op.type === 'removeLabel') return 0;
    if (op.type === 'addLabel') return 1;
    if (op.type === 'requestReview') return 2;
    return 2;
  }

  function operationText(op) {
    if (op.type === 'comment') return 'Post edited comment';
    if (op.type === 'addLabel') return `Add label: ${op.label}`;
    if (op.type === 'removeLabel') return `Remove label: ${op.label}`;
    if (op.type === 'requestReview') return `Request re-review from @${op.reviewer}`;
    if (op.type === 'close') return 'Will close issue/PR';
    return op.type;
  }

  function presentationFor(suggestion) {
    const copy = nextActionCopy(suggestion);
    return { short: copy.short, list: copy.list, long: copy.long };
  }

  function nextActionCopy(suggestion) {
    const actionId = suggestion?.actionId;
    const variantId = suggestion?.variantId;
    if (actionId === 'no-action') {
      return { short: 'None', list: 'None needed', long: 'None needed' };
    }
    if (actionId === 'waiting-author') {
      if (variantId === 'draft-pr') return { short: 'Wait', list: 'Wait for ready', long: 'Wait for author to mark ready' };
      if (variantId === 'changes-requested') return { short: 'Wait', list: 'Wait for changes', long: 'Wait for author changes' };
      return { short: 'Wait', list: 'Wait for response', long: 'Wait for contributor response' };
    }
    if (actionId === 'close-not-actionable') {
      if (variantId === 'out-of-scope') return { short: 'Close', list: 'Close—out of scope', long: 'Close issue—out of scope' };
      if (variantId === 'stale-waiting') return { short: 'Close', list: 'Close stale issue', long: 'Close stale issue' };
      if (variantId === 'research-note') return { short: 'Close', list: 'Close research note', long: 'Close research note' };
      return { short: 'Close', list: 'Close—can’t act', long: 'Close issue—can’t act yet' };
    }
    const map = {
      'fast-merge': ['Review', 'Fast-review PR', 'Fast-review PR'],
      'medium-review': ['Review', 'Review with budget', 'Review with explicit budget'],
      'needs-proof': ['Ask', 'Ask for details', 'Ask for reproduction details'],
      'needs-design': ['Proposal', 'Move to proposal', 'Move to proposal/design'],
      'has-candidate-pr': ['Review', 'Review the PR', 'Review the candidate PR'],
      'needs-rereview': ['Review', 'Re-review PR', 'Re-review PR'],
      'competing-prs': ['Choose', 'Choose PR path', 'Choose PR path'],
      'narrow-fast-path': ['Review', 'Review narrow PR', 'Review narrow PR first'],
      'needs-execution-plan': ['Plan', 'Ask for plan', 'Ask for execution plan'],
      'needs-owner': ['Owner', 'Find owner', 'Find an owner'],
      'no-capacity': ['Defer', 'Defer—no capacity', 'Defer—no maintainer capacity'],
      'duplicate-of': ['Close', 'Close as duplicate', 'Close as duplicate'],
    };
    const [short, list, long] = map[actionId] || ['Action', suggestion?.status || 'Decide next step', suggestion?.status || 'Decide next step'];
    return { short, list, long };
  }

  function isQuietSuggestion(suggestion) {
    return ['no-action', 'waiting-author'].includes(suggestion?.actionId);
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
    const key = itemKey(item);
    if (!options.force && suggestionCache.has(key)) return suggestionCache.get(key);
    const result = await apiRequest('/api/suggest', { ...item, force: !!options.force, url: location.href });
    suggestionCache.set(key, result);
    return result;
  }

  function invalidateSuggestion(item) {
    suggestionCache.delete(itemKey(item));
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

  async function reconsiderSuggestion(panel, item) {
    const button = panel.querySelector('[data-role="reconsider"]');
    button.disabled = true;
    invalidateSuggestion(item);
    setStatus(panel, 'Reconsidering…');
    try {
      await refreshPanel(panel, item, { force: true });
      setStatus(panel, 'Reconsidered from current GitHub state.');
    } catch (error) {
      setStatus(panel, `Could not reconsider: ${error.message}`);
    } finally {
      button.disabled = false;
    }
  }

  async function askCodexToReproduce(panel, item) {
    const button = panel.querySelector('[data-role="reproduce"]');
    button.disabled = true;
    setStatus(panel, 'Opening a local Codex terminal…');
    try {
      const result = await apiRequest('/api/reproduce', { ...item, url: location.href });
      setStatus(panel, result.message || 'Opened a local Codex reproduction session.');
    } catch (error) {
      setStatus(panel, `Could not open Codex: ${error.message}`);
    } finally {
      button.disabled = false;
    }
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
    const hasLocalActions = !panel.querySelector('[data-role="local-actions"]')?.hidden;
    const canApplyOperations = hasOperations && panel.dataset.applyEnabled === '1';
    if (field) field.hidden = !hasComment && !hasOperations && !hasLocalActions;
    if (draftSection) draftSection.hidden = !hasComment;
    button.hidden = !hasComment && !canApplyOperations;
    button.disabled = !hasComment && !canApplyOperations;
  }

  function updateLocalActions(panel) {
    const root = panel.querySelector('[data-role="local-actions"]');
    if (!root) return;
    const item = JSON.parse(panel.dataset.item || '{}');
    root.hidden = item.type !== 'issue' || !['medium-review', 'needs-proof', 'needs-owner'].includes(panel.dataset.action);
  }

  function setPanelMinimized(panel, minimized) {
    panel.dataset.minimized = minimized ? 'true' : 'false';
    const button = panel.querySelector('[data-role="minimize"]');
    if (!button) return;
    button.textContent = minimized ? '+' : '−';
    button.title = minimized ? 'Expand panel' : 'Minimize panel';
    button.setAttribute('aria-label', minimized ? 'Expand panel' : 'Minimize panel');
    button.setAttribute('aria-expanded', String(!minimized));
  }

  function dismissDetailPanel(item) {
    dismissedDetailPanels.add(itemKey(item));
  }

  function isDetailPanelDismissed(item) {
    return dismissedDetailPanels.has(itemKey(item));
  }

  function saveDetailPanelMinimized(item, minimized) {
    const key = itemKey(item);
    if (minimized) minimizedDetailPanels.add(key);
    else minimizedDetailPanels.delete(key);
  }

  function isDetailPanelMinimized(item) {
    return minimizedDetailPanels.has(itemKey(item));
  }

  function enableOutsidePopoverClose() {
    if (outsidePopoverListener) return;
    outsidePopoverListener = (event) => {
      const target = event.target;
      if (target.closest?.('.codex-triage-popover') || target.closest?.('.codex-triage-list-button')) return;
      document.querySelectorAll('.codex-triage-popover').forEach((el) => el.remove());
      if (!document.querySelector('.codex-triage-popover')) {
        document.removeEventListener('pointerdown', outsidePopoverListener, true);
        outsidePopoverListener = null;
      }
    };
    document.addEventListener('pointerdown', outsidePopoverListener, true);
  }

  function setStatus(panel, text) {
    const el = panel.querySelector('[data-role="status"]');
    if (el) el.textContent = text || '';
  }

  function cleanup(text) { return text.replace(/\s+/g, ' ').trim(); }
  function itemKey(item) { return `${item.repo}:${item.type}:${item.number}`; }
  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\'': '&#39;', '"': '&quot;' }[char]));
  }

  boot();
})();
