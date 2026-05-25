import json
import os
import requests
import time
import random
import asyncio
import sqlite3
import threading
from contextlib import AsyncExitStack

# --- 使用外部已配置代理；未配置时直连，避免本地未开 7890 时全链路空跑 ---

# Windows 下 ProactorEventLoop 在 TLS/WebSocket 断连时偶发触发底层
# transport/recv_messages AttributeError，切回 Selector 更稳。
if os.name == "nt" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import signal
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pymongo
from tqdm import tqdm

try:
    import websockets
    from websockets_proxy import proxy_connect
except ImportError:
    print("[!] 缺少 websockets 或 websockets_proxy 库，请运行: pip install websockets websockets_proxy")
    exit(1)

# ============================================================
# 配置加载
# ============================================================
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)


def _config_abspath(p):
    if not p:
        return p
    return p if os.path.isabs(p) else os.path.join(CONFIG_DIR, p)


def _ensure_sqlite_file(path):
    if not path:
        return
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    if not os.path.exists(path):
        conn = sqlite3.connect(path)
        conn.close()


def _resolve_existing_monitoring_table(db_path, preferred_table):
    table = str(preferred_table or "").strip()
    if not db_path or not os.path.exists(db_path):
        return table or "strategy_registry"
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        tables = [str(row[0]) for row in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()]
        if "strategy_registry" in tables:
            return "strategy_registry"
        if table and table in tables:
            return table
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
    return table or "strategy_registry"


def _sqlite_float_or_none(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sqlite_outcome_side_from_info(info):
    name = str((info.get("target_option") or {}).get("name", "")).strip().lower()
    if name in ("yes", "no"):
        return name
    return ""


def _sqlite_flat_from_depth_metrics(depth_metrics, info, book_data):
    """与 depth_metrics_json / 图表侧一致的标量字段，便于直接 SQL 查询。"""
    dm = depth_metrics or {}
    now_bid = _sqlite_float_or_none(dm.get("now_bid", dm.get("best_bid")))
    now_ask = _sqlite_float_or_none(dm.get("now_ask", dm.get("best_ask")))
    best_bid = _sqlite_float_or_none(dm.get("best_bid"))
    best_ask = _sqlite_float_or_none(dm.get("best_ask"))
    spread_c = _sqlite_float_or_none(dm.get("spread_c"))
    last_price = _sqlite_float_or_none(info.get("target_price"))
    if last_price is None and isinstance(book_data, dict):
        last_price = _sqlite_float_or_none(
            book_data.get("price") or book_data.get("last_trade_price") or book_data.get("lastPrice")
        )
    return (
        _sqlite_outcome_side_from_info(info),
        now_bid,
        now_ask,
        best_bid,
        best_ask,
        last_price,
        spread_c,
    )


def _sqlite_flat_from_delta_payload(payload, info):
    pl = payload if isinstance(payload, dict) else {}
    now_bid = _sqlite_float_or_none(pl.get("now_bid", pl.get("best_bid")))
    now_ask = _sqlite_float_or_none(pl.get("now_ask", pl.get("best_ask")))
    best_bid = _sqlite_float_or_none(pl.get("best_bid"))
    best_ask = _sqlite_float_or_none(pl.get("best_ask"))
    spread_c = _sqlite_float_or_none(pl.get("spread_c"))
    last_price = _sqlite_float_or_none(
        pl.get("last_price") or pl.get("price") or pl.get("last_trade_price") or pl.get("lastPrice")
    )
    return (
        _sqlite_outcome_side_from_info(info),
        now_bid,
        now_ask,
        best_bid,
        best_ask,
        last_price,
        spread_c,
    )


def _sqlite_ensure_flat_columns(conn):
    """为已有库追加展开列（不新建表）。"""
    for table, defs in (
        (
            "markets_state",
            (
                ("outcome_side", "TEXT"),
                ("now_bid", "REAL"),
                ("now_ask", "REAL"),
                ("best_bid", "REAL"),
                ("best_ask", "REAL"),
                ("last_price", "REAL"),
                ("spread_c", "REAL"),
            ),
        ),
        (
            "market_deltas",
            (
                ("outcome_side", "TEXT"),
                ("now_bid", "REAL"),
                ("now_ask", "REAL"),
                ("best_bid", "REAL"),
                ("best_ask", "REAL"),
                ("last_price", "REAL"),
                ("spread_c", "REAL"),
            ),
        ),
    ):
        existing = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        for col_name, col_type in defs:
            if col_name not in existing:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col_name}" {col_type}')


GAMMA_API = config["api"]["gamma_base"]
CLOB_API = config["api"]["clob_base"]

DB_CONFIG = config.get("database", {})
DELTAS_SUFFIX = DB_CONFIG.get("deltas_table_suffix", "_Deltas")

MONGO_CONFIG = DB_CONFIG.get("mongodb", {})
MONGO_ENABLED = MONGO_CONFIG.get("enabled", True)
MONGO_URI = MONGO_CONFIG.get("uri", "mongodb://localhost:27017")
DB_NAME = MONGO_CONFIG.get("db_name", "PolymarketWS")

SQLITE_CONFIG = DB_CONFIG.get("sqlite", {})
SQLITE_ENABLED = SQLITE_CONFIG.get("enabled", False)
SQLITE_PATH = _config_abspath(SQLITE_CONFIG.get("path", "polymarket_realtime.db"))
SQLITE_PATH_FAVOURITE = SQLITE_PATH
STRATEGY_MONITORING_STREAM = "StrategyMonitoring"

MAX_SPREAD_CENTS = config["filters"]["max_spread_cents"]
MIN_DEPTH_1C_USD = config["filters"]["min_depth_1c_usd"]

# High prob thresholds
HP_MIN_YES = config["filters"]["high_prob"]["min_yes_prob"]
HP_MAX_YES = config["filters"]["high_prob"]["max_yes_prob"]

# Low prob thresholds
LP_MIN_YES = config["filters"]["low_prob"]["min_yes_prob"]
LP_MAX_YES = config["filters"]["low_prob"]["max_yes_prob"]

# Holdings & Strategy Monitoring config
HOLDINGS_WALLETS = config.get("holdings", {}).get("wallet_addresses", [])
HOLDINGS_DATA_API = config.get("holdings", {}).get("data_api", "https://data-api.polymarket.com")
_strategy_monitoring_cfg = config.get("strategy_monitoring", {})
STRATEGY_MONITORING_CONDITION_IDS = _strategy_monitoring_cfg.get("condition_ids", [])
STRATEGY_MONITORING_DB_CFG = _strategy_monitoring_cfg.get("monitoring_db", {})
STRATEGY_MONITORING_ENABLED = bool(STRATEGY_MONITORING_DB_CFG.get("enabled", False))
STRATEGY_MONITORING_PATH = _config_abspath(STRATEGY_MONITORING_DB_CFG.get("path", "PolyMarketMonitoring.db"))
STRATEGY_MONITORING_TABLE = STRATEGY_MONITORING_DB_CFG.get("table", "strategy_registry")
STRATEGY_MONITORING_POLL = int(STRATEGY_MONITORING_DB_CFG.get("poll_interval_sec", 60))

try:
    from pathlib import Path as _Path

    from services.config_loader import BASE_DIR as _BASE_DIR
    from services.config_loader import get_market_realtime_db_path as _get_market_realtime_db_path
    from services.config_loader import load_web_settings as _load_web_settings

    _wset = _load_web_settings()
    _rt_p = str(_get_market_realtime_db_path(_wset) or "").strip()
    if _rt_p:
        _rt = _Path(_rt_p).expanduser()
        if not _rt.is_absolute():
            _rt = _BASE_DIR / _rt
        SQLITE_PATH = str(_rt.resolve())
        SQLITE_PATH_FAVOURITE = SQLITE_PATH
    _mon_p = str(_wset.get("strategy_monitoring_db_path") or "").strip()
    if _mon_p:
        _p = _Path(_mon_p).expanduser()
        if not _p.is_absolute():
            _p = _BASE_DIR / _p
        STRATEGY_MONITORING_PATH = str(_p.resolve())
        STRATEGY_MONITORING_ENABLED = True
    _mon_t = str(_wset.get("strategy_monitoring_table") or "").strip()
    if _mon_t:
        STRATEGY_MONITORING_TABLE = _resolve_existing_monitoring_table(STRATEGY_MONITORING_PATH, _mon_t)
except Exception:
    pass

for _db_path in {SQLITE_PATH, SQLITE_PATH_FAVOURITE, STRATEGY_MONITORING_PATH}:
    _ensure_sqlite_file(_db_path)

# WebSocket config
WS_URL = config["websocket"]["url"]
WS_RECONNECT_DELAY = config["websocket"]["reconnect_delay_sec"]
WS_MAX_RECONNECT_DELAY = config["websocket"]["max_reconnect_delay_sec"]
WS_PING_INTERVAL = config["websocket"]["ping_interval_sec"]
WS_STATS_INTERVAL = config["websocket"]["stats_interval_sec"]

WS_MAX_TOKENS_PER_WS = config["websocket"].get("max_tokens_per_ws", 50)
WS_WORKER_COUNT = config["websocket"].get("worker_count", 4)
WS_QUEUE_MAX_SIZE = config["websocket"].get("queue_max_size", 50000)
WS_WATCHDOG_INTERVAL = config["websocket"].get("watchdog_interval_sec", 1800)
WS_OPEN_TIMEOUT = int(config["websocket"].get("open_timeout_sec", 20))
WS_CONNECT_STAGGER_SEC = float(config["websocket"].get("connect_stagger_sec", 1.2))
WS_MAX_CONCURRENT_HANDSHAKES = max(1, int(config["websocket"].get("max_concurrent_handshakes", 2)))

