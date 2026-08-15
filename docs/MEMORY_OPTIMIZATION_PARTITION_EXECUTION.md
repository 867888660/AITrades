# DataTube 内存优化与分区执行方案

## 执行摘要

目标：让 DataTube 后端能够自动切分任务，使任何规模的研究都能安全执行，内存永远够用。

**核心思想**：将大型历史研究从"一次性全部加载"改为"按时间分区 + 滚动窗口"，每个分区独立执行、持久化、释放内存，然后继续下一个。

## 当前问题诊断

### 1. 根本原因：全量物化（Full Materialization）

当前架构在多个层次存在全量物化问题：

**数据加载层**：
```python
# services/data_platform/data_client.py:308
for row in batch.to_pylist():  # ← Arrow → Python dict 转换
    if requested_columns is not None:
        row = {column: row.get(column) for column in requested_columns}
    yield row
```

**数据处理层**：
```python
# services/data_platform/equity_factor_bridge.py:165
copied = [dict(row) for row in rows]  # ← 额外复制整个数据集

# services/data_platform/equity_monthly_research.py:583
panel_rows = panel_table.to_pylist()  # ← 全表转 Python 对象
```

**问题链**：
1. 从 Parquet 读取 25 年 × 5000 只股票 × 250 交易日 = ~31M 行
2. Arrow Table → Python list[dict]（内存膨胀 10-50x）
3. 按证券分组 → 再复制一次
4. Factor 计算 → 每个 Factor 可能再保留一份
5. Alpha 组合 → 又是完整副本
6. 峰值内存 = 原始数据 × (1 + 10 + 5 + 3 + 2) ≈ **21x 原始数据**

**实际测算**：
- 5 年全市场 CRSP 数据：~4GB Parquet → ~12GB RAM（安全）
- 25 年全市场数据：~20GB Parquet → **60-80GB RAM**（OOM）

### 2. 现有智能路由已完成 80%

✅ 已实现：
- 工作负载估算（ResearchWorkloadPlanner）
- 自动资源分类（LIGHT/HEAVY/PARTITIONED_REQUIRED）
- 准入控制（ResourceAdmissionController）
- 互斥执行（Heavy 任务排队）
- 前端内存保留（6GB reserve）
- 隔离执行（子进程 + 内存限制）

❌ 缺失：
- **分区执行引擎**（声明需要，但未实现）
- **Checkpoint 恢复**
- **Universe Membership Timeline 预编译**
- **流式 Factor/Alpha 计算**

当前行为：
```python
# workload_scheduler.py:148
if plan.hard_limit_exceeded:
    return RoutingDecision(
        execution_mode="PARTITIONED_REQUIRED",
        dispatch_policy="PREFLIGHT_ONLY",  # ← 提前失败，不执行
        reason_code="PARTITIONED_EXECUTION_REQUIRED",
    )
```

结果：大型任务快速失败，Agent 收到 `SYSTEM_BLOCKED`。

## 三层优化方案

### Level 1: 零代码改动 —— 立即提升 2-3x（1 周）

**目标**：在不改变执行流程的前提下，减少不必要的内存复制。

#### 1.1 消除 dict(row) 冗余复制

```python
# 当前：services/data_platform/equity_factor_bridge.py:165
def _project_if_needed(dataset: str, field: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    copied = [dict(row) for row in rows]  # ← 不必要的深拷贝
    ...
    return copied

# 优化：只在真正需要修改时才复制
def _project_if_needed(dataset: str, field: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dataset = _clean(dataset).lower()
    field = _clean(field).lower()
    if dataset != "fundamentals" or (rows and field in rows[0]):
        return rows  # ← 直接返回原始引用
    # 只有需要投影时才复制
    return _project_fundamental_rows(field, [dict(row) for row in rows])
```

#### 1.2 延迟物化：保持 Arrow 到最后一刻

