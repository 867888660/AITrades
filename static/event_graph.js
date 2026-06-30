const eventGraphForm = document.getElementById("eventGraphForm");
const eventGraphSearch = document.getElementById("eventGraphSearch");
const eventGraphCategoryInput = document.getElementById("eventGraphCategoryInput");
const eventGraphSort = document.getElementById("eventGraphSort");
const eventGraphLimit = document.getElementById("eventGraphLimit");
const eventGraphRefreshBtn = document.getElementById("eventGraphRefreshBtn");
const eventGraphBadge = document.getElementById("eventGraphBadge");
const eventGraphStats = document.getElementById("eventGraphStats");
const eventGraphMeta = document.getElementById("eventGraphMeta");
const eventGraphCanvas = document.getElementById("eventGraphCanvas");
const eventGraphDetails = document.getElementById("eventGraphDetails");
const eventGraphCategories = document.getElementById("eventGraphCategories");
const clearEventGraphCategory = document.getElementById("clearEventGraphCategory");
const eventGraphRankings = document.getElementById("eventGraphRankings");
const showEventsToggle = document.getElementById("showEventsToggle");
const showFinanceToggle = document.getElementById("showFinanceToggle");
const showSignalsToggle = document.getElementById("showSignalsToggle");
const eventGraphLocateBtn = document.getElementById("eventGraphLocateBtn");
const eventGraphZoomInBtn = document.getElementById("eventGraphZoomInBtn");
const eventGraphZoomOutBtn = document.getElementById("eventGraphZoomOutBtn");
const eventGraphResetViewBtn = document.getElementById("eventGraphResetViewBtn");

let eventGraphChart = null;
let eventGraphData = { nodes: [], edges: [], summary: {}, event_rankings: [] };
let selectedNodeId = "";
let lastFocusedNodeId = "";
let focusRetryTimer = null;

const NODE_COLORS = {
  EVENT: "#7db7ff",
  FINANCE: "#38d7b6",
  SIGNAL: "#f6c967",
};

