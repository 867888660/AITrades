// Workspace V3 UI patch. Keep chart ownership in strategy_workspace_v2.js.
(function () {
  "use strict";

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

  const fmtNum = (value, digits = 2) => {
    if (value === null || value === undefined || value === "") return "-";
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: digits }) : "-";
  };
  const fmtPct = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : "-";
  };
  const fmtMoney = (value, suffix = "") => {
    const text = fmtNum(value, 2);
    return text === "-" || !suffix ? text : `${text} ${suffix}`;
  };
  const isBinanceTarget = (target = {}) => {
    const text = [
      target.type,
      target.source,
      target.venue,
      target.asset_class,
      target.instrument_id,
    ].map((value) => String(value || "").toLowerCase()).join("|");
    return text.includes("binance") || text.includes("crypto_spot");
  };
  const latestEquityPoint = (run = {}) => {
    const points = Array.isArray(run?.equity) ? run.equity : [];
    return points.length ? points[points.length - 1] : null;
  };
  const backtestOrderCount = (run = {}) => {
    const metrics = run?.metrics || {};
    if (Array.isArray(metrics.orders)) return metrics.orders.length;
    if (metrics.orders !== undefined && metrics.orders !== null) return metrics.orders;
    return Array.isArray(run?.orders) ? run.orders.length : null;
  };

  const drawerToggle = document.getElementById("workspaceDrawerToggle");
  const drawer = document.getElementById("workspaceDrawer");
  if (drawerToggle && drawer) {
    drawerToggle.addEventListener("click", () => drawer.classList.toggle("collapsed"));
  }

  const drawerTabs = document.querySelectorAll("[data-drawer-tab]");
  const drawerPanes = document.querySelectorAll("[data-drawer-pane]");
  drawerTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      drawerTabs.forEach((item) => item.classList.remove("active"));
      drawerPanes.forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      document.querySelector(`[data-drawer-pane="${tab.dataset.drawerTab}"]`)?.classList.add("active");
    });
  });

  const filterTabs = document.querySelectorAll("[data-event-filter]");
  let activeEventFilter = "all";

  function applyEventFilter() {
    document.querySelectorAll(".ws3-event-item").forEach((item) => {
      if (activeEventFilter === "all") {
        item.style.display = "";
        return;
      }
      const cls = item.className;
      const visible =
        (activeEventFilter === "print" && cls.includes("ev-print")) ||
        (activeEventFilter === "action" && cls.includes("ev-action")) ||
        (activeEventFilter === "trade" && (cls.includes("ev-trade-virtual") || cls.includes("ev-trade-real"))) ||
        (activeEventFilter === "error" && cls.includes("ev-error")) ||
        (activeEventFilter === "settings" && cls.includes("ev-settings"));
      item.style.display = visible ? "" : "none";
    });
  }

  function visibleEventCount() {
    let count = 0;
    document.querySelectorAll(".ws3-event-item").forEach((item) => {
      if (item.style.display !== "none") count += 1;
    });
    return count;
  }

  filterTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      activeEventFilter = tab.dataset.eventFilter;
      filterTabs.forEach((item) => item.classList.toggle("active", item.dataset.eventFilter === activeEventFilter));
      _lastEventsSignature = "";
      applyEventFilter();
      if (activeEventFilter !== "all" && visibleEventCount() === 0 && typeof window.loadWorkspaceEvents === "function") {
        window.loadWorkspaceEvents().catch(() => {});
      }
    });
  });

  function eventClass(event) {
    const type = String(event?.event_type || event?.type || "").toLowerCase();
    const source = String(event?.source || event?.env || "").toLowerCase();
    if (type.includes("trade") || type.includes("fill") || type.includes("order")) {
      return source.includes("real") ? "ev-trade-real" : "ev-trade-virtual";
    }
    if (type.includes("action")) return "ev-action";
    if (type.includes("error") || type.includes("fail") || type.includes("block")) return "ev-error";
    if (type.includes("settings")) return "ev-settings";
    if (type.includes("settle")) return "ev-settle";
    return "ev-print";
  }

  function eventKind(event) {
    const type = String(event?.event_type || event?.type || "").toLowerCase();
    const subtype = String(event?.event_subtype || "").toLowerCase();
    if (type.includes("error")) return "ERROR";
    if (type.includes("trade") || type.includes("fill") || type.includes("order")) {
      if (subtype === "blocked" || subtype === "failed") return "BLOCKED";
      return "TRADE";
    }
    if (type.includes("fail") || type.includes("block")) return "BLOCKED";
    if (type.includes("action")) return "ACTION";
    if (type.includes("settings")) return "SETTINGS";
    if (type.includes("settle")) return "SETTLE";
    if (type.includes("print")) return "PRINT";
    return type.toUpperCase() || "EVENT";
  }

  function formatTimeShort(value) {
    if (!value) return "-";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}:${String(d.getUTCSeconds()).padStart(2, "0")}`;
  }

  function _formatEventBody(event) {
    const raw = event.summary || event.label || event.event_type || event.type || "-";
    const type = String(event?.event_type || event?.type || "").toLowerCase();
    if (type.includes("action")) {
      try {
        const obj = JSON.parse(raw);
        if (obj && obj.type) {
          const parts = [obj.type];
          if (obj.side) parts.push(obj.side);
          if (obj.qty != null) parts.push(`qty=${Number(obj.qty).toFixed(1)}`);
          if (obj.price != null) parts.push(`@${Number(obj.price).toFixed(4)}`);
          if (obj.status && obj.status !== "filled") parts.push(`[${obj.status}]`);
          if (obj.target_pct != null) parts.push(`target=${(obj.target_pct * 100).toFixed(0)}%`);
          if (obj.reason) parts.push(obj.reason);
          return parts.join(" ");
        }
      } catch {}
    }
    return raw;
  }

  let _lastEventsSignature = "";

  window.renderEvents = function renderEventsV3(events) {
    const container = document.getElementById("workspaceEvents");
    if (!container) return;
    if (!events?.length) {
      // 只有从未渲染过数据时才显示"暂无事件"，避免刷新期间清空已有内容
      if (_lastEventsSignature === "") {
        container.innerHTML = '<div class="ws3-status">暂无事件</div>';
        _lastEventsSignature = "__empty__";
      }
      return;
    }
    // 签名只计算当前过滤器可见的事件，避免其他类型事件变化触发重渲染
    const visibleForSig = activeEventFilter === "all" ? events : events.filter((e) => {
      const t = String(e?.event_type || e?.type || "").toLowerCase();
      const s = String(e?.source || "").toLowerCase();
      if (activeEventFilter === "trade") return t.includes("trade") || t.includes("fill") || t.includes("order");
      if (activeEventFilter === "action") return t.includes("action");
      if (activeEventFilter === "print") return t.includes("print");
      if (activeEventFilter === "error") return t.includes("error") || t.includes("fail") || t.includes("block");
      if (activeEventFilter === "settings") return t.includes("settings");
      return true;
    });
    const sig = visibleForSig.map((e) => `${e.id || ""}|${e.ts || ""}|${e.repeat_count || e.payload?.repeat_count || ""}`).join(";");
    if (typeof pushDebug === "function") {
      if (sig === _lastEventsSignature) {
        pushDebug("[events] renderEvents:skip (sig unchanged)", { filter: activeEventFilter, visible: visibleForSig.length, total: events.length });
      } else {
        pushDebug("[events] renderEvents:render", { filter: activeEventFilter, visible: visibleForSig.length, total: events.length, sig_changed: true, prev_sig_prefix: _lastEventsSignature.slice(0, 60), new_sig_prefix: sig.slice(0, 60) });
      }
    }
    if (sig === _lastEventsSignature) return;
    _lastEventsSignature = sig;
    container.innerHTML = events.map((event) => {
      const source = String(event?.source || event?.env || "").toLowerCase();
      const envClass = source.includes("real") ? "real" : source.includes("virtual") ? "virtual" : "";
      const body = _formatEventBody(event);
      const count = Number(event?.repeat_count || event?.payload?.repeat_count || event?.duplicate_count || 1);
      return `
        <div class="ws3-event-item ${eventClass(event)}">
          <div class="ws3-event-meta">
            <span class="ws3-event-kind">${esc(eventKind(event))}</span>
            ${envClass ? `<span class="ws3-event-env ${envClass}">${esc(envClass)}</span>` : ""}
            <span class="ws3-event-ts">${esc(formatTimeShort(event.ts))}</span>
          </div>
          <div class="ws3-event-body">${esc(body)}</div>
          ${count > 1 ? `<span class="ws3-event-count" title="Repeated ${esc(count)} times">${esc(count)}</span>` : "<span></span>"}
        </div>
      `;
    }).join("");
    applyEventFilter();
  };

  window.renderSummary = function renderSummaryV3(strategy) {
    const bar = document.getElementById("workspaceLegsBar");
    if (!bar) return;
    const markets = Array.isArray(window.workspaceTrackedMarkets) ? window.workspaceTrackedMarkets : [];
    const primary = markets[0] || {};
    const validModes = ["Stop", "Virtual", "Real"];
    const legacyState = validModes.includes(strategy?.state) ? strategy.state : "";
    const mode = strategy?.mode || legacyState || "Stop";
    const machineState = strategy?.machine_state || (!validModes.includes(strategy?.state) ? strategy?.state : "") || "auto";
    if (isBinanceTarget(primary)) {
      const run = window.selectedBacktestResults?.selected_run || window.workspaceState?.backtest?.latest_run || {};
      const metrics = run?.metrics || run || {};
      const latest = latestEquityPoint(run) || {};
      const latestMeta = latest?.meta || {};
      const equity = metrics.final_equity ?? latest.equity ?? strategy?.strategy_bankroll;
      const initial = metrics.initial_equity ?? metrics.initial_cash;
      const pnl = Number(latest?.pnl);
      const pnlClass = pnl > 0 ? "positive" : pnl < 0 ? "negative" : "";
      const positionRatio = latestMeta.position_ratio;
      const orders = backtestOrderCount(run);
      bar.innerHTML = `
        <div class="ws3-strat-summary crypto">
          <div class="ws3-leg-title">策略汇总 <span class="ws3-leg-direction observe">crypto</span></div>
          <div class="ws3-leg-row"><span>Instrument</span><span class="val">${esc(primary.symbol || primary.label || "-")}</span></div>
          <div class="ws3-leg-row"><span>Equity</span><span class="val">${esc(fmtMoney(equity, "USDT"))}</span></div>
          <div class="ws3-leg-row"><span>PnL</span><span class="val ws3-leg-pnl ${pnlClass}">${esc(fmtMoney(Number.isFinite(pnl) ? pnl : null, "USDT"))}</span></div>
          <div class="ws3-leg-row"><span>Mode</span><span class="val">${esc(mode)}</span></div>
          <div class="ws3-leg-row"><span>State</span><span class="val">${esc(machineState)}</span></div>
        </div>
        <div class="ws3-leg-card crypto">
          <div class="ws3-leg-title">主标的 <span class="ws3-leg-direction observe">Binance</span></div>
          <div class="ws3-leg-row"><span>K线</span><span class="val">${esc(primary.symbol || "-")} · ${esc(primary.interval || "1m")}</span></div>
          <div class="ws3-leg-row"><span>Return / DD</span><span class="val">${esc(fmtPct(metrics.total_return))} / ${esc(fmtPct(metrics.max_drawdown))}</span></div>
          <div class="ws3-leg-row"><span>Position</span><span class="val">${esc(fmtPct(positionRatio))} · ${esc(fmtNum(latestMeta.position_qty, 8))} BTC</span></div>
          <div class="ws3-leg-row"><span>Mark</span><span class="val">${esc(fmtMoney(latestMeta.close, "USDT"))}</span></div>
          <div class="ws3-leg-row"><span>Trades</span><span class="val">${esc(fmtNum(orders, 0))}</span></div>
        </div>
      `;
      if (window.syncWorkspaceStateControl) {
        window.syncWorkspaceStateControl(strategy || {});
      }
      if (window.syncWorkspaceMachineStateControl) {
        window.syncWorkspaceMachineStateControl(strategy || {}, window.workspaceStateStore || null);
      }
      return;
    }
    const pnl = Number(strategy?.strategy_pnl);
    const pnlClass = pnl > 0 ? "positive" : pnl < 0 ? "negative" : "";
    const yesQty = Number(strategy?.yes_qty || 0);
    const noQty = Number(strategy?.no_qty || 0);
    const yesAvg = Number(strategy?.yes_avg || 0);
    const noAvg = Number(strategy?.no_avg || 0);
    const exposure = yesQty * yesAvg + noQty * noAvg;
    bar.innerHTML = `
      <div class="ws3-strat-summary">
        <div class="ws3-leg-title">策略汇总</div>
        <div class="ws3-leg-row"><span>Bankroll</span><span class="val">${fmtNum(strategy?.strategy_bankroll)}</span></div>
        <div class="ws3-leg-row"><span>Exposure</span><span class="val">${fmtNum(exposure)}</span></div>
        <div class="ws3-leg-row"><span>PnL</span><span class="val ws3-leg-pnl ${pnlClass}">${fmtNum(pnl)}</span></div>
        <div class="ws3-leg-row"><span>Mode</span><span class="val">${esc(mode)}</span></div>
        <div class="ws3-leg-row"><span>State</span><span class="val">${esc(machineState)}</span></div>
      </div>
      <div class="ws3-leg-card ${esc(String(mode).toLowerCase())}">
        <div class="ws3-leg-title">主市场 <span class="ws3-leg-direction observe">observe</span></div>
        <div class="ws3-leg-row"><span>Yes Bid/Ask</span><span class="val">${fmtNum(strategy?.yes_bid, 4)} / ${fmtNum(strategy?.yes_ask, 4)}</span></div>
        <div class="ws3-leg-row"><span>No Bid/Ask</span><span class="val">${fmtNum(strategy?.no_bid, 4)} / ${fmtNum(strategy?.no_ask, 4)}</span></div>
        <div class="ws3-leg-row"><span>Yes Qty/Avg Cost</span><span class="val">${fmtNum(strategy?.yes_qty)} / ${fmtNum(strategy?.yes_avg, 4)}</span></div>
        <div class="ws3-leg-row"><span>No Qty/Avg Cost</span><span class="val">${fmtNum(strategy?.no_qty)} / ${fmtNum(strategy?.no_avg, 4)}</span></div>
      </div>
    `;

    if (window.syncWorkspaceStateControl) {
      window.syncWorkspaceStateControl(strategy || {});
    }
    if (window.syncWorkspaceMachineStateControl) {
      window.syncWorkspaceMachineStateControl(strategy || {}, window.workspaceStateStore || null);
    }
  };

  window.renderHeader = function renderHeaderV3(strategy) {
    const title = document.getElementById("workspaceTitle");
    const subtitle = document.getElementById("workspaceSubtitle");
    const markets = Array.isArray(window.workspaceTrackedMarkets) ? window.workspaceTrackedMarkets : [];
    const primary = markets[0] || {};
    if (title) title.textContent = strategy?.display_name || strategy?.strategy || "Unnamed";
    if (subtitle) {
      subtitle.textContent = isBinanceTarget(primary)
        ? `${primary.symbol || primary.label || "-"} · ${primary.interval || "1m"} · Row ${strategy?.row_id || "-"}`
        : `${strategy?.question || "-"} | Row ${strategy?.row_id || "-"}`;
    }
  };
})();
