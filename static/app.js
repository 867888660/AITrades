const overviewCards = document.getElementById("overviewCards");
const holdingsSummary = document.getElementById("holdingsSummary");
const holdingsTable = document.getElementById("holdingsTable");
const marketTable = document.getElementById("marketTable");
const marketMeta = document.getElementById("marketMeta");
const marketCategoryInput = document.getElementById("marketCategory");
const marketCategoryChips = document.getElementById("marketCategoryChips");
const marketSortSelect = document.getElementById("marketSort");
const marketSortDirSelect = document.getElementById("marketSortDir");
const binanceMarketTabs = Array.from(document.querySelectorAll("[data-binance-tab]"));
const binanceMarketPanes = Array.from(document.querySelectorAll("[data-binance-pane]"));
const binanceMarketForms = Array.from(document.querySelectorAll("[data-binance-form]"));
const binanceMarketViews = {
  crypto_spot: {
    table: document.getElementById("binanceSpotTable"),
    meta: document.getElementById("binanceSpotMeta"),
  },
  crypto_derivatives: {
    table: document.getElementById("binanceDerivativesTable"),
    meta: document.getElementById("binanceDerivativesMeta"),
  },
  rwa_stock_token: {
    table: document.getElementById("binanceStockTokensTable"),
    meta: document.getElementById("binanceStockTokensMeta"),
  },
  equity: {
    table: document.getElementById("binanceEquityTable"),
    meta: document.getElementById("binanceEquityMeta"),
  },
};
const cryptoTable = document.getElementById("cryptoTable");
const financeTable = document.getElementById("financeTable");
const collectorBadge = document.getElementById("collectorBadge");
const cryptoMeta = document.getElementById("cryptoMeta");
const financeMeta = document.getElementById("financeMeta");
const systemStatus = document.getElementById("systemStatus");
const strategyMeta = document.getElementById("strategyMeta");
const strategyTable = document.getElementById("strategyTable");
const dictionaryMeta = document.getElementById("dictionaryMeta");
const dictionarySummary = document.getElementById("dictionarySummary");
const dictionaryProgress = document.getElementById("dictionaryProgress");
const dictionaryLog = document.getElementById("dictionaryLog");
const refreshDictionaryBtn = document.getElementById("refreshDictionaryBtn");
const updateDictionaryBtn = document.getElementById("updateDictionaryBtn");
const agentMeta = document.getElementById("agentMeta");
const agentPendingApprovals = document.getElementById("agentPendingApprovals");
const agentPendingCount = document.getElementById("agentPendingCount");
const agentActivityList = document.getElementById("agentActivityList");
const agentActivityCount = document.getElementById("agentActivityCount");
const agentDraftList = document.getElementById("agentDraftList");
const agentDraftCount = document.getElementById("agentDraftCount");
const refreshAgentBtn = document.getElementById("refreshAgentBtn");
const agentApprovalModal = document.getElementById("agentApprovalModal");
const agentApprovalModalClose = document.getElementById("agentApprovalModalClose");
const agentApprovalCloseBtn = document.getElementById("agentApprovalCloseBtn");
const agentApprovalTitleEl = document.getElementById("agentApprovalTitle");
const agentApprovalSubtitleEl = document.getElementById("agentApprovalSubtitle");
const agentApprovalBody = document.getElementById("agentApprovalBody");
const agentApprovalApproveBtn = document.getElementById("agentApprovalApproveBtn");
const agentApprovalChangeBtn = document.getElementById("agentApprovalChangeBtn");
const agentApprovalRejectBtn = document.getElementById("agentApprovalRejectBtn");
const marketUi = window.PolyMarketUi;
const HOMEPAGE_STRATEGY_LIMIT = 30;

let uiRefreshTimer = null;
let hasLoadedOverview = false;
let hasLoadedCrypto = false;
let hasLoadedFinance = false;
let hasLoadedHoldings = false;
let hasLoadedMarkets = false;
let hasLoadedBinanceMarkets = false;
let hasLoadedStrategies = false;
let hasLoadedDictionary = false;
let hasLoadedAgentDashboard = false;
let currentMarketRows = [];
let marketCategoryOptions = [];
let activeBinanceCategory = "crypto_spot";
const currentBinanceRows = {
  crypto_spot: [],
  crypto_derivatives: [],
  rwa_stock_token: [],
  equity: [],
};
const strategyRowCache = new Map();
const expandedStrategyRows = new Set();
const previousValues = new Map();
let latestOverview = null;
let latestSystemLatency = null;
let lastLatencyFetchAt = 0;
let strategyLiveSource = null;
let dictionaryLiveSource = null;
let activeAgentApproval = null;
const MARKET_SORT_LABELS = {
  volume24h: "24小时热门",
  volume: "总交易量",
  liquidity: "流动性",
  spread: "价差",
  end_date: "到期时间",
  updated_at: "最新更新",
  price_change_24h: "24小时变化",
  last_trade_price: "最近成交价",
};
const MARKET_SORT_ASC_DEFAULTS = new Set(["spread", "end_date"]);

