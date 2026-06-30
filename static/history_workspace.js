const historyState = {
  source: "polymarket",
  selected: null,
  results: [],
  watchlist: [],
  cases: [],
  collections: [],
  strategies: [],
  strategyInputs: [],
  selectedWatchIds: new Set(),
  selectedPoolCaseIds: new Set(),
  selectedRunCaseIds: new Set(),
};

const $ = (id) => document.getElementById(id);

const els = {
  meta: $("historyMeta"),
  query: $("historyQueryInput"),
  quote: $("historyQuoteSelect"),
  quoteField: $("historyQuoteField"),
  interval: $("historyIntervalSelect"),
  start: $("historyStartInput"),
  end: $("historyEndInput"),
  searchBtn: $("historySearchBtn"),
  refreshBtn: $("historyRefreshBtn"),
  downloadBtn: $("historyDownloadBtn"),
  results: $("historyResults"),
  resultsMeta: $("historyResultsMeta"),
  watchlist: $("historyWatchlist"),
  watchlistMeta: $("historyWatchlistMeta"),
  caseName: $("historyCaseNameInput"),
  caseCollection: $("historyCaseCollectionInput"),
  caseCollections: $("historyCaseCollections"),
  caseCheck: $("historyCaseCheck"),
  caseSuiteName: $("historyCaseSuiteNameInput"),
  createCollectionBtn: $("historyCreateCollectionBtn"),
  createCaseBtn: $("historyCreateCaseBtn"),
  downloadWatchlistBtn: $("historyDownloadWatchlistBtn"),
  cases: $("historyCases"),
  casesMeta: $("historyCasesMeta"),
  runStrategy: $("historyRunStrategySelect"),
  runCollection: $("historyRunCollectionSelect"),
  runSelectedBtn: $("historyRunSelectedBtn"),
  runParams: $("historyRunParams"),
  runCases: $("historyRunCases"),
  preview: $("historyPreview"),
  previewMeta: $("historyPreviewMeta"),
  coverage: $("historyCoverage"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function setStatus(text) {
  els.meta.textContent = text;
}

function strategyLabel(codeName) {
  return String(codeName || "");
}

function renderStrategies() {
  const runSelected = els.runStrategy.value;
  const options = [`<option value="">请选择 StrategyCode</option>`].concat(
    historyState.strategies.map((codeName) => (
      `<option value="${escapeHtml(codeName)}">${escapeHtml(strategyLabel(codeName))}</option>`
    ))
  );
  els.runStrategy.innerHTML = options.join("");
  if ([...els.runStrategy.options].some((option) => option.value === runSelected)) {
    els.runStrategy.value = runSelected;
  }
}

function renderRunParams(inputs = historyState.strategyInputs) {
  historyState.strategyInputs = inputs || [];
  if (!els.runStrategy.value) {
    els.runParams.innerHTML = `<div class="status">请选择 StrategyCode 后设置回测参数。</div>`;
    return;
  }
  if (!historyState.strategyInputs.length) {
    els.runParams.innerHTML = `<div class="status">该策略代码没有声明参数。</div>`;
    return;
  }
  els.runParams.innerHTML = `
    <div class="history-param-grid">
      ${historyState.strategyInputs.map((item) => {
        const name = item.name || item.label || "";
        const label = item.label || name;
        const value = item.default ?? "";
        const values = Array.isArray(item.values) ? item.values : [];
        const input = values.length
          ? `<select data-run-param="${escapeHtml(name)}">${values.map((option) => `<option value="${escapeHtml(option)}" ${String(option) === String(value) ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}</select>`
          : `<input data-run-param="${escapeHtml(name)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(item.kind || "String")}">`;
        return `
          <label>
            ${escapeHtml(label)}
            ${input}
          </label>
        `;
      }).join("")}
    </div>
  `;
}

function collectRunParams() {
  const result = {};
  els.runParams.querySelectorAll("[data-run-param]").forEach((input) => {
    result[input.dataset.runParam] = input.value;
  });
  return result;
}

function renderCompatibility(data) {
  const compatibility = data?.compatibility || null;
  if (!compatibility) {
    els.caseCheck.textContent = "勾选 legs 后会自动检查数据覆盖；策略适配在回测运行台检查。";
    els.caseCheck.className = "history-case-check status";
    return;
  }
  const issues = compatibility.issues || [];
  const text = issues.length
    ? issues.map((item) => `${item.level}: ${item.message}`).join(" | ")
    : "OK: 当前策略和测试 legs 初步匹配。";
  els.caseCheck.textContent = `${compatibility.summary || "Check"} · ${text}`;
  els.caseCheck.className = `history-case-check status ${compatibility.severity === "error" ? "error" : compatibility.severity === "ok" ? "good" : "pending"}`;
}

function renderCollections() {
  const caseNames = (historyState.cases || []).map((row) => row.collection_name || "Default");
  const collectionNames = (historyState.collections || []).map((row) => row.collection_name || "Default");
  const names = [...new Set([...caseNames, ...collectionNames])].sort();
  const runnableNames = [...new Set(caseNames)].sort();
  els.caseCollections.innerHTML = names.map((name) => `<option value="${escapeHtml(name)}"></option>`).join("");
  const selected = els.runCollection.value;
  els.runCollection.innerHTML = [`<option value="">全部集合</option>`].concat(
    runnableNames.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`)
  ).join("");
  if ([...els.runCollection.options].some((option) => option.value === selected)) {
    els.runCollection.value = selected;
  }
}

