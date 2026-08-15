/* ========================================================================
   Resource Configuration JavaScript
   ======================================================================== */

// Resource Configuration State
let resourceConfig = null;
let resourceSnapshot = null;
let resourceRefreshInterval = null;

// Initialize Resource Configuration Tab
function initResourceConfig() {
  loadResourceConfig();
  loadResourceSnapshot();

  // Start auto-refresh every 5 seconds when on resource tab
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      if (mutation.type === "attributes" && mutation.attributeName === "class") {
        const resourceTab = document.querySelector('[data-tab-content="resource"]');
        if (resourceTab && resourceTab.classList.contains("active")) {
          if (!resourceRefreshInterval) {
            resourceRefreshInterval = setInterval(loadResourceSnapshot, 5000);
          }
        } else {
          if (resourceRefreshInterval) {
            clearInterval(resourceRefreshInterval);
            resourceRefreshInterval = null;
          }
        }
      }
    });
  });

  const resourceTab = document.querySelector('[data-tab-content="resource"]');
  if (resourceTab) {
    observer.observe(resourceTab, { attributes: true });
  }

  // Event listeners
  getEl("refreshResourceStatus")?.addEventListener("click", async () => {
    await loadResourceSnapshot();
    renderResourceMessage("已刷新", "success");
  });

  getEl("resourceConfigMode")?.addEventListener("change", (event) => {
    toggleManualSettings(event.target.value === "MANUAL");
  });

  getEl("resourceBudgetSlider")?.addEventListener("input", (event) => {
    updateBudgetDisplay(parseInt(event.target.value));
  });

  getEl("saveResourceConfig")?.addEventListener("click", saveResourceConfig);

  getEl("resetResourceConfig")?.addEventListener("click", async () => {
    if (confirm("确定要恢复默认配置吗？")) {
      await resetResourceConfig();
    }
  });
}

// Load Resource Configuration
async function loadResourceConfig() {
  try {
    const response = await fetch("/api/resource-config");
    const data = await response.json();

    if (!data.ok) {
      throw new Error(data.error || "加载资源配置失败");
    }

    resourceConfig = data.config;
    resourceSnapshot = data.runtime;

    // Update UI
    updateResourceConfigUI();
    updateResourceSnapshotUI();
  } catch (error) {
    renderResourceMessage(`加载失败: ${error.message}`, "error");
  }
}

// Load Real-time Resource Snapshot
async function loadResourceSnapshot() {
  try {
    const response = await fetch("/api/resource-config/snapshot");
    const data = await response.json();

    if (!data.ok) {
      throw new Error(data.error || "加载资源快照失败");
    }

    resourceSnapshot = data.data;
    updateResourceSnapshotUI();
  } catch (error) {
    console.error("Failed to load resource snapshot:", error);
  }
}

// Update Configuration UI
function updateResourceConfigUI() {
  if (!resourceConfig) return;

  // Config mode
  setValue("resourceConfigMode", resourceConfig.user_config_mode);
  toggleManualSettings(resourceConfig.user_config_mode === "MANUAL");

  // Budget slider
  const slider = getEl("resourceBudgetSlider");
  if (slider) {
    slider.min = "512";
    slider.max = String(resourceConfig.max_research_budget_mb);
    slider.value = String(resourceConfig.user_research_budget_mb);
    updateBudgetDisplay(resourceConfig.user_research_budget_mb);
  }

  getEl("resourceBudgetMin").textContent = "0.5 GB";
  getEl("resourceBudgetMax").textContent = `${(resourceConfig.max_research_budget_mb / 1024).toFixed(1)} GB`;

  // Manual mode settings
  setValue("resourceLightWorkerMb", resourceConfig.user_light_worker_mb || "");
  setValue("resourceHeavyWorkerMb", resourceConfig.user_heavy_worker_mb || "");
  setValue("resourceBacktestWorkerMb", resourceConfig.user_backtest_worker_mb || "");
  setValue("resourceStandardWorkerLimit", resourceConfig.user_standard_worker_limit || "");

  // Budget hint
  const budgetGb = (resourceConfig.user_research_budget_mb / 1024).toFixed(1);
  const maxGb = (resourceConfig.max_research_budget_mb / 1024).toFixed(1);
  const bufferGb = ((resourceConfig.max_research_budget_mb - resourceConfig.user_research_budget_mb) / 1024).toFixed(1);

  getEl("resourceBudgetHint").textContent =
    `当前设置 ${budgetGb} GB，留出 ${bufferGb} GB 缓冲。系统上限 ${maxGb} GB。`;
}

