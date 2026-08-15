from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass, asdict
from typing import Any

from .store import DataPlatformStore, utc_now


MIB = 1024 * 1024


@dataclass(frozen=True)
class ResourceConfig:
    """Complete three-layer resource configuration."""
    # Layer 1: System boundaries
    physical_memory_mb: int
    system_reserve_mb: int
    frontend_reserve_mb: int
    emergency_reserve_mb: int
    max_research_budget_mb: int

    # Layer 2: User configuration
    user_research_budget_mb: int
    user_config_mode: str  # 'AUTO' | 'MANUAL'
    user_light_worker_mb: int | None
    user_heavy_worker_mb: int | None
    user_backtest_worker_mb: int | None
    user_standard_worker_limit: int | None

    @property
    def effective_research_budget_mb(self) -> int:
        """Actual usable research budget = min(user config, system limit)"""
        return min(self.user_research_budget_mb, self.max_research_budget_mb)

    @property
    def current_available_mb(self) -> int:
        """Current available memory (real-time detection)"""
        _, available = _physical_memory_mb()
        # Subtract frontend reserve + emergency buffer
        return max(0, available - self.frontend_reserve_mb - self.emergency_reserve_mb)

    def validate_user_request(self, requested_mb: int) -> tuple[bool, str]:
        """Validate if user request is within allowed range"""
        if requested_mb > self.effective_research_budget_mb:
            return False, f"请求内存 {requested_mb} MB 超过用户配置上限 {self.effective_research_budget_mb} MB"
        if requested_mb > self.max_research_budget_mb:
            return False, f"请求内存 {requested_mb} MB 超过系统硬限制 {self.max_research_budget_mb} MB"
        if requested_mb > self.current_available_mb:
            return False, f"当前可用内存仅 {self.current_available_mb} MB，请求 {requested_mb} MB 暂时无法满足"
        return True, "OK"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResourceConfigService:
    """Manage user-configurable resource budgets."""

    def __init__(self, store: DataPlatformStore):
        self.store = store

    def get(self) -> ResourceConfig:
        """Read current configuration"""
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM system_resource_config WHERE config_id='singleton'"
            ).fetchone()
        if not row:
            # First run: auto-detect hardware and initialize
            return self._initialize_default()
        return ResourceConfig(**dict(row))

    def update_user_config(
        self,
        *,
        user_research_budget_mb: int | None = None,
        user_config_mode: str | None = None,
        user_light_worker_mb: int | None = None,
        user_heavy_worker_mb: int | None = None,
        user_backtest_worker_mb: int | None = None,
        user_standard_worker_limit: int | None = None,
        actor: str = "web_user",
    ) -> ResourceConfig:
        """Update user configuration (Layer 2)"""
        current = self.get()

        # Validate new configuration
        if user_research_budget_mb is not None:
            if user_research_budget_mb > current.max_research_budget_mb:
                raise ValueError(
                    f"用户预算 {user_research_budget_mb} MB 超过系统上限 {current.max_research_budget_mb} MB。"
                    f"系统可用于研究的最大内存为 {current.max_research_budget_mb} MB。"
                )
            if user_research_budget_mb < 512:
                raise ValueError("研究内存预算不能低于 512 MB")

        if user_config_mode is not None and user_config_mode not in {"AUTO", "MANUAL"}:
            raise ValueError("配置模式必须是 AUTO 或 MANUAL")

        # Build update statement
        updates: dict[str, Any] = {
            "last_updated_by": actor,
            "last_updated_at": utc_now(),
        }
        if user_research_budget_mb is not None:
            updates["user_research_budget_mb"] = user_research_budget_mb
        if user_config_mode is not None:
            updates["user_config_mode"] = user_config_mode
        if user_light_worker_mb is not None:
            updates["user_light_worker_mb"] = user_light_worker_mb
        if user_heavy_worker_mb is not None:
            updates["user_heavy_worker_mb"] = user_heavy_worker_mb
        if user_backtest_worker_mb is not None:
            updates["user_backtest_worker_mb"] = user_backtest_worker_mb
        if user_standard_worker_limit is not None:
            updates["user_standard_worker_limit"] = user_standard_worker_limit

        with self.store.transaction(immediate=True) as conn:
            set_clause = ", ".join(f"{key}=?" for key in updates.keys())
            conn.execute(
                f"UPDATE system_resource_config SET {set_clause} WHERE config_id='singleton'",
                tuple(updates.values()),
            )

        return self.get()

    def _initialize_default(self) -> ResourceConfig:
        """First run: auto-detect hardware and initialize"""
        physical_mb, _ = _physical_memory_mb()

        # Conservative estimates for various reserves
        if physical_mb >= 32768:  # 32 GB+
            system_reserve = 6144
            frontend_reserve = 2048
            emergency_reserve = 8192
        elif physical_mb >= 16384:  # 16 GB
            system_reserve = 4096
            frontend_reserve = 2048
            emergency_reserve = 4096
        else:  # < 16 GB
            system_reserve = 2048
            frontend_reserve = 1024
            emergency_reserve = 2048

        max_research = physical_mb - system_reserve - frontend_reserve - emergency_reserve
        max_research = max(512, max_research)  # At least 512 MB

        # User default budget: leave 25% buffer
        user_budget = int(max_research * 0.75)

        with self.store.transaction(immediate=True) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO system_resource_config(
                    config_id, physical_memory_mb, system_reserve_mb,
                    frontend_reserve_mb, emergency_reserve_mb, max_research_budget_mb,
                    user_research_budget_mb, user_config_mode, auto_detected_at
                ) VALUES ('singleton', ?, ?, ?, ?, ?, ?, 'AUTO', ?)
            """, (
                physical_mb, system_reserve, frontend_reserve, emergency_reserve,
                max_research, user_budget, utc_now()
            ))

        return self.get()

    def get_runtime_snapshot(self) -> dict[str, Any]:
        """Get real-time resource snapshot (for frontend dashboard)"""
        config = self.get()
        physical_total, physical_available = _physical_memory_mb()

        from .workload_scheduler import ResourceAdmissionController
        active = ResourceAdmissionController.active_snapshot()

        active_mb = sum(item["worker_memory_mb"] for item in active.values())

        return {
            "physical": {
                "total_mb": physical_total,
                "available_mb": physical_available,
                "used_mb": physical_total - physical_available,
                "used_percent": int(((physical_total - physical_available) / physical_total) * 100) if physical_total else 0,
            },
            "reserves": {
                "system_mb": config.system_reserve_mb,
                "frontend_mb": config.frontend_reserve_mb,
                "emergency_mb": config.emergency_reserve_mb,
                "total_reserved_mb": (
                    config.system_reserve_mb +
                    config.frontend_reserve_mb +
                    config.emergency_reserve_mb
                ),
            },
            "research": {
                "max_budget_mb": config.max_research_budget_mb,
                "user_budget_mb": config.user_research_budget_mb,
                "effective_budget_mb": config.effective_research_budget_mb,
                "active_mb": active_mb,
                "active_percent": int((active_mb / config.effective_research_budget_mb) * 100) if config.effective_research_budget_mb else 0,
                "available_now_mb": max(0, config.current_available_mb - active_mb),
            },
            "config_mode": config.user_config_mode,
            "active_workers": active,
        }


def _physical_memory_mb() -> tuple[int, int]:
    """Return (total memory MB, available memory MB)"""
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys / MIB), int(status.ullAvailPhys / MIB)
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total = int(os.sysconf("SC_PHYS_PAGES")) * page_size
        available = int(os.sysconf("SC_AVPHYS_PAGES")) * page_size
        return int(total / MIB), int(available / MIB)
    except (AttributeError, OSError, ValueError):
        # Conservative fallback: close admission rather than risk the UI
        return 0, 0


__all__ = [
    "ResourceConfig",
    "ResourceConfigService",
]
