const settingsForm = document.getElementById("settingsForm");
const settingsMessage = document.getElementById("settingsMessage");
const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanes = document.querySelectorAll(".tab-pane");
let dataSourceState = null;
let openbbSettingsBaseline = "";
const SECRET_MASK = "********";
const OPENBB_PROVIDER_BY_CREDENTIAL = {
  polygon_api_key: "polygon",
  tiingo_token: "tiingo",
  fmp_api_key: "fmp",
  intrinio_api_key: "intrinio",
  fred_api_key: "fred",
};

function getEl(id) {
  return document.getElementById(id);
}

function setValue(id, value) {
  const el = getEl(id);
  if (el) {
    el.value = value;
  }
}

function getValue(id, fallback = "") {
  const el = getEl(id);
  return el ? el.value : fallback;
}

function setChecked(id, value) {
  const el = getEl(id);
  if (el) {
    el.checked = Boolean(value);
  }
}

function getChecked(id, fallback = false) {
  const el = getEl(id);
  return el ? Boolean(el.checked) : fallback;
}

function linesToArray(text) {
  return String(text || "")
    .replaceAll(",", "\n")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function arrayToLines(value) {
  return Array.isArray(value) ? value.join("\n") : "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function getSecretInput(field) {
  return Array.from(document.querySelectorAll("[data-secret-field]"))
    .find((input) => input.dataset.secretField === field);
}

function syncSecretToggle(input, visible) {
  const button = input.closest(".secret-control")?.querySelector(".secret-toggle");
  if (!button) return;
  button.setAttribute("aria-pressed", String(visible));
  button.setAttribute("aria-label", visible ? "隐藏参数" : "显示参数");
  button.title = visible ? "隐藏参数" : "显示参数";
}

function setSecretVisibility(input, visible) {
  if (input.tagName === "TEXTAREA") {
    input.classList.toggle("secret-concealed", !visible);
  } else {
    input.type = visible ? "text" : "password";
  }
  syncSecretToggle(input, visible);
}

function setSecretFieldState(field, configured) {
  const input = getSecretInput(field);
  if (!input) return;
  input.value = "";
  input.dataset.secretConfigured = String(Boolean(configured));
  input.dataset.secretLoaded = "false";
  input.placeholder = configured ? SECRET_MASK : (input.dataset.emptyPlaceholder || "");
  setSecretVisibility(input, false);
}

async function toggleSecretVisibility(input) {
  const button = input.closest(".secret-control")?.querySelector(".secret-toggle");
  const visible = button?.getAttribute("aria-pressed") === "true";
  if (visible) {
    if (input.dataset.secretLoaded === "true") {
      input.value = "";
      input.dataset.secretLoaded = "false";
      input.placeholder = SECRET_MASK;
    }
    setSecretVisibility(input, false);
    return;
  }

  if (!input.value && input.dataset.secretConfigured === "true") {
    if (button) button.disabled = true;
    try {
      const response = await fetchJson("/api/settings/secrets/reveal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ field: input.dataset.secretField }),
      });
      const rawValue = response.data?.value;
      input.value = Array.isArray(rawValue) ? rawValue.join("\n") : String(rawValue || "");
      input.dataset.secretLoaded = "true";
    } catch (error) {
      renderMessage(`参数显示失败: ${error.message}`);
      return;
    } finally {
      if (button) button.disabled = false;
    }
  }

  if (input.value) setSecretVisibility(input, true);
}

function initializeSecretControls() {
  document.querySelectorAll("[data-secret-field]").forEach((input) => {
    input.dataset.emptyPlaceholder = input.placeholder || "";
    input.dataset.secretConfigured = "false";
    const wrapper = document.createElement("div");
    wrapper.className = "secret-control";
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "secret-toggle";
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", "显示参数");
    button.title = "显示参数";
    button.innerHTML = `
      <svg class="secret-eye-on" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"></path>
        <circle cx="12" cy="12" r="2.5"></circle>
      </svg>
      <svg class="secret-eye-off" viewBox="0 0 24 24" aria-hidden="true">
        <path d="m3 3 18 18"></path>
        <path d="M10.6 6.2A9.8 9.8 0 0 1 12 6c6 0 9.5 6 9.5 6a15.7 15.7 0 0 1-2.1 2.8"></path>
        <path d="M6.2 6.2C3.8 8 2.5 12 2.5 12s3.5 6 9.5 6a9.8 9.8 0 0 0 3.3-.6"></path>
      </svg>`;
    button.addEventListener("click", () => toggleSecretVisibility(input));
    input.addEventListener("input", () => {
      input.dataset.secretLoaded = "false";
    });
    wrapper.appendChild(button);
    setSecretVisibility(input, false);
  });
}

function setSelectValue(id, value, fallback = "") {
  const el = getEl(id);
  if (!el) {
    return;
  }
  el.value = value || fallback;
  if (el.value !== (value || fallback)) {
    el.value = fallback;
  }
}

function fillAgentPolicy(agentPolicy) {
  const agent = agentPolicy || {};
  const limits = agent.limits || {};
  const defaults = agent.defaults || {};
  const permissions = agent.permissions || {};
  const eventGraphApproval = agent.event_graph_approval || {};

  setChecked("agentEnabled", agent.enabled !== false);
  setChecked("agentRequireHumanApproval", limits.require_human_approval !== false);
  setChecked("agentAllowMarketOrder", limits.allow_market_order);
  setValue("agentMaxStrategyBudgetUsdc", limits.max_strategy_budget_usdc ?? 100);
  setValue("agentMaxSingleOrderUsdc", limits.max_single_order_usdc ?? 20);
  setValue("agentMaxDailySpendUsdc", limits.max_daily_spend_usdc ?? 150);
  setValue("agentMaxMarketExposureUsdc", limits.max_market_exposure_usdc ?? 50);
  setValue("agentMaxGlobalExposureUsdc", limits.max_global_exposure_usdc ?? 300);
  setValue("agentMaxSlippageBps", limits.max_slippage_bps ?? 100);
  setValue("agentApprovalExpiresMinutes", limits.approval_expires_minutes ?? 1440);
  setValue("agentAllowedVenues", arrayToLines(limits.allowed_venues || ["polymarket"]));
  setValue("agentAllowedMarketIds", arrayToLines(limits.allowed_market_ids || []));

  setValue("agentProposalBudgetUsdc", defaults.proposal_budget_usdc ?? 20);
  setValue("agentProposalSingleOrderUsdc", defaults.proposal_single_order_usdc ?? 5);
  setValue("agentMaxBatchDrafts", defaults.max_batch_drafts ?? 5);
  setSelectValue("agentSelectionMode", defaults.selection_mode || "yes", "yes");
  setValue("agentScanCategories", arrayToLines(defaults.scan_categories || ["Elections Politics", "World", "Geopolitics"]));
  setValue("agentScanSorts", arrayToLines(defaults.scan_sorts || ["volume24h", "volume", "liquidity", "spread"]));
  setSelectValue("eventGraphApprovalMode", eventGraphApproval.mode || "manual", "manual");
  setValue("eventGraphAutoApplyActorId", eventGraphApproval.auto_apply_actor_id || "event_graph_trusted_rule");
  setValue("eventGraphMaxItemsPerRequest", eventGraphApproval.max_items_per_request ?? 100);
  setValue("eventGraphMinConfidence", eventGraphApproval.min_confidence ?? 0);
  setChecked("eventGraphRequireEvidenceSummary", eventGraphApproval.require_evidence_summary);

  document.querySelectorAll("[data-agent-permission]").forEach((input) => {
    input.checked = permissions[input.dataset.agentPermission] !== false;
  });
}

