const rowId = document.body.dataset.rowId;
const workspaceTitle = document.getElementById("workspaceTitle");
const workspaceSubtitle = document.getElementById("workspaceSubtitle");
const workspaceSummary = document.getElementById("workspaceSummary");
const workspaceChartMeta = document.getElementById("workspaceChartMeta");
const workspaceCharts = document.getElementById("workspaceCharts");
const workspaceEvents = document.getElementById("workspaceEvents");
const workspaceSources = document.getElementById("workspaceSources");
const workspaceBacktest = document.getElementById("workspaceBacktest");
const workspaceChartInsights = document.getElementById("workspaceChartInsights");
const settingsForm = document.getElementById("workspaceSettingsForm");
const settingsMessage = document.getElementById("workspaceSettingsMessage");
const chartFrom = document.getElementById("chartFrom");
const chartTo = document.getElementById("chartTo");
const chartInterval = document.getElementById("chartInterval");
const chartMainSide = document.getElementById("chartMainSide");
const chartCustomToggle = document.getElementById("chartCustomToggle");
const chartCustomPanel = document.getElementById("chartCustomPanel");
const chartToMode = document.getElementById("chartToMode");
const chartToFixedWrap = document.getElementById("chartToFixedWrap");
const chartToSummary = document.getElementById("chartToSummary");
const chartReturnLatestBtn = document.getElementById("chartReturnLatestBtn");
const chartResolutionMode = document.getElementById("chartResolutionMode");
const chartMetricPicker = document.getElementById("chartMetricPicker");
const chartOverlayPicker = document.getElementById("chartOverlayPicker");
const workspaceMarketStatus = document.getElementById("workspaceMarketStatus");
const workspaceTrackedMarkets = document.getElementById("workspaceTrackedMarkets");
const workspaceMarketResults = document.getElementById("workspaceMarketResults");
const marketConditionInput = document.getElementById("marketConditionInput");
const marketTokenInput = document.getElementById("marketTokenInput");
const marketKeywordInput = document.getElementById("marketKeywordInput");
const workspacePresetName = document.getElementById("workspacePresetName");
const workspacePresetSelect = document.getElementById("workspacePresetSelect");
const workspacePresetScope = document.getElementById("workspacePresetScope");
const workspacePresetStatus = document.getElementById("workspacePresetStatus");
const workspaceAutoRefreshBadge = document.getElementById("workspaceAutoRefreshBadge");
const workspaceAutoRefreshText = document.getElementById("workspaceAutoRefreshText");
const workspaceStateSelect = document.getElementById("workspaceStateSelect");
const workspaceMachineStateSelect = document.getElementById("workspaceMachineStateSelect");
const workspaceDebugMeta = document.getElementById("workspaceDebugMeta");
const workspaceDebugLog = document.getElementById("workspaceDebugLog");
const workspaceDebugClearBtn = document.getElementById("workspaceDebugClearBtn");
const marketUi = window.PolyMarketUi;
const workspaceUrlParams = new URLSearchParams(window.location.search);
let workspaceViewMode = workspaceUrlParams.get("source") === "backtest" ? "backtest" : "live";
let selectedBacktestRunId = workspaceUrlParams.get("run_id") || "";
let selectedBacktestResults = null;
let backtestWindowAppliedForRun = "";
let workspaceBacktestEquityChart = null;

const PARAM_LABELS = {
  entry_z: "入场 Z 分数",
  exit_z: "离场 Z 分数",
  fast_window: "快线窗口",
  slow_window: "慢线窗口",
  fee_bps: "手续费",
  initial_cash: "初始资金",
  stop_loss_pct: "止损比例",
  trailing_stop_pct: "移动止损",
  target_position: "目标仓位",
  fair_price: "合理价格",
  entry_edge: "入场边际",
  bankroll: "资金规模",
  strategy_bankroll: "策略资金",
};
const PARAM_HINTS = {
  entry_z: "价格偏离趋势均值达到这个 Z 分数才允许开仓；数值越高，信号越少。",
  exit_z: "价格回归到这个 Z 分数以内时离场；数值越低，持仓更久。",
  fast_window: "短周期趋势窗口，越小越敏感。",
  slow_window: "长周期趋势窗口，越大越平滑。",
  fee_bps: "单边手续费，1 bps = 0.01%。",
  initial_cash: "回测或模拟账户的初始现金。",
  stop_loss_pct: "从入场或峰值回撤达到该比例时触发止损。",
  trailing_stop_pct: "盈利后按最高价回撤比例跟踪离场。",
  target_position: "目标持仓比例，1 表示满仓，0.5 表示半仓。",
};
const PARAM_GROUPS = [
  { key: "signal", title: "信号参数", keys: ["entry_z", "exit_z", "fast_window", "slow_window", "fair_price", "entry_edge"] },
  { key: "risk", title: "风控与交易", keys: ["target_position", "stop_loss_pct", "trailing_stop_pct", "fee_bps"] },
  { key: "capital", title: "资金", keys: ["initial_cash", "bankroll", "strategy_bankroll"] },
];
const SERIES_LABEL_OVERRIDES = {
  backtest_position_ratio: "回测仓位比例",
  backtest_position_qty: "回测持仓数量",
  backtest_equity: "回测权益",
  backtest_pnl: "回测 PnL",
  backtest_return: "回测收益率",
  backtest_drawdown: "回测回撤",
  strategy_pnl: "策略 PnL",
  strategy_bankroll: "策略资金",
  initial_capital: "初始资金",
};

const EVENT_TIMELINE_CATEGORIES = [
  { key: "print", label: "Print", color: "#94a3b8", lane: 0 },
  { key: "action", label: "Actions", color: "#60a5fa", lane: 1 },
  { key: "trade", label: "Trades", color: "#22d3ee", lane: 2 },
  { key: "error", label: "Errors", color: "#f43f5e", lane: 3 },
  { key: "settings", label: "Settings", color: "#a78bfa", lane: 4 },
];
const EVENT_CATEGORY_COLORS_KEY = "workspaceEventCategoryColors";
function loadEventCategoryColors() {
  return loadJsonStorage(EVENT_CATEGORY_COLORS_KEY, {});
}
function saveEventCategoryColor(key, color) {
  const stored = loadEventCategoryColors();
  stored[key] = color;
  localStorage.setItem(EVENT_CATEGORY_COLORS_KEY, JSON.stringify(stored));
}
const STATE_LANE_COLORS_KEY = "workspaceStateLaneColors";
function loadStateLaneColors() {
  return loadJsonStorage(STATE_LANE_COLORS_KEY, {});
}
function stateLaneIdentity(lane = {}) {
  return String(lane.key || lane.label || `lane_${lane.lane ?? 0}`);
}
function isTemporalStateLaneKey(key) {
  const text = String(key || "").trim().toLowerCase();
  return text === "now" || text === "ts" || text === "timestamp" || text === "time"
    || text.endsWith("_until") || text.endsWith("_at") || text.endsWith("_time");
}
function looksLikeDateTimeText(value) {
  const text = String(value || "").trim();
  return text.length >= 16 && text.includes("T") && (text.endsWith("Z") || text.slice(10).includes("+") || (text.match(/-/g) || []).length >= 2);
}
function isStateLaneMetricDisplayable(item = {}) {
  return !isTemporalStateLaneKey(item.key) && !looksLikeDateTimeText(item.latest_value);
}
function isBacktestDerivedMetricItem(item = {}) {
  const source = item?.meta && typeof item.meta === "object" ? item.meta.source : "";
  return source === "backtest_derived" || String(item.key || "").startsWith("backtest_");
}
function isBacktestDerivedSeries(item = {}) {
  return item.category === "backtest_metric" || item.panel === "backtest_metrics" || String(item.metric_key || item.key || "").includes("backtest_");
}
function isBacktestDerivedLane(lane = {}) {
  return String(lane.key || "").startsWith("backtest_") || lane.panel === "backtest_states";
}
function isStateLaneDisplayable(lane = {}) {
  return !isTemporalStateLaneKey(stateLaneIdentity(lane));
}
function stateLaneDisplayName(lane = {}) {
  const label = String(lane.label || lane.key || "State Lane");
  const key = String(lane.key || "");
  return key && key !== label ? `${label} (${key})` : label;
}
function stateLanePanelId(lane = {}) {
  return `metric_state_lane:${Number(lane.lane ?? 0)}:${stableHash(stateLaneIdentity(lane))}`;
}
function isStateLanePanel(panel = {}) {
  return String(panel.id || "").startsWith("metric_state_lane:");
}
function stateLaneSegmentValue(segment = {}) {
  return String(segment.value ?? segment.label ?? "");
}
const BOOL_STATE_LABELS = {
  cooldown_active: { true: "Cooldown active", false: "Cooldown inactive" },
  shock_cooldown_active: { true: "Shock cooldown active", false: "Shock cooldown inactive" },
  shock_block_active: { true: "Shock block active", false: "Shock block inactive" },
  manual_pause_open: { true: "Manual pause open", false: "Manual pause closed" },
  stop_loss_locked: { true: "Stop loss locked", false: "Stop loss unlocked" },
  force_flat: { true: "Force flat", false: "Force flat off" },
  rank_ok: { true: "Rank OK", false: "Rank not OK" },
};
function boolStateValue(value) {
  const text = String(value ?? "").trim().toLowerCase();
  if (text === "true") return true;
  if (text === "false") return false;
  return null;
}
function boolStateLabel(laneKey, value) {
  const mapped = BOOL_STATE_LABELS[String(laneKey || "")];
  if (mapped) return mapped[value ? "true" : "false"];
  const words = String(laneKey || "").replaceAll("_", " ").trim();
  if (words.endsWith(" active")) {
    const base = words.slice(0, -" active".length).trim();
    return `${base.replace(/\b\w/g, (ch) => ch.toUpperCase())} ${value ? "active" : "inactive"}`;
  }
  if (words.endsWith(" open")) {
    const base = words.slice(0, -" open".length).trim();
    return `${base.replace(/\b\w/g, (ch) => ch.toUpperCase())} ${value ? "open" : "closed"}`;
  }
  if (words.endsWith(" locked")) {
    const base = words.slice(0, -" locked".length).trim();
    return `${base.replace(/\b\w/g, (ch) => ch.toUpperCase())} ${value ? "locked" : "unlocked"}`;
  }
  return value ? "True" : "False";
}
function stateLaneSegmentLabel(segment = {}, lane = {}) {
  const rawValue = stateLaneSegmentValue(segment);
  const label = String(segment.label ?? "").trim();
  const boolValue = boolStateValue(rawValue);
  if (boolValue !== null && (!label || label.toLowerCase() === String(boolValue))) {
    return boolStateLabel(stateLaneIdentity(lane), boolValue);
  }
  return String(segment.label || segment.value || "-");
}
function stateLaneColorKey(laneKey, stateValue) {
  return `${String(laneKey || "lane")}::${String(stateValue ?? "")}`;
}
function saveStateLaneColor(laneKey, stateValue, color) {
  const stored = loadStateLaneColors();
  stored[stateLaneColorKey(laneKey, stateValue)] = color;
  localStorage.setItem(STATE_LANE_COLORS_KEY, JSON.stringify(stored));
}
function stateLaneSegmentColor(lane = {}, segment = {}, overrideColors = loadStateLaneColors()) {
  const valueKey = stateLaneSegmentValue(segment);
  return overrideColors[stateLaneColorKey(stateLaneIdentity(lane), valueKey)]
    || overrideColors[stateLaneColorKey(lane.key, valueKey)]
    || segment.color
    || colorForSeries(`metric_state:${stateLaneIdentity(lane)}:${valueKey}`);
}

const SUB_METRIC_GROUPS = [
  { id: "positions", title: "仓位", keys: ["yes_position", "no_position"] },
  { id: "sizes", title: "数量", keys: ["yes_qty", "no_qty"] },
  { id: "averages", title: "均价 / 收益", keys: ["yes_avg", "no_avg", "strategy_pnl"] },
];
const SUB_METRIC_LABELS = {
  yes_position: "Yes Position",
  no_position: "No Position",
  yes_qty: "Yes Qty",
  no_qty: "No Qty",
  yes_avg: "Yes Avg",
  no_avg: "No Avg",
  strategy_pnl: "Strategy PnL",
  strategy_bankroll: "Strategy Bankroll",
  initial_capital: "Initial Capital",
  profit_roll_ratio: "Profit Roll Ratio",
  realized_profit: "Realized Profit",
};
const QUICK_OVERLAY_SYMBOLS = {
  crypto: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  finance: ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"],
};
const OVERLAY_FIELD_LABELS = {
  price: "Price",
  mcap_usd: "McapUsd",
  fdv_usd: "FdvUsd",
  vol_24h_base: "Vol24hBase",
  vol_24h_quote: "Vol24hQuote",
  circ_supply: "CircSupply",
  total_supply: "TotalSupply",
  max_supply: "MaxSupply",
};
const CHART_MODE_CONFIG = {
  compact: { label: "紧凑", height: 520, gap: 2.5, topStart: 8, usable: 80 },
  standard: { label: "标准", height: 680, gap: 3, topStart: 7, usable: 84 },
  relaxed: { label: "舒展", height: 860, gap: 3.5, topStart: 6, usable: 87 },
};
const CHART_HEIGHT_STORAGE_KEY = "workspaceChartHeight";
const CHART_MIN_HEIGHT = 420;
const CHART_MAX_HEIGHT = 1400;
const LEG_COLOR_PAIRS = [
  { yes: "#22d3ee", no: "#0e7490" },
  { yes: "#a78bfa", no: "#6d28d9" },
  { yes: "#f59e0b", no: "#b45309" },
  { yes: "#34d399", no: "#047857" },
  { yes: "#fb7185", no: "#be123c" },
  { yes: "#60a5fa", no: "#1d4ed8" },
  { yes: "#f472b6", no: "#be185d" },
  { yes: "#c4b5fd", no: "#7c3aed" },
];
const AUTO_SERIES_COLORS = [
  "#22d3ee",
  "#f97316",
  "#34d399",
  "#f472b6",
  "#facc15",
  "#60a5fa",
  "#fb7185",
  "#a3e635",
  "#38bdf8",
  "#c084fc",
  "#2dd4bf",
  "#f59e0b",
  "#818cf8",
  "#e879f9",
  "#10b981",
  "#ef4444",
];
const LEGACY_OVERLAY_AUTO_COLORS = new Set(["#a78bfa", "#ff0000", "#ff0a0a"]);
const PANEL_WEIGHTS = {
  main: 76,
  positions: 12,
  sizes: 12,
  averages: 12,
  capital: 12,
  market_price: 14,
  market_mcap: 14,
  market_volume: 14,
  market_supply: 14,
  indicator_macd: 14,
  metric_values: 12,
  metric_states: 10,
  metric_state_lane: 7,
  event_timeline: 10,
};
const DELTA_STREAM_INTERVALS = {
  price: 2000,
  stats: 5000,
  metrics: 5000,
  watch_markets: 10000,
  overlay: 20000,
  events: 15000,
};
const EVENT_LIST_BASE_LIMIT = 120;
const EVENT_TYPE_GUARANTEE_LIMIT = 20;
const EVENT_TYPE_GUARANTEE_KEYS = ["print", "action", "trade", "error", "settings"];
const DEBUG_MAX_LINES = 160;
const DEBUG_MAX_LINE_CHARS = 2400;
const DEBUG_URL_QUERY_PREVIEW_CHARS = 120;
const CHART_LAYOUT_VERSION = "20260505-compact-series-v1";
const TIMELINE_RANGE_MS = {
  "15m": 15 * 60 * 1000,
  "1h": 60 * 60 * 1000,
  "6h": 6 * 60 * 60 * 1000,
  "1d": 24 * 60 * 60 * 1000,
  "3d": 3 * 24 * 60 * 60 * 1000,
  "1w": 7 * 24 * 60 * 60 * 1000,
  "14d": 14 * 24 * 60 * 60 * 1000,
  "90d": 90 * 24 * 60 * 60 * 1000,
};
const LEGACY_RANGE_MAP = {
  "24h": "1d",
  "72h": "3d",
  "7d": "1w",
};

let workspaceState = null;
let workspaceStateStore = null;
let workspaceChartInstance = null;
let selectedOverlayState = {
  crypto: { symbols: [], fields: [] },
  finance: { symbols: [], fields: [] },
};
let trackedMarkets = [];
let marketResults = [];
let chartDisplayMode = localStorage.getItem("workspaceChartAppearance") || "standard";
let workspaceChartHeight = readStoredChartHeight();
let chartHeightUserAdjusted = false;
let seriesStyleState = loadJsonStorage("workspaceSeriesStyles", {});
let activeQuickRange = "1d";
let autoRefreshEnabled = true;
let workspaceBootReady = false;
let workspaceBooting = false;
let _fullEventsList = []; // canonical list from loadEvents; SSE appends merge into this
let refreshTimers = { chart: null, events: null, workspace: null };
let currentLegendNameToKey = new Map();
let currentChartRequestId = 0;
let currentChartAbortController = null;
let chartRefreshDebounceTimer = null;
const CHART_RELOAD_DEBOUNCE_MS = 420;
const CHART_RELOAD_BUSY_DEBOUNCE_MS = 700;
let workspaceDebugLines = [];
let isChartLoading = false;
let chartViewState = {
  start: null,
  end: null,
  startValue: null,
  endValue: null,
};
let chartZoomSyncSuppressedUntil = 0;
let chartLegendSelectedState = loadJsonStorage("workspaceLegendSelectedState", {});
let lastSeriesControlsSignature = "";
let lastChartStructureSignature = "";
let lastChartTimeExtent = null;
let workspaceLiveSource = null;
let currentChartPayload = null;
let currentChartReloadSignature = "";
let deltaStreamState = createDeltaStreamState();
let lastFullChartLoadedAt = 0;
let isEventsLoading = false;

function loadJsonStorage(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback));
  } catch {
    return fallback;
  }
}

function clampChartHeight(value) {
  const height = Math.round(Number(value));
  if (!Number.isFinite(height)) {
    return CHART_MODE_CONFIG.standard.height;
  }
  return Math.min(CHART_MAX_HEIGHT, Math.max(CHART_MIN_HEIGHT, height));
}

