const runId = window.BACKTEST_RUN_ID;

const $ = (id) => document.getElementById(id);

const els = {
  title: $("reportTitle"),
  meta: $("reportMeta"),
  status: $("reportStatus"),
  metrics: $("reportMetrics"),
  equityMeta: $("reportEquityMeta"),
  equityChart: $("reportEquityChart"),
  equity: $("reportEquity"),
  legs: $("reportLegs"),
  orders: $("reportOrders"),
  events: $("reportEvents"),
  refresh: $("reportRefreshBtn"),
  windowStart: $("reportWindowStart"),
  windowEnd: $("reportWindowEnd"),
  windowHint: $("reportWindowHint"),
  useActualWindow: $("reportUseActualWindowBtn"),
  rerun: $("reportRerunBtn"),
};

let equityChart = null;
let currentRun = null;
let reportPollTimer = null;

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

function emptyState(text) {
  return `<div class="status">${escapeHtml(text)}</div>`;
}

function metricCard(label, value) {
  return `
    <article class="metric-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </article>
  `;
}

function numberValue(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function fmtPct(value) {
  const num = numberValue(value);
  return num === null ? "--" : `${(num * 100).toFixed(2)}%`;
}

function fmtNum(value, digits = 2) {
  const num = numberValue(value);
  return num === null ? "--" : num.toFixed(digits);
}

function fmtQty(value) {
  const num = numberValue(value);
  return num === null ? "--" : num.toLocaleString(undefined, { maximumFractionDigits: 8 });
}

function statusClass(status) {
  if (status === "completed") return "good";
  if (status === "failed") return "error";
  return "pending";
}

function isoToDatetimeLocal(value) {
  if (!value) return "";
  const match = String(value).replace("Z", "+00:00").match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return match ? `${match[1]}T${match[2]}` : "";
}

function datetimeLocalToIso(value) {
  return value ? `${value}:00+00:00` : null;
}

function technicalPoints(run) {
  return (run.equity || [])
    .map((point) => ({ ...point, equity_value: numberValue(point.equity) }))
    .filter((point) => point.equity_value !== null)
    .sort((a, b) => String(a.ts_utc || "").localeCompare(String(b.ts_utc || "")));
}

function periodText(run, points) {
  const metrics = run.metrics || {};
  const snapshot = run.case_snapshot || {};
  const windowData = snapshot.data_window || {};
  const start = metrics.period_start || points[0]?.ts_utc || windowData.start || "";
  const end = metrics.period_end || points[points.length - 1]?.ts_utc || windowData.end || "";
  if (!start && !end) return "--";
  return `${start || "--"} -> ${end || "--"}`;
}

function requestedText(run) {
  const metrics = run.metrics || {};
  const snapshot = run.case_snapshot || {};
  const windowData = snapshot.data_window || {};
  const start = metrics.requested_start || windowData.start || "";
  const end = metrics.requested_end || windowData.end || "";
  if (!start && !end) return "--";
  return `${start || "--"} -> ${end || "--"}`;
}

function computeTechnicalMetrics(run) {
  const points = technicalPoints(run);
  const metrics = run.metrics || {};
  return {
    points,
    initial: numberValue(metrics.initial_equity) ?? points[0]?.equity_value ?? null,
    final: numberValue(metrics.final_equity) ?? points[points.length - 1]?.equity_value ?? null,
    totalReturn: numberValue(metrics.total_return),
    maxDrawdown: numberValue(metrics.max_drawdown),
    sharpe: numberValue(metrics.sharpe),
    period: periodText(run, points),
    requested: requestedText(run),
  };
}

function renderWindowEditor(run) {
  const metrics = run.metrics || {};
  const snapshot = run.case_snapshot || {};
  const windowData = snapshot.data_window || {};
  if (!els.windowStart || !els.windowEnd) return;
  els.windowStart.value = isoToDatetimeLocal(windowData.start || metrics.requested_start || metrics.period_start || "");
  els.windowEnd.value = isoToDatetimeLocal(windowData.end || metrics.requested_end || metrics.period_end || "");
  const actual = metrics.period_start && metrics.period_end
    ? `实际回测区间：${metrics.period_start} -> ${metrics.period_end}`
    : "暂无实际回测区间。";
  const availability = metrics.data_availability || {};
  const availableSummary = availability.summary || (
    metrics.available_start && metrics.available_end ? `${metrics.available_start} -> ${metrics.available_end}` : ""
  );
  const available = availableSummary
    ? `有效时间轴：${availableSummary}`
    : "";
  const requested = metrics.requested_start || metrics.requested_end
    ? `请求窗口：${metrics.requested_start || "--"} -> ${metrics.requested_end || "--"}`
    : "请求窗口：自动";
  const note = metrics.data_window_note ? ` | ${metrics.data_window_note}` : "";
  els.windowHint.textContent = `${requested} | ${actual}${available ? ` | ${available}` : ""}${note}`;
  els.windowHint.classList.toggle("warning", Boolean(metrics.data_window_note));
}

function renderMetrics(run) {
  const metrics = run.metrics || {};
  const technical = computeTechnicalMetrics(run);
  els.metrics.innerHTML = [
    metricCard("状态", run.status || "-"),
    metricCard("总收益率", fmtPct(technical.totalReturn)),
    metricCard("最大回撤", fmtPct(technical.maxDrawdown)),
    metricCard("Sharpe", fmtNum(technical.sharpe, 2)),
    metricCard("初始权益", fmtNum(technical.initial, 2)),
    metricCard("结束权益", fmtNum(technical.final, 2)),
    metricCard("请求窗口", technical.requested),
    metricCard("实际时间段", technical.period),
    metricCard("样本点", metrics.equity_points ?? technical.points.length),
    metricCard("订单数", metrics.orders ?? (run.orders || []).length),
  ].join("");
}

function renderStatus(run) {
  const text = run.status === "completed"
    ? "回测已完成，报告使用本地历史数据生成。"
    : run.status === "failed"
      ? (run.error || "回测失败，请查看事件日志。")
      : "回测正在执行或等待刷新。";
  els.status.innerHTML = `
    <div class="report-status-line">
      <span class="badge ${statusClass(run.status)}">${escapeHtml(run.status || "planned")}</span>
      <span>${escapeHtml(text)}</span>
    </div>
  `;
}

function renderLegs(run) {
  const legs = run.case_snapshot?.legs || [];
  if (!legs.length) {
    els.legs.innerHTML = emptyState("这个 run 没有 legs 快照。");
    return;
  }
  els.legs.innerHTML = `
    <div class="table-scroll">
      <table class="history-table">
        <tr><th>Source</th><th>Instrument</th><th>Interval</th><th>Side</th></tr>
        <tbody>
          ${legs.map((leg) => `
            <tr>
              <td><span class="badge">${escapeHtml(leg.source || "-")}</span></td>
              <td><strong>${escapeHtml(leg.display_name || leg.symbol || leg.instrument_id)}</strong><div class="muted mono">${escapeHtml(leg.instrument_id || "-")}</div></td>
              <td>${escapeHtml(leg.interval || "-")}</td>
              <td>${escapeHtml(leg.side || "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderEquity(run) {
  const technical = computeTechnicalMetrics(run);
  const limits = run.display_limits || {};
  renderEquityChart(technical.points);
  if (!technical.points.length) {
    els.equityMeta.textContent = "暂无资金曲线数据。若回测失败，请先看事件日志里的原因。";
    els.equity.innerHTML = emptyState("暂无资金曲线。");
    return;
  }
  const shownRows = technical.points;
  const sampleText = limits.equity_sampled
    ? ` | 展示抽样 ${limits.equity_returned}/${limits.equity_total} 点，指标按全量计算`
    : "";
  els.equityMeta.textContent = `${technical.period} | return ${fmtPct(technical.totalReturn)} | max drawdown ${fmtPct(technical.maxDrawdown)}${sampleText}`;
  els.equity.innerHTML = `
    <div class="table-scroll report-data-table">
      <table class="history-table">
        <tr><th>Time</th><th>Equity</th><th>Cash</th><th>Exposure</th><th>PNL</th></tr>
        <tbody>
          ${shownRows.map((point) => `
            <tr>
              <td class="mono">${escapeHtml(point.ts_utc)}</td>
              <td class="num">${fmtNum(point.equity, 2)}</td>
              <td class="num">${fmtNum(point.cash, 2)}</td>
              <td class="num">${fmtNum(point.exposure, 2)}</td>
              <td class="num">${fmtNum(point.pnl, 2)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderEquityChart(points) {
  if (!els.equityChart) return;
  if (!points.length) {
    els.equityChart.innerHTML = `<div class="report-chart-empty">暂无资金曲线数据</div>`;
    return;
  }
  if (!window.echarts) {
    els.equityChart.innerHTML = `<div class="report-chart-empty">图表库未加载</div>`;
    return;
  }
  const chartPoints = points;
  if (!equityChart) {
    equityChart = window.echarts.init(els.equityChart);
    window.addEventListener("resize", () => equityChart?.resize());
  }
  equityChart.setOption({
    animation: true,
    backgroundColor: "transparent",
    grid: { left: 48, right: 18, top: 22, bottom: 34 },
    tooltip: { trigger: "axis", confine: true },
    xAxis: {
      type: "category",
      data: chartPoints.map((point) => point.ts_utc || ""),
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
      data: chartPoints.map((point) => point.equity_value),
    }],
  }, true);
}

function renderOrders(run) {
  const orders = run.orders || [];
  if (!orders.length) {
    els.orders.innerHTML = emptyState("暂无订单明细。");
    return;
  }
  els.orders.innerHTML = `
    <div class="table-scroll report-data-table">
      <table class="history-table">
        <tr><th>Time</th><th>Instrument</th><th>Side</th><th>Qty</th><th>Price</th><th>Fee</th><th>Reason</th></tr>
        <tbody>
          ${orders.map((order) => `
            <tr>
              <td class="mono">${escapeHtml(order.ts_utc)}</td>
              <td>${escapeHtml(order.instrument_id || order.leg_id || "-")}</td>
              <td>${escapeHtml(order.side || "-")}</td>
              <td class="num">${fmtQty(order.quantity)}</td>
              <td class="num">${fmtNum(order.price, 6)}</td>
              <td class="num">${fmtNum(order.fee, 4)}</td>
              <td>${escapeHtml(order.reason || order.status || "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderEvents(run) {
  const events = run.events || [];
  if (!events.length) {
    els.events.innerHTML = emptyState("暂无事件日志。");
    return;
  }
  els.events.innerHTML = `
    <div class="table-scroll report-data-table">
      <table class="history-table">
        <tr><th>Time</th><th>Type</th><th>Message</th></tr>
        <tbody>
          ${events.map((item) => `
            <tr>
              <td class="mono">${escapeHtml(item.ts_utc)}</td>
              <td>${escapeHtml(item.event_type || "-")}</td>
              <td>${escapeHtml(item.message || "-")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderStatus(run) {
  const metrics = run.metrics || {};
  const status = String(run.status || "planned").toLowerCase();
  const progress = Math.max(0, Math.min(100, Number(metrics.progress_percent ?? (status === "completed" ? 100 : 0))));
  const stage = metrics.progress_stage || status;
  const downloadRequired = Boolean(metrics.download_required);
  const message = metrics.progress_message || run.error || (
    status === "completed"
      ? "Backtest completed."
      : status === "failed"
        ? "Backtest failed. Check the event log below."
        : "Backtest is queued or running."
  );
  els.status.innerHTML = `
    <div class="report-status-line">
      <span class="badge ${statusClass(status)}">${escapeHtml(status)}</span>
      <span>${escapeHtml(message)}</span>
      ${downloadRequired ? `<button id="reportDownloadMissingBtn" class="mini" type="button">下载缺失数据并重跑</button>` : ""}
      <strong class="report-progress-number">${progress.toFixed(0)}%</strong>
    </div>
    <div class="report-progress-track" aria-label="Backtest progress">
      <div class="report-progress-fill" style="width: ${progress.toFixed(2)}%"></div>
    </div>
    <div class="report-progress-meta">
      <span>${escapeHtml(stage)}</span>
      <span>${escapeHtml(metrics.progress_updated_at || "")}</span>
    </div>
  `;
  const downloadBtn = document.getElementById("reportDownloadMissingBtn");
  if (downloadBtn) {
    downloadBtn.addEventListener("click", () => rerunWithEditedWindow({ autoDownload: true }));
  }
}

function scheduleReportPolling(run) {
  if (reportPollTimer) {
    clearTimeout(reportPollTimer);
    reportPollTimer = null;
  }
  const status = String(run.status || "").toLowerCase();
  if (["planned", "queued", "running"].includes(status)) {
    reportPollTimer = setTimeout(() => {
      loadReport().catch((err) => {
        els.status.innerHTML = `<div class="status error">${escapeHtml(err.message)}</div>`;
      });
    }, 2000);
  }
}

function renderReport(run) {
  currentRun = run;
  const snapshot = run.case_snapshot || {};
  els.title.textContent = snapshot.case_name || `Backtest Run ${run.run_id}`;
  els.meta.textContent = `run_id=${run.run_id} | case_id=${run.case_id} | strategy=${snapshot.run_strategy_code || run.strategy_id || "-"} | created=${run.created_at_utc || "-"}`;
  renderStatus(run);
  renderWindowEditor(run);
  renderMetrics(run);
  renderEquity(run);
  renderLegs(run);
  renderOrders(run);
  renderEvents(run);
  scheduleReportPolling(run);
}

async function loadReport() {
  const payload = await apiJson(`/api/history/backtest-runs/${runId}?equity_limit=5000&orders_limit=3000&events_limit=500`);
  renderReport(payload.data || {});
}

async function rerunWithEditedWindow(options = {}) {
  if (!els.rerun) return;
  const autoDownload = Boolean(options.autoDownload);
  els.rerun.disabled = true;
  els.rerun.textContent = "运行中...";
  els.windowHint.textContent = autoDownload ? "正在下载缺失数据并重新回测..." : "正在按新的测试时间段重新回测...";
  try {
    const payload = await apiJson(`/api/history/backtest-runs/${runId}/rerun`, {
      method: "POST",
      body: JSON.stringify({
        auto_download: autoDownload,
        data_window: {
          start: datetimeLocalToIso(els.windowStart.value),
          end: datetimeLocalToIso(els.windowEnd.value),
        },
      }),
    });
    renderReport(payload.data || {});
  } catch (err) {
    els.status.innerHTML = `<div class="status error">${escapeHtml(err.message)}</div>`;
  } finally {
    els.rerun.disabled = false;
    els.rerun.textContent = "重新回测";
  }
}

function useActualWindow() {
  const metrics = currentRun?.metrics || {};
  if (metrics.period_start) els.windowStart.value = isoToDatetimeLocal(metrics.period_start);
  if (metrics.period_end) els.windowEnd.value = isoToDatetimeLocal(metrics.period_end);
}

els.refresh.addEventListener("click", () => {
  loadReport().catch((err) => {
    els.status.innerHTML = `<div class="status error">${escapeHtml(err.message)}</div>`;
  });
});

els.rerun?.addEventListener("click", () => rerunWithEditedWindow());
els.useActualWindow?.addEventListener("click", () => useActualWindow());

loadReport().catch((err) => {
  els.status.innerHTML = `<div class="status error">${escapeHtml(err.message)}</div>`;
});
