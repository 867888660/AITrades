#!/usr/bin/env python3
"""
DataTube Checkpoint Manager CLI

管理分区执行的 Checkpoint：查看、清理、验证

Usage:
    python checkpoint_cli.py list [bundle_hash]
    python checkpoint_cli.py cleanup [bundle_hash] --days 7
    python checkpoint_cli.py verify [bundle_hash]
    python checkpoint_cli.py stats
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from services.data_platform import CheckpointManager, DataPlatformStore


def format_size(bytes_val: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"


def list_checkpoints(manager: CheckpointManager, bundle_hash: str | None = None) -> None:
    """列出 Checkpoint"""
    store = manager.store

    if bundle_hash:
        # 列出特定 Bundle 的 Checkpoint
        checkpoints = manager.list_completed(bundle_hash)

        if not checkpoints:
            print(f"No checkpoints found for bundle: {bundle_hash}")
            return

        print(f"\nCheckpoints for bundle: {bundle_hash[:8]}...")
        print("─" * 80)
        print(f"{'Partition ID':<20} {'Rows':>10} {'Completed At':<25} {'Valid'}")
        print("─" * 80)

        for cp in checkpoints:
            # 获取文件大小
            factor_path = Path(cp.factor_artifact_id)
            alpha_path = Path(cp.alpha_artifact_id)
            total_size = 0
            if factor_path.exists():
                total_size += factor_path.stat().st_size
            if alpha_path.exists():
                total_size += alpha_path.stat().st_size

            valid = "✓" if manager._verify_checkpoint(cp) else "✗"
            print(f"{cp.partition_id:<20} {cp.row_count:>10,} {cp.completed_at:<25} {valid}")

        print("─" * 80)
        print(f"Total: {len(checkpoints)} partitions")

    else:
        # 列出所有 Bundle 的统计
        with store.connection() as conn:
            results = conn.execute("""
                SELECT
                    bundle_hash,
                    COUNT(*) as partition_count,
                    SUM(row_count) as total_rows,
                    MAX(completed_at) as last_completed
                FROM research_partition_checkpoints
                GROUP BY bundle_hash
                ORDER BY last_completed DESC
            """).fetchall()

        if not results:
            print("No checkpoints found.")
            return

        print("\nAll Checkpoint Bundles")
        print("─" * 90)
        print(f"{'Bundle Hash':<12} {'Partitions':>10} {'Total Rows':>15} {'Last Completed'}")
        print("─" * 90)

        for row in results:
            print(
                f"{row['bundle_hash'][:10]:<12} "
                f"{row['partition_count']:>10} "
                f"{row['total_rows']:>15,} "
                f"{row['last_completed']}"
            )

        print("─" * 90)
        print(f"Total: {len(results)} bundles")


def cleanup_checkpoints(
    manager: CheckpointManager,
    bundle_hash: str | None = None,
    keep_days: int = 7,
) -> None:
    """清理过期 Checkpoint"""
    store = manager.store

    if bundle_hash:
        # 清理特定 Bundle
        deleted_count = manager.cleanup(bundle_hash, keep_days)
        print(f"Cleaned up {deleted_count} checkpoints for bundle {bundle_hash[:8]}...")

    else:
        # 清理所有 Bundle
        with store.connection() as conn:
            bundle_hashes = conn.execute("""
                SELECT DISTINCT bundle_hash
                FROM research_partition_checkpoints
            """).fetchall()

        total_deleted = 0
        for row in bundle_hashes:
            deleted = manager.cleanup(row["bundle_hash"], keep_days)
            if deleted > 0:
                print(f"Bundle {row['bundle_hash'][:10]}: deleted {deleted} checkpoints")
                total_deleted += deleted

        print(f"\nTotal cleaned up: {total_deleted} checkpoints")


def verify_checkpoints(manager: CheckpointManager, bundle_hash: str) -> None:
    """验证 Checkpoint 完整性"""
    checkpoints = manager.list_completed(bundle_hash)

    if not checkpoints:
        print(f"No checkpoints found for bundle: {bundle_hash}")
        return

    print(f"\nVerifying checkpoints for bundle: {bundle_hash[:8]}...")
    print("─" * 80)

    valid_count = 0
    invalid_count = 0

    for cp in checkpoints:
        is_valid = manager._verify_checkpoint(cp)
        status = "✓ VALID" if is_valid else "✗ INVALID"

        print(f"{cp.partition_id:<20} {status}")

        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1

    print("─" * 80)
    print(f"Valid: {valid_count}, Invalid: {invalid_count}")

    if invalid_count > 0:
        print("\nInvalid checkpoints will be automatically removed on next access.")


def show_stats(manager: CheckpointManager) -> None:
    """显示 Checkpoint 统计信息"""
    store = manager.store

    with store.connection() as conn:
        # 总体统计
        stats = conn.execute("""
            SELECT
                COUNT(DISTINCT bundle_hash) as bundle_count,
                COUNT(*) as checkpoint_count,
                SUM(row_count) as total_rows
            FROM research_partition_checkpoints
        """).fetchone()

        # 最近活动
        recent = conn.execute("""
            SELECT
                bundle_hash,
                partition_id,
                completed_at
            FROM research_partition_checkpoints
            ORDER BY completed_at DESC
            LIMIT 5
        """).fetchall()

    print("\n" + "=" * 60)
    print("DataTube Checkpoint Statistics")
    print("=" * 60)

    print(f"\nOverall:")
    print(f"  Bundles:     {stats['bundle_count']:>10,}")
    print(f"  Checkpoints: {stats['checkpoint_count']:>10,}")
    print(f"  Total Rows:  {stats['total_rows']:>10,}")

    # 计算磁盘占用
    checkpoint_root = manager.checkpoint_root
    if checkpoint_root.exists():
        total_size = sum(
            f.stat().st_size
            for f in checkpoint_root.rglob("*.parquet")
            if f.is_file()
        )
        print(f"  Disk Usage:  {format_size(total_size):>10}")

    if recent:
        print("\nRecent Activity:")
        for row in recent:
            print(
                f"  {row['bundle_hash'][:10]} / {row['partition_id']:<20} "
                f"@ {row['completed_at']}"
            )

    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DataTube Checkpoint Manager CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list command
    list_parser = subparsers.add_parser("list", help="List checkpoints")
    list_parser.add_argument("bundle_hash", nargs="?", help="Bundle hash (optional)")

    # cleanup command
    cleanup_parser = subparsers.add_parser("cleanup", help="Cleanup old checkpoints")
    cleanup_parser.add_argument("bundle_hash", nargs="?", help="Bundle hash (optional)")
    cleanup_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Keep checkpoints newer than N days (default: 7)",
    )

    # verify command
    verify_parser = subparsers.add_parser("verify", help="Verify checkpoint integrity")
    verify_parser.add_argument("bundle_hash", help="Bundle hash")

    # stats command
    subparsers.add_parser("stats", help="Show checkpoint statistics")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 初始化
    store = DataPlatformStore()
    checkpoint_root = Path(store.db_path).parent / "research_checkpoints"
    manager = CheckpointManager(store, checkpoint_root)

    # 执行命令
    try:
        if args.command == "list":
            list_checkpoints(manager, args.bundle_hash)

        elif args.command == "cleanup":
            cleanup_checkpoints(manager, args.bundle_hash, args.days)

        elif args.command == "verify":
            verify_checkpoints(manager, args.bundle_hash)

        elif args.command == "stats":
            show_stats(manager)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
