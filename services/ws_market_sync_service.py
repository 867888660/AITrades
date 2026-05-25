from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict

from services.config_loader import BASE_DIR, get_market_realtime_db_path, load_web_settings


class WsMarketSyncService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._started_at: str | None = None
        self._last_error: str | None = None
        self._monitor = None

    def _resolve_sqlite_path(self) -> str:
        settings = load_web_settings()
        realtime_path = get_market_realtime_db_path(settings)
        if realtime_path:
            return realtime_path
        return str(Path("polymarket_realtime.db").resolve())

    def _run(self) -> None:
        try:
            import main as ws_main

            realtime_path = self._resolve_sqlite_path()
            ws_main.SQLITE_PATH = realtime_path
            ws_main.SQLITE_PATH_FAVOURITE = realtime_path

            # 与 Flask 工作台一致：策略监控库路径以 web_settings 为准，保证监控表内期权双边订阅。
            settings = load_web_settings()
            mon_path = str(settings.get("strategy_monitoring_db_path") or "").strip()
            if mon_path:
                p = Path(mon_path).expanduser()
                if not p.is_absolute():
                    p = BASE_DIR / p
                ws_main.STRATEGY_MONITORING_PATH = str(p.resolve())
                ws_main.STRATEGY_MONITORING_ENABLED = True
            mon_table = str(settings.get("strategy_monitoring_table") or "").strip()
            if mon_table:
                ws_main.STRATEGY_MONITORING_TABLE = ws_main._resolve_existing_monitoring_table(
                    ws_main.STRATEGY_MONITORING_PATH,
                    mon_table,
                )

            markets = ws_main.fetch_all_active_markets()
            # The Flask app needs fresh prices for explicit strategy/holding tokens.
            # Subscribing the broad probability scan here can produce tens of thousands
            # of tokens and starve the strategy WS feed during startup/reconnect.
            candidates_high, candidates_low = [], []
            candidates_holding = ws_main.fetch_my_holdings(markets)
            candidates_strategy_monitoring = ws_main.load_strategy_monitoring_candidates(markets)

            monitor = ws_main.WebSocketMonitor(
                candidates_high,
                candidates_low,
                candidates_holding,
                candidates_strategy_monitoring,
                enable_probability_scan=False,
            )
            self._monitor = monitor
            with self._lock:
                self._running = True
                self._last_error = None
            monitor.run()
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
        finally:
            with self._lock:
                self._running = False

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True, name="ws-market-sync")
            self._thread.start()

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def get_state(self) -> Dict[str, Any]:
        realtime_path = self._resolve_sqlite_path()
        with self._lock:
            return {
                "running": self._running,
                "last_error": self._last_error,
                "sqlite_path": realtime_path,
                "thread_alive": bool(self._thread and self._thread.is_alive()),
                "monitor": self._monitor.get_runtime_state() if self._monitor is not None else None,
            }


ws_market_sync = WsMarketSyncService()