const NODE_CATEGORY_INDEX = {
  EVENT: 0,
  FINANCE: 1,
  SIGNAL: 2,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function statusHtml(text, tone = "") {
  return `<div class="status ${tone}">${escapeHtml(text)}</div>`;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "-";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return num.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function formatHeat(value) {
  const num = Number(value ?? 0);
  return Number.isFinite(num) ? `${num.toFixed(1)}` : "-";
}

function nodeTone(type) {
  return String(type || "").toLowerCase();
}

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function graphQueryString(forceRefresh = false) {
  const params = new URLSearchParams();
  const query = eventGraphSearch?.value?.trim();
  const category = eventGraphCategoryInput?.value?.trim();
  if (query) params.set("q", query);
  if (category) params.set("category", category);
  params.set("sort", eventGraphSort?.value || "volume24h");
  params.set("order", "desc");
  params.set("limit", eventGraphLimit?.value || "80");
  if (forceRefresh) params.set("refresh", "1");
  return params.toString();
}

function visibleTypes() {
  const types = new Set();
  if (showEventsToggle?.checked) types.add("EVENT");
  if (showFinanceToggle?.checked) types.add("FINANCE");
  if (showSignalsToggle?.checked) types.add("SIGNAL");
  return types;
}

function visibleGraph() {
  const types = visibleTypes();
  const nodes = (eventGraphData.nodes || []).filter((node) => types.has(node.type));
  const ids = new Set(nodes.map((node) => node.id));
  const edges = (eventGraphData.edges || []).filter((edge) => ids.has(edge.source) && ids.has(edge.target));
  return { nodes, edges };
}

function stableHash(value) {
  const text = String(value || "");
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash >>> 0);
}

function buildStableNodePositions(nodes, edges) {
  const positions = new Map();
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const eventRank = new Map((eventGraphData.event_rankings || []).map((node, index) => [node.id, index]));
  const events = nodes
    .filter((node) => node.type === "EVENT")
    .sort((a, b) => (eventRank.get(a.id) ?? 9999) - (eventRank.get(b.id) ?? 9999));
  const nonEvents = nodes.filter((node) => node.type !== "EVENT");

  const innerCount = Math.min(events.length, 12);
  const outerCount = Math.max(0, events.length - innerCount);
  const innerRadiusX = events.length <= 6 ? 360 : 520;
  const innerRadiusY = events.length <= 6 ? 230 : 330;
  events.forEach((node, index) => {
    const isInner = index < innerCount;
    const ringIndex = isInner ? index : index - innerCount;
    const ringCount = isInner ? Math.max(innerCount, 1) : Math.max(outerCount, 1);
    const angleOffset = isInner ? -Math.PI / 2 : -Math.PI / 2 + Math.PI / Math.max(ringCount, 8);
    const angle = angleOffset + (Math.PI * 2 * ringIndex) / ringCount;
    const radiusX = isInner ? innerRadiusX : innerRadiusX + 360;
    const radiusY = isInner ? innerRadiusY : innerRadiusY + 250;
    positions.set(node.id, {
      x: Math.cos(angle) * radiusX,
      y: Math.sin(angle) * radiusY,
    });
  });

  const attached = new Map();
  const relationWeight = {
    DIRECTLY_PRICES: 5,
    TRACKS: 4,
    IMPACTS: 3,
    RELATED_TO: 2,
  };
  edges.forEach((edge) => {
    const source = nodeById.get(edge.source);
    const target = nodeById.get(edge.target);
    if (!source || !target) return;
    const event = source.type === "EVENT" ? source : target.type === "EVENT" ? target : null;
    const other = event?.id === source.id ? target : source;
    if (!event || !other || other.type === "EVENT") return;
    if (!attached.has(event.id)) attached.set(event.id, []);
    attached.get(event.id).push({
      id: other.id,
      weight: relationWeight[edge.relation_type] || 1,
      confidence: Number(edge.confidence || 0),
    });
  });

  attached.forEach((rows, eventId) => {
    const base = positions.get(eventId);
    if (!base) return;
    const seen = new Set();
    const ids = rows
      .sort((a, b) => (b.weight + b.confidence) - (a.weight + a.confidence))
      .filter((row) => {
        if (seen.has(row.id)) return false;
        seen.add(row.id);
        return true;
      })
      .map((row) => row.id);
    ids.forEach((id, index) => {
      if (positions.has(id)) return;
      const node = nodeById.get(id);
      const ringSize = 10;
      const ring = Math.floor(index / ringSize);
      const slot = index % ringSize;
      const direction = Math.atan2(base.y, base.x) || -Math.PI / 2;
      const fan = Math.PI * 1.15;
      const angle = direction - fan / 2 + (fan * (slot + 0.5)) / ringSize;
      const radius = (node?.type === "SIGNAL" ? 145 : 185) + ring * 84;
      const jitter = ((stableHash(id) % 100) / 100 - 0.5) * 28;
      positions.set(id, {
        x: base.x + Math.cos(angle) * (radius + jitter),
        y: base.y + Math.sin(angle) * (radius * 0.78 + jitter),
      });
    });
  });

  const orphanByType = {
    FINANCE: nonEvents.filter((node) => node.type === "FINANCE" && !positions.has(node.id)),
    SIGNAL: nonEvents.filter((node) => node.type === "SIGNAL" && !positions.has(node.id)),
  };
  orphanByType.FINANCE.forEach((node, index) => {
    positions.set(node.id, { x: -980, y: -360 + index * 112 });
  });
  orphanByType.SIGNAL.forEach((node, index) => {
    positions.set(node.id, { x: 980, y: -360 + index * 112 });
  });

  return positions;
}

function renderStats(summary = {}) {
  if (!eventGraphStats) return;
  const cards = [
    ["Events", summary.events ?? 0, "canonical event buckets"],
    ["Markets", summary.markets ?? 0, "Polymarket markets scanned"],
    ["Finance", summary.finance_nodes ?? 0, "market and asset nodes"],
    ["Edges", summary.edges ?? 0, `max heat ${formatHeat(summary.max_heat)}`],
  ];
  eventGraphStats.innerHTML = cards.map(([label, value, sub]) => `
    <article class="card event-graph-stat-card">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value">${escapeHtml(formatNumber(value, 0))}</div>
      <div class="subvalue">${escapeHtml(sub)}</div>
    </article>
  `).join("");
}

function renderCategories(rows = []) {
  if (!eventGraphCategories) return;
  if (!rows.length) {
    eventGraphCategories.innerHTML = statusHtml("暂无分类");
    return;
  }
  const current = String(eventGraphCategoryInput?.value || "").trim().toLowerCase();
  eventGraphCategories.innerHTML = rows.slice(0, 80).map((item) => {
    const name = String(item.name || "Unknown");
    const active = current && current === name.toLowerCase() ? " active" : "";
    return `<button class="event-graph-category-chip${active}" type="button" data-category="${escapeHtml(name)}">${escapeHtml(name)} <span>${escapeHtml(item.count ?? "")}</span></button>`;
  }).join("");
}

function renderRankings(rows = []) {
  if (!eventGraphRankings) return;
  if (!rows.length) {
    eventGraphRankings.innerHTML = statusHtml("暂无高热事件");
    return;
  }
  eventGraphRankings.innerHTML = rows.slice(0, 18).map((node, index) => `
    <button class="event-rank-row ${selectedNodeId === node.id ? "active" : ""}" type="button" data-node-id="${escapeHtml(node.id)}">
      <span class="event-rank-index">${index + 1}</span>
      <span class="event-rank-main">
        <strong>${escapeHtml(node.label)}</strong>
        <small>${escapeHtml(node.subtitle || node.source_type || "")}</small>
      </span>
      <span class="event-rank-heat">${escapeHtml(formatHeat(node.heat))}</span>
    </button>
  `).join("");
}

function initChart() {
  if (!eventGraphCanvas || !window.echarts) return null;
  if (!eventGraphChart) {
    eventGraphChart = echarts.init(eventGraphCanvas, null, { renderer: "canvas" });
    eventGraphChart.on("click", (params) => {
      if (params.dataType === "node" && params.data?.id) {
        selectNode(params.data.id);
      }
    });
    window.addEventListener("resize", () => eventGraphChart?.resize());
  }
  return eventGraphChart;
}

function renderChart() {
  const chart = initChart();
  if (!chart) return;
  const { nodes, edges } = visibleGraph();
  if (!nodes.length) {
    chart.clear();
    if (eventGraphMeta) eventGraphMeta.textContent = "当前筛选下没有节点";
    return;
  }
  const positions = buildStableNodePositions(nodes, edges);
  const chartNodes = nodes.map((node) => {
    const heat = Number(node.heat || 1);
    const rankIndex = (eventGraphData.event_rankings || []).findIndex((item) => item.id === node.id);
    const sizeBase = node.type === "EVENT" ? 24 : node.type === "FINANCE" ? 16 : 12;
    const size = Math.max(sizeBase, Math.min(node.type === "EVENT" ? 58 : 34, sizeBase + heat * (node.type === "EVENT" ? 0.32 : 0.18)));
    const position = positions.get(node.id) || { x: 0, y: 0 };
    const isSelected = selectedNodeId === node.id;
    const showLabel = isSelected || (node.type === "EVENT" && (rankIndex >= 0 ? rankIndex < 14 : heat >= 65));
    return {
      id: node.id,
      name: node.label,
      value: heat,
      x: position.x,
      y: position.y,
      fixed: true,
      category: NODE_CATEGORY_INDEX[node.type] ?? 0,
      symbolSize: size,
      draggable: true,
      itemStyle: {
        color: NODE_COLORS[node.type] || "#90a5c3",
        borderColor: isSelected ? "#ffffff" : "rgba(235,245,255,0.42)",
        borderWidth: isSelected ? 4 : 1,
        shadowBlur: isSelected ? 24 : node.type === "EVENT" ? 14 : 8,
        shadowColor: NODE_COLORS[node.type] || "rgba(144,165,195,0.55)",
      },
      label: {
        show: showLabel,
        color: isSelected ? "#ffffff" : "#dcecff",
        fontSize: node.type === "EVENT" ? 12 : 10,
        fontWeight: node.type === "EVENT" ? 700 : 600,
        width: node.type === "EVENT" ? 150 : 110,
        overflow: "truncate",
        position: "bottom",
        distance: 8,
        backgroundColor: isSelected ? "rgba(8,14,26,0.78)" : "rgba(8,14,26,0.52)",
        borderColor: isSelected ? "rgba(255,255,255,0.38)" : "rgba(255,255,255,0.08)",
        borderWidth: 1,
        borderRadius: 6,
        padding: [3, 6],
      },
      emphasis: {
        scale: 1.18,
        label: {
          show: true,
          color: "#ffffff",
          fontWeight: 800,
          backgroundColor: "rgba(8,14,26,0.9)",
          borderColor: "rgba(255,255,255,0.28)",
          borderWidth: 1,
          borderRadius: 6,
          padding: [4, 7],
        },
      },
      raw: node,
    };
  });
  const chartEdges = edges.map((edge) => ({
    source: edge.source,
    target: edge.target,
    value: edge.confidence ?? 0.5,
    lineStyle: {
      width: edge.relation_type === "DIRECTLY_PRICES" ? 1.8 : 1,
      opacity: edge.relation_type === "DIRECTLY_PRICES" ? 0.5 : 0.22,
      curveness: 0.18,
    },
    label: {
      show: false,
      formatter: edge.relation_type,
    },
    raw: edge,
  }));
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      borderWidth: 0,
      backgroundColor: "rgba(9,16,28,0.96)",
      extraCssText: "box-shadow: 0 18px 44px rgba(0,0,0,0.38); border-radius: 10px; padding: 10px 12px;",
      textStyle: { color: "#e5eefc" },
      formatter: (params) => {
        if (params.dataType === "edge") {
          const edge = params.data.raw || {};
          return `<strong>${escapeHtml(edge.relation_type || "Edge")}</strong><br>${escapeHtml(edge.reason || "")}`;
        }
        const node = params.data.raw || {};
        return `<strong>${escapeHtml(node.label || "")}</strong><br>${escapeHtml(node.type || "")} · heat ${escapeHtml(formatHeat(node.heat))}`;
      },
    },
    legend: { show: false },
    series: [{
      type: "graph",
      layout: "none",
      roam: true,
      center: ["50%", "50%"],
      zoom: nodes.length > 120 ? 0.34 : nodes.length > 80 ? 0.42 : nodes.length > 48 ? 0.54 : 0.72,
      scaleLimit: { min: 0.16, max: 5.2 },
      data: chartNodes,
      links: chartEdges,
      categories: [
        { name: "Event" },
        { name: "Finance" },
        { name: "Signal" },
      ],
      emphasis: {
        focus: "adjacency",
        lineStyle: { opacity: 0.92, width: 2.6 },
      },
      blur: {
        itemStyle: { opacity: 0.18 },
        lineStyle: { opacity: 0.035 },
        label: { opacity: 0.12 },
      },
      edgeSymbol: ["none", "arrow"],
      edgeSymbolSize: [0, 6],
      labelLayout: { hideOverlap: true },
      lineStyle: {
        color: "source",
        cap: "round",
      },
    }],
  }, true);
  if (eventGraphMeta) {
    eventGraphMeta.textContent = `${nodes.length} nodes · ${edges.length} edges · ${eventGraphData.source || "derived preview"}`;
  }
}