session = requests.Session()
session.headers.update({"User-Agent": "polymarket_datatube/1.0"})
session.trust_env = True  # 允许读取环境变量中的代理配置 (解决 timeout 问题)

# ============================================================
# Phase 1: 全量拉取活跃市场 (不变)
# ============================================================
def fetch_all_active_markets():
    """使用 Gamma API 极速拉取所有活跃的 Events 及其附属的 Markets，抓取分类 (10 并发提速)"""
    print("[*] 开始全盘拉取所有活跃事件及市场 (10 并发)，并提取类别 Category，请稍候...")
    start_t = time.time()
    all_markets = []
    
    def fetch_page(offset):
        params = {
            "limit": 500,
            "offset": offset,
            "closed": "false",
            "active": "true"
        }
        for attempt in range(3):
            try:
                r = session.get(f"{GAMMA_API}/events", params=params, timeout=10)
                if r.status_code == 200:
                    batch = r.json()
                    data = batch.get("data", batch) if isinstance(batch, dict) else batch
                    if isinstance(data, list):
                        return data
                time.sleep(1)
            except Exception:
                time.sleep(1)
        return []

    offsets = [i * 500 for i in range(30)]
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_page, offset): offset for offset in offsets}
        for future in tqdm(as_completed(futures), total=len(futures), desc="获取活跃事件分页"):
            events_data = future.result()
            if events_data:
                for event in events_data:
                    tags = event.get("tags", [])
                    event_category = tags[0].get("label", "Unknown") if tags else "Unknown"
                    
                    market_list = event.get("markets", [])
                    for m in market_list:
                        if m.get("closed") or not m.get("active"):
                            continue
                        m["category"] = event_category
                        all_markets.append(m)
                
    unique_markets = {m.get("conditionId", str(random.random())): m for m in all_markets}.values()
            
    print(f"[*] 拉取完成，合并得到共计 {len(unique_markets)} 个活跃市场，耗时 {time.time()-start_t:.1f} 秒\n")
    return list(unique_markets)

# ============================================================
# Phase 2: 本地概率过滤 (不变)
# ============================================================
def _append_no_outcome_candidate_for_binary_market(
    market: dict,
    outcomes,
    outcome_prices,
    clob_token_ids,
    candidates_dest: list,
) -> None:
    """CLOB WS 按 token 推送；高/低概率筛的是 Yes 侧，需同时订 No 侧才有双边落库与 outcome_side=no。"""
    if not isinstance(outcomes, list) or not isinstance(clob_token_ids, list):
        return
    no_idx = None
    for k, name in enumerate(outcomes):
        if str(name).strip().lower() == "no":
            no_idx = k
            break
    if no_idx is None or len(clob_token_ids) <= no_idx:
        return
    no_token = str(clob_token_ids[no_idx] or "").strip()
    if not no_token:
        return
    price_str = (
        outcome_prices[no_idx]
        if isinstance(outcome_prices, list) and len(outcome_prices) > no_idx
        else None
    )
    try:
        no_price = float(price_str) if price_str is not None else 0.0
    except Exception:
        no_price = 0.0
    candidates_dest.append(
        {
            "market": market,
            "target_option": {"name": "no", "clobTokenId": no_token},
            "target_price": no_price,
        }
    )


def local_probability_filter(markets):
    """根据本地赔率初筛，分成高胜率和低胜率两股暗流，并剥离冗余字段"""
    print("[*] 开始本地计算初筛与双轨分流...")
    start_t = time.time()
    candidates_high = []
    candidates_low = []
    
    for m in markets:
        m.pop('image', None)
        m.pop('icon', None)
        
        outcomes_str = m.get("outcomes", "[]")
        outcomePrices_str = m.get("outcomePrices", "[]")
        clobTokenIds_str = m.get("clobTokenIds", "[]")
        
        try:
            outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
            outcomePrices = json.loads(outcomePrices_str) if isinstance(outcomePrices_str, str) else outcomePrices_str
            clobTokenIds = json.loads(clobTokenIds_str) if isinstance(clobTokenIds_str, str) else clobTokenIds_str
        except Exception:
            continue
            
        if not isinstance(outcomes, list): continue
        
        for k, name in enumerate(outcomes):
            name = str(name).strip().lower()
            if name != "yes": continue
            
            price_str = outcomePrices[k] if isinstance(outcomePrices, list) and len(outcomePrices) > k else None
            token_id = clobTokenIds[k] if isinstance(clobTokenIds, list) and len(clobTokenIds) > k else None
            
            if price_str is None or token_id is None: continue
            try:
                price = float(price_str)
            except Exception:
                continue
            
            payload = {
                "market": m,
                "target_option": {"name": name, "clobTokenId": token_id},
                "target_price": price
            }
            
            if HP_MIN_YES <= price <= HP_MAX_YES:
                candidates_high.append(payload)
                _append_no_outcome_candidate_for_binary_market(
                    m, outcomes, outcomePrices, clobTokenIds, candidates_high
                )
            elif LP_MIN_YES <= price <= LP_MAX_YES:
                candidates_low.append(payload)
                _append_no_outcome_candidate_for_binary_market(
                    m, outcomes, outcomePrices, clobTokenIds, candidates_low
                )
                
    print(f"[*] 概率过滤完成: 高胜率({HP_MIN_YES}-{HP_MAX_YES})存活 {len(candidates_high)} 个 | 低胜率({LP_MIN_YES}-{LP_MAX_YES})存活 {len(candidates_low)} 个。耗时 {time.time()-start_t:.3f} 秒\n")
    return candidates_high, candidates_low

# ============================================================
# Phase 2.5: 持仓拉取 & 收藏加载 (无概率过滤)
# ============================================================
def fetch_my_holdings(all_markets):
    """从 data-api 自动拉取钱包持仓，与全量市场匹配，构造 candidate 列表（无概率过滤）"""
    if not HOLDINGS_WALLETS:
        print("[*] 未配置钱包地址，跳过持仓拉取。")
        return []
    
    print(f"[*] 开始拉取持仓数据 ({len(HOLDINGS_WALLETS)} 个钱包地址)...")
    start_t = time.time()
    
    # 收集所有钱包的持仓 token_ids
    holding_token_ids = set()
    for addr in HOLDINGS_WALLETS:
        try:
            url = f"{HOLDINGS_DATA_API}/positions?user={addr}&sizeThreshold=0"
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                positions = data if isinstance(data, list) else data.get("data", [])
                for pos in positions:
                    # 提取 token_id（兼容多种字段名）
                    tid = pos.get("asset") or pos.get("token_id") or pos.get("tokenId") or pos.get("clobTokenId", "")
                    if tid:
                        holding_token_ids.add(str(tid))
                print(f"  钱包 {addr[:10]}...{addr[-6:]}: 发现 {len(positions)} 个持仓")
            else:
                print(f"  [!] 钱包 {addr[:10]}...{addr[-6:]} 请求失败: HTTP {r.status_code}")
        except Exception as e:
            print(f"  [!] 钱包 {addr[:10]}...{addr[-6:]} 拉取异常: {e}")
    
    if not holding_token_ids:
        print(f"[*] 未发现任何持仓，耗时 {time.time()-start_t:.1f} 秒")
        return []
    
    # 构建 clobTokenId -> market 的反向映射 (从全量市场中)
    token_to_market = {}
    for m in all_markets:
        clobTokenIds_str = m.get("clobTokenIds", "[]")
        outcomePrices_str = m.get("outcomePrices", "[]")
        outcomes_str = m.get("outcomes", "[]")
        try:
            clobTokenIds = json.loads(clobTokenIds_str) if isinstance(clobTokenIds_str, str) else clobTokenIds_str
            outcomePrices = json.loads(outcomePrices_str) if isinstance(outcomePrices_str, str) else outcomePrices_str
            outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
        except Exception:
            continue
        if not isinstance(clobTokenIds, list):
            continue
        for k, tid in enumerate(clobTokenIds):
            token_to_market[str(tid)] = {
                "market": m,
                "outcome_index": k,
                "outcomes": outcomes,
                "outcomePrices": outcomePrices
            }
    
    # 匹配持仓到 market，构造 candidate
    candidates = []
    for tid in holding_token_ids:
        info = token_to_market.get(tid)
        if not info:
            continue
        m = info["market"]
        k = info["outcome_index"]
        outcomes = info["outcomes"]
        outcomePrices = info["outcomePrices"]
        
        name = str(outcomes[k]).strip().lower() if isinstance(outcomes, list) and len(outcomes) > k else "unknown"
        price_str = outcomePrices[k] if isinstance(outcomePrices, list) and len(outcomePrices) > k else "0"
        try:
            price = float(price_str)
        except Exception:
            price = 0
        
        m.pop('image', None)
        m.pop('icon', None)
        candidates.append({
            "market": m,
            "target_option": {"name": name, "clobTokenId": tid},
            "target_price": price
        })
    
    print(f"[*] 持仓匹配完成: 匹配到 {len(candidates)} 个活跃市场 (共 {len(holding_token_ids)} 个持仓 token)。耗时 {time.time()-start_t:.1f} 秒\n")
    return candidates


def _fav_cell_str(v):
    if v is None:
        return ""
    s = str(v).strip()
    if not s or s.lower() == "none":
        return ""
    return s


def _parse_fav_monitoring_row(r):
    """解析监控表一行: condition_id / yes_token / no_token 三者至少一个有效即可。"""
    if not r:
        return None
    lookup_cid = _fav_cell_str(r[0]) if len(r) > 0 else ""
    yes_t = _fav_cell_str(r[1]) if len(r) > 1 else ""
    no_t = _fav_cell_str(r[2]) if len(r) > 2 else ""
    if not lookup_cid and not yes_t and not no_t:
        return None
    if lookup_cid:
        display_cid = lookup_cid
    elif yes_t:
        display_cid = f"fav:yes:{yes_t}"
    else:
        display_cid = f"fav:no:{no_t}"
    lk = lookup_cid or None
    return lk, display_cid, yes_t, no_t