function setStatus(container, text) {
  container.innerHTML = `<div class="status">${escapeHtml(text)}</div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const num = Number(value);
  return Number.isFinite(num) ? num.toLocaleString(undefined, { maximumFractionDigits: digits }) : String(value);
}

function formatFixed(value, digits = 2) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const num = Number(value);
  return Number.isFinite(num)
    ? num.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : String(value);
}

function formatSignedFixed(value, digits = 2) {
  const num = Number(value ?? 0);
  if (!Number.isFinite(num)) {
    return "-";
  }
  const prefix = num > 0 ? "+" : "";
  return `${prefix}${formatFixed(num, digits)}`;
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return String(value);
  }
  const cls = num > 0 ? "positive" : num < 0 ? "negative" : "";
  return `<span class="${cls}">${num.toFixed(2)}%</span>`;
}

function firstPresent(...values) {
  for (const value of values) {
    if (value !== null && value !== undefined && value !== "") {
      return value;
    }
  }
  return null;
}

function parseJsonish(value) {
  if (typeof value !== "string") {
    return value;
  }
  const text = value.trim();
  if (!text || !["[", "{"].includes(text[0])) {
    return value;
  }
  try {
    return JSON.parse(text);
  } catch (_error) {
    return value;
  }
}

function compactParams(value) {
  if (Array.isArray(value)) {
    return value
      .map((item) => compactParams(item))
      .filter((item) => item !== undefined);
  }
  if (!value || typeof value !== "object") {
    return value === "" || value === undefined ? undefined : value;
  }
  const out = {};
  Object.entries(value).forEach(([key, child]) => {
    const compacted = compactParams(child);
    if (compacted === undefined) {
      return;
    }
    if (Array.isArray(compacted) && !compacted.length) {
      return;
    }
    if (
      compacted &&
      typeof compacted === "object" &&
      !Array.isArray(compacted) &&
      !Object.keys(compacted).length
    ) {
      return;
    }
    out[key] = compacted;
  });
  return out;
}

function copyButton(label, attrs = {}) {
  const attrText = Object.entries(attrs)
    .map(([key, value]) => `${key}="${escapeHtml(value)}"`)
    .join(" ");
  return `<button class="mini ghost copy-params-btn" type="button" ${attrText}>${escapeHtml(label)}</button>`;
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

async function copyJsonParams(payload, button) {
  const text = JSON.stringify(compactParams(payload), null, 2);
  await copyTextToClipboard(text);
  if (!button) {
    return;
  }
  const oldText = button.textContent;
  button.textContent = "已复制";
  button.classList.add("copied");
  window.setTimeout(() => {
    button.textContent = oldText || "复制参数";
    button.classList.remove("copied");
  }, 1200);
}

function formatRatioPercent(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return String(value);
  }
  return formatPercent(num * 100);
}

function formatPnL(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return String(value);
  }
  const cls = num > 0 ? "positive" : num < 0 ? "negative" : "";
  return `<span class="${cls}">${formatSignedFixed(num, 2)}</span>`;
}

function formatDateTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString();
}

function formatDateShort(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleDateString();
}

function parseMarketCategories(value) {
  return String(value || "")
    .split(/[/,;]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function uniqueMarketCategories(categories) {
  const seen = new Set();
  return categories.filter((category) => {
    const key = category.toLowerCase();
    if (!category || seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function setMarketCategories(categories) {
  if (!marketCategoryInput) {
    return;
  }
  marketCategoryInput.value = uniqueMarketCategories(categories).join(", ");
  syncMarketCategoryChips();
}

function syncMarketCategoryChips() {
  if (!marketCategoryChips) {
    return;
  }
  const selected = new Set(parseMarketCategories(marketCategoryInput?.value || "").map((item) => item.toLowerCase()));
  marketCategoryChips.querySelectorAll("[data-market-category-chip]").forEach((chip) => {
    const category = String(chip.dataset.marketCategoryChip || "").toLowerCase();
    chip.classList.toggle("active", selected.has(category));
  });
}

function renderMarketCategoryChips(categories) {
  if (!marketCategoryChips) {
    return;
  }
  if (!categories.length) {
    marketCategoryChips.innerHTML = `<span class="category-chip-empty">暂无类别</span>`;
    return;
  }
  marketCategoryChips.innerHTML = categories
    .map((item) => {
      const name = String(item.name || "").trim();
      if (!name) {
        return "";
      }
      const count = Number(item.count || 0);
      return `<button class="category-chip" type="button" data-market-category-chip="${escapeHtml(name)}">${escapeHtml(name)}${count ? ` <span>${formatNumber(count, 0)}</span>` : ""}</button>`;
    })
    .join("");
  syncMarketCategoryChips();
}

async function loadMarketCategories() {
  if (!marketCategoryChips) {
    return;
  }
  marketCategoryChips.innerHTML = `<span class="category-chip-empty">加载类别...</span>`;
  const payload = await fetchJson("/api/polymarket/market-categories?limit=80");
  marketCategoryOptions = payload.data || [];
  renderMarketCategoryChips(marketCategoryOptions);
}

function marketSortLabel(sort) {
  return MARKET_SORT_LABELS[sort] || sort || "默认";
}

function renderMarketMeta(payload, formData) {
  if (!marketMeta) {
    return;
  }
  const categories = parseMarketCategories(formData.get("category"));
  const sort = payload?.sort || formData.get("sort") || "relevance";
  const order = payload?.order || formData.get("order") || "desc";
  const orderLabel = order === "asc" ? "小到大" : "大到小";
  const categoryText = categories.length ? categories.join(" / ") : "全部类别";
  marketMeta.innerHTML = `类别: ${escapeHtml(categoryText)} | 排序: ${escapeHtml(marketSortLabel(sort))} ${escapeHtml(orderLabel)} | 返回: ${escapeHtml(payload?.count ?? 0)} 条`;
}

function formatShortTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    const text = String(value);
    const match = text.match(/(\d{2}:\d{2}:\d{2})/);
    return match ? match[1] : text;
  }
  return date.toLocaleTimeString(undefined, { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatRoi(pnl, bankroll) {
  const capital = Number(bankroll || 0);
  if (!Number.isFinite(capital) || capital <= 0) {
    return "-";
  }
  const value = (Number(pnl || 0) / capital) * 100;
  const cls = value > 0 ? "positive" : value < 0 ? "negative" : "";
  return `<span class="${cls}">${value > 0 ? "+" : ""}${value.toFixed(1)}%</span>`;
}

function resolveStrategyProfit(row) {
  return row?.strategy_pnl ?? row?.profit ?? row?.raw?.profit ?? row?.raw?.Profit ?? 0;
}

function getChangeClass(group, key, value) {
  const fullKey = `${group}:${key}`;
  const nextValue = String(value ?? "");
  const previous = previousValues.get(fullKey);
  previousValues.set(fullKey, nextValue);
  return previous !== undefined && previous !== nextValue ? "value-pop" : "";
}

function animateHtml(group, key, html, compareValue = html) {
  const cls = getChangeClass(group, key, compareValue);
  return `<span class="${cls}">${html}</span>`;
}

function feedDataTime(feed) {
  return feed?.ts_utc || feed?.data?.[0]?.ts_utc || feed?.last_run_at || "";
}

function feedMetaText(feed) {
  const dataTime = feedDataTime(feed) || "-";
  const refreshTime = feed?.last_run_at || "-";
  const countText = `${feed?.count || 0} rows`;
  const baseText = `Refresh: ${refreshTime} | Data: ${dataTime} | ${countText}`;
  if (feed?.stale) {
    const source = feed?.fallback_source || feed?.history_source || "fallback";
    return `${baseText} | STALE ${source}`;
  }
  return baseText;
}

function statusInfo(status, running = false, hasUsableData = false) {
  if (running && (status === "good" || status === "running")) {
    return { tone: "good", label: "稳定运行" };
  }
  if (status === "degraded" || status === "warning") {
    return { tone: "pending", label: "部分可用" };
  }
  if (status === "error") {
    return { tone: "error", label: "运行错误" };
  }
  if (hasUsableData && status !== "error") {
    return { tone: "good", label: "稳定运行" };
  }
  if (status === "good") {
    return { tone: "good", label: "稳定运行" };
  }
  return { tone: "pending", label: "待运行" };
}

function sourceSummary(source, fallbackValue = "-") {
  if (!source) {
    return fallbackValue;
  }
  if (source.error) {
    return source.error;
  }
  if (source.total_strategy_profit !== undefined) {
    return `运行策略 ${source.running_strategy_count || 0} | 盈利 ${formatNumber(source.total_strategy_profit || 0, 2)}`;
  }
  if (source.wallet_count !== undefined) {
    return `钱包 ${source.wallet_count || 0} | 持仓 ${source.count || 0}`;
  }
  if (source.count !== undefined) {
    return `${source.count} 条`;
  }
  return fallbackValue;
}

function formatLatencyMs(value) {
  if (value === null || value === undefined || value === "") {
    return "超时/不可用";
  }
  const num = Number(value);
  return Number.isFinite(num) ? `${num} ms` : "超时/不可用";
}

function latencyGroupValue(latency, groupKey) {
  const group = latency?.groups?.[groupKey];
  if (!group) {
    return "Latency: 检测中";
  }
  const failedText = group.failed ? ` | 失败 ${group.failed}/${group.count}` : "";
  if (group.latency_ms === null || group.latency_ms === undefined) {
    return `Latency: 超时/不可用${failedText}`;
  }
  return `Latency: ${formatLatencyMs(group.latency_ms)}${failedText}`;
}

function latencyKeyValue(latency, key) {
  const items = [...(latency?.sqlite || []), ...(latency?.external || [])];
  const item = items.find((entry) => entry.key === key);
  if (!item) {
    return "Latency: 检测中";
  }
  return `Latency: ${formatLatencyMs(item.latency_ms)}${item.ok ? "" : " | 不可用"}`;
}

function latencyStatus(status, ok) {
  if (status === "warning") {
    return { tone: "pending", label: "偏慢" };
  }
  return statusInfo(status || (ok ? "good" : "error"), false, Boolean(ok));
}

function renderLatencyWindow(latency) {
  if (!latency) {
    return `
      <div class="latency-window status">
        <strong>Latency 检查</strong>
        <div>检测中：latency 是服务器连接数据源花费的时间，单位 ms。</div>
      </div>
    `;
  }
  const rows = [...(latency.external || []), ...(latency.sqlite || [])];
  const body = rows
    .map((item) => {
      const info = latencyStatus(item.status, item.ok);
      const detail = item.error ? ` | ${item.error}` : item.path ? ` | ${item.path}` : item.url ? ` | ${item.url}` : item.host ? ` | ${item.host}` : "";
      return `
        <div class="latency-row">
          <div>
            <strong>${escapeHtml(item.label)}</strong>
            <span>${escapeHtml(detail)}</span>
          </div>
          <span class="state-chip ${info.tone}">${escapeHtml(formatLatencyMs(item.latency_ms))}</span>
        </div>
      `;
    })
    .join("");
  return `
    <div class="latency-window">
      <div class="latency-title">
        <strong>Latency 检查窗口</strong>
        <span>${escapeHtml(latency.explanation || "latency 是服务器连接数据源花费的时间，单位 ms。")}</span>
      </div>
      <div class="latency-list">${body}</div>
    </div>
  `;
}

function renderCards(data) {
  const collector = data.collector || {};
  const totalStrategyProfit = Number(data.total_strategy_profit ?? 0);
  const totalStrategyBankroll = Number(data.total_strategy_bankroll ?? 0);
  const strategyReturnPct = Number.isFinite(totalStrategyBankroll) && totalStrategyBankroll > 0
    ? totalStrategyProfit / totalStrategyBankroll
    : data.total_strategy_return_pct;
  const cards = [
    { label: "活跃市场", value: data.market_count ?? 0, subvalue: `分类 ${data.category_count ?? 0}` },
    { label: "默认钱包", value: (data.wallets || []).length, subvalue: `${data.position_count ?? 0} 条持仓` },
    { label: "运行策略", value: data.running_strategy_count ?? 0, subvalue: "当前有仓位策略数" },
    { label: "总策略盈利", value: formatNumber(data.total_strategy_profit ?? 0, 2), subvalue: strategyReturnPct != null ? `收益率 ${(strategyReturnPct * 100).toFixed(2)}%` : "基于远端仓位+本地订单估算" },
    { label: "Crypto 最新", value: (collector.crypto?.count ?? 0), subvalue: feedMetaText(collector.crypto) || "未启动" },
    { label: "Finance 最新", value: (collector.finance?.count ?? 0), subvalue: feedMetaText(collector.finance) || "未启动" },
  ];
  overviewCards.innerHTML = cards
    .map(
      (card, idx) => `
        <div class="card">
          <div class="label">${escapeHtml(card.label)}</div>
          <div class="value">${animateHtml("card-value", idx, escapeHtml(card.value), card.value)}</div>
          <div class="subvalue">${animateHtml("card-subvalue", idx, escapeHtml(card.subvalue), card.subvalue)}</div>
        </div>
      `
    )
    .join("");
}

function renderTable(container, columns, rows) {
  if (!rows || !rows.length) {
    setStatus(container, "没有数据");
    return;
  }
  const head = columns.map((col) => `<th>${escapeHtml(col.label)}</th>`).join("");
  const body = rows
    .map((row) => {
      const rowKey = String((row && (row.id || row.instrument_id || row.symbol || row.condition_id || row.token_id || row.wallet || row.strategy || row.question)) ?? Math.random());
      const tds = columns
        .map((col) => {
          const rawValue = col.value ? col.value(row) : row[col.key];
          const display = col.render ? col.render(row, rawValue) : escapeHtml(rawValue ?? "-");
          const compareValue = col.compare ? col.compare(row) : rawValue;
          return `<td>${animateHtml(`table-${container.id}`, `${rowKey}-${col.label}`, display ?? "-", compareValue)}</td>`;
        })
        .join("");
      return `<tr>${tds}</tr>`;
    })
    .join("");
  container.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

async function fetchJson(url, options = undefined) {
  const t0 = performance.now();
  console.log(`[FE][start] ${url}`, options || {});
  try {
    const response = await fetch(url, options);
    const data = await response.json();
    const dt = (performance.now() - t0).toFixed(1);
    console.log(`[FE][done] ${url} status=${response.status} cost=${dt}ms`, data);
    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  } catch (error) {
    const dt = (performance.now() - t0).toFixed(1);
    console.error(`[FE][error] ${url} cost=${dt}ms`, error);
    throw error;
  }
}

function agentStateLabel(state) {
  return {
    AI_DRAFTING: "AI 编写中",
    AI_PROPOSED: "AI 已提案",
    WAITING_HUMAN_CONFIRM: "待人工确认",
    HUMAN_REVISION_REQUESTED: "要求修改",
    HUMAN_APPROVED: "已批准",
    HUMAN_REJECTED: "已拒绝",
    RISK_BLOCKED: "风控阻断",
    ACTIVE: "运行中",
    PAUSED: "已暂停",
    CANCELLED: "已取消",
    ARCHIVED: "已归档",
    EXPIRED: "已过期",
  }[state] || state || "-";
}

function agentStateTone(state) {
  if (["HUMAN_APPROVED", "ACTIVE"].includes(state)) return "good";
  if (["WAITING_HUMAN_CONFIRM", "AI_DRAFTING", "AI_PROPOSED", "HUMAN_REVISION_REQUESTED"].includes(state)) return "pending";
  if (["RISK_BLOCKED", "HUMAN_REJECTED", "EXPIRED"].includes(state)) return "error";
  return "neutral";
}

function agentStateChip(state) {
  return `<span class="state-chip ${agentStateTone(state)}">${escapeHtml(agentStateLabel(state))}</span>`;
}

function agentDraftTitle(draft) {
  return draft?.draft?.name || draft?.name || "未命名策略";
}

function agentApprovalTitle(approval) {
  return approval?.snapshot?.snapshot?.name || approval?.draft?.draft?.name || approval?.draft?.name || approval?.approval_id || "-";
}

function agentApprovalBudget(approval) {
  const budget = approval?.snapshot?.snapshot?.budget || approval?.draft?.draft?.budget || {};
  const total = budget.max_total_usdc ?? 0;
  const single = budget.max_single_order_usdc ?? 0;
  return `预算 ${formatFixed(total, 2)} / 单笔 ${formatFixed(single, 2)}`;
}

function agentRiskText(report = {}) {
  if (!report || !Object.keys(report).length) {
    return "未检查";
  }
  const passed = report.passed ? "通过" : "未通过";
  const level = report.risk_level || "-";
  const violations = Array.isArray(report.violations) ? report.violations.length : 0;
  return `${passed} · ${level} · ${violations} 项`;
}

function agentRiskViolationText(report = {}) {
  const violations = Array.isArray(report?.violations) ? report.violations : [];
  if (!violations.length) return "";
  return violations.map((item) => {
    const field = item.field ? `${item.field}: ` : "";
    const current = item.current !== undefined ? `当前 ${agentFormatValue(item.current)}` : "";
    const allowed = item.allowed !== undefined ? `上限 ${agentFormatValue(item.allowed)}` : "";
    const detail = [current, allowed].filter(Boolean).join(" / ");
    return `${field}${item.message || item.code || "风控未通过"}${detail ? `（${detail}）` : ""}`;
  }).join("；");
}

function agentHumanOverride(snapshot = {}) {
  const override = snapshot?.human_override;
  return override && typeof override === "object" && override.allows_risk_approval ? override : null;
}

function agentApprovalBlockedByRisk(report = {}, snapshot = {}) {
  return Boolean(report && Object.keys(report).length && report.passed === false && !agentHumanOverride(snapshot));
}

function agentRiskOverrideText(snapshot = {}) {
  const override = agentHumanOverride(snapshot);
  if (!override) return "";
  const actor = override.actor_id || "human";
  const reason = override.reason || "human edited pending approval parameters";
  return `人工已超控风控：${actor} 手动修改参数（${reason}）`;
}

function agentObjectEntries(value = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value).filter(([, child]) => child !== undefined && child !== null && child !== "");
}

function agentFormatValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return Number.isFinite(value) ? formatNumber(value, 6) : "-";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.length ? value.map((item) => agentFormatValue(item)).join(", ") : "[]";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function renderAgentKvGrid(title, obj = {}, options = {}) {
  const entries = agentObjectEntries(obj);
  if (!entries.length && !options.showEmpty) return "";
  const body = entries.length
    ? entries.map(([key, value]) => `
      <div class="agent-kv">
        <div class="agent-kv-key">${escapeHtml(key)}</div>
        <div class="agent-kv-value">${escapeHtml(agentFormatValue(value))}</div>
      </div>
    `).join("")
    : `<div class="status">暂无数据</div>`;
  return `
    <section class="agent-detail-section">
      <h4>${escapeHtml(title)}</h4>
      <div class="agent-kv-grid">${body}</div>
    </section>
  `;
}

function agentReportFromDraft(source = {}) {
  const report = source?.agent_report && typeof source.agent_report === "object" ? source.agent_report : {};
  const thesis = source?.thesis || "";
  return {
    strategy_reason: report.strategy_reason || thesis || "",
    market_observation: report.market_observation || "",
    parameter_rationale: report.parameter_rationale || "",
    risk_control: report.risk_control || "",
    human_review_focus: report.human_review_focus || "",
  };
}

function agentReportRows(report = {}, options = {}) {
  const rows = [
    ["选择原因", report.strategy_reason],
    ["参数思考", report.parameter_rationale],
    ["风险边界", report.risk_control],
    ["人工确认", report.human_review_focus],
  ];
  const limit = options.limit || rows.length;
  return rows
    .filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== "")
    .slice(0, limit);
}

function renderAgentReportSnippet(report = {}, options = {}) {
  const rows = agentReportRows(report, options);
  if (!rows.length) return "";
  const title = options.title || "Agent 提交说明";
  return `
    <div class="agent-report-snippet">
      <div class="agent-report-title">${escapeHtml(title)}</div>
      ${rows.map(([label, value]) => `
        <div class="agent-report-row">
          <span>${escapeHtml(label)}</span>
          <p>${escapeHtml(String(value))}</p>
        </div>
      `).join("")}
    </div>
  `;
}

function renderAgentMarkets(markets = []) {
  if (!Array.isArray(markets) || !markets.length) {
    return `
      <section class="agent-detail-section">
        <h4>Markets / Legs</h4>
        <div class="status">暂无市场</div>
      </section>
    `;
  }
  const rows = markets.map((market, index) => `
    <tr>
      <td>${index + 1}</td>
      <td>${marketExternalLink(market, market.question || market.title || market.condition_id || "-")}</td>
      <td>${escapeHtml(market.outcome || "YES")}</td>
      <td>${escapeHtml(market.action || "buy")}</td>
      <td class="num">${formatFixed(market.max_entry_price ?? market.best_ask, 4)}</td>
      <td class="num">${formatFixed(market.max_exposure_usdc, 2)}</td>
      <td><span class="mono truncate" title="${escapeHtml(market.condition_id || "")}">${escapeHtml(market.condition_id || "-")}</span></td>
    </tr>
  `).join("");
  return `
    <section class="agent-detail-section">
      <h4>Markets / Legs</h4>
      <div class="agent-detail-table-wrap">
        <table class="agent-detail-table">
          <thead><tr><th>#</th><th>Question</th><th>Outcome</th><th>Action</th><th>Max Entry</th><th>Exposure</th><th>Condition</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderAgentRiskReport(report = {}) {
  const violations = Array.isArray(report.violations) ? report.violations : [];
  const violationRows = violations.length
    ? violations.map((item) => `
      <tr>
        <td>${escapeHtml(item.code || "-")}</td>
        <td>${escapeHtml(item.message || "-")}</td>
        <td>${escapeHtml(item.field || "-")}</td>
        <td>${escapeHtml(agentFormatValue(item.current))}</td>
        <td>${escapeHtml(agentFormatValue(item.allowed))}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="5" class="empty-cell">无风控违规</td></tr>`;
  return `
    <section class="agent-detail-section">
      <h4>Risk Check</h4>
      <div class="agent-detail-summary">
        ${agentStateChip(report.passed ? "HUMAN_APPROVED" : "RISK_BLOCKED")}
        <span>level: ${escapeHtml(report.risk_level || "-")}</span>
        <span>violations: ${escapeHtml(violations.length)}</span>
      </div>
      <div class="agent-detail-table-wrap">
        <table class="agent-detail-table">
          <thead><tr><th>Code</th><th>Message</th><th>Field</th><th>Current</th><th>Allowed</th></tr></thead>
          <tbody>${violationRows}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderAgentSimulation(sim = {}) {
  const scenarios = Array.isArray(sim.scenarios) ? sim.scenarios : [];
  const scenarioRows = scenarios.length
    ? scenarios.map((item) => `
      <tr>
        <td>${escapeHtml(item.name || "-")}</td>
        <td class="num">${formatPnL(item.estimated_pnl_usdc)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="2" class="empty-cell">暂无模拟场景</td></tr>`;
  return `
    <section class="agent-detail-section">
      <h4>Simulation</h4>
      <div class="agent-detail-summary">
        <span>max loss: ${formatFixed(sim.max_loss_usdc, 2)} USDC</span>
        <span>max exposure: ${formatFixed(sim.max_exposure_usdc, 2)} USDC</span>
        <span>orders: ${escapeHtml(sim.estimated_orders ?? "-")}</span>
      </div>
      <div class="agent-detail-table-wrap">
        <table class="agent-detail-table">
          <thead><tr><th>Scenario</th><th>Estimated PnL</th></tr></thead>
          <tbody>${scenarioRows}</tbody>
        </table>
      </div>
    </section>
  `;
}

function openAgentApprovalModal(approval) {
  if (!agentApprovalModal || !agentApprovalBody) return;
  activeAgentApproval = approval;
  const snapshot = approval?.snapshot?.snapshot || approval?.draft?.draft || {};
  const draft = approval?.draft || {};
  const title = agentApprovalTitle(approval);
  const params = snapshot.params || {};
  const budget = snapshot.budget || {};
  const executionRules = snapshot.execution_rules || {};
  const exitRules = snapshot.exit_rules || {};
  const markets = snapshot.markets || [];
  const risk = approval.risk_report || approval?.snapshot?.risk || draft.last_risk_report || {};
  const simulation = draft.last_simulation || {};
  const agentReport = agentReportFromDraft(snapshot);
  if (agentApprovalTitleEl) agentApprovalTitleEl.textContent = title;
  if (agentApprovalSubtitleEl) {
    agentApprovalSubtitleEl.textContent = `审批单 ${approval.approval_id || "-"} · ${agentStateLabel(approval.status)} · 策略代码 ${snapshot.strategy_code || "-"}`;
  }
  agentApprovalBody.innerHTML = `
    <section class="agent-detail-section">
      <h4>Strategy Summary</h4>
      <div class="agent-detail-summary">
        ${agentStateChip(approval.status)}
        <span>strategy_code: ${escapeHtml(snapshot.strategy_code || "-")}</span>
        <span>version: ${escapeHtml(approval.draft_version || draft.current_version || "-")}</span>
        <span>expires: ${formatShortTime(approval.expires_at)}</span>
      </div>
      <div class="agent-thesis">${escapeHtml(snapshot.thesis || "-")}</div>
    </section>
    <section class="agent-detail-section">
      <h4>Agent 提交说明</h4>
      ${renderAgentReportSnippet(agentReport, { title: "提交模板" }) || '<div class="status">暂无提交说明</div>'}
    </section>
    ${renderAgentMarkets(markets)}
    ${renderAgentKvGrid("Params / Strategy Inputs", params, { showEmpty: true })}
    ${renderAgentKvGrid("Budget", budget, { showEmpty: true })}
    ${renderAgentKvGrid("Execution Rules", executionRules, { showEmpty: true })}
    ${renderAgentKvGrid("Exit Rules", exitRules, { showEmpty: true })}
    ${renderAgentRiskReport(risk)}
    ${renderAgentSimulation(simulation)}
    ${renderAgentKvGrid("Risk Notes", { notes: snapshot.risk_notes || [] })}
  `;
  const isPending = approval.status === "WAITING_HUMAN_CONFIRM";
  if (agentApprovalApproveBtn) agentApprovalApproveBtn.disabled = !isPending;
  if (agentApprovalChangeBtn) agentApprovalChangeBtn.disabled = !isPending;
  if (agentApprovalRejectBtn) agentApprovalRejectBtn.disabled = !isPending;
  agentApprovalModal.hidden = false;
}

function closeAgentApprovalModal() {
  if (agentApprovalModal) agentApprovalModal.hidden = true;
  activeAgentApproval = null;
}

function renderAgentPendingApprovals(rows = []) {
  if (!agentPendingApprovals) return;
  if (agentPendingCount) agentPendingCount.textContent = String(rows.length);
  if (!rows.length) {
    setStatus(agentPendingApprovals, "暂无待人工确认策略");
    return;
  }
  agentPendingApprovals.innerHTML = rows.map((approval) => {
    const title = agentApprovalTitle(approval);
    const report = approval.risk_report || approval.snapshot?.risk || {};
    const snapshot = approval.snapshot?.snapshot || approval.draft?.draft || {};
    const thesis = snapshot.thesis || "";
    const agentReport = agentReportFromDraft(snapshot);
    const reportHtml = renderAgentReportSnippet(agentReport, { limit: 3 });
    const riskViolation = agentRiskViolationText(report);
    const riskOverride = agentRiskOverrideText(snapshot);
    const approvalBlocked = agentApprovalBlockedByRisk(report, snapshot);
    return `
      <div class="agent-item">
        <div class="agent-item-main">
          <div class="agent-item-title">${escapeHtml(title)}</div>
          <div class="agent-item-meta">
            ${agentStateChip(approval.status)}
            <span>${escapeHtml(agentApprovalBudget(approval))}</span>
            <span>风控 ${escapeHtml(agentRiskText(report))}</span>
            <span>提交 ${formatShortTime(approval.created_at)}</span>
          </div>
          ${riskViolation ? `<div class="agent-risk-warning">${escapeHtml(riskViolation)}</div>` : ""}
          ${riskOverride ? `<div class="agent-risk-override">${escapeHtml(riskOverride)}</div>` : ""}
          ${reportHtml || (thesis ? `<div class="agent-item-note">${escapeHtml(thesis)}</div>` : "")}
        </div>
        <div class="agent-actions">
          <button class="mini ghost" type="button" data-agent-view-approval="${escapeHtml(approval.approval_id)}">参数</button>
          <button class="mini" type="button" data-agent-approve="${escapeHtml(approval.approval_id)}" ${approvalBlocked ? `disabled title="${escapeHtml(riskViolation || "风控未通过，不能批准")}"` : ""}>批准</button>
          <button class="mini ghost" type="button" data-agent-change="${escapeHtml(approval.approval_id)}">要求修改</button>
          <button class="mini danger" type="button" data-agent-reject="${escapeHtml(approval.approval_id)}">拒绝</button>
        </div>
      </div>
    `;
  }).join("");
}

function renderAgentActivity(rows = []) {
  if (!agentActivityList) return;
  if (agentActivityCount) agentActivityCount.textContent = String(rows.length);
  if (!rows.length) {
    setStatus(agentActivityList, "暂无 Agent 活动");
    return;
  }
  agentActivityList.innerHTML = rows.map((event) => `
    <div class="agent-activity-row">
      <span>${formatShortTime(event.created_at)}</span>
      ${agentStateChip(event.state)}
      <strong>${escapeHtml(event.agent_id || "agent")}</strong>
      <span class="truncate">${escapeHtml(event.message || "-")}</span>
    </div>
  `).join("");
}

function renderAgentDrafts(rows = []) {
  if (!agentDraftList) return;
  if (agentDraftCount) agentDraftCount.textContent = String(rows.length);
  if (!rows.length) {
    setStatus(agentDraftList, "暂无策略草案");
    return;
  }
  agentDraftList.innerHTML = rows.map((draft) => {
    const d = draft.draft || {};
    const markets = Array.isArray(d.markets) ? d.markets : [];
    const budget = d.budget || {};
    const canDelete = !["WAITING_HUMAN_CONFIRM", "HUMAN_APPROVED"].includes(draft.lifecycle_state);
    const canSubmit = !["WAITING_HUMAN_CONFIRM", "HUMAN_APPROVED", "CANCELLED", "ARCHIVED"].includes(draft.lifecycle_state);
    const reportHtml = renderAgentReportSnippet(agentReportFromDraft(d), { limit: 2 });
    return `
      <div class="agent-item agent-draft-item">
        <div class="agent-item-main">
          <div class="agent-item-title">${escapeHtml(agentDraftTitle(draft))}</div>
          <div class="agent-item-meta">
            ${agentStateChip(draft.lifecycle_state)}
            <span>版本 ${escapeHtml(draft.current_version || 1)}</span>
            <span>${escapeHtml(markets.length)} markets</span>
            <span>预算 ${formatFixed(budget.max_total_usdc, 2)}</span>
            <span>风控 ${escapeHtml(agentRiskText(draft.last_risk_report))}</span>
            <span>更新 ${formatShortTime(draft.updated_at)}</span>
          </div>
          ${reportHtml || (d.thesis ? `<div class="agent-item-note">${escapeHtml(d.thesis)}</div>` : "")}
        </div>
        <div class="agent-actions">
          <button class="mini ghost" type="button" data-agent-view-draft="${escapeHtml(draft.draft_id)}">查看</button>
          <button class="mini ghost" type="button" data-agent-risk="${escapeHtml(draft.draft_id)}">风控</button>
          <button class="mini ghost" type="button" data-agent-simulate="${escapeHtml(draft.draft_id)}">模拟</button>
          ${canSubmit ? `<button class="mini" type="button" data-agent-submit="${escapeHtml(draft.draft_id)}">提交确认</button>` : ""}
          ${canDelete ? `<button class="mini danger" type="button" data-agent-delete-draft="${escapeHtml(draft.draft_id)}">删除</button>` : ""}
        </div>
      </div>
    `;
  }).join("");
}

function renderAgentDashboard(data = {}) {
  const pending = data.pending_approvals || [];
  const activity = data.activity || [];
  const drafts = data.drafts || [];
  if (agentMeta) {
    const limits = data.policy?.limits || {};
    agentMeta.innerHTML = `待确认: ${escapeHtml(pending.length)} | 草案: ${escapeHtml(drafts.length)} | 单策略上限: ${formatFixed(limits.max_strategy_budget_usdc, 2)} USDC | 单笔上限: ${formatFixed(limits.max_single_order_usdc, 2)} USDC`;
  }
  renderAgentPendingApprovals(pending);
  renderAgentActivity(activity);
  renderAgentDrafts(drafts);
}

async function loadAgentDashboard(options = {}) {
  const { silent = false } = options;
  if (!agentPendingApprovals || !agentActivityList || !agentDraftList) {
    return;
  }
  if (!silent && !hasLoadedAgentDashboard) {
    setStatus(agentPendingApprovals, "加载中...");
    setStatus(agentActivityList, "加载中...");
    setStatus(agentDraftList, "加载中...");
  }
  const payload = await fetchJson("/api/agent/dashboard?limit=20");
  renderAgentDashboard(payload.data || {});
  hasLoadedAgentDashboard = true;
}

async function postAgentAction(url, body = {}) {
  return fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function refreshSystemLatency(options = {}) {
  const { force = false } = options;
  const now = Date.now();
  if (!force && now - lastLatencyFetchAt < 30000) {
    return latestSystemLatency;
  }
  lastLatencyFetchAt = now;
  const payload = await fetchJson("/api/system/latency");
  latestSystemLatency = payload?.data || null;
  if (latestOverview) {
    renderSystemStatus(latestOverview, latestSystemLatency);
  }
  return latestSystemLatency;
}

function renderSystemStatus(overview, latency = latestSystemLatency) {
  const state = overview.collector || {};
  const settings = overview.settings || {};
  const sources = overview.sources || {};
  const items = [
    { label: "Collector", value: state.running ? "运行中" : "未运行", status: state.running ? "good" : "pending", ready: state.running },
    { label: "行情 SQLite", value: state.db_path || settings.sqlite_db_path || "-", status: "good" },
    {
      label: "实时市场 SQLite",
      value: settings.market_realtime_db_path || "-",
      status: "good",
    },
    {
      label: "Polymarket 市场",
      value: sourceSummary(sources.markets_api, "等待检测"),
      status: sources.markets_api?.status || "pending",
      ready: Boolean((sources.markets_api?.count || 0) > 0 || sources.markets_api?.history_loaded),
    },
    {
      label: "Polymarket 持仓",
      value: sourceSummary(sources.holdings_api, "等待检测"),
      status: sources.holdings_api?.status || "pending",
      ready: Boolean((sources.holdings_api?.count || 0) > 0 || sources.holdings_api?.history_loaded),
    },
    {
      label: "策略利润计算",
      value: sourceSummary(sources.strategy_profit, "等待检测"),
      status: sources.strategy_profit?.status || "pending",
      ready: Boolean(
        sources.strategy_profit?.history_loaded
        || (sources.strategy_profit?.running_strategy_count || 0) > 0
        || sources.strategy_profit?.total_strategy_profit !== undefined
      ),
    },
    {
      label: "Crypto 刷新",
      value: `Interval: ${settings.crypto_refresh_sec || "-"}s | ${feedMetaText(state.crypto)}`,
      status: state.crypto?.status || "pending",
      ready: Boolean((state.crypto?.count || state.crypto?.data?.length || 0) > 0 && !state.crypto?.stale && !(state.crypto?.errors || []).length),
    },
    {
      label: "Finance 刷新",
      value: `Interval: ${settings.finance_refresh_sec || "-"}s | ${feedMetaText(state.finance)}`,
      status: state.finance?.status || "pending",
      ready: Boolean((state.finance?.count || state.finance?.data?.length || 0) > 0 && !state.finance?.stale && !(state.finance?.errors || []).length),
    },
    { label: "前端刷新", value: `${settings.ui_refresh_sec || "-"} 秒`, status: "pending" },
  ];
  let sqliteIndex = 0;
  const sqliteKeys = ["market_data_db", "market_realtime_db"];
  items.forEach((item) => {
    if (String(item.label).includes("SQLite")) {
      item.value = `${item.value} | ${latencyKeyValue(latency, sqliteKeys[sqliteIndex] || "market_data_db")}`;
      item.status = latency?.groups?.sqlite?.status || item.status;
      sqliteIndex += 1;
    } else if (String(item.label).includes("Polymarket")) {
      item.value = `${item.value} | ${latencyGroupValue(latency, "polymarket")}`;
      item.status = item.ready ? item.status : (latency?.groups?.polymarket?.status || item.status);
    } else if (String(item.label).includes("Crypto")) {
      item.value = `${item.value} | ${latencyGroupValue(latency, "crypto")}`;
      item.status = item.ready ? item.status : (latency?.groups?.crypto?.status || item.status);
    } else if (String(item.label).includes("Finance")) {
      item.value = `${item.value} | ${latencyGroupValue(latency, "finance")}`;
      item.status = item.ready ? item.status : (latency?.groups?.finance?.status || item.status);
    }
  });
  systemStatus.innerHTML = items
    .map((item, idx) => {
      const info = statusInfo(item.status, item.label === "Collector" && state.running, Boolean(item.ready));
      return `
        <div class="info-item">
          <div class="info-item-header">
            <strong>${escapeHtml(item.label)}</strong>
            <span class="state-chip ${info.tone}">${info.label}</span>
          </div>
          <div>${animateHtml("system-status", idx, escapeHtml(item.value), item.value)}</div>
        </div>
      `;
    })
    .join("") + renderLatencyWindow(latency);
}

function dictionaryTone(state) {
  if (state?.running) {
    return { tone: "pending", label: "更新中" };
  }
  if (state?.last_error) {
    return { tone: "error", label: "失败" };
  }
  if ((state?.count || 0) > 0) {
    return { tone: "good", label: "已就绪" };
  }
  return { tone: "pending", label: "待初始化" };
}

function renderDictionaryStatus(state) {
  const info = dictionaryTone(state);
  const stats = state?.stats || {};
  const logLines = state?.logs || [];
  const progressText = state?.running
    ? `阶段: ${stats.mode || state.phase || "-"} | 页数: ${stats.pages_processed || 0} | 拉取: ${stats.markets_fetched || 0} | 新增: ${stats.inserted || 0} | 已存在: ${stats.skipped_existing || 0} | 过期跳过: ${stats.skipped_expired || 0}`
    : state?.last_error
      ? `更新失败: ${state.last_error}`
      : (state?.last_summary || "等待手动触发更新");

  dictionaryMeta.innerHTML = `表: ${animateHtml("dictionary-meta", "table", escapeHtml(state?.table_name || "-"), state?.table_name || "-")} | 状态: <span class="state-chip ${info.tone}">${info.label}</span> | 文件更新时间: ${animateHtml("dictionary-meta", "mtime", escapeHtml(formatDateTime(state?.file_updated_at)), state?.file_updated_at || "-")}`;

  const items = [
    { label: "字典数量", value: state?.count ?? 0 },
    { label: "数据库路径", value: state?.db_path || "-" },
    { label: "开始时间", value: formatDateTime(state?.started_at) },
    { label: "结束时间", value: formatDateTime(state?.finished_at) },
    { label: "过期跳过", value: stats.skipped_expired ?? 0 },
    { label: "Tags 命中", value: stats.tags_hit ?? 0 },
    { label: "Tags 未命中", value: stats.tags_miss ?? 0 },
  ];
  dictionarySummary.innerHTML = items
    .map(
      (item, idx) => `
        <div class="info-item">
          <div class="info-item-header">
            <strong>${escapeHtml(item.label)}</strong>
          </div>
          <div>${animateHtml("dictionary-summary", idx, escapeHtml(item.value), item.value)}</div>
        </div>
      `
    )
    .join("");
  dictionaryProgress.textContent = progressText;
  dictionaryLog.textContent = logLines.length ? logLines.join("\n") : "暂无更新日志。";
  updateDictionaryBtn.disabled = Boolean(state?.running);
  updateDictionaryBtn.textContent = state?.running ? "更新中..." : "更新字典";
  hasLoadedDictionary = true;
}

async function loadOverview(options = {}) {
  const { silent = false } = options;
  const t0 = performance.now();
  if (!silent && !hasLoadedOverview) {
    setStatus(overviewCards, "加载中...");
  }
  const data = await fetchJson("/api/overview");
  const t1 = performance.now();
  latestOverview = data;
  renderCards(data);
  renderSystemStatus(data, latestSystemLatency);
  refreshSystemLatency({ force: !latestSystemLatency }).catch((error) => console.error("[FE][latency]", error));
  const badgeState = data.collector?.running ? statusInfo("good", true) : statusInfo("pending");
  collectorBadge.textContent = badgeState.label;
  collectorBadge.className = `badge ${badgeState.tone}`;
  hasLoadedOverview = true;
  const t2 = performance.now();
  console.log(
    `[FE] loadOverview fetch=${(t1 - t0).toFixed(1)}ms render=${(t2 - t1).toFixed(1)}ms total=${(t2 - t0).toFixed(1)}ms`
  );
  return data;
}

async function loadRealtimeCrypto(options = {}) {
  const { silent = false } = options;
  const t0 = performance.now();
  if (!silent && !hasLoadedCrypto) {
    setStatus(cryptoTable, "加载中...");
  }
  const payload = await fetchJson("/api/realtime/crypto");
  const t1 = performance.now();
  const data = payload.data || {};
  const cryptoMetaText = feedMetaText(data);
  cryptoMeta.innerHTML = animateHtml("crypto-meta", "text", escapeHtml(cryptoMetaText), cryptoMetaText);
  renderTable(
    cryptoTable,
    [
      { key: "symbol", label: "Symbol", render: (row) => escapeHtml(row.symbol) },
      { key: "price", label: "Price", render: (row) => formatNumber(row.price, 4) },
      { key: "market_cap_usd", label: "MCap", render: (row) => formatNumber(row.market_cap_usd, 0) },
      { key: "fdv_usd", label: "FDV", render: (row) => formatNumber(row.fdv_usd, 0) },
      { key: "vol_24h_quote", label: "24h Quote Vol", render: (row) => formatNumber(row.vol_24h_quote, 0) },
      { key: "base_asset", label: "Base", render: (row) => escapeHtml(row.base_asset || "-") },
    ],
    data.data || []
  );
  hasLoadedCrypto = true;
  const t2 = performance.now();
  console.log(
    `[FE] loadRealtimeCrypto fetch=${(t1 - t0).toFixed(1)}ms render=${(t2 - t1).toFixed(1)}ms total=${(t2 - t0).toFixed(1)}ms rows=${(data.data || []).length}`
  );
}

async function loadRealtimeFinance(options = {}) {
  const { silent = false } = options;
  const t0 = performance.now();
  if (!silent && !hasLoadedFinance) {
    setStatus(financeTable, "加载中...");
  }
  const payload = await fetchJson("/api/realtime/finance");
  const t1 = performance.now();
  const data = payload.data || {};
  const financeMetaText = feedMetaText(data);
  financeMeta.innerHTML = animateHtml("finance-meta", "text", escapeHtml(financeMetaText), financeMetaText);
  if (data.ok === false && (!data.data || !data.data.length)) {
    if (!silent || !hasLoadedFinance) {
      setStatus(financeTable, (data.errors || []).join(" | ") || "暂无数据");
    }
    const t2 = performance.now();
    console.log(
      `[FE] loadRealtimeFinance fetch=${(t1 - t0).toFixed(1)}ms render=${(t2 - t1).toFixed(1)}ms total=${(t2 - t0).toFixed(1)}ms rows=0`
    );
    return;
  }
  renderTable(
    financeTable,
    [
      { key: "symbol", label: "Symbol", render: (row) => escapeHtml(row.symbol) },
      { key: "company_name", label: "Name", render: (row) => escapeHtml(row.company_name || "-") },
      { key: "price", label: "Price", render: (row) => formatNumber(row.price, 2) },
      { key: "change_percent", label: "Change %", render: (row) => formatPercent(row.change_percent) },
      { key: "market_cap_usd", label: "MCap", render: (row) => formatNumber(row.market_cap_usd, 0) },
      { key: "exchange", label: "Exchange", render: (row) => escapeHtml(row.exchange || "-") },
    ],
    data.data || []
  );
  hasLoadedFinance = true;
  const t2 = performance.now();
  console.log(
    `[FE] loadRealtimeFinance fetch=${(t1 - t0).toFixed(1)}ms render=${(t2 - t1).toFixed(1)}ms total=${(t2 - t0).toFixed(1)}ms rows=${(data.data || []).length}`
  );
}

async function loadHoldings(wallet = "", options = {}) {
  const { silent = false } = options;
  if (!silent && !hasLoadedHoldings) {
    setStatus(holdingsTable, "加载中...");
  }
  const qs = new URLSearchParams();
  if (wallet) {
    qs.set("wallet", wallet);
  }
  const data = await fetchJson(`/api/polymarket/holdings?${qs.toString()}`);
  const walletText = (data.wallets || []).join(", ") || "未配置";
  holdingsSummary.innerHTML = `钱包: ${animateHtml("holdings-summary", "wallets", escapeHtml(walletText), walletText)} | 持仓条数: ${animateHtml("holdings-summary", "count", escapeHtml(data.count || 0), data.count || 0)}`;
  renderTable(
    holdingsTable,
    [
      { key: "wallet", label: "Wallet", render: (row) => escapeHtml(row.wallet) },
      { key: "title", label: "Title", render: (row) => marketExternalLink({
        question: row.title || row.question || row.market,
        slug: row.slug,
        event_slug: row.event_slug,
        group_item_title: row.group_item_title,
        url: row.url,
        condition_id: row.condition_id,
        yes_token: row.yes_token,
        no_token: row.no_token,
        token_id: row.token_id,
      }, row.title || row.question || row.market || "-") },
      { key: "outcome", label: "Outcome", render: (row) => escapeHtml(row.outcome || "-") },
      { key: "size", label: "Size", render: (row) => formatNumber(row.size, 4) },
      { key: "avg_price", label: "Avg Price", render: (row) => formatNumber(row.avg_price, 4) },
      { key: "cash_pnl", label: "Cash PnL", render: (row) => formatNumber(row.cash_pnl, 2) },
    ],
    data.positions || []
  );
  hasLoadedHoldings = true;
}

async function loadMarkets(formData, options = {}) {
  const { silent = false } = options;
  if (!silent && !hasLoadedMarkets) {
    setStatus(marketTable, "加载中...");
  }
  const qs = new URLSearchParams(formData);
  if (!qs.get("refresh")) {
    qs.delete("refresh");
  } else {
    qs.set("refresh", "1");
  }
  const data = await fetchJson(`/api/polymarket/markets?${qs.toString()}`);
  currentMarketRows = data.data || [];
  renderMarketMeta(data, formData);
  renderMarketSearchTable(currentMarketRows);
  hasLoadedMarkets = true;
}

function marketQuestionLink(row) {
  const label = row.question || row.slug || row.condition_id || "-";
  return marketExternalLink(row, label);
}

function watchlistButtonLabel(row) {
  return marketUi.isInWatchlist(row) ? "取消自选" : "加入自选";
}

function internalMarketLink(row) {
  const params = new URLSearchParams();
  if (row.condition_id) {
    params.set("condition_id", row.condition_id);
  }
  if (row.yes_token) {
    params.set("yes_token", row.yes_token);
  }
  if (row.no_token) {
    params.set("no_token", row.no_token);
  }
  if (row.question) {
    params.set("question", row.question);
  }
  if (row.slug) {
    params.set("slug", row.slug);
  }
  if (row.event_slug) {
    params.set("event_slug", row.event_slug);
  }
  if (row.group_item_title) {
    params.set("group_item_title", row.group_item_title);
  }
  if (row.url) {
    params.set("url", row.url);
  }
  if (row.category) {
    params.set("category", row.category);
  }
  return `/watchlist?${params.toString()}`;
}

function marketOutcomes(row) {
  const raw = row.raw || {};
  const outcomes = parseJsonish(firstPresent(row.outcomes, raw.outcomes));
  if (Array.isArray(outcomes) && outcomes.length) {
    return outcomes.map((item) => {
      if (item && typeof item === "object") {
        return String(firstPresent(item.name, item.title, item.label, item.outcome, "") || "");
      }
      return String(item || "");
    }).filter(Boolean);
  }
  return ["Yes", "No"];
}

function buildPolymarketStrategyParams(row) {
  const raw = row.raw || {};
  const rawRules = firstPresent(
    row.rules,
    raw.rules,
    raw.description,
    raw.resolutionCriteria,
    raw.resolution_criteria,
    raw.resolutionDetails,
    raw.longDescription
  );
  const resolutionSource = firstPresent(
    row.resolution_source,
    raw.resolutionSource,
    raw.resolution_source,
    raw.resolutionSourceName,
    raw.oracle,
    raw.source
  );
  return {
    source: "polymarket",
    type: "prediction_market_binary",
    question: firstPresent(row.question, raw.question, raw.title),
    slug: firstPresent(row.slug, raw.slug),
    event_slug: firstPresent(row.event_slug, raw.eventSlug, raw.event_slug),
    url: marketUi.buildPolymarketUrl(row),
    category: firstPresent(row.category, raw.category),
    rules: {
      raw: rawRules,
      rules_available: Boolean(rawRules),
      note: rawRules ? undefined : "当前搜索结果未返回 Polymarket rules/description，需要打开市场详情补全。",
      resolution_source: resolutionSource,
      end_date: firstPresent(row.end_date, raw.endDate, raw.umaEndDate),
      resolution_timezone: firstPresent(raw.resolutionTimezone, raw.timezone, "UTC"),
      outcomes: marketOutcomes(row),
    },
    market: {
      yes_price: row.yes_price,
      no_price: row.no_price,
      last_trade_price: row.last_trade_price,
      best_bid: firstPresent(row.best_bid, raw.bestBid),
      best_ask: firstPresent(row.best_ask, raw.bestAsk),
      spread: row.spread,
      liquidity: row.liquidity,
      volume: row.volume,
      active: row.active,
      closed: row.closed,
      resolved: firstPresent(row.resolved, raw.resolved),
      accepting_orders: firstPresent(row.accepting_orders, raw.acceptingOrders, row.active && !row.closed),
    },
    identifiers: {
      condition_id: firstPresent(row.condition_id, raw.conditionId, raw.condition_id),
      market_id: firstPresent(row.market_id, raw.id, raw.marketId),
      clob_token_ids: {
        yes: row.yes_token,
        no: row.no_token,
      },
    },
    snapshot: {
      copied_at: new Date().toISOString(),
      price_source: "polymarket_market_search",
    },
  };
}

function renderMarketSearchTable(rows) {
  renderTable(
    marketTable,
    [
      { key: "question", label: "Question", render: (row) => marketQuestionLink(row) },
      { key: "category", label: "Category", render: (row) => escapeHtml(row.category || "-") },
      { key: "volume_24h", label: "24h Vol", render: (row) => formatNumber(firstPresent(row.volume_24h, row.raw?.volume24hr, row.raw?.volume24hrClob), 0) },
      { key: "volume", label: "Volume", render: (row) => formatNumber(firstPresent(row.volume, row.raw?.volumeNum, row.raw?.volume), 0) },
      { key: "liquidity", label: "Liquidity", render: (row) => formatNumber(firstPresent(row.liquidity, row.raw?.liquidityNum, row.raw?.liquidity), 0) },
      { key: "yes_price", label: "Yes", render: (row) => formatNumber(row.yes_price, 4) },
      { key: "no_price", label: "No", render: (row) => formatNumber(row.no_price, 4) },
      { key: "yes_bid", label: "Yes Bid", render: (row) => formatNumber(firstPresent(row.yes_bid, row.best_bid, row.raw?.bestBid, row.raw?.bid), 4) },
      { key: "yes_ask", label: "Yes Ask", render: (row) => formatNumber(firstPresent(row.yes_ask, row.best_ask, row.raw?.bestAsk, row.raw?.ask), 4) },
      { key: "no_bid", label: "No Bid", render: (row) => formatNumber(firstPresent(row.no_bid, row.raw?.noBid, row.raw?.opp_bid_price, row.raw?.opp_bids_price), 4) },
      { key: "no_ask", label: "No Ask", render: (row) => formatNumber(firstPresent(row.no_ask, row.raw?.noAsk, row.raw?.opp_ask_price), 4) },
      { key: "spread", label: "Spread", render: (row) => formatNumber(row.spread, 4) },
      { key: "last_trade_price", label: "Last Trade", render: (row) => formatNumber(row.last_trade_price, 4) },
      { key: "end_date", label: "Ends", render: (row) => escapeHtml(formatDateShort(firstPresent(row.end_date, row.raw?.endDate, row.raw?.umaEndDate))) },
      { key: "slug", label: "Slug", render: (row) => escapeHtml(row.slug || "-") },
      {
        key: "actions",
        label: "操作",
        render: (row) => `
          <div class="table-actions">
            <a class="table-link-button" href="${escapeHtml(marketUi.buildPolymarketUrl(row))}" target="_blank" rel="noopener noreferrer">打开</a>
            <a class="table-link-button" href="${escapeHtml(internalMarketLink(row))}" target="_blank" rel="noopener noreferrer">系统内打开</a>
            ${copyButton("复制参数", { "data-copy-market": marketUi.marketIdentityKey(row) })}
            <button class="mini ghost" type="button" data-watch-market="${escapeHtml(marketUi.marketIdentityKey(row))}">${watchlistButtonLabel(row)}</button>
          </div>
        `,
      },
    ],
    rows
  );
}

function binanceView(category) {
  return binanceMarketViews[category] || binanceMarketViews.crypto_spot;
}

function binanceForm(category) {
  return binanceMarketForms.find((form) => form.dataset.binanceForm === category);
}

function capabilityTags(row) {
  const caps = row.capabilities || {};
  const tags = [];
  if (caps.spot) tags.push("Spot");
  if (caps.margin) tags.push("Margin");
  if (caps.derivatives) tags.push(row.subtype_label || "Derivatives");
  if (caps.tokenized_stock) tags.push("Tokenized");
  if (caps.equity) tags.push("Equity");
  if (!tags.length && row.market_kind) tags.push(row.market_kind);
  return `<div class="capability-tags">${tags.map((tag) => `<span class="capability-tag">${escapeHtml(tag)}</span>`).join("")}</div>`;
}

function statusChip(value) {
  const text = value || "-";
  const normalized = String(text).toUpperCase();
  const tone = ["TRADING", "ACTIVE", "QUOTE_READY", "OK"].includes(normalized)
    ? "good"
    : ["CONFIGURED", "LOOKUP_ONLY", "PENDING_TRADING", "DEGRADED"].includes(normalized)
      ? "pending"
      : normalized.includes("ERROR")
        ? "error"
        : "pending";
  return `<span class="state-chip ${tone}">${escapeHtml(text)}</span>`;
}

function sourceMetaLine(payload) {
  const meta = payload.meta || {};
  const status = meta.source_status || "ok";
  const errors = meta.errors || [];
  const sourceText = meta.source || meta.sources?.map((item) => item.subtype).join(", ") || "-";
  const total = meta.total_source_symbols ?? meta.total_source_rows ?? "";
  const totalText = total === "" ? "" : ` | 源条数: ${escapeHtml(total)}`;
  const cacheText = meta.cache_status ? ` | 缓存: ${escapeHtml(meta.cache_status)}` : "";
  const fallbackText = meta.fallback ? ` | fallback: ${escapeHtml(meta.fallback_reason || "local")}` : "";
  const errorText = errors.length ? ` | 诊断: ${escapeHtml(errors.slice(0, 2).join(" ; "))}` : "";
  return `状态: ${statusChip(status === "ok" ? "OK" : "DEGRADED")} | 来源: ${escapeHtml(sourceText)} | 返回: ${escapeHtml(payload.count ?? 0)}${totalText}${cacheText}${fallbackText}${errorText}`;
}

function shortContract(value) {
  const text = String(value || "");
  if (!text) return "-";
  if (text.length <= 22) return escapeHtml(text);
  return `<span class="contract-code" title="${escapeHtml(text)}">${escapeHtml(text.slice(0, 10))}...${escapeHtml(text.slice(-8))}</span>`;
}

function instrumentCode(row) {
  return `<span class="instrument-code">${escapeHtml(row.instrument_id || "-")}</span>`;
}

function binanceCopyAction(category, row) {
  const rowKey = row.instrument_id || row.symbol || row.ticker || row.contract_address || "";
  return copyButton("复制参数", {
    "data-copy-binance-category": category,
    "data-copy-binance-row": rowKey,
  });
}

function buildBinanceStrategyParams(row, category) {
  const base = {
    source: "binance",
    type: row.asset_class || category,
    leg_kind: row.leg_kind || row.kind || "",
    instrument: row.instrument_id,
    symbol: row.symbol,
    display_name: row.display_name,
    status: row.status,
    snapshot: {
      copied_at: new Date().toISOString(),
      price_source: row.source || "binance_market_search",
      data_quality: row.data_quality,
      note: row.fallback_reason,
    },
  };

  if (category === "crypto_spot") {
    return {
      ...base,
      type: "crypto_spot",
      base_asset: row.base_asset,
      quote_asset: row.quote_asset,
      market: {
        last_price: row.price,
        price_change_24h_pct: row.change_percent_24h,
        quote_volume_24h: row.volume_24h_quote,
        base_volume_24h: row.volume_24h_base,
        bid_price: row.bid_price,
        ask_price: row.ask_price,
        spread: row.spread,
      },
      trading_rules: {
        ...(row.trading_rules || {}),
        price_precision: row.price_precision,
        quantity_precision: row.quantity_precision,
        base_asset_precision: row.base_asset_precision,
        quote_asset_precision: row.quote_asset_precision,
        margin_enabled: row.is_margin_trading_allowed,
      },
      capabilities: row.capabilities,
    };
  }

  if (category === "crypto_derivatives") {
    return {
      ...base,
      type: row.asset_class === "crypto_option" ? "crypto_option" : "crypto_derivatives",
      contract_type: row.contract_type,
      subtype: row.subtype,
      subtype_label: row.subtype_label,
      base_asset: row.base_asset,
      quote_asset: row.quote_asset,
      settlement_asset: row.settlement_asset,
      market: {
        last_price: row.price,
        mark_price: row.mark_price,
        index_price: row.index_price,
        price_change_24h_pct: row.change_percent_24h,
        quote_volume_24h: row.volume_24h_quote,
        open_interest: row.open_interest,
        funding_rate: row.funding_rate,
        next_funding_time: row.next_funding_time,
        spread: row.spread,
      },
      trading_rules: {
        ...(row.trading_rules || {}),
        price_precision: row.price_precision,
        quantity_precision: row.quantity_precision,
        max_leverage: row.max_leverage,
      },
      lifecycle: {
        onboard_date: row.onboard_date,
        delivery_date: row.delivery_date,
      },
      capabilities: row.capabilities,
    };
  }

  if (category === "rwa_stock_token") {
    return {
      ...base,
      type: "stock_token",
      ticker: row.ticker || row.underlying_symbol,
      token_symbol: row.symbol,
      chain_id: row.chain_id,
      chain_label: row.chain_label,
      asset_info: {
        underlying: row.underlying_symbol || row.ticker,
        is_tokenized: true,
        multiplier: row.multiplier,
        contract_address: row.contract_address,
        tradability: row.tradability || "query_only",
      },
      capabilities: row.capabilities,
    };
  }

  return {
    ...base,
    type: "stock_equity",
    ticker: row.symbol,
    quote_asset: row.currency,
    exchange: row.exchange,
    market: {
      last_price: row.price,
      change: row.change,
      change_percent: row.change_percent,
      market_cap_usd: row.market_cap_usd,
    },
    asset_info: {
      underlying: row.symbol,
      is_tokenized: false,
      tradability: row.status === "QUOTE_READY" ? "quote_ready" : "lookup_only",
    },
    capabilities: row.capabilities,
  };
}

function renderBinanceSpotTable(rows) {
  renderTable(
    binanceView("crypto_spot").table,
    [
      { key: "symbol", label: "Symbol", render: (row) => `<strong>${escapeHtml(row.display_symbol || row.symbol || "-")}</strong>` },
      { key: "base_asset", label: "Base", render: (row) => escapeHtml(row.base_asset || "-") },
      { key: "quote_asset", label: "Quote", render: (row) => escapeHtml(row.quote_asset || "-") },
      { key: "price", label: "Price", render: (row) => formatNumber(row.price, 8) },
      { key: "change_percent_24h", label: "24h", render: (row) => formatPercent(row.change_percent_24h) },
      { key: "volume_24h_quote", label: "Quote Vol", render: (row) => formatNumber(row.volume_24h_quote, 0) },
      { key: "status", label: "Status", render: (row) => statusChip(row.status) },
      { key: "capabilities", label: "Caps", render: (row) => capabilityTags(row) },
      { key: "instrument_id", label: "Instrument", render: (row) => instrumentCode(row) },
      { key: "actions", label: "操作", render: (row) => binanceCopyAction("crypto_spot", row) },
    ],
    rows
  );
}

function renderBinanceDerivativesTable(rows) {
  renderTable(
    binanceView("crypto_derivatives").table,
    [
      { key: "symbol", label: "Symbol", render: (row) => `<strong>${escapeHtml(row.symbol || "-")}</strong>` },
      { key: "subtype_label", label: "Type", render: (row) => escapeHtml(row.subtype_label || row.subtype || "-") },
      { key: "base_asset", label: "Base", render: (row) => escapeHtml(row.base_asset || "-") },
      { key: "settlement_asset", label: "Settle", render: (row) => escapeHtml(row.settlement_asset || "-") },
      { key: "contract_type", label: "Contract", render: (row) => escapeHtml(row.contract_type || "-") },
      { key: "status", label: "Status", render: (row) => statusChip(row.status) },
      { key: "instrument_id", label: "Instrument", render: (row) => instrumentCode(row) },
      { key: "actions", label: "操作", render: (row) => binanceCopyAction("crypto_derivatives", row) },
    ],
    rows
  );
}

function renderBinanceStockTokensTable(rows) {
  renderTable(
    binanceView("rwa_stock_token").table,
    [
      { key: "ticker", label: "Ticker", render: (row) => `<strong>${escapeHtml(row.ticker || row.underlying_symbol || "-")}</strong>` },
      { key: "symbol", label: "Token", render: (row) => escapeHtml(row.symbol || "-") },
      { key: "chain_label", label: "Chain", render: (row) => escapeHtml(row.chain_label || row.chain_id || "-") },
      { key: "multiplier", label: "Multiplier", render: (row) => formatNumber(row.multiplier, 8) },
      { key: "status", label: "Status", render: (row) => statusChip(row.status) },
      { key: "contract_address", label: "Contract", render: (row) => shortContract(row.contract_address) },
      { key: "instrument_id", label: "Instrument", render: (row) => instrumentCode(row) },
      { key: "actions", label: "操作", render: (row) => binanceCopyAction("rwa_stock_token", row) },
    ],
    rows
  );
}

function renderBinanceEquityTable(rows) {
  renderTable(
    binanceView("equity").table,
    [
      { key: "symbol", label: "Symbol", render: (row) => `<strong>${escapeHtml(row.symbol || "-")}</strong>` },
      { key: "display_name", label: "Name", render: (row) => escapeHtml(row.display_name || "-") },
      { key: "venue", label: "Venue", render: (row) => escapeHtml(row.venue || "-") },
      { key: "price", label: "Price", render: (row) => formatNumber(row.price, 4) },
      { key: "change_percent", label: "Change", render: (row) => formatPercent(row.change_percent) },
      { key: "exchange", label: "Exchange", render: (row) => escapeHtml(row.exchange || "-") },
      { key: "status", label: "Status", render: (row) => statusChip(row.status) },
      { key: "instrument_id", label: "Instrument", render: (row) => instrumentCode(row) },
      { key: "actions", label: "操作", render: (row) => binanceCopyAction("equity", row) },
    ],
    rows
  );
}

function renderBinanceMarkets(category, payload) {
  const rows = payload.data || [];
  currentBinanceRows[category] = rows;
  binanceView(category).meta.innerHTML = sourceMetaLine(payload);
  if (category === "crypto_spot") {
    renderBinanceSpotTable(rows);
  } else if (category === "crypto_derivatives") {
    renderBinanceDerivativesTable(rows);
  } else if (category === "rwa_stock_token") {
    renderBinanceStockTokensTable(rows);
  } else {
    renderBinanceEquityTable(rows);
  }
}

async function loadBinanceMarkets(category, formData, options = {}) {
  const { silent = false } = options;
  const view = binanceView(category);
  if (!silent && view.table) {
    setStatus(view.table, "加载中...");
  }
  const qs = new URLSearchParams(formData);
  qs.set("category", category);
  if (!qs.get("refresh")) {
    qs.delete("refresh");
  } else {
    qs.set("refresh", "1");
  }
  const payload = await fetchJson(`/api/binance/markets/search?${qs.toString()}`);
  renderBinanceMarkets(category, payload);
  hasLoadedBinanceMarkets = true;
}

function switchBinanceTab(category) {
  activeBinanceCategory = category;
  binanceMarketTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.binanceTab === category);
  });
  binanceMarketPanes.forEach((pane) => {
    pane.classList.toggle("active", pane.dataset.binancePane === category);
  });
  if (!currentBinanceRows[category]?.length) {
    const form = binanceForm(category);
    if (form) {
      loadBinanceMarkets(category, new FormData(form)).catch((error) => {
        setStatus(binanceView(category).table, error.message);
        binanceView(category).meta.innerHTML = `状态: ${statusChip("ERROR")} | 诊断: ${escapeHtml(error.message)}`;
      });
    }
  }
}

async function loadStrategies(options = {}) {
  const { silent = false } = options;
  const t0 = performance.now();
  if (!silent && !hasLoadedStrategies) {
    setStatus(strategyTable, "加载中...");
  }
  const data = await fetchJson(`/api/polymarket/strategies?limit=${HOMEPAGE_STRATEGY_LIMIT}&sync_stats=0`);
  const t1 = performance.now();
  renderStrategyMeta(data);
  if (!data.ok) {
    if (!silent || !hasLoadedStrategies) {
      setStatus(strategyTable, data.error || "策略监控读取失败");
    }
    const t2 = performance.now();
    console.log(
      `[FE] loadStrategies fetch=${(t1 - t0).toFixed(1)}ms render=${(t2 - t1).toFixed(1)}ms total=${(t2 - t0).toFixed(1)}ms rows=0`
    );
    return;
  }
  renderStrategyRows(data.data || []);
  hasLoadedStrategies = true;
  const t2 = performance.now();
  console.log(
    `[FE] loadStrategies fetch=${(t1 - t0).toFixed(1)}ms render=${(t2 - t1).toFixed(1)}ms total=${(t2 - t0).toFixed(1)}ms rows=${(data.data || []).length}`
  );
}

function summarizeStrategyRows(rows) {
  const totalStrategyProfit = rows.reduce((sum, row) => sum + Number(resolveStrategyProfit(row) || 0), 0);
  const totalStrategyBankroll = rows.reduce((sum, row) => sum + Number(row.strategy_bankroll || 0), 0);
  return {
    ok: true,
    status: rows.length ? "good" : "pending",
    count: rows.length,
    running_strategy_count: rows.filter((row) => Number(row.yes_qty || 0) > 0 || Number(row.no_qty || 0) > 0).length,
    total_strategy_profit: totalStrategyProfit,
    total_strategy_bankroll: totalStrategyBankroll,
    total_strategy_return_pct: totalStrategyBankroll > 0 ? totalStrategyProfit / totalStrategyBankroll : null,
  };
}

function renderStrategyMeta(data = {}) {
  const rows = data.rows || data.data || [];
  const summary = {
    ...summarizeStrategyRows(rows),
    ...data,
  };
  const status = statusInfo(summary.status || (summary.ok ? "good" : "error"), false, Boolean(rows.length));
  const sourceStatuses = summary.source_statuses || {};
  const sourceProblems = Object.entries(sourceStatuses)
    .filter(([, value]) => value && value.error)
    .map(([key, value]) => `${key}: ${value.error}`)
    .join(" | ");
  strategyMeta.innerHTML = `数据源: ${animateHtml("strategy-meta", "source", escapeHtml(summary.table || "-"), summary.table || "-")} | 监控库: ${animateHtml("strategy-meta", "snapshot-db", escapeHtml(summary.snapshot_db_path || "-"), summary.snapshot_db_path || "-")} | 实时快照库: ${animateHtml("strategy-meta", "realtime-db", escapeHtml(summary.realtime_snapshot_db_path || "-"), summary.realtime_snapshot_db_path || "-")} | 策略统计目录: ${animateHtml("strategy-meta", "metrics-dir", escapeHtml(summary.strategy_metrics_db_dir || "-"), summary.strategy_metrics_db_dir || "-")} | 状态: <span class="state-chip ${status.tone}">${status.label}</span> | 条数: ${animateHtml("strategy-meta", "count", escapeHtml(summary.count || 0), summary.count || 0)} | 运行策略: ${animateHtml("strategy-meta", "running", escapeHtml(summary.running_strategy_count || 0), summary.running_strategy_count || 0)} | 总盈利: ${animateHtml("strategy-meta", "profit", formatNumber(summary.total_strategy_profit || 0, 2), summary.total_strategy_profit || 0)}${sourceProblems ? ` | 诊断: ${escapeHtml(sourceProblems)}` : ""}`;
}

async function loadDictionaryStatus(options = {}) {
  const { silent = false } = options;
  if (!silent && !hasLoadedDictionary) {
    setStatus(dictionarySummary, "加载中...");
    dictionaryProgress.textContent = "加载中...";
    dictionaryLog.textContent = "加载中...";
  }
  const payload = await fetchJson("/api/polymarket/dictionary");
  renderDictionaryStatus(payload.data || {});
}

async function triggerDictionaryUpdate() {
  updateDictionaryBtn.disabled = true;
  updateDictionaryBtn.textContent = "提交中...";
  const payload = await fetchJson("/api/polymarket/dictionary/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  renderDictionaryStatus(payload.data || {});
}

function strategyWatchlistBtn(row) {
  if (!row.condition_id) return "";
  const inList = marketUi.isInWatchlist(row);
  const safeData = escapeHtml(JSON.stringify({
    condition_id: row.condition_id,
    yes_token: row.yes_token || "",
    no_token: row.no_token || "",
    slug: row.slug || row.matched_market_raw?.slug || "",
    event_slug: row.event_slug || row.matched_market_raw?.eventSlug || row.matched_market_raw?.event_slug || "",
    group_item_title: row.group_item_title || row.matched_market_raw?.groupItemTitle || "",
    url: row.url || row.matched_market_raw?.url || "",
    question: row.question || "",
  }));
  return `<button class="mini ghost stg-watch-btn" type="button" data-stg-watch="${safeData}" title="${inList ? "取消自选" : "加入自选"}">${inList ? "\u2605" : "\u2606"}</button>`;
}

function renderStrategyRowsLegacy(rows) {
  strategyRowCache.clear();
  for (const r of rows) strategyRowCache.set(String(r.row_id), r);
  renderTable(
    strategyTable,
    [
      { key: "display_name", label: "Strategy", render: (row) => escapeHtml(row.display_name || row.strategy || "-") },
      { key: "question", label: "Question", render: (row) => `<span class="stg-question-cell">${escapeHtml(row.question || "-")}${strategyWatchlistBtn(row)}</span>` },
      { key: "score", label: "Score", render: (row) => formatNumber(row.score, 2) },
      { key: "yes_ask", label: "Yes_ask", render: (row) => formatNumber(row.yes_ask, 4) },
      { key: "yes_bid", label: "Yes_bids", render: (row) => formatNumber(row.yes_bid, 4) },
      { key: "no_ask", label: "No_ask", render: (row) => formatNumber(row.no_ask, 4) },
      { key: "no_bid", label: "No_bids", render: (row) => formatNumber(row.no_bid, 4) },
      { key: "yes_qty", label: "Yes_Qty", render: (row) => formatNumber(row.yes_qty, 4) },
      { key: "yes_avg", label: "Yes_Avg", render: (row) => formatNumber(row.yes_avg, 4) },
      { key: "no_qty", label: "No_Qty", render: (row) => formatNumber(row.no_qty, 4) },
      { key: "no_avg", label: "No_Avg", render: (row) => formatNumber(row.no_avg, 4) },
      { key: "strategy_bankroll", label: "Strategy_Bankroll", render: (row) => formatNumber(row.strategy_bankroll, 2) },
      { key: "yes_position", label: "Yes_CurrentPct", render: (row) => formatRatioPercent(row.yes_position) },
      { key: "no_position", label: "No_CurrentPct", render: (row) => formatRatioPercent(row.no_position) },
      { key: "mode", label: "Mode", render: (row) => {
        const sid = row.strategy_id ?? row.row_id;
        const cur = strategyMode(row);
        return `<select class="state-select mode-select state-${cur}" data-mode-sid="${sid}" data-sid="${sid}" data-prev="${escapeHtml(cur)}">${
          ["Stop", "Virtual", "Real"].map(s =>
            `<option value="${s}"${s === cur ? " selected" : ""}>${s}</option>`
          ).join("")
        }</select>`;
      }},
      { key: "machine_state", label: "State", render: (row) => renderMachineStateSelect(row, row.strategy_id ?? row.row_id) },
      {
        key: "profit",
        label: "profit",
        render: (row) => formatPnL(resolveStrategyProfit(row)),
        compare: (row) => resolveStrategyProfit(row),
      },
      {
        key: "actions",
        label: "操作",
        render: (row) => {
          const rid = row.row_id;
          return `
          <div class="stg-action-group">
            <a class="stg-btn stg-btn-default" href="/strategies/${rid}/workspace" target="_blank" rel="noopener noreferrer">工作台</a>
            <button class="stg-btn stg-btn-edit" data-edit-sid="${rid}">设置参数</button>
            <button class="stg-btn stg-btn-state" data-state-sid="${rid}">State</button>
            <button class="stg-btn stg-btn-warning" data-flat-sid="${rid}" data-flat-name="${escapeHtml(row.strategy || row.display_name || String(rid))}" data-flat-mode="${escapeHtml(strategyMode(row))}">平仓</button>
            <button class="stg-btn stg-btn-danger" data-delete-sid="${rid}" data-delete-name="${escapeHtml(row.strategy || row.display_name || String(rid))}">删除</button>
          </div>
        `;
        },
      },
    ],
    rows
  );
}

function strategyMode(row) {
  const validModes = ["Stop", "Virtual", "Real"];
  const isVirtual = String(row.is_virtual ?? row.editable?.IsVirtual ?? "").trim().toLowerCase() === "true";
  const legacyState = validModes.includes(row.state) ? row.state : "";
  const mode = row.mode || legacyState || (isVirtual ? "Virtual" : "Stop");
  return validModes.includes(mode) ? mode : "Stop";
}

function defaultStrategyStateOptions() {
  return [
    { value: "auto", label: "Auto" },
    { value: "idle", label: "Idle" },
    { value: "holding", label: "Holding" },
    { value: "cooldown", label: "Cooldown" },
    { value: "manual_review", label: "Manual Review" },
    { value: "stop_loss_locked", label: "Stop Loss Locked" },
  ];
}

function strategyMachineState(row) {
  const validModes = ["Stop", "Virtual", "Real"];
  const value = row.machine_state || (!validModes.includes(row.state) ? row.state : "") || "auto";
  return String(value || "auto");
}

function renderMachineStateSelect(row, sid) {
  const current = strategyMachineState(row);
  const options = Array.isArray(row.state_options) && row.state_options.length
    ? row.state_options
    : defaultStrategyStateOptions();
  const hasCurrent = options.some((item) => String(item.value ?? item) === current);
  const normalized = hasCurrent ? options : [{ value: current, label: current }, ...options];
  return `<select class="state-select strategy-machine-state-select" data-machine-state-sid="${escapeHtml(sid)}" data-prev="${escapeHtml(current)}">${
    normalized.map((item) => {
      const value = String(item.value ?? item);
      const label = String(item.label ?? value);
      return `<option value="${escapeHtml(value)}"${value === current ? " selected" : ""}>${escapeHtml(label)}</option>`;
    }).join("")
  }</select>`;
}

function strategyName(row) {
  return row.strategy_name || row.display_name || row.strategy || "-";
}

function strategyCode(row) {
  return row.strategy_code || row.raw?.Code || row.editable?.StrategyCode || "-";
}

function strategyLegsLabel(row) {
  const count = Number(row.legs_count ?? row.legs_snapshot?.length ?? 1);
  if (!Number.isFinite(count) || count <= 0) {
    return "1";
  }
  return count > 1 ? `${count} legs` : "1";
}

function resolveStrategyExposure(row) {
  const explicitValue = Number(row.exposure);
  if (Number.isFinite(explicitValue)) {
    return explicitValue;
  }
  const yesQty = Number(row.yes_qty || 0);
  const noQty = Number(row.no_qty || 0);
  const yesAvg = Number(row.yes_avg || 0);
  const noAvg = Number(row.no_avg || 0);
  return yesQty * yesAvg + noQty * noAvg;
}

function strategyActionClass(row) {
  const type = String(row.last_action_type || "").toLowerCase();
  const text = String(row.last_action || "").toLowerCase();
  if (type === "error" || text.startsWith("error") || text.includes("missing")) {
    return "strategy-last-action error";
  }
  if (text === "hold" || text === "no signal" || text.startsWith("print:")) {
    return "strategy-last-action muted-action";
  }
  return "strategy-last-action";
}

function strategySideBadge(side) {
  const safe = escapeHtml(side || "None");
  return `<span class="side-chip side-${safe.toLowerCase()}">${safe}</span>`;
}

function strategyEventBadge(type) {
  const safe = escapeHtml(type || "print");
  return `<span class="event-chip event-${safe}">${safe}</span>`;
}

function formatLegQty(leg) {
  return formatNumber(leg.qty ?? 0, 4);
}

function formatLegAvg(leg) {
  return formatFixed(leg.avg, 4);
}

function formatLegParams(leg) {
  return leg.params_summary || "-";
}

function marketExternalLink(market, label, className = "market-text-link") {
  const text = label || market?.question || market?.label || market?.slug || market?.condition_id || "-";
  const url = marketUi.buildPolymarketUrl(market || {});
  return `<a class="${className}" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(text)}">${escapeHtml(text)}</a>`;
}

function renderStrategyExpanded(row) {
  const legs = row.legs_snapshot || [];
  const events = row.recent_events || [];
  const legRows = legs.length
    ? legs.map((leg) => `
      <tr>
        <td class="cell-center">${escapeHtml(leg.leg ?? Number(leg.leg_index || 0) + 1)}</td>
        <td>${marketExternalLink(leg, leg.question || "-")}</td>
        <td><span class="truncate strategy-params" title="${escapeHtml(formatLegParams(leg))}">${escapeHtml(formatLegParams(leg))}</span></td>
        <td>${strategySideBadge(leg.side)}</td>
        <td class="num">${formatNumber(leg.yes_qty, 4)}</td>
        <td class="num">${formatFixed(leg.yes_avg, 4)}</td>
        <td class="num">${formatFixed(leg.yes_mark, 4)}</td>
        <td class="num">${formatNumber(leg.no_qty, 4)}</td>
        <td class="num">${formatFixed(leg.no_avg, 4)}</td>
        <td class="num">${formatFixed(leg.no_mark, 4)}</td>
        <td class="num">${formatFixed(leg.exposure, 2)}</td>
        <td class="num">${formatPnL(leg.pnl)}</td>
        <td>${formatShortTime(leg.updated_at)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="13" class="empty-cell">No leg snapshot</td></tr>`;
  const eventRows = events.length
    ? events.map((event) => `
      <tr>
        <td>${formatShortTime(event.time)}</td>
        <td>${strategyEventBadge(event.type)}</td>
        <td><span class="truncate strategy-event-content" title="${escapeHtml(event.content || "-")}">${escapeHtml(event.content || "-")}</span></td>
      </tr>
    `).join("")
    : `<tr><td colspan="3" class="empty-cell">No recent action / print</td></tr>`;
  return `
    <tr class="strategy-detail-row">
      <td colspan="12">
        <div class="strategy-detail-card">
          <div class="strategy-detail-section">
            <h3>Legs Snapshot</h3>
            <div class="strategy-subtable-wrap">
              <table class="strategy-subtable">
                <thead><tr><th>Leg</th><th>Question</th><th>Params</th><th>Side</th><th>YES Qty</th><th>YES Avg</th><th>YES Mark</th><th>NO Qty</th><th>NO Avg</th><th>NO Mark</th><th>Exposure</th><th>Net PnL</th><th>Updated</th></tr></thead>
                <tbody>${legRows}</tbody>
              </table>
            </div>
          </div>
          <div class="strategy-detail-section">
            <h3>Recent Action / Print</h3>
            <div class="strategy-subtable-wrap strategy-events-scroll">
              <table class="strategy-subtable">
                <thead><tr><th>Time</th><th>Type</th><th>Content</th></tr></thead>
                <tbody>${eventRows}</tbody>
              </table>
            </div>
          </div>
        </div>
      </td>
    </tr>
  `;
}

function renderStrategyRows(rows) {
  strategyRowCache.clear();
  for (const r of rows) strategyRowCache.set(String(r.row_id), r);
  if (!rows || !rows.length) {
    setStatus(strategyTable, "没有策略数据");
    return;
  }
  const body = rows.map((row) => {
    const rid = String(row.row_id);
    const isExpanded = expandedStrategyRows.has(rid);
    const pnl = resolveStrategyProfit(row);
    const exposure = resolveStrategyExposure(row);
    const mode = strategyMode(row);
    const mainRow = `
      <tr class="strategy-summary-row${isExpanded ? " expanded" : ""}">
        <td>
          <button class="strategy-expand-btn" type="button" data-expand-sid="${escapeHtml(rid)}" aria-label="${isExpanded ? "收起" : "展开"}">${isExpanded ? "▼" : "▶"}</button>
          <span class="strategy-name truncate" title="${escapeHtml(strategyName(row))}">${escapeHtml(strategyName(row))}</span>
        </td>
        <td><span class="strategy-code-chip" title="${escapeHtml(strategyCode(row))}">${escapeHtml(strategyCode(row))}</span></td>
        <td>
          <select class="state-select mode-select state-${escapeHtml(mode)}" data-mode-sid="${escapeHtml(rid)}" data-sid="${escapeHtml(rid)}" data-prev="${escapeHtml(mode)}">
            ${["Stop", "Virtual", "Real"].map(s => `<option value="${s}"${s === mode ? " selected" : ""}>${s}</option>`).join("")}
          </select>
        </td>
        <td>${renderMachineStateSelect(row, rid)}</td>
        <td>${escapeHtml(strategyLegsLabel(row))}</td>
        <td class="num">${formatFixed(row.strategy_bankroll, 2)}</td>
        <td class="num">${formatFixed(exposure, 2)}</td>
        <td class="num">${formatPnL(pnl)}</td>
        <td class="num">${formatRoi(pnl, row.strategy_bankroll)}</td>
        <td><span class="${strategyActionClass(row)} truncate" title="${escapeHtml(row.last_action || "No signal")}">${escapeHtml(row.last_action || "No signal")}</span></td>
        <td>${formatShortTime(row.updated_at || row.market_updated_at)}</td>
        <td>
          <div class="strategy-actions">
            <a class="stg-btn stg-btn-default" href="/strategies/${escapeHtml(rid)}/workspace" target="_blank" rel="noopener noreferrer">工作台</a>
            <button class="stg-btn stg-btn-edit" data-edit-sid="${escapeHtml(rid)}">参数</button>
            <button class="stg-btn stg-btn-state" data-state-sid="${escapeHtml(rid)}">State</button>
            <button class="stg-btn stg-btn-warning" data-flat-sid="${escapeHtml(rid)}" data-flat-name="${escapeHtml(strategyName(row))}" data-flat-mode="${escapeHtml(mode)}">平仓</button>
            <button class="stg-btn stg-btn-danger" data-delete-sid="${escapeHtml(rid)}" data-delete-name="${escapeHtml(strategyName(row))}">删除</button>
          </div>
        </td>
      </tr>
    `;
    return mainRow + (isExpanded ? renderStrategyExpanded(row) : "");
  }).join("");
  strategyTable.innerHTML = `
    <div class="strategy-monitor-shell">
      <table class="strategy-monitor-table">
        <thead>
          <tr>
            <th>Name</th><th>Strategy_Code</th><th>Mode</th><th>State</th><th>Legs</th><th>Bankroll</th><th>Exposure</th><th>PnL</th><th>ROI</th><th>Last Action</th><th>Updated</th><th>Action</th>
          </tr>
        </thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

function disconnectStrategiesLive() {
  if (strategyLiveSource) {
    strategyLiveSource.close();
    strategyLiveSource = null;
  }
}

function disconnectDictionaryLive() {
  if (dictionaryLiveSource) {
    dictionaryLiveSource.close();
    dictionaryLiveSource = null;
  }
}

function connectStrategiesLive() {
  disconnectStrategiesLive();
  strategyLiveSource = new EventSource(`/api/live/strategies?limit=${HOMEPAGE_STRATEGY_LIMIT}`);

  strategyLiveSource.addEventListener("rows", (evt) => {
    try {
      const payload = JSON.parse(evt.data || "{}");
      renderStrategyMeta(payload);
      renderStrategyRows(payload.rows || []);
      hasLoadedStrategies = true;
    } catch {}
  });

  strategyLiveSource.addEventListener("error", () => {
    // Let EventSource reconnect automatically.
  });
}

function connectDictionaryLive() {
  disconnectDictionaryLive();
  dictionaryLiveSource = new EventSource("/api/live/polymarket/dictionary");

  dictionaryLiveSource.addEventListener("state", (evt) => {
    try {
      const payload = JSON.parse(evt.data || "{}");
      renderDictionaryStatus(payload);
    } catch {}
  });

  dictionaryLiveSource.addEventListener("error", () => {
    // Let EventSource reconnect automatically.
  });
}

function startUiPolling(seconds) {
  if (uiRefreshTimer) {
    clearInterval(uiRefreshTimer);
  }
  uiRefreshTimer = setInterval(() => {
    loadAgentDashboard({ silent: true }).catch((error) => {
      if (agentMeta) agentMeta.textContent = error.message;
    });
    loadOverview({ silent: true }).catch((error) => setStatus(systemStatus, error.message));
    loadRealtimeCrypto({ silent: true }).catch((error) => setStatus(cryptoTable, error.message));
    loadRealtimeFinance({ silent: true }).catch((error) => setStatus(financeTable, error.message));
  }, Math.max(2, seconds) * 1000);
}

document.getElementById("refreshOverviewBtn").addEventListener("click", () => {
  loadAgentDashboard().catch((error) => {
    if (agentMeta) agentMeta.textContent = error.message;
  });
  loadOverview().catch((error) => setStatus(overviewCards, error.message));
  loadRealtimeCrypto().catch((error) => setStatus(cryptoTable, error.message));
  loadRealtimeFinance().catch((error) => setStatus(financeTable, error.message));
});

refreshAgentBtn?.addEventListener("click", () => {
  loadAgentDashboard().catch((error) => {
    if (agentMeta) agentMeta.textContent = error.message;
  });
});

document.getElementById("refreshCryptoBtn").addEventListener("click", () => {
  loadRealtimeCrypto().catch((error) => setStatus(cryptoTable, error.message));
});

document.getElementById("refreshFinanceBtn").addEventListener("click", () => {
  loadRealtimeFinance().catch((error) => setStatus(financeTable, error.message));
});

document.getElementById("refreshStrategyBtn").addEventListener("click", () => {
  loadStrategies().catch((error) => setStatus(strategyTable, error.message));
});

refreshDictionaryBtn.addEventListener("click", () => {
  loadDictionaryStatus().catch((error) => {
    setStatus(dictionarySummary, error.message);
    dictionaryProgress.textContent = error.message;
  });
});

updateDictionaryBtn.addEventListener("click", () => {
  triggerDictionaryUpdate().catch((error) => {
    updateDictionaryBtn.disabled = false;
    updateDictionaryBtn.textContent = "更新字典";
    dictionaryProgress.textContent = error.message;
  });
});

document.getElementById("holdingsForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const wallet = document.getElementById("walletInput").value.trim();
  loadHoldings(wallet).catch((error) => setStatus(holdingsTable, error.message));
});

document.getElementById("marketForm").addEventListener("submit", (event) => {
  event.preventDefault();
  loadMarkets(new FormData(event.currentTarget)).catch((error) => setStatus(marketTable, error.message));
});

marketCategoryInput?.addEventListener("input", () => {
  syncMarketCategoryChips();
});

marketCategoryChips?.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-market-category-chip]");
  if (!chip) {
    return;
  }
  const category = chip.dataset.marketCategoryChip || "";
  const current = parseMarketCategories(marketCategoryInput?.value || "");
  const exists = current.some((item) => item.toLowerCase() === category.toLowerCase());
  setMarketCategories(exists
    ? current.filter((item) => item.toLowerCase() !== category.toLowerCase())
    : [...current, category]);
});

marketSortSelect?.addEventListener("change", () => {
  if (!marketSortDirSelect) {
    return;
  }
  marketSortDirSelect.value = MARKET_SORT_ASC_DEFAULTS.has(marketSortSelect.value) ? "asc" : "desc";
});

binanceMarketTabs.forEach((tab) => {
  tab.addEventListener("click", () => switchBinanceTab(tab.dataset.binanceTab));
});

binanceMarketForms.forEach((form) => {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const category = event.currentTarget.dataset.binanceForm;
    loadBinanceMarkets(category, new FormData(event.currentTarget)).catch((error) => {
      setStatus(binanceView(category).table, error.message);
      binanceView(category).meta.innerHTML = `状态: ${statusChip("ERROR")} | 诊断: ${escapeHtml(error.message)}`;
    });
  });
});

document.addEventListener("click", (event) => {
  const marketButton = event.target.closest("[data-copy-market]");
  if (marketButton) {
    const row = currentMarketRows.find((item) => marketUi.marketIdentityKey(item) === marketButton.dataset.copyMarket);
    if (!row) {
      return;
    }
    copyJsonParams(buildPolymarketStrategyParams(row), marketButton).catch((error) => {
      console.error("[copy params] polymarket failed", error);
      marketButton.textContent = "复制失败";
    });
    return;
  }

  const binanceButton = event.target.closest("[data-copy-binance-row]");
  if (binanceButton) {
    const category = binanceButton.dataset.copyBinanceCategory || activeBinanceCategory;
    const rowKey = binanceButton.dataset.copyBinanceRow || "";
    const row = (currentBinanceRows[category] || []).find((item) => {
      return [item.instrument_id, item.symbol, item.ticker, item.contract_address].some((value) => String(value || "") === rowKey);
    });
    if (!row) {
      return;
    }
    copyJsonParams(buildBinanceStrategyParams(row, category), binanceButton).catch((error) => {
      console.error("[copy params] binance failed", error);
      binanceButton.textContent = "复制失败";
    });
  }
});

document.querySelector(".agent-workbench")?.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const approvalToApprove = button.dataset.agentApprove;
  const approvalToReject = button.dataset.agentReject;
  const approvalToChange = button.dataset.agentChange;
  const approvalToView = button.dataset.agentViewApproval;
  const draftToRisk = button.dataset.agentRisk;
  const draftToSimulate = button.dataset.agentSimulate;
  const draftToSubmit = button.dataset.agentSubmit;
  const draftToDelete = button.dataset.agentDeleteDraft;
  const draftToView = button.dataset.agentViewDraft;
  try {
    if (approvalToView) {
      button.disabled = true;
      const payload = await fetchJson(`/api/agent/approvals/${encodeURIComponent(approvalToView)}`);
      await openAgentApprovalStrategyModal(payload.data || {});
      return;
    }
    if (approvalToApprove) {
      if (!confirm("确认批准这个 Agent 策略？批准后会创建正式策略，默认 Stop 模式。")) return;
      button.disabled = true;
      await postAgentAction(`/api/approvals/${encodeURIComponent(approvalToApprove)}/approve`, {
        actor_type: "human",
        actor_id: "local_user",
      });
      await Promise.allSettled([loadAgentDashboard({ silent: true }), loadStrategies({ silent: true })]);
      return;
    }
    if (approvalToReject) {
      const reason = prompt("拒绝原因", "风险或参数不合适");
      if (reason === null) return;
      button.disabled = true;
      await postAgentAction(`/api/approvals/${encodeURIComponent(approvalToReject)}/reject`, {
        actor_type: "human",
        actor_id: "local_user",
        reason,
      });
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (approvalToChange) {
      const reason = prompt("希望 Agent 如何修改？", "请降低预算或收紧入场价格");
      if (reason === null) return;
      button.disabled = true;
      await postAgentAction(`/api/approvals/${encodeURIComponent(approvalToChange)}/request-changes`, {
        actor_type: "human",
        actor_id: "local_user",
        reason,
      });
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (draftToRisk) {
      button.disabled = true;
      await postAgentAction(`/api/agent/strategy-drafts/${encodeURIComponent(draftToRisk)}/risk-check`, {
        actor_type: "agent",
        actor_id: "agent_strategy_assistant",
      });
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (draftToSimulate) {
      button.disabled = true;
      const payload = await postAgentAction(`/api/agent/strategy-drafts/${encodeURIComponent(draftToSimulate)}/simulate`, {
        actor_type: "agent",
        actor_id: "agent_strategy_assistant",
      });
      const sim = payload.data || {};
      alert(`模拟完成\n最大亏损: ${formatFixed(sim.max_loss_usdc, 2)} USDC\n最大敞口: ${formatFixed(sim.max_exposure_usdc, 2)} USDC`);
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (draftToSubmit) {
      if (!confirm("提交后会进入 WAITING_HUMAN_CONFIRM，等待人工确认。继续？")) return;
      button.disabled = true;
      await postAgentAction(`/api/agent/strategy-drafts/${encodeURIComponent(draftToSubmit)}/submit`, {
        actor_type: "agent",
        actor_id: "agent_strategy_assistant",
      });
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (draftToDelete) {
      if (!confirm("删除这个未提交草案？")) return;
      button.disabled = true;
      await fetchJson(`/api/agent/strategy-drafts/${encodeURIComponent(draftToDelete)}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          actor_type: "agent",
          actor_id: "agent_strategy_assistant",
        }),
      });
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (draftToView) {
      const payload = await fetchJson(`/api/agent/strategy-drafts/${encodeURIComponent(draftToView)}`);
      alert(JSON.stringify(payload.data?.draft || payload.data || {}, null, 2));
    }
  } catch (error) {
    alert(error.message || String(error));
    await loadAgentDashboard({ silent: true }).catch(() => {});
  } finally {
    button.disabled = false;
  }
});