function collectAgentPermissions() {
  const permissions = {};
  document.querySelectorAll("[data-agent-permission]").forEach((input) => {
    permissions[input.dataset.agentPermission] = Boolean(input.checked);
  });
  return permissions;
}

function buildAgentPolicyPayload() {
  return {
    enabled: getChecked("agentEnabled", true),
    permissions: collectAgentPermissions(),
    limits: {
      max_strategy_budget_usdc: getValue("agentMaxStrategyBudgetUsdc"),
      max_single_order_usdc: getValue("agentMaxSingleOrderUsdc"),
      max_daily_spend_usdc: getValue("agentMaxDailySpendUsdc"),
      max_market_exposure_usdc: getValue("agentMaxMarketExposureUsdc"),
      max_global_exposure_usdc: getValue("agentMaxGlobalExposureUsdc"),
      max_slippage_bps: getValue("agentMaxSlippageBps"),
      allowed_market_ids: linesToArray(getValue("agentAllowedMarketIds")),
      allowed_venues: linesToArray(getValue("agentAllowedVenues")) || ["polymarket"],
      allow_market_order: getChecked("agentAllowMarketOrder"),
      require_human_approval: getChecked("agentRequireHumanApproval", true),
      approval_expires_minutes: getValue("agentApprovalExpiresMinutes"),
    },
    defaults: {
      scan_categories: linesToArray(getValue("agentScanCategories")),
      scan_sorts: linesToArray(getValue("agentScanSorts")),
      proposal_budget_usdc: getValue("agentProposalBudgetUsdc"),
      proposal_single_order_usdc: getValue("agentProposalSingleOrderUsdc"),
      max_batch_drafts: getValue("agentMaxBatchDrafts"),
      selection_mode: getValue("agentSelectionMode", "yes"),
    },
    event_graph_approval: {
      mode: getValue("eventGraphApprovalMode", "manual"),
      auto_apply_actor_id: getValue("eventGraphAutoApplyActorId", "event_graph_trusted_rule").trim(),
      max_items_per_request: getValue("eventGraphMaxItemsPerRequest"),
      min_confidence: getValue("eventGraphMinConfidence"),
      require_evidence_summary: getChecked("eventGraphRequireEvidenceSummary"),
    },
  };
}

function fillLlmSettings(settings) {
  const llm = settings.llm_settings || {};
  setChecked("llmEnabled", Boolean(llm.enabled));
  setSelectValue("llmProvider", llm.provider || "dashscope_openai_compatible", "dashscope_openai_compatible");
  setValue("llmBaseUrl", llm.base_url || "https://dashscope.aliyuncs.com/compatible-mode/v1");
  setValue("llmModel", llm.model || "qwen-plus");
  setSecretFieldState("llm_api_key", settings.has_llm_api_key);
  getEl("llmKeyStatus").textContent = settings.has_llm_api_key ? "已加密保存；留空会保留" : "未配置";
  setChecked("clearLlmApiKey", false);
  setValue("llmTemperature", llm.temperature ?? 0.2);
  setValue("llmMaxTokens", llm.max_tokens ?? 2048);
  setValue("llmTimeoutSec", llm.timeout_sec ?? 60);
}

function buildLlmSettingsPayload() {
  return {
    enabled: getChecked("llmEnabled"),
    provider: getValue("llmProvider", "dashscope_openai_compatible"),
    base_url: getValue("llmBaseUrl").trim(),
    model: getValue("llmModel").trim(),
    temperature: getValue("llmTemperature"),
    max_tokens: getValue("llmMaxTokens"),
    timeout_sec: getValue("llmTimeoutSec"),
  };
}

function fillOpenbbSettings(settings) {
  const openbb = settings.openbb_settings || {};
  setChecked("openbbEnabled", Boolean(openbb.enabled));
  setValue("openbbBaseUrl", openbb.base_url || "http://127.0.0.1:6901");
  setValue("openbbDefaultProvider", openbb.default_provider || "yfinance");
  setValue("openbbAllowedProviders", arrayToLines(openbb.allowed_providers || ["yfinance"]));
  setValue("openbbTimeoutSec", openbb.timeout_sec ?? 30);
  const credentialStatus = settings.openbb_provider_credential_status || {};
  document.querySelectorAll("[data-openbb-credential]").forEach((input) => {
    const key = input.dataset.openbbCredential;
    const configured = Boolean(credentialStatus[key]) || (key === "fred_api_key" && settings.has_openbb_fred_api_key);
    setSecretFieldState(input.dataset.secretField, configured);
  });
  document.querySelectorAll("[data-openbb-credential-status]").forEach((target) => {
    const key = target.dataset.openbbCredentialStatus;
    const configured = Boolean(credentialStatus[key]) || (key === "fred_api_key" && settings.has_openbb_fred_api_key);
    target.textContent = configured ? "已加密保存；留空会保留" : "未配置";
  });
  document.querySelectorAll("[data-clear-openbb-credential]").forEach((input) => {
    input.checked = false;
  });
  openbbSettingsBaseline = JSON.stringify(buildOpenbbSettingsPayload());
}

