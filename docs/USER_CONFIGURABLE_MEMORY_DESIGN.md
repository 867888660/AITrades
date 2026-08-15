# 用户可配置内存上限设计方案

## 核心理念

**让用户配置，但保留安全边界：**
- 用户可以根据场景调整内存预算（白天保守、夜间激进）
- 系统自动检测硬件并给出智能默认值
- 后端强制安全上限，防止配置失误导致系统崩溃

## 三层内存管理架构

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: 系统物理边界 (System Hard Limit)                │
│  ─────────────────────────────────────────────────────  │
│  32 GB 物理内存                                           │
│  - 6 GB  Windows/基础环境 (不可侵占)                      │
│  - 2 GB  Web + DB (前端保留)                             │
│  - 8 GB  Emergency Reserve (强制保留)                     │
│  ────────────────────────────────                        │
│  = 16 GB 最大可分配给研究                                  │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│  Layer 2: 用户全局配置 (User Global Preference)           │
│  ─────────────────────────────────────────────────────  │
│  用户在前端设置："研究内存预算 = 12 GB"                     │
│  (必须 ≤ Layer 1 的 16 GB)                               │
│                                                         │
│  配置模式：                                               │
│  • 自动模式 (推荐)：系统动态分配 ≤ 12 GB                   │
│  • 手动模式：用户明确指定每种任务的内存                     │
└─────────────────────────────────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────────┐
│  Layer 3: 单次任务级别 (Per-Task Override)                │
│  ─────────────────────────────────────────────────────  │
│  Agent 提交任务时可选覆盖：                                │
│  "这次实验最多用 8 GB"                                     │
│  (必须 ≤ Layer 2 的 12 GB)                               │
└─────────────────────────────────────────────────────────┘
```

## 前端配置界面设计

### 1. 系统状态卡片（顶部显著位置）

```
┌────────────────────────────────────────────────────┐
│  💻 研究引擎资源状态                                │
│                                                    │
│  ┌──────────────────────────────────────────┐    │
│  │  物理内存: 32 GB                          │    │
│  │  ▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 25%  │    │
│  │                                           │    │
│  │  系统保留:    6 GB  ▓▓░░░░░░░░░░          │    │
│  │  前端/数据库: 2 GB  ▓░░░░░░░░░░░          │    │
│  │  安全缓冲:    8 GB  ▓▓▓▓░░░░░░░░          │    │
│  │  ──────────────────────────────────       │    │
│  │  可用于研究: 16 GB                         │    │
│  └──────────────────────────────────────────┘    │
│                                                    │
│  当前研究占用: 4.2 GB  ▓▓░░░░░░░░░░░░░░ 26%      │
│  - Job #1234: 3.8 GB (RUNNING)                   │
│  - Job #1235: 0.4 GB (QUEUED)                    │
└────────────────────────────────────────────────────┘
```

### 2. 内存配置面板

```
┌────────────────────────────────────────────────────┐
│  ⚙️ 研究内存预算配置                                │
│                                                    │
│  配置模式:                                         │
│  ○ 自动模式 (推荐)                                 │
│     系统根据任务规模自动分配，最大不超过设定值       │
│                                                    │
│  ● 手动模式                                        │
│     明确指定不同类型任务的内存上限                  │
│                                                    │
│  ┌──────────────────────────────────────────┐    │
│  │  研究内存总预算                            │    │
│  │  ├─────────────────────┤                 │    │
│  │  0 GB            12 GB            16 GB   │    │
│  │                   ▲                       │    │
│  │              当前设置: 12 GB               │    │
│  │                                           │    │
│  │  说明: 系统可用于研究的最大内存为 16 GB    │    │
│  │        建议留出 4 GB 缓冲以保持系统稳定    │    │
│  └──────────────────────────────────────────┘    │
│                                                    │
│  单个任务类型限制 (手动模式):                      │
│  ┌──────────────────────────────────────────┐    │
│  │  • 轻量研究 (Light Research)              │    │
│  │    单任务最大: [2] GB                     │    │
│  │    并发数量: [2] 个                       │    │
│  │                                           │    │
│  │  • 重型研究 (Heavy Research)              │    │
│  │    单任务最大: [8] GB                     │    │
│  │    并发数量: [1] 个 (互斥)                │    │
│  │                                           │    │
│  │  • 历史回测 (Backtest)                    │    │
│  │    单任务最大: [4] GB                     │    │
│  │    并发数量: [1] 个                       │    │
│  └──────────────────────────────────────────┘    │
│                                                    │
│  [ 恢复默认值 ]  [ 应用 ]  [ 取消 ]                │
└────────────────────────────────────────────────────┘
```

### 3. 任务提交时的内存选项（可选）

```
┌────────────────────────────────────────────────────┐
│  提交研究实验                                       │
│                                                    │
│  研究目标: 2000-2025 美股全市场 Momentum Alpha     │
│  ...                                               │
│                                                    │
│  高级选项 ▼                                        │
│  ┌──────────────────────────────────────────┐    │
│  │  内存配置:                                 │    │
│  │  ○ 自动 (推荐)                            │    │
│  │     系统根据数据规模自动估算               │    │
│  │     预计需要: 约 6.5 GB                   │    │
│  │                                           │    │
│  │  ○ 手动指定                               │    │
│  │     本次实验最大内存: [8] GB              │    │
│  │     (不超过你的全局配置 12 GB)             │    │
│  └──────────────────────────────────────────┘    │
│                                                    │
│  [ 提交 ]  [ 取消 ]                                │
└────────────────────────────────────────────────────┘
```

### 4. 实时监控面板（Dashboard）

```
┌────────────────────────────────────────────────────┐
│  📊 资源监控                                        │
│                                                    │
│  研究内存占用 (实时)                                │
│  ┌──────────────────────────────────────────┐    │
│  │  ▓▓▓▓▓░░░░░░░░░░░░░░░░░░ 4.2 / 12 GB     │    │
│  └──────────────────────────────────────────┘    │
│                                                    │
│  活跃任务:                                         │
│  ┌──────────────────────────────────────────┐    │
│  │  Experiment #1234                         │    │
│  │  Momentum Alpha 2020-2025                 │    │
│  │  内存: 3.8 GB / 8.0 GB (47%)              │    │
│  │  状态: RUNNING - PARTITION_2023 (75%)     │    │
│  │  [取消] [查看详情]                         │    │
│  ├──────────────────────────────────────────┤    │
│  │  Experiment #1235                         │    │
│  │  Value Factor 2015-2020                   │    │
│  │  内存: 已分配 4.0 GB, 等待资源            │    │
│  │  状态: WAITING_RESOURCE (队列位置: 1/3)   │    │
│  │  [取消] [查看详情]                         │    │
│  └──────────────────────────────────────────┘    │
│                                                    │
│  近 24 小时内存使用趋势:                            │
│  ┌──────────────────────────────────────────┐    │
│  │  12 GB ┤                                  │    │
│  │   8 GB ┤     ╱‾‾╲                         │    │
│  │   4 GB ┤  ╱‾╯    ╲___╱‾╲__               │    │
│  │   0 GB └─────────────────────────        │    │
│  │         00:00   06:00   12:00   18:00    │    │
│  └──────────────────────────────────────────┘    │
└────────────────────────────────────────────────────┘
```

## 后端实现：三层验证

### 1. 系统配置表（SQLite）

```sql
CREATE TABLE IF NOT EXISTS system_resource_config(
    config_id TEXT PRIMARY KEY DEFAULT 'singleton',
    -- Layer 1: 系统硬限制
    physical_memory_mb INTEGER NOT NULL,
    system_reserve_mb INTEGER NOT NULL,      -- Windows/基础环境
    frontend_reserve_mb INTEGER NOT NULL,    -- Web + DB
    emergency_reserve_mb INTEGER NOT NULL,   -- 安全缓冲
    max_research_budget_mb INTEGER NOT NULL, -- = physical - reserves
    
    -- Layer 2: 用户全局配置
    user_research_budget_mb INTEGER NOT NULL,
    user_config_mode TEXT NOT NULL DEFAULT 'AUTO', -- 'AUTO' | 'MANUAL'
    user_light_worker_mb INTEGER,
    user_heavy_worker_mb INTEGER,
    user_backtest_worker_mb INTEGER,
    user_standard_worker_limit INTEGER,
    
    -- 元数据
    auto_detected_at TEXT,
    last_updated_by TEXT,
    last_updated_at TEXT,
    
    CHECK (user_research_budget_mb <= max_research_budget_mb),
    CHECK (user_config_mode IN ('AUTO', 'MANUAL'))
);

