from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from services.config_loader import load_public_web_settings, load_web_settings, load_web_settings_for_ui, save_web_settings
from services.crypto_service import fetch_crypto_quotes
from services.finance_service import fetch_finance_quotes
from services.http_client import SESSION
from services.backtest_service import create_backtest_placeholder, get_backtest_placeholder
from services.polymarket_dictionary_service import get_dictionary_status, start_dictionary_refresh
from services.ledger_service import get_ledger_snapshot
from services.polymarket_service import (
    fetch_strategy_detail,
    fetch_strategy_monitoring,
    fetch_wallet_positions,
    get_overview,
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


@app.get("/api/polymarket/markets")
def polymarket_markets():
    query = request.args.get("q", "")
    category = request.args.get("category", "")
    force_refresh = request.args.get("refresh", "0") == "1"
    limit = request.args.get("limit", "60")
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
        )
        return jsonify({"ok": True, "count": len(data), "data": data})
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
        return jsonify({"ok": True, "data": get_strategy_workspace(row_id, include_events=include_events)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/usedata")
@debug_timing("strategy_usedata")
def polymarket_strategy_usedata(row_id: int):
    try:
        return jsonify({"ok": True, "data": get_strategy_usedata_snapshot(row_id)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/strategies/usedata/draft")
@debug_timing("strategy_usedata_draft")
def polymarket_strategy_usedata_draft():
    try:
        return jsonify({"ok": True, "data": get_strategy_usedata_draft(request.get_json(silent=True) or {})})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/backtest")
def polymarket_strategy_backtest(row_id: int):
    try:
        return jsonify({"ok": True, "data": get_backtest_placeholder(row_id)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/strategies/<int:row_id>/backtest")
def polymarket_strategy_backtest_create(row_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(create_backtest_placeholder(row_id, payload)), 501
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/backtest/results")
def polymarket_strategy_backtest_results(row_id: int):
    try:
        return jsonify(create_backtest_placeholder(row_id, {"results_only": True})), 501
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
                        "state": row.get("state"),
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


@app.patch("/api/registry/strategies/<int:strategy_id>/state")
@debug_timing("registry_state")
def registry_strategies_state(strategy_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        new_state = str(payload.get("state") or "").strip()
        result = update_strategy_state(strategy_id, new_state)
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
                "mode": strategy.get("state") or "Stop",
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
        if ns not in {"user", "runtime"}:
            return _json_error(ValueError("namespace must be controls/user or runtime"), 400)
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
        if ns == "runtime" and strategy.get("state") != "Stop" and not force:
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
        if ns not in {"user", "runtime"}:
            return _json_error(ValueError("namespace must be controls/user or runtime"), 400)
        strategy = get_registry_strategy(strategy_id)
        if not strategy:
            return jsonify({"ok": False, "error": "strategy not found"}), 404
        force = str(request.args.get("force") or "").lower() in {"1", "true", "yes"}
        if ns == "runtime" and strategy.get("state") != "Stop" and not force:
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
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