function buildOpenbbSettingsPayload() {
  return {
    enabled: getChecked("openbbEnabled"),
    base_url: getValue("openbbBaseUrl", "http://127.0.0.1:6901").trim(),
    default_provider: getValue("openbbDefaultProvider", "yfinance").trim().toLowerCase(),
    allowed_providers: linesToArray(getValue("openbbAllowedProviders")).map((item) => item.toLowerCase()),
    timeout_sec: getValue("openbbTimeoutSec", "30"),
  };
}

function collectOpenbbCredentials() {
  const credentials = {};
  document.querySelectorAll("[data-openbb-credential]").forEach((input) => {
    const value = input.value.trim();
    if (value) credentials[input.dataset.openbbCredential] = value;
  });
  return credentials;
}

function collectClearedOpenbbCredentials() {
  return Array.from(document.querySelectorAll("[data-clear-openbb-credential]:checked"))
    .map((input) => input.dataset.clearOpenbbCredential);
}

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function renderMessage(text) {
  settingsMessage.textContent = text;
}

function fillForm(settings) {
  setSecretFieldState("finnhub_api_keys", Boolean(settings.finnhub_api_key_count));
  setSecretFieldState("active_finnhub_api_key", settings.has_active_finnhub_api_key);
  getEl("finnhubKeyStatus").textContent = settings.finnhub_api_key_count ? `已加密保存 ${settings.finnhub_api_key_count} 个；留空会保留` : "未配置";
  setChecked("clearFinnhubKeys", false);
  setValue("walletAddresses", (settings.wallet_addresses || []).join("\n"));
  setValue("sqliteDbPath", settings.sqlite_db_path || "");
  setValue("orderListDbPath", settings.order_list_db_path || "");
  setValue("strategyMonitoringDbPath", settings.strategy_monitoring_db_path || "");
  setValue("marketRealtimeDbPath", settings.market_realtime_db_path || "");
  setValue("polymarketDictionaryDbPath", settings.polymarket_dictionary_db_path || "");
  setValue("strategyMetricsDbDir", settings.strategy_metrics_db_dir || "");
  setValue("historyDataRoot", settings.history_data_root || "");
  setValue("cryptoSymbols", (settings.crypto_symbols || []).join("\n"));
  setValue("financeSymbols", (settings.finance_symbols || []).join("\n"));
  setValue("cryptoRefreshSec", settings.crypto_refresh_sec || 15);
  setValue("financeRefreshSec", settings.finance_refresh_sec || 20);
  setValue("uiRefreshSec", settings.ui_refresh_sec || 5);
  setSecretFieldState("coingecko_api_key", settings.has_coingecko_api_key);
  getEl("coingeckoKeyStatus").textContent = settings.has_coingecko_api_key ? "已加密保存；留空会保留" : "未配置";
  setChecked("clearCoingeckoApiKey", false);
  setValue("coingeckoApiHeader", settings.coingecko_api_key_header || "x-cg-demo-api-key");
  fillLlmSettings(settings);
  fillOpenbbSettings(settings);
  setChecked("includeCryptoFundamentals", settings.include_crypto_fundamentals);
  fillAgentPolicy(settings.agent_policy || {});
}

async function loadSettings() {
  renderMessage("加载设置中...");
  const response = await fetchJson("/api/settings");
  fillForm(response.data || {});
  renderMessage("设置已加载。");
}

const HISTORY_ASSET_LABELS = {
  crypto_spot: "加密货币现货",
  equity: "美股",
  polymarket_binary: "Polymarket",
};

const HISTORY_DATA_TYPE_LABELS = {
  bars: "OHLCV K线",
  price_history: "价格历史",
};

const HISTORY_SOURCE_LABELS = {
  BINANCE: "Binance",
  "LOCAL/DAILY_SNAPSHOTS": "本地日快照（已规范化子集）",
  "OPENBB/YFINANCE": "OpenBB / YFinance",
  polymarket_clob: "Polymarket CLOB",
};

function formatHistoryCoverageDate(value) {
  const text = String(value || "").trim();
  return text ? text.slice(0, 10) : "—";
}

