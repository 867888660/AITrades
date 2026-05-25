const watchlistTable = document.getElementById("watchlistTable");
const watchlistMeta = document.getElementById("watchlistMeta");
const watchlistRefreshBtn = document.getElementById("watchlistRefreshBtn");
const marketUi = window.PolyMarketUi;
let focusedIdentity = "";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(container, text) {
  container.innerHTML = `<div class="status">${escapeHtml(text)}</div>`;
}

function formatTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? escapeHtml(value) : escapeHtml(date.toLocaleString());
}

function buildInternalWatchlistUrl(market) {
  const params = new URLSearchParams();
  const normalized = marketUi.normalizeWatchlistMarket(market);
  if (!normalized) {
    return "/watchlist";
  }
  if (normalized.condition_id) {
    params.set("condition_id", normalized.condition_id);
  }
  if (normalized.yes_token) {
    params.set("yes_token", normalized.yes_token);
  }
  if (normalized.no_token) {
    params.set("no_token", normalized.no_token);
  }
  if (normalized.question) {
    params.set("question", normalized.question);
  }
  if (normalized.slug) {
    params.set("slug", normalized.slug);
  }
  if (normalized.event_slug) {
    params.set("event_slug", normalized.event_slug);
  }
  if (normalized.group_item_title) {
    params.set("group_item_title", normalized.group_item_title);
  }
  if (normalized.url) {
    params.set("url", normalized.url);
  }
  if (normalized.category) {
    params.set("category", normalized.category);
  }
  params.set("focus", marketUi.marketIdentityKey(normalized));
  return `/watchlist?${params.toString()}`;
}

function consumeMarketFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const market = marketUi.normalizeWatchlistMarket({
    condition_id: params.get("condition_id") || "",
    yes_token: params.get("yes_token") || "",
    no_token: params.get("no_token") || "",
    question: params.get("question") || "",
    slug: params.get("slug") || "",
    event_slug: params.get("event_slug") || "",
    group_item_title: params.get("group_item_title") || "",
    url: params.get("url") || "",
    category: params.get("category") || "",
  });
  if (!market) {
    focusedIdentity = params.get("focus") || "";
    return;
  }
  const result = marketUi.addToWatchlist(market);
  focusedIdentity = params.get("focus") || marketUi.marketIdentityKey(result.data || market);
  watchlistMeta.textContent = "已从其他页面同步打开该市场。";
  window.history.replaceState({}, document.title, window.location.pathname);
}

function renderWatchlist() {
  const markets = marketUi.loadWatchlist();
  watchlistMeta.textContent = focusedIdentity
    ? `当前共 ${markets.length} 个自选市场，已聚焦当前打开的目标。`
    : `当前共 ${markets.length} 个自选市场`;
  if (!markets.length) {
    setStatus(watchlistTable, "还没有自选市场，请先在首页查询或工作台里加入。");
    return;
  }
  const rows = markets.map((market) => {
    const url = marketUi.buildPolymarketUrl(market);
    const identity = marketUi.marketIdentityKey(market);
    const activeStyle = identity === focusedIdentity ? ' style="background:rgba(59,130,246,0.08);outline:1px solid rgba(59,130,246,0.35);"' : "";
    return `
      <tr${activeStyle}>
        <td>
          <div style="display:flex;flex-direction:column;gap:6px;">
            <strong><a class="market-text-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(market.question || market.label || "-")}</a></strong>
            <div class="muted">Condition ID: ${escapeHtml(market.condition_id || "-")}</div>
          </div>
        </td>
        <td>${escapeHtml(market.category || "-")}</td>
        <td>${escapeHtml(market.end_date || "-")}</td>
        <td>${formatTime(market.added_at)}</td>
        <td>
          <div class="table-actions">
            <a class="table-link-button" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">外链</a>
            <a class="table-link-button" href="${escapeHtml(buildInternalWatchlistUrl(market))}" target="_blank" rel="noopener noreferrer">系统内打开</a>
            <button class="mini ghost" type="button" data-remove-watch="${escapeHtml(identity)}">移除</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
  watchlistTable.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Question</th>
          <th>Category</th>
          <th>End Date</th>
          <th>Added</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

watchlistTable.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-watch]");
  if (!button) {
    return;
  }
  if (button.dataset.removeWatch === focusedIdentity) {
    focusedIdentity = "";
  }
  marketUi.removeFromWatchlist(button.dataset.removeWatch);
  renderWatchlist();
});

watchlistRefreshBtn.addEventListener("click", renderWatchlist);

window.addEventListener("storage", (event) => {
  if (event.key !== "polymarketWatchlist") {
    return;
  }
  renderWatchlist();
});

consumeMarketFromQuery();
renderWatchlist();