agentApprovalModalClose?.addEventListener("click", closeAgentApprovalModal);
agentApprovalCloseBtn?.addEventListener("click", closeAgentApprovalModal);
agentApprovalModal?.addEventListener("click", (event) => {
  if (event.target === agentApprovalModal) closeAgentApprovalModal();
});

agentApprovalApproveBtn?.addEventListener("click", async () => {
  if (!activeAgentApproval?.approval_id) return;
  if (!confirm("确认批准这个 Agent 策略？批准后会创建正式策略，默认 Stop 模式。")) return;
  agentApprovalApproveBtn.disabled = true;
  try {
    await postAgentAction(`/api/approvals/${encodeURIComponent(activeAgentApproval.approval_id)}/approve`, {
      actor_type: "human",
      actor_id: "local_user",
    });
    closeAgentApprovalModal();
    await Promise.allSettled([loadAgentDashboard({ silent: true }), loadStrategies({ silent: true })]);
  } catch (error) {
    alert(error.message || String(error));
  } finally {
    agentApprovalApproveBtn.disabled = false;
  }
});

agentApprovalRejectBtn?.addEventListener("click", async () => {
  if (!activeAgentApproval?.approval_id) return;
  const reason = prompt("拒绝原因", "风险或参数不合适");
  if (reason === null) return;
  agentApprovalRejectBtn.disabled = true;
  try {
    await postAgentAction(`/api/approvals/${encodeURIComponent(activeAgentApproval.approval_id)}/reject`, {
      actor_type: "human",
      actor_id: "local_user",
      reason,
    });
    closeAgentApprovalModal();
    await loadAgentDashboard({ silent: true });
  } catch (error) {
    alert(error.message || String(error));
  } finally {
    agentApprovalRejectBtn.disabled = false;
  }
});