function readStoredChartHeight() {
  const stored = localStorage.getItem(CHART_HEIGHT_STORAGE_KEY);
  if (stored) {
    return clampChartHeight(stored);
  }
  const legacyMode = localStorage.getItem("workspaceChartAppearance") || "standard";
  return clampChartHeight((CHART_MODE_CONFIG[legacyMode] || CHART_MODE_CONFIG.standard).height);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(container, text) {
  container.innerHTML = `<div class="status">${escapeHtml(text)}</div>`;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function formatDebugPayload(payload) {
  if (payload === null || payload === undefined) {
    return "";
  }
  if (typeof payload === "string") {
    return payload.length > DEBUG_MAX_LINE_CHARS ? `${payload.slice(0, DEBUG_MAX_LINE_CHARS)}...` : payload;
  }
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

function compactDebugUrl(url) {
  const text = String(url || "");
  const queryIndex = text.indexOf("?");
  if (queryIndex < 0) {
    return {
      url: text,
      query_chars: 0,
    };
  }
  const path = text.slice(0, queryIndex);
  const query = text.slice(queryIndex + 1);
  return {
    url: `${path}?${query.slice(0, DEBUG_URL_QUERY_PREVIEW_CHARS)}${query.length > DEBUG_URL_QUERY_PREVIEW_CHARS ? "..." : ""}`,
    query_chars: query.length,
  };
}

function compactFetchDebug(method, url, options = {}) {
  const summary = compactDebugUrl(url);
  return {
    method,
    url: summary.url,
    query_chars: summary.query_chars,
    has_body: Boolean(options?.body),
    has_signal: Boolean(options?.signal),
    headers: options?.headers ? Object.keys(options.headers) : [],
  };
}

function pushDebug(tag, payload = null) {
  const now = new Date();
  const ts = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())} `
    + `${pad2(now.getHours())}:${pad2(now.getMinutes())}:${pad2(now.getSeconds())}`;
  const formatted = formatDebugPayload(payload);
  let line = formatted ? `[${ts}] ${tag} ${formatted}` : `[${ts}] ${tag}`;
  if (line.length > DEBUG_MAX_LINE_CHARS) {
    const omitted = line.length - DEBUG_MAX_LINE_CHARS;
    line = `${line.slice(0, DEBUG_MAX_LINE_CHARS)}\n... truncated ${omitted} chars`;
  }
  workspaceDebugLines.push(line);
  if (workspaceDebugLines.length > DEBUG_MAX_LINES) {
    workspaceDebugLines = workspaceDebugLines.slice(-DEBUG_MAX_LINES);
  }
  if (workspaceDebugLog) {
    workspaceDebugLog.textContent = workspaceDebugLines.join("\n\n");
    workspaceDebugLog.scrollTop = workspaceDebugLog.scrollHeight;
  }
}

function setDebugMeta(text) {
  if (workspaceDebugMeta) {
    workspaceDebugMeta.textContent = text || "暂无调试信息。";
  }
}

function chartDebugSummary(payload) {
  const debug = payload?.meta?.debug || {};
  const requested = debug.requested_sub_metrics || [];
  const selected = debug.selected_sub_metrics || [];
  const rejected = debug.rejected_sub_metrics || [];
  const parts = [
    `requested=${requested.length}`,
    `selected=${selected.length}`,
    `rejected=${rejected.length}`,
    `metric_series=${debug.metric_series_count ?? "-"}`,
    `state_lanes=${debug.state_lane_count ?? "-"}`,
    `catalog=${debug.metric_catalog_items ?? "-"}`,
    `detail_row=${debug.strategy_detail_row_id ?? "-"}`,
  ];
  if (rejected.length) {
    parts.push(`rejected_keys=${rejected.join(",")}`);
  }
  return parts.join(" | ");
}

function summarizeSeriesRenderData(payload, option = null, targetKeys = null) {
  const rows = payload?.rows || [];
  const series = payload?.series || [];
  const allowed = targetKeys ? new Set(targetKeys) : null;
  const optionSeriesById = new Map((option?.series || []).map((item) => [String(item?.id || item?.name || ""), item]));
  const output = [];
  series.forEach((item) => {
    const key = String(item?.key || "");
    if (!key || (allowed && !allowed.has(key))) return;
    const values = rows.map((row) => {
      const value = Number(row?.[key]);
      return Number.isFinite(value) ? value : null;
    });
    const finiteIndexes = [];
    values.forEach((value, index) => {
      if (Number.isFinite(value)) finiteIndexes.push(index);
    });
    let gap_count = 0;
    let max_gap_rows = 0;
    for (let index = 1; index < finiteIndexes.length; index += 1) {
      const gap = finiteIndexes[index] - finiteIndexes[index - 1] - 1;
      if (gap > 0) {
        gap_count += 1;
        max_gap_rows = Math.max(max_gap_rows, gap);
      }
    }
    const firstIndex = finiteIndexes[0];
    const lastIndex = finiteIndexes[finiteIndexes.length - 1];
    const optionSeries = optionSeriesById.get(key) || optionSeriesById.get(String(item?.label || ""));
    output.push({
      key,
      label: item?.label || key,
      panel: item?.panel || "",
      category: item?.category || "",
      rows: rows.length,
      finite: finiteIndexes.length,
      nulls: Math.max(0, rows.length - finiteIndexes.length),
      gaps: gap_count,
      max_gap_rows,
      first_ts: firstIndex !== undefined ? rows[firstIndex]?.ts : null,
      last_ts: lastIndex !== undefined ? rows[lastIndex]?.ts : null,
      first_value: firstIndex !== undefined ? values[firstIndex] : null,
      last_value: lastIndex !== undefined ? values[lastIndex] : null,
      option_points: Array.isArray(optionSeries?.data) ? optionSeries.data.length : null,
      connect_nulls: optionSeries?.connectNulls ?? null,
      show_symbol: optionSeries?.showSymbol ?? null,
      visible: (seriesStyleState[key] || item?.style || {}).visible !== false,
    });
  });
  return output;
}

function importantRenderDiagnostics(payload, option = null, targetKeys = null) {
  const summaries = summarizeSeriesRenderData(payload, option, targetKeys);
  const important = summaries.filter((item) => (
    item.key === "market_0_yes_bid"
    || item.key === "market_0_yes_ask"
    || item.key === "market_0_no_bid"
    || item.key === "market_0_no_ask"
    || item.key === "strategy_pnl"
    || Number(item.nulls || 0) > 0
    || Number(item.gaps || 0) > 0
  ));
  return important.slice(0, 24);
}

function compactPriceRenderSummary(payload) {
  const byKey = new Map(summarizeSeriesRenderData(payload).map((item) => [item.key, item]));
  return ["market_0_yes_bid", "market_0_yes_ask", "market_0_no_bid", "market_0_no_ask"]
    .map((key) => {
      const item = byKey.get(key);
      if (!item) return `${key}=missing`;
      return `${key}=${item.finite}/${item.rows} gaps=${item.gaps}`;
    })
    .join(" | ");
}

function fillChartRowsForward(rows) {
  const ordered = (rows || [])
    .map((row) => ({ ...(row || {}), ts: String(row?.ts || "").trim() }))
    .filter((row) => row.ts)
    .sort((a, b) => (parseTimeSafe(a.ts) ?? 0) - (parseTimeSafe(b.ts) ?? 0));
  const output = [];
  let current = {};
  ordered.forEach((row) => {
    current = {
      ...current,
      ...row,
    };
    output.push({
      ...current,
      ts: row.ts,
    });
  });
  return output;
}

function normalizeChartPayloadRows(payload, reason = "chart-load") {
  if (!payload || !Array.isArray(payload.rows) || !payload.rows.length) {
    return payload;
  }
  const before = compactPriceRenderSummary(payload);
  const normalized = {
    ...payload,
    rows: fillChartRowsForward(payload.rows),
  };
  const after = compactPriceRenderSummary(normalized);
  if (before !== after) {
    pushDebug("[WS] chart:normalize-rows", {
      row_id: Number(rowId),
      reason,
      before,
      after,
    });
  }
  return normalized;
}

function isAbortError(error) {
  return error?.name === "AbortError";
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const num = Number(value);
  return Number.isFinite(num) ? num.toLocaleString(undefined, { maximumFractionDigits: digits }) : String(value);
}

function formatCurrency(value, digits = 2, suffix = "") {
  const text = formatNumber(value, digits);
  return text === "-" || !suffix ? text : `${text} ${suffix}`;
}

function formatPercent(value) {
  const num = Number(value);
  return Number.isFinite(num) ? `${(num * 100).toFixed(2)}%` : "-";
}

function numericValue(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function formatTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return `${date.getUTCFullYear()}-${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())} ${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}:${pad2(date.getUTCSeconds())} UTC`;
}

function normalizeParamKey(value) {
  return String(value || "").trim().toLowerCase();
}

function humanizeParamKey(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  return text
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function strategyParamLabel(key, fallback = "") {
  const normalized = normalizeParamKey(key || fallback);
  const rawFallback = String(fallback || "").trim();
  if (PARAM_LABELS[normalized]) return PARAM_LABELS[normalized];
  if (rawFallback && normalizeParamKey(rawFallback) !== normalized) return rawFallback;
  return humanizeParamKey(key || fallback);
}

function strategyParamHint(key, fallback = "") {
  const normalized = normalizeParamKey(key);
  return PARAM_HINTS[normalized] || fallback || "";
}

function displaySeriesLabel(item = {}) {
  const key = String(item.key || "");
  if (SERIES_LABEL_OVERRIDES[key]) return SERIES_LABEL_OVERRIDES[key];
  if (item.base_key === "ohlc") return `${item.market_label || item.label || "BTCUSDT"} K线`;
  if (item.base_key === "close") return `${item.market_label || item.label || "BTCUSDT"} 收盘价`;
  if (item.base_key === "volume") return `${item.market_label || item.label || "BTCUSDT"} 成交量`;
  return item.label || key || "-";
}

function isCryptoSeriesMeta(meta = {}) {
  const source = String(meta.source_label || meta.source_detail || "").toLowerCase();
  return source.includes("binance");
}

function workspaceStrategyMode(strategy = {}) {
  const validModes = ["Stop", "Virtual", "Real"];
  const isVirtual = String(strategy.is_virtual ?? strategy.editable?.IsVirtual ?? "").trim().toLowerCase() === "true";
  const legacyState = validModes.includes(strategy.state) ? strategy.state : "";
  const mode = strategy.mode || legacyState || (isVirtual ? "Virtual" : "Stop");
  return validModes.includes(mode) ? mode : "Stop";
}

function syncWorkspaceStateControl(strategy = {}) {
  if (!workspaceStateSelect) return;
  const mode = workspaceStrategyMode(strategy);
  workspaceStateSelect.value = mode;
  workspaceStateSelect.dataset.prev = mode;
  workspaceStateSelect.className = `state-select workspace-state-select state-${mode}`;
}

window.syncWorkspaceStateControl = syncWorkspaceStateControl;

function defaultWorkspaceStateOptions() {
  return [
    { value: "auto", label: "Auto" },
    { value: "idle", label: "Idle" },
    { value: "holding", label: "Holding" },
    { value: "cooldown", label: "Cooldown" },
    { value: "manual_review", label: "Manual Review" },
    { value: "stop_loss_locked", label: "Stop Loss Locked" },
  ];
}

function workspaceMachineState(strategy = {}, stateStore = null) {
  const validModes = ["Stop", "Virtual", "Real"];
  const storeState = stateStore?.machine_state || stateStore?.state || stateStore?.machine?.state;
  const strategyState = strategy.machine_state || (!validModes.includes(strategy.state) ? strategy.state : "");
  return String(storeState || strategyState || "auto");
}

function workspaceStateOptions(strategy = {}, stateStore = null) {
  const raw = stateStore?.state_options || strategy.state_options || stateStore?.state_machine_schema?.states || [];
  const options = Array.isArray(raw) && raw.length ? raw : defaultWorkspaceStateOptions();
  const current = workspaceMachineState(strategy, stateStore);
  const hasCurrent = options.some((item) => String(item.value ?? item) === current);
  return hasCurrent ? options : [{ value: current, label: current }, ...options];
}

function syncWorkspaceMachineStateControl(strategy = {}, stateStore = null) {
  if (!workspaceMachineStateSelect) return;
  const current = workspaceMachineState(strategy, stateStore);
  workspaceMachineStateSelect.innerHTML = workspaceStateOptions(strategy, stateStore).map((item) => {
    const value = String(item.value ?? item);
    const label = String(item.label ?? value);
    return `<option value="${escapeHtml(value)}"${value === current ? " selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
  workspaceMachineStateSelect.value = current;
  workspaceMachineStateSelect.dataset.prev = current;
}

window.syncWorkspaceMachineStateControl = syncWorkspaceMachineStateControl;

function stateTransitionConfirmMessage(prev, next) {
  return {
    "Virtual->Real": "当前虚拟仓位、虚拟订单、虚拟收益不会迁移到实盘。\n切换后策略将基于真实账户状态重新运行。\n\n确认切换到实盘？",
    "Real->Virtual": "真实仓位和真实挂单不会迁移到虚拟盘，也不会自动平仓或撤单。\n切换后策略将使用虚拟账户重新运行。\n\n确认切换到虚拟盘？",
    "Stop->Real": "切换到实盘将使用真实资金和真实仓位运行策略。\n\n确认切换到实盘？",
    "Real->Stop": "切换到 Stop 不会自动撤销已有真实挂单或平仓。\n\n确认暂停策略？",
  }[`${prev}->${next}`] || "";
}

function localInputToIso(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

function normalizeTimelineRange(value) {
  const text = String(value || "").trim().toLowerCase();
  return LEGACY_RANGE_MAP[text] || text || "1d";
}

function formatLocalDateTimeInput(date) {
  const value = date instanceof Date ? date : new Date(date);
  if (Number.isNaN(value.getTime())) {
    return "";
  }
  return `${value.getFullYear()}-${pad2(value.getMonth() + 1)}-${pad2(value.getDate())}T${pad2(value.getHours())}:${pad2(value.getMinutes())}`;
}

function backtestRunWindow(run = {}) {
  const metrics = run.metrics || {};
  const snapshot = run.case_snapshot || {};
  const windowData = snapshot.data_window || {};
  const start = metrics.period_start || metrics.requested_start || windowData.start || windowData.from || windowData.history_start || null;
  const end = metrics.period_end || metrics.requested_end || windowData.end || windowData.to || windowData.history_end || null;
  return { start, end };
}

function setBacktestUrlState(mode, runId = "") {
  const url = new URL(window.location.href);
  if (mode === "backtest" && runId) {
    url.searchParams.set("source", "backtest");
    url.searchParams.set("run_id", runId);
  } else {
    url.searchParams.delete("source");
    url.searchParams.delete("run_id");
  }
  window.history.replaceState({}, "", url.toString());
}

function updateWorkspaceViewBadge() {
  if (!workspaceAutoRefreshBadge || !workspaceAutoRefreshText) return;
  if (workspaceViewMode === "backtest") {
    workspaceAutoRefreshBadge.className = "state-chip pending";
    workspaceAutoRefreshBadge.textContent = "backtest";
    workspaceAutoRefreshText.textContent = selectedBacktestRunId
      ? `回测视图 · Run ${selectedBacktestRunId}`
      : "回测视图";
  }
}

function applyBacktestWindowToChart(run = {}) {
  if (!run?.run_id || backtestWindowAppliedForRun === String(run.run_id)) return;
  const range = backtestRunWindow(run);
  if (!range.start && !range.end) return;
  if (range.start && chartFrom) {
    chartFrom.value = formatLocalDateTimeInput(new Date(range.start));
  }
  if (range.end && chartTo) {
    chartTo.value = formatLocalDateTimeInput(new Date(range.end));
  }
  if (chartToMode) {
    chartToMode.value = range.end ? "specific" : "latest";
  }
  if (chartResolutionMode && range.start && range.end) {
    const windowMs = Date.parse(range.end) - Date.parse(range.start);
    if (Number.isFinite(windowMs) && windowMs > TIMELINE_RANGE_MS["14d"] && chartResolutionMode.value === "15m") {
      chartResolutionMode.value = "auto";
    }
  }
  activeQuickRange = "custom";
  chartViewState = { start: null, end: null, startValue: null, endValue: null };
  backtestWindowAppliedForRun = String(run.run_id);
  updateQuickRangeButtons();
}

function formatTimelineLabel(value) {
  const date = value instanceof Date ? value : new Date(value || "");
  if (Number.isNaN(date.getTime())) {
    return String(value || "-").replace("T", " ").slice(0, 16);
  }
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function parseRangeMs(rangeValue = activeQuickRange) {
  const range = normalizeTimelineRange(rangeValue);
  return TIMELINE_RANGE_MS[range] || TIMELINE_RANGE_MS["1d"];
}

function deriveTimelineWindowMs() {
  const fromTs = Date.parse(chartFrom?.value || "");
  const toTs = Date.parse(chartTo?.value || "");
  if (Number.isFinite(fromTs) && Number.isFinite(toTs) && toTs > fromTs) {
    return toTs - fromTs;
  }
  return parseRangeMs(activeQuickRange);
}

function autoIntervalForWindowMs(windowMs) {
  if (windowMs <= TIMELINE_RANGE_MS["15m"]) return "5s";
  if (windowMs <= TIMELINE_RANGE_MS["1h"]) return "5s";
  if (windowMs <= TIMELINE_RANGE_MS["6h"]) return "30s";
  if (windowMs <= TIMELINE_RANGE_MS["1d"]) return "1m";
  if (windowMs <= TIMELINE_RANGE_MS["3d"]) return "5m";
  if (windowMs <= TIMELINE_RANGE_MS["14d"]) return "15m";
  if (windowMs <= TIMELINE_RANGE_MS["90d"]) return "1h";
  return "4h";
}

function currentResolutionInterval() {
  const mode = chartResolutionMode?.value || "auto";
  return mode === "auto" ? autoIntervalForWindowMs(deriveTimelineWindowMs()) : mode;
}

function suppressProgrammaticChartZoomSync(durationMs = 250) {
  chartZoomSyncSuppressedUntil = Math.max(chartZoomSyncSuppressedUntil, Date.now() + durationMs);
}

function isProgrammaticChartZoomSync() {
  return Date.now() < chartZoomSyncSuppressedUntil;
}

function syncTimelineUi() {
  const toMode = chartToMode?.value || "latest";
  if (chartToFixedWrap) {
    chartToFixedWrap.hidden = toMode !== "specific";
  }
  if (chartToSummary) {
    chartToSummary.textContent = toMode === "specific" && chartTo?.value
      ? `To: ${formatTimelineLabel(chartTo.value)}`
      : "To: Latest";
  }
  if (chartReturnLatestBtn) {
    chartReturnLatestBtn.hidden = toMode !== "specific";
  }
  const interval = currentResolutionInterval();
  if (chartInterval) {
    chartInterval.value = interval;
  }
  if (chartResolutionMode) {
    chartResolutionMode.title = chartResolutionMode.value === "auto"
      ? `Auto, current interval: ${interval}`
      : `Fixed interval: ${interval}`;
  }
}

function renderTimelineStatus(payload) {
  const meta = payload?.meta || {};
  const sources = meta.sources || {};
  const fromText = formatTimelineLabel(meta.from);
  const toIsLatest = chartToMode?.value !== "specific";
  const toText = toIsLatest ? "Latest" : formatTimelineLabel(meta.to || chartTo?.value);
  const interval = meta.interval || chartInterval?.value || autoIntervalForWindowMs(deriveTimelineWindowMs());
  const parts = [];
  if (toIsLatest && activeQuickRange !== "custom") {
    parts.push(`Current view: last ${String(activeQuickRange || "1d").toUpperCase()}`);
    parts.push("End follows latest data");
  } else {
    parts.push(`Current view: ${fromText} -> ${toText}`);
    if (toIsLatest) {
      parts.push("End follows latest data");
    }
  }
  parts.push(`Interval: ${interval}`);
  if (toIsLatest) {
    parts.push(`Last update: ${formatTimelineLabel(meta.to).slice(11)}`);
  }
  if (Number(sources.history_price_points || 0) < 2) {
    parts.push("Insufficient history; showing current snapshot only");
  } else if (sources.history_price_points !== undefined) {
    parts.push(`History samples: ${sources.history_price_points}`);
  }
  if (Number(sources.history_metric_points || 0) > 0) {
    parts.push(`Metrics ${sources.history_metric_points}`);
  }
  return parts.join(" 路 ");
}

function openCustomTimelinePanel() {
  if (!chartCustomPanel) return;
  const now = new Date();
  if (!chartFrom?.value) {
    chartFrom.value = formatLocalDateTimeInput(new Date(now.getTime() - parseRangeMs(activeQuickRange)));
  }
  if (chartToMode && !chartToMode.value) {
    chartToMode.value = "latest";
  }
  chartCustomPanel.hidden = false;
  activeQuickRange = "custom";
  updateQuickRangeButtons();
  syncTimelineUi();
}

function closeCustomTimelinePanel() {
  if (chartCustomPanel) {
    chartCustomPanel.hidden = true;
  }
}

function setTimelineToLatest() {
  const windowMs = deriveTimelineWindowMs();
  const now = new Date();
  if (chartToMode) chartToMode.value = "latest";
  if (chartTo) chartTo.value = "";
  if (activeQuickRange === "custom" && chartFrom) {
    chartFrom.value = formatLocalDateTimeInput(new Date(now.getTime() - windowMs));
  }
  syncTimelineUi();
}

function valueToLocalTimelineInput(value) {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return formatLocalDateTimeInput(new Date(value));
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? formatLocalDateTimeInput(new Date(parsed)) : "";
}

function normalizeSymbols(list) {
  return [...new Set((list || []).map((item) => String(item || "").trim().toUpperCase()).filter(Boolean))];
}

function normalizeOverlayFields(list) {
  return [...new Set((list || []).map((item) => String(item || "").trim().toLowerCase()).filter(Boolean))];
}

function overlaySeriesKey(type, symbol, fieldKey) {
  return `${type}_${String(symbol || "").trim().toUpperCase()}_${String(fieldKey || "").trim().toLowerCase()}`;
}

function isOverlaySeriesKey(key) {
  const text = String(key || "");
  return /^(crypto|finance)_[A-Z0-9._-]+_/.test(text)
    && Object.keys(OVERLAY_FIELD_LABELS).some((fieldKey) => text.endsWith(`_${fieldKey}`));
}

function stableHash(text) {
  let hash = 2166136261;
  String(text || "").split("").forEach((ch) => {
    hash ^= ch.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  });
  return hash >>> 0;
}

function autoSeriesColor(key) {
  const overlayMatch = String(key || "").match(/^(crypto|finance)_([A-Z0-9._-]+)_(.+)$/);
  if (overlayMatch) {
    const [, type, symbol, fieldKey] = overlayMatch;
    const symbols = selectedOverlaySymbols(type);
    const fields = selectedOverlayFields(type);
    const symbolIndex = symbols.indexOf(symbol);
    const fieldIndex = fields.indexOf(fieldKey);
    if (symbolIndex >= 0 && fieldIndex >= 0) {
      return AUTO_SERIES_COLORS[(symbolIndex * Math.max(1, fields.length) + fieldIndex) % AUTO_SERIES_COLORS.length];
    }
  }
  return AUTO_SERIES_COLORS[stableHash(key) % AUTO_SERIES_COLORS.length];
}

function normalizeColorText(value) {
  return String(value || "").trim().toLowerCase();
}

function overlayStyleColor(key, current = {}, fallback = {}) {
  if (!isOverlaySeriesKey(key)) {
    return current.color || fallback.color || colorForSeries(key);
  }
  const currentColor = normalizeColorText(current.color);
  const fallbackColor = normalizeColorText(fallback.color);
  if (current.auto_color === false && currentColor && !LEGACY_OVERLAY_AUTO_COLORS.has(currentColor)) {
    return current.color;
  }
  if (fallback.auto_color === false && fallbackColor && !LEGACY_OVERLAY_AUTO_COLORS.has(fallbackColor)) {
    return fallback.color;
  }
  return autoSeriesColor(key);
}

function isPrimaryPriceSeriesKey(key) {
  return /^(market_\d+_)?(yes|no)_(bid|ask)$/.test(String(key || ""));
}

function persistSeriesStyles() {
  localStorage.setItem("workspaceSeriesStyles", JSON.stringify(seriesStyleState));
}

function ensureOverlayStyleEntries(type, symbols, fields, visible = true) {
  let changed = false;
  normalizeSymbols(symbols).forEach((symbol) => {
    normalizeOverlayFields(fields).forEach((fieldKey) => {
      const key = overlaySeriesKey(type, symbol, fieldKey);
      const current = { ...(seriesStyleState[key] || {}) };
      const nextVisible = visible ? true : current.visible !== false;
      const next = {
        color: overlayStyleColor(key, current),
        width: Number(current.width || 2),
        line_type: current.line_type || "solid",
        smooth: Boolean(current.smooth ?? false),
        show_symbol: Boolean(current.show_symbol ?? false),
        visible: nextVisible,
        auto_color: current.auto_color === false ? false : true,
        macd: {
          enabled: Boolean(current.macd?.enabled),
          fast: Number(current.macd?.fast || 12),
          slow: Number(current.macd?.slow || 26),
          signal: Number(current.macd?.signal || 9),
        },
      };
      if (JSON.stringify(current) !== JSON.stringify(next)) {
        seriesStyleState[key] = next;
        changed = true;
      }
    });
  });
  if (changed) {
    persistSeriesStyles();
  }
}

function revealOverlaySeries(type, symbols, fields) {
  ensureOverlayStyleEntries(type, symbols, fields, true);
}

function revealSelectedOverlaySeries(type) {
  revealOverlaySeries(type, selectedOverlayState[type]?.symbols || [], selectedOverlayState[type]?.fields || []);
}

function persistChartAppearance() {
  localStorage.setItem("workspaceChartAppearance", chartDisplayMode);
  localStorage.setItem(CHART_HEIGHT_STORAGE_KEY, String(workspaceChartHeight));
}

function persistLegendSelectedState() {
  localStorage.setItem("workspaceLegendSelectedState", JSON.stringify(chartLegendSelectedState));
}

function buildSeriesControlsSignature(series, payload = {}) {
  return JSON.stringify(
    {
      series: (series || []).map((item) => {
        const style = seriesStyleState[item.key] || {};
        return {
          key: item.key,
          label: item.label,
          render: item.render,
          panel: item.panel,
          removable: Boolean(item.removable),
          visible: style.visible !== false,
          macd: Boolean(style.macd?.enabled),
        };
      }),
      state_lanes: (payload.metric_state_lanes || []).filter(isStateLaneDisplayable).map((lane) => ({
        key: stateLaneIdentity(lane),
        label: lane.label || lane.key,
        states: [...new Set((lane.segments || []).map((segment) => stateLaneSegmentValue(segment)))],
      })),
    }
  );
}

function buildChartStructureSignature(payload) {
  const hasEventTimeline = isEventTimelineSelected() && (payload.events || []).some((event) => event?.ts);
  const expandedPanels = expandChartPanels(payload.panels || [], payload.metric_state_lanes || [], payload.events || []);
  return JSON.stringify({
    panels: expandedPanels.map((p) => p.id),
    series: (payload.series || []).map((s) => ({
      key: s.key,
      panel: s.panel,
      render: s.render,
      label: s.label,
    })),
    state_lanes: (payload.metric_state_lanes || []).filter(isStateLaneDisplayable).map((lane) => ({
      key: stateLaneIdentity(lane),
      segment_count: (lane.segments || []).length,
    })),
    has_event_timeline: hasEventTimeline,
    height: workspaceChartHeight,
  });
}

function repairSeriesStyleState() {
  let changed = false;
  const next = { ...(seriesStyleState || {}) };
  Object.entries(next).forEach(([key, value]) => {
    if (!value || typeof value !== "object") return;
    if (isPrimaryPriceSeriesKey(key) && value.visible === false) {
      next[key] = {
        ...value,
        visible: true,
      };
      changed = true;
    }
  });
  if (changed) {
    seriesStyleState = next;
    persistSeriesStyles();
  }
}

function scheduleChartReload(delayMs = null) {
  if (currentChartAbortController) {
    currentChartAbortController.abort();
  }
  clearTimeout(chartRefreshDebounceTimer);
  const delay = Number.isFinite(Number(delayMs))
    ? Number(delayMs)
    : (isChartLoading ? CHART_RELOAD_BUSY_DEBOUNCE_MS : CHART_RELOAD_DEBOUNCE_MS);
  chartRefreshDebounceTimer = setTimeout(() => {
    loadChart().catch((error) => {
      if (isAbortError(error)) {
        return;
      }
      setStatus(workspaceCharts, error.message);
    });
  }, delay);
}

async function fetchJson(url, options) {
  const t0 = performance.now();
  const method = options?.method || "GET";
  const fetchSummary = compactFetchDebug(method, url, options || {});
  console.log(`[WS-FE][start] ${fetchSummary.url}`, fetchSummary);
  pushDebug("[WS-FE][start]", fetchSummary);
  try {
    const response = await fetch(url, options);
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (parseError) {
      const dt = (performance.now() - t0).toFixed(1);
      console.error(`[WS-FE][parse-error] ${fetchSummary.url} cost=${dt}ms`, parseError, text);
      pushDebug("[WS-FE][parse-error]", {
        method,
        url: fetchSummary.url,
        query_chars: fetchSummary.query_chars,
        cost_ms: Number(dt),
        raw_text_head: String(text || "").slice(0, 1000),
      });
      throw new Error(`鎺ュ彛杩斿洖涓嶆槸鍚堟硶 JSON: ${url}`);
    }
    const dt = (performance.now() - t0).toFixed(1);
    console.log(`[WS-FE][done] ${fetchSummary.url} status=${response.status} cost=${dt}ms`, {
      ok: response.ok,
      keys: Object.keys(data || {}),
    });
    pushDebug("[WS-FE][done]", {
      method,
      url: fetchSummary.url,
      query_chars: fetchSummary.query_chars,
      status: response.status,
      cost_ms: Number(dt),
      ok: response.ok,
      keys: Object.keys(data || {}),
    });
    if (!response.ok) {
      throw new Error(data.error || data.message || `HTTP ${response.status}`);
    }
    return data;
  } catch (error) {
    const dt = (performance.now() - t0).toFixed(1);
    if (isAbortError(error)) {
      console.warn(`[WS-FE][abort] ${fetchSummary.url} cost=${dt}ms`);
      pushDebug("[WS-FE][abort]", {
        method,
        url: fetchSummary.url,
        query_chars: fetchSummary.query_chars,
        cost_ms: Number(dt),
      });
    } else {
      console.error(`[WS-FE][error] ${fetchSummary.url} cost=${dt}ms`, error);
      pushDebug("[WS-FE][error]", {
        method,
        url: fetchSummary.url,
        query_chars: fetchSummary.query_chars,
        cost_ms: Number(dt),
        name: error?.name || "Error",
        message: error?.message || String(error),
      });
    }
    throw error;
  }
}

function summaryCard(label, value, subvalue) {
  return `
    <div class="card">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value">${escapeHtml(value)}</div>
      <div class="subvalue">${escapeHtml(subvalue)}</div>
    </div>
  `;
}

function marketIdentity(target) {
  return [
    target.type || "",
    target.condition_id || "",
    target.yes_token || "",
    target.no_token || "",
    target.symbol || "",
    target.instrument_id || "",
    target.interval || "",
  ].join("|");
}

function isBinanceTarget(target = {}) {
  const text = [
    target.type,
    target.source,
    target.venue,
    target.asset_class,
    target.instrument_id,
  ].map((value) => String(value || "").toLowerCase()).join("|");
  return text.includes("binance") || text.includes("crypto_spot");
}

function buildStrategyTarget() {
  const ctx = workspaceState?.market_context || {};
  return {
    type: "strategy",
    row_id: Number(rowId),
    label: ctx.question || workspaceState?.strategy?.question || "策略默认市场",
    question: ctx.question || workspaceState?.strategy?.question || "",
    slug: ctx.slug || workspaceState?.strategy?.matched_market_raw?.slug || "",
    event_slug: ctx.event_slug || workspaceState?.strategy?.event_slug || workspaceState?.strategy?.matched_market_raw?.eventSlug || workspaceState?.strategy?.matched_market_raw?.event_slug || "",
    group_item_title: ctx.group_item_title || workspaceState?.strategy?.group_item_title || workspaceState?.strategy?.matched_market_raw?.groupItemTitle || "",
    url: ctx.url || workspaceState?.strategy?.matched_market_raw?.url || "",
    condition_id: ctx.condition_id || "",
    yes_token: ctx.yes_token || "",
    no_token: ctx.no_token || "",
    is_primary: true,
  };
}

function buildDefaultMarketTargets() {
  const legs = workspaceState?.market_context?.legs || [];
  const byLeg = legs.map((leg, index) => {
    const binance = isBinanceTarget(leg);
    const symbol = String(leg.symbol || "").toUpperCase();
    return {
      type: binance ? "binance" : "market",
      source: binance ? "binance" : (leg.source || ""),
      venue: leg.venue || (binance ? "binance" : ""),
      asset_class: leg.asset_class || (binance ? "crypto_spot" : ""),
      row_id: Number(rowId),
      leg_index: Number.isFinite(Number(leg.leg_index)) ? Number(leg.leg_index) : index,
      label: leg.label || symbol || `Leg ${index + 1}`,
      question: leg.question || leg.label || symbol || `Leg ${index + 1}`,
      condition_id: leg.condition_id || "",
      yes_token: leg.yes_token || "",
      no_token: leg.no_token || "",
      symbol,
      interval: leg.interval || leg.instrument_json?.interval || "1m",
      instrument_id: leg.instrument_id || (symbol ? `crypto_spot:binance:${symbol}` : ""),
      is_primary: index === 0,
    };
  }).filter((item) => item.condition_id || item.yes_token || item.no_token || item.symbol || item.instrument_id);
  return byLeg.length ? byLeg : [buildStrategyTarget()];
}

function normalizeTrackedMarkets(list) {
  const result = [];
  const seen = new Set();
  (list || []).forEach((item, index) => {
    if (!item) {
      return;
    }
    const next = {
      type: String(item.type || "market"),
      source: item.source || "",
      venue: item.venue || "",
      asset_class: item.asset_class || "",
      row_id: item.row_id ? Number(item.row_id) : undefined,
      label: item.label || item.question || item.symbol || item.condition_id || `Market ${index + 1}`,
      question: item.question || item.label || "",
      slug: item.slug || item.raw?.slug || "",
      event_slug: item.event_slug || item.eventSlug || item.raw?.eventSlug || item.raw?.event_slug || "",
      group_item_title: item.group_item_title || item.groupItemTitle || item.raw?.groupItemTitle || "",
      url: item.url || item.raw?.url || "",
      condition_id: item.condition_id || "",
      yes_token: item.yes_token || "",
      no_token: item.no_token || "",
      symbol: String(item.symbol || "").toUpperCase(),
      interval: item.interval || "1m",
      instrument_id: item.instrument_id || "",
      is_primary: index === 0,
    };
    const identity = marketIdentity(next);
    if (!identity.replaceAll("|", "") || seen.has(identity)) {
      return;
    }
    seen.add(identity);
    result.push(next);
  });
  if (!result.length && workspaceState) {
    result.push(...buildDefaultMarketTargets());
  }
  result.forEach((item, index) => {
    item.is_primary = index === 0;
  });
  trackedMarkets = result;
  window.workspaceTrackedMarkets = trackedMarkets;
}

function primaryTrackedMarket() {
  return trackedMarkets[0] || buildDefaultMarketTargets()[0] || buildStrategyTarget() || {};
}

function syncMainChartModeOptions() {
  if (!chartMainSide) return;
  const primary = primaryTrackedMarket();
  const cryptoMode = isBinanceTarget(primary);
  const mode = cryptoMode ? "crypto" : "binary";
  if (chartMainSide.dataset.mode !== mode) {
    if (cryptoMode) {
      const label = `${primary.symbol || primary.label || "Crypto"} K线 / 成交量`;
      chartMainSide.innerHTML = `<option value="all">${escapeHtml(label)}</option>`;
    } else {
      chartMainSide.innerHTML = [
        '<option value="all">Yes + No</option>',
        '<option value="yes">Yes</option>',
        '<option value="no">No</option>',
      ].join("");
    }
    chartMainSide.dataset.mode = mode;
  }
  if (cryptoMode) {
    chartMainSide.value = "all";
    chartMainSide.disabled = true;
    chartMainSide.title = "Crypto leg 使用 K线、成交量、仓位和权益曲线，不适用 Yes/No 主图。";
    return;
  }
  chartMainSide.disabled = false;
  chartMainSide.title = "";
  if (!["all", "yes", "no"].includes(chartMainSide.value)) {
    chartMainSide.value = "all";
  }
}

function syncMarketSelectorInputs(force = false) {
  const primary = trackedMarkets[0] || buildStrategyTarget();
  if (force || !marketConditionInput.value.trim()) {
    marketConditionInput.value = primary?.condition_id || "";
  }
}

function setAutoRefresh(enabled) {
  autoRefreshEnabled = enabled;
  pushDebug("[WS] auto-refresh:set", {
    enabled,
    delta_streams: DELTA_STREAM_INTERVALS,
    boot_ready: workspaceBootReady,
    has_workspace: Boolean(workspaceState),
  });
  Object.values(refreshTimers).forEach((timer) => timer && clearInterval(timer));
  refreshTimers = { chart: null, events: null, workspace: null };
  workspaceAutoRefreshBadge.className = `state-chip ${enabled ? "good" : "pending"}`;
  workspaceAutoRefreshBadge.textContent = enabled ? "live" : "pause";
  workspaceAutoRefreshText.textContent = enabled ? "自动刷新中 · 主图 2s / 统计 5s / 对比 10s / overlay 20s" : "自动刷新已暂停";
  if (!enabled) {
    return;
  }
  if (!workspaceBootReady || !workspaceState) {
    pushDebug("[WS] auto-refresh:defer", {
      row_id: Number(rowId),
      reason: "workspace-not-ready",
    });
    return;
  }
  refreshTimers.chart = setInterval(() => {
    if (!document.hidden) {
      if (isChartLoading) {
        pushDebug("[WS] auto-refresh:skip", {
          row_id: Number(rowId),
          reason: "chart-request-in-flight",
        });
        return;
      }
      pushDebug("[WS] auto-refresh:tick", {
        row_id: Number(rowId),
        delta_streams: DELTA_STREAM_INTERVALS,
        tracked_markets: trackedMarkets.length,
        chart_interval: chartInterval?.value || "",
        due_streams: dueDeltaStreams(),
      });
      refreshChartAuto().catch((error) => {
        if (isAbortError(error)) {
          return;
        }
        setStatus(workspaceCharts, error.message);
      });
    }
  }, DELTA_STREAM_INTERVALS.price);
}

function renderHeader(strategy) {
  workspaceTitle.textContent = strategy.display_name || strategy.strategy || "Unnamed";
  const primary = trackedMarkets[0] || buildDefaultMarketTargets()[0] || {};
  workspaceSubtitle.textContent = isBinanceTarget(primary)
    ? `${primary.symbol || primary.label || "-"} · ${primary.interval || "1m"} · Row ${strategy.row_id || rowId || "-"}`
    : `${strategy.question || "-"} | Row ${strategy.row_id || "-"} | Condition ${strategy.condition_id || "-"}`;
  syncWorkspaceStateControl(strategy || {});
  syncWorkspaceMachineStateControl(strategy || {}, workspaceStateStore);
}

function renderSummary(strategy) {
  if (typeof window.renderSummary === "function" && window.renderSummary !== renderSummary) {
    window.renderSummary(strategy || {});
  }
  syncWorkspaceStateControl(strategy || {});
  syncWorkspaceMachineStateControl(strategy || {}, workspaceStateStore);
  const primary = primaryTrackedMarket();
  if (isBinanceTarget(primary)) {
    const backtestRun = selectedBacktestResults?.selected_run || workspaceState?.backtest?.latest_run || null;
    const metrics = backtestRun?.metrics || backtestRun || {};
    const lastEquity = latestBacktestEquityPoint(backtestRun);
    const lastMeta = lastEquity?.meta || {};
    const pnlValue = metrics.total_return !== undefined && metrics.total_return !== null
      ? formatPercent(metrics.total_return)
      : formatNumber(lastEquity?.pnl ?? strategy.strategy_pnl, 2);
    const equityValue = metrics.final_equity ?? lastEquity?.equity ?? strategy.strategy_bankroll;
    const orderCount = Array.isArray(metrics.orders)
      ? metrics.orders.length
      : (metrics.orders ?? backtestRun?.orders?.length);
    workspaceSummary.innerHTML = [
      summaryCard("Instrument", primary.symbol || primary.label || "-", `${primary.venue || "binance"} · ${primary.interval || "1m"}`),
      summaryCard("Mode", strategy.mode || strategy.state || "-", workspaceViewMode === "backtest" ? `Backtest run ${selectedBacktestRunId || backtestRun?.run_id || "-"}` : "Live workspace"),
      summaryCard("Equity", formatCurrency(equityValue ?? metrics.initial_cash, 2, "USDT"), `Initial ${formatCurrency(metrics.initial_equity ?? metrics.initial_cash, 2, "USDT")}`),
      summaryCard("Return / DD", `${pnlValue} / ${formatPercent(metrics.max_drawdown)}`, `Sharpe ${formatNumber(metrics.sharpe, 2)}`),
      summaryCard("Position", formatPercent(lastMeta.position_ratio), `Qty ${formatNumber(lastMeta.position_qty, 8)} · Mark ${formatCurrency(lastMeta.close, 2, "USDT")}`),
      summaryCard("Trades", formatNumber(orderCount, 0), `Legs ${trackedMarkets.length}`),
    ].join("");
    return;
  }
  workspaceSummary.innerHTML = [
    summaryCard("Yes Bid / Ask", `${formatNumber(strategy.yes_bid, 4)} / ${formatNumber(strategy.yes_ask, 4)}`, `Price Source: ${strategy.price_source || "-"}`),
    summaryCard("No Bid / Ask", `${formatNumber(strategy.no_bid, 4)} / ${formatNumber(strategy.no_ask, 4)}`, `Market Updated: ${formatTime(strategy.market_updated_at)}`),
    summaryCard("Yes Qty / Avg", `${formatNumber(strategy.yes_qty, 4)} / ${formatNumber(strategy.yes_avg, 4)}`, `Position: ${formatPercent(strategy.yes_position)}`),
    summaryCard("No Qty / Avg", `${formatNumber(strategy.no_qty, 4)} / ${formatNumber(strategy.no_avg, 4)}`, `Position: ${formatPercent(strategy.no_position)}`),
    summaryCard("Strategy PnL", formatNumber(strategy.strategy_pnl, 2), `Bankroll: ${formatNumber(strategy.strategy_bankroll, 2)}`),
    summaryCard("Market Legs", `${trackedMarkets.length}`, primary ? `Primary: ${primary.label}` : "No market"),
  ].join("");
}

function renderSources(sourceStatuses) {
  const entries = Object.entries(sourceStatuses || {});
  workspaceSources.innerHTML = entries.map(([key, value]) => {
    const tone = value.status === "good" ? "good" : value.status === "error" ? "error" : "pending";
    const text = value.path || value.value || (value.exists !== undefined ? String(value.exists) : "-");
    const tail = value.updated_at ? ` | ${formatTime(value.updated_at)}` : "";
    return `
      <div class="info-item">
        <div class="info-item-header">
          <strong>${escapeHtml(key)}</strong>
          <span class="state-chip ${tone}">${escapeHtml(value.status || "pending")}</span>
        </div>
        <div>${escapeHtml(text)}${tail ? `<span class="muted">${escapeHtml(tail)}</span>` : ""}</div>
      </div>
    `;
  }).join("");
}

function backtestEquityPoints(run = {}) {
  return (run.equity || [])
    .map((point) => ({
      ...point,
      equity_value: numericValue(point.equity),
      pnl_value: numericValue(point.pnl),
    }))
    .filter((point) => point.equity_value !== null)
    .sort((a, b) => String(a.ts_utc || "").localeCompare(String(b.ts_utc || "")));
}

function latestBacktestEquityPoint(run = {}) {
  const points = backtestEquityPoints(run || {});
  return points.length ? points[points.length - 1] : null;
}

function backtestPositionSnapshot(run = {}) {
  const latest = latestBacktestEquityPoint(run);
  const meta = latest?.meta && typeof latest.meta === "object" ? latest.meta : {};
  return {
    ts: latest?.ts_utc || "",
    equity: latest?.equity,
    cash: latest?.cash,
    exposure: latest?.exposure,
    pnl: latest?.pnl,
    position_ratio: meta.position_ratio,
    position_qty: meta.position_qty,
    mark_price: meta.close,
  };
}

function disposeWorkspaceBacktestChart() {
  if (workspaceBacktestEquityChart) {
    workspaceBacktestEquityChart.dispose();
    workspaceBacktestEquityChart = null;
  }
}

function renderWorkspaceBacktestEquity(run = {}) {
  const chartEl = document.getElementById("workspaceBacktestEquityChart");
  if (!chartEl) return;
  const points = backtestEquityPoints(run);
  if (!points.length) {
    disposeWorkspaceBacktestChart();
    chartEl.innerHTML = `<div class="workspace-backtest-empty">暂无资金曲线数据</div>`;
    return;
  }
  if (!window.echarts) {
    disposeWorkspaceBacktestChart();
    chartEl.innerHTML = `<div class="workspace-backtest-empty">图表库未加载</div>`;
    return;
  }
  disposeWorkspaceBacktestChart();
  workspaceBacktestEquityChart = window.echarts.init(chartEl, null, { renderer: "canvas" });
  workspaceBacktestEquityChart.setOption({
    animation: true,
    backgroundColor: "transparent",
    grid: { left: 48, right: 14, top: 18, bottom: 28 },
    tooltip: { trigger: "axis", confine: true },
    xAxis: {
      type: "category",
      data: points.map((point) => point.ts_utc || ""),
      axisLabel: { color: "#8fb3dc", hideOverlap: true },
      axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.22)" } },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "#8fb3dc" },
      splitLine: { lineStyle: { color: "rgba(148, 163, 184, 0.12)" } },
    },
    series: [{
      name: "Equity",
      type: "line",
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2, color: "#56a7ff" },
      areaStyle: { color: "rgba(86, 167, 255, 0.12)" },
      data: points.map((point) => point.equity_value),
    }],
  }, true);
}

function renderWorkspaceBacktestOrders(run = {}) {
  const ordersEl = document.getElementById("workspaceBacktestOrders");
  if (!ordersEl) return;
  const orders = (run.orders || []).slice(-12).reverse();
  if (!orders.length) {
    ordersEl.innerHTML = `<div class="workspace-backtest-empty">暂无订单明细</div>`;
    return;
  }
  ordersEl.innerHTML = `
    <div class="workspace-backtest-table-scroll">
      <table class="workspace-backtest-table">
        <thead><tr><th>Time</th><th>Instrument</th><th>Side</th><th>Qty</th><th>Price</th><th>Fee</th></tr></thead>
        <tbody>
          ${orders.map((order) => `
            <tr>
              <td class="mono">${escapeHtml(formatTime(order.ts_utc))}</td>
              <td>${escapeHtml(order.instrument_id || order.leg_id || "-")}</td>
              <td>${escapeHtml(order.side || "-")}</td>
              <td class="num">${escapeHtml(formatNumber(order.quantity, 8))}</td>
              <td class="num">${escapeHtml(formatNumber(order.price, 6))}</td>
              <td class="num">${escapeHtml(formatNumber(order.fee, 4))}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function backtestKpiCard(label, value, subvalue = "") {
  return `
    <div class="workspace-backtest-kpi">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      ${subvalue ? `<small>${escapeHtml(subvalue)}</small>` : ""}
    </div>
  `;
}

function renderWorkspaceBacktestPositions(run = {}) {
  const el = document.getElementById("workspaceBacktestPositions");
  if (!el) return;
  const snapshot = backtestPositionSnapshot(run);
  if (!snapshot.ts) {
    el.innerHTML = `<div class="workspace-backtest-empty">暂无仓位快照</div>`;
    return;
  }
  const rows = [
    ["时间", formatTime(snapshot.ts)],
    ["仓位比例", formatPercent(snapshot.position_ratio)],
    ["BTC 数量", formatNumber(snapshot.position_qty, 8)],
    ["标记价格", formatCurrency(snapshot.mark_price, 2, "USDT")],
    ["权益", formatCurrency(snapshot.equity, 2, "USDT")],
    ["现金", formatCurrency(snapshot.cash, 2, "USDT")],
    ["敞口", formatCurrency(snapshot.exposure, 2, "USDT")],
    ["PnL", formatCurrency(snapshot.pnl, 2, "USDT")],
  ];
  el.innerHTML = `
    <div class="workspace-backtest-position-grid">
      ${rows.map(([label, value]) => `
        <div class="workspace-backtest-position-item">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `).join("")}
    </div>
  `;
}

function renderWorkspaceBacktestAnalysis(run = null) {
  if (!run) {
    disposeWorkspaceBacktestChart();
    return;
  }
  renderWorkspaceBacktestEquity(run);
  renderWorkspaceBacktestOrders(run);
  renderWorkspaceBacktestPositions(run);
}

function renderBacktest(backtest) {
  if (!backtest) {
    setStatus(workspaceBacktest, "Backtest placeholder is unavailable.");
    return;
  }
  const latest = backtest.latest_run || null;
  const selectedRun = selectedBacktestResults?.selected_run || null;
  const selectedMetrics = selectedRun?.metrics || {};
  const selectedWindow = selectedRun ? backtestRunWindow(selectedRun) : {};
  const selectedLatest = selectedRun ? latestBacktestEquityPoint(selectedRun) : null;
  const selectedOrderCount = selectedRun
    ? (Array.isArray(selectedMetrics.orders) ? selectedMetrics.orders.length : (selectedMetrics.orders ?? selectedRun.orders?.length))
    : null;
  const viewTone = workspaceViewMode === "backtest" ? "pending" : "good";
  const selectedMetricsLine = selectedRun
    ? [
        selectedWindow.start || selectedWindow.end ? `${formatTime(selectedWindow.start)} -> ${formatTime(selectedWindow.end)}` : "",
        selectedMetrics.total_return !== null && selectedMetrics.total_return !== undefined ? `Return ${formatPercent(selectedMetrics.total_return)}` : "",
        selectedMetrics.max_drawdown !== null && selectedMetrics.max_drawdown !== undefined ? `DD ${formatPercent(selectedMetrics.max_drawdown)}` : "",
        selectedMetrics.sharpe !== null && selectedMetrics.sharpe !== undefined ? `Sharpe ${formatNumber(selectedMetrics.sharpe, 2)}` : "",
        selectedMetrics.engine ? `Engine ${selectedMetrics.engine}` : "",
      ].filter(Boolean).join(" | ")
    : "";
  const latestMetrics = latest
    ? [
        latest.period_start || latest.period_end ? `${formatTime(latest.period_start)} -> ${formatTime(latest.period_end)}` : "",
        latest.total_return !== null && latest.total_return !== undefined ? `Return ${formatPercent(latest.total_return)}` : "",
        latest.max_drawdown !== null && latest.max_drawdown !== undefined ? `DD ${formatPercent(latest.max_drawdown)}` : "",
        latest.sharpe !== null && latest.sharpe !== undefined ? `Sharpe ${formatNumber(latest.sharpe, 2)}` : "",
      ].filter(Boolean).join(" | ")
    : "No backtest run yet.";
  const statusTone = backtest.status === "ready" ? "good" : backtest.status === "metadata_ready" ? "pending" : "neutral";
  const latestTone = latest?.tone || "neutral";
  const actionLabel = backtest.status === "ready" ? "创建回测" : "保存样例";
  workspaceBacktest.innerHTML = `
    <div class="info-item">
      <div class="info-item-header">
        <strong>${escapeHtml(backtest.title || "Backtest")}</strong>
        <span class="state-chip ${statusTone}">${escapeHtml(backtest.status || "planned")}</span>
      </div>
      <div class="table-actions" style="justify-content:flex-start;margin:6px 0 8px;">
        <button class="ws3-btn ${workspaceViewMode === "live" ? "" : "ws3-btn-ghost"}" type="button" data-workspace-view="live">实盘</button>
        <button class="ws3-btn ${workspaceViewMode === "backtest" ? "" : "ws3-btn-ghost"}" type="button" data-workspace-view="backtest" ${selectedRun || latest ? "" : "disabled"}>回测</button>
        <span class="state-chip ${viewTone}">${workspaceViewMode === "backtest" ? "Backtest view" : "Live view"}</span>
      </div>
      <div>${escapeHtml(backtest.summary || "")}</div>
      <div class="muted">Default cash ${escapeHtml(String(backtest.defaults?.start_cash ?? "-"))} | ${escapeHtml((backtest.legs || []).length)} legs | ${escapeHtml((backtest.recent_runs || []).length)} recent runs</div>
      ${selectedRun ? `
        <div class="info-item-header" style="margin-top:10px;">
          <span class="state-chip ${selectedRun.status === "completed" ? "good" : selectedRun.status === "failed" ? "error" : "pending"}">Run ${escapeHtml(String(selectedRun.run_id))}</span>
          <span class="muted">${escapeHtml(selectedMetricsLine || selectedRun.status || "")}</span>
        </div>
        <div class="workspace-backtest-analysis">
          <div class="workspace-backtest-kpi-grid">
            ${backtestKpiCard("总收益", formatPercent(selectedMetrics.total_return), `PnL ${formatCurrency(selectedLatest?.pnl, 2, "USDT")}`)}
            ${backtestKpiCard("最大回撤", formatPercent(selectedMetrics.max_drawdown), `Sharpe ${formatNumber(selectedMetrics.sharpe, 2)}`)}
            ${backtestKpiCard("最终权益", formatCurrency(selectedMetrics.final_equity ?? selectedLatest?.equity, 2, "USDT"), `初始 ${formatCurrency(selectedMetrics.initial_equity ?? selectedMetrics.initial_cash, 2, "USDT")}`)}
            ${backtestKpiCard("订单数", formatNumber(selectedOrderCount, 0), `${formatNumber((selectedRun.equity || []).length, 0)} equity points`)}
          </div>
          <div class="workspace-backtest-analysis-head">
            <strong>权益曲线</strong>
            <span class="muted">${escapeHtml(String((selectedRun.equity || []).length))} points · ${(selectedRun.orders || []).length} orders</span>
          </div>
          <div id="workspaceBacktestEquityChart" class="workspace-backtest-equity-chart"></div>
          <div class="workspace-backtest-analysis-head" style="margin-top:8px;">
            <strong>当前仓位</strong>
            <span class="muted">latest equity snapshot</span>
          </div>
          <div id="workspaceBacktestPositions"></div>
          <div class="workspace-backtest-analysis-head" style="margin-top:8px;">
            <strong>最近订单</strong>
            <span class="muted">latest 12</span>
          </div>
          <div id="workspaceBacktestOrders"></div>
        </div>
      ` : ""}
      <div class="info-item-header" style="margin-top:10px;">
        <span class="state-chip ${latestTone}">${escapeHtml(latest?.status || "none")}</span>
        <span class="muted">${escapeHtml(latestMetrics)}</span>
      </div>
      <div class="table-actions" style="justify-content:flex-start;margin-top:10px;">
        <button class="ws3-btn" type="button" data-backtest-create>${escapeHtml(actionLabel)}</button>
        ${selectedRun?.run_id ? `<a class="ws3-btn ws3-btn-ghost" href="/backtests/${escapeHtml(String(selectedRun.run_id))}" target="_blank" rel="noopener noreferrer">当前报告</a>` : ""}
        ${latest?.report_url ? `<a class="ws3-btn ws3-btn-ghost" href="${escapeHtml(latest.report_url)}" target="_blank" rel="noopener noreferrer">打开报告</a>` : ""}
        <a class="ws3-btn ws3-btn-ghost" href="/history" target="_blank" rel="noopener noreferrer">History</a>
      </div>
    </div>
  `;
  renderWorkspaceBacktestAnalysis(selectedRun);
}

async function createWorkspaceBacktest() {
  if (!workspaceBacktest) return;
  const button = workspaceBacktest.querySelector("[data-backtest-create]");
  if (button) {
    button.disabled = true;
    button.textContent = "创建中...";
  }
  const idleLabel = button?.textContent === "创建中..."
    ? (workspaceState?.backtest?.status === "ready" ? "创建回测" : "保存样例")
    : "创建回测";
  const range = currentTimeRangeParams();
  const dataWindow = {};
  if (range.from) dataWindow.start = range.from;
  if (range.to) dataWindow.end = range.to;
  const backtestReady = workspaceState?.backtest?.status === "ready";
  try {
    const payload = await fetchJson(`/api/polymarket/strategies/${rowId}/backtest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        data_window: dataWindow,
        metadata_only: !backtestReady,
        strict_window: Boolean(dataWindow.start || dataWindow.end),
      }),
    });
    const run = payload?.data?.run || {};
    pushDebug("[WS] backtest:create", {
      row_id: Number(rowId),
      run_id: run.run_id,
      case_id: payload?.data?.case?.case_id,
      status: run.status,
    });
    await loadWorkspace(false, true);
    const reportUrl = payload?.data?.report_url;
    if (reportUrl) {
      window.open(reportUrl, "_blank", "noopener,noreferrer");
    }
  } catch (error) {
    setStatus(workspaceBacktest, `创建回测失败: ${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = idleLabel;
    }
  }
}

async function switchWorkspaceView(mode) {
  const nextMode = mode === "backtest" ? "backtest" : "live";
  if (nextMode === "backtest") {
    const targetRunId = selectedBacktestRunId || workspaceState?.backtest?.latest_run?.run_id;
    if (!targetRunId) return;
    workspaceViewMode = "backtest";
    selectedBacktestRunId = String(targetRunId);
    setBacktestUrlState("backtest", selectedBacktestRunId);
    disconnectWorkspaceLive();
    setAutoRefresh(false);
    const payload = await fetchJson(`/api/polymarket/strategies/${rowId}/backtest/results?run_id=${encodeURIComponent(selectedBacktestRunId)}`);
    selectedBacktestResults = payload?.data || null;
    window.selectedBacktestResults = selectedBacktestResults;
    window.workspaceViewMode = workspaceViewMode;
    if (selectedBacktestResults?.selected_run) {
      applyBacktestWindowToChart(selectedBacktestResults.selected_run);
    }
    renderSummary(workspaceState?.strategy || {});
    renderWorkspaceModeEvents();
    renderBacktest(workspaceState?.backtest || null);
    updateWorkspaceViewBadge();
    await loadChart();
    return;
  }
  workspaceViewMode = "live";
  selectedBacktestRunId = "";
  selectedBacktestResults = null;
  backtestWindowAppliedForRun = "";
  window.selectedBacktestResults = selectedBacktestResults;
  window.workspaceViewMode = workspaceViewMode;
  setBacktestUrlState("live");
  renderSummary(workspaceState?.strategy || {});
  renderWorkspaceModeEvents();
  renderBacktest(workspaceState?.backtest || null);
  if (workspaceBootReady) {
    connectWorkspaceLive();
    setAutoRefresh(true);
  }
  await loadChart();
}

function buildFieldControl(field, editable) {
  const value = editable?.[field.key];
  const description = buildFieldDescription(field);
  const label = strategyParamLabel(field.key, field.label);
  const keyChip = field.key && normalizeParamKey(label) !== normalizeParamKey(field.key)
    ? `<span class="settings-field-key">${escapeHtml(field.key)}</span>`
    : "";
  const tooltip = description
    ? `<span class="settings-help" tabindex="0" title="${escapeHtml(description)}" aria-label="${escapeHtml(description)}">?</span>`
    : "";
  const autofill = field.group === "inputs"
    ? `<button type="button" class="settings-autofill" data-autofill-key="${escapeHtml(field.key)}" title="UseData" aria-label="UseData" hidden>↻</button>`
    : "";
  if (field.type === "boolean") {
    return `
      <label class="checkbox workspace-checkbox">
        <input type="checkbox" data-setting-key="${escapeHtml(field.key)}" ${String(value).toLowerCase() === "true" ? "checked" : ""}>
        <span class="settings-label-text"><span>${escapeHtml(label)}</span>${keyChip}${tooltip}</span>
      </label>
    `;
  }
  return `
    <label class="settings-field" title="${escapeHtml(description)}">
      <span class="settings-label-text"><span>${escapeHtml(label)}</span>${field.required ? '<span class="settings-required">*</span>' : ""}${keyChip}${tooltip}</span>
      <span class="settings-input-row">
        <input
          data-setting-key="${escapeHtml(field.key)}"
          type="${field.type === "number" ? "number" : "text"}"
          ${field.type === "number" ? 'step="any"' : ""}
          value="${escapeHtml(value ?? "")}"
        >
        ${autofill}
      </span>
    </label>
  `;
}
function buildFieldDescription(field) {
  const parts = [];
  const hint = strategyParamHint(field.key);
  if (hint) parts.push(hint);
  if (field.description && String(field.description) !== hint) parts.push(String(field.description));
  if (field.required) parts.push("必填参数");
  if (field.source === "strategy_code") parts.push("说明来自策略代码 FunctionIntroduction");
  if (field.key) parts.push(`字段名: ${field.key}`);
  return parts.join("\n");
}

function normalizeSettingKey(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function candidateUseDataKeys(settingKey) {
  const key = String(settingKey || "").trim();
  const normalized = normalizeSettingKey(key);
  const aliases = {
    enddate: ["Enddate", "L0_EndTime", "end_date", "EndTime"],
    endtime: ["L0_EndTime", "Enddate", "end_date"],
    startday: ["start_day", "StartDay", "day_to_end", "L0_DayToEnd", "DayToEnd"],
    daytoend: ["day_to_end", "L0_DayToEnd", "DayToEnd"],
    daystoend: ["day_to_end", "L0_DayToEnd", "DayToEnd"],
    hourtoend: ["hour_to_end", "L0_HourToEnd", "HourToEnd"],
    hourstoend: ["hour_to_end", "L0_HourToEnd", "HourToEnd"],
    budgetcap: ["BudgetCap", "L0_BudgetCap"],
    strategybankroll: ["StrategyBankroll"],
    legcount: ["LegCount"],
  };
  return [key, ...(aliases[normalized] || [])];
}

function findUseDataValue(useData, settingKey, options = {}) {
  if (!useData || typeof useData !== "object") {
    console.log("[workspace-autofill] invalid UseData", { settingKey, useData });
    return null;
  }
  const includeOriginalKey = options.includeOriginalKey !== false;
  const normalizedSettingKey = normalizeSettingKey(settingKey);
  const candidates = candidateUseDataKeys(settingKey).filter((key, index) => {
    if (includeOriginalKey) return true;
    return index > 0 && normalizeSettingKey(key) !== normalizedSettingKey;
  });
  console.log("[workspace-autofill] candidates", { settingKey, includeOriginalKey, candidates, useDataKeys: Object.keys(useData) });
  for (const key of candidates) {
    if (Object.prototype.hasOwnProperty.call(useData, key) && isUsableWorkspaceUseDataValue(useData[key])) {
      console.log("[workspace-autofill] matched direct key", { settingKey, key, value: useData[key] });
      return { key, value: useData[key] };
    }
  }
  if (!includeOriginalKey) {
    console.log("[workspace-autofill] normalized match skipped for input_json echo", { settingKey });
    return null;
  }
  const normalized = normalizedSettingKey;
  const matchKey = Object.keys(useData).find((key) => normalizeSettingKey(key) === normalized && isUsableWorkspaceUseDataValue(useData[key]));
  console.log("[workspace-autofill] normalized match", { settingKey, normalized, matchKey, value: matchKey ? useData[matchKey] : undefined });
  if (matchKey) {
    return { key: matchKey, value: useData[matchKey] };
  }
  return null;
}

function isUsableWorkspaceUseDataValue(value) {
  return value !== undefined && value !== null && String(value).trim() !== "";
}

let useDataSnapshotCache = null;
let useDataSnapshotLoadedAt = 0;

async function loadUseDataSnapshot() {
  const now = Date.now();
  if (useDataSnapshotCache && now - useDataSnapshotLoadedAt < 5000) {
    console.log("[workspace-autofill] cache hit", { rowId, useDataSnapshotCache });
    return useDataSnapshotCache;
  }
  const url = `/api/polymarket/strategies/${rowId}/usedata`;
  console.log("[workspace-autofill] fetch", { rowId, url });
  const payload = await fetchJson(url);
  useDataSnapshotCache = payload.data?.data || {};
  useDataSnapshotLoadedAt = now;
  console.log("[workspace-autofill] response", { rowId, payload, useDataSnapshotCache });
  return useDataSnapshotCache;
}

async function autofillSettingFromUseData(button) {
  const key = button?.dataset?.autofillKey || "";
  console.log("[workspace-autofill] click", { key, rowId, button });
  const field = Array.from(settingsForm.querySelectorAll("[data-setting-key]")).find((item) => item.dataset.settingKey === key);
  if (!field) {
    console.log("[workspace-autofill] skipped: field not found", { key });
    return;
  }
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "...";
  settingsMessage.textContent = "正在从 UseData 读取参数...";
  try {
    const useData = await loadUseDataSnapshot();
    const match = findUseDataValue(useData, key, { includeOriginalKey: false });
    if (!match) {
      console.log("[workspace-autofill] no match", { key, rowId, useData });
      settingsMessage.textContent = `UseData 中没有找到 ${key} 的可用字段。`;
      return;
    }
    const nextValue = match.value == null ? "" : String(match.value);
    if (field.type === "checkbox") {
      field.checked = ["1", "true", "yes", "y", "on"].includes(nextValue.trim().toLowerCase());
    } else {
      field.value = nextValue;
    }
    console.log("[workspace-autofill] filled", { key, matchedKey: match.key, value: match.value, fieldValue: field.type === "checkbox" ? field.checked : field.value });
    settingsMessage.textContent = `已从 UseData.${match.key} 填入 ${key}，保存后生效。`;
  } catch (error) {
    console.log("[workspace-autofill] error", { key, rowId, error });
    settingsMessage.textContent = `UseData 读取失败: ${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = previous || "↻";
  }
}

async function refreshSettingsUseDataControls({ fillEmpty = false } = {}) {
  const fields = Array.from(settingsForm.querySelectorAll("[data-setting-key]"));
  const buttons = Array.from(settingsForm.querySelectorAll("[data-autofill-key]"));
  buttons.forEach((button) => {
    button.hidden = true;
    button.disabled = true;
  });
  if (!fields.length || !buttons.length) return;
  try {
    const useData = await loadUseDataSnapshot();
    let visibleCount = 0;
    let filledCount = 0;
    for (const field of fields) {
      const key = field.dataset.settingKey || "";
      const button = buttons.find((item) => item.dataset.autofillKey === key);
      if (!button) continue;
      const match = findUseDataValue(useData, key, { includeOriginalKey: false });
      if (!match || !isUsableWorkspaceUseDataValue(match.value)) {
        button.hidden = true;
        continue;
      }
      visibleCount += 1;
      button.hidden = false;
      button.disabled = false;
      button.dataset.useDataKey = match.key;
      if (fillEmpty && String(field.value ?? "").trim() === "") {
        if (field.type === "checkbox") {
          field.checked = ["1", "true", "yes", "y", "on"].includes(String(match.value).trim().toLowerCase());
        } else {
          field.value = String(match.value);
        }
        filledCount += 1;
      }
    }
    console.log("[workspace-autofill] refreshed controls", { visibleCount, filledCount, fillEmpty });
    if (settingsMessage && filledCount > 0) {
      settingsMessage.textContent = `已从 UseData 自动填入 ${filledCount} 个参数，保存后生效。`;
    }
  } catch (error) {
    console.log("[workspace-autofill] refresh controls error", { rowId, error });
  }
}

function parameterGroupKey(field = {}) {
  const key = normalizeParamKey(field.key || field.label);
  const match = PARAM_GROUPS.find((group) => group.keys.includes(key));
  return match?.key || "other";
}

function renderInputFieldGroups(fields, editable) {
  const grouped = new Map(PARAM_GROUPS.map((group) => [group.key, { ...group, fields: [] }]));
  grouped.set("other", { key: "other", title: "其他参数", fields: [] });
  (fields || []).forEach((field) => {
    const key = parameterGroupKey(field);
    if (!grouped.has(key)) grouped.set(key, { key, title: "其他参数", fields: [] });
    grouped.get(key).fields.push(field);
  });
  return [...grouped.values()]
    .filter((group) => group.fields.length)
    .map((group) => `
      <div class="workspace-settings-subgroup">
        <div class="workspace-settings-subtitle">${escapeHtml(group.title)}</div>
        <div class="grid two">${group.fields.map((field) => buildFieldControl(field, editable)).join("")}</div>
      </div>
    `)
    .join("");
}

function renderSettings(schema, strategy) {
  const editable = strategy.editable || {};
  const stateStore = workspaceStateStore || {};
  const pastePlaceholder = isBinanceTarget(primaryTrackedMarket())
    ? '{"entry_z": 0.002, "exit_z": 0.0005, "fast_window": 20, "slow_window": 60}'
    : '{"fair_price": 0.40, "entry_edge": 0.05}';
  const groups = {
    inputs: schema.filter((field) => field.group === "inputs"),
    capital: schema.filter((field) => field.group === "capital"),
  };
  const mode = stateStore.mode || workspaceStrategyMode(strategy);
  const runtimeEditable = mode === "Stop";
  settingsForm.innerHTML = `
    <div class="settings-param-paste">
      <div class="settings-param-paste-head">
        <span>批量粘贴参数</span>
        <button type="button" class="settings-paste-apply" data-settings-paste-apply>填入匹配字段</button>
      </div>
      <textarea data-settings-paste-text spellcheck="false" placeholder='${escapeHtml(pastePlaceholder)}'></textarea>
    </div>
    <div class="workspace-settings-group">
      <h3>策略参数</h3>
      ${renderInputFieldGroups(groups.inputs, editable) || '<div class="ws3-status">当前策略没有可编辑输入参数。</div>'}
    </div>
    ${renderWorkspaceMachineStateSection(stateStore, strategy)}
    ${renderWorkspaceControlsSection(stateStore)}
    ${renderWorkspaceRuntimeSection(stateStore, runtimeEditable)}
    <div class="workspace-settings-group">
      <h3>Capital</h3>
      <div class="grid two">${groups.capital.map((field) => buildFieldControl(field, editable)).join("")}</div>
    </div>
    <div class="actions">
      <button type="submit">保存策略设置</button>
    </div>
  `;
  settingsMessage.textContent = "已加载策略设置。";
  refreshSettingsUseDataControls({ fillEmpty: true });
}

function stateEditorJson(value) {
  const obj = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return JSON.stringify(obj, null, 2);
}

function stateSchemaType(meta) {
  const type = String(meta?.type || "").toLowerCase();
  if (["number", "integer", "float", "int"].includes(type)) return "number";
  if (["bool", "boolean"].includes(type)) return "boolean";
  return "text";
}

function stateBoolValue(value) {
  if (typeof value === "boolean") return value;
  return ["1", "true", "yes", "y", "on"].includes(String(value ?? "").trim().toLowerCase());
}

function stateLabel(key, meta) {
  return meta?.label || key;
}

function stateDescription(key, meta) {
  return [meta?.description, meta?.default !== undefined ? `Default: ${meta.default}` : ""]
    .filter(Boolean)
    .join("\n");
}

function stateFieldKeys(schema, ...valueObjects) {
  return Array.from(new Set([
    ...Object.keys(schema || {}),
    ...valueObjects.flatMap((obj) => Object.keys(obj || {})),
  ]));
}

function buildWorkspaceStateField(key, meta, values, namespace, options = {}) {
  const type = stateSchemaType(meta);
  const value = values?.[key] ?? meta?.default ?? "";
  const declared = options.declared !== false;
  const description = declared ? stateDescription(key, meta) : "未在当前策略代码的 RuntimeStateSchema 中声明。";
  const disabled = options.disabled ? "disabled" : "";
  const badge = declared ? "" : '<span class="state-undeclared-badge">未声明</span>';
  const tooltip = description
    ? `<span class="settings-help" tabindex="0" title="${escapeHtml(description)}" aria-label="${escapeHtml(description)}">?</span>`
    : "";
  const comment = description ? `<span class="state-field-comment">${escapeHtml(description.split("\n")[0])}</span>` : "";
  if (type === "boolean") {
    return `
      <label class="settings-field state-boolean-field" title="${escapeHtml(description)}">
        <span class="settings-label-text">${escapeHtml(stateLabel(key, meta))}${badge}${tooltip}</span>
        <span class="state-checkbox-row">
          <input type="checkbox" data-${namespace}-state-key="${escapeHtml(key)}" ${stateBoolValue(value) ? "checked" : ""} ${disabled}>
          <span>${escapeHtml(key)}</span>
        </span>
        ${comment}
      </label>
    `;
  }
  return `
    <label class="settings-field" title="${escapeHtml(description)}">
      <span class="settings-label-text">${escapeHtml(stateLabel(key, meta))}${badge}${tooltip}</span>
      <input data-${namespace}-state-key="${escapeHtml(key)}" type="${type === "number" ? "number" : "text"}" ${type === "number" ? 'step="any"' : ""} value="${escapeHtml(value ?? "")}" ${disabled}>
      ${comment}
    </label>
  `;
}

function renderWorkspaceMachineStateSection(stateStore, strategy) {
  const schema = stateStore?.state_machine_schema || {};
  const current = workspaceMachineState(strategy || workspaceState?.strategy || {}, stateStore);
  const options = workspaceStateOptions(strategy || workspaceState?.strategy || {}, stateStore);
  return `
    <div class="workspace-settings-group">
      <h3>Strategy State</h3>
      <div class="grid two">
        <label class="settings-field">
          <span class="settings-label-text">${escapeHtml(schema.label || "State")}</span>
          <select data-machine-state-key="state">
            ${options.map((item) => {
              const value = String(item.value ?? item);
              const label = String(item.label ?? value);
              return `<option value="${escapeHtml(value)}"${value === current ? " selected" : ""}>${escapeHtml(label)}</option>`;
            }).join("")}
          </select>
          <span class="state-field-comment">${escapeHtml(schema.description || "Independent from Stop / Virtual / Real mode.")}</span>
        </label>
      </div>
    </div>
  `;
}

function renderWorkspaceControlsSection(stateStore) {
  const schema = stateStore.controls_schema || {};
  const values = stateStore.controls || stateStore.user || stateStore.user_overrides || {};
  const effective = stateStore.controls || stateStore.user || {};
  const fields = Object.keys(schema).map((key) => buildWorkspaceStateField(key, schema[key], values, "user")).join("");
  return `
    <div class="workspace-settings-group">
      <h3>Controls / UserState</h3>
      ${fields ? `<div class="grid two">${fields}</div>` : '<div class="ws3-status">当前策略没有声明 ControlsSchema。</div>'}
      <details class="state-effective-details">
        <summary>查看有效值 JSON</summary>
        <pre class="state-effective-preview" data-user-state-effective>${escapeHtml(stateEditorJson(effective))}</pre>
      </details>
      <div class="state-editor-actions">
        <button type="button" class="mini ghost" data-clear-user-state>Clear Controls Override</button>
      </div>
    </div>
  `;
}

function renderWorkspaceRuntimeSection(stateStore, runtimeEditable) {
  const schema = stateStore.runtime_state_schema || stateStore.schemas?.runtime || {};
  const runtime = stateStore.runtime || {};
  const values = { ...runtime, ...(stateStore.runtime_overrides || {}) };
  const fields = stateFieldKeys(schema, runtime, stateStore.runtime_overrides || {})
    .map((key) => buildWorkspaceStateField(key, schema[key] || { type: "string" }, values, "runtime", {
      disabled: !runtimeEditable,
      declared: Object.prototype.hasOwnProperty.call(schema, key),
    }))
    .join("");
  return `
    <div class="workspace-settings-group">
      <details class="runtime-state-details">
        <summary>
          <span>RuntimeState</span>
          <span class="state-editor-badge ${runtimeEditable ? "editable" : "locked"}">${runtimeEditable ? "Stop 时可编辑" : "运行中只读"}</span>
        </summary>
        <div class="state-editor-caption">策略运行记忆。字段来自当前策略代码 RuntimeStateSchema；运行中主要由策略写入。</div>
        ${fields ? `<div class="grid two">${fields}</div>` : '<div class="ws3-status">当前策略还没有 RuntimeState。</div>'}
        <details class="state-effective-details">
          <summary>查看有效值 JSON</summary>
          <pre class="state-effective-preview" data-runtime-state-effective>${escapeHtml(stateEditorJson(runtime))}</pre>
        </details>
      </details>
      <div class="state-editor-actions">
        <button type="button" class="mini ghost" data-clear-runtime-state ${runtimeEditable ? "" : "disabled"}>Clear Runtime Override</button>
      </div>
    </div>
  `;
}

function renderMetricPicker(capabilities, defaults) {
  const allowed = new Set(capabilities?.sub_allowed || []);
  const selected = new Set(defaults?.sub_series || []);
  const metricCatalog = capabilities?.metric_catalog || {};
  const numericMetrics = (metricCatalog.numeric || []).filter((item) => !isBacktestDerivedMetricItem(item));
  const backtestMetrics = (metricCatalog.numeric || []).filter(isBacktestDerivedMetricItem);
  const stateMetrics = (metricCatalog.state || []).filter((item) => !isBacktestDerivedMetricItem(item)).filter(isStateLaneMetricDisplayable);
  const backtestStateMetrics = (metricCatalog.state || []).filter(isBacktestDerivedMetricItem).filter(isStateLaneMetricDisplayable);
  const missingBacktestStrategyCatalog = workspaceViewMode === "backtest"
    && selectedBacktestRunId
    && !numericMetrics.length
    && !stateMetrics.length
    && (backtestMetrics.length || backtestStateMetrics.length);
  chartMetricPicker.innerHTML = `
    <div class="metric-picker-title">副图组</div>
    <div class="metric-picker-groups">
      ${SUB_METRIC_GROUPS.map((group) => {
        const keys = group.keys.filter((key) => allowed.has(key));
        if (!keys.length) return "";
        return `
          <div class="metric-picker-group">
            <div class="metric-picker-group-title">${escapeHtml(group.title)}</div>
            <div class="metric-picker-options">
              ${keys.map((key) => `
                <label class="checkbox">
                  <input type="checkbox" data-sub-metric="${escapeHtml(key)}" ${selected.has(key) ? "checked" : ""}>
                  ${escapeHtml(SUB_METRIC_LABELS[key] || key)}
                </label>
              `).join("")}
            </div>
          </div>
        `;
      }).join("")}
      ${numericMetrics.length ? `
        <div class="metric-picker-group">
          <div class="metric-picker-group-title">Strategy Metrics</div>
          <div class="metric-picker-options">
            ${numericMetrics.map((item) => {
              const key = `metric:${item.key}`;
              return `
                <label class="checkbox">
                  <input type="checkbox" data-sub-metric="${escapeHtml(key)}" ${selected.has(key) ? "checked" : ""}>
                  ${escapeHtml(item.label || item.key)}
                </label>
              `;
            }).join("")}
          </div>
        </div>
      ` : (missingBacktestStrategyCatalog ? `
        <div class="metric-picker-group">
          <div class="metric-picker-group-title">Strategy Metrics</div>
          <div class="metric-picker-empty">当前回测 run 没有保存策略代码返回的 metrics；重新回测后会显示策略内部数值指标。</div>
        </div>
      ` : "")}
      ${backtestMetrics.length ? `
        <div class="metric-picker-group">
          <div class="metric-picker-group-title">Backtest Metrics</div>
          <div class="metric-picker-options">
            ${backtestMetrics.map((item) => {
              const key = `metric:${item.key}`;
              return `
                <label class="checkbox">
                  <input type="checkbox" data-sub-metric="${escapeHtml(key)}" ${selected.has(key) ? "checked" : ""}>
                  ${escapeHtml(item.label || item.key)}
                </label>
              `;
            }).join("")}
          </div>
        </div>
      ` : (missingBacktestStrategyCatalog ? `
        <div class="metric-picker-group">
          <div class="metric-picker-group-title">State Lanes</div>
          <div class="metric-picker-empty">当前回测 run 没有保存策略代码返回的状态字段；重新回测后会显示状态机与因子状态色带。</div>
        </div>
      ` : "")}
      ${stateMetrics.length ? `
        <div class="metric-picker-group">
          <div class="metric-picker-group-title">State Lanes</div>
          <div class="metric-picker-options">
            ${stateMetrics.map((item) => {
              const key = `metric_state:${item.key}`;
              return `
                <label class="checkbox">
                  <input type="checkbox" data-sub-metric="${escapeHtml(key)}" ${selected.has(key) ? "checked" : ""}>
                  ${escapeHtml(item.label || item.key)}
                </label>
              `;
            }).join("")}
          </div>
        </div>
      ` : ""}
      ${backtestStateMetrics.length ? `
        <div class="metric-picker-group">
          <div class="metric-picker-group-title">Backtest State</div>
          <div class="metric-picker-options">
            ${backtestStateMetrics.map((item) => {
              const key = `metric_state:${item.key}`;
              return `
                <label class="checkbox">
                  <input type="checkbox" data-sub-metric="${escapeHtml(key)}" ${selected.has(key) ? "checked" : ""}>
                  ${escapeHtml(item.label || item.key)}
                </label>
              `;
            }).join("")}
          </div>
        </div>
      ` : ""}
      <div class="metric-picker-group">
        <div class="metric-picker-group-title">事件流</div>
        <div class="metric-picker-options">
          <label class="checkbox">
            <input type="checkbox" data-sub-metric="__event_timeline" ${selected.has("__event_timeline") ? "checked" : ""}>
            事件流
          </label>
        </div>
      </div>
    </div>
  `;
}

function syncOverlayState(capabilities, defaults, forceReset = false) {
  const allowed = capabilities?.overlay_allowed || {};
  const allowedFields = capabilities?.overlay_field_allowed || {};
  const nextState = forceReset
    ? { crypto: { symbols: [], fields: [] }, finance: { symbols: [], fields: [] } }
    : { crypto: { ...(selectedOverlayState.crypto || { symbols: [], fields: [] }) }, finance: { ...(selectedOverlayState.finance || { symbols: [], fields: [] }) } };
  ["crypto", "finance"].forEach((type) => {
    const allowedList = normalizeSymbols(allowed[type] || []);
    const allowedSet = new Set(allowedList);
    const symbolSource = forceReset || !nextState[type]?.symbols?.length ? (defaults?.[`overlay_${type}`] || []) : nextState[type].symbols;
    nextState[type].symbols = normalizeSymbols(symbolSource).filter((symbol) => allowedSet.has(symbol));
    const allowedFieldList = normalizeOverlayFields(allowedFields[type] || []);
    const allowedFieldSet = new Set(allowedFieldList);
    const fieldSource = forceReset || !nextState[type]?.fields?.length ? (defaults?.[`overlay_${type}_fields`] || []) : nextState[type].fields;
    nextState[type].fields = normalizeOverlayFields(fieldSource).filter((fieldKey) => allowedFieldSet.has(fieldKey));
  });
  selectedOverlayState = nextState;
}

function overlayQuickRow(type, allowedList) {
  const quick = QUICK_OVERLAY_SYMBOLS[type].filter((symbol) => allowedList.includes(symbol));
  return `
    <div class="overlay-picker-section">
      <div class="overlay-picker-title">${escapeHtml(type === "crypto" ? "Crypto 常用" : "Finance 常用")}</div>
      <div class="overlay-chip-row">
        ${quick.map((symbol) => {
          const active = (selectedOverlayState[type]?.symbols || []).includes(symbol);
          return `
            <button type="button" class="overlay-chip ${active ? "active" : ""}" data-overlay-toggle="${escapeHtml(symbol)}" data-overlay-type="${escapeHtml(type)}">
              <span class="overlay-dot ${type}"></span>
              ${escapeHtml(symbol)}
            </button>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

function overlayMoreRow(type, allowedList) {
  const quick = new Set(QUICK_OVERLAY_SYMBOLS[type]);
  const more = allowedList.filter((symbol) => !quick.has(symbol));
  if (!more.length) return "";
  return `
    <details class="overlay-more">
      <summary>更多 ${escapeHtml(type === "crypto" ? "Crypto" : "Finance")}</summary>
      <div class="overlay-chip-row overlay-chip-row-more">
        ${more.map((symbol) => {
          const active = (selectedOverlayState[type]?.symbols || []).includes(symbol);
          return `
            <button type="button" class="overlay-chip ${active ? "active" : ""}" data-overlay-toggle="${escapeHtml(symbol)}" data-overlay-type="${escapeHtml(type)}">
              <span class="overlay-dot ${type}"></span>
              ${escapeHtml(symbol)}
            </button>
          `;
        }).join("")}
      </div>
    </details>
  `;
}

function overlayFieldRow(type, allowedFields) {
  if (!allowedFields.length) return "";
  return `
    <div class="overlay-picker-section">
      <div class="overlay-picker-title">${escapeHtml(type === "crypto" ? "Crypto 字段" : "Finance 字段")}</div>
      <div class="overlay-chip-row">
        ${allowedFields.map((fieldKey) => {
          const active = (selectedOverlayState[type]?.fields || []).includes(fieldKey);
          return `
            <button type="button" class="overlay-chip overlay-field-chip ${active ? "active" : ""}" data-overlay-field="${escapeHtml(fieldKey)}" data-overlay-type="${escapeHtml(type)}">
              ${escapeHtml(OVERLAY_FIELD_LABELS[fieldKey] || fieldKey)}
            </button>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

function renderOverlayPicker(capabilities, defaults) {
  syncOverlayState(capabilities, defaults);
  revealSelectedOverlaySeries("crypto");
  revealSelectedOverlaySeries("finance");
  const allowed = capabilities?.overlay_allowed || {};
  const allowedFields = capabilities?.overlay_field_allowed || {};
  const selectedItems = [
    ...(selectedOverlayState.crypto?.symbols || []).map((symbol) => ({ type: "crypto", symbol })),
    ...(selectedOverlayState.finance?.symbols || []).map((symbol) => ({ type: "finance", symbol })),
  ];
  chartOverlayPicker.innerHTML = `
    <div class="metric-picker-title">外部行情叠加</div>
    <div class="overlay-picker-panel">
      ${overlayQuickRow("crypto", normalizeSymbols(allowed.crypto || []))}
      ${overlayMoreRow("crypto", normalizeSymbols(allowed.crypto || []))}
      ${overlayFieldRow("crypto", normalizeOverlayFields(allowedFields.crypto || []))}
      ${overlayQuickRow("finance", normalizeSymbols(allowed.finance || []))}
      ${overlayMoreRow("finance", normalizeSymbols(allowed.finance || []))}
      ${overlayFieldRow("finance", normalizeOverlayFields(allowedFields.finance || []))}
      <div class="overlay-picker-section">
        <div class="overlay-picker-title">已选 Overlay</div>
        <div class="overlay-selected-list">
          ${selectedItems.length ? selectedItems.map((item) => `
            <button type="button" class="overlay-selected-chip" data-overlay-remove="${escapeHtml(item.symbol)}" data-overlay-type="${escapeHtml(item.type)}">
              <span class="overlay-dot ${item.type}"></span>
              ${escapeHtml(item.symbol)}
              <span class="overlay-remove">x</span>
            </button>
          `).join("") : '<div class="muted">暂无已选外部行情</div>'}
        </div>
      </div>
    </div>
  `;
}

function toggleOverlaySymbol(type, symbol) {
  const normalized = String(symbol || "").trim().toUpperCase();
  if (!normalized) return;
  const current = new Set(selectedOverlayState[type]?.symbols || []);
  const adding = !current.has(normalized);
  adding ? current.add(normalized) : current.delete(normalized);
  selectedOverlayState = { ...selectedOverlayState, [type]: { ...(selectedOverlayState[type] || { fields: [] }), symbols: [...current] } };
  if (adding) {
    revealOverlaySeries(type, [normalized], selectedOverlayState[type]?.fields || []);
  }
}

function toggleOverlayField(type, fieldKey) {
  const normalized = String(fieldKey || "").trim().toLowerCase();
  if (!normalized) return;
  const current = new Set(selectedOverlayState[type]?.fields || []);
  const adding = !current.has(normalized);
  adding ? current.add(normalized) : current.delete(normalized);
  selectedOverlayState = { ...selectedOverlayState, [type]: { ...(selectedOverlayState[type] || { symbols: [] }), fields: [...current] } };
  if (adding) {
    revealOverlaySeries(type, selectedOverlayState[type]?.symbols || [], [normalized]);
  }
}

function removeOverlaySymbol(type, symbol) {
  selectedOverlayState = {
    ...selectedOverlayState,
    [type]: {
      ...(selectedOverlayState[type] || { fields: [] }),
      symbols: (selectedOverlayState[type]?.symbols || []).filter((item) => item !== symbol),
    },
  };
}

function isEventTimelineSelected() {
  const el = chartMetricPicker.querySelector("[data-sub-metric=\"__event_timeline\"]");
  return el ? el.checked : false;
}

function selectedSubMetrics() {
  return [...chartMetricPicker.querySelectorAll("[data-sub-metric]:checked")]
    .map((el) => el.dataset.subMetric)
    .filter((k) => k !== "__event_timeline")
    .filter((k) => !String(k || "").startsWith("metric_state:") || !isTemporalStateLaneKey(String(k).slice("metric_state:".length)));
}

function revealSelectedSubMetricSeries(keys = selectedSubMetrics()) {
  let changed = false;
  (keys || []).forEach((key) => {
    if (!key || String(key).startsWith("metric_state:")) return;
    const current = { ...(seriesStyleState[key] || {}) };
    if (current.visible === false) {
      current.visible = true;
      seriesStyleState[key] = current;
      changed = true;
    }
  });
  if (changed) {
    persistSeriesStyles();
  }
}

function selectedOverlaySymbols(type) {
  return [...(selectedOverlayState[type]?.symbols || [])];
}

function selectedOverlayFields(type) {
  return [...(selectedOverlayState[type]?.fields || [])];
}

function renderPresetOptions(presets) {
  workspacePresetSelect.innerHTML = ['<option value="">选择预设</option>', ...(presets || []).map((preset) => `<option value="${escapeHtml(String(preset.id))}">${escapeHtml(preset.name)}${preset.scope === "global" ? " / global" : ""}</option>`)].join("");
}

function renderTrackedMarkets() {
  syncMainChartModeOptions();
  if (!trackedMarkets.length) {
    setStatus(workspaceTrackedMarkets, "当前没有可用市场，请先返回策略市场或添加一个市场。");
    return;
  }
  workspaceTrackedMarkets.innerHTML = trackedMarkets.map((market, index) => `
    <div class="tracked-market-chip ${index === 0 ? "" : "secondary-chip"}">
      <div class="tracked-market-head">
        ${isBinanceTarget(market)
          ? `<span class="table-link-button">${escapeHtml(market.label || market.symbol || "Binance")}</span>`
          : `<a class="table-link-button" href="${escapeHtml(marketUi.buildPolymarketUrl(market))}" target="_blank" rel="noopener noreferrer">${escapeHtml(market.label)}</a>`}
        <div class="table-actions">
          ${isBinanceTarget(market) ? "" : `<button type="button" class="ghost mini" data-watch-market="${escapeHtml(marketIdentity(market))}">${marketUi.isInWatchlist(market) ? "取消自选" : "加入自选"}</button>`}
          <button type="button" class="ghost mini" data-remove-market="${escapeHtml(marketIdentity(market))}" ${trackedMarkets.length === 1 ? "disabled" : ""}>移除</button>
        </div>
      </div>
      <div class="tracked-market-meta">
        <span>${isBinanceTarget(market) ? "Binance" : (market.type === "strategy" ? "策略默认" : "附加市场")}</span>
        <span>${isBinanceTarget(market) ? `${escapeHtml(market.symbol || "-")} · ${escapeHtml(market.interval || "1m")}` : `Condition ${escapeHtml(market.condition_id || "-")}`}</span>
      </div>
    </div>
  `).join("");
}

function renderMarketStatus() {
  const primary = trackedMarkets[0];
  workspaceMarketStatus.textContent = primary
    ? (isBinanceTarget(primary)
        ? `当前主标的 ${primary.label || primary.symbol} | Binance ${primary.symbol || "-"} ${primary.interval || "1m"} | 已加载 ${trackedMarkets.length} 个数据集`
        : `当前主市场 ${primary.label} | Condition ${primary.condition_id || "-"} | 已加载 ${trackedMarkets.length} 个市场数据集`)
    : "当前没有市场数据集。";
}

function renderMarketResults() {
  if (!marketResults.length) {
    setStatus(workspaceMarketResults, "通过 conditionId / tokenId / 关键词搜索后，可把额外市场加入当前图表。");
    return;
  }
  const identities = new Set(trackedMarkets.map((item) => marketIdentity(item)));
  workspaceMarketResults.innerHTML = marketResults.map((market) => {
    const nextMarket = {
      type: "market",
      label: market.question || market.slug || market.condition_id || "Unnamed Market",
      question: market.question || market.slug || "",
      slug: market.slug || "",
      event_slug: market.event_slug || market.eventSlug || market.raw?.eventSlug || market.raw?.event_slug || "",
      group_item_title: market.group_item_title || market.groupItemTitle || market.raw?.groupItemTitle || "",
      url: market.url || market.raw?.url || "",
      condition_id: market.condition_id || "",
      yes_token: market.yes_token || "",
      no_token: market.no_token || "",
      category: market.category || "",
    };
    const added = identities.has(marketIdentity(nextMarket));
    return `
      <div class="workspace-market-card ${added ? "active" : ""}">
        <div class="workspace-market-card-title">
          <a class="table-link-button" href="${escapeHtml(marketUi.buildPolymarketUrl(nextMarket))}" target="_blank" rel="noopener noreferrer">${escapeHtml(nextMarket.label)}</a>
          <div class="table-actions">
            <button type="button" class="ghost mini" data-watch-result="${escapeHtml(marketIdentity(nextMarket))}">${marketUi.isInWatchlist(nextMarket) ? "取消自选" : "加入自选"}</button>
            <button type="button" class="secondary" data-add-market="${escapeHtml(marketIdentity(nextMarket))}" ${added ? "disabled" : ""}>${added ? "已添加" : "添加"}</button>
          </div>
        </div>
        <div class="workspace-market-card-meta">
          <span>Condition ${escapeHtml(nextMarket.condition_id || "-")}</span>
          <span>Yes ${escapeHtml(nextMarket.yes_token || "-")}</span>
          <span>No ${escapeHtml(nextMarket.no_token || "-")}</span>
          <span>${escapeHtml(market.category || "Unknown")}</span>
        </div>
      </div>
    `;
  }).join("");
}

function renderEvents(events) {
  if (typeof window.renderEvents === "function" && window.renderEvents !== renderEvents) {
    window.renderEvents(events);
    return;
  }
  if (!events?.length) {
    setStatus(workspaceEvents, "暂无事件");
    return;
  }
  workspaceEvents.innerHTML = `
    <div class="workspace-event-list">
      ${events.map((event) => {
        const tone = eventTone(event);
        const type = event.event_type || event.type || "-";
        const source = event.source || event.env || "system";
        return `
          <div class="workspace-event-item ${tone.className}">
            <div class="workspace-event-meta">
              <span class="event-kind" style="--event-color:${escapeHtml(tone.color)}">${escapeHtml(tone.label)}</span>
              <span class="state-chip ${String(source).toLowerCase().includes("real") ? "good" : "pending"}">${escapeHtml(source)}</span>
              <span class="muted">${escapeHtml(formatTime(event.ts))}</span>
            </div>
            <strong>${escapeHtml(type)}</strong>
            <div>${escapeHtml(event.summary || event.label || "-")}</div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function orderInstrumentLabel(order = {}) {
  const instrument = String(order.instrument_id || order.leg_id || "").trim();
  if (instrument.includes(":")) return instrument.split(":").pop();
  return instrument || "asset";
}

function backtestEventsForWorkspace(run = {}) {
  const orderEvents = (run.orders || []).map((order) => {
    const side = String(order.side || "ORDER").toUpperCase();
    const reason = String(order.reason || order.meta?.reason || "").trim();
    return {
      ts: order.ts_utc,
      type: "trade",
      source: "backtest_order",
      severity: "info",
      summary: `${side} ${orderInstrumentLabel(order)} · qty ${formatNumber(order.quantity, 8)} · price ${formatCurrency(order.price, 2, "USDT")}${reason ? ` · ${reason}` : ""}`,
    };
  });
  const engineEvents = (run.events || []).map((event) => ({
    ts: event.ts_utc || event.ts,
    type: event.event_type || event.type || "backtest",
    source: event.source || "backtest",
    severity: event.severity || "info",
    summary: event.message || event.summary || event.label || event.status || "Backtest event",
  }));
  const tradeEvents = sortDedupeEvents(orderEvents).slice(0, 80);
  const diagnosticEvents = sortDedupeEvents(engineEvents.filter((event) => {
    const type = String(event.type || "").toLowerCase();
    const text = String(event.summary || "").toLowerCase();
    return type.includes("complete") || type.includes("error") || type.includes("fail") || text.includes("complete") || text.includes("failed");
  })).slice(0, 8);
  return [...tradeEvents, ...diagnosticEvents];
}

function renderWorkspaceModeEvents() {
  if (workspaceViewMode === "backtest" && selectedBacktestResults?.selected_run) {
    _fullEventsList = backtestEventsForWorkspace(selectedBacktestResults.selected_run);
    renderEvents(_fullEventsList);
    return;
  }
  if ((workspaceState?.recent_events || []).length || !_fullEventsList.length) {
    _fullEventsList = limitEventsWithTypeGuarantee(workspaceState?.recent_events || []);
    renderEvents(_fullEventsList);
  }
}

function patchWorkspaceSummary(fields) {
  if (!workspaceState) {
    workspaceState = {};
  }
  workspaceState.strategy = {
    ...(workspaceState.strategy || {}),
    ...(fields || {}),
  };
  renderSummary(workspaceState.strategy || {});
}

function eventIdentity(event) {
  if (!event || typeof event !== "object") {
    return "";
  }
  return [
    event.ts || "",
    event.event_type || event.type || "",
    event.summary || event.label || "",
  ].join("|");
}

function eventTypeKey(event) {
  const type = String(event?.event_type || event?.type || "").toLowerCase();
  if (type.includes("trade") || type.includes("fill") || type.includes("order")) return "trade";
  if (type.includes("action")) return "action";
  if (type.includes("error") || type.includes("fail") || type.includes("block")) return "error";
  if (type.includes("settings")) return "settings";
  if (type.includes("print")) return "print";
  return type || "event";
}

function eventTimeMs(event) {
  const ms = Date.parse(event?.ts || "");
  return Number.isFinite(ms) ? ms : 0;
}

function sortDedupeEvents(events) {
  const seen = new Set();
  return (events || [])
    .filter((event) => event && typeof event === "object")
    .sort((a, b) => eventTimeMs(b) - eventTimeMs(a))
    .filter((event) => {
      const identity = eventIdentity(event);
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    });
}

function limitEventsWithTypeGuarantee(events, baseLimit = EVENT_LIST_BASE_LIMIT) {
  const sorted = sortDedupeEvents(events);
  const selected = sorted.slice(0, baseLimit);
  const selectedIds = new Set(selected.map(eventIdentity));
  for (const key of EVENT_TYPE_GUARANTEE_KEYS) {
    let count = 0;
    for (const event of sorted) {
      if (eventTypeKey(event) !== key) continue;
      const identity = eventIdentity(event);
      if (selectedIds.has(identity)) {
        count += 1;
      } else {
        selected.push(event);
        selectedIds.add(identity);
        count += 1;
      }
      if (count >= EVENT_TYPE_GUARANTEE_LIMIT) break;
    }
  }
  return sortDedupeEvents(selected);
}

function appendWorkspaceEvent(event) {
  if (!event || typeof event !== "object") {
    return;
  }
  if (!workspaceState) {
    workspaceState = {};
  }
  const base = _fullEventsList.length ? _fullEventsList : (Array.isArray(workspaceState.recent_events) ? workspaceState.recent_events : []);
  const next = limitEventsWithTypeGuarantee([event, ...base]);
  _fullEventsList = next;
  workspaceState.recent_events = next.slice(0, 30);
  renderEvents(next);
}

function disconnectWorkspaceLive() {
  if (workspaceLiveSource) {
    workspaceLiveSource.close();
    workspaceLiveSource = null;
  }
}

function connectWorkspaceLive() {
  disconnectWorkspaceLive();
  workspaceLiveSource = new EventSource(`/api/live/strategies/${encodeURIComponent(rowId)}/workspace`);

  workspaceLiveSource.addEventListener("summary", (evt) => {
    try {
      const payload = JSON.parse(evt.data || "{}");
      patchWorkspaceSummary(payload.summary || {});
    } catch {}
  });

  workspaceLiveSource.addEventListener("event_append", (evt) => {
    try {
      const payload = JSON.parse(evt.data || "{}");
      appendWorkspaceEvent(payload);
    } catch {}
  });

  workspaceLiveSource.addEventListener("error", () => {
    // Let EventSource handle automatic reconnects.
  });
}

function colorForSeries(key) {
  const persisted = seriesStyleState[key]?.color;
  if (persisted) return persisted;
  if (isOverlaySeriesKey(key)) return autoSeriesColor(key);
  const marketMatch = String(key || "").match(/^market_(\d+)_(yes|no)_/);
  if (marketMatch) {
    const pair = LEG_COLOR_PAIRS[Number(marketMatch[1]) % LEG_COLOR_PAIRS.length];
    return pair[marketMatch[2]] || pair.yes;
  }
  const palette = {
    yes_position: "#14b8a6",
    no_position: "#fb7185",
    yes_qty: "#8b5cf6",
    no_qty: "#f97316",
    yes_avg: "#22c55e",
    no_avg: "#ef4444",
    strategy_pnl: "#38bdf8",
    strategy_bankroll: "#c084fc",
    initial_capital: "#94a3b8",
    profit_roll_ratio: "#fde047",
    realized_profit: "#2dd4bf",
  };
  if (palette[key]) return palette[key];
  if (key.includes("__macd")) return "#facc15";
  if (String(key).startsWith("metric:") || String(key).startsWith("metric_state:")) return autoSeriesColor(key);
  const overlayFieldKey = Object.keys(OVERLAY_FIELD_LABELS).find((fieldKey) => key.endsWith(`_${fieldKey}`));
  return overlayFieldKey ? autoSeriesColor(key) : "#60a5fa";
}

function eventTone(event) {
  const type = String(event?.event_type || event?.type || "").toLowerCase();
  const source = String(event?.source || event?.env || "").toLowerCase();
  if (type.includes("error")) return { className: "event-error", color: "#f43f5e", label: "error" };
  if (type.includes("fail") || type.includes("block")) return { className: "event-blocked", color: "#f97316", label: "blocked" };
  if (type.includes("trade") || type.includes("fill") || type.includes("order")) {
    return source.includes("real")
      ? { className: "event-real", color: "#22c55e", label: "real trade" }
      : { className: "event-virtual", color: "#22d3ee", label: "virtual trade" };
  }
  if (type.includes("action")) return { className: "event-action", color: "#60a5fa", label: "action" };
  if (type.includes("print")) return { className: "event-print", color: "#94a3b8", label: "print" };
  if (type.includes("settings")) return { className: "event-settings", color: "#a78bfa", label: "settings" };
  return { className: "event-generic", color: "#64748b", label: type || "event" };
}

function ensureChartShell() {
  if (!document.getElementById("workspaceChartShell")) {
    workspaceCharts.innerHTML = `
      <div id="workspaceChartShell" class="workspace-chart-shell">
        <div class="workspace-chart-config">
          <div class="workspace-chart-config-title">图表外观</div>
          <div class="workspace-chart-resize-readout" id="workspaceChartResizeReadout">${workspaceChartHeight}px</div>
        </div>
        <div id="workspaceChartCanvas" class="workspace-chart-canvas"></div>
        <div class="workspace-chart-resize-handle" data-chart-resize-handle title="Drag to resize chart height" aria-label="Drag to resize chart height"></div>
      </div>
    `;
    setupChartResizeHandle();
  }
  workspaceCharts.style.height = `${workspaceChartHeight}px`;
  workspaceCharts.style.minHeight = `${CHART_MIN_HEIGHT}px`;
  const readout = document.getElementById("workspaceChartResizeReadout");
  if (readout) readout.textContent = `${workspaceChartHeight}px`;
  const canvas = document.getElementById("workspaceChartCanvas");
  if (workspaceChartInstance && workspaceChartInstance.getDom() !== canvas) {
    workspaceChartInstance.dispose();
    workspaceChartInstance = null;
  }
  if (workspaceChartInstance) workspaceChartInstance.resize();
  return canvas;
}

function applyChartHeight(height, persist = true) {
  workspaceChartHeight = clampChartHeight(height);
  if (persist) {
    persistChartAppearance();
  }
  if (workspaceCharts) {
    workspaceCharts.style.height = `${workspaceChartHeight}px`;
  }
  const readout = document.getElementById("workspaceChartResizeReadout");
  if (readout) readout.textContent = `${workspaceChartHeight}px`;
  if (workspaceChartInstance) {
    workspaceChartInstance.resize();
  }
}

function setupChartResizeHandle() {
  const handle = workspaceCharts.querySelector("[data-chart-resize-handle]");
  if (!handle || handle.dataset.bound === "true") return;
  handle.dataset.bound = "true";
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = workspaceChartHeight;
    const scrollParent = workspaceCharts.closest(".ws3-main");
    const startTop = workspaceCharts.getBoundingClientRect().top;
    handle.setPointerCapture?.(event.pointerId);
    document.body.classList.add("chart-resizing");

    const onMove = (moveEvent) => {
      applyChartHeight(startHeight + moveEvent.clientY - startY, false);
      if (scrollParent) {
        const currentTop = workspaceCharts.getBoundingClientRect().top;
        scrollParent.scrollTop += currentTop - startTop;
      }
    };
    const onUp = () => {
      document.body.classList.remove("chart-resizing");
      chartHeightUserAdjusted = true;
      persistChartAppearance();
      if (currentChartPayload) {
        renderCharts(currentChartPayload);
      }
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
    window.addEventListener("pointercancel", onUp, { once: true });
  });
}

function ensureReadableBacktestChartHeight(payload = {}) {
  if (chartHeightUserAdjusted || workspaceViewMode !== "backtest") {
    return;
  }
  const expandedPanels = expandChartPanels(payload.panels || [], payload.metric_state_lanes || [], payload.events || []);
  const panelCount = expandedPanels.length || 1;
  const targetHeight = panelCount >= 7 ? 980 : panelCount >= 6 ? 900 : panelCount >= 5 ? 820 : 0;
  if (targetHeight && workspaceChartHeight < targetHeight) {
    applyChartHeight(targetHeight, false);
  }
}

function setupEventTimelineHover(chart) {
  if (!chart || chart.__workspaceEventTimelineHoverBound) return;
  chart.__workspaceEventTimelineHoverBound = true;
  chart.getZr().on("mousemove", (event) => {
    const point = [event.offsetX, event.offsetY];
    const option = chart.getOption?.() || {};
    const yAxis = option.yAxis || [];
    const eventGridIndex = yAxis.findIndex((axis) => axis?.name === "Events");
    if (eventGridIndex < 0 || !chart.containPixel({ gridIndex: eventGridIndex }, point)) {
      return;
    }
    const eventSeries = (option.series || [])
      .map((series, index) => ({ series, index }))
      .filter((item) => String(item.series?.id || "").startsWith("__event_timeline:"));
    let best = null;
    eventSeries.forEach(({ series, index }) => {
      (series.data || []).forEach((data, dataIndex) => {
        const value = data?.value || data;
        if (!Array.isArray(value)) return;
        const pixel = chart.convertToPixel({ seriesIndex: index }, [value[0], value[1]]);
        if (!Array.isArray(pixel)) return;
        const dx = pixel[0] - point[0];
        const dy = pixel[1] - point[1];
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (!best || distance < best.distance) {
          best = { seriesIndex: index, dataIndex, distance };
        }
      });
    });
    if (best && best.distance <= 18) {
      chart.dispatchAction({ type: "showTip", seriesIndex: best.seriesIndex, dataIndex: best.dataIndex });
    } else {
      chart.dispatchAction({ type: "hideTip" });
    }
  });
}

function ensureChartInstance() {
  if (!window.echarts) throw new Error("ECharts 未加载");
  const canvas = ensureChartShell();
  if (!workspaceChartInstance) {
    workspaceChartInstance = window.echarts.init(canvas, null, { renderer: "canvas" });
    workspaceChartInstance.on("legendselectchanged", (params) => {
      chartLegendSelectedState = { ...(params.selected || {}) };
      persistLegendSelectedState();
    });
    workspaceChartInstance.on("datazoom", () => {
      if (isProgrammaticChartZoomSync()) {
        return;
      }
      const option = workspaceChartInstance.getOption();
      const dz = option?.dataZoom?.[0];
      if (!dz) return;
      chartViewState = {
        start: dz.start ?? null,
        end: dz.end ?? null,
        startValue: dz.startValue ?? null,
        endValue: dz.endValue ?? null,
      };
    });
    workspaceChartInstance.getZr().on("dblclick", () => {
      chartViewState = { start: null, end: null, startValue: null, endValue: null };
      setTimelineToLatest();
      closeCustomTimelinePanel();
      scheduleChartReload();
    });
    setupEventTimelineHover(workspaceChartInstance);
  }
  return workspaceChartInstance;
}

function getChartLayoutConfig() {
  if (workspaceChartHeight <= 460) {
    return CHART_MODE_CONFIG.compact;
  }
  if (workspaceChartHeight >= 620) {
    return CHART_MODE_CONFIG.relaxed;
  }
  return CHART_MODE_CONFIG.standard;
}

function chartPanelWeight(panel) {
  if (isStateLanePanel(panel)) {
    return PANEL_WEIGHTS.metric_state_lane;
  }
  return PANEL_WEIGHTS[panel.id] || 12;
}

function buildPanelLayout(panels) {
  const mode = getChartLayoutConfig();
  const panelCount = panels.length || 1;
  const totalGap = mode.gap * Math.max(0, panelCount - 1);
  const available = mode.usable - totalGap;
  const weights = panels.map((panel) => chartPanelWeight(panel));
  const weightSum = weights.reduce((sum, item) => sum + item, 0) || 1;
  let top = mode.topStart;
  return panels.map((panel, index) => {
    const height = (weights[index] / weightSum) * available;
    const layout = { panel, top: `${top}%`, height: `${height}%` };
    top += height + mode.gap;
    return layout;
  });
}

function formatChartValue(value, unit) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  if (unit === "ratio") return `${(num * 100).toFixed(2)}%`;
  if (unit === "currency") return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (unit === "qty") return num.toLocaleString(undefined, { maximumFractionDigits: 4 });
  if (unit === "compact_currency" || unit === "compact_number") {
    return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 }).format(num);
  }
  return num.toLocaleString(undefined, { maximumFractionDigits: Math.abs(num) < 10 ? 4 : 2 });
}

function latestFiniteSeriesValue(rows = [], key) {
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const value = Number(rows[index]?.[key]);
    if (Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

function latestStateLaneSegment(lane = {}, toTs = "") {
  const segments = (lane.segments || []).filter((segment) => segment.from && segment.to);
  if (!segments.length) return null;
  const targetTime = new Date(toTs || segments[segments.length - 1].to).getTime();
  const active = segments.find((segment) => {
    const start = new Date(segment.from).getTime();
    const end = new Date(segment.to).getTime();
    return Number.isFinite(start) && Number.isFinite(end) && start <= targetTime && targetTime <= end;
  });
  return active || segments[segments.length - 1];
}

function renderChartInsights(payload = {}) {
  if (!workspaceChartInsights) return;
  const rows = payload.rows || [];
  const buildMetricChips = (seriesItems) => seriesItems.map((item) => {
    const value = latestFiniteSeriesValue(rows, item.key);
    if (value === null) return "";
    return `
      <span class="workspace-chart-insight-chip" title="${escapeHtml(item.label || item.key)}">
        <span class="workspace-chart-insight-dot" style="background:${escapeHtml(colorForSeries(item.key))}"></span>
        <span class="workspace-chart-insight-label">${escapeHtml(displaySeriesLabel(item))}</span>
        <span class="workspace-chart-insight-value">${escapeHtml(formatChartValue(value, item.unit))}</span>
      </span>
    `;
  }).filter(Boolean);
  const strategyMetricChips = buildMetricChips(
    (payload.series || []).filter((item) => item.panel === "metric_values" && !isBacktestDerivedSeries(item))
  );
  const backtestMetricChips = buildMetricChips(
    (payload.series || []).filter((item) => item.panel === "backtest_metrics" || isBacktestDerivedSeries(item))
  );

  const buildLaneChips = (lanes) => lanes.map((lane) => {
      const segment = latestStateLaneSegment(lane, payload.meta?.to || "");
      if (!segment) return "";
      const color = stateLaneSegmentColor(lane, segment);
      return `
        <span class="workspace-chart-insight-chip" title="${escapeHtml(stateLaneIdentity(lane))}">
          <span class="workspace-chart-insight-dot" style="background:${escapeHtml(color)}"></span>
          <span class="workspace-chart-insight-label">${escapeHtml(stateLaneDisplayName(lane))}</span>
          <span class="workspace-chart-insight-value">${escapeHtml(stateLaneSegmentLabel(segment, lane))}</span>
        </span>
      `;
    })
    .filter(Boolean);
  const strategyLaneChips = buildLaneChips(
    (payload.metric_state_lanes || [])
      .filter((lane) => !isBacktestDerivedLane(lane))
      .filter((lane) => isStateLaneDisplayable(lane) && (lane.segments || []).length)
  );
  const backtestLaneChips = buildLaneChips(
    (payload.metric_state_lanes || [])
      .filter(isBacktestDerivedLane)
      .filter((lane) => isStateLaneDisplayable(lane) && (lane.segments || []).length)
  );

  const groups = [];
  if (strategyMetricChips.length) {
    groups.push(`
      <div class="workspace-chart-insight-group">
        <div class="workspace-chart-insight-title">Strategy Metrics</div>
        <div class="workspace-chart-insight-row">${strategyMetricChips.join("")}</div>
      </div>
    `);
  }
  if (backtestMetricChips.length) {
    groups.push(`
      <div class="workspace-chart-insight-group">
        <div class="workspace-chart-insight-title">Backtest Metrics</div>
        <div class="workspace-chart-insight-row">${backtestMetricChips.join("")}</div>
      </div>
    `);
  }
  if (strategyLaneChips.length) {
    groups.push(`
      <div class="workspace-chart-insight-group">
        <div class="workspace-chart-insight-title">State Lanes</div>
        <div class="workspace-chart-insight-row">${strategyLaneChips.join("")}</div>
      </div>
    `);
  }
  if (backtestLaneChips.length) {
    groups.push(`
      <div class="workspace-chart-insight-group">
        <div class="workspace-chart-insight-title">Backtest State</div>
        <div class="workspace-chart-insight-row">${backtestLaneChips.join("")}</div>
      </div>
    `);
  }

  workspaceChartInsights.hidden = groups.length === 0;
  workspaceChartInsights.innerHTML = groups.join("");
}

function formatTooltipValue(value, unit, meta = {}) {
  const text = formatChartValue(value, unit);
  if (text === "-") return text;
  if (unit === "price" && isCryptoSeriesMeta(meta)) return `${text} USDT`;
  if (unit === "currency") {
    const suffix = isCryptoSeriesMeta(meta) || String(meta.key || "").startsWith("backtest_") ? "USDT" : "USD";
    return `${text} ${suffix}`;
  }
  return text;
}

function formatAxisTimeLabel(value, rangeMs) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value ?? "");
  const hhmm = `${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}`;
  return rangeMs <= 36 * 60 * 60 * 1000 ? hhmm : `${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())} ${hhmm}`;
}

function getTimeExtent(rows, meta = {}) {
  const metaMin = new Date(meta?.from || "").getTime();
  const metaMax = new Date(meta?.to || "").getTime();
  if (Number.isFinite(metaMin) && Number.isFinite(metaMax) && metaMin < metaMax) {
    return { minTs: metaMin, maxTs: metaMax, rangeMs: metaMax - metaMin };
  }
  const parsed = rows.map((row) => new Date(row.ts).getTime()).filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!parsed.length) {
    const now = Date.now();
    return { minTs: now - 24 * 60 * 60 * 1000, maxTs: now, rangeMs: 24 * 60 * 60 * 1000 };
  }
  return { minTs: parsed[0], maxTs: parsed[parsed.length - 1], rangeMs: Math.max(60 * 1000, parsed[parsed.length - 1] - parsed[0]) };
}

function chartTimeExtentKey(rows, meta = {}) {
  const { minTs, maxTs } = getTimeExtent(rows || [], meta);
  return { minTs, maxTs };
}

function chartTimeExtentEqual(a, b) {
  return Boolean(a && b && a.minTs === b.minTs && a.maxTs === b.maxTs);
}

/** Matches `strategy_chart_service._ema` for incremental MACD refresh. */
function emaSeries(values, period) {
  const alpha = 2.0 / (period + 1.0);
  const result = [];
  let previous = null;
  for (const value of values) {
    if (value === null || value === undefined) {
      result.push(previous);
      continue;
    }
    const v = Number(value);
    if (!Number.isFinite(v)) {
      result.push(previous);
      continue;
    }
    if (previous === null) {
      previous = v;
    } else {
      previous = v * alpha + previous * (1.0 - alpha);
    }
    result.push(previous);
  }
  return result;
}

function recomputeMacdOverlayColumns(rows, seriesList) {
  if (!rows?.length) return;
  const bases = new Set();
  (seriesList || []).forEach((item) => {
    const key = String(item?.key || "");
    if (!key || item.render === "bar" || key.includes("__macd")) return;
    if (!(seriesStyleState[key] || {}).macd?.enabled) return;
    bases.add(key);
  });
  bases.forEach((baseKey) => {
    const macdCfg = (seriesStyleState[baseKey] || {}).macd || {};
    const fast = Math.max(2, Math.floor(Number(macdCfg.fast) || 12));
    const slow = Math.max(fast + 1, Math.floor(Number(macdCfg.slow) || 26));
    const signalPeriod = Math.max(2, Math.floor(Number(macdCfg.signal) || 9));
    const baseValues = rows.map((row) => {
      const v = row[baseKey];
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    });
    const emaFast = emaSeries(baseValues, fast);
    const emaSlow = emaSeries(baseValues, slow);
    const macdLine = emaFast.map((f, i) => {
      const s = emaSlow[i];
      if (f == null || s == null) return null;
      return f - s;
    });
    const signalLine = emaSeries(macdLine, signalPeriod);
    const macdKey = `${baseKey}__macd`;
    const signalKey = `${baseKey}__macd_signal`;
    rows.forEach((row, i) => {
      const m = macdLine[i];
      const sig = signalLine[i];
      if (m != null && Number.isFinite(m)) {
        row[macdKey] = m;
      } else {
        delete row[macdKey];
      }
      if (sig != null && Number.isFinite(sig)) {
        row[signalKey] = sig;
      } else {
        delete row[signalKey];
      }
    });
  });
}

function parseChartTime(value) {
  if (value instanceof Date) {
    const t = value.getTime();
    return Number.isFinite(t) ? t : null;
  }
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 1000000000) {
    return numeric;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function tooltipMarker(color) {
  return `<span style="display:inline-block;margin-right:4px;border-radius:50%;width:8px;height:8px;background:${escapeHtml(color || "#94a3b8")}"></span>`;
}

function isBidAskReferenceSeries(item = {}) {
  const key = String(item.key || "");
  return item.panel === "main" && /_(yes|no)_(bid|ask)$/.test(key);
}

function prepareTooltipRows(payload = {}) {
  return (payload.rows || [])
    .map((row) => ({ row, ts: parseChartTime(row.ts) }))
    .filter((item) => item.ts !== null)
    .sort((a, b) => a.ts - b.ts);
}

function nearestChartRow(rows = [], rawTs) {
  if (!rows.length) return null;
  const target = parseChartTime(rawTs);
  if (target === null) return rows[rows.length - 1].row;
  let best = rows[0];
  let bestDelta = Math.abs(best.ts - target);
  for (let i = 1; i < rows.length; i += 1) {
    const delta = Math.abs(rows[i].ts - target);
    if (delta <= bestDelta) {
      best = rows[i];
      bestDelta = delta;
    } else if (rows[i].ts > target) {
      break;
    }
  }
  return best.row;
}

function buildReferenceTooltipLines(payload = {}, rawTs, seenSeriesIds = new Set(), rows = null, referenceSeries = null) {
  const preparedRows = rows || prepareTooltipRows(payload);
  const references = referenceSeries || (payload.series || []).filter(isBidAskReferenceSeries);
  const row = nearestChartRow(preparedRows, rawTs);
  if (!row || !references.length) return [];
  return references.map((item) => {
    if (seenSeriesIds.has(item.key)) return null;
    const value = Number(row[item.key]);
    if (!Number.isFinite(value)) return null;
    const color = colorForSeries(item.key);
    return `${tooltipMarker(color)}${escapeHtml(item.label || item.key)}: ${escapeHtml(formatChartValue(value, item.unit))} <span style="color:#90a5c3">(ref)</span>`;
  }).filter(Boolean);
}

function eventStatusParts(data = {}) {
  return [data.type, data.subtype, data.source, data.severity]
    .map((value) => String(value || "").trim())
    .filter(Boolean);
}

function formatEventTooltipLine(data = {}, summaryLimit = 160) {
  const summary = String(data.summary || data.label || "-");
  const status = eventStatusParts(data).join(" · ");
  const head = `${tooltipMarker(data.color)}<span style="color:${escapeHtml(data.color || "#94a3b8")}">${escapeHtml(data.category || "Event")}</span>`;
  const main = `${head}: ${escapeHtml(summary.slice(0, summaryLimit))}`;
  return status
    ? `${main}<br><span style="color:#90a5c3;margin-left:12px">${escapeHtml(status)}</span>`
    : main;
}

function buildTooltipFormatter(seriesMap, payload = {}) {
  const rows = prepareTooltipRows(payload);
  const referenceSeries = (payload.series || []).filter(isBidAskReferenceSeries);
  return function formatter(params) {
    const items = Array.isArray(params) ? params : (params ? [params] : []);
    if (!items.length) return "";
    const rawTs = Array.isArray(items[0].value) ? items[0].value[0] : (items[0].axisValue || "");
    const lines = [escapeHtml(formatTime(rawTs))];
    const seenSeriesIds = new Set();
    items.forEach((param) => {
      if (param.seriesId) seenSeriesIds.add(param.seriesId);
      if (String(param.seriesId || "").startsWith("__event_timeline")) {
        const d = param.data || {};
        lines.push(formatEventTooltipLine(d, 180));
        return;
      }
      if (String(param.seriesId || "").startsWith("__metric_state_lane:")) {
        const d = param.data || {};
        const state = d.state || (Array.isArray(param.value) ? param.value[4] : "");
        if (state) {
          const from = d.from ? formatTime(d.from) : "";
          const to = d.to ? formatTime(d.to) : "";
          const range = from || to ? ` <span style="color:#90a5c3">(${escapeHtml(from)} - ${escapeHtml(to)})</span>` : "";
          lines.push(`${param.marker}${escapeHtml(d.lane || param.seriesName || "State Lane")}: ${escapeHtml(state)}${range}`);
        }
        return;
      }
      const meta = seriesMap.get(param.seriesId || param.seriesName);
      if (!meta) return;
      const label = displaySeriesLabel(meta);
      if (meta.render === "candlestick") {
        const values = Array.isArray(param.value) ? param.value.slice(1) : [];
        if (values.length >= 4) {
          lines.push(`${param.marker}${escapeHtml(label)}: 开 ${escapeHtml(formatTooltipValue(values[0], meta?.unit, meta))} · 高 ${escapeHtml(formatTooltipValue(values[3], meta?.unit, meta))} · 低 ${escapeHtml(formatTooltipValue(values[2], meta?.unit, meta))} · 收 ${escapeHtml(formatTooltipValue(values[1], meta?.unit, meta))}`);
        }
        return;
      }
      const rawValue = Array.isArray(param.value) ? param.value[1] : param.value;
      if (rawValue === null || rawValue === undefined || rawValue === "") return;
      lines.push(`${param.marker}${escapeHtml(label)}: ${escapeHtml(formatTooltipValue(rawValue, meta?.unit, meta))}`);
    });
    lines.push(...buildReferenceTooltipLines(payload, rawTs, seenSeriesIds, rows, referenceSeries));
    return lines.join("<br>");
  };
}

function buildChartDataZoom(layout) {
  return [
    { type: "inside", xAxisIndex: layout.map((_, index) => index), filterMode: "none", throttle: 40, moveOnMouseMove: true, moveOnMouseWheel: false, zoomOnMouseWheel: true, preventDefaultMouseMove: true },
    { type: "slider", xAxisIndex: layout.map((_, index) => index), filterMode: "none", bottom: 8, left: 22, right: 82, height: 18, realtime: true, brushSelect: true, showDetail: false, handleSize: 14, moveHandleSize: 8, handleStyle: { color: "#dbeafe", borderColor: "#60a5fa", borderWidth: 1 }, backgroundColor: "rgba(255, 255, 255, 0.035)", fillerColor: "rgba(86, 167, 255, 0.16)", borderColor: "rgba(148, 163, 184, 0.14)", textStyle: { color: "#90a5c3" } },
  ];
}

function applyChartViewStateToDataZoom(dataZoom) {
  if (chartViewState.start === null || chartViewState.end === null) {
    return dataZoom;
  }
  return (dataZoom || []).map((dz) => ({
    ...dz,
    start: chartViewState.start,
    end: chartViewState.end,
    startValue: chartViewState.startValue,
    endValue: chartViewState.endValue,
  }));
}

function expandChartPanels(basePanels = [], stateLanes = [], events = []) {
  const lanePanels = (stateLanes || [])
    .filter((lane) => isStateLaneDisplayable(lane) && (lane.segments || []).length)
    .map((lane) => ({
      id: stateLanePanelId(lane),
      title: stateLaneDisplayName(lane),
      state_lane_key: stateLaneIdentity(lane),
      state_lane_index: Number(lane.lane ?? 0),
      is_state_lane: true,
    }));
  let panels = [];
  let insertedStateLanes = false;
  (basePanels || []).forEach((panel) => {
    if (panel.id === "metric_states") {
      panels.push(...lanePanels);
      insertedStateLanes = true;
      return;
    }
    panels.push(panel);
  });
  if (lanePanels.length && !insertedStateLanes) {
    panels.push(...lanePanels);
  }
  const includeEventTimeline = isEventTimelineSelected();
  if (includeEventTimeline && (events || []).some((event) => event?.ts) && !panels.find((p) => p.id === "event_timeline")) {
    panels.push({ id: "event_timeline", title: "Events" });
  }
  return panels;
}

function buildChartCoordinateState(payload) {
  let panels = expandChartPanels(payload.panels || [], payload.metric_state_lanes || [], payload.events || []);
  const series = payload.series || [];
  const rows = payload.rows || [];
  const layout = buildPanelLayout(panels);
  const { minTs, maxTs, rangeMs } = getTimeExtent(rows, payload.meta || {});
  const panelIndex = new Map(layout.map((item, index) => [item.panel.id, index]));
  const grid = layout.map((item) => ({
    left: item.panel.id === "event_timeline" ? 92 : 22,
    right: 82,
    top: item.top,
    height: item.height,
    containLabel: false,
  }));
  const xAxis = layout.map((_, index) => ({
    type: "time",
    gridIndex: index,
    min: minTs,
    max: maxTs,
    boundaryGap: false,
    axisLabel: { show: index === layout.length - 1, color: "#90a5c3", hideOverlap: true, formatter(value) { return formatAxisTimeLabel(value, rangeMs); } },
    axisTick: { show: index === layout.length - 1 },
    axisLine: { lineStyle: { color: "rgba(148, 163, 184, 0.28)" } },
    splitLine: { show: false },
  }));
  const yAxis = layout.map((item, index) => {
    const isStatePanel = isStateLanePanel(item.panel);
    const isEventPanel = item.panel.id === "event_timeline";
    const isMainPanel = item.panel.id === "main";
    const eventLaneCount = EVENT_TIMELINE_CATEGORIES.length;
    const eventLaneLabels = new Map(EVENT_TIMELINE_CATEGORIES.map((cat) => [cat.lane, cat.label]));
    return {
      type: "value",
      gridIndex: index,
      position: isEventPanel ? "left" : "right",
      scale: !isStatePanel && !isEventPanel,
      min: isStatePanel ? 0 : (isEventPanel ? -0.5 : undefined),
      max: isStatePanel ? 1 : (isEventPanel ? eventLaneCount - 0.5 : undefined),
      name: item.panel.title,
      nameLocation: "end",
      nameGap: isMainPanel ? 12 : 8,
      nameTextStyle: { color: "#90a5c3", padding: [0, 4, 0, 0], width: 72, overflow: "truncate", align: isEventPanel ? "left" : "right" },
      axisLabel: {
        show: !isStatePanel,
        color: "#90a5c3",
        fontSize: isEventPanel ? 11 : undefined,
        margin: isEventPanel ? 10 : 10,
        lineHeight: isEventPanel ? 18 : undefined,
        formatter(value) {
          if (isEventPanel) {
            return eventLaneLabels.get(Math.round(Number(value))) || "";
          }
          const panelSeries = series.find((entry) => panelIndex.get(entry.panel) === index);
          return formatChartValue(value, panelSeries?.unit);
        },
      },
      axisTick: { show: !isStatePanel && !isEventPanel },
      axisLine: { show: true, lineStyle: { color: "rgba(148, 163, 184, 0.22)" } },
      splitLine: { show: isEventPanel ? true : (!isStatePanel && !isEventPanel), lineStyle: { color: isEventPanel ? "rgba(148, 163, 184, 0.08)" : "rgba(148, 163, 184, 0.09)" } },
    };
  });
  const dataZoom = applyChartViewStateToDataZoom(buildChartDataZoom(layout));
  return {
    layout,
    panelIndex,
    grid,
    xAxis,
    yAxis,
    dataZoom,
    rangeMs,
  };
}

function buildChartSeriesOption(payload, targetKeys = null) {
  const series = payload.series || [];
  const rows = payload.rows || [];
  const { panelIndex } = buildChartCoordinateState(payload);
  const seriesMetaById = new Map();
  const allowedKeys = targetKeys ? new Set(targetKeys) : null;
  const chartSeries = series.map((item) => {
    if (allowedKeys && !allowedKeys.has(item.key)) return null;
    if (item.render === "candlestick") {
      const style = seriesStyleState[item.key] || item.style || {};
      if (style.visible === false) return null;
      const data = rows.map((row) => {
        const raw = row[item.key];
        const values = Array.isArray(raw)
          ? raw.map((value) => Number(value))
          : [
              Number(row[item.key.replace(/ohlc$/, "open")]),
              Number(row[item.key.replace(/ohlc$/, "close")]),
              Number(row[item.key.replace(/ohlc$/, "low")]),
              Number(row[item.key.replace(/ohlc$/, "high")]),
            ];
        if (values.length >= 4 && values.every((value) => Number.isFinite(value))) {
          return [row.ts, values[0], values[1], values[2], values[3]];
        }
        return null;
      }).filter(Boolean);
      if (!data.length) return null;
      seriesMetaById.set(item.key, item);
      return {
        id: item.key,
        name: displaySeriesLabel(item),
        type: "candlestick",
        xAxisIndex: panelIndex.get(item.panel) || 0,
        yAxisIndex: panelIndex.get(item.panel) || 0,
        data,
        itemStyle: {
          color: style.up_color || "#31d0aa",
          color0: style.down_color || "#f05d6a",
          borderColor: style.up_color || "#31d0aa",
          borderColor0: style.down_color || "#f05d6a",
        },
        barWidth: "54%",
        barMinWidth: 1,
        barMaxWidth: 14,
        emphasis: { focus: "series" },
        z: 6,
      };
    }
    const isBar = item.render === "bar";
    const isBidAsk = /_(bid|ask)$/.test(item.key);
    const isPriceSeries = item.category === "market_target" || item.category === "price" || /_(bid|ask|mid|last_price)$/.test(item.key);
    const data = rows.map((row) => {
      const value = Number(row[item.key]);
      if (Number.isFinite(value)) return [row.ts, value];
      return isPriceSeries ? [row.ts, null] : null;
    }).filter(Boolean);
    const hasFiniteValue = data.some((point) => Array.isArray(point) && Number.isFinite(point[1]));
    const style = seriesStyleState[item.key] || item.style || {};
    if (style.visible === false) return null;
    const color = style.color || colorForSeries(item.key);
    const isMetric = item.category === "strategy_metric";
    const forceSparseSymbols = hasFiniteValue && (data.filter((point) => Number.isFinite(point?.[1])).length < 2 || isMetric);
    seriesMetaById.set(item.key, item);
    return {
      id: item.key,
      name: displaySeriesLabel(item),
      type: isBar ? "bar" : "line",
      xAxisIndex: panelIndex.get(item.panel) || 0,
      yAxisIndex: panelIndex.get(item.panel) || 0,
      showSymbol: isBar ? false : (Boolean(style.show_symbol) || forceSparseSymbols),
      symbolSize: forceSparseSymbols ? 7 : 4,
      connectNulls: !isBar && !isPriceSeries,
      step: item.render === "step" ? "end" : false,
      smooth: isBar ? false : Boolean(style.smooth),
      data,
      barMaxWidth: isBar ? 12 : undefined,
      lineStyle: isBar ? undefined : { width: Number(style.width || (item.panel === "main" ? 2.4 : 2)), type: style.line_type || (isBidAsk ? "dashed" : "solid"), color, opacity: isBidAsk ? 0.55 : 0.95 },
      itemStyle: { color, opacity: isBidAsk ? 0.75 : 1 },
      emphasis: { focus: "series" },
    };
  }).filter(Boolean).filter((item) => item.data.some((point) => Array.isArray(point) && Number.isFinite(point[1])));
  return { chartSeries, seriesMetaById };
}

function isPrintEvent(event) {
  const text = [
    event?.event_type,
    event?.type,
    event?.event_subtype,
    event?.source,
    event?.id,
    event?.summary,
    event?.label,
  ].map((value) => String(value || "").toLowerCase()).join(" ");
  return text.includes("print") || text.includes("[input]") || text.includes("[calc]");
}

function chartVisibleEvents(events = []) {
  return (events || []).filter((event) => !isPrintEvent(event));
}

function buildEventMarkLines(events = []) {
  return chartVisibleEvents(events).slice(0, 80).map((event) => {
    const tone = eventTone(event);
    return {
      name: tone.label,
      xAxis: event.ts,
      lineStyle: { color: tone.color, width: 1.5, type: "dashed", opacity: 0.68 },
      label: { show: false },
      emphasis: {
        label: {
          show: true,
          formatter: `${tone.label}: ${event.summary || event.label || ""}`.slice(0, 80),
          color: "#e5eefc",
          backgroundColor: "rgba(7, 17, 31, 0.92)",
          borderColor: tone.color,
          borderWidth: 1,
          borderRadius: 4,
          padding: [4, 6],
        },
      },
    };
  }).filter((item) => item.xAxis);
}

function buildEventMarkLineSeries(payload, panelIndex) {
  return [];
}

function tradeEventSide(event = {}) {
  const direct = String(event.side || event.action || "").trim().toUpperCase();
  if (direct === "BUY" || direct === "SELL") return direct;
  const text = String(event.label || event.summary || event.type || event.event_type || "").toUpperCase();
  if (text.includes("SELL")) return "SELL";
  if (text.includes("BUY")) return "BUY";
  return "";
}

function tradeEventPrice(event = {}) {
  const direct = numericValue(event.price);
  if (direct !== null) return direct;
  const text = String(event.label || event.summary || "");
  const match = text.match(/(?:price=|@|price\s+)([0-9,.]+)/i);
  if (!match) return null;
  return numericValue(match[1].replaceAll(",", ""));
}

function tradeEventQuantity(event = {}) {
  const direct = numericValue(event.quantity);
  if (direct !== null) return direct;
  const text = String(event.label || event.summary || "");
  const match = text.match(/qty=([0-9,.]+)/i);
  return match ? numericValue(match[1].replaceAll(",", "")) : null;
}

function buildTradeMarkerSeries(payload, panelIndex) {
  const mainPanelIndex = panelIndex.get("main") ?? 0;
  const bySide = { BUY: [], SELL: [] };
  (payload.events || []).forEach((event) => {
    const side = tradeEventSide(event);
    if (!side || !bySide[side]) return;
    const price = tradeEventPrice(event);
    const ts = event.ts;
    if (!ts || price === null) return;
    bySide[side].push({
      value: [ts, price],
      side,
      price,
      quantity: tradeEventQuantity(event),
      reason: event.reason || "",
      label: event.label || event.summary || "",
      ts,
      itemStyle: {
        color: side === "BUY" ? "#22c55e" : "#ef4444",
        borderColor: side === "BUY" ? "#bbf7d0" : "#fecdd3",
        borderWidth: 1,
      },
    });
  });
  return Object.entries(bySide).map(([side, data]) => {
    if (!data.length) return null;
    const isBuy = side === "BUY";
    return {
      id: `__trade_marker:${side}`,
      name: isBuy ? "BUY" : "SELL",
      type: "scatter",
      xAxisIndex: mainPanelIndex,
      yAxisIndex: mainPanelIndex,
      data,
      symbol: "triangle",
      symbolRotate: isBuy ? 0 : 180,
      symbolSize: 8,
      symbolOffset: isBuy ? [0, 7] : [0, -7],
      z: 30,
      emphasis: { scale: 1.6, focus: "self" },
      tooltip: {
        trigger: "item",
        formatter(param) {
          const d = param.data || {};
          const qty = d.quantity === null || d.quantity === undefined ? "-" : formatNumber(d.quantity, 8);
          return [
            `<strong style="color:${isBuy ? "#86efac" : "#fca5a5"}">${escapeHtml(side)}</strong> <span style="color:#90a5c3">${escapeHtml(formatTime(d.ts))}</span>`,
            `Price: ${escapeHtml(formatCurrency(d.price, 2, "USDT"))}`,
            `Qty: ${escapeHtml(qty)}`,
            d.reason ? `Reason: ${escapeHtml(d.reason)}` : "",
          ].filter(Boolean).join("<br>");
        },
      },
    };
  }).filter(Boolean);
}

function buildMetricStateSeries(payload, panelIndex) {
  const lanes = (payload.metric_state_lanes || []).filter(isStateLaneDisplayable);
  if (!lanes.length) return [];
  const overrideColors = loadStateLaneColors();
  return lanes.map((lane) => {
    const statePanelIndex = panelIndex.get(stateLanePanelId(lane));
    if (statePanelIndex === undefined) return null;
    const laneName = stateLaneDisplayName(lane);
    const segments = (lane.segments || []).map((segment) => {
      const color = stateLaneSegmentColor(lane, segment, overrideColors);
      const state = stateLaneSegmentLabel(segment, lane);
      return {
        value: [segment.from, 0.5, segment.to, color, state],
        lane: laneName,
        laneKey: stateLaneIdentity(lane),
        rawState: stateLaneSegmentValue(segment),
        state,
        from: segment.from,
        to: segment.to,
        color,
        itemStyle: { color },
      };
    }).filter((segment) => segment.from && segment.to);
    if (!segments.length) return null;
    return {
      id: `__metric_state_lane:${stateLanePanelId(lane)}`,
      name: laneName,
      type: "custom",
      xAxisIndex: statePanelIndex,
      yAxisIndex: statePanelIndex,
      silent: false,
      encode: { x: [0, 2], y: 1 },
      data: segments,
      renderItem(params, api) {
        const start = api.coord([api.value(0), 0.15]);
        const end = api.coord([api.value(2), 0.85]);
        const rect = {
          x: Math.min(start[0], end[0]),
          y: Math.min(start[1], end[1]),
          width: Math.max(1, Math.abs(end[0] - start[0])),
          height: Math.max(6, Math.abs(end[1] - start[1])),
        };
        const clipped = window.echarts.graphic.clipRectByRect(rect, {
          x: params.coordSys.x,
          y: params.coordSys.y,
          width: params.coordSys.width,
          height: params.coordSys.height,
        });
        if (!clipped) return null;
        return {
          type: "rect",
          shape: clipped,
          style: api.style({ fill: api.value(3) || "#60a5fa", opacity: 0.7 }),
          emphasis: {
            style: {
              opacity: 0.92,
              shadowBlur: 8,
              shadowColor: "rgba(148, 163, 184, 0.22)",
            },
          },
        };
      },
      tooltip: {
        trigger: "item",
        formatter(param) {
          const data = param.data || {};
          const from = data.from ? formatTime(data.from) : "-";
          const to = data.to ? formatTime(data.to) : "-";
          return [
            `<strong>${escapeHtml(data.lane || "State Lane")}</strong>`,
            `${escapeHtml(data.state || "-")}`,
            `<span style="color:#90a5c3">${escapeHtml(from)} - ${escapeHtml(to)}</span>`,
          ].join("<br>");
        },
      },
    };
  }).filter(Boolean);
}

function classifyEventCategory(event) {
  const type = String(event?.event_type || event?.type || "").toLowerCase();
  if (type.includes("error") || type.includes("fail") || type.includes("block")) return "error";
  if (type.includes("trade") || type.includes("fill") || type.includes("order")) return "trade";
  if (type.includes("action")) return "action";
  if (type.includes("settings")) return "settings";
  return "print";
}

function buildEventTimelineSeries(payload, panelIndex) {
  const events = payload.events || [];
  const eventPanelIndex = panelIndex.get("event_timeline");
  if (eventPanelIndex === undefined || !events.length) return [];
  const overrideColors = loadEventCategoryColors();
  const categoryMap = new Map(EVENT_TIMELINE_CATEGORIES.map((cat) => [cat.key, cat]));
  const dataByCategory = new Map(EVENT_TIMELINE_CATEGORIES.map((cat) => [cat.key, []]));
  events.forEach((event) => {
    const cat = classifyEventCategory(event);
    const info = categoryMap.get(cat) || categoryMap.get("print");
    const ts = new Date(event.ts).getTime();
    if (!Number.isFinite(ts)) return;
    const color = overrideColors[info.key] || info.color;
    const summary = event.summary || event.label || event.event_type || event.type || "-";
    dataByCategory.get(info.key)?.push({
      value: [ts, info.lane, summary],
      itemStyle: { color },
      category: info.label,
      categoryKey: info.key,
      color,
      summary,
      label: event.label || event.summary || "",
      type: event.event_type || event.type || "",
      subtype: event.event_subtype || event.subtype || "",
      source: event.source || event.env || "",
      severity: event.severity || "",
      ts: event.ts,
    });
  });
  return EVENT_TIMELINE_CATEGORIES.map((cat) => {
    const data = dataByCategory.get(cat.key) || [];
    if (!data.length) return null;
    return {
      id: `__event_timeline:${cat.key}`,
      name: "Events",
      type: "scatter",
      xAxisIndex: eventPanelIndex,
      yAxisIndex: eventPanelIndex,
      symbolSize: 12,
      symbol: cat.key === "action" ? "diamond" : "circle",
      data,
      z: 12,
      emphasis: { scale: 1.45, focus: "self" },
      tooltip: {
        trigger: "item",
        formatter(param) {
          const d = param.data || {};
          const ts = new Date(Array.isArray(param.value) ? param.value[0] : "");
          const timeStr = Number.isFinite(ts.getTime()) ? `${formatTime(d.ts || ts.toISOString())}` : "-";
          const rawTs = d.ts || (Array.isArray(param.value) ? param.value[0] : null);
          const lines = [
            `<strong>${escapeHtml(d.category || "Event")}</strong> <span style="color:#90a5c3">${escapeHtml(timeStr)}</span>`,
            formatEventTooltipLine(d, 220),
          ];
          lines.push(...buildReferenceTooltipLines(payload, rawTs));
          return lines.join("<br>");
        },
      },
    };
  }).filter(Boolean);
}

function buildChartOption(payload) {
  const { grid, xAxis, yAxis, dataZoom, panelIndex } = buildChartCoordinateState(payload);
  const { chartSeries, seriesMetaById } = buildChartSeriesOption(payload);
  const stateSeries = buildMetricStateSeries(payload, panelIndex);
  const eventTimelineSeries = buildEventTimelineSeries(payload, panelIndex);
  const tradeMarkerSeries = buildTradeMarkerSeries(payload, panelIndex);
  const eventMarkerSeries = buildEventMarkLineSeries(payload, panelIndex);
  const legendSeries = [...chartSeries, ...tradeMarkerSeries, ...stateSeries, ...eventTimelineSeries];
  const allSeries = [...legendSeries, ...eventMarkerSeries];
  currentLegendNameToKey = new Map();
  legendSeries.forEach((item) => {
    currentLegendNameToKey.set(item.name, item.id);
  });
  const legendSelected = {};
  legendSeries.forEach((item) => {
    legendSelected[item.name] = chartLegendSelectedState[item.name] !== false;
  });
  const legendNames = [...new Set(legendSeries.map((item) => item.name))];
  return {
    backgroundColor: "transparent",
    animation: false,
    legend: {
      show: chartSeries.length > 0,
      type: "scroll",
      top: 0,
      left: 8,
      right: 28,
      height: 32,
      pageIconColor: "#93c5fd",
      pageIconInactiveColor: "rgba(148, 163, 184, 0.32)",
      pageTextStyle: { color: "#9db2d1", fontSize: 10 },
      textStyle: { color: "#cbd5e1", fontSize: 11 },
      itemWidth: 12,
      itemHeight: 8,
      data: legendNames.map((name) => ({ name, icon: "roundRect" })),
      selected: legendSelected,
    },
    tooltip: { trigger: "axis", axisPointer: { type: "cross", link: [{ xAxisIndex: "all" }], lineStyle: { color: "rgba(226, 232, 240, 0.45)" } }, confine: true, backgroundColor: "rgba(2, 6, 23, 0.96)", borderColor: "rgba(148, 163, 184, 0.22)", borderWidth: 1, padding: [8, 10], textStyle: { color: "#e5eefc", fontSize: 12, lineHeight: 18 }, formatter: buildTooltipFormatter(seriesMetaById, payload) },
    axisPointer: { link: [{ xAxisIndex: "all" }], label: { backgroundColor: "#1e293b" } },
    dataZoom,
    grid,
    xAxis,
    yAxis,
    series: allSeries,
  };
}

function isCompactDefaultHiddenSeries(item) {
  const key = String(item?.key || "");
  const panel = String(item?.panel || "");
  const category = String(item?.category || "");
  if (category === "strategy_metric") {
    return false;
  }
  return (
    key.includes("__macd") ||
    panel === "indicator_macd" ||
    panel.startsWith("market_") ||
    category === "macd_overlay"
  );
}

function shouldShowSeriesControlCard(item) {
  const style = seriesStyleState[item.key] || {};
  return item.panel === "main" || style.visible !== false || Boolean(style.macd?.enabled);
}

function ensureSeriesStyleState(series) {
  const needsLayoutMigration = localStorage.getItem("workspaceChartLayoutVersion") !== CHART_LAYOUT_VERSION;
  let changed = false;
  (series || []).forEach((item) => {
    const hasExisting = Object.prototype.hasOwnProperty.call(seriesStyleState, item.key);
    const existing = seriesStyleState[item.key] || {};
    const fromPayload = item.style || {};
    const macd = existing.macd || {};
    const compactHidden = isCompactDefaultHiddenSeries(item);
    const payloadHidden = fromPayload.visible === false;
    const visible = needsLayoutMigration && compactHidden
      ? false
      : (hasExisting ? existing.visible !== false : (!compactHidden && !payloadHidden));
    seriesStyleState[item.key] = {
      color: overlayStyleColor(item.key, existing, fromPayload),
      width: Number(existing.width || fromPayload.width || (item.panel === "main" ? 2.4 : 2)),
      line_type: existing.line_type || fromPayload.line_type || "solid",
      smooth: Boolean(existing.smooth ?? fromPayload.smooth),
      show_symbol: Boolean(existing.show_symbol ?? fromPayload.show_symbol),
      visible,
      auto_color: isOverlaySeriesKey(item.key) ? (existing.auto_color === false ? false : true) : existing.auto_color,
      macd: {
        enabled: needsLayoutMigration && compactHidden ? false : Boolean(macd.enabled),
        fast: Number(macd.fast || 12),
        slow: Number(macd.slow || 26),
        signal: Number(macd.signal || 9),
      },
    };
    changed = changed || !hasExisting || (needsLayoutMigration && compactHidden);
  });
  if (needsLayoutMigration) {
    localStorage.setItem("workspaceChartLayoutVersion", CHART_LAYOUT_VERSION);
  }
  if (changed || needsLayoutMigration) {
    persistSeriesStyles();
  }
}

function isSeriesRemovable(item) {
  return Boolean(item?.removable);
}

function deleteSeriesCard(seriesKey) {
  const current = { ...(seriesStyleState[seriesKey] || {}), macd: { ...((seriesStyleState[seriesKey] || {}).macd || {}) } };
  current.visible = false;
  current.macd.enabled = false;
  seriesStyleState[seriesKey] = current;
  if (!seriesKey.includes("__macd")) {
    seriesStyleState[`${seriesKey}__macd`] = { ...(seriesStyleState[`${seriesKey}__macd`] || {}), visible: false };
    seriesStyleState[`${seriesKey}__macd_signal`] = { ...(seriesStyleState[`${seriesKey}__macd_signal`] || {}), visible: false };
  }
  persistSeriesStyles();
}

function seriesCardSubtitle(item) {
  const parts = [item?.source_label, item?.source_detail].filter(Boolean);
  if (parts.length) {
    return parts.join(" | ");
  }
  return item?.category ? `Source ${item.category}` : "Source not labeled";
}

function clampText(value, limit = 120) {
  const text = String(value || "").trim();
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit - 1).trimEnd()}...`;
}

function stateLaneColorEntries(lane, overrideColors = loadStateLaneColors()) {
  const seen = new Map();
  (lane.segments || []).forEach((segment) => {
    const value = stateLaneSegmentValue(segment);
    if (seen.has(value)) return;
    seen.set(value, {
      value,
      label: stateLaneSegmentLabel(segment, lane),
      color: stateLaneSegmentColor(lane, segment, overrideColors),
    });
  });
  return [...seen.values()];
}

function renderStateLaneColorControls(container, payload = {}) {
  const lanes = (payload.metric_state_lanes || []).filter((lane) => isStateLaneDisplayable(lane) && (lane.segments || []).length);
  if (!lanes.length) return;
  const title = document.createElement("div");
  title.className = "series-controls-title";
  title.textContent = "State Lane Colors";
  container.appendChild(title);

  const row = document.createElement("div");
  row.className = "series-control-row";
  const overrideColors = loadStateLaneColors();
  lanes.forEach((lane) => {
    const laneKey = stateLaneIdentity(lane);
    const entries = stateLaneColorEntries(lane, overrideColors);
    if (!entries.length) return;
    const card = document.createElement("div");
    card.className = "series-control-chip state-lane-control-chip";
    card.innerHTML = [
      '<div class="series-control-head">',
      '<div class="series-control-titlebox"><span class="series-control-dot"></span><div class="series-control-titlecopy"><strong></strong><div class="series-control-badge">State lane</div><div class="series-control-subtitle"></div></div></div>',
      '<div class="series-control-actions"></div>',
      '</div>',
      '<div class="series-control-block">',
      '<div class="series-control-fields state-lane-color-grid"></div>',
      '</div>',
    ].join("");
    card.querySelector(".series-control-dot").style.background = entries[0].color;
    card.querySelector("strong").textContent = stateLaneDisplayName(lane);
    const subtitle = card.querySelector(".series-control-subtitle");
    subtitle.textContent = clampText(laneKey, 96);
    subtitle.title = laneKey;
    const grid = card.querySelector(".state-lane-color-grid");
    entries.forEach((entry) => {
      const label = document.createElement("label");
      label.title = entry.label;
      const text = document.createElement("span");
      text.textContent = entry.label;
      const input = document.createElement("input");
      input.type = "color";
      input.value = entry.color;
      input.dataset.stateLaneKey = laneKey;
      input.dataset.stateLaneValue = entry.value;
      input.addEventListener("input", (event) => {
        const newColor = event.target.value;
        saveStateLaneColor(laneKey, entry.value, newColor);
        if (entry === entries[0]) {
          card.querySelector(".series-control-dot").style.background = newColor;
        }
        if (currentChartPayload) renderCharts(currentChartPayload);
      });
      label.append(text, input);
      grid.appendChild(label);
    });
    row.appendChild(card);
  });
  container.appendChild(row);
}

function renderSeriesControls(series, payload = {}) {
  const container = document.getElementById("workspaceSeriesControls");
  if (!container) return;
  ensureSeriesStyleState(series);
  const list = series || [];
  const mainSeries = list.filter((item) => item.panel === "main");
  const otherSeries = list.filter((item) => item.panel !== "main");
  const hiddenSeries = list.filter((item) => (seriesStyleState[item.key] || {}).visible === false);
  const controlSeries = list.filter(shouldShowSeriesControlCard);
  container.innerHTML = "";

  const summary = document.createElement("div");
  summary.className = "series-controls-title";
  summary.textContent = "Main " + mainSeries.length + " / Other " + otherSeries.length + " / Hidden " + hiddenSeries.length;
  container.appendChild(summary);

  const legendRow = document.createElement("div");
  legendRow.className = "series-legend-row";
  list.slice(0, 48).forEach((item) => {
    const style = seriesStyleState[item.key] || {};
    const button = document.createElement("button");
    button.type = "button";
    button.className = "series-legend-chip" + (style.visible === false ? " is-off" : "");
    button.dataset.seriesStyle = item.key;
    button.dataset.styleField = "visible";
    button.title = seriesCardSubtitle(item);
    const dot = document.createElement("span");
    dot.className = "series-control-dot";
    dot.style.background = style.color || colorForSeries(item.key);
    const label = document.createElement("span");
    label.textContent = displaySeriesLabel(item);
    button.append(dot, label);
    legendRow.appendChild(button);
  });
  container.appendChild(legendRow);

  const title = document.createElement("div");
  title.className = "series-controls-title";
  title.textContent = "Visible Controls";
  container.appendChild(title);

  const row = document.createElement("div");
  row.className = "series-control-row";
  controlSeries.forEach((item) => {
    const style = seriesStyleState[item.key] || {};
    const card = document.createElement("div");
    card.className = "series-control-chip";
    card.innerHTML = [
      '<div class="series-control-head">',
      '<div class="series-control-titlebox"><span class="series-control-dot"></span><div class="series-control-titlecopy"><strong></strong><div class="series-control-badge"></div><div class="series-control-subtitle"></div></div></div>',
      '<div class="series-control-actions"></div>',
      '</div>',
      '<div class="series-control-block">',
      '<div class="series-control-block-title">Style</div>',
      '<div class="series-control-fields series-control-grid basic-grid">',
      '<label>Color<input type="color" data-style-field="color"></label>',
      '<label>Width<input type="number" min="1" max="8" step="0.5" data-style-field="width"></label>',
      '<label>Line<select data-style-field="line_type"><option value="solid">solid</option><option value="dashed">dashed</option><option value="dotted">dotted</option></select></label>',
      '</div>',
      '<div class="series-control-fields series-control-toggle-row">',
      '<label class="checkbox"><input type="checkbox" data-style-field="smooth"> Smooth</label>',
      '<label class="checkbox"><input type="checkbox" data-style-field="show_symbol"> Points</label>',
      '<label class="checkbox"><input type="checkbox" data-style-field="visible"> Show</label>',
      '</div>',
      '</div>'
    ].join("");
    const color = style.color || colorForSeries(item.key);
    card.querySelector(".series-control-dot").style.background = color;
    card.querySelector("strong").textContent = displaySeriesLabel(item);
    card.querySelector(".series-control-badge").textContent = item?.source_label || "Market line";
    const subtitle = card.querySelector(".series-control-subtitle");
    subtitle.textContent = clampText(item?.source_detail || item?.category || "");
    subtitle.title = seriesCardSubtitle(item);
    if (isSeriesRemovable(item)) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "ghost mini";
      del.dataset.deleteSeries = item.key;
      del.textContent = "Hide";
      card.querySelector(".series-control-actions").appendChild(del);
    }
    card.querySelectorAll("[data-style-field]").forEach((field) => {
      field.dataset.seriesStyle = item.key;
    });
    card.querySelector('[data-style-field="color"]').value = color;
    card.querySelector('[data-style-field="width"]').value = String(style.width || 2);
    card.querySelector('[data-style-field="line_type"]').value = style.line_type || "solid";
    card.querySelector('[data-style-field="smooth"]').checked = Boolean(style.smooth);
    card.querySelector('[data-style-field="show_symbol"]').checked = Boolean(style.show_symbol);
    card.querySelector('[data-style-field="visible"]').checked = style.visible !== false;

    const canMacd = item.render !== "bar" && !String(item.key || "").includes("__macd");
    if (canMacd) {
      const macd = document.createElement("div");
      macd.className = "series-control-block macd-block";
      macd.innerHTML = [
        '<div class="series-control-block-title">MACD</div>',
        '<div class="series-control-fields series-control-toggle-row"><label class="checkbox"><input type="checkbox" data-style-field="macd.enabled"> Enable MACD</label></div>',
        '<div class="series-control-fields series-control-grid macd-grid">',
        '<label>Fast EMA<input type="number" min="2" max="60" data-style-field="macd.fast"></label>',
        '<label>Slow EMA<input type="number" min="3" max="120" data-style-field="macd.slow"></label>',
        '<label>Signal<input type="number" min="2" max="60" data-style-field="macd.signal"></label>',
        '</div>',
        '<div class="series-control-help">Adds MACD and signal lines for this series.</div>'
      ].join("");
      macd.querySelectorAll("[data-style-field]").forEach((field) => {
        field.dataset.seriesStyle = item.key;
      });
      macd.querySelector('[data-style-field="macd.enabled"]').checked = Boolean(style.macd?.enabled);
      macd.querySelector('[data-style-field="macd.fast"]').value = String(style.macd?.fast || 12);
      macd.querySelector('[data-style-field="macd.slow"]').value = String(style.macd?.slow || 26);
      macd.querySelector('[data-style-field="macd.signal"]').value = String(style.macd?.signal || 9);
      card.appendChild(macd);
    }
    row.appendChild(card);
  });
  container.appendChild(row);

  renderStateLaneColorControls(container, payload);

  // Event Timeline dot color editor
  const etTitle = document.createElement("div");
  etTitle.className = "series-controls-title";
  etTitle.textContent = "Event Timeline Colors";
  container.appendChild(etTitle);

  const etRow = document.createElement("div");
  etRow.className = "series-control-row";
  const overrideColors = loadEventCategoryColors();
  EVENT_TIMELINE_CATEGORIES.forEach((cat) => {
    const color = overrideColors[cat.key] || cat.color;
    const card = document.createElement("div");
    card.className = "series-control-chip";
    card.innerHTML = `<div class="series-control-head"><div class="series-control-titlebox"><span class="series-control-dot" style="background:${color}"></span><div class="series-control-titlecopy"><strong>${escapeHtml(cat.label)}</strong></div></div><div class="series-control-actions"></div></div><div class="series-control-block"><div class="series-control-fields series-control-grid basic-grid"><label>Color<input type="color" value="${color}" data-et-cat="${escapeHtml(cat.key)}"></label></div></div>`;
    card.querySelector("input[type=color]").addEventListener("input", (e) => {
      const newColor = e.target.value;
      saveEventCategoryColor(cat.key, newColor);
      card.querySelector(".series-control-dot").style.background = newColor;
      if (currentChartPayload) renderCharts(currentChartPayload);
    });
    etRow.appendChild(card);
  });
  container.appendChild(etRow);
}
function renderCharts(payload) {
  const panels = payload.panels || [];
  const series = payload.series || [];
  const rows = payload.rows || [];
  const target = payload.meta?.target || {};
  pushDebug("[WS] renderCharts:start", {
    row_id: Number(rowId),
    rows: rows.length,
    series: series.length,
    panels: panels.length,
  });

  workspaceChartMeta.textContent = renderTimelineStatus(payload);

  const hasStateLanes = (payload.metric_state_lanes || []).some((lane) => (lane.segments || []).length);
  const hasEvents = (payload.events || []).length > 0;
  if (!panels.length || (!series.length && !hasStateLanes && !hasEvents) || !rows.length) {
    pushDebug("[WS] renderCharts:empty", {
      row_id: Number(rowId),
      rows: rows.length,
      series: series.length,
      panels: panels.length,
    });
    renderChartInsights({});
    setStatus(workspaceCharts, "该范围暂无可绘制数据。");
    return;
  }

  renderChartInsights(payload);
  ensureReadableBacktestChartHeight(payload);
  ensureChartShell();

  const controlsSignature = buildSeriesControlsSignature(series, payload);
  if (controlsSignature !== lastSeriesControlsSignature) {
    renderSeriesControls(series, payload);
    lastSeriesControlsSignature = controlsSignature;
  }

  const chart = ensureChartInstance();
  const option = buildChartOption(payload);
  const renderDiagnostics = importantRenderDiagnostics(payload, option);
  const structureSignature = buildChartStructureSignature(payload);
  lastChartTimeExtent = chartTimeExtentKey(rows, payload.meta || {});

  if (structureSignature !== lastChartStructureSignature) {
    suppressProgrammaticChartZoomSync();
    chart.setOption(option, {
      notMerge: true,
      lazyUpdate: true,
      silent: true,
    });
    lastChartStructureSignature = structureSignature;
  } else {
    suppressProgrammaticChartZoomSync();
    chart.setOption({
      legend: option.legend,
      xAxis: option.xAxis,
      yAxis: option.yAxis,
      series: option.series,
      dataZoom: option.dataZoom,
    }, {
      notMerge: false,
      lazyUpdate: true,
      silent: true,
    });
  }
  pushDebug("[WS] renderCharts:done", {
    row_id: Number(rowId),
    rows: rows.length,
    series: series.length,
    panels: panels.length,
    chart_option_series: option.series?.length || 0,
    render_diagnostics: renderDiagnostics,
  });
}

function seriesKeysForDeltaStreams(payload, streamKeys) {
  const requested = new Set(streamKeys || []);
  const output = new Set();
  (payload?.series || []).forEach((item) => {
    const key = String(item?.key || "");
    const category = String(item?.category || "");
    const marketIndex = Number(item?.market_index ?? -1);
    if (requested.has("price") && marketIndex === 0 && category === "market_target") {
      output.add(key);
    }
    if (requested.has("stats") && marketIndex === 0 && category === "market_target") {
      output.add(key);
    }
    if (requested.has("stats") && !key.startsWith("market_") && ["position", "size", "average", "capital"].includes(category)) {
      output.add(key);
    }
    if (requested.has("metrics") && category === "strategy_metric") {
      output.add(key);
    }
    if (requested.has("watch_markets") && marketIndex > 0 && category === "market_target") {
      output.add(key);
    }
    if (requested.has("overlay") && category.startsWith("overlay_")) {
      output.add(key);
    }
  });
  return [...output];
}

function expandMacdTargetKeys(baseKeys) {
  const out = new Set(baseKeys || []);
  (baseKeys || []).forEach((key) => {
    if (!key || String(key).includes("__macd")) return;
    if (!(seriesStyleState[key] || {}).macd?.enabled) return;
    out.add(`${key}__macd`);
    out.add(`${key}__macd_signal`);
  });
  return [...out];
}

function streamsRequireMacdRecompute(payload, streamKeys) {
  return seriesKeysForDeltaStreams(payload, streamKeys).some((key) => {
    if (!key || String(key).includes("__macd")) return false;
    return Boolean((seriesStyleState[key] || {}).macd?.enabled);
  });
}

function applyDeltaChartPatch(payload, streamKeys) {
  if (!payload || !workspaceChartInstance) {
    renderCharts(payload);
    return;
  }
  const structureSignature = buildChartStructureSignature(payload);
  if (structureSignature !== lastChartStructureSignature) {
    renderCharts(payload);
    return;
  }
  const targetKeys = expandMacdTargetKeys(seriesKeysForDeltaStreams(payload, streamKeys));
  if (!targetKeys.length) {
    return;
  }
  const chart = ensureChartInstance();
  const extentNow = chartTimeExtentKey(payload.rows || [], payload.meta || {});
  const axesUnchanged = lastChartTimeExtent && chartTimeExtentEqual(lastChartTimeExtent, extentNow);
  lastChartTimeExtent = extentNow;
  const { xAxis, yAxis, dataZoom } = buildChartCoordinateState(payload);
  const { chartSeries } = buildChartSeriesOption(payload, targetKeys);
  if (!chartSeries.length) {
    return;
  }
  const renderDiagnostics = importantRenderDiagnostics(payload, { series: chartSeries }, targetKeys);
  const patch = axesUnchanged
    ? { series: chartSeries }
    : { xAxis, yAxis, dataZoom, series: chartSeries };
  suppressProgrammaticChartZoomSync();
  chart.setOption(patch, {
    notMerge: false,
    lazyUpdate: true,
    silent: true,
  });
  workspaceChartMeta.textContent = `${renderTimelineStatus(payload)} · 增量更新 ${targetKeys.length} 条线`;
  pushDebug("[WS] chart:delta-patch", {
    row_id: Number(rowId),
    streams: streamKeys,
    target_keys: targetKeys,
    series_only_patch: Boolean(axesUnchanged),
    chart_option_series: chartSeries.length,
    render_diagnostics: renderDiagnostics,
  });
}

function updateQuickRangeButtons() {
  activeQuickRange = normalizeTimelineRange(activeQuickRange);
  document.querySelectorAll("[data-range-value]").forEach((button) => button.classList.toggle("active", normalizeTimelineRange(button.dataset.rangeValue) === activeQuickRange));
  if (chartCustomToggle) {
    chartCustomToggle.classList.toggle("active", activeQuickRange === "custom");
  }
  syncTimelineUi();
}

function currentTimeRangeParams() {
  const fromIso = localInputToIso(chartFrom.value);
  const isSpecificTo = chartToMode?.value === "specific";
  const toIso = isSpecificTo ? localInputToIso(chartTo.value) : "";
  if (fromIso && toIso) return { from: fromIso, to: toIso };
  if (toIso) {
    const toTs = Date.parse(toIso);
    if (Number.isFinite(toTs)) {
      return {
        from: new Date(toTs - deriveTimelineWindowMs()).toISOString(),
        to: toIso,
      };
    }
  }
  if (fromIso) return { from: fromIso };
  const range = normalizeTimelineRange(activeQuickRange || workspaceState?.chart_defaults?.range || "1d");
  return { range: range === "custom" ? "1d" : range };
}

function createDeltaStreamState() {
  return {
    cursors: {
      price: "",
      stats: "",
      metrics: "",
      watch_markets: "",
      overlay: "",
      events: "",
    },
    lastSuccessAt: {
      price: 0,
      stats: 0,
      metrics: 0,
      watch_markets: 0,
      overlay: 0,
      events: 0,
    },
    failureCount: {
      price: 0,
      stats: 0,
      metrics: 0,
      watch_markets: 0,
      overlay: 0,
      events: 0,
    },
  };
}

function cloneChartPayload(payload) {
  return payload ? {
    ...payload,
    meta: { ...(payload.meta || {}) },
    panels: [...(payload.panels || [])].map((item) => ({ ...item })),
    series: [...(payload.series || [])].map((item) => ({ ...item })),
    events: [...(payload.events || [])].map((item) => ({ ...item })),
    rows: [...(payload.rows || [])].map((row) => ({ ...row })),
  } : null;
}

function chartPayloadLastTs(payload = currentChartPayload) {
  const rows = payload?.rows || [];
  return rows.length ? String(rows[rows.length - 1].ts || "") : "";
}

function buildChartReloadSignature() {
  return JSON.stringify({
    range: normalizeTimelineRange(activeQuickRange || ""),
    from: chartFrom?.value || "",
    to_mode: chartToMode?.value || "latest",
    to: chartTo?.value || "",
    interval: chartInterval?.value || "",
    main_side: chartMainSide?.value || "",
    tracked_markets: trackedMarkets.map((item) => marketIdentity(item)),
    sub_metrics: selectedSubMetrics(),
    include_events: isEventTimelineSelected(),
    overlay_crypto: selectedOverlaySymbols("crypto"),
    overlay_finance: selectedOverlaySymbols("finance"),
    overlay_crypto_fields: selectedOverlayFields("crypto"),
    overlay_finance_fields: selectedOverlayFields("finance"),
  });
}

function resetDeltaState(reason = "reset") {
  deltaStreamState = createDeltaStreamState();
  pushDebug("[WS] delta:reset", {
    row_id: Number(rowId),
    reason,
  });
}

function syncDeltaStateFromChartPayload(payload, reason = "chart-load") {
  const cursor = chartPayloadLastTs(payload) || payload?.meta?.to || "";
  Object.keys(deltaStreamState.cursors).forEach((key) => {
    deltaStreamState.cursors[key] = cursor;
    deltaStreamState.lastSuccessAt[key] = Date.now();
    deltaStreamState.failureCount[key] = 0;
  });
  pushDebug("[WS] delta:sync", {
    row_id: Number(rowId),
    reason,
    cursor,
  });
}

function cacheChartPayload(payload, reason = "chart-load") {
  currentChartPayload = cloneChartPayload(normalizeChartPayloadRows(payload, reason));
  currentChartReloadSignature = buildChartReloadSignature();
  syncDeltaStateFromChartPayload(currentChartPayload, reason);
  if (reason === "full-load") {
    lastFullChartLoadedAt = Date.now();
  }
}

function needsFullReload(reason = "refresh") {
  if (!currentChartPayload) {
    return true;
  }
  const nextSignature = buildChartReloadSignature();
  const changed = !currentChartReloadSignature || currentChartReloadSignature !== nextSignature;
  if (changed) {
    pushDebug("[WS] chart:reload-required", {
      row_id: Number(rowId),
      reason,
      previous_signature: currentChartReloadSignature,
      next_signature: nextSignature,
    });
  }
  return changed;
}

function renderCurrentChartPayload(reason = "local-render") {
  if (!currentChartPayload) {
    return;
  }
  pushDebug("[WS] chart:render-current", {
    row_id: Number(rowId),
    reason,
    rows: (currentChartPayload.rows || []).length,
  });
  renderCharts(currentChartPayload);
}

function shouldUseDeltaRefresh() {
  return Boolean(currentChartPayload && !needsFullReload("delta-check"));
}

function parseTimeSafe(value) {
  const ts = Date.parse(value || "");
  return Number.isFinite(ts) ? ts : null;
}

function trimRowsToMetaRange(rows, meta) {
  const fromTs = parseTimeSafe(meta?.from);
  const toTs = parseTimeSafe(meta?.to);
  if (!Number.isFinite(fromTs) || !Number.isFinite(toTs)) {
    return rows;
  }
  return rows.filter((row) => {
    const current = parseTimeSafe(row.ts);
    return current !== null && current >= fromTs && current <= toTs;
  });
}

function sampleRows(rows, maxPoints = 1400) {
  if ((rows || []).length <= maxPoints) {
    return rows;
  }
  const stride = Math.max(1, Math.ceil(rows.length / maxPoints));
  const sampled = rows.filter((_, index) => index % stride === 0);
  if (sampled[sampled.length - 1]?.ts !== rows[rows.length - 1]?.ts) {
    sampled.push(rows[rows.length - 1]);
  }
  return sampled;
}

function mergeDeltaPoints(rows, points, meta) {
  const baseRows = rows || [];
  const orderedPoints = (points || [])
    .map((point) => ({ ...point, ts: String(point?.ts || "").trim() }))
    .filter((point) => point.ts)
    .sort((a, b) => (parseTimeSafe(a.ts) ?? 0) - (parseTimeSafe(b.ts) ?? 0));
  if (!orderedPoints.length) {
    return baseRows;
  }
  const lastRow = baseRows[baseRows.length - 1] || null;
  const lastBaseTs = parseTimeSafe(lastRow?.ts);
  const firstPointTs = parseTimeSafe(orderedPoints[0]?.ts);
  if (lastRow && lastBaseTs !== null && firstPointTs !== null && firstPointTs >= lastBaseTs) {
    const output = baseRows.slice();
    orderedPoints.forEach((point) => {
      const tail = output[output.length - 1] || {};
      const tailTs = String(tail.ts || "");
      if (point.ts === tailTs) {
        output[output.length - 1] = {
          ...tail,
          ...point,
          ts: tailTs,
        };
        return;
      }
      output.push({
        ...tail,
        ...point,
        ts: point.ts,
      });
    });
    return sampleRows(trimRowsToMetaRange(output, meta));
  }
  const merged = new Map((rows || []).map((row) => [String(row.ts || ""), { ...row }]));
  orderedPoints.forEach((point) => {
    merged.set(point.ts, {
      ...(merged.get(point.ts) || { ts: point.ts }),
      ...point,
      ts: point.ts,
    });
  });
  const ordered = [...merged.values()].sort((a, b) => {
    const left = parseTimeSafe(a.ts) ?? 0;
    const right = parseTimeSafe(b.ts) ?? 0;
    return left - right;
  });
  const output = [];
  let current = {};
  ordered.forEach((row) => {
    current = {
      ...current,
      ...row,
    };
    output.push({
      ...current,
      ts: row.ts,
    });
  });
  return sampleRows(trimRowsToMetaRange(output, meta));
}

function streamIsDue(streamKey, now = Date.now()) {
  const interval = DELTA_STREAM_INTERVALS[streamKey] || 10000;
  return now - Number(deltaStreamState.lastSuccessAt[streamKey] || 0) >= interval;
}

function dueDeltaStreams(now = Date.now()) {
  return Object.keys(DELTA_STREAM_INTERVALS).filter((key) => key !== "events" && streamIsDue(key, now));
}

function expectedSeriesStyleKeys() {
  const keys = new Set();
  const metricKeys = selectedSubMetrics();
  metricKeys.forEach((key) => keys.add(key));
  const marketCount = Math.max(1, trackedMarkets.length || 0);
  for (let index = 0; index < marketCount; index += 1) {
    ["yes_bid", "yes_ask", "no_bid", "no_ask", "yes_mid", "no_mid"].forEach((key) => keys.add(`market_${index}_${key}`));
  }
  ["crypto", "finance"].forEach((type) => {
    selectedOverlaySymbols(type).forEach((symbol) => {
      selectedOverlayFields(type).forEach((fieldKey) => {
        const key = overlaySeriesKey(type, symbol, fieldKey);
        keys.add(key);
        keys.add(`${key}__macd`);
        keys.add(`${key}__macd_signal`);
      });
    });
  });
  (currentChartPayload?.series || []).forEach((item) => {
    if (item?.key) keys.add(item.key);
  });
  Object.entries(seriesStyleState || {}).forEach(([key, style]) => {
    if (!style || typeof style !== "object") return;
    const baseKey = String(key).replace(/__macd(?:_signal)?$/, "");
    if (keys.has(baseKey) || style.macd?.enabled) {
      keys.add(key);
      keys.add(`${baseKey}__macd`);
      keys.add(`${baseKey}__macd_signal`);
    }
  });
  return keys;
}

function currentSeriesStylePayload() {
  const allowed = expectedSeriesStyleKeys();
  const output = {};
  Object.entries(seriesStyleState || {}).forEach(([key, style]) => {
    if (allowed.has(key)) {
      output[key] = style;
    }
  });
  return output;
}

function buildChartRequestParams(options = {}) {
  const includeStyle = options.includeStyle !== false;
  syncTimelineUi();
  syncMainChartModeOptions();
  const params = new URLSearchParams({
    interval: chartInterval.value,
    main_side: chartMainSide.value,
    sub_metrics: selectedSubMetrics().join(","),
    include_events: isEventTimelineSelected() ? "1" : "0",
    overlay_crypto: selectedOverlaySymbols("crypto").join(","),
    overlay_finance: selectedOverlaySymbols("finance").join(","),
    overlay_crypto_fields: selectedOverlayFields("crypto").join(","),
    overlay_finance_fields: selectedOverlayFields("finance").join(","),
    market_targets_json: JSON.stringify(trackedMarkets),
  });
  if (workspaceViewMode === "backtest" && selectedBacktestRunId) {
    params.set("backtest_run_id", selectedBacktestRunId);
  }
  if (includeStyle) {
    params.set("series_style_json", JSON.stringify(currentSeriesStylePayload()));
  }
  const timeRange = currentTimeRangeParams();
  Object.entries(timeRange).forEach(([key, value]) => params.set(key, value));
  return params;
}

function collectWorkspaceConfig() {
  return {
    chart: {
      range: normalizeTimelineRange(activeQuickRange || ""),
      from: chartFrom.value || "",
      to_mode: chartToMode?.value || "latest",
      to: chartTo.value || "",
      interval: chartInterval.value,
      resolution_mode: chartResolutionMode?.value || "auto",
      main_side: chartMainSide.value,
      sub_metrics: selectedSubMetrics(),
      overlay_crypto: selectedOverlaySymbols("crypto"),
      overlay_finance: selectedOverlaySymbols("finance"),
      overlay_crypto_fields: selectedOverlayFields("crypto"),
      overlay_finance_fields: selectedOverlayFields("finance"),
      chart_mode: chartDisplayMode,
      chart_height: workspaceChartHeight,
      market_targets: trackedMarkets,
    },
    styles: seriesStyleState,
    indicators: {},
  };
}

function applyWorkspaceConfig(config) {
  const chart = config?.chart || {};
  seriesStyleState = config?.styles && typeof config.styles === "object" ? { ...config.styles } : {};
  chartDisplayMode = chart.chart_mode || chartDisplayMode || "standard";
  workspaceChartHeight = clampChartHeight(
    chart.chart_height || (chart.chart_mode && CHART_MODE_CONFIG[chart.chart_mode]?.height) || workspaceChartHeight
  );
  persistSeriesStyles();
  persistChartAppearance();
  chartMainSide.value = chart.main_side || workspaceState?.chart_defaults?.main_side || "all";
  if (chartResolutionMode) {
    chartResolutionMode.value = chart.resolution_mode || (chart.interval ? chart.interval : "auto");
    if (![...chartResolutionMode.options].some((option) => option.value === chartResolutionMode.value)) {
      chartResolutionMode.value = "auto";
    }
  }
  activeQuickRange = normalizeTimelineRange(chart.range || workspaceState?.chart_defaults?.range || "1d");
  chartFrom.value = chart.from || "";
  chartTo.value = chart.to || "";
  if (chartToMode) {
    chartToMode.value = chart.to_mode || (chart.to ? "specific" : "latest");
  }
  updateQuickRangeButtons();
  selectedOverlayState = {
    crypto: { symbols: chart.overlay_crypto || [], fields: chart.overlay_crypto_fields || [] },
    finance: { symbols: chart.overlay_finance || [], fields: chart.overlay_finance_fields || [] },
  };
  normalizeTrackedMarkets(chart.market_targets || buildDefaultMarketTargets());
  syncMainChartModeOptions();
  syncMarketSelectorInputs(true);
  renderTrackedMarkets();
  renderMarketStatus();
}

async function loadPresetList() {
  const payload = await fetchJson(`/api/polymarket/workspace-presets?row_id=${encodeURIComponent(rowId)}`);
  workspaceState.workspace_presets = payload.data || [];
  renderPresetOptions(workspaceState.workspace_presets);
}

async function savePreset() {
  const name = workspacePresetName.value.trim();
  if (!name) {
    workspacePresetStatus.textContent = "请先填写预设名称。";
    return;
  }
  workspacePresetStatus.textContent = "正在保存预设...";
  pushDebug("[WS] preset:save:start", {
    row_id: Number(rowId),
    name,
    scope: workspacePresetScope?.value || "",
  });
  const payload = await fetchJson("/api/polymarket/workspace-presets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      scope: workspacePresetScope.value,
      strategy_row_id: rowId,
      target: { type: "strategy", row_id: Number(rowId) },
      config: collectWorkspaceConfig(),
    }),
  });
  workspacePresetStatus.textContent = `已保存预设 ${payload.data?.name || name}`;
  await loadPresetList();
  workspacePresetSelect.value = String(payload.data?.id || "");
  pushDebug("[WS] preset:save:done", {
    row_id: Number(rowId),
    selected_preset_id: workspacePresetSelect?.value || "",
  });
}

async function loadPresetSelection() {
  const presetId = workspacePresetSelect.value;
  if (!presetId) {
    workspacePresetStatus.textContent = "请选择一个预设。";
    return;
  }
  workspacePresetStatus.textContent = "正在加载预设...";
  pushDebug("[WS] preset:load:start", {
    row_id: Number(rowId),
    preset_id: presetId,
  });
  const payload = await fetchJson(`/api/polymarket/workspace-presets/${encodeURIComponent(presetId)}`);
  const preset = payload.data || {};
  workspacePresetName.value = preset.name || "";
  workspacePresetScope.value = preset.scope || "strategy";
  applyWorkspaceConfig(preset.config || {});
  renderOverlayPicker(workspaceState?.chart_capabilities || {}, workspaceState?.chart_defaults || {});
  await loadChart();
  workspacePresetStatus.textContent = `已加载预设 ${preset.name || presetId}`;
  pushDebug("[WS] preset:load:done", {
    row_id: Number(rowId),
    preset_id: workspacePresetSelect?.value || "",
  });
}

async function deletePresetSelection() {
  const presetId = workspacePresetSelect.value;
  if (!presetId) {
    workspacePresetStatus.textContent = "请选择一个预设。";
    return;
  }
  workspacePresetStatus.textContent = "正在删除预设...";
  pushDebug("[WS] preset:delete:start", {
    row_id: Number(rowId),
    preset_id: presetId,
  });
  await fetchJson(`/api/polymarket/workspace-presets/${encodeURIComponent(presetId)}`, { method: "DELETE" });
  workspacePresetName.value = "";
  workspacePresetStatus.textContent = "预设已删除。";
  await loadPresetList();
  pushDebug("[WS] preset:delete:done", {
    row_id: Number(rowId),
    preset_id,
  });
}

async function resolveMarkets(forceRefresh = false) {
  const params = new URLSearchParams({
    q: marketKeywordInput.value.trim(),
    condition_id: marketConditionInput.value.trim(),
    token_id: marketTokenInput.value.trim(),
    limit: "12",
  });
  if (forceRefresh) params.set("refresh", "1");
  const payload = await fetchJson(`/api/polymarket/markets/resolve?${params.toString()}`);
  marketResults = payload.data?.results || [];
  renderMarketResults();
}

async function loadWorkspace(forceResetOverlay = false, silent = false) {
  const t0 = performance.now();
  console.log("[WS] loadWorkspace start", { forceResetOverlay, silent });
  pushDebug("[WS] loadWorkspace:start", {
    row_id: Number(rowId),
    forceResetOverlay,
    silent,
  });
  setDebugMeta(`正在加载工作台 row=${rowId} ...`);
  if (!silent && !workspaceState) {
    setStatus(workspaceSummary, "加载工作台中...");
  }
  let payload;
  let statePayload;
  let backtestPayload;
  try {
    const workspaceParams = new URLSearchParams({ include_events: "0" });
    if (workspaceViewMode === "backtest" && selectedBacktestRunId) {
      workspaceParams.set("source", "backtest");
      workspaceParams.set("run_id", selectedBacktestRunId);
    }
    [payload, statePayload, backtestPayload] = await Promise.all([
      fetchJson(`/api/polymarket/strategies/${rowId}/workspace?${workspaceParams.toString()}`),
      fetchJson(`/api/registry/strategies/${rowId}/state-store`).catch((error) => {
        pushDebug("[WS] state-store:error", { row_id: Number(rowId), message: error?.message || String(error) });
        return null;
      }),
      selectedBacktestRunId
        ? fetchJson(`/api/polymarket/strategies/${rowId}/backtest/results?run_id=${encodeURIComponent(selectedBacktestRunId)}`).catch((error) => {
            pushDebug("[WS] backtest-result:error", { row_id: Number(rowId), run_id: selectedBacktestRunId, message: error?.message || String(error) });
            return null;
          })
        : Promise.resolve(null),
    ]);
  } catch (error) {
    pushDebug("[WS] loadWorkspace:error", {
      row_id: Number(rowId),
      message: error?.message || String(error),
    });
    setDebugMeta(`workspace error | row=${rowId} | ${error?.message || error}`);
    throw error;
  }
  const t1 = performance.now();
  workspaceState = payload.data || {};
  workspaceStateStore = statePayload?.data || null;
  selectedBacktestResults = backtestPayload?.data || selectedBacktestResults;
  window.workspaceState = workspaceState;
  window.selectedBacktestResults = selectedBacktestResults;
  window.workspaceViewMode = workspaceViewMode;
  if (workspaceViewMode === "backtest" && selectedBacktestResults?.selected_run) {
    applyBacktestWindowToChart(selectedBacktestResults.selected_run);
  }
  window.workspaceStateStore = workspaceStateStore;
  renderHeader(workspaceState.strategy || {});
  renderSources(workspaceState.source_statuses || {});
  renderSettings(workspaceState.settings_schema || [], workspaceState.strategy || {});
  renderMetricPicker(workspaceState.chart_capabilities || {}, workspaceState.chart_defaults || {});
  syncOverlayState(workspaceState.chart_capabilities || {}, workspaceState.chart_defaults || {}, forceResetOverlay);
  renderOverlayPicker(workspaceState.chart_capabilities || {}, workspaceState.chart_defaults || {});
  if (!silent) {
    chartMainSide.value = workspaceState.chart_defaults?.main_side || chartMainSide.value || "all";
    activeQuickRange = normalizeTimelineRange(activeQuickRange || workspaceState.chart_defaults?.range || "1d");
  }
  if (!silent || !trackedMarkets.length) {
    normalizeTrackedMarkets(buildDefaultMarketTargets());
  }
  syncMainChartModeOptions();
  syncMarketSelectorInputs(!silent);
  renderTrackedMarkets();
  renderMarketStatus();
  renderSummary(workspaceState.strategy || {});
  renderWorkspaceModeEvents();
  renderBacktest(workspaceState.backtest || null);
  updateWorkspaceViewBadge();
  renderPresetOptions(workspaceState.workspace_presets || []);
  renderMarketResults();
  updateQuickRangeButtons();
  const t2 = performance.now();
  console.log(
    `[WS] loadWorkspace fetch=${(t1 - t0).toFixed(1)}ms render=${(t2 - t1).toFixed(1)}ms total=${(t2 - t0).toFixed(1)}ms`
  );
  pushDebug("[WS] loadWorkspace:done", {
    row_id: Number(rowId),
    tracked_markets: trackedMarkets.length,
    preset_count: (workspaceState?.workspace_presets || []).length,
    source_status_keys: Object.keys(workspaceState?.source_statuses || {}),
    fetch_ms: Number((t1 - t0).toFixed(1)),
    render_ms: Number((t2 - t1).toFixed(1)),
    total_ms: Number((t2 - t0).toFixed(1)),
  });
  setDebugMeta(
    `workspace ok | row=${rowId} | trackedMarkets=${trackedMarkets.length} | presets=${(workspaceState?.workspace_presets || []).length}`
  );
}

async function loadChart() {
  const t0 = performance.now();
  syncTimelineUi();
  console.log("[WS] loadChart start", {
    interval: chartInterval.value,
    mainSide: chartMainSide.value,
    trackedMarkets: trackedMarkets.length,
    overlayCrypto: selectedOverlaySymbols("crypto"),
    overlayFinance: selectedOverlaySymbols("finance"),
    subMetrics: selectedSubMetrics(),
  });
  pushDebug("[WS] loadChart:start", {
    row_id: Number(rowId),
    from: chartFrom?.value || "",
    to: chartTo?.value || "",
    interval: chartInterval?.value || "",
    main_side: chartMainSide?.value || "",
    tracked_markets: trackedMarkets.map((m) => ({
      label: m.label,
      condition_id: m.condition_id,
      yes_token: m.yes_token,
      no_token: m.no_token,
    })),
    sub_metrics: selectedSubMetrics(),
    overlay_crypto: selectedOverlaySymbols("crypto"),
    overlay_finance: selectedOverlaySymbols("finance"),
    overlay_crypto_fields: selectedOverlayFields("crypto"),
    overlay_finance_fields: selectedOverlayFields("finance"),
  });
  setDebugMeta(`正在加载图表 row=${rowId} ...`);
  repairSeriesStyleState();
  revealSelectedSubMetricSeries();
  ensureChartShell();
  workspaceChartMeta.textContent = "图表加载中...";
  isChartLoading = true;
  const previousController = currentChartAbortController;
  const requestAbortController = new AbortController();
  if (previousController) {
    previousController.abort();
  }
  currentChartAbortController = requestAbortController;
  const requestId = ++currentChartRequestId;
  const params = buildChartRequestParams();
  try {
    const t1 = performance.now();
    const payload = await fetchJson(`/api/polymarket/strategies/${rowId}/chart?${params.toString()}`, {
      signal: requestAbortController.signal,
    });
    const t2 = performance.now();
    if (requestId !== currentChartRequestId) {
      console.warn("[WS] loadChart dropped outdated response");
      pushDebug("[WS] loadChart:dropped", {
        row_id: Number(rowId),
        request_id: requestId,
        current_request_id: currentChartRequestId,
      });
      return;
    }
    const chartData = payload?.data || {};
    cacheChartPayload(chartData, "full-load");
    const renderPayload = currentChartPayload || chartData;
    renderCharts(renderPayload);
    const t3 = performance.now();
    console.log(
      `[WS] loadChart prepare=${(t1 - t0).toFixed(1)}ms fetch=${(t2 - t1).toFixed(1)}ms render=${(t3 - t2).toFixed(1)}ms total=${(t3 - t0).toFixed(1)}ms`
    );
    pushDebug("[WS] loadChart:done", {
      row_id: Number(rowId),
      row_count: (renderPayload.rows || []).length,
      series_count: (renderPayload.series || []).length,
      panel_count: (renderPayload.panels || []).length,
      meta: renderPayload.meta || {},
      prepare_ms: Number((t1 - t0).toFixed(1)),
      fetch_ms: Number((t2 - t1).toFixed(1)),
      render_ms: Number((t3 - t2).toFixed(1)),
      total_ms: Number((t3 - t0).toFixed(1)),
    });
    setDebugMeta(
      `chart ok | row=${rowId} | rows=${(renderPayload.rows || []).length} | series=${(renderPayload.series || []).length} | panels=${(renderPayload.panels || []).length} | ${compactPriceRenderSummary(renderPayload)} | ${chartDebugSummary(renderPayload)}`
    );
  } catch (error) {
    const tErr = performance.now();
    if (isAbortError(error)) {
      console.warn(`[WS] loadChart aborted total=${(tErr - t0).toFixed(1)}ms`);
      pushDebug("[WS] loadChart:aborted", {
        row_id: Number(rowId),
        total_ms: Number((tErr - t0).toFixed(1)),
      });
      return;
    }
    console.error(`[WS] loadChart fail total=${(tErr - t0).toFixed(1)}ms`, error);
    pushDebug("[WS] loadChart:error", {
      row_id: Number(rowId),
      message: error?.message || String(error),
      total_ms: Number((tErr - t0).toFixed(1)),
    });
    setDebugMeta(`chart error | row=${rowId} | ${error?.message || error}`);
    throw error;
  } finally {
    if (currentChartRequestId === requestId) {
      isChartLoading = false;
    }
    if (currentChartAbortController === requestAbortController) {
      currentChartAbortController = null;
    }
  }
}

async function loadChartDelta(streams) {
  const requestedStreams = [...new Set((streams || []).filter(Boolean))];
  if (!requestedStreams.length || !shouldUseDeltaRefresh()) {
    return null;
  }
  if (!currentChartPayload || !lastFullChartLoadedAt || Date.now() - lastFullChartLoadedAt < 1500) {
    return null;
  }
  const t0 = performance.now();
  pushDebug("[WS] delta:start", {
    row_id: Number(rowId),
    streams: requestedStreams,
    cursors: requestedStreams.reduce((acc, key) => ({ ...acc, [key]: deltaStreamState.cursors[key] || "" }), {}),
  });
  setDebugMeta(`增量刷新中 row=${rowId} | streams=${requestedStreams.join(",")}`);
  isChartLoading = true;
  const previousController = currentChartAbortController;
  const requestAbortController = new AbortController();
  if (previousController) {
    previousController.abort();
  }
  currentChartAbortController = requestAbortController;
  const requestId = ++currentChartRequestId;
  const params = buildChartRequestParams({ includeStyle: false });
  params.set("streams", requestedStreams.join(","));
  requestedStreams.forEach((streamKey) => {
    params.set(`cursor_${streamKey}`, deltaStreamState.cursors[streamKey] || "");
  });
  let payload;
  try {
    payload = await fetchJson(`/api/polymarket/strategies/${rowId}/chart-delta?${params.toString()}`, {
      signal: requestAbortController.signal,
    });
  } catch (error) {
    if (isAbortError(error)) {
      pushDebug("[WS] delta:aborted", {
        row_id: Number(rowId),
        streams: requestedStreams,
      });
      return null;
    }
    requestedStreams.forEach((streamKey) => {
      deltaStreamState.failureCount[streamKey] = Number(deltaStreamState.failureCount[streamKey] || 0) + 1;
    });
    pushDebug("[WS] delta:error", {
      row_id: Number(rowId),
      streams: requestedStreams,
      message: error?.message || String(error),
    });
    throw error;
  } finally {
    if (currentChartAbortController === requestAbortController) {
      currentChartAbortController = null;
    }
    if (currentChartRequestId === requestId) {
      isChartLoading = false;
    }
  }
  if (requestId !== currentChartRequestId) {
    pushDebug("[WS] delta:dropped", {
      row_id: Number(rowId),
      request_id: requestId,
      current_request_id: currentChartRequestId,
    });
    return null;
  }
  const deltaData = payload?.data || {};
  if (!currentChartPayload || deltaData.reload_required) {
    await loadChart();
    return null;
  }
  const nextPayload = {
    ...currentChartPayload,
    meta: { ...(currentChartPayload.meta || {}) },
    panels: currentChartPayload.panels || [],
    series: currentChartPayload.series || [],
    events: currentChartPayload.events || [],
    rows: currentChartPayload.rows || [],
  };
  nextPayload.meta = {
    ...(nextPayload.meta || {}),
    ...(deltaData.meta || {}),
  };
  let rowsChanged = false;
  requestedStreams.forEach((streamKey) => {
    const streamPayload = deltaData[streamKey] || {};
    if (Array.isArray(streamPayload.points) && streamPayload.points.length) {
      nextPayload.rows = mergeDeltaPoints(nextPayload.rows || [], streamPayload.points, nextPayload.meta);
      rowsChanged = true;
    }
    if (streamPayload.next_cursor) {
      deltaStreamState.cursors[streamKey] = streamPayload.next_cursor;
    }
    deltaStreamState.lastSuccessAt[streamKey] = Date.now();
    deltaStreamState.failureCount[streamKey] = 0;
  });
  if (rowsChanged && streamsRequireMacdRecompute(nextPayload, requestedStreams)) {
    recomputeMacdOverlayColumns(nextPayload.rows, nextPayload.series);
  }
  currentChartPayload = nextPayload;
  currentChartReloadSignature = buildChartReloadSignature();
  applyDeltaChartPatch(currentChartPayload, requestedStreams);
  pushDebug("[WS] delta:done", {
    row_id: Number(rowId),
    streams: requestedStreams,
    rows: (currentChartPayload.rows || []).length,
    render_summary: compactPriceRenderSummary(currentChartPayload),
    total_ms: Number((performance.now() - t0).toFixed(1)),
  });
  setDebugMeta(`delta ok | row=${rowId} | streams=${requestedStreams.join(",")} | rows=${(currentChartPayload.rows || []).length} | ${compactPriceRenderSummary(currentChartPayload)}`);
  return deltaData;
}

async function refreshChartAuto() {
  if (!workspaceBootReady || workspaceBooting || !workspaceState) {
    pushDebug("[WS] auto-refresh:skip", {
      row_id: Number(rowId),
      reason: "workspace-not-ready",
    });
    return;
  }
  if (needsFullReload("auto-refresh")) {
    resetDeltaState("auto-refresh-full-reload");
    await loadChart();
  } else {
    const streams = dueDeltaStreams();
    if (streams.length) {
      await loadChartDelta(streams);
    }
  }
  if (streamIsDue("events") && !isEventsLoading) {
    isEventsLoading = true;
    try {
      await loadEvents();
      deltaStreamState.lastSuccessAt.events = Date.now();
      deltaStreamState.failureCount.events = 0;
    } catch (error) {
      deltaStreamState.failureCount.events = Number(deltaStreamState.failureCount.events || 0) + 1;
      pushDebug("[WS] events:auto:error", {
        row_id: Number(rowId),
        message: error?.message || String(error),
      });
    } finally {
      isEventsLoading = false;
    }
  }
}

async function loadEvents() {
  if (workspaceViewMode === "backtest" && selectedBacktestResults?.selected_run) {
    renderWorkspaceModeEvents();
    return;
  }
  const t0 = performance.now();
  console.log("[WS] loadEvents start");
  pushDebug("[WS] loadEvents:start", { row_id: Number(rowId) });
  let payload;
  try {
    payload = await fetchJson(`/api/polymarket/strategies/${rowId}/events?limit=120`);
  } catch (error) {
    pushDebug("[WS] loadEvents:error", {
      row_id: Number(rowId),
      message: error?.message || String(error),
    });
    throw error;
  }
  const t1 = performance.now();
  _fullEventsList = limitEventsWithTypeGuarantee(payload.data?.data || []);
  renderEvents(_fullEventsList);
  const t2 = performance.now();
  console.log(
    `[WS] loadEvents fetch=${(t1 - t0).toFixed(1)}ms render=${(t2 - t1).toFixed(1)}ms total=${(t2 - t0).toFixed(1)}ms`
  );
  pushDebug("[WS] loadEvents:done", {
    row_id: Number(rowId),
    event_count: (payload?.data?.data || []).length,
    fetch_ms: Number((t1 - t0).toFixed(1)),
    render_ms: Number((t2 - t1).toFixed(1)),
    total_ms: Number((t2 - t0).toFixed(1)),
  });
  deltaStreamState.lastSuccessAt.events = Date.now();
  deltaStreamState.failureCount.events = 0;
}

window.loadWorkspaceEvents = loadEvents;

if (workspaceDebugClearBtn) {
  workspaceDebugClearBtn.addEventListener("click", () => {
    workspaceDebugLines = [];
    if (workspaceDebugLog) {
      workspaceDebugLog.textContent = "日志已清空。";
    }
    setDebugMeta("日志已清空。");
  });
}

function collectSettingsPayload() {
  const payload = {};
  settingsForm.querySelectorAll("[data-setting-key]").forEach((field) => {
    payload[field.dataset.settingKey] = field.type === "checkbox" ? (field.checked ? "True" : "False") : field.value;
  });
  return payload;
}

function collectWorkspaceUserState() {
  const values = {};
  settingsForm.querySelectorAll("[data-user-state-key]").forEach((field) => {
    values[field.dataset.userStateKey] = field.type === "checkbox" ? field.checked : field.value;
  });
  return values;
}

function collectWorkspaceMachineState() {
  const value = settingsForm?.querySelector("[data-machine-state-key='state']")?.value
    || workspaceMachineStateSelect?.value
    || "auto";
  return { state: value };
}

function parseWorkspaceRuntimeState() {
  const values = {};
  settingsForm.querySelectorAll("[data-runtime-state-key]").forEach((field) => {
    values[field.dataset.runtimeStateKey] = field.type === "checkbox" ? field.checked : field.value;
  });
  return values;
}

async function saveWorkspaceStateNamespaces() {
  if (!workspaceStateStore) return;
  const stateStore = workspaceStateStore || {};
  const mode = stateStore.mode || workspaceStrategyMode(workspaceState?.strategy || {});
  await fetchJson(`/api/registry/strategies/${rowId}/state-store/machine`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      values: collectWorkspaceMachineState(),
      replace: false,
      reason: "workspace settings edit",
    }),
  });
  await fetchJson(`/api/registry/strategies/${rowId}/state-store/user`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      values: collectWorkspaceUserState(),
      replace: true,
      reason: "workspace settings edit",
    }),
  });
  if (mode === "Stop") {
    await fetchJson(`/api/registry/strategies/${rowId}/state-store/runtime`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        values: parseWorkspaceRuntimeState(),
        replace: true,
        reason: "workspace settings edit",
      }),
    });
  }
}

function applyWorkspaceParamPaste() {
  const helper = window.StrategyParamPaste;
  const textArea = settingsForm.querySelector("[data-settings-paste-text]");
  if (!helper || !textArea) return;
  const params = helper.parseParamText(textArea.value);
  const fields = Array.from(settingsForm.querySelectorAll("[data-setting-key]"));
  const result = helper.applyParamsToFields(params, fields, (field) => field.dataset.settingKey || "");
  if (!Object.keys(params).length) {
    settingsMessage.textContent = "没有识别到可填入的参数。支持 JSON 或 key = value。";
  } else if (result.matched.length) {
    const suffix = result.unmatched.length ? `，${result.unmatched.length} 个字段未匹配` : "";
    settingsMessage.textContent = `已填入 ${result.matched.length} 个参数${suffix}，保存后生效。`;
  } else {
    settingsMessage.textContent = "已解析参数，但当前策略没有匹配字段。";
  }
}

chartOverlayPicker.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-overlay-toggle]");
  if (toggle) {
    toggleOverlaySymbol(toggle.dataset.overlayType, toggle.dataset.overlayToggle);
    renderOverlayPicker(workspaceState?.chart_capabilities || {}, workspaceState?.chart_defaults || {});
    scheduleChartReload();
    return;
  }
  const fieldToggle = event.target.closest("[data-overlay-field]");
  if (fieldToggle) {
    toggleOverlayField(fieldToggle.dataset.overlayType, fieldToggle.dataset.overlayField);
    renderOverlayPicker(workspaceState?.chart_capabilities || {}, workspaceState?.chart_defaults || {});
    scheduleChartReload();
    return;
  }
  const remove = event.target.closest("[data-overlay-remove]");
  if (remove) {
    removeOverlaySymbol(remove.dataset.overlayType, remove.dataset.overlayRemove);
    renderOverlayPicker(workspaceState?.chart_capabilities || {}, workspaceState?.chart_defaults || {});
    scheduleChartReload();
  }
});

chartMetricPicker.addEventListener("change", () => {
  revealSelectedSubMetricSeries();
  scheduleChartReload();
});

document.addEventListener("click", (event) => {
  const legendToggle = event.target.closest(".series-legend-chip[data-series-style]");
  if (legendToggle) {
    const seriesKey = legendToggle.dataset.seriesStyle;
    const current = { ...(seriesStyleState[seriesKey] || {}) };
    current.visible = current.visible === false;
    seriesStyleState[seriesKey] = current;
    persistSeriesStyles();
    renderCurrentChartPayload("legend-toggle");
    return;
  }
  const deleteButton = event.target.closest("[data-delete-series]");
  if (deleteButton) {
    deleteSeriesCard(deleteButton.dataset.deleteSeries);
    renderCurrentChartPayload("delete-series");
  }
});

document.addEventListener("input", (event) => {
  const target = event.target.closest("[data-series-style]");
  if (!target) return;

  const key = target.dataset.seriesStyle;
  const field = target.dataset.styleField;
  const current = {
    ...(seriesStyleState[key] || {}),
    macd: { ...((seriesStyleState[key] || {}).macd || {}) },
  };

  if (field.startsWith("macd.")) {
    const macdField = field.split(".")[1];
    current.macd[macdField] = target.type === "checkbox" ? target.checked : Number(target.value || 0);
  } else {
    current[field] = target.type === "checkbox"
      ? target.checked
      : (field === "width" ? Number(target.value || 2) : target.value);
    if (field === "color") {
      current.auto_color = false;
    }
  }

  seriesStyleState[key] = current;
  persistSeriesStyles();
});

document.addEventListener("change", (event) => {
  const machineStateSelect = event.target.closest("#workspaceMachineStateSelect, [data-machine-state-key='state']");
  if (machineStateSelect) {
    const prev = machineStateSelect.dataset.prev || workspaceMachineState(workspaceState?.strategy || {}, workspaceStateStore);
    const next = machineStateSelect.value || "auto";
    if (next === prev && machineStateSelect.id === "workspaceMachineStateSelect") return;
    machineStateSelect.disabled = true;
    fetchJson(`/api/registry/strategies/${rowId}/state-store/machine`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        values: { state: next },
        replace: false,
        reason: "workspace state switch",
      }),
    }).then((payload) => {
      workspaceStateStore = {
        ...(workspaceStateStore || {}),
        ...(payload.data || {}),
        state: next,
        machine_state: next,
        machine: { ...((payload.data || {}).machine || {}), state: next },
      };
      window.workspaceStateStore = workspaceStateStore;
      if (!workspaceState) workspaceState = {};
      workspaceState.strategy = {
        ...(workspaceState.strategy || {}),
        state: next,
        machine_state: next,
      };
      syncWorkspaceMachineStateControl(workspaceState.strategy || {}, workspaceStateStore);
      renderSummary(workspaceState.strategy || {});
      renderSettings(workspaceState.settings_schema || [], workspaceState.strategy || {});
    }).catch((error) => {
      machineStateSelect.value = prev;
      alert("State switch failed: " + (error?.message || error));
    }).finally(() => {
      machineStateSelect.disabled = false;
    });
    return;
  }

  const stateSelect = event.target.closest("#workspaceStateSelect");
  if (stateSelect) {
    const prev = stateSelect.dataset.prev || workspaceStrategyMode(workspaceState?.strategy || {});
    const next = stateSelect.value;
    if (next === prev) return;

    const confirmMessage = stateTransitionConfirmMessage(prev, next);
    if (confirmMessage && !confirm(confirmMessage)) {
      stateSelect.value = prev;
      stateSelect.className = `state-select workspace-state-select state-${prev}`;
      return;
    }

    stateSelect.disabled = true;
    fetchJson(`/api/registry/strategies/${rowId}/mode`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: next }),
    }).then((payload) => {
      if (!workspaceState) workspaceState = {};
      const machineState = workspaceMachineState(workspaceState.strategy || {}, workspaceStateStore);
      workspaceState.strategy = {
        ...(workspaceState.strategy || {}),
        ...(payload.data || {}),
        mode: next,
        state: machineState,
        machine_state: machineState,
      };
      syncWorkspaceStateControl(workspaceState.strategy || {});
      renderSummary(workspaceState.strategy || {});
      return loadWorkspace(false, true);
    }).catch((error) => {
      stateSelect.value = prev;
      stateSelect.className = `state-select workspace-state-select state-${prev}`;
      alert("鐘舵€佸垏鎹㈠け璐? " + (error?.message || error));
    }).finally(() => {
      stateSelect.disabled = false;
    });
    return;
  }

  const target = event.target.closest("[data-series-style]");
  if (!target) return;
  renderCurrentChartPayload("series-style-change");
});

document.getElementById("workspaceRefreshBtn").addEventListener("click", async () => {
  try {
    await loadWorkspace(false, true);
    await loadChart();
    loadEvents().catch(() => {});
    if (workspaceViewMode === "backtest") {
      disconnectWorkspaceLive();
      setAutoRefresh(false);
    } else {
      connectWorkspaceLive();
    }
  } catch (error) {
    setStatus(workspaceSummary, error.message);
  }
});

document.getElementById("chartReloadBtn").addEventListener("click", () => {
  loadChart().catch((error) => setStatus(workspaceCharts, error.message));
});

document.getElementById("workspaceUseStrategyMarketBtn").addEventListener("click", async () => {
  normalizeTrackedMarkets(buildDefaultMarketTargets());
  syncMarketSelectorInputs(true);
  renderTrackedMarkets();
  renderMarketStatus();
  renderSummary(workspaceState?.strategy || {});
  await loadChart();
});

document.getElementById("workspaceSearchRefreshBtn").addEventListener("click", () => {
  resolveMarkets(true).catch((error) => setStatus(workspaceMarketResults, error.message));
});

document.getElementById("marketSearchBtn").addEventListener("click", () => {
  resolveMarkets(false).catch((error) => setStatus(workspaceMarketResults, error.message));
});

workspaceTrackedMarkets.addEventListener("click", async (event) => {
  const watch = event.target.closest("[data-watch-market]");
  if (watch) {
    const market = trackedMarkets.find((item) => marketIdentity(item) === watch.dataset.watchMarket);
    if (!market) return;
    marketUi.toggleWatchlist(market);
    renderTrackedMarkets();
    return;
  }
  const remove = event.target.closest("[data-remove-market]");
  if (!remove) return;
  trackedMarkets = trackedMarkets.filter((item) => marketIdentity(item) !== remove.dataset.removeMarket);
  normalizeTrackedMarkets(trackedMarkets);
  renderTrackedMarkets();
  renderMarketStatus();
  renderSummary(workspaceState?.strategy || {});
  await loadChart();
});

workspaceMarketResults.addEventListener("click", async (event) => {
  const watch = event.target.closest("[data-watch-result]");
  if (watch) {
    const market = marketResults.find((item) => marketIdentity({
      type: "market",
      condition_id: item.condition_id || "",
      yes_token: item.yes_token || "",
      no_token: item.no_token || "",
    }) === watch.dataset.watchResult);
    if (!market) return;
    marketUi.toggleWatchlist({
      type: "market",
      label: market.question || market.slug || market.condition_id || "Manual Market",
      question: market.question || market.slug || "",
      slug: market.slug || "",
      event_slug: market.event_slug || market.eventSlug || market.raw?.eventSlug || market.raw?.event_slug || "",
      group_item_title: market.group_item_title || market.groupItemTitle || market.raw?.groupItemTitle || "",
      url: market.url || market.raw?.url || "",
      condition_id: market.condition_id || "",
      yes_token: market.yes_token || "",
      no_token: market.no_token || "",
      category: market.category || "",
    });
    renderMarketResults();
    return;
  }
  const add = event.target.closest("[data-add-market]");
  if (!add) return;
  const market = marketResults.find((item) => marketIdentity({
    type: "market",
    condition_id: item.condition_id || "",
    yes_token: item.yes_token || "",
    no_token: item.no_token || "",
  }) === add.dataset.addMarket);
  if (!market) return;
  normalizeTrackedMarkets([
    ...trackedMarkets,
    {
      type: "market",
      label: market.question || market.slug || market.condition_id || "Manual Market",
      question: market.question || market.slug || "",
      slug: market.slug || "",
      event_slug: market.event_slug || market.eventSlug || market.raw?.eventSlug || market.raw?.event_slug || "",
      group_item_title: market.group_item_title || market.groupItemTitle || market.raw?.groupItemTitle || "",
      url: market.url || market.raw?.url || "",
      condition_id: market.condition_id || "",
      yes_token: market.yes_token || "",
      no_token: market.no_token || "",
      category: market.category || "",
    },
  ]);
  renderTrackedMarkets();
  renderMarketStatus();
  renderMarketResults();
  renderSummary(workspaceState?.strategy || {});
  await loadChart();
});

workspaceBacktest?.addEventListener("click", (event) => {
  const view = event.target.closest("[data-workspace-view]");
  if (view) {
    event.preventDefault();
    switchWorkspaceView(view.dataset.workspaceView).catch((error) => setStatus(workspaceBacktest, `切换视图失败: ${error.message}`));
    return;
  }
  const create = event.target.closest("[data-backtest-create]");
  if (!create) return;
  event.preventDefault();
  createWorkspaceBacktest().catch((error) => setStatus(workspaceBacktest, `创建回测失败: ${error.message}`));
});

document.querySelectorAll("[data-range-value]").forEach((button) => {
  button.addEventListener("click", () => {
    activeQuickRange = normalizeTimelineRange(button.dataset.rangeValue);
    chartFrom.value = "";
    chartTo.value = "";
    if (chartToMode) chartToMode.value = "latest";
    closeCustomTimelinePanel();
    chartViewState = { start: null, end: null, startValue: null, endValue: null };
    updateQuickRangeButtons();
    scheduleChartReload();
  });
});

[chartFrom, chartTo, chartToMode].forEach((element) => {
  element?.addEventListener("change", () => {
    activeQuickRange = "custom";
    chartViewState = { start: null, end: null, startValue: null, endValue: null };
    updateQuickRangeButtons();
    scheduleChartReload();
  });
});

chartResolutionMode?.addEventListener("change", () => {
  syncTimelineUi();
  scheduleChartReload();
});

chartCustomToggle?.addEventListener("click", () => {
  openCustomTimelinePanel();
});

chartReturnLatestBtn?.addEventListener("click", () => {
  chartViewState = { start: null, end: null, startValue: null, endValue: null };
  setTimelineToLatest();
  scheduleChartReload();
});

[chartMainSide].forEach((element) => {
  element?.addEventListener("change", () => {
    scheduleChartReload();
  });
});

[marketConditionInput, marketTokenInput, marketKeywordInput].forEach((input) => {
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    resolveMarkets(false).catch((error) => setStatus(workspaceMarketResults, error.message));
  });
});

document.getElementById("workspacePresetSaveBtn").addEventListener("click", () => {
  savePreset().catch((error) => {
    workspacePresetStatus.textContent = `保存失败: ${error.message}`;
  });
});

document.getElementById("workspacePresetLoadBtn").addEventListener("click", () => {
  loadPresetSelection().catch((error) => {
    workspacePresetStatus.textContent = `加载失败: ${error.message}`;
  });
});

document.getElementById("workspacePresetDeleteBtn").addEventListener("click", () => {
  deletePresetSelection().catch((error) => {
    workspacePresetStatus.textContent = `删除失败: ${error.message}`;
  });
});

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  settingsMessage.textContent = "保存中...";
  try {
    await fetchJson(`/api/polymarket/strategies/${rowId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectSettingsPayload()),
    });
    await saveWorkspaceStateNamespaces();
    await loadWorkspace(false, true);
    await loadChart();
    settingsMessage.textContent = "保存成功。";
  } catch (error) {
    settingsMessage.textContent = `保存失败: ${error.message}`;
  }
});

settingsForm.addEventListener("click", (event) => {
  const pasteButton = event.target.closest("[data-settings-paste-apply]");
  if (pasteButton) {
    event.preventDefault();
    applyWorkspaceParamPaste();
    return;
  }
  const button = event.target.closest("[data-autofill-key]");
  if (button) {
    event.preventDefault();
    autofillSettingFromUseData(button);
    return;
  }
  const clearUser = event.target.closest("[data-clear-user-state]");
  if (clearUser) {
    event.preventDefault();
    fetchJson(`/api/registry/strategies/${rowId}/state-store/user?reason=workspace%20clear`, { method: "DELETE" })
      .then(() => loadWorkspace(false, true))
      .catch((error) => { settingsMessage.textContent = `清除 Controls 失败: ${error.message}`; });
    return;
  }
  const clearRuntime = event.target.closest("[data-clear-runtime-state]");
  if (clearRuntime) {
    event.preventDefault();
    fetchJson(`/api/registry/strategies/${rowId}/state-store/runtime?reason=workspace%20clear`, { method: "DELETE" })
      .then(() => loadWorkspace(false, true))
      .catch((error) => { settingsMessage.textContent = `清除 RuntimeState 失败: ${error.message}`; });
  }
});

window.addEventListener("resize", () => {
  if (workspaceChartInstance) {
    workspaceChartInstance.resize();
  }
  if (workspaceBacktestEquityChart) {
    workspaceBacktestEquityChart.resize();
  }
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    workspaceAutoRefreshBadge.className = "state-chip pending";
    workspaceAutoRefreshBadge.textContent = "hold";
    workspaceAutoRefreshText.textContent = "标签页隐藏中，自动刷新暂时降频。";
  } else {
    if (workspaceViewMode === "backtest") {
      updateWorkspaceViewBadge();
      return;
    }
    if (!workspaceBootReady || workspaceBooting || !workspaceState) {
      pushDebug("[WS] visibility:defer", {
        row_id: Number(rowId),
        reason: "workspace-not-ready",
      });
      return;
    }
    connectWorkspaceLive();
    setAutoRefresh(autoRefreshEnabled);
    refreshChartAuto().catch(() => {});
  }
});

window.addEventListener("beforeunload", () => {
  disconnectWorkspaceLive();
});

async function boot() {
  workspaceBooting = true;
  workspaceBootReady = false;
  try {
    await loadWorkspace(true);
    const initialEventsLoad = loadEvents().catch((error) => {
      pushDebug("[WS] boot:events:error", {
        row_id: Number(rowId),
        message: error?.message || String(error),
      });
    });
    await loadChart();
    workspaceBootReady = true;
    initialEventsLoad.catch(() => {});
    if (workspaceViewMode === "backtest") {
      disconnectWorkspaceLive();
      setAutoRefresh(false);
      updateWorkspaceViewBadge();
    } else {
      connectWorkspaceLive();
      setAutoRefresh(true);
    }
  } catch (error) {
    setStatus(workspaceSummary, error.message);
    setStatus(workspaceCharts, error.message);
  } finally {
    workspaceBooting = false;
  }
}

boot();
