"""
Partition Executor - 逐分区执行研究任务

核心流程：
1. 加载 Warmup + Execution 窗口数据
2. 计算 Factors（只保留 execution 窗口结果）
3. 计算 Alphas（只保留 execution 窗口结果）
4. 写入 Checkpoint
5. 显式释放内存
6. 返回元数据

内存模型：
- 不累积结果，每个分区独立执行
- 执行完立即持久化并释放
- 峰值内存 = O(1 年 + warmup)，而非 O(全部历史)
"""
from __future__ import annotations

import gc
from typing import Any

from .checkpoint_manager import CheckpointManager, PartitionCheckpoint
from .data_client import FrozenManifestData
from .partition_planner import PartitionPlan
from .store import DataPlatformStore


class PartitionedResearchExecutor:
    """
    分区研究执行器

    负责执行单个分区的完整计算流程
    """

    def __init__(
        self,
        store: DataPlatformStore,
        checkpoint_manager: CheckpointManager,
    ):
        self.store = store
        self.checkpoint_manager = checkpoint_manager

    def execute_partition(
        self,
        partition: PartitionPlan,
        frozen_input: dict[str, Any],
        manifest_id: str,
    ) -> PartitionCheckpoint:
        """
        执行单个分区

        Args:
            partition: 分区计划
            frozen_input: 研究的 frozen input closure
            manifest_id: Manifest ID

        Returns:
            PartitionCheckpoint

        内存管理：
        - 每个阶段后显式 del 和 gc.collect()
        - 只保留 execution 窗口的结果
        - Warmup 数据仅用于计算，不持久化
        """
        try:
            # === 阶段 1: 加载数据 ===
            warmup_rows, execution_rows = self._load_partition_data(
                manifest_id=manifest_id,
                partition=partition,
            )

            # === 阶段 2: 计算 Factors ===
            factor_results = self._calculate_factors(
                warmup_rows=warmup_rows,
                execution_rows=execution_rows,
                partition=partition,
                factor_specs=frozen_input.get("factors", []),
            )

            # 释放原始数据
            del warmup_rows, execution_rows
            gc.collect()

            # === 阶段 3: 计算 Alphas ===
            alpha_results = self._calculate_alphas(
                factor_results=factor_results,
                partition=partition,
                alpha_specs=frozen_input.get("alphas", []),
            )

            # === 阶段 4: 持久化 Checkpoint ===
            bundle_hash = frozen_input.get("_bundle_hash", "unknown")
            checkpoint = self.checkpoint_manager.save(
                partition_id=partition.partition_id,
                bundle_hash=bundle_hash,
                factor_rows=factor_results,
                alpha_rows=alpha_results,
            )

            # === 阶段 5: 释放内存 ===
            del factor_results, alpha_results
            gc.collect()

            return checkpoint

        except Exception as e:
            # 确保异常时也释放内存
            gc.collect()
            raise RuntimeError(
                f"分区 {partition.partition_id} 执行失败: {e}"
            ) from e

    def aggregate_partitions(
        self,
        checkpoints: list[PartitionCheckpoint],
        frozen_input: dict[str, Any],
    ) -> dict[str, Any]:
        """
        聚合所有分区结果

        读取所有 Checkpoint，合并成最终结果

        Args:
            checkpoints: 所有分区的 Checkpoint
            frozen_input: 研究的 frozen input closure

        Returns:
            研究结果字典
        """
        # 合并所有 Alpha 结果（按时间排序）
        all_alpha_rows: list[dict[str, Any]] = []

        for checkpoint in checkpoints:
            partition_alpha_rows = self.checkpoint_manager.read_alpha_rows(checkpoint)
            all_alpha_rows.extend(partition_alpha_rows)

        # 按 available_time 排序
        all_alpha_rows.sort(key=lambda r: r["available_time"])

        # 计算统计指标
        total_factor_rows = sum(cp.row_count for cp in checkpoints)

        # 如果需要组合回测，在这里执行
        # （简化实现：暂不支持）

        return {
            "status": "COMPLETE",
            "execution_mode": "PARTITIONED",
            "partition_count": len(checkpoints),
            "factor_rows": total_factor_rows,
            "alpha_rows": len(all_alpha_rows),
            "alpha_timeline": all_alpha_rows,  # 完整时间线
        }

    def _load_partition_data(
        self,
        manifest_id: str,
        partition: PartitionPlan,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        加载分区数据

        Returns:
            (warmup_rows, execution_rows)
        """
        data_loader = FrozenManifestData(self.store, manifest_id)

        # 加载 Warmup 窗口
        warmup_rows = list(data_loader.read_rows(
            start_time=partition.warmup_start,
            end_time=partition.warmup_end,
        ))

        # 加载 Execution 窗口
        execution_rows = list(data_loader.read_rows(
            start_time=partition.calendar_start,
            end_time=partition.calendar_end,
        ))

        return warmup_rows, execution_rows

    def _calculate_factors(
        self,
        warmup_rows: list[dict[str, Any]],
        execution_rows: list[dict[str, Any]],
        partition: PartitionPlan,
        factor_specs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        计算 Factors

        流程：
        1. 合并 Warmup + Execution 数据（按证券分组）
        2. 逐证券计算 Factor
        3. 只保留 execution 窗口的结果

        Returns:
            Factor 结果（只包含 execution 窗口）
        """
        # 合并数据
        all_rows = warmup_rows + execution_rows

        # 按证券分组
        by_instrument: dict[str, list[dict[str, Any]]] = {}
        for row in all_rows:
            instrument_id = row["instrument_id"]
            if instrument_id not in by_instrument:
                by_instrument[instrument_id] = []
            by_instrument[instrument_id].append(row)

        # 逐证券计算
        factor_results: list[dict[str, Any]] = []

        from .factor_engine_v4 import FactorEngineV4

        engine = FactorEngineV4()

        for instrument_id, instrument_rows in by_instrument.items():
            # 排序（按时间）
            instrument_rows.sort(key=lambda r: r["available_time"])

            # 计算所有 Factors
            for factor_spec in factor_specs:
                try:
                    factor_values = engine.calculate(factor_spec, instrument_rows)

                    # 只保留 execution 窗口的结果
                    for fv in factor_values:
                        if partition.calendar_start <= fv["available_time"] <= partition.calendar_end:
                            factor_results.append(fv)

                except Exception as e:
                    # Factor 计算失败：记录但继续
                    print(f"Warning: Factor {factor_spec.get('name')} 计算失败 ({instrument_id}): {e}")

        return factor_results

    def _calculate_alphas(
        self,
        factor_results: list[dict[str, Any]],
        partition: PartitionPlan,
        alpha_specs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        计算 Alphas

        流程：
        1. 按交易日分组 Factor 结果
        2. 逐交易日计算 cross-sectional Alpha
        3. 返回 Alpha 时间线

        Returns:
            Alpha 结果
        """
        # 按交易日分组
        by_date: dict[str, list[dict[str, Any]]] = {}
        for fr in factor_results:
            date_key = fr["available_time"][:10]  # "2020-03-15"
            if date_key not in by_date:
                by_date[date_key] = []
            by_date[date_key].append(fr)

        # 逐交易日计算 Alpha
        alpha_results: list[dict[str, Any]] = []

        from .alpha_calculator import AlphaCalculator

        calculator = AlphaCalculator()

        for date_key in sorted(by_date.keys()):
            date_factors = by_date[date_key]

            for alpha_spec in alpha_specs:
                try:
                    alpha_values = calculator.calculate_cross_sectional(
                        alpha_spec=alpha_spec,
                        factor_rows=date_factors,
                        as_of_date=date_key,
                    )

                    alpha_results.extend(alpha_values)

                except Exception as e:
                    # Alpha 计算失败：记录但继续
                    print(f"Warning: Alpha {alpha_spec.get('name')} 计算失败 ({date_key}): {e}")

        return alpha_results


class AlphaCalculator:
    """Alpha 计算器（简化实现）"""

    def calculate_cross_sectional(
        self,
        alpha_spec: dict[str, Any],
        factor_rows: list[dict[str, Any]],
        as_of_date: str,
    ) -> list[dict[str, Any]]:
        """
        计算横截面 Alpha

        简化实现：
        - 只支持单 Factor 排序
        - 输出 percentile score
        """
        # 提取 Factor 值
        instruments = []
        for row in factor_rows:
            instrument_id = row.get("instrument_id")
            factor_value = row.get("value")

            if instrument_id and factor_value is not None:
                instruments.append({
                    "instrument_id": instrument_id,
                    "factor_value": factor_value,
                })

        if not instruments:
            return []

        # 排序
        instruments.sort(key=lambda x: x["factor_value"])

        # 计算 percentile
        n = len(instruments)
        alpha_results = []

        for rank, inst in enumerate(instruments):
            percentile = (rank + 1) / n

            alpha_results.append({
                "instrument_id": inst["instrument_id"],
                "available_time": f"{as_of_date}T23:59:59+00:00",
                "alpha_name": alpha_spec.get("name", "alpha"),
                "alpha_value": percentile,
                "rank": rank + 1,
                "universe_size": n,
            })

        return alpha_results
