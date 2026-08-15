# DataTube 后端分发与故障隔离

## 目标

DataTube 将 HTTP/Web 视为控制面，将 Research 与历史回测视为计算面：

- 前端读取、状态查询和 Agent 轮询必须持续可用。
- Agent 或前端提交计算时只创建持久任务，不在 HTTP 请求中执行计算。
- 计算任务按队列顺序和资源等级派发，并持续返回位置、阶段、心跳与终态。
- 单个任务超时、OOM、异常退出或日志失控，只能终止该任务，不能终止 Web。

## 请求路径

```text
Frontend / Agent
       |
       v
Flask control plane -----> SQLite durable queue/status
       |                              |
       | read/status                  | claim + lease + heartbeat
       v                              v
fast snapshot APIs          resource admission controller
                                      |
                                      v
                         low-priority isolated child process
                         (memory/time/log/process-tree limits)
```

Research Experiment 编排器只负责语义编译、数据准备和观察 Run 状态。正式
Research Run 由独立 Run 调度器领取；历史回测使用自己的单消费者队列，但共享同一
资源准入控制器。两类计算不能绕回 HTTP 同步执行。

## 优先级与资源准入

- Web 预留内存默认 `6144 MiB`。低于该余量时，计算保持排队。
- Research Run 视为 `HEAVY`，全局互斥，默认子进程上限 `8192 MiB`。
- 历史回测视为 `STANDARD`，单消费者，默认子进程上限 `2048 MiB`。
- 子进程使用低于 Web 的 Windows 调度优先级。
- 所有子进程和后代进程都属于同一个 Windows Job Object；超限时整棵进程树退出。

可调环境变量：

| 变量 | 默认值 | 含义 |
| --- | ---: | --- |
| `DATATUBE_FRONTEND_MEMORY_RESERVE_MB` | 6144 | Web/系统预留内存 |
| `DATATUBE_RESEARCH_WORKER_MEMORY_MB` | 8192 | Heavy Research 进程树内存上限 |
| `DATATUBE_STANDARD_WORKER_MEMORY_MB` | 4096 | Standard Experiment 编排进程上限 |
| `DATATUBE_BACKTEST_WORKER_MEMORY_MB` | 2048 | 历史回测进程树内存上限 |
| `DATATUBE_RESEARCH_EXPERIMENT_TIMEOUT_SECONDS` | 3600 | Experiment 编排超时 |
| `DATATUBE_BACKTEST_TIMEOUT_SECONDS` | 1800 | 历史回测超时 |
| `DATATUBE_RESEARCH_HEARTBEAT_SECONDS` | 15 | Research Run 心跳间隔 |

## 大任务预检

正式 Research Run 在读取 Parquet 前，根据冻结 Manifest 的行数、文件体积、研究
区间和 Universe 规模估算当前 Python 执行器的峰值内存。如果估算超过 worker
安全水位，任务以 `FORMAL_RESEARCH_RESOURCE_LIMIT` 停止，并映射为公开状态
`RESEARCH_RESOURCE_PLAN_BLOCKED`。

该状态表示当前任务需要分区执行引擎，不是研究结论。它不可自动重试；相同输入
连续重跑无法改变内存需求，只会制造 OOM 风暴。

## 状态与恢复

Research Experiment 查询返回：

- `queue.state / position / total / resource_class / reason`
- `progress.phase / percent / heartbeat_at / attempt`

Research Run 与历史回测查询也返回相同含义的 `queue` 和 `progress`。服务重启时：

- 尚未领取的 `QUEUED` 任务继续排队。
- 上一个进程遗留的 `RUNNING` 任务标记为 `PROCESS_INTERRUPTED`，不静默重跑。
- 已成功的重试会清除先前尝试的错误字段。

worker 日志保存在元数据目录下的 `worker_logs/`，按任务隔离。单日志默认最大
32 MiB，超限会终止对应 worker，避免日志占满磁盘。

## 验证基线

变更后至少运行：

```powershell
python -m unittest discover -s tests/unit -p "test_*.py"
python -m unittest discover -s tests/integration -p "test_*.py"
python .agents/skills/datatube/scripts/bootstrap.py status --json
```

验收时必须同时检查：Web 接口延迟、Web 进程内存、队列位置、worker 心跳、终态
错误码和 `worker_logs`。不得仅以“提交 API 返回 201”作为计算系统健康证明。