function chartNodeIndex(nodeId) {
  const option = eventGraphChart?.getOption?.();
  const data = option?.series?.[0]?.data || [];
  return data.findIndex((item) => item?.id === nodeId);
}

function chartNodePixel(nodeId) {
  const chart = eventGraphChart;
  if (!chart) return null;
  const dataIndex = chartNodeIndex(nodeId);
  if (dataIndex < 0) return null;
  const series = chart.getModel()?.getSeriesByIndex?.(0);
  const layout = series?.getData?.()?.getItemLayout?.(dataIndex);
  if (!Array.isArray(layout) || layout.length < 2) return null;
  try {
    const converted = chart.convertToPixel({ seriesIndex: 0 }, layout);
    if (Array.isArray(converted) && Number.isFinite(converted[0]) && Number.isFinite(converted[1])) {
      return { dataIndex, x: converted[0], y: converted[1] };
    }
  } catch (_) {
    // Force graph layouts are already stored as canvas pixels in this ECharts build.
  }
  if (Number.isFinite(layout[0]) && Number.isFinite(layout[1])) {
    return { dataIndex, x: layout[0], y: layout[1] };
  }
  return null;
}

function focusNodeInChart(nodeId, options = {}) {
  const chart = eventGraphChart;
  if (!chart || !nodeId) return false;
  const point = chartNodePixel(nodeId);
  if (!point) {
    const retries = Number(options.retries ?? 8);
    if (retries > 0) {
      window.clearTimeout(focusRetryTimer);
      focusRetryTimer = window.setTimeout(() => {
        focusNodeInChart(nodeId, { ...options, retries: retries - 1 });
      }, Number(options.delay ?? 50));
    } else if (eventGraphMeta) {
      eventGraphMeta.textContent = "Target node is hidden by current filters.";
    }
    return false;
  }

  chart.dispatchAction({ type: "downplay", seriesIndex: 0 });
  chart.dispatchAction({ type: "highlight", seriesIndex: 0, dataIndex: point.dataIndex });
  chart.dispatchAction({ type: "focusNodeAdjacency", seriesIndex: 0, dataIndex: point.dataIndex });

  const rect = eventGraphCanvas.getBoundingClientRect();
  chart.dispatchAction({
    type: "graphRoam",
    seriesIndex: 0,
    dx: rect.width / 2 - point.x,
    dy: rect.height / 2 - point.y,
  });
  if (options.zoom) {
    chart.dispatchAction({
      type: "graphRoam",
      seriesIndex: 0,
      zoom: Number(options.zoom),
      originX: rect.width / 2,
      originY: rect.height / 2,
    });
  }

  lastFocusedNodeId = nodeId;
  if (options.showTip !== false) {
    chart.dispatchAction({ type: "showTip", seriesIndex: 0, dataIndex: point.dataIndex });
  }
  if (eventGraphMeta) {
    const node = findNode(nodeId);
    eventGraphMeta.textContent = node ? `Located: ${node.label}` : "Located node";
  }
  return true;
}

