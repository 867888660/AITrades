from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from services.config_loader import load_public_web_settings, load_web_settings, load_web_settings_for_ui, save_web_settings
from services import agent_interface_service as agent_service
from services.binance_market_service import search_binance_markets
from services.crypto_service import fetch_crypto_quotes
from services.event_graph_service import build_event_graph, get_event_graph_categories
from services.event_news_service import (
    deduplicate_derived_events,
    event_news_scheduler,
    get_status as get_event_news_status,
    list_events as list_news_events,
    list_observations as list_news_observations,
    refresh_news,
)
from services.finance_service import fetch_finance_quotes
from services.history_data_service import (
    add_watchlist_item as add_history_watchlist_item,
    create_backtest_batch as create_history_backtest_batch,
    create_backtest_case as create_history_backtest_case,
    create_backtest_collection as create_history_backtest_collection,
    create_backtest_run as create_history_backtest_run,
    delete_backtest_batch as delete_history_backtest_batch,
    delete_backtest_case as delete_history_backtest_case,
    delete_backtest_run as delete_history_backtest_run,
    delete_watchlist_item as delete_history_watchlist_item,
    download_binance_klines,
    download_binance_klines_range,
    download_polymarket_price_history,
    evaluate_backtest_case_payload,
    get_coverage as get_history_coverage,
    health_snapshot as get_history_health,
    get_backtest_batch as get_history_backtest_batch,
    get_backtest_run as get_history_backtest_run,
    import_backtest_run_to_workspace as import_history_backtest_run_to_workspace,
    list_backtest_batches as list_history_backtest_batches,
    list_backtest_cases as list_history_backtest_cases,
    list_backtest_collections as list_history_backtest_collections,
    list_backtest_runs as list_history_backtest_runs,
    list_watchlist as list_history_watchlist,
    preview_history,
    rename_backtest_batch as rename_history_backtest_batch,
    rename_backtest_case as rename_history_backtest_case,
    rename_backtest_run as rename_history_backtest_run,
    rerun_backtest_run as rerun_history_backtest_run,
)
from services.http_client import SESSION
from services.backtest_service import (
    create_strategy_backtest,
    get_strategy_backtest,
    get_strategy_backtest_results,
)
from services.polymarket_dictionary_service import get_dictionary_status, start_dictionary_refresh
from services.ledger_service import get_ledger_snapshot
from services.polymarket_service import (
    fetch_strategy_detail,
    fetch_strategy_monitoring,
    fetch_wallet_positions,
    get_overview,
    list_market_categories,
    resolve_market_selection,
    search_markets,
)
from services.realtime_collector import collector
from services.strategy_chart_delta_service import get_strategy_chart_delta
from services.strategy_chart_service import get_strategy_chart
from services.strategy_data_source import (
    read_strategy_state_bundle,
    reset_strategy_state_namespace,
    write_strategy_state_values,
)
from services.strategy_schema_service import get_strategy_code_schemas, strategy_state_payload
from services.strategy_event_service import list_strategy_events
from services.strategy_exit_service import force_flat_strategy
from services.strategy_registry_service import (
    create_strategy,
    delete_strategy,
    get_strategy as get_registry_strategy,
    list_strategies as list_registry_strategies,
    get_strategy_code_inputs,
    list_strategy_codes,
    update_strategy as update_registry_strategy,
    update_strategy_legs,
    update_strategy_mode,
    update_strategy_state,
)
from services.strategy_settings_service import update_strategy_settings
from services.strategy_workspace_service import get_strategy_usedata_draft, get_strategy_usedata_snapshot, get_strategy_workspace
from services.ws_market_sync_service import ws_market_sync
from services.workspace_preset_service import (
    delete_workspace_preset,
    get_workspace_preset,
    list_workspace_presets,
    save_workspace_preset,
)


app = Flask(__name__)


BASE_DIR = Path(__file__).resolve().parent
EXTERNAL_LATENCY_TARGETS = [
    {"key": "polymarket_web", "label": "Polymarket Web", "url": "https://polymarket.com", "group": "polymarket"},
    {"key": "polymarket_clob", "label": "Polymarket CLOB", "url": "https://clob.polymarket.com", "group": "polymarket"},
    {"key": "polymarket_gamma", "label": "Polymarket Gamma", "url": "https://gamma-api.polymarket.com/markets?limit=1", "group": "polymarket"},
    {"key": "polymarket_data", "label": "Polymarket Data API", "url": "https://data-api.polymarket.com", "group": "polymarket"},
    {"key": "binance", "label": "Binance", "url": "https://api.binance.com/api/v3/time", "group": "crypto"},
    {"key": "coingecko", "label": "CoinGecko", "url": "https://api.coingecko.com/api/v3/ping", "group": "crypto"},
    {"key": "finnhub", "label": "Finnhub", "url": "https://finnhub.io/api/v1/quote?symbol=AAPL", "group": "finance"},
]


