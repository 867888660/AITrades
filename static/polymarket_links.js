const POLYMARKET_WATCHLIST_KEY = "polymarketWatchlist";

function safeText(value) {
  return String(value ?? "").trim();
}

function marketIdentityKey(market) {
  return [
    safeText(market?.condition_id),
    safeText(market?.yes_token),
    safeText(market?.no_token),
  ].join("|");
}

function normalizeWatchlistMarket(market) {
  if (!market || typeof market !== "object") {
    return null;
  }
  const question = safeText(market.question) || safeText(market.label) || safeText(market.slug) || "Unnamed Market";
  const normalized = {
    condition_id: safeText(market.condition_id),
    yes_token: safeText(market.yes_token),
    no_token: safeText(market.no_token),
    slug: safeText(market.slug) || safeText(market.raw?.slug),
    event_slug: safeText(market.event_slug) || safeText(market.eventSlug) || safeText(market.raw?.eventSlug) || safeText(market.raw?.event_slug),
    group_item_title: safeText(market.group_item_title) || safeText(market.groupItemTitle) || safeText(market.raw?.groupItemTitle),
    url: safeText(market.url) || safeText(market.raw?.url),
    question,
    label: safeText(market.label) || question,
    category: safeText(market.category) || safeText(market.market_category),
    end_date: safeText(market.end_date),
    added_at: safeText(market.added_at) || new Date().toISOString(),
  };
  return marketIdentityKey(normalized).replaceAll("|", "") ? normalized : null;
}

function buildPolymarketUrl(market) {
  const directUrl = safeText(market?.url) || safeText(market?.raw?.url);
  if (directUrl) {
    if (directUrl.startsWith("http://") || directUrl.startsWith("https://")) {
      return directUrl;
    }
    if (directUrl.startsWith("/")) {
      return `https://polymarket.com${directUrl}`;
    }
  }
  const slug = safeText(market?.slug) || safeText(market?.raw?.slug);
  const eventSlug = safeText(market?.event_slug) || safeText(market?.eventSlug) || safeText(market?.raw?.eventSlug) || safeText(market?.raw?.event_slug);
  if (eventSlug && slug) {
    return `https://polymarket.com/event/${encodeURIComponent(eventSlug)}/${encodeURIComponent(slug)}`;
  }
  const groupItemTitle = safeText(market?.group_item_title) || safeText(market?.groupItemTitle) || safeText(market?.raw?.groupItemTitle);
  const question = safeText(market?.question) || safeText(market?.label);
  if (slug && groupItemTitle && question) {
    return `https://polymarket.com/search?q=${encodeURIComponent(question)}`;
  }
  if (slug) {
    return `https://polymarket.com/event/${encodeURIComponent(slug)}`;
  }
  if (question) {
    return `https://polymarket.com/search?q=${encodeURIComponent(question)}`;
  }
  const identity = safeText(market?.condition_id) || safeText(market?.token_id) || safeText(market?.yes_token) || safeText(market?.no_token);
  if (identity) {
    return `https://polymarket.com/search?q=${encodeURIComponent(identity)}`;
  }
  return "https://polymarket.com";
}

function loadWatchlist() {
  try {
    const parsed = JSON.parse(localStorage.getItem(POLYMARKET_WATCHLIST_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.map(normalizeWatchlistMarket).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function saveWatchlist(markets) {
  localStorage.setItem(POLYMARKET_WATCHLIST_KEY, JSON.stringify((markets || []).map(normalizeWatchlistMarket).filter(Boolean)));
}

function isInWatchlist(market) {
  const key = marketIdentityKey(market);
  return Boolean(key) && loadWatchlist().some((item) => marketIdentityKey(item) === key);
}

function addToWatchlist(market) {
  const normalized = normalizeWatchlistMarket(market);
  if (!normalized) {
    return { ok: false };
  }
  const list = loadWatchlist().filter((item) => marketIdentityKey(item) !== marketIdentityKey(normalized));
  list.unshift(normalized);
  saveWatchlist(list);
  return { ok: true, data: normalized };
}

function removeFromWatchlist(identity) {
  const key = safeText(identity);
  const list = loadWatchlist().filter((item) => marketIdentityKey(item) !== key);
  saveWatchlist(list);
  return { ok: true };
}

function toggleWatchlist(market) {
  const normalized = normalizeWatchlistMarket(market);
  if (!normalized) {
    return { ok: false, active: false };
  }
  const key = marketIdentityKey(normalized);
  if (isInWatchlist(normalized)) {
    removeFromWatchlist(key);
    return { ok: true, active: false, data: normalized };
  }
  addToWatchlist(normalized);
  return { ok: true, active: true, data: normalized };
}

window.PolyMarketUi = {
  buildPolymarketUrl,
  normalizeWatchlistMarket,
  marketIdentityKey,
  loadWatchlist,
  saveWatchlist,
  isInWatchlist,
  addToWatchlist,
  removeFromWatchlist,
  toggleWatchlist,
};
