# -*- coding: utf-8 -*-
"""Build Aug-31-2026 Rank2/Rank3 draft payloads for Stragy_Fllow_Stock_Value and
Stragy_MultiLeg_Stock_Rank_Value."""
import json
import os

TMP = os.environ.get("TEMP", ".")
with open(os.path.join(TMP, "aug31_markets.json"), encoding="utf-8-sig") as f:
    raw = json.load(f)
markets_raw = raw["data"]["data"]
by_title = {m["group_item_title"]: m for m in markets_raw}

ENDDATE = "2026-08-31T23:59:00Z"

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

leg_titles = ["NVIDIA", "Apple", "Alphabet", "Microsoft", "Amazon", "Tesla", "Saudi Aramco", "Broadcom"]
multileg_markets = [norm_market(by_title[t], 0.99, 12.5) for t in leg_titles if t in by_title]

def fllow_report(anchor, rank_pos, ask, bid, company_cn, mcap_rank_note):
    return {
        "strategy_reason": (
            f"{company_cn} 当前全球市值第 {rank_pos}（8 月 31 日结算市场 YES ask={ask}），"
            f"以最新数据跟踪其维持第 {rank_pos} 名及以上的排名窗口，跑模拟盘。"
        ),
        "market_observation": (
            f"最新盘口（2026-08-14）：{company_cn} Aug31 YES bid={bid} / ask={ask}，"
            f"结算日 2026-08-31T23:59:00Z；{mcap_rank_note}。"
        ),
        "parameter_rationale": (
            f"AnchorCompany={anchor}、RankPosition={rank_pos}，单步仓位迁移防抖动；"
            f"总预算 100 USDC、单笔上限 50 USDC；Enddate={ENDDATE}。"
        ),
        "risk_control": (
            "模拟盘（Virtual）运行，总预算 100 USDC 不变；排名离开目标名次或 F_NEUTRAL 时自动减仓；"
            "临近 8/31 结算自动降风险；人工确认前不落地任何实盘动作。"
        ),
        "human_review_focus": (
            f"1. 确认 {company_cn} 当前市值排名第 {rank_pos}；"
            f"2. 确认 {company_cn} 维持第 {rank_pos} 名及以上的跟踪逻辑与预算 100 USDC；3. 确认模拟盘运行。"
        ),
        "summary": (
            f"{company_cn} 当前全球市值第 {rank_pos}，8 月 31 日结算 YES ask={ask}，"
            f"新建 Stragy_Fllow_Stock_Value 模拟盘策略跟踪第 {rank_pos} 名。"
        ),
    }

def multileg_report(rank, mcap_note):
    return {
        "strategy_reason": (
            f"用最新盘口重建 Largest Company Aug 31 2026 多腿市值排名策略（TargetRanks={rank}）："
            "8 条在交易的腿，总预算 100 USDC，模拟盘。"
        ),
        "market_observation": (
            f"最新盘口（2026-08-14）：NVDA 0.966 / AAPL 0.021 / GOOGL 0.016 / MSFT 0.002 / AMZN 0.002 / "
            "TSLA 0.001 / SAUDIARAMCO 0.001 / BROADCOM 0.001；结算日 2026-08-31T23:59:00Z。"
        ),
        "parameter_rationale": (
            f"沿用原 MultiLeg 参数（AddEdge=0.03、OpenEdge=0.01、MaxEntryPrice=0.99、MaxSpread=0.05），"
            f"TargetRanks={rank}、UniverseSymbols=NVDA,AAPL,GOOGL,MSFT,AMZN,TSLA，Enddate={ENDDATE}；"
            "每腿预算 12.5 USDC，合计 100 USDC。"
        ),
        "risk_control": (
            "模拟盘运行，总预算 100 USDC、单笔上限 100 USDC；临近 8/31 结算（de_risk_start_days=14）自动降风险；"
            "人工确认前不落地实盘。"
        ),
        "human_review_focus": (
            f"1. 确认 TargetRanks={rank} 的目标名次定义与方向符合预期；"
            "2. 确认每腿 12.5 USDC、总预算 100 USDC 可接受；3. 确认模拟盘运行。"
        ),
        "summary": (
            f"重建 Largest Company Aug 31 2026 多腿市值排名模拟盘策略，8 腿、总预算 100 USDC、TargetRanks={rank}。"
        ),
    }

