"""
Checkpoint Manager - 管理分区执行的 Checkpoint 持久化和恢复

核心功能：
1. 将分区执行结果持久化到 Parquet
2. 支持进程崩溃后从 Checkpoint 恢复
3. 验证 Checkpoint 完整性
4. 清理过期 Checkpoint
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .store import DataPlatformStore, utc_now


@dataclass(frozen=True)
class PartitionCheckpoint:
    """单个分区的 Checkpoint 元数据"""
    checkpoint_id: str  # "checkpoint_abc123..."
    partition_id: str  # "PARTITION_2020"
    bundle_hash: str  # 关联到 frozen_research_bundles
    factor_artifact_id: str  # Factor 结果的 artifact ID
    alpha_artifact_id: str  # Alpha 结果的 artifact ID
    row_count: int  # 行数
    completed_at: str  # ISO timestamp
    verification_hash: str  # SHA256(factor + alpha artifacts)


class CheckpointManager:
    """
    Checkpoint 管理器

    存储结构：
    research_checkpoints/
      {bundle_hash[:8]}/
        partition_2020_factors.parquet
        partition_2020_alphas.parquet
        partition_2021_factors.parquet
        partition_2021_alphas.parquet
        ...
    """

    def __init__(self, store: DataPlatformStore, checkpoint_root: Path):
        self.store = store
        self.checkpoint_root = checkpoint_root
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """确保 Checkpoint 表存在"""
        with self.store.transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_partition_checkpoints(
                    checkpoint_id TEXT PRIMARY KEY DEFAULT ('checkpoint_' || lower(hex(randomblob(16)))),
                    partition_id TEXT NOT NULL,
                    bundle_hash TEXT NOT NULL,
                    factor_artifact_id TEXT NOT NULL,
                    alpha_artifact_id TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    completed_at TEXT NOT NULL,
                    verification_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    UNIQUE(partition_id, bundle_hash)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_checkpoint_bundle
                ON research_partition_checkpoints(bundle_hash, partition_id)
            """)

    def save(
        self,
        partition_id: str,
        bundle_hash: str,
        factor_rows: list[dict[str, Any]],
        alpha_rows: list[dict[str, Any]],
    ) -> PartitionCheckpoint:
        """
        持久化分区 Checkpoint

        Args:
            partition_id: "PARTITION_2020"
            bundle_hash: bundle 哈希
            factor_rows: Factor 计算结果
            alpha_rows: Alpha 计算结果

        Returns:
            PartitionCheckpoint
        """
        # 创建目录
        bundle_dir = self.checkpoint_root / bundle_hash[:8]
        bundle_dir.mkdir(parents=True, exist_ok=True)

        # 写入 Factor Checkpoint
        year = partition_id.split("_")[1]  # "PARTITION_2020" -> "2020"
        factor_path = bundle_dir / f"partition_{year}_factors.parquet"
        self._write_parquet(factor_path, factor_rows)

        # 写入 Alpha Checkpoint
        alpha_path = bundle_dir / f"partition_{year}_alphas.parquet"
        self._write_parquet(alpha_path, alpha_rows)

        # 计算验证哈希
        verification_hash = self._compute_verification_hash(factor_path, alpha_path)

        # 持久化元数据到 SQLite
        checkpoint_id = f"checkpoint_{hashlib.md5(f'{bundle_hash}:{partition_id}'.encode()).hexdigest()[:16]}"

        with self.store.transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO research_partition_checkpoints(
                    checkpoint_id, partition_id, bundle_hash,
                    factor_artifact_id, alpha_artifact_id,
                    row_count, completed_at, verification_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                checkpoint_id,
                partition_id,
                bundle_hash,
                str(factor_path),
                str(alpha_path),
                len(factor_rows),
                utc_now(),
                verification_hash,
            ))

        return PartitionCheckpoint(
            checkpoint_id=checkpoint_id,
            partition_id=partition_id,
            bundle_hash=bundle_hash,
            factor_artifact_id=str(factor_path),
            alpha_artifact_id=str(alpha_path),
            row_count=len(factor_rows),
            completed_at=utc_now(),
            verification_hash=verification_hash,
        )

    def load(
        self,
        partition_id: str,
        bundle_hash: str,
    ) -> PartitionCheckpoint | None:
        """
        加载分区 Checkpoint（如果存在且有效）

        用于恢复场景：检查是否已经执行过这个分区

        Returns:
            PartitionCheckpoint if exists and valid, else None
        """
        with self.store.connection() as conn:
            row = conn.execute("""
                SELECT * FROM research_partition_checkpoints
                WHERE partition_id=? AND bundle_hash=?
                ORDER BY completed_at DESC LIMIT 1
            """, (partition_id, bundle_hash)).fetchone()

        if not row:
            return None

        checkpoint = PartitionCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            partition_id=row["partition_id"],
            bundle_hash=row["bundle_hash"],
            factor_artifact_id=row["factor_artifact_id"],
            alpha_artifact_id=row["alpha_artifact_id"],
            row_count=row["row_count"],
            completed_at=row["completed_at"],
            verification_hash=row["verification_hash"],
        )

        # 验证文件完整性
        if not self._verify_checkpoint(checkpoint):
            # Checkpoint 损坏，删除
            self._delete_checkpoint(checkpoint)
            return None

        return checkpoint

    def list_completed(
        self,
        bundle_hash: str,
    ) -> list[PartitionCheckpoint]:
        """
        列出所有已完成的分区 Checkpoint

        用于 Aggregation 阶段：读取所有分区结果
        """
        with self.store.connection() as conn:
            rows = conn.execute("""
                SELECT * FROM research_partition_checkpoints
                WHERE bundle_hash=?
                ORDER BY partition_id
            """, (bundle_hash,)).fetchall()

        checkpoints = []
        for row in rows:
            checkpoint = PartitionCheckpoint(
                checkpoint_id=row["checkpoint_id"],
                partition_id=row["partition_id"],
                bundle_hash=row["bundle_hash"],
                factor_artifact_id=row["factor_artifact_id"],
                alpha_artifact_id=row["alpha_artifact_id"],
                row_count=row["row_count"],
                completed_at=row["completed_at"],
                verification_hash=row["verification_hash"],
            )

            # 只返回有效的 Checkpoint
            if self._verify_checkpoint(checkpoint):
                checkpoints.append(checkpoint)

        return checkpoints

    def read_factor_rows(self, checkpoint: PartitionCheckpoint) -> list[dict[str, Any]]:
        """读取 Factor Checkpoint 数据"""
        return self._read_parquet(Path(checkpoint.factor_artifact_id))

    def read_alpha_rows(self, checkpoint: PartitionCheckpoint) -> list[dict[str, Any]]:
        """读取 Alpha Checkpoint 数据"""
        return self._read_parquet(Path(checkpoint.alpha_artifact_id))

    def cleanup(
        self,
        bundle_hash: str,
        keep_days: int = 7,
    ) -> int:
        """
        清理过期 Checkpoint

        Args:
            bundle_hash: bundle 哈希
            keep_days: 保留天数

        Returns:
            删除的 Checkpoint 数量
        """
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()

        with self.store.connection() as conn:
            old_checkpoints = conn.execute("""
                SELECT checkpoint_id, factor_artifact_id, alpha_artifact_id
                FROM research_partition_checkpoints
                WHERE bundle_hash=? AND completed_at < ?
            """, (bundle_hash, cutoff)).fetchall()

        deleted_count = 0
        for row in old_checkpoints:
            # 删除文件
            factor_path = Path(row["factor_artifact_id"])
            alpha_path = Path(row["alpha_artifact_id"])

            if factor_path.exists():
                factor_path.unlink()
            if alpha_path.exists():
                alpha_path.unlink()

            # 删除元数据
            with self.store.transaction() as conn:
                conn.execute("""
                    DELETE FROM research_partition_checkpoints
                    WHERE checkpoint_id=?
                """, (row["checkpoint_id"],))

            deleted_count += 1

        # 如果目录为空，删除目录
        bundle_dir = self.checkpoint_root / bundle_hash[:8]
        if bundle_dir.exists() and not any(bundle_dir.iterdir()):
            bundle_dir.rmdir()

        return deleted_count

    def _verify_checkpoint(self, checkpoint: PartitionCheckpoint) -> bool:
        """
        验证 Checkpoint 完整性

        检查：
        1. 文件存在
        2. 验证哈希匹配
        """
        factor_path = Path(checkpoint.factor_artifact_id)
        alpha_path = Path(checkpoint.alpha_artifact_id)

        if not factor_path.exists() or not alpha_path.exists():
            return False

        # 重新计算验证哈希
        current_hash = self._compute_verification_hash(factor_path, alpha_path)
        return current_hash == checkpoint.verification_hash

    def _delete_checkpoint(self, checkpoint: PartitionCheckpoint) -> None:
        """删除损坏的 Checkpoint"""
        factor_path = Path(checkpoint.factor_artifact_id)
        alpha_path = Path(checkpoint.alpha_artifact_id)

        if factor_path.exists():
            factor_path.unlink()
        if alpha_path.exists():
            alpha_path.unlink()

        with self.store.transaction() as conn:
            conn.execute("""
                DELETE FROM research_partition_checkpoints
                WHERE checkpoint_id=?
            """, (checkpoint.checkpoint_id,))

    @staticmethod
    def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
        """写入 Parquet 文件（压缩）"""
        if not rows:
            # 空数据：写入空 Parquet
            schema = pa.schema([
                ("available_time", pa.string()),
                ("instrument_id", pa.string()),
            ])
            table = pa.table({}, schema=schema)
        else:
            table = pa.Table.from_pylist(rows)

        pq.write_table(
            table,
            path,
            compression="zstd",  # Zstandard 压缩（快速 + 高压缩率）
            compression_level=3,
        )

    @staticmethod
    def _read_parquet(path: Path) -> list[dict[str, Any]]:
        """读取 Parquet 文件"""
        table = pq.read_table(path)
        return table.to_pylist()

    @staticmethod
    def _compute_verification_hash(factor_path: Path, alpha_path: Path) -> str:
        """计算验证哈希（SHA256）"""
        hasher = hashlib.sha256()

        # 读取 Factor 文件
        with factor_path.open("rb") as f:
            hasher.update(f.read())

        # 读取 Alpha 文件
        with alpha_path.open("rb") as f:
            hasher.update(f.read())

        return hasher.hexdigest()
