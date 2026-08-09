# Requirement 数据自动维护与实时进度

更新日期：2026-08-04

本文说明 Requirement Library 与 Research Data 页的数据维护语义、后台调度边界、实时进度字段和故障排查方式。这里的“自动维护”只负责准备研究所需的历史数据，不会创建 Strategy，也不会触发 Virtual 或 Live Trade。

本文不改变 History Workspace 的显式下载工具：用户在 History Workspace 主动选择任意时间窗口、导出或启动 Backtest Report 下载，仍属于独立交互。Research / Library Requirement 则始终采用本文的后台自动维护逻辑。

## 产品原则

- 用户只负责定义或引用 Requirement，不负责点击按钮启动下载。
- 后端持续扫描 Requirement Library；即使某个 Library Requirement 尚未被 Research 引用，也会参与覆盖检查。
- 后端同时扫描活跃 Research 的非 `SUPERSEDED` RequirementSet，覆盖由 Factor / Alpha 派生、但未单独发布到 Library 的需求。
- 普通数据缺口进入 `QUEUED / PREPARING / CHECKING`，不显示 `Needs Attention`，也不要求用户执行 `Complete Missing Data`、独立下载或下载重试操作。
- 只有无法自动解决的合同错误、Provider 不支持或任务终态失败，才在 Library 与 Research 同时显示错误。
- Research Run 不在计算过程中联网补数据；Run 只消费已经提交并通过 Manifest 校验的数据。

## 后台维护链路

```text
Requirement Library + Active Research RequirementSets
→ 每 30 秒扫描 Coverage
→ 规范化 Instrument / Provider / Frequency / Range
→ 生成确定性幂等键
→ 创建受限的系统维护 Task
→ Provider Worker 下载、校验并提交 Manifest
→ Requirement Workspace 重新解析 Coverage
→ Research / Library 展示最新状态与进度
```

当前自动维护支持：

| 数据合同 | 系统任务 | 当前约束 |
|---|---|---|
| Binance Spot Bars | `BINANCE_BARS_BACKFILL` | 规范化 Instrument 必须为 `crypto_spot:BINANCE:*` |
| OpenBB Equity Daily Bars | `OPENBB_EQUITY_DAILY_EXPORT` | XNAS/XNYS、`1d`；未明确上游时使用 `yfinance` |
| Polymarket Price History | `POLYMARKET_PRICE_HISTORY_EXPORT` | 规范化 Instrument 必须为 `polymarket_binary:POLYMARKET:*` |

FRED、分钟级股票数据或其他尚无维护适配器的合同不会伪装成正在下载，而是显示 `UNAVAILABLE` 或明确错误。

## 状态语义

| 状态 | 用户含义 | 是否需要用户操作 |
|---|---|---|
| `READY` | 所需字段、频率、范围与 Manifest 已满足 | 否 |
| `QUEUED` | 后端已创建任务，等待 Worker | 否 |
| `PREPARING` | 正在下载、分页、校验或写入 | 否 |
| `CHECKING` | 数据已提交，正在重新检查 Coverage / Manifest | 否 |
| `FAILED` | 自动维护已停止，并保留终态错误 | 查看错误并修正合同或运行环境 |
| `UNAVAILABLE` | 当前 Provider / 频率 / 数据类型没有受支持的准备路径 | 修改 Requirement 或接入 Provider |

`NEEDS_ATTENTION` 不再用于表达“数据还没下载完”。旧数据中若仍存在该状态，聚合层会把可自动准备的缺口转换为 `PREPARING`；真正无法自动解决的问题统一进入 `FAILED / UNAVAILABLE`。

## 实时下载进度

Research Data 页与 Library Requirement 卡片读取同一份后端状态。活动任务显示 `Live Download`，可能包含：

| 字段 | 含义 |
|---|---|
| `phase` | 当前阶段，例如排队、下载与校验、重新检查 |
| `percent` | 可计算时的总体百分比；无法可靠估算时不伪造 |
| `completed_partitions / total_partitions` | 已完成与总分区数 |
| `current_range` | 当前处理的时间范围 |
| `rows_fetched` | 已获取或已处理的行数 |
| `eta_seconds` | 有足够分区样本时计算的预计剩余时间 |
| `elapsed_seconds` | 当前任务已运行时间 |
| `message` | Worker 或维护服务给出的阶段说明 |

页面轮询只负责读取状态，不负责触发任务。刷新、切换 Tab 或保存 Factor 不会创建下载任务；任务由后端维护循环独立产生。

## 幂等、重试与资源边界

- 幂等键由维护器版本、Instrument、数据类型、频率、起止范围、Provider 与 Adjustment 共同生成；同一缺口不会因页面刷新重复建任务。
- 每个任务默认最多尝试 3 次；单次预算为 20 MB 下载量和 300 秒运行时间，编译器硬上限为 100 MB 和 900 秒。
- 维护任务写入隐藏、已归档的系统项目，并带内部 `SYSTEM_REQUIREMENT_MAINTENANCE` 授权标记。
- 公共 API 不能自行添加该标记；执行器只接受三种白名单数据任务，并再次校验系统项目与授权模式。
- 已达到维护器当前版本的终态失败会保持可见，不会被 30 秒循环无限重建。
- 系统维护不借用、不扩大用户或 Research Agent 的 Grant / Session Policy。

## 排查顺序

1. 先看 Research Data 或 Library 卡片的状态与错误文本，确认是 `PREPARING`、`FAILED` 还是 `UNAVAILABLE`。
2. `PREPARING` 时查看 `phase`、分区、行数、当前范围、ETA 与已运行时间；百分比暂时为空不等于任务停止。
3. `FAILED` 时检查 `.datatube/app.stderr.log` 与 `.datatube/app.stdout.log` 中的 `REQUIREMENT-MAINTENANCE`、Binance、OpenBB 或 Polymarket Worker 记录。
4. `UNAVAILABLE` 时检查 Requirement 的 Provider、Market、Instrument、Frequency、Data Type 和 Adjustment 是否属于上表支持范围。
5. 修正合同或运行环境后，让新的 RequirementSet / 维护器版本产生新的幂等任务；不要通过前端重复点击制造任务。

## 验收

完整 Research Workspace 回归：

```powershell
python .agents/skills/datatube/scripts/research_workspace_test.py --mode all --repo .
```

当前基线验收覆盖 JavaScript 语法、Python 编译、218 个单元测试、21 个集成测试、1 个故障注入测试和只读在线检查。验收重点包括：Library 未引用 Requirement 也会被扫描、系统维护任务幂等、无用户 Grant 的受限执行路径、Research / Library 进度展示，以及 Data 状态页不再出现手动补数或下载重试按钮。

## 关键实现

- [Requirement Maintenance Service](../../services/data_platform/requirement_maintenance_service.py)
- [Requirement Workspace Service](../../services/data_platform/requirement_workspace_service.py)
- [Research Control Plane](../../services/data_platform/research_control_plane.py)
- [Research Workspace UI](../../static/research_workspace_simple.js)
- [Application Loop](../../app.py)
