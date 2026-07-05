from __future__ import annotations

import hashlib
import html
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote_plus
from xml.etree import ElementTree

from services.config_loader import BASE_DIR
from services.event_graph_logic import (
    STRUCTURED_EVENT_FIELDS,
    derived_edges_from_expression,
    infer_relation_class,
    normalize_event_semantic,
)
from services.http_client import SESSION


EVENT_DB_PATH = BASE_DIR / "Data" / "EventGraph.db"
DEFAULT_REFRESH_SECONDS = 900
DEFAULT_FEED_LIMIT = 24
MAX_FEED_LIMIT = 80

GLOBAL_NEWS_FEEDS: List[Dict[str, str]] = [
    {
        "id": "google_top_us",
        "label": "Google News Top Stories",
        "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        "region": "global",
        "category": "top",
    },
    {
        "id": "google_world",
        "label": "Google News World",
        "url": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
        "region": "global",
        "category": "world",
    },
    {
        "id": "google_business",
        "label": "Google News Business",
        "url": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
        "region": "global",
        "category": "business",
    },
    {
        "id": "google_technology",
        "label": "Google News Technology",
        "url": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
        "region": "global",
        "category": "technology",
    },
    {
        "id": "bbc_top",
        "label": "BBC News",
        "url": "https://feeds.bbci.co.uk/news/rss.xml",
        "region": "global",
        "category": "top",
    },
    {
        "id": "bbc_world",
        "label": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "region": "global",
        "category": "world",
    },
    {
        "id": "bbc_business",
        "label": "BBC Business",
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "region": "global",
        "category": "business",
    },
]