drafts = []

# ---- Fllow Rank2 AAPL ----
aapl = by_title["Apple"]
drafts.append({
    "name": "Stock MCap Rank2 - AAPL Aug2026",
    "strategy_code": "Stragy_Fllow_Stock_Value",
    "mode": "Virtual",
    "thesis": "Apple 当前全球市值第 2（8 月 31 日结算 YES ask=0.021），跟踪 AAPL 维持第 2 名及以上至 2026-08-31，跑模拟盘。",
    "markets": [norm_market(aapl, 0.99, 100.0)],
    "budget": {"max_single_order_usdc": 50.0, "max_total_usdc": 100.0},
    "execution_rules": {"cooldown_seconds": 300, "max_slippage_bps": 100.0, "order_type": "limit"},
    "exit_rules": {},
    "params": {"AnchorCompany": "AAPL", "RankPosition": 2, "Enddate": ENDDATE},
    "risk_notes": ["8 月 31 日 23:59 UTC 结算，剩余约 17 天。", "AAPL YES ask=0.021 远低于默认开仓价 0.70，可正常开仓。", "单策略预算 100 USDC，模拟盘运行。"],
    "agent_report": fllow_report("AAPL", 2, 0.021, 0.02, "Apple", "当前排名第 2（落后 NVDA，领先 GOOGL）"),
})

# ---- Fllow Rank3 GOOGL ----
googl = by_title["Alphabet"]
drafts.append({
    "name": "Stock MCap Rank3 - GOOGL Aug2026",
    "strategy_code": "Stragy_Fllow_Stock_Value",
    "mode": "Virtual",
    "thesis": "Alphabet 当前全球市值第 3（8 月 31 日结算 YES ask=0.016），跟踪 GOOGL 维持第 3 名及以上至 2026-08-31，跑模拟盘。",
    "markets": [norm_market(googl, 0.99, 100.0)],
    "budget": {"max_single_order_usdc": 50.0, "max_total_usdc": 100.0},
    "execution_rules": {"cooldown_seconds": 300, "max_slippage_bps": 100.0, "order_type": "limit"},
    "exit_rules": {},
    "params": {"AnchorCompany": "GOOGL", "RankPosition": 3, "Enddate": ENDDATE},
    "risk_notes": ["8 月 31 日 23:59 UTC 结算，剩余约 17 天。", "GOOGL YES ask=0.016 远低于默认开仓价 0.70，可正常开仓。", "单策略预算 100 USDC，模拟盘运行。"],
    "agent_report": fllow_report("GOOGL", 3, 0.016, 0.015, "Alphabet", "当前排名第 3（落后 AAPL，领先 MSFT）"),
})

# ---- MultiLeg TargetRanks=2 ----
drafts.append({
    "name": "MultiLeg Stock MCap Rank2 - All Largest Company Aug2026",
    "strategy_code": "Stragy_MultiLeg_Stock_Rank_Value",
    "mode": "Virtual",
    "thesis": (
        "Use all currently live Largest Company Aug 31 2026 Polymarket legs with the multi-leg "
        "stock market-cap rank value strategy at TargetRanks=2, capped at 100 USDC (Virtual)."
    ),
    "markets": multileg_markets,
    "budget": {"max_single_order_usdc": 100.0, "max_total_usdc": 100.0},
    "execution_rules": {"allow_market_order": False, "cooldown_seconds": 300, "max_slippage_bps": 100.0, "order_type": "limit"},
    "exit_rules": {"cooldown_minutes": 30, "de_risk_start_days": 14, "force_flat_supported": True, "stop_new_orders_before_end_hours": 168},
    "params": {
        "AddEdge": 0.03, "CloseEdge": -0.01, "CooldownMinutes": 30.0, "DebugTopN": 20, "Enddate": ENDDATE,
        "MaxCompanyExposurePct": 1.0, "MaxDataAgeSec": 900.0, "MaxEntryPrice": 0.99, "MaxPerLegPct": 1.0,
        "MaxRankExposurePct": 1.0, "MaxSideExposurePct": 1.0, "MaxSpread": 0.05, "MaxStepPct": 1.0,
        "MaxTotalExposurePct": 1.0, "MinAnnualizedRoi": 0.0, "MinAskDepthNotional": 5.0, "MinDaysToOpen": 0.25,
        "MinOpenPct": 0.1, "ModelAnnualVol": 0.35, "OpenEdge": 0.01, "PairwiseCorr": 0.65, "ReduceEdge": 0.005,
        "ShockFloor": 0.015, "SwitchEdge": 0.05, "TargetRanks": "2", "UniverseSymbols": "NVDA,AAPL,GOOGL,MSFT,AMZN,TSLA",
    },
    "risk_notes": ["8 月 31 日 23:59 UTC 结算，剩余约 17 天；总预算 100 USDC，单腿 12.5 USDC。", "TargetRanks=2：目标为结算时市值第 2 名。", "模拟盘（Virtual）运行，人工确认后才生效。"],
    "agent_report": multileg_report(2, "当前市值排名：NVDA1 / AAPL2 / GOOGL3 / MSFT4 / AMZN5 / TSLA8"),
})

