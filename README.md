# DataTube

量化研究入口：`http://127.0.0.1:5001/research`。英文 UI 以 `Research` 和 `Library` 为两个核心名称：Research 用于创建、修改、验证和测试工作卡片；Library 只保存已发布、带版本且可复用的正式资产。发布会生成独立的不可变 Library 版本，不会改变或持续同步原 Research 工作卡片；从 Library 添加时默认保存只读版本引用，只有 `Copy and Edit` 才创建新的 Research 草稿。单个 Research 提供 `Overview / Universe / Factor / Alpha / Data / Strategy / Runs`；Runs 已拆分为 Factor Evaluation、Alpha Evaluation 与 Research Backtest，分别负责因子评估、Signal 评估和组合/交易/成本/收益。这里 `Requirements` 是“研究需要什么数据”的可复用契约，`Data` 只展示这些需求的 Coverage、自动维护状态、实时下载进度与终态错误；实际数据资产仍由 `Data Catalog` 管理。后台每 30 秒扫描 Requirement Library 与活跃 Research，自动创建受限、幂等、可审计的数据维护任务，普通缺口不需要用户点击补数。Research Agent 支持从自然语言目标 `START`，也可以从 Project、Run、Preview、Bundle、Factor、Alpha 或 Session `RESUME`，并通过 AgentMonitor 查看可恢复的实验过程。详见 [Factor Run、Alpha Run 与 Research Backtest MVP](文档/03-features/factor-alpha-run-mvp.md)、[Requirement 数据自动维护](文档/04-operations/requirement-data-maintenance.md)、[Research Agent Skill](文档/03-features/research-agent-skill.md)、[Research Run 语义与实现基线](文档/03-features/research-run-semantics.md)、[Research 工作台操作说明](文档/04-operations/research-workspace-operation-guide.md) 和 [交付状态](文档/04-operations/research-baseline-delivery-status.md)。

Local Flask dashboard and collectors for Polymarket market data, strategy monitoring, virtual execution, and related crypto/finance feeds.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.json config.json
Copy-Item web_settings.example.json web_settings.json
python app.py
```

Open <http://127.0.0.1:5001>. The app intentionally binds to `127.0.0.1` by default.

## DataTube Skill

DataTube v1.0 can be published as a Codex/Claude Code/OpenClaw style Skill.
After this repo is pushed to GitHub, users can install the Skill with:

```text
帮我安装这个 skill：https://github.com/867888660/AITrades/tree/main/skills/datatube
```

After installing and restarting the Agent, users can say:

```text
启动 DataTube
检查 DataTube 状态
研究 BTC 相关 Polymarket 市场，并结合 Binance 数据
帮我研究 BTC 趋势策略
从 run_123 继续研究，并尝试降低最大回撤
```

The Skill entry point is `skills/datatube/SKILL.md`. Its bootstrap script creates
`.venv`, installs `requirements.txt`, copies example config files when local
config is missing, starts the local app, and talks to the controlled Agent APIs.
Research-only requests create a bounded Research Session automatically; users do
not create or manage a Grant. The Agent passes `session_id` to controlled
Research APIs and cannot publish globally, create live strategies, delete
lineage, or enlarge the server-managed limits.

Do not publish virtual environments, local configs, secrets, caches, logs, or
databases. Publish the source code, example configs, requirements files, and
`skills/datatube/`.

## Local Data

Runtime databases and caches are created automatically when the relevant service first runs. They are intentionally ignored by Git:

- `Data/*.db`, `Data/*.db-wal`, `Data/*.db-shm`
- `strategy_metrics_dbs/*.db`
- `strategy_workspace_*.db`
- `polymarket_active_markets_cache.json`

Fresh databases start empty. Use the Dictionary refresh button or the collector workflows to populate market data.

## Settings And Secrets

`config.json` and `web_settings.json` are local files. Use the `*.example.json` files as publishable templates.

API keys entered on the Settings page are saved to `web_settings.secrets.json`. On Windows, secrets use current-user DPAPI protection; non-Windows installations use a local Fernet key. Secret files are ignored by Git. The Settings page and `/api/settings` are restricted to requests from this computer, and the API returns only configured/count flags—not saved secret values. Leaving a secret field blank preserves the current value; deletion requires the explicit clear checkbox.

### Managed History Data root

Settings > History Data can consolidate historical storage under one absolute
directory, such as `E:\DataTubeHistoricalData`. The inspection step inventories
the History workspace DB, Data Platform storage, strategy-history DBs, configured
source archives, and external catalog paths. Normalization runs in the background,
uses online SQLite backups, copies files through temporary targets, preserves all
source data, and writes the activation marker only after verification succeeds.
Restart DataTube after a successful normalization so every history and Research
service uses the managed root. Existing immutable Manifest paths are resolved
through marker aliases instead of rewriting Manifest records.

Before publishing, rotate any API key that was ever committed or shared.

## Offline Package

On the online build machine, create a self-contained zip with local wheels:

```powershell
.\scripts\prepare_offline_package.ps1
```

This creates `dist/polymarket_datatube_offline.zip`. The zip includes `wheelhouse`, source files, example config files, and the offline installer. It does not include local databases, caches, API keys, or machine-specific settings.

If you also want to include a prebuilt virtual environment for same-OS emergency use, run:

```powershell
.\scripts\prepare_offline_package.ps1 -IncludePreparedVenv
```

On the offline target machine, unzip the package and run:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force
.\scripts\install_offline.ps1
```

The installer creates `.venv`, installs dependencies from `wheelhouse` with `--no-index`, creates `config.json` and `web_settings.json` from examples when missing, and prepares empty runtime data directories.