async function loadCollections() {
  const payload = await apiJson("/api/history/backtest-collections");
  historyState.collections = payload.data || [];
  renderCollections();
  renderRunCases();
}

function selectedInstrumentId(row) {
  if (historyState.source === "binance") {
    return row.instrument_id || `crypto_spot:binance:${row.symbol || ""}`;
  }
  const token = row.yes_token || row.token || row.token_id || "";
  if (token) return `polymarket:token:${token}`;
  return `polymarket:condition:${row.condition_id || ""}`;
}

function selectedToken(row) {
  return String(row.yes_token || row.token || row.token_id || "").trim();
}

function selectedDisplay(row) {
  if (historyState.source === "binance") {
    return row.display_symbol || row.symbol || row.instrument_id || "-";
  }
  return row.question || row.title || row.market_title || row.condition_id || "-";
}

function coverageText(coverage) {
  if (!coverage) return "No coverage info";
  if (coverage.source === "binance") {
    return `${coverage.count || 0} points ${coverage.from || "-"} -> ${coverage.to || "-"}`;
  }
  const local = coverage.local_market_deltas || {};
  const downloaded = coverage.downloaded_price_history || {};
  return `local ${local.count || 0} points / official ${downloaded.count || 0} points`;
}

function applySelectedCoverage(coverage) {
  if (!historyState.selected || !coverage) return;
  historyState.selected.history_coverage = coverage;
  if (historyState.source === "binance") {
    const symbol = String(historyState.selected.symbol || "").toUpperCase();
    historyState.results = (historyState.results || []).map((row) => (
      String(row.symbol || "").toUpperCase() === symbol ? { ...row, history_coverage: coverage } : row
    ));
    const refreshed = historyState.results.find((row) => String(row.symbol || "").toUpperCase() === symbol);
    if (refreshed) historyState.selected = refreshed;
    return;
  }
  const conditionId = String(historyState.selected.condition_id || "");
  const tokenId = selectedToken(historyState.selected);
  historyState.results = (historyState.results || []).map((row) => (
    String(row.condition_id || "") === conditionId || selectedToken(row) === tokenId
      ? { ...row, history_coverage: coverage }
      : row
  ));
  const refreshed = historyState.results.find((row) => String(row.condition_id || "") === conditionId || selectedToken(row) === tokenId);
  if (refreshed) historyState.selected = refreshed;
}

function availabilityText(row) {
  const availability = row?.data_availability || {};
  if (availability.has_common_window) {
    return availability.summary || `${availability.common_start || "-"} -> ${availability.common_end || "-"}`;
  }
  if (availability.status === "missing_data") return "缺少历史数据";
  if (availability.status === "no_overlap") return "legs 无共同时间轴";
  return availability.summary || "Unchecked";
}

function availabilityClass(row) {
  const status = row?.data_availability?.status || "";
  if (status === "ok") return "good";
  if (status === "missing_data" || status === "no_overlap") return "error";
  return "pending";
}

