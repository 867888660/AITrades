const ledgerMeta = document.getElementById("ledgerMeta");
const ledgerRefreshBtn = document.getElementById("ledgerRefreshBtn");
const ledgerLimitSelect = document.getElementById("ledgerLimitSelect");
const ledgerDebugBadge = document.getElementById("ledgerDebugBadge");
const ledgerDebugSummary = document.getElementById("ledgerDebugSummary");
const ledgerDebugChecks = document.getElementById("ledgerDebugChecks");
const ledgerOrders = document.getElementById("ledgerOrders");
const ledgerPositions = document.getElementById("ledgerPositions");
const ledgerLegs = document.getElementById("ledgerLegs");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatNumber(value, digits = 4) {
  const num = Number(value ?? 0);
  return Number.isFinite(num) ? num.toLocaleString(undefined, { maximumFractionDigits: digits }) : "-";
}

function formatShortTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString(undefined, { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatLegLabel(index) {
  const num = Number(index);
  if (!Number.isFinite(num)) return "-";
  return `L${num + 1}`;
}

async function fetchJson(url) {
  const resp = await fetch(url);
  const payload = await resp.json().catch(() => ({}));
  if (!resp.ok || payload.ok === false) {
    throw new Error(payload.error || `HTTP ${resp.status}`);
  }
  return payload;
}

function statusLabel(status) {
  if (status === "ok") return "OK";
  if (status === "warning") return "WARN";
  return "ERROR";
}

function statusClass(status) {
  if (status === "ok") return "good";
  if (status === "warning") return "pending";
  return "error";
}

function renderRawSample(sample) {
  if (!sample || !sample.length) {
    return "";
  }
  return `<details class="ledger-sample"><summary>查看原始样本 (${sample.length})</summary><pre>${escapeHtml(JSON.stringify(sample, null, 2))}</pre></details>`;
}

function renderDebug(debug) {
  const status = debug?.status || "error";
  ledgerDebugBadge.className = `badge ${statusClass(status)}`;
  ledgerDebugBadge.textContent = statusLabel(status);

  const counts = debug?.table_counts || {};
  ledgerDebugSummary.innerHTML = Object.entries(counts).map(([key, value]) => `
    <div class="ledger-count">
      <span>${escapeHtml(key)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");

  ledgerDebugChecks.innerHTML = (debug?.checks || []).map((check) => `
    <article class="ledger-check ${statusClass(check.status)}">
      <div class="ledger-check-head">
        <span class="ledger-check-status">${statusLabel(check.status)}</span>
        <strong>${escapeHtml(check.title)}</strong>
        <span class="muted">${escapeHtml(check.count ?? 0)}</span>
      </div>
      <p>${escapeHtml(check.detail)}</p>
      ${renderRawSample(check.sample)}
    </article>
  `).join("") || `<div class="status">暂无 debug 检查。</div>`;
}

function strategyNameMap(strategies) {
  const result = new Map();
  for (const item of strategies || []) {
    result.set(String(item.strategy_id), item.strategy_name || `#${item.strategy_id}`);
  }
  return result;
}

function renderOrders(data) {
  const names = strategyNameMap(data.strategies);
  const virtualRows = (data.virtual_open_orders || []).map((row) => ({ ...row, mode: "Virtual" }));
  const realRows = (data.real_open_orders || []).map((row) => ({ ...row, mode: "Real", id: row.order_id, updated_at_utc: row.updated_at || row.created_at }));
  const rows = [...virtualRows, ...realRows];
  if (!rows.length) {
    ledgerOrders.innerHTML = `<div class="status">没有活跃挂单。</div>`;
    return;
  }
  ledgerOrders.innerHTML = `
    <div class="ledger-table-wrap">
      <table class="ledger-table">
        <thead><tr><th>Mode</th><th>Strategy</th><th>Leg UID</th><th>Token</th><th>Side</th><th>Action</th><th>Qty</th><th>Price</th><th>Status</th><th>Tag</th><th>Updated</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td><span class="state-chip ${String(row.mode).toLowerCase() === "real" ? "good" : "pending"}">${escapeHtml(row.mode)}</span></td>
              <td>${escapeHtml(names.get(String(row.strategy_id)) || `#${row.strategy_id || "-"}`)}</td>
              <td class="mono truncate">${escapeHtml(row.leg_uid || "-")}</td>
              <td class="mono truncate">${escapeHtml(row.token_id || "-")}</td>
              <td>${escapeHtml(row.side || row.direction || "-")}</td>
              <td>${escapeHtml(row.action || row.side || "-")}</td>
              <td class="num">${formatNumber(row.remaining_qty ?? row.qty)}</td>
              <td class="num">${formatNumber(row.price, 4)}</td>
              <td>${escapeHtml(row.status || "-")}</td>
              <td>${escapeHtml(row.client_order_tag || "-")}</td>
              <td>${formatShortTime(row.updated_at_utc)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderPositions(data) {
  const names = strategyNameMap(data.strategies);
  const legsByStrategyIndex = new Map((data.legs || []).map((leg) => [
    `${leg.strategy_id}:${leg.leg_index}`,
    leg,
  ]));
  function legacyInstrument(row) {
    const leg = legsByStrategyIndex.get(`${row.strategy_id}:${row.leg_index}`) || {};
    const side = String(row.side || "").toUpperCase();
    const token = side === "YES" ? leg.yes_token : side === "NO" ? leg.no_token : "";
    return {
      ...row,
      mode: "Virtual",
      asset_class: leg.asset_class || "polymarket_binary",
      venue: leg.venue || "polymarket",
      symbol: leg.symbol || "",
      instrument: leg.instrument_id || leg.condition_id || token || `${formatLegLabel(row.leg_index)} ${side}`,
      side,
      market_value: Number(row.qty || 0) * Number(row.avg_price || 0),
      unrealized_pnl: row.unrealized_pnl ?? 0,
      source: "virtual_v1",
    };
  }
  const rows = [
    ...(data.virtual_positions || []).map(legacyInstrument),
    ...(data.virtual_positions_v2 || []).map((row) => ({
      ...row,
      mode: "Virtual v2",
      instrument: row.instrument_id,
      source: "virtual_v2",
    })),
    ...(data.real_positions || []).map((row) => ({
      ...row,
      mode: "Real",
      asset_class: "polymarket_binary",
      venue: "polymarket",
      instrument: row.token_id || row.condition_id,
      side: row.outcome,
      market_value: row.market_value ?? row.cost,
    })),
    ...(data.unassigned_positions || []).map((row) => ({
      ...row,
      mode: "Unassigned",
      strategy_id: "",
      asset_class: "polymarket_binary",
      venue: "polymarket",
      instrument: row.token_id || row.condition_id,
      side: row.outcome,
      cost: Number(row.qty || 0) * Number(row.avg_price || 0),
      market_value: Number(row.qty || 0) * Number(row.avg_price || 0),
    })),
  ];
  if (!rows.length) {
    ledgerPositions.innerHTML = `<div class="status">没有持仓。</div>`;
    return;
  }
  ledgerPositions.innerHTML = `
    <div class="ledger-table-wrap">
      <table class="ledger-table">
        <thead><tr><th>Mode</th><th>Strategy</th><th>Asset</th><th>Venue</th><th>Symbol</th><th>Instrument</th><th>Side</th><th>Qty</th><th>Avg</th><th>Cost</th><th>Market Value</th><th>Unrealized</th><th>Realized</th><th>Source</th><th>Updated</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td><span class="state-chip ${row.mode === "Unassigned" ? "error" : row.mode === "Real" ? "good" : "pending"}">${escapeHtml(row.mode)}</span></td>
              <td>${escapeHtml(row.strategy_id ? (names.get(String(row.strategy_id)) || `#${row.strategy_id}`) : "-")}</td>
              <td>${escapeHtml(row.asset_class || "-")}</td>
              <td>${escapeHtml(row.venue || "-")}</td>
              <td>${escapeHtml(row.symbol || "-")}</td>
              <td class="mono truncate">${escapeHtml(row.instrument || "-")}</td>
              <td>${escapeHtml(row.side || "-")}</td>
              <td class="num">${formatNumber(row.qty)}</td>
              <td class="num">${formatNumber(row.avg_price)}</td>
              <td class="num">${formatNumber(row.cost, 2)}</td>
              <td class="num">${formatNumber(row.market_value, 2)}</td>
              <td class="num">${formatNumber(row.unrealized_pnl, 2)}</td>
              <td class="num">${formatNumber(row.realized_pnl, 2)}</td>
              <td>${escapeHtml(row.source || row.reason || "-")}</td>
              <td>${formatShortTime(row.updated_at_utc)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderLegs(data) {
  const names = strategyNameMap(data.strategies);
  const rows = data.legs || [];
  if (!rows.length) {
    ledgerLegs.innerHTML = `<div class="status">没有 strategy legs。</div>`;
    return;
  }
  ledgerLegs.innerHTML = `
    <div class="ledger-table-wrap">
      <table class="ledger-table">
        <thead><tr><th>Strategy</th><th>Leg</th><th>Leg UID</th><th>Asset</th><th>Venue</th><th>Symbol</th><th>Instrument</th><th>Polymarket Tokens</th><th>Budget</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>${escapeHtml(names.get(String(row.strategy_id)) || `#${row.strategy_id}`)}</td>
              <td>${escapeHtml(formatLegLabel(row.leg_index))}</td>
              <td class="mono truncate">${escapeHtml(row.leg_uid || "-")}</td>
              <td>${escapeHtml(row.asset_class || "-")}</td>
              <td>${escapeHtml(row.venue || "-")}</td>
              <td>${escapeHtml(row.symbol || "-")}</td>
              <td class="mono truncate">${escapeHtml(row.instrument_id || row.condition_id || "-")}</td>
              <td class="mono truncate">${escapeHtml([row.yes_token ? `YES ${row.yes_token}` : "", row.no_token ? `NO ${row.no_token}` : ""].filter(Boolean).join(" | ") || "-")}</td>
              <td class="num">${formatNumber(row.budget_cap, 2)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function loadLedger() {
  ledgerMeta.textContent = "正在读取账本...";
  ledgerDebugBadge.className = "badge";
  ledgerDebugBadge.textContent = "Loading";
  try {
    const limit = ledgerLimitSelect.value || "100";
    const payload = await fetchJson(`/api/ledger?limit=${encodeURIComponent(limit)}`);
    const data = payload.data || {};
    renderDebug(data.debug || {});
    renderOrders(data);
    renderPositions(data);
    renderLegs(data);
    ledgerMeta.textContent = `已加载：${new Date().toLocaleTimeString()}`;
  } catch (err) {
    ledgerDebugBadge.className = "badge error";
    ledgerDebugBadge.textContent = "ERROR";
    ledgerMeta.textContent = `加载失败：${err.message}`;
    ledgerDebugChecks.innerHTML = `<div class="status error">加载失败：${escapeHtml(err.message)}</div>`;
  }
}

ledgerRefreshBtn.addEventListener("click", loadLedger);
ledgerLimitSelect.addEventListener("change", loadLedger);
loadLedger();
