"""
market_deltas 保留期清理。

保留策略：
  book / price_change  : 7 天
  SYSTEM_ADD / SYSTEM_REMOVE : 7 天
  其余（含 StrategyMonitoring）: 90 天

策略当前监控的 condition_id / token 对应的行永不删除。

调用方式：
  from services.market_deltas_cleanup import run_cleanup
  run_cleanup()          # 使用默认路径
  run_cleanup(db_path)   # 指定路径
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, Set

# 各 event_type 对应保留天数（未匹配的走 DEFAULT）
_RETENTION: dict[str, int] = {
    "book":           7,
    "price_change":   7,
    "SYSTEM_ADD":    7,
    "SYSTEM_REMOVE":  7,
}
_DEFAULT_RETENTION = 90


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _active_tokens_and_conditions() -> Set[str]:
    """从策略库读取所有活跃 leg 的 condition_id / yes_token / no_token。"""
    try:
        from services.strategy_data_source import db_path as monitoring_db_path
        path = str(monitoring_db_path())
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                "SELECT condition_id, yes_token, no_token FROM strategy_legs"
            ).fetchall()
        finally:
            conn.close()
        result: Set[str] = set()
        for cid, yt, nt in rows:
            if cid:
                result.add(cid)
            if yt:
                result.add(yt)
            if nt:
                result.add(nt)
        return result
    except Exception:
        return set()


def run_cleanup(db_path: Optional[str] = None) -> dict[str, int]:
    """
    清理 market_deltas 过期行，跳过策略当前监控的 token/condition。
    返回 {event_type: deleted_count, ...} 统计。
    """
    if db_path is None:
        from services.config_loader import load_web_settings, BASE_DIR
        settings = load_web_settings()
        raw = str(settings.get("market_realtime_db_path", "")).strip()
        db_path = raw if raw else str(BASE_DIR / "Data" / "polymarket_realtime.db")

    protected = _active_tokens_and_conditions()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    stats: dict[str, int] = {}
    try:
        rows = conn.execute(
            "SELECT DISTINCT event_type FROM market_deltas WHERE event_type IS NOT NULL"
        ).fetchall()
        for row in rows:
            et = row[0]
            days = _RETENTION.get(et, _DEFAULT_RETENTION)
            cut = _cutoff(days)
            if protected:
                placeholders = ",".join("?" * len(protected))
                cur = conn.execute(
                    f"""DELETE FROM market_deltas
                        WHERE event_type = ? AND timestamp < ?
                          AND condition_id NOT IN ({placeholders})
                          AND clobTokenId NOT IN ({placeholders})""",
                    (et, cut, *protected, *protected),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM market_deltas WHERE event_type = ? AND timestamp < ?",
                    (et, cut),
                )
            if cur.rowcount:
                stats[et] = cur.rowcount

        # NULL event_type
        cut = _cutoff(_DEFAULT_RETENTION)
        if protected:
            placeholders = ",".join("?" * len(protected))
            cur = conn.execute(
                f"""DELETE FROM market_deltas
                    WHERE event_type IS NULL AND timestamp < ?
                      AND condition_id NOT IN ({placeholders})
                      AND clobTokenId NOT IN ({placeholders})""",
                (cut, *protected, *protected),
            )
        else:
            cur = conn.execute(
                "DELETE FROM market_deltas WHERE event_type IS NULL AND timestamp < ?",
                (cut,),
            )
        if cur.rowcount:
            stats["(null)"] = cur.rowcount

        conn.commit()
    finally:
        conn.close()

    return stats