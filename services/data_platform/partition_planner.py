"""
Partition Planner - 自动将大型历史研究切分为年度分区

核心思想：
- 将 25 年历史研究切分为 25 个年度分区
- 每个分区包含主执行窗口（1年）+ Warmup 窗口（前 252 交易日）
- 内存需求从 O(全部历史) 降为 O(1年 + warmup)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PartitionPlan:
    """单个年度分区的执行计划"""
    partition_id: str  # "PARTITION_2020"
    calendar_year: int  # 2020
    calendar_start: str  # "2020-01-01"
    calendar_end: str  # "2020-12-31"
    warmup_start: str  # "2019-10-01" (前 252 trading days 或 ~90 calendar days)
    warmup_end: str  # "2019-12-31"
    estimated_rows: int  # 预估行数
    estimated_mb: int  # 预估内存占用（MB）
    checkpoint_path: Path  # Checkpoint 文件路径


@dataclass(frozen=True)
class ResearchPartitionStrategy:
    """完整的分区执行策略"""
    partitions: tuple[PartitionPlan, ...]
    total_estimated_mb: int  # 如果不分区的总内存
    per_partition_peak_mb: int  # 单个分区的峰值内存
    execution_mode: str  # "PARTITIONED" | "LEGACY"
    reason: str  # 为什么选择这种模式


class ResearchPartitionPlanner:
    """
    研究分区规划器

    职责：
    1. 根据历史长度、Universe 大小自动切分年度分区
    2. 为每个分区计算 Warmup 窗口（基于最大 Factor window）
    3. 估算每个分区的内存需求
    4. 决定是否需要分区执行
    """

    # 常量：内存估算参数
    BYTES_PER_BAR_ROW = 200  # 每行 OHLCV 数据的 Python dict 开销
    BYTES_PER_FACTOR_ROW = 150  # 每行 Factor 结果
    BYTES_PER_ALPHA_ROW = 100  # 每行 Alpha 结果
    MEMORY_OVERHEAD_MULTIPLIER = 1.3  # 额外开销（临时对象、GC 延迟等）

    # 常量：分区决策阈值
    SAFE_PARTITION_MB = 3500  # 单个分区安全内存上限（考虑 4GB Worker - 500MB reserve）
    REQUIRE_PARTITION_THRESHOLD_MB = 8192  # 超过此值强制分区

    def __init__(self, checkpoint_root: Path | None = None):
        self.checkpoint_root = checkpoint_root or Path("research_checkpoints")

    def plan(
        self,
        frozen_input: dict[str, Any],
        bundle_hash: str,
    ) -> ResearchPartitionStrategy:
        """
        生成分区执行策略

        Args:
            frozen_input: 研究的 frozen input closure
            bundle_hash: bundle 哈希（用于 checkpoint 路径）

        Returns:
            ResearchPartitionStrategy
        """
        # 解析时间范围
        history_start = frozen_input["history_start"][:10]  # "2000-01-01"
        history_end = frozen_input["history_end"][:10]  # "2025-12-31"

        # 解析 Universe 大小
        universe_size = self._estimate_universe_size(frozen_input)

        # 计算最大 Factor window（决定 warmup 长度）
        max_factor_window_days = self._max_factor_window(frozen_input)

        # 计算如果不分区的总内存需求
        total_years = int(history_end[:4]) - int(history_start[:4]) + 1
        trading_days_total = total_years * 252  # 简化估算
        total_estimated_mb = self._estimate_memory_mb(
            universe_size=universe_size,
            trading_days=trading_days_total,
            factor_count=len(frozen_input.get("factors", [])),
            alpha_count=len(frozen_input.get("alphas", [])),
        )

        # 决策：是否需要分区
        if total_estimated_mb < self.SAFE_PARTITION_MB:
            # 内存足够，使用传统单次执行
            return ResearchPartitionStrategy(
                partitions=(),
                total_estimated_mb=total_estimated_mb,
                per_partition_peak_mb=total_estimated_mb,
                execution_mode="LEGACY",
                reason=f"预估内存 {total_estimated_mb} MB < 安全阈值 {self.SAFE_PARTITION_MB} MB",
            )

        # 需要分区：按年度切分
        partitions = self._generate_yearly_partitions(
            history_start=history_start,
            history_end=history_end,
            universe_size=universe_size,
            max_factor_window_days=max_factor_window_days,
            factor_count=len(frozen_input.get("factors", [])),
            alpha_count=len(frozen_input.get("alphas", [])),
            bundle_hash=bundle_hash,
        )

        # 计算单个分区的峰值内存
        per_partition_peak_mb = max(p.estimated_mb for p in partitions)

        return ResearchPartitionStrategy(
            partitions=partitions,
            total_estimated_mb=total_estimated_mb,
            per_partition_peak_mb=per_partition_peak_mb,
            execution_mode="PARTITIONED",
            reason=f"预估总内存 {total_estimated_mb} MB 超过安全阈值，切分为 {len(partitions)} 个年度分区",
        )

    def _generate_yearly_partitions(
        self,
        history_start: str,
        history_end: str,
        universe_size: int,
        max_factor_window_days: int,
        factor_count: int,
        alpha_count: int,
        bundle_hash: str,
    ) -> tuple[PartitionPlan, ...]:
        """生成年度分区列表"""
        start_year = int(history_start[:4])
        end_year = int(history_end[:4])

        partitions: list[PartitionPlan] = []

        for year in range(start_year, end_year + 1):
            # 主执行窗口
            calendar_start = f"{year}-01-01"
            calendar_end = f"{year}-12-31"

            # 如果是第一年，从 history_start 开始
            if year == start_year:
                calendar_start = history_start

            # 如果是最后一年，到 history_end 结束
            if year == end_year:
                calendar_end = history_end

            # 计算 Warmup 窗口
            warmup_start, warmup_end = self._calculate_warmup_window(
                execution_start=calendar_start,
                max_factor_window_days=max_factor_window_days,
                absolute_earliest=history_start,  # 不能早于研究起始时间
            )

            # 估算这个分区的内存
            # Warmup + Execution 的总交易日数
            warmup_days = self._calendar_days_to_trading_days(
                (datetime.fromisoformat(warmup_end) - datetime.fromisoformat(warmup_start)).days
            )
            execution_days = self._calendar_days_to_trading_days(
                (datetime.fromisoformat(calendar_end) - datetime.fromisoformat(calendar_start)).days
            )
            total_trading_days = warmup_days + execution_days

            estimated_mb = self._estimate_memory_mb(
                universe_size=universe_size,
                trading_days=total_trading_days,
                factor_count=factor_count,
                alpha_count=alpha_count,
            )

            estimated_rows = universe_size * total_trading_days

            # Checkpoint 路径
            checkpoint_path = (
                self.checkpoint_root / bundle_hash[:8] / f"partition_{year}.parquet"
            )

            partition = PartitionPlan(
                partition_id=f"PARTITION_{year}",
                calendar_year=year,
                calendar_start=calendar_start,
                calendar_end=calendar_end,
                warmup_start=warmup_start,
                warmup_end=warmup_end,
                estimated_rows=estimated_rows,
                estimated_mb=estimated_mb,
                checkpoint_path=checkpoint_path,
            )

            partitions.append(partition)

        return tuple(partitions)

    def _calculate_warmup_window(
        self,
        execution_start: str,
        max_factor_window_days: int,
        absolute_earliest: str,
    ) -> tuple[str, str]:
        """
        计算 Warmup 窗口

        Args:
            execution_start: 执行窗口起始日期 "2020-01-01"
            max_factor_window_days: 最大 Factor window（交易日）
            absolute_earliest: 绝对最早日期（研究起始时间）

        Returns:
            (warmup_start, warmup_end)
        """
        execution_date = datetime.fromisoformat(execution_start)

        # Warmup 结束 = 执行开始前一天
        warmup_end_date = execution_date - timedelta(days=1)

        # Warmup 开始 = 往前推 max_factor_window_days 个交易日
        # 简化：1 trading day ≈ 1.4 calendar days（考虑周末、节假日）
        warmup_calendar_days = int(max_factor_window_days * 1.4) + 10  # +10 缓冲
        warmup_start_date = execution_date - timedelta(days=warmup_calendar_days)

        # 不能早于绝对最早日期
        absolute_earliest_date = datetime.fromisoformat(absolute_earliest)
        if warmup_start_date < absolute_earliest_date:
            warmup_start_date = absolute_earliest_date

        warmup_start = warmup_start_date.date().isoformat()
        warmup_end = warmup_end_date.date().isoformat()

        return warmup_start, warmup_end

    def _max_factor_window(self, frozen_input: dict[str, Any]) -> int:
        """
        计算所有 Factor 中最大的 window（交易日）

        用于决定 Warmup 窗口长度
        """
        max_window = 252  # 默认 1 年

        for factor_spec in frozen_input.get("factors", []):
            window = factor_spec.get("window", 1)
            if window > max_window:
                max_window = window

        # 如果有 Factor Pack，可能需要更长 warmup
        for pack_id in frozen_input.get("factor_pack_ids", []):
            if "alpha158" in pack_id.lower():
                # Alpha158 需要约 180 天 warmup
                max_window = max(max_window, 180)

        return max_window

    def _estimate_universe_size(self, frozen_input: dict[str, Any]) -> int:
        """估算 Universe 大小（证券数量）"""
        # 简化实现：从 frozen input 推断
        # 实际应该从 Universe snapshot 查询

        # 如果是 static list
        if "instrument_scope" in frozen_input:
            return len(frozen_input["instrument_scope"])

        # 如果是动态 Universe，估算
        universe_policy = frozen_input.get("universe_policy", {})
        eligibility = universe_policy.get("eligibility", {})
        mode = eligibility.get("mode", "")

        if mode == "STATIC_LIST":
            return len(eligibility.get("instrument_scope", []))
        elif "US_EQUITY" in str(frozen_input.get("asset_scope", {})):
            # 美股全市场估算
            return 5000
        else:
            # 保守估算
            return 1000

    def _estimate_memory_mb(
        self,
        universe_size: int,
        trading_days: int,
        factor_count: int,
        alpha_count: int,
    ) -> int:
        """
        估算内存占用（MB）

        公式：
        原始数据（bars）= universe_size × trading_days × BYTES_PER_BAR_ROW
        Factor 结果 = universe_size × trading_days × factor_count × BYTES_PER_FACTOR_ROW
        Alpha 结果 = universe_size × trading_days × alpha_count × BYTES_PER_ALPHA_ROW
        峰值内存 = (原始 + Factor + Alpha) × MEMORY_OVERHEAD_MULTIPLIER
        """
        raw_rows = universe_size * trading_days
        raw_mb = (raw_rows * self.BYTES_PER_BAR_ROW) / (1024 * 1024)

        factor_rows = universe_size * trading_days * factor_count
        factor_mb = (factor_rows * self.BYTES_PER_FACTOR_ROW) / (1024 * 1024)

        alpha_rows = universe_size * trading_days * alpha_count
        alpha_mb = (alpha_rows * self.BYTES_PER_ALPHA_ROW) / (1024 * 1024)

        peak_mb = (raw_mb + factor_mb + alpha_mb) * self.MEMORY_OVERHEAD_MULTIPLIER

        return int(peak_mb)

    @staticmethod
    def _calendar_days_to_trading_days(calendar_days: int) -> int:
        """
        日历日转交易日（简化估算）

        假设：
        - 每周 5 个交易日
        - 每年约 252 个交易日（= 365 * 5/7 ≈ 260，减去节假日）
        """
        return int(calendar_days * (252 / 365))