```python
# 当前：data_client.py 立即转 pylist
def iter_rows(...):
    for batch in parquet.iter_batches(batch_size=65_536):
        for row in batch.to_pylist():  # ← 过早转换
            yield row

# 优化：批量保持 Arrow，只在需要字段访问时转换
def iter_rows(...):
    for batch in parquet.iter_batches(batch_size=65_536):
        if requested_columns:
            batch = batch.select(requested_columns)
        # 如果下游能接受 RecordBatch，直接传递
        yield batch  # ← 保持 Arrow 格式
```

#### 1.3 显式内存释放

```python
# 在大型处理后立即释放
def _advance_locked(self, experiment_id: str) -> dict[str, Any]:
    ...
    if current["status"] == "RUNNING":
        result = self._execute_research(current)
        del current  # ← 显式删除不再需要的对象
        import gc; gc.collect()  # ← 强制回收
    return result
```

**预期效果**：
- 峰值内存：60GB → **20-25GB**
- 现有 8GB Worker 可处理 15 年全市场（当前仅能 5 年）

---

### Level 2: 分区执行引擎 —— 突破内存上限（3-4 周）

**目标**：实现年度分区 + 滚动窗口，使内存需求与历史长度解耦。

#### 2.1 分区执行架构

```
原始模式（Current）:
┌─────────────────────────────────────┐
│  Load 2000-2025 (31M rows)          │
│           ↓                         │
│  Calculate All Factors              │
│           ↓                         │
│  Calculate All Alphas               │
│           ↓                         │
│  Evaluate                           │
└─────────────────────────────────────┘
内存峰值 = f(25 years)


分区模式（Target）:
┌─────────────────────────────────────┐
│  PARTITION_2000:                    │
│    Warmup: 1999-Q4                  │
│    Execute: 2000-01-01 → 2000-12-31 │
│    Checkpoint → Disk                │
│    Release Memory                   │
├─────────────────────────────────────┤
│  PARTITION_2001:                    │
│    Warmup: 2000-Q4                  │
│    Execute: 2001-01-01 → 2001-12-31 │
│    Checkpoint → Disk                │
│    Release Memory                   │
├─────────────────────────────────────┤
│  ...                                │
├─────────────────────────────────────┤
│  PARTITION_2025:                    │
│    Warmup: 2024-Q4                  │
│    Execute: 2025-01-01 → 2025-12-31 │
│    Checkpoint → Disk                │
└─────────────────────────────────────┘
           ↓
     AGGREGATE_PHASE:
       Read All Checkpoints
       Compute Portfolio Metrics
       Persist Final Result

内存峰值 = f(1 year + warmup) ≈ 常数
```

#### 2.2 核心组件

##### A. Partition Planner

```python
# services/data_platform/partition_planner.py
@dataclass(frozen=True)
class PartitionPlan:
    partition_id: str  # "PARTITION_2020"
    calendar_start: str  # "2020-01-01"
    calendar_end: str  # "2020-12-31"
    warmup_start: str  # "2019-10-01" (252 trading days before)
    warmup_end: str  # "2019-12-31"
    estimated_rows: int
    estimated_mb: int
    checkpoint_path: Path

class ResearchPartitionPlanner:
    def plan(self, frozen_input: dict) -> tuple[PartitionPlan, ...]:
        """
        根据 history_start/history_end 自动切分年度分区。
        每个分区包含：
        - 主执行窗口（1 年）
        - Warmup 窗口（前 252 交易日或 1 年，取决于最大 Factor window）
        """
        history_start = frozen_input["history_start"][:10]
        history_end = frozen_input["history_end"][:10]
        max_window_days = self._max_factor_window(frozen_input)
        
        partitions = []
        for year in range(int(history_start[:4]), int(history_end[:4]) + 1):
            warmup_start = self._calculate_warmup_start(
                f"{year}-01-01", max_window_days
            )
            partition = PartitionPlan(
                partition_id=f"PARTITION_{year}",
                calendar_start=f"{year}-01-01",
                calendar_end=f"{year}-12-31",
                warmup_start=warmup_start,
                warmup_end=f"{year-1}-12-31",
                ...
            )
            partitions.append(partition)
        return tuple(partitions)
```

##### B. Partition Executor