-- 默认配置记录
INSERT OR IGNORE INTO system_resource_config(
    config_id,
    physical_memory_mb,
    system_reserve_mb,
    frontend_reserve_mb,
    emergency_reserve_mb,
    max_research_budget_mb,
    user_research_budget_mb,
    user_config_mode,
    auto_detected_at
) VALUES (
    'singleton',
    32768,  -- 32 GB (自动检测)
    6144,   -- 6 GB
    2048,   -- 2 GB
    8192,   -- 8 GB
    16384,  -- 16 GB = 32 - 6 - 2 - 8
    12288,  -- 12 GB (默认留 4 GB 缓冲)
    'AUTO',
    datetime('now')
);
```

### 2. 资源配置服务

```python
# services/data_platform/resource_config_service.py
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Any

from .store import DataPlatformStore, utc_now


@dataclass(frozen=True)
class ResourceConfig:
    """完整的三层资源配置"""
    # Layer 1: 系统边界
    physical_memory_mb: int
    system_reserve_mb: int
    frontend_reserve_mb: int
    emergency_reserve_mb: int
    max_research_budget_mb: int
    
    # Layer 2: 用户配置
    user_research_budget_mb: int
    user_config_mode: str  # 'AUTO' | 'MANUAL'
    user_light_worker_mb: int | None
    user_heavy_worker_mb: int | None
    user_backtest_worker_mb: int | None
    user_standard_worker_limit: int | None
    
    # 计算属性
    @property
    def effective_research_budget_mb(self) -> int:
        """实际可用的研究预算 = min(用户配置, 系统上限)"""
        return min(self.user_research_budget_mb, self.max_research_budget_mb)
    
    @property
    def current_available_mb(self) -> int:
        """当前可用内存（实时检测）"""
        _, available = _physical_memory_mb()
        # 减去前端保留 + 安全缓冲
        return max(0, available - self.frontend_reserve_mb - self.emergency_reserve_mb)
    
    def validate_user_request(self, requested_mb: int) -> tuple[bool, str]:
        """验证用户请求是否在允许范围内"""
        if requested_mb > self.effective_research_budget_mb:
            return False, f"请求内存 {requested_mb} MB 超过用户配置上限 {self.effective_research_budget_mb} MB"
        if requested_mb > self.max_research_budget_mb:
            return False, f"请求内存 {requested_mb} MB 超过系统硬限制 {self.max_research_budget_mb} MB"
        if requested_mb > self.current_available_mb:
            return False, f"当前可用内存仅 {self.current_available_mb} MB，请求 {requested_mb} MB 暂时无法满足"
        return True, "OK"


