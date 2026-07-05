const settingsForm = document.getElementById("settingsForm");
const settingsMessage = document.getElementById("settingsMessage");
const tabButtons = document.querySelectorAll(".tab-btn");
const tabPanes = document.querySelectorAll(".tab-pane");

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
  setValue("llmApiKey", settings.llm_api_key || "");
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
  setValue("finnhubKeys", (settings.finnhub_api_keys || []).join("\n"));
  setValue("activeFinnhubKey", settings.active_finnhub_api_key || "");
  setValue("walletAddresses", (settings.wallet_addresses || []).join("\n"));
  setValue("sqliteDbPath", settings.sqlite_db_path || "");
  setValue("orderListDbPath", settings.order_list_db_path || "");
  setValue("strategyMonitoringDbPath", settings.strategy_monitoring_db_path || "");
  setValue("marketRealtimeDbPath", settings.market_realtime_db_path || "");
  setValue("polymarketDictionaryDbPath", settings.polymarket_dictionary_db_path || "");
  setValue("strategyMetricsDbDir", settings.strategy_metrics_db_dir || "");
  setValue("cryptoSymbols", (settings.crypto_symbols || []).join("\n"));
  setValue("financeSymbols", (settings.finance_symbols || []).join("\n"));
  setValue("cryptoRefreshSec", settings.crypto_refresh_sec || 15);
  setValue("financeRefreshSec", settings.finance_refresh_sec || 20);
  setValue("uiRefreshSec", settings.ui_refresh_sec || 5);
  setValue("coingeckoApiKey", settings.coingecko_api_key || "");
  setValue("coingeckoApiHeader", settings.coingecko_api_key_header || "x-cg-demo-api-key");
  fillLlmSettings(settings);
  setChecked("includeCryptoFundamentals", settings.include_crypto_fundamentals);
  fillAgentPolicy(settings.agent_policy || {});
}

async function loadSettings() {
  renderMessage("加载设置中...");
  const response = await fetchJson("/api/settings");
  fillForm(response.data || {});
  renderMessage("设置已加载。");
}

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  renderMessage("保存中...");

  const payload = {
    finnhub_api_keys: linesToArray(getValue("finnhubKeys")),
    active_finnhub_api_key: getValue("activeFinnhubKey").trim(),
    wallet_addresses: linesToArray(getValue("walletAddresses")),
    sqlite_db_path: getValue("sqliteDbPath").trim(),
    order_list_db_path: getValue("orderListDbPath").trim(),
    strategy_monitoring_db_path: getValue("strategyMonitoringDbPath").trim(),
    market_realtime_db_path: getValue("marketRealtimeDbPath").trim(),
    polymarket_dictionary_db_path: getValue("polymarketDictionaryDbPath").trim(),
    strategy_metrics_db_dir: getValue("strategyMetricsDbDir").trim(),
    crypto_symbols: linesToArray(getValue("cryptoSymbols")),
    finance_symbols: linesToArray(getValue("financeSymbols")),
    crypto_refresh_sec: getValue("cryptoRefreshSec"),
    finance_refresh_sec: getValue("financeRefreshSec"),
    ui_refresh_sec: getValue("uiRefreshSec"),
    coingecko_api_key: getValue("coingeckoApiKey").trim(),
    coingecko_api_key_header: getValue("coingeckoApiHeader").trim(),
    llm_settings: buildLlmSettingsPayload(),
    llm_api_key: getValue("llmApiKey").trim(),
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
    renderMessage("保存成功，新的设置已经生效。");
  } catch (error) {
    renderMessage(`保存失败: ${error.message}`);
  }
});

tabButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const tab = button.dataset.tab;
    tabButtons.forEach((item) => item.classList.toggle("active", item === button));
    tabPanes.forEach((pane) => pane.classList.toggle("active", pane.dataset.tabContent === tab));
  });
});

loadSettings().catch((error) => renderMessage(`加载失败: ${error.message}`));