_DDL = """
CREATE TABLE IF NOT EXISTS event_graph_observations (
    observation_id      TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL DEFAULT '',
    source_label        TEXT NOT NULL DEFAULT '',
    source_url          TEXT NOT NULL DEFAULT '',
    source_type         TEXT NOT NULL DEFAULT 'rss',
    region              TEXT NOT NULL DEFAULT '',
    category            TEXT NOT NULL DEFAULT '',
    query               TEXT NOT NULL DEFAULT '',
    title               TEXT NOT NULL DEFAULT '',
    clean_title         TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    url                 TEXT NOT NULL DEFAULT '',
    published_at_utc    TEXT NOT NULL DEFAULT '',
    fetched_at_utc      TEXT NOT NULL,
    heat                REAL NOT NULL DEFAULT 1.0,
    raw_json            TEXT NOT NULL DEFAULT '{}',
    UNIQUE(url)
);

CREATE INDEX IF NOT EXISTS idx_event_graph_observations_fetched
ON event_graph_observations(fetched_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_event_graph_observations_query
ON event_graph_observations(query, fetched_at_utc DESC);

CREATE TABLE IF NOT EXISTS event_graph_events (
    event_id            TEXT PRIMARY KEY,
    canonical_key       TEXT NOT NULL UNIQUE,
    title               TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'ACTIVE',
    heat                REAL NOT NULL DEFAULT 1.0,
    source_count        INTEGER NOT NULL DEFAULT 0,
    observation_count   INTEGER NOT NULL DEFAULT 0,
    first_seen_utc      TEXT NOT NULL,
    last_seen_utc       TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'SYSTEM_DERIVED',
    source_type         TEXT NOT NULL DEFAULT 'NEWS_RSS'
);

CREATE INDEX IF NOT EXISTS idx_event_graph_events_heat
ON event_graph_events(heat DESC, updated_at_utc DESC);

CREATE TABLE IF NOT EXISTS event_graph_event_observations (
    event_id        TEXT NOT NULL,
    observation_id  TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.65,
    reason          TEXT NOT NULL DEFAULT '',
    created_at_utc  TEXT NOT NULL,
    PRIMARY KEY(event_id, observation_id)
);

CREATE INDEX IF NOT EXISTS idx_event_graph_event_observations_observation
ON event_graph_event_observations(observation_id);

CREATE TABLE IF NOT EXISTS event_graph_refresh_runs (
    run_id                  TEXT PRIMARY KEY,
    mode                    TEXT NOT NULL DEFAULT 'global',
    query                   TEXT NOT NULL DEFAULT '',
    status                  TEXT NOT NULL DEFAULT 'running',
    started_at_utc          TEXT NOT NULL,
    finished_at_utc         TEXT,
    sources_attempted       INTEGER NOT NULL DEFAULT 0,
    sources_ok              INTEGER NOT NULL DEFAULT 0,
    observations_seen       INTEGER NOT NULL DEFAULT 0,
    observations_inserted   INTEGER NOT NULL DEFAULT 0,
    observations_linked     INTEGER NOT NULL DEFAULT 0,
    events_upserted         INTEGER NOT NULL DEFAULT 0,
    error                   TEXT NOT NULL DEFAULT '',
    details_json            TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_event_graph_refresh_runs_started
ON event_graph_refresh_runs(started_at_utc DESC);

CREATE TABLE IF NOT EXISTS agent_event_change_requests (
    request_id      TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL DEFAULT '',
    workflow_id     TEXT NOT NULL DEFAULT '',
    change_type     TEXT NOT NULL DEFAULT '',
    requester       TEXT NOT NULL DEFAULT '',
    requester_type  TEXT NOT NULL DEFAULT 'agent',
    requester_id    TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'PENDING',
    risk_level      TEXT NOT NULL DEFAULT 'medium',
    title           TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    evidence_summary TEXT NOT NULL DEFAULT '',
    patch_json      TEXT NOT NULL DEFAULT '{}',
    validation_json TEXT NOT NULL DEFAULT '{}',
    target_refs_json TEXT NOT NULL DEFAULT '[]',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    created_at_utc  TEXT NOT NULL,
    updated_at_utc  TEXT NOT NULL DEFAULT '',
    reviewed_at_utc TEXT,
    reviewer_type   TEXT NOT NULL DEFAULT '',
    reviewer_id     TEXT NOT NULL DEFAULT '',
    review_note     TEXT NOT NULL DEFAULT '',
    applied_at_utc  TEXT,
    applied_by_type TEXT NOT NULL DEFAULT '',
    applied_by_id   TEXT NOT NULL DEFAULT '',
    apply_error_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_agent_event_change_requests_status
ON agent_event_change_requests(status, created_at_utc DESC);

CREATE TABLE IF NOT EXISTS graph_events (
    event_id            TEXT PRIMARY KEY,
    title               TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    event_type          TEXT NOT NULL DEFAULT 'ATOMIC',
    lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
    verification_status TEXT NOT NULL DEFAULT 'HUMAN_VERIFIED',
    time_window_start   TEXT NOT NULL DEFAULT '',
    time_window_end     TEXT NOT NULL DEFAULT '',
    source_type         TEXT NOT NULL DEFAULT 'GRAPH_CORE',
    origin_request_id   TEXT NOT NULL DEFAULT '',
    origin_run_id       TEXT NOT NULL DEFAULT '',
    current_version     INTEGER NOT NULL DEFAULT 1,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_events_updated
ON graph_events(updated_at_utc DESC);

CREATE TABLE IF NOT EXISTS graph_event_versions (
    version_id          TEXT PRIMARY KEY,
    event_id            TEXT NOT NULL,
    version_number      INTEGER NOT NULL,
    request_id          TEXT NOT NULL DEFAULT '',
    run_id              TEXT NOT NULL DEFAULT '',
    actor_type          TEXT NOT NULL DEFAULT '',
    actor_id            TEXT NOT NULL DEFAULT '',
    change_type         TEXT NOT NULL DEFAULT '',
    before_json         TEXT NOT NULL DEFAULT '{}',
    after_json          TEXT NOT NULL DEFAULT '{}',
    patch_item_json     TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_event_versions_event
ON graph_event_versions(event_id, version_number DESC);

CREATE TABLE IF NOT EXISTS graph_event_semantics (
    event_id            TEXT PRIMARY KEY,
    subject             TEXT NOT NULL DEFAULT '',
    predicate           TEXT NOT NULL DEFAULT '',
    object              TEXT NOT NULL DEFAULT '',
    comparator          TEXT NOT NULL DEFAULT '',
    threshold           REAL,
    unit                TEXT NOT NULL DEFAULT '',
    time_window_start   TEXT NOT NULL DEFAULT '',
    time_window_end     TEXT NOT NULL DEFAULT '',
    jurisdiction        TEXT NOT NULL DEFAULT '',
    resolution_rule     TEXT NOT NULL DEFAULT '',
    resolution_source   TEXT NOT NULL DEFAULT '',
    outcome_space_id    TEXT NOT NULL DEFAULT '',
    semantic_type       TEXT NOT NULL DEFAULT '',
    confidence          REAL,
    source              TEXT NOT NULL DEFAULT '',
    current_version     INTEGER NOT NULL DEFAULT 1,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_event_semantics_family
ON graph_event_semantics(subject, predicate, unit, time_window_start, time_window_end);

CREATE INDEX IF NOT EXISTS idx_graph_event_semantics_outcome
ON graph_event_semantics(outcome_space_id);

CREATE TABLE IF NOT EXISTS graph_event_semantic_versions (
    version_id          TEXT PRIMARY KEY,
    event_id            TEXT NOT NULL,
    version_number      INTEGER NOT NULL,
    request_id          TEXT NOT NULL DEFAULT '',
    run_id              TEXT NOT NULL DEFAULT '',
    actor_type          TEXT NOT NULL DEFAULT '',
    actor_id            TEXT NOT NULL DEFAULT '',
    before_json         TEXT NOT NULL DEFAULT '{}',
    after_json          TEXT NOT NULL DEFAULT '{}',
    patch_item_json     TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_event_semantic_versions_event
ON graph_event_semantic_versions(event_id, version_number DESC);

CREATE TABLE IF NOT EXISTS graph_finance_nodes (
    finance_id          TEXT PRIMARY KEY,
    label               TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    finance_type        TEXT NOT NULL DEFAULT 'VARIABLE',
    venue               TEXT NOT NULL DEFAULT '',
    symbol              TEXT NOT NULL DEFAULT '',
    lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
    verification_status TEXT NOT NULL DEFAULT 'HUMAN_VERIFIED',
    source_type         TEXT NOT NULL DEFAULT 'GRAPH_CORE',
    origin_request_id   TEXT NOT NULL DEFAULT '',
    origin_run_id       TEXT NOT NULL DEFAULT '',
    current_version     INTEGER NOT NULL DEFAULT 1,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_finance_nodes_updated
ON graph_finance_nodes(updated_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_graph_finance_nodes_symbol
ON graph_finance_nodes(symbol, venue);

CREATE TABLE IF NOT EXISTS graph_finance_versions (
    version_id          TEXT PRIMARY KEY,
    finance_id          TEXT NOT NULL,
    version_number      INTEGER NOT NULL,
    request_id          TEXT NOT NULL DEFAULT '',
    run_id              TEXT NOT NULL DEFAULT '',
    actor_type          TEXT NOT NULL DEFAULT '',
    actor_id            TEXT NOT NULL DEFAULT '',
    change_type         TEXT NOT NULL DEFAULT '',
    before_json         TEXT NOT NULL DEFAULT '{}',
    after_json          TEXT NOT NULL DEFAULT '{}',
    patch_item_json     TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_finance_versions_node
ON graph_finance_versions(finance_id, version_number DESC);

CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id             TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL DEFAULT '',
    source_type         TEXT NOT NULL DEFAULT 'node',
    target_id           TEXT NOT NULL DEFAULT '',
    target_type         TEXT NOT NULL DEFAULT 'node',
    relation_class      TEXT NOT NULL DEFAULT '',
    relation_type       TEXT NOT NULL DEFAULT '',
    confidence          REAL NOT NULL DEFAULT 0.65,
    strength            TEXT NOT NULL DEFAULT 'MEDIUM',
    mechanism           TEXT NOT NULL DEFAULT '',
    reason              TEXT NOT NULL DEFAULT '',
    evidence_summary    TEXT NOT NULL DEFAULT '',
    lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
    verification_status TEXT NOT NULL DEFAULT 'HUMAN_VERIFIED',
    source_kind         TEXT NOT NULL DEFAULT 'GRAPH_CORE',
    origin_request_id   TEXT NOT NULL DEFAULT '',
    origin_run_id       TEXT NOT NULL DEFAULT '',
    current_version     INTEGER NOT NULL DEFAULT 1,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_edges_source
ON graph_edges(source_id, relation_type);

CREATE INDEX IF NOT EXISTS idx_graph_edges_target
ON graph_edges(target_id, relation_type);

CREATE TABLE IF NOT EXISTS graph_edge_versions (
    version_id          TEXT PRIMARY KEY,
    edge_id             TEXT NOT NULL,
    version_number      INTEGER NOT NULL,
    request_id          TEXT NOT NULL DEFAULT '',
    run_id              TEXT NOT NULL DEFAULT '',
    actor_type          TEXT NOT NULL DEFAULT '',
    actor_id            TEXT NOT NULL DEFAULT '',
    change_type         TEXT NOT NULL DEFAULT '',
    before_json         TEXT NOT NULL DEFAULT '{}',
    after_json          TEXT NOT NULL DEFAULT '{}',
    patch_item_json     TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_edge_versions_edge
ON graph_edge_versions(edge_id, version_number DESC);

CREATE TABLE IF NOT EXISTS graph_expressions (
    expression_id       TEXT PRIMARY KEY,
    label               TEXT NOT NULL DEFAULT '',
    summary             TEXT NOT NULL DEFAULT '',
    expression_type     TEXT NOT NULL DEFAULT 'RULE',
    language            TEXT NOT NULL DEFAULT 'json',
    expression_json     TEXT NOT NULL DEFAULT '{}',
    lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
    verification_status TEXT NOT NULL DEFAULT 'HUMAN_VERIFIED',
    source_type         TEXT NOT NULL DEFAULT 'GRAPH_CORE',
    origin_request_id   TEXT NOT NULL DEFAULT '',
    origin_run_id       TEXT NOT NULL DEFAULT '',
    current_version     INTEGER NOT NULL DEFAULT 1,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_expressions_updated
ON graph_expressions(updated_at_utc DESC);

CREATE TABLE IF NOT EXISTS graph_expression_versions (
    version_id          TEXT PRIMARY KEY,
    expression_id       TEXT NOT NULL,
    version_number      INTEGER NOT NULL,
    request_id          TEXT NOT NULL DEFAULT '',
    run_id              TEXT NOT NULL DEFAULT '',
    actor_type          TEXT NOT NULL DEFAULT '',
    actor_id            TEXT NOT NULL DEFAULT '',
    change_type         TEXT NOT NULL DEFAULT '',
    before_json         TEXT NOT NULL DEFAULT '{}',
    after_json          TEXT NOT NULL DEFAULT '{}',
    patch_item_json     TEXT NOT NULL DEFAULT '{}',
    created_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_expression_versions_expr
ON graph_expression_versions(expression_id, version_number DESC);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _hash_id(prefix: str, value: str, size: int = 16) -> str:
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:size]
    return f"{prefix}_{digest}"


def _connect() -> sqlite3.Connection:
    EVENT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(EVENT_DB_PATH), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(_DDL)
    _ensure_change_request_columns(conn)
    conn.commit()
    return conn


def _ensure_change_request_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(agent_event_change_requests)").fetchall()
    existing = {str(row["name"]) for row in rows}
    columns = {
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "workflow_id": "TEXT NOT NULL DEFAULT ''",
        "requester_type": "TEXT NOT NULL DEFAULT 'agent'",
        "requester_id": "TEXT NOT NULL DEFAULT ''",
        "risk_level": "TEXT NOT NULL DEFAULT 'medium'",
        "title": "TEXT NOT NULL DEFAULT ''",
        "summary": "TEXT NOT NULL DEFAULT ''",
        "reason": "TEXT NOT NULL DEFAULT ''",
        "evidence_summary": "TEXT NOT NULL DEFAULT ''",
        "patch_json": "TEXT NOT NULL DEFAULT '{}'",
        "validation_json": "TEXT NOT NULL DEFAULT '{}'",
        "target_refs_json": "TEXT NOT NULL DEFAULT '[]'",
        "updated_at_utc": "TEXT NOT NULL DEFAULT ''",
        "reviewer_type": "TEXT NOT NULL DEFAULT ''",
        "reviewer_id": "TEXT NOT NULL DEFAULT ''",
        "applied_at_utc": "TEXT",
        "applied_by_type": "TEXT NOT NULL DEFAULT ''",
        "applied_by_id": "TEXT NOT NULL DEFAULT ''",
        "apply_error_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE agent_event_change_requests ADD COLUMN {name} {definition}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_event_change_requests_run "
        "ON agent_event_change_requests(run_id, created_at_utc DESC)"
    )


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


def _parse_json(value: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or json.dumps(default, ensure_ascii=False))
    except Exception:
        return default


def _safe_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        result = default
    return max(min_value, min(result, max_value))


def _strip_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_title(value: Any) -> str:
    text = _strip_html(value)
    # Google News RSS often emits "headline - publisher"; keep the event phrase.
    if " - " in text:
        head, tail = text.rsplit(" - ", 1)
        if head and len(tail) <= 80:
            text = head
    return re.sub(r"\s+", " ", text).strip()


def _canonical_key(title: str) -> str:
    text = _clean_title(title).lower()
    text = re.sub(r"['’]s\b", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "as", "by",
        "from", "at", "after", "before", "over", "under", "is", "are", "was", "were",
        "will", "could", "may", "says", "said", "live", "updates",
    }
    tokens = [token for token in re.sub(r"\s+", " ", text).strip().split(" ") if token and token not in stop]
    return " ".join(tokens[:14]) or text[:120] or "untitled"


def _parse_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return ""


def _find_text(element: ElementTree.Element, *names: str) -> str:
    for name in names:
        child = element.find(name)
        if child is not None and child.text:
            return child.text
    for child in list(element):
        short_name = child.tag.split("}")[-1].lower()
        if short_name in {name.lower().split("}")[-1] for name in names} and child.text:
            return child.text
    return ""


def _rss_items(xml_text: str) -> List[Dict[str, Any]]:
    root = ElementTree.fromstring(xml_text.encode("utf-8", errors="ignore"))
    rows: List[Dict[str, Any]] = []
    for item in root.findall(".//item"):
        rows.append({
            "title": _find_text(item, "title"),
            "summary": _find_text(item, "description", "summary"),
            "url": _find_text(item, "link"),
            "published_at": _find_text(item, "pubDate", "published", "updated"),
            "raw_tag": item.tag,
        })
    atom_ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(f".//{atom_ns}entry"):
        link = ""
        for link_el in entry.findall(f"{atom_ns}link"):
            link = link_el.attrib.get("href", "") or link
        rows.append({
            "title": _find_text(entry, f"{atom_ns}title", "title"),
            "summary": _find_text(entry, f"{atom_ns}summary", "summary", f"{atom_ns}content", "content"),
            "url": link,
            "published_at": _find_text(entry, f"{atom_ns}published", "published", f"{atom_ns}updated", "updated"),
            "raw_tag": entry.tag,
        })
    return rows


def _observation_heat(index: int, published_at: str, category: str) -> float:
    rank_component = max(0.0, 28.0 - min(index, 28) * 0.8)
    category_bonus = 8.0 if category in {"top", "world", "business"} else 4.0
    recency_component = 0.0
    if published_at:
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            hours = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)
            recency_component = max(0.0, 28.0 - hours * 1.4)
        except Exception:
            recency_component = 8.0
    return round(max(1.0, min(100.0, 30.0 + rank_component + category_bonus + recency_component)), 1)


def _row_to_observation(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["raw"] = _parse_json(data.pop("raw_json", "{}"), {})
    return data


def _row_to_event(row: sqlite3.Row, observations: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    data = dict(row)
    if observations is not None:
        data["observations"] = observations
    return data


def _fetch_feed(feed: Dict[str, str], limit: int) -> Dict[str, Any]:
    response = SESSION.get(feed["url"], timeout=12)
    response.raise_for_status()
    rows = _rss_items(response.text)
    return {"feed": feed, "items": rows[:limit], "status_code": response.status_code}


def _search_feed(query: str) -> Dict[str, str]:
    encoded = quote_plus(query)
    return {
        "id": "google_news_search",
        "label": "Google News Search",
        "url": f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en",
        "region": "global",
        "category": "search",
    }


def _ingest_items(
    conn: sqlite3.Connection,
    *,
    feed: Dict[str, str],
    items: Iterable[Dict[str, Any]],
    query: str = "",
) -> Dict[str, int]:
    stats = {
        "observations_seen": 0,
        "observations_inserted": 0,
        "observations_linked": 0,
        "events_upserted": 0,
    }
    now = _now_iso()
    for index, item in enumerate(items):
        title = _strip_html(item.get("title"))
        clean_title = _clean_title(title)
        url = str(item.get("url") or "").strip()
        if not clean_title or not url:
            continue
        stats["observations_seen"] += 1
        published_at = _parse_date(item.get("published_at"))
        summary = _strip_html(item.get("summary"))
        heat = _observation_heat(index, published_at, str(feed.get("category") or ""))
        observation_id = _hash_id("obs", url, 18)
        raw = {
            "feed": feed,
            "item": item,
        }
        before = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO event_graph_observations(
                observation_id, source_id, source_label, source_url, source_type,
                region, category, query, title, clean_title, summary, url,
                published_at_utc, fetched_at_utc, heat, raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                observation_id,
                feed.get("id") or "",
                feed.get("label") or "",
                feed.get("url") or "",
                "rss_search" if query else "rss",
                feed.get("region") or "",
                feed.get("category") or "",
                query,
                title,
                clean_title,
                summary,
                url,
                published_at,
                now,
                heat,
                _json_text(raw),
            ),
        )
        observation_inserted = conn.total_changes > before
        if observation_inserted:
            stats["observations_inserted"] += 1
        else:
            existing_links = conn.execute(
                """SELECT event_id
                   FROM event_graph_event_observations
                   WHERE observation_id = ?""",
                (observation_id,),
            ).fetchall()
            if existing_links:
                for link in existing_links:
                    _refresh_event_rollup(conn, str(link["event_id"] or ""))
                continue
            existing_observation = conn.execute(
                """SELECT clean_title, summary, published_at_utc, heat
                   FROM event_graph_observations
                   WHERE observation_id = ?""",
                (observation_id,),
            ).fetchone()
            if existing_observation:
                clean_title = str(existing_observation["clean_title"] or clean_title)
                summary = str(existing_observation["summary"] or summary)
                published_at = str(existing_observation["published_at_utc"] or published_at)
                heat = float(existing_observation["heat"] or heat)

        canonical_key = _canonical_key(clean_title)
        event_id = _hash_id("news_evt", canonical_key, 16)
        first_seen = published_at or now
        conn.execute(
            """INSERT OR IGNORE INTO event_graph_events(
                event_id, canonical_key, title, summary, heat, source_count,
                observation_count, first_seen_utc, last_seen_utc, updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (event_id, canonical_key, clean_title, summary, heat, 1, 0, first_seen, now, now),
        )
        link_before = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO event_graph_event_observations(
                event_id, observation_id, confidence, reason, created_at_utc
            ) VALUES (?,?,?,?,?)""",
            (event_id, observation_id, 0.72, "News title canonical-key match.", now),
        )
        if conn.total_changes > link_before:
            stats["observations_linked"] += 1
        stats["events_upserted"] += 1
        _refresh_event_rollup(conn, event_id)
    return stats


def _refresh_event_rollup(conn: sqlite3.Connection, event_id: str) -> None:
    rows = conn.execute(
        """SELECT o.*
           FROM event_graph_observations o
           JOIN event_graph_event_observations l ON l.observation_id = o.observation_id
           WHERE l.event_id = ?
           ORDER BY o.heat DESC, o.published_at_utc DESC, o.fetched_at_utc DESC""",
        (event_id,),
    ).fetchall()
    if not rows:
        return
    source_count = len({str(row["source_id"] or row["source_label"] or "") for row in rows})
    observation_count = len(rows)
    max_heat = max(float(row["heat"] or 1.0) for row in rows)
    heat = min(100.0, max_heat + min(18.0, math.log1p(observation_count) * 5.5) + min(12.0, source_count * 2.5))
    first_seen = min([str(row["published_at_utc"] or row["fetched_at_utc"]) for row in rows if str(row["published_at_utc"] or row["fetched_at_utc"])])
    last_seen = max([str(row["fetched_at_utc"] or row["published_at_utc"]) for row in rows if str(row["fetched_at_utc"] or row["published_at_utc"])])
    top = rows[0]
    conn.execute(
        """UPDATE event_graph_events
           SET title = ?, summary = ?, heat = ?, source_count = ?, observation_count = ?,
               first_seen_utc = ?, last_seen_utc = ?, updated_at_utc = ?
           WHERE event_id = ?""",
        (
            top["clean_title"] or top["title"] or "",
            top["summary"] or "",
            round(heat, 1),
            source_count,
            observation_count,
            first_seen,
            last_seen,
            _now_iso(),
            event_id,
        ),
    )


def refresh_news(
    *,
    query: str = "",
    limit_per_source: int = DEFAULT_FEED_LIMIT,
    feeds: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    query = str(query or "").strip()
    limit = _safe_int(limit_per_source, DEFAULT_FEED_LIMIT, 1, MAX_FEED_LIMIT)
    selected_feeds = [_search_feed(query)] if query else list(feeds or GLOBAL_NEWS_FEEDS)
    run_id = _new_id("run")
    mode = "search" if query else "global"
    started = _now_iso()
    details: List[Dict[str, Any]] = []
    totals = {
        "sources_attempted": 0,
        "sources_ok": 0,
        "observations_seen": 0,
        "observations_inserted": 0,
        "observations_linked": 0,
        "events_upserted": 0,
    }
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO event_graph_refresh_runs(
                run_id, mode, query, status, started_at_utc, details_json
            ) VALUES (?,?,?,?,?,?)""",
            (run_id, mode, query, "running", started, "[]"),
        )
        conn.commit()
        for feed in selected_feeds:
            totals["sources_attempted"] += 1
            detail = {"source_id": feed.get("id"), "label": feed.get("label"), "ok": False, "count": 0, "error": ""}
            try:
                fetched = _fetch_feed(feed, limit)
                items = fetched["items"]
                detail["count"] = len(items)
                ingest_stats = _ingest_items(conn, feed=feed, items=items, query=query)
                for key in ("observations_seen", "observations_inserted", "observations_linked", "events_upserted"):
                    totals[key] += ingest_stats[key]
                totals["sources_ok"] += 1
                detail["ok"] = True
                detail["status_code"] = fetched.get("status_code")
                conn.commit()
            except Exception as exc:
                conn.rollback()
                detail["error"] = str(exc)[:500]
            details.append(detail)
        status = "ok" if totals["sources_ok"] else "failed"
        finished = _now_iso()
        error = "" if totals["sources_ok"] else "; ".join(item.get("error", "") for item in details if item.get("error"))[:900]
        conn.execute(
            """UPDATE event_graph_refresh_runs
               SET status = ?, finished_at_utc = ?, sources_attempted = ?, sources_ok = ?,
                   observations_seen = ?, observations_inserted = ?, observations_linked = ?,
                   events_upserted = ?, error = ?, details_json = ?
               WHERE run_id = ?""",
            (
                status,
                finished,
                totals["sources_attempted"],
                totals["sources_ok"],
                totals["observations_seen"],
                totals["observations_inserted"],
                totals["observations_linked"],
                totals["events_upserted"],
                error,
                _json_text(details),
                run_id,
            ),
        )
        conn.commit()
        return {
            "run_id": run_id,
            "mode": mode,
            "query": query,
            "status": status,
            "started_at": started,
            "finished_at": finished,
            **totals,
            "details": details,
            "error": error,
        }
    finally:
        conn.close()