function rowPayload(row) {
  if (historyState.source === "binance") {
    return {
      source: "binance",
      instrument_id: selectedInstrumentId(row),
      symbol: row.symbol || "",
      display_name: row.display_name || row.display_symbol || row.symbol || "",
      venue: "binance",
      asset_class: row.asset_class || "crypto_spot",
      interval: els.interval.value || "1m",
      meta: row,
    };
  }
  return {
    source: "polymarket",
    instrument_id: selectedInstrumentId(row),
    symbol: row.symbol || "",
    display_name: selectedDisplay(row),
    venue: "polymarket",
    asset_class: "polymarket_binary",
    condition_id: row.condition_id || "",
    token_id: selectedToken(row),
    side: "Yes",
    interval: "",
    meta: row,
  };
}

function renderResults() {
  const rows = historyState.results || [];
  if (!rows.length) {
    els.results.innerHTML = `<div class="status">暂无结果。</div>`;
    els.resultsMeta.textContent = "No instruments to display.";
    return;
  }
  els.resultsMeta.textContent = `${historyState.source} search results: ${rows.length}.`;
  const head = historyState.source === "binance"
    ? `<tr><th>Symbol</th><th>价格</th><th>24h量</th><th>本地覆盖</th><th>操作</th></tr>`
    : `<tr><th>Market</th><th>Condition</th><th>本地覆盖</th><th>操作</th></tr>`;
  const body = rows.map((row, idx) => {
    const active = historyState.selected === row ? " selected" : "";
    if (historyState.source === "binance") {
      return `
        <tr class="${active}">
          <td><strong>${escapeHtml(row.display_symbol || row.symbol)}</strong><div class="muted">${escapeHtml(row.display_name || "")}</div></td>
          <td class="num">${escapeHtml(row.price ?? "-")}</td>
          <td class="num">${escapeHtml(row.volume_24h_quote ?? "-")}</td>
          <td>${escapeHtml(coverageText(row.history_coverage))}</td>
          <td class="table-actions">
            <button class="mini" data-select="${idx}" type="button">选择</button>
            <button class="mini ghost" data-add="${idx}" type="button">加入自选</button>
          </td>
        </tr>
      `;
    }
    return `
      <tr class="${active}">
        <td><strong>${escapeHtml(selectedDisplay(row))}</strong><div class="muted">${escapeHtml(row.category || row.end_date || "")}</div></td>
        <td class="mono">${escapeHtml(row.condition_id || "-")}</td>
        <td>${escapeHtml(coverageText(row.history_coverage))}</td>
        <td class="table-actions">
          <button class="mini" data-select="${idx}" type="button">选择</button>
          <button class="mini ghost" data-add="${idx}" type="button">加入自选</button>
        </td>
      </tr>
    `;
  }).join("");
  els.results.innerHTML = `<div class="table-scroll"><table class="history-table">${head}<tbody>${body}</tbody></table></div>`;
}