```python
# services/data_platform/partition_executor.py
class PartitionedResearchExecutor:
    def execute_partition(
        self,
        partition: PartitionPlan,
        frozen_input: dict,
    ) -> PartitionCheckpoint:
        """
        执行单个分区：
        1. 加载 Warmup + Execution 窗口数据
        2. 计算 Factors（只保留 execution 窗口结果）
        3. 计算 Alphas（只保留 execution 窗口结果）
        4. 写入 Checkpoint
        5. 返回元数据，释放所有内存
        """
        # 1. 加载数据（限定时间范围）
        data_loader = FrozenManifestData(self.store, manifest_id)
        warmup_rows = data_loader.read_rows(
            start_time=partition.warmup_start,
            end_time=partition.warmup_end,
        )
        execution_rows = data_loader.read_rows(
            start_time=partition.calendar_start,
            end_time=partition.calendar_end,
        )
        
        # 2. 计算 Factors（流式，逐证券）
        factor_results = {}
        for instrument_id in universe:
            instrument_warmup = [r for r in warmup_rows if r["instrument_id"] == instrument_id]
            instrument_exec = [r for r in execution_rows if r["instrument_id"] == instrument_id]
            combined = instrument_warmup + instrument_exec
            
            factor_values = self._calculate_factors(combined, frozen_input["factors"])
            # 只保留 execution 窗口的结果
            factor_results[instrument_id] = [
                fv for fv in factor_values
                if partition.calendar_start <= fv["available_time"] <= partition.calendar_end
            ]
            
            del instrument_warmup, instrument_exec, combined  # 立即释放
        
        # 3. 计算 Alphas（cross-sectional，按交易日）
        alpha_results = self._calculate_alphas_cross_sectional(
            factor_results, frozen_input["alphas"]
        )
        
        # 4. 写入 Checkpoint
        checkpoint = PartitionCheckpoint(
            partition_id=partition.partition_id,
            factor_artifact_id=self._persist_factor_checkpoint(factor_results),
            alpha_artifact_id=self._persist_alpha_checkpoint(alpha_results),
            row_count=sum(len(v) for v in factor_results.values()),
            completed_at=utc_now(),
        )
        
        # 5. 显式释放内存
        del warmup_rows, execution_rows, factor_results, alpha_results
        import gc; gc.collect()
        
        return checkpoint
```

##### C. Checkpoint Manager

```python
# services/data_platform/checkpoint_manager.py
@dataclass(frozen=True)
class PartitionCheckpoint:
    partition_id: str
    bundle_hash: str
    factor_artifact_id: str
    alpha_artifact_id: str
    row_count: int
    completed_at: str
    verification_hash: str

class CheckpointManager:
    def save(self, checkpoint: PartitionCheckpoint) -> None:
        """持久化 Checkpoint 到 SQLite + Parquet"""
        with self.store.transaction() as conn:
            conn.execute("""
                INSERT INTO research_partition_checkpoints(
                    partition_id, bundle_hash, factor_artifact_id,
                    alpha_artifact_id, row_count, completed_at, verification_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                checkpoint.partition_id,
                checkpoint.bundle_hash,
                checkpoint.factor_artifact_id,
                checkpoint.alpha_artifact_id,
                checkpoint.row_count,
                checkpoint.completed_at,
                checkpoint.verification_hash,
            ))
    
    def load(self, partition_id: str, bundle_hash: str) -> PartitionCheckpoint | None:
        """检查是否存在有效 Checkpoint（用于恢复）"""
        with self.store.connection() as conn:
            row = conn.execute("""
                SELECT * FROM research_partition_checkpoints
                WHERE partition_id=? AND bundle_hash=?
                ORDER BY completed_at DESC LIMIT 1
            """, (partition_id, bundle_hash)).fetchone()
        if not row:
            return None
        # 验证 Artifact 完整性
        self._verify_artifacts(row["factor_artifact_id"], row["alpha_artifact_id"])
        return PartitionCheckpoint(**dict(row))
    
    def list_completed(self, bundle_hash: str) -> list[PartitionCheckpoint]:
        """列出所有已完成的分区（用于 Aggregation）"""
        ...
```

#### 2.3 执行流程集成

