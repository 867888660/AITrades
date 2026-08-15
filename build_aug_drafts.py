# -*- coding: utf-8 -*-
"""Build Aug-31-2026 strategy draft payloads for Stragy_Fllow_Stock_Value and
Stragy_MultiLeg_Stock_Rank_Value from the latest market snapshots."""
import json
import os

TMP = os.environ.get("TEMP", ".")
with open(os.path.join(TMP, "aug31_markets.json"), encoding="utf-8-sig") as f:
    raw = json.load(f)
markets_raw = raw["data"]["data"]  # list of full market objects

ENDDATE = "2026-08-31T23:59:00Z"

# ---- build normalized market dicts ----
def norm_market(m, max_entry_price, max_exposure_usdc):
    out = dict(m)
    out["instrument_id"] = m["condition_id"]
    out["action"] = "buy"
    out["outcome"] = "YES"
    out["venue"] = "polymarket"
    out["max_entry_price"] = max_entry_price
    out["max_exposure_usdc"] = max_exposure_usdc
    out["end_date"] = m.get("end_date") or ENDDATE
    return out

by_title = {m["group_item_title"]: m for m in markets_raw}

# =====================================================================
# Draft A: Stragy_Fllow_Stock_Value  ->  NVDA Aug2026 (Rank 1)
# =====================================================================
nvda = by_title["NVIDIA"]
fllow_market = norm_market(nvda, max_entry_price=0.99, max_exposure_usdc=100.0)

draft_fllow = {
    "name": "Stock MCap Rank1 - NVDA Aug2026",
    "strategy_code": "Stragy_Fllow_Stock_Value",
    "mode": "Virtual",
    "thesis": (
        "NVIDIA 当前全球市值第 1（8 月 31 日结算市场 YES ask=0.966），"
        "跟踪 NVDA 维持第 1 排名至 2026-08-31，跑模拟盘。"
    ),
    "markets": [fllow_market],
    "budget": {"max_single_order_usdc": 50.0, "max_total_usdc": 100.0},
    "execution_rules": {
        "cooldown_seconds": 300,
        "max_slippage_bps": 100.0,
        "order_type": "limit",
    },
    "exit_rules": {},
    "params": {
        "AnchorCompany": "NVDA",
        "RankPosition": 1,
        "Enddate": ENDDATE,
    },
    "risk_notes": [
        "8 月 31 日 23:59 UTC 结算，剩余约 17 天。",
        "NVDA YES ask=0.966 高于策略默认开仓价 0.70：批准后请在策略 Controls 中把 max_yes_entry_price / max_no_entry_price 调到 0.99 左右，否则不会开仓。",
        "单策略预算 100 USDC，模拟盘运行，人工确认后才生效。",
    ],
    "agent_report": {
        "strategy_reason": (
            "NVDA 当前全球市值第 1，8 月 31 日结算市场 YES ask=0.966、bid=0.965，"
            "流动性充足（24h 成交约 4.2 万 USDC），以最新数据跟踪第 1 名。"
        ),
        "market_observation": (
            "最新盘口：NVIDIA Aug31 YES bid=0.965 / ask=0.966（2026-08-14），"
            "结算日 2026-08-31T23:59:00Z；其余候选（AAPL/GOOGL 等）概率均低于 NVDA。"
        ),
        "parameter_rationale": (
            "AnchorCompany=NVDA、RankPosition=1，单步仓位迁移防抖动；"
            "总预算 100 USDC、单笔上限 50 USDC；Enddate=2026-08-31T23:59:00Z。"
            "注意：策略 Controls 默认 max_yes_entry_price=0.70，需在批准后调高至 0.99 才能在当前价位开仓。"
        ),
        "risk_control": (
            "模拟盘（Virtual）运行，总预算 100 USDC 不变；若排名掉出第 1 或 F_NEUTRAL 自动减仓；"
            "临近 8/31 结算自动降风险；人工确认前不落地任何实盘动作。"
        ),
        "human_review_focus": (
            "1. 确认 NVDA 是否仍为当前市值第 1；"
            "2. 批准后请在策略 Controls 把 max_yes_entry_price 设为 0.99（当前 ask=0.966，默认 0.70 会拦截开仓）；"
            "3. 确认 100 USDC 模拟盘预算。"
        ),
        "summary": (
            "NVDA 全球市值第 1，8 月 31 日结算 YES ask=0.966，"
            "新建 Stragy_Fllow_Stock_Value 模拟盘策略跟踪第 1 名。"
        ),
    },
}

# =====================================================================
# Draft B: Stragy_MultiLeg_Stock_Rank_Value -> All Largest Company Aug2026
# =====================================================================
UNIVERSE_AUG = "NVDA,AAPL,GOOGL,MSFT,AMZN,TSLA"
leg_titles = ["NVIDIA", "Apple", "Alphabet", "Microsoft", "Amazon", "Tesla", "Saudi Aramco", "Broadcom"]
multileg_markets = [
    norm_market(by_title[t], max_entry_price=0.99, max_exposure_usdc=12.5)
    for t in leg_titles
    if t in by_title
]