function roamGraph(payload = {}) {
  if (!eventGraphChart || !eventGraphCanvas) return;
  const rect = eventGraphCanvas.getBoundingClientRect();
  eventGraphChart.dispatchAction({
    type: "graphRoam",
    seriesIndex: 0,
    originX: rect.width / 2,
    originY: rect.height / 2,
    ...payload,
  });
}

function locateSelectedNode() {
  const target = selectedNodeId || (eventGraphData.event_rankings || [])[0]?.id || (eventGraphData.nodes || [])[0]?.id;
  if (target) selectNode(target);
}

function resetGraphView() {
  renderChart();
  if (selectedNodeId && findNode(selectedNodeId)) {
    window.requestAnimationFrame(() => focusNodeInChart(selectedNodeId, { showTip: false, zoom: false }));
  }
}

function findNode(nodeId) {
  return (eventGraphData.nodes || []).find((node) => node.id === nodeId);
}

function connectedEdges(nodeId) {
  return (eventGraphData.edges || []).filter((edge) => edge.source === nodeId || edge.target === nodeId);
}

function connectedNodes(nodeId) {
  const ids = new Set();
  connectedEdges(nodeId).forEach((edge) => {
    ids.add(edge.source === nodeId ? edge.target : edge.source);
  });
  return [...ids].map(findNode).filter(Boolean);
}

