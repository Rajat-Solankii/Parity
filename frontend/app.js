/**
 * Parity Frontend — app.js
 * Client-side logic for the Parity Environment Dashboard.
 *
 * Connects to the Flask API at http://localhost:5117
 */

const API_BASE = 'http://localhost:5117/api';

// ─── State ───
const state = {
  projectPath: '.',
  findings: [],
  scan: null,
  fingerprint: null,
  lastAnalyzed: null,
};

// ─── DOM refs ───
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ─── Navigation ───
function switchView(viewId) {
  $$('.view').forEach((v) => v.classList.remove('active'));
  $$('.nav-item').forEach((n) => n.classList.remove('active'));

  const view = $(`#view-${viewId}`);
  if (view) view.classList.add('active');

  const nav = $(`[data-view="${viewId}"]`);
  if (nav) nav.classList.add('active');

  const titles = {
    dashboard: 'Dashboard',
    findings: 'Findings',
    scan: 'Scan',
    fingerprint: 'Fingerprint',
    tools: 'Native Tools',
    packages: 'Packages',
    resolve: 'Resolve Import',
    'json-viewer': 'Raw JSON',
  };

  const breadcrumbs = {
    dashboard: 'Overview',
    findings: 'Diagnostics',
    scan: 'Dependencies',
    fingerprint: 'Environment',
    tools: 'Environment',
    packages: 'Environment',
    resolve: 'Utilities',
    'json-viewer': 'Utilities',
  };

  $('#page-title').textContent = titles[viewId] || viewId;
  $('#breadcrumb-current').textContent = breadcrumbs[viewId] || viewId;
}

// Attach nav clicks
document.addEventListener('click', (e) => {
  const navItem = e.target.closest('[data-view]');
  if (navItem) {
    switchView(navItem.dataset.view);
  }
});

// ─── Loading Overlay ───
function showLoading(msg = 'Analyzing project…') {
  $('#loading-text').textContent = msg;
  $('#loading-overlay').classList.add('visible');
}

function hideLoading() {
  $('#loading-overlay').classList.remove('visible');
}