class ResourceConfigService:
    def __init__(self, store: DataPlatformStore):
        self.store = store
    
    def get(self) -> ResourceConfig:
        """读取当前配置"""
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM system_resource_config WHERE config_id='singleton'"
            ).fetchone()
        if not row:
            # 首次运行，自动检测并初始化
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
        """更新用户配置（Layer 2）"""
        current = self.get()
        
        # 验证新配置
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
        
        # 构建更新语句
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
        """首次运行：自动检测硬件并初始化"""
        physical_mb, _ = _physical_memory_mb()
        
        # 保守估算各项保留
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
        max_research = max(512, max_research)  # 至少 512 MB
        
        # 用户默认预算：留出 25% 缓冲
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
        """获取实时资源快照（供前端仪表盘使用）"""
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
                "used_percent": int(((physical_total - physical_available) / physical_total) * 100),
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
    """返回 (总内存 MB, 可用内存 MB)"""
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
            MIB = 1024 * 1024
            return int(status.ullTotalPhys / MIB), int(status.ullAvailPhys / MIB)
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total = int(os.sysconf("SC_PHYS_PAGES")) * page_size
        available = int(os.sysconf("SC_AVPHYS_PAGES")) * page_size
        MIB = 1024 * 1024
        return int(total / MIB), int(available / MIB)
    except (AttributeError, OSError, ValueError):
        return 0, 0