```python
# services/data_platform/research_run_service.py
class ResearchRunWorker:
    def _execute_research(self, run: dict) -> dict:
        bundle = self.service.get_bundle(run["bundle_id"])
        frozen_input = bundle["canonical_payload"]["input_closure"]
        
        # 检查是否需要分区执行
        plan = ResearchWorkloadPlanner(self.store).plan(run)
        if plan.hard_limit_exceeded:
            return self._execute_partitioned(run, frozen_input)
        else:
            return self._execute_legacy(run, frozen_input)  # 原有逻辑
    
    def _execute_partitioned(self, run: dict, frozen_input: dict) -> dict:
        """分区执行主流程"""
        planner = ResearchPartitionPlanner(self.store)
        executor = PartitionedResearchExecutor(self.store)
        checkpoint_mgr = CheckpointManager(self.store)
        
        # 1. 生成分区计划
        partitions = planner.plan(frozen_input)
        bundle_hash = self.service.get_bundle(run["bundle_id"])["bundle_hash"]
        
        # 2. 逐个执行分区
        completed_checkpoints = []
        for i, partition in enumerate(partitions):
            # 更新进度
            self._update_progress(run["run_id"], {
                "phase": f"PARTITION_{partition.partition_id}",
                "progress": int((i / len(partitions)) * 100),
                "message": f"正在处理 {partition.calendar_start[:4]} 年数据"
            })
            
            # 检查是否已有 Checkpoint（恢复场景）
            existing = checkpoint_mgr.load(partition.partition_id, bundle_hash)
            if existing:
                _emit_inspection_safely(
                    event_type="PARTITION_CHECKPOINT_REUSED",
                    context={"partition_id": partition.partition_id}
                )
                completed_checkpoints.append(existing)
                continue
            
            # 执行分区
            checkpoint = executor.execute_partition(partition, frozen_input)
            checkpoint_mgr.save(checkpoint)
            completed_checkpoints.append(checkpoint)
        
        # 3. 聚合所有分区结果
        self._update_progress(run["run_id"], {
            "phase": "AGGREGATING",
            "progress": 95,
            "message": "正在聚合所有年度结果"
        })
        final_result = executor.aggregate_partitions(
            completed_checkpoints, frozen_input
        )
        
        return final_result
```

#### 2.4 Progress & Recovery

**进度追踪**：
```python
# 每个分区执行后更新
{
  "phase": "PARTITION_2020",
  "progress": 40,  # (10 / 25 年) × 100
  "partitions_completed": 10,
  "partitions_total": 25,
  "current_partition": {
    "partition_id": "PARTITION_2020",
    "calendar_year": 2020,
    "estimated_rows": 1250000,
    "started_at": "2026-08-15T10:30:00Z"
  }
}
```

**恢复机制**：
```python
# 进程崩溃后重启
def _execute_partitioned(self, run: dict, frozen_input: dict) -> dict:
    ...
    completed = checkpoint_mgr.list_completed(bundle_hash)
    completed_ids = {cp.partition_id for cp in completed}
    
    for partition in partitions:
        if partition.partition_id in completed_ids:
            continue  # ← 跳过已完成分区
        checkpoint = executor.execute_partition(partition, frozen_input)
        ...
```

**预期效果**：
- 25 年全市场研究：60GB → **4GB** 峰值内存
- 执行时间：30 分钟 → **45-60 分钟**（增加 50-100%，但可以完成）
- 可恢复：进程崩溃后自动从最后完成的分区继续

---

### Level 3: Universe Timeline + 流式计算 —— 终极优化（4-6 周）

#### 3.1 Universe Membership Timeline 预编译

**问题**：
现在每次回测都要：
1. 扫描 SEC 全部 13F/DEF14A 文件（~10M 行）
2. 扫描 CRSP 全部上市/退市记录（~50M 行）
3. 临时判断每个交易日的成分股