agentApprovalChangeBtn?.addEventListener("click", async () => {
  if (!activeAgentApproval?.approval_id) return;
  const reason = prompt("希望 Agent 如何修改？", "请调整参数后重新提交");
  if (reason === null) return;
  agentApprovalChangeBtn.disabled = true;
  try {
    await postAgentAction(`/api/approvals/${encodeURIComponent(activeAgentApproval.approval_id)}/request-changes`, {
      actor_type: "human",
      actor_id: "local_user",
      reason,
    });
    closeAgentApprovalModal();
    await loadAgentDashboard({ silent: true });
  } catch (error) {
    alert(error.message || String(error));
  } finally {
    agentApprovalChangeBtn.disabled = false;
  }
});

marketTable.addEventListener("click", (event) => {
  const button = event.target.closest("[data-watch-market]");
  if (!button) {
    return;
  }
  const row = currentMarketRows.find((item) => marketUi.marketIdentityKey(item) === button.dataset.watchMarket);
  if (!row) {
    return;
  }
  marketUi.toggleWatchlist(row);
  renderMarketSearchTable(currentMarketRows);
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    disconnectStrategiesLive();
    disconnectDictionaryLive();
    return;
  }
  connectStrategiesLive();
  connectDictionaryLive();
});

window.addEventListener("beforeunload", () => {
  disconnectStrategiesLive();
  disconnectDictionaryLive();
});