// Update Snapshot UI
function updateResourceSnapshotUI() {
  if (!resourceSnapshot) return;

  const physical = resourceSnapshot.physical;
  const research = resourceSnapshot.research;

  // Overview metrics
  getEl("resourcePhysicalMemory").textContent = `${(physical.total_mb / 1024).toFixed(1)} GB`;
  getEl("resourceAvailableMemory").textContent = `${(physical.available_mb / 1024).toFixed(1)} GB`;
  getEl("resourceMaxBudget").textContent = `${(research.max_budget_mb / 1024).toFixed(1)} GB`;

  // Usage percentage
  getEl("resourceUsagePercent").textContent = `${research.active_percent}%`;

  // Resource bar
  const totalMb = physical.total_mb;
  const reserves = resourceSnapshot.reserves;
  const systemPct = (reserves.system_mb / totalMb) * 100;
  const frontendPct = (reserves.frontend_mb / totalMb) * 100;
  const activePct = (research.active_mb / totalMb) * 100;
  const userAvailPct = (research.available_now_mb / totalMb) * 100;
  const emergencyPct = (reserves.emergency_mb / totalMb) * 100;

  getEl("resourceSystemBar").style.width = `${systemPct}%`;
  getEl("resourceFrontendBar").style.width = `${frontendPct}%`;
  getEl("resourceActiveBar").style.width = `${activePct}%`;
  getEl("resourceUserBar").style.width = `${userAvailPct}%`;
  getEl("resourceEmergencyBar").style.width = `${emergencyPct}%`;

  // Active workers
  const activeWorkers = resourceSnapshot.active_workers || {};
  const workerCount = Object.keys(activeWorkers).length;

  getEl("resourceActiveCount").textContent =
    workerCount === 0 ? "0 个活跃任务" :
    workerCount === 1 ? "1 个活跃任务" :
    `${workerCount} 个活跃任务`;

  const workerList = getEl("resourceActiveWorkers");
  if (workerList) {
    if (workerCount === 0) {
      workerList.innerHTML = '<p class="settings-note">当前没有运行中的研究任务</p>';
    } else {
      workerList.innerHTML = Object.entries(activeWorkers)
        .map(([token, worker]) => `
          <div class="resource-worker-item">
            <div class="resource-worker-info">
              <div class="resource-worker-name">${escapeHtml(token)}</div>
              <div class="resource-worker-meta">${worker.resource_class} Research</div>
            </div>
            <div class="resource-worker-memory">${(worker.worker_memory_mb / 1024).toFixed(2)} GB</div>
          </div>
        `).join("");
    }
  }
}

// Toggle Manual Settings Visibility
function toggleManualSettings(show) {
  const manualSettings = getEl("resourceManualSettings");
  if (manualSettings) {
    manualSettings.style.display = show ? "block" : "none";
  }
}