def debug_timing(name: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                dt = (time.perf_counter() - t0) * 1000
                print(f"[BE][OK] {name} {request.method} {request.path} {dt:.1f}ms")
                return result
            except Exception as exc:
                dt = (time.perf_counter() - t0) * 1000
                print(f"[BE][ERR] {name} {request.method} {request.path} {dt:.1f}ms error={exc}")
                raise

        return wrapper

    return decorator


def _json_error(exc: Exception, status_code: int = 500):
    try:
        path = request.path or ""
        if path.startswith("/api/agent/") or path.startswith("/api/approvals/") or path.startswith("/api/event-graph/change-requests/"):
            payload = request.get_json(silent=True) if request.method not in {"GET", "HEAD"} else dict(request.args)
            if not isinstance(payload, dict):
                payload = {}
            payload.setdefault("actor_type", "agent" if path.startswith("/api/agent/") else "human")
            payload.setdefault("actor_id", "agent_strategy_assistant" if path.startswith("/api/agent/") else "local_user")
            payload.setdefault("_endpoint", path)
            payload.setdefault("_method", request.method)
            agent_service.record_request_error(
                path=path,
                method=request.method,
                status_code=status_code,
                error=str(exc),
                payload=payload,
            )
    except Exception:
        pass
    return jsonify({"ok": False, "error": str(exc)}), status_code


def _is_local_request() -> bool:
    remote = request.remote_addr or ""
    return remote in {"127.0.0.1", "::1", "localhost"}


def require_local_request(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_local_request():
            return jsonify({"ok": False, "error": "Settings are only available from this computer."}), 403
        return func(*args, **kwargs)

    return wrapper


def _latency_tone(latency_ms: int | None, ok: bool) -> str:
    if not ok or latency_ms is None:
        return "error"
    if latency_ms <= 500:
        return "good"
    return "warning"


def _check_http_latency(target: dict, timeout: float = 2.5) -> dict:
    started = time.perf_counter()
    url = str(target.get("url") or "")
    try:
        response = SESSION.get(url, timeout=timeout, allow_redirects=True)
        latency_ms = int((time.perf_counter() - started) * 1000)
        ok = response.status_code < 500
        return {
            **target,
            "ok": ok,
            "status": _latency_tone(latency_ms, ok),
            "latency_ms": latency_ms,
            "http_status": response.status_code,
            "error": None if ok else f"HTTP {response.status_code}",
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            **target,
            "ok": False,
            "status": "error",
            "latency_ms": latency_ms,
            "http_status": None,
            "error": str(exc),
        }


def _resolve_data_path(raw_path: str | None, fallback: str) -> Path:
    text = str(raw_path or fallback or "").strip()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _check_sqlite_latency(key: str, label: str, path: Path) -> dict:
    started = time.perf_counter()
    try:
        if not path.exists():
            raise FileNotFoundError(str(path))
        with sqlite3.connect(str(path), timeout=1.0) as conn:
            conn.execute("SELECT 1").fetchone()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "key": key,
            "label": label,
            "path": str(path),
            "ok": True,
            "status": _latency_tone(latency_ms, True),
            "latency_ms": latency_ms,
            "size_bytes": path.stat().st_size,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "key": key,
            "label": label,
            "path": str(path),
            "ok": False,
            "status": "error",
            "latency_ms": latency_ms,
            "error": str(exc),
        }


def _group_latency_status(items: list[dict]) -> dict:
    usable = [item for item in items if item.get("ok") and item.get("latency_ms") is not None]
    failed = [item for item in items if not item.get("ok")]
    if not usable:
        return {"ok": False, "status": "error", "latency_ms": None, "failed": len(failed), "count": len(items)}
    max_latency = max(int(item.get("latency_ms") or 0) for item in usable)
    status = "warning" if failed else _latency_tone(max_latency, True)
    return {
        "ok": not failed,
        "status": status,
        "latency_ms": max_latency,
        "failed": len(failed),
        "count": len(items),
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/settings")
@require_local_request
def settings_page():
    return render_template("settings.html")


@app.get("/watchlist")
def watchlist_page():
    return render_template("watchlist.html")


@app.get("/history")
def history_workspace_page():
    return render_template("history_workspace.html")


@app.get("/backtests/<int:run_id>")
def backtest_report_page(run_id: int):
    return render_template("backtest_report.html", run_id=run_id)


@app.get("/agent-monitor")
def agent_monitor_page():
    return render_template("agent_monitor.html")


@app.get("/event-graph")
def event_graph_page():
    return render_template("event_graph.html")


@app.get("/eventgraph")
def eventgraph_page_alias():
    return render_template("event_graph.html")


@app.get("/ledger")
def ledger_page():
    return render_template("ledger.html")


@app.get("/strategies/<int:row_id>/workspace")
def strategy_workspace_page(row_id: int):
    return render_template("strategy_workspace.html", row_id=row_id)


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "collector_running": collector.is_running(),
            "ws_market_sync": ws_market_sync.get_state(),
        }
    )