function graphCoreObjectType(node) {
  if (!node || node.source_type !== "GRAPH_CORE") return "";
  if (node.type === "EVENT") return "event";
  if (node.type === "FINANCE") return "finance";
  if (node.type === "SIGNAL" && node.details?.signal_type === "GRAPH_EXPRESSION") return "expression";
  return "";
}

function versionSummary(version = {}) {
  const patch = version.patch_item || {};
  const after = version.after || {};
  const before = version.before || {};
  const action = patch.action || version.change_type || "-";
  const label = after.title || after.label || before.title || before.label || after.lifecycle || before.lifecycle || "";
  return [action, label].filter(Boolean).join(" · ");
}

function renderVersionRows(payload = {}) {
  const versions = Array.isArray(payload.versions) ? payload.versions : [];
  if (!versions.length) return statusHtml("暂无版本记录");
  return `
    <div class="event-version-list">
      ${versions.map((version) => `
        <div class="event-version-row">
          <div>
            <strong>v${escapeHtml(version.version_number ?? "-")}</strong>
            <span>${escapeHtml(versionSummary(version))}</span>
          </div>
          <small>${escapeHtml(version.created_at_utc || "")}</small>
          <small>request ${escapeHtml(version.request_id || "-")} · run ${escapeHtml(version.run_id || "-")}</small>
        </div>
      `).join("")}
    </div>
  `;
}

