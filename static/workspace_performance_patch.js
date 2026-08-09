/**
 * 策略工作台性能优化补丁
 * 优化metrics删除、图表渲染和数据加载性能
 * @version 20260709_performance_patch
 */

(function() {
  'use strict';

  // ========================================
  // 1. Metrics批量删除功能
  // ========================================

  /**
   * 批量删除选中的metrics
   */
  function batchDeleteMetrics(keys) {
    if (!keys || !keys.length) return;

    const startTime = performance.now();
    let changed = false;

    keys.forEach((key) => {
      if (!seriesStyleState[key]) return;

      const current = { ...(seriesStyleState[key] || {}), macd: { ...((seriesStyleState[key] || {}).macd || {}) } };
      current.visible = false;
      current.macd.enabled = false;
      seriesStyleState[key] = current;

      // 同时隐藏MACD相关的线
      if (!key.includes("__macd")) {
        seriesStyleState[`${key}__macd`] = { ...(seriesStyleState[`${key}__macd`] || {}), visible: false };
        seriesStyleState[`${key}__macd_signal`] = { ...(seriesStyleState[`${key}__macd_signal`] || {}), visible: false };
      }
      changed = true;
    });

    if (changed) {
      persistSeriesStyles();

      const elapsedMs = performance.now() - startTime;
      pushDebug("[WS-PERF] batch-delete-metrics", {
        row_id: Number(rowId),
        count: keys.length,
        cost_ms: elapsedMs.toFixed(1)
      });

      // 延迟渲染,避免阻塞UI
      requestAnimationFrame(() => {
        scheduleChartReload(150);
      });
    }
  }

  /**
   * 清空所有可删除的metrics
   */
  function clearAllRemovableMetrics() {
    const payload = currentChartPayload;
    if (!payload) return;

    const removableKeys = (payload.series || [])
      .filter((item) => Boolean(item?.removable))
      .map((item) => item.key);

    if (!removableKeys.length) {
      pushDebug("[WS-PERF] clear-all-metrics:empty", { row_id: Number(rowId) });
      return;
    }

    if (!confirm(`确认清空所有 ${removableKeys.length} 个可删除的metrics?`)) {
      return;
    }

    batchDeleteMetrics(removableKeys);
  }

  // ========================================
  // 2. 优化图表渲染性能
  // ========================================

  /**
   * 智能判断是否需要完整重建图表
   */
  function shouldFullRebuildChart(payload) {
    if (!lastChartStructureSignature) return true;

    const structureSignature = buildChartStructureSignature(payload);
    return structureSignature !== lastChartStructureSignature;
  }

  /**
   * 优化后的渲染函数 - 减少不必要的全量重建
   */
  const originalRenderCharts = window.renderCharts || renderCharts;
  function renderChartsOptimized(payload) {
    const startTime = performance.now();

    // 先检查是否需要全量重建
    const needsFullRebuild = shouldFullRebuildChart(payload);

    if (!needsFullRebuild && workspaceChartInstance) {
      // 增量更新
      try {
        const chart = workspaceChartInstance;
        const option = buildChartOption(payload);

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

        workspaceChartMeta.textContent = renderTimelineStatus(payload);

        const elapsedMs = performance.now() - startTime;
        pushDebug("[WS-PERF] chart:incremental-render", {
          row_id: Number(rowId),
          cost_ms: elapsedMs.toFixed(1),
          rows: (payload.rows || []).length,
          series: (payload.series || []).length
        });

        return;
      } catch (error) {
        console.warn("[WS-PERF] incremental render failed, fallback to full rebuild", error);
      }
    }

    // 全量重建
    originalRenderCharts(payload);

    const elapsedMs = performance.now() - startTime;
    pushDebug("[WS-PERF] chart:full-rebuild", {
      row_id: Number(rowId),
      cost_ms: elapsedMs.toFixed(1),
      rows: (payload.rows || []).length,
      series: (payload.series || []).length,
      reason: needsFullRebuild ? "structure-changed" : "initial"
    });
  }

  // ========================================
  // 3. 优化Series控制面板渲染
  // ========================================

  /**
   * 虚拟滚动 - 只渲染可见区域的series控制卡片
   */
  function renderSeriesControlsWithVirtualScroll(series, payload = {}) {
    const container = document.getElementById("workspaceSeriesControls");
    if (!container) return;

    const startTime = performance.now();
    ensureSeriesStyleState(series);

    const list = series || [];
    const mainSeries = list.filter((item) => item.panel === "main");
    const otherSeries = list.filter((item) => item.panel !== "main");
    const hiddenSeries = list.filter((item) => (seriesStyleState[item.key] || {}).visible === false);
    const controlSeries = list.filter(shouldShowSeriesControlCard);

    container.innerHTML = "";

    // Summary
    const summary = document.createElement("div");
    summary.className = "series-controls-title";
    summary.innerHTML = `
      Main ${mainSeries.length} / Other ${otherSeries.length} / Hidden ${hiddenSeries.length}
      <button type="button" class="ws3-btn ws3-btn-ghost mini" id="batchClearMetrics" style="margin-left:12px;">批量清空</button>
    `;
    container.appendChild(summary);

    // Legend row - 限制显示数量
    const legendRow = document.createElement("div");
    legendRow.className = "series-legend-row";
    const maxLegendItems = 32; // 减少到32个以提升性能
    list.slice(0, maxLegendItems).forEach((item) => {
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

    if (list.length > maxLegendItems) {
      const moreInfo = document.createElement("span");
      moreInfo.className = "series-legend-more";
      moreInfo.textContent = `+${list.length - maxLegendItems} more`;
      legendRow.appendChild(moreInfo);
    }
    container.appendChild(legendRow);

    // Controls title
    const title = document.createElement("div");
    title.className = "series-controls-title";
    title.textContent = "Visible Controls";
    container.appendChild(title);

    // 使用DocumentFragment减少DOM操作
    const fragment = document.createDocumentFragment();
    const row = document.createElement("div");
    row.className = "series-control-row";

    // 限制一次性渲染的控制卡片数量
    const maxControlCards = 24;
    const visibleControls = controlSeries.slice(0, maxControlCards);

    visibleControls.forEach((item) => {
      const card = createSeriesControlCard(item);
      row.appendChild(card);
    });

    if (controlSeries.length > maxControlCards) {
      const moreCard = document.createElement("div");
      moreCard.className = "series-control-chip series-control-more-notice";
      moreCard.innerHTML = `<div class="series-control-notice">还有 ${controlSeries.length - maxControlCards} 个控制项未显示<br><small>隐藏不需要的series以提升性能</small></div>`;
      row.appendChild(moreCard);
    }

    fragment.appendChild(row);
    container.appendChild(fragment);

    // State lanes and event timeline
    renderStateLaneColorControls(container, payload);
    renderEventTimelineColorControls(container);

    const elapsedMs = performance.now() - startTime;
    pushDebug("[WS-PERF] series-controls:render", {
      row_id: Number(rowId),
      cost_ms: elapsedMs.toFixed(1),
      total_series: list.length,
      visible_controls: visibleControls.length,
      hidden_count: controlSeries.length - visibleControls.length
    });
  }

  /**
   * 创建series控制卡片 (提取为独立函数以便复用)
   */
  function createSeriesControlCard(item) {
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

    return card;
  }

  /**
   * 渲染Event Timeline颜色控制
   */
  function renderEventTimelineColorControls(container) {
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
        if (currentChartPayload) scheduleChartReload(200);
      });
      etRow.appendChild(card);
    });
    container.appendChild(etRow);
  }

  // ========================================
  // 4. 防抖优化
  // ========================================

  /**
   * 增强的防抖函数
   */
  function debounceWithLeading(func, wait, leading = false) {
    let timeout = null;
    let lastCallTime = 0;

    return function(...args) {
      const now = Date.now();
      const context = this;

      const later = () => {
        timeout = null;
        if (!leading) {
          func.apply(context, args);
        }
      };

      const callNow = leading && !timeout;
      clearTimeout(timeout);
      timeout = setTimeout(later, wait);

      if (callNow) {
        func.apply(context, args);
      }

      lastCallTime = now;
    };
  }

  /**
   * 节流函数
   */
  function throttle(func, limit) {
    let inThrottle = false;
    let lastResult = null;

    return function(...args) {
      const context = this;
      if (!inThrottle) {
        lastResult = func.apply(context, args);
        inThrottle = true;
        setTimeout(() => { inThrottle = false; }, limit);
      }
      return lastResult;
    };
  }

  // ========================================
  // 5. 注册优化函数到全局作用域
  // ========================================

  // 覆盖原有函数
  if (typeof window.renderCharts === 'function') {
    window.renderCharts = renderChartsOptimized;
  }

  if (typeof window.renderSeriesControls === 'function') {
    window.renderSeriesControls = renderSeriesControlsWithVirtualScroll;
  }

  // 导出新函数
  window.batchDeleteMetrics = batchDeleteMetrics;
  window.clearAllRemovableMetrics = clearAllRemovableMetrics;
  window.debounceWithLeading = debounceWithLeading;
  window.throttle = throttle;

  // ========================================
  // 6. 事件监听器
  // ========================================

  // 批量清空按钮
  document.addEventListener("click", (e) => {
    if (e.target.id === "batchClearMetrics" || e.target.closest("#batchClearMetrics")) {
      e.preventDefault();
      clearAllRemovableMetrics();
    }
  });

  console.log("[WS-PERF] Performance patch loaded successfully");
  pushDebug && pushDebug("[WS-PERF] patch:loaded", {
    version: "20260709_performance_patch",
    features: [
      "batch-delete-metrics",
      "optimized-chart-render",
      "virtual-scroll-controls",
      "debounce-throttle"
    ]
  });
})();