draft_multileg = {
    "name": "MultiLeg Stock MCap Rank1 - All Largest Company Aug2026",
    "strategy_code": "Stragy_MultiLeg_Stock_Rank_Value",
    "mode": "Virtual",
    "thesis": (
        "Use all currently live Largest Company Aug 31 2026 Polymarket legs with the multi-leg "
        "stock market-cap rank value strategy, capped at 100 USDC total budget (Virtual)."
    ),
    "markets": multileg_markets,
    "budget": {"max_single_order_usdc": 100.0, "max_total_usdc": 100.0},
    "execution_rules": {
        "allow_market_order": False,
        "cooldown_seconds": 300,
        "max_slippage_bps": 100.0,
        "order_type": "limit",
    },
    "exit_rules": {
        "cooldown_minutes": 30,
        "de_risk_start_days": 14,
        "force_flat_supported": True,
        "stop_new_orders_before_end_hours": 168,
    },
    "params": {
        "AddEdge": 0.03,
        "CloseEdge": -0.01,
        "CooldownMinutes": 30.0,
        "DebugTopN": 20,
        "Enddate": ENDDATE,
        "MaxCompanyExposurePct": 1.0,
        "MaxDataAgeSec": 900.0,
        "MaxEntryPrice": 0.99,
        "MaxPerLegPct": 1.0,
        "MaxRankExposurePct": 1.0,
        "MaxSideExposurePct": 1.0,
        "MaxSpread": 0.05,
        "MaxStepPct": 1.0,
        "MaxTotalExposurePct": 1.0,
        "MinAnnualizedRoi": 0.0,
        "MinAskDepthNotional": 5.0,
        "MinDaysToOpen": 0.25,
        "MinOpenPct": 0.1,
        "ModelAnnualVol": 0.35,
        "OpenEdge": 0.01,
        "PairwiseCorr": 0.65,
        "ReduceEdge": 0.005,
        "ShockFloor": 0.015,
        "SwitchEdge": 0.05,
        "TargetRanks": "1",
        "UniverseSymbols": UNIVERSE_AUG,
    },
    "risk_notes": [
        "8 月 31 日 23:59 UTC 结算，剩余约 17 天；总预算 100 USDC，单腿 12.5 USDC。",
        "该事件无 META/SPCX 腿；Saudi Aramco/Broadcom 腿如无法识别公司会被跳过。",
        "模拟盘（Virtual）运行，人工确认后才生效。",
    ],
    "agent_report": {
        "strategy_reason": (
            "用最新盘口重建 Largest Company Aug 31 2026 多腿市值排名策略："
            "8 条在交易的腿（NVDA/AAPL/GOOGL/MSFT/AMZN/TSLA/SAUDIARAMCO/BROADCOM），"
            "总预算 100 USDC，模拟盘。"
        ),
        "market_observation": (
            "最新盘口（2026-08-14）：NVDA YES 0.966、AAPL 0.021、GOOGL 0.016、MSFT 0.002、"
            "AMZN 0.002、TSLA 0.001、SAUDIARAMCO 0.001、BROADCOM 0.001；结算日 2026-08-31T23:59:00Z。"
        ),
        "parameter_rationale": (
            "沿用原 MultiLeg 参数（AddEdge=0.03、OpenEdge=0.01、MaxEntryPrice=0.99、"
            "MaxSpread=0.05、TargetRanks=1、UniverseSymbols=NVDA,AAPL,GOOGL,MSFT,AMZN,TSLA），"
            "Enddate=2026-08-31T23:59:00Z；每腿预算 12.5 USDC，合计 100 USDC。"
        ),
        "risk_control": (
            "模拟盘运行，总预算 100 USDC、单笔上限 100 USDC；临近 8/31 结算（de_risk_start_days=14）"
            "自动降风险；人工确认前不落地实盘。"
        ),
        "human_review_focus": (
            "1. 确认 8 月 31 日到期标的与方向（NVDA 第 1 名押 YES，其余高位 NO）符合预期；"
            "2. 确认每腿 12.5 USDC、总预算 100 USDC 可接受；"
            "3. 确认模拟盘运行。"
        ),
        "summary": (
            "重建 Largest Company Aug 31 2026 多腿市值排名模拟盘策略，"
            "8 腿、总预算 100 USDC、TargetRanks=1。"
        ),
    },
}

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "draft_fllow_aug.json"), "w", encoding="utf-8") as f:
    json.dump(draft_fllow, f, ensure_ascii=False, indent=1)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "draft_multileg_aug.json"), "w", encoding="utf-8") as f:
    json.dump(draft_multileg, f, ensure_ascii=False, indent=1)

print("fllow markets:", len(draft_fllow["markets"]))
print("multileg markets:", len(draft_multileg["markets"]))
print("written OK")