function formatLocalSnapshotEnd(value, source) {
  if (source !== "LOCAL/DAILY_SNAPSHOTS") return formatHistoryCoverageDate(value);
  const text = String(value || "").trim();
  if (!text) return "—";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return formatHistoryCoverageDate(value);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

function formatHistoryCount(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function aggregateHistoryCoverage(rows) {
  const groups = new Map();
  (Array.isArray(rows) ? rows : [])
    .filter((row) => String(row.status || "").toUpperCase() === "READY")
    .forEach((row) => {
      const instrumentParts = String(row.instrument_id || "").split(":");
      const assetClass = instrumentParts[0] || "other";
      const venue = instrumentParts[1] || "";
      const source = String(row.source || "UNKNOWN");
      const dataType = String(row.data_type || "unknown");
      const frequency = String(row.frequency || "—");
      const key = [assetClass, source, dataType, frequency, venue].join("|");
      if (!groups.has(key)) {
        groups.set(key, {
          assetClass,
          venue,
          source,
          dataType,
          frequency,
          datasets: 0,
          instruments: new Set(),
          symbols: new Set(),
          rows: 0,
          start: "",
          end: "",
          qualityPass: 0,
        });
      }
      const group = groups.get(key);
      group.datasets += 1;
      group.instruments.add(String(row.instrument_id || row.dataset_id || "unknown"));
      group.symbols.add(instrumentParts.at(-1) || String(row.instrument_id || "unknown"));
      group.rows += Number(row.row_count || 0);
      const start = String(row.start_time || "");
      const end = String(row.end_time || "");
      if (start && (!group.start || start < group.start)) group.start = start;
      if (end && (!group.end || end > group.end)) group.end = end;
      if (String(row.quality_status || "").toUpperCase() === "PASS") group.qualityPass += 1;
    });
  return Array.from(groups.values()).sort((left, right) => (
    `${left.assetClass}|${left.source}|${left.frequency}`.localeCompare(
      `${right.assetClass}|${right.source}|${right.frequency}`
    )
  ));
}

function renderNormalizedHistoryCoverage(rows) {
  const grid = getEl("historyNormalizedGrid");
  const meta = getEl("historyNormalizedMeta");
  if (!grid || !meta) return;
  const groups = aggregateHistoryCoverage(rows);
  const readyDatasetCount = groups.reduce((total, group) => total + group.datasets, 0);
  meta.textContent = `${groups.length} 类 · ${readyDatasetCount} 个 READY 数据集`;
  grid.innerHTML = groups.map((group) => {
    const assetLabel = HISTORY_ASSET_LABELS[group.assetClass] || group.assetClass;
    const dataTypeLabel = HISTORY_DATA_TYPE_LABELS[group.dataType] || group.dataType;
    const sourceLabel = HISTORY_SOURCE_LABELS[group.source] || group.source;
    const qualityClass = group.qualityPass === group.datasets ? "pass" : "partial";
    const qualityLabel = group.qualityPass === group.datasets ? "PASS" : `${group.qualityPass}/${group.datasets} PASS`;
    const isLocalSubset = group.source === "LOCAL/DAILY_SNAPSHOTS";
    const isMarketWideCollection = ["CRSP/CIZ", "SEC/COMPANYFACTS"].includes(group.source);
    const symbols = Array.from(group.symbols).sort().join("、");
    return `
      <article class="history-coverage-card${isLocalSubset ? " subset" : ""}">
        <div class="history-coverage-card-head">
          <span class="history-coverage-type">${escapeHtml(assetLabel)}</span>
          <span class="history-coverage-quality ${qualityClass}">${escapeHtml(qualityLabel)}</span>
        </div>
        <h4>${escapeHtml(dataTypeLabel)} · ${escapeHtml(group.frequency)}</h4>
        <p>${escapeHtml(sourceLabel)}${group.venue ? ` · ${escapeHtml(group.venue)}` : ""}</p>
        <div class="history-coverage-window">
          <span>覆盖范围</span>
          <strong>${escapeHtml(formatHistoryCoverageDate(group.start))} <i>→</i> ${escapeHtml(formatLocalSnapshotEnd(group.end, group.source))}</strong>
        </div>
        <div class="history-coverage-stats">
          ${isMarketWideCollection ? '<span><b>全市场</b> 集合</span>' : `<span><b>${formatHistoryCount(group.instruments.size)}</b> 标的</span>`}
          <span><b>${formatHistoryCount(group.rows)}</b> 行</span>
          <span><b>${formatHistoryCount(group.datasets)}</b> 数据集</span>
        </div>
        ${isLocalSubset ? `<p class="history-coverage-note"><strong>Catalog 子集：</strong>${escapeHtml(symbols)}。这不是原始美股档案的全部标的。</p>` : ""}
        ${isMarketWideCollection ? '<p class="history-coverage-note">该 Manifest 是全市场集合型长表，不按单个 ticker 拆分，不能把 Catalog 中的 1 个集合误读为 1 个标的。</p>' : ""}
      </article>`;
  }).join("") || '<p class="settings-note">Catalog 中暂时没有 READY 历史数据。</p>';
}

function renderArchiveHistoryCoverage(data) {
  const grid = getEl("historyArchiveGrid");
  const meta = getEl("historyArchiveMeta");
  if (!grid || !meta) return;
  const collections = Array.isArray(data.collections) ? data.collections : [];
  if (data.state === "INVENTORY_MISSING") {
    meta.textContent = "尚未建立档案清单";
    grid.innerHTML = '<p class="settings-note">统一目录已配置，但没有找到原始档案 inventory.json。</p>';
    return;
  }
  if (data.state === "UNCONFIGURED") {
    meta.textContent = "目录未配置";
    grid.innerHTML = '<p class="settings-note">请先设置历史数据保存目录。</p>';
    return;
  }
  meta.textContent = `${formatHistoryCount(data.archive_count)} 个压缩档案 · ${formatBytes(data.archive_bytes)}`;
  grid.innerHTML = collections.map((collection) => `
    <article class="history-coverage-card archive${collection.id === "us_equity_daily_snapshots" ? " featured" : ""}">
      <div class="history-coverage-card-head">
        <span class="history-coverage-type">${escapeHtml(collection.asset_class === "equity" ? "美股" : "美股期权")}</span>
        <span class="history-coverage-quality archive">${escapeHtml(collection.status_label || "原始档案")}</span>
      </div>
      <h4>${escapeHtml(collection.title)}</h4>
      <p>${escapeHtml(collection.data_type)}</p>
      <div class="history-coverage-window">
        <span>原始文件覆盖范围</span>
        <strong>${escapeHtml(formatHistoryCoverageDate(collection.start))} <i>→</i> ${escapeHtml(formatHistoryCoverageDate(collection.end))}</strong>
      </div>
      <div class="history-coverage-stats">
        ${(collection.metrics || []).map((metric) => `<span><b>${formatHistoryCount(metric.value)}</b> ${escapeHtml(metric.label)}</span>`).join("")}
      </div>
      <p class="history-coverage-note">${escapeHtml(collection.note || "")}</p>
    </article>`).join("") || '<p class="settings-note">档案清单中没有可显示的数据集合。</p>';
}

async function loadHistoryDataCoverage() {
  const [archiveResponse, catalogResponse] = await Promise.all([
    fetchJson("/api/history/storage/coverage", { cache: "no-store" }),
    fetchJson("/api/research/data/catalog", { cache: "no-store" }),
  ]);
  const archiveData = archiveResponse.data || {};
  const catalogRows = catalogResponse.data || [];
  renderArchiveHistoryCoverage(archiveData);
  renderNormalizedHistoryCoverage(catalogRows);
  const meta = getEl("historyCoverageMeta");
  if (meta) {
    const rawCount = Array.isArray(archiveData.collections) ? archiveData.collections.length : 0;
    const readyCount = (Array.isArray(catalogRows) ? catalogRows : [])
      .filter((row) => String(row.status || "").toUpperCase() === "READY").length;
    meta.textContent = `${rawCount} 类原始档案 · ${readyCount} 个 READY 数据集`;
  }
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / (1024 ** index)).toFixed(index >= 3 ? 2 : 1)} ${units[index]}`;
}

function historyStoragePayload() {
  return {
    root: getValue("historyDataRoot").trim(),
    source_roots: linesToArray(getValue("historyDataSourceRoots")),
  };
}

const HISTORY_STORAGE_PHASE_LABELS = {
  PREPARING: "准备迁移清单",
  CREATING_LAYOUT: "创建规范目录",
  COPYING_WORKSPACE: "复制历史工作库",
  COPYING_RESEARCH: "复制 Research 数据",
  COPYING_BACKTESTS: "复制回测与策略产物",
  COPYING_SOURCE_ARCHIVE: "复制原始历史档案",
  FINAL_SYNC: "最终同步与一致性校验",
  READY: "迁移完成",
  FAILED: "迁移失败",
};

function historyStoragePhaseLabel(value) {
  const phase = String(value || "").toUpperCase();
  return HISTORY_STORAGE_PHASE_LABELS[phase] || phase.replaceAll("_", " ") || "等待开始";
}

function renderHistoryStorageStatus(data) {
  const target = getEl("historyStorageStatus");
  if (!target) return;
  if (!getValue("historyDataSourceRoots").trim() && (data.suggested_source_roots || []).length) {
    setValue("historyDataSourceRoots", arrayToLines(data.suggested_source_roots));
  }
  const job = data.job || {};
  const progress = job.total_bytes > 0
    ? Math.min(100, Math.round((Number(job.copied_bytes || 0) / Number(job.total_bytes)) * 100))
    : 0;
  const status = String(job.status || "").toUpperCase();
  const normalizeButton = getEl("normalizeHistoryStorage");
  if (normalizeButton) {
    const waitingForRestart = Boolean(data.restart_required) && status !== "RUNNING";
    normalizeButton.disabled = status === "RUNNING" || waitingForRestart;
    normalizeButton.textContent = status === "RUNNING"
      ? "规范化进行中"
      : (waitingForRestart ? "等待重启启用" : (data.active_root ? "重新规范化" : "开始规范化"));
  }
  const configuredRoot = data.configured_root || "尚未设置";
  const targetWorkspace = data.configured_root
    ? `${String(data.configured_root).replace(/[\\/]+$/, "")}\\workspace\\history_workspace.db`
    : "尚未生成";
  let tone = "idle";
  let badge = "尚未配置";
  let title = "请选择 History Data 文件夹";
  let explanation = "设置统一目录后，可以先检查迁移规模，再开始复制与校验。";
  if (status === "RUNNING") {
    tone = "running";
    const isIncrementalCheck = String(data.state || "").toUpperCase() === "READY";
    badge = isIncrementalCheck ? "正在执行增量复检" : "正在复制与校验";
    title = isIncrementalCheck ? `正在重新核对 ${configuredRoot}` : `正在迁移到 ${configuredRoot}`;
    explanation = isIncrementalCheck
      ? "E 盘已经生成 READY 数据快照；当前正在复检新增或变化的文件。复检完成前请勿重启 DataTube。"
      : "迁移完成并安全重启前，DataTube 会继续使用原 G 盘历史库；这属于正常的保护机制。请勿在当前阶段重启。";
  } else if (status === "FAILED") {
    tone = "failed";
    badge = "迁移失败";
    title = "历史数据迁移未完成";
    explanation = "原文件和当前历史库仍然保留，可根据错误信息排查后重新执行。";
  } else if (data.restart_required || status === "SUCCEEDED") {
    tone = "ready";
    badge = "迁移完成，等待重启";
    title = `数据已安全复制到 ${configuredRoot}`;
    explanation = "请在确认没有运行中的任务后重启 DataTube，重启后所有历史服务才会正式使用 E 盘目录。";
  } else if (data.active_root) {
    tone = "active";
    badge = "已启用";
    title = `History Data 已使用 ${data.active_root}`;
    explanation = "历史工作库和规范化后的历史数据服务已从统一目录读取。";
  } else if (data.configured_root) {
    tone = "configured";
    badge = "目录已配置";
    title = `目标目录：${configuredRoot}`;
    explanation = "目录设置已保存，尚未完成复制、校验和启用。";
  }
  const migrationDone = status === "SUCCEEDED" || Boolean(data.restart_required) || Boolean(data.active_root);
  const lifecycle = `
    <div class="history-storage-lifecycle" aria-label="History Data 启用流程">
      <div class="done"><span>1</span><strong>目录已配置</strong><small>${escapeHtml(configuredRoot)}</small></div>
      <div class="${status === "RUNNING" ? "active" : (migrationDone ? "done" : "pending")}"><span>2</span><strong>复制并校验</strong><small>${escapeHtml(historyStoragePhaseLabel(job.phase))}</small></div>
      <div class="${status === "RUNNING" ? "pending" : (data.restart_required ? "active" : (data.active_root ? "done" : "pending"))}"><span>3</span><strong>安全重启后启用</strong><small>${data.restart_required ? "复检结束后即可重启" : (data.active_root ? "已完成切换" : "尚未切换运行路径")}</small></div>
    </div>`;
  const jobBlock = job.job_id ? `
    <div class="history-storage-job ${escapeHtml(String(job.status || "").toLowerCase())}">
      <div class="history-storage-progress-head">
        <div><span>当前阶段</span><strong>${escapeHtml(historyStoragePhaseLabel(job.phase))}</strong></div>
        <strong class="history-storage-percent">${progress}%</strong>
      </div>
      <div class="data-source-progress history-storage-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><span style="width:${progress}%"></span></div>
      <div class="history-storage-progress-meta">
        <span>${escapeHtml(job.copied_files || 0)} / ${escapeHtml(job.total_files || 0)} 个文件</span>
        <span>${escapeHtml(formatBytes(job.copied_bytes))} / ${escapeHtml(formatBytes(job.total_bytes))}</span>
      </div>
      ${job.error ? `<p class="history-storage-error">${escapeHtml(job.error)}</p>` : ""}
    </div>` : "";
  target.innerHTML = `
    <div class="history-storage-hero ${tone}">
      <div>
        <span class="history-storage-badge">${escapeHtml(badge)}</span>
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(explanation)}</p>
      </div>
      ${job.job_id ? `
        <div class="history-storage-hero-meter" style="--history-progress:${progress}%">
          <div><strong>${progress}%</strong><span>本次任务</span></div>
        </div>` : ""}
    </div>
    ${lifecycle}
    <div class="history-storage-paths">
      <div><span>当前正在使用（迁移期间）</span><strong class="mono">${escapeHtml(data.runtime_workspace_db || "-")}</strong></div>
      <div><span>迁移目标（完成后）</span><strong class="mono">${escapeHtml(targetWorkspace)}</strong></div>
    </div>
    ${jobBlock}`;
}

function renderHistoryStoragePlan(data) {
  const target = getEl("historyStoragePlan");
  if (!target) return;
  target.hidden = false;
  target.innerHTML = `
    <div class="history-storage-plan-head">
      <div><span>需要复制</span><strong>${escapeHtml(formatBytes(data.total_bytes))}</strong></div>
      <div><span>文件数</span><strong>${escapeHtml(data.total_files || 0)}</strong></div>
      <div><span>目标盘可用</span><strong>${escapeHtml(formatBytes(data.free_bytes))}</strong></div>
      <div><span>空间检查</span><strong class="${data.enough_space ? "good" : "bad"}">${data.enough_space ? "通过" : "空间不足"}</strong></div>
    </div>
    <div class="history-storage-entry-list">
      ${(data.entries || []).map((entry) => `
        <div><span>${escapeHtml(entry.kind)}</span><code>${escapeHtml(entry.source)}</code><strong>${escapeHtml(formatBytes(entry.bytes))}</strong></div>
      `).join("") || '<p class="settings-note">没有发现需要复制的历史数据。</p>'}
    </div>
    <p class="settings-note">执行方式：复制并校验。原目录会保留，规范化成功后重启 DataTube 才会切换。</p>`;
}

async function loadHistoryStorageStatus() {
  const response = await fetchJson("/api/history/storage", { cache: "no-store" });
  const data = response.data || {};
  renderHistoryStorageStatus(data);
  const job = data.job || {};
  if (job.status === "RUNNING") {
    if (!historyStoragePollTimer) {
      historyStoragePollTimer = window.setInterval(() => {
        loadHistoryStorageStatus().catch(() => {});
      }, 1500);
    }
  } else if (historyStoragePollTimer) {
    window.clearInterval(historyStoragePollTimer);
    historyStoragePollTimer = null;
  }
  return data;
}

async function inspectHistoryStorage(button = getEl("inspectHistoryStorage")) {
  setBusy(button, true, "正在检查...");
  try {
    const response = await fetchJson("/api/history/storage/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(historyStoragePayload()),
    });
    renderHistoryStoragePlan(response.data || {});
    renderMessage("History Data 目录检查完成。");
    return response.data || {};
  } catch (error) {
    renderMessage(`History Data 检查失败: ${error.message}`);
    throw error;
  } finally {
    setBusy(button, false);
  }
}

async function normalizeHistoryStorage(button = getEl("normalizeHistoryStorage")) {
  const plan = await inspectHistoryStorage(getEl("inspectHistoryStorage"));
  if (!plan.enough_space) throw new Error("目标磁盘空间不足");
  const accepted = window.confirm(
    `将复制并校验 ${formatBytes(plan.total_bytes)} 历史数据到\n${plan.root}\n\n原文件不会删除。现在开始吗？`
  );
  if (!accepted) return;
  setBusy(button, true, "正在启动...");
  try {
    const response = await fetchJson("/api/history/storage/normalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(historyStoragePayload()),
    });
    renderMessage("History Data 规范化任务已在后台启动，可以继续使用设置页查看进度。");
    await loadHistoryStorageStatus();
  } catch (error) {
    renderMessage(`History Data 规范化启动失败: ${error.message}`);
    throw error;
  } finally {
    setBusy(button, false);
  }
}

const DATA_SOURCE_STATUS_LABELS = {
  ready: "Ready",
  disabled: "Disabled",
  activation_required: "等待启用",
  not_installed: "Not installed",
  credential_required: "Credential required",
  unavailable: "Unavailable",
};

function setDataSourceOperation({ title, detail, progress = 10, state = "loading" }) {
  const panel = getEl("dataSourceOperation");
  if (!panel) return;
  panel.hidden = false;
  panel.classList.toggle("success", state === "success");
  panel.classList.toggle("error", state === "error");
  getEl("dataSourceOperationTitle").textContent = title;
  getEl("dataSourceOperationDetail").textContent = detail;
  getEl("dataSourceOperationProgress").style.width = `${Math.max(0, Math.min(100, progress))}%`;
}

function setBusy(button, busy, busyText = "处理中...") {
  if (!button) return;
  if (busy) {
    button.dataset.idleText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.idleText || button.textContent;
    button.disabled = false;
  }
}

function renderDataSourceSummary(data) {
  const summary = data.summary || {};
  const target = getEl("dataSourceSummary");
  if (!target) return;
  target.innerHTML = [
    ["Data Sources", summary.total || 0],
    ["Configured", summary.configured || 0],
    ["Ready", summary.ready || 0],
    ["Historical", summary.formal_historical || 0],
  ].map(([label, value]) => `
    <div class="data-source-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
  `).join("");
}

function renderDataSourceCatalog(data) {
  const target = getEl("dataSourceCatalog");
  if (!target) return;
  target.innerHTML = (data.sources || []).map((source) => {
    const status = source.runtime_status || "unavailable";
    const capabilities = (source.capability_keys || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
    const credentialText = (source.credential_keys || []).length
      ? (source.credential_loaded === true
        ? "Credential loaded"
        : (source.credential_configured ? "Credential encrypted" : "Credential missing"))
      : "No credential required";
    const action = source.can_activate
      ? `<button type="button" data-activate-openbb="${escapeHtml(source.provider_id.toLowerCase())}">启用并加载</button>`
      : "";
    return `
      <article class="data-source-card" data-source-id="${escapeHtml(source.source_id)}">
        <div class="data-source-card-head">
          <div><span class="data-source-gateway">${escapeHtml(source.gateway)}</span><h3>${escapeHtml(source.label)}</h3></div>
          <span class="data-source-status ${escapeHtml(status)}">${escapeHtml(DATA_SOURCE_STATUS_LABELS[status] || status)}</span>
        </div>
        <p>${escapeHtml(source.description || "")}</p>
        <p class="data-source-card-detail">${escapeHtml(source.status_detail || "")}</p>
        <div class="data-source-tags">${capabilities || "<span>Context only</span>"}</div>
        <div class="data-source-meta"><span>${escapeHtml(credentialText)}</span><span>${source.installed === false ? "Extension missing" : "Extension available"}</span></div>
        ${action ? `<div class="data-source-card-actions">${action}</div>` : ""}
      </article>
    `;
  }).join("");
  target.querySelectorAll("[data-activate-openbb]").forEach((button) => {
    button.addEventListener("click", () => activateOpenbbProvider(button.dataset.activateOpenbb, button));
  });
}

async function activateOpenbbProvider(providerId, button) {
  setBusy(button, true, "正在加载...");
  setDataSourceOperation({
    title: `正在启用 OpenBB · ${providerId.toUpperCase()}`,
    detail: "第 1/3 步：检查已加密凭据与 Provider 扩展。",
    progress: 20,
  });
  try {
    setDataSourceOperation({
      title: `正在启用 OpenBB · ${providerId.toUpperCase()}`,
      detail: "第 2/3 步：写入允许列表，重启 OpenBB，并注入凭据。通常需要几秒钟。",
      progress: 62,
    });
    const response = await fetchJson("/api/data-sources/openbb/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider_id: providerId }),
    });
    renderDataSources(response.data.data_sources || {});
    const settings = await fetchJson("/api/settings");
    fillForm(settings.data || {});
    await loadOpenbbRuntimeStatus();
    setDataSourceOperation({
      title: `OpenBB · ${providerId.toUpperCase()} 已加载`,
      detail: "第 3/3 步：OpenBB 健康检查通过，凭据已注入当前进程。",
      progress: 100,
      state: "success",
    });
  } catch (error) {
    setDataSourceOperation({
      title: `OpenBB · ${providerId.toUpperCase()} 加载失败`,
      detail: error.message,
      progress: 100,
      state: "error",
    });
    setBusy(button, false);
  }
}

async function reloadOpenbbGateway(button = getEl("reloadOpenbbGateway")) {
  setBusy(button, true, "正在重新加载...");
  setDataSourceOperation({
    title: "正在重新加载 OpenBB",
    detail: "正在停止旧进程、注入已保存凭据并等待健康检查。",
    progress: 58,
  });
  try {
    const response = await fetchJson("/api/data-sources/openbb/reload", { method: "POST" });
    renderDataSources(response.data.data_sources || {});
    await loadOpenbbRuntimeStatus();
    setDataSourceOperation({
      title: "OpenBB 已重新加载",
      detail: "服务健康检查通过，当前已保存凭据已注入新进程。",
      progress: 100,
      state: "success",
    });
    return response;
  } catch (error) {
    setDataSourceOperation({
      title: "OpenBB 重新加载失败",
      detail: error.message,
      progress: 100,
      state: "error",
    });
    throw error;
  } finally {
    setBusy(button, false);
  }
}

function moveRoutingItem(item, direction) {
  const list = item.parentElement;
  if (direction < 0 && item.previousElementSibling) {
    list.insertBefore(item, item.previousElementSibling);
  } else if (direction > 0 && item.nextElementSibling) {
    list.insertBefore(item.nextElementSibling, item);
  }
}

function bindRoutingInteractions() {
  document.querySelectorAll(".data-source-route-item").forEach((item) => {
    item.querySelector("[data-route-up]")?.addEventListener("click", () => moveRoutingItem(item, -1));
    item.querySelector("[data-route-down]")?.addEventListener("click", () => moveRoutingItem(item, 1));
    item.addEventListener("dragstart", (event) => {
      item.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", item.dataset.sourceId);
    });
    item.addEventListener("dragend", () => item.classList.remove("dragging"));
  });
  document.querySelectorAll("[data-policy-order]").forEach((list) => {
    list.addEventListener("dragover", (event) => {
      event.preventDefault();
      const dragged = list.querySelector(".dragging");
      if (!dragged) return;
      const siblings = Array.from(list.querySelectorAll(".data-source-route-item:not(.dragging)"));
      const next = siblings.find((item) => event.clientY < item.getBoundingClientRect().top + item.offsetHeight / 2);
      list.insertBefore(dragged, next || null);
    });
  });
}

function renderDataSourceRouting(data) {
  const target = getEl("dataSourceRoutingPolicies");
  if (!target) return;
  setSelectValue("dataSourceMode", data.mode || "HYBRID", "HYBRID");
  target.innerHTML = (data.routing_policies || []).map((policy) => `
    <section class="data-source-policy" data-policy-key="${escapeHtml(policy.policy_key)}">
      <div><h3>${escapeHtml(policy.label)}</h3><code>${escapeHtml(policy.policy_key)}</code></div>
      <ol data-policy-order>
        ${(policy.sources || []).map((source, index) => `
          <li class="data-source-route-item" draggable="true" data-source-id="${escapeHtml(source.source_id)}">
            <span class="route-handle">☰</span><strong>${index + 1}</strong>
            <span class="route-source-name">${escapeHtml(source.label)}</span>
            <span class="data-source-status ${escapeHtml(source.runtime_status)}">${escapeHtml(DATA_SOURCE_STATUS_LABELS[source.runtime_status] || source.runtime_status)}</span>
            <button type="button" class="route-move" data-route-up aria-label="上移">↑</button>
            <button type="button" class="route-move" data-route-down aria-label="下移">↓</button>
          </li>
        `).join("")}
      </ol>
    </section>
  `).join("");
  bindRoutingInteractions();
}

function renderDataSources(data) {
  dataSourceState = data;
  renderDataSourceSummary(data);
  renderDataSourceCatalog(data);
  renderDataSourceRouting(data);
  const message = getEl("dataSourceRoutingMessage");
  if (message) message.textContent = `后端策略版本 ${data.version}`;
}

async function loadDataSources() {
  const response = await fetchJson("/api/data-sources");
  renderDataSources(response.data || {});
}

async function saveDataSourceRouting() {
  if (!dataSourceState) return;
  const priorityOrders = {};
  document.querySelectorAll("[data-policy-key]").forEach((policy) => {
    priorityOrders[policy.dataset.policyKey] = Array.from(policy.querySelectorAll("[data-source-id]"))
      .map((item) => item.dataset.sourceId);
  });
  const message = getEl("dataSourceRoutingMessage");
  if (message) message.textContent = "保存排序中...";
  try {
    const response = await fetchJson("/api/data-sources/routing", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_version: dataSourceState.version,
        mode: getValue("dataSourceMode", "HYBRID"),
        priority_orders: priorityOrders,
      }),
    });
    renderDataSources(response.data || {});
    if (message) message.textContent = `保存成功，后端策略版本 ${response.data.version}`;
  } catch (error) {
    if (message) message.textContent = `保存失败：${error.message}`;
  }
}

async function loadOpenbbRuntimeStatus() {
  const target = getEl("openbbRuntimeStatus");
  if (!target) return;
  try {
    const response = await fetchJson("/api/research/data/providers/openbb/worker-status");
    const data = response.data || {};
    const provider = data.provider || {};
    const counts = (data.worker || {}).counts || {};
    const providerText = provider.enabled ? (provider.ok ? "服务正常" : "服务不可用") : "未启用";
    target.textContent = `OpenBB ${providerText} | READY ${counts.READY || 0} | RUNNING ${counts.RUNNING || 0} | SUCCEEDED ${counts.SUCCEEDED || 0} | FAILED ${counts.FAILED || 0}`;
  } catch (error) {
    target.textContent = `OpenBB 状态读取失败: ${error.message}`;
  }
}

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  renderMessage("保存中...");

  const openbbCredentials = collectOpenbbCredentials();
  const clearedOpenbbCredentials = collectClearedOpenbbCredentials();
  const openbbSettings = buildOpenbbSettingsPayload();
  Object.keys(openbbCredentials).forEach((credentialKey) => {
    const provider = OPENBB_PROVIDER_BY_CREDENTIAL[credentialKey];
    if (provider && !openbbSettings.allowed_providers.includes(provider)) {
      openbbSettings.allowed_providers.push(provider);
    }
  });
  const reloadOpenbb = Boolean(openbbSettings.enabled && (
    Object.keys(openbbCredentials).length
    || clearedOpenbbCredentials.length
    || JSON.stringify(openbbSettings) !== openbbSettingsBaseline
  ));
  const saveButton = getEl("saveSettingsButton");
  setBusy(saveButton, true, "正在保存...");
  if (reloadOpenbb) {
    setDataSourceOperation({
      title: "正在保存 OpenBB 设置",
      detail: "第 1/3 步：加密保存凭据，并更新允许的 Providers。",
      progress: 22,
    });
  }

  const payload = {
    finnhub_api_keys: linesToArray(getValue("finnhubKeys")),
    clear_finnhub_api_keys: getChecked("clearFinnhubKeys"),
    active_finnhub_api_key: getValue("activeFinnhubKey").trim(),
    wallet_addresses: linesToArray(getValue("walletAddresses")),
    sqlite_db_path: getValue("sqliteDbPath").trim(),
    order_list_db_path: getValue("orderListDbPath").trim(),
    strategy_monitoring_db_path: getValue("strategyMonitoringDbPath").trim(),
    market_realtime_db_path: getValue("marketRealtimeDbPath").trim(),
    polymarket_dictionary_db_path: getValue("polymarketDictionaryDbPath").trim(),
    strategy_metrics_db_dir: getValue("strategyMetricsDbDir").trim(),
    history_data_root: getValue("historyDataRoot").trim(),
    crypto_symbols: linesToArray(getValue("cryptoSymbols")),
    finance_symbols: linesToArray(getValue("financeSymbols")),
    crypto_refresh_sec: getValue("cryptoRefreshSec"),
    finance_refresh_sec: getValue("financeRefreshSec"),
    ui_refresh_sec: getValue("uiRefreshSec"),
    coingecko_api_key: getValue("coingeckoApiKey").trim(),
    clear_coingecko_api_key: getChecked("clearCoingeckoApiKey"),
    coingecko_api_key_header: getValue("coingeckoApiHeader").trim(),
    llm_settings: buildLlmSettingsPayload(),
    llm_api_key: getValue("llmApiKey").trim(),
    clear_llm_api_key: getChecked("clearLlmApiKey"),
    openbb_settings: openbbSettings,
    openbb_provider_credentials: openbbCredentials,
    clear_openbb_provider_credentials: clearedOpenbbCredentials,
    include_crypto_fundamentals: getChecked("includeCryptoFundamentals"),
    agent_policy: buildAgentPolicyPayload(),
  };

  try {
    const response = await fetchJson("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    fillForm(response.data || {});
    if (reloadOpenbb) {
      setDataSourceOperation({
        title: "正在加载 OpenBB 凭据",
        detail: "第 2/3 步：重启隔离进程并等待健康检查。通常需要几秒钟。",
        progress: 60,
      });
      await reloadOpenbbGateway(null);
      setDataSourceOperation({
        title: "OpenBB 设置已生效",
        detail: "第 3/3 步：凭据已加密保存、Provider 已启用、OpenBB 健康检查通过。",
        progress: 100,
        state: "success",
      });
    } else {
      await loadDataSources();
    }
    renderMessage("保存成功，新的设置已经生效。");
  } catch (error) {
    renderMessage(`保存失败: ${error.message}`);
    if (reloadOpenbb) {
      setDataSourceOperation({
        title: "OpenBB 设置未完全生效",
        detail: error.message,
        progress: 100,
        state: "error",
      });
    }
  } finally {
    setBusy(saveButton, false);
  }
});

tabButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const tab = button.dataset.tab;
    tabButtons.forEach((item) => item.classList.toggle("active", item === button));
    tabPanes.forEach((pane) => pane.classList.toggle("active", pane.dataset.tabContent === tab));
  });
});

initializeSecretControls();
loadSettings().catch((error) => renderMessage(`加载失败: ${error.message}`));
loadOpenbbRuntimeStatus();
loadHistoryDataCoverage().catch((error) => {
  const archiveGrid = getEl("historyArchiveGrid");
  const normalizedGrid = getEl("historyNormalizedGrid");
  const meta = getEl("historyCoverageMeta");
  if (archiveGrid) archiveGrid.innerHTML = `<p class="history-coverage-error">原始档案读取失败：${escapeHtml(error.message)}</p>`;
  if (normalizedGrid) normalizedGrid.innerHTML = `<p class="history-coverage-error">Catalog 读取失败：${escapeHtml(error.message)}</p>`;
  if (meta) meta.textContent = "读取失败";
});
loadDataSources().catch((error) => {
  const target = getEl("dataSourceRoutingMessage");
  if (target) target.textContent = `Data Source 加载失败：${error.message}`;
});
getEl("saveDataSourceRouting")?.addEventListener("click", saveDataSourceRouting);
getEl("reloadOpenbbGateway")?.addEventListener("click", (event) => {
  reloadOpenbbGateway(event.currentTarget).catch(() => {});
});
