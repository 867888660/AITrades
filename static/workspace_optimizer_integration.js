/**
 * 策略工作台性能优化集成脚本
 * 将性能优化模块集成到现有工作台中，替换高频轮询和DOM操作
 */

(function() {
  'use strict';

  console.log('[WorkspaceOptimizer] 开始集成性能优化...');

  // 等待性能模块加载
  if (!window.WorkspacePerformance) {
    console.error('[WorkspaceOptimizer] 性能模块未加载，请检查 workspace_performance_optimization.js');
    return;
  }

  const {
    refreshManager,
    domOptimizer,
    perfMonitor,
    fillChartRowsForwardOptimized,
    ChartIncrementalUpdater,
  } = window.WorkspacePerformance;

  // ========== 1. 替换原有的轮询机制 ==========
  let chartUpdater = null;

  // 重写 setAutoRefresh 函数
  const originalSetAutoRefresh = window.setAutoRefresh || function() {};

  window.setAutoRefresh = function(enabled) {
    console.log('[WorkspaceOptimizer] 切换自动刷新:', enabled);

    if (enabled) {
      // 启动智能刷新管理器
      refreshManager.setupVisibilityControl();

      // 主图数据刷新（3秒）
      refreshManager.start('price', () => {
        if (typeof window.refreshChartAuto === 'function') {
          const t0 = performance.now();
          window.refreshChartAuto().then(() => {
            const duration = performance.now() - t0;
            perfMonitor.recordMetric('chartRenderTime', duration);
            updatePerformanceBadge();
          }).catch(err => {
            console.error('[WorkspaceOptimizer] Chart refresh error:', err);
          });
        }
      });

      // 统计数据刷新（8秒）
      refreshManager.start('stats', () => {
        if (typeof window.refreshWorkspaceStats === 'function') {
          window.refreshWorkspaceStats();
        }
      });

      // 市场数据刷新（15秒）
      refreshManager.start('watch_markets', () => {
        if (typeof window.refreshWatchMarkets === 'function') {
          window.refreshWatchMarkets();
        }
      });

      // 事件流刷新（20秒）
      refreshManager.start('events', () => {
        if (typeof window.refreshWorkspaceEvents === 'function') {
          window.refreshWorkspaceEvents();
        }
      });

    } else {
      // 停止所有刷新
      refreshManager.stopAll();
    }

    // 更新UI状态
    updateAutoRefreshBadge(enabled);
  };

  // ========== 2. 优化图表更新逻辑 ==========
  const originalLoadChart = window.loadChart || function() {};

  window.loadChart = async function() {
    const t0 = performance.now();

    try {
      // 调用原始加载逻辑
      const result = await originalLoadChart.apply(this, arguments);

      // 创建增量更新器
      if (window.workspaceChartInstance && !chartUpdater) {
        chartUpdater = new ChartIncrementalUpdater(window.workspaceChartInstance);
        console.log('[WorkspaceOptimizer] Chart incremental updater initialized');
      }

      const duration = performance.now() - t0;
      perfMonitor.recordMetric('chartRenderTime', duration);
      updatePerformanceBadge();

      return result;
    } catch (error) {
      console.error('[WorkspaceOptimizer] Chart load error:', error);
      throw error;
    }
  };

  // ========== 3. 优化数据填充 ==========
  if (window.fillChartRowsForward) {
    const originalFillRows = window.fillChartRowsForward;

    window.fillChartRowsForward = function(rows) {
      const t0 = performance.now();

      // 使用优化版本
      const result = fillChartRowsForwardOptimized(rows);

      const duration = performance.now() - t0;
      perfMonitor.recordMetric('dataProcessTime', duration);

      // 如果耗时超过50ms，记录警告
      if (duration > 50) {
        console.warn(`[WorkspaceOptimizer] fillChartRowsForward 耗时 ${duration.toFixed(1)}ms (行数: ${rows?.length || 0})`);
      }

      return result;
    };
  }

  // ========== 4. 优化DOM更新 ==========
  function optimizedUpdateElement(elementId, updateFn) {
    domOptimizer.scheduleUpdate(elementId, updateFn);
  }

  // 导出优化函数供其他模块使用
  window.optimizedUpdateElement = optimizedUpdateElement;

  // ========== 5. 性能监控UI ==========
  let perfBadgeVisible = localStorage.getItem('ws-perf-badge-visible') === 'true';
  let fpsCounter = createFpsCounter();

  function updatePerformanceBadge() {
    const badge = document.getElementById('ws-performance-badge');
    if (!badge) return;

    const report = perfMonitor.getReport();

    const chartEl = document.getElementById('ws-perf-chart');
    const domEl = document.getElementById('ws-perf-dom');
    const dataEl = document.getElementById('ws-perf-data');
    const fpsEl = document.getElementById('ws-perf-fps');

    if (chartEl) {
      chartEl.textContent = report.chartRenderAvg;
      chartEl.className = getPerformanceClass(parseFloat(report.chartRenderAvg), 100, 300);
    }

    if (domEl) {
      domEl.textContent = report.domUpdateAvg;
      domEl.className = getPerformanceClass(parseFloat(report.domUpdateAvg), 50, 150);
    }

    if (dataEl) {
      dataEl.textContent = report.dataProcessAvg;
      dataEl.className = getPerformanceClass(parseFloat(report.dataProcessAvg), 30, 100);
    }

    if (fpsEl) {
      const fps = fpsCounter.getFps();
      fpsEl.textContent = fps.toFixed(0);
      fpsEl.className = getPerformanceClass(fps, 45, 30, true);
    }
  }

  function getPerformanceClass(value, warningThreshold, errorThreshold, inverted = false) {
    let className = 'ws-perf-value';

    if (inverted) {
      // FPS: 值越低越差
      if (value < errorThreshold) {
        className += ' error';
      } else if (value < warningThreshold) {
        className += ' warning';
      }
    } else {
      // 延迟: 值越高越差
      if (value > errorThreshold) {
        className += ' error';
      } else if (value > warningThreshold) {
        className += ' warning';
      }
    }

    return className;
  }

  function updateAutoRefreshBadge(enabled) {
    const badge = document.getElementById('workspaceAutoRefreshBadge');
    const text = document.getElementById('workspaceAutoRefreshText');

    if (badge) {
      badge.className = `ws3-live-dot ${enabled ? 'good' : 'pending'}`;
      badge.textContent = enabled ? 'live' : 'pause';
    }

    if (text) {
      text.textContent = enabled
        ? '智能刷新 · 主图 3s / 统计 8s / 市场 15s / 事件 20s'
        : '自动刷新已暂停';
    }
  }

  // FPS计数器
  function createFpsCounter() {
    let lastTime = performance.now();
    let frames = 0;
    let fps = 60;

    function tick() {
      frames++;
      const now = performance.now();
      const delta = now - lastTime;

      if (delta >= 1000) {
        fps = Math.round((frames * 1000) / delta);
        frames = 0;
        lastTime = now;
      }

      requestAnimationFrame(tick);
    }

    tick();

    return {
      getFps: () => fps
    };
  }

  // ========== 6. 键盘快捷键 ==========
  document.addEventListener('keydown', (e) => {
    // Ctrl/Cmd + Shift + P: 切换性能监控面板
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'P') {
      e.preventDefault();
      perfBadgeVisible = !perfBadgeVisible;
      localStorage.setItem('ws-perf-badge-visible', perfBadgeVisible);

      const badge = document.getElementById('ws-performance-badge');
      if (badge) {
        badge.style.display = perfBadgeVisible ? 'block' : 'none';
      }

      console.log(`[WorkspaceOptimizer] 性能监控面板 ${perfBadgeVisible ? '显示' : '隐藏'}`);
    }

    // Ctrl/Cmd + Shift + R: 重置性能统计
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'R') {
      e.preventDefault();
      perfMonitor.metrics = {
        chartRenderTime: [],
        domUpdateTime: [],
        dataProcessTime: [],
      };
      console.log('[WorkspaceOptimizer] 性能统计已重置');
    }
  });

  // ========== 7. 初始化 ==========
  function initialize() {
    console.log('[WorkspaceOptimizer] 初始化完成');

    // 显示性能监控面板（如果之前启用）
    const badge = document.getElementById('ws-performance-badge');
    if (badge && perfBadgeVisible) {
      badge.style.display = 'block';
    }

    // 定期更新性能面板
    setInterval(updatePerformanceBadge, 1000);

    // 输出优化摘要
    console.log('[WorkspaceOptimizer] 优化配置:', {
      refreshRates: refreshManager.refreshRates,
      chartHeight: window.workspaceChartHeight,
      perfMonitoring: perfBadgeVisible,
    });

    // 提示用户
    if (perfBadgeVisible) {
      console.log('%c💡 提示: Ctrl/Cmd + Shift + P 可切换性能监控面板', 'color: #22d3ee; font-weight: bold');
    }
  }

  // DOM加载完成后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize);
  } else {
    initialize();
  }

  // 导出到全局
  window.WorkspaceOptimizer = {
    refreshManager,
    domOptimizer,
    perfMonitor,
    chartUpdater,
    updatePerformanceBadge,
  };

})();