# ---- MultiLeg TargetRanks=3 ----
drafts.append({
    "name": "MultiLeg Stock MCap Rank3 - All Largest Company Aug2026",
    "strategy_code": "Stragy_MultiLeg_Stock_Rank_Value",
    "mode": "Virtual",
    "thesis": (
        "Use all currently live Largest Company Aug 31 2026 Polymarket legs with the multi-leg "
        "stock market-cap rank value strategy at TargetRanks=3, capped at 100 USDC (Virtual)."
    ),
    "markets": multileg_markets,
    "budget": {"max_single_order_usdc": 100.0, "max_total_usdc": 100.0},
    "execution_rules": {"allow_market_order": False, "cooldown_seconds": 300, "max_slippage_bps": 100.0, "order_type": "limit"},
    "exit_rules": {"cooldown_minutes": 30, "de_risk_start_days": 14, "force_flat_supported": True, "stop_new_orders_before_end_hours": 168},
    "params": {
        "AddEdge": 0.03, "CloseEdge": -0.01, "CooldownMinutes": 30.0, "DebugTopN": 20, "Enddate": ENDDATE,
        "MaxCompanyExposurePct": 1.0, "MaxDataAgeSec": 900.0, "MaxEntryPrice": 0.99, "MaxPerLegPct": 1.0,
        "MaxRankExposurePct": 1.0, "MaxSideExposurePct": 1.0, "MaxSpread": 0.05, "MaxStepPct": 1.0,
        "MaxTotalExposurePct": 1.0, "MinAnnualizedRoi": 0.0, "MinAskDepthNotional": 5.0, "MinDaysToOpen": 0.25,
        "MinOpenPct": 0.1, "ModelAnnualVol": 0.35, "OpenEdge": 0.01, "PairwiseCorr": 0.65, "ReduceEdge": 0.005,
        "ShockFloor": 0.015, "SwitchEdge": 0.05, "TargetRanks": "3", "UniverseSymbols": "NVDA,AAPL,GOOGL,MSFT,AMZN,TSLA",
    },
    "risk_notes": ["8 月 31 日 23:59 UTC 结算，剩余约 17 天；总预算 100 USDC，单腿 12.5 USDC。", "TargetRanks=3：目标为结算时市值第 3 名。", "模拟盘（Virtual）运行，人工确认后才生效。"],
    "agent_report": multileg_report(3, "当前市值排名：NVDA1 / AAPL2 / GOOGL3 / MSFT4 / AMZN5 / TSLA8"),
})

base = os.path.dirname(os.path.abspath(__file__))
out_files = ["draft_fllow_rank2_aug.json", "draft_fllow_rank3_aug.json",
             "draft_multileg_rank2_aug.json", "draft_multileg_rank3_aug.json"]
for payload, name in zip(drafts, out_files):
    with open(os.path.join(base, name), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(name, "| markets:", len(payload["markets"]), "| params:", json.dumps(payload["params"], ensure_ascii=False))
print("written OK")
