# DataTube

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
```

The Skill entry point is `skills/datatube/SKILL.md`. Its bootstrap script creates
`.venv`, installs `requirements.txt`, copies example config files when local
config is missing, starts the local app, and talks to the controlled Agent APIs.

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

API keys entered on the Settings page are saved to `web_settings.secrets.json`, encrypted with a local key in `.datatube_secret.key`. Both files are ignored by Git. The Settings page and `/api/settings` are restricted to requests from this computer.

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