def list_events(*, q: str = "", limit: int = 50, include_observations: bool = True) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        limit_num = _safe_int(limit, 50, 1, 300)
        query = str(q or "").strip()
        args: List[Any] = []
        where = ""
        if query:
            where = "WHERE title LIKE ? OR summary LIKE ? OR canonical_key LIKE ?"
            like = f"%{query}%"
            args.extend([like, like, like])
        rows = conn.execute(
            f"""SELECT * FROM event_graph_events
                {where}
                ORDER BY heat DESC, last_seen_utc DESC
                LIMIT ?""",
            (*args, limit_num),
        ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            observations: Optional[List[Dict[str, Any]]] = None
            if include_observations:
                obs_rows = conn.execute(
                    """SELECT o.*
                       FROM event_graph_observations o
                       JOIN event_graph_event_observations l ON l.observation_id = o.observation_id
                       WHERE l.event_id = ?
                       ORDER BY o.heat DESC, o.published_at_utc DESC, o.fetched_at_utc DESC
                       LIMIT 8""",
                    (row["event_id"],),
                ).fetchall()
                observations = [_row_to_observation(obs) for obs in obs_rows]
            result.append(_row_to_event(row, observations))
        return result
    finally:
        conn.close()


def list_observations(*, event_id: str = "", q: str = "", limit: int = 80) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        limit_num = _safe_int(limit, 80, 1, 500)
        args: List[Any] = []
        if event_id:
            rows = conn.execute(
                """SELECT o.*
                   FROM event_graph_observations o
                   JOIN event_graph_event_observations l ON l.observation_id = o.observation_id
                   WHERE l.event_id = ?
                   ORDER BY o.fetched_at_utc DESC
                   LIMIT ?""",
                (event_id, limit_num),
            ).fetchall()
        else:
            where = ""
            if q:
                where = "WHERE title LIKE ? OR summary LIKE ? OR clean_title LIKE ?"
                like = f"%{q}%"
                args.extend([like, like, like])
            rows = conn.execute(
                f"""SELECT * FROM event_graph_observations
                    {where}
                    ORDER BY fetched_at_utc DESC
                    LIMIT ?""",
                (*args, limit_num),
            ).fetchall()
        return [_row_to_observation(row) for row in rows]
    finally:
        conn.close()


def _duplicate_observation_link_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS c
           FROM (
             SELECT observation_id
             FROM event_graph_event_observations
             GROUP BY observation_id
             HAVING COUNT(DISTINCT event_id) > 1
           )"""
    ).fetchone()
    return int(row["c"] if row else 0)


def _orphan_derived_event_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS c
           FROM event_graph_events e
           LEFT JOIN event_graph_event_observations l ON l.event_id = e.event_id
           WHERE l.event_id IS NULL"""
    ).fetchone()
    return int(row["c"] if row else 0)


def _choose_derived_event_keeper(conn: sqlite3.Connection, event_ids: List[str]) -> str:
    placeholders = ",".join("?" for _ in event_ids)
    rows = conn.execute(
        f"""SELECT e.*,
                  COUNT(l.observation_id) AS actual_observation_count
           FROM event_graph_events e
           LEFT JOIN event_graph_event_observations l ON l.event_id = e.event_id
           WHERE e.event_id IN ({placeholders})
           GROUP BY e.event_id""",
        event_ids,
    ).fetchall()
    if not rows:
        return sorted(event_ids)[0]

    def sort_key(row: sqlite3.Row) -> tuple:
        return (
            -int(row["actual_observation_count"] or 0),
            -int(row["source_count"] or 0),
            -float(row["heat"] or 0.0),
            str(row["first_seen_utc"] or ""),
            str(row["event_id"] or ""),
        )

    return str(sorted(rows, key=sort_key)[0]["event_id"])


def deduplicate_derived_events(*, dry_run: bool = False, limit: int = 500) -> Dict[str, Any]:
    """Merge duplicate news-derived events that share the same observation.

    This operates only on the derived/news layer tables:
    event_graph_events and event_graph_event_observations. Graph Core tables
    are intentionally untouched.
    """
    conn = _connect()
    try:
        limit_num = _safe_int(limit, 500, 1, 5000)
        before_duplicate_observations = _duplicate_observation_link_count(conn)
        before_orphan_events = _orphan_derived_event_count(conn)
        rows = conn.execute(
            """SELECT observation_id, GROUP_CONCAT(event_id) AS event_ids
               FROM event_graph_event_observations
               GROUP BY observation_id
               HAVING COUNT(DISTINCT event_id) > 1
               LIMIT ?""",
            (limit_num,),
        ).fetchall()

        parent: Dict[str, str] = {}

        def find(value: str) -> str:
            parent.setdefault(value, value)
            if parent[value] != value:
                parent[value] = find(parent[value])
            return parent[value]

        def union(left: str, right: str) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for row in rows:
            event_ids = [part for part in str(row["event_ids"] or "").split(",") if part]
            if not event_ids:
                continue
            first = event_ids[0]
            find(first)
            for event_id in event_ids[1:]:
                union(first, event_id)

        grouped: Dict[str, List[str]] = {}
        for event_id in list(parent):
            grouped.setdefault(find(event_id), []).append(event_id)
        components = [sorted(set(event_ids)) for event_ids in grouped.values() if len(set(event_ids)) > 1]

        report_components: List[Dict[str, Any]] = []
        moved_links = 0
        deleted_links = 0
        deleted_events = 0
        refreshed_events: List[str] = []
        now = _now_iso()

        for event_ids in components:
            keeper = _choose_derived_event_keeper(conn, event_ids)
            sources = [event_id for event_id in event_ids if event_id != keeper]
            component_report = {
                "keeper_event_id": keeper,
                "merged_event_ids": sources,
                "moved_links": 0,
                "deleted_links": 0,
                "deleted_events": 0,
            }
            if dry_run:
                source_placeholders = ",".join("?" for _ in sources)
                if source_placeholders:
                    row = conn.execute(
                        f"""SELECT COUNT(*) AS c
                            FROM event_graph_event_observations
                            WHERE event_id IN ({source_placeholders})""",
                        sources,
                    ).fetchone()
                    component_report["moved_links"] = int(row["c"] if row else 0)
                    component_report["deleted_links"] = component_report["moved_links"]
                    component_report["deleted_events"] = len(sources)
                report_components.append(component_report)
                continue

            for source_event_id in sources:
                link_rows = conn.execute(
                    """SELECT observation_id, confidence, reason, created_at_utc
                       FROM event_graph_event_observations
                       WHERE event_id = ?""",
                    (source_event_id,),
                ).fetchall()
                for link in link_rows:
                    insert_before = conn.total_changes
                    conn.execute(
                        """INSERT OR IGNORE INTO event_graph_event_observations(
                            event_id, observation_id, confidence, reason, created_at_utc
                        ) VALUES (?,?,?,?,?)""",
                        (
                            keeper,
                            link["observation_id"],
                            float(link["confidence"] or 0.65),
                            str(link["reason"] or f"Merged duplicate derived event {source_event_id}."),
                            str(link["created_at_utc"] or now),
                        ),
                    )
                    if conn.total_changes > insert_before:
                        moved_links += 1
                        component_report["moved_links"] += 1
                cursor = conn.execute(
                    "DELETE FROM event_graph_event_observations WHERE event_id = ?",
                    (source_event_id,),
                )
                deleted = max(0, cursor.rowcount)
                deleted_links += deleted
                component_report["deleted_links"] += deleted
                cursor = conn.execute("DELETE FROM event_graph_events WHERE event_id = ?", (source_event_id,))
                deleted = max(0, cursor.rowcount)
                deleted_events += deleted
                component_report["deleted_events"] += deleted

            _refresh_event_rollup(conn, keeper)
            refreshed_events.append(keeper)
            report_components.append(component_report)

        orphan_deleted = 0
        if not dry_run:
            cursor = conn.execute(
                """DELETE FROM event_graph_events
                   WHERE event_id NOT IN (
                     SELECT DISTINCT event_id FROM event_graph_event_observations
                   )"""
            )
            orphan_deleted = max(0, cursor.rowcount)
            conn.commit()

        after_duplicate_observations = _duplicate_observation_link_count(conn)
        after_orphan_events = _orphan_derived_event_count(conn)
        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "limited_to_duplicate_observations": limit_num,
            "before": {
                "duplicate_observations": before_duplicate_observations,
                "orphan_events": before_orphan_events,
            },
            "after": {
                "duplicate_observations": after_duplicate_observations,
                "orphan_events": after_orphan_events,
            },
            "components_found": len(components),
            "components": report_components[:50],
            "moved_links": moved_links,
            "deleted_links": deleted_links,
            "deleted_events": deleted_events,
            "orphan_events_deleted": orphan_deleted,
            "refreshed_event_ids": refreshed_events[:50],
            "scope": "derived_news_only",
        }
    finally:
        conn.close()


