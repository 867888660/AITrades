# Setup Workflow

Use this reference for installing, starting, stopping, repairing, and publishing
DataTube v1.0.

## User Goal

Make these prompts work with minimal user effort:

```text
Install DataTube
Start DataTube
Fix DataTube
Open DataTube
Check DataTube status
```

## Install Shape

DataTube v1.0 is published as one skill folder plus the runtime source repo.
The skill may be installed from GitHub with a prompt like:

```text
Install this skill: https://github.com/867888660/AITrades/tree/main/skills/datatube
```

Do not publish or copy:

```text
.venv/
config.json
web_settings.json
web_settings.secrets.json
.datatube_secret.key
Data/*.db
strategy_metrics_dbs/*.db
polymarket_active_markets_cache.json
```

Publish:

```text
skills/datatube/
app.py
services/
templates/
static/
requirements.txt
config.example.json
web_settings.example.json
README.md
```

## Bootstrap Commands

Run from the skill directory:

```bash
python scripts/bootstrap.py status --json
python scripts/bootstrap.py ensure --json
python scripts/bootstrap.py start --json
python scripts/bootstrap.py doctor --json
python scripts/bootstrap.py stop --json
```

When the runtime is not adjacent to the skill, pass a repo URL:

```bash
python scripts/bootstrap.py ensure --repo-url https://github.com/867888660/AITrades.git --json
```

## Expected Bootstrap Behavior

`ensure` must:

1. Locate a runtime root containing `app.py` and `requirements.txt`, or clone one
   when `--repo-url` is provided.
2. Create `.venv` using the active Python.
3. Install `requirements.txt`.
4. Copy `config.example.json` to `config.json` only if missing.
5. Copy `web_settings.example.json` to `web_settings.json` only if missing.
6. Create runtime data directories.
7. Avoid printing or logging secrets.

`start` must:

1. Run `ensure`.
2. If `http://127.0.0.1:5001/api/health` already responds, report
   `already_running`.
3. Start `app.py` with the runtime `.venv` Python.
4. Write logs to `.datatube/server.log`.
5. Return the URL and health result.

## Manual Fallback

If script execution is unavailable, show the user the equivalent commands:

Windows:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json -ErrorAction SilentlyContinue
Copy-Item web_settings.example.json web_settings.json -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe app.py
```

macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -f config.json || cp config.example.json config.json
test -f web_settings.json || cp web_settings.example.json web_settings.json
.venv/bin/python app.py
```

## Closeout

Report the local URL, health status, whether dependencies were installed, and
any manual action the user needs to complete. Do not ask for API keys unless a
specific feature requires them; point the user to the Settings page instead.
