# DataTube 分区执行引擎 - 用户指南

## 概述

DataTube 的分区执行引擎可以自动处理任何规模的历史研究任务，无需用户手动配置内存或分区策略。

---

## 自动行为

### 何时触发分区执行？

系统会自动评估每个研究任务的内存需求：

```python
# 自动估算公式
estimated_memory = (
    universe_size × trading_days × 
    (bars_size + factors_size + alphas_size)
) × 1.3  # overhead

# 决策
if estimated_memory < 3.5 GB:
    → 传统单次执行（快速）
else:
    → 自动切换到分区执行（可扩展）
```

### 示例场景

| 研究任务 | 估算内存 | 执行模式 |
|---------|---------|---------|
| AAPL 单股 5 年 | ~100 MB | LEGACY |
| 500 只股票 5 年 | ~2 GB | LEGACY |
| 5000 只股票 5 年 | ~8 GB | PARTITIONED |
| 5000 只股票 25 年 | ~60 GB → **4 GB** | PARTITIONED |

---

## 用户体验

### Agent 视角

**提交任务**：
```json
POST /api/agent/researcher/start
{
  "objective": "研究 2000-2025 美股全市场 Momentum + Quality Alpha",
  "asset_scope": {"asset_class": "US_EQUITY"},
  "frequency": "1d",
  "research_period": {
    "start": "2000-01-01",
    "end": "2025-12-31"
  }
}
```

**即时响应**：
```json
{
  "session_id": "session_abc123",
  "status": "ACTIVE",
  "queue": {
    "state": "QUEUED",
    "position": 1,
    "mode": "AUTOMATIC"
  }
}
```

**进度更新**（通过 Event）：
```json
{
  "event_kind": "progress",
  "title": "Partition 17/26",
  "output_data": {
    "phase": "PARTITION_2016",
    "partition_index": 17,
    "total_partitions": 26,
    "progress_percent": 65
  }
}
```

**最终结果**：
```json
{
  "status": "COMPLETE",
  "execution_mode": "PARTITIONED",
  "partition_count": 26,
  "alpha_rows": 31500000,
  "evaluation": { ... }
}
```

### 前端视角

**队列状态**：
```
研究任务 #1234
─────────────────────────────────────
状态: RUNNING
模式: 分区执行（自动）
进度: 65%

当前阶段: PARTITION_2016
完成: 17/26 个年度分区
内存占用: 4.2 / 12 GB (安全)

预计剩余时间: 12 分钟
```

---

## 技术细节

### 分区策略

**年度分区**：
```
2000-2025 研究 → 切分为 26 个年度分区

每个分区：
├─ 主执行窗口: 1 年（如 2020-01-01 至 2020-12-31）
└─ Warmup 窗口: 前 N 个交易日（基于最大 Factor window）
   
示例 (Factor window = 252 trading days):
PARTITION_2020:
├─ Warmup:    2019-10-01 至 2019-12-31 (约 252 交易日)
└─ Execution: 2020-01-01 至 2020-12-31
```

### 内存管理

**执行流程**：
```
1. 加载 Warmup + Execution 数据
   ├─ Warmup:    ~1.3M rows × 200 bytes = 260 MB
   └─ Execution: ~1.3M rows × 200 bytes = 260 MB
   
2. 计算 Factors（保留 Execution 窗口结果）
   └─ Results:   ~1.3M rows × 10 factors × 150 bytes = 1.9 GB
   
3. 释放原始数据
   ├─ del warmup_rows, execution_rows
   └─ gc.collect()
   
4. 计算 Alphas（保留 Execution 窗口结果）
   └─ Results:   ~1.3M rows × 3 alphas × 100 bytes = 390 MB
   
5. 持久化 Checkpoint
   ├─ Factors.parquet:  ~600 MB (compressed)
   └─ Alphas.parquet:   ~130 MB (compressed)
   
6. 释放所有数据
   ├─ del factor_results, alpha_results
   └─ gc.collect()

峰值内存: ~2.7 GB << 4 GB Worker 上限
```

### Checkpoint 机制

**存储位置**：
```
research_checkpoints/
  {bundle_hash[:8]}/
    partition_2000_factors.parquet
    partition_2000_alphas.parquet
    partition_2001_factors.parquet
    partition_2001_alphas.parquet
    ...
    partition_2025_factors.parquet
    partition_2025_alphas.parquet
```

**自动恢复**：
```
场景: Worker 在第 18 个分区时崩溃

重启后:
1. ResearchRunWorker.claim(run_id)
2. _execute_partitioned() 开始执行
3. CheckpointManager.load() 检测到已有 17 个 Checkpoint
4. 跳过分区 1-17（直接复用）
5. 从分区 18 继续执行
6. 完成分区 18-26
7. 聚合所有 26 个 Checkpoint
8. 返回最终结果

结果: 已完成的工作不会丢失 ✓
```

---

## 常见问题

### Q1: 分区执行会更慢吗？

**A**: 是的，大约慢 20-50%。