function renderWatchlist() {
  const rows = historyState.watchlist || [];
  const selectedCount = historyState.selectedWatchIds.size;
  els.watchlistMeta.textContent = `${rows.length} watchlist items, selected ${selectedCount}.`;
  if (!rows.length) {
    els.watchlist.innerHTML = `<div class="status">历史自选池为空。</div>`;
    return;
  }
  els.watchlist.innerHTML = `
    <div class="table-scroll">
      <table class="history-table">
        <tr><th>选</th><th>Source</th><th>Instrument</th><th>Interval</th><th>Updated</th><th>操作</th></tr>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td><input type="checkbox" data-watch-check="${row.id}" ${historyState.selectedWatchIds.has(Number(row.id)) ? "checked" : ""}></td>
              <td><span class="badge">${escapeHtml(row.source)}</span></td>
              <td><strong>${escapeHtml(row.display_name || row.symbol || row.instrument_id)}</strong><div class="muted mono">${escapeHtml(row.instrument_id)}</div></td>
              <td>${escapeHtml(row.interval || "-")}</td>
              <td>${escapeHtml(row.updated_at_utc || "-")}</td>
              <td class="table-actions">
                <button class="mini ghost" data-watch-preview="${row.id}" type="button">预览</button>
                <button class="mini danger" data-watch-delete="${row.id}" type="button">删除</button>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderCases() {
  const rows = historyState.cases || [];
  els.casesMeta.textContent = `${rows.length} backtest cases.`;
  const validIds = new Set(rows.map((row) => Number(row.case_id)));
  historyState.selectedPoolCaseIds = new Set([...historyState.selectedPoolCaseIds].filter((id) => validIds.has(id)));
  renderCollections();
  renderRunCases();
  if (!rows.length) {
    els.cases.innerHTML = `<div class="status">还没有测试样例。先在历史自选池里勾选 legs，再创建样例。</div>`;
    return;
  }
  const grouped = rows.reduce((acc, row) => {
    const name = row.collection_name || "Default";
    if (!acc[name]) acc[name] = [];
    acc[name].push(row);
    return acc;
  }, {});
  els.cases.innerHTML = Object.entries(grouped).map(([collection, items]) => `
    <section class="history-case-group">
      <div class="history-case-group-head">
        <strong>${escapeHtml(collection)}</strong>
        <span class="muted">${escapeHtml(items.length)} cases</span>
      </div>
      <div class="table-scroll">
        <table class="history-table">
          <tr><th>选</th><th>Case</th><th>Legs</th><th>Check</th><th>Status</th><th>Updated</th><th>操作</th></tr>
          <tbody>
            ${items.map((row) => {
              const legs = Array.isArray(row.legs) ? row.legs : [];
              const check = row.execution_config?.compatibility || {};
              const checkClass = check.severity === "ok" ? "good" : check.severity === "error" ? "error" : "pending";
              return `
                <tr>
                  <td><input type="checkbox" data-pool-case-check="${escapeHtml(row.case_id)}" ${historyState.selectedPoolCaseIds.has(Number(row.case_id)) ? "checked" : ""}></td>
                  <td><strong>${escapeHtml(row.case_name || `Case ${row.case_id}`)}</strong><div class="muted mono">case_id=${escapeHtml(row.case_id)}${row.strategy_id ? ` / strategy=${escapeHtml(row.strategy_id)}` : ""}</div></td>
                  <td>${escapeHtml(legs.length)} legs<div class="muted">${escapeHtml(legs.map((leg) => leg.display_name || leg.symbol || leg.instrument_id).slice(0, 3).join(" | "))}</div><div class="muted mono">有效时间轴：${escapeHtml(availabilityText(row))}</div></td>
                  <td><span class="badge ${escapeHtml(checkClass)}">${escapeHtml(check.summary || "unchecked")}</span></td>
                  <td><span class="badge pending">${escapeHtml(row.status || "draft")}</span></td>
                  <td>${escapeHtml(row.updated_at_utc || "-")}</td>
                  <td class="table-actions">
                    <button class="mini ghost" data-case-load="${row.case_id}" type="button">查看</button>
                    <button class="mini danger" data-case-delete="${row.case_id}" type="button">删除</button>
                  </td>
                </tr>
              `;
            }).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `).join("");
}
function filteredRunCases() {
  const collection = els.runCollection.value || "";
  return (historyState.cases || []).filter((row) => !collection || (row.collection_name || "Default") === collection);
}

function renderRunCases() {
  const rows = filteredRunCases();
  const validIds = new Set(rows.map((row) => Number(row.case_id)));
  historyState.selectedRunCaseIds = new Set([...historyState.selectedRunCaseIds].filter((id) => validIds.has(id)));
  if (!rows.length) {
    els.runCases.innerHTML = `<div class="status">当前集合没有回测样例。</div>`;
    return;
  }
  els.runCases.innerHTML = `
    <div class="table-scroll">
      <table class="history-table">
        <tr><th><input type="checkbox" data-run-select-all ${rows.length && rows.every((row) => historyState.selectedRunCaseIds.has(Number(row.case_id))) ? "checked" : ""}></th><th>Case</th><th>Collection</th><th>Legs</th><th>Check</th></tr>
        <tbody>
          ${rows.map((row) => {
            const legs = Array.isArray(row.legs) ? row.legs : [];
            const check = row.run_compatibility || row.execution_config?.compatibility || {};
            const checkClass = check.severity === "ok" ? "good" : check.severity === "error" ? "error" : "pending";
            return `
              <tr>
                <td><input type="checkbox" data-run-case-check="${escapeHtml(row.case_id)}" ${historyState.selectedRunCaseIds.has(Number(row.case_id)) ? "checked" : ""}></td>
                <td><strong>${escapeHtml(row.case_name || `Case ${row.case_id}`)}</strong><div class="muted mono">case_id=${escapeHtml(row.case_id)}</div></td>
                <td>${escapeHtml(row.collection_name || "Default")}</td>
                <td>${escapeHtml(legs.length)} legs<div class="muted mono">有效时间轴：${escapeHtml(availabilityText(row))}</div></td>
                <td><span class="badge ${escapeHtml(checkClass)}">${escapeHtml(check.summary || "unchecked")}</span></td>
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
  `;
}
function renderCoverage(data) {
  els.coverage.textContent = JSON.stringify(data || {}, null, 2);
}

function renderPreview(points, source) {
  if (!points || !points.length) {
    els.preview.innerHTML = `<div class="status">暂无本地预览数据。</div>`;
    els.previewMeta.textContent = "Download or select covered data first.";
    return;
  }
  els.previewMeta.textContent = `${source} latest ${points.length} points.`;
  const keys = source === "binance" ? ["ts", "open", "high", "low", "close", "volume"] : ["ts", "price"];
  els.preview.innerHTML = `
    <div class="table-scroll">
      <table class="history-table">
        <tr>${keys.map((key) => `<th>${escapeHtml(key)}</th>`).join("")}</tr>
        <tbody>
          ${points.slice(-120).map((point) => `
            <tr>${keys.map((key) => `<td class="${key === "ts" ? "mono" : "num"}">${escapeHtml(point[key] ?? "-")}</td>`).join("")}</tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function loadHealth() {
  const payload = await apiJson("/api/history/health");
  setStatus(`历史库 ${payload.db_path} | 自选 ${payload.watchlist_count} | Binance ${payload.binance_kline_count} | Polymarket ${payload.polymarket_price_count}`);
}

async function loadWatchlist() {
  const payload = await apiJson("/api/history/watchlist");
  historyState.watchlist = payload.data || [];
  const validIds = new Set(historyState.watchlist.map((row) => Number(row.id)));
  historyState.selectedWatchIds = new Set([...historyState.selectedWatchIds].filter((id) => validIds.has(id)));
  renderWatchlist();
}

async function loadCases() {
  const payload = await apiJson("/api/history/backtest-cases");
  historyState.cases = payload.data || [];
  renderCases();
}

async function loadStrategies() {
  const payload = await apiJson("/api/strategy-codes");
  historyState.strategies = payload.data || [];
  renderStrategies();
  await loadRunStrategyInputs();
}

async function loadRunStrategyInputs() {
  const codeName = els.runStrategy.value || "";
  if (!codeName) {
    renderRunParams([]);
    return;
  }
  const payload = await apiJson(`/api/strategy-codes/${encodeURIComponent(codeName)}/inputs`);
  renderRunParams(payload.data || []);
  await evaluateRunCases();
}

async function evaluateRunCases() {
  const strategyCode = els.runStrategy.value || "";
  if (!strategyCode || !(historyState.cases || []).length) {
    historyState.cases = (historyState.cases || []).map((row) => ({ ...row, run_compatibility: null }));
    renderRunCases();
    return;
  }
  const evaluated = [];
  for (const row of historyState.cases || []) {
    const payload = await apiJson("/api/history/backtest-cases/evaluate", {
      method: "POST",
      body: JSON.stringify({
        strategy_code: strategyCode,
        legs: row.legs || [],
      }),
    });
    evaluated.push({ ...row, run_compatibility: payload.compatibility || null });
  }
  historyState.cases = evaluated;
  renderCases();
}

async function evaluateCaseSelection() {
  const ids = [...historyState.selectedWatchIds];
  if (!ids.length) {
    renderCompatibility(null);
    return;
  }
  const payload = await apiJson("/api/history/backtest-cases/evaluate", {
    method: "POST",
    body: JSON.stringify({
      watchlist_ids: ids,
      strategy_code: els.runStrategy.value || "",
    }),
  });
  renderCompatibility(payload);
}

async function searchHistory() {
  const params = new URLSearchParams({
    source: historyState.source,
    q: els.query.value.trim(),
    interval: els.interval.value,
    limit: "50",
  });
  if (historyState.source === "binance" && els.quote.value) {
    params.set("quote", els.quote.value);
  }
  els.results.innerHTML = `<div class="status">搜索中...</div>`;
  const payload = await apiJson(`/api/history/search?${params.toString()}`);
  historyState.results = payload.data || [];
  historyState.selected = historyState.results[0] || null;
  renderResults();
  if (historyState.selected) {
    await refreshSelectedCoverage();
  }
}

async function addToWatchlist(row) {
  await apiJson("/api/history/watchlist", {
    method: "POST",
    body: JSON.stringify(rowPayload(row)),
  });
  await Promise.all([loadWatchlist(), loadHealth()]);
}

async function deleteWatchlist(id) {
  await apiJson(`/api/history/watchlist/${id}`, { method: "DELETE" });
  historyState.selectedWatchIds.delete(Number(id));
  await Promise.all([loadWatchlist(), loadHealth()]);
}

async function createCaseFromSelection() {
  const ids = [...historyState.selectedWatchIds];
  if (!ids.length) {
    setStatus("Select at least one watchlist item first.");
    return;
  }
  const name = els.caseName.value.trim() || `Case ${new Date().toISOString().slice(0, 19).replace("T", " ")}`;
  const collectionName = els.caseCollection.value.trim() || "Default";
  const created = await apiJson("/api/history/backtest-cases", {
    method: "POST",
    body: JSON.stringify({
      case_name: name,
      collection_name: collectionName,
      watchlist_ids: ids,
      data_window: {
        start: els.start.value || null,
        end: els.end.value || null,
        interval: els.interval.value || null,
      },
      execution_config: {
        model: "draft_not_executable_yet",
      },
    }),
  });
  els.caseName.value = "";
  await Promise.all([loadCases(), loadCollections(), loadWatchlist(), loadHealth()]);
  if ([...els.runCollection.options].some((option) => option.value === collectionName)) {
    els.runCollection.value = collectionName;
  }
  renderCases();
  renderRunCases();
  renderCompatibility(null);
  setStatus("Backtest case created.");
}

async function createCollectionFromSelection() {
  const name = els.caseSuiteName.value.trim();
  if (!name) {
    setStatus("Enter a collection name first.");
    return;
  }
  const caseIds = [...historyState.selectedPoolCaseIds];
  if (!caseIds.length) {
    setStatus("Select cases to package first.");
    return;
  }
  await apiJson("/api/history/backtest-collections", {
    method: "POST",
    body: JSON.stringify({
      collection_name: name,
      case_ids: caseIds,
    }),
  });
  historyState.selectedPoolCaseIds.clear();
  els.caseSuiteName.value = "";
  await Promise.all([loadCases(), loadCollections()]);
  setStatus(`Collection ${name} created with ${caseIds.length} cases.`);
}

async function deleteCase(id) {
  await apiJson(`/api/history/backtest-cases/${id}`, { method: "DELETE" });
  await loadCases();
}

async function createBacktestRun(caseId, strategyCode = "", params = {}) {
  const payload = await apiJson(`/api/history/backtest-cases/${caseId}/runs`, {
    method: "POST",
    body: JSON.stringify({ strategy_code: strategyCode, params }),
  });
  const run = payload.data || {};
  if (!run.run_id) {
    throw new Error("backtest run was not created");
  }
  setStatus(`Backtest run ${run.run_id} created. Opening report...`);
  window.open(`/backtests/${run.run_id}`, "_blank", "noopener,noreferrer");
}

async function runSelectedCases() {
  const strategyCode = els.runStrategy.value || "";
  if (!strategyCode) {
    setStatus("Select a StrategyCode first.");
    return;
  }
  const caseIds = [...historyState.selectedRunCaseIds];
  if (!caseIds.length) {
    setStatus("Select at least one backtest case first.");
    return;
  }
  const runs = [];
  const params = collectRunParams();
  for (const caseId of caseIds) {
    const payload = await apiJson(`/api/history/backtest-cases/${caseId}/runs`, {
      method: "POST",
      body: JSON.stringify({ strategy_code: strategyCode, params }),
    });
    if (payload.data?.run_id) {
      runs.push(payload.data);
    }
  }
  renderCoverage({
    status: "runs_created",
    strategy_code: strategyCode,
    params,
    run_count: runs.length,
    reports: runs.map((run) => ({ run_id: run.run_id, url: `/backtests/${run.run_id}` })),
  });
  if (runs[0]?.run_id) {
    window.open(`/backtests/${runs[0].run_id}`, "_blank", "noopener,noreferrer");
  }
  setStatus(`Created ${runs.length} backtest runs. The first report has been opened.`);
}

async function refreshSelectedCoverage() {
  const row = historyState.selected;
  if (!row) {
    renderCoverage({ status: "no_selection" });
    return;
  }
  const params = new URLSearchParams({ source: historyState.source });
  if (historyState.source === "binance") {
    params.set("symbol", row.symbol || "");
    params.set("interval", els.interval.value || "1m");
  } else {
    params.set("condition_id", row.condition_id || "");
    params.set("token_id", selectedToken(row));
  }
  const payload = await apiJson(`/api/history/coverage?${params.toString()}`);
  applySelectedCoverage(payload.data);
  renderResults();
  renderCoverage(payload.data);
}

async function downloadSelected() {
  const row = historyState.selected;
  if (!row) {
    setStatus("Please select an instrument first.");
    return;
  }
  const body = {
    start: els.start.value,
    end: els.end.value,
    interval: els.interval.value || "1m",
  };
  let payload;
  if (historyState.source === "binance") {
    payload = await apiJson("/api/history/binance/download", {
      method: "POST",
      body: JSON.stringify({ ...body, symbol: row.symbol }),
    });
  } else {
    payload = await apiJson("/api/history/polymarket/download", {
      method: "POST",
      body: JSON.stringify({
        ...body,
        token_id: selectedToken(row),
        condition_id: row.condition_id || "",
        fidelity: "60",
      }),
    });
  }
  const coverage = payload.coverage || payload;
  applySelectedCoverage(coverage);
  renderResults();
  renderCoverage(coverage);
  await Promise.all([loadHealth(), loadWatchlist(), loadCases(), previewSelected()]);
  await refreshSelectedCoverage();
  const pagesText = payload.pages ? `, pages ${payload.pages}` : "";
  setStatus(`Download complete: fetched ${payload.fetched || 0}, stored ${payload.stored || 0}${pagesText}.`);
}
async function downloadWatchlistSelection() {
  const ids = [...historyState.selectedWatchIds];
  if (!ids.length) {
    setStatus("Select watchlist instruments to backfill first.");
    return;
  }
  const items = ids
    .map((id) => historyState.watchlist.find((row) => Number(row.id) === Number(id)))
    .filter(Boolean);
  const base = {
    start: els.start.value,
    end: els.end.value,
    interval: els.interval.value || "1m",
  };
  const results = [];
  for (const item of items) {
    let payload;
    if (item.source === "binance") {
      payload = await apiJson("/api/history/binance/download", {
        method: "POST",
        body: JSON.stringify({
          ...base,
          symbol: item.symbol,
          interval: item.interval || base.interval,
        }),
      });
    } else if (item.source === "polymarket") {
      payload = await apiJson("/api/history/polymarket/download", {
        method: "POST",
        body: JSON.stringify({
          ...base,
          token_id: item.token_id,
          condition_id: item.condition_id || "",
          fidelity: "60",
        }),
      });
    } else {
      payload = { source: item.source, error: "unsupported source", fetched: 0, stored: 0 };
    }
    results.push({
      id: item.id,
      source: item.source,
      symbol: item.symbol,
      name: item.display_name || item.instrument_id,
      fetched: payload.fetched || 0,
      stored: payload.stored || 0,
      error: payload.error || null,
      coverage: payload.coverage || null,
    });
  }
  await Promise.all([loadHealth(), loadWatchlist(), loadCases(), evaluateCaseSelection()]);
  renderCoverage({ status: "watchlist_download_complete", results });
  const stored = results.reduce((sum, item) => sum + Number(item.stored || 0), 0);
  setStatus(`Selected data backfill complete: ${results.length} instruments, stored ${stored} points.`);
}

async function previewSelected(row = historyState.selected) {
  if (!row) return;
  const params = new URLSearchParams({ source: historyState.source, limit: "240" });
  if (historyState.source === "binance") {
    params.set("symbol", row.symbol || "");
    params.set("interval", els.interval.value || "1m");
  } else {
    params.set("token_id", selectedToken(row));
  }
  const payload = await apiJson(`/api/history/preview?${params.toString()}`);
  renderPreview(payload.points || [], historyState.source);
}

function setSource(source) {
  historyState.source = source;
  historyState.selected = null;
  historyState.results = [];
  document.querySelectorAll(".history-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.source === source);
  });
  els.quoteField.hidden = source !== "binance";
  els.results.innerHTML = `<div class="status">输入关键词开始搜索。</div>`;
  els.preview.innerHTML = `<div class="status">尚未预览。</div>`;
  renderCoverage({ status: "source_changed", source });
}

function rowFromWatchItem(item) {
  if (item.source === "binance") {
    return { symbol: item.symbol, instrument_id: item.instrument_id, display_name: item.display_name };
  }
  return { condition_id: item.condition_id, token_id: item.token_id, yes_token: item.token_id, question: item.display_name };
}

els.searchBtn.addEventListener("click", () => searchHistory().catch((err) => setStatus(err.message)));
els.refreshBtn.addEventListener("click", () => initHistoryWorkspace().catch((err) => setStatus(err.message)));
els.downloadBtn.addEventListener("click", () => downloadSelected().catch((err) => setStatus(err.message)));
els.downloadWatchlistBtn.addEventListener("click", () => downloadWatchlistSelection().catch((err) => setStatus(err.message)));
els.createCollectionBtn.addEventListener("click", () => createCollectionFromSelection().catch((err) => setStatus(err.message)));
els.createCaseBtn.addEventListener("click", () => createCaseFromSelection().catch((err) => setStatus(err.message)));
els.runSelectedBtn.addEventListener("click", () => runSelectedCases().catch((err) => setStatus(err.message)));
els.runStrategy.addEventListener("change", () => loadRunStrategyInputs().catch((err) => setStatus(err.message)));
els.runCollection.addEventListener("change", () => renderRunCases());
els.query.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    searchHistory().catch((err) => setStatus(err.message));
  }
});
document.querySelectorAll(".history-tab").forEach((button) => {
  button.addEventListener("click", () => setSource(button.dataset.source || "polymarket"));
});
els.results.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  const selectIdx = target.dataset.select;
  const addIdx = target.dataset.add;
  if (selectIdx !== undefined) {
    historyState.selected = historyState.results[Number(selectIdx)];
    renderResults();
    await refreshSelectedCoverage();
    await previewSelected();
  }
  if (addIdx !== undefined) {
    await addToWatchlist(historyState.results[Number(addIdx)]);
  }
});
els.watchlist.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  const checkbox = event.target.closest("input[type='checkbox'][data-watch-check]");
  if (checkbox) {
    const id = Number(checkbox.dataset.watchCheck);
    if (checkbox.checked) {
      historyState.selectedWatchIds.add(id);
    } else {
      historyState.selectedWatchIds.delete(id);
    }
    renderWatchlist();
    await evaluateCaseSelection();
    return;
  }
  if (!target) return;
  if (target.dataset.watchDelete) {
    await deleteWatchlist(Number(target.dataset.watchDelete));
  }
  if (target.dataset.watchPreview) {
    const item = historyState.watchlist.find((row) => String(row.id) === String(target.dataset.watchPreview));
    if (!item) return;
    setSource(item.source || "polymarket");
    historyState.selected = rowFromWatchItem(item);
    if (item.interval) els.interval.value = item.interval;
    await refreshSelectedCoverage();
    await previewSelected();
  }
});
els.runCases.addEventListener("click", (event) => {
  const selectAll = event.target.closest("input[type='checkbox'][data-run-select-all]");
  if (selectAll) {
    const rows = filteredRunCases();
    if (selectAll.checked) {
      rows.forEach((row) => historyState.selectedRunCaseIds.add(Number(row.case_id)));
    } else {
      rows.forEach((row) => historyState.selectedRunCaseIds.delete(Number(row.case_id)));
    }
    renderRunCases();
    return;
  }
  const checkbox = event.target.closest("input[type='checkbox'][data-run-case-check]");
  if (!checkbox) return;
  const id = Number(checkbox.dataset.runCaseCheck);
  if (checkbox.checked) {
    historyState.selectedRunCaseIds.add(id);
  } else {
    historyState.selectedRunCaseIds.delete(id);
  }
  renderRunCases();
});
els.cases.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  const checkbox = event.target.closest("input[type='checkbox'][data-pool-case-check]");
  if (checkbox) {
    const id = Number(checkbox.dataset.poolCaseCheck);
    if (checkbox.checked) {
      historyState.selectedPoolCaseIds.add(id);
    } else {
      historyState.selectedPoolCaseIds.delete(id);
    }
    renderCases();
    return;
  }
  if (!target) return;
  if (target.dataset.caseDelete) {
    await deleteCase(Number(target.dataset.caseDelete));
  }
  if (target.dataset.caseLoad) {
    const item = historyState.cases.find((row) => String(row.case_id) === String(target.dataset.caseLoad));
    renderCoverage(item || { status: "case_not_found" });
  }
});

async function initHistoryWorkspace() {
  await Promise.all([loadHealth(), loadWatchlist()]);
  await loadCases();
  await Promise.all([loadCollections(), loadStrategies()]);
  renderCases();
  renderRunCases();
}

setSource("polymarket");
initHistoryWorkspace().catch((err) => setStatus(err.message));