```

### 3. 集成到智能路由

```python
# services/data_platform/workload_scheduler.py (修改)
class IntelligentWorkloadRouter:
    def __init__(self, resource_config: ResourceConfig | None = None):
        self.resource_config = resource_config
    
    def route_research(self, plan: WorkloadPlan) -> RoutingDecision:
        # 1. 检查是否超出用户配置上限
        if self.resource_config:
            allowed, reason = self.resource_config.validate_user_request(
                plan.worker_memory_mb
            )
            if not allowed:
                return RoutingDecision(
                    priority=self.PRIORITY_HEAVY_RESEARCH,
                    workload_kind="HEAVY_RESEARCH",
                    resource_class="HEAVY",
                    execution_mode="WAITING_USER_CONFIG",
                    worker_memory_mb=plan.worker_memory_mb,
                    estimated_working_set_mb=plan.estimated_working_set_mb,
                    checkpoint_enabled=False,
                    dispatch_policy="WAIT_OR_PARTITION",
                    reason_code=f"USER_BUDGET_EXCEEDED: {reason}",
                )
        
        # 2. 如果用户是手动模式，使用用户指定的 Worker 大小
        if self.resource_config and self.resource_config.user_config_mode == "MANUAL":
            if plan.resource_class == "STANDARD" and self.resource_config.user_light_worker_mb:
                plan = WorkloadPlan(
                    **{**plan.to_dict(), "worker_memory_mb": self.resource_config.user_light_worker_mb}
                )
            elif plan.resource_class == "HEAVY" and self.resource_config.user_heavy_worker_mb:
                plan = WorkloadPlan(
                    **{**plan.to_dict(), "worker_memory_mb": self.resource_config.user_heavy_worker_mb}
                )
        
        # 3. 继续原有逻辑
        if plan.hard_limit_exceeded:
            return RoutingDecision(
                priority=self.PRIORITY_HEAVY_RESEARCH,
                workload_kind="HEAVY_RESEARCH",
                resource_class="HEAVY",
                execution_mode="PARTITIONED_REQUIRED",
                worker_memory_mb=plan.worker_memory_mb,
                estimated_working_set_mb=plan.estimated_working_set_mb,
                checkpoint_enabled=True,
                dispatch_policy="PREFLIGHT_ONLY",
                reason_code="PARTITIONED_EXECUTION_REQUIRED",
            )
        ...
```

### 4. 前端 API 接口

```python
# app.py (新增路由)
from services.data_platform.resource_config_service import ResourceConfigService

@app.route("/api/resource-config", methods=["GET"])
def get_resource_config():
    """获取当前资源配置"""
    service = ResourceConfigService(data_store)
    config = service.get()
    snapshot = service.get_runtime_snapshot()
    return jsonify({
        "config": {
            "physical_memory_mb": config.physical_memory_mb,
            "max_research_budget_mb": config.max_research_budget_mb,
            "user_research_budget_mb": config.user_research_budget_mb,
            "user_config_mode": config.user_config_mode,
            "user_light_worker_mb": config.user_light_worker_mb,
            "user_heavy_worker_mb": config.user_heavy_worker_mb,
            "user_backtest_worker_mb": config.user_backtest_worker_mb,
            "user_standard_worker_limit": config.user_standard_worker_limit,
        },
        "runtime": snapshot,
    })

@app.route("/api/resource-config", methods=["PUT"])
def update_resource_config():
    """更新用户资源配置"""
    payload = request.get_json() or {}
    service = ResourceConfigService(data_store)
    
    try:
        updated = service.update_user_config(
            user_research_budget_mb=payload.get("user_research_budget_mb"),
            user_config_mode=payload.get("user_config_mode"),
            user_light_worker_mb=payload.get("user_light_worker_mb"),
            user_heavy_worker_mb=payload.get("user_heavy_worker_mb"),
            user_backtest_worker_mb=payload.get("user_backtest_worker_mb"),
            user_standard_worker_limit=payload.get("user_standard_worker_limit"),
            actor="web_user",
        )
        return jsonify({
            "status": "success",
            "message": "资源配置已更新",
            "config": {
                "user_research_budget_mb": updated.user_research_budget_mb,
                "user_config_mode": updated.user_config_mode,
            },
        })
    except ValueError as exc:
        return jsonify({
            "status": "error",
            "message": str(exc),
        }), 400

@app.route("/api/resource-config/snapshot", methods=["GET"])
def get_resource_snapshot():
    """实时资源快照（供仪表盘轮询）"""
    service = ResourceConfigService(data_store)
    snapshot = service.get_runtime_snapshot()
    return jsonify(snapshot)