// --- Strategy Modal + State switch ---
const strategyModal = document.getElementById("strategyModal");
const strategyModalForm = document.getElementById("strategyModalForm");
const strategyModalTitle = document.getElementById("strategyModalTitle");
const strategyCodeSelect = document.getElementById("strategyCodeSelect");
const addStrategyBtn = document.getElementById("addStrategyBtn");
const strategyDynamicMessage = document.getElementById("strategyDynamicMessage");
const strategyStateModal = document.getElementById("strategyStateModal");
const strategyStateModalTitle = document.getElementById("strategyStateModalTitle");
const strategyStateModalSubtitle = document.getElementById("strategyStateModalSubtitle");
const strategyUserStateJson = document.getElementById("strategyUserStateJson");
const strategyRuntimeStateJson = document.getElementById("strategyRuntimeStateJson");
const strategyUserStateEffective = document.getElementById("strategyUserStateEffective");
const strategyRuntimeStateEffective = document.getElementById("strategyRuntimeStateEffective");
const strategyStateMessage = document.getElementById("strategyStateMessage");
const strategyRuntimeStateBadge = document.getElementById("strategyRuntimeStateBadge");
const strategyUserStateSave = document.getElementById("strategyUserStateSave");
const strategyUserStateReset = document.getElementById("strategyUserStateReset");
const strategyRuntimeStateSave = document.getElementById("strategyRuntimeStateSave");
const strategyRuntimeStateReset = document.getElementById("strategyRuntimeStateReset");
const strategyLegRows = document.getElementById("strategyLegRows");
const addStrategyLegBtn = document.getElementById("addStrategyLegBtn");
const strategyModalStateSections = document.getElementById("strategyModalStateSections");

let activeStateStrategyId = "";
let activeStateMode = "Stop";
let activeModalStateStore = null;
let strategyLegDraft = [];
let activeConditionLegIndex = 0;
let strategyLegSchemas = [];
let strategyModalDirty = false;
let activeAgentStrategyEditContext = null;

function stateEditorJson(value) {
  const obj = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return JSON.stringify(obj, null, 2);
}