async function loadGraphVersionHistory(objectType, objectId) {
  const target = document.getElementById("eventGraphVersionHistory");
  if (!target || !objectType || !objectId) return;
  target.innerHTML = statusHtml("加载版本历史...", "pending");
  try {
    const payload = await fetchJson(`/api/agent/event-graph/core/versions?object_type=${encodeURIComponent(objectType)}&object_id=${encodeURIComponent(objectId)}&limit=12&actor_type=human&actor_id=local_user`);
    target.innerHTML = renderVersionRows(payload.data || {});
  } catch (error) {
    target.innerHTML = statusHtml(error.message || "版本历史加载失败", "error");
  }
}

function metricGrid(metrics = {}) {
  const entries = Object.entries(metrics || {}).filter(([, value]) => value !== "" && value !== null && value !== undefined);
  if (!entries.length) return "";
  return `
    <div class="event-detail-metrics">
      ${entries.map(([key, value]) => `
        <div>
          <span>${escapeHtml(key)}</span>
          <strong>${escapeHtml(typeof value === "number" ? formatNumber(value, 2) : value)}</strong>
        </div>
      `).join("")}
    </div>
  `;
}

function renderMarkets(markets = []) {
  if (!Array.isArray(markets) || !markets.length) return "";
  return `
    <section class="event-detail-section">
      <h3>Top Markets</h3>
      <div class="event-detail-market-list">
        ${markets.map((market) => `
          <a class="event-detail-market" href="${escapeHtml(market.url || "#")}" target="_blank" rel="noopener noreferrer">
            <strong>${escapeHtml(market.question || market.condition_id || "-")}</strong>
            <span>heat ${escapeHtml(formatHeat(market.heat))} · 24h ${escapeHtml(formatNumber(market.volume_24h, 0))} · liq ${escapeHtml(formatNumber(market.liquidity, 0))}</span>
          </a>
        `).join("")}
      </div>
    </section>
  `;
}