def _gamma_outcome_index(outcomes, name_lower):
    if not isinstance(outcomes, list):
        return None
    for k, name in enumerate(outcomes):
        if str(name).strip().lower() == name_lower:
            return k
    return None


def _resolve_binary_sibling_token(all_markets, token_id, sibling_side_lower):
    """从全量活跃市场里，找到与 token_id 同市场的另一 outcome（yes/no）及价格。"""
    tid = str(token_id or "").strip()
    want = str(sibling_side_lower or "").strip().lower()
    if not tid or want not in {"yes", "no"}:
        return None, None, None
    for m in all_markets:
        clob_str = m.get("clobTokenIds", "[]")
        outcomes_str = m.get("outcomes", "[]")
        outcome_prices_str = m.get("outcomePrices", "[]")
        try:
            clob_ids = json.loads(clob_str) if isinstance(clob_str, str) else clob_str
            outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
            outcome_prices = (
                json.loads(outcome_prices_str) if isinstance(outcome_prices_str, str) else outcome_prices_str
            )
        except Exception:
            continue
        if not isinstance(clob_ids, list) or not isinstance(outcomes, list):
            continue
        for k, cid_t in enumerate(clob_ids):
            if str(cid_t).strip() != tid:
                continue
            want_idx = _gamma_outcome_index(outcomes, want)
            if want_idx is None or want_idx >= len(clob_ids):
                return None, None, None
            sib_tok = str(clob_ids[want_idx]).strip()
            pr = outcome_prices[want_idx] if isinstance(outcome_prices, list) and len(outcome_prices) > want_idx else 0.5
            try:
                prf = float(pr)
            except Exception:
                prf = 0.5
            return dict(m), sib_tok, prf
    return None, None, None


def _resolve_market_by_token(all_markets, token_id):
    """从全量活跃市场里，根据任一 token 反查真实 market。"""
    tid = str(token_id or "").strip()
    if not tid:
        return None
    for m in all_markets:
        clob_str = m.get("clobTokenIds", "[]")
        try:
            clob_ids = json.loads(clob_str) if isinstance(clob_str, str) else clob_str
        except Exception:
            continue
        if not isinstance(clob_ids, list):
            continue
        for item in clob_ids:
            if str(item).strip() == tid:
                return dict(m)
    return None


def _strategy_monitoring_fingerprint():
    """用于检测策略监控库是否变化（有效行的 display_cid / yes / no 集合）。"""
    if not STRATEGY_MONITORING_ENABLED:
        return None
    if not os.path.isfile(STRATEGY_MONITORING_PATH):
        return ()
    try:
        conn = sqlite3.connect(STRATEGY_MONITORING_PATH, check_same_thread=False)
        q = f'SELECT condition_id, yes_token, no_token, question FROM "{STRATEGY_MONITORING_TABLE}"'
        rows = conn.execute(q).fetchall()
        conn.close()
        out = []
        for r in rows:
            p = _parse_fav_monitoring_row(r)
            if not p:
                continue
            _lk, display_cid, yes_t, no_t = p
            out.append((display_cid, yes_t, no_t))
        return tuple(sorted(out))
    except Exception as e:
        print(f"[!] 读取策略监控库失败 ({STRATEGY_MONITORING_PATH}): {e}")
        return ()


def load_strategy_monitoring_from_db(all_markets, skip_tokens=None):
    """从策略监控表读取 condition_id / yes_token / no_token，并尽量展开出 Yes/No 两边 token。"""
    if not STRATEGY_MONITORING_ENABLED:
        return []
    if not os.path.isfile(STRATEGY_MONITORING_PATH):
        print(f"[*] 策略监控库文件不存在，跳过: {STRATEGY_MONITORING_PATH}")
        return []

    skip_tokens = {str(token).strip() for token in (skip_tokens or []) if str(token).strip()}
    cond_to_market = {}
    for m in all_markets:
        cid = m.get("conditionId", "")
        if cid:
            cond_to_market[cid] = m

    # --- NEW PATH: read tokens from strategy_registry + strategy_legs ---
    try:
        from services import strategy_data_source as _sds
        _token_rows = _sds.get_all_tokens()
    except Exception:
        _token_rows = []
    if _token_rows:
        rows = [(r.get("condition_id", ""), r.get("yes_token"), r.get("no_token"), r.get("strategy_name", "")) for r in _token_rows]
    else:
        # --- LEGACY PATH ---
        try:
            conn = sqlite3.connect(STRATEGY_MONITORING_PATH, check_same_thread=False)
            q = f'SELECT condition_id, yes_token, no_token, question FROM "{STRATEGY_MONITORING_TABLE}"'
            rows = conn.execute(q).fetchall()
            conn.close()
        except Exception as e:
            print(f"[!] 查询策略监控表失败 ({STRATEGY_MONITORING_TABLE}): {e}")
            return []

    candidates = []
    seen_entries = set()
    seen_tokens = set(skip_tokens)
    n_rows = len(rows)
    n_skip = 0
    n_synthetic = 0
    n_cid_only_unresolved = 0

    def _append_candidate(market_obj, side_name, token_id, price):
        token_text = str(token_id or "").strip()
        side_text = str(side_name or "").strip().lower()
        if not token_text or side_text not in {"yes", "no"}:
            return
        if token_text in seen_tokens:
            return
        seen_tokens.add(token_text)
        try:
            price_num = float(price)
        except Exception:
            price_num = 0.5
        candidates.append({
            "market": market_obj,
            "target_option": {"name": side_text, "clobTokenId": token_text},
            "target_price": price_num,
        })

    for r in rows:
        p = _parse_fav_monitoring_row(r)
        if not p:
            n_skip += 1
            continue
        lookup_cid, display_cid, yes_t, no_t = p
        entry_key = (display_cid, yes_t, no_t)
        if entry_key in seen_entries:
            continue
        seen_entries.add(entry_key)
        if not lookup_cid:
            n_synthetic += 1

        m_src = cond_to_market.get(lookup_cid) if lookup_cid else None
        if m_src:
            m = dict(m_src)
            m.pop("image", None)
            m.pop("icon", None)
            outcomes_str = m.get("outcomes", "[]")
            outcome_prices_str = m.get("outcomePrices", "[]")
            clob_str = m.get("clobTokenIds", "[]")
            try:
                outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
                outcome_prices = json.loads(outcome_prices_str) if isinstance(outcome_prices_str, str) else outcome_prices_str
                clob_token_ids = json.loads(clob_str) if isinstance(clob_str, str) else clob_str
            except Exception:
                continue
            if not isinstance(outcomes, list):
                continue

            gamma_yes_idx = _gamma_outcome_index(outcomes, "yes")
            gamma_no_idx = _gamma_outcome_index(outcomes, "no")
            gamma_yes_token = clob_token_ids[gamma_yes_idx] if gamma_yes_idx is not None and isinstance(clob_token_ids, list) and len(clob_token_ids) > gamma_yes_idx else None
            gamma_no_token = clob_token_ids[gamma_no_idx] if gamma_no_idx is not None and isinstance(clob_token_ids, list) and len(clob_token_ids) > gamma_no_idx else None
            gamma_yes_price = outcome_prices[gamma_yes_idx] if gamma_yes_idx is not None and isinstance(outcome_prices, list) and len(outcome_prices) > gamma_yes_idx else 0.5
            gamma_no_price = outcome_prices[gamma_no_idx] if gamma_no_idx is not None and isinstance(outcome_prices, list) and len(outcome_prices) > gamma_no_idx else 0.5

            if yes_t or gamma_yes_token:
                _append_candidate(m, "yes", yes_t or gamma_yes_token, gamma_yes_price)
            if no_t or gamma_no_token:
                _append_candidate(m, "no", no_t or gamma_no_token, gamma_no_price)
        else:
            if not yes_t and not no_t:
                n_cid_only_unresolved += 1
                continue
            matched_market = _resolve_market_by_token(all_markets, yes_t or no_t)
            if matched_market:
                matched_market.pop("image", None)
                matched_market.pop("icon", None)
                outcomes_str = matched_market.get("outcomes", "[]")
                outcome_prices_str = matched_market.get("outcomePrices", "[]")
                clob_str = matched_market.get("clobTokenIds", "[]")
                try:
                    outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
                    outcome_prices = json.loads(outcome_prices_str) if isinstance(outcome_prices_str, str) else outcome_prices_str
                    clob_token_ids = json.loads(clob_str) if isinstance(clob_str, str) else clob_str
                except Exception:
                    outcomes = []
                    outcome_prices = []
                    clob_token_ids = []
                gamma_yes_idx = _gamma_outcome_index(outcomes, "yes")
                gamma_no_idx = _gamma_outcome_index(outcomes, "no")
                gamma_yes_token = clob_token_ids[gamma_yes_idx] if gamma_yes_idx is not None and isinstance(clob_token_ids, list) and len(clob_token_ids) > gamma_yes_idx else None
                gamma_no_token = clob_token_ids[gamma_no_idx] if gamma_no_idx is not None and isinstance(clob_token_ids, list) and len(clob_token_ids) > gamma_no_idx else None
                gamma_yes_price = outcome_prices[gamma_yes_idx] if gamma_yes_idx is not None and isinstance(outcome_prices, list) and len(outcome_prices) > gamma_yes_idx else 0.5
                gamma_no_price = outcome_prices[gamma_no_idx] if gamma_no_idx is not None and isinstance(outcome_prices, list) and len(outcome_prices) > gamma_no_idx else 0.5
                if yes_t or gamma_yes_token:
                    _append_candidate(matched_market, "yes", yes_t or gamma_yes_token, gamma_yes_price)
                if no_t or gamma_no_token:
                    _append_candidate(matched_market, "no", no_t or gamma_no_token, gamma_no_price)
                continue

            cid = ""
            fallback_key = display_cid
            q_mon = r[3] if len(r) > 3 and r[3] is not None else None
            q_str = str(q_mon).strip() if q_mon else ""
            title = q_str if q_str else (f"[监控库] {fallback_key[:18]}…" if len(fallback_key) > 18 else f"[监控库] {fallback_key}")
            if yes_t and no_t:
                clob_ids = [yes_t, no_t]
            elif yes_t:
                clob_ids = [yes_t]
            else:
                clob_ids = [no_t]
            m = {
                "conditionId": cid,
                "question": title,
                "category": STRATEGY_MONITORING_STREAM,
                "outcomes": "[\"Yes\", \"No\"]",
                "outcomePrices": "[\"0.5\", \"0.5\"]",
                "clobTokenIds": json.dumps(clob_ids),
                "active": True,
                "closed": False,
            }
            if yes_t:
                _append_candidate(m, "yes", yes_t, 0.5)
            if no_t:
                _append_candidate(m, "no", no_t, 0.5)
            if yes_t and not no_t:
                m2, no_r, p_no = _resolve_binary_sibling_token(all_markets, yes_t, "no")
                if m2 and no_r:
                    m2.pop("image", None)
                    m2.pop("icon", None)
                    _append_candidate(m2, "no", no_r, p_no)
            if no_t and not yes_t:
                m2, yes_r, p_yes = _resolve_binary_sibling_token(all_markets, no_t, "yes")
                if m2 and yes_r:
                    m2.pop("image", None)
                    m2.pop("icon", None)
                    _append_candidate(m2, "yes", yes_r, p_yes)

    print(
        f"[*] 策略监控库扫描: 表 {STRATEGY_MONITORING_TABLE} 共 {n_rows} 行, "
        f"跳过(三者皆空) {n_skip}, 无 condition_id 用 token 占位 {n_synthetic}, "
        f"仅 condition_id 且未匹配 Gamma {n_cid_only_unresolved}, 有效订阅 {len(candidates)} 条"
    )
    return candidates


