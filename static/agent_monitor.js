const agentMeta = document.getElementById("agentMeta");
const agentPendingApprovals = document.getElementById("agentPendingApprovals");
const agentPendingCount = document.getElementById("agentPendingCount");
const agentActivityList = document.getElementById("agentActivityList");
const agentActivityCount = document.getElementById("agentActivityCount");
const agentActivityModeLabel = document.getElementById("agentActivityModeLabel");
const agentActivitySearch = document.getElementById("agentActivitySearch");
const agentActivityCategory = document.getElementById("agentActivityCategory");
const agentActivityPinBtn = document.getElementById("agentActivityPinBtn");
const agentActivityClearBtn = document.getElementById("agentActivityClearBtn");
const agentActivityFilters = document.getElementById("agentActivityFilters");
const agentDraftList = document.getElementById("agentDraftList");
const agentDraftCount = document.getElementById("agentDraftCount");
const refreshAgentBtn = document.getElementById("refreshAgentBtn");
const agentApprovalModal = document.getElementById("agentApprovalModal");
const agentApprovalModalClose = document.getElementById("agentApprovalModalClose");
const agentApprovalCloseBtn = document.getElementById("agentApprovalCloseBtn");
const agentApprovalTitleEl = document.getElementById("agentApprovalTitle");
const agentApprovalSubtitleEl = document.getElementById("agentApprovalSubtitle");
const agentApprovalBody = document.getElementById("agentApprovalBody");
const agentApprovalSaveBtn = document.getElementById("agentApprovalSaveBtn");
const agentApprovalApproveBtn = document.getElementById("agentApprovalApproveBtn");
const agentApprovalChangeBtn = document.getElementById("agentApprovalChangeBtn");
const agentApprovalRejectBtn = document.getElementById("agentApprovalRejectBtn");
const agentAuditModal = document.getElementById("agentAuditModal");
const agentAuditModalClose = document.getElementById("agentAuditModalClose");
const agentAuditCloseBtn = document.getElementById("agentAuditCloseBtn");
const agentAuditTitle = document.getElementById("agentAuditTitle");
const agentAuditSubtitle = document.getElementById("agentAuditSubtitle");
const agentAuditBody = document.getElementById("agentAuditBody");
const agentMonitorTabs = document.querySelectorAll("[data-agent-monitor-tab]");
const agentMonitorPanes = document.querySelectorAll("[data-agent-monitor-pane]");
const agentOverviewCards = document.getElementById("agentOverviewCards");
const agentOverviewPending = document.getElementById("agentOverviewPending");
const agentOverviewPendingCount = document.getElementById("agentOverviewPendingCount");
const agentOverviewInternal = document.getElementById("agentOverviewInternal");
const agentOverviewInternalCount = document.getElementById("agentOverviewInternalCount");
const agentInternalList = document.getElementById("agentInternalList");
const agentInternalCount = document.getElementById("agentInternalCount");
const agentExternalActivityList = document.getElementById("agentExternalActivityList");
const agentExternalActivityCount = document.getElementById("agentExternalActivityCount");
const internalEventSearchForm = document.getElementById("internalEventSearchForm");
const internalEventSearchInput = document.getElementById("internalEventSearchInput");
const internalEventSearchLimit = document.getElementById("internalEventSearchLimit");
const internalEventSearchMeta = document.getElementById("internalEventSearchMeta");
const internalEventSearchResult = document.getElementById("internalEventSearchResult");
const internalEventGraphLink = document.getElementById("internalEventGraphLink");
const graphChangeForm = document.getElementById("graphChangeForm");
const graphChangeType = document.getElementById("graphChangeType");
const graphChangeTitle = document.getElementById("graphChangeTitle");
const graphChangeEvidence = document.getElementById("graphChangeEvidence");
const graphChangePatch = document.getElementById("graphChangePatch");
const graphChangeValidateBtn = document.getElementById("graphChangeValidateBtn");
const graphChangeValidation = document.getElementById("graphChangeValidation");
const graphChangeMeta = document.getElementById("graphChangeMeta");

let hasLoadedAgentDashboard = false;
let activeAgentApproval = null;
let agentRefreshTimer = null;
let agentAuditRows = [];
let filteredAgentAuditRows = [];
let agentGraphChangeRows = [];

const AGENT_ACTIVITY_PIN_KEY = "agent_monitor_activity_pinned_category";

const AGENT_APPROVAL_MODES = ["Stop", "Virtual", "Real"];
const AGENT_DEADLINE_PARAM = {
  name: "Enddate",
  kind: "String",
  required: false,
  default: "",
  description: "Strategy deadline. Auto-filled from the selected market end date; edit it if the market date is wrong.",
};
const AGENT_DEADLINE_ALIASES = new Set(["enddate", "endtime", "l0endtime"]);
const AGENT_AUDIT_CATEGORIES = {
  all: { label: "全部类别", tone: "all" },
  market: { label: "市场/搜索", tone: "market" },
  strategy_read: { label: "策略查看", tone: "read" },
  draft: { label: "草案", tone: "draft" },
  risk: { label: "风控", tone: "risk" },
  simulation: { label: "模拟", tone: "simulation" },
  approval: { label: "审批", tone: "approval" },
  audit: { label: "审计/系统", tone: "audit" },
  event_graph: { label: "EventGraph", tone: "event" },
  other: { label: "其它", tone: "other" },
};

const AGENT_AUDIT_CAPABILITY_LABELS = {
  "market.category.list": "查看市场分类",
  "market.search": "搜索市场",
  "market.resolve": "解析市场",
  "market.hot_scan": "热门市场扫描",
  "strategy.read_all": "查看策略列表",
  "strategy.detail.read": "查看策略详情",
  "strategy.workspace.read": "查看策略工作台",
  "strategy.usedata.read": "查看 UseData",
  "strategy.events.read": "查看 action 日志",
  "strategy.state.read": "查看策略 State",
  "strategy.draft.list": "查看草案列表",
  "strategy.draft.read": "查看草案",
  "strategy.draft.create": "创建草案",
  "strategy.draft.update": "修改草案",
  "strategy.draft.delete": "删除草案",
  "strategy.batch.propose": "批量生成策略",
  "risk.check": "风控检查",
  "strategy.simulate": "策略模拟",
  "strategy.submit": "提交人工确认",
  "strategy.approval.update_draft": "人工修改审批参数",
  "strategy.approve": "人工批准",
  "strategy.reject": "人工拒绝",
  "strategy.request_changes": "要求修改",
  "approval.status": "查看审批",
  "agent.activity": "写入活动",
  "audit.clear": "清除审计",
  "event.read": "读取 EventGraph",
  "event.news.refresh": "刷新新闻事件",
  "event.news.search": "检索新闻事件",
  "event.graph.patch.validate": "校验图谱 Patch",
  "event.graph.change_request": "提交图谱变更",
  "event.investigation.claim": "领取调查任务",
  "event.investigation.report": "提交调查报告",
  "event.proposal.create": "创建图谱 Proposal",
  "event.proposal.review": "审核图谱 Proposal",
};