def get_status(limit: int = 10) -> Dict[str, Any]:
    conn = _connect()
    try:
        events = conn.execute("SELECT COUNT(*) AS c FROM event_graph_events").fetchone()["c"]
        observations = conn.execute("SELECT COUNT(*) AS c FROM event_graph_observations").fetchone()["c"]
        duplicate_observations = _duplicate_observation_link_count(conn)
        orphan_events = _orphan_derived_event_count(conn)
        last_rows = conn.execute(
            """SELECT * FROM event_graph_refresh_runs
               ORDER BY started_at_utc DESC
               LIMIT ?""",
            (_safe_int(limit, 10, 1, 50),),
        ).fetchall()
        runs = []
        for row in last_rows:
            item = dict(row)
            item["details"] = _parse_json(item.pop("details_json", "[]"), [])
            runs.append(item)
        return {
            "ok": True,
            "db_path": str(EVENT_DB_PATH),
            "events": events,
            "observations": observations,
            "duplicate_observations": duplicate_observations,
            "orphan_events": orphan_events,
            "sources": GLOBAL_NEWS_FEEDS,
            "scheduler": event_news_scheduler.state(),
            "runs": runs,
        }
    finally:
        conn.close()


def _row_to_change_request(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["payload"] = _parse_json(item.pop("payload_json", "{}"), {})
    item["patch"] = _parse_json(item.pop("patch_json", "{}"), {})
    item["validation"] = _parse_json(item.pop("validation_json", "{}"), {})
    item["target_refs"] = _parse_json(item.pop("target_refs_json", "[]"), [])
    if not item.get("requester_id"):
        item["requester_id"] = item.get("requester") or ""
    if not item.get("updated_at_utc"):
        item["updated_at_utc"] = item.get("created_at_utc") or ""
    return item


def create_change_request(
    change_type: str,
    requester: str,
    payload: Dict[str, Any],
    *,
    run_id: str = "",
    workflow_id: str = "",
    requester_type: str = "agent",
    requester_id: str = "",
    title: str = "",
    summary: str = "",
    reason: str = "",
    evidence_summary: str = "",
    risk_level: str = "medium",
    status: str = "PENDING",
    patch: Optional[Dict[str, Any]] = None,
    validation: Optional[Dict[str, Any]] = None,
    target_refs: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    request_id = f"cr_{uuid.uuid4().hex[:16]}"
    now = _now_iso()
    patch = patch if isinstance(patch, dict) else {}
    validation = validation if isinstance(validation, dict) else {}
    target_refs = target_refs if isinstance(target_refs, list) else []
    requester_id = str(requester_id or requester or "").strip()
    requester_type = str(requester_type or "agent").strip() or "agent"
    risk_level = str(risk_level or "medium").strip().lower() or "medium"
    status = str(status or "PENDING").strip().upper() or "PENDING"
    with _connect() as conn:
        conn.execute(
            """INSERT INTO agent_event_change_requests(
                request_id, run_id, workflow_id, change_type, requester, requester_type,
                requester_id, status, risk_level, title, summary, reason, evidence_summary,
                patch_json, validation_json, target_refs_json, payload_json, created_at_utc,
                updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                request_id,
                str(run_id or "").strip(),
                str(workflow_id or "").strip(),
                str(change_type or "").strip(),
                requester_id,
                requester_type,
                requester_id,
                status,
                risk_level,
                str(title or "").strip(),
                str(summary or "").strip(),
                str(reason or "").strip(),
                str(evidence_summary or "").strip(),
                _json_text(patch),
                _json_text(validation),
                _json_text(target_refs),
                _json_text(payload),
                now,
                now,
            ),
        )
    return {
        "request_id": request_id,
        "status": status,
        "risk_level": risk_level,
        "change_type": str(change_type or "").strip(),
        "run_id": str(run_id or "").strip(),
        "workflow_id": str(workflow_id or "").strip(),
        "created_at_utc": now,
    }


def list_change_requests(*, status: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM agent_event_change_requests WHERE status=? ORDER BY created_at_utc DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_event_change_requests ORDER BY created_at_utc DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_change_request(r) for r in rows]


def get_change_request(request_id: str) -> Dict[str, Any]:
    request_id = str(request_id or "").strip()
    if not request_id:
        raise ValueError("request_id is required")
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agent_event_change_requests WHERE request_id = ?", (request_id,)).fetchone()
    if not row:
        raise ValueError("change request not found")
    return _row_to_change_request(row)


def review_change_request(
    request_id: str,
    *,
    decision: str,
    reviewer_type: str = "human",
    reviewer_id: str = "",
    note: str = "",
) -> Dict[str, Any]:
    request_id = str(request_id or "").strip()
    decision_key = str(decision or "").strip().lower()
    status_map = {
        "approve": "APPROVED",
        "approved": "APPROVED",
        "reject": "REJECTED",
        "rejected": "REJECTED",
        "request_changes": "NEEDS_CHANGES",
        "needs_changes": "NEEDS_CHANGES",
    }
    status = status_map.get(decision_key)
    if not status:
        raise ValueError("invalid review decision")
    now = _now_iso()
    with _connect() as conn:
        row = conn.execute("SELECT request_id FROM agent_event_change_requests WHERE request_id = ?", (request_id,)).fetchone()
        if not row:
            raise ValueError("change request not found")
        conn.execute(
            """UPDATE agent_event_change_requests
               SET status = ?, reviewed_at_utc = ?, reviewer_type = ?, reviewer_id = ?,
                   review_note = ?, updated_at_utc = ?
               WHERE request_id = ?""",
            (
                status,
                now,
                str(reviewer_type or "human").strip() or "human",
                str(reviewer_id or "").strip(),
                str(note or "").strip(),
                now,
                request_id,
            ),
        )
    return get_change_request(request_id)


def _graph_event_from_row(row: sqlite3.Row | None) -> Dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    item["payload"] = _parse_json(item.pop("payload_json", "{}"), {})
    return item


def _graph_event_semantic_from_row(row: sqlite3.Row | None) -> Dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    item["payload"] = _parse_json(item.pop("payload_json", "{}"), {})
    return normalize_event_semantic(item, fallback=item.get("payload") if isinstance(item.get("payload"), dict) else {})


def _event_semantic_raw_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item.get("semantic") if isinstance(item.get("semantic"), dict) else item.get("semantics")
    if not isinstance(raw, dict):
        raw = {}
    direct = {field: item.get(field) for field in STRUCTURED_EVENT_FIELDS if item.get(field) not in (None, "")}
    if item.get("semantic_type") not in (None, ""):
        direct["semantic_type"] = item.get("semantic_type")
    if item.get("semantic_confidence") not in (None, ""):
        direct["confidence"] = item.get("semantic_confidence")
    if item.get("semantic_source") not in (None, ""):
        direct["source"] = item.get("semantic_source")
    return {**raw, **direct}


def _event_semantic_from_item(item: Dict[str, Any], *, existing: Dict[str, Any] | None = None) -> Dict[str, Any]:
    existing = dict(existing or {})
    raw = _event_semantic_raw_from_item(item)
    if raw:
        return normalize_event_semantic(raw, fallback=existing)
    if existing:
        return normalize_event_semantic(existing)
    return {}


def _load_event_semantics(conn: sqlite3.Connection, event_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [str(event_id or "").strip() for event_id in event_ids if str(event_id or "").strip()]
    if not ids:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(ids), 200):
        chunk = ids[start:start + 200]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT * FROM graph_event_semantics WHERE event_id IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            item = _graph_event_semantic_from_row(row)
            result[str(row["event_id"])] = item
    return result


def _write_graph_event_semantic_version(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    version_number: int,
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    before: Dict[str, Any],
    after: Dict[str, Any],
    patch_item: Dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        """INSERT INTO graph_event_semantic_versions(
            version_id, event_id, version_number, request_id, run_id, actor_type, actor_id,
            before_json, after_json, patch_item_json, created_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _new_id("gesv"),
            event_id,
            int(version_number),
            request.get("request_id") or "",
            request.get("run_id") or "",
            actor_type,
            actor_id,
            _json_text(before),
            _json_text(after),
            _json_text(patch_item),
            now,
        ),
    )


def _upsert_graph_event_semantic(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    semantic: Dict[str, Any],
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    patch_item: Dict[str, Any],
    now: str,
) -> Dict[str, Any]:
    semantic = normalize_event_semantic(semantic)
    if not semantic or not any(str(semantic.get(field) or "").strip() for field in ("subject", "predicate", "outcome_space_id", "resolution_rule")):
        return {}
    existing_row = conn.execute("SELECT * FROM graph_event_semantics WHERE event_id = ?", (event_id,)).fetchone()
    existing = _graph_event_semantic_from_row(existing_row)
    version_number = int(existing.get("current_version") or 0) + 1 if existing else 1
    payload = {
        "family_key": semantic.get("family_key") or "",
        "comparison_interval": semantic.get("comparison_interval") or {},
        "structured": bool(semantic.get("structured")),
    }
    values = (
        event_id,
        str(semantic.get("subject") or ""),
        str(semantic.get("predicate") or ""),
        str(semantic.get("object") or ""),
        str(semantic.get("comparator") or ""),
        semantic.get("threshold") if semantic.get("threshold") not in ("", None) else None,
        str(semantic.get("unit") or ""),
        str(semantic.get("time_window_start") or ""),
        str(semantic.get("time_window_end") or ""),
        str(semantic.get("jurisdiction") or ""),
        str(semantic.get("resolution_rule") or ""),
        str(semantic.get("resolution_source") or ""),
        str(semantic.get("outcome_space_id") or ""),
        str(semantic.get("semantic_type") or ""),
        semantic.get("confidence") if semantic.get("confidence") not in ("", None) else None,
        str(semantic.get("source") or ""),
        version_number,
        _json_text(payload),
        now,
        now,
    )
    if existing:
        conn.execute(
            """UPDATE graph_event_semantics
               SET subject = ?, predicate = ?, object = ?, comparator = ?, threshold = ?,
                   unit = ?, time_window_start = ?, time_window_end = ?, jurisdiction = ?,
                   resolution_rule = ?, resolution_source = ?, outcome_space_id = ?,
                   semantic_type = ?, confidence = ?, source = ?, current_version = ?,
                   payload_json = ?, updated_at_utc = ?
               WHERE event_id = ?""",
            (*values[1:18], values[19], event_id),
        )
    else:
        conn.execute(
            """INSERT INTO graph_event_semantics(
                event_id, subject, predicate, object, comparator, threshold, unit,
                time_window_start, time_window_end, jurisdiction, resolution_rule,
                resolution_source, outcome_space_id, semantic_type, confidence, source,
                current_version, payload_json, created_at_utc, updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )
    after = {**semantic, "event_id": event_id, "current_version": version_number, "payload": payload}
    _write_graph_event_semantic_version(
        conn,
        event_id=event_id,
        version_number=version_number,
        request=request,
        actor_type=actor_type,
        actor_id=actor_id,
        before=existing,
        after=after,
        patch_item=patch_item,
        now=now,
    )
    return after


def _event_after_from_item(
    item: Dict[str, Any],
    *,
    existing: Dict[str, Any] | None = None,
    request: Dict[str, Any],
    now: str,
) -> Dict[str, Any]:
    existing = dict(existing or {})
    payload = dict(existing.get("payload") or {})
    extra_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    payload.update(extra_payload)
    for key, value in item.items():
        if key not in {
            "action",
            "event_id",
            "title",
            "summary",
            "event_type",
            "lifecycle",
            "verification_status",
            "time_window_start",
            "time_window_end",
            "source_type",
            "payload",
            "semantic",
            "semantics",
            "semantic_type",
            "semantic_confidence",
            "semantic_source",
            *STRUCTURED_EVENT_FIELDS,
        }:
            payload.setdefault(key, value)

    title = str(item.get("title") or existing.get("title") or request.get("title") or "Untitled event").strip()
    event_id = str(item.get("event_id") or existing.get("event_id") or "").strip()
    if not event_id:
        event_id = _hash_id("g_evt", title or request.get("request_id") or now, 18)
    semantic = _event_semantic_from_item(item, existing=existing.get("semantic") if isinstance(existing.get("semantic"), dict) else {})
    return {
        "event_id": event_id,
        "title": title,
        "summary": str(item.get("summary") or existing.get("summary") or request.get("summary") or "").strip(),
        "event_type": str(item.get("event_type") or existing.get("event_type") or "ATOMIC").strip() or "ATOMIC",
        "lifecycle": str(item.get("lifecycle") or existing.get("lifecycle") or "ACTIVE").strip() or "ACTIVE",
        "verification_status": str(item.get("verification_status") or existing.get("verification_status") or "HUMAN_VERIFIED").strip() or "HUMAN_VERIFIED",
        "time_window_start": str(item.get("time_window_start") or existing.get("time_window_start") or "").strip(),
        "time_window_end": str(item.get("time_window_end") or existing.get("time_window_end") or "").strip(),
        "source_type": str(item.get("source_type") or existing.get("source_type") or "GRAPH_CORE").strip() or "GRAPH_CORE",
        "origin_request_id": str(existing.get("origin_request_id") or request.get("request_id") or "").strip(),
        "origin_run_id": str(existing.get("origin_run_id") or request.get("run_id") or "").strip(),
        "payload": payload,
        "semantic": semantic,
    }


def _write_graph_event_version(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    version_number: int,
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    before: Dict[str, Any],
    after: Dict[str, Any],
    patch_item: Dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        """INSERT INTO graph_event_versions(
            version_id, event_id, version_number, request_id, run_id, actor_type, actor_id,
            change_type, before_json, after_json, patch_item_json, created_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _new_id("gev"),
            event_id,
            int(version_number),
            request.get("request_id") or "",
            request.get("run_id") or "",
            actor_type,
            actor_id,
            request.get("change_type") or "",
            _json_text(before),
            _json_text(after),
            _json_text(patch_item),
            now,
        ),
    )


def _graph_finance_from_row(row: sqlite3.Row | None) -> Dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    item["payload"] = _parse_json(item.pop("payload_json", "{}"), {})
    return item


def _graph_edge_from_row(row: sqlite3.Row | None) -> Dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    item["payload"] = _parse_json(item.pop("payload_json", "{}"), {})
    return item


def _graph_expression_from_row(row: sqlite3.Row | None) -> Dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    item["expression"] = _parse_json(item.pop("expression_json", "{}"), {})
    item["payload"] = _parse_json(item.pop("payload_json", "{}"), {})
    return item


def _clamp_confidence(value: Any, default: float = 0.65) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return round(max(0.0, min(1.0, result)), 4)


def _node_type_from_item(item: Dict[str, Any], prefix: str, existing_value: str = "") -> str:
    explicit = str(item.get(f"{prefix}_type") or existing_value or "").strip().lower()
    if explicit in {"event", "finance", "signal", "node"}:
        return explicit
    if item.get(f"{prefix}_event_id"):
        return "event"
    if item.get(f"{prefix}_finance_id"):
        return "finance"
    return explicit or "node"


def _source_id_from_item(item: Dict[str, Any]) -> str:
    return str(item.get("source_id") or item.get("source_event_id") or item.get("source_finance_id") or "").strip()


def _target_id_from_item(item: Dict[str, Any]) -> str:
    return str(item.get("target_id") or item.get("target_event_id") or item.get("target_finance_id") or "").strip()


def _infer_relation_class(relation_type: str, relation_class: str = "") -> str:
    return infer_relation_class(relation_type, relation_class)


def _expression_value_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    expression = item.get("expression")
    if isinstance(expression, dict):
        return dict(expression)
    if isinstance(expression, list):
        return {"items": expression}
    expression_value = str(expression or item.get("formula") or item.get("condition") or "").strip()
    result: Dict[str, Any] = {}
    if expression_value:
        result["formula"] = expression_value
    inputs = item.get("inputs")
    if isinstance(inputs, list):
        result["inputs"] = inputs
    output = item.get("output")
    if isinstance(output, dict):
        result["output"] = output
    target_refs = item.get("target_refs")
    if isinstance(target_refs, list):
        result["target_refs"] = target_refs
    return result


def _node_exists(conn: sqlite3.Connection, node_id: str, node_type: str = "") -> bool:
    node_id = str(node_id or "").strip()
    node_type = str(node_type or "").strip().lower()
    if not node_id:
        return False
    if node_type == "event":
        return bool(conn.execute("SELECT 1 FROM graph_events WHERE event_id = ?", (node_id,)).fetchone())
    if node_type == "finance":
        return bool(conn.execute("SELECT 1 FROM graph_finance_nodes WHERE finance_id = ?", (node_id,)).fetchone())
    return bool(conn.execute("SELECT 1 FROM graph_events WHERE event_id = ?", (node_id,)).fetchone()) or bool(
        conn.execute("SELECT 1 FROM graph_finance_nodes WHERE finance_id = ?", (node_id,)).fetchone()
    )


def _finance_after_from_item(
    item: Dict[str, Any],
    *,
    existing: Dict[str, Any] | None = None,
    request: Dict[str, Any],
    now: str,
) -> Dict[str, Any]:
    existing = dict(existing or {})
    payload = dict(existing.get("payload") or {})
    extra_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    payload.update(extra_payload)
    for key, value in item.items():
        if key not in {
            "action",
            "finance_id",
            "target_id",
            "label",
            "name",
            "title",
            "summary",
            "finance_type",
            "venue",
            "symbol",
            "lifecycle",
            "verification_status",
            "source_type",
            "payload",
        }:
            payload.setdefault(key, value)

    label = str(item.get("label") or item.get("name") or item.get("title") or existing.get("label") or item.get("symbol") or request.get("title") or "Untitled finance node").strip()
    venue = str(item.get("venue") or existing.get("venue") or "").strip()
    symbol = str(item.get("symbol") or existing.get("symbol") or "").strip()
    finance_id = str(item.get("finance_id") or item.get("target_id") or existing.get("finance_id") or "").strip()
    if not finance_id:
        finance_id = _hash_id("g_fin", f"{venue}|{symbol}|{label}" or request.get("request_id") or now, 18)
    return {
        "finance_id": finance_id,
        "label": label,
        "summary": str(item.get("summary") or existing.get("summary") or request.get("summary") or "").strip(),
        "finance_type": str(item.get("finance_type") or existing.get("finance_type") or "VARIABLE").strip().upper() or "VARIABLE",
        "venue": venue,
        "symbol": symbol,
        "lifecycle": str(item.get("lifecycle") or existing.get("lifecycle") or "ACTIVE").strip().upper() or "ACTIVE",
        "verification_status": str(item.get("verification_status") or existing.get("verification_status") or "HUMAN_VERIFIED").strip() or "HUMAN_VERIFIED",
        "source_type": str(item.get("source_type") or existing.get("source_type") or "GRAPH_CORE").strip() or "GRAPH_CORE",
        "origin_request_id": str(existing.get("origin_request_id") or request.get("request_id") or "").strip(),
        "origin_run_id": str(existing.get("origin_run_id") or request.get("run_id") or "").strip(),
        "payload": payload,
    }


def _edge_after_from_item(
    item: Dict[str, Any],
    *,
    existing: Dict[str, Any] | None = None,
    request: Dict[str, Any],
    now: str,
) -> Dict[str, Any]:
    existing = dict(existing or {})
    payload = dict(existing.get("payload") or {})
    extra_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    payload.update(extra_payload)
    for key, value in item.items():
        if key not in {
            "action",
            "edge_id",
            "source_id",
            "source_event_id",
            "source_finance_id",
            "source_type",
            "target_id",
            "target_event_id",
            "target_finance_id",
            "target_type",
            "relation_class",
            "relation_type",
            "confidence",
            "strength",
            "mechanism",
            "reason",
            "evidence_summary",
            "lifecycle",
            "verification_status",
            "source_kind",
            "payload",
        }:
            payload.setdefault(key, value)

    source_id = _source_id_from_item(item) or str(existing.get("source_id") or "").strip()
    target_id = _target_id_from_item(item) or str(existing.get("target_id") or "").strip()
    relation_type = str(item.get("relation_type") or existing.get("relation_type") or "ASSOCIATED").strip().upper() or "ASSOCIATED"
    relation_class = _infer_relation_class(relation_type, str(item.get("relation_class") or existing.get("relation_class") or ""))
    edge_id = str(item.get("edge_id") or existing.get("edge_id") or "").strip()
    if not edge_id:
        edge_id = _hash_id("g_edge", f"{source_id}|{target_id}|{relation_type}", 18)
    return {
        "edge_id": edge_id,
        "source_id": source_id,
        "source_type": _node_type_from_item(item, "source", str(existing.get("source_type") or "")),
        "target_id": target_id,
        "target_type": _node_type_from_item(item, "target", str(existing.get("target_type") or "")),
        "relation_class": relation_class,
        "relation_type": relation_type,
        "confidence": _clamp_confidence(item.get("confidence", existing.get("confidence", 0.65))),
        "strength": str(item.get("strength") or existing.get("strength") or "MEDIUM").strip().upper() or "MEDIUM",
        "mechanism": str(item.get("mechanism") or existing.get("mechanism") or "").strip(),
        "reason": str(item.get("reason") or existing.get("reason") or request.get("reason") or "").strip(),
        "evidence_summary": str(item.get("evidence_summary") or existing.get("evidence_summary") or request.get("evidence_summary") or "").strip(),
        "lifecycle": str(item.get("lifecycle") or existing.get("lifecycle") or "ACTIVE").strip().upper() or "ACTIVE",
        "verification_status": str(item.get("verification_status") or existing.get("verification_status") or "HUMAN_VERIFIED").strip() or "HUMAN_VERIFIED",
        "source_kind": str(item.get("source_kind") or existing.get("source_kind") or "GRAPH_CORE").strip() or "GRAPH_CORE",
        "origin_request_id": str(existing.get("origin_request_id") or request.get("request_id") or "").strip(),
        "origin_run_id": str(existing.get("origin_run_id") or request.get("run_id") or "").strip(),
        "payload": payload,
    }


def _expression_after_from_item(
    item: Dict[str, Any],
    *,
    existing: Dict[str, Any] | None = None,
    request: Dict[str, Any],
    now: str,
) -> Dict[str, Any]:
    existing = dict(existing or {})
    payload = dict(existing.get("payload") or {})
    extra_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    payload.update(extra_payload)
    for key, value in item.items():
        if key not in {
            "action",
            "expression_id",
            "label",
            "name",
            "title",
            "summary",
            "expression_type",
            "language",
            "expression",
            "formula",
            "condition",
            "inputs",
            "output",
            "target_refs",
            "lifecycle",
            "verification_status",
            "source_type",
            "payload",
        }:
            payload.setdefault(key, value)

    expression = _expression_value_from_item(item) or dict(existing.get("expression") or {})
    label = str(item.get("label") or item.get("name") or item.get("title") or existing.get("label") or request.get("title") or "Untitled expression").strip()
    expression_id = str(item.get("expression_id") or existing.get("expression_id") or "").strip()
    if not expression_id:
        expression_id = _hash_id("g_expr", f"{label}|{_json_text(expression)}" or request.get("request_id") or now, 18)
    return {
        "expression_id": expression_id,
        "label": label,
        "summary": str(item.get("summary") or existing.get("summary") or request.get("summary") or "").strip(),
        "expression_type": str(item.get("expression_type") or existing.get("expression_type") or "RULE").strip().upper() or "RULE",
        "language": str(item.get("language") or existing.get("language") or "json").strip() or "json",
        "expression": expression,
        "lifecycle": str(item.get("lifecycle") or existing.get("lifecycle") or "ACTIVE").strip().upper() or "ACTIVE",
        "verification_status": str(item.get("verification_status") or existing.get("verification_status") or "HUMAN_VERIFIED").strip() or "HUMAN_VERIFIED",
        "source_type": str(item.get("source_type") or existing.get("source_type") or "GRAPH_CORE").strip() or "GRAPH_CORE",
        "origin_request_id": str(existing.get("origin_request_id") or request.get("request_id") or "").strip(),
        "origin_run_id": str(existing.get("origin_run_id") or request.get("run_id") or "").strip(),
        "payload": payload,
    }


def _write_graph_finance_version(
    conn: sqlite3.Connection,
    *,
    finance_id: str,
    version_number: int,
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    before: Dict[str, Any],
    after: Dict[str, Any],
    patch_item: Dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        """INSERT INTO graph_finance_versions(
            version_id, finance_id, version_number, request_id, run_id, actor_type, actor_id,
            change_type, before_json, after_json, patch_item_json, created_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _new_id("gfv"),
            finance_id,
            int(version_number),
            request.get("request_id") or "",
            request.get("run_id") or "",
            actor_type,
            actor_id,
            request.get("change_type") or "",
            _json_text(before),
            _json_text(after),
            _json_text(patch_item),
            now,
        ),
    )


def _write_graph_edge_version(
    conn: sqlite3.Connection,
    *,
    edge_id: str,
    version_number: int,
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    before: Dict[str, Any],
    after: Dict[str, Any],
    patch_item: Dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        """INSERT INTO graph_edge_versions(
            version_id, edge_id, version_number, request_id, run_id, actor_type, actor_id,
            change_type, before_json, after_json, patch_item_json, created_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _new_id("gedv"),
            edge_id,
            int(version_number),
            request.get("request_id") or "",
            request.get("run_id") or "",
            actor_type,
            actor_id,
            request.get("change_type") or "",
            _json_text(before),
            _json_text(after),
            _json_text(patch_item),
            now,
        ),
    )


def _write_graph_expression_version(
    conn: sqlite3.Connection,
    *,
    expression_id: str,
    version_number: int,
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    before: Dict[str, Any],
    after: Dict[str, Any],
    patch_item: Dict[str, Any],
    now: str,
) -> None:
    conn.execute(
        """INSERT INTO graph_expression_versions(
            version_id, expression_id, version_number, request_id, run_id, actor_type, actor_id,
            change_type, before_json, after_json, patch_item_json, created_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _new_id("gxv"),
            expression_id,
            int(version_number),
            request.get("request_id") or "",
            request.get("run_id") or "",
            actor_type,
            actor_id,
            request.get("change_type") or "",
            _json_text(before),
            _json_text(after),
            _json_text(patch_item),
            now,
        ),
    )


def _apply_graph_event_item(
    conn: sqlite3.Connection,
    *,
    item: Dict[str, Any],
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    now: str,
) -> Dict[str, Any]:
    action = str(item.get("action") or request.get("change_type") or "").strip().lower()
    event_id = str(item.get("event_id") or item.get("target_id") or "").strip()
    existing_row = conn.execute("SELECT * FROM graph_events WHERE event_id = ?", (event_id,)).fetchone() if event_id else None
    existing = _graph_event_from_row(existing_row)
    if existing:
        existing_semantics = _load_event_semantics(conn, [str(existing.get("event_id") or "")])
        existing["semantic"] = existing_semantics.get(str(existing.get("event_id") or ""), {})

    if action == "event_create" and existing:
        raise ValueError(f"event already exists: {event_id}")
    if action in {"event_update", "event_archive"} and not existing:
        raise ValueError(f"event not found for update: {event_id}")

    event_item = dict(item)
    if action == "event_archive":
        event_item["lifecycle"] = "ARCHIVED"
    after = _event_after_from_item(event_item, existing=existing, request=request, now=now)
    version_number = int(existing.get("current_version") or 0) + 1 if existing else 1

    if existing:
        conn.execute(
            """UPDATE graph_events
               SET title = ?, summary = ?, event_type = ?, lifecycle = ?,
                   verification_status = ?, time_window_start = ?, time_window_end = ?,
                   source_type = ?, current_version = ?, payload_json = ?,
                   updated_at_utc = ?
               WHERE event_id = ?""",
            (
                after["title"],
                after["summary"],
                after["event_type"],
                after["lifecycle"],
                after["verification_status"],
                after["time_window_start"],
                after["time_window_end"],
                after["source_type"],
                version_number,
                _json_text(after["payload"]),
                now,
                after["event_id"],
            ),
        )
    else:
        conn.execute(
            """INSERT INTO graph_events(
                event_id, title, summary, event_type, lifecycle, verification_status,
                time_window_start, time_window_end, source_type, origin_request_id,
                origin_run_id, current_version, payload_json, created_at_utc, updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                after["event_id"],
                after["title"],
                after["summary"],
                after["event_type"],
                after["lifecycle"],
                after["verification_status"],
                after["time_window_start"],
                after["time_window_end"],
                after["source_type"],
                request.get("request_id") or "",
                request.get("run_id") or "",
                version_number,
                _json_text(after["payload"]),
                now,
                now,
            ),
        )

    semantic_after = _upsert_graph_event_semantic(
        conn,
        event_id=after["event_id"],
        semantic=after.get("semantic") if isinstance(after.get("semantic"), dict) else {},
        request=request,
        actor_type=actor_type,
        actor_id=actor_id,
        patch_item=item,
        now=now,
    )
    if semantic_after:
        after["semantic"] = semantic_after

    _write_graph_event_version(
        conn,
        event_id=after["event_id"],
        version_number=version_number,
        request=request,
        actor_type=actor_type,
        actor_id=actor_id,
        before=existing,
        after=after,
        patch_item=item,
        now=now,
    )
    return {"event_id": after["event_id"], "version": version_number, "action": action}


def _apply_graph_finance_item(
    conn: sqlite3.Connection,
    *,
    item: Dict[str, Any],
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    now: str,
) -> Dict[str, Any]:
    action = str(item.get("action") or request.get("change_type") or "").strip().lower()
    finance_id = str(item.get("finance_id") or item.get("target_id") or "").strip()
    existing_row = conn.execute("SELECT * FROM graph_finance_nodes WHERE finance_id = ?", (finance_id,)).fetchone() if finance_id else None
    existing = _graph_finance_from_row(existing_row)

    if action == "finance_create" and existing:
        raise ValueError(f"finance node already exists: {finance_id}")
    if action in {"finance_update", "finance_archive"} and not existing:
        raise ValueError(f"finance node not found for update: {finance_id}")

    finance_item = dict(item)
    if action == "finance_archive":
        finance_item["lifecycle"] = "ARCHIVED"
    after = _finance_after_from_item(finance_item, existing=existing, request=request, now=now)
    version_number = int(existing.get("current_version") or 0) + 1 if existing else 1

    if existing:
        conn.execute(
            """UPDATE graph_finance_nodes
               SET label = ?, summary = ?, finance_type = ?, venue = ?, symbol = ?,
                   lifecycle = ?, verification_status = ?, source_type = ?,
                   current_version = ?, payload_json = ?, updated_at_utc = ?
               WHERE finance_id = ?""",
            (
                after["label"],
                after["summary"],
                after["finance_type"],
                after["venue"],
                after["symbol"],
                after["lifecycle"],
                after["verification_status"],
                after["source_type"],
                version_number,
                _json_text(after["payload"]),
                now,
                after["finance_id"],
            ),
        )
    else:
        conn.execute(
            """INSERT INTO graph_finance_nodes(
                finance_id, label, summary, finance_type, venue, symbol, lifecycle,
                verification_status, source_type, origin_request_id, origin_run_id,
                current_version, payload_json, created_at_utc, updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                after["finance_id"],
                after["label"],
                after["summary"],
                after["finance_type"],
                after["venue"],
                after["symbol"],
                after["lifecycle"],
                after["verification_status"],
                after["source_type"],
                request.get("request_id") or "",
                request.get("run_id") or "",
                version_number,
                _json_text(after["payload"]),
                now,
                now,
            ),
        )

    _write_graph_finance_version(
        conn,
        finance_id=after["finance_id"],
        version_number=version_number,
        request=request,
        actor_type=actor_type,
        actor_id=actor_id,
        before=existing,
        after=after,
        patch_item=item,
        now=now,
    )
    return {"finance_id": after["finance_id"], "version": version_number, "action": action}


def _apply_graph_edge_item(
    conn: sqlite3.Connection,
    *,
    item: Dict[str, Any],
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    now: str,
) -> Dict[str, Any]:
    action = str(item.get("action") or request.get("change_type") or "").strip().lower()
    edge_id = str(item.get("edge_id") or "").strip()
    if not edge_id:
        source_id = _source_id_from_item(item)
        target_id = _target_id_from_item(item)
        relation_type = str(item.get("relation_type") or "ASSOCIATED").strip().upper()
        edge_id = _hash_id("g_edge", f"{source_id}|{target_id}|{relation_type}", 18)

    existing_row = conn.execute("SELECT * FROM graph_edges WHERE edge_id = ?", (edge_id,)).fetchone()
    existing = _graph_edge_from_row(existing_row)

    if action in {"edge_create", "finance_mapping_create"} and existing:
        raise ValueError(f"edge already exists: {edge_id}")
    if action in {"edge_update", "edge_delete"} and not existing:
        raise ValueError(f"edge not found for update: {edge_id}")

    edge_item = dict(item)
    if action == "edge_delete":
        edge_item["lifecycle"] = "ARCHIVED"
    after = _edge_after_from_item(edge_item, existing=existing, request=request, now=now)
    if not after["source_id"] or not after["target_id"]:
        raise ValueError("edge requires source_id and target_id")
    if not _node_exists(conn, after["source_id"], after["source_type"]):
        raise ValueError(f"edge source node not found: {after['source_id']}")
    if not _node_exists(conn, after["target_id"], after["target_type"]):
        raise ValueError(f"edge target node not found: {after['target_id']}")

    version_number = int(existing.get("current_version") or 0) + 1 if existing else 1
    if existing:
        conn.execute(
            """UPDATE graph_edges
               SET source_id = ?, source_type = ?, target_id = ?, target_type = ?,
                   relation_class = ?, relation_type = ?, confidence = ?, strength = ?,
                   mechanism = ?, reason = ?, evidence_summary = ?, lifecycle = ?,
                   verification_status = ?, source_kind = ?, current_version = ?,
                   payload_json = ?, updated_at_utc = ?
               WHERE edge_id = ?""",
            (
                after["source_id"],
                after["source_type"],
                after["target_id"],
                after["target_type"],
                after["relation_class"],
                after["relation_type"],
                after["confidence"],
                after["strength"],
                after["mechanism"],
                after["reason"],
                after["evidence_summary"],
                after["lifecycle"],
                after["verification_status"],
                after["source_kind"],
                version_number,
                _json_text(after["payload"]),
                now,
                after["edge_id"],
            ),
        )
    else:
        conn.execute(
            """INSERT INTO graph_edges(
                edge_id, source_id, source_type, target_id, target_type, relation_class,
                relation_type, confidence, strength, mechanism, reason, evidence_summary,
                lifecycle, verification_status, source_kind, origin_request_id, origin_run_id,
                current_version, payload_json, created_at_utc, updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                after["edge_id"],
                after["source_id"],
                after["source_type"],
                after["target_id"],
                after["target_type"],
                after["relation_class"],
                after["relation_type"],
                after["confidence"],
                after["strength"],
                after["mechanism"],
                after["reason"],
                after["evidence_summary"],
                after["lifecycle"],
                after["verification_status"],
                after["source_kind"],
                request.get("request_id") or "",
                request.get("run_id") or "",
                version_number,
                _json_text(after["payload"]),
                now,
                now,
            ),
        )

    _write_graph_edge_version(
        conn,
        edge_id=after["edge_id"],
        version_number=version_number,
        request=request,
        actor_type=actor_type,
        actor_id=actor_id,
        before=existing,
        after=after,
        patch_item=item,
        now=now,
    )
    return {"edge_id": after["edge_id"], "version": version_number, "action": action}


def _apply_graph_expression_item(
    conn: sqlite3.Connection,
    *,
    item: Dict[str, Any],
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    now: str,
) -> Dict[str, Any]:
    action = str(item.get("action") or request.get("change_type") or "").strip().lower()
    expression_id = str(item.get("expression_id") or "").strip()
    existing_row = conn.execute("SELECT * FROM graph_expressions WHERE expression_id = ?", (expression_id,)).fetchone() if expression_id else None
    existing = _graph_expression_from_row(existing_row)

    if action == "expression_create" and existing:
        raise ValueError(f"expression already exists: {expression_id}")
    if action in {"expression_update", "expression_archive"} and not existing:
        raise ValueError(f"expression not found for update: {expression_id}")

    expression_item = dict(item)
    if action == "expression_archive":
        expression_item["lifecycle"] = "ARCHIVED"
    after = _expression_after_from_item(expression_item, existing=existing, request=request, now=now)
    version_number = int(existing.get("current_version") or 0) + 1 if existing else 1

    if existing:
        conn.execute(
            """UPDATE graph_expressions
               SET label = ?, summary = ?, expression_type = ?, language = ?,
                   expression_json = ?, lifecycle = ?, verification_status = ?,
                   source_type = ?, current_version = ?, payload_json = ?, updated_at_utc = ?
               WHERE expression_id = ?""",
            (
                after["label"],
                after["summary"],
                after["expression_type"],
                after["language"],
                _json_text(after["expression"]),
                after["lifecycle"],
                after["verification_status"],
                after["source_type"],
                version_number,
                _json_text(after["payload"]),
                now,
                after["expression_id"],
            ),
        )
    else:
        conn.execute(
            """INSERT INTO graph_expressions(
                expression_id, label, summary, expression_type, language, expression_json,
                lifecycle, verification_status, source_type, origin_request_id, origin_run_id,
                current_version, payload_json, created_at_utc, updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                after["expression_id"],
                after["label"],
                after["summary"],
                after["expression_type"],
                after["language"],
                _json_text(after["expression"]),
                after["lifecycle"],
                after["verification_status"],
                after["source_type"],
                request.get("request_id") or "",
                request.get("run_id") or "",
                version_number,
                _json_text(after["payload"]),
                now,
                now,
            ),
        )

    _write_graph_expression_version(
        conn,
        expression_id=after["expression_id"],
        version_number=version_number,
        request=request,
        actor_type=actor_type,
        actor_id=actor_id,
        before=existing,
        after=after,
        patch_item=item,
        now=now,
    )
    derived_edges: List[Dict[str, Any]] = []
    if after.get("lifecycle") == "ACTIVE":
        for edge_item in derived_edges_from_expression(after["expression_id"], after.get("expression") or {}):
            source_id = str(edge_item.get("source_id") or "").strip()
            target_id = str(edge_item.get("target_id") or "").strip()
            edge_id = str(edge_item.get("edge_id") or "").strip()
            if not _node_exists(conn, source_id, "event") or not _node_exists(conn, target_id, "event"):
                derived_edges.append({
                    "edge_id": edge_id,
                    "action": "skipped",
                    "reason": "derived edge endpoint event not found",
                    "source_id": source_id,
                    "target_id": target_id,
                })
                continue
            if edge_id and conn.execute("SELECT 1 FROM graph_edges WHERE edge_id = ?", (edge_id,)).fetchone():
                derived_edges.append({"edge_id": edge_id, "action": "skipped", "reason": "derived edge already exists"})
                continue
            try:
                derived_edges.append(
                    _apply_graph_edge_item(
                        conn,
                        item=edge_item,
                        request=request,
                        actor_type="system",
                        actor_id="event_expression_deriver",
                        now=now,
                    )
                )
            except Exception as exc:
                derived_edges.append({"edge_id": edge_id, "action": "skipped", "reason": str(exc)})
    return {"expression_id": after["expression_id"], "version": version_number, "action": action, "derived_edges": derived_edges}


def _event_merge_source_ids(item: Dict[str, Any], target_event_id: str) -> List[str]:
    values: List[Any] = []
    for key in ("source_event_ids", "merged_event_ids", "event_ids"):
        raw = item.get(key)
        if isinstance(raw, list):
            values.extend(raw)
    for key in ("source_event_id", "source_id"):
        if item.get(key):
            values.append(item.get(key))
    seen = set()
    source_ids: List[str] = []
    for value in values:
        event_id = str(value or "").strip()
        if not event_id or event_id == target_event_id or event_id in seen:
            continue
        seen.add(event_id)
        source_ids.append(event_id)
    return source_ids


def _update_graph_event_current(
    conn: sqlite3.Connection,
    *,
    after: Dict[str, Any],
    version_number: int,
    now: str,
) -> None:
    conn.execute(
        """UPDATE graph_events
           SET title = ?, summary = ?, event_type = ?, lifecycle = ?,
               verification_status = ?, time_window_start = ?, time_window_end = ?,
               source_type = ?, current_version = ?, payload_json = ?,
               updated_at_utc = ?
           WHERE event_id = ?""",
        (
            after["title"],
            after["summary"],
            after["event_type"],
            after["lifecycle"],
            after["verification_status"],
            after["time_window_start"],
            after["time_window_end"],
            after["source_type"],
            version_number,
            _json_text(after["payload"]),
            now,
            after["event_id"],
        ),
    )


def _update_graph_edge_current(
    conn: sqlite3.Connection,
    *,
    after: Dict[str, Any],
    version_number: int,
    now: str,
) -> None:
    conn.execute(
        """UPDATE graph_edges
           SET source_id = ?, source_type = ?, target_id = ?, target_type = ?,
               relation_class = ?, relation_type = ?, confidence = ?, strength = ?,
               mechanism = ?, reason = ?, evidence_summary = ?, lifecycle = ?,
               verification_status = ?, source_kind = ?, current_version = ?,
               payload_json = ?, updated_at_utc = ?
           WHERE edge_id = ?""",
        (
            after["source_id"],
            after["source_type"],
            after["target_id"],
            after["target_type"],
            after["relation_class"],
            after["relation_type"],
            after["confidence"],
            after["strength"],
            after["mechanism"],
            after["reason"],
            after["evidence_summary"],
            after["lifecycle"],
            after["verification_status"],
            after["source_kind"],
            version_number,
            _json_text(after["payload"]),
            now,
            after["edge_id"],
        ),
    )


def _apply_graph_event_merge_item(
    conn: sqlite3.Connection,
    *,
    item: Dict[str, Any],
    request: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    now: str,
) -> Dict[str, Any]:
    target_event_id = str(
        item.get("target_event_id")
        or item.get("canonical_event_id")
        or item.get("event_id")
        or item.get("target_id")
        or ""
    ).strip()
    if not target_event_id:
        raise ValueError("event_merge requires target_event_id")
    source_event_ids = _event_merge_source_ids(item, target_event_id)
    if not source_event_ids:
        raise ValueError("event_merge requires source_event_ids")

    target_row = conn.execute("SELECT * FROM graph_events WHERE event_id = ?", (target_event_id,)).fetchone()
    target_before = _graph_event_from_row(target_row)
    if not target_before:
        raise ValueError(f"merge target event not found: {target_event_id}")

    source_events: List[Dict[str, Any]] = []
    for source_id in source_event_ids:
        row = conn.execute("SELECT * FROM graph_events WHERE event_id = ?", (source_id,)).fetchone()
        source = _graph_event_from_row(row)
        if not source:
            raise ValueError(f"merge source event not found: {source_id}")
        source_events.append(source)

    target_payload = dict(target_before.get("payload") or {})
    merged_ids = list(dict.fromkeys([*(target_payload.get("merged_event_ids") or []), *source_event_ids]))
    target_payload["merged_event_ids"] = merged_ids
    target_payload.setdefault("merge_history", [])
    if isinstance(target_payload["merge_history"], list):
        target_payload["merge_history"] = [
            *target_payload["merge_history"][-19:],
            {
                "request_id": request.get("request_id") or "",
                "run_id": request.get("run_id") or "",
                "source_event_ids": source_event_ids,
                "merged_at_utc": now,
                "reason": item.get("reason") or request.get("reason") or "",
            },
        ]
    if isinstance(item.get("aliases"), list):
        target_payload["aliases"] = list(dict.fromkeys([*(target_payload.get("aliases") or []), *item.get("aliases")]))

    target_item = dict(item)
    target_item["action"] = "event_update"
    target_item["event_id"] = target_event_id
    target_item["payload"] = target_payload
    target_after = _event_after_from_item(target_item, existing=target_before, request=request, now=now)
    target_version = int(target_before.get("current_version") or 0) + 1
    _update_graph_event_current(conn, after=target_after, version_number=target_version, now=now)
    _write_graph_event_version(
        conn,
        event_id=target_after["event_id"],
        version_number=target_version,
        request=request,
        actor_type=actor_type,
        actor_id=actor_id,
        before=target_before,
        after=target_after,
        patch_item=item,
        now=now,
    )

    archived_sources: List[Dict[str, Any]] = []
    for source in source_events:
        source_payload = dict(source.get("payload") or {})
        source_payload["merged_into_event_id"] = target_event_id
        source_payload["merge_request_id"] = request.get("request_id") or ""
        source_item = {
            "action": "event_merge_archive_source",
            "event_id": source["event_id"],
            "lifecycle": "MERGED",
            "payload": source_payload,
        }
        source_after = _event_after_from_item(source_item, existing=source, request=request, now=now)
        source_version = int(source.get("current_version") or 0) + 1
        _update_graph_event_current(conn, after=source_after, version_number=source_version, now=now)
        _write_graph_event_version(
            conn,
            event_id=source_after["event_id"],
            version_number=source_version,
            request=request,
            actor_type=actor_type,
            actor_id=actor_id,
            before=source,
            after=source_after,
            patch_item=source_item,
            now=now,
        )
        archived_sources.append({"event_id": source_after["event_id"], "version": source_version, "lifecycle": source_after["lifecycle"]})

    rewire_edges = str(item.get("rewire_edges", "true")).strip().lower() not in {"0", "false", "no", "off"}
    rewired_edges: List[Dict[str, Any]] = []
    if rewire_edges:
        placeholders = ",".join("?" for _ in source_event_ids)
        edge_rows = conn.execute(
            f"""SELECT * FROM graph_edges
                WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})""",
            (*source_event_ids, *source_event_ids),
        ).fetchall()
        for row in edge_rows:
            before = _graph_edge_from_row(row)
            after = dict(before)
            after_payload = dict(before.get("payload") or {})
            after_payload["rewired_by_event_merge"] = {
                "request_id": request.get("request_id") or "",
                "target_event_id": target_event_id,
                "source_event_ids": source_event_ids,
            }
            after["payload"] = after_payload
            if after.get("source_id") in source_event_ids:
                after["source_id"] = target_event_id
                after["source_type"] = "event"
            if after.get("target_id") in source_event_ids:
                after["target_id"] = target_event_id
                after["target_type"] = "event"
            if after.get("source_id") == after.get("target_id"):
                after["lifecycle"] = "ARCHIVED"
                after["reason"] = str(after.get("reason") or "Archived after event merge produced a self-loop.").strip()
            edge_version = int(before.get("current_version") or 0) + 1
            _update_graph_edge_current(conn, after=after, version_number=edge_version, now=now)
            _write_graph_edge_version(
                conn,
                edge_id=after["edge_id"],
                version_number=edge_version,
                request=request,
                actor_type=actor_type,
                actor_id=actor_id,
                before=before,
                after=after,
                patch_item=item,
                now=now,
            )
            rewired_edges.append({"edge_id": after["edge_id"], "version": edge_version, "lifecycle": after.get("lifecycle")})

    return {
        "target_event_id": target_event_id,
        "target_version": target_version,
        "source_event_ids": source_event_ids,
        "archived_sources": archived_sources,
        "rewired_edges": rewired_edges,
        "action": "event_merge",
    }


def apply_change_request(
    request_id: str,
    *,
    actor_type: str = "human",
    actor_id: str = "",
) -> Dict[str, Any]:
    request = get_change_request(request_id)
    if request.get("status") not in {"APPROVED"}:
        raise ValueError("change request must be APPROVED before apply")
    validation = request.get("validation") if isinstance(request.get("validation"), dict) else {}
    if validation and validation.get("valid") is False:
        raise ValueError("cannot apply invalid change request")
    patch = request.get("patch") if isinstance(request.get("patch"), dict) else {}
    items = patch.get("items") if isinstance(patch.get("items"), list) else []
    if not items:
        raise ValueError("change request patch has no items")

    now = _now_iso()
    applied_events: List[Dict[str, Any]] = []
    applied_finance_nodes: List[Dict[str, Any]] = []
    applied_edges: List[Dict[str, Any]] = []
    applied_merges: List[Dict[str, Any]] = []
    applied_expressions: List[Dict[str, Any]] = []
    try:
        with _connect() as conn:
            for item in items:
                if not isinstance(item, dict):
                    continue
                action = str(item.get("action") or request.get("change_type") or "").strip().lower()
                apply_actor_type = str(actor_type or "human").strip() or "human"
                apply_actor_id = str(actor_id or "").strip()
                if action == "event_merge":
                    applied_merges.append(
                        _apply_graph_event_merge_item(
                            conn,
                            item=item,
                            request=request,
                            actor_type=apply_actor_type,
                            actor_id=apply_actor_id,
                            now=now,
                        )
                    )
                elif action in {"event_create", "event_update", "event_archive"}:
                    applied_events.append(
                        _apply_graph_event_item(
                            conn,
                            item=item,
                            request=request,
                            actor_type=apply_actor_type,
                            actor_id=apply_actor_id,
                            now=now,
                        )
                    )
                elif action in {"finance_create", "finance_update", "finance_archive"}:
                    applied_finance_nodes.append(
                        _apply_graph_finance_item(
                            conn,
                            item=item,
                            request=request,
                            actor_type=apply_actor_type,
                            actor_id=apply_actor_id,
                            now=now,
                        )
                    )
                elif action in {"edge_create", "edge_update", "edge_delete", "finance_mapping_create"}:
                    applied_edges.append(
                        _apply_graph_edge_item(
                            conn,
                            item=item,
                            request=request,
                            actor_type=apply_actor_type,
                            actor_id=apply_actor_id,
                            now=now,
                        )
                    )
                elif action in {"expression_create", "expression_update", "expression_archive"}:
                    applied_expressions.append(
                        _apply_graph_expression_item(
                            conn,
                            item=item,
                            request=request,
                            actor_type=apply_actor_type,
                            actor_id=apply_actor_id,
                            now=now,
                        )
                    )
                else:
                    raise ValueError(f"unsupported apply action: {action}")

            conn.execute(
                """UPDATE agent_event_change_requests
                   SET status = 'APPLIED', applied_at_utc = ?, applied_by_type = ?,
                       applied_by_id = ?, updated_at_utc = ?, apply_error_json = '{}'
                   WHERE request_id = ?""",
                (now, str(actor_type or "human").strip() or "human", str(actor_id or "").strip(), now, request_id),
            )
        return {
            "request_id": request_id,
            "status": "APPLIED",
            "applied_events": applied_events,
            "applied_finance_nodes": applied_finance_nodes,
            "applied_edges": applied_edges,
            "applied_merges": applied_merges,
            "applied_expressions": applied_expressions,
            "applied_at_utc": now,
        }
    except Exception as exc:
        with _connect() as conn:
            conn.execute(
                """UPDATE agent_event_change_requests
                   SET status = 'APPLY_FAILED', updated_at_utc = ?, apply_error_json = ?
                   WHERE request_id = ?""",
                (now, _json_text({"message": str(exc)}), request_id),
            )
        raise


def list_graph_events(*, q: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    query = str(q or "").strip()
    limit_num = _safe_int(limit, 50, 1, 300)
    with _connect() as conn:
        args: List[Any] = []
        where = ""
        if query:
            where = "WHERE event_id LIKE ? OR title LIKE ? OR summary LIKE ?"
            like = f"%{query}%"
            args.extend([like, like, like])
        rows = conn.execute(
            f"""SELECT * FROM graph_events
                {where}
                ORDER BY updated_at_utc DESC
                LIMIT ?""",
            (*args, limit_num),
        ).fetchall()
        events = [_graph_event_from_row(row) for row in rows]
        semantics = _load_event_semantics(conn, [str(event.get("event_id") or "") for event in events])
    for event in events:
        event["semantic"] = semantics.get(str(event.get("event_id") or ""), {})
    return events


def list_graph_finance_nodes(*, q: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    query = str(q or "").strip()
    limit_num = _safe_int(limit, 50, 1, 300)
    with _connect() as conn:
        args: List[Any] = []
        where = ""
        if query:
            where = "WHERE finance_id LIKE ? OR label LIKE ? OR summary LIKE ? OR symbol LIKE ? OR venue LIKE ?"
            like = f"%{query}%"
            args.extend([like, like, like, like, like])
        rows = conn.execute(
            f"""SELECT * FROM graph_finance_nodes
                {where}
                ORDER BY updated_at_utc DESC
                LIMIT ?""",
            (*args, limit_num),
        ).fetchall()
    return [_graph_finance_from_row(row) for row in rows]


def list_graph_edges(*, q: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    query = str(q or "").strip()
    limit_num = _safe_int(limit, 50, 1, 300)
    with _connect() as conn:
        args: List[Any] = []
        where = ""
        if query:
            where = """WHERE edge_id LIKE ? OR source_id LIKE ? OR target_id LIKE ?
                       OR relation_type LIKE ? OR relation_class LIKE ? OR reason LIKE ?
                       OR mechanism LIKE ? OR evidence_summary LIKE ?"""
            like = f"%{query}%"
            args.extend([like, like, like, like, like, like, like, like])
        rows = conn.execute(
            f"""SELECT * FROM graph_edges
                {where}
                ORDER BY updated_at_utc DESC
                LIMIT ?""",
            (*args, limit_num),
        ).fetchall()
    return [_graph_edge_from_row(row) for row in rows]


def list_graph_expressions(*, q: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    query = str(q or "").strip()
    limit_num = _safe_int(limit, 50, 1, 300)
    with _connect() as conn:
        args: List[Any] = []
        where = ""
        if query:
            where = """WHERE expression_id LIKE ? OR label LIKE ? OR summary LIKE ?
                       OR expression_type LIKE ? OR language LIKE ? OR expression_json LIKE ?"""
            like = f"%{query}%"
            args.extend([like, like, like, like, like, like])
        rows = conn.execute(
            f"""SELECT * FROM graph_expressions
                {where}
                ORDER BY updated_at_utc DESC
                LIMIT ?""",
            (*args, limit_num),
        ).fetchall()
    return [_graph_expression_from_row(row) for row in rows]


def _graph_version_from_row(row: sqlite3.Row, *, object_type: str, object_id: str) -> Dict[str, Any]:
    item = dict(row)
    item["object_type"] = object_type
    item["object_id"] = object_id
    item["before"] = _parse_json(item.pop("before_json", "{}"), {})
    item["after"] = _parse_json(item.pop("after_json", "{}"), {})
    item["patch_item"] = _parse_json(item.pop("patch_item_json", "{}"), {})
    return item


def list_graph_versions(*, object_type: str, object_id: str, limit: int = 30) -> Dict[str, Any]:
    object_key = str(object_type or "").strip().lower()
    object_id = str(object_id or "").strip()
    if not object_key:
        raise ValueError("object_type is required")
    if not object_id:
        raise ValueError("object_id is required")
    config = {
        "event": ("graph_event_versions", "event_id"),
        "finance": ("graph_finance_versions", "finance_id"),
        "finance_node": ("graph_finance_versions", "finance_id"),
        "edge": ("graph_edge_versions", "edge_id"),
        "expression": ("graph_expression_versions", "expression_id"),
    }.get(object_key)
    if not config:
        raise ValueError("object_type must be event, finance, edge, or expression")
    table, id_column = config
    limit_num = _safe_int(limit, 30, 1, 100)
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM {table}
                WHERE {id_column} = ?
                ORDER BY version_number DESC
                LIMIT ?""",
            (object_id, limit_num),
        ).fetchall()
    normalized_type = "finance" if object_key == "finance_node" else object_key
    versions = [_graph_version_from_row(row, object_type=normalized_type, object_id=object_id) for row in rows]
    return {
        "object_type": normalized_type,
        "object_id": object_id,
        "count": len(versions),
        "versions": versions,
    }