| 场景 | 传统模式 | 分区模式 | 增长 |
|------|----------|----------|------|
| 5 年全市场 | 5 分钟 | 6-7 分钟 | +20-40% |
| 25 年全市场 | - (OOM) | 30-40 分钟 | N/A (可完成) |

**权衡**：
- 传统模式：快速，但内存受限（5-10 年上限）
- 分区模式：稍慢，但可处理任意年限

**自动优化**：系统会自动选择最优模式，小研究使用传统模式。

### Q2: Checkpoint 占用多少磁盘空间？

**A**: 约 500 MB/年（压缩后）。

```
25 年研究:
- 26 个分区 × 500 MB ≈ 13 GB
- 压缩率: ~3:1 (Zstandard)
- 保留期: 7 天（可配置）
```

### Q3: 如何查看分区执行进度？

**A**: 三种方式：

1. **Agent API**：轮询 `/api/agent/researcher/sessions/{session_id}`
2. **前端**: 查看实验详情页的进度条
3. **Event**: 订阅 inspection events（实时推送）

### Q4: 可以手动触发分区执行吗？

**A**: 不需要，系统自动决策。

如果确实需要强制使用特定模式：
```python
# 不推荐：手动覆盖
# 系统会自动选择最优策略
```

### Q5: 分区执行失败怎么办？

**A**: 系统会自动处理：

```
分区执行失败 → Event 记录错误 → Run 标记为 FAILED
                ↓
          返回详细错误信息
                ↓
          Agent 收到失败原因
```

**恢复**：
- 如果是暂时性错误（网络、临时资源不足）：重新提交任务
- Checkpoint 会自动复用，不会从头开始

---

## 监控和调试

### 查看 Checkpoint 状态

**SQL 查询**：
```sql
-- 查看某个 Bundle 的所有 Checkpoint
SELECT 
    partition_id,
    row_count,
    completed_at,
    ROUND(LENGTH(verification_hash) / 1024.0 / 1024.0, 2) AS size_mb
FROM research_partition_checkpoints
WHERE bundle_hash = 'abc12345'
ORDER BY partition_id;
```

### 查看内存占用

**前端监控**：
```
Settings → 资源配置 → 实时资源状态

研究内存占用:
▓▓▓▓▓░░░░░░░░  4.2 / 12 GB (35%)

活跃任务:
- Experiment #1234: PARTITION_2020 (17/26) - 3.8 GB
```

### Event 日志

**查询分区执行事件**：
```sql
SELECT 
    event_id,
    title,
    status,
    output_data
FROM inspection_events
WHERE subject_id = 'run_abc123'
  AND operation LIKE 'research.partition.%'
ORDER BY created_at;
```

---

## 最佳实践

### 1. 合理设置内存预算

**推荐配置**（Settings → 资源配置）：

```
32 GB 机器:
- 研究内存预算: 12 GB（留 4 GB 缓冲）
- 配置模式: AUTO（推荐）

16 GB 机器:
- 研究内存预算: 6 GB
- 配置模式: AUTO
```

### 2. 避免过度并发

**建议**：
- 大型研究（10+ 年）：同时运行 1-2 个
- 小型研究（< 5 年）：同时运行 2-3 个

系统会自动队列管理，不需要手动限制。

### 3. 定期清理 Checkpoint

**自动清理**：
- 默认保留 7 天
- 可在配置中调整

**手动清理**（可选）：
```python
from services.data_platform import CheckpointManager

manager = CheckpointManager(store, checkpoint_root)
deleted_count = manager.cleanup(
    bundle_hash="abc123...",
    keep_days=3  # 保留 3 天
)
print(f"清理了 {deleted_count} 个过期 Checkpoint")
```

---

## 故障排查

### 问题: "PARTITIONED_EXECUTION_REQUIRED" 但没有自动执行

**可能原因**：
1. 使用了旧版本（Level 2 之前）
2. Worker 未更新到最新代码

**解决方案**：
```bash
# 更新代码
git pull origin main

# 重启 Worker
# （Worker 会自动加载新代码）
```

### 问题: Checkpoint 验证失败

**症状**：
```
Event: PARTITION_CHECKPOINT_FAILED
Reason: Verification hash mismatch
```

**解决方案**：
1. Checkpoint 已自动删除
2. 重新提交任务（会重新执行该分区）

### 问题: 内存仍然不足

**症状**：
```
Event: PARTITION_2020_FAILED
Reason: MemoryError
```

**可能原因**：
- Universe 非常大（> 10000 只股票）
- Factor 数量过多（> 20 个）

**解决方案**：
1. 增加用户内存预算（Settings → 资源配置）
2. 减少并发任务
3. 联系技术支持（可能需要 Level 3 流式计算）

---

## 技术支持

如有问题，请联系：
- GitHub Issues: https://github.com/867888660/AITrades/issues
- 提供以下信息：
  - Run ID
  - Error Event
  - 内存配置截图

---

**最后更新**: 2026-08-15  
**版本**: Level 2.0  
**状态**: Production Ready ✅