```

## 使用场景示例

### 场景 1: 白天保守模式

```
用户上午 9 点开始工作：
1. 前端设置：研究内存预算 = 4 GB
2. 提交小型实验（3 年数据）：自动分配 2 GB，立即执行
3. 提交大型实验（25 年数据）：预计需要 8 GB
   → 系统提示："此实验预计需要 8 GB 内存，超过你的当前预算 4 GB。
               建议：调整预算至 10 GB，或在晚间执行。"
4. 用户选择："加入队列，晚上自动执行"
```

### 场景 2: 夜间激进模式

```
用户晚上 10 点离开：
1. 前端设置：研究内存预算 = 14 GB（几乎用满）
2. 提交 3 个大型实验（各需 8 GB）
3. 系统自动排队：
   - Experiment #1: 立即执行（8 GB）
   - Experiment #2: 等待资源（队列 #1）
   - Experiment #3: 等待资源（队列 #2）
4. 第二天早上 8 点：
   - 自动降回白天模式：预算 = 4 GB
   - 如果还有任务在跑，保持运行，但不接受新的大任务
```

### 场景 3: 任务级覆盖

```
Agent 提交任务：
{
  "candidate": {...},
  "resource_hint": {
    "max_memory_mb": 6000,  // 本次最多用 6 GB
    "priority": "HIGH"
  }
}

系统处理：
1. 检查 6000 MB ≤ 用户配置 (12 GB) ✓
2. 检查 6000 MB ≤ 系统上限 (16 GB) ✓
3. 检查当前可用 (10 GB 空闲) ✓
4. 接受任务，分配 6 GB Worker
```

## 安全保护机制

### 1. 强制上限验证

```python
def validate_request(self, requested_mb: int) -> tuple[bool, str]:
    """三层验证"""
    # Layer 1: 系统物理边界
    if requested_mb > self.max_research_budget_mb:
        return False, "SYSTEM_HARD_LIMIT_EXCEEDED"
    
    # Layer 2: 用户全局配置
    if requested_mb > self.user_research_budget_mb:
        return False, "USER_BUDGET_EXCEEDED"
    
    # Layer 3: 当前实时可用
    if requested_mb > self.current_available_mb:
        return False, "INSUFFICIENT_AVAILABLE_MEMORY"
    
    return True, "OK"
```

### 2. 自动降级

```python
# 如果用户配置的总预算超出实际可用
if config.user_research_budget_mb > snapshot.current_available_mb:
    # 自动临时降级，不修改用户配置
    effective_budget = snapshot.current_available_mb
    _emit_warning(
        "用户配置的研究预算为 {config.user_research_budget_mb} MB，"
        "但当前可用内存仅 {snapshot.current_available_mb} MB。"
        "系统已临时降级至可用内存上限。"
    )
```

### 3. 实时监控告警

```python
# 后台监控线程
def monitor_memory_pressure():
    while True:
        snapshot = service.get_runtime_snapshot()
        usage_percent = snapshot["research"]["active_percent"]
        
        if usage_percent > 90:
            _emit_alert("研究内存占用 {usage_percent}%，接近上限")
        elif usage_percent > 95:
            _emit_critical("研究内存占用 {usage_percent}%，暂停接受新任务")
            # 自动暂停新任务接受，直到降到 80% 以下
```

## 总结

### 三层架构优势

1. **灵活性**：用户可根据场景调整
2. **安全性**：后端强制上限，用户配置失误也不会崩溃
3. **透明性**：实时可视化，用户知道资源去哪了
4. **智能化**：自动检测硬件，给出合理默认值

### 与分区执行方案的协同

```
用户配置 8 GB → 系统估算需要 12 GB
↓
提示用户两个选择：
1. 调高预算至 12 GB，整体执行（30 分钟）
2. 保持 8 GB，自动分区执行（45 分钟，但不影响其他任务）
```

### 下一步

1. 实现 `ResourceConfigService` 和 SQLite schema
2. 实现前端配置界面和实时监控面板
3. 集成到现有 `IntelligentWorkloadRouter`
4. 添加配置变更审计日志

这样用户既有灵活性，又不会因误配置导致系统崩溃。你觉得这个方案如何？