function renderDetails(node) {
  if (!eventGraphDetails) return;
  if (!node) {
    eventGraphDetails.innerHTML = statusHtml("点击图中的 Event、Finance 或 Signal 查看详情");
    return;
  }
  const details = node.details || {};
  const edges = connectedEdges(node.id);
  const neighbors = connectedNodes(node.id);
  const titleMeta = [
    node.type,
    node.verification_status,
    node.source_type,
  ].filter(Boolean).join(" · ");
  const nodeUrl = details.url ? `
    <a class="table-link-button" href="${escapeHtml(details.url)}" target="_blank" rel="noopener noreferrer">打开来源</a>
  ` : "";
  const versionObjectType = graphCoreObjectType(node);
  const versionSection = versionObjectType ? `
    <section class="event-detail-section">
      <div class="event-detail-section-head">
        <h3>Version History</h3>
        <button class="mini ghost" type="button" data-graph-version-type="${escapeHtml(versionObjectType)}" data-graph-version-id="${escapeHtml(node.id)}">加载</button>
      </div>
      <div id="eventGraphVersionHistory">${statusHtml("点击加载查看最近版本")}</div>
    </section>
  ` : "";
  const rules = details.rules || details.rules_sample;
  eventGraphDetails.innerHTML = `
    <article class="event-detail-card ${nodeTone(node.type)}">
      <div class="event-detail-title-row">
        <div>
          <div class="event-detail-type">${escapeHtml(titleMeta)}</div>
          <h3>${escapeHtml(node.label)}</h3>
          <p>${escapeHtml(node.subtitle || "")}</p>
        </div>
        <div class="event-detail-heat">${escapeHtml(formatHeat(node.heat))}</div>
      </div>
      ${nodeUrl}
      ${metricGrid(details.heat_metrics || details.metrics)}
      ${details.question ? `
        <section class="event-detail-section">
          <h3>Question</h3>
          <p>${escapeHtml(details.question)}</p>
        </section>
      ` : ""}
      ${rules ? `
        <section class="event-detail-section">
          <h3>Rules / Evidence Sample</h3>
          <p>${escapeHtml(rules)}</p>
        </section>
      ` : ""}
      ${renderMarkets(details.top_markets)}
      ${versionSection}
      <section class="event-detail-section">
        <h3>Relations</h3>
        <div class="event-detail-edge-list">
          ${edges.length ? edges.map((edge) => {
            const otherId = edge.source === node.id ? edge.target : edge.source;
            const other = findNode(otherId);
            return `
              <button class="event-detail-edge" type="button" data-node-id="${escapeHtml(otherId)}">
                <span>${escapeHtml(edge.relation_type)}</span>
                <strong>${escapeHtml(other?.label || otherId)}</strong>
                <small>${escapeHtml(edge.strength || "")} · conf ${escapeHtml(edge.confidence ?? "-")}</small>
              </button>
            `;
          }).join("") : statusHtml("暂无关系")}
        </div>
      </section>
      ${neighbors.length ? `
        <section class="event-detail-section">
          <h3>Connected Nodes</h3>
          <div class="event-detail-neighbor-list">
            ${neighbors.map((item) => `
              <button class="event-detail-neighbor ${nodeTone(item.type)}" type="button" data-node-id="${escapeHtml(item.id)}">${escapeHtml(item.label)}</button>
            `).join("")}
          </div>
        </section>
      ` : ""}
    </article>
  `;
}

function selectNode(nodeId) {
  if (!findNode(nodeId)) return;
  selectedNodeId = nodeId;
  renderRankings(eventGraphData.event_rankings || []);
  renderDetails(findNode(nodeId));
  window.requestAnimationFrame(() => focusNodeInChart(nodeId));
}

async function loadCategories() {
  const payload = await fetchJson("/api/event-graph/categories?limit=160");
  renderCategories(payload.data || []);
}