const AGENT_ACTOR_GROUPS = {
  internal: {
    label: "EventGraph 变更",
    description: "历史内置记录或图谱变更相关活动",
    actorTypes: new Set(["internal_agent"]),
  },
  external: {
    label: "外接 Agent",
    description: "外部接口、策略草案、风控、模拟和审批提交",
    actorTypes: new Set(["agent", "external_agent"]),
  },
  human: {
    label: "人工",
    description: "审批、修改、拒绝和系统设置",
    actorTypes: new Set(["human"]),
  },
  system: {
    label: "系统",
    description: "后台任务、自动采集和系统维护",
    actorTypes: new Set(["system"]),
  },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(container, text) {
  if (!container) return;
  container.innerHTML = `<div class="status">${escapeHtml(text)}</div>`;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "-";
  const num = Number(value);
  return Number.isFinite(num) ? num.toLocaleString(undefined, { maximumFractionDigits: digits }) : String(value);
}

function formatFixed(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "-";
  const num = Number(value);
  return Number.isFinite(num)
    ? num.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : String(value);
}

function firstPresent(...values) {
  return values.find((value) => value !== null && value !== undefined && value !== "");
}

function marketSideBidAsk(market = {}) {
  const outcome = String(market.outcome || "YES").toUpperCase();
  const bid = outcome === "NO"
    ? firstPresent(market.no_bid, market.raw?.no_bid, market.raw?.noBid)
    : firstPresent(market.yes_bid, market.best_bid, market.raw?.yes_bid, market.raw?.bestBid);
  const ask = outcome === "NO"
    ? firstPresent(market.no_ask, market.raw?.no_ask, market.raw?.noAsk)
    : firstPresent(market.yes_ask, market.best_ask, market.raw?.yes_ask, market.raw?.bestAsk);
  return `${formatFixed(bid, 4)} / ${formatFixed(ask, 4)}`;
}

function marketAllBidAsk(market = {}) {
  const yesBid = firstPresent(market.yes_bid, market.best_bid, market.raw?.yes_bid, market.raw?.bestBid);
  const yesAsk = firstPresent(market.yes_ask, market.best_ask, market.raw?.yes_ask, market.raw?.bestAsk);
  const noBid = firstPresent(market.no_bid, market.raw?.no_bid, market.raw?.noBid);
  const noAsk = firstPresent(market.no_ask, market.raw?.no_ask, market.raw?.noAsk);
  return `Y ${formatFixed(yesBid, 4)}/${formatFixed(yesAsk, 4)} · N ${formatFixed(noBid, 4)}/${formatFixed(noAsk, 4)}`;
}

function marketEntryPrice(market = {}) {
  if (market.max_entry_price !== null && market.max_entry_price !== undefined && market.max_entry_price !== "") {
    return market.max_entry_price;
  }
  if (market.selected_outcome_price !== null && market.selected_outcome_price !== undefined && market.selected_outcome_price !== "") {
    return market.selected_outcome_price;
  }
  return String(market.outcome || "YES").toUpperCase() === "NO"
    ? firstPresent(market.no_ask, market.raw?.no_ask, market.raw?.noAsk)
    : firstPresent(market.yes_ask, market.best_ask, market.raw?.yes_ask, market.raw?.bestAsk);
}

function formatSignedFixed(value, digits = 2) {
  const num = Number(value ?? 0);
  if (!Number.isFinite(num)) return "-";
  return `${num > 0 ? "+" : ""}${formatFixed(num, digits)}`;
}

function formatPnL(value) {
  if (value === null || value === undefined || value === "") return "-";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  const cls = num > 0 ? "positive" : num < 0 ? "negative" : "";
  return `<span class="${cls}">${formatSignedFixed(num, 2)}</span>`;
}

function formatShortTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    const text = String(value);
    const match = text.match(/(\d{2}:\d{2}:\d{2})/);
    return match ? match[1] : text;
  }
  return date.toLocaleTimeString(undefined, { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function postAgentAction(url, body = {}) {
  return fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

function agentActorGroup(event = {}) {
  const actorType = String(event.actor_type || "").trim();
  const capability = String(event.capability || "");
  const targetType = String(event.target_type || "");
  if (
    actorType === "internal_agent" ||
    capability.startsWith("event.investigation.") ||
    capability.startsWith("event.proposal.") ||
    targetType.includes("investigation") ||
    targetType.includes("graph_proposal")
  ) {
    return "internal";
  }
  if (actorType === "agent" || actorType === "external_agent") return "external";
  if (actorType === "human") return "human";
  if (actorType === "system") return "system";
  return "external";
}

function agentActorGroupMeta(group) {
  return AGENT_ACTOR_GROUPS[group] || AGENT_ACTOR_GROUPS.external;
}

function agentActorLabel(event = {}) {
  const group = agentActorGroup(event);
  const meta = agentActorGroupMeta(group);
  const actorId = event.actor_id || event.actor_type || "-";
  return `${meta.label}: ${actorId}`;
}

function agentDraftTitle(draft) {
  return draft?.draft?.name || draft?.name || "未命名策略";
}

function agentApprovalTitle(approval) {
  return approval?.snapshot?.snapshot?.name || approval?.draft?.draft?.name || approval?.draft?.name || approval?.approval_id || "-";
}

function agentApprovalBudget(approval) {
  const budget = approval?.snapshot?.snapshot?.budget || approval?.draft?.draft?.budget || {};
  return `预算 ${formatFixed(budget.max_total_usdc ?? 0, 2)} / 单笔 ${formatFixed(budget.max_single_order_usdc ?? 0, 2)}`;
}

function agentRiskText(report = {}) {
  if (!report || !Object.keys(report).length) return "未检查";
  const passed = report.passed ? "通过" : "未通过";
  const level = report.risk_level || "-";
  const violations = Array.isArray(report.violations) ? report.violations.length : 0;
  return `${passed} · ${level} · ${violations} 项`;
}

function agentFormatValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return Number.isFinite(value) ? formatNumber(value, 6) : "-";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.length ? value.map((item) => agentFormatValue(item)).join(", ") : "[]";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function agentObjectEntries(value = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value).filter(([, child]) => child !== undefined && child !== null && child !== "");
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

function marketExternalLink(market, label, className = "market-text-link") {
  const url = market?.url || "";
  if (!url) return escapeHtml(label || "-");
  return `<a class="${className}" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label || url)}</a>`;
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
      <td class="num">${escapeHtml(marketSideBidAsk(market))}</td>
      <td class="num">${escapeHtml(marketAllBidAsk(market))}</td>
      <td class="num">${formatFixed(marketEntryPrice(market), 4)}</td>
      <td class="num">${formatFixed(market.max_exposure_usdc, 2)}</td>
      <td><span class="mono truncate" title="${escapeHtml(market.condition_id || "")}">${escapeHtml(market.condition_id || "-")}</span></td>
    </tr>
  `).join("");
  return `
    <section class="agent-detail-section">
      <h4>Markets / Legs</h4>
      <div class="agent-detail-table-wrap">
        <table class="agent-detail-table">
          <thead><tr><th>#</th><th>Question</th><th>Outcome</th><th>Action</th><th>Side Bid/Ask</th><th>All Bid/Ask</th><th>Max Entry</th><th>Exposure</th><th>Condition</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderAgentRiskReport(report = {}) {
  if (!report || !Object.keys(report).length) {
    return `
      <section class="agent-detail-section">
        <h4>Risk Check</h4>
        <div class="status">暂无风控报告</div>
      </section>
    `;
  }
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

function agentApprovalSnapshot(approval = activeAgentApproval) {
  return approval?.snapshot?.snapshot || approval?.draft?.draft || {};
}

function agentApprovalMode(snapshot = {}) {
  const mode = String(snapshot.mode || snapshot.state || "Stop").trim() || "Stop";
  return AGENT_APPROVAL_MODES.includes(mode) ? mode : "Stop";
}

function normalizeAgentApprovalParamKey(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function ensureAgentApprovalDeadlineInput(inputs = []) {
  const list = Array.isArray(inputs) ? inputs : [];
  const hasDeadline = list.some((inp) => AGENT_DEADLINE_ALIASES.has(normalizeAgentApprovalParamKey(inp?.name)));
  return hasDeadline ? list : [...list, AGENT_DEADLINE_PARAM];
}

function agentApprovalInputType(inp = {}) {
  const kind = String(inp.kind || inp.type || "").trim().toLowerCase();
  if (["num", "number", "float", "int", "integer"].includes(kind)) return "number";
  if (["bool", "boolean"].includes(kind)) return "checkbox";
  return "text";
}

function agentApprovalBoolValue(value) {
  return ["1", "true", "yes", "on"].includes(String(value ?? "").trim().toLowerCase());
}

function renderAgentApprovalParamControl(inp = {}, rawValue = "") {
  const name = String(inp.name || "").trim();
  const type = agentApprovalInputType(inp);
  const required = inp.required ? " required" : "";
  if (Array.isArray(inp.values) && inp.values.length) {
    const current = String(rawValue ?? "");
    return `
      <select name="_agent_param_${escapeHtml(name)}" data-agent-approval-param="${escapeHtml(name)}"${required}>
        ${inp.values.map((value) => {
          const text = String(value);
          return `<option value="${escapeHtml(text)}"${text === current ? " selected" : ""}>${escapeHtml(text)}</option>`;
        }).join("")}
      </select>
    `;
  }
  if (type === "checkbox") {
    return `<input name="_agent_param_${escapeHtml(name)}" data-agent-approval-param="${escapeHtml(name)}" type="checkbox" value="true"${agentApprovalBoolValue(rawValue) ? " checked" : ""}>`;
  }
  const value = rawValue !== undefined && rawValue !== null ? ` value="${escapeHtml(String(rawValue))}"` : "";
  const step = type === "number" ? ' step="any"' : "";
  return `<input name="_agent_param_${escapeHtml(name)}" data-agent-approval-param="${escapeHtml(name)}" type="${type}"${step}${required}${value}>`;
}

function renderAgentApprovalParamInputs(inputs = [], prefill = {}) {
  const dyn = document.getElementById("agentApprovalDynamicInputs");
  if (!dyn) return;
  const params = prefill && typeof prefill === "object" ? prefill : {};
  if (!inputs.length && !Object.keys(params).length) {
    dyn.innerHTML = `<div class="status">暂无策略参数</div>`;
    return;
  }
  const list = inputs.length
    ? inputs
    : Object.keys(params).map((key) => ({ name: key, kind: typeof params[key] === "number" ? "Number" : "String" }));
  dyn.innerHTML = list.map((inp) => {
    const name = String(inp.name || "").trim();
    if (!name) return "";
    const rawValue = params[name] !== undefined ? params[name] : (inp.default ?? "");
    const description = [inp.description, inp.required ? "必填参数" : "", inp.default !== undefined && inp.default !== null && String(inp.default) !== "" ? `默认值: ${inp.default}` : ""]
      .filter(Boolean)
      .join("\n");
    const help = description
      ? `<span class="strategy-param-help" tabindex="0" title="${escapeHtml(description)}" aria-label="${escapeHtml(description)}">?</span>`
      : "";
    return `
      <label class="strategy-param-field" title="${escapeHtml(description)}">
        <span class="strategy-param-label">${escapeHtml(name)}${inp.required ? '<span class="strategy-param-required">*</span>' : ""}${help}</span>
        <span class="strategy-param-input-row">
          ${renderAgentApprovalParamControl(inp, rawValue)}
          <button type="button" class="strategy-param-autofill" title="UseData" aria-label="UseData" hidden>↻</button>
        </span>
      </label>
    `;
  }).join("");
}

function renderAgentApprovalLegRows(markets = []) {
  const rows = Array.isArray(markets) && markets.length ? markets : [{}];
  return `
    <div class="strategy-legs-editor agent-approval-legs">
      <div class="strategy-legs-header">
        <span>Condition IDs</span>
        <span class="strategy-legs-fixed">Fixed by strategy code</span>
      </div>
      <div class="strategy-leg-rows">
        ${rows.map((market, index) => `
          <div class="strategy-leg-row" data-agent-approval-leg-row="${index}">
            <div class="strategy-leg-title">Leg ${index + 1} · binary_market</div>
            <div class="condition-id-group">
              <input data-agent-approval-leg-field="condition_id" value="${escapeHtml(market.condition_id || "")}" placeholder="condition_id">
              <button class="mini" type="button" disabled title="AgentMonitor 暂不打开自选选择器">自选</button>
              <input data-agent-approval-leg-field="budget_cap" type="number" step="any" value="${escapeHtml(market.max_exposure_usdc ?? market.budget_cap ?? "")}" placeholder="资金">
            </div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function renderAgentApprovalRiskSummary(report = {}, snapshot = {}) {
  if (!report || !Object.keys(report).length) {
    return `
      <section class="agent-approval-section">
        <h4>Risk Check</h4>
        <div class="status">暂无风控报告</div>
      </section>
    `;
  }
  const violations = Array.isArray(report.violations) ? report.violations : [];
  const overrideText = agentRiskOverrideText(snapshot);
  return `
    <section class="agent-approval-section">
      <h4>Risk Check</h4>
      <div class="agent-detail-summary">
        ${agentStateChip(report.passed ? "HUMAN_APPROVED" : "RISK_BLOCKED")}
        <span>level: ${escapeHtml(report.risk_level || "-")}</span>
        <span>violations: ${escapeHtml(violations.length)}</span>
      </div>
      ${overrideText ? `<div class="agent-risk-override">${escapeHtml(overrideText)}</div>` : ""}
      ${violations.length ? `
        <div class="agent-approval-notes">
          ${violations.map((item) => `
            <div class="agent-approval-note">
              <strong>${escapeHtml(item.code || "risk")}</strong>
              <span>${escapeHtml(item.message || "-")}</span>
              <small>${escapeHtml([item.field, item.current !== undefined ? `当前 ${agentFormatValue(item.current)}` : "", item.allowed !== undefined ? `上限 ${agentFormatValue(item.allowed)}` : ""].filter(Boolean).join(" · "))}</small>
            </div>
          `).join("")}
        </div>
      ` : `<div class="agent-approval-muted">无风控违规</div>`}
    </section>
  `;
}

function renderAgentApprovalSimulationSummary(sim = {}) {
  const scenarios = Array.isArray(sim.scenarios) ? sim.scenarios : [];
  return `
    <section class="agent-approval-section">
      <h4>Simulation</h4>
      <div class="agent-detail-summary">
        <span>max loss: ${formatFixed(sim.max_loss_usdc, 2)} USDC</span>
        <span>max exposure: ${formatFixed(sim.max_exposure_usdc, 2)} USDC</span>
        <span>orders: ${escapeHtml(sim.estimated_orders ?? "-")}</span>
      </div>
      <div class="agent-approval-notes">
        ${(scenarios.length ? scenarios : [{ name: "暂无模拟场景", estimated_pnl_usdc: "" }]).map((item) => `
          <div class="agent-approval-note">
            <strong>${escapeHtml(item.name || "-")}</strong>
            <span>${escapeHtml(item.estimated_pnl_usdc === "" ? "" : `${formatSignedFixed(item.estimated_pnl_usdc, 2)} USDC`)}</span>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function parseAgentApprovalParamPaste(text) {
  const raw = String(text || "").trim();
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {}
  const result = {};
  raw.split(/\r?\n|,/).forEach((line) => {
    const match = line.match(/^\s*([A-Za-z0-9_.-]+)\s*[:=]\s*(.*?)\s*$/);
    if (match) result[match[1]] = match[2];
  });
  return result;
}

function setAgentApprovalParamField(key, value) {
  const fields = Array.from(document.querySelectorAll("#agentApprovalDynamicInputs [data-agent-approval-param]"));
  const normalizedKey = normalizeAgentApprovalParamKey(key);
  const field = fields.find((item) => normalizeAgentApprovalParamKey(item.dataset.agentApprovalParam) === normalizedKey);
  if (!field) return false;
  if (field.type === "checkbox") {
    field.checked = agentApprovalBoolValue(value);
  } else {
    field.value = value ?? "";
  }
  return true;
}

function applyAgentApprovalParamPaste() {
  const textArea = document.getElementById("agentApprovalParamPasteText");
  const message = document.getElementById("agentApprovalEditMessage");
  const params = parseAgentApprovalParamPaste(textArea?.value || "");
  let matched = 0;
  Object.entries(params).forEach(([key, value]) => {
    if (normalizeAgentApprovalParamKey(key) === "strategybankroll") {
      const bankroll = document.querySelector("#agentApprovalForm [name='strategy_bankroll']");
      if (bankroll) {
        bankroll.value = value ?? "";
        matched += 1;
      }
      return;
    }
    if (setAgentApprovalParamField(key, value)) matched += 1;
  });
  if (message) {
    message.textContent = Object.keys(params).length
      ? `已填入 ${matched} 个匹配字段，保存后生效。`
      : "没有识别到可填入的参数。支持 JSON 或 key = value。";
  }
}

async function populateAgentApprovalStrategyCodes(current = "") {
  const select = document.getElementById("agentApprovalStrategyCodeSelect");
  if (!select) return;
  try {
    const resp = await fetchJson("/api/strategy-codes");
    const codes = resp.data || [];
    const values = Array.from(new Set([current, ...codes].filter(Boolean)));
    select.innerHTML = `<option value="">-- 选择 --</option>` + values.map((code) => `
      <option value="${escapeHtml(code)}"${code === current ? " selected" : ""}>${escapeHtml(code)}</option>
    `).join("");
  } catch {
    select.innerHTML = current
      ? `<option value="${escapeHtml(current)}" selected>${escapeHtml(current)}</option>`
      : `<option value="">-- 选择 --</option>`;
  }
}

async function loadAgentApprovalDynamicInputs(code, prefill = {}) {
  if (!code) {
    renderAgentApprovalParamInputs([], prefill);
    return;
  }
  try {
    const resp = await fetchJson(`/api/strategy-codes/${encodeURIComponent(code)}/inputs`);
    const inputs = ensureAgentApprovalDeadlineInput(resp.data || []);
    renderAgentApprovalParamInputs(inputs, prefill);
  } catch {
    renderAgentApprovalParamInputs([], prefill);
  }
}

function collectAgentApprovalParams() {
  const params = {};
  document.querySelectorAll("#agentApprovalDynamicInputs [data-agent-approval-param]").forEach((field) => {
    const key = field.dataset.agentApprovalParam;
    if (!key) return;
    if (field.type === "checkbox") {
      params[key] = field.checked ? "true" : "false";
      return;
    }
    const value = String(field.value ?? "").trim();
    if (value !== "") params[key] = value;
  });
  return params;
}

function collectAgentApprovalLegs() {
  const snapshot = agentApprovalSnapshot();
  const markets = Array.isArray(snapshot.markets) ? snapshot.markets : [];
  return Array.from(document.querySelectorAll("[data-agent-approval-leg-row]")).map((row, index) => {
    const market = markets[index] || {};
    const conditionId = row.querySelector("[data-agent-approval-leg-field='condition_id']")?.value?.trim() || "";
    const budgetCap = row.querySelector("[data-agent-approval-leg-field='budget_cap']")?.value?.trim() || "";
    return {
      leg_index: index,
      condition_id: conditionId,
      yes_token: market.yes_token || "",
      no_token: market.no_token || "",
      asset_class: "polymarket_binary",
      venue: market.venue || "polymarket",
      instrument_id: market.instrument_id || conditionId,
      instrument_json: {
        question: market.question || market.title || "",
        outcome: market.outcome || "YES",
        url: market.url || "",
      },
      budget_cap: budgetCap,
      params_json: {
        outcome: market.outcome || "YES",
        action: market.action || "buy",
        max_entry_price: market.max_entry_price ?? market.best_ask ?? "",
        max_exposure_usdc: budgetCap,
      },
    };
  });
}

function collectAgentApprovalDraftPayload() {
  const form = document.getElementById("agentApprovalForm");
  const fd = new FormData(form);
  const legs = collectAgentApprovalLegs();
  return {
    actor_type: "human",
    actor_id: "local_user",
    reason: "human edited pending approval parameters from AgentMonitor",
    strategy_name: String(fd.get("strategy_name") || "").trim(),
    strategy_code: String(fd.get("strategy_code") || "").trim(),
    mode: String(fd.get("mode") || "Stop").trim(),
    strategy_bankroll: String(fd.get("strategy_bankroll") || "").trim(),
    input_json: collectAgentApprovalParams(),
    legs,
    condition_id: legs[0]?.condition_id || "",
  };
}

function openAgentApprovalModal(approval) {
  if (!agentApprovalModal || !agentApprovalBody) return;
  activeAgentApproval = approval;
  const snapshot = agentApprovalSnapshot(approval);
  const draft = approval?.draft || {};
  const title = agentApprovalTitle(approval);
  const params = snapshot.params || {};
  const budget = snapshot.budget || {};
  const markets = Array.isArray(snapshot.markets) ? snapshot.markets : [];
  const risk = approval.risk_report || approval?.snapshot?.risk || draft.last_risk_report || {};
  const simulation = draft.last_simulation || {};
  const agentReport = agentReportFromDraft(snapshot);
  const mode = agentApprovalMode(snapshot);
  if (agentApprovalTitleEl) agentApprovalTitleEl.textContent = "设置参数";
  if (agentApprovalSubtitleEl) {
    agentApprovalSubtitleEl.textContent = `审批单 ${approval.approval_id || "-"} · ${agentStateLabel(approval.status)} · ${title}`;
  }
  agentApprovalBody.innerHTML = `
    <form id="agentApprovalForm" class="modal-form agent-approval-form">
      <label>
        策略名称
        <input name="strategy_name" value="${escapeHtml(snapshot.name || title)}">
      </label>
      <label>
        策略代码文件
        <select id="agentApprovalStrategyCodeSelect" name="strategy_code">
          <option value="${escapeHtml(snapshot.strategy_code || "")}" selected>${escapeHtml(snapshot.strategy_code || "-- 选择 --")}</option>
        </select>
      </label>
      <label>
        Mode
        <select name="mode">
          ${AGENT_APPROVAL_MODES.map((item) => `<option value="${escapeHtml(item)}"${item === mode ? " selected" : ""}>${escapeHtml(item)}</option>`).join("")}
        </select>
      </label>
      ${renderAgentApprovalLegRows(markets)}
      <label>
        策略占用资金
        <input name="strategy_bankroll" type="number" step="any" value="${escapeHtml(budget.max_total_usdc ?? "")}">
      </label>
      <div class="strategy-param-paste">
        <div class="strategy-param-paste-head">
          <span>批量粘贴参数</span>
          <button id="agentApprovalParamPasteApply" class="mini ghost" type="button">填入匹配字段</button>
        </div>
        <textarea id="agentApprovalParamPasteText" placeholder='{"fair_price": 0.40, "entry_edge": 0.05}'></textarea>
      </div>
      <div id="agentApprovalDynamicInputs" class="agent-approval-dynamic-inputs"></div>
      <div id="agentApprovalEditMessage" class="agent-approval-edit-message">正在编辑 Agent 待确认草案；保存后会重新风控和模拟，仍需人工批准。</div>
      <section class="agent-approval-section">
        <h4>Agent 提交说明</h4>
        ${renderAgentReportSnippet(agentReport, { title: "提交模板" }) || '<div class="status">暂无提交说明</div>'}
      </section>
      ${renderAgentApprovalRiskSummary(risk, snapshot)}
      ${renderAgentApprovalSimulationSummary(simulation)}
    </form>
  `;
  const isPending = approval.status === "WAITING_HUMAN_CONFIRM";
  if (agentApprovalSaveBtn) agentApprovalSaveBtn.disabled = !isPending;
  if (agentApprovalApproveBtn) agentApprovalApproveBtn.disabled = !isPending;
  if (agentApprovalChangeBtn) agentApprovalChangeBtn.disabled = !isPending;
  if (agentApprovalRejectBtn) agentApprovalRejectBtn.disabled = !isPending;
  populateAgentApprovalStrategyCodes(snapshot.strategy_code || "").catch(() => {});
  loadAgentApprovalDynamicInputs(snapshot.strategy_code || "", params).catch(() => renderAgentApprovalParamInputs([], params));
  document.getElementById("agentApprovalParamPasteApply")?.addEventListener("click", applyAgentApprovalParamPaste);
  document.getElementById("agentApprovalStrategyCodeSelect")?.addEventListener("change", async (event) => {
    const currentParams = collectAgentApprovalParams();
    await loadAgentApprovalDynamicInputs(event.target.value || "", currentParams);
  });
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

function agentAuditCategory(event = {}) {
  const capability = String(event.capability || "");
  const targetType = String(event.target_type || "");
  if (capability.startsWith("event.") || targetType.includes("event_graph") || targetType.includes("investigation") || targetType.includes("graph_proposal")) return "event_graph";
  if (capability.startsWith("market.")) return "market";
  if (capability === "risk.check") return "risk";
  if (capability === "strategy.simulate") return "simulation";
  if (capability.startsWith("strategy.draft.")) return "draft";
  if (capability.includes("approval") || capability === "approval.status" || capability === "strategy.submit" || capability === "strategy.approve" || capability === "strategy.reject" || capability === "strategy.request_changes") return "approval";
  if (capability.startsWith("strategy.") && capability.includes(".read")) return "strategy_read";
  if (capability === "strategy.read_all" || capability === "strategy.workspace.read" || capability === "strategy.usedata.read" || capability === "strategy.events.read" || capability === "strategy.state.read") return "strategy_read";
  if (capability.startsWith("audit.") || capability === "agent.activity") return "audit";
  return "other";
}

function agentAuditCategoryMeta(category) {
  return AGENT_AUDIT_CATEGORIES[category] || AGENT_AUDIT_CATEGORIES.other;
}

function agentAuditCapabilityLabel(capability) {
  return AGENT_AUDIT_CAPABILITY_LABELS[capability] || capability || "-";
}

function agentAuditCompactValue(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function agentAuditResultCount(event = {}) {
  const output = event.output || {};
  if (output.count !== undefined) return output.count;
  if (output.candidate_count !== undefined) return output.candidate_count;
  if (output.created !== undefined) return output.created;
  if (output.deleted !== undefined) return output.deleted;
  if (Array.isArray(output.data)) return output.data.length;
  return "";
}

function agentAuditSummary(event = {}) {
  const input = event.input || {};
  const output = event.output || {};
  const parts = [];
  if (input.q || input.query) parts.push(`query=${input.q || input.query}`);
  if (input.category || input.categories) parts.push(`category=${Array.isArray(input.categories) ? input.categories.join(",") : input.category}`);
  if (input.sort || input.sort_by) parts.push(`sort=${input.sort || input.sort_by}`);
  if (input.strategy_name) parts.push(input.strategy_name);
  if (input.reason) parts.push(input.reason);
  if (event.target_type || event.target_id) parts.push(`${event.target_type || "target"}:${event.target_id || "-"}`);
  const count = agentAuditResultCount(event);
  if (count !== "") parts.push(`结果 ${count}`);
  if (!parts.length && output.actor?.id) parts.push(output.actor.id);
  return parts.join(" · ") || "查看详情";
}

function agentAuditSearchText(event = {}) {
  return [
    event.event_id,
    event.actor_type,
    event.actor_id,
    event.capability,
    event.target_type,
    event.target_id,
    event.policy_decision,
    event.risk_decision,
    agentAuditCapabilityLabel(event.capability),
    agentAuditSummary(event),
    agentAuditCompactValue(event.input),
  ].join(" ").toLowerCase();
}

function currentAgentActivityCategory() {
  return agentActivityCategory?.value || "all";
}

function renderAgentActivityControls() {
  if (agentActivityCategory && !agentActivityCategory.dataset.ready) {
    agentActivityCategory.innerHTML = Object.entries(AGENT_AUDIT_CATEGORIES).map(([key, item]) => (
      `<option value="${escapeHtml(key)}">${escapeHtml(item.label)}</option>`
    )).join("");
    const pinned = localStorage.getItem(AGENT_ACTIVITY_PIN_KEY) || "all";
    agentActivityCategory.value = AGENT_AUDIT_CATEGORIES[pinned] ? pinned : "all";
    agentActivityCategory.dataset.ready = "1";
  }
  const selected = currentAgentActivityCategory();
  const pinned = localStorage.getItem(AGENT_ACTIVITY_PIN_KEY) || "all";
  if (agentActivityPinBtn) {
    agentActivityPinBtn.textContent = selected !== "all" && pinned === selected ? "已固定" : "固定";
  }
  if (agentActivityModeLabel) {
    const meta = agentAuditCategoryMeta(selected);
    const pinText = selected !== "all" && pinned === selected ? " · 已固定" : "";
    agentActivityModeLabel.textContent = `${meta.label}${pinText}`;
  }
  if (agentActivityFilters) {
    agentActivityFilters.innerHTML = Object.entries(AGENT_AUDIT_CATEGORIES).map(([key, item]) => {
      const count = key === "all" ? agentAuditRows.length : agentAuditRows.filter((row) => agentAuditCategory(row) === key).length;
      const active = selected === key ? " active" : "";
      const pinnedClass = pinned === key && key !== "all" ? " pinned" : "";
      return `<button class="agent-activity-filter ${item.tone}${active}${pinnedClass}" type="button" data-agent-activity-category="${escapeHtml(key)}">${escapeHtml(item.label)} <span>${count}</span></button>`;
    }).join("");
  }
}

function filteredAgentActivityRows() {
  const category = currentAgentActivityCategory();
  const query = String(agentActivitySearch?.value || "").trim().toLowerCase();
  return agentAuditRows.filter((event) => {
    if (category !== "all" && agentAuditCategory(event) !== category) return false;
    if (query && !agentAuditSearchText(event).includes(query)) return false;
    return true;
  });
}

function renderAgentActivity(rows = []) {
  if (!agentActivityList) return;
  agentAuditRows = rows;
  renderAgentActivityControls();
  filteredAgentAuditRows = filteredAgentActivityRows();
  if (agentActivityCount) agentActivityCount.textContent = filteredAgentAuditRows.length === rows.length ? String(rows.length) : `${filteredAgentAuditRows.length}/${rows.length}`;
  if (!filteredAgentAuditRows.length) {
    setStatus(agentActivityList, "暂无 Agent 活动");
    return;
  }
  agentActivityList.innerHTML = filteredAgentAuditRows.map((event) => {
    const category = agentAuditCategory(event);
    const meta = agentAuditCategoryMeta(category);
    const riskTone = event.risk_decision === "blocked" ? " blocked" : "";
    return `
    <button class="agent-activity-row ${meta.tone}${riskTone}" type="button" data-agent-audit-id="${escapeHtml(event.event_id)}">
      <span>${formatShortTime(event.created_at)}</span>
      <span class="agent-activity-chip ${meta.tone}">${escapeHtml(meta.label)}</span>
      <strong class="agent-actor ${escapeHtml(agentActorGroup(event))}">${escapeHtml(agentActorLabel(event))}</strong>
      <span class="truncate"><b>${escapeHtml(agentAuditCapabilityLabel(event.capability))}</b> ${escapeHtml(agentAuditSummary(event))}</span>
    </button>
  `;
  }).join("");
}

function renderAuditRows(container, rows = [], emptyText = "暂无记录") {
  if (!container) return;
  if (!rows.length) {
    setStatus(container, emptyText);
    return;
  }
  container.innerHTML = rows.map((event) => {
    const category = agentAuditCategory(event);
    const meta = agentAuditCategoryMeta(category);
    const group = agentActorGroup(event);
    const riskTone = event.risk_decision === "blocked" ? " blocked" : "";
    return `
    <button class="agent-activity-row ${meta.tone}${riskTone}" type="button" data-agent-audit-id="${escapeHtml(event.event_id)}">
      <span>${formatShortTime(event.created_at)}</span>
      <span class="agent-activity-chip ${meta.tone}">${escapeHtml(meta.label)}</span>
      <strong class="agent-actor ${escapeHtml(group)}">${escapeHtml(agentActorLabel(event))}</strong>
      <span class="truncate"><b>${escapeHtml(agentAuditCapabilityLabel(event.capability))}</b> ${escapeHtml(agentAuditSummary(event))}</span>
    </button>
  `;
  }).join("");
}

function newInternalEventSearchRunId() {
  const randomPart = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID().replaceAll("-", "").slice(0, 12)
    : Math.random().toString(36).slice(2, 10);
  return `run_internal_event_search_${Date.now().toString(36)}_${randomPart}`;
}

function compactText(value, limit = 180) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, Math.max(0, limit - 3)).trim()}...` : text;
}

function internalEventSearchParams(query, limit, runId) {
  const params = new URLSearchParams();
  params.set("actor_type", "internal_agent");
  params.set("actor_id", "eventgraph_internal");
  params.set("workflow_id", "INT_EVENT_INVESTIGATION");
  params.set("run_id", runId);
  params.set("q", query);
  params.set("query", query);
  params.set("limit", String(limit));
  return params;
}

function internalEventSearchUrl(path, query, limit, runId, extra = {}) {
  const params = internalEventSearchParams(query, limit, runId);
  Object.entries(extra).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  });
  return `${path}?${params.toString()}`;
}

function renderInternalEventSearchIdle() {
  if (!internalEventSearchResult) return;
  internalEventSearchResult.innerHTML = `
    <div class="internal-event-empty">
      <strong>等待检索</strong>
      <span>输入关键词后会以 internal_agent 身份读取 events、observations 和 EventGraph。</span>
    </div>
  `;
}

function renderInternalEventList(title, rows = [], type = "event") {
  const items = rows.slice(0, 8);
  const empty = type === "event" ? "没有匹配的 derived event" : type === "observation" ? "没有匹配的 observation" : "没有图谱命中";
  return `
    <section class="internal-event-result-column">
      <div class="internal-event-result-head">
        <h4>${escapeHtml(title)}</h4>
        <span>${escapeHtml(rows.length)}</span>
      </div>
      <div class="internal-event-result-list">
        ${items.length ? items.map((item) => {
          const label = item.title || item.clean_title || item.label || item.event_id || item.observation_id || item.id || "-";
          const subtitle = item.source_label || item.source_type || item.subtitle || item.canonical_key || item.event_id || "";
          const heat = item.heat !== undefined ? `Heat ${formatFixed(item.heat, 1)}` : "";
          const time = item.published_at_utc || item.last_seen_utc || item.updated_at_utc || "";
          const detail = item.summary || item.url || item.details?.summary || "";
          const href = item.url || "";
          const labelHtml = href
            ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(compactText(label, 92))}</a>`
            : `<strong>${escapeHtml(compactText(label, 92))}</strong>`;
          return `
            <article class="internal-event-result-item ${escapeHtml(type)}">
              ${labelHtml}
              <div class="internal-event-result-meta">
                ${subtitle ? `<span>${escapeHtml(compactText(subtitle, 70))}</span>` : ""}
                ${heat ? `<span>${escapeHtml(heat)}</span>` : ""}
                ${time ? `<span>${escapeHtml(formatShortTime(time))}</span>` : ""}
              </div>
              ${detail ? `<p>${escapeHtml(compactText(detail, 170))}</p>` : ""}
            </article>
          `;
        }).join("") : `<div class="status">${escapeHtml(empty)}</div>`}
      </div>
    </section>
  `;
}

function renderInternalEventSearchResult({ query, runId, eventsPayload, observationsPayload, graphPayload }) {
  if (!internalEventSearchResult) return;
  const events = Array.isArray(eventsPayload?.events) ? eventsPayload.events : [];
  const observations = Array.isArray(observationsPayload?.observations) ? observationsPayload.observations : [];
  const graph = graphPayload || {};
  const summary = graph.summary || {};
  const graphMatches = Array.isArray(graph.event_rankings)
    ? graph.event_rankings
    : Array.isArray(graph.nodes)
      ? graph.nodes.filter((node) => node.type === "EVENT")
      : [];
  if (internalEventSearchMeta) {
    internalEventSearchMeta.textContent = `run_id=${runId} | events ${events.length} | observations ${observations.length} | graph edges ${summary.edges ?? 0}`;
  }
  if (internalEventGraphLink) {
    const graphParams = new URLSearchParams();
    graphParams.set("q", query);
    graphParams.set("include_news", "1");
    graphParams.set("news_limit", "20");
    graphParams.set("limit", "80");
    internalEventGraphLink.href = `/event-graph?${graphParams.toString()}`;
  }
  internalEventSearchResult.innerHTML = `
    <div class="internal-event-search-summary">
      <div><span>Graph Events</span><strong>${escapeHtml(summary.events ?? graphMatches.length ?? 0)}</strong></div>
      <div><span>News Events</span><strong>${escapeHtml(summary.news_events ?? events.length)}</strong></div>
      <div><span>Finance</span><strong>${escapeHtml(summary.finance_nodes ?? 0)}</strong></div>
      <div><span>Edges</span><strong>${escapeHtml(summary.edges ?? 0)}</strong></div>
    </div>
    <div class="internal-event-result-grid">
      ${renderInternalEventList("Derived Events", events, "event")}
      ${renderInternalEventList("Observations", observations, "observation")}
      ${renderInternalEventList("Graph Matches", graphMatches.slice(0, 8), "graph")}
    </div>
  `;
}

async function runInternalEventSearch() {
  if (!internalEventSearchResult) return;
  const query = String(internalEventSearchInput?.value || "").trim();
  const limit = Math.max(3, Math.min(Number(internalEventSearchLimit?.value || 8) || 8, 30));
  if (!query) {
    renderInternalEventSearchIdle();
    if (internalEventSearchInput) internalEventSearchInput.focus();
    return;
  }
  const runId = newInternalEventSearchRunId();
  internalEventSearchResult.innerHTML = `<div class="status">检索中...</div>`;
  if (internalEventSearchMeta) internalEventSearchMeta.textContent = `run_id=${runId} | RUNNING`;
  const [eventsResp, observationsResp, graphResp] = await Promise.all([
    fetchJson(internalEventSearchUrl("/api/agent/event-graph/events", query, limit, runId)),
    fetchJson(internalEventSearchUrl("/api/agent/event-graph/observations", query, limit, runId)),
    fetchJson(internalEventSearchUrl("/api/agent/event-graph", query, Math.max(10, limit * 2), runId, {
      include_news: "1",
      news_limit: Math.max(6, limit),
    })),
  ]);
  renderInternalEventSearchResult({
    query,
    runId,
    eventsPayload: eventsResp.data || {},
    observationsPayload: observationsResp.data || {},
    graphPayload: graphResp.data || {},
  });
  await loadAgentDashboard({ silent: true }).catch(() => {});
}

function newGraphChangeRunId() {
  const randomPart = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID().replaceAll("-", "").slice(0, 12)
    : Math.random().toString(36).slice(2, 10);
  return `run_ext_graph_change_${Date.now().toString(36)}_${randomPart}`;
}

function parseGraphChangePatch() {
  const text = String(graphChangePatch?.value || "").trim();
  if (!text) return { items: [] };
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) return { items: parsed };
    if (parsed && typeof parsed === "object") return parsed;
    throw new Error("Patch JSON must be an object or array");
  } catch (error) {
    throw new Error(`Patch JSON 解析失败: ${error.message || String(error)}`);
  }
}

function graphChangePayload(runId = newGraphChangeRunId()) {
  const changeType = String(graphChangeType?.value || "event_update").trim();
  const patch = parseGraphChangePatch();
  return {
    actor_type: "agent",
    actor_id: "agent_strategy_assistant",
    workflow_id: "EXT_GRAPH_CHANGE_REQUEST",
    run_id: runId,
    change_type: changeType,
    title: String(graphChangeTitle?.value || changeType).trim(),
    evidence_summary: String(graphChangeEvidence?.value || "").trim(),
    reason: String(graphChangeEvidence?.value || "").trim(),
    patch,
    proposed_changes: patch,
  };
}

function graphChangeStatusChip(status) {
  const value = String(status || "PENDING").toUpperCase();
  const label = {
    PENDING: "待审核",
    NEEDS_CHANGES: "需修改",
    APPROVED: "已批准",
    REJECTED: "已拒绝",
    APPLIED: "已应用",
    SUPERSEDED: "已替代",
  }[value] || value;
  const tone = value === "NEEDS_CHANGES" || value === "REJECTED" ? "error" : value === "APPROVED" || value === "APPLIED" ? "good" : "pending";
  return `<span class="state-chip ${tone}">${escapeHtml(label)}</span>`;
}

function graphChangeActionButtons(item = {}) {
  const requestId = String(item.request_id || "").trim();
  if (!requestId) return "";
  const escapedId = escapeHtml(requestId);
  const status = String(item.status || "").toUpperCase();
  const reviewButtons = status === "PENDING" ? `
    <button class="mini primary" type="button" data-agent-graph-approve="${escapedId}">批准</button>
    <button class="mini ghost" type="button" data-agent-graph-change="${escapedId}">要求修改</button>
    <button class="mini danger" type="button" data-agent-graph-reject="${escapedId}">拒绝</button>
  ` : "";
  const applyButton = status === "APPROVED" ? `
    <button class="mini primary" type="button" data-agent-graph-apply="${escapedId}">应用</button>
  ` : "";
  return `
    ${applyButton}
    ${reviewButtons}
    <button class="mini ghost" type="button" data-agent-view-change="${escapedId}">详情</button>
  `;
}

function renderGraphChangeValidation(result = {}) {
  if (!graphChangeValidation) return;
  const errors = Array.isArray(result.errors) ? result.errors : [];
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  const targetRefs = Array.isArray(result.target_refs) ? result.target_refs : [];
  const tone = result.valid ? "good" : "error";
  graphChangeValidation.innerHTML = `
    <div class="graph-change-validation ${escapeHtml(tone)}">
      <div class="graph-change-validation-head">
        <strong>${result.valid ? "校验通过" : "校验未通过"}</strong>
        <span>risk=${escapeHtml(result.risk_level || "-")} · human_review=${escapeHtml(result.requires_human_review ? "yes" : "no")}</span>
      </div>
      <div class="internal-event-search-summary">
        <div><span>Errors</span><strong>${escapeHtml(errors.length)}</strong></div>
        <div><span>Warnings</span><strong>${escapeHtml(warnings.length)}</strong></div>
        <div><span>Targets</span><strong>${escapeHtml(targetRefs.length)}</strong></div>
        <div><span>Items</span><strong>${escapeHtml(result.normalized_patch?.items?.length || 0)}</strong></div>
      </div>
      ${errors.length ? `<div class="graph-change-message-list">${errors.map((item) => `<p>${escapeHtml(item.code || "ERROR")}: ${escapeHtml(item.message || JSON.stringify(item))}</p>`).join("")}</div>` : ""}
      ${warnings.length ? `<div class="graph-change-message-list warning">${warnings.map((item) => `<p>${escapeHtml(item.code || "WARNING")}: ${escapeHtml(item.message || JSON.stringify(item))}</p>`).join("")}</div>` : ""}
    </div>
  `;
}

async function validateGraphChange() {
  const payload = graphChangePayload();
  if (graphChangeMeta) graphChangeMeta.textContent = `run_id=${payload.run_id} | validating`;
  const resp = await postAgentAction("/api/agent/event-graph/patches/validate", payload);
  renderGraphChangeValidation(resp.data || {});
  if (graphChangeMeta) graphChangeMeta.textContent = `run_id=${payload.run_id} | validation ${resp.data?.valid ? "valid" : "needs changes"}`;
  await loadAgentDashboard({ silent: true }).catch(() => {});
  return { payload, validation: resp.data || {} };
}

async function submitGraphChange() {
  const runId = newGraphChangeRunId();
  const payload = graphChangePayload(runId);
  if (graphChangeMeta) graphChangeMeta.textContent = `run_id=${runId} | submitting`;
  const resp = await postAgentAction("/api/agent/event-graph/change-requests", payload);
  const status = resp.data?.status || "PENDING";
  if (graphChangeMeta) graphChangeMeta.textContent = `request_id=${resp.data?.request_id || "-"} | status=${status} | risk=${resp.data?.risk_level || "-"}`;
  graphChangeValidation.innerHTML = `<div class="status good">已提交 EventGraph change request: ${escapeHtml(resp.data?.request_id || "-")} (${escapeHtml(status)})</div>`;
  await loadAgentDashboard({ silent: true });
}

function renderAgentGraphChanges(payload = {}, auditRows = []) {
  const rows = Array.isArray(payload.items) ? payload.items : [];
  agentGraphChangeRows = rows;
  if (agentInternalCount) agentInternalCount.textContent = String(rows.length);
  if (!agentInternalList) return;
  if (!rows.length) {
    const recent = auditRows.filter((row) => agentAuditCategory(row) === "event_graph").slice(0, 8);
    renderAuditRows(agentInternalList, recent, "暂无 EventGraph 变更请求");
    return;
  }
  agentInternalList.innerHTML = rows.map((item) => {
    const validation = item.validation || {};
    const errors = Array.isArray(validation.errors) ? validation.errors.length : 0;
    const warnings = Array.isArray(validation.warnings) ? validation.warnings.length : 0;
    const targetRefs = Array.isArray(item.target_refs) ? item.target_refs : [];
    const title = item.title || item.summary || item.change_type || item.request_id;
    const evidence = item.evidence_summary || item.reason || item.payload?.evidence_summary || "";
    return `
      <div class="agent-item graph-change-item">
        <div class="agent-item-main">
          <div class="agent-item-title">${escapeHtml(title || "-")}</div>
          <div class="agent-item-meta">
            ${graphChangeStatusChip(item.status)}
            <span>${escapeHtml(item.change_type || "-")}</span>
            <span>risk ${escapeHtml(item.risk_level || validation.risk_level || "-")}</span>
            <span>${escapeHtml(item.requester_id || item.requester || "-")}</span>
            <span>${formatShortTime(item.created_at_utc)}</span>
          </div>
          <div class="agent-item-note">${escapeHtml(compactText(evidence || "无 evidence summary", 220))}</div>
          <div class="graph-change-ref-row">
            <span>run ${escapeHtml(item.run_id || "-")}</span>
            <span>targets ${escapeHtml(targetRefs.length)}</span>
            <span>errors ${escapeHtml(errors)}</span>
            <span>warnings ${escapeHtml(warnings)}</span>
          </div>
        </div>
        <div class="agent-actions">
          ${graphChangeActionButtons(item)}
        </div>
      </div>
    `;
  }).join("");
}

function openGraphChangeModal(requestId, detailItem = null) {
  const item = detailItem || agentGraphChangeRows.find((row) => row.request_id === requestId);
  if (!item || !agentAuditModal || !agentAuditBody) return;
  if (agentAuditTitle) agentAuditTitle.textContent = `EventGraph Change ${item.request_id || ""}`;
  if (agentAuditSubtitle) {
    agentAuditSubtitle.textContent = `${item.status || "-"} · ${item.change_type || "-"} · ${item.requester_type || "agent"}:${item.requester_id || item.requester || "-"}`;
  }
  agentAuditBody.innerHTML = `
    <section class="agent-audit-detail-section">
      <h4>概要</h4>
      <div class="agent-audit-meta-grid">
        <div><span>Status</span><strong>${escapeHtml(item.status || "-")}</strong></div>
        <div><span>Risk</span><strong>${escapeHtml(item.risk_level || item.validation?.risk_level || "-")}</strong></div>
        <div><span>Type</span><strong>${escapeHtml(item.change_type || "-")}</strong></div>
        <div><span>Requester</span><strong>${escapeHtml(item.requester_id || item.requester || "-")}</strong></div>
        <div><span>Run</span><strong>${escapeHtml(item.run_id || "-")}</strong></div>
        <div><span>Workflow</span><strong>${escapeHtml(item.workflow_id || "-")}</strong></div>
        <div><span>Created</span><strong>${escapeHtml(item.created_at_utc || "-")}</strong></div>
        <div><span>Updated</span><strong>${escapeHtml(item.updated_at_utc || "-")}</strong></div>
      </div>
      <div class="agent-audit-summary">${escapeHtml(item.evidence_summary || item.reason || item.summary || "无摘要")}</div>
    </section>
    ${agentAuditJsonBlock("Patch", item.patch || {})}
    ${agentAuditJsonBlock("Validation", item.validation || {})}
    ${agentAuditJsonBlock("Raw Payload", item.payload || {})}
  `;
  agentAuditModal.hidden = false;
}

async function fetchGraphChangeDetail(requestId) {
  const payload = await fetchJson(`/api/agent/event-graph/change-requests/${encodeURIComponent(requestId)}?actor_type=human&actor_id=local_user`);
  const item = payload.data || {};
  if (item.request_id) {
    const index = agentGraphChangeRows.findIndex((row) => row.request_id === item.request_id);
    if (index >= 0) {
      agentGraphChangeRows[index] = item;
    } else {
      agentGraphChangeRows.unshift(item);
    }
  }
  return item;
}

function renderPendingSummaryList(container, rows = [], emptyText = "暂无待处理策略") {
  if (!container) return;
  if (!rows.length) {
    setStatus(container, emptyText);
    return;
  }
  container.innerHTML = rows.slice(0, 6).map((approval) => `
    <button class="agent-compact-row" type="button" data-agent-view-approval="${escapeHtml(approval.approval_id)}">
      <span>${agentStateChip(approval.status)}</span>
      <strong class="truncate">${escapeHtml(agentApprovalTitle(approval))}</strong>
      <small>${escapeHtml(agentApprovalBudget(approval))}</small>
    </button>
  `).join("");
}

function renderAgentOverview(data = {}, auditRows = [], changePayload = {}) {
  const pending = data.pending_approvals || [];
  const drafts = data.drafts || [];
  const changeRows = Array.isArray(changePayload.items) ? changePayload.items : [];
  const externalRows = auditRows.filter((row) => agentActorGroup(row) === "external");
  const blockedRows = auditRows.filter((row) => row.risk_decision === "blocked");
  if (agentOverviewCards) {
    const cards = [
      ["待人工确认", pending.length, "外接 Agent 提交后等待人类处理"],
      ["策略草案", drafts.length, "尚未或已经进入审批流程的草案"],
      ["图谱变更", changeRows.length, "EventGraph change request 与 patch"],
      ["风控阻断", blockedRows.length, "最近审计中的 blocked 决策"],
    ];
    agentOverviewCards.innerHTML = cards.map(([label, value, note]) => `
      <div class="agent-overview-card">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
        <small>${escapeHtml(note)}</small>
      </div>
    `).join("");
  }
  if (agentOverviewPendingCount) agentOverviewPendingCount.textContent = String(pending.length);
  if (agentOverviewInternalCount) agentOverviewInternalCount.textContent = String(changeRows.length);
  renderPendingSummaryList(agentOverviewPending, pending);
  renderGraphChangeSummaryList(agentOverviewInternal, changeRows.slice(0, 8), "暂无 EventGraph 变更请求");
  renderAuditRows(agentExternalActivityList, externalRows.slice(0, 12), "暂无外接 Agent 活动");
  if (agentExternalActivityCount) agentExternalActivityCount.textContent = String(externalRows.length);
}

function renderGraphChangeSummaryList(container, rows = [], emptyText = "暂无 EventGraph 变更请求") {
  if (!container) return;
  if (!rows.length) {
    setStatus(container, emptyText);
    return;
  }
  container.innerHTML = rows.map((item) => `
    <button class="agent-compact-row" type="button" data-agent-view-change="${escapeHtml(item.request_id)}">
      <span>${graphChangeStatusChip(item.status)}</span>
      <strong class="truncate">${escapeHtml(item.title || item.change_type || item.request_id || "-")}</strong>
      <small>${escapeHtml(item.risk_level || item.validation?.risk_level || "-")}</small>
    </button>
  `).join("");
}

function agentAuditJsonBlock(title, value) {
  return `
    <section class="agent-audit-detail-section">
      <h4>${escapeHtml(title)}</h4>
      <pre>${escapeHtml(JSON.stringify(value ?? {}, null, 2))}</pre>
    </section>
  `;
}

function openAgentAuditModal(eventId) {
  const event = agentAuditRows.find((item) => item.event_id === eventId);
  if (!event || !agentAuditModal || !agentAuditBody) return;
  const category = agentAuditCategory(event);
  const meta = agentAuditCategoryMeta(category);
  const actorGroup = agentActorGroup(event);
  const actorMeta = agentActorGroupMeta(actorGroup);
  if (agentAuditTitle) agentAuditTitle.textContent = agentAuditCapabilityLabel(event.capability);
  if (agentAuditSubtitle) {
    agentAuditSubtitle.textContent = `${formatShortTime(event.created_at)} · ${actorMeta.label} · ${meta.label} · ${event.actor_type || "-"}:${event.actor_id || "-"}`;
  }
  agentAuditBody.innerHTML = `
    <section class="agent-audit-detail-section">
      <h4>概要</h4>
      <div class="agent-audit-meta-grid">
        <div><span>类别</span><strong>${escapeHtml(meta.label)}</strong></div>
        <div><span>Agent 分组</span><strong>${escapeHtml(actorMeta.label)}</strong></div>
        <div><span>Capability</span><strong>${escapeHtml(event.capability || "-")}</strong></div>
        <div><span>Actor</span><strong>${escapeHtml(`${event.actor_type || "-"}:${event.actor_id || "-"}`)}</strong></div>
        <div><span>Target</span><strong>${escapeHtml(`${event.target_type || "-"}:${event.target_id || "-"}`)}</strong></div>
        <div><span>Policy</span><strong>${escapeHtml(event.policy_decision || "-")}</strong></div>
        <div><span>Risk</span><strong>${escapeHtml(event.risk_decision || "-")}</strong></div>
        <div><span>Event ID</span><strong>${escapeHtml(event.event_id || "-")}</strong></div>
        <div><span>Created</span><strong>${escapeHtml(event.created_at || "-")}</strong></div>
      </div>
      <div class="agent-audit-summary">${escapeHtml(agentAuditSummary(event))}</div>
    </section>
    ${agentAuditJsonBlock("Input", event.input || {})}
    ${agentAuditJsonBlock("Output", event.output || {})}
  `;
  agentAuditModal.hidden = false;
}

function closeAgentAuditModal() {
  if (agentAuditModal) agentAuditModal.hidden = true;
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

function renderAgentDashboard(data = {}, auditRows = [], changePayload = {}) {
  const pending = data.pending_approvals || [];
  const drafts = data.drafts || [];
  const changeRows = Array.isArray(changePayload.items) ? changePayload.items : [];
  const externalRows = auditRows.filter((row) => agentActorGroup(row) === "external");
  if (agentMeta) {
    const limits = data.policy?.limits || {};
    agentMeta.innerHTML = `图谱变更: ${escapeHtml(changeRows.length)} | 外接: ${escapeHtml(externalRows.length)} | 待确认: ${escapeHtml(pending.length)} | 草案: ${escapeHtml(drafts.length)} | 单策略上限: ${formatFixed(limits.max_strategy_budget_usdc, 2)} USDC`;
  }
  renderAgentOverview(data, auditRows, changePayload);
  renderAgentGraphChanges(changePayload, auditRows);
  renderAgentPendingApprovals(pending);
  renderAgentActivity(auditRows);
  renderAgentDrafts(drafts);
}

async function loadAgentDashboard(options = {}) {
  const { silent = false } = options;
  if (!silent && !hasLoadedAgentDashboard) {
    setStatus(agentPendingApprovals, "加载中...");
    setStatus(agentActivityList, "加载中...");
    setStatus(agentDraftList, "加载中...");
  }
  const [dashboardPayload, auditPayload, changePayload] = await Promise.all([
    fetchJson("/api/agent/dashboard?limit=50"),
    fetchJson("/api/agent/audit?limit=300&actor_type=human&actor_id=local_user"),
    fetchJson("/api/agent/event-graph/change-requests?limit=80&actor_type=agent&actor_id=agent_strategy_assistant"),
  ]);
  renderAgentDashboard(dashboardPayload.data || {}, auditPayload.data || [], changePayload.data || {});
  hasLoadedAgentDashboard = true;
}

document.querySelector(".agent-workbench")?.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.id === "agentActivityClearBtn" || button.id === "agentActivityPinBtn") return;
  const approvalToApprove = button.dataset.agentApprove;
  const approvalToReject = button.dataset.agentReject;
  const approvalToChange = button.dataset.agentChange;
  const approvalToView = button.dataset.agentViewApproval;
  const draftToRisk = button.dataset.agentRisk;
  const draftToSimulate = button.dataset.agentSimulate;
  const draftToSubmit = button.dataset.agentSubmit;
  const draftToDelete = button.dataset.agentDeleteDraft;
  const draftToView = button.dataset.agentViewDraft;
  const auditToView = button.dataset.agentAuditId;
  const changeToView = button.dataset.agentViewChange;
  const graphToApprove = button.dataset.agentGraphApprove;
  const graphToReject = button.dataset.agentGraphReject;
  const graphToChange = button.dataset.agentGraphChange;
  const graphToApply = button.dataset.agentGraphApply;
  const activityCategory = button.dataset.agentActivityCategory;
  try {
    if (activityCategory) {
      if (agentActivityCategory) agentActivityCategory.value = activityCategory;
      renderAgentActivity(agentAuditRows);
      return;
    }
    if (auditToView) {
      openAgentAuditModal(auditToView);
      return;
    }
    if (changeToView) {
      button.disabled = true;
      const detail = await fetchGraphChangeDetail(changeToView);
      openGraphChangeModal(changeToView, detail);
      return;
    }
    if (graphToApprove) {
      if (!confirm("批准这个 EventGraph 变更请求？批准后仍需点击应用才会写入 Graph Core。")) return;
      button.disabled = true;
      await postAgentAction(`/api/event-graph/change-requests/${encodeURIComponent(graphToApprove)}/approve`, {
        actor_type: "human",
        actor_id: "local_user",
      });
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (graphToReject) {
      const reason = prompt("拒绝原因", "证据不足或 patch 不符合当前图谱策略");
      if (reason === null) return;
      button.disabled = true;
      await postAgentAction(`/api/event-graph/change-requests/${encodeURIComponent(graphToReject)}/reject`, {
        actor_type: "human",
        actor_id: "local_user",
        reason,
      });
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (graphToChange) {
      const reason = prompt("希望外部 Agent 如何修改？", "请补充证据、收窄影响范围或调整 patch item");
      if (reason === null) return;
      button.disabled = true;
      await postAgentAction(`/api/event-graph/change-requests/${encodeURIComponent(graphToChange)}/request-changes`, {
        actor_type: "human",
        actor_id: "local_user",
        reason,
      });
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (graphToApply) {
      if (!confirm("应用这个 EventGraph 变更到 Graph Core？系统会写入 event 当前态和版本记录。")) return;
      button.disabled = true;
      await postAgentAction(`/api/event-graph/change-requests/${encodeURIComponent(graphToApply)}/apply`, {
        actor_type: "human",
        actor_id: "local_user",
      });
      await loadAgentDashboard({ silent: true });
      return;
    }
    if (approvalToView) {
      button.disabled = true;
      const payload = await fetchJson(`/api/agent/approvals/${encodeURIComponent(approvalToView)}?actor_type=human&actor_id=local_user`);
      openAgentApprovalModal(payload.data || {});
      return;
    }
    if (approvalToApprove) {
      if (!confirm("确认批准这个 Agent 策略？批准后会按当前参数创建正式策略。")) return;
      button.disabled = true;
      await postAgentAction(`/api/approvals/${encodeURIComponent(approvalToApprove)}/approve`, {
        actor_type: "human",
        actor_id: "local_user",
      });
      await loadAgentDashboard({ silent: true });
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
      const payload = await fetchJson(`/api/agent/strategy-drafts/${encodeURIComponent(draftToView)}?actor_type=human&actor_id=local_user`);
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

agentAuditModalClose?.addEventListener("click", closeAgentAuditModal);
agentAuditCloseBtn?.addEventListener("click", closeAgentAuditModal);
agentAuditModal?.addEventListener("click", (event) => {
  if (event.target === agentAuditModal) closeAgentAuditModal();
});

agentActivitySearch?.addEventListener("input", () => renderAgentActivity(agentAuditRows));
agentActivityCategory?.addEventListener("change", () => renderAgentActivity(agentAuditRows));
agentActivityPinBtn?.addEventListener("click", () => {
  const selected = currentAgentActivityCategory();
  const pinned = localStorage.getItem(AGENT_ACTIVITY_PIN_KEY) || "all";
  if (selected === "all" || pinned === selected) {
    localStorage.removeItem(AGENT_ACTIVITY_PIN_KEY);
  } else {
    localStorage.setItem(AGENT_ACTIVITY_PIN_KEY, selected);
  }
  renderAgentActivity(agentAuditRows);
});
agentActivityClearBtn?.addEventListener("click", async () => {
  const ids = filteredAgentAuditRows.map((event) => event.event_id).filter(Boolean);
  if (!ids.length) return;
  const category = agentAuditCategoryMeta(currentAgentActivityCategory()).label;
  if (!confirm(`确认清除当前筛选下的 ${ids.length} 条 Activity？类别：${category}`)) return;
  agentActivityClearBtn.disabled = true;
  try {
    await fetchJson("/api/agent/audit", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actor_type: "human",
        actor_id: "local_user",
        event_ids: ids,
      }),
    });
    await loadAgentDashboard({ silent: true });
  } catch (error) {
    alert("清除失败: " + (error.message || String(error)));
  } finally {
    agentActivityClearBtn.disabled = false;
  }
});

internalEventSearchForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitter = internalEventSearchForm.querySelector("button[type='submit']");
  try {
    if (submitter) submitter.disabled = true;
    await runInternalEventSearch();
  } catch (error) {
    setStatus(internalEventSearchResult, error.message || "Event 检索失败");
    if (internalEventSearchMeta) internalEventSearchMeta.textContent = "检索失败";
  } finally {
    if (submitter) submitter.disabled = false;
  }
});

graphChangeValidateBtn?.addEventListener("click", async () => {
  graphChangeValidateBtn.disabled = true;
  try {
    const { validation } = await validateGraphChange();
    if (!validation.valid && graphChangeMeta) graphChangeMeta.textContent = "校验未通过，请修改 patch 后重试";
  } catch (error) {
    setStatus(graphChangeValidation, error.message || "Patch 校验失败");
    if (graphChangeMeta) graphChangeMeta.textContent = "校验失败";
  } finally {
    graphChangeValidateBtn.disabled = false;
  }
});

graphChangeForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitter = graphChangeForm.querySelector("button[type='submit']");
  try {
    if (submitter) submitter.disabled = true;
    await submitGraphChange();
  } catch (error) {
    setStatus(graphChangeValidation, error.message || "提交失败");
    if (graphChangeMeta) graphChangeMeta.textContent = "提交失败";
  } finally {
    if (submitter) submitter.disabled = false;
  }
});

agentApprovalSaveBtn?.addEventListener("click", async () => {
  if (!activeAgentApproval?.approval_id) return;
  if (activeAgentApproval.status !== "WAITING_HUMAN_CONFIRM") return;
  agentApprovalSaveBtn.disabled = true;
  try {
    const payload = await fetchJson(`/api/agent/approvals/${encodeURIComponent(activeAgentApproval.approval_id)}/draft`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectAgentApprovalDraftPayload()),
    });
    activeAgentApproval = payload.data || activeAgentApproval;
    openAgentApprovalModal(activeAgentApproval);
    await loadAgentDashboard({ silent: true });
  } catch (error) {
    alert("保存失败: " + (error.message || String(error)));
  } finally {
    if (agentApprovalSaveBtn) {
      agentApprovalSaveBtn.disabled = activeAgentApproval?.status !== "WAITING_HUMAN_CONFIRM";
    }
  }
});

agentApprovalApproveBtn?.addEventListener("click", async () => {
  if (!activeAgentApproval?.approval_id) return;
  if (!confirm("确认批准这个 Agent 策略？批准后会按当前参数创建正式策略。")) return;
  agentApprovalApproveBtn.disabled = true;
  try {
    await postAgentAction(`/api/approvals/${encodeURIComponent(activeAgentApproval.approval_id)}/approve`, {
      actor_type: "human",
      actor_id: "local_user",
    });
    closeAgentApprovalModal();
    await loadAgentDashboard({ silent: true });
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

refreshAgentBtn?.addEventListener("click", () => {
  loadAgentDashboard().catch((error) => {
    if (agentMeta) agentMeta.textContent = error.message;
  });
});

agentMonitorTabs.forEach((button) => {
  button.addEventListener("click", () => {
    const tab = button.dataset.agentMonitorTab || "overview";
    agentMonitorTabs.forEach((item) => item.classList.toggle("active", item === button));
    agentMonitorPanes.forEach((pane) => pane.classList.toggle("active", pane.dataset.agentMonitorPane === tab));
  });
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (agentRefreshTimer) clearInterval(agentRefreshTimer);
    agentRefreshTimer = null;
    return;
  }
  loadAgentDashboard({ silent: true }).catch(() => {});
  startAgentPolling();
});

function startAgentPolling() {
  if (agentRefreshTimer) clearInterval(agentRefreshTimer);
  agentRefreshTimer = setInterval(() => {
    loadAgentDashboard({ silent: true }).catch((error) => {
      if (agentMeta) agentMeta.textContent = error.message;
    });
  }, 5000);
}

renderInternalEventSearchIdle();

loadAgentDashboard()
  .then(startAgentPolling)
  .catch((error) => {
    if (agentMeta) agentMeta.textContent = error.message;
    setStatus(agentPendingApprovals, error.message);
    setStatus(agentActivityList, error.message);
    setStatus(agentDraftList, error.message);
  });