**优化**：
```python
# services/data_platform/universe_timeline.py
@dataclass(frozen=True)
class UniverseMembershipTimeline:
    """压缩的时间线：只记录成分变化点"""
    universe_snapshot_id: str
    timeline: tuple[MembershipEvent, ...]  # 按时间排序

@dataclass(frozen=True)
class MembershipEvent:
    event_date: str  # "2020-03-15"
    instrument_id: str
    action: str  # "ENTER" | "EXIT"
    reason: str  # "IPO" | "DELISTED" | "MARKET_CAP_THRESHOLD"

class UniverseTimelineCompiler:
    def compile(self, universe_snapshot_id: str) -> UniverseMembershipTimeline:
        """
        一次性编译完整 Timeline：
        1. 从 SEC/CRSP 扫描所有符合条件的事件
        2. 压缩成状态变化序列
        3. 持久化到 Parquet
        4. 后续研究直接读取，无需重新扫描
        """
        snapshot = UniverseService(self.store).get_snapshot(universe_snapshot_id)
        events = []
        
        # 扫描 CRSP 上市/退市
        for security in self._scan_crsp_listings(snapshot.parameters):
            events.append(MembershipEvent(
                event_date=security["listing_date"],
                instrument_id=security["instrument_id"],
                action="ENTER",
                reason="IPO",
            ))
            if security["delisting_date"]:
                events.append(MembershipEvent(
                    event_date=security["delisting_date"],
                    instrument_id=security["instrument_id"],
                    action="EXIT",
                    reason="DELISTED",
                ))
        
        # 应用 PIT 过滤器（如果有）
        for filter_rule in snapshot.parameters.get("point_in_time_filters", []):
            events.extend(self._apply_pit_filter(filter_rule))
        
        # 排序并去重
        sorted_events = sorted(events, key=lambda e: (e.event_date, e.instrument_id, e.action))
        return UniverseMembershipTimeline(
            universe_snapshot_id=universe_snapshot_id,
            timeline=tuple(sorted_events),
        )
    
    def query_members_at(self, timeline: UniverseMembershipTimeline, as_of_date: str) -> set[str]:
        """快速查询某一天的成分股"""
        members = set()
        for event in timeline.timeline:
            if event.event_date > as_of_date:
                break
            if event.action == "ENTER":
                members.add(event.instrument_id)
            elif event.action == "EXIT":
                members.discard(event.instrument_id)
        return members
```

**效果**：
- Universe 编译：30 秒 → **2 秒**（直接读 Timeline）
- 内存：不再需要保留完整 SEC/CRSP 历史数据

#### 3.2 流式 Factor 计算

**当前**：
```python
# 按证券分组，全部加载到内存
by_instrument = defaultdict(list)
for row in all_rows:  # ← 31M 行全部在内存
    by_instrument[row["instrument_id"]].append(row)

for instrument_id, rows in by_instrument.items():
    factors = calculate_factors(rows)
```

**优化**：
```python
# 流式处理，每次只保留一个证券的数据
def calculate_factors_streaming(
    manifest: FrozenManifestData,
    universe: set[str],
    factor_specs: list[FactorSpec],
    start_time: str,
    end_time: str,
) -> Iterator[dict]:
    """
    逐证券流式计算，立即 yield，不累积
    """
    for instrument_id in sorted(universe):
        # 只加载当前证券的数据
        rows = list(manifest.read_rows(
            start_time=start_time,
            end_time=end_time,
            instrument_filter=instrument_id,  # ← Parquet 列式过滤
        ))
        
        # 计算 Factors
        for factor_spec in factor_specs:
            result_rows = factor_engine.calculate(factor_spec, rows)
            for result_row in result_rows:
                yield result_row  # ← 立即输出，不累积
        
        # 释放当前证券数据
        del rows
```

**效果**：
- 内存：31M 行 × 300 bytes = 9GB → **50K 行 × 300 bytes = 15MB**（per-instrument）
- 可并行：可以启动 N 个 Worker 同时处理不同证券

---

## 实施路线图

### Phase 1: 快速优化（Week 1）
- [ ] 消除 `dict(row)` 冗余复制
- [ ] 延迟 Arrow → Python 转换
- [ ] 显式内存释放 + gc.collect()
- [ ] 验证：15 年全市场研究可在 8GB Worker 完成