function setStateEffectivePreview(element, value) {
  if (!element) return;
  element.textContent = stateEditorJson(value);
}

function parseStateEditor(textarea, label) {
  const raw = textarea?.value?.trim() || "";
  if (!raw) return {};
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return parsed;
}

function setStateEditorMessage(text, tone = "") {
  if (!strategyStateMessage) return;
  strategyStateMessage.textContent = text;
  strategyStateMessage.className = `state-editor-message muted ${tone}`.trim();
}

function setRuntimeStateControls(mode) {
  activeStateMode = mode || "Stop";
  const editable = activeStateMode === "Stop";
  if (strategyRuntimeStateJson) strategyRuntimeStateJson.readOnly = !editable;
  if (strategyRuntimeStateSave) strategyRuntimeStateSave.disabled = !editable;
  if (strategyRuntimeStateReset) strategyRuntimeStateReset.disabled = !editable;
  if (strategyRuntimeStateBadge) {
    strategyRuntimeStateBadge.textContent = editable ? "Editable while stopped" : "Locked while running";
    strategyRuntimeStateBadge.className = `state-editor-badge ${editable ? "editable" : "locked"}`;
  }
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

function buildInlineStateField(key, meta, values, namespace, options = {}) {
  const type = stateSchemaType(meta);
  const value = values?.[key] ?? meta?.default ?? "";
  const declared = options.declared !== false;
  const description = declared ? stateDescription(key, meta) : "未在当前策略代码的 RuntimeStateSchema 中声明。";
  const disabled = options.disabled ? "disabled" : "";
  const badge = declared ? "" : '<span class="state-undeclared-badge">未声明</span>';
  const help = description
    ? `<span class="strategy-param-help" tabindex="0" title="${escapeHtml(description)}" aria-label="${escapeHtml(description)}">?</span>`
    : "";
  const comment = description ? `<span class="state-field-comment">${escapeHtml(description.split("\n")[0])}</span>` : "";
  if (type === "boolean") {
    return `
      <label class="strategy-param-field state-boolean-field" title="${escapeHtml(description)}">
        <span class="strategy-param-label">${escapeHtml(stateLabel(key, meta))}${badge}${help}</span>
        <span class="state-checkbox-row">
          <input type="checkbox" data-inline-${namespace}-state-key="${escapeHtml(key)}" ${stateBoolValue(value) ? "checked" : ""} ${disabled}>
          <span>${escapeHtml(key)}</span>
        </span>
        ${comment}
      </label>
    `;
  }
  return `
    <label class="strategy-param-field" title="${escapeHtml(description)}">
      <span class="strategy-param-label">${escapeHtml(stateLabel(key, meta))}${badge}${help}</span>
      <input data-inline-${namespace}-state-key="${escapeHtml(key)}" type="${type === "number" ? "number" : "text"}" ${type === "number" ? 'step="any"' : ""} value="${escapeHtml(value ?? "")}" ${disabled}>
      ${comment}
    </label>
  `;
}

function inlineMachineState(stateStore) {
  return String(stateStore?.machine_state || stateStore?.state || stateStore?.machine?.state || "auto");
}

function inlineStateOptions(stateStore) {
  const raw = stateStore?.state_options || stateStore?.state_machine_schema?.states || [];
  const options = Array.isArray(raw) && raw.length ? raw : defaultStrategyStateOptions();
  const current = inlineMachineState(stateStore);
  const hasCurrent = options.some((item) => String(item.value ?? item) === current);
  return hasCurrent ? options : [{ value: current, label: current }, ...options];
}

function buildInlineMachineStateSection(stateStore) {
  const schema = stateStore?.state_machine_schema || {};
  const current = inlineMachineState(stateStore);
  const options = inlineStateOptions(stateStore);
  return `
    <div class="workspace-settings-group">
      <h3>Strategy State</h3>
      <div class="grid two">
        <label class="strategy-param-field">
          <span class="strategy-param-label">${escapeHtml(schema.label || "State")}</span>
          <select data-inline-machine-state-key="state">
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

function renderInlineStrategyStateStore(stateStore) {
  if (!strategyModalStateSections) return;
  activeModalStateStore = stateStore || null;
  if (!stateStore) {
    strategyModalStateSections.innerHTML = `
      <div class="workspace-settings-group">
        <h3>Controls / UserState</h3>
        <div class="muted">保存策略后可以在这里编辑 Controls 和 RuntimeState。</div>
      </div>
    `;
    return;
  }
  const mode = stateStore.mode || activeStateMode || "Stop";
  const runtimeEditable = mode === "Stop";
  const controlsSchema = stateStore.controls_schema || {};
  const controlValues = stateStore.controls || stateStore.user || stateStore.user_overrides || {};
  const controlFields = Object.keys(controlsSchema).map((key) => buildInlineStateField(key, controlsSchema[key], controlValues, "user")).join("");
  const runtimeSchema = stateStore.runtime_state_schema || stateStore.schemas?.runtime || {};
  const runtime = stateStore.runtime || {};
  const runtimeValues = { ...runtime, ...(stateStore.runtime_overrides || {}) };
  const runtimeFields = stateFieldKeys(runtimeSchema, runtime, stateStore.runtime_overrides || {})
    .map((key) => buildInlineStateField(key, runtimeSchema[key] || { type: "string" }, runtimeValues, "runtime", {
      disabled: !runtimeEditable,
      declared: Object.prototype.hasOwnProperty.call(runtimeSchema, key),
    }))
    .join("");
  strategyModalStateSections.innerHTML = `
    ${buildInlineMachineStateSection(stateStore)}
    <div class="workspace-settings-group">
      <h3>Controls / UserState</h3>
      ${controlFields ? `<div class="grid two">${controlFields}</div>` : '<div class="muted">当前策略没有声明 ControlsSchema。</div>'}
      <details class="state-effective-details">
        <summary>查看有效值 JSON</summary>
        <pre class="state-effective-preview">${escapeHtml(stateEditorJson(stateStore.controls || stateStore.user || {}))}</pre>
      </details>
      <div class="state-editor-actions">
        <button type="button" class="mini ghost" data-inline-clear-user-state>Clear Controls Override</button>
      </div>
    </div>
    <div class="workspace-settings-group">
      <details class="runtime-state-details">
        <summary>
          <span>RuntimeState</span>
          <span class="state-editor-badge ${runtimeEditable ? "editable" : "locked"}">${runtimeEditable ? "Stop 时可编辑" : "运行中只读"}</span>
        </summary>
        <div class="state-editor-caption">策略运行记忆。字段来自当前策略代码 RuntimeStateSchema；运行中主要由策略写入。</div>
        ${runtimeFields ? `<div class="grid two">${runtimeFields}</div>` : '<div class="muted">当前策略还没有 RuntimeState。</div>'}
        <details class="state-effective-details">
          <summary>查看有效值 JSON</summary>
          <pre class="state-effective-preview">${escapeHtml(stateEditorJson(stateStore.runtime || {}))}</pre>
        </details>
      </details>
      <div class="state-editor-actions">
        <button type="button" class="mini ghost" data-inline-clear-runtime-state ${runtimeEditable ? "" : "disabled"}>Clear Runtime Override</button>
      </div>
    </div>
  `;
}

function collectInlineUserState() {
  const values = {};
  strategyModalStateSections?.querySelectorAll("[data-inline-user-state-key]").forEach((field) => {
    values[field.dataset.inlineUserStateKey] = field.type === "checkbox" ? field.checked : field.value;
  });
  return values;
}

function collectInlineMachineState() {
  const value = strategyModalStateSections?.querySelector("[data-inline-machine-state-key='state']")?.value || "auto";
  return { state: value };
}

function parseInlineRuntimeState() {
  const values = {};
  strategyModalStateSections?.querySelectorAll("[data-inline-runtime-state-key]").forEach((field) => {
    values[field.dataset.inlineRuntimeStateKey] = field.type === "checkbox" ? field.checked : field.value;
  });
  return values;
}

async function loadInlineStrategyStateStore(sid) {
  if (!sid) {
    renderInlineStrategyStateStore(null);
    return null;
  }
  const resp = await fetchJson(`/api/registry/strategies/${encodeURIComponent(sid)}/state-store`);
  renderInlineStrategyStateStore(resp.data || null);
  return resp.data || null;
}

async function saveInlineStrategyStateStore(sid) {
  if (!sid || !activeModalStateStore) return;
  await fetchJson(`/api/registry/strategies/${encodeURIComponent(sid)}/state-store/machine`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      values: collectInlineMachineState(),
      replace: false,
      reason: "strategy settings edit",
    }),
  });
  await fetchJson(`/api/registry/strategies/${encodeURIComponent(sid)}/state-store/user`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      values: collectInlineUserState(),
      replace: true,
      reason: "strategy settings edit",
    }),
  });
  const mode = activeModalStateStore.mode || activeStateMode || "Stop";
  if (mode === "Stop") {
    await fetchJson(`/api/registry/strategies/${encodeURIComponent(sid)}/state-store/runtime`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        values: parseInlineRuntimeState(),
        replace: true,
        reason: "strategy settings edit",
      }),
    });
  }
}

async function loadStrategyStateStoreIntoModal(sid) {
  setStateEditorMessage("Loading state store...");
  const resp = await fetchJson(`/api/registry/strategies/${encodeURIComponent(sid)}/state-store`);
  const data = resp.data || {};
  const mode = data.mode || "Stop";
  if (strategyUserStateJson) strategyUserStateJson.value = stateEditorJson(data.user_overrides || {});
  if (strategyRuntimeStateJson) strategyRuntimeStateJson.value = stateEditorJson(data.runtime_overrides || {});
  setStateEffectivePreview(strategyUserStateEffective, data.controls || data.user || {});
  setStateEffectivePreview(strategyRuntimeStateEffective, data.runtime || {});
  setRuntimeStateControls(mode);
  setStateEditorMessage(
    mode === "Stop"
      ? "Controls and RuntimeState are loaded. Editors save overrides only; effective values include strategy defaults."
      : "Controls overrides are editable. RuntimeState overrides are locked until the strategy is switched to Stop."
  );
}

async function openStrategyStateModal(row) {
  const sid = row?.strategy_id ?? row?.row_id;
  if (!sid || !strategyStateModal) return;
  activeStateStrategyId = String(sid);
  activeStateMode = strategyMode(row || {});
  if (strategyStateModalTitle) strategyStateModalTitle.textContent = `State Variables · ${strategyName(row || {})}`;
  if (strategyStateModalSubtitle) {
    strategyStateModalSubtitle.textContent = `Strategy ${activeStateStrategyId} · Mode ${activeStateMode}`;
  }
  if (strategyUserStateJson) strategyUserStateJson.value = "{}";
  if (strategyRuntimeStateJson) strategyRuntimeStateJson.value = "{}";
  setStateEffectivePreview(strategyUserStateEffective, {});
  setStateEffectivePreview(strategyRuntimeStateEffective, {});
  setRuntimeStateControls(activeStateMode);
  strategyStateModal.hidden = false;
  try {
    await loadStrategyStateStoreIntoModal(activeStateStrategyId);
  } catch (error) {
    setStateEditorMessage(`Load failed: ${error.message}`, "error");
  }
}

function closeStrategyStateModal() {
  if (strategyStateModal) strategyStateModal.hidden = true;
}

async function saveStrategyStateNamespace(namespace) {
  if (!activeStateStrategyId) return;
  const isRuntime = namespace === "runtime";
  const label = isRuntime ? "RuntimeState" : "Controls";
  const values = parseStateEditor(isRuntime ? strategyRuntimeStateJson : strategyUserStateJson, label);
  setStateEditorMessage(`Saving ${label} overrides...`);
  await fetchJson(`/api/registry/strategies/${encodeURIComponent(activeStateStrategyId)}/state-store/${namespace}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      values,
      replace: true,
      reason: "manual UI edit",
    }),
  });
  await loadStrategyStateStoreIntoModal(activeStateStrategyId);
  loadStrategies({ silent: true }).catch(() => {});
}

async function resetStrategyStateNamespaceFromUi(namespace) {
  if (!activeStateStrategyId) return;
  const label = namespace === "runtime" ? "RuntimeState" : "Controls";
  if (!confirm(`Clear ${label} overrides?`)) return;
  setStateEditorMessage(`Clearing ${label} overrides...`);
  await fetchJson(`/api/registry/strategies/${encodeURIComponent(activeStateStrategyId)}/state-store/${namespace}?reason=manual%20UI%20clear`, {
    method: "DELETE",
  });
  await loadStrategyStateStoreIntoModal(activeStateStrategyId);
  loadStrategies({ silent: true }).catch(() => {});
}

function normalizeStrategyLegs(legs) {
  const source = Array.isArray(legs) ? legs : [];
  const normalized = source.map((leg, index) => ({
    leg_index: Number.isFinite(Number(leg?.leg_index)) ? Number(leg.leg_index) : index,
    condition_id: String(leg?.condition_id || "").trim(),
    yes_token: leg?.yes_token || "",
    no_token: leg?.no_token || "",
    leg_kind: leg?.leg_kind || leg?.kind || "",
    asset_class: leg?.asset_class || "polymarket_binary",
    venue: leg?.venue || "polymarket",
    symbol: leg?.symbol || "",
    instrument_id: leg?.instrument_id || "",
    instrument_json: leg?.instrument_json || {},
    budget_cap: leg?.budget_cap ?? "",
    params_json: leg?.params_json || {},
  }));
  return normalized.length ? normalized : [{
    leg_index: 0,
    condition_id: "",
    asset_class: "polymarket_binary",
    venue: "polymarket",
  }];
}

function defaultStrategyLegSchemas() {
  return [{
    leg_index: 0,
    name: "Primary Polymarket",
    label: "Leg 1",
    purpose: "Primary trading market",
    leg_kind: "binary_market",
    asset_class: "polymarket_binary",
    venue: "polymarket",
    required: true,
    default: {},
  }];
}

function normalizeStrategyLegSchemas(raw) {
  const source = Array.isArray(raw) && raw.length ? raw : defaultStrategyLegSchemas();
  return source.map((schema, index) => ({
    leg_index: Number.isFinite(Number(schema?.leg_index)) ? Number(schema.leg_index) : index,
    name: String(schema?.name || schema?.label || `Leg ${index + 1}`),
    label: String(schema?.label || schema?.name || `Leg ${index + 1}`),
    purpose: String(schema?.purpose || schema?.description || ""),
    leg_kind: String(schema?.leg_kind || schema?.kind || ""),
    asset_class: String(schema?.asset_class || "polymarket_binary"),
    venue: String(schema?.venue || (schema?.asset_class === "polymarket_binary" ? "polymarket" : "")),
    symbol: String(schema?.symbol || "").toUpperCase(),
    required: schema?.required !== false,
    default: schema?.default && typeof schema.default === "object" ? schema.default : {},
    instrument_json: schema?.instrument_json && typeof schema.instrument_json === "object" ? schema.instrument_json : {},
    params_schema: schema?.params_schema && typeof schema.params_schema === "object" ? schema.params_schema : {},
  })).sort((a, b) => a.leg_index - b.leg_index);
}

function legSchemaForIndex(index) {
  return strategyLegSchemas[index] || defaultStrategyLegSchemas()[0];
}

function mergeLegWithSchema(leg, schema, index) {
  const defaults = schema?.default || {};
  const assetClass = leg?.asset_class || defaults.asset_class || schema?.asset_class || "polymarket_binary";
  const legKind = leg?.leg_kind || leg?.kind || defaults.leg_kind || schema?.leg_kind || "";
  return {
    ...defaults,
    ...(leg || {}),
    leg_index: index,
    leg_kind: legKind,
    asset_class: assetClass,
    venue: leg?.venue || defaults.venue || schema?.venue || (assetClass === "polymarket_binary" ? "polymarket" : ""),
    symbol: String(leg?.symbol || defaults.symbol || schema?.symbol || "").trim().toUpperCase(),
    condition_id: String(leg?.condition_id || defaults.condition_id || "").trim(),
    instrument_json: leg?.instrument_json || schema?.instrument_json || {},
    params_json: leg?.params_json || {},
  };
}

function syncStrategyLegDraftFromDom() {
  if (!strategyLegRows) return normalizeStrategyLegs(strategyLegDraft);
  const legs = strategyLegSchemas.map((schema, index) => {
    const row = strategyLegRows.querySelector(`[data-strategy-leg-row="${index}"]`);
    return mergeLegWithSchema({
      ...(strategyLegDraft[index] || {}),
      condition_id: String(row?.querySelector("[data-leg-condition-id]")?.value || "").trim(),
      venue: String(row?.querySelector("[data-leg-venue]")?.value || "").trim(),
      symbol: String(row?.querySelector("[data-leg-symbol]")?.value || "").trim().toUpperCase(),
      budget_cap: row?.querySelector("[data-leg-budget-cap]")?.value ?? (strategyLegDraft[index]?.budget_cap ?? ""),
    }, schema, index);
  });
  strategyLegDraft = normalizeStrategyLegs(legs);
  return strategyLegDraft;
}

function renderStrategyLegRows(legs = strategyLegDraft) {
  if (!strategyLegRows) return;
  strategyLegSchemas = normalizeStrategyLegSchemas(strategyLegSchemas);
  const existing = normalizeStrategyLegs(legs);
  strategyLegDraft = strategyLegSchemas.map((schema, index) => {
    const matched = existing.find((leg) => Number(leg.leg_index) === index) || existing[index] || {};
    return mergeLegWithSchema(matched, schema, index);
  });
  strategyLegRows.innerHTML = strategyLegDraft.map((leg, index) => {
    const schema = legSchemaForIndex(index);
    const isPoly = leg.asset_class === "polymarket_binary";
    const title = `${schema.label || `Leg ${index + 1}`} · ${leg.leg_kind || leg.asset_class}`;
    const purpose = schema.purpose ? `<div class="strategy-leg-purpose">${escapeHtml(schema.purpose)}</div>` : "";
    const budget = `<input data-leg-budget-cap data-leg-index="${index}" type="number" step="any" placeholder="Budget cap" value="${escapeHtml(leg.budget_cap ?? "")}">`;
    const body = isPoly
      ? `<div class="condition-id-group">
          <input data-leg-condition-id data-leg-index="${index}" ${index === 0 ? 'id="conditionIdInput" name="condition_id"' : ""} placeholder="Condition ID for ${escapeHtml(schema.label || `Leg ${index + 1}`)}" value="${escapeHtml(leg.condition_id || "")}">
          <button type="button" class="mini" data-leg-pick="${index}">自选</button>
          ${budget}
        </div>`
      : `<div class="strategy-leg-instrument-grid">
          <input data-leg-venue data-leg-index="${index}" placeholder="Venue" value="${escapeHtml(leg.venue || "")}">
          <input data-leg-symbol data-leg-index="${index}" placeholder="Symbol" value="${escapeHtml(leg.symbol || "")}">
          ${budget}
        </div>`;
    return `
      <div class="strategy-leg-row" data-strategy-leg-row="${index}" data-leg-asset-class="${escapeHtml(leg.asset_class)}">
        <div class="strategy-leg-title">${escapeHtml(title)}${schema.required ? "" : " (optional)"}</div>
        ${purpose}
        ${body}
      </div>
    `;
  }).join("");
  activeConditionLegIndex = Math.min(activeConditionLegIndex, strategyLegDraft.length - 1);
}

function collectStrategyLegsForSave() {
  return syncStrategyLegDraftFromDom()
    .map((leg, index) => ({ ...leg, leg_index: index, condition_id: String(leg.condition_id || "").trim() }));
}

function primaryStrategyConditionId() {
  return collectStrategyLegsForSave()[0]?.condition_id || "";
}

function openStrategyModal() {
  activeAgentStrategyEditContext = null;
  strategyModalTitle.textContent = "\u65b0\u589e\u7b56\u7565";
  setStrategyModalSubmitText("保存");
  strategyModalForm.reset();
  strategyModalForm.querySelector('[name="strategy_id"]').value = "";
  _setField("mode", "Stop");
  activeStateMode = "Stop";
  strategyModalDirty = false;
  activeConditionLegIndex = 0;
  strategyLegSchemas = defaultStrategyLegSchemas();
  renderStrategyLegRows([{ leg_index: 0, condition_id: "", asset_class: "polymarket_binary", venue: "polymarket" }]);
  const dyn = document.getElementById("strategyDynamicInputs");
  if (dyn) dyn.innerHTML = "";
  renderInlineStrategyStateStore(null);
  const pasteText = document.getElementById("strategyParamPasteText");
  if (pasteText) pasteText.value = "";
  if (strategyDynamicMessage) strategyDynamicMessage.textContent = "";
  fetchJson("/api/strategy-codes").then((resp) => {
    const codes = resp.data || [];
    strategyCodeSelect.innerHTML = '<option value="">-- \u9009\u62e9 --</option>' +
      codes.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
  }).catch(() => {});
  strategyModal.hidden = false;
}

