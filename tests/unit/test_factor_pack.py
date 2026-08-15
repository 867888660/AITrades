from __future__ import annotations

import unittest

from integrations.qlib.alpha158_import import (
    ALPHA158_NO_VWAP_FACTOR_COUNT,
    ALPHA158_NO_VWAP_PACK_ID,
    EXCLUDED_FACTORS,
)
from services.data_platform.factor_pack import FactorPackRegistry
from services.data_platform.research_semantics import (
    ResearchSemanticError,
    build_research_contract,
    normalize_candidate,
)


class NativeFactorPackTests(unittest.TestCase):
    def test_alpha158_importer_uses_native_factor_pack_identity(self) -> None:
        pack = FactorPackRegistry.require(ALPHA158_NO_VWAP_PACK_ID)

        self.assertEqual(157, pack.factor_count)
        self.assertEqual(ALPHA158_NO_VWAP_FACTOR_COUNT, pack.factor_count)
        self.assertEqual(("VWAP0",), pack.excluded_factors)
        self.assertEqual(EXCLUDED_FACTORS, pack.excluded_factors)
        self.assertFalse(pack.is_standard_alpha158)
        self.assertEqual(("open", "high", "low", "close", "volume"), pack.required_fields)
        self.assertEqual(60, pack.minimum_history_bars)

    def test_alpha158_goal_freezes_pack_in_research_contract(self) -> None:
        contract = build_research_contract({
            "objective": "评价 AAPL 的 Qlib Alpha158 without VWAP",
            "instrument_scope": ["AAPL"],
            "frequency": "1d",
            "research_period": {"start": "2020-01-01", "end": "2025-12-31"},
            "universe_policy": {
                "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                "selection": {"method": "ALL_ELIGIBLE"},
            },
        })

        pack = FactorPackRegistry.require(ALPHA158_NO_VWAP_PACK_ID)
        self.assertEqual(pack.goal_identity(), contract["factor_pack"])

    def test_alpha158_goal_without_universe_asks_research_question(self) -> None:
        with self.assertRaises(ResearchSemanticError) as caught:
            build_research_contract({"objective": "研究 Qlib Alpha158 without VWAP"})

        self.assertEqual("RESEARCH_UNIVERSE_REQUIRED", caught.exception.code)
        self.assertIn("recommended", caught.exception.context)

    def test_candidate_cannot_replace_frozen_pack_with_single_factor(self) -> None:
        pack = FactorPackRegistry.require(ALPHA158_NO_VWAP_PACK_ID)
        contract = {
            "factor_pack": pack.goal_identity(),
            "evaluation": {"run_type": "FACTOR_EVALUATION", "primary_metric": "rank_ic"},
        }
        candidate = {
            "hypothesis": "用单因子替换因子包",
            "intervention_set": [{"component": "factor", "change": "replace pack"}],
            "factor": {"name": "probe", "operator": "pct_change", "window": 1},
        }

        with self.assertRaisesRegex(ValueError, "不能用单一 Factor 替换"):
            normalize_candidate(candidate, contract)


if __name__ == "__main__":
    unittest.main()