// ─── Toasts ───
function showToast(message, type = 'info') {
  const container = $('#toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span>${type === 'success' ? '✓' : type === 'error' ? '✕' : 'ℹ'}</span>
    <span>${message}</span>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.transition = 'opacity 0.3s, transform 0.3s';
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ─── API Calls ───
async function apiCall(endpoint, options = {}) {
  try {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(errData.error || `HTTP ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    if (err.message.includes('Failed to fetch') || err.message.includes('NetworkError')) {
      throw new Error('Cannot connect to Parity server. Is server.py running on port 5117?');
    }
    throw err;
  }
}

// ─── Main Analysis ───
async function analyzeProject() {
  const path = $('#project-path').value.trim();
  if (!path) {
    showToast('Please enter a project path.', 'error');
    return;
  }

  state.projectPath = path;
  showLoading('Scanning project and diagnosing…');

  try {
    // Run all three API calls in parallel
    const [scanData, findingsData, fpData] = await Promise.all([
      apiCall('/scan', {
        method: 'POST',
        body: JSON.stringify({ path }),
      }),
      apiCall('/diagnose', {
        method: 'POST',
        body: JSON.stringify({ path }),
      }),
      apiCall('/fingerprint', {
        method: 'POST',
        body: JSON.stringify({ path }),
      }),
    ]);

    state.scan = scanData;
    state.findings = findingsData.findings || [];
    state.fingerprint = fpData;
    state.lastAnalyzed = new Date();

    renderAll();
    showToast('Analysis complete!', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    hideLoading();
  }
}

// ─── Renderers ───

function renderAll() {
  renderStats();
  renderDashboardPreview();
  renderDashboardEnv();
  renderFindings();
  renderScan();
  renderFingerprint();
  renderTools();
  renderPackages();
  renderJsonViewer();
  updateBadges();
  updateTimestamp();
}

function updateTimestamp() {
  if (state.lastAnalyzed) {
    const fmt = state.lastAnalyzed.toLocaleTimeString();
    $('#last-analyzed').textContent = `Last analyzed ${fmt}`;
  }
}

function updateBadges() {
  const count = state.findings.length;
  const badge = $('#findings-badge');
  if (count > 0) {
    badge.textContent = count;
    badge.style.display = 'inline';
  } else {
    badge.style.display = 'none';
  }
}

function renderStats() {
  const f = state.findings.length;
  const d = state.scan
    ? Object.keys(state.scan.declared?.runtime || {}).length +
      Object.keys(state.scan.declared?.dev || {}).length
    : 0;
  const i = state.scan
    ? Object.keys(state.scan.imported?.runtime || {}).length +
      Object.keys(state.scan.imported?.dev || {}).length +
      Object.keys(state.scan.imported?.optional || {}).length
    : 0;
  const p = state.fingerprint
    ? Object.keys(state.fingerprint.installed_packages || {}).length
    : 0;

  animateCounter('stat-findings', f);
  animateCounter('stat-declared', d);
  animateCounter('stat-imported', i);
  animateCounter('stat-packages', p);
}

function animateCounter(id, target) {
  const el = $(`#${id}`);
  const current = parseInt(el.textContent) || 0;
  if (current === target) {
    el.textContent = target;
    return;
  }
  const step = target > current ? 1 : -1;
  const steps = Math.abs(target - current);
  const duration = Math.min(500, steps * 30);
  const interval = duration / steps;
  let val = current;
  const timer = setInterval(() => {
    val += step;
    el.textContent = val;
    if (val === target) clearInterval(timer);
  }, interval);
}

function renderDashboardPreview() {
  const container = $('#dashboard-findings-preview');
  if (!state.findings.length) {
    if (state.lastAnalyzed) {
      container.innerHTML = `
        <div class="empty-state" style="padding:var(--space-8);">
          <div class="icon">✅</div>
          <h3>No issues found</h3>
          <p>Your project's dependencies look healthy!</p>
        </div>`;
    }
    return;
  }

  // Show top 5
  const top = state.findings.slice(0, 5);
  container.innerHTML = top.map((f, i) => renderFindingItem(f, i)).join('');
  
  // Attach solve handlers
  container.querySelectorAll('[data-solve-idx]').forEach((btn) => {
    btn.addEventListener('click', () => solveFinding(parseInt(btn.dataset.solveIdx, 10), btn));
  });
}

function renderDashboardEnv() {
  const container = $('#dashboard-env-summary');
  const fp = state.fingerprint;
  if (!fp) return;

  container.innerHTML = `
    <div class="env-grid">
      <div class="env-row">
        <span class="env-label">Operating System</span>
        <span class="env-value">${esc(fp.os_name)} ${esc(fp.os_release)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Architecture</span>
        <span class="env-value">${esc(fp.architecture)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Python</span>
        <span class="env-value">${esc(fp.python_version)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Virtual Env</span>
        <span class="env-value">${fp.is_venv ? '✓ Yes' : '✕ No'}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Pip</span>
        <span class="env-value">${esc(fp.pip_version || 'N/A')}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Installed Packages</span>
        <span class="env-value">${Object.keys(fp.installed_packages || {}).length}</span>
      </div>
    </div>`;
}

function renderFindings() {
  const list = $('#findings-list');
  const sevFilter = $('#filter-severity').value;
  const ruleFilter = $('#filter-rule').value;

  let filtered = state.findings;
  if (sevFilter !== 'all') {
    filtered = filtered.filter((f) => f.severity === sevFilter);
  }
  if (ruleFilter !== 'all') {
    filtered = filtered.filter((f) => f.rule_id === ruleFilter);
  }

  $('#findings-count').textContent = `${filtered.length} finding${filtered.length !== 1 ? 's' : ''}`;

  if (!filtered.length) {
    list.innerHTML = `
      <div class="empty-state">
        <div class="icon">${state.findings.length ? '🔎' : '✅'}</div>
        <h3>${state.findings.length ? 'No matching findings' : 'No findings'}</h3>
        <p>${state.findings.length ? 'Try adjusting the filters.' : 'Analyze a project to see diagnostic findings.'}</p>
      </div>`;
    return;
  }

  list.innerHTML = filtered.map((f, i) => renderFindingItem(f, i)).join('');
  
  // Attach solve handlers
  list.querySelectorAll('[data-solve-idx]').forEach((btn) => {
    btn.addEventListener('click', () => solveFinding(parseInt(btn.dataset.solveIdx, 10), btn));
  });
}

function renderFindingItem(finding, idx) {
  const sevClass = finding.severity || 'medium';
  const evidenceHtml = (finding.evidence || [])
    .map(
      (ev) =>
        `<span class="finding-tag">📄 ${esc(ev.file ? ev.file.split(/[/\\]/).pop() : '?')}:${ev.line}</span>`
    )
    .join('');

  let fixHtml = '';
  if (finding.fix) {
    if (finding.fix.auto) {
      fixHtml = `<span class="finding-fix" style="display:inline-flex;align-items:center;gap:8px;">
        <span>⚡ Auto: ${esc(finding.fix.kind)} ${esc(finding.fix.target)}</span>
        <button class="btn btn-primary" style="font-size:0.7rem;padding:2px 8px;border-radius:4px;" data-solve-idx="${idx}">Solve</button>
      </span>`;
    } else {
      fixHtml = `<span class="finding-fix" style="display:inline-flex;align-items:center;gap:8px;">
        <span>🔧 Manual: ${esc(finding.fix.kind)} ${esc(finding.fix.target)}</span>
        <button class="btn btn-secondary" style="font-size:0.7rem;padding:2px 8px;border-radius:4px;" data-solve-idx="${idx}" data-manual="true">Solve...</button>
      </span>`;
    }
  }

  return `
    <div class="finding-item severity-${sevClass}">
      <div class="finding-header">
        <span class="finding-rule">${esc(finding.rule_id)}</span>
        <span class="finding-title">${esc(finding.title)}</span>
        <span class="finding-severity ${sevClass}">${esc(finding.severity)}</span>
      </div>
      <div class="finding-explanation">${esc(finding.explanation)}</div>
      <div class="finding-meta">
        <span class="finding-tag">🏷 ${esc(finding.group)}</span>
        <span class="finding-tag">🎯 ${esc(finding.confidence)}</span>
        ${evidenceHtml}
        ${fixHtml}
      </div>
    </div>`;
}

function promptManualFix(defaultTarget) {
  return new Promise((resolve) => {
    const modal = $('#manual-solve-modal');
    const input = $('#manual-solve-input');
    const btnOk = $('#manual-solve-ok');
    const btnCancel = $('#manual-solve-cancel');
    
    input.value = defaultTarget;
    modal.classList.remove('hidden');
    input.focus();
    input.select();
    
    const cleanup = () => {
      modal.classList.add('hidden');
      btnOk.removeEventListener('click', onOk);
      btnCancel.removeEventListener('click', onCancel);
      input.removeEventListener('keydown', onKey);
    };
    
    const onOk = () => { cleanup(); resolve(input.value); };
    const onCancel = () => { cleanup(); resolve(null); };
    const onKey = (e) => {
      if (e.key === 'Enter') onOk();
      if (e.key === 'Escape') onCancel();
    };
    
    btnOk.addEventListener('click', onOk);
    btnCancel.addEventListener('click', onCancel);
    input.addEventListener('keydown', onKey);
  });
}

async function solveFinding(idx, btn) {
  let finding = state.findings[idx];
  if (!finding) return;
  
  if (btn.dataset.manual === 'true') {
    const defaultTarget = (finding.fix.target || '').split(' | ')[0];
    const userInput = await promptManualFix(defaultTarget);
    if (userInput === null || !userInput.trim()) return; // User cancelled
    
    finding = JSON.parse(JSON.stringify(finding)); // Clone it
    finding.fix.target = userInput.trim();
    if (finding.rule_id === 'R1' || finding.rule_id === 'R2') {
        finding.fix.kind = 'INSTALL_PACKAGE'; // Make it parseable by backend
    }
  }
  
  const originalHtml = btn.innerHTML;
  btn.innerHTML = '<div class="spinner" style="width:12px;height:12px;display:inline-block;"></div>';
  btn.disabled = true;
  
  try {
    const data = await apiCall('/solve', {
      method: 'POST',
      body: JSON.stringify({ path: $('#project-path').value, finding })
    });
    
    showToast(data.message || 'Fix applied!', 'success');
    btn.parentElement.innerHTML = `<span style="color:var(--accent-emerald);">✓ Fixed: ${data.message}</span>`;
  } catch(err) {
    showToast(`Failed to apply fix: ${err.message}`, 'error');
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
}

function renderScan() {
  const declContainer = $('#scan-declared');
  const impContainer = $('#scan-imported');
  const scan = state.scan;
  if (!scan) return;

  // Declared
  const decl = scan.declared || {};
  const runtime = Object.entries(decl.runtime || {});
  const dev = Object.entries(decl.dev || {});
  const unresolved = decl.unresolved || [];

  if (runtime.length || dev.length) {
    let html = '<table class="data-table"><thead><tr><th>Package</th><th>Group</th><th>Specifier</th></tr></thead><tbody>';
    for (const [name, locs] of runtime) {
      const spec = locs?.[0]?.specifier || '';
      html += `<tr><td class="mono">${esc(name)}</td><td><span class="status-badge found">runtime</span></td><td class="mono">${esc(spec)}</td></tr>`;
    }
    for (const [name, locs] of dev) {
      const spec = locs?.[0]?.specifier || '';
      html += `<tr><td class="mono">${esc(name)}</td><td><span class="status-badge not-on-path">dev</span></td><td class="mono">${esc(spec)}</td></tr>`;
    }
    if (unresolved.length) {
      for (const u of unresolved) {
        html += `<tr><td class="mono">${esc(u)}</td><td><span class="status-badge broken">unresolved</span></td><td>—</td></tr>`;
      }
    }
    html += '</tbody></table>';
    declContainer.innerHTML = html;
  }

  // Imported
  const imp = scan.imported || {};
  const runtimeImp = Object.keys(imp.runtime || {});
  const devImp = Object.keys(imp.dev || {});
  const optImp = Object.keys(imp.optional || {});

  function renderLocations(locs) {
    if (!locs || !locs.length) return '0 files';
    const count = locs.length;
    const label = `${count} file${count !== 1 ? 's' : ''}`;
    if (count === 0) return label;
    
    const listHtml = locs.map(loc => {
      const file = loc.file ? loc.file.split(/[/\\]/).pop() : '?';
      return `<div style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${esc(loc.file)}">📄 ${esc(file)}:${loc.line}</div>`;
    }).join('');
    
    return `
      <details>
        <summary style="cursor:pointer; color:var(--accent-blue); user-select:none; font-weight:500;">${label}</summary>
        <div style="margin-top:6px; font-size:0.75rem; color:var(--text-secondary); max-height:120px; overflow-y:auto; padding-left:12px; border-left:2px solid var(--border-color);">
          ${listHtml}
        </div>
      </details>
    `;
  }

  if (runtimeImp.length || devImp.length || optImp.length) {
    let html =
      '<table class="data-table"><colgroup><col style="width:30%"><col style="width:20%"><col style="width:50%"></colgroup><thead><tr><th>Module</th><th>Group</th><th>Locations</th></tr></thead><tbody>';
    for (const name of runtimeImp) {
      html += `<tr><td class="mono">${esc(name)}</td><td style="vertical-align:top;"><span class="status-badge found">runtime</span></td><td style="vertical-align:top;">${renderLocations(imp.runtime[name])}</td></tr>`;
    }
    for (const name of devImp) {
      html += `<tr><td class="mono">${esc(name)}</td><td style="vertical-align:top;"><span class="status-badge not-on-path">dev</span></td><td style="vertical-align:top;">${renderLocations(imp.dev[name])}</td></tr>`;
    }
    for (const name of optImp) {
      html += `<tr><td class="mono">${esc(name)}</td><td style="vertical-align:top;"><span class="status-badge broken">optional</span></td><td style="vertical-align:top;">${renderLocations(imp.optional[name])}</td></tr>`;
    }
    html += '</tbody></table>';
    impContainer.innerHTML = html;
  }
}

function renderFingerprint() {
  const container = $('#fingerprint-detail');
  const fp = state.fingerprint;
  if (!fp) return;

  container.innerHTML = `
    <div class="env-grid">
      <div class="env-row">
        <span class="env-label">Schema Version</span>
        <span class="env-value">${esc(fp.schema_version)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Parity Version</span>
        <span class="env-value">${esc(fp.parity_version)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Timestamp</span>
        <span class="env-value">${esc(fp.timestamp)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Operating System</span>
        <span class="env-value">${esc(fp.os_name)} ${esc(fp.os_release)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Architecture</span>
        <span class="env-value">${esc(fp.architecture)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Platform Tags</span>
        <span class="env-value" style="font-size:0.75rem;">${(fp.platform_tags || []).slice(0, 5).map(esc).join(', ')}${(fp.platform_tags || []).length > 5 ? '…' : ''}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Python Version</span>
        <span class="env-value">${esc(fp.python_version)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Python Executable</span>
        <span class="env-value" style="font-size:0.75rem;">${esc(fp.python_executable)}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Virtual Environment</span>
        <span class="env-value">${fp.is_venv ? '✓ Yes' : '✕ No'}</span>
      </div>
      <div class="env-row">
        <span class="env-label">Pip Version</span>
        <span class="env-value">${esc(fp.pip_version || 'N/A')}</span>
      </div>
    </div>`;
}

function renderTools() {
  const container = $('#tools-list');
  const fp = state.fingerprint;
  if (!fp || !fp.native_tools || !Object.keys(fp.native_tools).length) return;

  let html =
    '<table class="data-table"><colgroup><col style="width:12%"><col style="width:15%"><col style="width:15%"><col style="width:40%"><col style="width:18%"></colgroup><thead><tr><th>Tool</th><th>Status</th><th>Version</th><th>Path</th><th></th></tr></thead><tbody>';

  for (const [name, tool] of Object.entries(fp.native_tools)) {
    const statusMap = {
      FOUND: ['found', '● Found'],
      FOUND_NOT_ON_PATH: ['not-on-path', '◐ Not on PATH'],
      NOT_FOUND: ['not-found', '○ Not Found'],
      BROKEN: ['broken', '✕ Broken'],
      FOUND_INCOMPLETE: ['broken', '◑ Incomplete'],
    };
    const [cls, label] = statusMap[tool.status] || ['not-found', tool.status];

    const canInstall = tool.status === 'NOT_FOUND' || tool.status === 'BROKEN';
    const canAddToPath = tool.status === 'FOUND_NOT_ON_PATH' && tool.path;
    
    let actionHtml = '';
    if (canInstall) {
      actionHtml = `<button class="btn btn-primary" style="font-size:0.72rem;padding:4px 12px;" data-install-tool="${esc(name)}" id="install-btn-${esc(name)}">⬇ Install</button>`;
    } else if (canAddToPath) {
      actionHtml = `<button class="btn btn-primary" style="font-size:0.72rem;padding:4px 12px;" data-add-path="${esc(tool.path)}" id="path-btn-${esc(name)}">➕ Add to PATH</button>`;
    } else {
      actionHtml = '<span style="color:var(--accent-emerald);font-size:0.75rem;">✓</span>';
    }

    html += `<tr id="tool-row-${esc(name)}">
      <td class="mono" style="font-weight:600;">${esc(name)}</td>
      <td id="tool-status-${esc(name)}"><span class="status-badge ${cls}">${label}</span></td>
      <td class="mono" id="tool-version-${esc(name)}">${esc(tool.version || '—')}</td>
      <td class="mono text-secondary" style="font-size:0.75rem;" id="tool-path-${esc(name)}">${esc(tool.path || '—')}</td>
      <td id="tool-action-${esc(name)}">${actionHtml}</td>
    </tr>`;
  }

  html += '</tbody></table>';
  container.innerHTML = html;

  // Attach tool installation handlers
  container.querySelectorAll('[data-install-tool]').forEach((btn) => {
    btn.addEventListener('click', () => installTool(btn.dataset.installTool));
  });
  
  // Attach add-to-path handlers
  container.querySelectorAll('[data-add-path]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const originalHtml = btn.innerHTML;
      btn.innerHTML = '<div class="spinner" style="width:12px;height:12px;display:inline-block;"></div>';
      btn.disabled = true;
      try {
        const data = await apiCall('/add-to-path', {
          method: 'POST',
          body: JSON.stringify({ path: btn.dataset.addPath })
        });
        showToast(data.message, 'success');
        btn.parentElement.innerHTML = '<span style="color:var(--accent-emerald);font-size:0.75rem;">✓ Added</span>';
      } catch (err) {
        showToast(err.message, 'error');
        btn.innerHTML = originalHtml;
        btn.disabled = false;
      }
    });
  });

  // Attach install click handlers
  container.querySelectorAll('[data-install-tool]').forEach((btn) => {
    btn.addEventListener('click', () => installTool(btn.dataset.installTool));
  });
}

async function installTool(toolName) {
  const btnCell = $(`#tool-action-${toolName}`);
  const statusCell = $(`#tool-status-${toolName}`);
  const versionCell = $(`#tool-version-${toolName}`);
  const pathCell = $(`#tool-path-${toolName}`);

  // Show spinner
  btnCell.innerHTML = '<div class="spinner" style="width:16px;height:16px;"></div> <span style="font-size:0.72rem;color:var(--text-secondary);">Installing…</span>';

  try {
    const data = await apiCall('/install-tool', {
      method: 'POST',
      body: JSON.stringify({ tool: toolName }),
    });

    // Update the row
    statusCell.innerHTML = '<span class="status-badge found">● Found</span>';
    pathCell.textContent = data.path || '—';
    btnCell.innerHTML = '<span style="color:var(--accent-emerald);font-size:0.75rem;">✓</span>';
    versionCell.textContent = 'installed';

    showToast(`${toolName} installed successfully!`, 'success');
  } catch (err) {
    btnCell.innerHTML = `<button class="btn btn-danger" style="font-size:0.72rem;padding:4px 12px;" data-install-tool="${esc(toolName)}">✕ Retry</button>`;
    // Re-attach handler to retry button
    const retryBtn = btnCell.querySelector('[data-install-tool]');
    if (retryBtn) retryBtn.addEventListener('click', () => installTool(toolName));

    showToast(`Failed to install ${toolName}: ${err.message}`, 'error');
  }
}

function renderPackages() {
  const container = $('#packages-table-container');
  const fp = state.fingerprint;
  if (!fp || !fp.installed_packages) return;

  const searchVal = ($('#pkg-search').value || '').toLowerCase();
  const entries = Object.entries(fp.installed_packages)
    .filter(([name]) => name.toLowerCase().includes(searchVal))
    .sort(([a], [b]) => a.localeCompare(b));

  if (!entries.length) {
    container.innerHTML = `
      <div class="empty-state" style="padding:var(--space-8);">
        <div class="icon">🔎</div>
        <h3>No matching packages</h3>
      </div>`;
    return;
  }

  let html =
    '<table class="data-table"><colgroup><col style="width:45%"><col style="width:35%"><col style="width:20%"></colgroup><thead><tr><th>Package</th><th>Version</th><th></th></tr></thead><tbody>';
  for (const [name, version] of entries) {
    html += `<tr id="pkg-row-${esc(name)}">
      <td class="mono">${esc(name)}</td>
      <td class="mono">${esc(version)}</td>
      <td id="pkg-action-${esc(name)}">
        <button class="btn btn-danger" style="font-size:0.7rem;padding:3px 10px;" data-uninstall-pkg="${esc(name)}">🗑 Delete</button>
      </td>
    </tr>`;
  }
  html += '</tbody></table>';
  container.innerHTML = html;

  // Attach uninstall handlers
  container.querySelectorAll('[data-uninstall-pkg]').forEach((btn) => {
    btn.addEventListener('click', () => uninstallPackage(btn.dataset.uninstallPkg));
  });
}

async function uninstallPackage(pkgName) {
  if (!confirm(`Uninstall "${pkgName}"? This will run pip uninstall -y ${pkgName}.`)) {
    return;
  }

  const actionCell = $(`#pkg-action-${pkgName}`);
  if (actionCell) {
    actionCell.innerHTML = '<div class="spinner" style="width:14px;height:14px;display:inline-block;"></div> <span style="font-size:0.7rem;color:var(--text-secondary);">Removing…</span>';
  }

  try {
    await apiCall('/uninstall-package', {
      method: 'POST',
      body: JSON.stringify({ package: pkgName }),
    });

    // Remove from local state
    if (state.fingerprint && state.fingerprint.installed_packages) {
      delete state.fingerprint.installed_packages[pkgName];
    }

    // Animate row removal
    const row = $(`#pkg-row-${pkgName}`);
    if (row) {
      row.style.transition = 'opacity 0.3s, transform 0.3s';
      row.style.opacity = '0';
      row.style.transform = 'translateX(20px)';
      setTimeout(() => {
        row.remove();
        // Update stats
        const count = Object.keys(state.fingerprint?.installed_packages || {}).length;
        $('#stat-packages').textContent = count;
      }, 300);
    }

    showToast(`${pkgName} uninstalled successfully!`, 'success');
  } catch (err) {
    if (actionCell) {
      actionCell.innerHTML = `<button class="btn btn-danger" style="font-size:0.7rem;padding:3px 10px;" data-uninstall-pkg="${esc(pkgName)}">🗑 Retry</button>`;
      const retryBtn = actionCell.querySelector('[data-uninstall-pkg]');
      if (retryBtn) retryBtn.addEventListener('click', () => uninstallPackage(pkgName));
    }
    showToast(`Failed to uninstall ${pkgName}: ${err.message}`, 'error');
  }
}

// ─── Resolve ───
async function resolveImport(silent = false) {
  const name = $('#resolve-input').value.trim();
  if (!name) {
    if (!silent) showToast('Enter an import name.', 'error');
    if (silent) $('#resolve-result').innerHTML = `
      <div class="empty-state" style="padding:var(--space-8);">
        <div class="icon">🔗</div>
        <h3>Enter an import name</h3>
        <p>Type a Python import name and click Resolve to see which distribution provides it.</p>
      </div>`;
    return;
  }

  const container = $('#resolve-result');
  container.innerHTML = '<div style="text-align:center;padding:var(--space-6);"><div class="spinner"></div></div>';

  try {
    const data = await apiCall('/resolve', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });

    const candidates = data.candidates || [];
    if (!candidates.length) {
      container.innerHTML = `
        <div class="empty-state" style="padding:var(--space-6);">
          <div class="icon">❓</div>
          <h3>No distribution found</h3>
          <p>Could not resolve "${esc(name)}" to any known distribution.</p>
        </div>`;
      return;
    }

    let html = '';
    for (let i = 0; i < candidates.length; i++) {
      const c = candidates[i];
      html += `
        <div class="resolve-candidate" id="candidate-${i}" style="display:flex; justify-content:space-between; align-items:center;">
          <div>
            <span class="dist-name" style="font-weight:600; font-size:1.1rem; color:var(--text-primary); margin-right:8px;">${esc(c.distribution)}</span>
            <span class="layer" style="margin-right:8px;">${esc(c.layer)}</span>
            <span class="confidence">
              <span class="status-badge ${c.confidence === 'High' ? 'found' : c.confidence === 'Medium' ? 'not-on-path' : 'broken'}">
                ${esc(c.confidence)}
              </span>
            </span>
            <div id="pypi-info-${i}" style="margin-top:4px; font-size:0.8rem; color:var(--text-secondary);">
              <span class="spinner" style="width:10px;height:10px;display:inline-block;margin-right:4px;"></span> Fetching PyPI info...
            </div>
          </div>
          <div id="candidate-action-${i}">
            <button class="btn btn-primary" style="font-size:0.8rem; padding:6px 12px;" data-install-pip="${esc(c.distribution)}" id="btn-install-${i}">⬇ Install</button>
          </div>
        </div>`;
    }
    container.innerHTML = html;

    // Attach install handlers and fetch PyPI info
    for (let i = 0; i < candidates.length; i++) {
      const c = candidates[i];
      const btn = document.getElementById(`btn-install-${i}`);
      if (btn) {
        btn.addEventListener('click', () => installPythonPackage(c.distribution, `candidate-action-${i}`));
      }

      // Fetch PyPI info asynchronously
      fetch(`https://pypi.org/pypi/${c.distribution}/json`)
        .then(res => res.json())
        .then(data => {
          const latest = data.info.version;
          const infoEl = document.getElementById(`pypi-info-${i}`);
          if (infoEl) {
            infoEl.innerHTML = `<span style="color:var(--text-primary);">Latest version:</span> <span class="mono">${esc(latest)}</span>`;
          }
        })
        .catch(err => {
          const infoEl = document.getElementById(`pypi-info-${i}`);
          if (infoEl) {
            infoEl.innerHTML = `<span style="color:var(--accent-red);">PyPI fetch failed</span>`;
          }
        });
    }

  } catch (err) {
    showToast(err.message, 'error');
    container.innerHTML = `
      <div class="empty-state" style="padding:var(--space-6);">
        <div class="icon">❌</div>
        <h3>Error</h3>
        <p>${esc(err.message)}</p>
      </div>`;
  }
}

async function installPythonPackage(pkgName, actionContainerId) {
  const actionContainer = document.getElementById(actionContainerId);
  if (actionContainer) {
    actionContainer.innerHTML = '<div class="spinner" style="width:16px;height:16px;display:inline-block;margin-right:6px;"></div><span style="font-size:0.8rem;color:var(--text-secondary);">Installing...</span>';
  }

  try {
    const data = await apiCall('/install-pip', {
      method: 'POST',
      body: JSON.stringify({ package: pkgName }),
    });

    if (actionContainer) {
      actionContainer.innerHTML = '<span style="color:var(--accent-emerald); font-weight:bold;">✓ Installed</span>';
    }
    showToast(`${pkgName} installed successfully via pip!`, 'success');
  } catch (err) {
    if (actionContainer) {
      actionContainer.innerHTML = `<button class="btn btn-danger" style="font-size:0.8rem; padding:6px 12px;" onclick="installPythonPackage('${esc(pkgName)}', '${actionContainerId}')">✕ Retry</button>`;
    }
    showToast(`Failed to install ${pkgName}: ${err.message}`, 'error');
  }
}

// ─── JSON Viewer ───
function renderJsonViewer() {
  const source = $('#json-source').value;
  let data;
  switch (source) {
    case 'findings':
      data = state.findings;
      break;
    case 'scan':
      data = state.scan;
      break;
    case 'fingerprint':
      data = state.fingerprint;
      break;
    default:
      data = null;
  }
  $('#json-output').innerHTML = syntaxHighlight(JSON.stringify(data, null, 2));
}

function syntaxHighlight(json) {
  if (!json) return '<span class="null">null</span>';
  return json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(
      /("(\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
      (match) => {
        let cls = 'num';
        if (/^"/.test(match)) {
          cls = /:$/.test(match) ? 'key' : 'str';
        } else if (/true|false/.test(match)) {
          cls = 'bool';
        } else if (/null/.test(match)) {
          cls = 'null';
        }
        return `<span class="${cls}">${match}</span>`;
      }
    );
}

// ─── Export Fingerprint ───
function exportFingerprint() {
  if (!state.fingerprint) {
    showToast('No fingerprint to export.', 'error');
    return;
  }
  const blob = new Blob([JSON.stringify(state.fingerprint, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'env_fingerprint.json';
  a.click();
  URL.revokeObjectURL(url);
  showToast('Fingerprint exported!', 'success');
}

// ─── Copy JSON ───
function copyJson() {
  const text = JSON.stringify(
    state[$('#json-source').value] || state.findings,
    null,
    2
  );
  navigator.clipboard.writeText(text).then(() => showToast('Copied!', 'success'));
}

// ─── Helpers ───
function esc(str) {
  if (str == null) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}

// ─── Browse Folder ───
async function browseFolder() {
  try {
    const data = await apiCall('/browse', { method: 'POST' });
    if (data.path) {
      $('#project-path').value = data.path;
      showToast('Folder selected: ' + data.path, 'success');
    }
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ─── Event Bindings ───
$('#btn-browse').addEventListener('click', browseFolder);
$('#btn-analyze').addEventListener('click', analyzeProject);
$('#btn-refresh').addEventListener('click', analyzeProject);
$('#btn-resolve').addEventListener('click', () => resolveImport(false));

let resolveDebounceTimeout;
$('#resolve-input').addEventListener('input', () => {
  clearTimeout(resolveDebounceTimeout);
  resolveDebounceTimeout = setTimeout(() => {
    resolveImport(true);
  }, 500);
});

$('#btn-export-fp').addEventListener('click', exportFingerprint);
$('#btn-copy-json').addEventListener('click', copyJson);
$('#json-source').addEventListener('change', renderJsonViewer);
$('#filter-severity').addEventListener('change', renderFindings);
$('#filter-rule').addEventListener('change', renderFindings);
$('#pkg-search').addEventListener('input', renderPackages);

$('#resolve-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') resolveImport();
});

$('#project-path').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') analyzeProject();
});

// Mobile menu toggle
$('#btn-menu').addEventListener('click', () => {
  $('#sidebar').classList.toggle('open');
});

// Check responsive
function checkResponsive() {
  const isMobile = window.innerWidth <= 768;
  $('#btn-menu').style.display = isMobile ? 'flex' : 'none';
  if (!isMobile) {
    $('#sidebar').classList.remove('open');
  }
}
window.addEventListener('resize', checkResponsive);
checkResponsive();