function applyDashboardParamPaste() {
  const helper = window.StrategyParamPaste;
  const textArea = document.getElementById("strategyParamPasteText");
  if (!helper || !textArea) return;
  const params = helper.parseParamText(textArea.value);
  const fields = Array.from(strategyModalForm.querySelectorAll("[data-strategy-param], [name='strategy_bankroll']"));
  const result = helper.applyParamsToFields(params, fields, (field) => {
    if (field.dataset.strategyParam) return field.dataset.strategyParam;
    return field.name || "";
  });
  strategyModalDirty = true;
  if (strategyDynamicMessage) {
    if (!Object.keys(params).length) {
      strategyDynamicMessage.textContent = "没有识别到可填入的参数。支持 JSON 或 key = value。";
    } else if (result.matched.length) {
      const suffix = result.unmatched.length ? `，${result.unmatched.length} 个字段未匹配` : "";
      strategyDynamicMessage.textContent = `已填入 ${result.matched.length} 个参数${suffix}，保存后生效。`;
    } else {
      strategyDynamicMessage.textContent = "已解析参数，但当前策略没有匹配字段。";
    }
  }
}

// 编辑已有策略：预填所有字段（含动态 input_json）
async function openEditModal(monitorRow) {
  activeAgentStrategyEditContext = null;
  strategyModalTitle.textContent = "\u8bbe\u7f6e\u53c2\u6570";
  setStrategyModalSubmitText("保存");
  strategyModalForm.reset();
  const pasteText = document.getElementById("strategyParamPasteText");
  if (pasteText) pasteText.value = "";
  strategyModalDirty = false;

  // 并行拉 registry 完整数据 + 策略代码列表
  const sid = monitorRow.strategy_id ?? monitorRow.row_id;
  const [codesResp, regResp, stateResp] = await Promise.allSettled([
    fetchJson("/api/strategy-codes"),
    sid ? fetchJson(`/api/registry/strategies/${sid}`) : Promise.resolve(null),
    sid
      ? fetchJson(`/api/registry/strategies/${sid}/state-store`).catch((error) => ({ ok: false, error: error.message }))
      : Promise.resolve(null),
  ]);

  const codes = codesResp.status === "fulfilled" ? (codesResp.value?.data || []) : [];
  // registry 数据优先，fallback 到监控行
  const reg = (regResp.status === "fulfilled" && regResp.value?.data) ? regResp.value.data : null;
  const r = reg || monitorRow;
  const stateStore = (stateResp.status === "fulfilled" && stateResp.value?.data) ? stateResp.value.data : null;

  strategyCodeSelect.innerHTML = '<option value="">-- \u9009\u62e9 --</option>' +
    codes.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");

  strategyModalForm.querySelector('[name="strategy_id"]').value = sid ?? "";
  _setField("strategy_name", r.strategy_name || monitorRow.strategy || monitorRow.display_name || "");
  // condition_id 优先取 legs[0]，fallback 到监控行
  const legCid = (r.legs && r.legs[0]) ? (r.legs[0].condition_id || "") : "";
  renderStrategyLegRows((r.legs && r.legs.length) ? r.legs : [{
    leg_index: 0,
    condition_id: legCid || r.condition_id || monitorRow.condition_id || "",
    asset_class: "polymarket_binary",
    venue: "polymarket",
  }]);
  _setField("condition_id", legCid || r.condition_id || monitorRow.condition_id || "");
  _setField("strategy_bankroll", r.strategy_bankroll ?? monitorRow.strategy_bankroll ?? "");
  _setField("mode", strategyMode(r || monitorRow || {}));
  activeStateMode = strategyMode(r || monitorRow || {});
  renderInlineStrategyStateStore(stateStore);

  const codeVal = r.strategy_code || "";
  if (codeVal) {
    strategyCodeSelect.value = codeVal;
    await _loadDynamicInputs(codeVal, r.input_json || {}, r.legs || []);
  } else {
    const dyn = document.getElementById("strategyDynamicInputs");
    if (dyn) dyn.innerHTML = "";
  }

  strategyModal.hidden = false;
}

function setStrategyModalSubmitText(text) {
  const submit = strategyModalForm?.querySelector('[type="submit"]');
  if (submit) submit.textContent = text || "保存";
}

function agentSnapshotToStrategyLegs(snapshot = {}) {
  const markets = Array.isArray(snapshot.markets) ? snapshot.markets : [];
  return markets.length ? markets.map((market, index) => ({
    leg_index: index,
    condition_id: market.condition_id || "",
    yes_token: market.yes_token || "",
    no_token: market.no_token || "",
    leg_kind: "binary_market",
    asset_class: "polymarket_binary",
    venue: market.venue || "polymarket",
    symbol: market.symbol || "",
    instrument_id: market.instrument_id || market.condition_id || "",
    instrument_json: {
      question: market.question || market.title || "",
      outcome: market.outcome || "YES",
      url: market.url || "",
    },
    budget_cap: market.max_exposure_usdc ?? "",
    params_json: {
      outcome: market.outcome || "YES",
      action: market.action || "buy",
      max_entry_price: market.max_entry_price ?? market.best_ask ?? "",
      max_exposure_usdc: market.max_exposure_usdc ?? "",
    },
  })) : [{
    leg_index: 0,
    condition_id: "",
    asset_class: "polymarket_binary",
    venue: "polymarket",
  }];
}

async function openAgentApprovalStrategyModal(approval) {
  const snapshot = approval?.snapshot?.snapshot || approval?.draft?.draft || {};
  activeAgentStrategyEditContext = {
    type: "approval",
    approvalId: approval?.approval_id || "",
    approval,
    snapshot,
  };
  strategyModalTitle.textContent = "设置参数";
  setStrategyModalSubmitText("保存草案");
  strategyModalForm.reset();
  const pasteText = document.getElementById("strategyParamPasteText");
  if (pasteText) pasteText.value = "";
  strategyModalDirty = false;
  activeConditionLegIndex = 0;

  const codesResp = await fetchJson("/api/strategy-codes");
  const codes = codesResp.data || [];
  strategyCodeSelect.innerHTML = '<option value="">-- 选择 --</option>' +
    codes.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");

  strategyModalForm.querySelector('[name="strategy_id"]').value = "";
  _setField("strategy_name", snapshot.name || agentApprovalTitle(approval));
  _setField("strategy_code", snapshot.strategy_code || "");
  _setField("mode", "Stop");
  _setField("strategy_bankroll", snapshot.budget?.max_total_usdc ?? "");
  const legs = agentSnapshotToStrategyLegs(snapshot);
  renderStrategyLegRows(legs);
  _setField("condition_id", legs[0]?.condition_id || "");
  renderInlineStrategyStateStore(null);
  if (snapshot.strategy_code) {
    strategyCodeSelect.value = snapshot.strategy_code;
    await _loadDynamicInputs(snapshot.strategy_code, snapshot.params || {}, legs);
  } else {
    const dyn = document.getElementById("strategyDynamicInputs");
    if (dyn) dyn.innerHTML = "";
  }
  if (strategyDynamicMessage) {
    strategyDynamicMessage.textContent = "正在编辑 Agent 待确认草案；保存后会重新风控和模拟，仍需人工批准。";
  }
  strategyModal.hidden = false;
}

function buildAgentApprovalDraftPayloadFromStrategyModal() {
  const body = buildStrategyDraftPayload();
  return {
    actor_type: "human",
    actor_id: "local_user",
    reason: "human edited pending approval parameters",
    strategy_name: body.strategy_name,
    strategy_code: body.strategy_code,
    mode: body.mode,
    strategy_bankroll: body.strategy_bankroll,
    input_json: body.input_json,
    legs: body.legs,
    condition_id: body.condition_id,
  };
}

function _setField(name, value) {
  const el = strategyModalForm.querySelector(`[name="${name}"]`);
  if (el) el.value = value ?? "";
}

const STRATEGY_DEADLINE_PARAM = {
  name: "Enddate",
  kind: "String",
  required: false,
  default: "",
  description: "Strategy deadline. Auto-filled from the selected market end date; edit it if the market date is wrong.",
};
const STRATEGY_DEADLINE_ALIASES = new Set(["enddate", "endtime", "l0endtime"]);

function ensureStrategyDeadlineInput(inputs) {
  const list = Array.isArray(inputs) ? inputs : [];
  const hasDeadline = list.some((inp) => STRATEGY_DEADLINE_ALIASES.has(normalizeStrategyParamKey(inp?.name)));
  return hasDeadline ? list : [...list, STRATEGY_DEADLINE_PARAM];
}

function findStrategyDeadlineField() {
  const fields = Array.from(strategyModalForm?.querySelectorAll("[data-strategy-param]") || []);
  return fields.find((field) => STRATEGY_DEADLINE_ALIASES.has(normalizeStrategyParamKey(field.dataset.strategyParam)));
}

function readMarketEndDate(market) {
  return String(
    market?.end_date
    || market?.endDate
    || market?.raw?.endDate
    || market?.raw?.umaEndDate
    || ""
  ).trim();
}

function fillStrategyDeadlineFromMarket(market, { force = false } = {}) {
  const field = findStrategyDeadlineField();
  const endDate = readMarketEndDate(market);
  if (!field || !endDate) return false;
  if (!force && String(field.value ?? "").trim()) return false;
  field.value = endDate;
  return true;
}

function selectedMarketFromResolveResponse(resp) {
  const data = resp?.data;
  if (Array.isArray(data)) return data[0] || null;
  return data?.selected || data?.results?.[0] || null;
}

async function _loadDynamicInputs(code, prefill = {}, legsPrefill = null) {
  const dyn = document.getElementById("strategyDynamicInputs");
  if (!dyn) return;
  try {
    const [inputsResp, schemasResp] = await Promise.all([
      fetchJson(`/api/strategy-codes/${encodeURIComponent(code)}/inputs`),
      fetchJson(`/api/strategy-codes/${encodeURIComponent(code)}/schemas`),
    ]);
    const inputs = ensureStrategyDeadlineInput(inputsResp.data || []);
    strategyLegSchemas = normalizeStrategyLegSchemas(schemasResp.data?.legs || []);
    renderStrategyLegRows(legsPrefill || strategyLegDraft);
    dyn.innerHTML = inputs.map((inp) => {
      const req = inp.required ? " required" : "";
      const rawValue = prefill[inp.name] !== undefined ? prefill[inp.name] : (inp.default ?? "");
      const description = buildStrategyParamDescription(inp);
      const help = description
        ? `<span class="strategy-param-help" tabindex="0" title="${escapeHtml(description)}" aria-label="${escapeHtml(description)}">?</span>`
        : "";
      return `
        <label class="strategy-param-field" title="${escapeHtml(description)}">
          <span class="strategy-param-label">${escapeHtml(inp.name)}${inp.required ? '<span class="strategy-param-required">*</span>' : ""}${help}</span>
          <span class="strategy-param-input-row">
            ${renderStrategyParamControl(inp, rawValue, req)}
            <button type="button" class="strategy-param-autofill" data-strategy-param-autofill="${escapeHtml(inp.name)}" title="UseData" aria-label="UseData" hidden>↻</button>
          </span>
        </label>
      `;
    }).join("");
    if (strategyDynamicMessage) {
      strategyDynamicMessage.textContent = inputs.length
        ? "默认参数已从策略代码同步；悬停 ? 查看含义，↻ 可从 UseData 填入当前值。"
        : "该策略代码未声明可编辑参数。";
    }
    refreshStrategyParamUseData({ fillEmpty: true }).catch((error) => {
      console.log("[strategy-autofill] background refresh failed", { error });
    });
  } catch {
    dyn.innerHTML = "";
    if (strategyDynamicMessage) strategyDynamicMessage.textContent = "无法读取策略参数。";
  }
}

function strategyInputType(inp) {
  const kind = String(inp?.kind || "").trim().toLowerCase();
  if (["num", "number", "float", "int", "integer"].includes(kind)) return "number";
  if (["bool", "boolean"].includes(kind)) return "checkbox";
  if (["select", "enum"].includes(kind)) return "select";
  return "text";
}

function strategyBoolValue(value) {
  return ["1", "true", "yes", "y", "on"].includes(String(value ?? "").trim().toLowerCase());
}

function renderStrategyParamControl(inp, rawValue, req) {
  const name = escapeHtml(inp.name);
  const type = strategyInputType(inp);
  if (type === "select" && Array.isArray(inp.values) && inp.values.length) {
    const current = rawValue !== undefined && rawValue !== null ? String(rawValue) : "";
    return `<select name="_inp_${name}" data-strategy-param="${name}"${req}>
      ${inp.values.map((value) => {
        const text = String(value);
        const selected = text === current ? " selected" : "";
        return `<option value="${escapeHtml(text)}"${selected}>${escapeHtml(text)}</option>`;
      }).join("")}
    </select>`;
  }
  if (type === "checkbox") {
    return `<input name="_inp_${name}" data-strategy-param="${name}" type="checkbox" value="true"${strategyBoolValue(rawValue) ? " checked" : ""}>`;
  }
  const step = type === "number" ? ' step="any"' : "";
  const value = rawValue !== undefined && rawValue !== null ? ` value="${escapeHtml(String(rawValue))}"` : "";
  return `<input name="_inp_${name}" data-strategy-param="${name}" type="${type}"${step}${req}${value}>`;
}

function buildStrategyParamDescription(inp) {
  const parts = [];
  if (inp?.description) parts.push(String(inp.description));
  if (inp?.required) parts.push("必填参数");
  if (inp?.default !== undefined && inp.default !== null && String(inp.default) !== "") {
    parts.push(`默认值: ${inp.default}`);
  }
  parts.push("说明来自策略代码 FunctionIntroduction");
  return parts.join("\n");
}

function normalizeStrategyParamKey(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function strategyUseDataCandidates(paramName) {
  const key = String(paramName || "").trim();
  const normalized = normalizeStrategyParamKey(key);
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

function findStrategyUseDataValue(useData, paramName) {
  if (!useData || typeof useData !== "object") {
    console.log("[strategy-autofill] invalid UseData", { paramName, useData });
    return null;
  }
  const candidates = strategyUseDataCandidates(paramName);
  console.log("[strategy-autofill] candidates", { paramName, candidates, useDataKeys: Object.keys(useData) });
  for (const key of candidates) {
    if (Object.prototype.hasOwnProperty.call(useData, key) && isUsableStrategyUseDataValue(useData[key])) {
      console.log("[strategy-autofill] matched direct key", { paramName, key, value: useData[key] });
      return { key, value: useData[key] };
    }
  }
  const normalized = normalizeStrategyParamKey(paramName);
  const matchKey = Object.keys(useData).find((key) => normalizeStrategyParamKey(key) === normalized && isUsableStrategyUseDataValue(useData[key]));
  console.log("[strategy-autofill] normalized match", { paramName, normalized, matchKey, value: matchKey ? useData[matchKey] : undefined });
  return matchKey ? { key: matchKey, value: useData[matchKey] } : null;
}

function buildStrategyDraftPayload() {
  const fd = new FormData(strategyModalForm);
  const body = {};
  const inputJson = {};
  for (const [k, v] of fd.entries()) {
    if (k.startsWith("_inp_")) {
      continue;
    } else if (k !== "strategy_id") {
      body[k] = v;
    }
  }
  strategyModalForm.querySelectorAll("[data-strategy-param]").forEach((field) => {
    const key = field.dataset.strategyParam;
    if (!key) return;
    if (field.type === "checkbox") {
      inputJson[key] = field.checked ? "true" : "false";
      return;
    }
    const value = String(field.value ?? "").trim();
    if (value !== "") inputJson[key] = value;
  });
  body.input_json = inputJson;
  body.legs = collectStrategyLegsForSave();
  body.condition_id = primaryStrategyConditionId();
  console.log("[strategy-autofill] draft payload", body);
  return body;
}

function isUsableStrategyUseDataValue(value) {
  return value !== undefined && value !== null && String(value).trim() !== "";
}

let strategyUseDataRefreshTimer = null;

function scheduleStrategyParamUseDataRefresh() {
  if (strategyUseDataRefreshTimer) clearTimeout(strategyUseDataRefreshTimer);
  strategyUseDataRefreshTimer = setTimeout(() => {
    refreshStrategyParamUseData({ fillEmpty: true });
  }, 300);
}

async function loadStrategyModalUseData() {
  const sid = strategyModalForm?.querySelector('[name="strategy_id"]')?.value;
  const conditionId = primaryStrategyConditionId();
  if (!sid && !conditionId) {
    console.log("[strategy-autofill] no UseData source", { sid, conditionId });
    if (strategyDynamicMessage) {
      strategyDynamicMessage.textContent = "请先填写或自选 Condition ID；生成 UseData 后会自动填入 start_day。";
    }
    return null;
  }
  const url = sid
    ? `/api/polymarket/strategies/${encodeURIComponent(sid)}/usedata?live_orderbook=0`
    : "/api/polymarket/strategies/usedata/draft?live_orderbook=0";
  const options = sid
    ? undefined
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildStrategyDraftPayload()),
      };
  console.log("[strategy-autofill] load UseData", { url, mode: sid ? "saved" : "draft" });
  const payload = await fetchJson(url, options);
  const useData = payload.data?.data || {};
  console.log("[strategy-autofill] loaded UseData", { payload, useData });
  return useData;
}

async function refreshStrategyParamUseData({ fillEmpty = false } = {}) {
  const fields = Array.from(strategyModalForm.querySelectorAll("[data-strategy-param]"));
  const buttons = Array.from(strategyModalForm.querySelectorAll("[data-strategy-param-autofill]"));
  buttons.forEach((button) => {
    button.hidden = true;
    button.disabled = true;
  });
  if (!fields.length) return;
  try {
    const useData = await loadStrategyModalUseData();
    if (!useData) return;
    let visibleCount = 0;
    let filledCount = 0;
    for (const field of fields) {
      const paramName = field.dataset.strategyParam || "";
      const button = buttons.find((item) => item.dataset.strategyParamAutofill === paramName);
      const match = findStrategyUseDataValue(useData, paramName);
      if (!match || !isUsableStrategyUseDataValue(match.value)) {
        if (button) button.hidden = true;
        continue;
      }
      visibleCount += 1;
      if (button) {
        button.hidden = false;
        button.disabled = false;
        button.dataset.strategyUseDataKey = match.key;
      }
      if (fillEmpty && field.type !== "checkbox" && String(field.value ?? "").trim() === "") {
        field.value = String(match.value);
        filledCount += 1;
      }
    }
    console.log("[strategy-autofill] refreshed fields", { visibleCount, filledCount, fillEmpty });
    if (strategyDynamicMessage && filledCount > 0) {
      strategyDynamicMessage.textContent = `已从 UseData 自动填入 ${filledCount} 个参数，保存后生效。`;
    }
  } catch (error) {
    console.log("[strategy-autofill] refresh error", { error });
  }
}