def load_strategy_monitoring_candidates(all_markets):
    """从配置与策略监控库合并加载监控候选，构造 candidate 列表（无概率过滤）。"""
    start_t = time.time()
    candidates = []
    seen_tokens = set()

    cond_to_market = {}
    for m in all_markets:
        cid = m.get("conditionId", "")
        if cid:
            cond_to_market[cid] = m

    if STRATEGY_MONITORING_CONDITION_IDS:
        print(f"[*] 开始加载策略监控市场 (config {len(STRATEGY_MONITORING_CONDITION_IDS)} 个 conditionId)...")
        not_found = []
        for cid in STRATEGY_MONITORING_CONDITION_IDS:
            m0 = cond_to_market.get(cid)
            if not m0:
                not_found.append(cid)
                continue
            m = dict(m0)
            m.pop("image", None)
            m.pop("icon", None)

            outcomes_str = m.get("outcomes", "[]")
            outcomePrices_str = m.get("outcomePrices", "[]")
            clobTokenIds_str = m.get("clobTokenIds", "[]")
            try:
                outcomes = json.loads(outcomes_str) if isinstance(outcomes_str, str) else outcomes_str
                outcomePrices = json.loads(outcomePrices_str) if isinstance(outcomePrices_str, str) else outcomePrices_str
                clobTokenIds = json.loads(clobTokenIds_str) if isinstance(clobTokenIds_str, str) else clobTokenIds_str
            except Exception:
                continue

            if not isinstance(outcomes, list):
                continue

            for k, name in enumerate(outcomes):
                name_lower = str(name).strip().lower()
                if name_lower != "yes":
                    continue
                price_str = outcomePrices[k] if isinstance(outcomePrices, list) and len(outcomePrices) > k else "0"
                token_id = clobTokenIds[k] if isinstance(clobTokenIds, list) and len(clobTokenIds) > k else None
                if token_id is None:
                    continue
                try:
                    price = float(price_str)
                except Exception:
                    price = 0
                token_text = str(token_id).strip()
                if not token_text or token_text in seen_tokens:
                    continue
                seen_tokens.add(token_text)
                candidates.append({
                    "market": m,
                    "target_option": {"name": name_lower, "clobTokenId": token_text},
                    "target_price": price,
                })
                no_idx = _gamma_outcome_index(outcomes, "no")
                if (
                    no_idx is not None
                    and isinstance(clobTokenIds, list)
                    and len(clobTokenIds) > no_idx
                ):
                    no_tid = str(clobTokenIds[no_idx] or "").strip()
                    if no_tid and no_tid not in seen_tokens:
                        seen_tokens.add(no_tid)
                        no_ps = (
                            outcomePrices[no_idx]
                            if isinstance(outcomePrices, list) and len(outcomePrices) > no_idx
                            else "0"
                        )
                        try:
                            no_price = float(no_ps)
                        except Exception:
                            no_price = 0.0
                        candidates.append({
                            "market": m,
                            "target_option": {"name": "no", "clobTokenId": no_tid},
                            "target_price": no_price,
                        })
                break

        if not_found:
            print(f"  [!] {len(not_found)} 个策略监控 conditionId 未找到匹配的活跃市场")

    extra = load_strategy_monitoring_from_db(all_markets, skip_tokens=seen_tokens)
    candidates.extend(extra)

    if not candidates:
        if STRATEGY_MONITORING_CONDITION_IDS or STRATEGY_MONITORING_ENABLED:
            print("[*] 无有效策略监控条目（config 未匹配或监控库无可用行）。")
        else:
            print("[*] 未配置策略监控列表且未启用监控库，跳过监控加载。")
        return []

    print(f"[*] 策略监控加载完成: 共 {len(candidates)} 个（config + 监控库）。耗时 {time.time()-start_t:.3f} 秒\n")
    return candidates


# ============================================================
# 深度计算 (复用原逻辑)
# ============================================================
def compute_1c_depth(book, current_price=None):
    """计算一美分宽度内的流动性"""
    if not book: return None
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    
    def _parse(li):
        res = []
        for x in li:
            if isinstance(x, dict): res.append((float(x["price"]), float(x["size"])))
            elif isinstance(x, list) and len(x) >= 2: res.append((float(x[0]), float(x[1])))
        return res
    bids, asks = _parse(bids), _parse(asks)
    
    best_bid = max([p for p, s in bids]) if bids else None
    best_ask = min([p for p, s in asks]) if asks else None
    
    if best_bid is None or best_ask is None: return None
    
    spread_c = (best_ask - best_bid) * 100.0
    
    mid = 0.5 * (best_bid + best_ask)
    band = min(0.01, 0.1 * mid, 0.1 * (1.0 - mid))
    band_c_used = round(band * 100.0, 2)
    
    th_bid = max(0.001, best_bid - band)
    th_ask = min(0.999, best_ask + band)
    
    depth_bid_usd = sum([p*s for p,s in bids if p >= th_bid])
    depth_ask_usd = sum([p*s for p,s in asks if p <= th_ask])
    
    depth_ask_qty = sum([s for p,s in asks if p <= th_ask])
    depth_bid_qty = sum([s for p,s in bids if p >= th_bid])
    
    n_bid_1c = sum([1 for p,s in bids if p >= th_bid])
    n_ask_1c = sum([1 for p,s in asks if p <= th_ask])
    
    vwap_bid = (depth_bid_usd/depth_bid_qty) if depth_bid_qty > 0 else 0
    vwap_ask = (depth_ask_usd/depth_ask_qty) if depth_ask_qty > 0 else 0
    
    return {
        "now_bid": best_bid, "now_ask": best_ask,
        "best_bid": best_bid, "best_ask": best_ask, "spread_c": spread_c,
        "depth_bid_usd": depth_bid_usd, "depth_ask_usd": depth_ask_usd,
        "depth_ask_qty": depth_ask_qty, "depth_bid_qty": depth_bid_qty,
        "band_c_used": band_c_used, "vwap_bid": vwap_bid, "vwap_ask": vwap_ask,
        "n_bid_1c": n_bid_1c, "n_ask_1c": n_ask_1c
    }

