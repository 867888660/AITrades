// 策略工作台性能优化模块
// 解决高频轮询和图表重渲染导致的卡顿问题

// ========== 1. 智能刷新管理器 ==========
class SmartRefreshManager {
  constructor() {
    this.refreshRates = {
      price: 3000,       // 降低主图刷新频率 2s -> 3s
      stats: 8000,       // 统计数据 5s -> 8s
      metrics: 8000,     // 指标数据 5s -> 8s
      watch_markets: 15000,   // 市场数据 10s -> 15s
      overlay: 30000,    // 对比数据 20s -> 30s
      events: 20000,     // 事件流 15s -> 20s
    };
    this.activeTimers = new Map();
    this.isPaused = false;
    this.visibilityHandler = null;
  }

  // 启动智能刷新 - 页面隐藏时自动暂停
  start(streamType, callback) {
    if (this.isPaused) return;

    const interval = this.refreshRates[streamType] || 5000;

    // 清理旧定时器
    if (this.activeTimers.has(streamType)) {
      clearInterval(this.activeTimers.get(streamType));
    }

    // 启动新定时器
    const timer = setInterval(() => {
      if (!document.hidden && !this.isPaused) {
        callback();
      }
    }, interval);

    this.activeTimers.set(streamType, timer);

    // 首次立即执行
    if (!document.hidden) {
      callback();
    }
  }

  // 停止所有刷新
  stopAll() {
    this.activeTimers.forEach(timer => clearInterval(timer));
    this.activeTimers.clear();
  }

  // 暂停刷新（页面隐藏时）
  pause() {
    this.isPaused = true;
  }

  // 恢复刷新
  resume() {
    this.isPaused = false;
  }

  // 监听页面可见性变化
  setupVisibilityControl() {
    if (this.visibilityHandler) return;

    this.visibilityHandler = () => {
      if (document.hidden) {
        this.pause();
        console.log('[SmartRefresh] 页面隐藏，暂停刷新');
      } else {
        this.resume();
        console.log('[SmartRefresh] 页面可见，恢复刷新');
      }
    };

    document.addEventListener('visibilitychange', this.visibilityHandler);
  }

  destroy() {
    this.stopAll();
    if (this.visibilityHandler) {
      document.removeEventListener('visibilitychange', this.visibilityHandler);
    }
  }
}

// ========== 2. 图表增量更新器 ==========
class ChartIncrementalUpdater {
  constructor(chartInstance) {
    this.chart = chartInstance;
    this.lastUpdateTime = 0;
    this.pendingUpdate = null;
    this.updateThrottle = 200; // 节流：最快200ms更新一次
  }

  // 增量更新数据点（不重建图表）
  appendDataPoint(seriesIndex, dataPoint) {
    if (!this.chart) return;

    const now = Date.now();
    if (now - this.lastUpdateTime < this.updateThrottle) {
      // 节流：合并待更新数据
      if (!this.pendingUpdate) {
        this.pendingUpdate = setTimeout(() => {
          this._flushUpdate();
        }, this.updateThrottle);
      }
      return;
    }

    try {
      const option = this.chart.getOption();
      if (!option.series || !option.series[seriesIndex]) return;

      // 直接追加数据，避免完全重建
      this.chart.appendData({
        seriesIndex: seriesIndex,
        data: [dataPoint]
      });

      this.lastUpdateTime = now;
    } catch (error) {
      console.error('[ChartUpdate] 增量更新失败:', error);
    }
  }

  // 批量更新（用于多系列同时更新）
  batchUpdate(updates) {
    if (!this.chart) return;

    try {
      // 使用 notMerge: false 进行部分更新，而不是完全重建
      updates.forEach(({seriesIndex, data}) => {
        this.chart.appendData({
          seriesIndex,
          data: Array.isArray(data) ? data : [data]
        });
      });

      this.lastUpdateTime = Date.now();
    } catch (error) {
      console.error('[ChartUpdate] 批量更新失败:', error);
    }
  }

  _flushUpdate() {
    this.pendingUpdate = null;
    this.lastUpdateTime = Date.now();
  }

  destroy() {
    if (this.pendingUpdate) {
      clearTimeout(this.pendingUpdate);
    }
  }
}

// ========== 3. DOM更新优化器 ==========
class DOMUpdateOptimizer {
  constructor() {
    this.pendingUpdates = new Map();
    this.batchTimer = null;
    this.batchDelay = 16; // ~60fps
  }

  // 批量更新DOM，减少重排重绘
  scheduleUpdate(elementId, updateFn) {
    this.pendingUpdates.set(elementId, updateFn);

    if (!this.batchTimer) {
      this.batchTimer = requestAnimationFrame(() => {
        this._flushUpdates();
      });
    }
  }