### Phase 2: 分区引擎（Week 2-4）
- [ ] 实现 PartitionPlanner
- [ ] 实现 PartitionExecutor（年度分区）
- [ ] 实现 CheckpointManager（SQLite + Parquet）
- [ ] 集成到 ResearchRunWorker
- [ ] 恢复机制：进程崩溃后从 Checkpoint 继续
- [ ] 验证：25 年全市场研究可在 8GB Worker 完成

### Phase 3: Universe Timeline（Week 5-6）
- [ ] 实现 UniverseTimelineCompiler
- [ ] 预编译 CRSP/SEC Timeline 并持久化
- [ ] 修改 Universe 服务直接读 Timeline
- [ ] 验证：Universe 编译时间 < 5 秒

### Phase 4: 流式计算（Week 7-10）
- [ ] 重构 Factor Engine 支持 streaming
- [ ] 重构 Alpha Engine 支持 streaming
- [ ] 实现 per-instrument 并行计算
- [ ] 验证：50 年全市场研究可在 8GB Worker 完成

---

## 成功指标

### 内存效率
| 场景 | 当前 | Level 1 | Level 2 | Level 3 |
|------|------|---------|---------|---------|
| 5 年全市场 | 12GB | 8GB | 4GB | 2GB |
| 15 年全市场 | 36GB (OOM) | 18GB | 4GB | 2GB |
| 25 年全市场 | 60GB (OOM) | 30GB (OOM) | 4GB | 2GB |
| 50 年全市场 | 120GB (OOM) | 60GB (OOM) | 8GB | 3GB |

### 执行时间
| 场景 | 当前 | Level 2 | Level 3 |
|------|------|---------|---------|
| 5 年全市场 | 5 分钟 | 6 分钟 | 4 分钟 |
| 25 年全市场 | - (OOM) | 30 分钟 | 20 分钟 |

### 用户体验
- ✅ Agent 提交任务后立即返回（已实现）
- ✅ 队列状态实时可见（已实现）
- ✅ 进度百分比准确（Level 2 改进）
- ✅ 进程崩溃后自动恢复（Level 2 新增）
- ✅ 永远不会因内存不足而失败（Level 2-3 保证）

---

## 技术细节

### Warmup Window 计算

```python
def _calculate_warmup_start(self, execution_start: str, max_window_days: int) -> str:
    """
    根据最大 Factor window 计算 warmup 起始时间。
    
    Example:
    - execution_start = "2020-01-01"
    - max_window_days = 252 (1 年 rolling mean)
    - warmup_start = "2019-01-01" (往前推 252 trading days)
    """
    execution_date = datetime.fromisoformat(execution_start)
    # 简化：假设 1 trading day = 1.4 calendar days
    warmup_calendar_days = int(max_window_days * 1.4)
    warmup_date = execution_date - timedelta(days=warmup_calendar_days)
    return warmup_date.date().isoformat()
```

### Checkpoint Schema

```sql
CREATE TABLE IF NOT EXISTS research_partition_checkpoints(
    checkpoint_id TEXT PRIMARY KEY DEFAULT ('checkpoint_' || lower(hex(randomblob(16)))),
    partition_id TEXT NOT NULL,  -- "PARTITION_2020"
    bundle_hash TEXT NOT NULL,   -- 关联到 frozen_research_bundles
    factor_artifact_id TEXT NOT NULL,
    alpha_artifact_id TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    completed_at TEXT NOT NULL,
    verification_hash TEXT NOT NULL,  -- SHA256(factor_artifact + alpha_artifact)
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(partition_id, bundle_hash)
);

CREATE INDEX idx_checkpoint_bundle ON research_partition_checkpoints(bundle_hash, partition_id);
```

### Aggregation Phase