# ============================================================
# Phase 3 (新): WebSocket 实时监控
# ============================================================
class WebSocketMonitor:
    """通过 CLOB WebSocket 对 Phase 2 筛出的市场进行实时订单簿监控"""
    
    def __init__(
        self,
        candidates_high,
        candidates_low,
        candidates_holding=None,
        candidates_strategy_monitoring=None,
        enable_probability_scan=True,
    ):
        # 构建 token_id -> market_info 的映射
        # 单标签优先级：先写低优先级，后写高优先级自动覆盖
        # LowProb < HighProb < StrategyMonitoring < MyHolding
        self.token_map = {}  # token_id -> {market, target_option, target_price, stream}
        self._build_token_map(candidates_low, "LowProb")
        self._build_token_map(candidates_high, "HighProb")
        self._build_token_map(candidates_strategy_monitoring or [], STRATEGY_MONITORING_STREAM)
        self._build_token_map(candidates_holding or [], "MyHolding")
        
        # 统计
        self.msg_count = 0
        self.update_count = 0
        self.start_time = None
        self.last_stats_time = None
        self.last_msg_at = None
        self.last_update_at = None
        self.last_subscribe_at = None
        self.last_ws_error = None
        self.shard_connected = {}
        
        # MongoDB
        self.mongo_enabled = MONGO_ENABLED
        self.mongo_client = None
        self.db = None

        # SQLite（所有实时市场统一落入同一实时库）
        self.sqlite_enabled = SQLITE_ENABLED
        self.sqlite_path_regular = SQLITE_PATH
        self.sqlite_conn = None
        self.sqlite_lock = threading.Lock()
        self._sqlite_schema_ready = set()
        self._markets_snapshot = None
        
        # 运行控制
        self._running = True
        self.enable_probability_scan = bool(enable_probability_scan)
        
        # 队列和任务管理
        self.msg_queue = asyncio.Queue(maxsize=WS_QUEUE_MAX_SIZE)
        self.control_queues = {} # shard_id -> asyncio.Queue for subscribe/unsubscribe
        self.shard_tokens = {} # shard_id -> set(token_id), used to resubscribe after reconnect
        self.tasks = []
        self.connect_semaphore = None
        
        # 本地订单簿状态 (Memory Ledger)
        self.local_orderbooks = {} # token_id -> {"bids": {price: size}, "asks": {price: size}}
    
    def _build_token_map(self, candidates, stream_name):
        """从候选列表中提取 token_id 并映射到市场信息"""
        for c in candidates:
            token_id = str(c["target_option"].get("clobTokenId", ""))
            if not token_id:
                continue
            self.token_map[token_id] = {
                "market": c["market"],
                "target_option": c["target_option"],
                "target_price": c["target_price"],
                "stream": stream_name,
                "condition_id": c["market"].get("conditionId", ""),
                "question": c["market"].get("question", "未知市场"),
            }
    
    def _init_mongo(self):
        """初始化 MongoDB 连接，为所有集合创建索引"""
        if not self.mongo_enabled:
            print("[*] MongoDB 已禁用，跳过初始化")
            return

        try:
            self.mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            self.db = self.mongo_client[DB_NAME]
            
            print(f"[*] 正在清空数据库 {DB_NAME} 中的所有历史数据...")
            for col_name in self.db.list_collection_names():
                self.db[col_name].drop()
            print("[*] 历史数据已清理，重新构建索引...")
            
            # 为所有实时监控状态集合 (Table A) 建立唯一索引
            for col_name in ["HighProb_Markets", "LowProb_Markets", "MyHolding_Markets", f"{STRATEGY_MONITORING_STREAM}_Markets"]:
                col = self.db[col_name]
                col.create_index("condition_id", unique=True, sparse=True)
                
            # 为所有流水集合 (Table B) 建立时间序列/普通索引
            for col_name in ["HighProb" + DELTAS_SUFFIX, "LowProb" + DELTAS_SUFFIX, "MyHolding" + DELTAS_SUFFIX, STRATEGY_MONITORING_STREAM + DELTAS_SUFFIX]:
                col = self.db[col_name]
                col.create_index([("condition_id", 1), ("timestamp", 1)])
            
            print(f"[*] MongoDB 连接成功 ({MONGO_URI}), 数据库: {DB_NAME}")
        except Exception as e:
            print(f"[!] MongoDB 连接失败: {e}")
            self.db = None

    def _sqlite_init_schema(self, conn):
        cur = conn.cursor()
        tables = {str(row[0]) for row in cur.execute("select name from sqlite_master where type='table' order by name").fetchall()}
        if "markets_state" in tables:
            cols = cur.execute('PRAGMA table_info("markets_state")').fetchall()
            col_names = {str(row[1]) for row in cols}
            pk_cols = [str(row[1]) for row in cols if int(row[5] or 0) > 0]
            if "clobTokenId" not in col_names or pk_cols != ["clobTokenId"]:
                backup_name = f"markets_state_legacy_{int(time.time())}"
                cur.execute(f'ALTER TABLE "markets_state" RENAME TO "{backup_name}"')
                conn.commit()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS markets_state (
            clobTokenId TEXT PRIMARY KEY,
            condition_id TEXT,
            stream TEXT,
            question TEXT,
            category TEXT,
            target_option_json TEXT,
            target_price REAL,
            market_json TEXT,
            raw_clob_json TEXT,
            depth_metrics_json TEXT,
            updated_at_utc TEXT,
            status TEXT,
            outcome_side TEXT,
            now_bid REAL,
            now_ask REAL,
            best_bid REAL,
            best_ask REAL,
            last_price REAL,
            spread_c REAL
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS market_deltas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            stream TEXT,
            condition_id TEXT,
            clobTokenId TEXT,
            event_type TEXT,
            payload_json TEXT,
            reason TEXT,
            outcome_side TEXT,
            now_bid REAL,
            now_ask REAL,
            best_bid REAL,
            best_ask REAL,
            last_price REAL,
            spread_c REAL
        )
        """)
        _sqlite_ensure_flat_columns(conn)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_state_stream ON markets_state(stream)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_state_condition ON markets_state(condition_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deltas_cond_ts ON market_deltas(condition_id, timestamp)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_deltas_token_ts ON market_deltas(clobTokenId, timestamp)")
        conn.commit()

    def _sqlite_ensure_schema_ready(self, conn, force=False):
        if conn is None:
            return
        conn_key = id(conn)
        if force or conn_key not in self._sqlite_schema_ready:
            self._sqlite_init_schema(conn)
            self._sqlite_schema_ready.add(conn_key)

    def _sqlite_write_with_schema_retry(self, conn, writer, error_label):
        try:
            with self.sqlite_lock:
                self._sqlite_ensure_schema_ready(conn)
                writer()
                conn.commit()
            return
        except Exception as e:
            if "no such table" in str(e).lower():
                try:
                    with self.sqlite_lock:
                        self._sqlite_ensure_schema_ready(conn, force=True)
                        writer()
                        conn.commit()
                    print(f"[*] SQLite 缺失表已自动创建并重试成功 ({error_label})")
                    return
                except Exception as retry_e:
                    print(f"[!] {error_label}: {retry_e}")
                    return
            print(f"[!] {error_label}: {e}")

    def _init_sqlite(self):
        """初始化实时市场 SQLite（统一单库）。"""
        if not self.sqlite_enabled:
            print("[*] SQLite 已禁用，跳过初始化")
            return

        try:
            if self.sqlite_path_regular:
                parent = os.path.dirname(os.path.abspath(self.sqlite_path_regular))
                if parent:
                    os.makedirs(parent, exist_ok=True)

            self.sqlite_conn = sqlite3.connect(
                self.sqlite_path_regular,
                check_same_thread=False,
            )
            self.sqlite_conn.execute("PRAGMA journal_mode=WAL;")
            self.sqlite_conn.execute("PRAGMA synchronous=NORMAL;")
            self._sqlite_init_schema(self.sqlite_conn)
            self._sqlite_schema_ready.add(id(self.sqlite_conn))

            print(f"[*] SQLite 常规库: {self.sqlite_path_regular}")
        except Exception as e:
            print(f"[!] SQLite 初始化失败: {e}")
            self.sqlite_conn = None

    def _sqlite_conn_for_stream(self, stream):
        if not self.sqlite_enabled:
            return None
        return self.sqlite_conn
    
    def _upsert_to_mongo(self, token_id, book_data, depth_metrics, is_status_update=False):
        """将实时状态快照 (Table A) upsert 到 MongoDB"""
        if self.db is None:
            return
        
        info = self.token_map.get(token_id)
        if not info:
            return
        
        stream = info.get("stream", "Unknown")
        col_name = f"{stream}_Markets"
        
        # 如果是被 Dropped 的更新
        status = info.get("status", "MONITORING")
        
        doc = {
            "condition_id": info["condition_id"],
            "question": info["question"],
            "category": info["market"].get("category", "Unknown"),
            "target_option": info["target_option"],
            "target_price": info.get("target_price", 0),
            "market": info["market"],
            "raw_clob_json": book_data,
            "depth_metrics": depth_metrics,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": status
        }
        
        try:
            self.db[col_name].update_one(
                {"condition_id": info["condition_id"]},
                {"$set": doc},
                upsert=True
            )
        except Exception as e:
            print(f"[!] MongoDB Table A upsert 失败 ({col_name}): {e}")

    def _upsert_to_sqlite(self, token_id, book_data, depth_metrics, is_status_update=False):
        """将实时状态快照 (Table A) upsert 到 SQLite"""
        info = self.token_map.get(token_id)
        if not info:
            return

        stream = info.get("stream", "Unknown")
        conn = self._sqlite_conn_for_stream(stream)
        if conn is None:
            return

        status = info.get("status", "MONITORING")

        flat = _sqlite_flat_from_depth_metrics(depth_metrics, info, book_data)

        def _writer():
            conn.execute("""
                INSERT INTO markets_state (
                    clobTokenId, condition_id, stream, question, category,
                    target_option_json, target_price, market_json,
                    raw_clob_json, depth_metrics_json, updated_at_utc, status,
                    outcome_side, now_bid, now_ask, best_bid, best_ask, last_price, spread_c
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(clobTokenId) DO UPDATE SET
                    stream=excluded.stream,
                    condition_id=excluded.condition_id,
                    question=excluded.question,
                    category=excluded.category,
                    target_option_json=excluded.target_option_json,
                    target_price=excluded.target_price,
                    market_json=excluded.market_json,
                    raw_clob_json=excluded.raw_clob_json,
                    depth_metrics_json=excluded.depth_metrics_json,
                    updated_at_utc=excluded.updated_at_utc,
                    status=excluded.status,
                    outcome_side=excluded.outcome_side,
                    now_bid=excluded.now_bid,
                    now_ask=excluded.now_ask,
                    best_bid=excluded.best_bid,
                    best_ask=excluded.best_ask,
                    last_price=excluded.last_price,
                    spread_c=excluded.spread_c
                """, (
                    token_id,
                    info["condition_id"],
                    stream,
                    info["question"],
                    info["market"].get("category", "Unknown"),
                    json.dumps(info["target_option"], ensure_ascii=False),
                    float(info.get("target_price", 0) or 0),
                    json.dumps(info["market"], ensure_ascii=False),
                    json.dumps(book_data, ensure_ascii=False),
                    json.dumps(depth_metrics, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                    status,
                    *flat,
                ))

        self._sqlite_write_with_schema_retry(
            conn,
            _writer,
            "SQLite Table A upsert 失败 (markets_state)",
        )

    def _insert_delta_to_mongo(self, token_id, event_type, payload, reason=""):
        """将增量流水变化 (Table B) 插入到 MongoDB"""
        if self.db is None:
            return
            
        info = self.token_map.get(token_id)
        if not info:
            return
            
        stream = info.get("stream", "Unknown")
        col_name = f"{stream}{DELTAS_SUFFIX}"
        
        doc = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "condition_id": info.get("condition_id", ""),
            "clobTokenId": token_id,
            "event_type": event_type,
            "payload": payload,
            "reason": reason
        }
        
        try:
            self.db[col_name].insert_one(doc)
        except Exception as e:
            print(f"[!] MongoDB Table B insert 失败 ({col_name}): {e}")

    def _insert_delta_to_sqlite(self, token_id, event_type, payload, reason=""):
        """将增量流水变化 (Table B) 插入到 SQLite"""
        info = self.token_map.get(token_id)
        if not info:
            return

        stream = info.get("stream", "Unknown")
        conn = self._sqlite_conn_for_stream(stream)
        if conn is None:
            return

        flat = _sqlite_flat_from_delta_payload(payload, info)

        def _writer():
            conn.execute("""
                INSERT INTO market_deltas (
                    timestamp, stream, condition_id, clobTokenId,
                    event_type, payload_json, reason,
                    outcome_side, now_bid, now_ask, best_bid, best_ask, last_price, spread_c
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    stream,
                    info.get("condition_id", ""),
                    token_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    reason,
                    *flat,
                ))

        self._sqlite_write_with_schema_retry(
            conn,
            _writer,
            "SQLite Table B insert 失败 (market_deltas)",
        )

    def _upsert_state(self, token_id, book_data, depth_metrics, is_status_update=False):
        if self.mongo_enabled:
            self._upsert_to_mongo(token_id, book_data, depth_metrics, is_status_update=is_status_update)
        if self.sqlite_enabled:
            self._upsert_to_sqlite(token_id, book_data, depth_metrics, is_status_update=is_status_update)

    def _insert_delta(self, token_id, event_type, payload, reason=""):
        if self.mongo_enabled:
            self._insert_delta_to_mongo(token_id, event_type, payload, reason=reason)
        if self.sqlite_enabled:
            self._insert_delta_to_sqlite(token_id, event_type, payload, reason=reason)
    
    def _print_update(self, token_id, depth_metrics):
        """打印单条实时更新的简要信息"""
        info = self.token_map.get(token_id, {})
        question = info.get("question", "?")[:50]
        stream = info.get("stream", "?")
        
        bid = depth_metrics.get("now_bid", depth_metrics.get("best_bid", 0))
        ask = depth_metrics.get("now_ask", depth_metrics.get("best_ask", 0))
        spread = depth_metrics.get("spread_c", 0)
        depth_b = depth_metrics.get("depth_bid_usd", 0)
        depth_a = depth_metrics.get("depth_ask_usd", 0)
        
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  [{ts}] [{stream}] {question}  |  Bid={bid:.3f} Ask={ask:.3f} Spread={spread:.1f}¢  |  Depth B=${depth_b:.0f} A=${depth_a:.0f}")
    
    def _print_stats(self):
        """打印周期性统计摘要"""
        now = time.time()
        elapsed = now - self.start_time if self.start_time else 0
        mins = elapsed / 60.0
        
        print(f"\n{'='*70}")
        print(f"  [Stats] 监控统计 | 运行时间: {mins:.1f} 分钟 | 监控市场数: {len(self.token_map)}")
        print(f"  [Queue] 收到消息: {self.msg_count} | 有效更新: {self.update_count} | 队列积压: {self.msg_queue.qsize()}")
        
        # 分流统计
        high_count = sum(1 for v in self.token_map.values() if v["stream"] == "HighProb")
        low_count = sum(1 for v in self.token_map.values() if v["stream"] == "LowProb")
        holding_count = sum(1 for v in self.token_map.values() if v["stream"] == "MyHolding")
        strategy_monitoring_count = sum(1 for v in self.token_map.values() if v["stream"] == STRATEGY_MONITORING_STREAM)
        print(f"  [Split] HighProb: {high_count} | LowProb: {low_count} | MyHolding: {holding_count} | {STRATEGY_MONITORING_STREAM}: {strategy_monitoring_count}")
        print(f"{'='*70}\n")
        
        self.last_stats_time = now

    def get_runtime_state(self):
        return {
            "token_count": len(self.token_map),
            "msg_count": self.msg_count,
            "update_count": self.update_count,
            "queue_size": self.msg_queue.qsize() if self.msg_queue else None,
            "last_msg_at": self.last_msg_at,
            "last_update_at": self.last_update_at,
            "last_subscribe_at": self.last_subscribe_at,
            "last_ws_error": self.last_ws_error,
            "shard_connected": dict(self.shard_connected),
            "shard_token_counts": {sid: len(tokens) for sid, tokens in self.shard_tokens.items()},
        }

    def _reconcile_candidates(self, c_low, c_high, c_strategy_monitoring, c_holding):
        """根据四类候选刷新 token_map，并下发 subscribe/unsubscribe（与 Watchdog 逻辑一致）。"""
        new_token_map = {}
        for c in c_low:
            tid = str(c["target_option"].get("clobTokenId", ""))
            if tid:
                new_token_map[tid] = {"c": c, "stream": "LowProb"}
        for c in c_high:
            tid = str(c["target_option"].get("clobTokenId", ""))
            if tid:
                new_token_map[tid] = {"c": c, "stream": "HighProb"}
        for c in c_strategy_monitoring:
            tid = str(c["target_option"].get("clobTokenId", ""))
            if tid:
                new_token_map[tid] = {"c": c, "stream": STRATEGY_MONITORING_STREAM}
        for c in c_holding:
            tid = str(c["target_option"].get("clobTokenId", ""))
            if tid:
                new_token_map[tid] = {"c": c, "stream": "MyHolding"}

        new_set = set(new_token_map.keys())
        old_set = set(self.token_map.keys())

        to_add = list(new_set - old_set)
        to_remove = list(old_set - new_set)

        to_restream = []
        for tid in (new_set & old_set):
            if new_token_map[tid]["stream"] != self.token_map[tid].get("stream"):
                to_restream.append(tid)

        print(f"[*] [Diff] 需下线 {len(to_remove)} 个, 需上线 {len(to_add)} 个, 流变更 {len(to_restream)} 个.")

        for tid in to_restream:
            old_stream = self.token_map[tid].get("stream", "?")
            new_stream = new_token_map[tid]["stream"]
            self.token_map[tid]["stream"] = new_stream
            self._insert_delta(tid, "SYSTEM_RESTREAM", {"old": old_stream, "new": new_stream}, f"Stream changed {old_stream} -> {new_stream}")

        if to_remove:
            if 0 in self.control_queues:
                for i in range(0, len(to_remove), 300):
                    self.control_queues[0].put_nowait({"action": "unsubscribe", "tokens": to_remove[i : i + 300]})

            for tid in to_remove:
                for token_set in self.shard_tokens.values():
                    token_set.discard(tid)
                info = self.token_map.pop(tid, None)
                if info:
                    info["status"] = "DROPPED"
                    self.token_map[tid] = info
                    self._upsert_state(tid, {"info": "dropped"}, {}, is_status_update=True)
                    self._insert_delta(tid, "SYSTEM_REMOVE", {}, "Prob changed out of bound")
                    self.token_map.pop(tid, None)
                    self.local_orderbooks.pop(tid, None)

        if to_add:
            for tid in to_add:
                c_info = new_token_map[tid]["c"]
                stream = new_token_map[tid]["stream"]
                self.token_map[tid] = {
                    "market": c_info["market"],
                    "target_option": c_info["target_option"],
                    "target_price": c_info["target_price"],
                    "stream": stream,
                    "condition_id": c_info["market"].get("conditionId", ""),
                    "question": c_info["market"].get("question", "未知市场"),
                    "status": "MONITORING",
                }
                self._insert_delta(tid, "SYSTEM_ADD", {}, f"Discovered new market ({stream})")

            if 0 in self.control_queues:
                self.shard_tokens.setdefault(0, set()).update(to_add)
                for i in range(0, len(to_add), 300):
                    self.control_queues[0].put_nowait({"action": "subscribe", "tokens": to_add[i : i + 300]})

    async def _run_full_rescan(self):
        """全量拉市场并重算候选（监控库变更或 Watchdog 时调用）。"""
        loop = asyncio.get_running_loop()
        try:
            markets = await loop.run_in_executor(None, fetch_all_active_markets)
            self._markets_snapshot = markets
            if self.enable_probability_scan:
                c_high, c_low = await loop.run_in_executor(None, local_probability_filter, markets)
            else:
                c_high, c_low = [], []
            c_holding = await loop.run_in_executor(None, fetch_my_holdings, markets)
            c_strategy_monitoring = await loop.run_in_executor(None, load_strategy_monitoring_candidates, markets)
            self._reconcile_candidates(c_low, c_high, c_strategy_monitoring, c_holding)
        except Exception as e:
            print(f"[!] 全量重扫失败: {e}")

    async def _strategy_monitoring_poll_loop(self):
        """策略监控库变更时触发全量重扫，实现监控列表近实时同步。"""
        last_fp = None
        await asyncio.sleep(8)
        while self._running:
            if not STRATEGY_MONITORING_ENABLED:
                return
            try:
                loop = asyncio.get_running_loop()
                fp = await loop.run_in_executor(None, _strategy_monitoring_fingerprint)
                if last_fp is None:
                    last_fp = fp
                elif fp != last_fp:
                    print(f"\n[*] [{STRATEGY_MONITORING_STREAM}] 检测到策略监控库变化，触发全量重扫...")
                    last_fp = fp
                    await self._run_full_rescan()
            except Exception as e:
                print(f"[!] 策略监控库轮询异常: {e}")
            await asyncio.sleep(max(5, STRATEGY_MONITORING_POLL))

    async def _worker_loop(self, worker_id):
        """后台消费者协程：从队列获取消息并处理"""
        print(f"[*] Worker {worker_id} 启动")
        while self._running:
            try:
                # 使用 timeout 保证能及时响应 self._running 的变化退出
                raw_msg = await asyncio.wait_for(self.msg_queue.get(), timeout=1.0)
                try:
                    self._process_message(raw_msg)
                except Exception as e:
                    pass
                finally:
                    self.msg_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                pass
        print(f"[*] Worker {worker_id} 已停止")
    
    def _apply_delta_to_orderbook(self, token_id, msg):
        """在内存中维护 Local Orderbook State Machine"""
        if token_id not in self.local_orderbooks:
            self.local_orderbooks[token_id] = {"bids": {}, "asks": {}}
            
        ob = self.local_orderbooks[token_id]
        
        # 缝合 Bids
        for b in msg.get("bids", []):
            try:
                p, s = str(b.get("price")), float(b.get("size"))
                if s <= 0:
                    ob["bids"].pop(p, None) # 撤单，从字典剔除
                else:
                    ob["bids"][p] = s
            except:
                pass
                
        # 缝合 Asks
        for a in msg.get("asks", []):
            try:
                p, s = str(a.get("price")), float(a.get("size"))
                if s <= 0:
                    ob["asks"].pop(p, None) # 撤单，从字典剔除
                else:
                    ob["asks"][p] = s
            except:
                pass
                
        # 组装为完整的格式返回
        full_book = {
            "bids": [{"price": p, "size": s} for p, s in ob["bids"].items()],
            "asks": [{"price": p, "size": s} for p, s in ob["asks"].items()]
        }
        return full_book

    def _poll_clob_book_once(self, token_id):
        try:
            resp = requests.get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=10)
            resp.raise_for_status()
            book = resp.json()
            if not isinstance(book, dict):
                return False
            depth = compute_1c_depth(book)
            if not depth:
                return False
            self.update_count += 1
            self.last_update_at = datetime.now(timezone.utc).isoformat()
            self._upsert_state(token_id, book, depth)
            self._insert_delta(
                token_id,
                "book_rest_fallback",
                {
                    "now_bid": depth.get("now_bid"),
                    "now_ask": depth.get("now_ask"),
                    "best_bid": depth.get("best_bid"),
                    "best_ask": depth.get("best_ask"),
                    "last_price": book.get("price") or book.get("last_trade_price") or depth.get("now_ask"),
                },
                "WS unavailable; refreshed from CLOB REST book",
            )
            return True
        except Exception as e:
            self.last_ws_error = f"REST fallback failed: {type(e).__name__}: {e}"
            return False

    def _poll_shard_books_once(self, shard_id):
        refreshed = 0
        for token_id in list(self.shard_tokens.get(shard_id, set()))[:100]:
            if self._poll_clob_book_once(token_id):
                refreshed += 1
        if refreshed:
            self.last_msg_at = datetime.now(timezone.utc).isoformat()
        return refreshed
     
    def _process_message(self, raw_msg):
        """处理收到的 WebSocket 消息：内存中拼图 -> Insert Table B -> Upsert Table A"""
        self.msg_count += 1
        self.last_msg_at = datetime.now(timezone.utc).isoformat()
        
        try:
            data = json.loads(raw_msg) if isinstance(raw_msg, str) else raw_msg
        except json.JSONDecodeError:
            return
        
        messages = data if isinstance(data, list) else [data]
        
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            
            asset_id = msg.get("asset_id") or msg.get("token_id") or msg.get("market")
            
            if not asset_id or asset_id not in self.token_map:
                continue
            
            event_type = msg.get("event_type", "unknown")
            
            depth = None

            # ========== (路线1: 合并状态) Apply to Memory Ledger ==========
            if "bids" in msg or "asks" in msg:
                # 无论是全量快照(book)还是变化(price_change)，全部输入缝合机
                full_book = self._apply_delta_to_orderbook(asset_id, msg)
                
                # 基于缝合完的完美照片，计算深度指标
                depth = compute_1c_depth(full_book)
                if depth:
                    self.update_count += 1
                    self.last_update_at = datetime.now(timezone.utc).isoformat()
                    # self._print_update(asset_id, depth) # 注释掉以减少终端刷屏，全靠 DB
                    
                    # Upsert full state to Table A 
                    self._upsert_state(asset_id, full_book, depth)

            # ========== (路线1: 记录流水) Insert Delta to Table B ==========
            if event_type in ["book", "price_change"] and ("bids" in msg or "asks" in msg or "price" in msg):
                delta_payload = {k: v for k, v in msg.items() if k in ["bids", "asks", "price"]}
                if depth:
                    now_bid = depth.get("now_bid", depth.get("best_bid"))
                    now_ask = depth.get("now_ask", depth.get("best_ask"))
                    delta_payload.update(
                        {
                            "now_bid": now_bid,
                            "now_ask": now_ask,
                            "best_bid": depth.get("best_bid"),
                            "best_ask": depth.get("best_ask"),
                            "last_price": msg.get("price") or msg.get("last_trade_price") or now_ask,
                        }
                    )
                self._insert_delta(asset_id, event_type, delta_payload)
            
            # 处理价格变动消息
            if event_type == "price_change" or "price" in msg:
                price = msg.get("price") or msg.get("last_trade_price")
                if price is not None:
                    try:
                        price_f = float(price)
                        info = self.token_map.get(asset_id, {})
                        old_price = info.get("target_price", 0)
                        info["target_price"] = price_f
                        
                        if abs(price_f - old_price) > 0.001:
                            ts = datetime.now().strftime("%H:%M:%S")
                            question = info.get("question", "?")
                            if len(question) > 85:
                                question = question[:82] + "..."
                            stream = info.get("stream", "?")
                            print(f"  [{ts}] [{stream}] 💰 价格变动: {question:<85}  {old_price:.3f} → {price_f:.3f}")
                    except (ValueError, TypeError):
                        pass
    
    async def _shard_ws_loop(self, shard_id):
        """单个 WebSocket 分片的监控循环，监听流并可动态收发控制指令"""
        retry_delay = WS_RECONNECT_DELAY
        # 获取专属的控制信箱
        ctrl_q = self.control_queues[shard_id]
        
        while self._running:
            try:
                print(f"[*] Shard {shard_id} 正在连接... (open_timeout={WS_OPEN_TIMEOUT}s)")

                async with AsyncExitStack() as stack:
                    if self.connect_semaphore is not None:
                        await self.connect_semaphore.acquire()
                    try:
                        proxy_url = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY")
                        connect_kwargs = {
                            "ping_interval": WS_PING_INTERVAL,
                            "ping_timeout": 10,
                            "close_timeout": 5,
                            "open_timeout": WS_OPEN_TIMEOUT,
                            "max_size": 10 * 1024 * 1024,
                        }
                        if proxy_url:
                            from websockets_proxy import Proxy

                            proxy_obj = Proxy.from_url(proxy_url)
                            ws_ctx = proxy_connect(
                                WS_URL,
                                proxy=proxy_obj,
                                **connect_kwargs,
                            )
                        else:
                            ws_ctx = websockets.connect(
                                WS_URL,
                                **connect_kwargs,
                            )

                        ws = await stack.enter_async_context(ws_ctx)
                    finally:
                        if self.connect_semaphore is not None:
                            self.connect_semaphore.release()

                    print(f"[*] Shard {shard_id} connected successfully.")
                    self.shard_connected[shard_id] = True
                    self.last_ws_error = None
                    retry_delay = WS_RECONNECT_DELAY
                    subscribed_tokens = list(self.shard_tokens.get(shard_id, set()))
                    if subscribed_tokens:
                        for i in range(0, len(subscribed_tokens), 300):
                            chunk = subscribed_tokens[i : i + 300]
                            await ws.send(json.dumps({"assets_ids": chunk, "type": "market", "action": "subscribe"}))
                            self.last_subscribe_at = datetime.now(timezone.utc).isoformat()
                        print(f"[*] Shard {shard_id} resubscribed {len(subscribed_tokens)} tokens")
                    
                    while self._running:
                        try:
                            # 听风者：监听 WebSockets 管道 和 控制指令信箱 Queue
                            recv_task = asyncio.create_task(ws.recv())
                            ctrl_task = asyncio.create_task(ctrl_q.get())
                            
                            done, pending = await asyncio.wait(
                                [recv_task, ctrl_task], 
                                return_when=asyncio.FIRST_COMPLETED,
                                timeout=WS_PING_INTERVAL + 10
                            )
                            
                            if not done:
                                # Timeout
                                for t in pending: t.cancel()
                                try:
                                    pong = await ws.ping()
                                    await asyncio.wait_for(pong, timeout=5)
                                except:
                                    print(f"[!] Shard {shard_id} 心跳超时...")
                                    break
                                continue

                            # 提取结果并取消未完成的
                            for t in pending:
                                t.cancel()
                                
                            if recv_task in done:
                                raw_msg = recv_task.result()
                                try:
                                    self.msg_queue.put_nowait(raw_msg)
                                except asyncio.QueueFull:
                                    pass
                                    
                                # 如果之前 ctrl_task 没走完，把里面的东西放回去重新领
                                
                            if ctrl_task in done:
                                ctrl_cmd = ctrl_task.result()
                                action = ctrl_cmd.get("action")
                                tokens = ctrl_cmd.get("tokens", [])
                                if tokens:
                                    print(f"[*] Shard {shard_id} 执行动态指令 {action} {len(tokens)} 个 Tokens")
                                    await ws.send(json.dumps({"assets_ids": tokens, "type": "market", "action": action}))
                                ctrl_q.task_done()
                                
                        except asyncio.CancelledError:
                            break
                        except websockets.exceptions.ConnectionClosed as e:
                            self.shard_connected[shard_id] = False
                            self.last_ws_error = f"ConnectionClosed code={e.code}"
                            print(f"[!] Shard {shard_id} 断线: code={e.code}")
                            break
                        except Exception as e:
                            self.shard_connected[shard_id] = False
                            self.last_ws_error = f"{type(e).__name__}: {e}"
                            print(f"[!] Shard {shard_id} 处理单次循环异常: {e}")
                            break
                            
            except asyncio.TimeoutError:
                self.shard_connected[shard_id] = False
                self.last_ws_error = f"open timeout ({WS_OPEN_TIMEOUT}s)"
                print(f"[!] Shard {shard_id} 底层异常: WebSocket 握手超时 ({WS_OPEN_TIMEOUT}s)")
            except Exception as e:
                self.shard_connected[shard_id] = False
                self.last_ws_error = f"{type(e).__name__}: {e}"
                print(f"[!] Shard {shard_id} 底层异常: {type(e).__name__}: {e}")

            fallback_count = self._poll_shard_books_once(shard_id)
            if fallback_count:
                print(f"[*] Shard {shard_id} REST fallback refreshed {fallback_count} tokens")

            if not self._running:
                break
                
            sleep_for = retry_delay + random.uniform(0, min(3.0, max(0.5, retry_delay * 0.25)))
            print(f"[*] Shard {shard_id} 将在 {sleep_for:.1f} 秒后重连...")
            await asyncio.sleep(sleep_for)
            retry_delay = min(retry_delay * 2, WS_MAX_RECONNECT_DELAY)
            
    async def _watchdog_loop(self):
        """后台慢轨巡检：定期扫描最新市场，动态算出 To_Add 和 To_Remove"""
        await asyncio.sleep(60) # 启动后等一会儿再首次巡检
        
        while self._running:
            print("\n[*] [Watchdog] 巡检开始: 重新拉取最新线上市场...")
            try:
                loop = asyncio.get_running_loop()
                markets = await loop.run_in_executor(None, fetch_all_active_markets)
                self._markets_snapshot = markets
                if self.enable_probability_scan:
                    c_high, c_low = await loop.run_in_executor(None, local_probability_filter, markets)
                else:
                    c_high, c_low = [], []
                c_holding = await loop.run_in_executor(None, fetch_my_holdings, markets)
                c_strategy_monitoring = await loop.run_in_executor(None, load_strategy_monitoring_candidates, markets)
                self._reconcile_candidates(c_low, c_high, c_strategy_monitoring, c_holding)

            except Exception as e:
                print(f"[!] Watchdog 异常: {e}")

            print(f"[*] [Watchdog] 巡检结束，倒计时 {WS_WATCHDOG_INTERVAL} 秒...")
            await asyncio.sleep(WS_WATCHDOG_INTERVAL)
            
    async def _main_orchestrator(self):
        """总调度循环：拉起消费者和 WebSocket 分片"""
        self.connect_semaphore = asyncio.Semaphore(WS_MAX_CONCURRENT_HANDSHAKES)
        # 1. 启动 Worker
        for i in range(WS_WORKER_COUNT):
            task = asyncio.create_task(self._worker_loop(i))
            self.tasks.append(task)
            
        # 2. 划分 Token 并启动 Shard，注册控制信箱
        all_tokens = list(self.token_map.keys())
        MAX_SHARDS = 100  # 限制最大分片数，避免并发连接过多
        tokens_per_shard = max(WS_MAX_TOKENS_PER_WS, len(all_tokens) // MAX_SHARDS + 1) if all_tokens else WS_MAX_TOKENS_PER_WS
        print(f"[*] Token 总数: {len(all_tokens)}, 每分片承载: {tokens_per_shard}, 预计分片数: {min(MAX_SHARDS, (len(all_tokens) // tokens_per_shard) + 1)}")
        shard_id = 0
        for i in range(0, max(len(all_tokens), 1), tokens_per_shard):
            chunk = all_tokens[i:i + tokens_per_shard] if all_tokens else []
            self.control_queues[shard_id] = asyncio.Queue()
            self.shard_tokens[shard_id] = set(chunk)
            task = asyncio.create_task(self._shard_ws_loop(shard_id))
            self.tasks.append(task)
            # 初次启动投递订阅包
            shard_id += 1
            await asyncio.sleep(WS_CONNECT_STAGGER_SEC)  # 错峰启动，避免 VPN/TLS 拥塞
            
        # 3. 启动 Watchdog
        watchdog_task = asyncio.create_task(self._watchdog_loop())
        self.tasks.append(watchdog_task)

        if STRATEGY_MONITORING_ENABLED:
            strategy_monitoring_poll_task = asyncio.create_task(self._strategy_monitoring_poll_loop())
            self.tasks.append(strategy_monitoring_poll_task)
            
        self.start_time = time.time()
        self.last_stats_time = time.time()
        
        print(f"[*] ================= 核心组件已就绪 (分片 {shard_id} | 消费者 {WS_WORKER_COUNT}) =================")
        
        # 3. 阻塞主循环并定期打日志
        while self._running:
            await asyncio.sleep(1)
            now = time.time()
            if now - self.last_stats_time >= WS_STATS_INTERVAL:
                self._print_stats()
    
    def stop(self):
        """优雅停止"""
        self._running = False
        print("\n[*] 正在停止监控...")
    
    def run(self):
        """启动 WebSocket 监控 (阻塞)"""
        if not self.token_map:
            print("[!] Phase 2 没有筛出任何候选市场，无法启动 WebSocket 监控。")
            return
        
        print(f"\n{'='*70}")
        print("  [WS] Polymarket DataTube WebSocket 实时监控")
        print(f"  [WS] WebSocket: {WS_URL}")
        print(f"  [WS] 监控标的数: {len(self.token_map)}")
        
        high_count = sum(1 for v in self.token_map.values() if v["stream"] == "HighProb")
        low_count = sum(1 for v in self.token_map.values() if v["stream"] == "LowProb")
        holding_count = sum(1 for v in self.token_map.values() if v["stream"] == "MyHolding")
        strategy_monitoring_count = sum(1 for v in self.token_map.values() if v["stream"] == STRATEGY_MONITORING_STREAM)
        print(f"  [Stats] HighProb: {high_count} | LowProb: {low_count} | MyHolding: {holding_count} | {STRATEGY_MONITORING_STREAM}: {strategy_monitoring_count}")
        print(f"  [DB] MongoDB: {'ON' if self.mongo_enabled else 'OFF'}")
        if self.mongo_enabled:
            print(f"      URI={MONGO_URI} / DB={DB_NAME}")
        print(f"  [DB] SQLite: {'ON' if self.sqlite_enabled else 'OFF'}")
        if self.sqlite_enabled:
            print(f"      REGULAR={self.sqlite_path_regular}")
        if STRATEGY_MONITORING_ENABLED:
            print(f"  [{STRATEGY_MONITORING_STREAM}] 策略监控库: {STRATEGY_MONITORING_PATH} (表 {STRATEGY_MONITORING_TABLE}, 轮询 {STRATEGY_MONITORING_POLL}s)")
        print(f"  [Stats] 统计间隔: 每 {WS_STATS_INTERVAL} 秒")
        print(f"  [WS] 握手超时: {WS_OPEN_TIMEOUT}s | 启动错峰: {WS_CONNECT_STAGGER_SEC}s | 同时握手上限: {WS_MAX_CONCURRENT_HANDSHAKES}")
        print(f"{'='*70}\n")
        
        # 初始化 MongoDB / SQLite
        self._init_mongo()
        self._init_sqlite()
        
        # 设置信号处理 (Ctrl+C 优雅退出)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        def _signal_handler():
            self.stop()
        
        try:
            # Windows 上 signal 处理有限，使用 try/except KeyboardInterrupt
            loop.run_until_complete(self._main_orchestrator())
        except KeyboardInterrupt:
            self.stop()
            # 优雅取消剩余通过任务跑的协程
            try:
                pending = asyncio.all_tasks(loop=loop)
                for t in pending:
                    t.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        finally:
            # 打印最终统计
            if self.start_time:
                self._print_stats()
            
            # 清理 MongoDB 连接
            if self.mongo_client:
                self.mongo_client.close()
                print("[*] MongoDB 连接已关闭")

            # 清理 SQLite 连接
            if self.sqlite_conn:
                try:
                    self.sqlite_conn.commit()
                    self.sqlite_conn.close()
                    print("[*] SQLite 常规库连接已关闭")
                except Exception as e:
                    print(f"[!] SQLite 常规库关闭失败: {e}")
            
            loop.close()
            print("[*] 监控已完全停止。")


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    print(f"====== Polymarket DataTube 实时监控 (WebSocket 版) ======\n")
    
    # Phase 1: 全量拉取
    markets = fetch_all_active_markets()
    
    # Phase 2: 概率过滤
    candidates_high, candidates_low = local_probability_filter(markets)
    
    # Phase 2.5: 持仓 & 策略监控 (无概率过滤)
    candidates_holding = fetch_my_holdings(markets)
    candidates_strategy_monitoring = load_strategy_monitoring_candidates(markets)
    
    # Phase 3: WebSocket 实时监控
    monitor = WebSocketMonitor(candidates_high, candidates_low, candidates_holding, candidates_strategy_monitoring)
    monitor.run()
    
    print("====== 监控结束 ======")