// Update Budget Display
function updateBudgetDisplay(valueMb) {
  const valueGb = (valueMb / 1024).toFixed(1);
  getEl("resourceBudgetValue").textContent = `${valueGb} GB`;

  // Update hint based on percentage
  if (resourceConfig) {
    const pct = (valueMb / resourceConfig.max_research_budget_mb) * 100;
    let hint = "";

    if (pct >= 90) {
      hint = "⚠️ 激进模式：几乎用满系统资源，适合夜间无人值守";
    } else if (pct >= 75) {
      hint = "✅ 推荐配置：充分利用资源，留有安全缓冲";
    } else if (pct >= 50) {
      hint = "💡 平衡模式：适合日常使用，为其他应用预留空间";
    } else {
      hint = "🌅 保守模式：优先保证系统流畅，研究任务较慢";
    }

    getEl("resourceBudgetHint").textContent = hint;
  }
}

// Save Resource Configuration
async function saveResourceConfig() {
  const button = getEl("saveResourceConfig");
  if (!button) return;

  setBusy(button, true);
  renderResourceMessage("正在保存配置...", "info");

  try {
    const payload = {
      user_research_budget_mb: parseInt(getValue("resourceBudgetSlider")),
      user_config_mode: getValue("resourceConfigMode"),
    };

    // Manual mode settings
    if (payload.user_config_mode === "MANUAL") {
      const lightMb = getValue("resourceLightWorkerMb");
      const heavyMb = getValue("resourceHeavyWorkerMb");
      const backtestMb = getValue("resourceBacktestWorkerMb");
      const limit = getValue("resourceStandardWorkerLimit");

      if (lightMb) payload.user_light_worker_mb = parseInt(lightMb);
      if (heavyMb) payload.user_heavy_worker_mb = parseInt(heavyMb);
      if (backtestMb) payload.user_backtest_worker_mb = parseInt(backtestMb);
      if (limit) payload.user_standard_worker_limit = parseInt(limit);
    }

    const response = await fetch("/api/resource-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await response.json();

    if (!data.ok) {
      throw new Error(data.message || data.error || "保存失败");
    }

    renderResourceMessage("✅ 配置已保存并生效", "success");

    // Reload config and snapshot
    await loadResourceConfig();
    await loadResourceSnapshot();
  } catch (error) {
    renderResourceMessage(`❌ 保存失败: ${error.message}`, "error");
  } finally {
    setBusy(button, false);
  }
}

// Reset to Default Configuration
async function resetResourceConfig() {
  const button = getEl("resetResourceConfig");
  if (!button) return;

  setBusy(button, true);
  renderResourceMessage("正在恢复默认配置...", "info");

  try {
    // Reset to AUTO mode with 75% of max budget
    if (!resourceConfig) {
      throw new Error("无法读取当前配置");
    }

    const defaultBudget = Math.floor(resourceConfig.max_research_budget_mb * 0.75);

    const payload = {
      user_research_budget_mb: defaultBudget,
      user_config_mode: "AUTO",
      user_light_worker_mb: null,
      user_heavy_worker_mb: null,
      user_backtest_worker_mb: null,
      user_standard_worker_limit: null,
    };

    const response = await fetch("/api/resource-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await response.json();

    if (!data.ok) {
      throw new Error(data.message || data.error || "恢复失败");
    }

    renderResourceMessage("✅ 已恢复默认配置", "success");

    // Reload config and snapshot
    await loadResourceConfig();
    await loadResourceSnapshot();
  } catch (error) {
    renderResourceMessage(`❌ 恢复失败: ${error.message}`, "error");
  } finally {
    setBusy(button, false);
  }
}

// Render Resource Message
function renderResourceMessage(message, type = "info") {
  const messageEl = getEl("resourceConfigMessage");
  if (!messageEl) return;

  messageEl.textContent = message;
  messageEl.className = "status";

  if (type === "success") {
    messageEl.style.color = "var(--green)";
  } else if (type === "error") {
    messageEl.style.color = "var(--red)";
  } else {
    messageEl.style.color = "var(--muted)";
  }

  // Auto-clear after 5 seconds
  setTimeout(() => {
    if (messageEl.textContent === message) {
      messageEl.textContent = "";
    }
  }, 5000);
}

// Initialize when page loads
document.addEventListener("DOMContentLoaded", () => {
  initResourceConfig();
});