```python
def aggregate_partitions(
    self,
    checkpoints: list[PartitionCheckpoint],
    frozen_input: dict,
) -> dict:
    """
    聚合所有分区结果，计算组合级别指标。
    
    注意：不需要全部加载到内存，可以流式读取。
    """
    artifact_svc = ArtifactService(self.store)
    
    # 1. 合并所有 Alpha 结果（按交易日排序）
    all_alpha_rows = []
    for checkpoint in checkpoints:
        partition_rows = artifact_svc.read_rows(checkpoint.alpha_artifact_id)
        all_alpha_rows.extend(partition_rows)
    
    all_alpha_rows.sort(key=lambda r: r["available_time"])
    
    # 2. 计算组合回测（如果需要）
    if frozen_input["run_type"] == "RESEARCH_BACKTEST":
        portfolio_spec = PortfolioSpec(**frozen_input["portfolio_spec"])
        backtest_result = self._backtest_portfolio(
            all_alpha_rows, portfolio_spec
        )
    
    # 3. 计算统计指标
    evaluation = self._evaluate_alpha(all_alpha_rows)
    
    return {
        "status": "COMPLETE",
        "factor_rows": sum(cp.row_count for cp in checkpoints),
        "alpha_rows": len(all_alpha_rows),
        "evaluation": evaluation,
        "backtest": backtest_result if frozen_input["run_type"] == "RESEARCH_BACKTEST" else None,
    }
```

---

## 附录：内存估算公式

```python
def estimate_partition_memory(
    universe_size: int,
    trading_days_per_year: int,
    warmup_trading_days: int,
    factor_count: int,
    alpha_count: int,
) -> int:
    """
    估算单个分区的峰值内存（MB）。
    
    假设：
    - 每行原始数据（bars）：约 200 bytes（Python dict）
    - 每行 Factor 结果：约 150 bytes
    - 每行 Alpha 结果：约 100 bytes
    """
    # 原始数据（Warmup + Execution）
    total_trading_days = warmup_trading_days + trading_days_per_year
    raw_rows = universe_size * total_trading_days
    raw_mb = (raw_rows * 200) / (1024 * 1024)
    
    # Factor 结果（只保留 Execution 窗口）
    factor_rows = universe_size * trading_days_per_year * factor_count
    factor_mb = (factor_rows * 150) / (1024 * 1024)
    
    # Alpha 结果
    alpha_rows = universe_size * trading_days_per_year * alpha_count
    alpha_mb = (alpha_rows * 100) / (1024 * 1024)
    
    # 峰值 = 原始数据 + Factor 中间结果 + Alpha 结果 + 20% overhead
    peak_mb = (raw_mb + factor_mb + alpha_mb) * 1.2
    
    return int(peak_mb)

# 示例：5000 只股票 × 1 年 + 252 天 warmup
estimate_partition_memory(
    universe_size=5000,
    trading_days_per_year=252,
    warmup_trading_days=252,
    factor_count=10,
    alpha_count=3,
)
# ≈ 3800 MB
```

---

## 风险与缓解

### 风险 1：分区边界效应
**问题**：跨年度的 Factor window 可能不连续。  
**缓解**：Warmup 窗口确保每个分区开始时有完整历史。

### 风险 2：Checkpoint 磁盘空间
**问题**：25 个分区 × 5GB = 125GB 临时文件。  
**缓解**：
- Checkpoints 使用压缩 Parquet（约 1/3 大小）
- 聚合完成后自动清理临时 Checkpoints
- 配置最大保留时间（如 7 天）

### 风险 3：Aggregation Phase OOM
**问题**：合并 25 年结果时仍可能 OOM。  
**缓解**：
- Aggregation 也流式处理，不全部加载
- 组合回测使用增量计算，不累积全部持仓历史

### 风险 4：执行时间增加
**问题**：分区执行比整体慢 50-100%。  
**缓解**：
- 对于小型研究（< 5 年），仍使用原有快速路径
- 分区只用于 PARTITIONED_REQUIRED 场景
- Level 3 流式优化可部分抵消开销

---

## 总结

通过三层递进优化：

1. **Level 1**（1 周）：立即减少 50-60% 内存占用
2. **Level 2**（3-4 周）：突破内存上限，任何规模研究都能安全完成
3. **Level 3**（4-6 周）：极致优化，50 年全市场研究也只需 3GB

最终实现目标：
> **Agent 与用户不再需要关心内存、Worker、PID、并发数或重试策略。  
> 只需提交研究目标，DataTube 自动路由、自动切分、自动恢复。**
