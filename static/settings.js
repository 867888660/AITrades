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
  setChecked("includeCryptoFundamentals", settings.include_crypto_fundamentals);
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
    include_crypto_fundamentals: getChecked("includeCryptoFundamentals"),
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