async function autofillDashboardParam(button) {
  const paramName = button?.dataset?.strategyParamAutofill || "";
  const sid = strategyModalForm?.querySelector('[name="strategy_id"]')?.value;
  console.log("[strategy-autofill] click", { paramName, sid, button });
  if (false) {
    console.log("[strategy-autofill] skipped: missing strategy_id", { paramName });
    if (strategyDynamicMessage) strategyDynamicMessage.textContent = "新增策略尚未生成 UseData，请先保存后再从当前运行数据填入。";
    return;
  }
  const input = Array.from(strategyModalForm.querySelectorAll("[data-strategy-param]")).find((item) => item.dataset.strategyParam === paramName);
  if (!input) {
    console.log("[strategy-autofill] skipped: input not found", { paramName });
    return;
  }
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "...";
  if (strategyDynamicMessage) strategyDynamicMessage.textContent = "正在从 UseData 读取参数...";
  try {
    const useData = await loadStrategyModalUseData();
    console.log("[strategy-autofill] response", { paramName, sid, useData });
    const match = findStrategyUseDataValue(useData, paramName);
    if (!match) {
      console.log("[strategy-autofill] no match", { paramName, sid, useData });
      if (strategyDynamicMessage) strategyDynamicMessage.textContent = `UseData 中没有找到 ${paramName} 的可用字段。`;
      return;
    }
    if (input.type === "checkbox") {
      input.checked = strategyBoolValue(match.value);
    } else {
      input.value = match.value == null ? "" : String(match.value);
    }
    console.log("[strategy-autofill] filled", { paramName, key: match.key, value: match.value, inputValue: input.value });
    if (strategyDynamicMessage) strategyDynamicMessage.textContent = `已从 UseData.${match.key} 填入 ${paramName}，保存后生效。`;
  } catch (error) {
    console.log("[strategy-autofill] error", { paramName, sid, error });
    if (strategyDynamicMessage) strategyDynamicMessage.textContent = `UseData 读取失败: ${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = previous || "↻";
  }
}

function closeStrategyModal() {
  if (strategyModalDirty && !confirm("当前策略表单还没有保存，确认关闭吗？")) {
    return;
  }
  strategyModal.hidden = true;
  strategyModalDirty = false;
  activeAgentStrategyEditContext = null;
  setStrategyModalSubmitText("保存");
  if (conditionPicker) conditionPicker.hidden = true;
}

function closeStrategyModalAfterSave() {
  strategyModalDirty = false;
  strategyModal.hidden = true;
  activeAgentStrategyEditContext = null;
  setStrategyModalSubmitText("保存");
  if (conditionPicker) conditionPicker.hidden = true;
}

// --- Condition ID picker (from watchlist) ---
const conditionPickBtn = document.getElementById("conditionPickBtn");
const conditionPicker = document.getElementById("conditionPicker");
const conditionIdInput = document.getElementById("conditionIdInput");
conditionPickBtn?.addEventListener("click", () => {
  if (!conditionPicker) return;
  if (!conditionPicker.hidden) { conditionPicker.hidden = true; return; }
  const watchlist = marketUi.loadWatchlist();
  if (!watchlist.length) {
    conditionPicker.innerHTML = '<div style="padding:8px;color:var(--muted)">自选列表为空，请先在市场查询中加入自选</div>';
    conditionPicker.hidden = false;
    return;
  }
  conditionPicker.innerHTML = watchlist.map(m => {
    const cid = m.condition_id || "";
    const q = m.question || m.label || "";
    const endDate = readMarketEndDate(m);
    return `<div class="condition-picker-item" data-cid="${escapeHtml(cid)}" data-question="${escapeHtml(q)}" data-end-date="${escapeHtml(endDate)}">`
      + `<div>${escapeHtml(q.slice(0, 60) || cid.slice(0, 24) + "...")}</div>`
      + (cid ? `<div class="picker-question">${escapeHtml(cid.slice(0, 30))}...</div>` : "")
      + `</div>`;
  }).join("");
  conditionPicker.hidden = false;
});
conditionPicker?.addEventListener("click", (e) => {
  const item = e.target.closest(".condition-picker-item");
  if (!item) return;
  conditionIdInput.value = item.dataset.cid;
  _fillStrategyNameFromQuestion(item.dataset.question);
  fillStrategyDeadlineFromMarket({ end_date: item.dataset.endDate });
  conditionPicker.hidden = true;
  refreshStrategyParamUseData({ fillEmpty: true });
});

// 手动输入 condition_id 后 blur 时自动查询并填充策略名称
conditionIdInput?.addEventListener("input", () => {
  scheduleStrategyParamUseDataRefresh();
});

conditionIdInput?.addEventListener("blur", async () => {
  const cid = conditionIdInput.value.trim();
  if (!cid) return;
  const nameInput = strategyModalForm?.querySelector('[name="strategy_name"]');
  if (nameInput?.value.trim()) refreshStrategyParamUseData({ fillEmpty: true });
  if (nameInput?.value.trim()) return; // 已有名称不覆盖
  try {
    const resp = await fetchJson(`/api/polymarket/markets/resolve?condition_id=${encodeURIComponent(cid)}&limit=1`);
    const item = selectedMarketFromResolveResponse(resp);
    if (item?.question) _fillStrategyNameFromQuestion(item.question);
    fillStrategyDeadlineFromMarket(item);
  } catch {}
  refreshStrategyParamUseData({ fillEmpty: true });
});

function _fillStrategyNameFromQuestion(question) {
  if (!question) return;
  const nameInput = strategyModalForm?.querySelector('[name="strategy_name"]');
  if (nameInput && !nameInput.value.trim()) nameInput.value = question;
}
document.addEventListener("click", (e) => {
  if (conditionPicker && !conditionPicker.hidden && !conditionPicker.contains(e.target) && e.target !== conditionPickBtn) {
    conditionPicker.hidden = true;
  }
});

function getActiveLegConditionInput() {
  return strategyLegRows?.querySelector(`[data-leg-condition-id][data-leg-index="${activeConditionLegIndex}"]`)
    || strategyLegRows?.querySelector("[data-leg-condition-id]")
    || document.getElementById("conditionIdInput");
}

function openLegConditionPicker(index) {
  if (!conditionPicker) return;
  activeConditionLegIndex = Number.isFinite(Number(index)) ? Number(index) : 0;
  if (!conditionPicker.hidden) {
    conditionPicker.hidden = true;
    return;
  }
  const watchlist = marketUi.loadWatchlist();
  if (!watchlist.length) {
    conditionPicker.innerHTML = '<div style="padding:8px;color:var(--muted)">自选列表为空，请先在市场查询中加入自选</div>';
    conditionPicker.hidden = false;
    return;
  }
  conditionPicker.innerHTML = watchlist.map((m) => {
    const cid = m.condition_id || "";
    const q = m.question || m.label || "";
    const endDate = readMarketEndDate(m);
    return `<div class="condition-picker-item" data-cid="${escapeHtml(cid)}" data-question="${escapeHtml(q)}" data-end-date="${escapeHtml(endDate)}">`
      + `<div>${escapeHtml(q.slice(0, 60) || cid.slice(0, 24) + "...")}</div>`
      + (cid ? `<div class="picker-question">Leg ${activeConditionLegIndex + 1} · ${escapeHtml(cid.slice(0, 30))}...</div>` : "")
      + `</div>`;
  }).join("");
  conditionPicker.hidden = false;
}

strategyLegRows?.addEventListener("click", (e) => {
  const pick = e.target.closest("[data-leg-pick]");
  if (pick) {
    e.preventDefault();
    e.stopPropagation();
    openLegConditionPicker(pick.dataset.legPick);
    return;
  }
  const remove = e.target.closest("[data-leg-remove]");
  if (!remove) return;
  e.preventDefault();
});

strategyLegRows?.addEventListener("input", (e) => {
  const input = e.target.closest("[data-leg-condition-id]");
  if (!input) return;
  activeConditionLegIndex = Number(input.dataset.legIndex || 0);
  syncStrategyLegDraftFromDom();
  scheduleStrategyParamUseDataRefresh();
});

strategyLegRows?.addEventListener("blur", async (e) => {
  const input = e.target.closest("[data-leg-condition-id]");
  if (!input) return;
  activeConditionLegIndex = Number(input.dataset.legIndex || 0);
  const cid = input.value.trim();
  if (!cid) return;
  const nameInput = strategyModalForm?.querySelector('[name="strategy_name"]');
  if (nameInput?.value.trim()) {
    refreshStrategyParamUseData({ fillEmpty: true });
    return;
  }
  try {
    const resp = await fetchJson(`/api/polymarket/markets/resolve?condition_id=${encodeURIComponent(cid)}&limit=1`);
    const item = selectedMarketFromResolveResponse(resp);
    if (item?.question) _fillStrategyNameFromQuestion(item.question);
    fillStrategyDeadlineFromMarket(item);
  } catch {}
  refreshStrategyParamUseData({ fillEmpty: true });
}, true);

conditionPicker?.addEventListener("click", (e) => {
  const item = e.target.closest(".condition-picker-item");
  if (!item || !strategyLegRows) return;
  const input = getActiveLegConditionInput();
  if (input) input.value = item.dataset.cid || "";
  syncStrategyLegDraftFromDom();
  _fillStrategyNameFromQuestion(item.dataset.question);
  fillStrategyDeadlineFromMarket({ end_date: item.dataset.endDate });
  conditionPicker.hidden = true;
  refreshStrategyParamUseData({ fillEmpty: true });
});

strategyCodeSelect?.addEventListener("change", async () => {
  const code = strategyCodeSelect.value;
  if (!code) {
    const dyn = document.getElementById("strategyDynamicInputs");
    if (dyn) dyn.innerHTML = "";
    if (strategyDynamicMessage) strategyDynamicMessage.textContent = "";
    strategyLegSchemas = defaultStrategyLegSchemas();
    renderStrategyLegRows(strategyLegDraft);
    return;
  }
  await _loadDynamicInputs(code, {}, syncStrategyLegDraftFromDom());
  scheduleStrategyParamUseDataRefresh();
});

if (addStrategyBtn) {
  addStrategyBtn.addEventListener("click", openStrategyModal);
}
document.getElementById("strategyModalClose")?.addEventListener("click", closeStrategyModal);
document.getElementById("strategyModalCancel")?.addEventListener("click", closeStrategyModal);
strategyModal?.addEventListener("click", (e) => {
  if (e.target === strategyModal) {
    e.preventDefault();
    if (conditionPicker) conditionPicker.hidden = true;
  }
});

document.getElementById("strategyStateModalClose")?.addEventListener("click", closeStrategyStateModal);
strategyStateModal?.addEventListener("click", (e) => {
  if (e.target === strategyStateModal) e.preventDefault();
});
document.getElementById("strategyParamPasteApply")?.addEventListener("click", (e) => {
  e.preventDefault();
  applyDashboardParamPaste();
});
strategyUserStateSave?.addEventListener("click", () => {
  saveStrategyStateNamespace("user").catch((error) => setStateEditorMessage(`Save failed: ${error.message}`, "error"));
});
strategyRuntimeStateSave?.addEventListener("click", () => {
  saveStrategyStateNamespace("runtime").catch((error) => setStateEditorMessage(`Save failed: ${error.message}`, "error"));
});
strategyUserStateReset?.addEventListener("click", () => {
  resetStrategyStateNamespaceFromUi("user").catch((error) => setStateEditorMessage(`Clear failed: ${error.message}`, "error"));
});
strategyRuntimeStateReset?.addEventListener("click", () => {
  resetStrategyStateNamespaceFromUi("runtime").catch((error) => setStateEditorMessage(`Clear failed: ${error.message}`, "error"));
});

strategyModalForm?.addEventListener("click", (e) => {
  const clearUser = e.target.closest("[data-inline-clear-user-state]");
  if (clearUser) {
    e.preventDefault();
    const sid = strategyModalForm?.querySelector('[name="strategy_id"]')?.value;
    if (!sid) return;
    fetchJson(`/api/registry/strategies/${encodeURIComponent(sid)}/state-store/user?reason=strategy%20settings%20clear`, { method: "DELETE" })
      .then(() => loadInlineStrategyStateStore(sid))
      .catch((error) => { if (strategyDynamicMessage) strategyDynamicMessage.textContent = `Clear Controls failed: ${error.message}`; });
    return;
  }
  const clearRuntime = e.target.closest("[data-inline-clear-runtime-state]");
  if (clearRuntime) {
    e.preventDefault();
    const sid = strategyModalForm?.querySelector('[name="strategy_id"]')?.value;
    if (!sid) return;
    fetchJson(`/api/registry/strategies/${encodeURIComponent(sid)}/state-store/runtime?reason=strategy%20settings%20clear`, { method: "DELETE" })
      .then(() => loadInlineStrategyStateStore(sid))
      .catch((error) => { if (strategyDynamicMessage) strategyDynamicMessage.textContent = `Clear RuntimeState failed: ${error.message}`; });
    return;
  }
  const button = e.target.closest("[data-strategy-param-autofill]");
  if (!button) return;
  e.preventDefault();
  autofillDashboardParam(button);
});

strategyModalForm?.addEventListener("input", () => {
  if (!strategyModal?.hidden) strategyModalDirty = true;
});

strategyModalForm?.addEventListener("change", () => {
  if (!strategyModal?.hidden) strategyModalDirty = true;
});

strategyModalForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const sid = strategyModalForm?.querySelector('[name="strategy_id"]')?.value;
  const body = buildStrategyDraftPayload();
  const submitBtn = strategyModalForm.querySelector('[type="submit"]');
  if (submitBtn) submitBtn.disabled = true;
  try {
    if (activeAgentStrategyEditContext?.type === "approval") {
      const approvalId = activeAgentStrategyEditContext.approvalId;
      await fetchJson(`/api/agent/approvals/${encodeURIComponent(approvalId)}/draft`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildAgentApprovalDraftPayloadFromStrategyModal()),
      });
      closeStrategyModalAfterSave();
      await loadAgentDashboard({ silent: true });
    } else if (sid) {
      await fetchJson(`/api/registry/strategies/${sid}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await saveInlineStrategyStateStore(sid);
    } else {
      await fetchJson("/api/registry/strategies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }
    closeStrategyModalAfterSave();
    loadStrategies();
  } catch (err) {
    alert("保存失败: " + err.message);
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
});

// --- Strategy table watchlist toggle ---
strategyTable.addEventListener("click", (e) => {
  const btn = e.target.closest(".stg-watch-btn");
  if (!btn) return;
  try {
    const market = JSON.parse(btn.dataset.stgWatch);
    const result = marketUi.toggleWatchlist(market);
    btn.textContent = result.active ? "\u2605" : "\u2606";
    btn.title = result.active ? "取消自选" : "加入自选";
  } catch {}
});

// --- Modal condition_id watchlist button ---
const conditionWatchBtn = document.getElementById("conditionWatchBtn");
conditionWatchBtn?.addEventListener("click", () => {
  const cid = conditionIdInput?.value.trim();
  if (!cid) return;
  const market = { condition_id: cid, question: cid.slice(0, 30) };
  const result = marketUi.toggleWatchlist(market);
  conditionWatchBtn.textContent = result.active ? "\u2605 已自选" : "\u2606 自选";
});

strategyTable.addEventListener("click", async (e) => {
  const expandBtn = e.target.closest("[data-expand-sid]");
  if (expandBtn) {
    const sid = expandBtn.dataset.expandSid;
    if (expandedStrategyRows.has(sid)) {
      expandedStrategyRows.delete(sid);
    } else {
      expandedStrategyRows.add(sid);
    }
    renderStrategyRows(Array.from(strategyRowCache.values()));
    return;
  }

  const stateBtn = e.target.closest("[data-set-state-sid]");
  if (stateBtn) {
    const sid = stateBtn.dataset.setStateSid;
    const prev = stateBtn.dataset.prevState || strategyMode(strategyRowCache.get(sid) || {});
    const next = stateBtn.dataset.nextState || "Stop";
    if (prev === "Real" && next === "Stop" && !confirm("切换到 Stop 不会自动撤销已有真实挂单或平仓。\n\n确认暂停策略？")) {
      return;
    }
    try {
      await fetchJson(`/api/registry/strategies/${sid}/mode`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: next }),
      });
      const row = strategyRowCache.get(sid);
      if (row) {
        row.mode = next;
        if (["Stop", "Virtual", "Real"].includes(row.state)) row.state = next;
      }
      loadStrategies();
    } catch (err) {
      alert("状态切换失败: " + err.message);
    }
    return;
  }
  // 设置参数
  const editBtn = e.target.closest("[data-edit-sid]");
  if (editBtn) {
    const row = strategyRowCache.get(editBtn.dataset.editSid);
    if (row) openEditModal(row);
    return;
  }
  const stateEditBtn = e.target.closest("[data-state-sid]");
  if (stateEditBtn) {
    const row = strategyRowCache.get(stateEditBtn.dataset.stateSid);
    if (row) openStrategyStateModal(row);
    return;
  }
  const flatBtn = e.target.closest("[data-flat-sid]");
  if (flatBtn) {
    const sid = flatBtn.dataset.flatSid;
    const name = flatBtn.dataset.flatName || sid;
    const mode = flatBtn.dataset.flatMode || "Stop";
    const realWarning = mode === "Real" ? "\n\n实盘强平当前会先被后端阻断，直到实盘子账本归因完成。" : "";
    if (!confirm(`确认对策略「${name}」执行平仓？\n会先撤该策略相关挂单，再尝试清空归属仓位，并将策略切到 Stop。${realWarning}`)) return;
    try {
      flatBtn.disabled = true;
      flatBtn.textContent = "平仓中";
      const payload = await fetchJson(`/api/registry/strategies/${sid}/force-flat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor: "dashboard" }),
      });
      const data = payload.data || {};
      const errText = (data.errors || []).length ? `\n错误: ${(data.errors || []).join("; ")}` : "";
      alert(`平仓请求完成，订单动作 ${data.orders_placed ?? 0} 个。${errText}`);
      loadStrategies();
    } catch (err) {
      alert("平仓失败: " + err.message);
      flatBtn.disabled = false;
      flatBtn.textContent = "平仓";
    }
    return;
  }
  // 删除
  const btn = e.target.closest("[data-delete-sid]");
  if (!btn) return;
  const sid = btn.dataset.deleteSid;
  const name = btn.dataset.deleteName || sid;
  if (!confirm(`确认删除策略「${name}」？此操作不可恢复。`)) return;
  try {
    await fetchJson(`/api/registry/strategies/${sid}`, { method: "DELETE" });
    loadStrategies();
  } catch (err) {
    alert("删除失败: " + err.message);
  }
});

strategyTable.addEventListener("change", async (e) => {
  const machineSel = e.target.closest(".strategy-machine-state-select[data-machine-state-sid]");
  if (machineSel) {
    const sid = machineSel.dataset.machineStateSid;
    const prev = machineSel.dataset.prev || "auto";
    const next = machineSel.value || "auto";
    if (!sid || next === prev) return;
    try {
      machineSel.disabled = true;
      await fetchJson(`/api/registry/strategies/${encodeURIComponent(sid)}/state-store/machine`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          values: { state: next },
          replace: false,
          reason: "dashboard state switch",
        }),
      });
      machineSel.dataset.prev = next;
      const row = strategyRowCache.get(String(sid));
      if (row) {
        row.state = next;
        row.machine_state = next;
      }
    } catch (err) {
      machineSel.value = prev;
      alert("State switch failed: " + err.message);
    } finally {
      machineSel.disabled = false;
    }
    return;
  }

  const sel = e.target.closest(".mode-select[data-mode-sid]");
  if (!sel) return;
  const sid = sel.dataset.modeSid || sel.dataset.sid;
  const prev = sel.dataset.prev;
  const next = sel.value;
  if (next === prev) return;

  // 按切换类型决定是否需要确认
  const needsConfirm = {
    "Virtual→Real": "当前虚拟仓位、虚拟订单、虚拟收益不会迁移到实盘。\n切换后策略将基于真实账户状态重新运行。\n\n确认切换到实盘？",
    "Real→Virtual": "真实仓位和真实挂单不会迁移到虚拟盘，也不会自动平仓或撤单。\n切换后策略将使用虚拟账户重新运行。\n\n确认切换到虚拟盘？",
    "Stop→Real":    "切换到实盘将使用真实资金和真实仓位运行策略。\n\n确认切换到实盘？",
    "Real→Stop":    "切换到 Stop 不会自动撤销已有真实挂单或平仓。\n\n确认暂停策略？",
  }[`${prev}→${next}`];

  if (needsConfirm && !confirm(needsConfirm)) {
    sel.value = prev;
    return;
  }

  try {
    await fetchJson(`/api/registry/strategies/${sid}/mode`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: next }),
    });
    sel.dataset.prev = next;
    sel.className = `state-select mode-select state-${next}`;
    const row = strategyRowCache.get(String(sid));
    if (row) {
      row.mode = next;
      if (["Stop", "Virtual", "Real"].includes(row.state)) row.state = next;
    }
  } catch (err) {
    sel.value = prev;
    alert("状态切换失败: " + err.message);
  }
});

Promise.allSettled([
  fetchJson("/api/settings"),
  loadMarketCategories(),
  loadDictionaryStatus(),
  loadOverview(),
  loadRealtimeCrypto(),
  loadRealtimeFinance(),
  loadHoldings(),
  loadStrategies(),
  loadMarkets(new FormData(document.getElementById("marketForm"))),
  loadAgentDashboard(),
]).then((results) => {
  const settingsResp = results[0];
  const settings = settingsResp.status === "fulfilled" ? (settingsResp.value.data || {}) : {};
  document.getElementById("walletInput").value = (settings.wallet_addresses || []).join(", ");
  startUiPolling(settings.ui_refresh_sec || 5);
  connectDictionaryLive();
  connectStrategiesLive();
  const initialBinanceForm = binanceForm(activeBinanceCategory);
  if (initialBinanceForm) {
    loadBinanceMarkets(activeBinanceCategory, new FormData(initialBinanceForm)).catch((error) => {
      setStatus(binanceView(activeBinanceCategory).table, error.message);
      binanceView(activeBinanceCategory).meta.innerHTML = `状态: ${statusChip("ERROR")} | 诊断: ${escapeHtml(error.message)}`;
    });
  }

  if (results[1].status === "rejected" && marketCategoryChips) {
    marketCategoryChips.innerHTML = `<span class="category-chip-empty">${escapeHtml(results[1].reason.message)}</span>`;
  }
  if (results[2].status === "rejected" && !hasLoadedDictionary) {
    setStatus(dictionarySummary, results[2].reason.message);
    dictionaryProgress.textContent = results[2].reason.message;
  }
  if (results[3].status === "rejected" && !hasLoadedOverview) {
    setStatus(systemStatus, results[3].reason.message);
  }
  if (results[4].status === "rejected" && !hasLoadedCrypto) {
    setStatus(cryptoTable, results[4].reason.message);
  }
  if (results[5].status === "rejected" && !hasLoadedFinance) {
    setStatus(financeTable, results[5].reason.message);
  }
  if (results[6].status === "rejected" && !hasLoadedHoldings) {
    setStatus(holdingsTable, results[6].reason.message);
  }
  if (results[7].status === "rejected" && !hasLoadedStrategies) {
    setStatus(strategyTable, results[7].reason.message);
  }
  if (results[8].status === "rejected" && !hasLoadedMarkets) {
    setStatus(marketTable, results[8].reason.message);
  }
  if (results[9].status === "rejected" && !hasLoadedAgentDashboard && agentMeta) {
    agentMeta.textContent = results[9].reason.message;
  }
});
