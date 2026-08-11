const state = {
  surface: document.body.dataset.surface || 'research',
  projectId: document.body.dataset.projectId || '',
  researchTab: new URLSearchParams(window.location.search).get('tab') || 'overview',
  libraryTab: new URLSearchParams(window.location.search).get('tab') || 'universe',
  runFilter: '',
  projects: [],
  project: null,
  projectIndex: [],
  universes: [],
  sharedUniverses: [],
  universeBindings: [],
  universeEditor: null,
  snapshots: [],
  definitions: [],
  factorDrafts: [],
  alphaDrafts: [],
  library: [],
  refs: {},
  universeRef: null,
  requirements: [],
  requirementRef: null,
  requirementItems: [],
  dataStatus: null,
  requirementRefreshError: '',
  requirementRefreshTimer: null,
  requirementPrepareBusy: false,
  requirementReconciliations: {},
  autoReviewedDataKeys: new Set(),
  pendingRequirementContext: null,
  requirementFilters: {search: '', provider: '', dataType: '', status: '', usage: ''},
  libraryGroupsByType: {},
  libraryGroupMembershipByType: {},
  libraryActiveGroup: {},
  librarySelectedAssets: new Set(),
  libraryGroupSearch: '',
  sharedEditItem: null,
  requirementEditor: null,
  grants: [],
  runs: [],
  capabilities: {factor: {}, alpha: {}},
  dataCapabilities: {providers: [], summary: {}},
  factorInputCandidates: null,
  alphaFactorCandidates: null,
  coverage: null,
  checkedPreview: null,
  runSummaries: {},
};

const $ = id => document.getElementById(id);
const arr = value => Array.isArray(value) ? value : [];
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const json = value => JSON.stringify(value ?? {}, null, 2);

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
    ...options,
  });
  const body = await response.json().catch(() => ({ok: false, error: `HTTP ${response.status}`}));
  if (!response.ok || !body.ok) {
    const error = new Error(body.error || `HTTP ${response.status}`);
    error.code = body.code || '';
    error.data = body.data || {};
    error.status = response.status;
    throw error;
  }
  return body.data;
}

function notify(message, isError = false) {
  const node = $('notice');
  node.textContent = message;
  node.className = `notice${isError ? ' error' : ''}`;
  node.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => { node.hidden = true; }, 4800);
}

function openDialog(title, eyebrow, body) {
  $('editorDialog').classList.remove('universe-dialog', 'factor-dialog');
  $('dialogTitle').textContent = title;
  $('dialogEyebrow').textContent = eyebrow;
  $('dialogBody').innerHTML = body;
  $('editorDialog').showModal();
}

function closeDialog() { $('editorDialog').classList.remove('universe-dialog', 'factor-dialog'); $('editorDialog').close(); }

function openDrawer(title, eyebrow, body) {
  $('drawerTitle').textContent = title;
  $('drawerEyebrow').textContent = eyebrow;
  $('drawerBody').innerHTML = body;
  $('detailDrawer').classList.add('open');
  $('detailDrawer').setAttribute('aria-hidden', 'false');
  $('drawerScrim').hidden = false;
}

function closeDrawer() {
  $('detailDrawer').classList.remove('open');
  $('detailDrawer').classList.remove('run-result-drawer');
  $('detailDrawer').setAttribute('aria-hidden', 'true');
  $('drawerScrim').hidden = true;
}

function formatDate(value) {
  if (!value) return 'Open';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString('en-CA');
}

function localDateValue(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toISOString().slice(0, 10);
}

function requirementInstrumentLabel(value) {
  const parts = String(value || '').split(':').filter(Boolean);
  return parts.at(-1) || String(value || '');
}

function percent(value) {
  if (value === null || value === undefined || value === '') return '-';
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : '-';
}

function friendlyStatus(value) {
  const map = {
    DRAFT: 'Research Draft', VALIDATED: 'Validated', GLOBAL: 'From Library',
    READY: 'Ready', SATISFIED: 'Ready', SUCCEEDED: 'Completed',
    CHECKING: 'Checking', QUEUED: 'Queued', PREPARING: 'Preparing', RUNNING: 'Preparing', FAILED: 'Failed',
    BLOCKED: 'Unavailable', WARNING: 'Failed', NEEDS_ATTENTION: 'Failed', PENDING: 'Checking',
    PARTIAL: 'Preparing', NOT_PREPARED: 'Preparing', UNAVAILABLE: 'Unavailable',
    HIGHER_IS_BETTER: 'Higher values are stronger', LOWER_IS_BETTER: 'Lower values are stronger',
    EVENT_SIGNAL: 'Discrete event signal',
  };
  return map[String(value || '').toUpperCase()] || String(value || 'Not Configured');
}

function statusChip(label) {
  const tone = String(label).toLowerCase().replace(/[^a-z0-9]+/g, '-');
  return `<span class="status ${esc(tone)}">${esc(label)}</span>`;
}

function componentStatus(item) {
  if (item?.origin === 'LIBRARY') return 'Current Research Use';
  return item?.state === 'DRAFT' ? 'Research Draft' : 'Validated';
}

function publishedAssetForSource(sourceObjectId) {
  return state.library.find(item => item.source_object_id === sourceObjectId) || null;
}

function latestByName(items) {
  const grouped = new Map();
  items.forEach(item => {
    const current = grouped.get(item.name);
    if (!current || String(item.version).localeCompare(String(current.version), undefined, {numeric: true}) > 0) {
      grouped.set(item.name, item);
    }
  });
  return [...grouped.values()];
}

function nextSemanticVersion(items, name, current = '') {
  const versions = items.filter(item => item.name === name).map(item => item.version);
  const source = current || versions.sort((a, b) => String(a).localeCompare(String(b), undefined, {numeric: true})).at(-1);
  if (!source) return '1.0.0';
  const parts = String(source).split('.').map(Number);
  return parts.length === 3 && parts.every(Number.isFinite) ? `${parts[0]}.${parts[1]}.${parts[2] + 1}` : '1.0.0';
}

function latestRequirement() {
  const effectiveId = state.requirementRef?.requirement_set_id || '';
  return state.requirements.find(item => item.requirement_set_id === effectiveId) || null;
}
function activeGrant() { return state.grants.find(item => item.status === 'ACTIVE') || null; }
function grantAllowsRow(item, row) {
  const provider = String(row?.provider || '').toUpperCase();
  const interval = String(row?.frequency || '').toLowerCase();
  const instrument = String(row?.instrument_id || '').toUpperCase();
  const assetClass = instrument.split(':')[0]?.toLowerCase();
  const now = Date.now();
  if (item?.status !== 'ACTIVE') return false;
  if (item.expires_at && Date.parse(item.expires_at) <= now) return false;
  const scope = item.scope || {};
  const providers = arr(scope.allowed_providers || scope.providers).map(value => String(value).toUpperCase());
  const intervals = arr(scope.allowed_intervals || scope.intervals).map(value => String(value).toLowerCase());
  const instruments = arr(scope.allowed_instrument_ids).map(value => String(value).toUpperCase());
  const assetClasses = arr(scope.asset_classes).map(value => String(value).toLowerCase());
  return (!providers.length || providers.includes(provider))
    && (!intervals.length || intervals.includes(interval))
    && (!instruments.length || instruments.includes(instrument))
    && (!assetClasses.length || assetClasses.includes(assetClass));
}
function activeGrantFor(
  row,
  requirementSetId = state.dataStatus?.requirement_set_id || '',
) {
  return state.grants.find(item =>
    grantAllowsRow(item, row)
    && (
      !requirementSetId
      || !item.scope?.requirement_set_id
      || item.scope.requirement_set_id === requirementSetId
    )
  ) || null;
}
function activeGrantForRows(
  rows,
  requirementSetId = state.dataStatus?.requirement_set_id || '',
) {
  const scopedRows = arr(rows).filter(row => row?.instrument_id);
  return state.grants.find(item =>
    scopedRows.every(row => grantAllowsRow(item, row))
    && (
      !requirementSetId
      || !item.scope?.requirement_set_id
      || item.scope.requirement_set_id === requirementSetId
    )
  ) || null;
}
function researchRefs(type) { return Object.values(state.refs).filter(item => item.definition_type === type); }
function projectFactorDrafts() {
  return state.factorDrafts.filter(item => item.owner_project_id === state.projectId && item.state === 'DRAFT');
}
function projectAlphaDrafts() {
  return state.alphaDrafts.filter(item => item.owner_project_id === state.projectId && item.state === 'DRAFT');
}
function latestSnapshotForUniverse(id) { return state.snapshots.find(item => item.universe_definition_id === id) || null; }
function primaryUniverseBinding() { return state.universeBindings.find(item => item.role === 'PRIMARY') || state.universeBindings[0] || null; }
function currentSnapshot() {
  const sharedSnapshotId = primaryUniverseBinding()?.current_resolution?.legacy_snapshot_id;
  if (sharedSnapshotId) {
    const sharedSnapshot = state.snapshots.find(item => item.universe_snapshot_id === sharedSnapshotId);
    if (sharedSnapshot) return sharedSnapshot;
  }
  if (state.universeRef?.universe_snapshot_id) {
    return state.snapshots.find(item => item.universe_snapshot_id === state.universeRef.universe_snapshot_id) || null;
  }
  const required = latestRequirement()?.context?.universe_snapshot_id;
  if (required) return state.snapshots.find(item => item.universe_snapshot_id === required) || null;
  const owned = new Set(state.universes.filter(item => item.owner_project_id === state.projectId).map(item => item.universe_definition_id));
  return state.snapshots.find(item => owned.has(item.universe_definition_id)) || null;
}
function currentUniverse() {
  const shared = primaryUniverseBinding();
  if (shared) return shared;
  const snapshot = currentSnapshot();
  return snapshot ? state.universes.find(item => item.universe_definition_id === snapshot.universe_definition_id) || null : null;
}

function setActiveNavigation() {
  const active = state.surface === 'research-detail' ? 'research' : state.surface;
  document.querySelectorAll('[data-nav]').forEach(node => node.classList.toggle('active', node.dataset.nav === active));
}

async function loadBase() {
  setActiveNavigation();
  const [projects, universes, sharedUniverses, definitions, factorDrafts, alphaDrafts, library, runs, capabilities, dataCapabilities] = await Promise.all([
    api('/api/research/projects'),
    api('/api/research/universes?status='),
    api('/api/library/universes'),
    api('/api/research/definitions'),
    api('/api/research/factor-drafts'),
    api('/api/research/alpha-drafts'),
    api('/api/research/library'),
    api('/api/research/runs'),
    api('/api/research/engine-capabilities'),
    api('/api/research/data/capabilities'),
  ]);
  state.projects = projects;
  state.universes = universes;
  state.sharedUniverses = sharedUniverses;
  state.definitions = definitions;
  state.library = library;
  state.runs = runs;
  state.capabilities = capabilities;
  state.dataCapabilities = dataCapabilities;
  const snapshotGroups = await Promise.all(universes.map(item =>
    api(`/api/research/universes/${encodeURIComponent(item.universe_definition_id)}/snapshots`).catch(() => [])
  ));
  state.snapshots = snapshotGroups.flat();
  state.factorDrafts = factorDrafts;
  state.alphaDrafts = alphaDrafts;

  if (state.surface === 'research') await renderResearchIndex();
  else if (state.surface === 'research-detail') await loadResearch(state.projectId);
  else if (state.surface === 'library') await renderLibrary();
  else if (state.surface === 'runs') renderGlobalRuns();
  else if (state.surface === 'data-catalog') await renderDataCatalog();
  else if (state.surface === 'approvals') await renderApprovals();
}

async function loadResearch(projectId) {
  const automaticFactorAssets = await api(
    `/api/research/projects/${encodeURIComponent(projectId)}/factors/sync-library`,
    {method: 'POST', body: '{}'},
  );
  automaticFactorAssets.forEach(asset => {
    const index = state.library.findIndex(item => item.library_asset_id === asset.library_asset_id);
    if (index >= 0) state.library[index] = asset;
    else state.library.unshift(asset);
  });
  // Rebuild the single canonical set from the current Universe plus pinned,
  // validated Factor/Alpha references and explicit manual Requirements.
  let effectiveRefreshError = '';
  try {
    await api(`/api/research/projects/${encodeURIComponent(projectId)}/requirements/refresh`, {
      method: 'POST',
      body: '{}',
    });
  } catch (error) {
    effectiveRefreshError = error.message;
  }
  const [detail, refs, universeRef, universeBindings, requirements, requirementRef, grants, requirementItems, dataStatus, factorDrafts, factorInputCandidates, alphaDrafts, alphaFactorCandidates] = await Promise.all([
    api(`/api/research/projects/${encodeURIComponent(projectId)}`),
    api(`/api/research/projects/${encodeURIComponent(projectId)}/definition-refs`),
    api(`/api/research/projects/${encodeURIComponent(projectId)}/universe-ref`),
    api(`/api/research/projects/${encodeURIComponent(projectId)}/universes`),
    api(`/api/research/data/requirement-sets?project_id=${encodeURIComponent(projectId)}`),
    api(`/api/research/projects/${encodeURIComponent(projectId)}/requirements/ref`),
    api(`/api/research/projects/${encodeURIComponent(projectId)}/grants`),
    api(`/api/research/projects/${encodeURIComponent(projectId)}/requirements/items`).catch(() => []),
    api(`/api/research/projects/${encodeURIComponent(projectId)}/data-status`).catch(() => null),
    api(`/api/research/factor-drafts?owner_project_id=${encodeURIComponent(projectId)}`).catch(() => []),
    api(`/api/research/projects/${encodeURIComponent(projectId)}/factor-input-candidates`).catch(() => null),
    api(`/api/research/alpha-drafts?owner_project_id=${encodeURIComponent(projectId)}`).catch(() => []),
    api(`/api/research/projects/${encodeURIComponent(projectId)}/alpha-factor-candidates`).catch(() => null),
  ]);
  state.projectId = projectId;
  state.project = detail.project;
  state.refs = refs;
  state.universeRef = universeRef;
  state.universeBindings = universeBindings;
  state.requirements = requirements;
  state.requirementRef = requirementRef;
  state.grants = grants;
  state.requirementItems = requirementItems;
  state.dataStatus = dataStatus;
  state.factorInputCandidates = factorInputCandidates;
  state.alphaFactorCandidates = alphaFactorCandidates;
  state.factorDrafts = [
    ...state.factorDrafts.filter(item => item.owner_project_id !== projectId),
    ...factorDrafts,
  ];
  state.alphaDrafts = [
    ...state.alphaDrafts.filter(item => item.owner_project_id !== projectId),
    ...alphaDrafts,
  ];
  state.requirementReconciliations = {};
  state.requirementRefreshError = effectiveRefreshError;
  const staleBindings = universeBindings.filter(item => item.requirements_stale_at);
  if (staleBindings.length) {
    const reconciliationRows = [];
    for (const item of staleBindings) {
      try {
        reconciliationRows.push(await api(`/api/research/projects/${encodeURIComponent(projectId)}/requirements/reconcile`, {
          method: 'POST',
          body: JSON.stringify({universe_id: item.universe_id}),
        }));
      } catch (error) {
        reconciliationRows.push({status: 'ATTENTION', universe_id: item.universe_id, message: error.message, reasons: [error.message], changes: {added: [], removed: []}});
      }
    }
    state.requirementReconciliations = Object.fromEntries(reconciliationRows.map(item => [item.universe_id, item]));
    if (reconciliationRows.some(item => item.auto_updated)) {
      const [freshBindings, freshRequirements, freshRequirementRef, freshItems, freshStatus] = await Promise.all([
        api(`/api/research/projects/${encodeURIComponent(projectId)}/universes`),
        api(`/api/research/data/requirement-sets?project_id=${encodeURIComponent(projectId)}`),
        api(`/api/research/projects/${encodeURIComponent(projectId)}/requirements/ref`),
        api(`/api/research/projects/${encodeURIComponent(projectId)}/requirements/items`).catch(() => []),
        api(`/api/research/projects/${encodeURIComponent(projectId)}/data-status`).catch(() => null),
      ]);
      state.universeBindings = freshBindings;
      state.requirements = freshRequirements;
      state.requirementRef = freshRequirementRef;
      state.requirementItems = freshItems;
      state.dataStatus = freshStatus;
    }
  }
  const effectiveRequirement = latestRequirement();
  state.coverage = effectiveRequirement
    ? await api(`/api/research/data/requirement-sets/${encodeURIComponent(effectiveRequirement.requirement_set_id)}/coverage`).catch(() => null)
    : null;
  state.checkedPreview = null;
  renderResearchWorkspace();
  if (state.researchTab === 'data') {
    scheduleRequirementRefresh();
  }
}

async function researchSummary(project) {
  await api(`/api/research/projects/${encodeURIComponent(project.project_id)}/requirements/refresh`, {
    method: 'POST',
    body: '{}',
  }).catch(() => null);
  const [refs, universeRef, requirements, requirementRef, dataStatus] = await Promise.all([
    api(`/api/research/projects/${encodeURIComponent(project.project_id)}/definition-refs`).catch(() => ({})),
    api(`/api/research/projects/${encodeURIComponent(project.project_id)}/universe-ref`).catch(() => null),
    api(`/api/research/data/requirement-sets?project_id=${encodeURIComponent(project.project_id)}`).catch(() => []),
    api(`/api/research/projects/${encodeURIComponent(project.project_id)}/requirements/ref`).catch(() => null),
    api(`/api/research/projects/${encodeURIComponent(project.project_id)}/data-status`).catch(() => null),
  ]);
  const values = Object.values(refs);
  let coverage = null;
  const effectiveRequirement = requirements.find(
    item => item.requirement_set_id === requirementRef?.requirement_set_id
  );
  if (effectiveRequirement) {
    coverage = await api(`/api/research/data/requirement-sets/${encodeURIComponent(effectiveRequirement.requirement_set_id)}/coverage`).catch(() => null);
  }
  return {project, refs, universeRef, requirements, requirementRef, dataStatus, coverage, factors: values.filter(item => item.definition_type === 'FACTOR'), alphas: values.filter(item => item.definition_type === 'ALPHA')};
}

function summaryDataLabel(item) {
  if (!item.requirements.length) return 'Not configured';
  if (item.dataStatus) {
    return arr(item.dataStatus.rows).length && arr(item.dataStatus.rows).every(row => row.status === 'READY')
      ? 'Ready'
      : 'Missing data';
  }
  if (!item.coverage) return 'Not checked';
  return arr(item.coverage.checks).some(check => !check.satisfied) ? 'Missing data' : 'Ready';
}

async function renderResearchIndex() {
  state.projectIndex = await Promise.all(state.projects.map(researchSummary));
  $('appView').innerHTML = `<header class="page-heading"><div><span class="eyebrow">RESEARCH</span><h1>Research</h1><p>Create, combine, validate, and test a research idea.</p></div><button type="button" class="primary" data-action="new-research">New Research</button></header>
    <div class="research-grid">${state.projectIndex.map(item => {
      const universe = item.universeRef?.name || 'Not configured';
      return `<article class="research-card"><h2>${esc(item.project.title)}</h2><p>${esc(item.project.objective)}</p><div class="research-summary">
        <div class="summary-line"><span>Universe</span><strong>${esc(universe)}</strong></div>
        <div class="summary-line"><span>Factor</span><strong>${esc(item.factors[0]?.name || 'Not configured')}</strong></div>
        <div class="summary-line"><span>Alpha</span><strong>${esc(item.alphas[0]?.name || 'Not configured')}</strong></div>
        <div class="summary-line"><span>Data</span><strong>${esc(summaryDataLabel(item))}</strong></div>
        <div class="summary-line"><span>Strategy</span><strong>Not configured</strong></div>
      </div><footer><a class="button-link primary" href="/research/${encodeURIComponent(item.project.project_id)}">Open Research</a></footer></article>`;
    }).join('') || `<div class="empty-state"><h2>No Research yet</h2><p>Start with one hypothesis. Components can be published to Library after validation.</p><button type="button" class="primary" data-action="new-research">New Research</button></div>`}</div>`;
}

function renderResearchWorkspace() {
  if (!state.project) return;
  $('appView').innerHTML = `<a class="back-link" href="/research">&larr; All Research</a>
    <header class="research-heading"><div><span class="eyebrow">RESEARCH</span><h1>${esc(state.project.title)}</h1><p>${esc(state.project.objective)}</p></div><span class="safety-note">Research only · No trading</span></header>
    <nav class="research-tabs" aria-label="Research navigation">
      ${['overview','universe','factor','alpha','data','strategy','runs'].map(tab => `<button type="button" data-research-tab="${tab}" class="${state.researchTab === tab ? 'active' : ''}">${tab.charAt(0).toUpperCase() + tab.slice(1)}</button>`).join('')}
    </nav><section id="researchTabContent" class="tab-content"></section>`;
  renderResearchTab();
}

function switchResearchTab(tab) {
  state.researchTab = tab;
  document.querySelectorAll('[data-research-tab]').forEach(node => node.classList.toggle('active', node.dataset.researchTab === tab));
  renderResearchTab();
  if (tab === 'data') {
    scheduleRequirementRefresh();
  } else {
    clearTimeout(state.requirementRefreshTimer);
  }
}

function renderResearchTab() {
  const renderers = {overview: renderOverview, universe: renderUniverse, factor: renderFactors, alpha: renderAlphas, data: renderResearchData, strategy: renderStrategy, runs: renderResearchRuns};
  renderers[state.researchTab]?.();
}

function dataSummary() {
  if (!latestRequirement()) return {label: 'Not Configured', detail: 'Define Requirements: fields, frequency, instruments, and history range.'};
  if (state.dataStatus) {
    const rows = arr(state.dataStatus.rows);
    const pending = rows.filter(item => item.status !== 'READY');
    if (!pending.length) return {label: 'Ready', detail: 'Available data satisfies the current Effective RequirementSet.'};
    const actionable = pending.filter(item => item.can_prepare || ['CHECKING', 'QUEUED', 'PREPARING'].includes(item.status));
    if (actionable.length === pending.length) {
      return {label: 'Preparing', detail: `${pending.length} data contract${pending.length === 1 ? ' is' : 's are'} being completed automatically.`};
    }
    return {label: 'Needs Attention', detail: `${pending.length - actionable.length} data contract${pending.length - actionable.length === 1 ? '' : 's'} cannot be completed automatically.`};
  }
  if (!state.coverage) return {label: 'Not Checked', detail: 'Requirements are configured; data availability has not been checked.'};
  const missing = arr(state.coverage.checks).filter(item => !item.satisfied).length;
  return missing ? {label: 'Missing', detail: `${missing} required data item${missing === 1 ? '' : 's'} need attention.`} : {label: 'Ready', detail: 'Available data satisfies the current Requirements.'};
}

function overviewRow(label, title, detail, tab, status) {
  return `<button type="button" class="overview-row" data-action="go-research-tab" data-target="${esc(tab)}"><span>${esc(label)}</span><div><strong>${esc(title)}</strong><small>${esc(detail)}</small></div>${statusChip(status)}</button>`;
}

function universeMarketLabel(instrumentIds = []) {
  const scopes = [...new Set(arr(instrumentIds).map(value => {
    const parts = String(value || '').split(':');
    return parts.length >= 2 ? `${parts[0].toLowerCase()}:${parts[1].toUpperCase()}` : '';
  }).filter(Boolean))];
  if (!scopes.length) return 'Instrument';
  if (scopes.length > 1) return 'Mixed Instruments';
  const [assetClass, venue] = scopes[0].split(':');
  const assetLabel = {
    crypto_spot: 'Crypto Spot',
    crypto_derivative: 'Crypto Derivative',
    equity: 'Equity',
    polymarket_binary: 'Prediction Market',
    macro: 'Macro',
  }[assetClass] || assetClass.replaceAll('_', ' ').replace(/\b\w/g, value => value.toUpperCase());
  const venueLabel = {BINANCE: 'Binance', POLYMARKET: 'Polymarket', FRED: 'FRED'}[venue] || venue;
  return `${assetLabel} · ${venueLabel}`;
}

function nextStep() {
  if (!currentSnapshot()) return {label: 'Choose a Universe', tab: 'universe'};
  if (projectFactorDrafts().length) return {label: 'Complete and validate the Factor draft', tab: 'factor'};
  if (!researchRefs('FACTOR').length) return {label: 'Add a Factor', tab: 'factor'};
  if (researchRefs('FACTOR').some(item => item.state === 'DRAFT')) return {label: 'Validate the Factor', tab: 'factor'};
  if (!researchRefs('ALPHA').length) return {label: 'Add an Alpha', tab: 'alpha'};
  if (researchRefs('ALPHA').some(item => item.state === 'DRAFT')) return {label: 'Validate the Alpha', tab: 'alpha'};
  const data = dataSummary();
  if (data.label !== 'Ready') return {label: data.label === 'Not Configured' ? 'Configure Requirements' : 'View Data Status', tab: 'data'};
  return {label: 'Run an Alpha Test', tab: 'runs'};
}

function renderOverview() {
  const universe = currentUniverse();
  const snapshot = currentSnapshot();
  const factors = researchRefs('FACTOR');
  const alphas = researchRefs('ALPHA');
  const data = dataSummary();
  const next = nextStep();
  $('researchTabContent').innerHTML = `<article class="panel"><div class="panel-head"><div><span class="eyebrow">CURRENT RESEARCH</span><h2>Overview</h2><p>The components currently combined in this Research.</p></div></div><div class="overview-list">
    ${overviewRow('Universe', universe?.name || 'Not Configured', snapshot ? `${arr(snapshot.actual_instrument_ids).length} instruments · ${universeMarketLabel(snapshot.actual_instrument_ids)}` : 'Add from Library or create in Research.', 'universe', universe ? componentStatus(state.universeRef || universe) : 'Not Configured')}
    ${overviewRow('Factor', factors[0]?.name || 'Not Configured', factors.length ? `${factors.length} Factor${factors.length === 1 ? '' : 's'}` : 'Add from Library or create in Research.', 'factor', factors[0] ? componentStatus(factors[0]) : 'Not Configured')}
    ${overviewRow('Alpha', alphas[0]?.name || 'Not Configured', alphas.length ? `${alphas.length} Alpha${alphas.length === 1 ? '' : 's'}` : 'Combine validated Factors.', 'alpha', alphas[0] ? componentStatus(alphas[0]) : 'Not Configured')}
    ${overviewRow('Data', data.label, data.detail, 'data', data.label)}
    ${overviewRow('Strategy', 'Not Configured', 'Strategy will be connected after the research workflow is complete.', 'strategy', 'Not Configured')}
  </div><div class="next-step"><div><span>NEXT STEP</span><strong>${esc(next.label)}</strong></div><button type="button" class="primary" data-action="go-research-tab" data-target="${esc(next.tab)}">Continue</button></div></article>`;
}

function factorFormula(spec = {}) {
  const input = spec.formula?.input || spec.input_field || 'close';
  const operator = spec.formula?.operator || spec.operator || 'factor';
  const window = spec.formula?.window || spec.window;
  const parameters = spec.formula?.parameters || spec.parameters || {};
  if (operator === 'ma_crossover') return `ma_crossover(${input}, ${parameters.fast_window || 5}, ${window || 20})`;
  return `${operator}(${[input, window].filter(value => value !== undefined).join(', ')})`;
}

function factorOutput(spec = {}) {
  if (spec.output_direction === 'EVENT_SIGNAL' || spec.output_unit === 'DISCRETE') return 'Long / Short Signal';
  return spec.output_unit === 'RATIO' ? 'Continuous Value · Ratio' : 'Continuous Value';
}

function factorDraftFormula(document = {}) {
  if (document.formula?.source) return document.formula.source;
  const input = document.inputs?.[0]?.variable_name || document.input?.variable_name || document.input?.field || 'input';
  const operator = document.formula?.operator || 'formula';
  const parameters = arr(document.parameters).length
    ? document.parameters.map(item => item.name)
    : Object.keys(document.formula?.parameters || {});
  return `${operator}(${[input, ...parameters].filter(Boolean).join(', ')})`;
}

function factorDraftCard(draft) {
  const document = draft.document || {};
  const identity = document.identity || {};
  const input = document.inputs?.[0] || document.input || {};
  const output = document.output || {};
  return `<article class="component-card factor-draft-card"><div class="card-header"><div><h3>${esc(identity.name || 'Untitled Factor')}</h3><small>Changes backed up · ${esc(formatDate(draft.updated_at))}</small></div>${statusChip('In progress')}</div><div class="fact-grid">
    <div class="fact-block"><span>Input</span><strong>${esc(input.frequency || 'Not set')} Bars · ${esc(input.field || 'Field not set')}</strong></div>
    <div class="fact-block"><span>Formula</span><code>${esc(factorDraftFormula(document))}</code></div>
    <div class="fact-block"><span>Output</span><strong>${factorDraftFormula(document).startsWith('ma_crossover(') ? 'Discrete numeric event' : 'Numeric Factor value'}</strong></div>
  </div><div class="draft-contract-note"><span>Saved work</span><small>You can continue editing without losing the current content.</small></div><div class="card-actions"><button type="button" class="danger-quiet" data-action="discard-factor-draft" data-id="${esc(draft.draft_id)}">Discard Draft</button><button type="button" class="primary" data-action="edit-factor-draft" data-id="${esc(draft.draft_id)}">Continue editing</button></div></article>`;
}

function alphaDraftFormula(document = {}) {
  const candidates = arr(state.alphaFactorCandidates?.factors);
  const components = arr(document.components);
  if (!components.length) return 'No Factor input';
  return components.map(component => {
    const factor = candidates.find(item =>
      item.definition_id === component.factor_definition_id
      && item.version === component.factor_version
    );
    const name = factor?.name || component.factor_name || component.variable_name || 'Factor';
    const transform = component.transform || 'CS_RANK';
    const value = transform === 'RAW'
      ? name
      : `${transform}(${name}, ${component.ascending === false ? 'low→high' : 'high→high'})`;
    return `${Number(component.weight ?? 1)} × ${value}`;
  }).join(' + ');
}

function alphaDraftCard(draft) {
  const document = draft.document || {};
  const identity = document.identity || {};
  return `<article class="component-card factor-draft-card"><div class="card-header"><div><h3>${esc(identity.name || 'Untitled Alpha')}</h3><small>Changes backed up · ${esc(formatDate(draft.updated_at))}</small></div>${statusChip('In progress')}</div><div class="fact-grid">
    <div class="fact-block"><span>Input</span><strong>${esc(arr(document.components).length)} pinned Factor${arr(document.components).length === 1 ? '' : 's'}</strong></div>
    <div class="fact-block"><span>Formula</span><code>${esc(alphaDraftFormula(document))}</code></div>
    <div class="fact-block"><span>Output</span><strong>Prediction Score</strong></div>
  </div><div class="draft-contract-note"><span>Saved work</span><small>Preview is required before this Alpha can be validated.</small></div><div class="card-actions"><button type="button" class="danger-quiet" data-action="discard-alpha-draft" data-id="${esc(draft.draft_id)}">Discard Draft</button><button type="button" class="primary" data-action="edit-alpha-draft" data-id="${esc(draft.draft_id)}">Continue editing</button></div></article>`;
}

function alphaFormula(item) {
  const components = arr(item?.spec?.components);
  if (!components.length) return 'No Factor input';
  return components.map(component => {
    const factor = state.definitions.find(definition => definition.definition_id === component.factor_definition_id);
    const name = factor?.name || component.factor_name || 'Factor';
    const transformed = component.transform && component.transform !== 'RAW' ? `${component.transform}(${name})` : name;
    return `${Number(component.weight ?? 1)} × ${transformed}`;
  }).join(' + ');
}

function researchRemovalAction(item, type) {
  if (type === 'factor') return `<button type="button" class="danger-quiet" data-action="remove-research-factor" data-id="${esc(item.definition_id)}" data-slot="${esc(item.slot_key)}">Remove from Research</button>`;
  if (type === 'alpha') return `<button type="button" class="danger-quiet" data-action="remove-research-alpha" data-id="${esc(item.definition_id)}" data-slot="${esc(item.slot_key)}">Remove from Research</button>`;
  return '';
}

function componentActions(item, type) {
  const actions = [];
  if (item.origin === 'LIBRARY') {
    actions.push(`<button type="button" data-action="definition-details" data-id="${esc(item.definition_id)}">View</button>`);
    actions.push(`<button type="button" data-action="add-library-definition" data-kind="${esc(item.definition_type)}">Replace</button>`);
    actions.push(`<button type="button" class="primary" data-action="copy-library-definition" data-id="${esc(item.definition_id)}">Copy and Edit</button>`);
    if (type === 'factor' || type === 'alpha') actions.push(researchRemovalAction(item, type));
    return actions.join('');
  }
  actions.push(`<button type="button" data-action="edit-local-${type.toLowerCase()}" data-id="${esc(item.definition_id)}">Edit</button>`);
  if (item.state === 'DRAFT' && type !== 'FACTOR') {
    actions.push(`<button type="button" class="primary" data-action="validate-definition" data-id="${esc(item.definition_id)}">Validate</button>`);
  } else if (item.state === 'DRAFT' && type === 'FACTOR') {
    actions.push('<button type="button" disabled>Complete in Factor editor</button>');
  }
  if (item.state === 'VALIDATED' && type !== 'factor' && !publishedAssetForSource(item.definition_id)) {
    actions.push(`<button type="button" class="primary" data-action="publish-definition" data-id="${esc(item.definition_id)}">Publish to Library</button>`);
  }
  if (item.state === 'VALIDATED') {
    actions.push(`<button type="button" data-action="test-${type.toLowerCase()}">Test</button>`);
  }
  actions.push(`<button type="button" data-action="definition-details" data-id="${esc(item.definition_id)}">Details</button>`);
  if (type === 'factor' || type === 'alpha') actions.push(researchRemovalAction(item, type));
  return actions.join('');
}

function factorCard(item, library = false, usage = 0) {
  const spec = item.spec || {};
  const footer = library
    ? `<div class="usage">Usage <strong>${usage} Research</strong></div><div class="card-actions"><button type="button" class="primary" data-action="library-use" data-kind="FACTOR" data-id="${esc(item.library_asset_id)}">Use in Research</button><button type="button" data-action="library-new-version" data-kind="FACTOR" data-id="${esc(item.library_asset_id)}">Create New Version</button><button type="button" data-action="view-usage" data-kind="FACTOR" data-id="${esc(item.library_asset_id)}">View Usage</button><button type="button" data-action="definition-details" data-id="${esc(item.definition_id)}">Details</button><button type="button" class="danger-quiet" data-action="archive-library-definition" data-id="${esc(item.library_asset_id)}" data-name="${esc(item.name)}">Archive</button></div>`
    : `${publishedAssetForSource(item.definition_id) ? `<div class="usage">Available in <strong>Library v${publishedAssetForSource(item.definition_id).version}</strong></div>` : ''}<div class="card-actions">${componentActions(item, 'factor')}</div>`;
  const source = item.origin === 'LIBRARY' ? `Source: Library · v${item.library_version}` : 'Source: Current Research';
  return `<article class="component-card"><div class="card-header"><div><h3>${esc(item.name)}</h3><small>${library ? `Library · v${item.version}` : source}</small></div>${statusChip(library ? 'Published' : componentStatus(item))}</div><div class="fact-grid">
    <div class="fact-block"><span>Input</span><strong>${esc(spec.frequency || '1h')} Bars · ${esc(spec.formula?.input || spec.input_field || 'close')}</strong></div>
    <div class="fact-block"><span>Formula</span><code>${esc(factorFormula(spec))}</code></div>
    <div class="fact-block"><span>Output</span><strong>${esc(factorOutput(spec))}</strong></div>
  </div>${footer}</article>`;
}

function alphaCard(item, library = false, usage = 0) {
  const inputs = arr(item.spec?.components).map(component => state.definitions.find(definition => definition.definition_id === component.factor_definition_id)?.name || component.factor_name || 'Factor').join(', ');
  const footer = library
    ? `<div class="usage">Usage <strong>${usage} Research</strong></div><div class="card-actions"><button type="button" class="primary" data-action="library-use" data-kind="ALPHA" data-id="${esc(item.library_asset_id)}">Use in Research</button><button type="button" data-action="library-new-version" data-kind="ALPHA" data-id="${esc(item.library_asset_id)}">Create New Version</button><button type="button" data-action="view-usage" data-kind="ALPHA" data-id="${esc(item.library_asset_id)}">View Usage</button><button type="button" data-action="definition-details" data-id="${esc(item.definition_id)}">Details</button><button type="button" class="danger-quiet" data-action="archive-library-definition" data-id="${esc(item.library_asset_id)}" data-name="${esc(item.name)}">Archive</button></div>`
    : `${publishedAssetForSource(item.definition_id) ? `<div class="usage">Published as <strong>Library v${publishedAssetForSource(item.definition_id).version}</strong></div>` : ''}<div class="card-actions">${componentActions(item, 'alpha')}</div>`;
  const source = item.origin === 'LIBRARY' ? `Source: Library · v${item.library_version}` : 'Source: Current Research';
  return `<article class="component-card"><div class="card-header"><div><h3>${esc(item.name)}</h3><small>${library ? `Library · v${item.version}` : source}</small></div>${statusChip(library ? 'Published' : componentStatus(item))}</div><div class="fact-grid">
    <div class="fact-block"><span>Input</span><strong>${esc(inputs || 'No Factor input')}</strong></div>
    <div class="fact-block"><span>Formula</span><code>${esc(alphaFormula(item))}</code></div>
    <div class="fact-block"><span>Output</span><strong>Prediction Score</strong></div>
  </div>${footer}</article>`;
}

function renderUniverseLegacy() {
  const universe = currentUniverse();
  const snapshot = currentSnapshot();
  if (!universe || !snapshot) {
    $('researchTabContent').innerHTML = `<div class="empty-state"><h2>No Universe selected</h2><p>Add a published Universe from Library, or create one inside this Research.</p><div class="button-row"><button type="button" data-action="add-library-universe">Add from Library</button><button type="button" class="primary" data-action="new-universe">Create in Research</button></div></div>`;
    return;
  }
  const members = arr(snapshot.actual_instrument_ids);
  const fromLibrary = state.universeRef?.origin === 'LIBRARY';
  const published = publishedAssetForSource(universe.universe_definition_id);
  const localActions = `<button type="button" data-action="edit-local-universe" data-id="${esc(universe.universe_definition_id)}">Edit</button>${!published ? `<button type="button" class="primary" data-action="publish-universe" data-id="${esc(universe.universe_definition_id)}">Publish to Library</button>` : ''}<button type="button" data-action="universe-details" data-id="${esc(universe.universe_definition_id)}">Details</button><button type="button" class="danger-quiet" data-action="remove-legacy-universe">Remove from Research</button>`;
  const libraryActions = `<button type="button" data-action="universe-details" data-id="${esc(universe.universe_definition_id)}">View</button><button type="button" data-action="add-library-universe">Replace</button><button type="button" class="primary" data-action="copy-library-universe" data-id="${esc(universe.universe_definition_id)}">Copy and Edit</button><button type="button" class="danger-quiet" data-action="remove-legacy-universe">Remove from Research</button>`;
  const source = fromLibrary ? `Source: Library · v${state.universeRef.library_version}` : 'Source: Current Research';
  $('researchTabContent').innerHTML = `<article class="component-card"><div class="card-header"><div><h3>${esc(universe.name)}</h3><small>${esc(source)} · ${esc(universeMarketLabel(members))} · ${members.length} Instrument${members.length === 1 ? '' : 's'}</small></div>${statusChip(fromLibrary ? 'Current Research Use' : 'Validated')}</div><div class="member-tags">${members.map(item => `<span>${esc(item.split(':').pop())}</span>`).join('')}</div>${published && !fromLibrary ? `<div class="usage">Published as <strong>Library v${published.version}</strong></div>` : ''}<div class="card-actions">${fromLibrary ? libraryActions : localActions}</div></article>`;
}

function universeRequirementState(universe) {
  const reconciliation = state.requirementReconciliations[universe.universe_id];
  const requirement = state.requirementItems.find(item => {
    const target = item.spec?.target || {};
    return target.scope === 'SPECIFIC_UNIVERSE' && target.universe_id === universe.universe_id;
  });
  if (reconciliation?.status === 'ATTENTION') return {kind: 'attention', requirement, reconciliation};
  if (!requirement) return {kind: 'required', requirement: null, reconciliation};
  if (reconciliation?.status === 'PREPARING') return {kind: 'preparing', requirement, reconciliation};
  if (reconciliation?.status === 'READY') return {kind: 'ready', requirement, reconciliation};
  const members = new Set(arr(universe.current_resolution?.instrument_ids));
  const rows = arr(state.dataStatus?.rows).filter(row => members.has(row.instrument_id));
  const statuses = new Set(rows.map(row => row.status));
  if (statuses.has('UNAVAILABLE') || statuses.has('FAILED')) return {kind: 'attention', requirement, reconciliation};
  if (rows.length && [...statuses].every(status => status === 'READY')) return {kind: 'ready', requirement, reconciliation};
  return {kind: 'preparing', requirement, reconciliation};
}

function universeRequirementBanner(universe, dataState) {
  const changes = dataState.reconciliation?.changes || {added: [], removed: []};
  const newCount = arr(changes.added).length;
  if (dataState.kind === 'required') {
    return `<div class="universe-data-state required"><span class="eyebrow">DATA REQUIRED</span><strong>No data requirement is configured for this Universe.</strong><p>Create one before the next Preview or Run.</p><div><button type="button" class="primary" data-action="create-universe-requirement" data-id="${esc(universe.universe_id)}">Create Data Requirement</button></div></div>`;
  }
  if (dataState.kind === 'preparing') {
    const coverage = Number(dataState.reconciliation?.coverage_percent ?? 0);
    return `<div class="universe-data-state preparing"><span class="eyebrow">DATA PREPARING</span><strong>${dataState.reconciliation?.auto_updated ? 'Data requirements were updated automatically.' : 'Data preparation is in progress.'}</strong><p>Coverage ${esc(coverage)}% · Loading missing data</p><div><button type="button" data-action="view-data-progress">View Progress</button></div></div>`;
  }
  if (dataState.kind === 'attention') {
    const message = newCount
      ? `The current data configuration does not support ${esc(newCount)} newly added Instrument${newCount === 1 ? '' : 's'}.`
      : 'The current data configuration needs an update for this Universe.';
    return `<div class="universe-data-state attention"><span class="eyebrow">DATA NEEDS ATTENTION</span><strong>${message}</strong><p>Existing Runs will not change.</p><div class="button-row"><button type="button" data-action="view-universe-requirement-changes" data-id="${esc(universe.universe_id)}">View Changes</button><button type="button" class="primary" data-action="review-universe-requirement" data-id="${esc(universe.universe_id)}">Review &amp; Update</button></div></div>`;
  }
  return '';
}

function renderUniverse() {
  const bindings = state.universeBindings;
  if (!bindings.length) {
    if (currentUniverse() && currentSnapshot()) {
      renderUniverseLegacy();
      return;
    }
    $('researchTabContent').innerHTML = `<div class="empty-state"><h2>No Universe selected</h2><p>Add a shared Universe from Library, or create one for this Research.</p><div class="button-row"><button type="button" data-action="add-library-universe">Add from Library</button><button type="button" class="primary" data-action="new-universe">Create Universe</button></div></div>`;
    return;
  }
  const cards = bindings.map(item => {
    const resolution = item.current_resolution || {};
    const members = arr(resolution.instrument_ids);
    const dataState = universeRequirementState(item);
    return `<article class="component-card universe-shared-card"><div class="card-header"><div><h3>${esc(item.name)}</h3><small>Shared Universe · revision ${esc(item.revision_number)} · ${esc(item.type)}</small></div><div>${item.role === 'PRIMARY' ? statusChip('Primary') : statusChip('Reference')}${dataState.kind === 'ready' ? statusChip('Data Ready') : ''}</div></div><div class="fact-grid"><div class="fact-block"><span>Resolved</span><strong>${esc(resolution.member_count || 0)} Instruments${resolution.combination_count ? ` · ${esc(resolution.combination_count)} combinations` : ''}</strong></div><div class="fact-block"><span>Used by</span><strong>${esc(item.active_research_count || 0)} active Research</strong></div><div class="fact-block"><span>Updated</span><strong>${esc(formatDate(item.updated_at))}</strong></div></div><div class="member-tags">${members.slice(0, 12).map(value => `<span>${esc(value.split(':').pop())}</span>`).join('')}${members.length > 12 ? `<span>+${members.length - 12}</span>` : ''}</div>${universeRequirementBanner(item, dataState)}<div class="card-actions"><button type="button" class="primary" data-action="edit-shared-universe" data-id="${esc(item.universe_id)}">Edit</button><button type="button" data-action="copy-shared-universe" data-id="${esc(item.universe_id)}">Copy</button><button type="button" data-action="preview-shared-universe" data-id="${esc(item.universe_id)}">Preview</button><button type="button" data-action="shared-universe-details" data-id="${esc(item.universe_id)}">Details</button><details class="overflow-menu"><summary aria-label="More actions">···</summary><div>${item.role !== 'PRIMARY' ? `<button type="button" data-action="set-primary-universe" data-id="${esc(item.universe_id)}">Set as Primary</button>` : ''}<button type="button" data-action="shared-universe-usage" data-id="${esc(item.universe_id)}">Usage</button><button type="button" data-action="shared-universe-history" data-id="${esc(item.universe_id)}">History</button><button type="button" data-action="remove-shared-universe" data-id="${esc(item.universe_id)}">Remove from Research</button></div></details></div></article>`;
  }).join('');
  $('researchTabContent').innerHTML = `<article class="panel"><div class="panel-head"><div><span class="eyebrow">RESEARCH · UNIVERSE</span><h2>Universe</h2><p>Research references shared Universes. Editing updates the shared identity; Copy creates an isolated Universe.</p></div><div class="button-row"><button type="button" data-action="add-library-universe">Add from Library</button><button type="button" class="primary" data-action="new-universe">Create Universe</button></div></div><div class="card-stack">${cards}</div></article>`;
}

function renderFactors() {
  const refs = researchRefs('FACTOR');
  const drafts = projectFactorDrafts();
  const content = [
    ...drafts.map(item => factorDraftCard(item)),
    ...refs.map(item => factorCard(item)),
  ];
  $('researchTabContent').innerHTML = `<article class="panel"><div class="panel-head"><div><span class="eyebrow">FACTOR</span><h2>Factor</h2><p>Define a reusable measurement with an Input, Formula, Parameters, and a clear value meaning.</p></div><div class="button-row"><button type="button" data-action="add-library-definition" data-kind="FACTOR">Add from Library</button><button type="button" class="primary" data-action="new-factor">Create in Research</button></div></div>${drafts.length ? `<div class="draft-section-label"><span>SAVED WORK</span><strong>${drafts.length} backed-up Factor${drafts.length === 1 ? '' : 's'}</strong></div>` : ''}<div class="card-stack">${content.length ? content.join('') : '<div class="empty-state"><h2>No Factor selected</h2><p>Add a published Factor or create one in this Research.</p></div>'}</div></article>`;
}

function renderAlphas() {
  const refs = researchRefs('ALPHA');
  const drafts = projectAlphaDrafts();
  const content = [
    ...drafts.map(item => alphaDraftCard(item)),
    ...refs.map(item => alphaCard(item)),
  ];
  $('researchTabContent').innerHTML = `<article class="panel"><div class="panel-head"><div><span class="eyebrow">ALPHA</span><h2>Alpha</h2><p>Combine pinned validated Factors into a reproducible prediction definition.</p></div><div class="button-row"><button type="button" data-action="add-library-definition" data-kind="ALPHA">Add from Library</button><button type="button" class="primary" data-action="new-alpha">Create in Research</button></div></div>${drafts.length ? `<div class="draft-section-label"><span>SAVED WORK</span><strong>${drafts.length} backed-up Alpha${drafts.length === 1 ? '' : 's'}</strong></div>` : ''}<div class="card-stack">${content.length ? content.join('') : '<div class="empty-state"><h2>No Alpha selected</h2><p>Validate at least one Factor before creating an Alpha.</p></div>'}</div></article>`;
}

function renderStrategy() {
  $('researchTabContent').innerHTML = `<div class="empty-state"><h2>Strategy is not configured</h2><p>Strategy converts a validated Alpha into position, risk, and execution rules. It will be connected after the Research workflow is complete.</p><div class="strategy-stage"><span>BOUNDARY</span><strong>Research does not create or submit a trading strategy automatically.</strong><small class="muted">No trading action is available on this page.</small></div></div>`;
}

function requirementSource(requirementId) {
  const links = arr(latestRequirement()?.dependency_links).filter(item => item.requirement_id === requirementId);
  const origins = new Set(links.map(item => item.origin_type));
  if (origins.has('MANUAL')) return 'User Added';
  if (origins.has('FACTOR_SPEC')) return 'Factor';
  if (origins.has('EVALUATION_SPEC')) return 'Research Test';
  return 'System Required';
}

function dataRowStatus(requirement, instrumentId) {
  if (!state.coverage) return 'Not Checked';
  const check = arr(state.coverage.checks).find(item => item.requirement_id === requirement.requirement_id && item.instrument_id === instrumentId);
  return check?.satisfied ? 'Ready' : 'Missing';
}

function renderResearchDataLegacy() {
  const set = latestRequirement();
  if (!set) {
    $('researchTabContent').innerHTML = `<article class="panel"><div class="panel-head"><div><span class="eyebrow">DATA</span><h2>Data</h2><p>Requirements define what this Research needs. Data shows whether those needs are available and ready to use.</p></div></div><div class="empty-state"><span class="eyebrow">REQUIREMENTS</span><h2>No Requirements configured</h2><p>Choose a reusable Requirements asset from Library, or define one for this Research.</p><div class="button-row"><button type="button" data-action="add-library-requirements">Add from Library</button><button type="button" class="primary" data-action="add-data">Create in Research</button></div></div></article>`;
    return;
  }
  const fromLibrary = state.requirementRef?.origin === 'LIBRARY';
  const published = publishedAssetForSource(set.requirement_set_id);
  const rows = arr(set.requirements).flatMap(requirement => arr(requirement.instrument_ids).map(instrumentId => {
    const status = dataRowStatus(requirement, instrumentId);
    return `<tr><td><strong>${esc(instrumentId.split(':').pop())} ${esc(requirement.frequency)} Bars</strong><small>${esc(universeMarketLabel([instrumentId]))}</small></td><td>${esc(arr(requirement.fields).join(', '))}</td><td>${esc(requirementSource(requirement.requirement_id))}</td><td>${statusChip(status)}</td></tr>`;
  }));
  const source = fromLibrary ? `Source: Library · v${state.requirementRef.library_version}` : 'Source: Current Research';
  const sourceActions = fromLibrary
    ? `<button type="button" data-action="add-library-requirements">Replace</button><button type="button" class="primary" data-action="copy-library-requirements">Copy and Edit</button>`
    : `<button type="button" data-action="add-library-requirements">Add from Library</button><button type="button" data-action="add-data">Edit</button>${published ? '' : '<button type="button" class="primary" data-action="publish-requirements">Publish to Library</button>'}`;
  const fields = [...new Set(arr(set.requirements).flatMap(item => arr(item.fields)))].join(', ') || '-';
  $('researchTabContent').innerHTML = `<article class="panel"><div class="panel-head"><div><span class="eyebrow">DATA</span><h2>Data</h2><p>Requirements define what this Research needs. Data Coverage shows whether those needs are available and ready to use.</p></div></div><section class="data-section"><div class="section-heading"><div><span class="eyebrow">REQUIREMENTS</span><h3>${esc(state.project.title)} Requirements</h3><p>${esc(source)}</p></div>${statusChip(fromLibrary ? 'From Library' : published ? 'Validated' : 'Research Draft')}</div><div class="fact-grid"><div class="fact-block"><span>Frequency</span><strong>${esc(set.context?.frequency || '-')}</strong></div><div class="fact-block"><span>History Range</span><strong>${esc(formatDate(set.context?.history_start))} to ${esc(formatDate(set.context?.history_end))}</strong></div><div class="fact-block"><span>Fields</span><strong>${esc(fields)}</strong></div></div>${published && !fromLibrary ? `<div class="usage">Published as <strong>Library v${published.version}</strong></div>` : ''}<div class="card-actions">${sourceActions}</div></section><section class="data-section"><div class="section-heading"><div><span class="eyebrow">DATA COVERAGE</span><h3>Availability</h3><p>Actual datasets matched against the current Requirements.</p></div><div class="button-row"><button type="button" data-action="check-data">Check Data</button><button type="button" class="primary" data-action="fill-missing">Fill Missing</button></div></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Dataset</th><th>Fields</th><th>Required By</th><th>Status</th></tr></thead><tbody>${rows.join('') || '<tr><td colspan="4">No data items.</td></tr>'}</tbody></table></div></section></article>`;
}

function renderResearchDataLegacyCards() {
  const items = arr(state.requirementItems);
  const staleUniverses = state.universeBindings.filter(item => item.requirements_stale_at);
  const status = state.dataStatus || {summary: {ready: 0, partial: 0, missing: 0}, rows: []};
  const rows = arr(status.rows).map(row => `<tr><td><strong>${esc(row.instrument_id?.split(':').pop() || '-')} ${esc(row.frequency)} ${esc(row.data_type)}</strong><small>${esc(row.provider || '')}</small></td><td>${esc(arr(row.fields).join(', '))}</td><td>${esc(arr(row.required_by).join(', ') || '-')}</td><td>${statusChip(friendlyStatus(row.status))}</td><td>${row.status === 'READY' ? '' : '<button type="button" data-action="fill-missing">Prepare</button>'}</td></tr>`).join('');
  $('researchTabContent').innerHTML = `<article class="panel"><div class="panel-head"><div><span class="eyebrow">DATA</span><h2>Requirements and Data Status</h2><p>Requirements are the contracts this Research uses. Data Status resolves those contracts against available datasets.</p></div><div class="button-row"><button data-action="add-library-requirements">Add from Library</button><button class="primary" data-action="new-requirement">Create in Research</button></div></div><div class="requirements-layout"><section><div class="section-heading"><div><span class="eyebrow">REQUIREMENTS</span><h3>${items.length} work item${items.length === 1 ? '' : 's'}</h3></div></div><div class="card-stack">${items.map(renderRequirementWorkCard).join('') || '<div class="empty-state"><h2>No Requirements</h2><p>Create a local Requirement or add a published one from Library.</p></div>'}</div></section><aside class="requirements-summary"><span class="eyebrow">SUMMARY</span><strong>${items.length}</strong><small>Requirements</small><div><span>${status.summary.ready || 0} Ready</span><span>${status.summary.partial || 0} Partial</span><span>${status.summary.missing || 0} Missing</span></div></aside></div><section class="data-section"><div class="section-heading"><div><span class="eyebrow">DATA STATUS</span><h3>Resolved availability</h3><p>One merged view of what is required and what is actually available.</p></div><button data-action="check-data">Refresh Status</button></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Dataset</th><th>Fields</th><th>Required By</th><th>Status</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="5">No resolved data requirements yet.</td></tr>'}</tbody></table></div></section></article>`;
}

function renderResearchDataLegacyCoverage() {
  const items = arr(state.requirementItems);
  const status = state.dataStatus || {summary: {}, rows: []};
  const summary = status.summary || {};
  const rows = arr(status.rows).map(row => {
    const action = row.can_prepare ? '<button type="button" data-action="fill-missing">Prepare</button>' : row.status === 'UNAVAILABLE' ? '<button type="button" data-action="edit-first-requirement">Review Requirement</button>' : '';
    const available = row.available_range ? `${formatDate(row.available_range.start)} → ${formatDate(row.available_range.end)}` : 'Not in Data Catalog';
    return `<tr><td><strong>${esc(row.instrument_id?.split(':').pop() || '-')} ${esc(row.frequency)} ${esc(row.data_type)}</strong><small>${esc(row.provider || '')}</small></td><td>${esc(arr(row.fields).join(', '))}</td><td>${esc(arr(row.required_by).join(', ') || '-')}</td><td><strong>${esc(available)}</strong>${row.reason ? `<small>${esc(row.reason)}</small>` : ''}</td><td>${statusChip(friendlyStatus(row.status))}</td><td>${action}</td></tr>`;
  }).join('');
  $('researchTabContent').innerHTML = `<article class="panel"><div class="panel-head"><div><span class="eyebrow">DATA</span><h2>Requirements and Data Status</h2><p>Requirements describe what is needed. Data Status distinguishes prepared local coverage from data that can still be fetched.</p></div><div class="button-row"><button data-action="add-library-requirements">Add from Library</button><button class="primary" data-action="new-requirement">Create in Research</button></div></div><div class="requirements-layout"><section><div class="section-heading"><div><span class="eyebrow">REQUIREMENTS</span><h3>${items.length} work item${items.length === 1 ? '' : 's'}</h3></div></div><div class="card-stack">${items.map(renderRequirementWorkCard).join('') || '<div class="empty-state"><h2>No Requirements</h2><p>Create a local Requirement or add a published one from Library.</p></div>'}</div></section><aside class="requirements-summary"><span class="eyebrow">DATA STATUS</span><strong>${items.length}</strong><small>Requirements</small><div><span>${summary.ready || 0} Ready</span><span>${summary.partial || 0} Partial</span><span>${summary.not_prepared || 0} Not Prepared</span><span>${summary.unavailable || 0} Unavailable</span></div></aside></div><section class="data-section"><div class="section-heading"><div><span class="eyebrow">RESOLVED DATA</span><h3>Coverage and preparation</h3><p>Ready means an exact local Manifest exists. Partial means some local history exists. Not Prepared means the connector can fetch it. Latest moves forward when status is checked; data downloads only when Prepare is run.</p></div><button data-action="check-data">Refresh Status</button></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Dataset</th><th>Fields</th><th>Required By</th><th>Coverage</th><th>Status</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6">No resolved data requirements yet.</td></tr>'}</tbody></table></div></section></article>`;
}

function requirementStatusForItem(item) {
  const matching = requirementRowsForItem(item);
  const statuses = new Set(matching.map(row => (
    row.status === 'NEEDS_ATTENTION' && row.can_prepare && !activeGrantFor(row)
      ? 'NOT_PREPARED'
      : row.status
  )));
  const allUnavailable = matching.length > 0 && matching.every(row => row.status === 'UNAVAILABLE');
  let status = 'CHECKING';
  if (allUnavailable) status = 'UNAVAILABLE';
  else if (statuses.has('FAILED')) status = 'FAILED';
  else if (statuses.has('UNAVAILABLE') || statuses.has('NEEDS_ATTENTION')) status = 'NEEDS_ATTENTION';
  else if (statuses.has('NOT_PREPARED')) status = 'NOT_PREPARED';
  else status = ['PREPARING', 'QUEUED', 'CHECKING', 'READY'].find(value => statuses.has(value)) || 'CHECKING';
  const latest = matching.map(row => row.available_range?.end).filter(Boolean).sort().at(-1);
  const preparations = matching.map(row => row.preparation).filter(Boolean);
  const completed = preparations.reduce((total, row) => total + Number(row.completed_partitions || 0), 0);
  const totals = preparations.map(row => row.total_partitions);
  const total = totals.length && totals.every(value => value != null) ? totals.reduce((sum, value) => sum + Number(value), 0) : null;
  const current = preparations.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))[0] || null;
  const counts = matching.reduce((result, row) => {
    const key = row.status === 'NEEDS_ATTENTION' && row.can_prepare && !activeGrantFor(row) ? 'NOT_PREPARED' : row.status;
    result[key] = (result[key] || 0) + 1;
    return result;
  }, {});
  const statusSummary = [
    counts.READY ? `${counts.READY} ready` : '',
    counts.PREPARING || counts.QUEUED || counts.CHECKING ? `${(counts.PREPARING || 0) + (counts.QUEUED || 0) + (counts.CHECKING || 0)} preparing` : '',
    counts.NOT_PREPARED ? `${counts.NOT_PREPARED} not prepared` : '',
    counts.NEEDS_ATTENTION ? `${counts.NEEDS_ATTENTION} need review` : '',
    counts.UNAVAILABLE ? `${counts.UNAVAILABLE} unavailable` : '',
    counts.FAILED ? `${counts.FAILED} failed` : '',
  ].filter(Boolean).join(' · ');
  const reason = matching.find(row => row.status === status)?.reason || current?.message || '';
  const coverage = status === 'READY'
    ? `Complete coverage${latest ? ` through ${formatDateTime(latest)}` : ''}`
    : status === 'CHECKING' ? 'Checking existing coverage and available data sources.'
    : status === 'QUEUED' ? 'Automatic data preparation is queued.'
    : status === 'PREPARING' ? 'Preparing the missing data automatically.'
    : status === 'NOT_PREPARED' ? 'The data can be fetched and has not been prepared yet.'
    : status === 'NEEDS_ATTENTION' ? (reason || statusSummary || 'Review is required before automatic preparation can continue.')
    : status === 'FAILED' ? (reason || 'Automatic preparation failed.')
    : 'The selected provider cannot satisfy this Requirement.';
  return {status, coverage, rows: matching, preparation: current ? {...current, completed_partitions: completed, total_partitions: total, percent: total ? Math.min(100, Math.round(completed * 100 / total)) : current.percent} : null};
}

function requirementRowsForItem(item) {
  return arr(state.dataStatus?.rows).filter(row =>
    arr(row.library_asset_ids).includes(item?.library_asset_id) || arr(row.required_by).includes(item?.name)
  );
}

function requirementInstrumentStatus(row) {
  if (row.status === 'NEEDS_ATTENTION' && row.can_prepare && !activeGrantFor(row)) return 'Not prepared';
  return friendlyStatus(row.status);
}

function requirementInstrumentBreakdown(rows) {
  if (!rows.length) return '';
  return `<div class="requirement-instrument-status"><span>Availability</span><div>${rows.map(row => {
    const notPrepared = row.status === 'NEEDS_ATTENTION' && row.can_prepare && !activeGrantFor(row);
    return `<span class="instrument-status-item ${esc(notPrepared ? 'not-prepared' : String(row.status || '').toLowerCase())}" title="${esc(notPrepared ? 'Data has not been prepared yet.' : row.reason || '')}"><strong>${esc(row.instrument_label || row.instrument_id?.split(':').pop() || '-')}</strong><small>${esc(requirementInstrumentStatus(row))}</small></span>`;
  }).join('')}</div></div>`;
}

function formatDateTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('en-CA', {hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}

function formatEta(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return '';
  if (value < 60) return 'Less than a minute remaining';
  if (value < 3600) return `About ${Math.ceil(value / 60)} minutes remaining`;
  return `About ${Math.ceil(value / 3600)} hours remaining`;
}

function preparationBlock(resolved) {
  const preparation = resolved.preparation || {};
  if (['PREPARING', 'QUEUED', 'CHECKING'].includes(resolved.status)) {
    const percent = preparation.percent;
    const completed = preparation.completed_partitions;
    const total = preparation.total_partitions;
    const current = preparation.current_range || {};
    const elapsed = Number(preparation.elapsed_seconds);
    return `<div class="requirement-preparation"><div class="preparation-title"><span>Live Download</span><strong>${percent != null ? `${esc(percent)}%` : esc(friendlyStatus(resolved.status))}</strong></div>${percent != null ? `<div class="preparation-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${esc(percent)}"><span style="width:${Math.max(0, Math.min(100, Number(percent)))}%"></span></div>` : '<div class="preparation-track indeterminate"><span></span></div>'}<div class="preparation-meta">${preparation.phase ? `<span>${esc(preparation.phase)}</span>` : ''}${total != null ? `<span>${esc(completed)} of ${esc(total)} partitions complete</span>` : '<span>Waiting for provider progress</span>'}${preparation.rows_fetched != null ? `<span>${esc(preparation.rows_fetched)} rows downloaded</span>` : ''}${current.start ? `<span>Current range ${esc(formatDateTime(current.start))}${current.end ? ` → ${esc(formatDateTime(current.end))}` : ''}</span>` : ''}${preparation.eta_seconds != null ? `<span>${esc(formatEta(preparation.eta_seconds))}</span>` : ''}${Number.isFinite(elapsed) ? `<span>Running ${esc(Math.max(0, Math.round(elapsed)))}s</span>` : ''}</div></div>`;
  }
  const updated = preparation.updated_at || state.dataStatus?.latest_checked;
  const review = preparation.auto_review || {};
  const reviewLabel = review.status === 'COMPLETED' ? 'Automatic Review Complete' : 'Data Status';
  return `<div class="requirement-coverage ${esc(resolved.status.toLowerCase())}${review.status === 'COMPLETED' ? ' auto-reviewed' : ''}"><span>${esc(reviewLabel)}</span><strong>${esc(resolved.coverage)}</strong>${resolved.status === 'READY' && updated ? `<small>Last updated ${esc(formatDateTime(updated))}</small>` : ''}</div>`;
}

function renderRequirementWorkCard(item) {
  const spec = item.spec || {};
  const scope = spec.scope || {}; const time = spec.time || {}; const data = spec.data || {};
  const targetUniverse = state.universeBindings.find(universe => universe.universe_id === spec.target?.universe_id);
  const instruments = targetUniverse ? `Target: ${targetUniverse.name}` : arr(scope.instruments?.include).map(requirementInstrumentLabel).join(', ') || 'Rule based';
  const resolved = requirementStatusForItem(item);
  const adjustedStarts = resolved.rows
    .flatMap(row => arr(row.automatic_adjustments))
    .map(adjustment => adjustment.available_from)
    .filter(Boolean)
    .sort();
  const effectiveStart = adjustedStarts.at(-1) || '';
  const effectiveTimeNote = effectiveStart
    ? `<small>Automatically aligned to provider history: ${esc(formatDateTime(effectiveStart))}</small>`
    : '';
  const exceptionActions = '';
  const detailLabel = resolved.status === 'FAILED' ? 'View Error' : 'Details';
  return `<article class="requirement-card requirement-card-pro"><div class="card-header"><div><h3>${esc(item.name)}</h3><small>${esc(scope.provider || '-')} ${esc(scope.market || '')} · ${esc(instruments)}</small></div>${statusChip(friendlyStatus(resolved.status))}</div><div class="requirement-essentials"><div><span>Data</span><strong>${esc(data.frequency || '-')} ${esc(data.dataset_type || 'Data')} · ${esc(arr(data.fields).join(' / ') || '-')}</strong></div><div><span>Time</span><strong>${esc(formatDate(time.start))} → ${esc(time.end === 'LATEST_AVAILABLE' ? 'Latest' : formatDate(time.end))}</strong>${effectiveTimeNote}</div></div>${requirementInstrumentBreakdown(resolved.rows)}${preparationBlock(resolved)}<div class="card-actions requirement-actions"><button data-action="edit-requirement" data-id="${esc(item.ref_id)}">Edit</button><button data-action="replace-requirement" data-id="${esc(item.ref_id)}">Replace</button><button class="danger-quiet" data-action="remove-requirement" data-id="${esc(item.ref_id)}">Remove</button><button data-action="requirement-details" data-id="${esc(item.ref_id)}">${detailLabel}</button>${exceptionActions}</div></article>`;
}

function automaticRequirementGroups() {
  const groups = new Map();
  arr(state.dataStatus?.rows).forEach(row => {
    if (!groups.has(row.requirement_id)) groups.set(row.requirement_id, []);
    groups.get(row.requirement_id).push(row);
  });
  return [...groups.entries()].map(([requirementId, rows]) => {
    const statuses = rows.map(item => item.status);
    const status = statuses.every(item => item === 'READY')
      ? 'READY'
      : statuses.some(item => item === 'FAILED')
        ? 'FAILED'
        : statuses.some(item => item === 'UNAVAILABLE')
          ? 'UNAVAILABLE'
          : 'PREPARING';
    const active = rows.map(item => item.preparation).filter(Boolean)
      .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))[0] || null;
    const completed = rows.reduce((sum, item) => sum + Number(item.preparation?.completed_partitions || 0), 0);
    const totals = rows.map(item => item.preparation?.total_partitions).filter(value => value != null);
    const total = totals.length === rows.length ? totals.reduce((sum, value) => sum + Number(value), 0) : null;
    const preparation = active
      ? {...active, completed_partitions: completed, total_partitions: total, percent: total ? Math.min(100, Math.round(completed * 100 / total)) : active.percent}
      : null;
    return {requirementId, rows, status, preparation};
  });
}

function renderAutomaticRequirementCard(group) {
  const first = group.rows[0] || {};
  const evaluation = first.evaluation_range || {};
  const required = first.required_range || {};
  const history = first.additional_history || {};
  const ready = group.rows.filter(item => item.status === 'READY').length;
  const requiredBy = [...new Set(group.rows.flatMap(item => arr(item.required_by)))];
  const sources = [...new Set(group.rows.map(item => item.resolved_source || item.provider).filter(Boolean))];
  return `<article class="requirement-card requirement-card-pro factor-auto-requirement">
    <div class="card-header"><div><h3>Effective Data Contract</h3><small>${requiredBy.length ? `Required by ${esc(requiredBy.join(' / '))}` : 'Generated from the current Research definitions and Universe'}</small></div>${statusChip(friendlyStatus(group.status))}</div>
    <div class="requirement-essentials">
      <div><span>Data</span><strong>${esc(first.frequency || '-')} ${esc(first.data_type || 'Data')} · ${esc(arr(first.fields).join(' / ') || '-')}</strong></div>
      <div><span>Instruments</span><strong>${esc(group.rows.length)} · ${esc(ready)} Ready</strong></div>
      <div><span>Data Sources</span><strong>${esc(sources.join(' → ') || 'Best compatible source')}</strong></div>
      <div><span>Evaluation</span><strong>${esc(formatDate(evaluation.start))} → ${esc(formatDate(evaluation.end))}</strong></div>
      <div><span>Required Data</span><strong>${esc(formatDate(required.start))} → ${esc(formatDate(required.end))}</strong></div>
      <div><span>Formula History</span><strong>${esc(history.observations || 0)} additional observations</strong></div>
    </div>
    ${requirementInstrumentBreakdown(group.rows)}
    ${preparationBlock({status: group.status, coverage: group.rows.find(item => item.reason)?.reason || friendlyStatus(group.status), preparation: group.preparation})}
    <p class="form-help">Compiler-owned. It updates when pinned Factor/Alpha definitions, the Universe Snapshot, or explicit manual Requirements change.</p>
  </article>`;
}

function renderResearchData() {
  const items = arr(state.requirementItems);
  const automaticGroups = automaticRequirementGroups();
  const itemRequirementIds = new Set(items.flatMap(item =>
    requirementRowsForItem(item).map(row => row.requirement_id)
  ));
  const derivedGroups = automaticGroups.filter(group => !itemRequirementIds.has(group.requirementId));
  const staleUniverses = arr(state.universeBindings).filter(item => item.requirements_stale_at);
  const itemStatuses = [
    ...items.map(requirementStatusForItem),
    ...derivedGroups.map(item => ({status: item.status})),
  ];
  const count = status => itemStatuses.filter(item => item.status === status).length;
  const preparing = count('PREPARING') + count('QUEUED') + count('CHECKING');
  const exceptions = count('NEEDS_ATTENTION') + count('FAILED') + count('UNAVAILABLE');
  const refreshed = state.dataStatus?.latest_checked;
  const displayedCount = items.length + derivedGroups.length;
  const cards = [
    ...items.map(renderRequirementWorkCard),
    ...derivedGroups.map(renderAutomaticRequirementCard),
  ].join('');
  const rows = arr(state.dataStatus?.rows);
  const sources = [...new Set(rows.map(row => row.resolved_source || row.provider).filter(Boolean))];
  const setId = state.dataStatus?.requirement_set_id || latestRequirement()?.requirement_set_id || '';
  const effectiveSummary = setId
    ? `<section class="effective-requirement-summary"><div><span class="eyebrow">EFFECTIVE REQUIREMENTSET</span><strong>${esc(setId)}</strong><small>One canonical set for the current Universe, validated Factor/Alpha definitions, and explicit manual Requirements.</small></div><div class="effective-set-facts"><span><b>${automaticGroups.length}</b> data contracts</span><span><b>${rows.length}</b> instrument inputs</span><span><b>${sources.length}</b> resolved source${sources.length === 1 ? '' : 's'}</span></div>${sources.length ? `<div class="effective-source-list">${sources.map(source => `<span>${esc(source)}</span>`).join('')}</div>` : ''}</section>`
    : '';
  $('researchTabContent').innerHTML = `<article class="panel requirements-surface"><div class="requirements-page-head"><div><span class="eyebrow">DATA</span><h2>Data</h2><p>DataTube maintains every effective Requirement automatically and reports live download progress here.</p><div class="requirement-count-line"><strong>${displayedCount} Requirement${displayedCount === 1 ? '' : 's'}</strong><span>${count('READY')} Ready</span><span>${preparing} Preparing</span>${exceptions ? `<span>${exceptions} Errors</span>` : ''}<span class="last-updated">${state.requirementRefreshError ? 'Status update failed' : refreshed ? `Last updated: ${esc(new Date(refreshed).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}))}` : 'Checking status'}</span>${state.requirementRefreshError ? '<button class="text-button" data-action="retry-status-refresh">Retry</button>' : ''}</div></div><div class="button-row"><button data-action="add-library-requirements">Add Manual Requirement</button></div></div>${effectiveSummary}${staleUniverses.length ? `<div class="universe-data-state attention"><span class="eyebrow">DATA NEEDS ATTENTION</span><strong>One or more Universe changes need confirmation.</strong><p>Review the affected Requirement while keeping its current Dataset, Frequency, Fields, Time Range, and Provider.</p><div><button class="primary" data-action="review-universe-requirement" data-id="${esc(staleUniverses[0].universe_id)}">Review &amp; Update</button></div></div>` : ''}<div class="requirement-card-list">${cards || '<div class="empty-state"><h2>No effective data contracts yet</h2><p>Validate a Factor or Alpha, or add an explicit manual Requirement. The Effective RequirementSet will be generated automatically.</p><div class="button-row"><button data-action="add-library-requirements">Add from Library</button><button class="primary" data-action="new-requirement">New Requirement</button></div></div>'}</div></article>`;
}

function isLegacyHybridRun(run, summary = null) {
  if (summary?.product_run_type === 'LEGACY_HYBRID_RUN' || summary?.alpha_run?.legacy_hybrid) return true;
  return run?.run_type === 'ALPHA_EVALUATION' && arr(run?.output?.produced_backtest_artifact_ids).length > 0;
}

function testLabel(runType, run = null, summary = null) {
  if (isLegacyHybridRun(run, summary)) return 'Legacy Hybrid Run';
  const labels = {
    FACTOR_EVALUATION: 'Factor Evaluation',
    ALPHA_EVALUATION: 'Alpha Evaluation',
    RESEARCH_BACKTEST: 'Research Backtest',
  };
  return labels[runType] || 'Unsupported Run Type';
}

function runMetric(run) {
  const metrics = run?.output?.metrics || {};
  if (run?.run_type === 'RESEARCH_BACKTEST') return {performance: metrics};
  const root = Object.values(metrics)[0] || {};
  return root.evaluation ? {...root.evaluation, performance: root.performance || {}} : root;
}

function firstHorizonValue(values) {
  const keys = Object.keys(values || {}).sort((left, right) => Number(left) - Number(right));
  return keys.length ? values[keys[0]] : {};
}

function renderResearchRuns() {
  const runs = state.runs.filter(item => item.project_id === state.projectId && ['FACTOR_EVALUATION', 'ALPHA_EVALUATION', 'RESEARCH_BACKTEST'].includes(item.run_type));
  const filtered = state.runFilter ? runs.filter(item => item.run_type === state.runFilter) : runs;
  $('researchTabContent').innerHTML = `<article class="panel"><div class="panel-head"><div><span class="eyebrow">RUNS</span><h2>Evaluation and Backtest Runs</h2><p>Factor Evaluation tests features. Alpha Evaluation tests predictive signals. Research Backtest owns portfolio construction, execution, costs, and returns.</p></div><div class="button-row"><button type="button" data-action="test-factor">Factor Evaluation</button><button type="button" data-action="test-alpha">Alpha Evaluation</button><button type="button" class="primary" data-action="test-backtest">Research Backtest</button></div></div><div class="library-tabs"><button data-run-filter="" class="${state.runFilter ? '' : 'active'}">All</button><button data-run-filter="FACTOR_EVALUATION" class="${state.runFilter === 'FACTOR_EVALUATION' ? 'active' : ''}">Factor</button><button data-run-filter="ALPHA_EVALUATION" class="${state.runFilter === 'ALPHA_EVALUATION' ? 'active' : ''}">Alpha</button><button data-run-filter="RESEARCH_BACKTEST" class="${state.runFilter === 'RESEARCH_BACKTEST' ? 'active' : ''}">Backtest</button></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Run</th><th>Status</th><th>Result</th><th>Created</th><th></th></tr></thead><tbody>${filtered.map(run => {
    const metric = runMetric(run);
    const legacy = isLegacyHybridRun(run);
    const alphaRank = firstHorizonValue(metric.rank_ic);
    const backtestPerformance = (legacy || run.run_type === 'RESEARCH_BACKTEST') ? metric.performance || {} : metric;
    const resultText = run.run_type === 'FACTOR_EVALUATION'
      ? `Coverage ${percent(metric.coverage)}`
      : run.run_type === 'ALPHA_EVALUATION' && !legacy
        ? `Rank IC ${factorRunMetric(alphaRank.mean)}`
        : `CAGR ${percent(backtestPerformance.annualized_return)}`;
    return `<tr><td><strong>${esc(testLabel(run.run_type, run))}</strong></td><td>${statusChip(friendlyStatus(run.status))}</td><td>${run.status === 'SUCCEEDED' ? esc(resultText) : '-'}</td><td>${esc(formatDate(run.created_at))}</td><td><button type="button" data-action="run-details" data-id="${esc(run.run_id)}">${run.status === 'SUCCEEDED' ? 'View Result' : 'Details'}</button></td></tr>`;
  }).join('') || '<tr><td colspan="5">No evaluation or backtest runs yet.</td></tr>'}</tbody></table></div></article>`;
}

function definitionUsageCount(libraryAssetId) {
  const asset = state.library.find(item => item.library_asset_id === libraryAssetId);
  return state.projectIndex.reduce((count, item) => count + (Object.values(item.refs).some(ref =>
    ref.library_asset_id === libraryAssetId || ref.definition_id === asset?.source_object_id
  ) ? 1 : 0), 0);
}

function universeUsageCount(libraryAssetId) {
  return state.projectIndex.filter(item => item.universeRef?.library_asset_id === libraryAssetId).length;
}

function requirementUsageCount(libraryAssetId) {
  return state.projectIndex.filter(item => item.requirementRef?.library_asset_id === libraryAssetId).length;
}

async function ensureProjectIndex() {
  if (!state.projectIndex.length && state.projects.length) state.projectIndex = await Promise.all(state.projects.map(researchSummary));
}

const GROUPABLE_LIBRARY_TABS = new Set(['factor', 'alpha']);
const UNGROUPED_KEY = '__ungrouped__';

async function ensureLibraryGroups(assetType, force = false) {
  if (!force && state.libraryGroupsByType[assetType]) return;
  const [groups, membership] = await Promise.all([
    api(`/api/library/groups?asset_type=${encodeURIComponent(assetType)}`),
    api(`/api/library/groups/membership?asset_type=${encodeURIComponent(assetType)}`),
  ]);
  state.libraryGroupsByType[assetType] = groups;
  state.libraryGroupMembershipByType[assetType] = membership;
}

function libraryCardWithGroupTools(cardHtml, assetType, assetId, groups, currentGroupId) {
  const selectionKey = `${assetType}:${assetId}`;
  const checked = state.librarySelectedAssets.has(selectionKey) ? 'checked' : '';
  const moveOptions = [`<button type="button" data-action="library-move-asset" data-asset-type="${esc(assetType)}" data-asset-id="${esc(assetId)}" data-group-id="" ${!currentGroupId ? 'disabled' : ''}>Ungrouped</button>`]
    .concat(groups.map(group => `<button type="button" data-action="library-move-asset" data-asset-type="${esc(assetType)}" data-asset-id="${esc(assetId)}" data-group-id="${esc(group.group_id)}" ${currentGroupId === group.group_id ? 'disabled' : ''}>${esc(group.name)}</button>`));
  const tools = `<div class="library-card-tools"><label class="library-card-select"><input type="checkbox" data-library-select="${esc(selectionKey)}" ${checked}><span>Select</span></label><details class="overflow-menu"><summary aria-label="Move to Group">Move to &#9662;</summary><div>${moveOptions.join('')}</div></details></div>`;
  return `<div class="library-card-wrap" data-library-asset-id="${esc(selectionKey)}">${tools}${cardHtml}</div>`;
}

function libraryGroupRailHtml(assetType, totalCount, ungroupedCount, activeGroup, groups, searchConfig) {
  const searchHtml = searchConfig ? `<div class="library-group-search"><input type="search" id="${esc(searchConfig.inputId)}" placeholder="${esc(searchConfig.placeholder)}" value="${esc(searchConfig.value)}"></div>` : '';
  return `<nav class="library-group-rail" aria-label="Groups">
    ${searchHtml}
    <div class="library-group-list">
      <button type="button" class="library-group-item ${activeGroup === '' ? 'active' : ''}" data-library-group="">All<span>${totalCount}</span></button>
      <button type="button" class="library-group-item ${activeGroup === UNGROUPED_KEY ? 'active' : ''}" data-library-group="${UNGROUPED_KEY}">Ungrouped<span>${ungroupedCount}</span></button>
      <div class="library-group-divider"></div>
      ${groups.map((group, index) => `<div class="library-group-row ${activeGroup === group.group_id ? 'active' : ''}">
        <button type="button" class="library-group-item" data-library-group="${esc(group.group_id)}">${esc(group.name)}<span>${group.asset_count}</span></button>
        <details class="overflow-menu"><summary aria-label="Group options">&middot;&middot;&middot;</summary><div>
          <button type="button" data-action="move-library-group-up" data-id="${esc(group.group_id)}" ${index === 0 ? 'disabled' : ''}>Move Up</button>
          <button type="button" data-action="move-library-group-down" data-id="${esc(group.group_id)}" ${index === groups.length - 1 ? 'disabled' : ''}>Move Down</button>
          <button type="button" data-action="rename-library-group" data-id="${esc(group.group_id)}" data-name="${esc(group.name)}">Rename</button>
          <button type="button" class="danger-quiet" data-action="delete-library-group" data-id="${esc(group.group_id)}" data-name="${esc(group.name)}" data-count="${group.asset_count}">Delete</button>
        </div></details>
      </div>`).join('')}
      <button type="button" class="library-group-add" data-action="new-library-group" data-asset-type="${esc(assetType)}">+ New Group</button>
    </div>
  </nav>`;
}

function libraryBatchBarHtml(assetType, groups) {
  const selected = [...state.librarySelectedAssets].filter(key => key.startsWith(`${assetType}:`));
  if (!selected.length) return '';
  return `<div class="library-batch-bar">
    <span>${selected.length} selected</span>
    <select id="libraryBatchTarget"><option value="">Ungrouped</option>${groups.map(group => `<option value="${esc(group.group_id)}">${esc(group.name)}</option>`).join('')}</select>
    <button type="button" class="primary" data-action="library-batch-move" data-asset-type="${esc(assetType)}">Move</button>
    <button type="button" data-action="library-clear-selection">Clear</button>
  </div>`;
}

function attachLibraryGroupSearch(target, inputId) {
  const input = $(inputId);
  if (!input) return;
  const applySearch = () => {
    const query = (state.libraryGroupSearch || '').trim().toLowerCase();
    target.querySelectorAll('[data-library-asset-id]').forEach(card => {
      card.hidden = Boolean(query && !card.textContent.toLowerCase().includes(query));
    });
  };
  applySearch();
  input.addEventListener('input', () => {
    state.libraryGroupSearch = input.value;
    applySearch();
  });
}

function renderGroupedLibraryAssets(target, assetType, allItems) {
  const groups = state.libraryGroupsByType[assetType] || [];
  const membership = state.libraryGroupMembershipByType[assetType] || {};
  const activeGroup = state.libraryActiveGroup[assetType] || '';
  const ungroupedCount = allItems.filter(item => !membership[item.library_asset_id]).length;

  let visible = allItems;
  if (activeGroup === UNGROUPED_KEY) visible = visible.filter(item => !membership[item.library_asset_id]);
  else if (activeGroup) visible = visible.filter(item => membership[item.library_asset_id] === activeGroup);

  const cardFn = assetType === 'FACTOR' ? factorCard : alphaCard;
  const emptyCopy = assetType === 'FACTOR'
    ? {title: 'No Factor here', body: 'Validate a Factor in Research and it will appear here automatically.'}
    : {title: 'No Alpha here', body: 'Publish all required Factors before publishing an Alpha.'};

  const rail = libraryGroupRailHtml(assetType, allItems.length, ungroupedCount, activeGroup, groups, {
    inputId: 'libraryGroupSearchInput',
    placeholder: `Search ${assetType === 'FACTOR' ? 'Factor' : 'Alpha'}...`,
    value: state.libraryGroupSearch,
  });
  const batchBar = libraryBatchBarHtml(assetType, groups);

  const cards = visible.map(asset => {
    const cardHtml = cardFn({...asset.content, version: asset.version, library_asset_id: asset.library_asset_id}, true, definitionUsageCount(asset.library_asset_id));
    return libraryCardWithGroupTools(cardHtml, assetType, asset.library_asset_id, groups, membership[asset.library_asset_id] || '');
  }).join('') || `<div class="empty-state compact"><h2>${esc(emptyCopy.title)}</h2><p>${esc(emptyCopy.body)}</p></div>`;

  target.innerHTML = `<div class="library-grouped-layout">${rail}<div class="library-group-main">${batchBar}<div class="card-stack">${cards}</div></div></div>`;
  attachLibraryGroupSearch(target, 'libraryGroupSearchInput');
}

async function renderLibrary() {
  await ensureProjectIndex();
  $('appView').innerHTML = `<header class="page-heading"><div><span class="eyebrow">LIBRARY</span><h1>Library</h1><p>Reusable research components and data Requirements.</p></div></header><div class="library-intro">Library stores reusable components and all Requirements. Research selects what it needs.</div><nav class="library-tabs" aria-label="Library navigation">${['universe','factor','alpha','requirements','strategy'].map(tab => `<button type="button" data-library-tab="${tab}" class="${state.libraryTab === tab ? 'active' : ''}">${tab.charAt(0).toUpperCase() + tab.slice(1)}</button>`).join('')}</nav><section id="libraryTabContent" class="tab-content"></section>`;
  await renderLibraryTab();
  if (state.libraryTab === 'requirements') scheduleRequirementRefresh();
}

async function switchLibraryTab(tab) {
  state.libraryTab = tab;
  state.librarySelectedAssets.clear();
  state.libraryGroupSearch = '';
  document.querySelectorAll('[data-library-tab]').forEach(node => node.classList.toggle('active', node.dataset.libraryTab === tab));
  await renderLibraryTab();
  if (tab === 'requirements') scheduleRequirementRefresh();
  else clearTimeout(state.requirementRefreshTimer);
}

async function renderLibraryTabLegacy() {
  const target = $('libraryTabContent');
  if (!target) return;
  if (GROUPABLE_LIBRARY_TABS.has(state.libraryTab)) {
    const assetType = state.libraryTab.toUpperCase();
    await ensureLibraryGroups(assetType);
    const allItems = latestByName(state.library.filter(item => item.component_type === assetType));
    renderGroupedLibraryAssets(target, assetType, allItems);
    return;
  }
  if (state.libraryTab === 'universe') {
    const items = latestByName(state.library.filter(item => item.component_type === 'UNIVERSE'));
    target.innerHTML = `<div class="card-stack">${items.map(asset => {
      const item = asset.content.definition || {};
      const snapshot = asset.content.snapshot || {};
      const members = arr(snapshot?.actual_instrument_ids);
      return `<article class="component-card"><div class="card-header"><div><h3>${esc(asset.name)}</h3><small>Library · v${esc(asset.version)} · Current version</small></div>${statusChip('Published')}</div><div class="fact-grid"><div class="fact-block"><span>Market</span><strong>${esc(universeMarketLabel(members))}</strong></div><div class="fact-block"><span>Members</span><strong>${esc(members.map(member => member.split(':').pop()).join(' · ') || 'No resolved members')}</strong></div><div class="fact-block"><span>Selection</span><strong>${esc(item.universe_type === 'STATIC_LIST' ? 'Fixed list' : 'Rules based')}</strong></div></div><div class="usage">Usage <strong>${universeUsageCount(asset.library_asset_id)} Research</strong></div><div class="card-actions"><button type="button" class="primary" data-action="library-use" data-kind="UNIVERSE" data-id="${esc(asset.library_asset_id)}">Use in Research</button><button type="button" data-action="library-new-version" data-kind="UNIVERSE" data-id="${esc(asset.library_asset_id)}">Create New Version</button><button type="button" data-action="view-usage" data-kind="UNIVERSE" data-id="${esc(asset.library_asset_id)}">View Usage</button><button type="button" data-action="universe-details" data-id="${esc(asset.source_object_id)}">Details</button></div></article>`;
    }).join('') || '<div class="empty-state"><h2>No published Universe</h2><p>Validate a Universe in Research, then publish it to Library.</p></div>'}</div>`;
  } else if (state.libraryTab === 'factor') {
    const items = latestByName(state.library.filter(item => item.component_type === 'FACTOR'));
    target.innerHTML = `<div class="card-stack">${items.map(asset => factorCard({...asset.content, version: asset.version, library_asset_id: asset.library_asset_id}, true, definitionUsageCount(asset.library_asset_id))).join('') || '<div class="empty-state"><h2>No validated Factor</h2><p>Validate a Factor in Research and it will appear here automatically.</p></div>'}</div>`;
  } else if (state.libraryTab === 'alpha') {
    const items = latestByName(state.library.filter(item => item.component_type === 'ALPHA'));
    target.innerHTML = `<div class="card-stack">${items.map(asset => alphaCard({...asset.content, version: asset.version, library_asset_id: asset.library_asset_id}, true, definitionUsageCount(asset.library_asset_id))).join('') || '<div class="empty-state"><h2>No published Alpha</h2><p>Publish all required Factors before publishing an Alpha.</p></div>'}</div>`;
  } else if (state.libraryTab === 'requirements') {
    const items = latestByName(state.library.filter(item => item.component_type === 'REQUIREMENTS'));
    const drafts = arr(state.libraryRequirementDrafts);
    const card = asset => {
      const spec = asset.spec || asset.content?.spec || {};
      const scope = spec.scope || {}; const time = spec.time || {}; const data = spec.data || {};
      return `<article class="component-card"><div class="card-header"><div><h3>${esc(asset.name)}</h3><small>Library · v${esc(asset.version)} · Current version</small></div>${statusChip('Published')}</div><div class="fact-grid"><div class="fact-block"><span>Scope</span><strong>${esc(scope.provider || '-')} ${esc(scope.market || '')} · ${esc(arr(scope.instruments?.include).join(', ') || 'Rule based')}</strong></div><div class="fact-block"><span>Data</span><strong>${esc(data.frequency || '-')} · ${esc(arr(data.fields).join(', ') || '-')}</strong></div><div class="fact-block"><span>Time</span><strong>${esc(formatDate(time.start))} → ${esc(time.end === 'LATEST_AVAILABLE' ? 'Latest' : formatDate(time.end))}</strong></div></div><div class="usage">Usage <strong>${esc(asset.usage_count || 0)} Research</strong></div><div class="card-actions"><button class="primary" data-action="library-use" data-kind="REQUIREMENTS" data-id="${esc(asset.library_asset_id)}">Use in Research</button><button data-action="edit-library-requirement" data-id="${esc(asset.library_asset_id)}">Edit</button><button data-action="view-usage" data-kind="REQUIREMENTS" data-id="${esc(asset.library_asset_id)}">View Usage</button><button data-action="library-asset-details" data-id="${esc(asset.library_asset_id)}">Details</button></div></article>`;
    };
    target.innerHTML = `<div class="requirements-library-head"><div><h2>Requirements</h2><p>Reusable data contracts, authored and versioned entirely inside Library.</p></div><button class="primary" data-action="new-library-requirement">New Requirement</button></div>${drafts.length ? `<section class="library-drafts"><span class="eyebrow">DRAFTS</span><div class="card-stack">${drafts.map(draft => `<article class="requirement-card"><div class="card-header"><div><h3>${esc(draft.name)}</h3><small>Library Draft${draft.base_asset_version ? ` · based on v${esc(draft.base_asset_version)}` : ''}</small></div>${statusChip('Draft')}</div><div class="card-actions"><button data-action="open-library-draft" data-id="${esc(draft.draft_id)}">Continue Editing</button><button class="primary" data-action="publish-library-draft" data-id="${esc(draft.draft_id)}">Publish</button></div></article>`).join('')}</div></section>` : ''}<div class="card-stack">${items.map(card).join('') || '<div class="empty-state"><h2>No published Requirements</h2><p>Create a Library Requirement directly, then publish it when ready for reuse.</p></div>'}</div>`;
  } else if (state.libraryTab === 'requirements-legacy') {
    const items = latestByName(state.library.filter(item => item.component_type === 'REQUIREMENTS'));
    target.innerHTML = `<div class="library-intro">Requirements are reusable data contracts: instruments, fields, frequency, and history range. Actual datasets remain in Data Catalog.</div><div class="card-stack">${items.map(asset => `<article class="component-card"><div class="card-header"><div><h3>${esc(asset.name)}</h3><small>Library · v${esc(asset.version)} · Current version</small></div>${statusChip('Published')}</div><div class="fact-grid"><div class="fact-block"><span>Frequency</span><strong>${esc(asset.content.context?.frequency || '-')}</strong></div><div class="fact-block"><span>Fields</span><strong>${esc([...new Set(arr(asset.content.requirements).flatMap(item => arr(item.fields)))].join(', ') || '-')}</strong></div><div class="fact-block"><span>Required Datasets</span><strong>${arr(asset.content.requirements).length}</strong></div></div><div class="usage">Usage <strong>${requirementUsageCount(asset.library_asset_id)} Research</strong></div><div class="card-actions"><button type="button" class="primary" data-action="library-use" data-kind="REQUIREMENTS" data-id="${esc(asset.library_asset_id)}">Use in Research</button><button type="button" data-action="library-new-version" data-kind="REQUIREMENTS" data-id="${esc(asset.library_asset_id)}">Create New Version</button><button type="button" data-action="view-usage" data-kind="REQUIREMENTS" data-id="${esc(asset.library_asset_id)}">View Usage</button><button type="button" data-action="library-asset-details" data-id="${esc(asset.library_asset_id)}">Details</button></div></article>`).join('') || '<div class="empty-state"><h2>No published Requirements</h2><p>Define and verify Requirements in Research, then publish them to Library.</p></div>'}</div>`;
  } else {
    target.innerHTML = `<div class="empty-state"><h2>Strategy Library is not enabled</h2><p>Strategy will be added after the Research workflow and its approval boundary are complete.</p></div>`;
  }
}

function requirementLibraryCard(asset) {
  const spec = asset.spec || asset.content?.spec || {};
  const scope = spec.scope || {}; const time = spec.time || {}; const data = spec.data || {};
  const status = asset.data_status || {status: 'PENDING', coverage: 'Data status has not been checked.'};
  const resolved = {status: status.status || 'CHECKING', coverage: status.status === 'READY' && status.latest_available ? `Complete coverage through ${formatDateTime(status.latest_available)}` : status.coverage || friendlyStatus(status.status), preparation: status.preparation || null};
  const instruments = arr(scope.instruments?.include).map(requirementInstrumentLabel).join(', ') || 'Rule based';
  return `<article class="requirement-library-card"><div class="card-header"><div><h3>${esc(asset.name)}</h3><small>${esc(scope.provider || '-')} ${esc(scope.market || '')} · ${esc(instruments)}</small></div>${statusChip(friendlyStatus(resolved.status))}</div><div class="requirement-essentials library"><div><span>Scope</span><strong>${esc(scope.provider || '-')} ${esc(scope.market || '')} · ${esc(instruments)}</strong></div><div><span>Data</span><strong>${esc(data.frequency || '-')} ${esc(data.dataset_type || 'Data')} · ${esc(arr(data.fields).join(' / ') || '-')}</strong></div><div><span>Time</span><strong>${esc(formatDate(time.start))} → ${esc(time.end === 'LATEST_AVAILABLE' ? 'Latest' : formatDate(time.end))}</strong></div></div><div class="requirement-library-bottom"><div>${preparationBlock(resolved)}</div><div class="requirement-usage"><span>Used by</span><strong>${esc(asset.usage_count || 0)} Research</strong></div></div><div class="card-actions requirement-actions"><button data-action="edit-library-requirement" data-id="${esc(asset.library_asset_id)}">Edit</button><button data-action="save-as-library-requirement" data-id="${esc(asset.library_asset_id)}">Save As</button><button data-action="library-asset-details" data-id="${esc(asset.library_asset_id)}">Details</button><details class="overflow-menu"><summary aria-label="More actions">···</summary><div><button data-action="view-usage" data-id="${esc(asset.library_asset_id)}">View Usage</button><button data-action="archive-library-requirement" data-id="${esc(asset.library_asset_id)}">Archive</button></div></details></div></article>`;
}

async function renderRequirementLibrary() {
  await ensureLibraryGroups('REQUIREMENTS');
  const groups = state.libraryGroupsByType.REQUIREMENTS || [];
  const membership = state.libraryGroupMembershipByType.REQUIREMENTS || {};
  const activeGroup = state.libraryActiveGroup.REQUIREMENTS || '';
  const filters = state.requirementFilters;
  const all = state.library.filter(item => item.component_type === 'REQUIREMENTS');
  const ungroupedCount = all.filter(item => !membership[item.library_asset_id]).length;
  const providers = [...new Set(all.map(item => item.spec?.scope?.provider).filter(Boolean))].sort();
  const dataTypes = [...new Set(all.map(item => item.spec?.data?.dataset_type).filter(Boolean))].sort();
  const statuses = [...new Set(all.map(item => item.data_status?.status).filter(Boolean))].sort();
  let items = all.filter(asset => {
    return (!filters.provider || asset.spec?.scope?.provider === filters.provider)
      && (!filters.dataType || asset.spec?.data?.dataset_type === filters.dataType)
      && (!filters.status || asset.data_status?.status === filters.status)
      && (!filters.usage || (filters.usage === 'used' ? Number(asset.usage_count) > 0 : Number(asset.usage_count) === 0));
  });
  if (activeGroup === UNGROUPED_KEY) items = items.filter(item => !membership[item.library_asset_id]);
  else if (activeGroup) items = items.filter(item => membership[item.library_asset_id] === activeGroup);
  const ready = all.filter(item => item.data_status?.status === 'READY').length;
  const preparing = all.filter(item => ['CHECKING', 'QUEUED', 'PREPARING'].includes(item.data_status?.status)).length;
  const attention = all.filter(item => ['NEEDS_ATTENTION', 'FAILED', 'UNAVAILABLE'].includes(item.data_status?.status)).length;
  const rail = libraryGroupRailHtml('REQUIREMENTS', all.length, ungroupedCount, activeGroup, groups, null);
  const batchBar = libraryBatchBarHtml('REQUIREMENTS', groups);
  const cards = items.map(asset => libraryCardWithGroupTools(requirementLibraryCard(asset), 'REQUIREMENTS', asset.library_asset_id, groups, membership[asset.library_asset_id] || '')).join('')
    || '<div class="empty-state compact"><h2>No matching Requirements</h2><p>Adjust the filters or create a new Requirement.</p></div>';
  $('libraryTabContent').innerHTML = `<section class="requirements-library"><div class="requirements-page-head"><div><span class="eyebrow">LIBRARY · REQUIREMENTS</span><h2>Requirements</h2><p>Every shared data contract is maintained by the backend; live download progress appears on each card.</p><div class="requirement-count-line"><strong>${all.length} Requirement${all.length === 1 ? '' : 's'}</strong><span>${ready} Ready</span><span>${preparing} Preparing</span>${attention ? `<span>${attention} Errors</span>` : ''}<span class="last-updated">Last updated: ${esc(new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}))}</span></div></div><button class="primary" data-action="new-library-requirement">New Requirement</button></div><div class="requirements-toolbar"><label class="search-field"><span>Search</span><input id="requirementSearch" value="${esc(filters.search)}" placeholder="Name or instrument"></label><label><span>Provider</span><select id="requirementProvider"><option value="">All</option>${providers.map(value => `<option value="${esc(value)}" ${filters.provider === value ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select></label><label><span>Data Type</span><select id="requirementDataType"><option value="">All</option>${dataTypes.map(value => `<option value="${esc(value)}" ${filters.dataType === value ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select></label><label><span>Status</span><select id="requirementStatus"><option value="">All</option>${statuses.map(value => `<option value="${esc(value)}" ${filters.status === value ? 'selected' : ''}>${esc(friendlyStatus(value))}</option>`).join('')}</select></label><label><span>Usage</span><select id="requirementUsage"><option value="">All</option><option value="used" ${filters.usage === 'used' ? 'selected' : ''}>In use</option><option value="unused" ${filters.usage === 'unused' ? 'selected' : ''}>Not in use</option></select></label></div><div class="library-grouped-layout">${rail}<div class="library-group-main">${batchBar}<div class="requirement-card-list library-list">${cards}</div></div></div></section>`;
  if (filters.search) {
    const initialQuery = filters.search.toLowerCase();
    document.querySelectorAll('.requirement-library-card').forEach(card => { card.hidden = !card.textContent.toLowerCase().includes(initialQuery); });
  }
  $('requirementSearch').addEventListener('input', () => {
    const query = $('requirementSearch').value.trim().toLowerCase();
    state.requirementFilters.search = $('requirementSearch').value.trim();
    document.querySelectorAll('.requirement-library-card').forEach(card => { card.hidden = Boolean(query && !card.textContent.toLowerCase().includes(query)); });
  });
  ['requirementProvider', 'requirementDataType', 'requirementStatus', 'requirementUsage'].forEach(id => $(id).addEventListener('change', () => {
    state.requirementFilters = {search: $('requirementSearch').value.trim(), provider: $('requirementProvider').value, dataType: $('requirementDataType').value, status: $('requirementStatus').value, usage: $('requirementUsage').value};
    renderRequirementLibrary();
  }));
}

function sharedUniverseCard(item) {
  const resolution = item.current_resolution || {};
  const members = arr(resolution.instrument_ids);
  return `<article class="component-card universe-shared-card"><div class="card-header"><div><h3>${esc(item.name)}</h3><small>Stable ID · revision ${esc(item.revision_number)} · ${esc(item.type)}</small></div>${statusChip(item.status === 'VALID' ? 'Shared' : friendlyStatus(item.status))}</div><div class="fact-grid"><div class="fact-block"><span>Resolved</span><strong>${esc(resolution.member_count || 0)} Instruments${resolution.combination_count ? ` · ${esc(resolution.combination_count)} combinations` : ''}</strong></div><div class="fact-block"><span>Usage</span><strong>${esc(item.active_research_count || 0)} active Research</strong></div><div class="fact-block"><span>Updated</span><strong>${esc(formatDate(item.updated_at))}</strong></div></div><div class="member-tags">${members.slice(0, 10).map(value => `<span>${esc(value.split(':').pop())}</span>`).join('')}${members.length > 10 ? `<span>+${members.length - 10}</span>` : ''}</div><div class="card-actions"><button type="button" class="primary" data-action="edit-shared-universe" data-id="${esc(item.universe_id)}">Edit</button><button type="button" data-action="copy-shared-universe" data-id="${esc(item.universe_id)}">Copy</button><button type="button" data-action="preview-shared-universe" data-id="${esc(item.universe_id)}">Preview</button><button type="button" data-action="shared-universe-usage" data-id="${esc(item.universe_id)}">Usage</button><button type="button" data-action="shared-universe-details" data-id="${esc(item.universe_id)}">Details</button><details class="overflow-menu"><summary aria-label="More actions">···</summary><div><button type="button" class="danger-quiet" data-action="archive-library-universe" data-id="${esc(item.universe_id)}">Archive</button></div></details></div></article>`;
}

async function renderUniverseLibrary() {
  await ensureLibraryGroups('UNIVERSE');
  const groups = state.libraryGroupsByType.UNIVERSE || [];
  const membership = state.libraryGroupMembershipByType.UNIVERSE || {};
  const activeGroup = state.libraryActiveGroup.UNIVERSE || '';
  const all = state.sharedUniverses;
  const ungroupedCount = all.filter(item => !membership[item.universe_id]).length;
  let items = all;
  if (activeGroup === UNGROUPED_KEY) items = items.filter(item => !membership[item.universe_id]);
  else if (activeGroup) items = items.filter(item => membership[item.universe_id] === activeGroup);
  const rail = libraryGroupRailHtml('UNIVERSE', all.length, ungroupedCount, activeGroup, groups, {
    inputId: 'libraryGroupSearchInput',
    placeholder: 'Search Universe...',
    value: state.libraryGroupSearch,
  });
  const batchBar = libraryBatchBarHtml('UNIVERSE', groups);
  const cards = items.map(item => libraryCardWithGroupTools(sharedUniverseCard(item), 'UNIVERSE', item.universe_id, groups, membership[item.universe_id] || '')).join('')
    || '<div class="empty-state compact"><h2>No Universe here</h2><p>Create the first shared Universe.</p></div>';
  const target = $('libraryTabContent');
  target.innerHTML = `<section><div class="requirements-page-head"><div><span class="eyebrow">LIBRARY · UNIVERSE</span><h2>Universe</h2><p>Every Universe has a stable identity and immutable revision history. Research uses it by reference.</p></div><button type="button" class="primary" data-action="new-library-universe">Create Universe</button></div><div class="library-grouped-layout">${rail}<div class="library-group-main">${batchBar}<div class="card-stack">${cards}</div></div></div></section>`;
  attachLibraryGroupSearch(target, 'libraryGroupSearchInput');
}

async function renderLibraryTab() {
  if (state.libraryTab === 'universe') await renderUniverseLibrary();
  else if (state.libraryTab === 'requirements') await renderRequirementLibrary();
  else await renderLibraryTabLegacy();
}

const refreshCurrentLibraryTab = renderLibraryTab;

function renderGlobalRuns() {
  const runs = state.runs.filter(item => ['FACTOR_EVALUATION', 'ALPHA_EVALUATION', 'RESEARCH_BACKTEST'].includes(item.run_type));
  $('appView').innerHTML = `<header class="page-heading"><div><span class="eyebrow">RUNS</span><h1>Runs</h1><p>Factor Evaluation, Alpha Evaluation, and Research Backtest history.</p></div></header><div class="table-wrap"><table class="data-table"><thead><tr><th>Research</th><th>Run</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>${runs.map(run => {
    const project = state.projects.find(item => item.project_id === run.project_id);
    return `<tr><td><a href="/research/${encodeURIComponent(run.project_id)}">${esc(project?.title || 'Research')}</a></td><td>${esc(testLabel(run.run_type, run))}</td><td>${statusChip(friendlyStatus(run.status))}</td><td>${esc(formatDate(run.created_at))}</td><td><button type="button" data-action="run-details" data-id="${esc(run.run_id)}">View Result</button></td></tr>`;
  }).join('') || '<tr><td colspan="5">No Research runs yet.</td></tr>'}</tbody></table></div>`;
}

async function renderDataCatalog() {
  const catalog = await api('/api/research/data/catalog').catch(() => []);
  $('appView').innerHTML = `<header class="page-heading"><div><span class="eyebrow">DATA CATALOG</span><h1>Data Catalog</h1><p>Available datasets for Research.</p></div></header><div class="table-wrap"><table class="data-table"><thead><tr><th>Dataset</th><th>Provider</th><th>Frequency</th><th>Status</th></tr></thead><tbody>${arr(catalog).map(item => `<tr><td><strong>${esc(item.logical_name || item.dataset_id || 'Dataset')}</strong><small>${esc(item.instrument_id || '')}</small></td><td>${esc(item.provider || item.gateway || '-')}</td><td>${esc(item.frequency || item.interval || '-')}</td><td>${statusChip(friendlyStatus(item.status || 'Ready'))}</td></tr>`).join('') || '<tr><td colspan="4">No datasets are registered.</td></tr>'}</tbody></table></div>`;
}

async function renderApprovals() {
  const dashboard = await api('/api/agent/dashboard?limit=100').catch(() => ({}));
  const pending = arr(dashboard.pending_approvals);
  $('appView').innerHTML = `<header class="page-heading"><div><span class="eyebrow">APPROVALS</span><h1>Approvals</h1><p>Items waiting for human review. Research publication does not approve trading.</p></div><a class="button-link" href="/agent-monitor">Open Agent Monitor</a></header><div class="table-wrap"><table class="data-table"><thead><tr><th>Item</th><th>Status</th><th>Submitted</th></tr></thead><tbody>${pending.map(item => `<tr><td><strong>${esc(item.name || item.title || item.approval_id || 'Approval')}</strong></td><td>${statusChip('Waiting for Human')}</td><td>${esc(formatDate(item.created_at || item.updated_at))}</td></tr>`).join('') || '<tr><td colspan="3">No pending approvals.</td></tr>'}</tbody></table></div>`;
}

function newResearchDialog() {
  openDialog('New Research', 'RESEARCH', `<form id="researchForm" class="form-stack"><label>Name<input id="researchName" maxlength="120" required placeholder="BTC 1h MA Crossover Validation"></label><label>Research Objective<textarea id="researchGoal" required placeholder="Describe the hypothesis and what you want to validate."></textarea></label><div class="form-actions"><button type="submit" class="primary">Create Research</button></div></form>`);
  $('researchForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const created = await api('/api/research/projects', {method: 'POST', body: JSON.stringify({title: $('researchName').value.trim(), objective: $('researchGoal').value.trim()})});
      window.location.assign(`/research/${encodeURIComponent(created.project_id)}`);
    } catch (error) { notify(error.message, true); }
  });
}

function operatorName(id) {
  const names = {pct_change: 'Percentage Change', rolling_mean: 'Moving Average', rolling_std: 'Rolling Standard Deviation', rolling_zscore: 'Rolling Z-Score', rolling_min: 'Rolling Minimum', rolling_max: 'Rolling Maximum', rolling_return_std: 'Return Volatility', ema: 'Exponential Moving Average', ma_crossover: 'Moving Average Crossover'};
  return names[id] || String(id || '').split('_').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function operatorDescription(id) {
  const descriptions = {pct_change: 'Measures the percentage change over the selected window.', rolling_mean: 'Calculates the simple moving average over the selected window.', rolling_std: 'Measures dispersion over the selected window.', rolling_zscore: 'Standardizes the latest value against recent history.', rolling_min: 'Returns the lowest value in the selected window.', rolling_max: 'Returns the highest value in the selected window.', rolling_return_std: 'Measures the volatility of recent returns.', ema: 'Calculates an exponentially weighted moving average.', ma_crossover: 'Returns a positive signal on a golden cross and a negative signal on a death cross.'};
  return descriptions[id] || 'A deterministic formula supported by the research engine.';
}

function universeEditorMemberFormat(definition = {}) {
  return definition.type === 'multi_leg_set' ? 'instrument_group' : 'individual_instruments';
}

function universeEditorBuildMethod(definition = {}) {
  if (definition.type === 'instrument_set') return 'instrument_list';
  if (definition.type === 'benchmark_set') return 'benchmark_constituents';
  if (definition.type === 'composite_set') return 'combine_universes';
  return ({manual: 'manual_groups', cartesian_product: 'cross_source_groups', unordered_combination: 'unique_groups', permutation: 'ordered_groups'})[definition.combination?.mode] || 'unique_groups';
}

function universeEditorInstrumentRecord(value, details = {}) {
  const instrumentId = String(details.instrument_id || value || '').trim();
  const parts = instrumentId.split(':');
  const rawType = details.market_kind || details.asset_class || parts[0] || 'Instrument';
  return {
    instrument_id: instrumentId,
    symbol: details.symbol || parts.at(-1) || instrumentId,
    name: details.display_name || details.display_symbol || details.symbol || parts.at(-1) || instrumentId,
    venue: String(details.venue || parts.at(-2) || '').toUpperCase(),
    type: String(rawType).replaceAll('_', ' ').replace(/\b\w/g, value => value.toUpperCase()),
    status: details.status || 'Selected',
  };
}

function universeEditorSources() {
  const editor = state.universeEditor || {};
  return state.sharedUniverses.filter(item => item.universe_id !== editor.base?.universe_id && item.type !== 'multi_leg_set');
}

function universeEditorDefaultDrafts(definition) {
  const sources = universeEditorSources();
  const sourceId = definition.legs?.[0]?.source_universe_id || sources[0]?.universe_id || '';
  const legCount = Math.max(2, arr(definition.legs).length || arr(definition.manual_tuples)[0]?.length || 2);
  return {
    combine_universes: {operator: definition.expression?.operator || 'union', inputs: arr(definition.expression?.inputs).map(item => item.universe_id)},
    benchmark_constituents: {
      benchmarkId: definition.benchmark?.benchmark_id || '000300.SH',
      provider: definition.benchmark?.provider || 'OPENBB',
      effectiveAt: definition.benchmark?.effective_at || new Date().toISOString().slice(0, 10),
      constituents: arr(definition.constituents).map(item => ({instrument_id: item.instrument_id, weight: item.weight})),
    },
    manual_groups: {groupSize: legCount, groups: arr(definition.manual_tuples).map(row => [...row])},
    cross_source_groups: {
      legs: arr(definition.legs).length ? arr(definition.legs).map(item => ({...item})) : [{id: 'leg_1', name: 'Leg 1', source_universe_id: sourceId}, {id: 'leg_2', name: 'Leg 2', source_universe_id: sourceId}],
      allowSame: Boolean(definition.combination?.allow_same_instrument), maxGroups: Number(definition.combination?.max_combinations || 10000),
    },
    unique_groups: {sourceId, groupSize: legCount, maxGroups: Number(definition.combination?.max_combinations || 10000)},
    ordered_groups: {sourceId, groupSize: legCount, maxGroups: Number(definition.combination?.max_combinations || 10000)},
  };
}

function captureUniverseEditorState() {
  const editor = state.universeEditor;
  if (!editor?.methodDrafts) return;
  const draft = editor.methodDrafts[editor.buildMethod];
  if (editor.buildMethod === 'combine_universes' && $('universeOperator')) {
    draft.operator = $('universeOperator').value;
    draft.inputs = [...document.querySelectorAll('.universe-expression-source')].map(node => node.value).filter(Boolean);
  } else if (editor.buildMethod === 'benchmark_constituents' && $('universeBenchmarkId')) {
    draft.benchmarkId = $('universeBenchmarkId').value.trim();
    draft.provider = $('universeBenchmarkProvider').value.trim();
    draft.effectiveAt = $('universeBenchmarkEffectiveAt').value;
    draft.constituents = $('universeBenchmarkConstituents').value.split(/\r?\n/).map(line => {
      const [instrument_id, weight] = line.split(/[,;\t]+/).map(value => value.trim());
      return {instrument_id, weight: weight === '' || weight == null ? null : Number(weight)};
    }).filter(item => item.instrument_id);
  } else if (editor.buildMethod === 'manual_groups' && $('universeManualGroupSize')) {
    draft.groupSize = Number($('universeManualGroupSize').value || 2);
    draft.groups = [...document.querySelectorAll('.universe-manual-row')].map(row => [...row.querySelectorAll('.universe-manual-instrument')].map(input => input.value.trim()));
  } else if (editor.buildMethod === 'cross_source_groups' && $('universeLegs')) {
    draft.legs = [...document.querySelectorAll('.universe-leg-row')].map((row, index) => ({id: `leg_${index + 1}`, name: row.querySelector('.universe-leg-name').value.trim() || `Leg ${index + 1}`, source_universe_id: row.querySelector('.universe-leg-source').value}));
    draft.allowSame = $('universeAllowSame').checked;
    draft.maxGroups = Number($('universeMaxCombinations').value || 10000);
  } else if (['unique_groups', 'ordered_groups'].includes(editor.buildMethod) && $('universeGroupSource')) {
    draft.sourceId = $('universeGroupSource').value;
    draft.groupSize = Number($('universeGroupSize').value || 2);
    draft.maxGroups = Number($('universeMaxCombinations').value || 10000);
  }
}

function universeEditorDefinitionFromForm() {
  const editor = state.universeEditor || {};
  captureUniverseEditorState();
  const definition = JSON.parse(JSON.stringify(editor.definition || {}));
  definition.name = $('universeName').value.trim();
  definition.description = $('universeDescription').value.trim();
  definition.tags = $('universeTags').value.split(',').map(value => value.trim()).filter(Boolean);
  delete definition.members; delete definition.constituents; delete definition.benchmark; delete definition.expression; delete definition.legs; delete definition.combination; delete definition.manual_tuples;
  if (editor.buildMethod === 'instrument_list') {
    definition.type = 'instrument_set';
    definition.members = editor.instrumentMembers.map(item => item.instrument_id);
  } else if (editor.buildMethod === 'benchmark_constituents') {
    const draft = editor.methodDrafts.benchmark_constituents;
    definition.type = 'benchmark_set';
    definition.benchmark = {benchmark_id: draft.benchmarkId, provider: draft.provider, effective_at: draft.effectiveAt, weighting: 'PROVIDED'};
    definition.constituents = draft.constituents;
  } else if (editor.buildMethod === 'benchmark_constituents') {
    const draft = editor.methodDrafts.benchmark_constituents;
    const rows = arr(draft.constituents).map(item => `${item.instrument_id}${item.weight == null ? '' : `, ${item.weight}`}`).join('\n');
    $('universeTypeFields').innerHTML = `<section class="form-section"><div><span class="eyebrow">POINT-IN-TIME BENCHMARK</span><h3>Benchmark Constituents and Weights</h3><p>Each saved revision freezes one effective-date constituent snapshot. Create a new revision for each rebalance.</p></div><div class="form-grid"><label>Benchmark ID<input id="universeBenchmarkId" value="${esc(draft.benchmarkId)}" placeholder="000300.SH"></label><label>Membership Provider<input id="universeBenchmarkProvider" value="${esc(draft.provider)}" placeholder="OPENBB"></label><label>Effective Date<input id="universeBenchmarkEffectiveAt" type="date" value="${esc(String(draft.effectiveAt || '').slice(0, 10))}"></label></div><label>Canonical Instrument ID, Weight<textarea id="universeBenchmarkConstituents" rows="14" placeholder="equity:XSHG:600519, 0.05&#10;equity:XSHE:000001, 0.03">${esc(rows)}</textarea></label><p class="form-help">Weights are normalized by the backend. Omit every weight to use equal weighting.</p></section>`;
  } else if (editor.buildMethod === 'combine_universes') {
    const draft = editor.methodDrafts.combine_universes;
    definition.type = 'composite_set';
    definition.expression = {operator: draft.operator, inputs: draft.inputs.map(universe_id => ({universe_id}))};
  } else {
    const draft = editor.methodDrafts[editor.buildMethod];
    definition.type = 'multi_leg_set';
    if (editor.buildMethod === 'manual_groups') {
      definition.legs = Array.from({length: draft.groupSize}, (_, index) => ({id: `leg_${index + 1}`, name: `Leg ${index + 1}`}));
      definition.combination = {mode: 'manual', allow_same_instrument: false, treat_reversed_as_same: false, max_combinations: 10000};
      definition.manual_tuples = draft.groups.filter(row => row.length === draft.groupSize && row.every(Boolean)).map(row => row.slice(0, draft.groupSize));
    } else if (editor.buildMethod === 'cross_source_groups') {
      definition.legs = draft.legs;
      definition.combination = {mode: 'cartesian_product', allow_same_instrument: draft.allowSame, treat_reversed_as_same: false, max_combinations: draft.maxGroups};
    } else {
      definition.legs = Array.from({length: draft.groupSize}, (_, index) => ({id: `leg_${index + 1}`, name: `Leg ${index + 1}`, source_universe_id: draft.sourceId}));
      definition.combination = {mode: editor.buildMethod === 'unique_groups' ? 'unordered_combination' : 'permutation', allow_same_instrument: false, treat_reversed_as_same: editor.buildMethod === 'unique_groups', max_combinations: draft.maxGroups};
    }
  }
  return definition;
}

function universeEditorChoiceCard(value, selected, title, description, example = '') {
  return `<label class="universe-choice-card ${selected ? 'selected' : ''}"><input type="radio" name="universeMemberFormat" value="${esc(value)}" ${selected ? 'checked' : ''}><span><strong>${esc(title)}</strong><small>${esc(description)}</small>${example ? `<em>${esc(example)}</em>` : ''}</span></label>`;
}

function universeEditorMethodCard(value, selected, title, description) {
  return `<label class="universe-method-card ${selected ? 'selected' : ''}"><input type="radio" name="universeBuildMethod" value="${esc(value)}" ${selected ? 'checked' : ''}><span><strong>${esc(title)}</strong><small>${esc(description)}</small></span></label>`;
}

function renderUniverseEditorControls() {
  const editor = state.universeEditor;
  if (!editor || !$('universeFormatControls')) return;
  const individual = editor.memberFormat === 'individual_instruments';
  const methods = individual ? [
    ['instrument_list', 'Instrument List', 'Search, paste, or import individual Instruments.'],
    ['benchmark_constituents', 'Benchmark Constituents', 'Create a point-in-time weighted benchmark such as CSI300.'],
    ['combine_universes', 'Combine Existing Universes', 'Create a union, intersection, or directional difference.'],
  ] : [
    ['manual_groups', 'Manual Groups', 'Enter each final Instrument Group directly.'],
    ['cross_source_groups', 'Cross-source Groups', 'Choose one Instrument from each Leg source.'],
    ['unique_groups', 'Unique Groups', 'Choose unordered Groups from one Universe.'],
    ['ordered_groups', 'Ordered Groups', 'Choose ordered Groups from one Universe.'],
  ];
  $('universeFormatControls').innerHTML = `<section class="form-section"><div><span class="eyebrow">MEMBER FORMAT</span><h3>What is each Universe member?</h3></div><div class="universe-choice-grid">${universeEditorChoiceCard('individual_instruments', individual, 'Individual Instruments', 'Each member is one Instrument.', 'BTCUSDT, ETHUSDT')}${universeEditorChoiceCard('instrument_group', !individual, 'Instrument Groups', 'Each member contains two or more Instruments.', 'BTCUSDT – ETHUSDT')}</div></section><section class="form-section"><div><span class="eyebrow">BUILD METHOD</span><h3>${individual ? 'Build members by' : 'Build Groups by'}</h3></div><div class="universe-method-grid">${methods.map(([value, title, description]) => universeEditorMethodCard(value, editor.buildMethod === value, title, description)).join('')}</div></section>`;
}

function universeEditorSourceOptions(selected = '') {
  return universeEditorSources().map(item => `<option value="${esc(item.universe_id)}" ${item.universe_id === selected ? 'selected' : ''}>${esc(item.name)} · ${esc(item.current_resolution?.member_count || 0)} Instruments</option>`).join('');
}

function renderUniverseEditorInstrumentTable() {
  const editor = state.universeEditor;
  if (!$('universeInstrumentTable')) return;
  $('universeInstrumentCount').textContent = `${editor.instrumentMembers.length} Instrument${editor.instrumentMembers.length === 1 ? '' : 's'}`;
  $('universeInstrumentTable').innerHTML = editor.instrumentMembers.map((item, index) => `<tr><td><strong>${esc(item.symbol)}</strong><small class="technical-id">${esc(item.instrument_id)}</small></td><td>${esc(item.name)}</td><td>${esc(item.venue || '—')}</td><td>${esc(item.type)}</td><td><span class="instrument-status">${esc(item.status)}</span></td><td><button type="button" data-action="remove-universe-instrument" data-index="${index}">Remove</button></td></tr>`).join('') || '<tr><td colspan="6"><div class="instrument-empty">No Instruments yet. Search or paste a list to add members.</div></td></tr>';
}

function renderUniverseEditorSearchResults() {
  const editor = state.universeEditor;
  if (!$('universeInstrumentResults')) return;
  $('universeInstrumentResults').innerHTML = arr(editor.searchResults).map((item, index) => `<div class="instrument-result"><div><strong>${esc(item.display_symbol || item.symbol || item.instrument_id)}</strong><span>${esc(item.display_name || item.symbol || '')}</span><small>${esc(String(item.venue || '').toUpperCase())} · ${esc(item.market_kind || item.asset_class || 'Instrument')} · ${esc(item.status || '')}</small><code>${esc(item.instrument_id || '')}</code></div><button type="button" data-action="add-universe-search-result" data-index="${index}">Add</button></div>`).join('') || (editor.searchMessage ? `<p class="muted">${esc(editor.searchMessage)}</p>` : '');
}

function universeEditorDiscoveryProviders() {
  return arr(state.dataCapabilities.providers);
}

function universeEditorDiscoveryScope(definition = {}, resolution = {}) {
  const providers = universeEditorDiscoveryProviders();
  const rawMembers = [
    ...arr(definition.members),
    ...arr(resolution.instrument_ids),
  ];
  const instrumentIds = rawMembers.map(item => String(
    typeof item === 'string'
      ? item
      : item?.instrument_id || item?.canonical_instrument_id || '',
  ).trim()).filter(Boolean);
  const firstParts = instrumentIds[0]?.split(':') || [];
  const assetClass = String(firstParts[0] || '').toLowerCase();
  const venue = String(firstParts[1] || '').toUpperCase();

  const scopeFor = (providerId, marketId) => {
    const provider = providers.find(item => item.id === providerId);
    const market = arr(provider?.markets).find(item => item.id === marketId);
    return provider && market ? {provider: provider.id, market: market.id} : null;
  };
  if (assetClass === 'crypto_spot' && venue === 'BINANCE') {
    return scopeFor('BINANCE', 'SPOT') || {provider: 'BINANCE', market: 'SPOT'};
  }
  if (assetClass === 'polymarket_binary' && venue === 'POLYMARKET') {
    return scopeFor('POLYMARKET', 'BINARY') || {provider: 'POLYMARKET', market: 'BINARY'};
  }
  if (assetClass === 'macro' && venue === 'FRED') {
    return scopeFor('FRED', 'MACRO') || {provider: 'FRED', market: 'MACRO'};
  }
  if (assetClass === 'equity' && ['XNAS', 'XNYS'].includes(venue)) {
    const eligible = providers.filter(provider => arr(provider.markets).some(market => (
      market.id === venue && market.search_category === 'equity'
    )));
    eligible.sort((left, right) => {
      const score = provider => (
        (provider.gateway === 'OPENBB' ? 8 : 0)
        + (provider.configured !== false ? 4 : 0)
        + (provider.online !== false ? 2 : 0)
        + (arr(provider.markets).some(market => market.id === venue && market.prepare_supported) ? 1 : 0)
      );
      return score(right) - score(left);
    });
    if (eligible[0]) return {provider: eligible[0].id, market: venue};
  }

  const automatic = providers.find(provider => provider.id === 'AUTO');
  const firstProvider = automatic || providers.find(provider => provider.configured !== false && provider.online !== false) || providers[0];
  return {
    provider: firstProvider?.id || '',
    market: arr(firstProvider?.markets)[0]?.id || '',
  };
}

function universeEditorDiscoveryOptionLabel(provider) {
  const status = provider.configured === false
    ? ' · not configured'
    : provider.online === false
      ? ' · offline'
      : provider.discovery === false
        ? ' · limited discovery'
        : '';
  return `${provider.label}${status}`;
}

function syncUniverseEditorDiscoveryScope(preferredMarket = '') {
  const editor = state.universeEditor;
  const providerSelect = $('universeDiscoverySource');
  const marketSelect = $('universeDiscoveryMarket');
  if (!editor || !providerSelect || !marketSelect) return;
  const providers = universeEditorDiscoveryProviders();
  const provider = providers.find(item => item.id === providerSelect.value)
    || providers.find(item => item.id === editor.discoveryProvider)
    || providers.find(item => item.id === 'AUTO')
    || providers[0];
  editor.discoveryProvider = provider?.id || '';
  marketSelect.innerHTML = arr(provider?.markets).map(market => `<option value="${esc(market.id)}">${esc(market.label)}</option>`).join('');
  const requestedMarket = preferredMarket || editor.discoveryMarket;
  if (arr(provider?.markets).some(market => market.id === requestedMarket)) marketSelect.value = requestedMarket;
  const market = arr(provider?.markets).find(item => item.id === marketSelect.value);
  editor.discoveryMarket = market?.id || '';
  if ($('universeDiscoveryAssetType')) $('universeDiscoveryAssetType').value = String(market?.asset_type || 'Instrument').replaceAll('_', ' ');
  if ($('universeInstrumentSearch')) {
    $('universeInstrumentSearch').placeholder = market?.search_category === 'polymarket'
      ? 'Search Polymarket questions'
      : market?.search_category === 'fred'
        ? 'Enter an exact FRED series ID'
        : market?.search_category === 'coingecko'
          ? 'Context only — not a Universe Instrument'
          : 'Search Instruments';
  }
  const availability = provider?.configured === false
    ? 'Not configured.'
    : provider?.online === false
      ? 'Currently offline.'
      : market?.search_category === 'coingecko'
        ? 'Context data only; it cannot be added as a Universe Instrument.'
        : market?.prepare_supported
          ? 'Discovery and historical preparation are available.'
          : 'Discovery or definition is available; historical preparation is not connected.';
  if ($('universeDiscoveryNote')) $('universeDiscoveryNote').textContent = `${provider?.description || ''} ${availability}`.trim();
}

function universeEditorExpressionRow(sourceId, index, operator) {
  const label = operator === 'difference' ? (index === 0 ? 'Base Universe' : 'Remove Universe') : `Universe ${index + 1}`;
  return `<div class="universe-expression-row"><label>${label}<select class="universe-expression-source">${universeEditorSourceOptions(sourceId)}</select></label>${index >= 2 ? '<button type="button" data-action="remove-universe-expression-input">Remove</button>' : ''}</div>`;
}

function universeEditorLegRow(leg, index) {
  return `<div class="universe-leg-row"><span class="leg-grip" aria-hidden="true">≡</span><input class="universe-leg-name" value="${esc(leg.name || `Leg ${index + 1}`)}" aria-label="Leg name"><select class="universe-leg-source">${universeEditorSourceOptions(leg.source_universe_id)}</select>${index >= 2 ? '<button type="button" data-action="remove-universe-leg">Remove</button>' : '<span></span>'}</div>`;
}

function universeEditorManualRow(values, groupSize, index) {
  return `<tr class="universe-manual-row">${Array.from({length: groupSize}, (_, leg) => `<td><input class="universe-manual-instrument" list="universeInstrumentSuggestions" value="${esc(values?.[leg] || '')}" placeholder="Symbol or Instrument ID" aria-label="Group ${index + 1}, Leg ${leg + 1}"></td>`).join('')}<td><button type="button" data-action="remove-manual-group">Remove</button></td></tr>`;
}

function renderUniverseEditorFields() {
  const editor = state.universeEditor;
  if (!editor || !$('universeTypeFields')) return;
  const build = editor.buildMethod;
  const sources = universeEditorSources();
  if (build === 'instrument_list') {
    const providers = universeEditorDiscoveryProviders();
    $('universeTypeFields').innerHTML = `<section class="form-section instrument-picker-section"><div class="section-heading"><div><span class="eyebrow">INSTRUMENTS</span><h3>Universe members</h3><p>Search available data sources; selected results are registered and saved as Canonical Instrument IDs.</p></div><strong id="universeInstrumentCount">0 Instruments</strong></div><div class="instrument-scope-grid"><label>Discovery Source<select id="universeDiscoverySource">${providers.map(provider => `<option value="${esc(provider.id)}">${esc(universeEditorDiscoveryOptionLabel(provider))}</option>`).join('')}</select></label><label>Market<select id="universeDiscoveryMarket"></select></label><label>Asset Type<input id="universeDiscoveryAssetType" readonly value="Instrument"></label><span id="universeDiscoveryNote" class="source-capability-note"></span></div><p class="form-help">Discovery Source narrows search only. It is not part of the Universe identity.</p><div class="instrument-picker-toolbar"><div class="instrument-search"><input id="universeInstrumentSearch" placeholder="Search Instruments"><button type="button" data-action="search-universe-instruments">Search</button></div><button type="button" data-action="toggle-universe-paste">Paste List</button><input id="universeCsv" type="file" accept=".csv,.txt" hidden><button type="button" data-action="import-universe-csv">Import CSV</button></div><div id="universePastePanel" class="instrument-paste-panel" hidden><label>Paste Symbols or Canonical IDs<textarea id="universePasteInput" rows="5" placeholder="BTCUSDT\nETHUSDT\nSOLUSDT"></textarea></label><div class="form-actions"><span id="universePasteStatus" class="muted"></span><button type="button" class="primary" data-action="resolve-universe-paste">Resolve and Add</button></div></div><div id="universeInstrumentResults" class="instrument-results"></div><div class="table-wrap instrument-table-wrap"><table class="data-table instrument-member-table"><thead><tr><th>Symbol</th><th>Name</th><th>Venue</th><th>Type</th><th>Status</th><th>Action</th></tr></thead><tbody id="universeInstrumentTable"></tbody></table></div></section>`;
    const inferredScope = universeEditorDiscoveryScope(editor.definition, editor.base?.current_resolution);
    $('universeDiscoverySource').value = providers.some(provider => provider.id === editor.discoveryProvider)
      ? editor.discoveryProvider
      : inferredScope.provider;
    syncUniverseEditorDiscoveryScope(editor.discoveryMarket || inferredScope.market);
    renderUniverseEditorInstrumentTable(); renderUniverseEditorSearchResults();
  } else if (build === 'combine_universes') {
    const draft = editor.methodDrafts.combine_universes;
    const inputs = draft.inputs.length >= 2 ? draft.inputs : [sources[0]?.universe_id || '', sources[1]?.universe_id || sources[0]?.universe_id || ''];
    $('universeTypeFields').innerHTML = `<section class="form-section"><div><span class="eyebrow">SET EXPRESSION</span><h3>Combine Existing Universes</h3></div><label>Operation<select id="universeOperator"><option value="union" ${draft.operator === 'union' ? 'selected' : ''}>Combine All Members · Union</option><option value="intersection" ${draft.operator === 'intersection' ? 'selected' : ''}>Keep Common Members · Intersection</option><option value="difference" ${draft.operator === 'difference' ? 'selected' : ''}>Remove Members · Difference</option></select></label><div id="universeExpressionInputs" class="universe-expression-list">${inputs.map((id, index) => universeEditorExpressionRow(id, index, draft.operator)).join('')}</div><button type="button" data-action="add-universe-expression-input">+ Add Universe</button><p class="form-help">Difference is resolved in order: Base Universe minus each Remove Universe.</p></section>`;
  } else if (build === 'manual_groups') {
    const draft = editor.methodDrafts.manual_groups;
    const rows = draft.groups.length ? draft.groups : [Array(draft.groupSize).fill('')];
    $('universeTypeFields').innerHTML = `<section class="form-section"><div class="section-heading"><div><span class="eyebrow">MANUAL GROUPS</span><h3>Enter final Instrument Groups</h3><p>Each row is one final member. Symbols are resolved during Preview.</p></div><div class="button-row"><button type="button" data-action="paste-manual-groups">Paste Groups</button><button type="button" data-action="add-manual-group">Add Group</button></div></div><label class="compact-field">Number of Legs<input id="universeManualGroupSize" type="number" min="2" max="8" value="${esc(draft.groupSize)}"></label><datalist id="universeInstrumentSuggestions">${editor.instrumentMembers.map(item => `<option value="${esc(item.instrument_id)}"></option>`).join('')}</datalist><div class="table-wrap"><table class="data-table manual-group-table"><thead><tr>${Array.from({length: draft.groupSize}, (_, index) => `<th>Leg ${index + 1}</th>`).join('')}<th>Action</th></tr></thead><tbody id="universeManualGroups">${rows.map((row, index) => universeEditorManualRow(row, draft.groupSize, index)).join('')}</tbody></table></div><div id="universeManualPastePanel" class="instrument-paste-panel" hidden><label>Paste one Group per line<textarea id="universeManualPasteInput" rows="5" placeholder="BTCUSDT, ETHUSDT\nBTCUSDT, SOLUSDT"></textarea></label><div class="form-actions"><button type="button" class="primary" data-action="resolve-manual-groups">Add Groups</button></div></div></section>`;
  } else if (build === 'cross_source_groups') {
    const draft = editor.methodDrafts.cross_source_groups;
    $('universeTypeFields').innerHTML = `<section class="form-section"><div class="section-heading"><div><span class="eyebrow">CROSS-SOURCE GROUPS</span><h3>Leg Sources</h3><p>Each generated Group selects one Instrument from every Leg.</p></div><button type="button" data-action="add-universe-leg">Add Leg</button></div><div id="universeLegs" class="universe-legs">${draft.legs.map((leg, index) => universeEditorLegRow(leg, index)).join('')}</div><div class="form-grid"><label class="check-label"><input id="universeAllowSame" type="checkbox" ${draft.allowSame ? 'checked' : ''}>Allow the same Instrument in multiple Legs</label><label>Maximum generated Groups<input id="universeMaxCombinations" type="number" min="1" max="100000" value="${esc(draft.maxGroups)}"></label></div></section>`;
  } else {
    const draft = editor.methodDrafts[build];
    const unique = build === 'unique_groups';
    $('universeTypeFields').innerHTML = `<section class="form-section"><div><span class="eyebrow">${unique ? 'UNIQUE GROUPS' : 'ORDERED GROUPS'}</span><h3>${unique ? 'Create unordered Groups' : 'Create ordered Groups'}</h3><p>${unique ? 'BTC–ETH and ETH–BTC are treated as the same Group.' : 'BTC–ETH and ETH–BTC are treated as different Groups.'}</p></div><div class="form-grid"><label>Source Universe<select id="universeGroupSource">${universeEditorSourceOptions(draft.sourceId)}</select></label><label>Group Size<input id="universeGroupSize" type="number" min="2" max="8" value="${esc(draft.groupSize)}"></label><label>Maximum generated Groups<input id="universeMaxCombinations" type="number" min="1" max="100000" value="${esc(draft.maxGroups)}"></label></div></section>`;
  }
  renderUniverseEditorPreview();
}

function universeEditorEstimate() {
  const editor = state.universeEditor;
  const build = editor.buildMethod;
  if (build === 'instrument_list') return {count: editor.instrumentMembers.length, label: 'Resolved Members', sample: editor.instrumentMembers.map(item => item.symbol)};
  if (build === 'combine_universes') {
    const draft = editor.methodDrafts.combine_universes;
    const total = draft.inputs.reduce((sum, id) => sum + Number(state.sharedUniverses.find(item => item.universe_id === id)?.current_resolution?.member_count || 0), 0);
    return {count: total, label: 'Input Members', detail: `${draft.inputs.length} input Universes`};
  }
  if (build === 'manual_groups') {
    const groups = editor.methodDrafts.manual_groups.groups.filter(row => row.every(Boolean));
    return {count: groups.length, label: 'Groups', groups};
  }
  const draft = editor.methodDrafts[build];
  if (build === 'cross_source_groups') {
    const counts = draft.legs.map(leg => Number(state.sharedUniverses.find(item => item.universe_id === leg.source_universe_id)?.current_resolution?.member_count || 0));
    return {count: counts.reduce((total, count) => total * count, 1), label: 'Estimated Groups', detail: counts.map((count, index) => `Leg ${index + 1}: ${count}`).join(' · '), maximum: draft.maxGroups};
  }
  const n = Number(state.sharedUniverses.find(item => item.universe_id === draft.sourceId)?.current_resolution?.member_count || 0);
  const k = draft.groupSize;
  const product = (start, count) => Array.from({length: count}, (_, index) => start - index).reduce((total, value) => total * value, 1);
  const count = n >= k ? (build === 'unique_groups' ? product(n, k) / product(k, k) : product(n, k)) : 0;
  return {count, label: 'Estimated Groups', detail: `${n} source Instruments · Group Size ${k}`, maximum: draft.maxGroups};
}

function renderUniverseEditorPreview(result = null, {verified = false, error = ''} = {}) {
  if (!$('universeLivePreview')) return;
  const editor = state.universeEditor;
  let estimate = universeEditorEstimate();
  if (result) estimate = {count: result.combination_count || result.member_count || 0, label: arr(result.instrument_tuples).length ? 'Generated Groups' : 'Resolved Members', sample: arr(result.instrument_ids).map(value => value.split(':').pop()), groups: arr(result.instrument_tuples)};
  const overLimit = estimate.maximum && estimate.count > estimate.maximum;
  const status = error ? 'Invalid' : overLimit ? 'Limit exceeded' : verified ? (result?.status || 'Valid') : 'Estimated';
  const samples = estimate.groups?.length ? `<div class="preview-groups">${estimate.groups.slice(0, 8).map(row => `<div>${row.map(value => `<span>${esc(String(value).split(':').pop())}</span>`).join('')}</div>`).join('')}</div>` : `<div class="preview-members">${arr(estimate.sample).slice(0, 12).map(value => `<span>${esc(value)}</span>`).join('') || '<small>Run Preview to resolve final members.</small>'}</div>`;
  const affected = Number(editor.base?.active_research_count || 0);
  $('universeLivePreview').innerHTML = `<div class="live-preview-head"><span class="eyebrow">LIVE PREVIEW</span><span class="preview-state ${error || overLimit ? 'invalid' : verified ? 'valid' : ''}">${esc(status)}</span></div><div class="preview-metrics"><div><span>${esc(estimate.label)}</span><strong>${esc(estimate.count || 0)}</strong></div><div><span>Build Method</span><strong>${esc(editor.buildMethod.split('_').map(value => value[0].toUpperCase() + value.slice(1)).join(' '))}</strong></div></div>${estimate.detail ? `<p class="preview-detail">${esc(estimate.detail)}</p>` : ''}${error ? `<div class="preview-error">${esc(error)}</div>` : ''}${overLimit ? `<div class="preview-error">Estimated output exceeds the ${esc(estimate.maximum)} Group limit.</div>` : ''}${samples}<div class="data-impact"><span class="eyebrow">DATA IMPACT</span><div><span>Affected Research</span><strong>${affected}</strong></div><div><span>Requirements to Recheck</span><strong>${affected}</strong></div><small>Existing Frozen Bundles and completed Runs will not change.</small></div>`;
}

function loadUniverseEditorDefinition(definition) {
  const editor = state.universeEditor;
  const discoveryScope = universeEditorDiscoveryScope(definition, editor.base?.current_resolution);
  editor.definition = JSON.parse(JSON.stringify(definition));
  editor.memberFormat = universeEditorMemberFormat(definition);
  editor.buildMethod = universeEditorBuildMethod(definition);
  editor.instrumentMembers = (
    definition.type === 'benchmark_set'
      ? arr(definition.constituents).map(item => universeEditorInstrumentRecord(item.instrument_id))
      : arr(definition.members).map(value => universeEditorInstrumentRecord(value))
  );
  editor.methodDrafts = universeEditorDefaultDrafts(definition);
  editor.discoveryProvider = discoveryScope.provider;
  editor.discoveryMarket = discoveryScope.market;
  if ($('universeName')) $('universeName').value = definition.name || '';
  if ($('universeDescription')) $('universeDescription').value = definition.description || '';
  if ($('universeTags')) $('universeTags').value = arr(definition.tags).join(', ');
  renderUniverseEditorControls(); renderUniverseEditorFields();
}

async function syncUniverseEditorScript() {
  const editor = state.universeEditor;
  if (!editor || editor.syncing || !$('universeScript') || editor.mode === 'script') return;
  editor.syncing = true;
  try {
    $('universeScript').value = await api('/api/library/universes/script/render', {method: 'POST', body: JSON.stringify({definition: universeEditorDefinitionFromForm()})});
  } catch (error) { renderUniverseEditorPreview(null, {error: error.message}); }
  finally { editor.syncing = false; }
}

async function searchUniverseEditorInstruments(query) {
  const providerId = $('universeDiscoverySource')?.value || state.universeEditor?.discoveryProvider || '';
  const marketId = $('universeDiscoveryMarket')?.value || state.universeEditor?.discoveryMarket || '';
  if (!providerId || !marketId) throw new Error('Choose a Discovery Source and Market first.');
  const provider = universeEditorDiscoveryProviders().find(item => item.id === providerId);
  const market = arr(provider?.markets).find(item => item.id === marketId);
  const params = new URLSearchParams({q: query, limit: '20', provider: providerId, market: marketId, category: market?.search_category || ''});
  const response = await fetch(`/api/research/instruments/search?${params}`);
  const body = await response.json();
  if (!response.ok || body.ok === false) throw new Error(body.error || 'Instrument search failed.');
  return {items: arr(body.data), message: body.meta?.message || ''};
}

async function registerUniverseEditorInstrument(item) {
  const provider = $('universeDiscoverySource')?.value || state.universeEditor?.discoveryProvider || '';
  const market = $('universeDiscoveryMarket')?.value || state.universeEditor?.discoveryMarket || '';
  if (!provider || !market) throw new Error('Choose a Discovery Source and Market first.');
  const registered = await api('/api/research/instruments/register', {
    method: 'POST',
    body: JSON.stringify({provider, market, instrument: typeof item === 'string' ? {instrument_id: item} : item}),
  });
  return {
    ...(typeof item === 'object' ? item : {}),
    ...registered,
    symbol: registered.native_symbol,
    name: registered.display_name || registered.display_symbol || registered.native_symbol,
    type: registered.market_type || registered.asset_class,
  };
}

async function addUniverseEditorInstruments(items) {
  const editor = state.universeEditor;
  const existing = new Set(editor.instrumentMembers.map(item => item.instrument_id.toLowerCase()));
  let added = 0;
  for (const item of items) {
    const registered = await registerUniverseEditorInstrument(item);
    const record = universeEditorInstrumentRecord(registered.instrument_id, registered);
    if (!record.instrument_id || existing.has(record.instrument_id.toLowerCase())) continue;
    editor.instrumentMembers.push(record); existing.add(record.instrument_id.toLowerCase()); added += 1;
  }
  renderUniverseEditorInstrumentTable(); renderUniverseEditorPreview();
  if (editor.mode === 'split') syncUniverseEditorScript();
  return added;
}

async function resolveUniverseEditorTokens(tokens) {
  const unique = [...new Set(tokens.map(value => String(value).trim()).filter(Boolean))].slice(0, 500);
  const resolved = []; const invalid = [];
  await Promise.all(unique.map(async token => {
    if (token.includes(':')) { resolved.push(universeEditorInstrumentRecord(token)); return; }
    try {
      const result = await searchUniverseEditorInstruments(token);
      const exact = result.items.find(item => String(item.symbol || '').toUpperCase() === token.toUpperCase()) || (result.items.length === 1 ? result.items[0] : null);
      if (exact) resolved.push(exact); else invalid.push(token);
    } catch (_) { invalid.push(token); }
  }));
  const added = await addUniverseEditorInstruments(resolved);
  return {resolved: resolved.length, added, invalid};
}

function universeDialog(base = null, context = {}) {
  const surface = context.surface || (state.surface === 'research-detail' ? 'research' : 'library');
  const definition = JSON.parse(JSON.stringify(base?.definition || {name: 'New Universe', description: '', tags: [], type: 'instrument_set', members: []}));
  const discoveryScope = universeEditorDiscoveryScope(definition, base?.current_resolution);
  state.universeEditor = {base, surface, definition, mode: 'ui', memberFormat: universeEditorMemberFormat(definition), buildMethod: universeEditorBuildMethod(definition), instrumentMembers: (definition.type === 'benchmark_set' ? arr(definition.constituents).map(item => universeEditorInstrumentRecord(item.instrument_id)) : arr(definition.members).map(value => universeEditorInstrumentRecord(value))), methodDrafts: {}, searchResults: [], searchMessage: '', discoveryProvider: discoveryScope.provider, discoveryMarket: discoveryScope.market, syncing: false};
  state.universeEditor.methodDrafts = universeEditorDefaultDrafts(definition);
  const meta = base ? `${state.universeEditor.memberFormat === 'instrument_group' ? 'Instrument Groups' : 'Individual Instruments'} · Used in ${base.active_research_count || 0} Research` : 'Define the member format, then choose how members are built.';
  openDialog(base ? `Edit ${base.name}` : 'Create Universe', surface === 'research' ? 'RESEARCH · SHARED UNIVERSE' : 'LIBRARY · SHARED UNIVERSE', `<form id="universeForm" class="universe-editor"><div class="universe-editor-toolbar"><div class="mode-switch"><button type="button" class="active" data-action="universe-editor-mode" data-mode="ui">UI</button><button type="button" data-action="universe-editor-mode" data-mode="script">Script</button><button type="button" data-action="universe-editor-mode" data-mode="split">Split</button></div><div class="button-row">${base ? '<button type="button" data-action="copy-universe-editor">Copy</button>' : ''}<button type="button" data-action="preview-universe-editor">Preview</button><button type="submit" class="primary">${base ? 'Save' : 'Create Universe'}</button></div></div><p id="universeEditorMeta" class="universe-editor-meta">${esc(meta)}</p><div class="universe-workspace"><div id="universeDefinitionPanes" class="universe-definition-panes"><div id="universeUi" class="form-stack"><section class="form-section universe-overview"><div><span class="eyebrow">OVERVIEW</span><h3>Universe details</h3></div><div class="form-grid"><label>Name<input id="universeName" value="${esc(definition.name || '')}" required></label><label>Tags<input id="universeTags" value="${esc(arr(definition.tags).join(', '))}" placeholder="crypto, liquid"></label><label class="wide">Description<textarea id="universeDescription" rows="2" maxlength="600">${esc(definition.description || '')}</textarea></label></div></section><div id="universeFormatControls"></div><div id="universeTypeFields"></div></div><div id="universeScriptPane" class="script-pane" hidden><label>Universe YAML<textarea id="universeScript" class="technical script-editor" rows="28" spellcheck="false"></textarea></label><p class="form-help">Script and UI represent the same Canonical Definition. Invalid Script does not overwrite the last valid UI state.</p></div></div><aside id="universeLivePreview" class="universe-live-preview"></aside></div></form>`);
  $('editorDialog').classList.add('universe-dialog');
  renderUniverseEditorControls(); renderUniverseEditorFields();
  const editor = state.universeEditor;
  let uiTimer = null; let scriptTimer = null;
  $('universeForm').addEventListener('submit', async event => { event.preventDefault(); await saveUniverseEditor(); });
  $('universeForm').addEventListener('change', event => {
    if (event.target.name === 'universeMemberFormat') {
      captureUniverseEditorState(); editor.memberFormat = event.target.value; editor.buildMethod = editor.memberFormat === 'individual_instruments' ? 'instrument_list' : 'manual_groups'; if ($('universeEditorMeta')) $('universeEditorMeta').textContent = `${editor.memberFormat === 'instrument_group' ? 'Instrument Groups' : 'Individual Instruments'} · Used in ${base?.active_research_count || 0} Research`; renderUniverseEditorControls(); renderUniverseEditorFields();
    } else if (event.target.name === 'universeBuildMethod') {
      captureUniverseEditorState(); editor.buildMethod = event.target.value; renderUniverseEditorControls(); renderUniverseEditorFields();
    } else if (event.target.id === 'universeDiscoverySource') {
      syncUniverseEditorDiscoveryScope();
      editor.searchResults = []; editor.searchMessage = ''; renderUniverseEditorSearchResults();
    } else if (event.target.id === 'universeDiscoveryMarket') {
      syncUniverseEditorDiscoveryScope(event.target.value);
      editor.searchResults = []; editor.searchMessage = ''; renderUniverseEditorSearchResults();
    } else if (event.target.id === 'universeManualGroupSize' || event.target.id === 'universeOperator') {
      captureUniverseEditorState(); renderUniverseEditorFields();
    } else { captureUniverseEditorState(); renderUniverseEditorPreview(); }
    if (editor.mode === 'split') { clearTimeout(uiTimer); uiTimer = setTimeout(syncUniverseEditorScript, 220); }
  });
  $('universeForm').addEventListener('input', event => {
    if (event.target.id === 'universeScript') {
      if (editor.mode !== 'split') return;
      clearTimeout(scriptTimer);
      scriptTimer = setTimeout(async () => {
        try { const parsed = await api('/api/library/universes/script/parse', {method: 'POST', body: JSON.stringify({script: $('universeScript').value})}); editor.syncing = true; loadUniverseEditorDefinition(parsed); editor.syncing = false; }
        catch (error) { renderUniverseEditorPreview(null, {error: error.message}); }
      }, 450);
      return;
    }
    captureUniverseEditorState(); renderUniverseEditorPreview();
    if (editor.mode === 'split') { clearTimeout(uiTimer); uiTimer = setTimeout(syncUniverseEditorScript, 260); }
  });
}

async function saveUniverseEditor() {
  const editor = state.universeEditor;
  let definition = editor.mode === 'script' ? await api('/api/library/universes/script/parse', {method: 'POST', body: JSON.stringify({script: $('universeScript').value})}) : universeEditorDefinitionFromForm();
  try {
    let result;
    if (editor.base) {
      const payload = {definition, expected_current_revision_id: editor.base.current_revision_id, current_project_id: editor.surface === 'research' ? state.projectId : '', confirm_shared: false};
      try { result = await api(`/api/library/universes/${encodeURIComponent(editor.base.universe_id)}`, {method: 'PATCH', body: JSON.stringify(payload)}); }
      catch (error) {
        if (error.code !== 'UNIVERSE_SHARED_EDIT_CONFIRMATION_REQUIRED') throw error;
        const names = arr(error.data.affected_research).map(item => item.title || item.project_id).join('\n');
        const confirmed = window.confirm(`This is a shared Universe. Saving will affect ${arr(error.data.affected_research).length} other active Research project(s):\n\n${names}\n\nExisting Runs remain frozen. Continue with the shared edit?`);
        if (!confirmed) {
          if (editor.surface === 'research' && window.confirm('Create an isolated Copy for this Research instead?')) {
            result = await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/universes/${encodeURIComponent(editor.base.universe_id)}/copy`, {method: 'POST', body: JSON.stringify({name: `${definition.name} Copy`, definition, replace_primary: editor.base.role === 'PRIMARY'})});
          } else return;
        } else result = await api(`/api/library/universes/${encodeURIComponent(editor.base.universe_id)}`, {method: 'PATCH', body: JSON.stringify({...payload, confirm_shared: true})});
      }
    } else {
      const url = editor.surface === 'research' ? `/api/research/projects/${encodeURIComponent(state.projectId)}/universes` : '/api/library/universes';
      result = await api(url, {method: 'POST', body: JSON.stringify({definition})});
    }
    closeDialog(); await loadBase(); if (state.surface === 'research-detail') { await loadResearch(state.projectId); switchResearchTab('universe'); } else switchLibraryTab('universe');
    notify(result.requirements_invalidated ? 'Universe revision saved. Dependent Requirements now need review.' : 'Universe saved.');
  } catch (error) { notify(error.message, true); }
}

function legacyUniverseDialog(base = null) {
  const snapshot = base ? latestSnapshotForUniverse(base.universe_definition_id) : null;
  const name = base?.name || 'Binance Research Universe';
  const version = nextSemanticVersion(state.universes, name, base?.version || '');
  openDialog(base ? `Create New Version of ${name}` : 'Create Universe in Research', 'RESEARCH · UNIVERSE', `<form id="universeForm" class="form-stack"><section class="form-section"><h3>Universe</h3><label>Name<input id="universeName" value="${esc(name)}" ${base ? 'readonly' : ''} required></label><label>Binance Symbols<textarea id="universeSymbols" required placeholder="BTCUSDT\nETHUSDT">${esc(arr(snapshot?.actual_instrument_ids).map(item => item.split(':').pop()).join('\n'))}</textarea></label><p class="form-help">Saving creates a validated Research component. Publish it only if it should be reused.</p></section><div class="form-actions"><button type="submit" class="primary">Save in Research</button></div></form>`);
  $('universeForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const symbols = $('universeSymbols').value.split(/[\s,]+/).filter(Boolean).map(item => item.toUpperCase());
      const definition = await api('/api/research/universes', {method: 'POST', body: JSON.stringify({name: $('universeName').value.trim(), version, universe_type: 'STATIC_LIST', parameters: {instrument_ids: symbols.map(symbol => `crypto_spot:BINANCE:${symbol}`)}, selection_rule_version: 'universe-engine.v1', owner_project_id: state.projectId, library_scope: 'PROJECT'})});
      const created = await api(`/api/research/universes/${encodeURIComponent(definition.universe_definition_id)}/snapshots`, {method: 'POST', body: JSON.stringify({as_of_time: new Date().toISOString(), manifest_ids: []})});
      await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/universe-ref`, {method: 'PUT', body: JSON.stringify({universe_snapshot_id: created.universe_snapshot_id})});
      closeDialog(); await loadResearch(state.projectId); switchResearchTab('universe'); notify('Universe saved in Research.');
    } catch (error) { notify(error.message, true); }
  });
}

function legacyAddLibraryUniverseDialog() {
  const items = latestByName(state.library.filter(item => item.component_type === 'UNIVERSE'));
  openDialog('Add from Library', 'LIBRARY · UNIVERSE', `<div class="choice-list">${items.map(asset => `<div class="choice-card"><div><strong>${esc(asset.name)}</strong><small>Library v${esc(asset.version)} · ${esc(arr(asset.content.snapshot?.actual_instrument_ids).map(item => item.split(':').pop()).join(' · '))}</small></div><button type="button" class="primary" data-action="use-universe" data-id="${esc(asset.content.snapshot?.universe_snapshot_id)}" data-library-id="${esc(asset.library_asset_id)}">Add to Research</button></div>`).join('') || '<p class="muted">Library has no published Universe.</p>'}</div>`);
}

function addLibraryUniverseDialog() {
  const bound = new Set(state.universeBindings.map(item => item.universe_id));
  const items = state.sharedUniverses.filter(item => !bound.has(item.universe_id));
  openDialog('Add from Library', 'RESEARCH · SHARED UNIVERSE', `<div class="choice-list">${items.map(item => `<div class="choice-card"><div><strong>${esc(item.name)}</strong><small>${esc(item.type)} · ${esc(item.current_resolution?.member_count || 0)} Instruments · used by ${esc(item.active_research_count || 0)} Research</small></div><button type="button" class="primary" data-action="bind-shared-universe" data-id="${esc(item.universe_id)}">${state.universeBindings.length ? 'Add as Reference' : 'Use as Primary'}</button></div>`).join('') || '<p class="muted">Every shared Universe is already used by this Research.</p>'}</div>`);
}

function copySharedUniverseDialog(item) {
  if (!item) return;
  const research = state.surface === 'research-detail';
  openDialog(`Copy ${item.name}`, 'ISOLATED UNIVERSE', `<form id="copyUniverseForm" class="form-stack"><div class="immutable-banner"><strong>Copy breaks shared identity</strong><span>The new Universe has its own stable ID and revision history. Future edits will not affect the source.</span></div><label>New name<input id="copyUniverseName" value="${esc(item.name)} Copy" required></label>${research ? `<label class="check-label"><input id="copyReplacePrimary" type="checkbox" ${primaryUniverseBinding()?.universe_id === item.universe_id ? 'checked' : ''}>Replace the current Primary Universe</label>` : ''}<div class="form-actions"><button type="submit" class="primary">Create Copy</button></div></form>`);
  $('copyUniverseForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const url = research ? `/api/research/projects/${encodeURIComponent(state.projectId)}/universes/${encodeURIComponent(item.universe_id)}/copy` : `/api/library/universes/${encodeURIComponent(item.universe_id)}/copy`;
      await api(url, {method: 'POST', body: JSON.stringify({name: $('copyUniverseName').value.trim(), replace_primary: research && $('copyReplacePrimary').checked})});
      closeDialog(); await loadBase(); if (research) { await loadResearch(state.projectId); switchResearchTab('universe'); } else switchLibraryTab('universe'); notify('Isolated Universe copy created.');
    } catch (error) { notify(error.message, true); }
  });
}

async function previewSharedUniverse(item, definition = null) {
  const result = definition ? await api('/api/library/universes/preview', {method: 'POST', body: JSON.stringify({definition, universe_id: item?.universe_id || ''})}) : item.current_resolution;
  const tuples = arr(result.instrument_tuples);
  const body = `<div class="metric-grid"><div class="metric-card"><span>Status</span><strong>${esc(result.status || 'VALID')}</strong></div><div class="metric-card"><span>Instruments</span><strong>${esc(result.member_count || 0)}</strong></div><div class="metric-card"><span>Combinations</span><strong>${esc(result.combination_count || 0)}</strong></div></div>${tuples.length ? `<div class="table-wrap"><table class="data-table"><thead><tr>${tuples[0].map((_, index) => `<th>Leg ${index + 1}</th>`).join('')}</tr></thead><tbody>${tuples.slice(0, 100).map(row => `<tr>${row.map(value => `<td>${esc(value.split(':').pop())}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : `<div class="member-tags">${arr(result.instrument_ids).map(value => `<span>${esc(value.split(':').pop())}</span>`).join('')}</div>`}${tuples.length > 100 ? '<p class="muted">Showing the first 100 combinations.</p>' : ''}`;
  if ($('editorDialog').open && $('universeLivePreview')) renderUniverseEditorPreview(result, {verified: true});
  else openDrawer(item?.name || definition?.name || 'Universe Preview', 'RESOLUTION PREVIEW', body);
}

async function showSharedUniverseUsage(item) {
  const usage = await api(`/api/library/universes/${encodeURIComponent(item.universe_id)}/usage`);
  openDrawer(item.name, 'UNIVERSE USAGE', `<div class="shared-impact-warning"><strong>Shared edits affect future work</strong><span>Active Research will resolve the newest revision. Historical Frozen Runs remain immutable.</span></div><h3>Active Research (${esc(usage.active_research_count || 0)})</h3><div class="choice-list">${arr(usage.active_research).map(row => `<a class="choice-card" href="/research/${encodeURIComponent(row.project_id)}"><strong>${esc(row.title || row.project_id)}</strong><small>${esc(row.role || '')}</small></a>`).join('') || '<p class="muted">No active Research.</p>'}</div><h3>Frozen Runs (${esc(usage.frozen_run_count || 0)})</h3><div class="choice-list">${arr(usage.frozen_runs).map(row => `<div class="choice-card"><strong>${esc(row.run_id)}</strong><small>${esc(formatDate(row.created_at))} · immutable historical revision</small></div>`).join('') || '<p class="muted">No Frozen Runs.</p>'}</div>`);
}

async function showSharedUniverseHistory(item) {
  const history = await api(`/api/library/universes/${encodeURIComponent(item.universe_id)}/history`);
  openDrawer(item.name, 'IMMUTABLE REVISION HISTORY', `<div class="choice-list">${arr(history).map(row => `<div class="choice-card"><div><strong>Revision ${esc(row.revision_number)}</strong><small>${esc(row.change_summary || 'Updated Universe')} · ${esc(formatDate(row.created_at))}</small></div>${row.revision_id === item.current_revision_id ? statusChip('Current') : `<button type="button" data-action="restore-shared-universe" data-universe-id="${esc(item.universe_id)}" data-revision-id="${esc(row.revision_id)}">Restore as New Revision</button>`}</div>`).join('')}</div>`);
}

function showSharedUniverseDetails(item) {
  openDrawer(item.name, 'SHARED UNIVERSE DETAILS', `<div class="fact-grid"><div class="fact-block"><span>Stable ID</span><strong>${esc(item.universe_id)}</strong></div><div class="fact-block"><span>Current Revision</span><strong>${esc(item.revision_number)} · ${esc(item.current_revision_id)}</strong></div><div class="fact-block"><span>Type</span><strong>${esc(item.type)}</strong></div></div><h3>Resolved Membership</h3><div class="member-tags">${arr(item.current_resolution?.instrument_ids).map(value => `<span>${esc(value.split(':').pop())}</span>`).join('')}</div><details><summary>Canonical definition</summary><pre class="technical">${esc(json(item.definition))}</pre></details>`);
}

async function useUniverse(snapshotId, projectId = state.projectId, libraryAssetId = '') {
  await api(`/api/research/projects/${encodeURIComponent(projectId)}/universe-ref`, {method: 'PUT', body: JSON.stringify({universe_snapshot_id: snapshotId, library_asset_id: libraryAssetId})});
  if (projectId === state.projectId && state.surface === 'research-detail') {
    closeDialog(); await loadResearch(projectId); switchResearchTab('universe');
  } else closeDialog();
  notify('Universe added to Research.');
}

function legacyFactorDialog(base = null) {
  const cap = state.capabilities.factor || {};
  const operators = arr(cap.operator_schema);
  const features = arr(cap.features);
  const spec = base?.spec || {};
  const savedFormula = spec.formula || spec;
  const name = base?.name || 'new_factor';
  const version = nextSemanticVersion(state.definitions.filter(item => item.definition_type === 'FACTOR'), name, base?.version || '');
  openDialog(base ? `Create New Version of ${name}` : 'Create Factor in Research', 'RESEARCH · FACTOR', `<form id="factorForm" class="form-stack"><section class="form-section"><h3>Input</h3><div class="form-grid"><label>Name<input id="factorName" value="${esc(name)}" ${base ? 'readonly' : ''} required></label><label>Frequency<select id="factorFrequency">${arr(cap.frequencies).map(item => `<option ${item === (spec.frequency || '1h') ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></label><label>Field<select id="factorInput">${features.map(item => `<option value="${esc(item.id)}" ${item.id === (savedFormula.input || 'close') ? 'selected' : ''}>${esc(item.id)}</option>`).join('')}</select></label></div></section><section class="form-section"><h3>Formula</h3><label>Operator<select id="factorOperator">${operators.map(item => `<option value="${esc(item.id)}" ${item.id === (savedFormula.operator || 'pct_change') ? 'selected' : ''}>${esc(operatorName(item.id))}</option>`).join('')}</select></label><div id="factorParameters" class="form-grid"></div><p id="factorHelp" class="form-help"></p></section><section class="form-section"><h3>Output</h3><label>Output Type<select id="factorOutput"><option value="HIGHER_IS_BETTER" ${spec.output_direction === 'HIGHER_IS_BETTER' ? 'selected' : ''}>Continuous Value</option><option value="LOWER_IS_BETTER" ${spec.output_direction === 'LOWER_IS_BETTER' ? 'selected' : ''}>Continuous Value · Lower Is Better</option><option value="EVENT_SIGNAL" ${spec.output_direction === 'EVENT_SIGNAL' ? 'selected' : ''}>Long / Short Signal</option></select></label></section><div class="form-actions"><button type="submit" class="primary">Save Draft</button></div></form>`);
  function renderParameters() {
    const operator = operators.find(item => item.id === $('factorOperator').value) || operators[0];
    $('factorHelp').textContent = operatorDescription(operator?.id);
    $('factorParameters').innerHTML = arr(operator?.parameters).map(parameter => {
      const saved = parameter.name === 'window' ? savedFormula.window : savedFormula.parameters?.[parameter.name];
      const label = parameter.name === 'fast_window' ? 'Fast Window' : (parameter.name === 'window' && operator?.id === 'ma_crossover' ? 'Slow Window' : 'Window');
      return `<label>${esc(label)}<input id="factorParam_${esc(parameter.name)}" type="number" min="${esc(parameter.minimum || 1)}" value="${esc(saved ?? parameter.default ?? '')}" required></label>`;
    }).join('');
  }
  $('factorOperator').addEventListener('change', renderParameters);
  renderParameters();
  $('factorForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const operator = operators.find(item => item.id === $('factorOperator').value);
      const parameters = {}; let windowValue = 1;
      arr(operator?.parameters).forEach(parameter => {
        const value = Number($(`factorParam_${parameter.name}`).value);
        if (parameter.name === 'window') windowValue = value; else parameters[parameter.name] = value;
      });
      const definitionSpec = {name: $('factorName').value.trim(), version, operator: $('factorOperator').value, input_field: $('factorInput').value, window: windowValue, parameters, frequency: $('factorFrequency').value, missing_policy: arr(cap.missing_policies)[0] || 'STRICT', dimension: 'TIME_SERIES', time_alignment_policy: cap.time_alignment_policy || 'BAR_END_AVAILABLE_TIME', available_after: arr(cap.available_after)[0] || 'BAR_CLOSE', allow_incomplete_bar: false, output_unit: operator?.output_unit === 'SOURCE' ? 'SOURCE' : (operator?.output_unit || 'RATIO'), output_direction: $('factorOutput').value};
      const created = await api('/api/research/definitions', {method: 'POST', body: JSON.stringify({definition_type: 'FACTOR', state: 'DRAFT', spec: definitionSpec, owner_project_id: state.projectId, library_scope: 'PROJECT'})});
      state.definitions.unshift(created);
      await setResearchDefinition(created, 'TRACK_DRAFT');
      closeDialog(); await loadResearch(state.projectId); switchResearchTab('factor'); notify('Factor saved as a Research draft.');
    } catch (error) { notify(error.message, true); }
  });
}

function factorDocumentFromDefinitionStep2A(base, version) {
  const spec = base?.spec || {};
  const formula = spec.formula || spec;
  return {
    schema_version: 'factor_draft.v1',
    identity: {
      name: base?.name || spec.name || 'new_factor',
      version,
      description: '',
    },
    input: {
      variable_name: formula.input || spec.input_field || 'price',
      dataset: 'bars',
      field: formula.input || spec.input_field || 'close',
      frequency: spec.frequency || '1h',
    },
    formula: {
      operator: formula.operator || spec.operator || 'pct_change',
      parameters: {
        ...(formula.parameters || spec.parameters || {}),
        window: formula.window || spec.window || 20,
      },
    },
    output: {
      unit: spec.output_unit || 'RATIO',
      direction: spec.output_direction || 'HIGHER_IS_BETTER',
    },
    advanced: {
      missing_policy: spec.missing_policy || 'STRICT',
      minimum_observations: spec.minimum_observations || '',
      dimension: spec.dimension || 'TIME_SERIES',
      time_alignment_policy: spec.time_alignment_policy || 'BAR_END_AVAILABLE_TIME',
      available_after: spec.available_after || 'BAR_CLOSE',
      allow_incomplete_bar: Boolean(spec.allow_incomplete_bar),
    },
  };
}

function factorDraftDialogStep2A(base = null) {
  const cap = state.capabilities.factor || {};
  const operators = arr(cap.operator_schema);
  const features = arr(cap.features);
  const isDraft = Boolean(base?.draft_id);
  const draftVersions = state.factorDrafts.map(item => ({
    name: item.document?.identity?.name || '',
    version: item.document?.identity?.version || '',
  }));
  const baseName = isDraft ? (base.document?.identity?.name || 'new_factor') : (base?.name || 'new_factor');
  const version = isDraft
    ? (base.document?.identity?.version || '1.0.0')
    : nextSemanticVersion(
      [...state.definitions.filter(item => item.definition_type === 'FACTOR'), ...draftVersions],
      baseName,
      base?.version || '',
    );
  const document = isDraft ? base.document : factorDocumentFromDefinitionStep2A(base, version);
  const originalOperator = document.formula?.operator || 'pct_change';
  const universe = currentUniverse();
  const snapshot = currentSnapshot();
  let currentDraft = isDraft ? base : null;
  let validationTimer = null;
  let validationToken = 0;

  openDialog(
    isDraft ? `Edit ${baseName}` : (base ? `Create New Version of ${baseName}` : 'Create Factor'),
    'RESEARCH · FACTOR',
    `<form id="factorForm" class="factor-editor">
      <div class="factor-editor-context">
        <div><span>Universe</span><strong>${esc(universe?.name || 'Not configured')}</strong><small>${snapshot ? `${arr(snapshot.actual_instrument_ids).length} Instruments · ${esc(snapshot.as_of_time || 'Current snapshot')}` : 'Factor can be drafted before a Universe is ready.'}</small></div>
        <div><span>Draft State</span><strong id="factorDraftState">${currentDraft ? 'Saved Draft' : 'Unsaved'}</strong><small id="factorDraftFingerprint">${currentDraft ? esc(currentDraft.draft_fingerprint.slice(0, 12)) : 'No fingerprint yet'}</small></div>
        <div><span>Engine</span><strong>${esc(cap.engine_version || 'factor-engine.v3')}</strong><small>Time Series · Bar Close · AS_OF</small></div>
      </div>
      <div class="factor-editor-layout">
        <div class="factor-editor-main form-stack">
          <section class="form-section factor-identity">
            <div class="factor-section-heading"><div><span>DEFINITION</span><h3>Factor identity</h3></div><small>Name the reusable measurement, not a trade action.</small></div>
            <div class="form-grid"><label>Name<input id="factorName" value="${esc(document.identity?.name || '')}" placeholder="momentum_20"></label><label>Version<input id="factorVersion" value="${esc(document.identity?.version || version)}" readonly></label></div>
            <label>Description<textarea id="factorDescription" rows="2" placeholder="Describe what this Factor measures.">${esc(document.identity?.description || '')}</textarea></label>
          </section>
          <section class="form-section">
            <div class="factor-section-heading"><div><span>INPUT</span><h3>Select the data used by the formula</h3></div><small>One controlled Bars input in this engine version.</small></div>
            <div class="factor-input-card"><div class="form-grid"><label>Variable Name<input id="factorVariable" value="${esc(document.input?.variable_name || 'price')}" placeholder="price"></label><label>Dataset<input value="Bars" readonly></label><label>Field<select id="factorInput">${features.map(item => `<option value="${esc(item.id)}" ${item.id === (document.input?.field || 'close') ? 'selected' : ''}>${esc(item.id)}</option>`).join('')}</select></label><label>Frequency<select id="factorFrequency">${arr(cap.frequencies).map(item => `<option ${item === (document.input?.frequency || '1h') ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></label></div><div class="input-contract"><span>Provider <strong>Binance</strong></span><span>Scope <strong>Current Universe</strong></span><span>Available after <strong>Bar Close</strong></span></div></div>
          </section>
          <section class="form-section">
            <div class="factor-section-heading"><div><span>FORMULA</span><h3>Build a controlled formula</h3></div><small>Generated from the capability contract; arbitrary Python is not accepted.</small></div>
            <label>Function<select id="factorOperator">${operators.map(item => `<option value="${esc(item.id)}" ${item.id === originalOperator ? 'selected' : ''}>${esc(operatorName(item.id))} · ${esc(item.signature || item.id)}</option>`).join('')}</select></label>
            <div id="factorParameters" class="form-grid"></div>
            <p id="factorHelp" class="form-help"></p>
            <div class="formula-source"><span>Controlled Formula</span><pre id="factorFormulaSource"></pre></div>
          </section>
          <section class="form-section">
            <div class="factor-section-heading"><div><span>OUTPUT</span><h3>Define the calculated Factor Value</h3></div><small>Trade direction and position sizing belong to Alpha and Strategy.</small></div>
            <div class="form-grid"><label>Type<input id="factorOutputType" readonly></label><label>Unit<input id="factorOutputUnit" readonly></label><label>Evaluation Frequency<input id="factorEvaluationFrequency" readonly></label><label>Interpretation<select id="factorOutput"><option value="HIGHER_IS_BETTER" ${document.output?.direction === 'HIGHER_IS_BETTER' ? 'selected' : ''}>Higher values indicate a stronger signal</option><option value="LOWER_IS_BETTER" ${document.output?.direction === 'LOWER_IS_BETTER' ? 'selected' : ''}>Lower values indicate a stronger signal</option><option value="EVENT_SIGNAL" ${document.output?.direction === 'EVENT_SIGNAL' ? 'selected' : ''}>Discrete event signal</option></select></label></div>
            <details class="factor-advanced"><summary>Advanced semantics</summary><div class="form-grid"><label>Missing Policy<select id="factorMissing">${arr(cap.missing_policies).map(item => `<option ${item === (document.advanced?.missing_policy || 'STRICT') ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></label><label>Minimum Observations<input id="factorMinimum" type="number" min="1" value="${esc(document.advanced?.minimum_observations || '')}" placeholder="Calculated automatically"></label></div><label class="check-label"><input id="factorIncomplete" type="checkbox" ${document.advanced?.allow_incomplete_bar ? 'checked' : ''}> Allow incomplete bars for this draft</label></details>
          </section>
        </div>
        <aside class="factor-live-preview">
          <div class="live-preview-head"><div><span class="eyebrow">FORMULA CHECKS</span><h3>Draft validation</h3></div><span id="factorValidationState" class="preview-state">Checking</span></div>
          <div id="factorValidationSummary" class="preview-metrics"><div><span>Errors</span><strong>—</strong></div><div><span>Warnings</span><strong>—</strong></div></div>
          <div id="factorDiagnostics" class="factor-diagnostics"><p class="preview-detail">Checking the current document…</p></div>
          <div class="factor-preview-placeholder"><span>VALUE PREVIEW</span><strong>Connected in Step 3</strong><p>This step validates the definition contract. The next step will calculate actual values against a pinned Universe Snapshot and time range.</p></div>
          <div class="data-impact"><div><span>Draft</span><strong id="factorPreviewDraft">${currentDraft ? 'Saved' : 'Unsaved'}</strong></div><div><span>Definition</span><strong>Created only after Validate</strong></div><small>Any edit changes the fingerprint and invalidates the previous check.</small></div>
        </aside>
      </div>
      <div class="factor-editor-actions"><div><span id="factorSaveState">${currentDraft ? `Last saved ${esc(formatDate(currentDraft.updated_at))}` : 'Not saved yet'}</span><small>Save Draft accepts incomplete work. Validate requires zero blocking errors.</small></div><div class="form-actions"><button type="submit" data-save-mode="draft">Save Draft</button><button id="factorValidateButton" type="submit" data-save-mode="validate" class="primary" disabled>Validate Factor</button></div></div>
    </form>`,
  );
  $('editorDialog').classList.add('factor-dialog');

  function selectedOperator() {
    return operators.find(item => item.id === $('factorOperator').value) || operators[0] || {};
  }

  function renderParameters(useSaved = false) {
    const operator = selectedOperator();
    $('factorHelp').textContent = operatorDescription(operator?.id);
    $('factorParameters').innerHTML = arr(operator?.parameters).map(parameter => {
      const saved = useSaved && operator.id === originalOperator
        ? document.formula?.parameters?.[parameter.name]
        : undefined;
      const label = parameter.name === 'fast_window' ? 'Fast Window' : (parameter.name === 'window' && operator?.id === 'ma_crossover' ? 'Slow Window' : 'Window');
      return `<label>${esc(label)}<input id="factorParam_${esc(parameter.name)}" data-factor-parameter="${esc(parameter.name)}" type="number" min="${esc(parameter.minimum || 1)}" value="${esc(saved ?? parameter.default ?? '')}"><small>${esc(parameter.constraint || '')}</small></label>`;
    }).join('');
  }

  function currentDocument() {
    const operator = selectedOperator();
    const parameters = {};
    arr(operator.parameters).forEach(parameter => {
      const raw = $(`factorParam_${parameter.name}`)?.value ?? '';
      parameters[parameter.name] = raw === '' ? '' : Number(raw);
    });
    return {
      schema_version: 'factor_draft.v1',
      identity: {
        name: $('factorName').value.trim(),
        version: $('factorVersion').value.trim(),
        description: $('factorDescription').value.trim(),
      },
      input: {
        variable_name: $('factorVariable').value.trim(),
        dataset: 'bars',
        field: $('factorInput').value,
        frequency: $('factorFrequency').value,
      },
      formula: {operator: operator.id, parameters},
      output: {
        type: operator.output_type || 'NUMBER',
        unit: operator.output_unit || 'RATIO',
        direction: $('factorOutput').value,
        evaluation_frequency: $('factorFrequency').value,
      },
      advanced: {
        missing_policy: $('factorMissing').value,
        minimum_observations: $('factorMinimum').value,
        dimension: 'TIME_SERIES',
        time_alignment_policy: cap.time_alignment_policy || 'BAR_END_AVAILABLE_TIME',
        available_after: arr(cap.available_after)[0] || 'BAR_CLOSE',
        allow_incomplete_bar: $('factorIncomplete').checked,
      },
    };
  }

  function renderComputed() {
    const operator = selectedOperator();
    const draftDocument = currentDocument();
    const variable = draftDocument.input.variable_name || draftDocument.input.field || 'input';
    const values = arr(operator.parameters).map(parameter => draftDocument.formula.parameters[parameter.name] === '' ? parameter.name : draftDocument.formula.parameters[parameter.name]);
    $('factorFormulaSource').textContent = `${operator.id}(${[variable, ...values].join(', ')})`;
    $('factorOutputType').value = operator.output_type === 'SIGNAL' ? 'Numeric · Discrete event' : 'Numeric · Automatically detected';
    $('factorOutputUnit').value = operator.output_unit || 'RATIO';
    $('factorEvaluationFrequency').value = `Every ${draftDocument.input.frequency}`;
  }

  function renderValidation(result) {
    const stateNode = $('factorValidationState');
    stateNode.textContent = result.can_validate ? 'Ready' : 'Needs attention';
    stateNode.className = `preview-state ${result.can_validate ? 'valid' : 'invalid'}`;
    $('factorValidationSummary').innerHTML = `<div><span>Errors</span><strong>${esc(result.summary?.errors ?? 0)}</strong></div><div><span>Warnings</span><strong>${esc(result.summary?.warnings ?? 0)}</strong></div>`;
    const diagnostics = arr(result.diagnostics);
    $('factorDiagnostics').innerHTML = diagnostics.length
      ? diagnostics.map(item => `<div class="factor-diagnostic ${String(item.level).toLowerCase()}"><span>${esc(item.level)}</span><div><strong>${esc(item.message)}</strong><small>${esc(item.path)} · ${esc(item.code)}</small></div></div>`).join('')
      : '<div class="factor-diagnostic success"><span>PASS</span><div><strong>Formula contract is valid</strong><small>Input, parameters, output, and point-in-time semantics are consistent.</small></div></div>';
    $('factorValidateButton').disabled = !result.can_validate;
    $('factorDraftFingerprint').textContent = (currentDraft?.draft_fingerprint || result.draft_fingerprint || '').slice(0, 12) || 'No fingerprint yet';
  }

  async function checkDocument() {
    const token = ++validationToken;
    $('factorValidationState').textContent = 'Checking';
    $('factorValidationState').className = 'preview-state';
    try {
      const result = await api('/api/research/factor-drafts/validation', {
        method: 'POST',
        body: JSON.stringify({document: currentDocument()}),
      });
      if (token === validationToken) renderValidation(result);
    } catch (error) {
      if (token !== validationToken) return;
      $('factorValidationState').textContent = 'Unavailable';
      $('factorValidationState').className = 'preview-state invalid';
      $('factorDiagnostics').innerHTML = `<div class="preview-error">${esc(error.message)}</div>`;
      $('factorValidateButton').disabled = true;
    }
  }

  function scheduleCheck() {
    renderComputed();
    $('factorValidateButton').disabled = true;
    clearTimeout(validationTimer);
    validationTimer = window.setTimeout(checkDocument, 180);
  }

  async function persistDraft(showNotice = true) {
    const body = {document: currentDocument(), owner_project_id: state.projectId, library_scope: 'PROJECT'};
    const saved = currentDraft
      ? await api(`/api/research/factor-drafts/${encodeURIComponent(currentDraft.draft_id)}`, {
        method: 'PUT',
        body: JSON.stringify({document: body.document, expected_fingerprint: currentDraft.draft_fingerprint}),
      })
      : await api('/api/research/factor-drafts', {method: 'POST', body: JSON.stringify(body)});
    currentDraft = saved;
    const index = state.factorDrafts.findIndex(item => item.draft_id === saved.draft_id);
    if (index >= 0) state.factorDrafts[index] = saved; else state.factorDrafts.unshift(saved);
    $('factorDraftState').textContent = 'Saved Draft';
    $('factorPreviewDraft').textContent = 'Saved';
    $('factorSaveState').textContent = 'Draft saved just now';
    renderValidation(saved.validation);
    if (showNotice) notify('Factor Draft saved. You can close this editor and return later.');
    return saved;
  }

  $('factorOperator').addEventListener('change', () => {
    renderParameters(false);
    if (selectedOperator().output_type === 'SIGNAL') $('factorOutput').value = 'EVENT_SIGNAL';
    else if ($('factorOutput').value === 'EVENT_SIGNAL') $('factorOutput').value = 'HIGHER_IS_BETTER';
    scheduleCheck();
  });
  $('factorForm').addEventListener('input', event => {
    if (event.target.id !== 'factorOperator') scheduleCheck();
  });
  $('factorForm').addEventListener('submit', async event => {
    event.preventDefault();
    const mode = event.submitter?.dataset.saveMode || 'draft';
    try {
      const saved = await persistDraft(mode === 'draft');
      if (mode !== 'validate') return;
      if (!saved.validation?.can_validate) {
        notify('Resolve the blocking Formula checks before validation.', true);
        return;
      }
      const result = await api(`/api/research/factor-drafts/${encodeURIComponent(currentDraft.draft_id)}/validate`, {
        method: 'POST',
        body: JSON.stringify({expected_fingerprint: saved.draft_fingerprint}),
      });
      await setResearchDefinition(result.definition, 'PINNED');
      closeDialog();
      await loadBase();
      if (state.surface === 'research-detail') switchResearchTab('factor');
      notify('Factor validated as an immutable Definition and added to this Research.');
    } catch (error) {
      notify(error.message, true);
    }
  });

  renderParameters(true);
  renderComputed();
  checkDocument();
}

function normalizeFactorDraftDocument(raw = {}, version = '1.0.0') {
  const source = raw || {};
  const legacyFormula = source.formula || {};
  const legacyInput = source.input || {};
  const inputs = arr(source.inputs).length
    ? arr(source.inputs).map(item => ({...item}))
    : (Object.keys(legacyInput).length ? [{
      variable_name: legacyInput.variable_name || 'price',
      dataset: legacyInput.dataset || 'bars',
      field: legacyInput.field || 'close',
      frequency: legacyInput.frequency || '1h',
    }] : []);
  let parameters = arr(source.parameters).map(item => ({...item}));
  if (!parameters.length && legacyFormula.parameters && typeof legacyFormula.parameters === 'object') {
    parameters = Object.entries(legacyFormula.parameters).map(([name, value]) => ({name, value, unit: 'bars'}));
  }
  if (!parameters.length && legacyFormula.window != null) {
    parameters = [{name: 'window', value: legacyFormula.window, unit: 'bars'}];
  }
  const operator = legacyFormula.operator || 'time.std';
  if (!parameters.length) parameters = [{name: 'window', value: 20, unit: 'bars'}];
  const inputName = inputs[0]?.variable_name || 'price';
  const formulaSource = legacyFormula.source
    || `${operator}(${[inputName, ...parameters.map(item => item.name)].filter(Boolean).join(', ')})`;
  return {
    schema_version: 'factor_draft.v2',
    identity: {
      name: source.identity?.name || 'new_factor',
      version: source.identity?.version || version,
      description: source.identity?.description || '',
    },
    inputs,
    parameters,
    formula: {source: formulaSource},
    output: {
      final: source.output?.final || source.output?.final_name || legacyFormula.output || '',
      display_name: source.output?.display_name || '',
      direction: source.output?.direction || source.output_direction || 'NO_PREDEFINED_DIRECTION',
    },
    advanced: {
      missing_policy: source.advanced?.missing_policy || source.missing_policy || 'STRICT',
      minimum_observations: source.advanced?.minimum_observations || source.minimum_observations || '',
      dimension: source.advanced?.dimension || source.dimension || 'TIME_SERIES',
      time_alignment_policy: source.advanced?.time_alignment_policy || source.time_alignment_policy || 'BAR_END_AVAILABLE_TIME',
      available_after: source.advanced?.available_after || source.available_after || 'BAR_CLOSE',
      allow_incomplete_bar: Boolean(source.advanced?.allow_incomplete_bar || source.allow_incomplete_bar),
    },
  };
}

function factorDocumentFromDefinition(base, version) {
  const spec = base?.spec || {};
  const formula = spec.formula || spec;
  const inputVariable = 'price';
  const savedInputs = arr(spec.inputs);
  const savedParameters = arr(spec.parameters);
  const engineParameters = savedParameters.length ? {} : {...(formula.parameters || spec.parameters || {})};
  if (formula.window != null || spec.window != null) engineParameters.window = formula.window ?? spec.window;
  const parameterEntries = Object.entries(engineParameters);
  return normalizeFactorDraftDocument({
    identity: {
      name: base?.name || spec.name || 'new_factor',
      version,
      description: '',
    },
    inputs: savedInputs.length ? savedInputs : [{
      variable_name: inputVariable,
      dataset: 'bars',
      field: formula.input || spec.input_field || 'close',
      frequency: spec.frequency || '1h',
    }],
    parameters: savedParameters.length
      ? savedParameters
      : parameterEntries.length
      ? parameterEntries.map(([name, value]) => ({name, value, unit: 'bars'}))
      : [{name: 'window', value: 20, unit: 'bars'}],
    formula: {
      source: formula.source || `${formula.operator || spec.operator || 'time.std'}(${[
        inputVariable,
        ...((parameterEntries.length ? parameterEntries : [['window', 20]])).map(([name]) => name),
      ].join(', ')})`,
    },
    output: {
      final: formula.output || '',
      display_name: '',
      direction: spec.output_direction || 'NO_PREDEFINED_DIRECTION',
    },
    advanced: {
      missing_policy: spec.missing_policy || 'STRICT',
      minimum_observations: spec.minimum_observations || '',
      dimension: spec.dimension || 'TIME_SERIES',
      time_alignment_policy: spec.time_alignment_policy || 'BAR_END_AVAILABLE_TIME',
      available_after: spec.available_after || 'BAR_CLOSE',
      allow_incomplete_bar: Boolean(spec.allow_incomplete_bar),
    },
  }, version);
}

function factorDraftDialogStep2B(base = null) {
  const cap = state.capabilities.factor || {};
  const authoring = cap.authoring_contract || {};
  const operators = arr(cap.operator_schema);
  const features = arr(cap.features);
  const maxInputs = Number(authoring.max_inputs || 1);
  const isDraft = Boolean(base?.draft_id);
  const draftVersions = state.factorDrafts.map(item => ({
    name: item.document?.identity?.name || '',
    version: item.document?.identity?.version || '',
  }));
  const baseName = isDraft ? (base.document?.identity?.name || 'new_factor') : (base?.name || 'new_factor');
  const version = isDraft
    ? (base.document?.identity?.version || '1.0.0')
    : nextSemanticVersion(
      [...state.definitions.filter(item => item.definition_type === 'FACTOR'), ...draftVersions],
      baseName,
      base?.version || '',
    );
  const draftDocument = normalizeFactorDraftDocument(
    isDraft ? base.document : factorDocumentFromDefinition(base, version),
    version,
  );
  const universe = currentUniverse();
  const snapshot = currentSnapshot();
  let currentDraft = isDraft ? base : null;
  let inputRows = draftDocument.inputs.map(item => ({...item}));
  let parameterRows = draftDocument.parameters.map(item => ({...item}));
  let validationTimer = null;
  let validationToken = 0;

  openDialog(
    isDraft ? `Edit ${baseName}` : (base ? `Create New Version of ${baseName}` : 'Create Factor'),
    'RESEARCH · FACTOR',
    `<form id="factorForm" class="factor-editor factor-editor-v2">
      <div class="factor-editor-context">
        <div><span>Universe</span><strong>${esc(universe?.name || 'Not configured')}</strong><small>${snapshot ? `${arr(snapshot.actual_instrument_ids).length} Instruments · ${esc(snapshot.as_of_time || 'Current snapshot')}` : 'A Preview will require a pinned Universe Snapshot.'}</small></div>
        <div><span>Draft State</span><strong id="factorDraftState">${currentDraft ? 'Saved Draft' : 'Unsaved'}</strong><small id="factorDraftFingerprint">${currentDraft ? esc(currentDraft.draft_fingerprint.slice(0, 12)) : 'No fingerprint yet'}</small></div>
        <div><span>Engine</span><strong>${esc(cap.engine_version || 'factor-engine.v3')}</strong><small>Variable list · Maximum ${esc(maxInputs)} Input · No nesting or composition</small></div>
      </div>
      <div class="factor-editor-layout">
        <div class="factor-editor-main form-stack">
          <section class="form-section factor-identity">
            <div class="factor-section-heading"><div><span>DEFINITION</span><h3>Factor identity</h3></div><small>Name the reusable measurement, not a trade action.</small></div>
            <div class="form-grid"><label>Name<input id="factorName" value="${esc(draftDocument.identity.name)}" placeholder="volatility_20"></label><label>Version<input id="factorVersion" value="${esc(draftDocument.identity.version)}" readonly></label></div>
            <label>Description<textarea id="factorDescription" rows="2" placeholder="Describe what this Factor measures.">${esc(draftDocument.identity.description)}</textarea></label>
          </section>

          <section class="form-section">
            <div class="factor-section-heading"><div><span>INPUT</span><h3>Variables used by the Formula</h3></div><small>Inputs are named variables. Engine v3 compiles at most one Input.</small></div>
            <div id="factorInputRows" class="factor-variable-list"></div>
            <div class="factor-list-actions"><button id="factorAddInput" type="button">+ Add Input</button><small id="factorInputLimit"></small></div>
          </section>

          <section class="form-section">
            <div class="factor-section-heading"><div><span>FORMULA</span><h3>Controlled Factor DSL</h3></div><small>The server compiles this source; arbitrary Python is never executed.</small></div>
            <div class="factor-subsection-heading"><div><strong>Parameters</strong><small>Formula values are explicit, named, and unit-bearing.</small></div><button id="factorAddParameter" type="button">+ Add Parameter</button></div>
            <div id="factorParameterRows" class="factor-parameter-list"></div>
            <label class="factor-code-label">Formula
              <textarea id="factorFormulaSource" class="factor-code-editor" rows="5" spellcheck="false" autocomplete="off">${esc(draftDocument.formula.source)}</textarea>
              <small>Example: rolling_std(price, window)</small>
            </label>
            <div class="factor-function-helper">
              <label>Insert function<select id="factorFunctionHelper">${operators.map(item => `<option value="${esc(item.id)}">${esc(operatorName(item.id))} · ${esc(item.signature || item.id)}</option>`).join('')}</select></label>
              <button id="factorInsertFunction" type="button">Insert Function</button>
              <small>The picker only inserts supported source text. The Formula editor remains authoritative.</small>
            </div>
          </section>

          <section class="form-section">
            <div class="factor-section-heading"><div><span>OUTPUT</span><h3>Calculated Factor Value</h3></div><small>Trade direction and position sizing belong to Alpha and Strategy.</small></div>
            <div class="form-grid"><label>Type<input id="factorOutputType" value="Waiting for compilation" readonly></label><label>Unit<input id="factorOutputUnit" value="Waiting for compilation" readonly></label><label>Evaluation Frequency<input id="factorEvaluationFrequency" value="Waiting for compilation" readonly></label><label>Interpretation<select id="factorOutput"><option value="HIGHER_IS_BETTER" ${draftDocument.output.direction === 'HIGHER_IS_BETTER' ? 'selected' : ''}>Higher values indicate a stronger signal</option><option value="LOWER_IS_BETTER" ${draftDocument.output.direction === 'LOWER_IS_BETTER' ? 'selected' : ''}>Lower values indicate a stronger signal</option><option value="EVENT_SIGNAL" ${draftDocument.output.direction === 'EVENT_SIGNAL' ? 'selected' : ''}>Discrete event signal</option></select></label></div>
            <details class="factor-advanced"><summary>Advanced semantics</summary><div class="form-grid"><label>Missing Policy<select id="factorMissing">${arr(cap.missing_policies).map(item => `<option ${item === draftDocument.advanced.missing_policy ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></label><label>Minimum Observations<input id="factorMinimum" type="number" min="1" value="${esc(draftDocument.advanced.minimum_observations)}" placeholder="Calculated automatically"></label></div><label class="check-label"><input id="factorIncomplete" type="checkbox" ${draftDocument.advanced.allow_incomplete_bar ? 'checked' : ''}> Allow incomplete bars for this draft</label></details>
          </section>
        </div>

        <aside class="factor-live-preview">
          <div class="live-preview-head"><div><span class="eyebrow">FORMULA CHECKS</span><h3>Server compiler</h3></div><span id="factorValidationState" class="preview-state">Checking</span></div>
          <div id="factorValidationSummary" class="preview-metrics"><div><span>Errors</span><strong>–</strong></div><div><span>Warnings</span><strong>–</strong></div></div>
          <div id="factorDiagnostics" class="factor-diagnostics"><p class="preview-detail">Compiling the current source against Engine v3…</p></div>
          <div class="factor-compiled-spec"><span>COMPILED FACTORSPEC</span><p>Exact execution contract produced by the server.</p><pre id="factorCompiledSpec">Not compiled</pre></div>
          <div class="factor-preview-placeholder"><span>RUN PREVIEW</span><strong>Required before Validate</strong><p>Step 3 will pin Universe Snapshot, time range, data Manifests, engine version, and Preview fingerprint.</p></div>
          <div class="data-impact"><div><span>Draft</span><strong id="factorPreviewDraft">${currentDraft ? 'Saved' : 'Unsaved'}</strong></div><div><span>Flow</span><strong>Save → Preview → Validate</strong></div><small>Definition Checks alone cannot create a validated Factor.</small></div>
        </aside>
      </div>
      <div class="factor-editor-actions"><div><span id="factorSaveState">${currentDraft ? `Last saved ${esc(formatDate(currentDraft.updated_at))}` : 'Not saved yet'}</span><small>Drafts may contain compiler errors. Preview and Validate are intentionally gated.</small></div><div class="form-actions"><button id="factorSaveDraft" type="submit">Save Draft</button><button id="factorRunPreview" type="button" disabled>Run Preview · Step 3</button><button id="factorValidateButton" type="button" class="primary" disabled>Validate Factor</button></div></div>
    </form>`,
  );
  $('editorDialog').classList.add('factor-dialog');

  function collectInputs() {
    return [...document.querySelectorAll('[data-factor-input-row]')].map(row => ({
      variable_name: row.querySelector('[data-input-variable]').value.trim(),
      dataset: 'bars',
      field: row.querySelector('[data-input-field]').value,
      frequency: row.querySelector('[data-input-frequency]').value,
    }));
  }

  function collectParameters() {
    return [...document.querySelectorAll('[data-factor-parameter-row]')].map(row => {
      const raw = row.querySelector('[data-parameter-value]').value;
      return {
        name: row.querySelector('[data-parameter-name]').value.trim(),
        value: raw === '' ? '' : Number(raw),
        unit: row.querySelector('[data-parameter-unit]').value,
      };
    });
  }

  function renderInputs() {
    $('factorInputRows').innerHTML = inputRows.map((item, index) => `<div class="factor-variable-card" data-factor-input-row data-index="${index}">
      <div class="factor-variable-summary"><div><span>VARIABLE</span><strong>${esc(item.variable_name || 'Unnamed Input')}</strong><small>Bars · ${esc(item.field || 'Field not set')} · ${esc(item.frequency || 'Frequency not set')}</small></div><button type="button" data-factor-remove-input="${index}">Remove</button></div>
      <div class="form-grid"><label>Variable Name<input data-input-variable value="${esc(item.variable_name || '')}" placeholder="price"></label><label>Dataset<input value="Bars" readonly></label><label>Field<select data-input-field>${features.map(feature => `<option value="${esc(feature.id)}" ${feature.id === (item.field || 'close') ? 'selected' : ''}>${esc(feature.id)}</option>`).join('')}</select></label><label>Frequency<select data-input-frequency>${arr(cap.frequencies).map(frequency => `<option value="${esc(frequency)}" ${frequency === (item.frequency || '1h') ? 'selected' : ''}>${esc(frequency)}</option>`).join('')}</select></label></div>
      <div class="input-contract"><span>Provider <strong>Binance</strong></span><span>Scope <strong>Current Universe</strong></span><span>Availability <strong>Checked in Step 3</strong></span></div>
    </div>`).join('') || '<div class="factor-list-empty">No Input yet. Add one named variable for the Formula.</div>';
    $('factorAddInput').disabled = inputRows.length >= maxInputs;
    $('factorInputLimit').textContent = `${inputRows.length} / ${maxInputs} Inputs · ${inputRows.length >= maxInputs ? 'Engine v3 capability limit reached' : 'Add an Input variable'}`;
  }

  function renderParameters() {
    $('factorParameterRows').innerHTML = parameterRows.map((item, index) => `<div class="factor-parameter-card" data-factor-parameter-row data-index="${index}">
      <div><span>PARAMETER</span><strong>${esc(item.name || 'Unnamed')} = ${esc(item.value === '' ? 'Not set' : item.value)} ${esc(item.unit || 'bars')}</strong></div>
      <label>Name<input data-parameter-name value="${esc(item.name || '')}" placeholder="window"></label>
      <label>Value<input data-parameter-value type="number" min="1" value="${esc(item.value ?? '')}"></label>
      <label>Unit<select data-parameter-unit><option value="bars" selected>bars</option></select></label>
      <button type="button" data-factor-remove-parameter="${index}">Remove</button>
    </div>`).join('') || '<div class="factor-list-empty">No Parameter yet. Engine v3 functions require named bar-window Parameters.</div>';
  }

  function currentDocument() {
    const inputs = collectInputs();
    const parameters = collectParameters();
    return {
      schema_version: 'factor_draft.v2',
      identity: {
        name: $('factorName').value.trim(),
        version: $('factorVersion').value.trim(),
        description: $('factorDescription').value.trim(),
      },
      inputs,
      parameters,
      formula: {source: $('factorFormulaSource').value.trim()},
      output: {direction: $('factorOutput').value},
      advanced: {
        missing_policy: $('factorMissing').value,
        minimum_observations: $('factorMinimum').value,
        dimension: 'TIME_SERIES',
        time_alignment_policy: cap.time_alignment_policy || 'BAR_END_AVAILABLE_TIME',
        available_after: arr(cap.available_after)[0] || 'BAR_CLOSE',
        allow_incomplete_bar: $('factorIncomplete').checked,
      },
    };
  }

  function renderValidation(result) {
    const compilable = Boolean(result.can_compile);
    $('factorValidationState').textContent = compilable ? 'Compiles' : 'Needs attention';
    $('factorValidationState').className = `preview-state ${compilable ? 'valid' : 'invalid'}`;
    $('factorValidationSummary').innerHTML = `<div><span>Errors</span><strong>${esc(result.summary?.errors ?? 0)}</strong></div><div><span>Warnings</span><strong>${esc(result.summary?.warnings ?? 0)}</strong></div>`;
    const diagnostics = arr(result.diagnostics);
    const pass = compilable ? '<div class="factor-diagnostic success"><span>PASS</span><div><strong>Formula compiles for Factor Engine v3</strong><small>Inputs, named Parameters, function arguments, and FactorSpec are consistent.</small></div></div>' : '';
    $('factorDiagnostics').innerHTML = `${pass}${diagnostics.map(item => `<div class="factor-diagnostic ${String(item.level).toLowerCase()}"><span>${esc(item.level)}</span><div><strong>${esc(item.message)}</strong><small>${esc(item.path)} · ${esc(item.code)}</small></div></div>`).join('')}`;
    $('factorCompiledSpec').textContent = result.compiled_factor_spec ? json(result.compiled_factor_spec) : 'Not compiled';
    const spec = result.compiled_factor_spec;
    const operator = operators.find(item => item.id === spec?.formula?.operator);
    $('factorOutputType').value = spec ? (operator?.output_type === 'SIGNAL' ? 'Numeric · Discrete event' : 'Numeric · Automatically detected') : 'Waiting for compilation';
    $('factorOutputUnit').value = spec?.output_unit || 'Waiting for compilation';
    $('factorEvaluationFrequency').value = spec?.frequency ? `Every ${spec.frequency}` : 'Waiting for compilation';
    $('factorDraftFingerprint').textContent = (currentDraft?.draft_fingerprint || result.draft_fingerprint || '').slice(0, 12) || 'No fingerprint yet';
    $('factorRunPreview').disabled = true;
    $('factorValidateButton').disabled = true;
  }

  async function checkDocument() {
    const token = ++validationToken;
    $('factorValidationState').textContent = 'Compiling';
    $('factorValidationState').className = 'preview-state';
    try {
      const result = await api('/api/research/factor-drafts/validation', {
        method: 'POST',
        body: JSON.stringify({document: currentDocument()}),
      });
      if (token === validationToken) renderValidation(result);
    } catch (error) {
      if (token !== validationToken) return;
      $('factorValidationState').textContent = 'Unavailable';
      $('factorValidationState').className = 'preview-state invalid';
      $('factorDiagnostics').innerHTML = `<div class="preview-error">${esc(error.message)}</div>`;
      $('factorCompiledSpec').textContent = 'Not compiled';
    }
  }

  function scheduleCheck() {
    clearTimeout(validationTimer);
    validationTimer = window.setTimeout(checkDocument, 180);
  }

  async function persistDraft() {
    const body = {document: currentDocument(), owner_project_id: state.projectId, library_scope: 'PROJECT'};
    const saved = currentDraft
      ? await api(`/api/research/factor-drafts/${encodeURIComponent(currentDraft.draft_id)}`, {
        method: 'PUT',
        body: JSON.stringify({document: body.document, expected_fingerprint: currentDraft.draft_fingerprint}),
      })
      : await api('/api/research/factor-drafts', {method: 'POST', body: JSON.stringify(body)});
    currentDraft = saved;
    const index = state.factorDrafts.findIndex(item => item.draft_id === saved.draft_id);
    if (index >= 0) state.factorDrafts[index] = saved; else state.factorDrafts.unshift(saved);
    $('factorDraftState').textContent = 'Saved Draft';
    $('factorPreviewDraft').textContent = 'Saved';
    $('factorSaveState').textContent = 'Draft saved just now';
    renderValidation(saved.validation);
    notify('Factor Draft saved. Run Preview will become available in Step 3.');
  }

  $('factorForm').addEventListener('click', event => {
    const removeInput = event.target.closest('[data-factor-remove-input]');
    if (removeInput) {
      inputRows = collectInputs();
      inputRows.splice(Number(removeInput.dataset.factorRemoveInput), 1);
      renderInputs();
      scheduleCheck();
      return;
    }
    const removeParameter = event.target.closest('[data-factor-remove-parameter]');
    if (removeParameter) {
      parameterRows = collectParameters();
      parameterRows.splice(Number(removeParameter.dataset.factorRemoveParameter), 1);
      renderParameters();
      scheduleCheck();
    }
  });
  $('factorAddInput').addEventListener('click', () => {
    inputRows = collectInputs();
    if (inputRows.length >= maxInputs) return;
    inputRows.push({variable_name: `input_${inputRows.length + 1}`, dataset: 'bars', field: 'close', frequency: '1h'});
    renderInputs();
    scheduleCheck();
  });
  $('factorAddParameter').addEventListener('click', () => {
    parameterRows = collectParameters();
    let suffix = parameterRows.length + 1;
    let name = `parameter_${suffix}`;
    while (parameterRows.some(item => item.name === name)) name = `parameter_${++suffix}`;
    parameterRows.push({name, value: 20, unit: 'bars'});
    renderParameters();
    scheduleCheck();
  });
  $('factorInsertFunction').addEventListener('click', () => {
    inputRows = collectInputs();
    parameterRows = collectParameters();
    const operator = operators.find(item => item.id === $('factorFunctionHelper').value);
    if (!operator) return;
    const parameterNames = arr(operator.parameters).map((schema, index) => {
      let parameter = parameterRows.find(item => item.name === schema.name);
      if (!parameter) {
        parameter = {name: schema.name, value: schema.default ?? 20, unit: 'bars'};
        parameterRows.push(parameter);
      }
      return parameter.name;
    });
    renderParameters();
    const inputName = inputRows[0]?.variable_name || 'input';
    $('factorFormulaSource').value = `${operator.id}(${[inputName, ...parameterNames].join(', ')})`;
    if (operator.output_type === 'SIGNAL') $('factorOutput').value = 'EVENT_SIGNAL';
    scheduleCheck();
  });
  $('factorForm').addEventListener('input', event => {
    if (event.target.id !== 'factorFunctionHelper') scheduleCheck();
  });
  $('factorForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await persistDraft();
    } catch (error) {
      notify(error.message, true);
    }
  });

  renderInputs();
  renderParameters();
  checkDocument();
}

function factorDraftDialog(base = null) {
  const cap = state.capabilities.factor || {};
  const authoring = cap.authoring_contract || {};
  const operators = arr(cap.operator_schema);
  const inputContract = state.factorInputCandidates || {};
  const allInputCandidates = arr(inputContract.input_candidates);
  const inputCandidates = allInputCandidates.filter(item => item.factor_selectable);
  const inputDatasets = arr(inputContract.datasets);
  const inputContractDiagnostics = arr(inputContract.diagnostics);
  const instrumentSummary = inputContract.instrument_summary || {};
  const maxInputs = Number(authoring.max_inputs || 1);
  const isDraft = Boolean(base?.draft_id);
  const draftVersions = state.factorDrafts.map(item => ({
    name: item.document?.identity?.name || '',
    version: item.document?.identity?.version || '',
  }));
  const baseName = isDraft ? (base.document?.identity?.name || 'new_factor') : (base?.name || 'new_factor');
  const version = isDraft
    ? (base.document?.identity?.version || '1.0.0')
    : nextSemanticVersion(
      [...state.definitions.filter(item => item.definition_type === 'FACTOR'), ...draftVersions],
      baseName,
      base?.version || '',
    );
  const draftDocument = normalizeFactorDraftDocument(
    isDraft ? base.document : factorDocumentFromDefinition(base, version),
    version,
  );
  const universe = currentUniverse();
  const snapshot = currentSnapshot();
  const universeMembers = arr(universe?.current_resolution?.instrument_ids).length
    || arr(snapshot?.actual_instrument_ids).length;
  let currentDraft = isDraft ? base : null;
  let inputRows = (
    !isDraft && !base && !inputCandidates.length
      ? []
      : draftDocument.inputs.map(item => ({...item}))
  );
  let parameterRows = draftDocument.parameters.map(item => ({...item}));
  let validationTimer = null;
  let validationToken = 0;
  let backupTimer = null;
  let saveQueue = Promise.resolve();
  let latestValidation = null;
  let previewContext = null;
  let latestPreview = null;
  let dialogDestroyed = false;
  let suggestionIndex = 0;
  let formulaSuggestions = [];

  openDialog(
    isDraft ? `Edit ${baseName}` : (base ? `Create New Version of ${baseName}` : 'Create Factor'),
    'RESEARCH · FACTOR',
    `<form id="factorForm" class="factor-editor factor-editor-v3" novalidate>
      <div class="factor-editor-context factor-editor-context-user">
        <div><span>Universe</span><strong>${esc(universe?.name || 'Not configured')}</strong><small>${esc(universeMembers)} Instruments</small></div>
        <div><span>Status</span><strong id="factorUserStatus">${currentDraft ? 'Changes backed up' : 'Unsaved changes'}</strong><small id="factorStatusDetail">${currentDraft ? `Backed up ${esc(formatDate(currentDraft.updated_at))}` : 'Your work will be backed up automatically.'}</small></div>
      </div>
      <div class="factor-editor-layout">
        <div class="factor-editor-main form-stack">
          <section class="form-section factor-identity">
            <div class="factor-section-heading"><div><span>FACTOR</span><h3>Identity</h3></div><small>Describe the measurement, not a trading action.</small></div>
            <div class="form-grid factor-identity-grid">
              <label>Name<input id="factorName" value="${esc(draftDocument.identity.name)}" placeholder="price_variation_20h"></label>
              <label class="wide">Description<textarea id="factorDescription" rows="2" placeholder="What this Factor measures and why it is useful.">${esc(draftDocument.identity.description)}</textarea></label>
            </div>
            <input id="factorVersion" type="hidden" value="${esc(draftDocument.identity.version)}">
            <p class="form-help">Saving creates a new revision. Existing research runs remain unchanged.</p>
          </section>

          <section class="form-section">
            <div class="factor-section-heading"><div><span>INPUT</span><h3>Variables</h3></div><button id="factorAddInput" type="button">Add Input</button></div>
            <div class="factor-input-source">
              <div><span>Current Universe</span><strong>${esc(instrumentSummary.asset_type || 'Not resolved')} · ${esc(instrumentSummary.venue || '-')} · ${esc(instrumentSummary.quote_currency || '-')}</strong></div>
              <div><span>Provider IDs</span><strong>${esc(instrumentSummary.provider_id_matches || 0)} / ${esc(instrumentSummary.member_count || universeMembers)} matched</strong></div>
              <small>Options below are requestable for this Universe. “Prepared” only means matching local data exists; it does not mean the whole Preview range has been downloaded.</small>
            </div>
            <div id="factorInputCapabilityStatus" class="factor-input-capability-status"></div>
            <div id="factorInputRows" class="factor-variable-list"></div>
            <p id="factorInputLimit" class="form-help"></p>
          </section>

          <section class="form-section factor-formula-section">
            <div class="factor-section-heading"><div><span>FORMULA</span><h3>Formula</h3></div><button id="factorBrowseFunctions" type="button">Browse Functions</button></div>
            <div class="factor-editor-guide">
              <span>Nested functions, Conditional logic, and explicit mixed-frequency alignment are supported.</span>
              <span>Type an Input, Parameter, or function name for suggestions.</span>
              <span><code>Tab</code> moves between inserted arguments.</span>
            </div>
            <label>Formula<div class="factor-code-shell"><textarea id="factorFormulaSource" class="factor-code-editor" rows="9" spellcheck="false" aria-autocomplete="list">${esc(draftDocument.formula.source)}</textarea><div id="factorFormulaSuggestions" class="factor-formula-suggestions" role="listbox" hidden></div></div></label>
            <div id="factorFunctionBrowser" class="factor-function-browser" hidden>
              <div class="factor-catalog-head"><div><span>SUPPORTED FUNCTIONS</span><strong>Browse Functions</strong></div><button id="factorCloseFunctions" type="button" aria-label="Close Function browser">×</button></div>
              <label>Search functions<input id="factorFunctionSearch" type="search" placeholder="Search std, rank, alignment…"></label>
              <div id="factorFunctionBrowserResults" class="factor-function-browser-results"></div>
            </div>
            <div class="factor-section-heading compact"><div><span>PARAMETERS</span><h3>Named values</h3></div><button id="factorAddParameter" type="button">Add Parameter</button></div>
            <div id="factorParameterRows" class="factor-parameter-list"></div>
            <div class="factor-formula-explanation">
              <div><span>Resolved Formula</span><strong id="factorResolvedFormula">Waiting for a valid Formula</strong></div>
              <div><span>Required History</span><strong id="factorRequiredHistory">—</strong></div>
              <div><span>Formula Meaning</span><p id="factorFormulaMeaning">Complete the Formula to see a plain-language explanation.</p></div>
            </div>
          </section>

          <section class="form-section">
            <div class="factor-section-heading"><div><span>OUTPUT</span><h3>Factor Value</h3></div><small>The meaning is inferred from the Formula.</small></div>
            <div class="form-grid factor-output-grid">
              <label>Final Output<select id="factorFinalOutput"><option value="">Formula expression · automatic</option></select></label>
              <label>Display Name<input id="factorOutputDisplayName" value="${esc(draftDocument.output.display_name || '')}" placeholder="${esc(draftDocument.identity.name || 'Factor value')}"></label>
              <label>Type<input id="factorOutputType" value="Waiting for Formula" readonly></label>
              <label>Unit<input id="factorOutputUnit" value="Waiting for Formula" readonly></label>
              <label>Evaluation<input id="factorEvaluationFrequency" value="Waiting for Formula" readonly></label>
              <label>Dimension<input id="factorOutputDimension" value="Waiting for Formula" readonly></label>
              <label class="wide">Nullability<input id="factorOutputNullability" value="Waiting for Formula" readonly></label>
              <label class="wide">Value Meaning<textarea id="factorValueMeaning" rows="2" readonly>Complete the Formula to infer the value meaning.</textarea></label>
            </div>
          </section>
        </div>

        <aside class="factor-live-preview factor-checks-panel">
          <div class="live-preview-head"><div><span class="eyebrow">DEFINITION CHECKS</span><h3>Ready for Preview?</h3></div><span id="factorValidationState" class="preview-state">Checking</span></div>
          <div id="factorValidationSummary" class="preview-metrics"><div><span>Errors</span><strong>–</strong></div><div><span>Warnings</span><strong>–</strong></div></div>
          <div id="factorDiagnostics" class="factor-diagnostics"><p class="preview-detail">Checking the current Input, Formula, Parameters, and Output…</p></div>
          <section class="factor-values-preview">
            <div class="live-preview-head"><div><span class="eyebrow">VALUE PREVIEW</span><h3>Real Factor values</h3></div><span id="factorPreviewState" class="preview-state">Not run</span></div>
            <div id="factorPreviewContext" class="factor-preview-context"><p>Save the Draft to pin the current Universe Snapshot and available data range.</p></div>
            <div class="factor-preview-range">
              <label>Start · UTC<input id="factorPreviewStart" type="datetime-local" step="60"></label>
              <label>End · UTC<input id="factorPreviewEnd" type="datetime-local" step="60"></label>
            </div>
            <div id="factorPreviewRequirement" class="factor-preview-requirement"><p>The exact data Requirement is generated from the selected Inputs, Preview range, and Formula history when Preview starts.</p></div>
            <div id="factorPreviewResults" class="factor-preview-results"><p>No Preview has been run for this Draft revision.</p></div>
          </section>
          <details class="factor-advanced-details">
            <summary>Advanced Details</summary>
            <section>
              <span>EXECUTION CONTRACT</span>
              <div id="factorExecutionContract" class="factor-advanced-facts">Available after the Formula is checked.</div>
            </section>
            <section>
              <span>COMPILED SPECIFICATION</span>
              <pre id="factorCompiledSpecAdvanced">Not compiled</pre>
            </section>
            <section>
              <span>AUDIT INFORMATION</span>
              <div id="factorAuditInfo" class="factor-advanced-facts">No saved revision yet.</div>
            </section>
          </details>
        </aside>
      </div>
      <div class="factor-editor-actions">
        <div><span>Save Draft → Run Preview → Validate Factor</span><small id="factorSaveHint">Changes are backed up automatically. Validation requires a current Preview fingerprint.</small></div>
        <div class="form-actions"><button id="factorSaveDraft" type="submit">Save Draft</button><button id="factorRunPreview" type="button" disabled>Run Preview</button><button id="factorValidateFactor" type="button" class="primary" disabled>Validate Factor</button></div>
      </div>
    </form>`,
  );
  $('editorDialog').classList.add('factor-dialog');
  const dialog = $('editorDialog');
  dialog.addEventListener('close', () => {
    dialogDestroyed = true;
    clearTimeout(validationTimer);
    clearTimeout(backupTimer);
  }, {once: true});

  function formulaResultNames(source = $('factorFormulaSource')?.value || '') {
    const names = [];
    const pattern = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=/gm;
    let match;
    while ((match = pattern.exec(source))) {
      if (!names.includes(match[1])) names.push(match[1]);
    }
    return names;
  }

  function renderOutputChoices(compiledResults = null) {
    const select = $('factorFinalOutput');
    const previous = select.value || draftDocument.output.final || '';
    const names = compiledResults
      ? arr(compiledResults).map(item => item.name)
      : formulaResultNames();
    select.innerHTML = `<option value="">${names.length ? 'Final expression · automatic' : 'Formula expression · automatic'}</option>${names.map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join('')}`;
    if (names.includes(previous)) select.value = previous;
    else if (!previous && names.includes('factor')) select.value = 'factor';
    else if (!previous && names.length === 1) select.value = names[0];
  }

  function suggestedVariableName(field, frequency) {
    const preferred = {
      open: 'open_price',
      high: 'high_price',
      low: 'low_price',
      close: 'price',
      volume: 'volume',
      quote_volume: 'quote_volume',
      trade_count: 'trade_count',
    }[field] || String(field || 'value').replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
    const used = new Set(collectInputs().map(item => item.variable_name));
    if (!used.has(preferred)) return preferred;
    const frequencyName = `${preferred}_${String(frequency || '1h').replace(/[^A-Za-z0-9_]/g, '_')}`;
    if (!used.has(frequencyName)) return frequencyName;
    let suffix = 2;
    while (used.has(`${frequencyName}_${suffix}`)) suffix += 1;
    return `${frequencyName}_${suffix}`;
  }

  function replaceFormulaRange(start, end, value, selectToken = '') {
    const editor = $('factorFormulaSource');
    editor.value = `${editor.value.slice(0, start)}${value}${editor.value.slice(end)}`;
    let selectionStart = start + value.length;
    let selectionEnd = selectionStart;
    if (selectToken) {
      const tokenOffset = value.indexOf(selectToken);
      if (tokenOffset >= 0) {
        selectionStart = start + tokenOffset;
        selectionEnd = selectionStart + selectToken.length;
      }
    }
    editor.focus();
    editor.setSelectionRange(selectionStart, selectionEnd);
  }

  function insertFunction(operator, range = null) {
    if (!operator) return;
    const editor = $('factorFormulaSource');
    const signature = operator.signature || `${operator.id}(series)`;
    const start = range?.start ?? editor.selectionStart;
    const end = range?.end ?? editor.selectionEnd;
    const firstArgument = signature.slice(signature.indexOf('(') + 1, signature.indexOf(')')).split(',')[0].trim();
    replaceFormulaRange(start, end, signature, firstArgument);
    hideFormulaSuggestions();
    markChanged();
  }

  function renderFunctionBrowser() {
    const query = $('factorFunctionSearch').value.trim().toLowerCase();
    const filtered = operators.filter(operator => (
      !query
      || String(operator.id).toLowerCase().includes(query)
      || String(operator.label || '').toLowerCase().includes(query)
      || String(operator.description || '').toLowerCase().includes(query)
    ));
    const categories = [...new Set(filtered.map(item => item.category || 'Other'))];
    $('factorFunctionBrowserResults').innerHTML = categories.map(category => `
      <section class="factor-function-category"><div><strong>${esc(category)}</strong><small>${filtered.filter(item => (item.category || 'Other') === category).length} functions</small></div>
      ${filtered.filter(item => (item.category || 'Other') === category).map(operator => `<button type="button" data-factor-browser-function="${esc(operator.id)}"><span><strong>${esc(operator.label || operator.id)}</strong><code>${esc(operator.signature || operator.id)}</code><small>${esc(operator.description || '')}</small></span><span>Insert</span></button>`).join('')}</section>`).join('') || '<p>No supported functions match this search.</p>';
  }

  function suggestionToken() {
    const editor = $('factorFormulaSource');
    const before = editor.value.slice(0, editor.selectionStart);
    const match = before.match(/([A-Za-z_][A-Za-z0-9_.]*)$/);
    return match ? {
      value: match[1],
      start: editor.selectionStart - match[1].length,
      end: editor.selectionStart,
    } : null;
  }

  function hideFormulaSuggestions() {
    formulaSuggestions = [];
    suggestionIndex = 0;
    $('factorFormulaSuggestions').hidden = true;
    $('factorFormulaSuggestions').innerHTML = '';
  }

  function renderFormulaSuggestions() {
    const token = suggestionToken();
    if (!token) {
      hideFormulaSuggestions();
      return;
    }
    const currentInputs = collectInputs();
    const currentParameters = collectParameters();
    const resultNames = formulaResultNames();
    const query = token.value.toLowerCase();
    const symbols = [
      ...currentInputs.map(item => ({kind: 'Input', label: item.variable_name, detail: `${item.dataset}.${item.field} · ${item.frequency}`, insert: item.variable_name})),
      ...currentParameters.map(item => ({kind: 'Parameter', label: item.name, detail: `${item.value} ${item.unit}`, insert: item.name})),
      ...resultNames.map(name => ({kind: 'Calculated result', label: name, detail: 'Named Formula result', insert: name})),
      ...operators.map(operator => ({kind: operator.category || 'Function', label: operator.id, detail: `${operator.signature} · ${operator.description || ''}`, operator})),
    ];
    formulaSuggestions = symbols.filter(item => (
      query.length >= 2
      && (
        String(item.label).toLowerCase().includes(query)
        || String(item.detail).toLowerCase().includes(query)
      )
    )).slice(0, 10).map(item => ({...item, range: token}));
    suggestionIndex = 0;
    $('factorFormulaSuggestions').hidden = !formulaSuggestions.length;
    $('factorFormulaSuggestions').innerHTML = formulaSuggestions.map((item, index) => `<button type="button" role="option" aria-selected="${index === suggestionIndex}" data-factor-suggestion="${index}" class="${index === suggestionIndex ? 'active' : ''}"><span>${esc(item.kind)}</span><strong>${esc(item.label)}</strong><small>${esc(item.detail)}</small></button>`).join('');
  }

  function chooseFormulaSuggestion(index) {
    const item = formulaSuggestions[index];
    if (!item) return;
    if (item.operator) insertFunction(item.operator, item.range);
    else {
      replaceFormulaRange(item.range.start, item.range.end, item.insert || item.label);
      hideFormulaSuggestions();
      markChanged();
    }
  }

  function collectInputs() {
    return [...document.querySelectorAll('[data-factor-input-row]')].map(row => ({
      variable_name: row.querySelector('[data-input-variable]').value.trim(),
      dataset: row.querySelector('[data-input-dataset]').value,
      field: row.querySelector('[data-input-field]').value,
      frequency: row.querySelector('[data-input-frequency]').value,
    }));
  }

  function distinctBy(items, key) {
    return [...new Map(items.map(item => [item[key], item])).values()];
  }

  function candidatesFor(dataset = '', field = '') {
    return inputCandidates.filter(item => (
      (!dataset || item.dataset === dataset)
      && (!field || item.field === field)
    ));
  }

  function normalizedInput(item = {}) {
    const exact = inputCandidates.find(candidate => (
      candidate.dataset === item.dataset
      && candidate.field === item.field
      && candidate.frequency === item.frequency
    ));
    if (exact || !inputCandidates.length) return {...item};
    const fallback = candidatesFor(item.dataset, item.field)[0]
      || candidatesFor(item.dataset)[0]
      || inputCandidates[0];
    return {...item, dataset: fallback.dataset, field: fallback.field, frequency: fallback.frequency};
  }

  function collectParameters() {
    return [...document.querySelectorAll('[data-factor-parameter-row]')].map(row => {
      const raw = row.querySelector('[data-parameter-value]').value;
      return {
        name: row.querySelector('[data-parameter-name]').value.trim(),
        value: raw === '' ? '' : Number(raw),
        unit: row.querySelector('[data-parameter-unit]').value,
      };
    });
  }

  function renderInputs() {
    inputRows = inputRows.map(normalizedInput);
    $('factorInputCapabilityStatus').innerHTML = inputDatasets.map(dataset => {
      const provider = dataset.provider_status || {};
      const fields = arr(dataset.fields).map(item => item.id).join(', ');
      const frequencies = [...new Set(
        arr(dataset.fields).flatMap(item => arr(item.frequencies).map(candidate => candidate.frequency))
      )].join(', ');
      const unavailable = String(provider.status || '').toUpperCase() !== 'READY';
      return `<div class="factor-capability-row ${unavailable ? 'unavailable' : 'ready'}">
        <div><span>${esc(dataset.label || dataset.id)}</span><strong>${esc(fields || 'No fields')}</strong><small>${esc(frequencies || 'No supported frequencies')}</small></div>
        <div><span>Provider</span><strong>${esc(arr(provider.providers).join(', ') || 'Not resolved')}</strong><small>${esc(unavailable ? (provider.reason || 'Unavailable') : 'Requestable for the current Universe')}</small></div>
      </div>`;
    }).join('') || inputContractDiagnostics.map(item => `<div class="factor-capability-row unavailable"><div><strong>${esc(item.message)}</strong></div></div>`).join('');
    $('factorInputRows').innerHTML = inputRows.map((item, index) => {
      const datasetOptions = distinctBy(inputCandidates, 'dataset');
      const fieldOptions = distinctBy(candidatesFor(item.dataset), 'field');
      const frequencyOptions = candidatesFor(item.dataset, item.field);
      const selected = frequencyOptions.find(candidate => candidate.frequency === item.frequency);
      const unavailable = !selected;
      const fallbackDataset = {dataset: item.dataset || 'bars', dataset_label: item.dataset || 'Market Bars'};
      const fallbackField = {field: item.field || 'close', field_label: item.field || 'close'};
      const fallbackFrequency = {frequency: item.frequency || '1h'};
      const count = selected?.instrument_count ?? instrumentSummary.member_count ?? universeMembers;
      const requestable = selected?.requestable_instrument_count ?? 0;
      const prepared = selected?.prepared_instrument_count ?? 0;
      const unavailableReason = inputContractDiagnostics[0]?.message
        || 'This saved Input is not available for the current Universe.';
      return `<div class="factor-variable-card factor-input-binding ${unavailable ? 'unavailable' : ''}" data-factor-input-row data-index="${index}">
        <div class="factor-variable-summary"><div><span>${unavailable ? 'UNAVAILABLE INPUT' : 'VARIABLE'}</span><strong>${esc(item.variable_name || 'Unnamed Input')}</strong><small>${esc(item.dataset || 'dataset')}.${esc(item.field || 'field')} · ${esc(item.frequency || 'frequency')}</small></div>${inputRows.length > 1 || unavailable ? `<button type="button" data-factor-remove-input="${index}">Remove</button>` : ''}</div>
        <div class="form-grid">
          <label>Variable Name<input data-input-variable data-original-name="${esc(item.variable_name || '')}" value="${esc(item.variable_name || '')}" placeholder="price"></label>
          <label>Dataset<select data-input-dataset ${unavailable ? 'disabled' : ''}>${(unavailable ? [fallbackDataset] : datasetOptions).map(candidate => `<option value="${esc(candidate.dataset)}" ${candidate.dataset === item.dataset ? 'selected' : ''}>${esc(candidate.dataset_label || candidate.dataset)}</option>`).join('')}</select></label>
          <label>Field<select data-input-field ${unavailable ? 'disabled' : ''}>${(unavailable ? [fallbackField] : fieldOptions).map(candidate => `<option value="${esc(candidate.field)}" ${candidate.field === item.field ? 'selected' : ''}>${esc(candidate.field_label || candidate.field)}</option>`).join('')}</select></label>
          <label>Frequency<select data-input-frequency ${unavailable ? 'disabled' : ''}>${(unavailable ? [fallbackFrequency] : frequencyOptions).map(candidate => `<option value="${esc(candidate.frequency)}" ${candidate.frequency === item.frequency ? 'selected' : ''}>${esc(candidate.frequency)}</option>`).join('')}</select></label>
          ${unavailable ? `<div class="factor-input-unavailable"><strong>Unavailable for this Universe</strong><small>${esc(unavailableReason)}</small></div>` : ''}
        </div>
        <div class="factor-binding-map"><span><code>${esc(item.variable_name || 'variable')}</code> = <strong>${esc(item.dataset || 'dataset')}.${esc(item.field || 'field')} · ${esc(item.frequency || 'frequency')}</strong></span><small>${unavailable ? esc(unavailableReason) : `${esc(requestable)} / ${esc(count)} Instruments requestable · ${esc(prepared)} prepared locally`}</small></div>
      </div>`;
    }).join('') || '<div class="factor-list-empty">No requestable Inputs are available. Resolve a Universe with supported provider IDs first.</div>';
    $('factorAddInput').disabled = inputRows.length >= maxInputs || !inputCandidates.length;
    $('factorInputLimit').textContent = !inputCandidates.length
      ? 'No Factor Input candidates are available for the current Universe.'
      : inputRows.length >= maxInputs
      ? `Current Formula limit reached: ${maxInputs} Inputs.`
      : `${inputRows.length} of ${maxInputs} Inputs configured.`;
  }

  function renderParameters() {
    const formulaSource = $('factorFormulaSource')?.value || draftDocument.formula.source || '';
    const parameterUsage = name => {
      if (!name) return 'Name this Parameter to see where it is used.';
      const identifier = new RegExp(`(^|[^A-Za-z0-9_])${String(name).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^A-Za-z0-9_]|$)`);
      const lines = formulaSource.split('\n').map(item => item.trim()).filter(item => identifier.test(item));
      return lines.length ? `Used in: ${lines.slice(0, 2).join(' · ')}` : `Unused Parameter · ${name} is not referenced by the Formula.`;
    };
    $('factorParameterRows').innerHTML = parameterRows.map((item, index) => `<div class="factor-parameter-card" data-factor-parameter-row data-index="${index}">
      <div><span>PARAMETER</span><strong>${esc(item.name || 'Unnamed')} = ${esc(item.value === '' ? 'Not set' : item.value)} ${esc(item.unit || 'bars')}</strong><small>${esc(parameterUsage(item.name))}</small></div>
      <label>Name<input data-parameter-name value="${esc(item.name || '')}" placeholder="window"></label>
      <label>Value<input data-parameter-value type="number" min="1" value="${esc(item.value ?? '')}"></label>
      <label>Unit<select data-parameter-unit><option value="bars" selected>bars</option></select></label>
      <button type="button" data-factor-remove-parameter="${index}">Remove</button>
    </div>`).join('') || '<div class="factor-list-empty">No named Parameters yet.</div>';
  }

  function currentDocument() {
    return {
      schema_version: 'factor_draft.v2',
      identity: {
        name: $('factorName').value.trim(),
        version: $('factorVersion').value,
        description: $('factorDescription').value.trim(),
      },
      inputs: collectInputs(),
      parameters: collectParameters(),
      formula: {source: $('factorFormulaSource').value.trim()},
      output: {
        final: $('factorFinalOutput').value,
        display_name: $('factorOutputDisplayName').value.trim(),
        direction: 'NO_PREDEFINED_DIRECTION',
      },
      advanced: {
        missing_policy: draftDocument.advanced.missing_policy || 'STRICT',
        minimum_observations: draftDocument.advanced.minimum_observations || '',
        dimension: 'TIME_SERIES',
        time_alignment_policy: cap.time_alignment_policy || 'BAR_END_AVAILABLE_TIME',
        available_after: arr(cap.available_after)[0] || 'BAR_CLOSE',
        allow_incomplete_bar: false,
      },
    };
  }

  function friendlyDiagnostic(item) {
    const messages = {
      FACTOR_NAME_REQUIRED: ['Add a Factor name.', 'Use a short name that describes the measurement.'],
      INPUT_REQUIRED: ['Add an Input variable.', 'The Formula needs one data Input.'],
      INPUT_VARIABLE_REQUIRED: ['Name the Input variable.', 'For example: price.'],
      FORMULA_SOURCE_REQUIRED: ['Enter a Formula.', 'For example: universe.rank(time.pct_change(price, window)).'],
      FORMULA_PARAMETER_UNKNOWN: ['The Formula references a missing Parameter.', 'Add the named Parameter or update the Formula.'],
      FORMULA_INPUT_UNKNOWN: ['The Formula references an unknown Input.', 'Use the variable name shown in the Input section.'],
      FORMULA_NAME_UNKNOWN: ['The Formula references an unknown name.', 'Use an Input, Parameter, or calculated result shown in this editor.'],
      FORMULA_FUNCTION_UNSUPPORTED: ['This function is not supported by the current engine.', 'Choose a real function from Browse Functions.'],
      FORMULA_OUTPUT_REQUIRED: ['Choose the Final Output.', 'Select one named result in the Output section.'],
      FORMULA_OUTPUT_UNKNOWN: ['The selected Output no longer exists.', 'Choose a calculated result that is still defined in the Formula.'],
      FORMULA_OUTPUT_CONFLICT: ['The Formula and Output selection disagree.', 'Select the same final name or remove the trailing expression.'],
      FORMULA_RESULT_FORWARD_REFERENCE: ['A calculated result is used before it is defined.', 'Move its definition above the first use.'],
      FORMULA_RESULT_SELF_REFERENCE: ['A calculated result references itself.', 'Use an earlier result instead.'],
      FORMULA_RESULT_DUPLICATE: ['A calculated result is defined twice.', 'Give every calculated result one unique name.'],
      FORMULA_STATEMENT_UNSUPPORTED: ['This Formula statement is not allowed.', 'Use name = expression lines and one optional final expression.'],
      FACTOR_V4_FREQUENCY_ALIGNMENT_REQUIRED: ['These Inputs use different frequencies.', 'Use align.asof(slower_input, reference_input) to make the point-in-time rule explicit.'],
      FACTOR_V4_ALIGNMENT_REFERENCE_REQUIRED: ['Choose one reference frequency.', 'The second align.asof argument must resolve to one evaluation frequency.'],
      FACTOR_V4_BOOLEAN_REQUIRED: ['This function needs a Boolean condition.', 'Build one with greater, less, equal, or another Conditional function.'],
      FACTOR_V4_UNIT_MISMATCH: ['These values cannot be combined with this operator.', 'Use values with compatible units or an explicit conversion.'],
      FACTOR_INPUT_CANDIDATE_UNAVAILABLE: ['This Input is unavailable for the current Universe.', 'Remove it and choose a requestable Dataset, Field, and Frequency.'],
      INPUT_PROVIDER_UNAVAILABLE: ['The historical-data provider is unavailable.', 'Review Provider status in Input. Preview remains blocked until the provider is ready.'],
    };
    const mapped = messages[item.code];
    return mapped ? {title: mapped[0], detail: mapped[1]} : {title: item.message, detail: 'Review the highlighted field and try again.'};
  }

  function clearFieldErrors() {
    document.querySelectorAll('#factorForm .field-error').forEach(node => node.classList.remove('field-error'));
  }

  function highlightDiagnostics(diagnostics) {
    clearFieldErrors();
    const fieldForPath = path => {
      if (path === 'identity.name') return $('factorName');
      if (path.startsWith('inputs')) return document.querySelector('[data-factor-input-row] input, [data-factor-input-row] select');
      if (path.startsWith('parameters')) return document.querySelector('[data-factor-parameter-row] input, [data-factor-parameter-row] select');
      if (path.startsWith('formula')) return $('factorFormulaSource');
      if (path.startsWith('output')) return $('factorFinalOutput');
      return null;
    };
    const first = diagnostics.find(item => item.level === 'ERROR');
    diagnostics.filter(item => item.level === 'ERROR').forEach(item => fieldForPath(item.path)?.classList.add('field-error'));
    fieldForPath(first?.path)?.focus?.();
  }

  function renderValidation(result) {
    latestValidation = result;
    const ready = Boolean(result.can_preview);
    $('factorValidationState').textContent = ready ? 'Ready for Preview' : 'Needs attention';
    $('factorValidationState').className = `preview-state ${ready ? 'valid' : 'invalid'}`;
    $('factorValidationSummary').innerHTML = `<div><span>Errors</span><strong>${esc(result.summary?.errors ?? 0)}</strong></div><div><span>Warnings</span><strong>${esc(result.summary?.warnings ?? 0)}</strong></div>`;
    const diagnostics = arr(result.diagnostics);
    const checklist = ready ? [
      ['Inputs resolved', 'Every Formula variable maps to a selected data field.'],
      ['Formula graph is supported', 'Nested functions and composition passed server checks.'],
      ['Evaluation timing is safe', 'Mixed frequencies require explicit as-of alignment using available data only.'],
      ['Parameter units are valid', 'Named windows use bars.'],
      ['Output type inferred', 'Type, unit, evaluation, and meaning are available.'],
    ] : [];
    $('factorDiagnostics').innerHTML = `${checklist.map(item => `<div class="factor-diagnostic success"><span>PASS</span><div><strong>${esc(item[0])}</strong><small>${esc(item[1])}</small></div></div>`).join('')}${diagnostics.map(item => {
      const friendly = friendlyDiagnostic(item);
      return `<div class="factor-diagnostic ${String(item.level).toLowerCase()}"><span>${esc(item.level)}</span><div><strong>${esc(friendly.title)}</strong><small>${esc(friendly.detail)}</small></div></div>`;
    }).join('')}`;
    const compilation = result.compiled_formula;
    const spec = result.compiled_factor_spec;
    const output = compilation?.output_display || {};
    renderOutputChoices(compilation?.named_results || null);
    $('factorResolvedFormula').textContent = compilation?.resolved_formula || 'Waiting for a valid Formula';
    const historyByInput = Object.entries(compilation?.required_history_by_input || {});
    $('factorRequiredHistory').textContent = historyByInput.length > 1
      ? historyByInput.map(([name, value]) => `${name}: ${value}`).join(' · ')
      : (compilation?.required_history || '—');
    $('factorFormulaMeaning').textContent = compilation?.formula_meaning || 'Complete the Formula to see a plain-language explanation.';
    $('factorOutputType').value = output.type || 'Waiting for Formula';
    $('factorOutputUnit').value = output.unit || 'Waiting for Formula';
    $('factorEvaluationFrequency').value = output.evaluation || 'Waiting for Formula';
    $('factorOutputDimension').value = output.dimension || 'Waiting for Formula';
    $('factorOutputNullability').value = output.nullability || 'Waiting for Formula';
    $('factorValueMeaning').value = output.value_meaning || 'Complete the Formula to infer the value meaning.';
    $('factorExecutionContract').innerHTML = spec
      ? `<span>Engine <strong>${esc(spec.engine_version)}</strong></span><span>Dimension <strong>${esc(spec.dimension)}</strong></span><span>Evaluation point <strong>${esc(spec.available_after)}</strong></span><span>Point-in-time <strong>${esc(spec.time_alignment_policy)}</strong></span>`
      : 'Available after the Formula is checked.';
    $('factorCompiledSpecAdvanced').textContent = spec ? json(spec) : 'Not compiled';
    const fingerprint = currentDraft?.draft_fingerprint || result.draft_fingerprint || '';
    $('factorAuditInfo').innerHTML = `<span>Revision <strong>${esc($('factorVersion').value)}</strong></span><span>Draft fingerprint <strong>${esc(fingerprint ? fingerprint.slice(0, 16) : 'Not saved')}</strong></span><span>Preview fingerprint <strong>${esc(latestPreview?.preview_fingerprint ? latestPreview.preview_fingerprint.slice(0, 16) : 'Not run')}</strong></span><span>Specification hash <strong>${esc(compilation?.spec_hash ? compilation.spec_hash.slice(0, 16) : 'Not compiled')}</strong></span><span>Definition ID <strong>${esc(currentDraft?.validated_definition_id || 'Created on Validate Factor')}</strong></span>`;
    if ($('factorRunPreview')) $('factorRunPreview').disabled = !(currentDraft && ready);
    if ($('factorValidateFactor')) {
      $('factorValidateFactor').disabled = !(
        currentDraft
        && latestPreview
        && result.can_validate
        && latestPreview.draft_fingerprint === currentDraft.draft_fingerprint
      );
    }
  }

  function utcInputValue(value) {
    return value ? String(value).replace('Z', '').replace('+00:00', '').slice(0, 16) : '';
  }

  function utcIsoValue(value) {
    return value ? `${value}:00+00:00` : '';
  }

  function previewNumber(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return String(value);
    return Math.abs(numeric) >= 1000 || (Math.abs(numeric) > 0 && Math.abs(numeric) < .0001)
      ? numeric.toExponential(4)
      : numeric.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
  }

  function renderPreviewContext(context) {
    previewContext = context;
    const universeInfo = context?.universe || {};
    const range = context?.time_range || {};
    $('factorPreviewContext').innerHTML = context?.can_run_preview
      ? `<div><span>Universe Snapshot</span><strong>${esc(universeInfo.name || 'Current Universe')} · ${esc(universeInfo.member_count || 0)} Instruments</strong><small>${esc(universeInfo.universe_snapshot_id || '')}</small></div><div><span>Data Manifests</span><strong>${esc(arr(context.candidate_manifest_ids).length)} fixed Manifest(s)</strong><small>${esc(arr(context.candidate_manifest_ids).join(', '))}</small></div>`
      : arr(context?.diagnostics).map(item => `<div class="preview-error">${esc(item.message)}</div>`).join('');
    if (!$('factorPreviewStart').value && range.suggested_start) {
      $('factorPreviewStart').value = utcInputValue(range.suggested_start);
    }
    if (!$('factorPreviewEnd').value && range.suggested_end) {
      $('factorPreviewEnd').value = utcInputValue(range.suggested_end);
    }
    if (!$('factorPreviewEnd').value) {
      $('factorPreviewEnd').value = utcInputValue(new Date().toISOString());
    }
    if (!$('factorPreviewStart').value) {
      $('factorPreviewStart').value = utcInputValue(new Date(Date.now() - 7 * 86400000).toISOString());
    }
    $('factorRunPreview').disabled = !(currentDraft && latestValidation?.can_preview);
  }

  function renderCompiledRequirement(result) {
    const requirements = arr(result?.requirements);
    const statuses = arr(result?.data_status?.rows);
    if (!requirements.length) {
      $('factorPreviewRequirement').innerHTML = '<div class="preview-error">No data Requirement was generated.</div>';
      return;
    }
    const missing = statuses.filter(item => item.status !== 'READY');
    $('factorPreviewRequirement').innerHTML = `
      <div class="factor-section-heading compact"><div><span>PREVIEW REQUIREMENTSET</span><h3>Exact data needed</h3></div><div class="button-row">${statusChip(missing.length ? 'Preparing' : 'Ready')}</div></div>
      ${requirements.map(item => {
        const rowStatuses = statuses.filter(row => row.requirement_id === item.requirement_id);
        const ready = rowStatuses.filter(row => row.status === 'READY').length;
        return `<div class="factor-requirement-row">
          <strong>${esc(item.dataset)} · ${esc(arr(item.fields).join(', '))} · ${esc(item.frequency)}</strong>
          <span>${esc(item.instrument_count)} Instruments</span>
          <small>Evaluation ${esc(formatDate(item.evaluation_range?.start))} → ${esc(formatDate(item.evaluation_range?.end))}</small>
          <small>Required data starts ${esc(formatDate(item.required_range?.start))} · ${esc(item.additional_history?.observations || 0)} additional observations from Formula history</small>
          <small>${esc(ready)} / ${esc(rowStatuses.length)} Instruments ready</small>
        </div>`;
      }).join('')}`;
  }

  function renderPreview(preview, stateLabel = '') {
    latestPreview = preview || null;
    if (!preview) {
      $('factorPreviewState').textContent = stateLabel || 'Not run';
      $('factorPreviewState').className = `preview-state ${stateLabel === 'Stale' ? 'invalid' : ''}`;
      $('factorPreviewResults').innerHTML = `<p>${stateLabel === 'Stale' ? 'The Draft changed after Preview. Run Preview again.' : 'No Preview has been run for this Draft revision.'}</p>`;
      $('factorValidateFactor').disabled = true;
      return;
    }
    const overall = preview.analysis?.overall || {};
    const latest = arr(preview.analysis?.latest_cross_section);
    $('factorPreviewState').textContent = preview.status === 'READY' ? 'Ready' : friendlyStatus(preview.status);
    $('factorPreviewState').className = `preview-state ${preview.status === 'READY' ? 'valid' : 'invalid'}`;
    $('factorPreviewStart').value = utcInputValue(preview.time_range?.start);
    $('factorPreviewEnd').value = utcInputValue(preview.time_range?.end);
    $('factorPreviewResults').innerHTML = `
      <div class="factor-preview-metrics">
        <div><span>Valid values</span><strong>${esc(overall.valid_value_count || 0)}</strong></div>
        <div><span>Coverage</span><strong>${esc(overall.coverage_percent ?? 0)}%</strong></div>
        <div><span>Mean</span><strong>${esc(previewNumber(overall.mean))}</strong></div>
        <div><span>Std dev</span><strong>${esc(previewNumber(overall.standard_deviation))}</strong></div>
      </div>
      <div class="factor-preview-latest">
        <span>LATEST VALUES</span>
        ${latest.map(item => `<div><code>${esc(String(item.instrument_id).split(':').pop())}</code><strong>${esc(previewNumber(item.value))}</strong><small>${esc(formatDate(item.as_of_time))}</small></div>`).join('') || '<p>No usable values.</p>'}
      </div>
      <details><summary>Preview evidence</summary><div class="factor-advanced-facts"><span>Preview fingerprint <strong>${esc(preview.preview_fingerprint)}</strong></span><span>Universe Snapshot <strong>${esc(preview.universe_snapshot_id)}</strong></span><span>Manifest IDs <strong>${esc(arr(preview.manifest_ids).join(', '))}</strong></span><span>Engine <strong>${esc(preview.engine_version)}</strong></span><span>Specification hash <strong>${esc(preview.spec_hash)}</strong></span></div></details>`;
    $('factorValidateFactor').disabled = !(
      latestValidation?.can_validate
      && currentDraft
      && preview.draft_fingerprint === currentDraft.draft_fingerprint
    );
  }

  async function loadPreviewContext() {
    if (!currentDraft || dialogDestroyed) return;
    try {
      const context = await api(`/api/research/factor-drafts/${encodeURIComponent(currentDraft.draft_id)}/preview-context`);
      if (!dialogDestroyed) renderPreviewContext(context);
    } catch (error) {
      if (dialogDestroyed) return;
      $('factorPreviewContext').innerHTML = `<div class="preview-error">${esc(error.message)}</div>`;
      $('factorRunPreview').disabled = !(currentDraft && latestValidation?.can_preview);
    }
  }

  async function loadLatestPreview() {
    if (!currentDraft || dialogDestroyed) return;
    try {
      const preview = await api(`/api/research/factor-drafts/${encodeURIComponent(currentDraft.draft_id)}/previews/latest`);
      if (!dialogDestroyed) renderPreview(preview);
    } catch (error) {
      if (!dialogDestroyed) renderPreview(null);
    }
  }

  async function checkDocument() {
    const token = ++validationToken;
    $('factorValidationState').textContent = 'Checking';
    $('factorValidationState').className = 'preview-state';
    try {
      let result = await api('/api/research/factor-drafts/validation', {
        method: 'POST',
        body: JSON.stringify({
          document: currentDocument(),
          owner_project_id: state.projectId,
        }),
      });
      if (
        currentDraft
        && result.draft_fingerprint === currentDraft.draft_fingerprint
      ) {
        result = await api(`/api/research/factor-drafts/${encodeURIComponent(currentDraft.draft_id)}/validation`);
      }
      if (!dialogDestroyed && token === validationToken) renderValidation(result);
    } catch (error) {
      if (dialogDestroyed || token !== validationToken) return;
      $('factorValidationState').textContent = 'Unavailable';
      $('factorValidationState').className = 'preview-state invalid';
      $('factorDiagnostics').innerHTML = `<div class="preview-error">${esc(error.message)}</div>`;
    }
  }

  function scheduleCheck() {
    clearTimeout(validationTimer);
    validationTimer = window.setTimeout(checkDocument, 180);
  }

  function setUserStatus(status, detail) {
    if (dialogDestroyed) return;
    $('factorUserStatus').textContent = status;
    $('factorStatusDetail').textContent = detail;
  }

  async function persistDocument(documentSnapshot) {
    const body = {document: documentSnapshot, owner_project_id: state.projectId, library_scope: 'PROJECT'};
    const saved = currentDraft
      ? await api(`/api/research/factor-drafts/${encodeURIComponent(currentDraft.draft_id)}`, {
        method: 'PUT',
        body: JSON.stringify({document: body.document, expected_fingerprint: currentDraft.draft_fingerprint}),
      })
      : await api('/api/research/factor-drafts', {method: 'POST', body: JSON.stringify(body)});
    currentDraft = saved;
    const index = state.factorDrafts.findIndex(item => item.draft_id === saved.draft_id);
    if (index >= 0) state.factorDrafts[index] = saved; else state.factorDrafts.unshift(saved);
    if (!dialogDestroyed) {
      setUserStatus('Changes backed up', 'Backed up just now.');
      renderValidation(saved.validation);
      if (saved.validation?.preview_status === 'READY' && saved.validation.preview) {
        renderPreview(saved.validation.preview);
      } else if (saved.validation?.preview_status !== 'READY') {
        renderPreview(null, saved.validation?.preview_status === 'STALE' ? 'Stale' : '');
      }
      loadPreviewContext();
    }
    return saved;
  }

  function queuePersist(documentSnapshot) {
    saveQueue = saveQueue.catch(() => undefined).then(() => persistDocument(documentSnapshot));
    return saveQueue;
  }

  function scheduleBackup() {
    clearTimeout(backupTimer);
    setUserStatus('Unsaved changes', 'Backing up automatically…');
    backupTimer = window.setTimeout(async () => {
      try {
        await queuePersist(currentDocument());
      } catch (error) {
        setUserStatus('Unsaved changes', 'Automatic backup could not complete.');
        notify(`Automatic backup failed: ${error.message}`, true);
      }
    }, 1400);
  }

  function markPreviewStale() {
    if (!latestPreview) return;
    latestPreview = null;
    renderPreview(null, 'Stale');
    $('factorSaveHint').textContent = 'Draft changed. Save it and run a new Preview before validation.';
  }

  function markChanged() {
    markPreviewStale();
    scheduleCheck();
    scheduleBackup();
  }

  $('factorForm').addEventListener('click', event => {
    const browserFunction = event.target.closest('[data-factor-browser-function]');
    if (browserFunction) {
      const operator = operators.find(item => item.id === browserFunction.dataset.factorBrowserFunction);
      insertFunction(operator);
      $('factorFunctionBrowser').hidden = true;
      return;
    }
    const suggestion = event.target.closest('[data-factor-suggestion]');
    if (suggestion) {
      chooseFormulaSuggestion(Number(suggestion.dataset.factorSuggestion));
      return;
    }
    const removeInput = event.target.closest('[data-factor-remove-input]');
    if (removeInput) {
      inputRows = collectInputs();
      inputRows.splice(Number(removeInput.dataset.factorRemoveInput), 1);
      renderInputs();
      markChanged();
      return;
    }
    const removeParameter = event.target.closest('[data-factor-remove-parameter]');
    if (removeParameter) {
      parameterRows = collectParameters();
      parameterRows.splice(Number(removeParameter.dataset.factorRemoveParameter), 1);
      renderParameters();
      markChanged();
    }
  });
  $('factorAddInput').addEventListener('click', () => {
    inputRows = collectInputs();
    if (inputRows.length >= maxInputs || !inputCandidates.length) return;
    const candidate = inputCandidates[0];
    inputRows.push({
      variable_name: suggestedVariableName(candidate.field, candidate.frequency),
      dataset: candidate.dataset,
      field: candidate.field,
      frequency: candidate.frequency,
    });
    renderInputs();
    markChanged();
  });
  $('factorBrowseFunctions').addEventListener('click', () => {
    $('factorFunctionBrowser').hidden = false;
    $('factorFunctionSearch').value = '';
    renderFunctionBrowser();
    $('factorFunctionSearch').focus();
  });
  $('factorCloseFunctions').addEventListener('click', () => {
    $('factorFunctionBrowser').hidden = true;
    $('factorFormulaSource').focus();
  });
  $('factorFunctionSearch').addEventListener('input', renderFunctionBrowser);
  $('factorAddParameter').addEventListener('click', () => {
    parameterRows = collectParameters();
    let suffix = parameterRows.length + 1;
    let name = `parameter_${suffix}`;
    while (parameterRows.some(item => item.name === name)) name = `parameter_${++suffix}`;
    parameterRows.push({name, value: 20, unit: 'bars'});
    renderParameters();
    markChanged();
  });
  $('factorFormulaSource').addEventListener('keydown', event => {
    if (!formulaSuggestions.length && event.key === 'Tab') {
      const editor = event.target;
      const remainder = editor.value.slice(editor.selectionEnd);
      const match = remainder.match(/\b(series|source|reference|window|periods|fast_window|slow_window|left|right|condition|when_true|when_false|replacement|numerator|denominator)\b/);
      if (match) {
        event.preventDefault();
        const start = editor.selectionEnd + match.index;
        editor.setSelectionRange(start, start + match[0].length);
      }
      return;
    }
    if (!formulaSuggestions.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      suggestionIndex = (suggestionIndex + (event.key === 'ArrowDown' ? 1 : -1) + formulaSuggestions.length) % formulaSuggestions.length;
      [...$('factorFormulaSuggestions').querySelectorAll('[data-factor-suggestion]')].forEach((item, index) => {
        item.classList.toggle('active', index === suggestionIndex);
        item.setAttribute('aria-selected', String(index === suggestionIndex));
      });
    } else if (event.key === 'Enter') {
      event.preventDefault();
      chooseFormulaSuggestion(suggestionIndex);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      hideFormulaSuggestions();
    }
  });
  $('factorForm').addEventListener('input', event => {
    if (event.target.id === 'factorFunctionSearch') return;
    if (['factorPreviewStart', 'factorPreviewEnd'].includes(event.target.id)) {
      markPreviewStale();
      return;
    }
    if (event.target.id === 'factorFormulaSource') {
      renderOutputChoices();
      renderFormulaSuggestions();
    }
    markChanged();
  });
  $('factorForm').addEventListener('change', event => {
    if (['factorPreviewStart', 'factorPreviewEnd'].includes(event.target.id)) {
      markPreviewStale();
      return;
    }
    if (event.target.matches('[data-input-variable]')) {
      const previous = event.target.dataset.originalName || '';
      const next = event.target.value.trim();
      const peers = collectInputs().filter(item => item.variable_name === next);
      if (
        previous
        && previous !== next
        && /^[A-Za-z_][A-Za-z0-9_]*$/.test(next)
        && peers.length === 1
      ) {
        const escaped = previous.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        $('factorFormulaSource').value = $('factorFormulaSource').value.replace(
          new RegExp(`(^|[^A-Za-z0-9_])${escaped}(?=[^A-Za-z0-9_]|$)`, 'g'),
          (match, prefix) => `${prefix}${next}`,
        );
        event.target.dataset.originalName = next;
        renderOutputChoices();
      }
    }
    if (event.target.matches('[data-input-dataset], [data-input-field]')) {
      inputRows = collectInputs().map(normalizedInput);
      renderInputs();
    }
    markChanged();
  });
  $('factorForm').addEventListener('submit', async event => {
    event.preventDefault();
    clearTimeout(backupTimer);
    setUserStatus('Unsaved changes', 'Saving the current Draft…');
    $('factorSaveHint').textContent = 'Saving the current Input, Formula, Parameters, and Output.';
    try {
      await saveQueue;
      const saved = await queuePersist(currentDocument());
      if (!saved.validation?.can_preview) {
        renderValidation(saved.validation);
        highlightDiagnostics(arr(saved.validation.diagnostics));
        setUserStatus('Changes backed up', 'Draft saved. Fix the Definition issues before Preview.');
        $('factorSaveHint').textContent = `${saved.validation?.summary?.errors || 1} issue(s) must be fixed before Run Preview.`;
        notify('Draft saved with issues. The fields needing attention are highlighted.', true);
        return;
      }
      setUserStatus('Changes backed up', 'Draft saved. Run Preview against fixed research inputs.');
      $('factorSaveHint').textContent = 'Draft saved. Choose the UTC time range and run Preview.';
      notify('Factor Draft saved. Run Preview is ready.');
    } catch (error) {
      setUserStatus(currentDraft ? 'Changes backed up' : 'Unsaved changes', 'The Draft could not be saved. Your content remains in the editor.');
      $('factorSaveHint').textContent = error.message;
      notify(error.message, true);
    }
  });
  $('factorRunPreview').addEventListener('click', async () => {
    clearTimeout(backupTimer);
    $('factorRunPreview').disabled = true;
    $('factorPreviewState').textContent = 'Running';
    $('factorPreviewState').className = 'preview-state';
    $('factorSaveHint').textContent = 'Generating the exact data Requirement, checking gaps, then computing real values…';
    try {
      await saveQueue;
      const saved = await queuePersist(currentDocument());
      if (!saved.validation?.can_preview) {
        renderValidation(saved.validation);
        highlightDiagnostics(arr(saved.validation.diagnostics));
        throw new Error('Fix the Definition issues before Run Preview.');
      }
      const startTime = utcIsoValue($('factorPreviewStart').value);
      const endTime = utcIsoValue($('factorPreviewEnd').value);
      if (!startTime || !endTime) throw new Error('Choose a Preview start and end time.');
      const snapshotId = inputContract.universe?.universe_snapshot_id || currentSnapshot()?.universe_snapshot_id;
      const compiledRequirement = await api(`/api/research/factor-drafts/${encodeURIComponent(saved.draft_id)}/requirements`, {
        method: 'POST',
        body: JSON.stringify({
          expected_fingerprint: saved.draft_fingerprint,
          universe_snapshot_id: snapshotId,
          start_time: startTime,
          end_time: endTime,
        }),
      });
      renderCompiledRequirement(compiledRequirement);
      const notReady = arr(compiledRequirement.data_status?.rows).filter(item => item.status !== 'READY');
      if (notReady.length) {
        $('factorPreviewState').textContent = 'Preparing data';
        setUserStatus('Preparing data', 'Creating a scoped data task and waiting for exact coverage.');
        await prepareFactorPreviewRequirements(compiledRequirement, {
          renderStatus: renderCompiledRequirement,
        });
      }
      const context = await api(`/api/research/factor-drafts/${encodeURIComponent(saved.draft_id)}/preview-context`);
      renderPreviewContext(context);
      if (!context.can_run_preview) throw new Error('Requirement is ready, but the fixed Preview manifests could not be resolved.');
      const preview = await api(`/api/research/factor-drafts/${encodeURIComponent(saved.draft_id)}/previews`, {
        method: 'POST',
        body: JSON.stringify({
          expected_fingerprint: saved.draft_fingerprint,
          universe_snapshot_id: context.universe.universe_snapshot_id,
          start_time: startTime,
          end_time: endTime,
        }),
      });
      currentDraft.latest_preview_id = preview.preview_id;
      currentDraft.latest_preview_fingerprint = preview.preview_fingerprint;
      currentDraft.previewed_draft_fingerprint = currentDraft.draft_fingerprint;
      currentDraft.previewed_at = preview.created_at;
      latestPreview = preview;
      const validation = await api(`/api/research/factor-drafts/${encodeURIComponent(saved.draft_id)}/validation`);
      renderValidation(validation);
      renderPreview(preview);
      setUserStatus('Preview ready', 'Real values are pinned to the displayed Snapshot, range, and Manifests.');
      $('factorSaveHint').textContent = 'Preview is current. Review the values, then Validate Factor.';
      notify('Factor Preview completed with real values.');
    } catch (error) {
      $('factorPreviewState').textContent = 'Failed';
      $('factorPreviewState').className = 'preview-state invalid';
      $('factorPreviewResults').innerHTML = `<div class="preview-error">${esc(error.message)}</div>`;
      $('factorSaveHint').textContent = error.message;
      $('factorRunPreview').disabled = !(currentDraft && latestValidation?.can_preview);
      notify(error.message, true);
    }
  });
  $('factorValidateFactor').addEventListener('click', async () => {
    if (!currentDraft || !latestPreview) return;
    $('factorValidateFactor').disabled = true;
    setUserStatus('Preview ready', 'Checking the current Preview fingerprint…');
    $('factorSaveHint').textContent = 'Validating only if Draft, Snapshot, Manifests, Engine, and Preview fingerprint are still current.';
    try {
      const result = await api(`/api/research/factor-drafts/${encodeURIComponent(currentDraft.draft_id)}/validate`, {
        method: 'POST',
        body: JSON.stringify({
          expected_fingerprint: currentDraft.draft_fingerprint,
          preview_id: latestPreview.preview_id,
          preview_fingerprint: latestPreview.preview_fingerprint,
        }),
      });
      currentDraft = result.draft;
      const draftIndex = state.factorDrafts.findIndex(item => item.draft_id === result.draft.draft_id);
      if (draftIndex >= 0) state.factorDrafts[draftIndex] = result.draft;
      const definitionIndex = state.definitions.findIndex(item => item.definition_id === result.definition.definition_id);
      if (definitionIndex >= 0) state.definitions[definitionIndex] = result.definition;
      else state.definitions.unshift(result.definition);
      if (result.library_asset) {
        const libraryIndex = state.library.findIndex(item => item.library_asset_id === result.library_asset.library_asset_id);
        if (libraryIndex >= 0) state.library[libraryIndex] = result.library_asset;
        else state.library.unshift(result.library_asset);
      }
      setUserStatus('Factor validated', 'Ready in this Research and automatically available in Library.');
      notify('Factor validated and added to Library.');
      closeDialog();
      await loadResearch(state.projectId);
      switchResearchTab('factor');
    } catch (error) {
      setUserStatus('Preview needs review', 'Validation was blocked. The Draft and Preview remain available.');
      $('factorSaveHint').textContent = error.message;
      $('factorValidateFactor').disabled = false;
      notify(error.message, true);
    }
  });

  renderInputs();
  renderParameters();
  renderOutputChoices();
  checkDocument();
  if (currentDraft) {
    loadPreviewContext();
    loadLatestPreview();
  }
}

function addLibraryDefinitionDialog(type) {
  const items = latestByName(state.library.filter(item => item.component_type === type));
  const label = type === 'FACTOR' ? 'Factor' : 'Alpha';
  openDialog('Add from Library', `LIBRARY · ${label.toUpperCase()}`, `<div class="choice-list">${items.map(asset => `<div class="choice-card"><div><strong>${esc(asset.name)}</strong><small>Library v${esc(asset.version)} · ${type === 'FACTOR' ? esc(factorFormula(asset.content.spec)) : esc(alphaFormula(asset.content))}</small></div><button type="button" class="primary" data-action="use-definition" data-id="${esc(asset.source_object_id)}" data-library-id="${esc(asset.library_asset_id)}" data-project-id="${esc(state.projectId)}">Add to Research</button></div>`).join('') || `<p class="muted">Library has no published ${label}.</p>`}</div>`);
}

async function setResearchDefinition(definition, referenceMode = 'PINNED', projectId = state.projectId, libraryAssetId = '') {
  const slot = `${definition.definition_type.toLowerCase()}:${definition.name}`;
  return api(`/api/research/projects/${encodeURIComponent(projectId)}/definition-refs/${encodeURIComponent(slot)}`, {method: 'PUT', body: JSON.stringify({definition_id: definition.definition_id, definition_version: definition.version, reference_mode: referenceMode, library_asset_id: libraryAssetId})});
}

async function useDefinition(id, projectId = state.projectId, libraryAssetId = '') {
  const definition = state.definitions.find(item => item.definition_id === id);
  if (!definition) throw new Error('Library component not found.');
  await setResearchDefinition(definition, 'PINNED', projectId, libraryAssetId);
  if (projectId === state.projectId && state.surface === 'research-detail') {
    closeDialog(); await loadResearch(projectId); switchResearchTab(definition.definition_type === 'FACTOR' ? 'factor' : 'alpha');
  } else closeDialog();
  notify(`${definition.definition_type === 'FACTOR' ? 'Factor' : 'Alpha'} added to Research.`);
}

function legacyAlphaDialog(base = null) {
  const cap = state.capabilities.alpha || {};
  const factors = researchRefs('FACTOR').filter(ref => ref.state === 'VALIDATED');
  if (!factors.length) { notify('Validate a Factor first.', true); switchResearchTab('factor'); return; }
  const spec = base?.spec || {};
  const name = base?.name || 'new_alpha';
  const version = nextSemanticVersion(state.definitions.filter(item => item.definition_type === 'ALPHA'), name, base?.version || '');
  let components = arr(spec.components).length ? arr(spec.components).map(item => ({...item})) : [{factor_definition_id: factors[0].definition_id, factor_version: factors[0].definition_version, weight: 1, transform: 'RAW', ascending: true}];
  openDialog(base ? `Create New Version of ${name}` : 'Create Alpha in Research', 'RESEARCH · ALPHA', `<form id="alphaForm" class="form-stack"><section class="form-section"><h3>Alpha</h3><label>Name<input id="alphaName" value="${esc(name)}" ${base ? 'readonly' : ''} required></label></section><section class="form-section"><div class="panel-head"><div><h3>Input and Formula</h3></div><button type="button" data-action="add-alpha-component">Add Factor</button></div><div id="alphaComponents" class="form-stack"></div></section><section class="form-section"><h3>Output</h3><label>Output Type<select id="alphaOutput"><option>Prediction Score</option></select></label></section><div class="form-grid"><label>Minimum Coverage (%)<input id="alphaCoverage" type="number" min="1" max="100" value="${Math.round(Number(spec.minimum_coverage || 1) * 100)}"></label><label>Minimum Instruments<input id="alphaCross" type="number" min="1" value="${esc(spec.minimum_cross_section_size || 1)}"></label></div><div class="form-actions"><button type="submit" class="primary">Save Draft</button></div></form>`);
  function syncComponents() {
    document.querySelectorAll('[data-alpha-row]').forEach(row => {
      const index = Number(row.dataset.alphaRow);
      const factor = factors.find(item => item.definition_id === row.querySelector('[data-field="factor"]').value);
      components[index] = {factor_definition_id: factor.definition_id, factor_version: factor.definition_version, weight: Number(row.querySelector('[data-field="weight"]').value), transform: row.querySelector('[data-field="transform"]').value, ascending: true};
    });
  }
  function renderComponents() {
    $('alphaComponents').innerHTML = components.map((component, index) => `<div class="choice-card" data-alpha-row="${index}"><label>Factor<select data-field="factor">${factors.map(factor => `<option value="${esc(factor.definition_id)}" ${factor.definition_id === component.factor_definition_id ? 'selected' : ''}>${esc(factor.name)}</option>`).join('')}</select></label><label>Weight<input data-field="weight" type="number" step="0.1" value="${esc(component.weight ?? 1)}"></label><label>Transform<select data-field="transform">${(arr(cap.transforms).length ? arr(cap.transforms) : ['RAW', 'CS_RANK', 'ZSCORE']).map(value => `<option ${value === component.transform ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select></label><button type="button" data-action="remove-alpha-component" data-index="${index}" aria-label="Remove">&times;</button></div>`).join('');
  }
  renderComponents();
  $('alphaForm').addEventListener('click', event => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (action === 'add-alpha-component') { syncComponents(); components.push({factor_definition_id: factors[0].definition_id, factor_version: factors[0].definition_version, weight: 1, transform: 'RAW', ascending: true}); renderComponents(); }
    if (action === 'remove-alpha-component') { syncComponents(); components.splice(Number(event.target.dataset.index), 1); renderComponents(); }
  });
  $('alphaForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      syncComponents();
      const definitionSpec = {name: $('alphaName').value.trim(), version, components, universe_snapshot_id: currentSnapshot()?.universe_snapshot_id || '', minimum_coverage: Number($('alphaCoverage').value) / 100, minimum_cross_section_size: Number($('alphaCross').value), missing_policy: arr(cap.missing_policies)[0] || 'EXCLUDE', rank_method: arr(cap.rank_methods)[0] || 'AVERAGE', output_scale: arr(cap.output_scales)[0] || 'PERCENTILE'};
      const created = await api('/api/research/definitions', {method: 'POST', body: JSON.stringify({definition_type: 'ALPHA', state: 'DRAFT', spec: definitionSpec, owner_project_id: state.projectId, library_scope: 'PROJECT'})});
      state.definitions.unshift(created);
      await setResearchDefinition(created, 'TRACK_DRAFT');
      closeDialog(); await loadResearch(state.projectId); switchResearchTab('alpha'); notify('Alpha saved as a Research draft.');
    } catch (error) { notify(error.message, true); }
  });
}

function alphaDialog(base = null) {
  const cap = state.capabilities.alpha || {};
  const authoring = cap.authoring_contract || {};
  const candidates = arr(state.alphaFactorCandidates?.factors);
  if (!candidates.length) {
    notify('Validate and publish a Factor first.', true);
    switchResearchTab('factor');
    return;
  }
  let currentDraft = base?.draft_id ? base : null;
  let latestValidation = null;
  let latestPreview = null;
  let saveQueue = Promise.resolve();
  let validationTimer = null;
  let backupTimer = null;
  let validationToken = 0;
  let destroyed = false;
  const sourceDocument = base?.document || {};
  const sourceSpec = base?.spec || {};
  const sourceIdentity = sourceDocument.identity || {};
  const name = sourceIdentity.name || base?.name || 'new_alpha';
  const version = sourceIdentity.version || (base?.definition_id
    ? nextSemanticVersion([
      ...state.definitions.filter(item => item.definition_type === 'ALPHA'),
      ...state.alphaDrafts.map(item => ({
        name: item.document?.identity?.name,
        version: item.document?.identity?.version,
      })),
    ], name, base.version || '')
    : '1.0.0');
  const rawComponents = arr(sourceDocument.components).length
    ? sourceDocument.components
    : sourceSpec.components;
  let components = arr(rawComponents).length
    ? arr(rawComponents).map((item, index) => ({
      variable_name: item.variable_name || item.factor_name?.replace(/[^A-Za-z0-9_]/g, '_') || `factor_${index + 1}`,
      factor_definition_id: item.factor_definition_id,
      factor_version: item.factor_version,
      weight: Number(item.weight ?? 1),
      transform: item.transform || 'CS_RANK',
      ascending: item.transform === 'RAW' ? true : item.ascending !== false,
    }))
    : [{
      variable_name: 'factor_1',
      factor_definition_id: candidates[0].definition_id,
      factor_version: candidates[0].version,
      weight: 1,
      transform: 'CS_RANK',
      ascending: true,
    }];
  const advanced = sourceDocument.advanced || sourceSpec;
  const randomKey = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const clientDraftKey = `ui:${state.projectId}:${randomKey}:alpha-editor`;
  const universe = currentUniverse();
  const snapshot = currentSnapshot();
  const universeMembers = arr(universe?.current_resolution?.instrument_ids).length
    || arr(snapshot?.actual_instrument_ids).length;
  openDialog(
    currentDraft ? `Edit ${name}` : base?.definition_id ? `Create New Version of ${name}` : 'Create Alpha',
    'RESEARCH · ALPHA',
    `<form id="alphaForm" class="factor-editor alpha-editor" novalidate>
      <div class="factor-editor-context factor-editor-context-user">
        <div><span>Universe</span><strong>${esc(universe?.name || 'Not configured')}</strong><small>${esc(universeMembers)} Instruments</small></div>
        <div><span>Status</span><strong id="alphaUserStatus">${currentDraft ? 'Changes backed up' : 'Unsaved changes'}</strong><small id="alphaStatusDetail">${currentDraft ? `Revision ${esc(currentDraft.draft_fingerprint.slice(0, 12))}` : 'Your work will be backed up automatically.'}</small></div>
      </div>
      <div class="factor-editor-layout"><div class="factor-editor-main form-stack">
        <section class="form-section factor-identity"><div class="factor-section-heading"><div><span>ALPHA</span><h3>Identity</h3></div><small>Describe the prediction score, not a trading action.</small></div><div class="form-grid"><label>Name<input id="alphaName" value="${esc(name)}" ${currentDraft || base?.definition_id ? 'readonly' : ''} required></label><label>Version<input id="alphaVersion" value="${esc(version)}" readonly></label><label class="wide">Description<textarea id="alphaDescription" rows="2" placeholder="What this Alpha predicts and how its Factors contribute.">${esc(sourceIdentity.description || '')}</textarea></label></div><p class="form-help">Saving creates a new revision. Existing research runs remain unchanged.</p></section>
        <section class="form-section"><div class="factor-section-heading"><div><span>INPUT</span><h3>Factor Components</h3></div><button type="button" id="alphaAddComponent">Add Factor</button></div><div class="factor-input-source"><div><span>Current Universe</span><strong>${esc(universe?.name || 'Not configured')}</strong></div><div><span>Component limit</span><strong id="alphaComponentLimit">${esc(components.length)} / ${esc(Number(authoring.max_components || 8))} selected</strong></div><small>Every component pins an exact validated Factor ID and Version from this Research.</small></div><div id="alphaComponents" class="factor-variable-list"></div></section>
        <section class="form-section factor-formula-section"><div class="factor-section-heading"><div><span>FORMULA</span><h3>Weighted Component Sum</h3></div><small>Generated from the selected weights, transforms, and score directions.</small></div><div class="factor-editor-guide"><span>Exact Factor versions</span><span>Controlled transforms</span><span>Deterministic weighted sum</span></div><div class="formula-source"><span>Resolved Formula</span><pre id="alphaFormulaPreview"></pre></div></section>
        <section class="form-section"><div class="factor-section-heading"><div><span>OUTPUT</span><h3>Prediction Score</h3></div><small>Ranking and coverage are evaluated during Preview.</small></div><div class="form-grid factor-output-grid"><label>Output Type<input value="Prediction Score" readonly></label><label>Rank Method<input value="${esc(arr(cap.rank_methods)[0] || 'AVERAGE')}" readonly></label></div></section>
        <details class="form-section factor-advanced"><summary>Advanced Details</summary><div class="form-grid"><label>Minimum Coverage (%)<input id="alphaCoverage" type="number" min="1" max="100" value="${esc(Math.round(Number(advanced.minimum_coverage ?? 1) * 100))}"></label><label>Minimum Instruments<input id="alphaCross" type="number" min="1" value="${esc(advanced.minimum_cross_section_size || 2)}"></label><label>Missing Policy<input value="${esc(arr(cap.missing_policies)[0] || 'EXCLUDE')}" readonly></label><label>Output Scale<input value="${esc(arr(cap.output_scales)[0] || 'PERCENTILE')}" readonly></label></div></details>
      </div><aside class="factor-live-preview factor-checks-panel">
        <div class="live-preview-head"><div><span class="eyebrow">DEFINITION CHECKS</span><h3 id="alphaCheckTitle">Checking</h3></div><span id="alphaValidationState" class="preview-state">Checking</span></div>
        <div id="alphaValidationSummary" class="preview-metrics"><div><span>Errors</span><strong>—</strong></div><div><span>Warnings</span><strong>—</strong></div></div>
        <div id="alphaDiagnostics" class="factor-diagnostics"><p class="preview-detail">Checking the current identity, components, formula, and output…</p></div>
        <section class="factor-values-preview">
          <div class="live-preview-head"><div><span class="eyebrow">VALUE PREVIEW</span><h3>Real Alpha scores</h3></div><span id="alphaPreviewState" class="preview-state">Not run</span></div>
          <div id="alphaPreviewContext" class="factor-preview-context"><p>Save the Draft to pin the current Universe Snapshot and Factor closure.</p></div>
          <div class="factor-preview-range"><label>Start · UTC<input id="alphaPreviewStart" type="datetime-local" step="60"></label><label>End · UTC<input id="alphaPreviewEnd" type="datetime-local" step="60"></label></div>
          <div id="alphaPreviewRequirement" class="factor-preview-requirement"><p>The exact data Requirement is generated from the pinned Factors and Preview range.</p></div>
          <div id="alphaPreviewResults" class="factor-preview-results"><p>No Preview has been run for this Draft revision.</p></div>
        </section>
      </aside></div>
      <div class="factor-editor-actions"><div><span>Save Draft → Run Preview → Validate Alpha</span><small id="alphaSaveHint">Changes are backed up automatically. Validation requires a current Preview fingerprint.</small></div><div class="form-actions"><button type="submit">Save Draft</button><button type="button" id="alphaRunPreview" disabled>Run Preview</button><button type="button" class="primary" id="alphaValidateAlpha" disabled>Validate Alpha</button></div></div>
    </form>`,
  );
  $('editorDialog').classList.add('factor-dialog');
  $('editorDialog').addEventListener('close', () => {
    destroyed = true;
    clearTimeout(validationTimer);
    clearTimeout(backupTimer);
  }, {once: true});

  function candidateFor(component) {
    return candidates.find(item =>
      item.definition_id === component.factor_definition_id
      && item.version === component.factor_version
    );
  }
  function collectComponents() {
    return [...document.querySelectorAll('[data-alpha-row]')].map((row, index) => {
      const factorKey = row.querySelector('[data-field="factor"]').value;
      const factor = candidates.find(item => `${item.definition_id}@@${item.version}` === factorKey);
      const transform = row.querySelector('[data-field="transform"]').value;
      return {
        variable_name: row.querySelector('[data-field="variable"]').value.trim() || `factor_${index + 1}`,
        factor_definition_id: factor?.definition_id || factorKey.split('@@')[0],
        factor_version: factor?.version || factorKey.split('@@')[1] || '',
        weight: Number(row.querySelector('[data-field="weight"]').value),
        transform,
        ascending: transform === 'RAW' ? true : row.querySelector('[data-field="direction"]').value === 'HIGH_VALUE_HIGH_SCORE',
      };
    });
  }
  function renderComponents() {
    $('alphaComponents').innerHTML = components.map((component, index) => {
      const selected = candidateFor(component);
      const unavailable = selected ? '' : `<option value="${esc(`${component.factor_definition_id}@@${component.factor_version}`)}" selected>UNAVAILABLE FACTOR · ${esc(component.factor_definition_id)} @ ${esc(component.factor_version)}</option>`;
      return `<div class="factor-variable-card alpha-component-row" data-alpha-row="${index}">
        <div class="factor-variable-summary"><div><span>Component ${esc(index + 1)}</span><strong>${esc(selected?.name || component.factor_definition_id || 'Factor')}</strong><small>${esc(component.factor_version || 'Version not selected')} · ${esc(component.transform || 'RAW')}</small></div><button type="button" class="danger-quiet compact-button" data-action="remove-alpha-component" data-index="${index}" aria-label="Remove component">Remove</button></div>
        <div class="alpha-component-fields">
          <label>Variable Name<input data-field="variable" value="${esc(component.variable_name || `factor_${index + 1}`)}"></label>
          <label class="alpha-factor-select">Factor Version<select data-field="factor">${unavailable}${candidates.map(factor => `<option value="${esc(`${factor.definition_id}@@${factor.version}`)}" ${factor.definition_id === component.factor_definition_id && factor.version === component.factor_version ? 'selected' : ''}>${esc(factor.name)} @ ${esc(factor.version)} · ${esc(factor.origin)}</option>`).join('')}</select></label>
          <label>Weight<input data-field="weight" type="number" step="0.1" value="${esc(component.weight ?? 1)}"></label>
          <label>Transform<select data-field="transform">${arr(cap.transforms).map(value => `<option ${value === component.transform ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select></label>
          <label>Score Direction<select data-field="direction" ${component.transform === 'RAW' ? 'disabled' : ''}><option value="HIGH_VALUE_HIGH_SCORE" ${component.ascending !== false ? 'selected' : ''}>High value → high score</option><option value="LOW_VALUE_HIGH_SCORE" ${component.ascending === false ? 'selected' : ''}>Low value → high score</option></select></label>
        </div>
        <div class="factor-binding-map"><span><code>${esc(component.variable_name || `factor_${index + 1}`)}</code> = <strong>${esc(selected?.name || component.factor_definition_id || 'Factor')} @ ${esc(component.factor_version || '—')}</strong></span><small>${esc(component.weight ?? 1)} weight · ${esc(component.transform || 'RAW')} · ${component.ascending === false ? 'lower values score higher' : 'higher values score higher'}</small></div>
      </div>`;
    }).join('');
    $('alphaFormulaPreview').textContent = alphaDraftFormula({components});
    $('alphaAddComponent').disabled = components.length >= Number(authoring.max_components || 8);
    $('alphaComponentLimit').textContent = `${components.length} / ${Number(authoring.max_components || 8)} selected`;
  }
  function currentDocument() {
    components = collectComponents();
    return {
      schema_version: authoring.document_version || 'alpha_draft.v2',
      identity: {
        name: $('alphaName').value.trim(),
        description: $('alphaDescription').value.trim(),
        version: $('alphaVersion').value,
      },
      components,
      formula: {model: 'WEIGHTED_SUM'},
      output: {display_name: `${$('alphaName').value.trim()} Score`, kind: 'PREDICTION_SCORE'},
      advanced: {
        minimum_coverage: Number($('alphaCoverage').value) / 100,
        minimum_cross_section_size: Number($('alphaCross').value),
        missing_policy: arr(cap.missing_policies)[0] || 'EXCLUDE',
        rank_method: arr(cap.rank_methods)[0] || 'AVERAGE',
        output_scale: arr(cap.output_scales)[0] || 'PERCENTILE',
      },
    };
  }
  function renderValidation(result) {
    latestValidation = result;
    const ready = Boolean(result?.can_preview);
    const summary = result?.summary || {};
    $('alphaValidationState').textContent = ready ? 'Ready for Preview' : 'Needs attention';
    $('alphaValidationState').className = `preview-state ${ready ? 'valid' : 'invalid'}`;
    $('alphaValidationSummary').innerHTML = `<div><span>Errors</span><strong>${esc(summary.errors || 0)}</strong></div><div><span>Warnings</span><strong>${esc(summary.warnings || 0)}</strong></div>`;
    $('alphaCheckTitle').textContent = ready ? 'Definition is coherent' : 'Fix blocking fields';
    $('alphaDiagnostics').innerHTML = arr(result?.diagnostics).length
      ? arr(result.diagnostics).map(item => `<div class="factor-diagnostic ${item.level === 'ERROR' ? 'error' : 'warning'}"><span>${item.level === 'ERROR' ? 'ERROR' : 'WARN'}</span><div><strong>${esc(item.code)}</strong><small>${esc(item.message)}</small></div></div>`).join('')
      : '<div class="factor-diagnostic success"><span>PASS</span><div><strong>Definition is coherent</strong><small>Identity, components, settings, access, and Universe checks passed.</small></div></div>';
    $('alphaRunPreview').disabled = !(currentDraft && ready);
    $('alphaValidateAlpha').disabled = !(currentDraft && latestPreview && result?.can_validate);
  }
  const utcInputValue = value => value ? String(value).replace('Z', '').replace('+00:00', '').slice(0, 16) : '';
  const utcIsoValue = value => value ? `${value}:00+00:00` : '';
  const numberLabel = value => Number.isFinite(Number(value)) ? Number(value).toFixed(6).replace(/0+$/, '').replace(/\.$/, '') : '—';
  function renderPreviewContext(context) {
    const universe = context?.universe || {};
    const range = context?.time_range || {};
    $('alphaPreviewContext').innerHTML = context?.can_run_preview
      ? `<div><span>Universe Snapshot</span><strong>${esc(universe.name)} · ${esc(universe.member_count)} Instruments</strong><small>${esc(universe.universe_snapshot_id)}</small></div><div><span>Factor closure</span><strong>${esc(arr(context.factor_refs).length)} pinned Factor(s)</strong><small>${esc(context.dependency_fingerprint?.slice(0, 16) || '')}</small></div><div><span>Data Manifests</span><strong>${esc(arr(context.candidate_manifest_ids).length)} fixed Manifest(s)</strong></div>`
      : arr(context?.diagnostics).map(item => `<div class="preview-error">${esc(item.message)}</div>`).join('');
    if (!$('alphaPreviewStart').value && range.suggested_start) $('alphaPreviewStart').value = utcInputValue(range.suggested_start);
    if (!$('alphaPreviewEnd').value && range.suggested_end) $('alphaPreviewEnd').value = utcInputValue(range.suggested_end);
    $('alphaRunPreview').disabled = !(currentDraft && latestValidation?.can_preview && context?.can_run_preview);
  }
  function renderPreview(preview, label = '') {
    latestPreview = preview || null;
    if (!preview) {
      $('alphaPreviewState').textContent = label || 'Not run';
      $('alphaPreviewState').className = `preview-state ${label === 'Stale' ? 'invalid' : ''}`;
      $('alphaPreviewResults').innerHTML = `<p>${label === 'Stale' ? 'The Draft changed after Preview. Run Preview again.' : 'No Preview has been run for this Draft revision.'}</p>`;
      $('alphaValidateAlpha').disabled = true;
      return;
    }
    const overall = preview.analysis?.overall || {};
    const latest = arr(preview.analysis?.latest_cross_section);
    $('alphaPreviewState').textContent = 'Ready';
    $('alphaPreviewState').className = 'preview-state valid';
    $('alphaPreviewStart').value = utcInputValue(preview.time_range?.start);
    $('alphaPreviewEnd').value = utcInputValue(preview.time_range?.end);
    $('alphaPreviewResults').innerHTML = `<div class="factor-preview-metrics"><div><span>Valid scores</span><strong>${esc(overall.valid_value_count || 0)}</strong></div><div><span>Time points</span><strong>${esc(overall.time_point_count || 0)}</strong></div><div><span>Minimum coverage</span><strong>${esc(overall.minimum_coverage == null ? '—' : `${(overall.minimum_coverage * 100).toFixed(1)}%`)}</strong></div><div><span>Std dev</span><strong>${esc(numberLabel(overall.standard_deviation))}</strong></div></div><div class="factor-preview-latest"><span>LATEST SCORES</span>${latest.slice(0, 12).map(item => `<div><code>${esc(requirementInstrumentLabel(item.instrument_id))}</code><strong>${esc(numberLabel(item.raw_score))}</strong><small>Rank ${esc(item.rank)} · Percentile ${esc(numberLabel(item.percentile))}</small></div>`).join('') || '<p>No usable scores.</p>'}</div><details><summary>Preview evidence</summary><div class="factor-advanced-facts"><span>Preview fingerprint <strong>${esc(preview.preview_fingerprint)}</strong></span><span>Dependency fingerprint <strong>${esc(preview.dependency_fingerprint)}</strong></span><span>Manifest IDs <strong>${esc(arr(preview.manifest_ids).join(', '))}</strong></span></div></details>`;
    $('alphaValidateAlpha').disabled = !latestValidation?.can_validate;
  }
  async function checkDocument() {
    const token = ++validationToken;
    try {
      let result = await api('/api/research/alpha-drafts/validation', {
        method: 'POST',
        body: JSON.stringify({document: currentDocument(), owner_project_id: state.projectId}),
      });
      if (currentDraft && result.draft_fingerprint === currentDraft.draft_fingerprint) {
        result = await api(`/api/research/alpha-drafts/${encodeURIComponent(currentDraft.draft_id)}/validation`);
      }
      if (!destroyed && token === validationToken) renderValidation(result);
    } catch (error) {
      if (!destroyed && token === validationToken) $('alphaDiagnostics').innerHTML = `<div class="preview-error">${esc(error.message)}</div>`;
    }
  }
  async function loadPreviewContext() {
    if (!currentDraft || destroyed) return;
    try {
      renderPreviewContext(await api(`/api/research/alpha-drafts/${encodeURIComponent(currentDraft.draft_id)}/preview-context`));
    } catch (error) {
      if (!destroyed) $('alphaPreviewContext').innerHTML = `<div class="preview-error">${esc(error.message)}</div>`;
    }
  }
  async function loadLatestPreview() {
    if (!currentDraft || destroyed) return;
    try {
      renderPreview(await api(`/api/research/alpha-drafts/${encodeURIComponent(currentDraft.draft_id)}/previews/latest`));
    } catch (_) {
      renderPreview(null);
    }
  }
  async function persist(documentSnapshot) {
    const saved = currentDraft
      ? await api(`/api/research/alpha-drafts/${encodeURIComponent(currentDraft.draft_id)}`, {
        method: 'PUT',
        body: JSON.stringify({document: documentSnapshot, expected_fingerprint: currentDraft.draft_fingerprint}),
      })
      : await api('/api/research/alpha-drafts', {
        method: 'POST',
        body: JSON.stringify({owner_project_id: state.projectId, library_scope: 'PROJECT', client_draft_key: clientDraftKey, document: documentSnapshot}),
      });
    currentDraft = saved;
    $('alphaName').readOnly = true;
    const index = state.alphaDrafts.findIndex(item => item.draft_id === saved.draft_id);
    if (index >= 0) state.alphaDrafts[index] = saved; else state.alphaDrafts.unshift(saved);
    $('alphaUserStatus').textContent = 'Changes backed up';
    $('alphaStatusDetail').textContent = `Revision ${saved.draft_fingerprint.slice(0, 12)} · backed up just now`;
    renderValidation(saved.validation);
    if (saved.validation?.preview_status !== 'READY') renderPreview(null, saved.validation?.preview_status === 'STALE' ? 'Stale' : '');
    await loadPreviewContext();
    return saved;
  }
  function queuePersist(documentSnapshot) {
    saveQueue = saveQueue.catch(() => undefined).then(() => persist(documentSnapshot));
    return saveQueue;
  }
  function markChanged() {
    components = collectComponents();
    $('alphaFormulaPreview').textContent = alphaDraftFormula({components});
    if (latestPreview) renderPreview(null, 'Stale');
    clearTimeout(validationTimer);
    validationTimer = window.setTimeout(checkDocument, 180);
    clearTimeout(backupTimer);
    $('alphaUserStatus').textContent = 'Unsaved changes';
    $('alphaStatusDetail').textContent = 'Backing up automatically…';
    backupTimer = window.setTimeout(() => queuePersist(currentDocument()).catch(error => notify(error.message, true)), 1400);
  }

  $('alphaAddComponent').addEventListener('click', () => {
    components = collectComponents();
    if (components.length >= Number(authoring.max_components || 8)) return;
    components.push({
      variable_name: `factor_${components.length + 1}`,
      factor_definition_id: candidates[0].definition_id,
      factor_version: candidates[0].version,
      weight: 1,
      transform: 'CS_RANK',
      ascending: true,
    });
    renderComponents();
    markChanged();
  });
  $('alphaForm').addEventListener('click', event => {
    const remove = event.target.closest('[data-action="remove-alpha-component"]');
    if (!remove) return;
    components = collectComponents();
    components.splice(Number(remove.dataset.index), 1);
    renderComponents();
    markChanged();
  });
  $('alphaForm').addEventListener('input', event => {
    if (['alphaPreviewStart', 'alphaPreviewEnd'].includes(event.target.id)) {
      if (latestPreview) renderPreview(null, 'Stale');
      return;
    }
    markChanged();
  });
  $('alphaForm').addEventListener('change', event => {
    if (event.target.matches('[data-field="transform"]')) {
      components = collectComponents();
      renderComponents();
    }
    if (!['alphaPreviewStart', 'alphaPreviewEnd'].includes(event.target.id)) markChanged();
  });
  $('alphaForm').addEventListener('submit', async event => {
    event.preventDefault();
    clearTimeout(backupTimer);
    try {
      await saveQueue;
      const saved = await queuePersist(currentDocument());
      if (!saved.validation?.can_preview) {
        notify('Draft saved with issues. Fix Definition checks before Preview.', true);
        return;
      }
      $('alphaSaveHint').textContent = 'Draft saved. Choose the UTC range and run Preview.';
      notify('Alpha Draft saved. Run Preview is ready.');
    } catch (error) {
      $('alphaSaveHint').textContent = error.message;
      notify(error.message, true);
    }
  });
  $('alphaRunPreview').addEventListener('click', async () => {
    clearTimeout(backupTimer);
    $('alphaRunPreview').disabled = true;
    $('alphaPreviewState').textContent = 'Running';
    try {
      await saveQueue;
      const saved = await queuePersist(currentDocument());
      if (!saved.validation?.can_preview) throw new Error('Fix Definition issues before Run Preview.');
      const startTime = utcIsoValue($('alphaPreviewStart').value);
      const endTime = utcIsoValue($('alphaPreviewEnd').value);
      if (!startTime || !endTime) throw new Error('Choose a Preview start and end time.');
      const payload = {
        expected_fingerprint: saved.draft_fingerprint,
        universe_snapshot_id: currentSnapshot()?.universe_snapshot_id || '',
        start_time: startTime,
        end_time: endTime,
      };
      const requirement = await api(`/api/research/alpha-drafts/${encodeURIComponent(saved.draft_id)}/requirements`, {method: 'POST', body: JSON.stringify(payload)});
      const renderAlphaRequirement = result => {
        const notReady = arr(result.data_status?.rows).filter(item => item.status !== 'READY');
        $('alphaPreviewRequirement').innerHTML = `<small>${arr(result.requirements).length} exact Requirement(s) · ${notReady.length ? `${notReady.length} input(s) preparing automatically` : 'data ready'}</small>`;
      };
      renderAlphaRequirement(requirement);
      const notReady = arr(requirement.data_status?.rows).filter(item => item.status !== 'READY');
      if (notReady.length) {
        await prepareFactorPreviewRequirements(requirement, {
          renderStatus: renderAlphaRequirement,
          statusElementId: 'alphaSaveHint',
        });
      }
      const context = await api(`/api/research/alpha-drafts/${encodeURIComponent(saved.draft_id)}/preview-context`);
      renderPreviewContext(context);
      if (!context.can_run_preview) throw new Error('The fixed Preview manifests could not be resolved.');
      const preview = await api(`/api/research/alpha-drafts/${encodeURIComponent(saved.draft_id)}/previews`, {method: 'POST', body: JSON.stringify(payload)});
      currentDraft.latest_preview_id = preview.preview_id;
      currentDraft.latest_preview_fingerprint = preview.preview_fingerprint;
      currentDraft.previewed_draft_fingerprint = currentDraft.draft_fingerprint;
      const validation = await api(`/api/research/alpha-drafts/${encodeURIComponent(saved.draft_id)}/validation`);
      renderValidation(validation);
      renderPreview(preview);
      $('alphaSaveHint').textContent = 'Preview is current. Review values, then Validate Alpha.';
      notify('Alpha Preview completed with real values.');
    } catch (error) {
      $('alphaPreviewState').textContent = 'Failed';
      $('alphaPreviewState').className = 'preview-state invalid';
      $('alphaPreviewResults').innerHTML = `<div class="preview-error">${esc(error.message)}</div>`;
      $('alphaSaveHint').textContent = error.message;
      $('alphaRunPreview').disabled = !(currentDraft && latestValidation?.can_preview);
      notify(error.message, true);
    }
  });
  $('alphaValidateAlpha').addEventListener('click', async () => {
    if (!currentDraft || !latestPreview) return;
    $('alphaValidateAlpha').disabled = true;
    try {
      const result = await api(`/api/research/alpha-drafts/${encodeURIComponent(currentDraft.draft_id)}/validate`, {
        method: 'POST',
        body: JSON.stringify({
          expected_fingerprint: currentDraft.draft_fingerprint,
          preview_id: latestPreview.preview_id,
          preview_fingerprint: latestPreview.preview_fingerprint,
        }),
      });
      const draftIndex = state.alphaDrafts.findIndex(item => item.draft_id === result.draft.draft_id);
      if (draftIndex >= 0) state.alphaDrafts[draftIndex] = result.draft;
      state.definitions.unshift(result.definition);
      if (result.library_asset) state.library.unshift(result.library_asset);
      notify('Alpha validated and added to Library.');
      closeDialog();
      await loadResearch(state.projectId);
      switchResearchTab('alpha');
    } catch (error) {
      $('alphaSaveHint').textContent = error.message;
      $('alphaValidateAlpha').disabled = false;
      notify(error.message, true);
    }
  });

  renderComponents();
  checkDocument();
  if (currentDraft) {
    loadPreviewContext();
    loadLatestPreview();
  }
}

function requirementSpecFromForm() {
  const targetUniverseId = $('reqUniverseId')?.value || '';
  const symbols = targetUniverseId ? [] : $('reqInstruments').value.split(/[\s,;]+/).map(value => value.trim().toUpperCase()).filter(Boolean);
  const fields = [...document.querySelectorAll('[name="reqField"]:checked')].map(node => node.value);
  const provider = arr(state.dataCapabilities.providers).find(item => item.id === $('reqProvider').value);
  const market = arr(provider?.markets).find(item => item.id === $('reqMarket').value);
  const allowedSources = [...($('reqAllowedSources')?.selectedOptions || [])].map(node => node.value.toLowerCase());
  const preferredSources = [...($('reqPreferredSources')?.selectedOptions || [])].map(node => node.value.toLowerCase()).filter(value => allowedSources.includes(value));
  const providerPolicy = allowedSources.length > 1 ? 'PRIMARY_FALLBACK' : allowedSources.length === 1 ? 'FIXED' : 'AUTO';
  return {
    name: $('reqName').value.trim(),
    target: {scope: targetUniverseId ? 'SPECIFIC_UNIVERSE' : 'MANUAL_INSTRUMENTS', universe_id: targetUniverseId},
    scope: {provider: $('reqProvider').value, gateway: provider?.gateway || 'DATATUBE', market: $('reqMarket').value, asset_type: market?.asset_type || 'CRYPTO', instruments: {type: targetUniverseId ? 'UNIVERSE_REFERENCE' : 'STATIC_LIST', include: symbols}},
    time: {mode: $('reqTimeMode').value, start: $('reqStart').value, end: $('reqTimeMode').value === 'FIXED' ? $('reqEnd').value : 'LATEST_AVAILABLE', lookback_value: null, lookback_unit: 'DAYS'},
    data: {dataset_type: $('reqDataset').value, frequency: $('reqFrequency').value, fields, delivery_mode: 'HISTORICAL'},
    advanced: {point_in_time: $('reqPointInTime').value, available_time: $('reqAvailableTime').value, adjustment: $('reqAdjustment').value, quality_policy: $('reqQuality').value, provider_policy: providerPolicy, allowed_sources: allowedSources, preferred_sources: preferredSources, max_latency_seconds: null, gap_policy: $('reqGapPolicy').value},
  };
}

async function openRequirementEditor({target = 'research', item = null, baseAsset = null, saveAs = false, universeId = '', review = null} = {}) {
  const draft = null;
  const selectedUniverseId = universeId || item?.spec?.target?.universe_id || (target === 'research' ? primaryUniverseBinding()?.universe_id || '' : '');
  const suggestion = target === 'research' && selectedUniverseId
    ? await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/suggestion?universe_id=${encodeURIComponent(selectedUniverseId)}`)
    : null;
  const spec = structuredClone(item?.spec || baseAsset?.spec || suggestion?.spec || await api('/api/research/requirements/default'));
  if (suggestion && item && review) {
    spec.target = structuredClone(suggestion.spec.target);
    spec.scope = {...spec.scope, instruments: {type: 'UNIVERSE_REFERENCE', include: []}};
  }
  if (saveAs && spec.name && !String(spec.name).endsWith(' Copy')) spec.name = `${spec.name} Copy`;
  const script = await api('/api/research/requirements/script/render', {method: 'POST', body: JSON.stringify({spec})});
  state.requirementEditor = {target, item, baseAsset, saveAs, mode: 'ui', universeId: selectedUniverseId, review, suggestion};
  state.pendingRequirementContext = null;
  const selectedFields = new Set(arr(spec.data?.fields));
  const fieldOptions = ['open','high','low','close','volume','quote_volume','trade_count','bid','ask','bid_size','ask_size','mid','spread','price','size','side','trade_id','value','market_cap','volume_24h'];
  const universeSummary = suggestion?.universe;
  const scopeBody = selectedUniverseId && universeSummary
    ? `<input id="reqUniverseId" type="hidden" value="${esc(selectedUniverseId)}"><div class="form-grid"><label>Target<input value="Specific Universe" readonly></label><label>Universe<input value="${esc(universeSummary.name)}" readonly></label></div><div class="requirement-universe-summary"><div><span class="eyebrow">RESOLVED MEMBERS</span><strong>${esc(universeSummary.member_count)} Instrument${universeSummary.member_count === 1 ? '' : 's'}</strong></div><div class="member-tags">${arr(universeSummary.instrument_ids).map(value => `<span>${esc(requirementInstrumentLabel(value))}</span>`).join('')}</div><small>Members are read from the Universe and are not copied into this Requirement.</small></div>`
    : `<input id="reqUniverseId" type="hidden" value=""><label>Manual Instruments<textarea id="reqInstruments" placeholder="BTCUSDT, ETHUSDT" required>${esc(arr(spec.scope?.instruments?.include).join(', '))}</textarea></label><div class="instrument-search"><input id="reqInstrumentSearch" placeholder="Search instruments"><button type="button" data-action="search-requirement-instruments">Search</button></div><div id="reqInstrumentResults" class="member-tags"></div>`;
  const changeSummary = review
    ? `<section class="form-section universe-change-summary"><span class="eyebrow">UNIVERSE CHANGES</span><div class="change-columns"><div><strong>Added</strong><small>${esc(arr(review.changes?.added).map(requirementInstrumentLabel).join(', ') || 'None')}</small></div><div><strong>Removed</strong><small>${esc(arr(review.changes?.removed).map(requirementInstrumentLabel).join(', ') || 'None')}</small></div></div><div><strong>Compatibility</strong><small>${esc(arr(review.reasons).join(' ') || 'Compatible with the current data configuration.')}</small></div></section>`
    : '';
  const suggested = name => suggestion?.suggested?.[name] ? '<span class="suggested-label">Suggested — please confirm</span>' : '';
  const primaryLabel = target === 'library' ? 'Save Library Draft' : item ? 'Update & Prepare Data' : 'Create & Prepare Data';
  const secondaryLabel = item ? 'Save Changes' : 'Save Draft';
  openDialog(item || draft || baseAsset ? `Edit ${spec.name}` : 'New Requirement', target === 'library' ? 'LIBRARY · REQUIREMENT' : 'RESEARCH · REQUIREMENT', `<form id="requirementForm" class="requirement-editor"><nav class="editor-modes"><button type="button" class="active" data-action="requirement-mode" data-mode="ui">UI</button><button type="button" data-action="requirement-mode" data-mode="script">Script</button><button type="button" data-action="requirement-mode" data-mode="split">Split</button></nav><div class="requirement-editor-grid"><div id="requirementUi" class="form-stack">${changeSummary}<section class="form-section"><h3>Scope</h3><label>Name<input id="reqName" value="${esc(spec.name)}" required></label>${scopeBody}</section><section class="form-section"><h3>Data ${suggested('data')}</h3><div class="form-grid"><label>Dataset<select id="reqDataset"><option>BARS</option><option>QUOTES</option><option>TRADES</option><option>PRICE_HISTORY</option><option>SERIES</option></select></label><label>Frequency ${suggested('frequency')}<select id="reqFrequency">${['1m','5m','15m','1h','4h','1d'].map(value => `<option ${value === spec.data?.frequency ? 'selected' : ''}>${value}</option>`).join('')}</select></label></div><fieldset class="field-picker"><legend>Fields</legend>${fieldOptions.map(value => `<label><input type="checkbox" name="reqField" value="${value}" ${selectedFields.has(value) ? 'checked' : ''}> ${value}</label>`).join('')}</fieldset></section><section class="form-section"><h3>Time ${suggested('time')}</h3><div class="form-grid"><label>Range<select id="reqTimeMode"><option value="FIXED_START_LATEST_END">Project Range</option><option value="FIXED">Fixed range</option><option value="LATEST_AVAILABLE">Latest available</option></select></label><label>Start<input id="reqStart" type="date" value="${esc(spec.time?.start || '')}"></label><label>End<input id="reqEnd" type="date" value="${esc(spec.time?.end === 'LATEST_AVAILABLE' ? '' : spec.time?.end || '')}"></label></div></section><section class="form-section"><h3>Source ${suggested('source')}</h3><div class="form-grid"><label>Provider<select id="reqProvider"><option>BINANCE</option></select></label><label>Market<select id="reqMarket"><option>SPOT</option></select></label></div><p id="reqSourceSupports" class="form-help">${suggestion ? `Supports: ${esc(arr(suggestion.source_supports).join(' · '))}` : ''}</p></section><details class="form-section"><summary>Advanced</summary><div class="form-grid"><label>Point in time<select id="reqPointInTime"><option>AS_OF</option></select></label><label>Available time<select id="reqAvailableTime"><option>BAR_END_AVAILABLE_TIME</option></select></label><label>Adjustment<select id="reqAdjustment"><option>NONE</option><option>SPLIT_ADJUSTED</option></select></label><label>Quality<select id="reqQuality"><option>STRICT</option><option>STANDARD</option></select></label><label>Gap policy<select id="reqGapPolicy"><option>REQUIRE_COMPLETE</option><option>ALLOW_GAPS</option></select></label></div></details></div><div id="requirementScriptPane" class="script-pane" hidden><label>Requirement YAML<textarea id="reqScript" spellcheck="false">${esc(script)}</textarea></label><p class="form-help">Script and UI represent the same Requirement contract.</p></div></div><div class="form-actions spread"><button type="button" data-action="close-requirement-editor">Cancel</button><div class="button-row">${target === 'library' ? '' : `<button type="button" id="saveRequirementDraft">${secondaryLabel}</button>`}<button type="submit" class="primary">${saveAs ? 'Save As' : primaryLabel}</button></div></div></form>`);
  $('reqProvider').closest('label').firstChild.textContent = 'Instrument Provider';
  $('reqProvider').closest('.form-grid').insertAdjacentHTML('beforeend', '<label>Compatible Data Sources<select id="reqAllowedSources" multiple size="4"></select></label><label>Fallback Order<select id="reqPreferredSources" multiple size="4"></select></label>');
  $('reqProvider').closest('section').insertAdjacentHTML('beforeend', '<p class="form-help">The Effective RequirementSet can combine many providers across its contracts. Each individual contract lists only sources compatible with its instrument type and dataset; fallback order ranks candidates inside that safe boundary.</p>');
  $('reqProvider').value = spec.scope?.provider || 'BINANCE'; $('reqMarket').value = spec.scope?.market || 'SPOT'; $('reqTimeMode').value = spec.time?.mode || 'FIXED_START_LATEST_END'; $('reqDataset').value = spec.data?.dataset_type || 'BARS'; $('reqPointInTime').value = spec.advanced?.point_in_time || 'AS_OF'; $('reqAvailableTime').value = spec.advanced?.available_time || 'BAR_END_AVAILABLE_TIME'; $('reqAdjustment').value = spec.advanced?.adjustment || 'NONE'; $('reqQuality').value = spec.advanced?.quality_policy || 'STRICT'; $('reqGapPolicy').value = spec.advanced?.gap_policy || 'REQUIRE_COMPLETE';
  const fixedEndValue = spec.time?.end && spec.time.end !== 'LATEST_AVAILABLE' ? spec.time.end : '';
  const syncRequirementTime = () => {
    const fixed = $('reqTimeMode').value === 'FIXED';
    const endInput = $('reqEnd');
    if (fixed) {
      endInput.type = 'date';
      endInput.disabled = false;
      endInput.required = true;
      endInput.value = endInput.dataset.fixedValue || fixedEndValue;
      endInput.title = 'The Requirement stops at this date.';
    } else {
      if (endInput.type === 'date' && endInput.value) endInput.dataset.fixedValue = endInput.value;
      endInput.type = 'text';
      endInput.value = 'Latest available';
      endInput.disabled = true;
      endInput.required = false;
      endInput.title = 'Latest moves forward whenever data status is checked or data is prepared.';
    }
  };
  $('reqEnd').closest('label').insertAdjacentHTML('beforeend', '<small class="form-help">Latest is a moving target, not an empty date.</small>');
  $('reqTimeMode').addEventListener('change', syncRequirementTime);
  $('reqEnd').addEventListener('change', () => { if ($('reqEnd').type === 'date') $('reqEnd').dataset.fixedValue = $('reqEnd').value; });
  syncRequirementTime();
  const providerChoices = arr(state.dataCapabilities.providers);
  const sourceChoices = providerChoices.filter(provider => provider.historical && arr(provider.markets).some(market => market.prepare_supported));
  const selectedAllowed = new Set(arr(spec.advanced?.allowed_sources).map(value => String(value).toUpperCase()));
  const selectedPreferred = new Set(arr(spec.advanced?.preferred_sources).map(value => String(value).toUpperCase()));
  const sourceOptions = selected => sourceChoices.map(provider => `<option value="${esc(provider.id)}" ${selected.has(provider.id) ? 'selected' : ''}>${esc(provider.label)}</option>`).join('');
  $('reqAllowedSources').innerHTML = sourceOptions(selectedAllowed);
  $('reqPreferredSources').innerHTML = sourceOptions(selectedPreferred);
  $('reqProvider').innerHTML = providerChoices.map(provider => `<option value="${esc(provider.id)}" ${arr(provider.markets).length ? '' : 'disabled'}>${esc(provider.label)}${provider.online === false ? ' · offline' : provider.configured === false ? ' · not configured' : !arr(provider.markets).length ? ' · context only' : ''}</option>`).join('');
  $('reqProvider').value = providerChoices.some(provider => provider.id === spec.scope?.provider) ? spec.scope.provider : (providerChoices[0]?.id || 'BINANCE');
  const syncRequirementMarkets = (preferred = '') => {
    const provider = providerChoices.find(item => item.id === $('reqProvider').value);
    $('reqMarket').innerHTML = arr(provider?.markets).map(market => `<option value="${esc(market.id)}">${esc(market.label)}${market.prepare_supported ? '' : ' · definition only'}</option>`).join('');
    if (arr(provider?.markets).some(market => market.id === preferred)) $('reqMarket').value = preferred;
    const market = arr(provider?.markets).find(item => item.id === $('reqMarket').value);
    $('reqDataset').innerHTML = arr(market?.dataset_types).map(value => `<option>${esc(value)}</option>`).join('') || '<option>BARS</option>';
    $('reqFrequency').innerHTML = arr(market?.frequencies).map(value => `<option>${esc(value)}</option>`).join('') || '<option>1d</option>';
    if (arr(market?.dataset_types).includes(spec.data?.dataset_type)) $('reqDataset').value = spec.data.dataset_type;
    if (arr(market?.frequencies).includes(spec.data?.frequency)) $('reqFrequency').value = spec.data.frequency;
    const allowedFields = arr(market?.fields).length ? arr(market.fields) : fieldOptions;
    document.querySelectorAll('[name="reqField"]').forEach(input => {
      input.closest('label').hidden = !allowedFields.includes(input.value);
      if (!allowedFields.includes(input.value)) input.checked = false;
    });
    if (![...document.querySelectorAll('[name="reqField"]:checked')].length && allowedFields.length) {
      const first = document.querySelector(`[name="reqField"][value="${allowedFields[0]}"]`);
      if (first) first.checked = true;
    }
    if (market?.time_semantics) {
      if (![...$('reqAvailableTime').options].some(option => option.value === market.time_semantics)) $('reqAvailableTime').add(new Option(market.time_semantics, market.time_semantics));
      $('reqAvailableTime').value = market.time_semantics;
    }
    if ($('reqInstrumentSearch')) $('reqInstrumentSearch').placeholder = market?.search_category === 'polymarket' ? 'Search Polymarket questions' : market?.search_category === 'fred' ? 'Enter a FRED series ID' : 'Search instruments';
    const note = market?.prepare_supported ? 'Historical data can be prepared into Data Catalog.' : 'This source is visible for discovery, but historical preparation is not connected yet.';
    if ($('reqInstrumentResults')) $('reqInstrumentResults').innerHTML = `<span class="source-capability-note">${esc(note)}</span>`;
    if ($('reqSourceSupports') && !suggestion) $('reqSourceSupports').textContent = note;
  };
  syncRequirementMarkets(spec.scope?.market || 'SPOT');
  $('reqProvider').addEventListener('change', () => syncRequirementMarkets());
  $('reqMarket').addEventListener('change', () => syncRequirementMarkets($('reqMarket').value));
  document.querySelector('[data-action="search-requirement-instruments"]')?.addEventListener('click', async event => {
    event.stopPropagation();
    try {
      const query = $('reqInstrumentSearch').value.trim();
      const provider = providerChoices.find(item => item.id === $('reqProvider').value);
      const market = arr(provider?.markets).find(item => item.id === $('reqMarket').value);
      const params = new URLSearchParams({q: query, limit: '20', provider: $('reqProvider').value, market: $('reqMarket').value, category: market?.search_category || ''});
      const response = await fetch(`/api/research/instruments/search?${params}`); const body = await response.json();
      if (!response.ok || !body.ok) throw new Error(body.error || 'Instrument search failed.');
      $('reqInstrumentResults').innerHTML = arr(body.data).map(item => `<button type="button" data-instrument-id="${esc(item.instrument_id)}"><strong>${esc(item.display_symbol || item.symbol)}</strong><small>${esc(item.status || '')} · ${esc(item.venue || provider?.label || '')}</small></button>`).join('') || `<span>${esc(body.meta?.message || 'No matching instruments.')}</span>`;
    } catch (error) { notify(error.message, true); }
  });
  $('reqInstrumentResults')?.addEventListener('click', event => {
    const button = event.target.closest('[data-instrument-id]'); if (!button) return;
    event.stopPropagation();
    const values = new Set($('reqInstruments').value.split(/[\s,;]+/).filter(Boolean)); values.add(button.dataset.instrumentId); $('reqInstruments').value = [...values].join(', ');
  });
  const saveRequirement = async ({prepare}) => {
    try {
      const mode = state.requirementEditor.mode;
      const finalSpec = mode === 'script' ? await api('/api/research/requirements/script/parse', {method: 'POST', body: JSON.stringify({script: $('reqScript').value})}) : requirementSpecFromForm();
      if (target === 'library') {
        const url = baseAsset ? `/api/research/library/requirements/${encodeURIComponent(baseAsset.library_asset_id)}${saveAs ? '/save-as' : ''}` : '/api/research/library/requirements';
        await api(url, {method: baseAsset && !saveAs ? 'PATCH' : 'POST', body: JSON.stringify({spec: finalSpec})});
        closeDialog(); await loadBase(); switchLibraryTab('requirements'); notify(saveAs ? 'New Requirement saved to Library.' : 'Requirement saved to Library.');
      } else {
        const baseUrl = `/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/items`;
        const url = item ? `${baseUrl}/${encodeURIComponent(item.ref_id)}${saveAs ? '/save-as' : ''}` : baseUrl;
        await api(url, {method: item && !saveAs ? 'PATCH' : 'POST', body: JSON.stringify({spec: finalSpec})});
        closeDialog(); await loadResearch(state.projectId); switchResearchTab('data');
        if (prepare) {
          await checkData();
          const requirementSetId = state.dataStatus?.requirement_set_id;
          if (requirementSetId) {
            await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/resolved-plans`, {
              method: 'POST',
              body: JSON.stringify({
                logical_name: `${finalSpec.name} Data Plan`,
                requirement_set_id: requirementSetId,
                route: {gateway: finalSpec.scope.gateway, provider: finalSpec.scope.provider, market: finalSpec.scope.market},
                source_policy: {mode: finalSpec.advanced.provider_policy, providers: [finalSpec.scope.provider]},
                canonical: {universe_id: finalSpec.target?.universe_id || '', adjustment: finalSpec.advanced.adjustment},
                estimates: {resolved_datasets: arr(state.dataStatus?.rows).length},
              }),
            });
          }
          notify(item ? 'Data Requirements updated. Backend maintenance will keep them current.' : 'Data Requirement created. Backend maintenance will prepare it automatically.');
        } else {
          notify(item ? 'Changes saved.' : 'Requirement draft saved.');
        }
      }
    } catch (error) { notify(error.message, true); }
  };
  $('requirementForm').addEventListener('submit', async event => {
    event.preventDefault();
    await saveRequirement({prepare: target !== 'library'});
  });
  $('saveRequirementDraft')?.addEventListener('click', () => saveRequirement({prepare: false}));
}

function dataDialog(baseRequirements = null) {
  const current = baseRequirements?.context || latestRequirement()?.context || {};
  const snapshot = currentSnapshot();
  if (!snapshot) { notify('Choose a Universe first.', true); switchResearchTab('universe'); return; }
  const existingFields = [...new Set(arr(baseRequirements?.requirements || latestRequirement()?.requirements).flatMap(item => arr(item.fields)))];
  const defaultStart = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10);
  openDialog('Data', 'RESEARCH · DATA', `<form id="dataForm" class="form-stack"><section class="form-section"><h3>Data</h3><div class="form-grid"><label>Provider<input value="Binance" readonly></label><label>Frequency<select id="dataFrequency">${['1m','5m','15m','1h','4h','1d'].map(item => `<option ${item === (current.frequency || researchRefs('FACTOR')[0]?.spec?.frequency || '1h') ? 'selected' : ''}>${item}</option>`).join('')}</select></label><label>Start Date<input id="dataStart" type="date" value="${esc(localDateValue(current.history_start) || defaultStart)}" required></label><label>End Date<input id="dataEnd" type="date" value="${esc(localDateValue(current.history_end))}"></label></div><label>Additional Fields<input id="dataFields" value="${esc(existingFields.join(', '))}" placeholder="close, volume"></label><p class="form-help">Fields required by the current Factor are added automatically.</p></section><div class="form-actions"><button type="submit" class="primary">Save Data</button></div></form>`);
  $('dataForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const fields = $('dataFields').value.split(/[\s,]+/).filter(Boolean);
      const factorFields = new Set(researchRefs('FACTOR').map(item => item.spec?.input_field).filter(Boolean));
      const manual = fields.filter(field => !factorFields.has(field));
      const context = {universe_snapshot_id: snapshot.universe_snapshot_id, instrument_ids: snapshot.actual_instrument_ids, data_type: 'bars', frequency: $('dataFrequency').value, history_start: new Date(`${$('dataStart').value}T00:00:00Z`).toISOString(), history_end: $('dataEnd').value ? new Date(`${$('dataEnd').value}T23:59:59Z`).toISOString() : null, adjustment: 'NONE', time_semantics: 'BAR_END_AVAILABLE_TIME', point_in_time_policy: 'AS_OF', quality_policy: 'STRICT', source_policy: 'FIXED'};
      await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirement-sets`, {method: 'POST', body: JSON.stringify({context, factor_specs: researchRefs('FACTOR').map(item => item.spec), manual_requirements: manual.length ? [{id: 'user_added', fields: manual}] : [], evaluation_requirements: [], backtest_requirements: [], universe_requirements: []})});
      closeDialog(); await loadResearch(state.projectId); switchResearchTab('data'); notify('Requirements saved.');
    } catch (error) { notify(error.message, true); }
  });
}

async function checkData() {
  if (!state.projectId || !latestRequirement()) return;
  try {
    state.dataStatus = await fetchRequirementDataStatus();
    state.requirementRefreshError = '';
    if (state.surface === 'research-detail' && state.researchTab === 'data') renderResearchData();
  } catch (error) {
    state.requirementRefreshError = error.message;
    if (state.surface === 'research-detail' && state.researchTab === 'data') renderResearchData();
    throw error;
  }
}

async function fetchRequirementDataStatus(requirementSetId = '') {
  const params = requirementSetId
    ? `?requirement_set_id=${encodeURIComponent(requirementSetId)}`
    : '';
  return api(`/api/research/projects/${encodeURIComponent(state.projectId)}/data-status${params}`);
}

async function autoPrepareRequirements({
  force = false,
  throwOnError = false,
  dataStatus = null,
  requirementSetId = '',
  onStatus = null,
} = {}) {
  const scopedSetId = requirementSetId || dataStatus?.requirement_set_id || latestRequirement()?.requirement_set_id || '';
  if (state.requirementPrepareBusy || !state.projectId || !scopedSetId) return dataStatus;
  state.requirementPrepareBusy = true;
  let workingStatus = dataStatus;
  try {
    if (!workingStatus) {
      workingStatus = requirementSetId
        ? await fetchRequirementDataStatus(scopedSetId)
        : state.dataStatus || await fetchRequirementDataStatus();
    }
    const missing = arr(workingStatus?.rows).filter(item => {
      const completedTaskNeedsIncrement = item.preparation?.status === 'CHECKING'
        && item.raw_status !== 'READY'
        && item.required_range?.end === 'LATEST_AVAILABLE'
        && String(item.data_type || '').toUpperCase() === 'BARS';
      const autoReviewKey = `${state.projectId}:${item.instrument_id}:${item.frequency}`;
      const automaticRetry = item.preparation?.auto_review?.can_retry === true
        && !state.autoReviewedDataKeys.has(autoReviewKey);
      return item.can_prepare && (force || !item.preparation || completedTaskNeedsIncrement || automaticRetry);
    });
    const exhaustedGrant = missing.some(item =>
      String(item.preparation?.message || '').includes('approval budget exceeded')
    );
    if (missing.length && (
      exhaustedGrant
      || !activeGrantForRows(missing, scopedSetId)
    )) {
      await ensurePreparationScope(missing, scopedSetId, {
        forceNew: exhaustedGrant,
      });
    }
    const standardBars = missing.filter(item => String(item.data_type || '').toUpperCase() === 'BARS');
    const binanceRows = standardBars.filter(item => item.instrument_id.toLowerCase().startsWith('crypto_spot:binance:'));
    const polymarketRows = missing.filter(item => String(item.data_type || '').toUpperCase() === 'PRICE_HISTORY' && item.instrument_id.toLowerCase().startsWith('polymarket_binary:polymarket:'));
    const openbbRows = standardBars.filter(item => item.instrument_id.toLowerCase().startsWith('equity:'));
    const tasks = [];
    tasks.push(...binanceRows.flatMap(item => {
      const grant = activeGrantFor(item, scopedSetId);
      const autoReviewKey = `${state.projectId}:${item.instrument_id}:${item.frequency}`;
      const automaticRetry = item.preparation?.auto_review?.can_retry === true
        && !state.autoReviewedDataKeys.has(autoReviewKey);
      const retryTaskId = automaticRetry ? item.preparation?.task_id : '';
      return grant ? [api(`/api/agent/research/projects/${encodeURIComponent(state.projectId)}/backfill-tasks`, {method: 'POST', body: JSON.stringify({
        grant_id: grant.grant_id,
        workflow_run_id: retryTaskId ? `auto-review:${retryTaskId}` : force ? `auto-backfill:${state.projectId}:${crypto.randomUUID()}` : undefined,
        idempotency_key: retryTaskId ? `auto-review:${retryTaskId}` : force ? `retry:${crypto.randomUUID()}` : undefined,
        logical_key: `${item.instrument_id.split(':').pop()}:${item.frequency}:${item.library_asset_ids?.[0] || item.requirement_id}`,
        library_asset_id: item.library_asset_ids?.[0] || '',
        symbol: item.instrument_id.split(':').pop(), instrument_id: item.instrument_id,
        interval: item.frequency, start_time: item.required_range?.start,
        end_time: item.required_range?.resolved_end || (item.required_range?.end === 'LATEST_AVAILABLE' ? new Date().toISOString() : item.required_range?.end),
        requirement_id: item.requirement_id, page_limit: 1000, max_pages_per_attempt: 500,
        budget: {download_bytes: 20000000, runtime_seconds: 300},
      })}).then(result => {
        if (automaticRetry) state.autoReviewedDataKeys.add(autoReviewKey);
        return result;
      })] : [];
    }));
    tasks.push(...openbbRows.flatMap(item => {
      const grant = activeGrantFor(item, scopedSetId);
      const parts = String(item.instrument_id || '').split(':');
      return grant ? [api(`/api/agent/research/projects/${encodeURIComponent(state.projectId)}/openbb-export-tasks`, {method: 'POST', body: JSON.stringify({
        grant_id: grant.grant_id,
        workflow_run_id: force ? `auto-openbb:${state.projectId}:${crypto.randomUUID()}` : undefined,
        idempotency_key: force ? `retry:${crypto.randomUUID()}` : undefined,
        logical_key: `${String(item.provider || 'yfinance').toLowerCase()}:${parts[1]}:${parts.at(-1)}:${item.frequency}:${item.library_asset_ids?.[0] || item.requirement_id}`,
        library_asset_id: item.library_asset_ids?.[0] || '',
        provider: String(item.provider || 'yfinance').toLowerCase(),
        venue: parts[1], symbol: parts.at(-1), instrument_id: item.instrument_id,
        interval: item.frequency, adjustment: item.adjustment || 'NONE',
        start_time: item.required_range?.start,
        end_time: item.required_range?.resolved_end || (item.required_range?.end === 'LATEST_AVAILABLE' ? new Date().toISOString() : item.required_range?.end),
        latest_available: item.required_range?.end === 'LATEST_AVAILABLE',
        requirement_id: item.requirement_id,
        budget: {download_bytes: 20000000, runtime_seconds: 300},
      })})] : [];
    }));
    tasks.push(...polymarketRows.flatMap(item => {
      const grant = activeGrantFor(item, scopedSetId);
      return grant ? [api(`/api/agent/research/projects/${encodeURIComponent(state.projectId)}/polymarket-export-tasks`, {method: 'POST', body: JSON.stringify({
        grant_id: grant.grant_id,
        workflow_run_id: force ? `auto-polymarket:${state.projectId}:${crypto.randomUUID()}` : undefined,
        idempotency_key: force ? `retry:${crypto.randomUUID()}` : undefined,
        logical_key: `polymarket:${item.instrument_id.split(':').pop()}:${item.frequency}:${item.library_asset_ids?.[0] || item.requirement_id}`,
        library_asset_id: item.library_asset_ids?.[0] || '',
        instrument_id: item.instrument_id,
        interval: item.frequency,
        start_time: item.required_range?.start,
        end_time: item.required_range?.resolved_end || (item.required_range?.end === 'LATEST_AVAILABLE' ? new Date().toISOString() : item.required_range?.end),
        latest_available: item.required_range?.end === 'LATEST_AVAILABLE',
        requirement_id: item.requirement_id,
        budget: {download_bytes: 20000000, runtime_seconds: 300},
      })})] : [];
    }));
    if (tasks.length) {
      await Promise.all(tasks);
      await api('/api/research/data/backfill/binance/worker/start', {method: 'POST', body: '{}'}).catch(() => null);
      await api('/api/research/data/providers/openbb/worker/start', {method: 'POST', body: '{}'}).catch(() => null);
      await api('/api/research/data/providers/polymarket/worker/start', {method: 'POST', body: '{}'}).catch(() => null);
      workingStatus = await fetchRequirementDataStatus(scopedSetId);
      if (onStatus) onStatus(workingStatus);
      else if (!requirementSetId) state.dataStatus = workingStatus;
    } else if (arr(workingStatus?.rows).some(item => ['QUEUED', 'PREPARING'].includes(item.status))) {
      await api('/api/research/data/backfill/binance/worker/start', {method: 'POST', body: '{}'}).catch(() => null);
      await api('/api/research/data/providers/openbb/worker/start', {method: 'POST', body: '{}'}).catch(() => null);
      await api('/api/research/data/providers/polymarket/worker/start', {method: 'POST', body: '{}'}).catch(() => null);
    }
    return workingStatus;
  } catch (error) {
    state.requirementRefreshError = error.message;
    if (state.surface === 'research-detail' && state.researchTab === 'data') renderResearchData();
    if (throwOnError) throw error;
    return workingStatus;
  } finally {
    state.requirementPrepareBusy = false;
    if (!requirementSetId) scheduleRequirementRefresh();
  }
}

async function fillMissing() {
  await prepareRequirementData('', {force: true});
}

function scheduleRequirementRefresh(delay) {
  clearTimeout(state.requirementRefreshTimer);
  const researchActive = state.surface === 'research-detail' && state.researchTab === 'data';
  const libraryActive = state.surface === 'library' && state.libraryTab === 'requirements';
  if (!researchActive && !libraryActive) return;
  const statuses = researchActive
    ? arr(state.dataStatus?.rows).map(item => item.status)
    : state.library.filter(item => item.component_type === 'REQUIREMENTS').map(item => item.data_status?.status);
  const live = statuses.some(status => ['CHECKING', 'QUEUED', 'PREPARING'].includes(status));
  state.requirementRefreshTimer = window.setTimeout(refreshRequirementStatus, delay ?? (live ? 2000 : 15000));
}

async function refreshRequirementStatus() {
  try {
    if (state.surface === 'research-detail' && state.researchTab === 'data') {
      await checkData();
    } else if (state.surface === 'library' && state.libraryTab === 'requirements') {
      const library = await api('/api/research/library');
      const other = state.library.filter(item => item.component_type !== 'REQUIREMENTS');
      state.library = [...other, ...library.filter(item => item.component_type === 'REQUIREMENTS')];
      state.requirementRefreshError = '';
      renderRequirementLibrary();
      scheduleRequirementRefresh();
    }
  } catch (error) {
    state.requirementRefreshError = error.message;
    if (state.surface === 'research-detail' && state.researchTab === 'data') renderResearchData();
    scheduleRequirementRefresh(15000);
  }
}

function scopedRequirementRows(requirementRefId = '') {
  const item = requirementRefId
    ? state.requirementItems.find(candidate => candidate.ref_id === requirementRefId)
    : null;
  return item ? requirementRowsForItem(item) : arr(state.dataStatus?.rows);
}

async function ensurePreparationScope(
  rows,
  requirementSetId = state.dataStatus?.requirement_set_id || latestRequirement()?.requirement_set_id || '',
  {forceNew = false} = {},
) {
  const scopedRows = arr(rows).filter(row => row?.can_prepare && row?.instrument_id);
  const existing = activeGrantForRows(scopedRows, requirementSetId);
  if (existing && !forceNew) return existing;
  const snapshot = currentSnapshot();
  if (!scopedRows.length) throw new Error('No requestable data is available for this Requirement.');
  const allowedProviders = [...new Set(scopedRows.map(row => String(row.provider || '').toUpperCase()).filter(Boolean))];
  const allowedIntervals = [...new Set(scopedRows.map(row => String(row.frequency || '').toLowerCase()).filter(Boolean))];
  const scopedInstruments = [...new Set(scopedRows.map(row => row.instrument_id).filter(Boolean))];
  const starts = scopedRows.map(row => row.required_range?.start).filter(Boolean).sort();
  const fixedEnds = scopedRows
    .filter(row => row.required_range?.end !== 'LATEST_AVAILABLE')
    .map(row => row.required_range?.resolved_end || row.required_range?.end)
    .filter(Boolean)
    .sort();
  const expiresAt = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
  const timeStart = starts[0] || new Date().toISOString();
  const timeEnd = scopedRows.some(row => row.required_range?.end === 'LATEST_AVAILABLE')
    ? expiresAt
    : fixedEnds.at(-1) || expiresAt;
  const created = await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/run-grants`, {
    method: 'POST',
    body: JSON.stringify({
      objective: `Prepare data for ${state.project?.title || 'Research'}`,
      autonomy_level: 'AUTONOMOUS',
      allowed_operations: ['COVERAGE_CHECK', 'BACKFILL_CREATE', 'PREVIEW_CREATE', 'RUN_CREATE', 'RUN_EXECUTE'],
      allowed_providers: allowedProviders,
      allowed_intervals: allowedIntervals,
      allowed_instrument_ids: scopedInstruments,
      time_start: timeStart,
      time_end: timeEnd,
      allow_project_pin: false,
      allowed_run_types: ['FACTOR_EVALUATION', 'ALPHA_EVALUATION', 'RESEARCH_BACKTEST'],
      requirement_set_id: requirementSetId,
      universe_snapshot_id: snapshot?.universe_snapshot_id || '',
      source_policy: {mode: 'FIXED'},
      budgets: {
        max_backtest_runs: Math.max(10, scopedRows.length * 4),
        max_download_bytes: 500 * 1024 * 1024,
        max_runtime_seconds: 7200,
      },
      expires_at: expiresAt,
    }),
  });
  if (created?.grant_id) {
    state.grants = [created, ...state.grants.filter(item => item.grant_id !== created.grant_id)];
  } else {
    state.grants = await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/grants`);
  }
  return activeGrantForRows(scopedRows, requirementSetId);
}

function factorPreviewPreparationFailure(rows) {
  const failed = arr(rows).find(row => ['FAILED', 'UNAVAILABLE'].includes(row.status));
  if (failed) {
    return failed.reason
      || failed.preparation?.message
      || `Data preparation failed for ${failed.instrument_label || failed.instrument_id}.`;
  }
  const blocked = arr(rows).find(row => row.status === 'NEEDS_ATTENTION' && !row.can_prepare);
  return blocked
    ? blocked.reason || `Data preparation needs attention for ${blocked.instrument_label || blocked.instrument_id}.`
    : '';
}

async function prepareFactorPreviewRequirements(
  compiledRequirement,
  {
    timeoutMs = 5 * 60 * 1000,
    pollMs = 2000,
    renderStatus = null,
    statusElementId = 'factorSaveHint',
  } = {},
) {
  const deadline = Date.now() + timeoutMs;
  const requirementSetId = compiledRequirement.reference?.requirement_set_id
    || compiledRequirement.requirement_set?.requirement_set_id
    || '';
  let previewStatus = compiledRequirement.data_status;
  let remaining = arr(previewStatus?.rows).filter(row => row.status !== 'READY');
  while (remaining.length) {
    const failure = factorPreviewPreparationFailure(remaining);
    if (failure) throw new Error(failure);

    previewStatus = await fetchRequirementDataStatus(requirementSetId);
    remaining = arr(previewStatus?.rows).filter(row => row.status !== 'READY');
    if (renderStatus) renderStatus({...compiledRequirement, data_status: previewStatus});
    if (!remaining.length) return previewStatus;

    const refreshedFailure = factorPreviewPreparationFailure(remaining);
    if (refreshedFailure) throw new Error(refreshedFailure);

    const active = remaining.filter(row => {
      const preparationStatus = String(row.preparation?.status || '').toUpperCase();
      return ['QUEUED', 'PREPARING', 'CHECKING'].includes(row.status)
        || ['PENDING', 'READY', 'RUNNING', 'QUEUED', 'PREPARING', 'CHECKING'].includes(preparationStatus);
    });
    if (!active.length) {
      if ($(statusElementId)) $(statusElementId).textContent = `Backend maintenance is scheduling ${remaining.length} Instrument input(s).`;
    }

    const progress = active
      .map(row => row.preparation?.percent)
      .filter(value => value != null);
    const progressLabel = progress.length
      ? ` (${Math.min(...progress)}% minimum progress)`
      : '';
    if ($(statusElementId)) $(statusElementId).textContent = `Preparing exact data for ${remaining.length} Instrument input(s)${progressLabel}. Preview will continue automatically.`;
    if (Date.now() >= deadline) {
      throw new Error(`Data preparation is still running for ${remaining.length} Instrument input(s). It will continue in the background; run Preview again after the Data status becomes Ready.`);
    }
    await new Promise(resolve => window.setTimeout(resolve, pollMs));
  }
  return previewStatus;
}

async function prepareRequirementData(requirementRefId = '', {force = true} = {}) {
  if (!state.dataStatus) await checkData();
  const rows = scopedRequirementRows(requirementRefId);
  const requestable = rows.filter(row => row.can_prepare && row.status !== 'READY');
  if (!requestable.length) {
    notify(rows.every(row => row.status === 'READY') ? 'Data is already ready.' : 'No requestable data is available for this Requirement.', !rows.every(row => row.status === 'READY'));
    return;
  }
  await ensurePreparationScope(requestable);
  await autoPrepareRequirements({force});
  switchResearchTab('data');
  notify('Data preparation started.');
}

async function testDialog(type) {
  const isFactor = type === 'factor';
  const isBacktest = type === 'backtest';
  const runType = isFactor ? 'FACTOR_EVALUATION' : isBacktest ? 'RESEARCH_BACKTEST' : 'ALPHA_EVALUATION';
  const refs = researchRefs(isFactor ? 'FACTOR' : 'ALPHA');
  if (!refs.length || refs.some(item => item.state !== 'VALIDATED')) { notify(`Validate the ${isFactor ? 'Factor' : 'Alpha'} first.`, true); switchResearchTab(isFactor ? 'factor' : 'alpha'); return; }
  if (!latestRequirement()) { notify('Configure Requirements first.', true); switchResearchTab('data'); return; }
  if (!state.dataStatus) await checkData();
  const scopedRows = arr(state.dataStatus?.rows).filter(row => row.can_prepare);
  const existingGrant = activeGrantForRows(scopedRows);
  const allowedRunTypes = arr(existingGrant?.scope?.allowed_run_types).map(value => String(value).toUpperCase());
  if (!existingGrant || (allowedRunTypes.length && !allowedRunTypes.includes(runType))) {
    await ensurePreparationScope(
      scopedRows,
      state.dataStatus?.requirement_set_id || latestRequirement()?.requirement_set_id || '',
      {forceNew: Boolean(existingGrant)},
    );
  }
  const evaluationSetup = !isBacktest ? `<section class="form-section"><h3>Predictive Evaluation</h3><div class="form-grid"><label>Prediction Horizons<input id="testHorizons" value="1, 6, 24"></label><label>Quantile Groups<input id="testQuantiles" type="number" min="2" value="5"></label><label>Minimum Instruments<input id="testCross" type="number" min="1" value="${Math.max(1, arr(currentSnapshot()?.actual_instrument_ids).length)}"></label></div></section>` : '';
  const backtestSetup = isBacktest ? `<section class="form-section"><h3>Portfolio Rules</h3><div class="form-grid"><label>Top N<input id="testTopN" type="number" min="1" value="1"></label><label>Rebalance<select id="testRebalance"><option value="EVERY_SIGNAL">Every Signal</option><option value="DAILY">Daily</option></select></label><label>Max Position Weight<input id="testMaxWeight" type="number" min="0.01" max="1" step="0.01" value="1"></label><label>Cash Buffer<input id="testCashBuffer" type="number" min="0" max="0.99" step="0.01" value="0"></label></div></section><section class="form-section"><h3>Execution Assumptions</h3><div class="form-grid"><label>Initial Cash<input id="testInitialCash" type="number" min="1" value="10000"></label><label>Fee (bps)<input id="testFee" type="number" min="0" step="0.1" value="2"></label><label>Slippage (bps)<input id="testSlippage" type="number" min="0" step="0.1" value="10"></label><label>Fill Timing<input value="Next bar open" disabled></label></div></section><section class="form-section"><h3>Benchmark</h3><div class="form-grid"><label>Benchmark<select id="testBenchmark"><option value="EQUAL_WEIGHT_UNIVERSE">Equal-weight Universe</option><option value="BUY_AND_HOLD">Buy and Hold</option></select></label><label>Rebalance<input value="Monthly" disabled></label></div><p class="muted">Benchmark identity is frozen with the Run. Relative performance remains unavailable until its series is materialized.</p></section>` : '';
  openDialog(testLabel(runType), 'RUN SETUP', `<form id="testForm" class="form-stack">${evaluationSetup}${backtestSetup}<div id="testCheckResult"></div><div class="form-actions"><button type="submit" class="primary">Check Setup</button></div></form>`);
  $('testForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const set = latestRequirement(); const snapshot = currentSnapshot(); const grant = activeGrantForRows(scopedRows) || activeGrant();
      const evaluationSpec = isBacktest ? {} : {horizons: $('testHorizons').value.split(/[\s,]+/).filter(Boolean).map(Number), quantile_count: Number($('testQuantiles').value), minimum_cross_section_size: Number($('testCross').value), ic_methods: ['PEARSON', 'SPEARMAN'], fee_bps: 0, slippage_bps: 0, return_definition: 'NEXT_BAR_OPEN_TO_HORIZON_CLOSE'};
      const portfolioSpec = isBacktest ? {selection_method: 'TOP_N', top_n: Number($('testTopN').value), weighting_method: 'EQUAL_WEIGHT', direction: 'LONG_ONLY', rebalance_frequency: $('testRebalance').value, max_position_weight: Number($('testMaxWeight').value), cash_buffer: Number($('testCashBuffer').value)} : {};
      const executionSpec = isBacktest ? {initial_cash: Number($('testInitialCash').value), signal_generation: 'BAR_CLOSE', order_submission: 'NEXT_BAR_OPEN', fill_price_rule: 'NEXT_OPEN_PLUS_SLIPPAGE', cash_constraint: 'STRICT', fee_model: 'FIXED_BPS', slippage_model: 'FIXED_BPS', portfolio_input: 'TARGET_WEIGHT', allow_short: false, allow_leverage: false, fee_bps: Number($('testFee').value), slippage_bps: Number($('testSlippage').value), missing_price_policy: 'FAIL_RUN', quantity_rounding: 'FRACTIONAL', minimum_notional_policy: 'IGNORE', target_equity_reference: 'EXECUTION_OPEN_PRE_TRADE', sell_before_buy: true, random_seed: 0} : {};
      const benchmarkSpec = isBacktest ? {type: $('testBenchmark').value, rebalance_frequency: 'MONTHLY', cost_model: 'SAME_AS_STRATEGY'} : {};
      state.checkedPreview = await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/run-input-previews`, {method: 'POST', body: JSON.stringify({run_type: runType, requirement_set_id: set.requirement_set_id, universe_snapshot_id: snapshot.universe_snapshot_id, grant_id: grant.grant_id, source_selection_policy: {version: 'source_selection_policy.v2', mode: 'AUTO'}, evaluation_spec: evaluationSpec, portfolio_spec: portfolioSpec, execution_spec: executionSpec, benchmark_spec: benchmarkSpec, budget: {runs: 1, download_bytes: 0, runtime_seconds: 120}, verify_physical: true})});
      const ready = state.checkedPreview.readiness?.overall?.status === 'READY';
      const blocked = Object.values(state.checkedPreview.readiness?.dimensions || {}).flatMap(item => arr(item.checks)).filter(item => item.status === 'BLOCKED');
      $('testCheckResult').innerHTML = ready ? `<div class="next-step"><div><span>SETUP</span><strong>Ready to start</strong></div><button type="button" class="primary" data-action="start-test">Start Run</button></div>` : `<div class="card-stack">${blocked.map(item => `<div class="strategy-stage"><strong>${esc(item.object_ref || item.dimension)}</strong><small class="muted">${esc(item.message)}</small></div>`).join('') || '<p class="muted">Setup needs attention.</p>'}</div>`;
    } catch (error) { notify(error.message, true); }
  });
}

async function startCheckedTest() {
  if (!state.checkedPreview) return;
  const run = await api('/api/research/runs', {method: 'POST', body: JSON.stringify({preview_id: state.checkedPreview.preview_id, preview_fingerprint: state.checkedPreview.preview_fingerprint, idempotency_key: `ui:${state.checkedPreview.preview_fingerprint}:${crypto.randomUUID()}`})});
  if (run.status === 'QUEUED') await api('/api/research/run-worker/run-once', {method: 'POST', body: JSON.stringify({worker_id: 'local-ui-research-worker', lease_seconds: 300})});
  closeDialog(); await loadBase(); if (state.surface === 'research-detail') switchResearchTab('runs'); notify('Research run completed.');
}

async function validateDefinition(id) {
  const validated = await api(`/api/research/definitions/${encodeURIComponent(id)}/validate`, {method: 'POST', body: '{}'});
  await setResearchDefinition(validated, 'PINNED');
  await loadResearch(state.projectId);
  switchResearchTab(validated.definition_type === 'FACTOR' ? 'factor' : 'alpha');
  notify(`${validated.definition_type === 'FACTOR' ? 'Factor' : 'Alpha'} validated.`);
}

async function publishDefinition(id) {
  const item = state.definitions.find(definition => definition.definition_id === id);
  await api(`/api/research/definitions/${encodeURIComponent(id)}/publish`, {method: 'POST', body: JSON.stringify({project_id: state.projectId})});
  await loadBase();
  if (state.surface === 'research-detail') switchResearchTab(item?.definition_type === 'ALPHA' ? 'alpha' : 'factor');
  notify('Component published to Library.');
}

async function publishUniverse(id) {
  await api(`/api/research/universes/${encodeURIComponent(id)}/publish`, {method: 'POST', body: JSON.stringify({project_id: state.projectId})});
  await loadBase(); if (state.surface === 'research-detail') switchResearchTab('universe'); notify('Universe published to Library.');
}

function chooseResearchDialog(title, action, kind, libraryAssetId) {
  openDialog(title, 'SELECT RESEARCH', `<div class="choice-list">${state.projects.map(project => `<div class="choice-card"><div><strong>${esc(project.title)}</strong><small>${esc(project.objective)}</small></div><button type="button" class="primary" data-action="${esc(action)}" data-kind="${esc(kind)}" data-id="${esc(libraryAssetId)}" data-project-id="${esc(project.project_id)}">Select</button></div>`).join('') || '<p class="muted">Create a Research first.</p>'}</div>`);
}

async function applyLibraryAsset(libraryAssetId, kind, projectId) {
  const asset = state.library.find(item => item.library_asset_id === libraryAssetId);
  if (!asset) throw new Error('Library asset not found.');
  if (kind === 'UNIVERSE') {
    await useUniverse(asset.content.snapshot.universe_snapshot_id, projectId, asset.library_asset_id);
  } else if (kind === 'REQUIREMENTS') {
    await api(`/api/research/projects/${encodeURIComponent(projectId)}/requirements/library-items`, {method: 'POST', body: JSON.stringify({library_asset_id: asset.library_asset_id})});
    closeDialog();
    if (projectId === state.projectId && state.surface === 'research-detail') {
      await loadResearch(projectId); switchResearchTab('data');
    }
    notify('Requirements added to Research.');
  } else {
    await useDefinition(asset.source_object_id, projectId, asset.library_asset_id);
  }
}

function startLibraryVersion(libraryAssetId, projectId) {
  window.location.assign(`/research/${encodeURIComponent(projectId)}?clone=${encodeURIComponent(libraryAssetId)}`);
}

function openCloneEditor(asset) {
  if (!asset) return;
  if (asset.component_type === 'UNIVERSE') {
    universeDialog(state.universes.find(item => item.universe_definition_id === asset.source_object_id) || asset.content.definition);
  } else if (asset.component_type === 'FACTOR') {
    factorDraftDialog(state.definitions.find(item => item.definition_id === asset.source_object_id) || asset.content);
  } else if (asset.component_type === 'ALPHA') {
    alphaDialog(state.definitions.find(item => item.definition_id === asset.source_object_id) || asset.content);
  } else if (asset.component_type === 'REQUIREMENTS') {
    dataDialog(asset.content);
  }
}

function openRequestedClone() {
  const libraryAssetId = new URLSearchParams(window.location.search).get('clone');
  if (!libraryAssetId) return;
  window.history.replaceState({}, '', `/research/${encodeURIComponent(state.projectId)}`);
  openCloneEditor(state.library.find(item => item.library_asset_id === libraryAssetId));
}

function addLibraryRequirementsDialogLegacy() {
  const items = latestByName(state.library.filter(item => item.component_type === 'REQUIREMENTS'));
  openDialog('Add from Library', 'LIBRARY · REQUIREMENTS', `<div class="choice-list">${items.map(asset => `<div class="choice-card"><div><strong>${esc(asset.name)}</strong><small>Library v${esc(asset.version)} · ${esc(asset.content.context?.frequency || '-')}</small></div><button type="button" class="primary" data-action="confirm-library-use" data-kind="REQUIREMENTS" data-id="${esc(asset.library_asset_id)}" data-project-id="${esc(state.projectId)}">Add to Research</button></div>`).join('') || '<p class="muted">Library has no published Requirements.</p>'}</div>`);
}

function addLibraryRequirementsDialog() {
  const items = latestByName(state.library.filter(item => item.component_type === 'REQUIREMENTS'));
  openDialog('Add from Library', 'LIBRARY · REQUIREMENTS', `<div class="choice-list">${items.map(asset => `<div class="choice-card"><div><strong>${esc(asset.name)}</strong><small>Library v${esc(asset.version)} · ${esc(asset.spec?.data?.frequency || '-')} · ${esc(arr(asset.spec?.data?.fields).join(', '))}</small></div><button type="button" class="primary" data-action="confirm-library-use" data-kind="REQUIREMENTS" data-id="${esc(asset.library_asset_id)}" data-project-id="${esc(state.projectId)}">Add to Research</button></div>`).join('') || '<p class="muted">Library has no published Requirements.</p>'}</div>`);
}

function requirementChoiceCard(asset, input) {
  const spec = asset.spec || {};
  const scope = spec.scope || {}; const data = spec.data || {};
  return `<label class="requirement-choice"><input ${input} value="${esc(asset.library_asset_id)}"><span><strong>${esc(asset.name)}</strong><small>${esc(scope.provider || '-')} ${esc(scope.market || '')} · ${esc(arr(scope.instruments?.include).map(requirementInstrumentLabel).join(', ') || 'Rule based')}</small><small>${esc(data.frequency || '-')} ${esc(data.dataset_type || 'Data')} · ${esc(arr(data.fields).join(' / ') || '-')}</small></span>${statusChip(friendlyStatus(asset.data_status?.status || 'PENDING'))}</label>`;
}

function addLibraryRequirementsDialog() {
  const used = new Set(state.requirementItems.map(item => item.library_asset_id));
  const items = state.library.filter(item => item.component_type === 'REQUIREMENTS' && !used.has(item.library_asset_id));
  openDialog('Add from Library', 'REQUIREMENTS', `<form id="addRequirementsForm" class="form-stack"><label>Search Requirements<input id="addRequirementSearch" placeholder="Name or instrument"></label><div id="addRequirementChoices" class="requirement-choice-list">${items.map(asset => requirementChoiceCard(asset, 'type="checkbox" name="selectedRequirement"')).join('') || '<div class="empty-state"><p>Every Library Requirement is already used by this Research.</p></div>'}</div><div class="form-actions"><button type="button" data-action="close-requirement-editor">Cancel</button><button type="submit" class="primary" ${items.length ? '' : 'disabled'}>Add Selected</button></div></form>`);
  $('addRequirementSearch').addEventListener('input', () => {
    const query = $('addRequirementSearch').value.trim().toLowerCase();
    document.querySelectorAll('.requirement-choice').forEach(card => { card.hidden = Boolean(query && !card.textContent.toLowerCase().includes(query)); });
  });
  $('addRequirementsForm').addEventListener('submit', async event => {
    event.preventDefault();
    const selected = [...document.querySelectorAll('[name="selectedRequirement"]:checked')].map(input => input.value);
    if (!selected.length) { notify('Select at least one Requirement.', true); return; }
    for (const libraryAssetId of selected) {
      await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/library-items`, {method: 'POST', body: JSON.stringify({library_asset_id: libraryAssetId})});
    }
    closeDialog(); await loadResearch(state.projectId); switchResearchTab('data'); await checkData(); notify(`${selected.length} Requirement${selected.length === 1 ? '' : 's'} added.`);
  });
}

function replaceRequirementDialog(item) {
  const used = new Set(state.requirementItems.filter(value => value.ref_id !== item.ref_id).map(value => value.library_asset_id));
  const items = state.library.filter(asset => asset.component_type === 'REQUIREMENTS' && asset.library_asset_id !== item.library_asset_id && !used.has(asset.library_asset_id));
  openDialog('Replace Requirement', 'RESEARCH · DATA', `<form id="replaceRequirementForm" class="form-stack"><p class="dialog-note">Choose the Library Requirement that should replace <strong>${esc(item.name)}</strong>. The original remains in Library.</p><div class="requirement-choice-list">${items.map(asset => requirementChoiceCard(asset, 'type="radio" name="replacementRequirement" required')).join('') || '<div class="empty-state"><p>No other Requirement is available.</p></div>'}</div><div class="form-actions"><button type="button" data-action="close-requirement-editor">Cancel</button><button type="submit" class="primary" ${items.length ? '' : 'disabled'}>Replace</button></div></form>`);
  $('replaceRequirementForm').addEventListener('submit', async event => {
    event.preventDefault();
    const selected = document.querySelector('[name="replacementRequirement"]:checked');
    if (!selected) return;
    await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/items/${encodeURIComponent(item.ref_id)}/replace`, {method: 'POST', body: JSON.stringify({library_asset_id: selected.value})});
    closeDialog(); await loadResearch(state.projectId); switchResearchTab('data'); await checkData(); notify('Requirement replaced.');
  });
}

async function editSharedRequirement(item) {
  const usage = await api(`/api/research/library/${encodeURIComponent(item.library_asset_id)}/usage`);
  const context = state.pendingRequirementContext || {};
  if (usage.research_count <= 1) { await openRequirementEditor({target: 'research', item, ...context}); return; }
  state.sharedEditItem = item;
  openDialog(`Edit ${item.name}`, 'SHARED REQUIREMENT', `<div class="shared-impact"><div class="impact-number">${esc(usage.research_count)}</div><div><h3>Research use this Requirement</h3><p>Updating it changes the shared Requirement for every Research listed below.</p></div></div><div class="usage-list">${arr(usage.research).map(research => `<span>${esc(research.title)}</span>`).join('')}</div><div class="form-actions spread"><button type="button" data-action="close-requirement-editor">Cancel</button><div class="button-row"><button data-action="save-as-current-requirement">Save As for this Research</button><button class="primary" data-action="update-shared-requirement">Update Shared Requirement</button></div></div>`);
}

function showUniverseRequirementChanges(universeId) {
  const universe = state.universeBindings.find(item => item.universe_id === universeId);
  const reconciliation = state.requirementReconciliations[universeId] || {};
  const changes = reconciliation.changes || {added: [], removed: []};
  openDrawer(universe?.name || 'Universe Changes', 'UNIVERSE CHANGES', `<div class="change-columns"><div><strong>Added</strong><div class="member-tags">${arr(changes.added).map(value => `<span>${esc(requirementInstrumentLabel(value))}</span>`).join('') || '<small>None</small>'}</div></div><div><strong>Removed</strong><div class="member-tags">${arr(changes.removed).map(value => `<span>${esc(requirementInstrumentLabel(value))}</span>`).join('') || '<small>None</small>'}</div></div></div><section class="form-section universe-change-summary"><strong>Compatibility</strong><p>${esc(arr(reconciliation.reasons).join(' ') || 'The current data configuration remains compatible.')}</p></section>`);
}

async function publishRequirements() {
  const set = latestRequirement();
  if (!set) return;
  await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/publish`, {method: 'POST', body: JSON.stringify({requirement_set_id: set.requirement_set_id, name: `${state.project.title} Requirements`})});
  await loadBase(); if (state.surface === 'research-detail') switchResearchTab('data'); notify('Requirements published to Library.');
}

async function showLibraryUsage(libraryAssetId) {
  const usage = await api(`/api/research/library/${encodeURIComponent(libraryAssetId)}/usage`);
  openDrawer(usage.library_asset.name, 'LIBRARY USAGE', `<p class="muted">Library v${esc(usage.library_asset.version)} is used by ${usage.research_count} Research.</p><div class="choice-list">${arr(usage.research).map(item => `<a class="choice-card" href="/research/${encodeURIComponent(item.project_id)}"><strong>${esc(item.title)}</strong></a>`).join('') || '<p class="muted">Not used by any Research.</p>'}</div>`);
}

function showLibraryAssetDetails(id) {
  const asset = state.library.find(item => item.library_asset_id === id);
  if (!asset) return;
  openDrawer(asset.name, `LIBRARY · V${asset.version}`, `<p class="muted">Published ${esc(formatDate(asset.published_at))}. This version is immutable.</p><pre class="technical">${esc(json(asset))}</pre>`);
}

async function showLibraryUsage(libraryAssetId) {
  const usage = await api(`/api/research/library/${encodeURIComponent(libraryAssetId)}/usage`);
  openDrawer(usage.library_asset.name, 'REQUIREMENT USAGE', `<p class="muted">Used by ${usage.research_count} Research.</p><div class="choice-list">${arr(usage.research).map(item => `<a class="choice-card" href="/research/${encodeURIComponent(item.project_id)}"><strong>${esc(item.title)}</strong></a>`).join('') || '<p class="muted">Not used by any Research.</p>'}</div>`);
}

function showLibraryAssetDetails(id) {
  const asset = state.library.find(item => item.library_asset_id === id);
  if (!asset) return;
  openDrawer(asset.name, 'LIBRARY · REQUIREMENT', `<div class="fact-grid"><div class="fact-block"><span>Status</span><strong>${esc(friendlyStatus(asset.data_status?.status))}</strong></div><div class="fact-block"><span>Used by</span><strong>${esc(asset.usage_count || 0)} Research</strong></div><div class="fact-block"><span>Updated</span><strong>${esc(formatDate(asset.updated_at))}</strong></div></div><pre class="technical">${esc(json(asset.spec || {}))}</pre>`);
}

function showDefinitionDetails(id) {
  const definition = state.definitions.find(item => item.definition_id === id);
  if (!definition) return;
  openDrawer(definition.name, 'COMPONENT DETAILS', `<div class="fact-grid"><div class="fact-block"><span>Type</span><strong>${esc(definition.definition_type)}</strong></div><div class="fact-block"><span>Status</span><strong>${esc(friendlyStatus(definition.state))}</strong></div><div class="fact-block"><span>Source</span><strong>${esc(definition.owner_project_id ? 'Research' : 'Legacy')}</strong></div></div><pre class="technical">${esc(json(definition))}</pre>`);
}

function showUniverseDetails(id) {
  const universe = state.universes.find(item => item.universe_definition_id === id);
  const snapshots = state.snapshots.filter(item => item.universe_definition_id === id);
  if (!universe) return;
  openDrawer(universe.name, 'UNIVERSE DETAILS', `<div class="fact-grid"><div class="fact-block"><span>Status</span><strong>Validated</strong></div><div class="fact-block"><span>Snapshots</span><strong>${snapshots.length}</strong></div><div class="fact-block"><span>Type</span><strong>${esc(universe.universe_type)}</strong></div></div><pre class="technical">${esc(json({universe, snapshots}))}</pre>`);
}

function runSummaryValues(summary, run) {
  const root = Object.values(summary?.metrics || run?.output?.metrics || {})[0] || {};
  const evaluation = root.evaluation || root;
  const performance = root.performance || {};
  const firstMean = values => {
    if (values === null || values === undefined || values === '') return null;
    if (Number.isFinite(Number(values))) return Number(values);
    const item = Object.values(values || {})[0];
    return item && typeof item === 'object' && item.mean !== null && item.mean !== undefined
      ? item.mean
      : null;
  };
  return {
    evaluation,
    performance,
    ic: firstMean(evaluation.ic),
    rankIc: firstMean(evaluation.rank_ic),
    icir: Object.values(evaluation.rank_ic || {})[0]?.icir,
  };
}

function primaryFactorRunResult(summary) {
  return arr(summary?.factor_run?.results)[0] || null;
}

function factorRunMetric(value, digits = 4) {
  if (value === null || value === undefined || value === '') return '-';
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : '-';
}

function factorRunInlineSection(summary, key) {
  const contract = summary?.factor_run;
  if (!contract) return '';
  const primary = primaryFactorRunResult(summary) || {};
  const factor = primary.factor || {};
  if (key === 'overview') {
    const coverage = primary.coverage || {};
    const diagnostics = contract.diagnostic_summary || {};
    return `<div class="factor-run-boundary"><strong>Factor Run boundary</strong><span>Ends at predictive power and quantile performance. No positions, trades, costs, equity curve, or drawdown.</span></div><div class="metric-grid"><div class="metric-card"><span>Status</span><strong>${esc(friendlyStatus(contract.status))}</strong><small>${esc(contract.schema_version)}</small></div><div class="metric-card factor-run-name"><span>Factor</span><strong>${esc(factor.name || '-')}</strong><small>${esc(factor.version || '')}</small></div><div class="metric-card"><span>Coverage</span><strong>${esc(percent(coverage.overall))}</strong><small>${esc(coverage.valid_rows ?? '-')} / ${esc(coverage.total_rows ?? '-')} valid rows</small></div><div class="metric-card"><span>Diagnostics</span><strong>${esc(Object.values(diagnostics).reduce((sum, value) => sum + Number(value || 0), 0))}</strong><small>${esc(Object.entries(diagnostics).map(([name, value]) => `${name} ${value}`).join(' · ') || 'No recorded issues')}</small></div></div>`;
  }
  if (key === 'factor_definition') {
    return arr(summary?.run_contract?.factor_definitions).map(definition => `<article class="run-definition-card"><div class="fact-grid"><div class="fact-block"><span>Name</span><strong>${esc(definition.name)}</strong></div><div class="fact-block"><span>Version</span><strong>${esc(definition.version)}</strong></div><div class="fact-block"><span>Engine</span><strong>${esc(definition.engine_version || definition.spec?.engine_version)}</strong></div><div class="fact-block"><span>Spec hash</span><strong class="mono">${esc(short(definition.spec_hash))}</strong></div></div><pre class="technical">${esc(json(definition.spec || {}))}</pre></article>`).join('') || '<div class="empty-state compact"><h3>No Factor Definition</h3></div>';
  }
  if (key === 'universe') {
    const universe = contract.universe || {};
    const instruments = arr(universe.actual_instrument_ids || universe.instrument_ids);
    return `<div class="metric-grid"><div class="metric-card"><span>Snapshot</span><strong class="mono">${esc(short(universe.universe_snapshot_id))}</strong></div><div class="metric-card"><span>Members</span><strong>${esc(instruments.length)}</strong></div><div class="metric-card"><span>As of</span><strong>${esc(formatDate(universe.as_of_time))}</strong></div></div><div class="factor-run-instruments">${instruments.map(item => `<code>${esc(item)}</code>`).join('') || '<span class="muted">No members recorded.</span>'}</div>`;
  }
  if (key === 'data_inputs') {
    const inputs = arr(contract.data_inputs);
    return inputs.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Manifest</th><th>Dataset</th><th>Provider</th><th>Frequency</th><th>Rows</th><th>Window</th></tr></thead><tbody>${inputs.map(item => `<tr><td class="mono">${esc(short(item.manifest_id))}</td><td class="mono">${esc(short(item.dataset_id))}</td><td>${esc(item.source || item.provider || '-')}</td><td>${esc(item.frequency || '-')}</td><td>${esc(item.row_count ?? '-')}</td><td>${esc(item.start_time || '-')} → ${esc(item.end_time || '-')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty-state compact"><h3>No frozen Data Inputs</h3></div>';
  }
  if (key === 'logs') {
    const logs = summary.logs || {};
    return `<div class="factor-run-log"><div><span>Created</span><strong>${esc(formatDate(logs.created_at))}</strong></div><div><span>Queued</span><strong>${esc(formatDate(logs.queued_at))}</strong></div><div><span>Started</span><strong>${esc(formatDate(logs.started_at))}</strong></div><div><span>Finished</span><strong>${esc(formatDate(logs.finished_at))}</strong></div><div><span>Attempts</span><strong>${esc(logs.attempt_count ?? '-')}</strong></div></div>${Object.keys(logs.error || {}).length ? `<pre class="technical">${esc(json(logs.error))}</pre>` : '<p class="muted">No Run error was recorded.</p>'}`;
  }
  return '';
}

function factorRunSectionView(data, key) {
  const items = arr(data.items);
  if (key === 'coverage') {
    return items.map(item => { const value = item.coverage || {}; const rows = Object.entries(value.by_instrument || {}).map(([instrument_id, detail]) => ({instrument_id, ...detail})); return `<section class="factor-run-result-block"><h4>${esc(item.factor?.name || 'Factor')}</h4><div class="metric-grid"><div class="metric-card"><span>Coverage</span><strong>${esc(percent(value.overall))}</strong></div><div class="metric-card"><span>Valid / Total</span><strong>${esc(value.valid_rows ?? '-')} / ${esc(value.total_rows ?? '-')}</strong></div><div class="metric-card"><span>Missing</span><strong>${esc(percent(value.missing_rate))}</strong></div><div class="metric-card"><span>Eligible cross-sections</span><strong>${esc(value.eligible_cross_section_count ?? '-')}</strong></div></div>${rows.length ? runRowsTable(rows, key) : '<p class="muted">Per-instrument coverage is unavailable for this historical artifact.</p>'}</section>`; }).join('');
  }
  if (key === 'distribution') {
    return items.map(item => { const value = item.distribution || {}; const quantiles = Object.entries(value.quantiles || {}).map(([quantile, number]) => ({quantile: `P${quantile}`, value: number})); return `<section class="factor-run-result-block"><h4>${esc(item.factor?.name || 'Factor')}</h4><div class="metric-grid"><div class="metric-card"><span>Mean</span><strong>${esc(factorRunMetric(value.mean))}</strong></div><div class="metric-card"><span>Std dev</span><strong>${esc(factorRunMetric(value.std))}</strong></div><div class="metric-card"><span>5σ outliers</span><strong>${esc(percent(value.outlier_ratio_5sigma))}</strong></div><div class="metric-card"><span>Rank turnover</span><strong>${esc(factorRunMetric(value.average_rank_turnover))}</strong></div></div>${runRowsTable(quantiles, key)}</section>`; }).join('');
  }
  if (key === 'ic_rank_ic') {
    return items.map(item => `<section class="factor-run-result-block"><h4>${esc(item.factor?.name || 'Factor')}</h4>${runRowsTable(arr(item.predictive_power).map(row => ({horizon_bars: row.horizon_bars, ic_mean: row.ic?.mean, ic_std: row.ic?.std, ic_ir: row.ic?.icir, ic_t_stat: row.ic?.t_stat, ic_positive_rate: row.ic?.positive_rate, ic_count: row.ic?.count, rank_ic_mean: row.rank_ic?.mean, rank_ic_std: row.rank_ic?.std, rank_ic_ir: row.rank_ic?.icir, rank_ic_t_stat: row.rank_ic?.t_stat, rank_ic_positive_rate: row.rank_ic?.positive_rate, rank_ic_count: row.rank_ic?.count})), key)}</section>`).join('');
  }
  if (key === 'quantile_return') {
    return items.map(item => { const rows = arr(item.quantile_returns).flatMap(horizon => arr(horizon.groups).map(group => ({horizon_bars: horizon.horizon_bars, quantile: group.quantile, mean_return: group.mean_return, high_minus_low: horizon.high_minus_low, monotonicity: horizon.monotonicity}))); return `<section class="factor-run-result-block"><h4>${esc(item.factor?.name || 'Factor')}</h4>${runRowsTable(rows, key)}</section>`; }).join('');
  }
  if (key === 'diagnostics') {
    const rows = items.flatMap(item => arr(item.diagnostics).map(diagnostic => ({factor_name: item.factor?.name, ...diagnostic})));
    return rows.length ? `<div class="reason-list">${rows.map(item => `<div class="reason-item"><span class="reason-code">${esc(item.code || item.severity || 'INFO')}</span><div><strong>${esc(item.factor_name || 'Factor')}</strong><p>${esc(item.message || '')}</p></div>${chip(item.severity || 'INFO')}</div>`).join('')}</div>` : '<div class="empty-state compact"><h3>No diagnostics</h3><p>The evaluator recorded no warnings or blocking issues.</p></div>';
  }
  return runRowsTable(arr(data.rows), key);
}

function primaryAlphaRunResult(summary) {
  return arr((summary?.alpha_run || summary?.research_backtest)?.results)[0] || null;
}

function alphaRunMoney(value) {
  if (value === null || value === undefined || value === '') return '-';
  const number = Number(value);
  return Number.isFinite(number) ? `$${number.toLocaleString('en-US', {maximumFractionDigits: 2})}` : '-';
}

function alphaRunMultiple(value) {
  const number = factorRunMetric(value, 2);
  return number === '-' ? '-' : `${number}×`;
}

function alphaRunRows(data, item, source = 'rows') {
  const rows = arr(data?.[source]);
  const artifactId = item?.artifact?.artifact_id;
  return artifactId ? rows.filter(row => row._artifact_id === artifactId) : rows;
}

function alphaRunSeriesChart(rows, valueKey, {percentage = false, label = ''} = {}) {
  const points = arr(rows).map((row, index) => ({index, time: row.event_time || row.as_of_time, value: Number(row[valueKey])})).filter(point => Number.isFinite(point.value));
  if (points.length < 2) return '<div class="empty-state compact"><h3>No chart series</h3><p>This Run did not materialize enough points for a curve.</p></div>';
  const width = 760;
  const height = 190;
  const padX = 14;
  const padY = 16;
  const values = points.map(point => point.value);
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  if (minimum === maximum) { minimum -= 1; maximum += 1; }
  const coordinates = points.map((point, index) => {
    const x = padX + index * (width - padX * 2) / (points.length - 1);
    const y = padY + (maximum - point.value) * (height - padY * 2) / (maximum - minimum);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
  const formatValue = value => percentage ? percent(value) : alphaRunMoney(value);
  return `<figure class="alpha-run-chart"><figcaption><strong>${esc(label || valueKey.replaceAll('_', ' '))}</strong><span>${esc(formatDate(points[0].time))} → ${esc(formatDate(points.at(-1).time))}</span></figcaption><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label || valueKey)}"><line x1="${padX}" y1="${padY}" x2="${padX}" y2="${height - padY}"/><line x1="${padX}" y1="${height - padY}" x2="${width - padX}" y2="${height - padY}"/><polyline points="${coordinates}"/></svg><div class="alpha-run-chart-scale"><span>${esc(formatValue(minimum))}</span><span>${esc(formatValue(maximum))}</span></div></figure>`;
}

function alphaRunInlineSection(summary, key) {
  const contract = summary?.alpha_run || summary?.research_backtest;
  if (!contract) return '';
  const primary = primaryAlphaRunResult(summary) || {};
  const alpha = primary.alpha || {};
  const performance = primary.performance || {};
  const isStrictAlpha = summary?.product_run_type === 'ALPHA_RUN';
  const isResearchBacktest = summary?.product_run_type === 'RESEARCH_BACKTEST';
  if (key === 'overview' && isStrictAlpha) {
    const diagnostics = contract.diagnostic_summary || {};
    const firstRankIc = firstHorizonValue(primary.rank_ic);
    return `<div class="alpha-run-boundary"><strong>Alpha Evaluation boundary</strong><span>Evaluates signal quality and predictive power only: IC, Rank IC, decay, turnover, and regime stability. No portfolio, trades, equity curve, Sharpe, or drawdown is produced.</span></div><div class="metric-grid"><div class="metric-card"><span>Status</span><strong>${esc(friendlyStatus(contract.status))}</strong><small>${esc(contract.schema_version)}</small></div><div class="metric-card alpha-run-name"><span>Alpha</span><strong>${esc(alpha.name || '-')}</strong><small>${esc(alpha.version || '')}</small></div><div class="metric-card"><span>Signal Rows</span><strong>${esc(primary.signal_summary?.score_count ?? primary.signal_summary?.row_count ?? '-')}</strong><small>immutable observations</small></div><div class="metric-card"><span>Rank IC</span><strong>${esc(factorRunMetric(firstRankIc?.mean))}</strong><small>ICIR ${esc(factorRunMetric(firstRankIc?.icir, 2))}</small></div></div>${Object.keys(diagnostics).length ? `<p class="muted alpha-run-diagnostic-summary">${esc(Object.entries(diagnostics).map(([name, value]) => `${name} ${value}`).join(' / '))}</p>` : ''}`;
  }
  if (key === 'overview' && isResearchBacktest) {
    const benchmark = contract.benchmark_status || {};
    return `<div class="alpha-run-boundary"><strong>Research Backtest boundary</strong><span>Transforms an immutable Alpha signal into portfolio targets, simulated positions and trades, costs, equity, and drawdown. It does not deploy a Strategy or enter Paper/Live trading.</span></div><div class="metric-grid"><div class="metric-card"><span>Status</span><strong>${esc(friendlyStatus(contract.status))}</strong><small>${esc(contract.schema_version)}</small></div><div class="metric-card alpha-run-name"><span>Alpha Source</span><strong>${esc(alpha.name || '-')}</strong><small>${esc(alpha.version || '')}</small></div><div class="metric-card"><span>Total Return</span><strong>${esc(percent(performance.total_return))}</strong><small>${esc(alphaRunMoney(performance.final_equity))} final equity</small></div><div class="metric-card"><span>Benchmark</span><strong>${esc(benchmark.configured ? 'Configured' : 'Not configured')}</strong><small>${esc(benchmark.materialized ? 'comparison materialized' : 'absolute metrics only')}</small></div></div>`;
  }
  if (key === 'overview') {
    const diagnostics = contract.diagnostic_summary || {};
    const totalCosts = performance.fees === null || performance.fees === undefined || performance.slippage_cost === null || performance.slippage_cost === undefined ? null : Number(performance.fees) + Number(performance.slippage_cost);
    if (summary?.product_run_type === 'LEGACY_HYBRID_RUN') {
      return `<div class="alpha-run-boundary"><strong>Legacy Hybrid Run</strong><span>${esc(contract.migration_notice || 'This historical Alpha Run embedded portfolio and backtest outputs. New Alpha Evaluations stop at predictive signal evaluation; use Research Backtest for portfolio performance.')}</span></div><div class="metric-grid"><div class="metric-card"><span>Status</span><strong>${esc(friendlyStatus(contract.status))}</strong><small>${esc(contract.schema_version)}</small></div><div class="metric-card alpha-run-name"><span>Alpha</span><strong>${esc(alpha.name || '-')}</strong><small>${esc(alpha.version || '')}</small></div><div class="metric-card"><span>Total Return</span><strong>${esc(percent(performance.total_return))}</strong><small>${esc(alphaRunMoney(performance.final_equity))} final equity</small></div><div class="metric-card"><span>Trades / Costs</span><strong>${esc(performance.trade_count ?? '-')}</strong><small>${esc(alphaRunMoney(totalCosts))} fees + slippage</small></div></div>`;
    }
    return `<div class="alpha-run-boundary"><strong>Alpha Run boundary</strong><span>Starts at signal construction and ends at portfolio, trades, costs, equity, and drawdown. It does not deploy a Strategy or enter Paper/Live trading.</span></div><div class="metric-grid"><div class="metric-card"><span>Status</span><strong>${esc(friendlyStatus(contract.status))}</strong><small>${esc(contract.schema_version)}</small></div><div class="metric-card alpha-run-name"><span>Alpha</span><strong>${esc(alpha.name || '-')}</strong><small>${esc(alpha.version || '')}</small></div><div class="metric-card"><span>Total Return</span><strong>${esc(percent(performance.total_return))}</strong><small>${esc(alphaRunMoney(performance.final_equity))} final equity</small></div><div class="metric-card"><span>Trades / Costs</span><strong>${esc(performance.trade_count ?? '-')}</strong><small>${esc(alphaRunMoney(totalCosts))} fees + slippage</small></div></div>${Object.keys(diagnostics).length ? `<p class="muted alpha-run-diagnostic-summary">${esc(Object.entries(diagnostics).map(([name, value]) => `${name} ${value}`).join(' · '))}</p>` : ''}`;
  }
  if (key === 'alpha_definition') {
    return arr(summary?.run_contract?.alpha_definitions).map(definition => `<article class="run-definition-card"><div class="fact-grid"><div class="fact-block"><span>Name</span><strong>${esc(definition.name)}</strong></div><div class="fact-block"><span>Version</span><strong>${esc(definition.version)}</strong></div><div class="fact-block"><span>Engine</span><strong>${esc(definition.engine_version || definition.spec?.engine_version || '-')}</strong></div><div class="fact-block"><span>Spec hash</span><strong class="mono">${esc(short(definition.spec_hash))}</strong></div></div><pre class="technical">${esc(json(definition.spec || {}))}</pre></article>`).join('') || '<div class="empty-state compact"><h3>No Alpha Definition</h3></div>';
  }
  if (key === 'factor_inputs') {
    const inputs = arr(primary.factor_inputs);
    return inputs.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Factor</th><th>Version</th><th>Weight</th><th>Transform</th><th>Direction</th><th>Artifact</th></tr></thead><tbody>${inputs.map(item => `<tr><td>${esc(item.name || '-')}</td><td>${esc(item.version || '-')}</td><td>${esc(factorRunMetric(item.weight))}</td><td>${esc(item.transform || '-')}</td><td>${esc(item.ascending === true ? 'Ascending' : item.ascending === false ? 'Descending' : '-')}</td><td class="mono">${esc(short(item.artifact?.artifact_id))}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty-state compact"><h3>No Factor Inputs</h3></div>';
  }
  if (key === 'universe') {
    const universe = contract.universe || {};
    const instruments = arr(universe.actual_instrument_ids || universe.instrument_ids);
    return `<div class="metric-grid"><div class="metric-card"><span>Snapshot</span><strong class="mono">${esc(short(universe.universe_snapshot_id))}</strong></div><div class="metric-card"><span>Members</span><strong>${esc(instruments.length)}</strong></div><div class="metric-card"><span>As of</span><strong>${esc(formatDate(universe.as_of_time))}</strong></div></div><div class="factor-run-instruments">${instruments.map(item => `<code>${esc(item)}</code>`).join('') || '<span class="muted">No members recorded.</span>'}</div>`;
  }
  if (key === 'signal_rules') {
    const spec = primary.definition_spec || {};
    return `<div class="metric-grid"><div class="metric-card"><span>Output Scale</span><strong>${esc(spec.output_scale || '-')}</strong></div><div class="metric-card"><span>Minimum Coverage</span><strong>${esc(percent(spec.minimum_coverage))}</strong></div><div class="metric-card"><span>Minimum Cross-section</span><strong>${esc(spec.minimum_cross_section_size ?? '-')}</strong></div><div class="metric-card"><span>Missing Policy</span><strong>${esc(spec.missing_policy || '-')}</strong></div></div><pre class="technical">${esc(json(spec))}</pre>`;
  }
  if (key === 'portfolio_rules' || key === 'execution_assumptions') {
    const value = key === 'portfolio_rules' ? contract.portfolio_rules : contract.execution_assumptions;
    return `<div class="run-definition-card"><pre class="technical">${esc(json(value || {}))}</pre></div>`;
  }
  if (key === 'benchmark') {
    const benchmark = contract.benchmark_status || {};
    const benchmarkSpec = contract.benchmark_spec || {};
    return `<div class="alpha-run-boundary"><strong>Benchmark status</strong><span>${esc(benchmark.configured ? (benchmark.materialized ? 'Benchmark comparison is materialized.' : 'Benchmark is configured, but comparison series is not materialized in this result contract.') : 'No benchmark was configured. Excess return and Information Ratio are intentionally unavailable.')}</span></div><div class="run-definition-card"><pre class="technical">${esc(json(benchmarkSpec))}</pre></div>`;
  }
  if (key === 'logs') {
    const logs = summary.logs || {};
    return `<div class="factor-run-log"><div><span>Created</span><strong>${esc(formatDate(logs.created_at))}</strong></div><div><span>Queued</span><strong>${esc(formatDate(logs.queued_at))}</strong></div><div><span>Started</span><strong>${esc(formatDate(logs.started_at))}</strong></div><div><span>Finished</span><strong>${esc(formatDate(logs.finished_at))}</strong></div><div><span>Attempts</span><strong>${esc(logs.attempt_count ?? '-')}</strong></div></div>${Object.keys(logs.error || {}).length ? `<pre class="technical">${esc(json(logs.error))}</pre>` : '<p class="muted">No Run error was recorded.</p>'}`;
  }
  return '';
}

function alphaRunSectionView(data, key) {
  const items = arr(data.items);
  if (key === 'signals') {
    return items.map(item => {
      const value = item.signal_summary || {};
      const predictivePower = [...new Set([...Object.keys(item.ic || {}), ...Object.keys(item.rank_ic || {})])]
        .sort((left, right) => Number(left) - Number(right))
        .map(horizon_bars => ({
          horizon_bars,
          ic_mean: item.ic?.[horizon_bars]?.mean,
          ic_std: item.ic?.[horizon_bars]?.std,
          ic_ir: item.ic?.[horizon_bars]?.icir,
          ic_t_stat: item.ic?.[horizon_bars]?.t_stat,
          ic_positive_rate: item.ic?.[horizon_bars]?.positive_rate,
          ic_count: item.ic?.[horizon_bars]?.count,
          rank_ic_mean: item.rank_ic?.[horizon_bars]?.mean,
          rank_ic_std: item.rank_ic?.[horizon_bars]?.std,
          rank_ic_ir: item.rank_ic?.[horizon_bars]?.icir,
          rank_ic_t_stat: item.rank_ic?.[horizon_bars]?.t_stat,
          rank_ic_positive_rate: item.rank_ic?.[horizon_bars]?.positive_rate,
          rank_ic_count: item.rank_ic?.[horizon_bars]?.count,
        }));
      const decay = Object.entries(item.holding_period_decay || {}).map(([horizon_bars, metrics]) => ({horizon_bars, ...metrics}));
      const regimes = Object.entries(item.regime_performance || {}).flatMap(([horizon_bars, rows]) => Object.entries(rows || {}).map(([regime, metrics]) => ({horizon_bars, regime, ...metrics})));
      return `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4><div class="metric-grid"><div class="metric-card"><span>Scores</span><strong>${esc(value.score_count ?? value.row_count ?? '-')}</strong></div><div class="metric-card"><span>Mean / Std</span><strong>${esc(factorRunMetric(value.score_mean))} / ${esc(factorRunMetric(value.score_std))}</strong></div><div class="metric-card"><span>Rank Stability</span><strong>${esc(factorRunMetric(value.average_rank_stability))}</strong></div><div class="metric-card"><span>Membership Turnover</span><strong>${esc(percent(value.average_membership_turnover))}</strong></div></div>${predictivePower.length ? `<h5>IC / Rank IC</h5>${runRowsTable(predictivePower, 'ic_rank_ic')}` : ''}<h5>Signal observations</h5>${runRowsTable(alphaRunRows(data, item), key)}${decay.length ? `<h5>Holding-period decay</h5>${runRowsTable(decay, 'holding_period_decay')}` : ''}${regimes.length ? `<h5>Regime performance</h5>${runRowsTable(regimes, 'regime_performance')}` : ''}</section>`;
    }).join('');
  }
  if (key === 'ic_accuracy') {
    return items.map(item => {
      const rows = [...new Set([...Object.keys(item.ic || {}), ...Object.keys(item.rank_ic || {})])]
        .sort((left, right) => Number(left) - Number(right))
        .map(horizon_bars => ({
          horizon_bars,
          ic_mean: item.ic?.[horizon_bars]?.mean,
          ic_std: item.ic?.[horizon_bars]?.std,
          ic_ir: item.ic?.[horizon_bars]?.icir,
          ic_t_stat: item.ic?.[horizon_bars]?.t_stat,
          ic_positive_rate: item.ic?.[horizon_bars]?.positive_rate,
          ic_count: item.ic?.[horizon_bars]?.count,
          rank_ic_mean: item.rank_ic?.[horizon_bars]?.mean,
          rank_ic_std: item.rank_ic?.[horizon_bars]?.std,
          rank_ic_ir: item.rank_ic?.[horizon_bars]?.icir,
          rank_ic_t_stat: item.rank_ic?.[horizon_bars]?.t_stat,
          rank_ic_positive_rate: item.rank_ic?.[horizon_bars]?.positive_rate,
          rank_ic_count: item.rank_ic?.[horizon_bars]?.count,
        }));
      return `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4>${runRowsTable(rows, 'ic_rank_ic')}</section>`;
    }).join('');
  }
  if (key === 'decay') {
    return items.map(item => `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4>${runRowsTable(Object.entries(item.holding_period_decay || {}).map(([horizon_bars, metrics]) => ({horizon_bars, ...metrics})), 'holding_period_decay')}</section>`).join('');
  }
  if (key === 'turnover') {
    return items.map(item => { const value = item.turnover_summary || {}; return `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4><div class="metric-grid"><div class="metric-card"><span>Membership Turnover</span><strong>${esc(percent(value.average_membership_turnover))}</strong></div><div class="metric-card"><span>Rank Stability</span><strong>${esc(factorRunMetric(value.average_rank_stability))}</strong></div><div class="metric-card"><span>Signal Rows</span><strong>${esc(value.score_count ?? '-')}</strong></div></div></section>`; }).join('');
  }
  if (key === 'regime_analysis') {
    return items.map(item => `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4>${runRowsTable(Object.entries(item.regime_performance || {}).flatMap(([horizon_bars, rows]) => Object.entries(rows || {}).map(([regime, metrics]) => ({horizon_bars, regime, ...metrics}))), 'regime_performance')}</section>`).join('');
  }
  if (key === 'portfolio_targets') {
    return items.map(item => `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4><details class="factor-advanced-details"><summary>Portfolio construction rules</summary><pre class="technical">${esc(json(item.portfolio_rules || {}))}</pre></details>${runRowsTable(alphaRunRows(data, item), key)}</section>`).join('');
  }
  if (key === 'positions') {
    return items.map(item => { const value = item.exposure_summary || {}; return `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4><div class="metric-grid"><div class="metric-card"><span>Average Exposure</span><strong>${esc(percent(value.average_exposure))}</strong></div><div class="metric-card"><span>Average Cash</span><strong>${esc(percent(value.average_cash_ratio))}</strong></div><div class="metric-card"><span>Bars</span><strong>${esc(value.bar_count ?? '-')}</strong></div><div class="metric-card"><span>Instruments</span><strong>${esc(value.instrument_count ?? '-')}</strong></div></div>${runRowsTable(alphaRunRows(data, item), key)}</section>`; }).join('');
  }
  if (key === 'trades') {
    return items.map(item => { const value = item.trade_summary || {}; return `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4><div class="metric-grid"><div class="metric-card"><span>Trades</span><strong>${esc(value.trade_count ?? '-')}</strong></div><div class="metric-card"><span>Rebalances</span><strong>${esc(value.rebalance_count ?? '-')}</strong></div><div class="metric-card"><span>Flat Targets</span><strong>${esc(value.flat_rebalance_count ?? '-')}</strong></div><div class="metric-card"><span>Turnover</span><strong>${esc(alphaRunMultiple(value.turnover))}</strong></div><div class="metric-card"><span>Fees</span><strong>${esc(alphaRunMoney(value.fees))}</strong></div><div class="metric-card"><span>Slippage Cost</span><strong>${esc(alphaRunMoney(value.slippage_cost))}</strong></div></div>${runRowsTable(alphaRunRows(data, item), key)}</section>`; }).join('');
  }
  if (key === 'equity_curve') {
    return items.map(item => { const value = item.equity_summary || {}; const rows = alphaRunRows(data, item); const series = alphaRunRows(data, item, 'series'); return `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4><div class="metric-grid"><div class="metric-card"><span>Initial Equity</span><strong>${esc(alphaRunMoney(value.initial_cash))}</strong></div><div class="metric-card"><span>Final Equity</span><strong>${esc(alphaRunMoney(value.final_equity))}</strong></div><div class="metric-card"><span>Total Return</span><strong>${esc(percent(value.total_return))}</strong></div><div class="metric-card"><span>Sharpe</span><strong>${esc(factorRunMetric(value.sharpe, 2))}</strong></div></div>${alphaRunSeriesChart(series, 'equity', {label: 'Equity curve'})}${runRowsTable(rows, key)}</section>`; }).join('');
  }
  if (key === 'performance_metrics') {
    return items.map(item => { const value = item.performance || {}; return `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4><div class="metric-grid alpha-performance-grid"><div class="metric-card"><span>Total Return</span><strong>${esc(percent(value.total_return))}</strong></div><div class="metric-card"><span>Annualized Return</span><strong>${esc(percent(value.annualized_return))}</strong></div><div class="metric-card"><span>Volatility</span><strong>${esc(percent(value.volatility))}</strong></div><div class="metric-card"><span>Sharpe</span><strong>${esc(factorRunMetric(value.sharpe, 2))}</strong></div><div class="metric-card"><span>Max Drawdown</span><strong>${esc(percent(value.max_drawdown))}</strong></div><div class="metric-card"><span>Turnover</span><strong>${esc(alphaRunMultiple(value.turnover))}</strong></div><div class="metric-card"><span>Fees</span><strong>${esc(alphaRunMoney(value.fees))}</strong></div><div class="metric-card"><span>Slippage</span><strong>${esc(alphaRunMoney(value.slippage_cost))}</strong></div></div><details class="factor-advanced-details"><summary>Full immutable performance record</summary><pre class="technical">${esc(json(value))}</pre></details></section>`; }).join('');
  }
  if (key === 'drawdown') {
    return items.map(item => { const value = item.drawdown_summary || {}; const rows = alphaRunRows(data, item); const series = alphaRunRows(data, item, 'series'); return `<section class="alpha-run-result-block"><h4>${esc(item.alpha?.name || 'Alpha')}</h4><div class="metric-grid"><div class="metric-card"><span>Max Drawdown</span><strong>${esc(percent(value.max_drawdown))}</strong></div><div class="metric-card"><span>Peak</span><strong>${esc(formatDate(value.max_drawdown_peak_at))}</strong></div><div class="metric-card"><span>Trough</span><strong>${esc(formatDate(value.max_drawdown_at))}</strong></div><div class="metric-card"><span>Max Underwater Bars</span><strong>${esc(value.max_underwater_bars ?? '-')}</strong></div></div>${alphaRunSeriesChart(series, 'drawdown', {percentage: true, label: 'Drawdown curve'})}${runRowsTable(rows, key)}</section>`; }).join('');
  }
  if (key === 'diagnostics') {
    const rows = items.flatMap(item => arr(item.diagnostics).map(diagnostic => ({alpha_name: item.alpha?.name, ...diagnostic})));
    return rows.length ? `<div class="reason-list">${rows.map(item => `<div class="reason-item"><span class="reason-code">${esc(item.code || item.severity || 'INFO')}</span><div><strong>${esc(item.alpha_name || 'Alpha')}</strong><p>${esc(item.message || '')}</p></div>${chip(item.severity || 'INFO')}</div>`).join('')}</div>` : '<div class="empty-state compact"><h3>No diagnostics</h3><p>The Alpha evaluator recorded no warnings or blocking issues.</p></div>';
  }
  return runRowsTable(arr(data.rows), key);
}

function runInlineSection(summary, key) {
  const factorView = factorRunInlineSection(summary, key);
  if (factorView) return factorView;
  const alphaView = alphaRunInlineSection(summary, key);
  if (alphaView) return alphaView;
  const contract = summary?.run_contract || {};
  const inline = {
    overview: {
      run_id: summary?.run_id,
      run_type: summary?.product_run_type,
      status: summary?.status,
      started_at: summary?.started_at,
      finished_at: summary?.finished_at,
    },
    factor_definition: contract.factor_definitions,
    alpha_definition: contract.alpha_definitions,
    factor_inputs: contract.factor_inputs,
    universe: contract.universe,
    data_inputs: contract.data_inputs,
    signal_rules: contract.signal_rules,
    portfolio_rules: contract.portfolio_rules,
    execution_assumptions: contract.execution_assumptions,
    logs: summary?.logs,
  };
  return `<div class="run-definition-card"><pre class="technical">${esc(json(inline[key] ?? {}))}</pre></div>`;
}

function runRowsTable(rows, sectionKey) {
  if (!rows.length) return '<div class="empty-state compact"><h3>No rows</h3><p>This section has no materialized observations for the current Run.</p></div>';
  const available = [...new Set(rows.flatMap(row => Object.keys(row)))];
  const preferred = {
    factor_output: ['event_time', 'available_time', 'instrument_id', 'factor_name', 'value', 'quality_status'],
    signals: ['as_of_time', 'available_time', 'instrument_id', 'alpha_name', 'score', 'rank', 'percentile', 'target_weight'],
    positions: ['event_time', 'instrument_id', 'quantity', 'market_value', 'actual_weight', 'equity'],
    trades: ['event_time', 'instrument_id', 'side', 'quantity', 'reference_price', 'fill_price', 'fee', 'slippage_cost'],
    equity_curve: ['event_time', 'equity', 'cash', 'cash_ratio', 'gross_exposure'],
    drawdown: ['event_time', 'equity', 'peak_equity', 'peak_time', 'drawdown', 'underwater_bars'],
    performance_metrics: ['metrics', 'execution_spec', 'dataset_manifest_ids', 'universe_snapshot_ids'],
    distribution: ['quantile', 'value'],
    ic_rank_ic: ['horizon_bars', 'ic_mean', 'ic_std', 'ic_ir', 'ic_t_stat', 'ic_positive_rate', 'ic_count', 'rank_ic_mean', 'rank_ic_std', 'rank_ic_ir', 'rank_ic_t_stat', 'rank_ic_positive_rate', 'rank_ic_count'],
    quantile_return: ['horizon_bars', 'quantile', 'mean_return', 'high_minus_low', 'monotonicity'],
  }[sectionKey] || [];
  const keys = [...preferred.filter(key => available.includes(key)), ...available.filter(key => !preferred.includes(key))].slice(0, 14);
  return `<div class="table-wrap run-result-table"><table class="data-table"><thead><tr>${keys.map(key => `<th>${esc(key.replaceAll('_', ' '))}</th>`).join('')}</tr></thead><tbody>${rows.slice(0, 200).map(row => `<tr>${keys.map(key => {
    const value = row[key];
    return `<td>${esc(value && typeof value === 'object' ? json(value) : value ?? '-')}</td>`;
  }).join('')}</tr>`).join('')}</tbody></table></div>`;
}

async function showRunSection(id, key) {
  const summary = state.runSummaries[id];
  const target = $('runSectionPanel');
  if (!summary || !target) return;
  document.querySelectorAll('[data-action="run-section"]').forEach(button => button.classList.toggle('active', button.dataset.section === key));
  const section = arr(summary.sections).find(item => item.key === key);
  $('runSectionTitle').textContent = section?.label || key;
  if (!arr(section?.artifact_ids).length) {
    target.innerHTML = runInlineSection(summary, key);
    return;
  }
  target.innerHTML = '<p class="muted">Loading immutable result rows…</p>';
  try {
    const data = await api(`/api/research/runs/${encodeURIComponent(id)}/sections/${encodeURIComponent(key)}?limit=200`);
    let body = runRowsTable(arr(data.rows), key);
    if (summary.product_run_type === 'FACTOR_RUN' && data.view_type) body = factorRunSectionView(data, key);
    if (['ALPHA_RUN', 'LEGACY_HYBRID_RUN', 'RESEARCH_BACKTEST'].includes(summary.product_run_type) && data.view_type) body = alphaRunSectionView(data, key);
    target.innerHTML = `<div class="run-section-meta"><span>${esc(data.total_rows)} rows</span><span>${esc(data.artifact_type || '')}</span><span>${esc(data.schema_version || '')}</span></div>${body}`;
  } catch (error) {
    target.innerHTML = `<div class="empty-state compact"><h3>Unable to load section</h3><p>${esc(error.message)}</p></div>`;
  }
}

async function showRunDetails(id) {
  const run = state.runs.find(item => item.run_id === id);
  if (!run) return;
  let summary = null;
  try { summary = state.runSummaries[id] || await api(`/api/research/runs/${encodeURIComponent(id)}/result-summary`); state.runSummaries[id] = summary; } catch (_) {}
  if (!summary) {
    openDrawer(testLabel(run.run_type), 'RESEARCH RESULT', `<pre class="technical">${esc(json(run))}</pre>`);
    return;
  }
  const values = runSummaryValues(summary, run);
  const isFactor = run.run_type === 'FACTOR_EVALUATION';
  const isStrictAlpha = summary.product_run_type === 'ALPHA_RUN';
  const isResearchBacktest = summary.product_run_type === 'RESEARCH_BACKTEST';
  const isLegacyHybrid = summary.product_run_type === 'LEGACY_HYBRID_RUN';
  const factorResult = primaryFactorRunResult(summary);
  const alphaResult = primaryAlphaRunResult(summary);
  const firstPower = arr(factorResult?.predictive_power)[0] || {};
  const alphaPerformance = alphaResult?.performance || values.performance;
  const firstRankIc = firstHorizonValue(alphaResult?.rank_ic);
  const cards = isFactor
    ? [['Status', friendlyStatus(run.status)], ['Coverage', percent(factorResult?.coverage?.overall ?? values.evaluation.coverage)], ['IC', factorRunMetric(firstPower.ic?.mean ?? values.ic)], ['Rank IC / ICIR', `${factorRunMetric(firstPower.rank_ic?.mean ?? values.rankIc)} / ${factorRunMetric(firstPower.rank_ic?.icir ?? values.icir, 2)}`]]
    : isStrictAlpha
      ? [['Status', friendlyStatus(run.status)], ['Signal Rows', alphaResult?.signal_summary?.score_count ?? alphaResult?.signal_summary?.row_count ?? '-'], ['Rank IC', factorRunMetric(firstRankIc?.mean ?? values.rankIc)], ['Rank ICIR', factorRunMetric(firstRankIc?.icir ?? values.icir, 2)]]
      : (isResearchBacktest || isLegacyHybrid)
        ? [['Status', friendlyStatus(run.status)], ['Total Return', percent(alphaPerformance.total_return)], ['Sharpe', factorRunMetric(alphaPerformance.sharpe, 2)], ['Max Drawdown', percent(alphaPerformance.max_drawdown)]]
        : [['Status', friendlyStatus(run.status)], ['Run Type', 'Unsupported'], ['Result', '-'], ['Schema', summary.result_schema_version || '-']];
  const sections = arr(summary.sections);
  openDrawer(testLabel(run.run_type, run, summary), isResearchBacktest ? 'RESEARCH BACKTEST RESULT' : isStrictAlpha ? 'ALPHA EVALUATION RESULT' : isFactor ? 'FACTOR EVALUATION RESULT' : 'LEGACY RESEARCH RESULT', `<div class="metric-grid">${cards.map(([label, value]) => `<div class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('')}</div><div class="run-result-layout"><nav class="run-section-nav">${sections.map((section, index) => `<button type="button" class="${index === 0 ? 'active' : ''}" data-action="run-section" data-id="${esc(id)}" data-section="${esc(section.key)}"><span>${esc(section.label)}</span>${arr(section.artifact_ids).length ? `<small>${arr(section.artifact_ids).length} artifact${arr(section.artifact_ids).length === 1 ? '' : 's'}</small>` : ''}</button>`).join('')}</nav><section class="run-section-view"><div class="run-section-heading"><span class="eyebrow">SECTION</span><h3 id="runSectionTitle">${esc(sections[0]?.label || 'Overview')}</h3></div><div id="runSectionPanel">${runInlineSection(summary, sections[0]?.key || 'overview')}</div></section></div>`);
  $('detailDrawer').classList.add('run-result-drawer');
}

document.addEventListener('click', async event => {
  const researchTab = event.target.closest('[data-research-tab]');
  if (researchTab) { switchResearchTab(researchTab.dataset.researchTab); return; }
  const libraryTab = event.target.closest('[data-library-tab]');
  if (libraryTab) { switchLibraryTab(libraryTab.dataset.libraryTab); return; }
  const libraryGroup = event.target.closest('[data-library-group]');
  if (libraryGroup) {
    const assetType = state.libraryTab.toUpperCase();
    state.libraryActiveGroup[assetType] = libraryGroup.dataset.libraryGroup;
    await refreshCurrentLibraryTab();
    return;
  }
  const runFilter = event.target.closest('[data-run-filter]');
  if (runFilter) { state.runFilter = runFilter.dataset.runFilter; renderResearchRuns(); return; }
  const node = event.target.closest('[data-action]');
  if (!node) return;
  const action = node.dataset.action;
  try {
    if (action === 'new-research') newResearchDialog();
    else if (action === 'go-research-tab') switchResearchTab(node.dataset.target);
    else if (action === 'new-universe') universeDialog(null, {surface: 'research'});
    else if (action === 'new-library-universe') universeDialog(null, {surface: 'library'});
    else if (action === 'edit-shared-universe') universeDialog(state.sharedUniverses.find(item => item.universe_id === node.dataset.id) || state.universeBindings.find(item => item.universe_id === node.dataset.id));
    else if (action === 'copy-shared-universe') copySharedUniverseDialog(state.sharedUniverses.find(item => item.universe_id === node.dataset.id) || state.universeBindings.find(item => item.universe_id === node.dataset.id));
    else if (action === 'preview-shared-universe') { const item = state.sharedUniverses.find(value => value.universe_id === node.dataset.id) || state.universeBindings.find(value => value.universe_id === node.dataset.id); if (item) await previewSharedUniverse(item); }
    else if (action === 'shared-universe-usage') { const item = state.sharedUniverses.find(value => value.universe_id === node.dataset.id) || state.universeBindings.find(value => value.universe_id === node.dataset.id); if (item) await showSharedUniverseUsage(item); }
    else if (action === 'shared-universe-history') { const item = state.sharedUniverses.find(value => value.universe_id === node.dataset.id) || state.universeBindings.find(value => value.universe_id === node.dataset.id); if (item) await showSharedUniverseHistory(item); }
    else if (action === 'shared-universe-details') { const item = state.sharedUniverses.find(value => value.universe_id === node.dataset.id) || state.universeBindings.find(value => value.universe_id === node.dataset.id); if (item) showSharedUniverseDetails(item); }
    else if (action === 'create-universe-requirement') { await openRequirementEditor({target: 'research', universeId: node.dataset.id}); }
    else if (action === 'view-data-progress') { switchResearchTab('data'); await checkData(); }
    else if (action === 'view-universe-requirement-changes') showUniverseRequirementChanges(node.dataset.id);
    else if (action === 'review-universe-requirement') {
      const reconciliation = state.requirementReconciliations[node.dataset.id] || {changes: {added: [], removed: []}, reasons: []};
      const item = state.requirementItems.find(value => value.ref_id === reconciliation.requirement_ref_id) || state.requirementItems[0];
      if (item) {
        state.pendingRequirementContext = {universeId: node.dataset.id, review: reconciliation};
        await editSharedRequirement(item);
      } else await openRequirementEditor({target: 'research', universeId: node.dataset.id, review: reconciliation});
    }
    else if (action === 'bind-shared-universe') { await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/universes/add`, {method: 'POST', body: JSON.stringify({universe_id: node.dataset.id, role: state.universeBindings.length ? 'REFERENCE' : 'PRIMARY'})}); closeDialog(); await loadResearch(state.projectId); switchResearchTab('universe'); notify('Shared Universe added by reference.'); }
    else if (action === 'remove-shared-universe') { if (window.confirm('Remove this Universe from the current Research? The shared Universe and historical Runs will remain.')) { await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/universes/${encodeURIComponent(node.dataset.id)}`, {method: 'DELETE'}); await loadResearch(state.projectId); switchResearchTab('universe'); notify('Universe removed from this Research.'); } }
    else if (action === 'remove-legacy-universe') { if (window.confirm('Remove this Universe from the current Research? The Universe definition and historical Runs will remain.')) { await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/universe-ref`, {method: 'DELETE'}); await loadResearch(state.projectId); switchResearchTab('universe'); notify('Universe removed from this Research.'); } }
    else if (action === 'set-primary-universe') { await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/universe-bindings`, {method: 'PUT', body: JSON.stringify({universe_id: node.dataset.id})}); await loadResearch(state.projectId); switchResearchTab('universe'); notify('Primary Universe updated.'); }
    else if (action === 'restore-shared-universe') {
      const item = state.sharedUniverses.find(value => value.universe_id === node.dataset.universeId) || state.universeBindings.find(value => value.universe_id === node.dataset.universeId);
      if (item && window.confirm('Restore this historical definition as a new revision? Existing Runs remain unchanged.')) {
        const payload = {revision_id: node.dataset.revisionId, expected_current_revision_id: item.current_revision_id, current_project_id: state.surface === 'research-detail' ? state.projectId : ''};
        try { await api(`/api/library/universes/${encodeURIComponent(item.universe_id)}/restore`, {method: 'POST', body: JSON.stringify(payload)}); }
        catch (error) { if (error.code !== 'UNIVERSE_SHARED_EDIT_CONFIRMATION_REQUIRED' || !window.confirm(`${error.message}\n\nContinue and update all active Research for future work?`)) throw error; await api(`/api/library/universes/${encodeURIComponent(item.universe_id)}/restore`, {method: 'POST', body: JSON.stringify({...payload, confirm_shared: true})}); }
        closeDrawer(); await loadBase(); if (state.surface === 'research-detail') await loadResearch(state.projectId); notify('Historical definition restored as a new revision.');
      }
    }
    else if (action === 'universe-editor-mode') {
      const editor = state.universeEditor; if (!editor) return;
      const nextMode = node.dataset.mode;
      if (nextMode !== 'ui' && editor.mode === 'ui') { editor.definition = universeEditorDefinitionFromForm(); $('universeScript').value = await api('/api/library/universes/script/render', {method: 'POST', body: JSON.stringify({definition: editor.definition})}); }
      if (nextMode === 'ui' && editor.mode !== 'ui') { const parsed = await api('/api/library/universes/script/parse', {method: 'POST', body: JSON.stringify({script: $('universeScript').value})}); loadUniverseEditorDefinition(parsed); }
      editor.mode = nextMode; document.querySelectorAll('[data-action="universe-editor-mode"]').forEach(button => button.classList.toggle('active', button === node)); $('universeUi').hidden = nextMode === 'script'; $('universeScriptPane').hidden = nextMode === 'ui'; $('universeDefinitionPanes').classList.toggle('split', nextMode === 'split');
    }
    else if (action === 'copy-universe-editor') { if (state.universeEditor?.base) copySharedUniverseDialog(state.universeEditor.base); }
    else if (action === 'preview-universe-editor') {
      try { const definition = state.universeEditor.mode === 'script' ? await api('/api/library/universes/script/parse', {method: 'POST', body: JSON.stringify({script: $('universeScript').value})}) : universeEditorDefinitionFromForm(); await previewSharedUniverse(state.universeEditor.base, definition); }
      catch (error) { renderUniverseEditorPreview(null, {error: error.message}); }
    }
    else if (action === 'search-universe-instruments') { const result = await searchUniverseEditorInstruments($('universeInstrumentSearch').value.trim()); state.universeEditor.searchResults = result.items; state.universeEditor.searchMessage = result.message || (result.items.length ? '' : 'No matching Instruments.'); renderUniverseEditorSearchResults(); }
    else if (action === 'add-universe-search-result') { const item = state.universeEditor.searchResults[Number(node.dataset.index)]; if (item) await addUniverseEditorInstruments([item]); }
    else if (action === 'remove-universe-instrument') { state.universeEditor.instrumentMembers.splice(Number(node.dataset.index), 1); renderUniverseEditorInstrumentTable(); renderUniverseEditorPreview(); }
    else if (action === 'toggle-universe-paste') $('universePastePanel').hidden = !$('universePastePanel').hidden;
    else if (action === 'resolve-universe-paste') { const tokens = $('universePasteInput').value.split(/[\s,;]+/).filter(Boolean); const result = await resolveUniverseEditorTokens(tokens); $('universePasteStatus').textContent = `Resolved ${result.resolved} · Added ${result.added} · Invalid ${result.invalid.length}`; }
    else if (action === 'import-universe-csv') {
      const input = $('universeCsv'); input.onchange = async () => {
        const text = await input.files[0]?.text(); if (!text) return;
        const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean); const header = (lines[0] || '').toLowerCase().split(',').map(value => value.trim());
        const index = ['canonical_instrument_id', 'instrument_id', 'symbol'].map(name => header.indexOf(name)).find(value => value >= 0);
        const tokens = index >= 0 ? lines.slice(1).map(line => line.split(',')[index]?.trim()).filter(Boolean) : lines.flatMap(line => line.split(/[;,\s]+/).filter(Boolean));
        const result = await resolveUniverseEditorTokens(tokens); notify(`CSV resolved ${result.resolved}; ${result.invalid.length} invalid row(s).`, Boolean(result.invalid.length)); input.value = '';
      }; input.click();
    }
    else if (action === 'add-universe-expression-input') { captureUniverseEditorState(); const draft = state.universeEditor.methodDrafts.combine_universes; draft.inputs.push(universeEditorSources()[0]?.universe_id || ''); renderUniverseEditorFields(); }
    else if (action === 'remove-universe-expression-input') { captureUniverseEditorState(); const index = [...document.querySelectorAll('.universe-expression-row')].indexOf(node.closest('.universe-expression-row')); state.universeEditor.methodDrafts.combine_universes.inputs.splice(index, 1); renderUniverseEditorFields(); }
    else if (action === 'add-manual-group') { captureUniverseEditorState(); const draft = state.universeEditor.methodDrafts.manual_groups; draft.groups.push(Array(draft.groupSize).fill('')); renderUniverseEditorFields(); }
    else if (action === 'remove-manual-group') { captureUniverseEditorState(); const index = [...document.querySelectorAll('.universe-manual-row')].indexOf(node.closest('.universe-manual-row')); state.universeEditor.methodDrafts.manual_groups.groups.splice(index, 1); renderUniverseEditorFields(); }
    else if (action === 'paste-manual-groups') $('universeManualPastePanel').hidden = !$('universeManualPastePanel').hidden;
    else if (action === 'resolve-manual-groups') { captureUniverseEditorState(); const draft = state.universeEditor.methodDrafts.manual_groups; const rows = $('universeManualPasteInput').value.split(/\r?\n/).map(line => line.split(/[;,]+/).map(value => value.trim()).filter(Boolean)).filter(row => row.length === draft.groupSize); draft.groups.push(...rows); renderUniverseEditorFields(); }
    else if (action === 'add-universe-leg') { captureUniverseEditorState(); const draft = state.universeEditor.methodDrafts.cross_source_groups; draft.legs.push({id: `leg_${draft.legs.length + 1}`, name: `Leg ${draft.legs.length + 1}`, source_universe_id: universeEditorSources()[0]?.universe_id || ''}); renderUniverseEditorFields(); }
    else if (action === 'remove-universe-leg') { captureUniverseEditorState(); const index = [...document.querySelectorAll('.universe-leg-row')].indexOf(node.closest('.universe-leg-row')); state.universeEditor.methodDrafts.cross_source_groups.legs.splice(index, 1); renderUniverseEditorFields(); }
    else if (action === 'edit-local-universe') legacyUniverseDialog(state.universes.find(item => item.universe_definition_id === node.dataset.id));
    else if (action === 'copy-library-universe') openCloneEditor(state.library.find(item => item.source_object_id === node.dataset.id));
    else if (action === 'add-library-universe') addLibraryUniverseDialog();
    else if (action === 'use-universe') await useUniverse(node.dataset.id, state.projectId, node.dataset.libraryId || '');
    else if (action === 'publish-universe') await publishUniverse(node.dataset.id);
    else if (action === 'universe-details') showUniverseDetails(node.dataset.id);
    else if (action === 'new-factor') factorDraftDialog();
    else if (action === 'edit-factor-draft') factorDraftDialog(state.factorDrafts.find(item => item.draft_id === node.dataset.id));
    else if (action === 'discard-factor-draft') {
      const draft = state.factorDrafts.find(item => item.draft_id === node.dataset.id);
      const name = draft?.document?.identity?.name || 'Untitled Factor';
      if (draft && window.confirm(`Discard draft "${name}"? It will be removed from Saved Work. Validated Factors and historical Preview evidence will not be affected.`)) {
        node.disabled = true;
        await api(`/api/research/factor-drafts/${encodeURIComponent(draft.draft_id)}`, {
          method: 'DELETE',
          body: JSON.stringify({expected_fingerprint: draft.draft_fingerprint}),
        });
        state.factorDrafts = state.factorDrafts.filter(item => item.draft_id !== draft.draft_id);
        renderFactors();
        notify(`Draft "${name}" discarded.`);
      }
    }
    else if (action === 'remove-research-factor') {
      const factor = researchRefs('FACTOR').find(item => item.definition_id === node.dataset.id && item.slot_key === node.dataset.slot);
      if (factor && window.confirm(`Remove Factor "${factor.name}" from this Research? It will remain available in Library. Historical Previews and Runs will not change.`)) {
        node.disabled = true;
        await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/definition-refs/${encodeURIComponent(factor.slot_key)}`, {
          method: 'DELETE',
          body: JSON.stringify({expected_definition_id: factor.definition_id}),
        });
        await loadResearch(state.projectId);
        switchResearchTab('factor');
        notify(`Factor "${factor.name}" removed from this Research.`);
      }
    }
    else if (action === 'remove-research-alpha') {
      const alpha = researchRefs('ALPHA').find(item => item.definition_id === node.dataset.id && item.slot_key === node.dataset.slot);
      if (alpha && window.confirm(`Remove Alpha "${alpha.name}" from this Research? It will remain available in Library. Historical Previews and Runs will not change.`)) {
        node.disabled = true;
        await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/definition-refs/${encodeURIComponent(alpha.slot_key)}`, {
          method: 'DELETE',
          body: JSON.stringify({expected_definition_id: alpha.definition_id}),
        });
        await loadResearch(state.projectId);
        switchResearchTab('alpha');
        notify(`Alpha "${alpha.name}" removed from this Research.`);
      }
    }
    else if (action === 'edit-local-factor') factorDraftDialog(state.definitions.find(item => item.definition_id === node.dataset.id));
    else if (action === 'copy-library-definition') openCloneEditor(state.library.find(item => item.source_object_id === node.dataset.id));
    else if (action === 'add-library-definition') addLibraryDefinitionDialog(node.dataset.kind);
    else if (action === 'use-definition') await useDefinition(node.dataset.id, node.dataset.projectId || state.projectId, node.dataset.libraryId || '');
    else if (action === 'new-alpha') alphaDialog();
    else if (action === 'edit-alpha-draft') alphaDialog(state.alphaDrafts.find(item => item.draft_id === node.dataset.id));
    else if (action === 'discard-alpha-draft') {
      const draft = state.alphaDrafts.find(item => item.draft_id === node.dataset.id);
      const name = draft?.document?.identity?.name || 'Untitled Alpha';
      if (draft && window.confirm(`Discard draft "${name}"? Validated Alphas and historical Preview evidence will not be affected.`)) {
        node.disabled = true;
        await api(`/api/research/alpha-drafts/${encodeURIComponent(draft.draft_id)}`, {
          method: 'DELETE',
          body: JSON.stringify({expected_fingerprint: draft.draft_fingerprint}),
        });
        state.alphaDrafts = state.alphaDrafts.filter(item => item.draft_id !== draft.draft_id);
        renderAlphas();
        notify(`Draft "${name}" discarded.`);
      }
    }
    else if (action === 'edit-local-alpha') alphaDialog(state.definitions.find(item => item.definition_id === node.dataset.id));
    else if (action === 'validate-definition') await validateDefinition(node.dataset.id);
    else if (action === 'publish-definition') await publishDefinition(node.dataset.id);
    else if (action === 'definition-details') showDefinitionDetails(node.dataset.id);
    else if (action === 'add-data' || action === 'new-requirement') await openRequirementEditor({target: 'research', universeId: primaryUniverseBinding()?.universe_id || ''});
    else if (action === 'edit-requirement') { const item = state.requirementItems.find(value => value.ref_id === node.dataset.id); if (item) await editSharedRequirement(item); }
    else if (action === 'replace-requirement') { const item = state.requirementItems.find(value => value.ref_id === node.dataset.id); if (item) replaceRequirementDialog(item); }
    else if (action === 'edit-first-requirement') {
      let item = state.requirementItems.find(value => value.origin === 'RESEARCH');
      if (!item) {
        const libraryItem = state.requirementItems.find(value => value.origin === 'LIBRARY');
        if (libraryItem) item = await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/items/${encodeURIComponent(libraryItem.ref_id)}/duplicate`, {method: 'POST', body: '{}'});
      }
      if (item) { await loadResearch(state.projectId); await openRequirementEditor({target: 'research', item}); }
      else await openRequirementEditor({target: 'research'});
    }
    else if (action === 'duplicate-requirement') { await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/items/${encodeURIComponent(node.dataset.id)}/duplicate`, {method: 'POST', body: '{}'}); await loadResearch(state.projectId); switchResearchTab('data'); notify('Research Requirement copied.'); }
    else if (action === 'remove-requirement') { if (window.confirm('Remove this Requirement from the current Research? It will remain in Library and other Research will not be affected.')) { await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/items/${encodeURIComponent(node.dataset.id)}`, {method: 'DELETE'}); await loadResearch(state.projectId); switchResearchTab('data'); notify('Requirement removed from this Research.'); } }
    else if (action === 'publish-requirement-item') { await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/items/${encodeURIComponent(node.dataset.id)}/publish`, {method: 'POST', body: '{}'}); await loadBase(); switchResearchTab('data'); notify('Requirement published to Library.'); }
    else if (action === 'requirement-details') { const item = state.requirementItems.find(value => value.ref_id === node.dataset.id); if (item) openDrawer(item.name, 'REQUIREMENT', `<pre class="technical">${esc(json(item.spec))}</pre>`); }
    else if (action === 'new-library-requirement') await openRequirementEditor({target: 'library'});
    else if (action === 'edit-library-requirement') { const baseAsset = state.library.find(item => item.library_asset_id === node.dataset.id); if (baseAsset) await openRequirementEditor({target: 'library', baseAsset}); }
    else if (action === 'save-as-library-requirement') { const baseAsset = state.library.find(item => item.library_asset_id === node.dataset.id); if (baseAsset) await openRequirementEditor({target: 'library', baseAsset, saveAs: true}); }
    else if (action === 'archive-library-requirement') { if (window.confirm('Archive this Requirement? It must not be used by any Research.')) { await api(`/api/research/library/requirements/${encodeURIComponent(node.dataset.id)}`, {method: 'DELETE'}); await loadBase(); switchLibraryTab('requirements'); notify('Requirement archived.'); } }
    else if (action === 'archive-library-definition') { if (window.confirm(`Archive "${node.dataset.name}"? It must not be used by any Research.`)) { await api(`/api/research/library/${encodeURIComponent(node.dataset.id)}/archive`, {method: 'DELETE'}); await loadBase(); notify('Library component archived.'); } }
    else if (action === 'archive-library-universe') { if (window.confirm('Archive this Universe? It must not be used by any active Research.')) { await api(`/api/library/universes/${encodeURIComponent(node.dataset.id)}/archive`, {method: 'POST', body: '{}'}); await loadBase(); switchLibraryTab('universe'); notify('Universe archived.'); } }
    else if (action === 'update-shared-requirement') { const item = state.sharedEditItem; const context = state.pendingRequirementContext || {}; closeDialog(); if (item) await openRequirementEditor({target: 'research', item, ...context}); }
    else if (action === 'save-as-current-requirement') { const item = state.sharedEditItem; const context = state.pendingRequirementContext || {}; closeDialog(); if (item) await openRequirementEditor({target: 'research', item, saveAs: true, ...context}); }
    else if (action === 'open-library-draft') await openRequirementEditor({target: 'library', draft: state.libraryRequirementDrafts.find(item => item.draft_id === node.dataset.id)});
    else if (action === 'publish-library-draft') { await api(`/api/research/library/requirements/drafts/${encodeURIComponent(node.dataset.id)}/publish`, {method: 'POST', body: '{}'}); await loadBase(); switchLibraryTab('requirements'); notify('Library Requirement published.'); }
    else if (action === 'requirement-mode') { state.requirementEditor.mode = node.dataset.mode; document.querySelectorAll('[data-action="requirement-mode"]').forEach(button => button.classList.toggle('active', button === node)); $('requirementUi').hidden = node.dataset.mode === 'script'; $('requirementScriptPane').hidden = node.dataset.mode === 'ui'; document.querySelector('.requirement-editor-grid').classList.toggle('split', node.dataset.mode === 'split'); if (node.dataset.mode !== 'script') $('reqScript').value = await api('/api/research/requirements/script/render', {method: 'POST', body: JSON.stringify({spec: requirementSpecFromForm()})}); }
    else if (action === 'search-requirement-instruments') { const query = $('reqInstrumentSearch').value.trim(); const response = await fetch(`/api/binance/markets/search?q=${encodeURIComponent(query)}&limit=12`); const body = await response.json(); const results = arr(body.data || body.markets || body.items || body.results); $('reqInstrumentResults').innerHTML = results.map(item => { const symbol = item.symbol || item.market || item.id || ''; return `<button type="button" data-action="add-requirement-instrument" data-symbol="${esc(symbol)}">${esc(symbol)}</button>`; }).join('') || '<span>No symbols found</span>'; }
    else if (action === 'add-requirement-instrument') { const values = new Set($('reqInstruments').value.split(/[\s,;]+/).filter(Boolean)); values.add(node.dataset.symbol); $('reqInstruments').value = [...values].join(', '); }
    else if (action === 'close-requirement-editor') closeDialog();
    else if (action === 'copy-library-requirements') { const libraryItem = state.requirementItems.find(item => item.origin === 'LIBRARY'); if (libraryItem) { const copy = await api(`/api/research/projects/${encodeURIComponent(state.projectId)}/requirements/items/${encodeURIComponent(libraryItem.ref_id)}/duplicate`, {method: 'POST', body: '{}'}); await loadResearch(state.projectId); await openRequirementEditor({target: 'research', item: copy}); } }
    else if (action === 'add-library-requirements') addLibraryRequirementsDialog();
    else if (action === 'publish-requirements') await publishRequirements();
    else if (action === 'check-data') await checkData();
    else if (action === 'retry-status-refresh') { state.requirementRefreshError = ''; await refreshRequirementStatus(); }
    else if (action === 'test-factor') await testDialog('factor');
    else if (action === 'test-alpha') await testDialog('alpha');
    else if (action === 'test-backtest') await testDialog('backtest');
    else if (action === 'start-test') await startCheckedTest();
    else if (action === 'run-details') await showRunDetails(node.dataset.id);
    else if (action === 'run-section') await showRunSection(node.dataset.id, node.dataset.section);
    else if (action === 'library-use') chooseResearchDialog('Use in Research', 'confirm-library-use', node.dataset.kind, node.dataset.id);
    else if (action === 'confirm-library-use') await applyLibraryAsset(node.dataset.id, node.dataset.kind, node.dataset.projectId);
    else if (action === 'library-new-version') chooseResearchDialog('Create New Version', 'confirm-library-version', node.dataset.kind, node.dataset.id);
    else if (action === 'confirm-library-version') startLibraryVersion(node.dataset.id, node.dataset.projectId);
    else if (action === 'view-usage') await showLibraryUsage(node.dataset.id);
    else if (action === 'library-asset-details') showLibraryAssetDetails(node.dataset.id);
    else if (action === 'new-library-group') await newLibraryGroupDialog(node.dataset.assetType);
    else if (action === 'rename-library-group') await renameLibraryGroupDialog(node.dataset.id, node.dataset.name);
    else if (action === 'delete-library-group') await deleteLibraryGroup(node.dataset.id, node.dataset.name, Number(node.dataset.count || 0));
    else if (action === 'move-library-group-up') await reorderLibraryGroup(node.dataset.id, -1);
    else if (action === 'move-library-group-down') await reorderLibraryGroup(node.dataset.id, 1);
    else if (action === 'library-move-asset') await moveLibraryAssets(node.dataset.assetType, [node.dataset.assetId], node.dataset.groupId || null);
    else if (action === 'library-batch-move') {
      const assetType = node.dataset.assetType;
      const groupId = $('libraryBatchTarget')?.value || null;
      const assetIds = [...state.librarySelectedAssets].filter(key => key.startsWith(`${assetType}:`)).map(key => key.slice(assetType.length + 1));
      if (assetIds.length) await moveLibraryAssets(assetType, assetIds, groupId);
    }
    else if (action === 'library-clear-selection') { state.librarySelectedAssets.clear(); await refreshCurrentLibraryTab(); }
  } catch (error) { notify(error.message, true); }
});

document.addEventListener('change', event => {
  const checkbox = event.target.closest('[data-library-select]');
  if (!checkbox) return;
  const key = checkbox.dataset.librarySelect;
  if (checkbox.checked) state.librarySelectedAssets.add(key);
  else state.librarySelectedAssets.delete(key);
  refreshCurrentLibraryTab();
});

async function newLibraryGroupDialog(assetType) {
  const name = window.prompt('New Group name');
  if (!name || !name.trim()) return;
  await api('/api/library/groups', {method: 'POST', body: JSON.stringify({asset_type: assetType, name: name.trim()})});
  await ensureLibraryGroups(assetType, true);
  await refreshCurrentLibraryTab();
  notify(`Group "${name.trim()}" created.`);
}

async function renameLibraryGroupDialog(groupId, currentName) {
  const name = window.prompt('Rename Group', currentName);
  if (!name || !name.trim() || name.trim() === currentName) return;
  const assetType = state.libraryTab.toUpperCase();
  await api(`/api/library/groups/${encodeURIComponent(groupId)}`, {method: 'PATCH', body: JSON.stringify({name: name.trim()})});
  await ensureLibraryGroups(assetType, true);
  await refreshCurrentLibraryTab();
  notify(`Group renamed to "${name.trim()}".`);
}

async function deleteLibraryGroup(groupId, name, assetCount) {
  const message = assetCount
    ? `Delete "${name}"? ${assetCount} asset${assetCount === 1 ? '' : 's'} will be moved to Ungrouped. Assets will not be deleted.`
    : `Delete "${name}"?`;
  if (!window.confirm(message)) return;
  const assetType = state.libraryTab.toUpperCase();
  await api(`/api/library/groups/${encodeURIComponent(groupId)}`, {method: 'DELETE'});
  if (state.libraryActiveGroup[assetType] === groupId) state.libraryActiveGroup[assetType] = '';
  await ensureLibraryGroups(assetType, true);
  await refreshCurrentLibraryTab();
  notify(`Group "${name}" deleted.`);
}

async function reorderLibraryGroup(groupId, direction) {
  const assetType = state.libraryTab.toUpperCase();
  const groups = state.libraryGroupsByType[assetType] || [];
  const index = groups.findIndex(group => group.group_id === groupId);
  const targetIndex = index + direction;
  if (index < 0 || targetIndex < 0 || targetIndex >= groups.length) return;
  const ordered = groups.map(group => group.group_id);
  [ordered[index], ordered[targetIndex]] = [ordered[targetIndex], ordered[index]];
  await api('/api/library/groups/reorder', {method: 'POST', body: JSON.stringify({asset_type: assetType, group_ids: ordered})});
  await ensureLibraryGroups(assetType, true);
  await refreshCurrentLibraryTab();
}

async function moveLibraryAssets(assetType, assetIds, groupId) {
  await api('/api/library/groups/move-assets', {method: 'POST', body: JSON.stringify({asset_type: assetType, asset_ids: assetIds, group_id: groupId})});
  await ensureLibraryGroups(assetType, true);
  state.librarySelectedAssets.clear();
  await refreshCurrentLibraryTab();
  notify(assetIds.length > 1 ? `${assetIds.length} assets moved.` : 'Asset moved.');
}

$('refreshButton').addEventListener('click', () => loadBase().then(() => notify('Data refreshed.')).catch(error => notify(error.message, true)));
$('closeEditorDialog').addEventListener('click', closeDialog);
$('closeDrawer').addEventListener('click', closeDrawer);
$('drawerScrim').addEventListener('click', closeDrawer);

loadBase().then(() => {
  if (state.surface === 'research-detail') openRequestedClone();
}).catch(error => notify(`Unable to load: ${error.message}`, true));