  _flushUpdates() {
    const fragment = document.createDocumentFragment();

    this.pendingUpdates.forEach((updateFn, elementId) => {
      const element = document.getElementById(elementId);
      if (element) {
        // 在文档片段中更新，减少重排
        const clone = element.cloneNode(false);
        updateFn(clone);
        element.parentNode.replaceChild(clone, element);
      }
    });

    this.pendingUpdates.clear();
    this.batchTimer = null;
  }

  destroy() {
    if (this.batchTimer) {
      cancelAnimationFrame(this.batchTimer);
    }
    this.pendingUpdates.clear();
  }
}

// ========== 4. 数据填充性能优化 ==========
function fillChartRowsForwardOptimized(rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return [];
  }

  // 预先过滤和排序
  const validRows = rows
    .filter(row => row && row.ts)
    .sort((a, b) => {
      const tsA = new Date(a.ts).getTime();
      const tsB = new Date(b.ts).getTime();
      return tsA - tsB;
    });

  if (validRows.length === 0) return [];

  // 使用对象池复用，减少GC压力
  const result = new Array(validRows.length);
  let currentState = { ...validRows[0] };
  result[0] = currentState;

  // 单次遍历完成前向填充
  for (let i = 1; i < validRows.length; i++) {
    const row = validRows[i];
    // 只更新变化的字段
    for (const key in row) {
      if (row[key] !== undefined && row[key] !== null) {
        currentState[key] = row[key];
      }
    }
    result[i] = { ...currentState, ts: row.ts };
  }

  return result;
}

// ========== 5. 虚拟滚动事件列表 ==========
class VirtualEventList {
  constructor(containerId, itemHeight = 40) {
    this.container = document.getElementById(containerId);
    this.itemHeight = itemHeight;
    this.visibleCount = 20; // 只渲染可见区域的项
    this.scrollTop = 0;
    this.allEvents = [];
    this.setupScrollListener();
  }

  setupScrollListener() {
    if (!this.container) return;

    this.container.addEventListener('scroll', () => {
      this.scrollTop = this.container.scrollTop;
      this.render();
    });
  }

  setData(events) {
    this.allEvents = events || [];
    this.render();
  }

  render() {
    if (!this.container || this.allEvents.length === 0) return;

    const startIndex = Math.floor(this.scrollTop / this.itemHeight);
    const endIndex = Math.min(
      startIndex + this.visibleCount,
      this.allEvents.length
    );

    const visibleEvents = this.allEvents.slice(startIndex, endIndex);
    const offsetY = startIndex * this.itemHeight;

    // 只渲染可见区域
    this.container.innerHTML = `
      <div style="height: ${this.allEvents.length * this.itemHeight}px; position: relative;">
        <div style="transform: translateY(${offsetY}px);">
          ${visibleEvents.map(event => this.renderEventItem(event)).join('')}
        </div>
      </div>
    `;
  }

  renderEventItem(event) {
    return `<div class="event-item" style="height: ${this.itemHeight}px;">
      ${event.message || ''}
    </div>`;
  }
}

// ========== 6. 全局性能监控 ==========
class PerformanceMonitor {
  constructor() {
    this.metrics = {
      chartRenderTime: [],
      domUpdateTime: [],
      dataProcessTime: [],
    };
    this.maxSamples = 30; // 保留最近30次采样
  }

  recordMetric(type, duration) {
    if (!this.metrics[type]) {
      this.metrics[type] = [];
    }

    this.metrics[type].push(duration);

    // 只保留最近的采样
    if (this.metrics[type].length > this.maxSamples) {
      this.metrics[type].shift();
    }
  }

  getAverage(type) {
    const samples = this.metrics[type] || [];
    if (samples.length === 0) return 0;

    const sum = samples.reduce((a, b) => a + b, 0);
    return sum / samples.length;
  }

  getReport() {
    return {
      chartRenderAvg: this.getAverage('chartRenderTime').toFixed(2) + 'ms',
      domUpdateAvg: this.getAverage('domUpdateTime').toFixed(2) + 'ms',
      dataProcessAvg: this.getAverage('dataProcessTime').toFixed(2) + 'ms',
    };
  }
}

// ========== 7. 导出全局实例 ==========
window.WorkspacePerformance = {
  refreshManager: new SmartRefreshManager(),
  domOptimizer: new DOMUpdateOptimizer(),
  perfMonitor: new PerformanceMonitor(),
  fillChartRowsForwardOptimized,
  ChartIncrementalUpdater,
  VirtualEventList,
};

console.log('[WorkspacePerformance] 性能优化模块已加载');