def list_graph_core(*, q: str = "", limit: int = 50) -> Dict[str, Any]:
    limit_num = _safe_int(limit, 50, 1, 300)
    events = list_graph_events(q=q, limit=limit_num)
    finance_nodes = list_graph_finance_nodes(q=q, limit=limit_num)
    edges = list_graph_edges(q=q, limit=limit_num)
    expressions = list_graph_expressions(q=q, limit=limit_num)
    return {
        "query": str(q or "").strip(),
        "events": events,
        "finance_nodes": finance_nodes,
        "edges": edges,
        "expressions": expressions,
        "summary": {
            "events": len(events),
            "finance_nodes": len(finance_nodes),
            "edges": len(edges),
            "expressions": len(expressions),
        },
    }


class EventNewsScheduler:
    def __init__(self, interval_seconds: int = DEFAULT_REFRESH_SECONDS) -> None:
        self.interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_started_at = ""
        self._last_finished_at = ""
        self._last_error = ""
        self._running = False

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.state()
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="event-news-refresh", daemon=True)
            self._thread.start()
            return self.state()

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        return self.state()

    def state(self) -> Dict[str, Any]:
        thread_alive = bool(self._thread and self._thread.is_alive())
        return {
            "enabled": thread_alive,
            "running": self._running,
            "interval_seconds": self.interval_seconds,
            "last_started_at": self._last_started_at,
            "last_finished_at": self._last_finished_at,
            "last_error": self._last_error,
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._running = True
                self._last_started_at = _now_iso()
                refresh_news(limit_per_source=DEFAULT_FEED_LIMIT)
                self._last_error = ""
            except Exception as exc:
                self._last_error = str(exc)[:500]
            finally:
                self._running = False
                self._last_finished_at = _now_iso()
            self._stop.wait(self.interval_seconds)


event_news_scheduler = EventNewsScheduler()