@app.get("/api/system/latency")
@debug_timing("system_latency")
def system_latency():
    try:
        settings = load_web_settings()
        sqlite_targets = [
            (
                "market_data_db",
                "行情 SQLite",
                _resolve_data_path(settings.get("sqlite_db_path"), "Data/market_data.db"),
            ),
            (
                "market_realtime_db",
                "实时市场 SQLite",
                _resolve_data_path(settings.get("market_realtime_db_path"), "Data/polymarket_realtime.db"),
            ),
            (
                "order_list_db",
                "订单 SQLite",
                _resolve_data_path(settings.get("order_list_db_path"), "Data/PolyMarketOrderList.db"),
            ),
            (
                "strategy_monitoring_db",
                "策略监控 SQLite",
                _resolve_data_path(settings.get("strategy_monitoring_db_path"), "Data/PolyMarketMonitoring.db"),
            ),
            (
                "polymarket_dictionary_db",
                "Polymarket Dictionary SQLite",
                _resolve_data_path(settings.get("polymarket_dictionary_db_path"), "Data/PolyMarketDictionary.db"),
            ),
        ]

        external: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(8, len(EXTERNAL_LATENCY_TARGETS))) as executor:
            futures = [executor.submit(_check_http_latency, target) for target in EXTERNAL_LATENCY_TARGETS]
            for future in as_completed(futures):
                external.append(future.result())
        external.sort(key=lambda item: item.get("key", ""))

        sqlite_items = [_check_sqlite_latency(key, label, path) for key, label, path in sqlite_targets]
        groups = {
            "polymarket": _group_latency_status([item for item in external if item.get("group") == "polymarket"]),
            "crypto": _group_latency_status([item for item in external if item.get("group") == "crypto"]),
            "finance": _group_latency_status([item for item in external if item.get("group") == "finance"]),
            "sqlite": _group_latency_status(sqlite_items),
        }
        return jsonify(
            {
                "ok": True,
                "data": {
                    "explanation": "latency 是服务器连接数据源并完成握手花费的时间，单位 ms；超时或失败表示当前不可用。",
                    "external": external,
                    "sqlite": sqlite_items,
                    "groups": groups,
                    "timeout_ms": 2500,
                },
            }
        )
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/settings")
@require_local_request
def get_settings():
    try:
        return jsonify({"ok": True, "data": load_web_settings_for_ui()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/settings")
@require_local_request
def update_settings():
    try:
        payload = request.get_json(silent=True) or {}
        save_web_settings(payload)
        return jsonify({"ok": True, "data": load_web_settings_for_ui()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/overview")
@debug_timing("overview")
def overview():
    wallet = request.args.get("wallet", "")
    try:
        data = get_overview(wallet or None)
        data["collector"] = collector.get_state()
        data["settings"] = load_public_web_settings()
        return jsonify(data)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/dictionary")
def polymarket_dictionary_status():
    try:
        return jsonify({"ok": True, "data": get_dictionary_status()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/dictionary/update")
def polymarket_dictionary_update():
    try:
        return jsonify({"ok": True, "data": start_dictionary_refresh()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/live/polymarket/dictionary")
def live_polymarket_dictionary():
    def _sse(event_name: str, payload) -> str:
        return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def event_stream():
        last_payload = ""
        deadline = time.time() + 3600
        while time.time() < deadline:
            try:
                payload = get_dictionary_status()
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if encoded != last_payload:
                    last_payload = encoded
                    yield _sse("state", payload)
            except GeneratorExit:
                return
            except Exception as exc:
                yield _sse("error", {"error": str(exc)})
            time.sleep(1)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/polymarket/market-categories")
def polymarket_market_categories():
    force_refresh = request.args.get("refresh", "0") == "1"
    limit = request.args.get("limit", "120")
    try:
        limit_num = max(0, min(int(limit), 500))
    except ValueError:
        limit_num = 120
    try:
        data = list_market_categories(force_refresh=force_refresh, limit=limit_num)
        return jsonify({"ok": True, "count": len(data), "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph")
@debug_timing("event_graph")
def event_graph_api():
    try:
        return jsonify(build_event_graph(dict(request.args)))
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph/categories")
@debug_timing("event_graph_categories")
def event_graph_categories_api():
    try:
        limit = request.args.get("limit", "120")
        try:
            limit_num = max(1, min(int(limit), 240))
        except ValueError:
            limit_num = 120
        return jsonify({"ok": True, "data": get_event_graph_categories(limit=limit_num)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph/news/status")
@debug_timing("event_graph_news_status")
def event_graph_news_status_api():
    try:
        return jsonify({"ok": True, "data": get_event_news_status()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/news/refresh")
@debug_timing("event_graph_news_refresh")
def event_graph_news_refresh_api():
    try:
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("q") or payload.get("query") or request.args.get("q", "") or "").strip()
        limit = payload.get("limit_per_source") or payload.get("limit") or request.args.get("limit", "24")
        return jsonify({"ok": True, "data": refresh_news(query=query, limit_per_source=limit)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/news/search")
@debug_timing("event_graph_news_search")
def event_graph_news_search_api():
    try:
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("q") or payload.get("query") or request.args.get("q", "") or "").strip()
        if not query:
            return _json_error(ValueError("q is required"), 400)
        limit = payload.get("limit_per_source") or payload.get("limit") or request.args.get("limit", "30")
        return jsonify({"ok": True, "data": refresh_news(query=query, limit_per_source=limit)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph/events")
@debug_timing("event_graph_events")
def event_graph_events_api():
    try:
        query = str(request.args.get("q", "") or "").strip()
        limit = request.args.get("limit", "80")
        include_observations = str(request.args.get("include_observations", "1")).lower() not in {"0", "false", "no"}
        return jsonify({"ok": True, "data": list_news_events(q=query, limit=limit, include_observations=include_observations)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph/observations")
@debug_timing("event_graph_observations")
def event_graph_observations_api():
    try:
        event_id = str(request.args.get("event_id", "") or "").strip()
        query = str(request.args.get("q", "") or "").strip()
        limit = request.args.get("limit", "120")
        return jsonify({"ok": True, "data": list_news_observations(event_id=event_id, q=query, limit=limit)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/news/deduplicate")
@debug_timing("event_graph_news_deduplicate")
def event_graph_news_deduplicate_api():
    try:
        payload = request.get_json(silent=True) or {}
        dry_raw = payload.get("dry_run", request.args.get("dry_run", "0"))
        dry_run = str(dry_raw).strip().lower() in {"1", "true", "yes", "on"}
        limit = payload.get("limit") or request.args.get("limit", "500")
        return jsonify({"ok": True, "data": deduplicate_derived_events(dry_run=dry_run, limit=limit)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/markets")
def polymarket_markets():
    query = request.args.get("q", "")
    category_values = [value for value in request.args.getlist("category") if str(value or "").strip()]
    category = ",".join(category_values) if category_values else request.args.get("category", "")
    sort_by = request.args.get("sort", request.args.get("sort_by", ""))
    sort_dir = request.args.get("order", request.args.get("sort_dir", "desc"))
    force_refresh = request.args.get("refresh", "0") == "1"
    limit = request.args.get("limit", "60")
    price_filters = {
        "yes_ask": (
            request.args.get("yes_ask_min", request.args.get("ask_min")),
            request.args.get("yes_ask_max", request.args.get("ask_max")),
        ),
        "yes_bid": (
            request.args.get("yes_bid_min", request.args.get("bid_min")),
            request.args.get("yes_bid_max", request.args.get("bid_max")),
        ),
        "no_ask": (request.args.get("no_ask_min"), request.args.get("no_ask_max")),
        "no_bid": (request.args.get("no_bid_min"), request.args.get("no_bid_max")),
    }
    try:
        limit_num = max(1, min(int(limit), 200))
    except ValueError:
        limit_num = 60
    try:
        data = search_markets(
            query=query,
            category=category,
            limit=limit_num,
            force_refresh=force_refresh,
            sort_by=sort_by,
            sort_dir=sort_dir,
            price_filters=price_filters,
        )
        return jsonify({"ok": True, "count": len(data), "sort": sort_by, "order": sort_dir, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/markets/resolve")
def polymarket_market_resolve():
    query = request.args.get("q", "")
    condition_id = request.args.get("condition_id", "")
    token_id = request.args.get("token_id", "")
    force_refresh = request.args.get("refresh", "0") == "1"
    limit = request.args.get("limit", "20")
    try:
        limit_num = max(1, min(int(limit), 100))
    except ValueError:
        limit_num = 20
    try:
        data = resolve_market_selection(
            query=query,
            condition_id=condition_id,
            token_id=token_id,
            limit=limit_num,
            force_refresh=force_refresh,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/binance/markets/search")
def binance_markets_search():
    try:
        data = search_binance_markets(dict(request.args))
        return jsonify(data)
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/health")
@debug_timing("history_health")
def history_health():
    try:
        return jsonify(get_history_health())
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/search")
@debug_timing("history_search")
def history_search():
    source = str(request.args.get("source") or "polymarket").strip().lower()
    limit = request.args.get("limit", "40")
    try:
        limit_num = max(1, min(int(limit), 100))
    except ValueError:
        limit_num = 40
    try:
        if source == "binance":
            data = search_binance_markets(
                {
                    "category": "crypto_spot",
                    "q": request.args.get("q", ""),
                    "quote": request.args.get("quote", "USDT"),
                    "limit": limit_num,
                    "refresh": request.args.get("refresh", "0"),
                }
            )
            rows = data.get("data") if isinstance(data, dict) else []
            for row in rows or []:
                row["history_coverage"] = get_history_coverage(
                    "binance",
                    symbol=row.get("symbol"),
                    interval=request.args.get("interval", "1m"),
                )
            return jsonify({"ok": True, "source": source, "count": len(rows or []), "data": rows or [], "meta": data.get("meta", {})})
        if source == "polymarket":
            rows = search_markets(
                query=request.args.get("q", ""),
                category=request.args.get("category", ""),
                limit=limit_num,
                force_refresh=request.args.get("refresh", "0") == "1",
                sort_by=request.args.get("sort", "volume24h"),
                sort_dir=request.args.get("order", "desc"),
            )
            for row in rows:
                token_id = str(row.get("yes_token") or row.get("token") or "").strip()
                row["history_coverage"] = get_history_coverage(
                    "polymarket",
                    condition_id=row.get("condition_id"),
                    token_id=token_id,
                )
            return jsonify({"ok": True, "source": source, "count": len(rows), "data": rows})
        return _json_error(ValueError("source must be binance or polymarket"), 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/watchlist")
@debug_timing("history_watchlist")
def history_watchlist():
    try:
        source = str(request.args.get("source") or "").strip().lower()
        return jsonify({"ok": True, "data": list_history_watchlist(source)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/watchlist")
@debug_timing("history_watchlist_add")
def history_watchlist_add():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": add_history_watchlist_item(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/history/watchlist/<int:item_id>")
@debug_timing("history_watchlist_delete")
def history_watchlist_delete(item_id: int):
    try:
        return jsonify({"ok": True, "deleted": delete_history_watchlist_item(item_id)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-cases")
@debug_timing("history_backtest_cases")
def history_backtest_cases():
    try:
        return jsonify({"ok": True, "data": list_history_backtest_cases()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-collections")
@debug_timing("history_backtest_collections")
def history_backtest_collections():
    try:
        return jsonify({"ok": True, "data": list_history_backtest_collections()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-collections")
@debug_timing("history_backtest_collection_create")
def history_backtest_collection_create():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": create_history_backtest_collection(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-cases")
@debug_timing("history_backtest_case_create")
def history_backtest_case_create():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": create_history_backtest_case(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/history/backtest-cases/<int:case_id>")
@debug_timing("history_backtest_case_delete")
def history_backtest_case_delete(case_id: int):
    try:
        return jsonify({"ok": True, "deleted": delete_history_backtest_case(case_id)})
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/history/backtest-cases/<int:case_id>")
@debug_timing("history_backtest_case_rename")
def history_backtest_case_rename(case_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": rename_history_backtest_case(case_id, payload.get("name") or payload.get("case_name") or "")})
    except ValueError as exc:
        return _json_error(exc, 400 if "required" in str(exc).lower() else 404)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-cases/evaluate")
@debug_timing("history_backtest_case_evaluate")
def history_backtest_case_evaluate():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(evaluate_backtest_case_payload(payload))
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-batches")
@debug_timing("history_backtest_batches")
def history_backtest_batches():
    try:
        limit = int(request.args.get("limit") or 50)
        return jsonify({"ok": True, "data": list_history_backtest_batches(limit=limit)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-batches")
@debug_timing("history_backtest_batch_create")
def history_backtest_batch_create():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": create_history_backtest_batch(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-batches/<batch_id>")
@debug_timing("history_backtest_batch")
def history_backtest_batch(batch_id: str):
    try:
        include_runs = str(request.args.get("include_runs", "1")).lower() not in {"0", "false", "no"}
        data = get_history_backtest_batch(batch_id, include_runs=include_runs)
        if not data:
            return _json_error(ValueError("backtest batch not found"), 404)
        return jsonify({"ok": True, "data": data})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/history/backtest-batches/<batch_id>")
@debug_timing("history_backtest_batch_delete")
def history_backtest_batch_delete(batch_id: str):
    try:
        result = delete_history_backtest_batch(batch_id)
        if not result.get("deleted_runs"):
            return _json_error(ValueError("backtest batch not found"), 404)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/history/backtest-batches/<batch_id>")
@debug_timing("history_backtest_batch_rename")
def history_backtest_batch_rename(batch_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": rename_history_backtest_batch(batch_id, payload.get("name") or payload.get("batch_name") or "")})
    except ValueError as exc:
        return _json_error(exc, 400 if "required" in str(exc).lower() else 404)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-runs")
@debug_timing("history_backtest_runs")
def history_backtest_runs():
    try:
        case_id = request.args.get("case_id")
        batch_id = request.args.get("batch_id") or ""
        return jsonify({
            "ok": True,
            "data": list_history_backtest_runs(int(case_id), batch_id=batch_id) if case_id else list_history_backtest_runs(batch_id=batch_id),
        })
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/history/backtest-runs/<int:run_id>")
@debug_timing("history_backtest_run_delete")
def history_backtest_run_delete(run_id: int):
    try:
        deleted = delete_history_backtest_run(run_id)
        if not deleted:
            return _json_error(ValueError("backtest run not found"), 404)
        return jsonify({"ok": True, "data": {"run_id": run_id, "deleted": True}})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-runs/<int:run_id>/workspace")
@debug_timing("history_backtest_run_workspace_import")
def history_backtest_run_workspace_import(run_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": import_history_backtest_run_to_workspace(run_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/history/backtest-runs/<int:run_id>")
@debug_timing("history_backtest_run_rename")
def history_backtest_run_rename(run_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": rename_history_backtest_run(run_id, payload.get("name") or payload.get("run_name") or "")})
    except ValueError as exc:
        return _json_error(exc, 400 if "required" in str(exc).lower() else 404)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-cases/<int:case_id>/runs")
@debug_timing("history_backtest_run_create")
def history_backtest_run_create(case_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": create_history_backtest_run(case_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-runs/<int:run_id>")
@debug_timing("history_backtest_run")
def history_backtest_run(run_id: int):
    try:
        equity_limit = int(request.args.get("equity_limit") or 5000)
        orders_limit = int(request.args.get("orders_limit") or 3000)
        events_limit = int(request.args.get("events_limit") or 500)
        data = get_history_backtest_run(
            run_id,
            equity_limit=equity_limit,
            orders_limit=orders_limit,
            events_limit=events_limit,
        )
        if not data:
            return _json_error(ValueError("backtest run not found"), 404)
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-runs/<int:run_id>/rerun")
@debug_timing("history_backtest_run_rerun")
def history_backtest_run_rerun(run_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": rerun_history_backtest_run(run_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/coverage")
@debug_timing("history_coverage")
def history_coverage():
    try:
        return jsonify(
            {
                "ok": True,
                "data": get_history_coverage(
                    request.args.get("source", ""),
                    symbol=request.args.get("symbol", ""),
                    interval=request.args.get("interval", "1m"),
                    condition_id=request.args.get("condition_id", ""),
                    token_id=request.args.get("token_id", ""),
                ),
            }
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/binance/download")
@debug_timing("history_binance_download")
def history_binance_download():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(download_binance_klines_range(payload))
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/polymarket/download")
@debug_timing("history_polymarket_download")
def history_polymarket_download():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(download_polymarket_price_history(payload))
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/preview")
@debug_timing("history_preview")
def history_preview():
    try:
        return jsonify(
            preview_history(
                request.args.get("source", ""),
                symbol=request.args.get("symbol", ""),
                interval=request.args.get("interval", "1m"),
                token_id=request.args.get("token_id", ""),
                limit=request.args.get("limit", "240"),
            )
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/holdings")
def polymarket_holdings():
    wallet = request.args.get("wallet", "")
    try:
        return jsonify(fetch_wallet_positions(wallet or None))
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/ledger")
@debug_timing("ledger")
def ledger_snapshot():
    try:
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify(get_ledger_snapshot(limit=limit_num))
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/capabilities")
@debug_timing("agent_capabilities")
def agent_capabilities():
    try:
        return jsonify({"ok": True, "data": agent_service.get_capabilities()})
    except Exception as exc:
        return _json_error(exc)


def _agent_query_payload() -> dict:
    payload = dict(request.args)
    categories = [value for value in request.args.getlist("category") if str(value or "").strip()]
    if categories:
        payload["categories"] = categories
    payload.setdefault("actor_type", "agent")
    payload.setdefault("actor_id", "agent_strategy_assistant")
    payload.setdefault("_endpoint", request.path)
    payload.setdefault("_method", request.method)
    return payload


def _agent_body_payload(default_type: str = "agent", default_id: str = "agent_strategy_assistant") -> dict:
    payload = request.get_json(silent=True) or {}
    payload.setdefault("actor_type", default_type)
    payload.setdefault("actor_id", default_id)
    payload.setdefault("_endpoint", request.path)
    payload.setdefault("_method", request.method)
    return payload


@app.get("/api/agent/market-categories")
@debug_timing("agent_market_categories")
def agent_market_categories():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_market_categories(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/markets")
@debug_timing("agent_market_search")
def agent_market_search():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_search_markets(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/markets/resolve")
@debug_timing("agent_market_resolve")
def agent_market_resolve():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_resolve_market(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/market-scan")
@debug_timing("agent_market_scan")
def agent_market_scan():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_hot_market_scan(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/market-scan/propose-strategies")
@debug_timing("agent_market_scan_propose")
def agent_market_scan_propose():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.propose_strategies_from_market_scan(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph")
@debug_timing("agent_event_graph")
def agent_event_graph_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_event_graph(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/news/status")
@debug_timing("agent_event_news_status")
def agent_event_news_status_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_event_news_status(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/events")
@debug_timing("agent_event_graph_events")
def agent_event_graph_events_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_event_graph_events(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/observations")
@debug_timing("agent_event_graph_observations")
def agent_event_graph_observations_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_event_graph_observations(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/event-graph/news/refresh")
@debug_timing("agent_event_news_refresh")
def agent_event_news_refresh_api():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_refresh_event_news(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/event-graph/news/search")
@debug_timing("agent_event_news_search")
def agent_event_news_search_api():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_search_event_news(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/agent/event-graph/patches/validate")
@debug_timing("agent_event_graph_patch_validate")
def agent_event_graph_patch_validate_api():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_validate_event_graph_patch(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/event-graph/change-requests")
@debug_timing("agent_event_graph_change_request")
def agent_event_graph_change_request_api():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_submit_change_request(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/change-requests")
@debug_timing("agent_event_graph_change_requests_list")
def agent_event_graph_change_requests_list_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_change_requests(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/change-requests/<request_id>")
@debug_timing("agent_event_graph_change_request_detail")
def agent_event_graph_change_request_detail_api(request_id: str):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_change_request(request_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/events")
@debug_timing("agent_event_graph_core_events")
def agent_event_graph_core_events_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_events(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core")
@debug_timing("agent_event_graph_core")
def agent_event_graph_core_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/finance")
@debug_timing("agent_event_graph_core_finance")
def agent_event_graph_core_finance_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_finance_nodes(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/edges")
@debug_timing("agent_event_graph_core_edges")
def agent_event_graph_core_edges_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_edges(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/expressions")
@debug_timing("agent_event_graph_core_expressions")
def agent_event_graph_core_expressions_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_expressions(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/versions")
@debug_timing("agent_event_graph_core_versions")
def agent_event_graph_core_versions_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_versions(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/approve")
@debug_timing("event_graph_change_request_approve")
def event_graph_change_request_approve_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify(
            {
                "ok": True,
                "data": agent_service.human_review_event_graph_change_request(
                    request_id,
                    payload,
                    decision="approve",
                ),
            }
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/approve-and-apply")
@debug_timing("event_graph_change_request_approve_and_apply")
def event_graph_change_request_approve_and_apply_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.human_approve_and_apply_event_graph_change_request(request_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/reject")
@debug_timing("event_graph_change_request_reject")
def event_graph_change_request_reject_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify(
            {
                "ok": True,
                "data": agent_service.human_review_event_graph_change_request(
                    request_id,
                    payload,
                    decision="reject",
                ),
            }
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/request-changes")
@debug_timing("event_graph_change_request_needs_changes")
def event_graph_change_request_needs_changes_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify(
            {
                "ok": True,
                "data": agent_service.human_review_event_graph_change_request(
                    request_id,
                    payload,
                    decision="request_changes",
                ),
            }
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/apply")
@debug_timing("event_graph_change_request_apply")
def event_graph_change_request_apply_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.human_apply_event_graph_change_request(request_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/dashboard")
@debug_timing("agent_dashboard")
def agent_dashboard():
    try:
        limit = request.args.get("limit", "20")
        try:
            limit_num = max(1, min(int(limit), 100))
        except ValueError:
            limit_num = 20
        return jsonify({"ok": True, "data": agent_service.dashboard(limit=limit_num)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/activity")
@debug_timing("agent_activity_list")
def agent_activity_list():
    try:
        limit = request.args.get("limit", "50")
        try:
            limit_num = max(1, min(int(limit), 200))
        except ValueError:
            limit_num = 50
        return jsonify({"ok": True, "data": agent_service.list_activity(limit=limit_num)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/activity")
@debug_timing("agent_activity_create")
def agent_activity_create():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.create_activity(payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategy-drafts")
@debug_timing("agent_drafts_list")
def agent_strategy_drafts_list():
    try:
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify({"ok": True, "data": agent_service.list_drafts(limit=limit_num, payload=_agent_query_payload())})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/strategy-drafts")
@debug_timing("agent_drafts_create")
def agent_strategy_drafts_create():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.create_draft(payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategy-drafts/<draft_id>")
@debug_timing("agent_drafts_get")
def agent_strategy_drafts_get(draft_id: str):
    try:
        result = agent_service.get_draft(draft_id, _agent_query_payload())
        if not result:
            return jsonify({"ok": False, "error": "draft not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/agent/strategy-drafts/<draft_id>")
@debug_timing("agent_drafts_update")
def agent_strategy_drafts_update(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.update_draft(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/agent/strategy-drafts/<draft_id>")
@debug_timing("agent_drafts_delete")
def agent_strategy_drafts_delete(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.delete_draft(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/strategy-drafts/<draft_id>/risk-check")
@debug_timing("agent_drafts_risk")
def agent_strategy_drafts_risk(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.risk_check(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/strategy-drafts/<draft_id>/simulate")
@debug_timing("agent_drafts_simulate")
def agent_strategy_drafts_simulate(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.simulate_draft(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/strategy-drafts/<draft_id>/submit")
@debug_timing("agent_drafts_submit")
def agent_strategy_drafts_submit(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.submit_draft(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/approvals")
@debug_timing("agent_approvals_list")
def agent_approvals_list():
    try:
        status = request.args.get("status", "")
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify({"ok": True, "data": agent_service.list_approvals(status=status, limit=limit_num, payload=_agent_query_payload())})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/approvals/<approval_id>")
@debug_timing("agent_approvals_get")
def agent_approvals_get(approval_id: str):
    try:
        result = agent_service.get_approval(approval_id, _agent_query_payload())
        if not result:
            return jsonify({"ok": False, "error": "approval not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/agent/approvals/<approval_id>/draft")
@debug_timing("agent_approvals_update_draft")
def agent_approvals_update_draft(approval_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.update_approval_draft(approval_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/approvals/<approval_id>/approve")
@debug_timing("agent_approvals_approve")
def agent_approvals_approve(approval_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.approve_approval(approval_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/approvals/<approval_id>/reject")
@debug_timing("agent_approvals_reject")
def agent_approvals_reject(approval_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.reject_approval(approval_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/approvals/<approval_id>/request-changes")
@debug_timing("agent_approvals_changes")
def agent_approvals_changes(approval_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.request_changes(approval_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/audit")
@debug_timing("agent_audit")
def agent_audit_list():
    try:
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify({"ok": True, "data": agent_service.list_audit(limit=limit_num, payload=_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/runs")
@debug_timing("agent_runs")
def agent_runs_list():
    try:
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify({"ok": True, "data": agent_service.list_runs(limit=limit_num, payload=_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/runs/<run_id>/steps")
@debug_timing("agent_run_steps")
def agent_run_steps_list(run_id: str):
    try:
        limit = request.args.get("limit", "200")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 200
        return jsonify({"ok": True, "data": agent_service.list_run_steps(run_id, limit=limit_num, payload=_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/agent/audit")
@debug_timing("agent_audit_clear")
def agent_audit_clear():
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.clear_audit(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/cases")
@debug_timing("agent_backtest_cases")
def agent_backtest_cases():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_backtest_cases(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/backtests/cases")
@debug_timing("agent_backtest_case_create")
def agent_backtest_case_create():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_create_backtest_case(payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/runs")
@debug_timing("agent_backtest_runs")
def agent_backtest_runs():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_backtest_runs(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/backtests/cases/<int:case_id>/runs")
@debug_timing("agent_backtest_run_create")
def agent_backtest_run_create(case_id: int):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_create_backtest_run(case_id, payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/runs/<int:run_id>")
@debug_timing("agent_backtest_run")
def agent_backtest_run(run_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_backtest_run(run_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 404 if "not found" in str(exc).lower() else 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/batches")
@debug_timing("agent_backtest_batches")
def agent_backtest_batches():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_backtest_batches(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/backtests/batches")
@debug_timing("agent_backtest_batch_create")
def agent_backtest_batch_create():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_create_backtest_batch(payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/batches/<batch_id>")
@debug_timing("agent_backtest_batch")
def agent_backtest_batch(batch_id: str):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_backtest_batch(batch_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 404 if "not found" in str(exc).lower() else 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies")
@debug_timing("agent_strategies_list")
def agent_strategies_list():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_strategies(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>")
@debug_timing("agent_strategy_detail")
def agent_strategy_detail(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>/workspace")
@debug_timing("agent_strategy_workspace")
def agent_strategy_workspace(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy_workspace(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>/usedata")
@debug_timing("agent_strategy_usedata")
def agent_strategy_usedata(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy_usedata(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>/events")
@debug_timing("agent_strategy_events")
def agent_strategy_events(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy_events(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>/state")
@debug_timing("agent_strategy_state")
def agent_strategy_state(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy_state(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies")
@debug_timing("strategies")
def polymarket_strategies():
    try:
        limit = request.args.get("limit", "30")
        sync_stats = request.args.get("sync_stats", "0") == "1"
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 30
        return jsonify(fetch_strategy_monitoring(limit=limit_num, sync_stats=sync_stats))
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>")
@debug_timing("strategy_detail")
def polymarket_strategy_detail(row_id: int):
    try:
        return jsonify({"ok": True, "data": fetch_strategy_detail(row_id)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/strategies/<int:row_id>")
def polymarket_strategy_update(row_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": update_strategy_settings(row_id, payload)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/workspace-presets")
def polymarket_workspace_presets():
    row_id = request.args.get("row_id")
    try:
        row_id_num = int(row_id) if row_id else None
    except ValueError:
        row_id_num = None
    try:
        return jsonify({"ok": True, "data": list_workspace_presets(row_id_num)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/workspace-presets")
def polymarket_workspace_presets_save():
    payload = request.get_json(silent=True) or {}
    row_id = payload.get("strategy_row_id")
    try:
        row_id_num = int(row_id) if row_id not in (None, "") else None
    except (TypeError, ValueError):
        row_id_num = None
    try:
        return jsonify({"ok": True, "data": save_workspace_preset(row_id_num, payload)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/workspace-presets/<int:preset_id>")
def polymarket_workspace_preset_detail(preset_id: int):
    try:
        return jsonify({"ok": True, "data": get_workspace_preset(preset_id)})
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/polymarket/workspace-presets/<int:preset_id>")
def polymarket_workspace_preset_delete(preset_id: int):
    try:
        return jsonify({"ok": True, "data": delete_workspace_preset(preset_id)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/workspace")
@debug_timing("workspace")
def polymarket_strategy_workspace(row_id: int):
    try:
        include_events = request.args.get("include_events", "0") == "1"
        backtest_run_id = request.args.get("backtest_run_id") or request.args.get("run_id")
        if request.args.get("source") != "backtest":
            backtest_run_id = None
        return jsonify({"ok": True, "data": get_strategy_workspace(row_id, include_events=include_events, backtest_run_id=backtest_run_id)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/usedata")
@debug_timing("strategy_usedata")
def polymarket_strategy_usedata(row_id: int):
    try:
        include_live_orderbook = str(request.args.get("live_orderbook", "1")).lower() not in {"0", "false", "no"}
        return jsonify({
            "ok": True,
            "data": get_strategy_usedata_snapshot(row_id, include_live_orderbook=include_live_orderbook),
        })
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/strategies/usedata/draft")
@debug_timing("strategy_usedata_draft")
def polymarket_strategy_usedata_draft():
    try:
        include_live_orderbook = str(request.args.get("live_orderbook", "1")).lower() not in {"0", "false", "no"}
        return jsonify({
            "ok": True,
            "data": get_strategy_usedata_draft(
                request.get_json(silent=True) or {},
                include_live_orderbook=include_live_orderbook,
            ),
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/backtest")
def polymarket_strategy_backtest(row_id: int):
    try:
        return jsonify({"ok": True, "data": get_strategy_backtest(row_id)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/strategies/<int:row_id>/backtest")
def polymarket_strategy_backtest_create(row_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, "data": create_strategy_backtest(row_id, payload)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/backtest/results")
def polymarket_strategy_backtest_results(row_id: int):
    try:
        run_id = request.args.get("run_id")
        return jsonify({
            "ok": True,
            "data": get_strategy_backtest_results(row_id, int(run_id) if run_id else None),
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/chart")
@debug_timing("chart")
def polymarket_strategy_chart(row_id: int):
    try:
        return jsonify({"ok": True, "data": get_strategy_chart(row_id, request.args)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/chart-delta")
@debug_timing("chart_delta")
def polymarket_strategy_chart_delta(row_id: int):
    try:
        return jsonify({"ok": True, "data": get_strategy_chart_delta(row_id, request.args)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/events")
@debug_timing("events")
def polymarket_strategy_events(row_id: int):
    try:
        return jsonify({"ok": True, "data": list_strategy_events(row_id, request.args)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/live/strategies/<int:row_id>/workspace")
def live_strategy_workspace(row_id: int):
    def _sse(event_name: str, payload) -> str:
        return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def event_stream():
        last_event_ts = ""
        last_event_type = ""
        deadline = time.time() + 3600
        while time.time() < deadline:
            try:
                detail = fetch_strategy_detail(row_id, allow_remote_positions=False)
                events_payload = list_strategy_events(row_id, {"limit": 1})
                latest_event = (events_payload.get("data") or [None])[0]

                yield _sse(
                    "summary",
                    {
                        "type": "workspace_snapshot",
                        "summary": {
                            "yes_bid": detail.get("yes_bid"),
                            "yes_ask": detail.get("yes_ask"),
                            "no_bid": detail.get("no_bid"),
                            "no_ask": detail.get("no_ask"),
                            "yes_qty": detail.get("yes_qty"),
                            "no_qty": detail.get("no_qty"),
                            "yes_avg": detail.get("yes_avg"),
                            "no_avg": detail.get("no_avg"),
                            "yes_position": detail.get("yes_position"),
                            "no_position": detail.get("no_position"),
                            "strategy_pnl": detail.get("strategy_pnl"),
                            "strategy_bankroll": detail.get("strategy_bankroll"),
                            "market_updated_at": detail.get("market_updated_at"),
                            "price_source": detail.get("price_source"),
                        },
                    },
                )

                if latest_event:
                    next_ts = str(latest_event.get("ts") or "")
                    next_type = str(latest_event.get("event_type") or latest_event.get("type") or "")
                    if next_ts != last_event_ts or next_type != last_event_type:
                        last_event_ts = next_ts
                        last_event_type = next_type
                        yield _sse("event_append", latest_event)
            except GeneratorExit:
                return
            except Exception as exc:
                yield _sse("error", {"error": str(exc)})

            time.sleep(3)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/live/strategies")
def live_strategies():
    limit = request.args.get("limit", "30")
    try:
        limit_num = max(1, min(int(limit), 200))
    except ValueError:
        limit_num = 30

    def _sse(event_name: str, payload) -> str:
        return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def event_stream():
        deadline = time.time() + 3600
        while time.time() < deadline:
            try:
                payload = fetch_strategy_monitoring(limit=limit_num, sync_stats=False)
                rows = payload.get("data", [])
                valid_modes = {"Stop", "Virtual", "Real"}

                def _machine_state_from_row(row):
                    value = row.get("machine_state") or row.get("state") or "auto"
                    return "auto" if value in valid_modes else value

                light_rows = [
                    {
                        "row_id": row.get("row_id"),
                        "strategy_id": row.get("strategy_id"),
                        "display_name": row.get("display_name"),
                        "strategy": row.get("strategy"),
                        "question": row.get("question"),
                        "condition_id": row.get("condition_id"),
                        "yes_token": row.get("yes_token"),
                        "no_token": row.get("no_token"),
                        "slug": row.get("slug"),
                        "event_slug": row.get("event_slug"),
                        "group_item_title": row.get("group_item_title"),
                        "url": row.get("url"),
                        "score": row.get("score"),
                        "yes_ask": row.get("yes_ask"),
                        "yes_bid": row.get("yes_bid"),
                        "no_ask": row.get("no_ask"),
                        "no_bid": row.get("no_bid"),
                        "yes_qty": row.get("yes_qty"),
                        "yes_avg": row.get("yes_avg"),
                        "no_qty": row.get("no_qty"),
                        "no_avg": row.get("no_avg"),
                        "strategy_bankroll": row.get("strategy_bankroll"),
                        "yes_position": row.get("yes_position"),
                        "no_position": row.get("no_position"),
                        "strategy_pnl": row.get("strategy_pnl"),
                        "strategy_code": row.get("strategy_code"),
                        "strategy_name": row.get("strategy_name"),
                        "legs_count": row.get("legs_count"),
                        "legs_snapshot": row.get("legs_snapshot"),
                        "exposure": row.get("exposure"),
                        "last_action": row.get("last_action"),
                        "last_action_type": row.get("last_action_type"),
                        "updated_at": row.get("updated_at"),
                        "recent_events": row.get("recent_events"),
                        "profit": row.get("profit"),
                        "mode": row.get("mode") or (row.get("state") if row.get("state") in valid_modes else "Stop"),
                        "state": _machine_state_from_row(row),
                        "machine_state": _machine_state_from_row(row),
                        "state_options": row.get("state_options"),
                        "is_virtual": row.get("is_virtual"),
                        "editable": row.get("editable"),
                    }
                    for row in rows
                ]
                yield _sse(
                    "rows",
                    {
                        "rows": light_rows,
                        "ok": payload.get("ok"),
                        "status": payload.get("status"),
                        "count": payload.get("count"),
                        "db_path": payload.get("db_path"),
                        "table": payload.get("table"),
                        "snapshot_db_path": payload.get("snapshot_db_path"),
                        "realtime_snapshot_db_path": payload.get("realtime_snapshot_db_path"),
                        "running_strategy_count": payload.get("running_strategy_count"),
                        "total_strategy_profit": payload.get("total_strategy_profit"),
                        "total_strategy_bankroll": payload.get("total_strategy_bankroll"),
                        "total_strategy_return_pct": payload.get("total_strategy_return_pct"),
                        "source_statuses": payload.get("source_statuses"),
                        "strategy_metrics_db_dir": payload.get("strategy_metrics_db_dir"),
                    },
                )
            except GeneratorExit:
                return
            except Exception as exc:
                yield _sse("error", {"error": str(exc)})

            time.sleep(5)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/realtime/state")
def realtime_state():
    try:
        return jsonify({"ok": True, "data": collector.get_state()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/realtime/crypto")
def realtime_crypto():
    try:
        return jsonify({"ok": True, "data": collector.get_state().get("crypto", {})})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/realtime/finance")
def realtime_finance():
    try:
        return jsonify({"ok": True, "data": collector.get_state().get("finance", {})})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/crypto/quotes")
def crypto_quotes():
    symbols = request.args.get("symbols", "BTCUSDT,ETHUSDT,SOLUSDT")
    try:
        return jsonify(fetch_crypto_quotes(symbols))
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/finance/quotes")
def finance_quotes():
    symbols = request.args.get("symbols", "AAPL,MSFT,GLD,SLV")
    try:
        api_key = load_web_settings().get("active_finnhub_api_key") or None
        return jsonify(fetch_finance_quotes(symbols, api_key or None))
    except Exception as exc:
        return _json_error(exc)


# ---------------------------------------------------------------------------
# Strategy Registry (new tables) API
# ---------------------------------------------------------------------------

@app.get("/api/strategy-codes")
def api_strategy_codes():
    try:
        return jsonify({"ok": True, "data": list_strategy_codes()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/strategy-codes/<code_name>/inputs")
def api_strategy_code_inputs(code_name: str):
    try:
        return jsonify({"ok": True, "data": get_strategy_code_inputs(code_name)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/strategy-codes/<code_name>/schemas")
def api_strategy_code_schemas(code_name: str):
    try:
        return jsonify({"ok": True, "data": get_strategy_code_schemas(code_name)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/registry/strategies")
@debug_timing("registry_list")
def registry_strategies_list():
    try:
        return jsonify({"ok": True, "data": list_registry_strategies()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/registry/strategies")
@debug_timing("registry_create")
def registry_strategies_create():
    try:
        payload = request.get_json(silent=True) or {}
        result = create_strategy(payload)
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/registry/strategies/<int:strategy_id>")
@debug_timing("registry_get")
def registry_strategies_get(strategy_id: int):
    try:
        result = get_registry_strategy(strategy_id)
        if not result:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/registry/strategies/<int:strategy_id>")
@debug_timing("registry_update")
def registry_strategies_update(strategy_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        result = update_registry_strategy(strategy_id, payload)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/registry/strategies/<int:strategy_id>/mode")
@debug_timing("registry_mode")
def registry_strategies_mode(strategy_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        new_mode = str(payload.get("mode") or payload.get("state") or "").strip()
        result = update_strategy_mode(strategy_id, new_mode)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/registry/strategies/<int:strategy_id>/state")
@debug_timing("registry_state_compat")
def registry_strategies_state(strategy_id: int):
    """Compatibility route for the old Stop/Virtual/Real endpoint."""
    try:
        payload = request.get_json(silent=True) or {}
        new_mode = str(payload.get("mode") or payload.get("state") or "").strip()
        result = update_strategy_state(strategy_id, new_mode)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/registry/strategies/<int:strategy_id>/state-store")
@debug_timing("registry_state_store_get")
def registry_strategy_state_store_get(strategy_id: int):
    try:
        strategy = get_registry_strategy(strategy_id)
        if not strategy:
            return jsonify({"ok": False, "error": "strategy not found"}), 404
        return jsonify({
            "ok": True,
            "data": {
                "strategy_id": strategy_id,
                "mode": strategy.get("mode") or strategy.get("state") or "Stop",
                **strategy_state_payload(
                    strategy.get("strategy_code") or "",
                    read_strategy_state_bundle(strategy_id),
                ),
            },
        })
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/registry/strategies/<int:strategy_id>/state-store/<namespace>")
@debug_timing("registry_state_store_patch")
def registry_strategy_state_store_patch(strategy_id: int, namespace: str):
    try:
        ns = str(namespace or "").strip().lower()
        if ns == "controls":
            ns = "user"
        if ns not in {"user", "runtime", "machine", "state"}:
            return _json_error(ValueError("namespace must be controls/user, runtime, or machine/state"), 400)
        strategy = get_registry_strategy(strategy_id)
        if not strategy:
            return jsonify({"ok": False, "error": "strategy not found"}), 404
        payload = request.get_json(silent=True) or {}
        values = payload.get("values")
        if values is None:
            values = payload.get("state", {})
        if not isinstance(values, dict):
            return _json_error(ValueError("state values must be a JSON object"), 400)
        force = bool(payload.get("force"))
        mode = strategy.get("mode") or strategy.get("state") or "Stop"
        if ns == "runtime" and mode != "Stop" and not force:
            return _json_error(
                ValueError("RuntimeState can only be edited while the strategy is Stop"),
                400,
            )
        changed = write_strategy_state_values(
            strategy_id,
            values,
            namespace=ns,
            replace=bool(payload.get("replace", True)),
            actor=str(payload.get("actor") or "user"),
            reason=str(payload.get("reason") or ""),
        )
        return jsonify({
            "ok": True,
            "changed": changed,
            "data": strategy_state_payload(
                strategy.get("strategy_code") or "",
                read_strategy_state_bundle(strategy_id),
            ),
        })
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/registry/strategies/<int:strategy_id>/state-store/<namespace>")
@debug_timing("registry_state_store_reset")
def registry_strategy_state_store_reset(strategy_id: int, namespace: str):
    try:
        ns = str(namespace or "").strip().lower()
        if ns == "controls":
            ns = "user"
        if ns not in {"user", "runtime", "machine", "state"}:
            return _json_error(ValueError("namespace must be controls/user, runtime, or machine/state"), 400)
        strategy = get_registry_strategy(strategy_id)
        if not strategy:
            return jsonify({"ok": False, "error": "strategy not found"}), 404
        force = str(request.args.get("force") or "").lower() in {"1", "true", "yes"}
        mode = strategy.get("mode") or strategy.get("state") or "Stop"
        if ns == "runtime" and mode != "Stop" and not force:
            return _json_error(
                ValueError("RuntimeState can only be reset while the strategy is Stop"),
                400,
            )
        changed = reset_strategy_state_namespace(
            strategy_id,
            ns,
            actor=str(request.args.get("actor") or "user"),
            reason=str(request.args.get("reason") or ""),
        )
        return jsonify({
            "ok": True,
            "changed": changed,
            "data": strategy_state_payload(
                strategy.get("strategy_code") or "",
                read_strategy_state_bundle(strategy_id),
            ),
        })
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/registry/strategies/<int:strategy_id>/legs")
@debug_timing("registry_legs")
def registry_strategies_legs(strategy_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        legs = payload.get("legs") or []
        result = update_strategy_legs(strategy_id, legs)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/registry/strategies/<int:strategy_id>")
@debug_timing("registry_delete")
def registry_strategies_delete(strategy_id: int):
    try:
        ok = delete_strategy(strategy_id)
        if not ok:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/registry/strategies/<int:strategy_id>/force-flat")
@debug_timing("registry_force_flat")
def registry_strategy_force_flat(strategy_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        actor = str(payload.get("actor") or "user")
        return jsonify({"ok": True, "data": force_flat_strategy(strategy_id, actor=actor)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)

# ---------------------------------------------------------------------------
# Virtual trading API
# ---------------------------------------------------------------------------

from services.strategy_data_source import connect as _ds_connect
from services.virtual_execution import reset_virtual_account
from services.virtual_runner import virtual_runner



@app.get("/api/virtual/strategies/<int:strategy_id>/account")
def virtual_account(strategy_id: int):
    try:
        conn = _ds_connect(readonly=True)
        row = conn.execute(
            "SELECT * FROM strategy_virtual_account WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"ok": True, "data": None})
        return jsonify({"ok": True, "data": dict(row)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/virtual/strategies/<int:strategy_id>/positions")
def virtual_positions(strategy_id: int):
    try:
        conn = _ds_connect(readonly=True)
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_positions WHERE strategy_id = ? ORDER BY leg_index, side",
            (strategy_id,),
        ).fetchall()
        rows_v2 = conn.execute(
            "SELECT * FROM strategy_virtual_positions_v2 WHERE strategy_id = ? ORDER BY instrument_id, side",
            (strategy_id,),
        ).fetchall()
        conn.close()
        return jsonify({"ok": True, "data": [dict(r) for r in rows], "data_v2": [dict(r) for r in rows_v2]})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/virtual/strategies/<int:strategy_id>/orders")
def virtual_orders(strategy_id: int):
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = max(1, min(int(request.args.get("page_size", 50)), 200))
        offset = (page - 1) * page_size
        conn = _ds_connect(readonly=True)
        total = conn.execute(
            "SELECT COUNT(*) FROM strategy_virtual_orders WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_orders WHERE strategy_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (strategy_id, page_size, offset),
        ).fetchall()
        total_v2 = conn.execute(
            "SELECT COUNT(*) FROM strategy_virtual_orders_v2 WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()[0]
        rows_v2 = conn.execute(
            "SELECT * FROM strategy_virtual_orders_v2 WHERE strategy_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (strategy_id, page_size, offset),
        ).fetchall()
        conn.close()
        return jsonify({
            "ok": True,
            "data": [dict(r) for r in rows],
            "data_v2": [dict(r) for r in rows_v2],
            "total": total,
            "total_v2": total_v2,
            "page": page,
            "page_size": page_size,
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/virtual/strategies/<int:strategy_id>/events")
def virtual_events(strategy_id: int):
    try:
        event_type = request.args.get("event_type", "")
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
        conn = _ds_connect(readonly=True)
        if event_type:
            rows = conn.execute(
                "SELECT * FROM strategy_virtual_events WHERE strategy_id = ? AND event_type = ? ORDER BY id DESC LIMIT ?",
                (strategy_id, event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM strategy_virtual_events WHERE strategy_id = ? ORDER BY id DESC LIMIT ?",
                (strategy_id, limit),
            ).fetchall()
        conn.close()
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/virtual/strategies/<int:strategy_id>/ticks")
def virtual_ticks(strategy_id: int):
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
        conn = _ds_connect(readonly=True)
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_ticks WHERE strategy_id = ? ORDER BY tick_id DESC LIMIT ?",
            (strategy_id, limit),
        ).fetchall()
        conn.close()
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/virtual/strategies/<int:strategy_id>/reset")
def virtual_reset(strategy_id: int):
    try:
        from services.strategy_registry_service import get_strategy as _get_stg
        stg = _get_stg(strategy_id)
        if not stg:
            return jsonify({"ok": False, "error": "strategy not found"}), 404
        initial_cash = float(stg.get("strategy_bankroll") or 0.0)
        if initial_cash <= 0:
            initial_cash = sum(
                float((leg or {}).get("budget_cap") or 0.0)
                for leg in (stg.get("legs") or [])
            )
        reset_virtual_account(strategy_id, initial_cash)
        return jsonify({"ok": True})
    except Exception as exc:
        return _json_error(exc)


if __name__ == "__main__":
    ws_market_sync.start()
    collector.start()
    virtual_runner.start()
    event_news_scheduler.start()
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
