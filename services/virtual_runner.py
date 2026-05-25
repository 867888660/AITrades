"""
VirtualRunner — 虚拟盘调度循环。

每隔 N 秒读取 state=Virtual 的策略，依次：
  1. VirtualContextBuilder 构造 UseData
  2. 调用 SandboxRun.run_node() 获取 FunctionJson
  3. VirtualExecution 执行 actions，写五张虚拟盘表
  4. 写 tick 日志
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.strategy_audit_store import (
    MODE_VIRTUAL,
    create_run_tick,
    json_hash,
    update_run_tick,
    write_run_event,
    write_run_events,
)
from services.strategy_data_source import connect as ds_connect, list_strategies, write_strategy_state_updates
from services.strategy_metric_store import write_metric_events
from services.virtual_context_builder import build_use_data
from services.virtual_execution import (
    create_tick,
    execute_actions,
    sync_virtual_open_orders,
    update_tick,
    write_error_event,
    write_print_events,
)

_BASE_DIR = Path(__file__).resolve().parent.parent
_STRATEGY_CODE_DIR = _BASE_DIR / "StrategyCode"
_SANDBOX_PATH = _BASE_DIR / "参考" / "SandboxRun.py"

# 默认轮询间隔（秒）
DEFAULT_INTERVAL = 10
LOCK_TTL_SECONDS = max(60, DEFAULT_INTERVAL * 6)


# ---------------------------------------------------------------------------
# SandboxRun 动态加载（每次调用重新加载，隔离策略模块）
# ---------------------------------------------------------------------------

def _load_sandbox():
    """加载 SandboxRun 模块，返回模块对象。"""
    path = str(_SANDBOX_PATH)
    if not os.path.isfile(path):
        raise RuntimeError(f"SandboxRun.py 未找到：{path}")
    mod_name = f"_sandbox_runner_{id(threading.current_thread())}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_input_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw.strip() else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _strategy_file_path(code_name: str) -> Optional[Path]:
    raw = str(code_name or "").strip()
    if not raw:
        return None
    base = os.path.basename(raw)
    stem, ext = os.path.splitext(base)
    candidates = [base] if ext.lower() == ".py" else [base + ".py"]
    if stem and not stem.lower().startswith("stragy_"):
        candidates.append("Stragy_" + stem + ".py")
    for filename in dict.fromkeys(candidates):
        path = _STRATEGY_CODE_DIR / filename
        if path.is_file():
            return path
    return None


def _load_strategy_input_defs(code_name: str) -> List[Dict[str, Any]]:
    path = _strategy_file_path(code_name)
    if path is None:
        return []
    mod_name = f"_strategy_inputs_{abs(hash(str(path)))}_{id(threading.current_thread())}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(path))
        if spec is None or spec.loader is None:
            return []
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        inputs = getattr(mod, "Inputs", [])
        return [inp for inp in inputs if isinstance(inp, dict)]
    except Exception:
        return []
    finally:
        sys.modules.pop(mod_name, None)


def _input_value(params: Dict[str, Any], input_def: Dict[str, Any], index: int) -> Any:
    names = [
        input_def.get("name"),
        input_def.get("Id"),
        f"Input{index}",
        f"Inputs{index}",
    ]
    for name in names:
        if name and name in params:
            return params[name]
    return None


def _num_value(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_sandbox_node(strategy: Dict[str, Any], use_data: Dict[str, Any]) -> Dict[str, Any]:
    """构造传给 SandboxRun.run_node() 的 node 字典。"""
    code_name = str(strategy.get("strategy_code") or "").strip()
    use_data_str = json.dumps(use_data, ensure_ascii=False)
    input_params = _parse_input_json(strategy.get("input_json") or {})
    strategy_inputs = [
        inp for inp in _load_strategy_input_defs(code_name)
        if str(inp.get("name") or "").strip().lower() != "usedata"
    ]

    inputs = [
        # Input[0]: StrategyFolder
        {"Id": "Input1", "name": "StrategyFolder", "Kind": "String_FilePath",
         "Context": str(_STRATEGY_CODE_DIR), "Num": None, "Isnecessary": True, "IsLabel": False, "Link": 0},
        # Input[1]: StrategyName
        {"Id": "Input2", "name": "StrategyName", "Kind": "String",
         "Context": code_name, "Num": None, "Isnecessary": True, "IsLabel": False, "Link": 0},
        # Input[2]: UseData
        {"Id": "Input3", "name": "UseData", "Kind": "String",
         "Context": use_data_str, "Num": None, "Isnecessary": True, "IsLabel": False, "Link": 0},
    ]
    # Input3..Input15: 空占位
    for k in range(1, 14):
        input_def = strategy_inputs[k - 1] if len(strategy_inputs) >= k else {}
        value = _input_value(input_params, input_def, k)
        num = _num_value(value)
        kind = input_def.get("Kind") or ("Num" if num is not None else "String")
        inputs.append({
            "Id": f"Input{k + 3}",
            "name": input_def.get("name") or f"Input{k}",
            "Kind": kind,
            "Context": None if value is None else value,
            "Num": num,
            "Isnecessary": bool(input_def.get("Isnecessary", False)),
            "IsLabel": bool(input_def.get("IsLabel", False)),
            "Link": 0,
        })
    return {"Inputs": inputs}


def _parse_function_json(raw: Optional[str]) -> Dict[str, Any]:
    """解析 FunctionJson 字符串，返回 {actions, print, wake_reason}。"""
    if not raw:
        return {"actions": [], "print": [], "wake_reason": None, "metrics": {}, "metrics_meta": {}, "state_updates": {}}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"actions": [], "print": [str(data)], "wake_reason": None, "metrics": {}, "metrics_meta": {}, "state_updates": {}}
        return {
            "actions": data.get("actions") or [],
            "print": data.get("print") or [],
            "wake_reason": data.get("wake_reason"),
            "metrics": data.get("metrics") if isinstance(data.get("metrics"), dict) else {},
            "metrics_meta": data.get("metrics_meta") if isinstance(data.get("metrics_meta"), dict) else {},
            "state_updates": data.get("state_updates") if isinstance(data.get("state_updates"), dict) else {},
        }
    except Exception as e:
        return {"actions": [], "print": [f"[ParseError] {e}"], "wake_reason": None, "metrics": {}, "metrics_meta": {}, "state_updates": {}}


def _price_snapshot_from_use_data(use_data: Dict[str, Any]) -> Dict[str, Any]:
    """Persist the structured prices the strategy actually received this tick."""
    def pick(*keys: str) -> Any:
        for key in keys:
            value = use_data.get(key)
            if value is not None and str(value).strip() != "":
                return value
        return None

    return {
        "yes_bid": pick("L0_Yes_BidPrice", "Yes_BidPrice", "Yes_now_bid"),
        "yes_ask": pick("L0_Yes_AskPrice", "Yes_AskPrice", "Yes_now_ask"),
        "no_bid": pick("L0_No_BidPrice", "No_BidPrice", "No_now_bid"),
        "no_ask": pick("L0_No_AskPrice", "No_AskPrice", "No_now_ask"),
    }


def _lock_owner() -> str:
    return f"pid:{os.getpid()}:{threading.current_thread().name}"


def _acquire_strategy_lock(conn, strategy_id: int) -> Optional[str]:
    now = datetime.now(timezone.utc)
    owner = _lock_owner()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM strategy_virtual_run_locks WHERE strategy_id = ? AND expires_at_utc <= ?",
            (strategy_id, now.isoformat()),
        )
        conn.execute(
            """INSERT INTO strategy_virtual_run_locks(strategy_id, owner, acquired_at_utc, expires_at_utc)
               VALUES (?, ?, ?, ?)""",
            (
                strategy_id,
                owner,
                now.isoformat(),
                (now + timedelta(seconds=LOCK_TTL_SECONDS)).isoformat(),
            ),
        )
        conn.commit()
        return owner
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    except Exception:
        conn.rollback()
        raise


def _release_strategy_lock(conn, strategy_id: int, owner: str) -> None:
    conn.execute(
        "DELETE FROM strategy_virtual_run_locks WHERE strategy_id = ? AND owner = ?",
        (strategy_id, owner),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 单策略执行
# ---------------------------------------------------------------------------

def _run_one_strategy(strategy: Dict[str, Any], collector_state: Optional[Dict[str, Any]]) -> None:
    strategy_id: int = strategy["strategy_id"]
    strategy_bankroll: float = float(strategy.get("strategy_bankroll") or 0.0)
    market_category: Optional[str] = None

    run_at = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()

    from services.config_loader import load_web_settings
    settings = load_web_settings()
    realtime_db = str(settings.get("market_realtime_db_path") or "").strip()
    if not realtime_db:
        realtime_db = str(_BASE_DIR / "Data" / "polymarket_realtime.db")

    function_json_raw: Optional[str] = None
    error_msg: Optional[str] = None
    orders_placed = 0
    parsed: Dict[str, Any] = {"actions": [], "print": [], "wake_reason": None}
    use_data: Dict[str, Any] = {}

    # Single connection for the entire tick
    conn = ds_connect()
    lock_owner: Optional[str] = None
    try:
        lock_owner = _acquire_strategy_lock(conn, strategy_id)
        if not lock_owner:
            return
        audit_tick_id = create_run_tick(strategy_id, MODE_VIRTUAL, run_at, conn=conn)
        tick_id = create_tick(conn, strategy_id, run_at)

        try:
            use_data = build_use_data(strategy, realtime_db, collector_state)
            market_category = str(use_data.get("L0_MarketCategory") or "").strip() or None
            fills_synced, sync_errors = sync_virtual_open_orders(
                strategy_id=strategy_id,
                strategy_bankroll=strategy_bankroll,
                use_data=use_data,
                tick_id=tick_id,
                market_category=market_category,
                audit_tick_id=audit_tick_id,
            )
            if sync_errors:
                error_msg = "; ".join(sync_errors)
            if fills_synced:
                use_data = build_use_data(strategy, realtime_db, collector_state)

            sandbox = _load_sandbox()
            node = _build_sandbox_node(strategy, use_data)
            outputs = sandbox.run_node(node)

            if isinstance(outputs, list) and len(outputs) >= 1:
                function_json_raw = outputs[0].get("Context")
            else:
                error_msg = "SandboxRun 返回结构异常"

            parsed = _parse_function_json(function_json_raw)

            if parsed["actions"]:
                orders_placed, exec_errors = execute_actions(
                    strategy_id=strategy_id,
                    strategy_bankroll=strategy_bankroll,
                    actions=parsed["actions"],
                    use_data=use_data,
                    tick_id=tick_id,
                    market_category=market_category,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=json_hash(function_json_raw),
                )
                if exec_errors:
                    error_msg = "; ".join(exec_errors)

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"

        duration_ms = (time.perf_counter() - t0) * 1000
        mode_output = json.dumps(
            {
                "actions": parsed["actions"] or [],
                "state_updates": parsed.get("state_updates") or {},
                "price_snapshot": _price_snapshot_from_use_data(use_data),
            },
            ensure_ascii=False,
        )

        update_run_tick(
            audit_tick_id,
            duration_ms=duration_ms,
            function_json=function_json_raw,
            mode_output=mode_output,
            error=error_msg,
            actions_count=len(parsed["actions"] or []),
            orders_count=orders_placed,
            conn=conn,
        )

        update_tick(
            conn=conn,
            tick_id=tick_id,
            duration_ms=duration_ms,
            function_json=function_json_raw,
            mode_output=mode_output,
            error=error_msg,
            orders_placed=orders_placed,
        )

        if parsed.get("metrics"):
            write_metric_events(
                strategy_id=strategy_id,
                tick_id=tick_id,
                run_at_utc=run_at,
                metrics=parsed.get("metrics"),
                metrics_meta=parsed.get("metrics_meta"),
                conn=conn,
            )

        state_updates = dict(parsed.get("state_updates") or {})
        if state_updates and parsed.get("actions") and orders_placed <= 0:
            for key in ("last_action_at", "cooldown_until", "entry_price", "entry_side"):
                state_updates.pop(key, None)

        if state_updates:
            write_strategy_state_updates(
                strategy_id,
                state_updates,
                conn=conn,
            )
            conn.commit()

        if parsed.get("print"):
            write_run_events(strategy_id, MODE_VIRTUAL, "print", parsed["print"], tick_id=audit_tick_id, conn=conn)
            write_print_events(strategy_id, tick_id, parsed["print"])
        if error_msg:
            write_run_event(strategy_id, MODE_VIRTUAL, "error", error_msg, tick_id=audit_tick_id, conn=conn)
            write_error_event(strategy_id, tick_id, error_msg)
    finally:
        if lock_owner:
            try:
                _release_strategy_lock(conn, strategy_id, lock_owner)
            except Exception as e:
                print(f"[VirtualRunner] release lock failed strategy={strategy_id}: {e}")
        conn.close()


# ---------------------------------------------------------------------------
# 调度循环
# ---------------------------------------------------------------------------

class VirtualRunner:
    def __init__(self, interval: float = DEFAULT_INTERVAL):
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="VirtualRunner")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def _loop(self) -> None:
        from services.realtime_collector import collector

        while not self._stop_event.is_set():
            try:
                self._tick(collector.get_state() if hasattr(collector, "get_state") else None)
            except Exception as e:
                print(f"[VirtualRunner] loop error: {e}")
            self._stop_event.wait(self._interval)

    def _tick(self, collector_state: Optional[Dict[str, Any]]) -> None:
        strategies = list_strategies(state_filter="Virtual")
        for strategy in strategies:
            try:
                _run_one_strategy(strategy, collector_state)
            except Exception as e:
                sid = strategy.get("strategy_id")
                print(f"[VirtualRunner] strategy {sid} error: {e}")


# 全局单例
virtual_runner = VirtualRunner()