async function loadGraph(options = {}) {
  const forceRefresh = Boolean(options.forceRefresh);
  if (eventGraphBadge) {
    eventGraphBadge.textContent = forceRefresh ? "Refreshing" : "Loading";
    eventGraphBadge.className = "badge pending";
  }
  if (eventGraphMeta) eventGraphMeta.textContent = "加载图谱中...";
  const payload = await fetchJson(`/api/event-graph?${graphQueryString(forceRefresh)}`);
  eventGraphData = payload;
  renderStats(payload.summary || {});
  renderCategories((await fetchJson("/api/event-graph/categories?limit=160")).data || []);
  renderRankings(payload.event_rankings || []);
  renderChart();
  if (eventGraphBadge) {
    eventGraphBadge.textContent = payload.source || "Preview";
    eventGraphBadge.className = "badge good";
  }
  if (selectedNodeId && findNode(selectedNodeId)) {
    renderDetails(findNode(selectedNodeId));
    window.requestAnimationFrame(() => focusNodeInChart(selectedNodeId, { showTip: false }));
  } else {
    const first = (payload.event_rankings || [])[0] || (payload.nodes || [])[0];
    if (first) {
      selectNode(first.id);
    } else {
      renderDetails(null);
    }
  }
}

eventGraphForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  selectedNodeId = "";
  loadGraph().catch((error) => {
    if (eventGraphMeta) eventGraphMeta.textContent = error.message;
    if (eventGraphBadge) {
      eventGraphBadge.textContent = "Error";
      eventGraphBadge.className = "badge error";
    }
  });
});

eventGraphRefreshBtn?.addEventListener("click", () => {
  selectedNodeId = "";
  loadGraph({ forceRefresh: true }).catch((error) => {
    if (eventGraphMeta) eventGraphMeta.textContent = error.message;
  });
});

[showEventsToggle, showFinanceToggle, showSignalsToggle].forEach((el) => {
  el?.addEventListener("change", () => {
    renderChart();
    if (selectedNodeId && findNode(selectedNodeId)) {
      window.requestAnimationFrame(() => focusNodeInChart(selectedNodeId, { showTip: false }));
    }
  });
});

eventGraphCategories?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-category]");
  if (!button) return;
  if (eventGraphCategoryInput) eventGraphCategoryInput.value = button.dataset.category || "";
  selectedNodeId = "";
  loadGraph().catch((error) => {
    if (eventGraphMeta) eventGraphMeta.textContent = error.message;
  });
});

clearEventGraphCategory?.addEventListener("click", () => {
  if (eventGraphCategoryInput) eventGraphCategoryInput.value = "";
  selectedNodeId = "";
  loadGraph().catch((error) => {
    if (eventGraphMeta) eventGraphMeta.textContent = error.message;
  });
});

eventGraphRankings?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-node-id]");
  if (button) selectNode(button.dataset.nodeId);
});

eventGraphDetails?.addEventListener("click", (event) => {
  const versionButton = event.target.closest("button[data-graph-version-type][data-graph-version-id]");
  if (versionButton) {
    loadGraphVersionHistory(versionButton.dataset.graphVersionType, versionButton.dataset.graphVersionId);
    return;
  }
  const button = event.target.closest("button[data-node-id]");
  if (button) selectNode(button.dataset.nodeId);
});

eventGraphLocateBtn?.addEventListener("click", () => locateSelectedNode());
eventGraphZoomInBtn?.addEventListener("click", () => roamGraph({ zoom: 1.18 }));
eventGraphZoomOutBtn?.addEventListener("click", () => roamGraph({ zoom: 0.84 }));
eventGraphResetViewBtn?.addEventListener("click", () => resetGraphView());

loadCategories().catch(() => {});
loadGraph().catch((error) => {
  if (eventGraphMeta) eventGraphMeta.textContent = error.message;
  if (eventGraphDetails) eventGraphDetails.innerHTML = statusHtml(error.message, "error");
  if (eventGraphBadge) {
    eventGraphBadge.textContent = "Error";
    eventGraphBadge.className = "badge error";
  }
});
