from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from services.config_loader import load_public_web_settings, load_web_settings, load_web_settings_for_ui, save_web_settings
from services import agent_interface_service as agent_service
from services import inspection_service
from services.binance_market_service import search_binance_markets
from services.crypto_service import fetch_crypto_quotes
from services.data_source_management_service import (
    DataSourceManagementService,
    DataSourceRoutingConflict,
)
from services.data_source_connection_service import (
    DataSourceConnectionError,
    DataSourceConnectionService,
)
from services.data_source_definitions import OPENBB_CREDENTIAL_KEYS
from services.event_graph_service import build_event_graph, get_event_graph_categories
from services.event_news_service import (
    deduplicate_derived_events,
    event_news_scheduler,
    get_status as get_event_news_status,
    list_events as list_news_events,
    list_observations as list_news_observations,
    refresh_news,
)
from services.finance_service import fetch_finance_quotes
from services.openbb_provider_service import OpenBBProviderService, normalize_equity_adjustment
from services.history_data_service import (
    add_watchlist_item as add_history_watchlist_item,
    create_backtest_batch as create_history_backtest_batch,
    create_backtest_case as create_history_backtest_case,
    create_backtest_collection as create_history_backtest_collection,
    create_backtest_run as create_history_backtest_run,
    delete_backtest_batch as delete_history_backtest_batch,
    delete_backtest_case as delete_history_backtest_case,
    delete_backtest_run as delete_history_backtest_run,
    delete_watchlist_item as delete_history_watchlist_item,
    download_binance_klines,
    download_binance_klines_range,
    download_polymarket_price_history,
    evaluate_backtest_case_payload,
    get_coverage as get_history_coverage,
    health_snapshot as get_history_health,
    get_backtest_batch as get_history_backtest_batch,
    get_backtest_run as get_history_backtest_run,
    import_backtest_run_to_workspace as import_history_backtest_run_to_workspace,
    list_backtest_batches as list_history_backtest_batches,
    list_backtest_cases as list_history_backtest_cases,
    list_backtest_collections as list_history_backtest_collections,
    list_backtest_runs as list_history_backtest_runs,
    list_watchlist as list_history_watchlist,
    preview_history,
    rename_backtest_batch as rename_history_backtest_batch,
    rename_backtest_case as rename_history_backtest_case,
    rename_backtest_run as rename_history_backtest_run,
    recover_backtest_queue,
    rerun_backtest_run as rerun_history_backtest_run,
)
from services.history_storage_service import (
    HistoryStorageService,
    get_data_platform_storage_root,
    get_history_storage_job,
)
from services.data_platform.workload_scheduler import (
    IntelligentWorkloadRouter,
    ResearchWorkloadPlanner,
    ResourceAdmissionController,
)
from services.http_client import SESSION
from services.data_platform.factor_run_result_service import (
    FACTOR_RUN_RESULT_SCHEMA_VERSION,
    FACTOR_RUN_STRUCTURED_SECTIONS,
    FactorRunResultService,
)
from services.data_platform.alpha_run_result_service import (
    ALPHA_RUN_RESULT_SCHEMA_VERSION,
    ALPHA_RUN_STRUCTURED_SECTIONS,
    AlphaRunResultService,
)
from services.data_platform.research_backtest_result_service import (
    RESEARCH_BACKTEST_RESULT_SCHEMA_VERSION,
    RESEARCH_BACKTEST_STRUCTURED_SECTIONS,
    ResearchBacktestResultService,
)
from services.backtest_service import (
    create_strategy_backtest,
    get_strategy_backtest,
    get_strategy_backtest_results,
)
from services.data_platform import (
    ArtifactService,
    CURRENT_HISTORY_BACKTEST_CAPABILITIES,
    DataRequirementService,
    ResearchDataCapabilityService,
    PolymarketResearchTaskExecutor,
    PolymarketResearchWorker,
    RequirementCompiler,
    RequirementWorkspaceService,
    RequirementMaintenanceService,
    default_requirement_spec,
    ResearchInputBundleService,
    ResolvedDataPlanService,
    DatasetCatalogService,
    ExistingBacktestAdapter,
    ManifestProvenanceService,
    OpenBBResearchTaskExecutor,
    OpenBBResearchWorker,
    FrozenManifestData,
    InstrumentRegistry,
    RESEARCH_BACKTEST_CAPABILITIES,
    ResearchBacktestProvider,
    ResearchControlPlane,
    ResearchLibraryService,
    LibraryGroupService,
    SourcePolicy,
    SourcePolicyService,
    SharedUniverseService,
    UniverseService,
    UniverseConflictError,
    UniverseResolutionError,
    UniverseSharedImpactError,
    BinanceBackfillJobService,
    BinanceBackfillTaskExecutor,
    BinanceBackfillWorker,
    DefinitionRegistry,
    FactorDraftService,
    FactorDraftValidationError,
    FactorInputCandidateResolver,
    FactorPreviewError,
    FactorPreviewService,
    AlphaFactorCandidateResolver,
    AlphaDraftService,
    AlphaDraftValidationError,
    AlphaPreviewError,
    AlphaPreviewService,
    DeterministicManifestResolver,
    IdempotencyConflictError,
    PreviewStaleError,
    ReadinessBlockedError,
    ResearchRunPreviewService,
    ResearchRunService,
    ResearchRunWorker,
    DEFAULT_RESEARCH_OPERATIONS,
    ResearchAgentAuthorization,
    ResearchAgentSessionService,
    ResearchExperimentService,
    ResearchSemanticError,
    align_research_intent,
    normalize_research_brief,
    ResearchContextResolver,
    ResearchAuthorizationError,
    get_default_store,
    CrspBulkImportService,
    SecBulkImportService,
)
from services.data_platform.equity_monthly_research import EquityMonthlyResearchMaterializer
from services.polymarket_dictionary_service import get_dictionary_status, start_dictionary_refresh
from services.ledger_service import get_ledger_snapshot
from services.polymarket_service import (
    fetch_strategy_detail,
    fetch_strategy_monitoring,
    fetch_wallet_positions,
    get_overview,
    list_market_categories,
    resolve_market_selection,
    search_markets,
)
from services.realtime_collector import collector
from services.strategy_chart_delta_service import get_strategy_chart_delta
from services.strategy_chart_service import get_strategy_chart
from services.strategy_data_source import (
    read_strategy_state_bundle,
    reset_strategy_state_namespace,
    write_strategy_state_values,
)
from services.strategy_schema_service import get_strategy_code_schemas, strategy_state_payload
from services.strategy_event_service import list_strategy_events
from services.strategy_exit_service import force_flat_strategy
from services.strategy_registry_service import (
    create_strategy,
    delete_strategy,
    get_strategy as get_registry_strategy,
    list_strategies as list_registry_strategies,
    get_strategy_code_inputs,
    list_strategy_codes,
    update_strategy as update_registry_strategy,
    update_strategy_legs,
    update_strategy_mode,
    update_strategy_state,
)
from services.strategy_settings_service import update_strategy_settings
from services.strategy_signal_source_service import list_library_alpha_sources
from services.strategy_workspace_service import get_strategy_usedata_draft, get_strategy_usedata_snapshot, get_strategy_workspace
from services.ws_market_sync_service import ws_market_sync
from services.workspace_preset_service import (
    delete_workspace_preset,
    get_workspace_preset,
    list_workspace_presets,
    save_workspace_preset,
)


app = Flask(__name__)


@app.after_request
def _mark_legacy_research_session_surface(response: Response) -> Response:
    """Keep old clients working while making the Researcher migration explicit."""
    path = request.path
    if path.startswith("/api/agent/research/sessions") or path.startswith(
        "/api/agent/research/iterations"
    ) or path == "/api/agent/research/context":
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "Sat, 31 Jan 2027 00:00:00 GMT"
        response.headers["Link"] = '</api/agent/researcher/sessions>; rel="successor-version"'
    return response

_binance_backfill_worker_lock = threading.Lock()
_binance_backfill_worker_thread: threading.Thread | None = None
_openbb_export_worker_lock = threading.Lock()
_openbb_export_worker_thread: threading.Thread | None = None
_openbb_gateway_restart_lock = threading.Lock()
_polymarket_export_worker_lock = threading.Lock()
_polymarket_export_worker_thread: threading.Thread | None = None
_requirement_maintenance_thread: threading.Thread | None = None
_requirement_maintenance_lock = threading.Lock()
_research_experiment_thread: threading.Thread | None = None
_research_experiment_lock = threading.Lock()
_research_run_orchestrator_thread: threading.Thread | None = None
_research_run_worker_thread: threading.Thread | None = None
_research_run_lock = threading.Lock()
_research_run_admission = ResourceAdmissionController()


def _spawn_crsp_import_worker(job_id: str, *, chunk_rows: int = 250_000) -> dict:
    """Launch a durable process; the persisted checkpoint owns recovery."""
    log_path = BASE_DIR / ".datatube" / f"crsp-import-{job_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "scripts/us_equity_archive.py", "run-crsp-import-job",
        "--job-id", job_id, "--chunk-rows", str(max(10_000, int(chunk_rows))),
    ]
    kwargs = {"cwd": str(BASE_DIR), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, **kwargs)
    return {"started": True, "pid": process.pid, "log_path": str(log_path)}


def _spawn_sec_import_worker(job_id: str, *, target_rows: int = 250_000) -> dict:
    """Launch the resumable SEC bulk worker outside the HTTP process."""
    log_path = BASE_DIR / ".datatube" / f"sec-import-{job_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "scripts/us_equity_archive.py", "run-sec-bulk-import-job",
        "--job-id", job_id, "--target-rows", str(max(25_000, int(target_rows))),
    ]
    kwargs = {"cwd": str(BASE_DIR), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, **kwargs)
    return {"started": True, "pid": process.pid, "log_path": str(log_path)}


def _start_binance_backfill_worker() -> bool:
    """Start the local worker without holding an HTTP request open."""
    global _binance_backfill_worker_thread
    with _binance_backfill_worker_lock:
        if _binance_backfill_worker_thread and _binance_backfill_worker_thread.is_alive():
            return False

        def run() -> None:
            worker = BinanceBackfillWorker(
                BinanceBackfillTaskExecutor(get_default_store()),
                f"research-ui-backfill-{int(time.time())}",
            )
            while True:
                try:
                    result = worker.run_once(lease_seconds=300)
                except Exception as exc:
                    print(f"[BACKFILL][ERR] {exc}")
                    continue
                if result.get("status") == "IDLE":
                    return

        _binance_backfill_worker_thread = threading.Thread(
            target=run, name="research-ui-binance-backfill", daemon=True,
        )
        _binance_backfill_worker_thread.start()
        return True


def _start_openbb_export_worker() -> bool:
    """Run queued OpenBB exports in the same controlled task plane as Binance."""
    global _openbb_export_worker_thread
    _ensure_openbb_gateway()
    with _openbb_export_worker_lock:
        if _openbb_export_worker_thread and _openbb_export_worker_thread.is_alive():
            return False

        def run() -> None:
            worker = OpenBBResearchWorker(
                OpenBBResearchTaskExecutor(get_default_store(), load_web_settings()),
                f"research-ui-openbb-{int(time.time())}",
            )
            while True:
                try:
                    result = worker.run_once(lease_seconds=300)
                except Exception as exc:
                    print(f"[OPENBB][ERR] {exc}")
                    continue
                if result.get("status") == "IDLE":
                    return

        _openbb_export_worker_thread = threading.Thread(
            target=run, name="research-ui-openbb-export", daemon=True,
        )
        _openbb_export_worker_thread.start()
        return True


def _ensure_openbb_gateway(wait_seconds: int = 15) -> None:
    settings = load_web_settings()
    provider = OpenBBProviderService(settings)
    if not provider.config.enabled or provider.health().get("ok"):
        return
    python = BASE_DIR / ".openbb-venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError("OpenBB gateway runtime is not installed")
    log_path = BASE_DIR / ".datatube" / "openbb.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            [str(python), "scripts/openbb_service.py", "run"],
            cwd=str(BASE_DIR),
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    pid_path = BASE_DIR / ".datatube" / "openbb.pid"
    pid_path.write_text(str(process.pid), encoding="utf-8")
    deadline = time.time() + max(5, wait_seconds)
    while time.time() < deadline:
        if provider.health().get("ok"):
            return
        if process.poll() is not None:
            break
        time.sleep(0.5)
    raise RuntimeError(f"OpenBB gateway did not become ready; see {log_path}")


def _restart_openbb_gateway(wait_seconds: int = 30) -> dict:
    """Restart only the bootstrap-managed local OpenBB process and verify health."""

    with _openbb_gateway_restart_lock:
        settings = load_web_settings()
        provider = OpenBBProviderService(settings)
        if not provider.config.enabled:
            raise ValueError("OpenBB is disabled in Settings")
        pid_path = BASE_DIR / ".datatube" / "openbb.pid"
        managed_pid = None
        try:
            managed_pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, TypeError, ValueError, OSError):
            managed_pid = None
        if provider.health().get("ok") and not managed_pid:
            raise RuntimeError(
                "OpenBB is online but has no DataTube-managed process ID; stop it manually before reloading"
            )
        if managed_pid:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(managed_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                try:
                    os.kill(managed_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            deadline = time.time() + 10
            while time.time() < deadline and provider.health().get("ok"):
                time.sleep(0.25)
            if provider.health().get("ok"):
                raise RuntimeError("Managed OpenBB process did not stop; reload was aborted safely")
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
        _ensure_openbb_gateway(wait_seconds=wait_seconds)
        health = OpenBBProviderService(load_web_settings()).health()
        if not health.get("ok"):
            raise RuntimeError("OpenBB restarted but did not pass its health check")
        marker_path = BASE_DIR / ".datatube" / "openbb-runtime.json"
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if isinstance(marker, dict):
                marker["state"] = "ready"
                marker["verified_at"] = time.time()
                marker_path.write_text(
                    json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return health


def _start_polymarket_export_worker() -> bool:
    """Run queued Polymarket exports in the controlled Research task plane."""
    global _polymarket_export_worker_thread
    with _polymarket_export_worker_lock:
        if _polymarket_export_worker_thread and _polymarket_export_worker_thread.is_alive():
            return False

        def run() -> None:
            worker = PolymarketResearchWorker(
                PolymarketResearchTaskExecutor(get_default_store()),
                f"research-ui-polymarket-{int(time.time())}",
            )
            while True:
                try:
                    result = worker.run_once(lease_seconds=300)
                except Exception as exc:
                    print(f"[POLYMARKET][ERR] {exc}")
                    continue
                if result.get("status") == "IDLE":
                    return

        _polymarket_export_worker_thread = threading.Thread(
            target=run, name="research-ui-polymarket-export", daemon=True,
        )
        _polymarket_export_worker_thread.start()
        return True


def _start_requirement_maintenance() -> bool:
    """Continuously maintain Library and Research data without UI actions."""
    global _requirement_maintenance_thread
    with _requirement_maintenance_lock:
        if _requirement_maintenance_thread and _requirement_maintenance_thread.is_alive():
            return False

        def run() -> None:
            maintenance = RequirementMaintenanceService(get_default_store())
            while True:
                try:
                    result = maintenance.run_once()
                    task_types = set(result.get("task_types") or [])
                    if "BINANCE_BARS_BACKFILL" in task_types:
                        _start_binance_backfill_worker()
                    if "OPENBB_EQUITY_DAILY_EXPORT" in task_types:
                        _start_openbb_export_worker()
                    if "POLYMARKET_PRICE_HISTORY_EXPORT" in task_types:
                        _start_polymarket_export_worker()
                    for error in result.get("errors") or []:
                        print(
                            f"[REQUIREMENT-MAINTENANCE][ERR] "
                            f"owner={error.get('owner_id')} {error.get('error')}"
                        )
                except Exception as exc:
                    print(f"[REQUIREMENT-MAINTENANCE][ERR] {exc}")
                time.sleep(30)

        _requirement_maintenance_thread = threading.Thread(
            target=run,
            name="requirement-data-maintenance",
            daemon=True,
        )
        _requirement_maintenance_thread.start()
        return True


def _start_research_experiment_orchestrator() -> bool:
    """Advance semantic Experiments without exposing internal phases to Agents."""
    global _research_experiment_thread
    with _research_experiment_lock:
        if _research_experiment_thread and _research_experiment_thread.is_alive():
            return False

        def run() -> None:
            while True:
                try:
                    _advance_research_experiments_once()
                except Exception as exc:
                    print(f"[RESEARCH-EXPERIMENT][ERR] {type(exc).__name__}")
                time.sleep(10)

        _research_experiment_thread = threading.Thread(
            target=run,
            name="research-experiment-orchestrator",
            daemon=True,
        )
        _research_experiment_thread.start()
        return True


def _dispatch_research_run_once() -> bool:
    """Launch at most one isolated formal Run outside the HTTP request path."""

    global _research_run_worker_thread
    with _research_run_lock:
        if _research_run_worker_thread and _research_run_worker_thread.is_alive():
            return False
        with get_default_store().connection() as conn:
            queued = conn.execute(
                "SELECT run_id FROM research_runs_v2 WHERE status='QUEUED' "
                "ORDER BY priority DESC, queued_at LIMIT 1"
            ).fetchone()
        if queued is None:
            return False
        run_id = str(queued["run_id"])
        run_service = ResearchRunService(get_default_store())
        # Re-estimate at dispatch time so environment and dataset changes are
        # reflected without asking the caller to resubmit or choose a worker.
        run_service.apply_automatic_routing(run_id)
        run = run_service.get(run_id)
        if run is None:
            return False
        plan = ResearchWorkloadPlanner(get_default_store()).plan(run)
        route = IntelligentWorkloadRouter().route_research(plan)
        token = f"formal-research-dispatch:{run_id}"
        if not _research_run_admission.acquire(
            token, route.resource_class, route.worker_memory_mb
        ):
            return False

        def execute() -> None:
            try:
                ResearchRunWorker(
                    get_default_store(), "formal-research-dispatcher"
                ).run_once(
                    lease_seconds=300,
                    run_id=run_id,
                    isolate_execution=True,
                )
            except Exception as exc:
                print(
                    f"[RESEARCH-RUN][ERR] {type(exc).__name__}: {str(exc)[:500]}",
                    flush=True,
                )
            finally:
                _research_run_admission.release(token)

        _research_run_worker_thread = threading.Thread(
            target=execute,
            name="formal-research-worker",
            daemon=True,
        )
        _research_run_worker_thread.start()
        return True


def _start_research_run_orchestrator() -> bool:
    """Drain the durable formal-Run queue while preserving frontend capacity."""

    global _research_run_orchestrator_thread
    with _research_run_lock:
        if (
            _research_run_orchestrator_thread
            and _research_run_orchestrator_thread.is_alive()
        ):
            return False

        def run() -> None:
            # Any RUNNING lease at process startup belonged to a dead worker.
            # Quarantine it instead of silently repeating expensive compute.
            with get_default_store().connection() as conn:
                interrupted = [
                    str(row[0])
                    for row in conn.execute(
                        "SELECT run_id FROM research_runs_v2 WHERE status='RUNNING'"
                    ).fetchall()
                ]
            recovery_worker = ResearchRunWorker(
                get_default_store(), "formal-research-restart-recovery"
            )
            for run_id in interrupted:
                recovery_worker.fail_interrupted(run_id)
            while True:
                try:
                    _dispatch_research_run_once()
                except Exception as exc:
                    print(
                        f"[RESEARCH-RUN][DISPATCH-ERR] {type(exc).__name__}: {str(exc)[:500]}",
                        flush=True,
                    )
                time.sleep(2)

        _research_run_orchestrator_thread = threading.Thread(
            target=run,
            name="formal-research-orchestrator",
            daemon=True,
        )
        _research_run_orchestrator_thread.start()
        return True


def _advance_research_experiments_once() -> dict:
    """Dispatch Experiments and independently drain their provider tasks.

    Global Requirement maintenance may spend a long time resolving a very
    large Research universe. Provider workers must not wait for that scan to
    return after a scoped Experiment has already queued its preparation task.
    """
    result = ResearchExperimentService(
        get_default_store(), isolate_experiment_execution=True
    ).advance_pending(limit=20)
    _dispatch_research_run_once()
    _start_binance_backfill_worker()
    _start_openbb_export_worker()
    _start_polymarket_export_worker()
    return result


BASE_DIR = Path(__file__).resolve().parent


def _resolve_artifact_content_path(
    content_uri: str,
    *,
    allowed_roots: tuple[Path, ...] | None = None,
) -> Path:
    """Resolve an artifact only inside a trusted DataTube-owned storage root."""
    path = Path(content_uri)
    if not path.is_absolute():
        path = BASE_DIR / path
    path = path.resolve()
    roots = allowed_roots or (
        BASE_DIR.resolve(),
        get_data_platform_storage_root().resolve(),
    )
    if not any(path.is_relative_to(root.resolve()) for root in roots):
        raise ValueError("Artifact content is outside the trusted DataTube storage roots")
    return path
EXTERNAL_LATENCY_TARGETS = [
    {"key": "polymarket_web", "label": "Polymarket Web", "url": "https://polymarket.com", "group": "polymarket"},
    {"key": "polymarket_clob", "label": "Polymarket CLOB", "url": "https://clob.polymarket.com", "group": "polymarket"},
    {"key": "polymarket_gamma", "label": "Polymarket Gamma", "url": "https://gamma-api.polymarket.com/markets?limit=1", "group": "polymarket"},
    {"key": "polymarket_data", "label": "Polymarket Data API", "url": "https://data-api.polymarket.com", "group": "polymarket"},
    {"key": "binance", "label": "Binance", "url": "https://api.binance.com/api/v3/time", "group": "crypto"},
    {"key": "coingecko", "label": "CoinGecko", "url": "https://api.coingecko.com/api/v3/ping", "group": "crypto"},
    {"key": "finnhub", "label": "Finnhub", "url": "https://finnhub.io/api/v1/quote?symbol=AAPL", "group": "finance"},
]


def debug_timing(name: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                dt = (time.perf_counter() - t0) * 1000
                print(f"[BE][OK] {name} {request.method} {request.path} {dt:.1f}ms")
                return result
            except Exception as exc:
                dt = (time.perf_counter() - t0) * 1000
                print(f"[BE][ERR] {name} {request.method} {request.path} {dt:.1f}ms error={exc}")
                raise

        return wrapper

    return decorator


def _json_error(exc: Exception, status_code: int = 500):
    try:
        path = request.path or ""
        if path.startswith("/api/agent/") or path.startswith("/api/approvals/") or path.startswith("/api/event-graph/change-requests/"):
            payload = request.get_json(silent=True) if request.method not in {"GET", "HEAD"} else dict(request.args)
            if not isinstance(payload, dict):
                payload = {}
            payload.setdefault("actor_type", "agent" if path.startswith("/api/agent/") else "human")
            payload.setdefault("actor_id", "agent_strategy_assistant" if path.startswith("/api/agent/") else "local_user")
            payload.setdefault("_endpoint", path)
            payload.setdefault("_method", request.method)
            agent_service.record_request_error(
                path=path,
                method=request.method,
                status_code=status_code,
                error=str(exc),
                payload=payload,
            )
    except Exception:
        pass
    return jsonify({"ok": False, "error": str(exc)}), status_code


def _is_local_request() -> bool:
    remote = request.remote_addr or ""
    return remote in {"127.0.0.1", "::1", "localhost"}


def require_local_request(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _is_local_request():
            return jsonify({"ok": False, "error": "Settings are only available from this computer."}), 403
        return func(*args, **kwargs)

    return wrapper


def _latency_tone(latency_ms: int | None, ok: bool) -> str:
    if not ok or latency_ms is None:
        return "error"
    if latency_ms <= 500:
        return "good"
    return "warning"


def _check_http_latency(target: dict, timeout: float = 2.5) -> dict:
    started = time.perf_counter()
    url = str(target.get("url") or "")
    try:
        response = SESSION.get(url, timeout=timeout, allow_redirects=True)
        latency_ms = int((time.perf_counter() - started) * 1000)
        ok = response.status_code < 500
        return {
            **target,
            "ok": ok,
            "status": _latency_tone(latency_ms, ok),
            "latency_ms": latency_ms,
            "http_status": response.status_code,
            "error": None if ok else f"HTTP {response.status_code}",
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            **target,
            "ok": False,
            "status": "error",
            "latency_ms": latency_ms,
            "http_status": None,
            "error": str(exc),
        }


def _resolve_data_path(raw_path: str | None, fallback: str) -> Path:
    text = str(raw_path or fallback or "").strip()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _check_sqlite_latency(key: str, label: str, path: Path) -> dict:
    started = time.perf_counter()
    try:
        if not path.exists():
            raise FileNotFoundError(str(path))
        with sqlite3.connect(str(path), timeout=1.0) as conn:
            conn.execute("SELECT 1").fetchone()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "key": key,
            "label": label,
            "path": str(path),
            "ok": True,
            "status": _latency_tone(latency_ms, True),
            "latency_ms": latency_ms,
            "size_bytes": path.stat().st_size,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "key": key,
            "label": label,
            "path": str(path),
            "ok": False,
            "status": "error",
            "latency_ms": latency_ms,
            "error": str(exc),
        }


def _group_latency_status(items: list[dict]) -> dict:
    active = [item for item in items if item.get("status") != "disabled"]
    disabled = len(items) - len(active)
    usable = [item for item in active if item.get("ok") and item.get("latency_ms") is not None]
    failed = [item for item in active if not item.get("ok")]
    if not active:
        return {"ok": True, "status": "disabled", "latency_ms": None, "failed": 0, "count": 0, "disabled": disabled}
    if not usable:
        return {"ok": False, "status": "error", "latency_ms": None, "failed": len(failed), "count": len(active), "disabled": disabled}
    max_latency = max(int(item.get("latency_ms") or 0) for item in usable)
    status = "warning" if failed else _latency_tone(max_latency, True)
    return {
        "ok": not failed,
        "status": status,
        "latency_ms": max_latency,
        "failed": len(failed),
        "count": len(active),
        "disabled": disabled,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/settings")
@require_local_request
def settings_page():
    return render_template("settings.html")


@app.get("/research")
def research_workspace_page():
    # The Research index is the default landing surface. Render its compact
    # card projection into the first HTML response so users are not held behind
    # a full-page loading gate while the large workspace client initializes.
    try:
        initial_project_summaries = ResearchControlPlane(
            get_default_store()
        ).list_project_summaries(limit=500)
    except Exception as exc:
        # Preserve the client-side error/retry path if local metadata is
        # temporarily unavailable instead of turning the page shell into a 500.
        app.logger.warning("Unable to bootstrap Research index: %s", exc)
        initial_project_summaries = None
    return render_template(
        "research_workspace.html",
        initial_surface="research",
        initial_project_id="",
        initial_project_summaries=initial_project_summaries,
    )


@app.get("/research/<project_id>")
def research_detail_page(project_id: str):
    return render_template(
        "research_workspace.html",
        initial_surface="research-detail",
        initial_project_id=project_id,
    )


@app.get("/library")
def research_library_page():
    return render_template("research_workspace.html", initial_surface="library", initial_project_id="")


@app.get("/runs")
def research_runs_page():
    return render_template("research_workspace.html", initial_surface="runs", initial_project_id="")


@app.get("/data-catalog")
def research_data_catalog_page():
    return render_template("research_workspace.html", initial_surface="data-catalog", initial_project_id="")


@app.get("/approvals")
def approvals_page():
    return render_template("research_workspace.html", initial_surface="approvals", initial_project_id="")


@app.get("/watchlist")
def watchlist_page():
    return render_template("watchlist.html")


@app.get("/history")
def history_workspace_page():
    return render_template("history_workspace.html")


@app.get("/backtests/<int:run_id>")
def backtest_report_page(run_id: int):
    return render_template("backtest_report.html", run_id=run_id)


@app.get("/agent-monitor")
def agent_monitor_page():
    return render_template("agent_monitor.html")


@app.get("/event-graph")
def event_graph_page():
    return render_template("event_graph.html")


@app.get("/eventgraph")
def eventgraph_page_alias():
    return render_template("event_graph.html")


@app.get("/ledger")
def ledger_page():
    return render_template("ledger.html")


@app.get("/strategies/<int:row_id>/workspace")
def strategy_workspace_page(row_id: int):
    return render_template("strategy_workspace.html", row_id=row_id)


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "collector_running": collector.is_running(),
            "ws_market_sync": ws_market_sync.get_state(),
        }
    )


@app.get("/api/research/data/catalog")
@debug_timing("research_data_catalog")
def research_data_catalog():
    try:
        service = DatasetCatalogService(get_default_store())
        data = service.list_catalog(
            instrument_id=request.args.get("instrument_id", ""),
            data_type=request.args.get("data_type", ""),
            status=request.args.get("status", ""),
        )
        return jsonify({"ok": True, "data": [asdict(item) for item in data]})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/capabilities")
@debug_timing("research_data_capabilities")
def research_data_capabilities():
    try:
        return jsonify({
            "ok": True,
            "data": ResearchDataCapabilityService(load_web_settings(), base_dir=BASE_DIR).describe(),
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/projects/<project_id>/factor-input-candidates")
@debug_timing("research_factor_input_candidates")
def research_factor_input_candidates(project_id: str):
    try:
        data = FactorInputCandidateResolver(
            get_default_store(),
            settings=load_web_settings(),
            base_dir=BASE_DIR,
        ).resolve_project(project_id)
        return jsonify({"ok": True, "data": data})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/instruments/search")
@debug_timing("research_instrument_search")
def research_instrument_search():
    try:
        provider = str(request.args.get("provider") or "AUTO").strip().upper()
        market = str(request.args.get("market") or "SPOT").strip().upper()
        category = str(request.args.get("category") or "").strip()
        if not category:
            if market == "TOKENIZED_EQUITY":
                category = "rwa_stock_token"
            elif market in {"USDM_FUTURES", "COINM_FUTURES", "OPTIONS"}:
                category = "crypto_derivatives"
            elif provider in {"YFINANCE", "FINNHUB"} or market in {"EQUITY", "XNAS", "XNYS"}:
                category = "equity"
            else:
                category = "crypto_spot"
        if category == "fred":
            series_id = str(request.args.get("q") or "").strip().upper()
            rows = []
            if series_id and all(character.isalnum() or character in "._-" for character in series_id):
                rows.append({
                    "instrument_id": f"macro:FRED:{series_id}",
                    "asset_class": "macro",
                    "venue": "FRED",
                    "symbol": series_id,
                    "display_symbol": series_id,
                    "display_name": f"FRED series {series_id}",
                    "market_kind": "macro",
                    "status": "DEFINITION_ONLY",
                })
            return jsonify({
                "ok": True,
                "data": rows,
                "meta": {"message": "" if rows else "Enter an exact FRED series ID; catalog search is not connected."},
            })
        if category == "polymarket" or provider == "POLYMARKET":
            limit = max(1, min(int(request.args.get("limit") or 20), 50))
            markets = search_markets(
                query=str(request.args.get("q") or "").strip(),
                category=str(request.args.get("market_category") or "").strip(),
                limit=limit,
                force_refresh=request.args.get("refresh", "0") == "1",
                sort_by="volume24h",
                sort_dir="desc",
            )
            rows = []
            for market_row in markets:
                condition_id = str(market_row.get("condition_id") or "").strip()
                question = str(market_row.get("question") or market_row.get("title") or condition_id).strip()
                for side, token_key in (("YES", "yes_token"), ("NO", "no_token")):
                    token_id = str(market_row.get(token_key) or "").strip()
                    if not token_id:
                        continue
                    rows.append({
                        "instrument_id": f"polymarket_binary:POLYMARKET:{token_id}",
                        "symbol": token_id,
                        "display_symbol": f"{question} · {side.title()}",
                        "venue": "POLYMARKET",
                        "status": "ACTIVE" if market_row.get("active") and not market_row.get("closed") else "CLOSED",
                        "outcome": side,
                        "condition_id": condition_id,
                        "token_id": token_id,
                        "category": market_row.get("category"),
                        "end_date": market_row.get("end_date"),
                    })
            return jsonify({
                "ok": True,
                "data": rows,
                "meta": {"requested_provider": provider, "requested_market": market, "market_count": len(markets)},
            })
        if category == "coingecko":
            return jsonify({"ok": True, "data": [], "meta": {"message": "CoinGecko is available for context data, not historical Research contracts."}})
        args = dict(request.args)
        args["category"] = category
        if category == "crypto_spot":
            args.setdefault("status", "TRADING")
            args.setdefault("quote", "USDT")
        elif category == "crypto_derivatives":
            args.setdefault("status", "TRADING")
            args.setdefault("settlement", "USDT")
            if market == "USDM_FUTURES":
                args.setdefault("subtype", "usdm_futures")
        elif category == "rwa_stock_token":
            args.setdefault("status", "ACTIVE")
        result = search_binance_markets(args)
        if provider not in {"AUTO", "BINANCE", "BINANCE_WEB3", "FINNHUB"} and market in {"XNAS", "XNYS"}:
            for item in result.get("data") or []:
                symbol = str(item.get("symbol") or "").strip().upper()
                if symbol:
                    item["instrument_id"] = f"equity:{market}:{symbol}"
                    item["venue"] = market
        result.setdefault("meta", {})["requested_provider"] = provider
        result["meta"]["requested_market"] = market
        return jsonify(result)
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/instruments/register")
@require_local_request
@debug_timing("research_instrument_register")
def research_instrument_register():
    try:
        payload = request.get_json(silent=True) or {}
        provider = str(payload.get("provider") or "").strip().upper()
        market = str(payload.get("market") or "").strip().upper()
        instrument_payload = payload.get("instrument")
        if not isinstance(instrument_payload, dict):
            raise ValueError("instrument discovery result is required")

        capabilities = ResearchDataCapabilityService(load_web_settings(), base_dir=BASE_DIR).describe()
        provider_spec = next((item for item in capabilities["providers"] if item["id"] == provider), None)
        if provider_spec is None:
            raise ValueError(f"Unsupported discovery source: {provider}")
        market_spec = next((item for item in provider_spec.get("markets", []) if item["id"] == market), None)
        if market_spec is None:
            raise ValueError(f"Unsupported market {market} for {provider}")
        if market_spec.get("search_category") == "coingecko":
            raise ValueError("CoinGecko is context-only and cannot define a Universe Instrument")

        instrument_payload = {
            **instrument_payload,
            "asset_class": instrument_payload.get("asset_class") or market_spec.get("search_category"),
        }
        instrument = InstrumentRegistry(get_default_store()).register_discovered(
            instrument_payload,
            source=provider,
            market=market,
        )
        return jsonify({"ok": True, "data": asdict(instrument)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/prepare/polymarket")
@require_local_request
@debug_timing("research_polymarket_prepare")
def research_polymarket_prepare():
    return jsonify({
        "ok": False,
        "code": "CONTROLLED_TASK_REQUIRED",
        "error": "Refresh Research and use Prepare Data; Polymarket writes require a scoped Research task.",
    }), 410


@app.get("/api/research/data/providers/polymarket/worker-status")
def polymarket_worker_status():
    try:
        worker = PolymarketResearchWorker(
            PolymarketResearchTaskExecutor(get_default_store()), "status-reader"
        )
        return jsonify({"ok": True, "data": {"worker": worker.status()}})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/providers/polymarket/worker/start")
@require_local_request
@debug_timing("research_polymarket_worker_start")
def polymarket_worker_start():
    try:
        started = _start_polymarket_export_worker()
        return jsonify({"ok": True, "data": {"started": started, "running": True}}), 202
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/requirements")
@debug_timing("research_data_requirements")
def research_data_requirements():
    try:
        service = DataRequirementService(get_default_store())
        data = service.list(
            owner_type=request.args.get("owner_type", ""),
            owner_id=request.args.get("owner_id", ""),
            status=request.args.get("status", ""),
        )
        return jsonify({"ok": True, "data": [asdict(item) for item in data]})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/requirement-sets")
@debug_timing("research_data_requirement_sets")
def research_data_requirement_sets():
    try:
        service = RequirementCompiler(get_default_store())
        data = service.list(project_id=request.args.get("project_id", ""))
        return jsonify({"ok": True, "data": [asdict(item) for item in data]})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/requirement-sets/<requirement_set_id>")
@debug_timing("research_data_requirement_set")
def research_data_requirement_set(requirement_set_id: str):
    try:
        service = RequirementCompiler(get_default_store())
        result = service.get(requirement_set_id)
        if result is None:
            return jsonify({"ok": False, "error": "requirement set not found"}), 404
        return jsonify({"ok": True, "data": asdict(result)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/requirement-sets/<requirement_set_id>/coverage")
@debug_timing("research_data_requirement_set_coverage")
def research_data_requirement_set_coverage(requirement_set_id: str):
    try:
        return jsonify({"ok": True, "data": RequirementCompiler(get_default_store()).coverage(requirement_set_id)})
    except KeyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/resolved-plans")
@debug_timing("research_resolved_plans")
def research_resolved_plans():
    service = ArtifactService(get_default_store())
    return jsonify({"ok": True, "data": [asdict(item) for item in service.list(artifact_type="RESOLVED_DATA_PLAN")]})


@app.get("/api/research/input-bundles")
@debug_timing("research_input_bundles")
def research_input_bundles():
    service = ArtifactService(get_default_store())
    return jsonify({"ok": True, "data": [asdict(item) for item in service.list(artifact_type="RESEARCH_INPUT_BUNDLE")]})


@app.get("/api/research/input-bundles/<artifact_id>/verify")
@debug_timing("research_input_bundle_verify")
def research_input_bundle_verify(artifact_id: str):
    try:
        return jsonify({"ok": True, "data": ResearchInputBundleService(get_default_store()).verify(artifact_id)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/manifests/<manifest_id>")
@debug_timing("research_data_manifest")
def research_data_manifest(manifest_id: str):
    try:
        service = DatasetCatalogService(get_default_store())
        manifest = service.get_manifest(manifest_id)
        if manifest is None:
            return jsonify({"ok": False, "error": "dataset manifest not found"}), 404
        data = asdict(manifest)
        data["provenance"] = ManifestProvenanceService(get_default_store()).get(manifest_id)
        if request.args.get("verify", "0") == "1":
            data["physical_validation"] = FrozenManifestData(get_default_store(), manifest_id).verify()
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/backfill/binance/jobs")
@debug_timing("research_binance_backfill_jobs")
def research_binance_backfill_jobs():
    try:
        service = BinanceBackfillJobService(get_default_store())
        return jsonify({
            "ok": True,
            "data": service.list(
                status=request.args.get("status", ""),
                task_id=request.args.get("task_id", ""),
                limit=int(request.args.get("limit") or 200),
            ),
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/backfill/binance/jobs/<job_id>")
@debug_timing("research_binance_backfill_job")
def research_binance_backfill_job(job_id: str):
    try:
        job = BinanceBackfillJobService(get_default_store()).get(job_id)
        if job is None:
            return jsonify({"ok": False, "error": "Binance backfill job not found"}), 404
        return jsonify({"ok": True, "data": job})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/backfill/binance/worker-status")
@debug_timing("research_binance_backfill_worker_status")
def research_binance_backfill_worker_status():
    try:
        worker_id = request.args.get("worker_id", "binance-backfill-readonly-status")
        worker = BinanceBackfillWorker(BinanceBackfillTaskExecutor(get_default_store()), worker_id)
        return jsonify({"ok": True, "data": worker.status()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/backfill/binance/worker/start")
@require_local_request
@debug_timing("research_binance_backfill_worker_start")
def research_binance_backfill_worker_start():
    try:
        started = _start_binance_backfill_worker()
        return jsonify({"ok": True, "data": {"started": started, "running": True}}), 202
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/artifacts")
@debug_timing("research_artifacts")
def research_artifacts():
    try:
        service = ArtifactService(get_default_store())
        artifacts = service.list(
            artifact_type=request.args.get("artifact_type", ""),
            logical_name=request.args.get("logical_name", ""),
            limit=int(request.args.get("limit") or 200),
        )
        dependencies = service.dependencies_many(artifact.artifact_id for artifact in artifacts)
        data = []
        for artifact in artifacts:
            item = asdict(artifact)
            item["dependencies"] = dependencies.get(artifact.artifact_id, [])
            data.append(item)
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/backtest/capabilities")
@debug_timing("research_backtest_capabilities")
def research_backtest_capabilities():
    return jsonify({
        "ok": True,
        "data": {
            "legacy_provider": CURRENT_HISTORY_BACKTEST_CAPABILITIES.to_dict(),
            "research_provider": RESEARCH_BACKTEST_CAPABILITIES.to_dict(),
        },
    })


@app.post("/api/research/backtest/validate")
@debug_timing("research_backtest_validate")
def research_backtest_validate():
    try:
        payload = request.get_json(silent=True) or {}
        provider = str(payload.get("provider") or "legacy").strip().lower()
        if provider in {"research", "research_backtest_v2"}:
            result = ResearchBacktestProvider().validate(payload.get("execution_spec"))
        else:
            result = ExistingBacktestAdapter().validate(payload)
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc, 400)


@app.get("/api/research/universes")
@debug_timing("research_universes")
def research_universes():
    try:
        service = UniverseService(get_default_store())
        data = service.list_definitions(
            status=request.args.get("status", "ACTIVE"),
            limit=int(request.args.get("limit") or 200),
        )
        return jsonify({"ok": True, "data": [asdict(item) for item in data]})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/universes")
@require_local_request
@debug_timing("research_universe_create")
def research_universe_create():
    try:
        payload = request.get_json(silent=True) or {}
        result = UniverseService(get_default_store()).create_definition(
            name=str(payload.get("name") or ""),
            version=str(payload.get("version") or ""),
            universe_type=str(payload.get("universe_type") or "STATIC_LIST"),
            parameters=payload.get("parameters") or {},
            selection_rule_version=str(payload.get("selection_rule_version") or "universe-engine.v1"),
            owner_project_id=str(payload.get("owner_project_id") or ""),
            library_scope=str(payload.get("library_scope") or "GLOBAL"),
        )
        return jsonify({"ok": True, "data": asdict(result)}), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/universes/<universe_definition_id>/snapshots")
@require_local_request
@debug_timing("research_universe_snapshot_create")
def research_universe_snapshot_create(universe_definition_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        catalog = DatasetCatalogService(get_default_store())
        manifests = []
        for manifest_id in payload.get("manifest_ids") or []:
            manifest = catalog.get_manifest(str(manifest_id))
            if manifest is None:
                raise ValueError(f"dataset Manifest not found: {manifest_id}")
            manifests.append(manifest)
        result = UniverseService(get_default_store()).resolve_snapshot(
            universe_definition_id=universe_definition_id,
            as_of_time=str(payload.get("as_of_time") or ""),
            manifests=manifests,
        )
        return jsonify({"ok": True, "data": asdict(result)}), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/universes/<universe_definition_id>/publish")
@require_local_request
@debug_timing("research_universe_publish")
def research_universe_publish(universe_definition_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = ResearchLibraryService(get_default_store()).publish_universe(
            universe_definition_id=universe_definition_id,
            project_id=str(payload.get("project_id") or ""),
        )
        return jsonify({"ok": True, "data": result}), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/universes/<universe_definition_id>/snapshots")
@debug_timing("research_universe_snapshots")
def research_universe_snapshots(universe_definition_id: str):
    try:
        service = UniverseService(get_default_store())
        if service.get_definition(universe_definition_id) is None:
            return jsonify({"ok": False, "error": "universe definition not found"}), 404
        return jsonify({
            "ok": True,
            "data": [asdict(item) for item in service.list_snapshots(universe_definition_id)],
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/universe-snapshots/<universe_snapshot_id>")
@debug_timing("research_universe_snapshot")
def research_universe_snapshot(universe_snapshot_id: str):
    try:
        snapshot = UniverseService(get_default_store()).get_snapshot(universe_snapshot_id)
        if snapshot is None:
            return jsonify({"ok": False, "error": "universe snapshot not found"}), 404
        return jsonify({"ok": True, "data": asdict(snapshot)})
    except Exception as exc:
        return _json_error(exc)


def _shared_universe_error(exc: Exception):
    if isinstance(exc, UniverseConflictError):
        return jsonify({
            "ok": False, "code": exc.code, "error": str(exc),
            "data": {"current_revision_id": exc.current_revision_id},
        }), 409
    if isinstance(exc, UniverseSharedImpactError):
        return jsonify({
            "ok": False, "code": exc.code, "error": str(exc),
            "data": {"affected_research": exc.research},
        }), 409
    if isinstance(exc, UniverseResolutionError):
        return jsonify({
            "ok": False, "code": exc.code, "error": str(exc), "data": exc.details,
        }), 422
    if isinstance(exc, (TypeError, ValueError)):
        return _json_error(exc, 400)
    return _json_error(exc)


@app.get("/api/library/universes")
@debug_timing("shared_universe_list")
def shared_universe_list():
    try:
        data = SharedUniverseService(get_default_store()).list(
            include_archived=str(request.args.get("include_archived") or "").lower() in {"1", "true", "yes"}
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/library/universes")
@require_local_request
@debug_timing("shared_universe_create")
def shared_universe_create():
    try:
        result = SharedUniverseService(get_default_store()).create(
            request.get_json(silent=True) or {}, created_by="local_ui_user"
        )
        return jsonify({"ok": True, "data": result}), 201
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/library/universes/preview")
@debug_timing("shared_universe_preview")
def shared_universe_preview():
    try:
        payload = request.get_json(silent=True) or {}
        result = SharedUniverseService(get_default_store()).preview(
            payload, universe_id=str(payload.get("universe_id") or "")
        )
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/library/universes/script/render")
@debug_timing("shared_universe_script_render")
def shared_universe_script_render():
    try:
        payload = request.get_json(silent=True) or {}
        result = SharedUniverseService(get_default_store()).render_script(payload)
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/library/universes/script/parse")
@debug_timing("shared_universe_script_parse")
def shared_universe_script_parse():
    try:
        payload = request.get_json(silent=True) or {}
        result = SharedUniverseService(get_default_store()).parse_script(str(payload.get("script") or ""))
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.get("/api/library/universes/<universe_id>")
@debug_timing("shared_universe_detail")
def shared_universe_detail(universe_id: str):
    try:
        result = SharedUniverseService(get_default_store()).get(universe_id)
        if result is None:
            return jsonify({"ok": False, "error": "Universe not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.patch("/api/library/universes/<universe_id>")
@require_local_request
@debug_timing("shared_universe_update")
def shared_universe_update(universe_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = SharedUniverseService(get_default_store()).update(
            universe_id,
            payload,
            expected_current_revision_id=str(payload.get("expected_current_revision_id") or ""),
            confirm_shared=bool(payload.get("confirm_shared", False)),
            current_project_id=str(payload.get("current_project_id") or ""),
            created_by="local_ui_user",
            change_summary=str(payload.get("change_summary") or ""),
        )
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/library/universes/<universe_id>/copy")
@require_local_request
@debug_timing("shared_universe_copy")
def shared_universe_copy(universe_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = SharedUniverseService(get_default_store()).copy(
            universe_id,
            name=str(payload.get("name") or ""),
            project_id=str(payload.get("project_id") or ""),
            replace_primary=bool(payload.get("replace_primary", False)),
            definition_override=payload.get("definition"),
            created_by="local_ui_user",
        )
        return jsonify({"ok": True, "data": result}), 201
    except Exception as exc:
        return _shared_universe_error(exc)


@app.get("/api/library/universes/<universe_id>/usage")
@debug_timing("shared_universe_usage")
def shared_universe_usage(universe_id: str):
    try:
        return jsonify({"ok": True, "data": SharedUniverseService(get_default_store()).usage(universe_id)})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.get("/api/library/universes/<universe_id>/history")
@debug_timing("shared_universe_history")
def shared_universe_history(universe_id: str):
    try:
        return jsonify({"ok": True, "data": SharedUniverseService(get_default_store()).history(universe_id)})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/library/universes/<universe_id>/restore")
@require_local_request
@debug_timing("shared_universe_restore")
def shared_universe_restore(universe_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = SharedUniverseService(get_default_store()).restore(
            universe_id,
            str(payload.get("revision_id") or ""),
            expected_current_revision_id=str(payload.get("expected_current_revision_id") or ""),
            confirm_shared=bool(payload.get("confirm_shared", False)),
            current_project_id=str(payload.get("current_project_id") or ""),
            created_by="local_ui_user",
        )
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/library/universes/<universe_id>/archive")
@require_local_request
@debug_timing("shared_universe_archive")
def shared_universe_archive(universe_id: str):
    try:
        return jsonify({"ok": True, "data": SharedUniverseService(get_default_store()).archive(universe_id)})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.get("/api/research/projects/<project_id>/universes")
@debug_timing("research_shared_universe_list")
def research_shared_universe_list(project_id: str):
    try:
        data = SharedUniverseService(get_default_store()).list_project(
            project_id,
            include_removed=str(request.args.get("include_removed") or "").lower() in {"1", "true", "yes"},
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/research/projects/<project_id>/universes")
@require_local_request
@debug_timing("research_shared_universe_create")
def research_shared_universe_create(project_id: str):
    try:
        result = SharedUniverseService(get_default_store()).create(
            request.get_json(silent=True) or {}, created_by="local_ui_user", project_id=project_id
        )
        return jsonify({"ok": True, "data": result}), 201
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/research/projects/<project_id>/universes/add")
@require_local_request
@debug_timing("research_shared_universe_add")
def research_shared_universe_add(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = SharedUniverseService(get_default_store()).bind(
            project_id=project_id,
            universe_id=str(payload.get("universe_id") or ""),
            role=str(payload.get("role") or "REFERENCE"),
            replace_primary=bool(payload.get("replace_primary", False)),
        )
        return jsonify({"ok": True, "data": result}), 201
    except Exception as exc:
        return _shared_universe_error(exc)


@app.post("/api/research/projects/<project_id>/universes/<universe_id>/copy")
@require_local_request
@debug_timing("research_shared_universe_copy")
def research_shared_universe_copy(project_id: str, universe_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = SharedUniverseService(get_default_store()).copy(
            universe_id,
            name=str(payload.get("name") or ""),
            project_id=project_id,
            replace_primary=bool(payload.get("replace_primary", False)),
            definition_override=payload.get("definition"),
            created_by="local_ui_user",
        )
        return jsonify({"ok": True, "data": result}), 201
    except Exception as exc:
        return _shared_universe_error(exc)


@app.delete("/api/research/projects/<project_id>/universes/<universe_id>")
@require_local_request
@debug_timing("research_shared_universe_remove")
def research_shared_universe_remove(project_id: str, universe_id: str):
    try:
        result = SharedUniverseService(get_default_store()).remove_binding(
            project_id=project_id, universe_id=universe_id
        )
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.put("/api/research/projects/<project_id>/universe-bindings")
@require_local_request
@debug_timing("research_shared_universe_primary")
def research_shared_universe_primary(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = SharedUniverseService(get_default_store()).set_primary(
            project_id=project_id, universe_id=str(payload.get("universe_id") or "")
        )
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _shared_universe_error(exc)


@app.get("/api/research/projects")
@debug_timing("research_projects")
def research_projects():
    try:
        service = ResearchControlPlane(get_default_store())
        data = service.list_projects(
            summary_state=request.args.get("summary_state", ""),
            limit=int(request.args.get("limit") or 100),
            include_archived=str(request.args.get("include_archived") or "").lower() in {"1", "true", "yes"},
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/project-summaries")
@debug_timing("research_project_summaries")
def research_project_summaries():
    try:
        service = ResearchControlPlane(get_default_store())
        data = service.list_project_summaries(
            summary_state=request.args.get("summary_state", ""),
            limit=int(request.args.get("limit") or 100),
            include_archived=str(request.args.get("include_archived") or "").lower()
            in {"1", "true", "yes"},
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects")
@require_local_request
@debug_timing("research_project_create")
def research_project_create():
    try:
        payload = request.get_json(silent=True) or {}
        result = ResearchControlPlane(get_default_store()).create_project(
            title=str(payload.get("title") or "").strip(),
            objective=str(payload.get("objective") or "").strip(),
            created_by="local_ui_user",
        )
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/archive")
@require_local_request
@debug_timing("research_project_archive")
def research_project_archive(project_id: str):
    try:
        return jsonify({"ok": True, "data": RequirementWorkspaceService(get_default_store()).archive_project(project_id)})
    except ValueError as exc:
        return _json_error(exc, 404)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/requirement-sets")
@require_local_request
@debug_timing("research_requirement_set_compile")
def research_requirement_set_compile(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        if ResearchControlPlane(get_default_store()).get_project(project_id) is None:
            return jsonify({"ok": False, "error": "research project not found"}), 404
        context = dict(payload.get("context") or {})
        alias_source = str(payload.get("instrument_source") or "").strip().lower()
        if alias_source:
            registry = InstrumentRegistry(get_default_store())
            resolved_instruments = []
            unresolved_instruments = []
            for value in context.get("instrument_ids") or []:
                instrument_id = str(value or "").strip()
                if not instrument_id:
                    continue
                if ":" in instrument_id:
                    resolved_instruments.append(instrument_id)
                    continue
                resolved = registry.resolve_alias(alias_source, instrument_id.upper())
                if resolved:
                    resolved_instruments.append(resolved)
                else:
                    unresolved_instruments.append(instrument_id)
            if unresolved_instruments:
                raise ValueError(
                    f"instrument aliases not found for {alias_source}: {', '.join(unresolved_instruments)}"
                )
            context["instrument_ids"] = resolved_instruments
        store = get_default_store()
        result = RequirementCompiler(store).compile(
            project_id=project_id,
            factor_specs=payload.get("factor_specs") or [],
            universe_requirements=payload.get("universe_requirements") or [],
            evaluation_requirements=payload.get("evaluation_requirements") or [],
            backtest_requirements=payload.get("backtest_requirements") or [],
            manual_requirements=payload.get("manual_requirements") or [],
            context=context,
        )
        ResearchLibraryService(store).set_local_requirements(
            project_id=project_id,
            requirement_set_id=result.requirement_set_id,
        )
        return jsonify({"ok": True, "data": asdict(result)}), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/resolved-plans")
@require_local_request
@debug_timing("research_resolved_plan_create")
def research_resolved_plan_create(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = ResolvedDataPlanService(get_default_store()).create(
            project_id=project_id,
            logical_name=str(payload.get("logical_name") or "research_data_plan").strip(),
            requirement_set_id=str(payload.get("requirement_set_id") or "").strip(),
            route=payload.get("route") or {},
            source_policy=payload.get("source_policy") or {},
            canonical=payload.get("canonical") or {},
            estimates=payload.get("estimates") or {},
        )
        return jsonify({"ok": True, "data": asdict(result)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/input-bundles")
@require_local_request
@debug_timing("research_input_bundle_create")
def research_input_bundle_create(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        if ResearchControlPlane(get_default_store()).get_project(project_id) is None:
            return jsonify({"ok": False, "error": "research project not found"}), 404
        result = ResearchInputBundleService(get_default_store()).create(
            project_id=project_id,
            logical_name=str(payload.get("logical_name") or "research_input_bundle").strip(),
            manifest_ids=payload.get("manifest_ids") or [],
            universe_snapshot_id=str(payload.get("universe_snapshot_id") or "").strip(),
            requirement_set_id=str(payload.get("requirement_set_id") or "").strip(),
            resolved_plan_id=str(payload.get("resolved_plan_id") or "").strip(),
            policy_versions=payload.get("policy_versions") or {},
            compiler_version=str(payload.get("compiler_version") or "").strip(),
            canonicalizer_version=str(payload.get("canonicalizer_version") or "canonicalizer_v1").strip(),
        )
        verification = ResearchInputBundleService(get_default_store()).verify(result.artifact_id)
        data = asdict(result)
        data["verification"] = verification
        return jsonify({"ok": True, "data": data}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/projects/<project_id>")
@debug_timing("research_project")
def research_project(project_id: str):
    try:
        service = ResearchControlPlane(get_default_store())
        project = service.get_project(project_id)
        if project is None:
            return jsonify({"ok": False, "error": "research project not found"}), 404
        return jsonify({
            "ok": True,
            "data": {
                "project": project,
                "plans": service.list_plans(project_id),
                "grants": service.list_grants(project_id=project_id),
                "tasks": service.list_tasks(project_id=project_id),
            },
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/projects/<project_id>/plans")
@debug_timing("research_project_plans")
def research_project_plans(project_id: str):
    try:
        service = ResearchControlPlane(get_default_store())
        return jsonify({"ok": True, "data": service.list_plans(project_id)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/projects/<project_id>/tasks")
@debug_timing("research_project_tasks")
def research_project_tasks(project_id: str):
    try:
        service = ResearchControlPlane(get_default_store())
        return jsonify({
            "ok": True,
            "data": service.list_tasks(
                project_id=project_id,
                limit=int(request.args.get("limit") or 500),
            ),
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/projects/<project_id>/grants")
@debug_timing("research_project_grants")
def research_project_grants(project_id: str):
    try:
        service = ResearchControlPlane(get_default_store())
        return jsonify({"ok": True, "data": service.list_grants(project_id=project_id)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/projects/<project_id>/universe-ref")
@debug_timing("research_universe_ref_get")
def research_universe_ref_get(project_id: str):
    try:
        if ResearchControlPlane(get_default_store()).get_project(project_id) is None:
            return jsonify({"ok": False, "error": "Research not found"}), 404
        result = UniverseService(get_default_store()).get_research_ref(project_id)
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/research/projects/<project_id>/universe-ref")
@require_local_request
@debug_timing("research_universe_ref_set")
def research_universe_ref_set(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = UniverseService(get_default_store()).set_research_ref(
            project_id=project_id,
            universe_snapshot_id=str(payload.get("universe_snapshot_id") or ""),
            library_asset_id=str(payload.get("library_asset_id") or ""),
        )
        return jsonify({"ok": True, "data": result})
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/research/projects/<project_id>/universe-ref")
@require_local_request
@debug_timing("research_universe_ref_remove")
def research_universe_ref_remove(project_id: str):
    try:
        result = UniverseService(get_default_store()).remove_research_ref(project_id=project_id)
        return jsonify({"ok": True, "data": result})
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/run-grants")
@require_local_request
@debug_timing("research_project_run_grant")
def research_project_run_grant(project_id: str):
    """Local human approval boundary for formal Research Runs."""
    try:
        payload = request.get_json(silent=True) or {}
        control = ResearchControlPlane(get_default_store())
        allowed_providers = [
            str(item).strip().upper() for item in payload.get("allowed_providers") or [] if str(item).strip()
        ]
        allowed_instruments = [
            str(item).strip() for item in payload.get("allowed_instrument_ids") or [] if str(item).strip()
        ]
        allowed_universe_definitions = [
            str(item).strip() for item in payload.get("allowed_universe_definition_ids") or [] if str(item).strip()
        ]
        requested_snapshot = str(payload.get("universe_snapshot_id") or "").strip()
        allowed_universe_snapshots = [
            str(item).strip() for item in payload.get("allowed_universe_snapshot_ids") or [] if str(item).strip()
        ] or ([requested_snapshot] if requested_snapshot else [])
        allowed_intervals = [
            str(item).strip().lower() for item in payload.get("allowed_intervals") or [] if str(item).strip()
        ]
        if not allowed_providers:
            raise ValueError("Project Research Grant requires at least one allowed Provider")
        if not (allowed_instruments or allowed_universe_definitions or allowed_universe_snapshots):
            raise ValueError("Project Research Grant requires an explicit Instrument or Universe scope")
        instrument_parts = [item.split(":", 2) for item in allowed_instruments]
        asset_classes = sorted({parts[0].strip().lower() for parts in instrument_parts if parts and parts[0].strip()})
        venues = sorted({parts[1].strip().upper() for parts in instrument_parts if len(parts) > 1 and parts[1].strip()})
        endpoints = []
        if "BINANCE" in allowed_providers:
            endpoints.append("binance.klines")
        if "POLYMARKET" in allowed_providers:
            endpoints.append("polymarket.price_history")
        if "equity" in asset_classes and any(provider not in {"BINANCE", "POLYMARKET"} for provider in allowed_providers):
            endpoints.append("equity.price.historical")
        intent_payload = {
            "objective": str(payload.get("objective") or "formal research execution"),
            "requested_run_types": payload.get("allowed_run_types") or [],
            "requested_at": time.time(),
        }
        intent = control.create_plan(
            project_id=project_id, stage="INTENT", payload=intent_payload, created_by="local_ui_user"
        )
        plan_version = int(intent["plan_version"])
        resolved_payload = {
            **intent_payload,
            "requirement_set_id": str(payload.get("requirement_set_id") or ""),
            "universe_snapshot_id": str(payload.get("universe_snapshot_id") or ""),
            "source_policy": payload.get("source_policy") or {"mode": "FIXED"},
        }
        control.create_plan(
            project_id=project_id, stage="RESOLVED", payload=resolved_payload,
            created_by="local_ui_user", plan_version=plan_version,
        )
        result = control.approve_plan(
            project_id=project_id,
            plan_version=plan_version,
            scope={
                "grant_kind": "PROJECT_RESEARCH",
                "scope_version": "project_research_scope.v1",
                "autonomy_level": str(payload.get("autonomy_level") or "AUTONOMOUS").upper(),
                "allowed_operations": payload.get("allowed_operations") or list(DEFAULT_RESEARCH_OPERATIONS),
                "allowed_providers": allowed_providers,
                "allowed_instrument_ids": allowed_instruments,
                "allowed_universe_definition_ids": allowed_universe_definitions,
                "allowed_universe_snapshot_ids": allowed_universe_snapshots,
                "allowed_intervals": allowed_intervals,
                "asset_classes": asset_classes,
                "venues": venues,
                "symbols": sorted({
                    item.rsplit(":", 1)[-1].upper() for item in allowed_instruments if ":" in item
                }),
                "intervals": allowed_intervals,
                "endpoints": endpoints,
                "time_start": str(payload.get("time_start") or ""),
                "time_end": str(payload.get("time_end") or ""),
                "allow_project_pin": bool(payload.get("allow_project_pin", True)),
                "allow_global_library_publish": False,
                "allowed_run_types": payload.get("allowed_run_types") or [
                    "FACTOR_EVALUATION", "ALPHA_EVALUATION", "RESEARCH_BACKTEST"
                ],
                "providers": [item.lower() for item in allowed_providers],
                "requirement_set_id": resolved_payload["requirement_set_id"],
                "universe_snapshot_id": resolved_payload["universe_snapshot_id"],
            },
            budgets=payload.get("budgets") or {
                "max_backtest_runs": 10,
                "max_download_bytes": 0,
                "max_runtime_seconds": 3600,
            },
            approved_by="local_ui_user",
            actor_type="human",
            expires_at=payload.get("expires_at"),
        )
        return jsonify({"ok": True, "data": result}), 201
    except PermissionError as exc:
        return _json_error(exc, 403)
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/run-grants/<grant_id>/agent-state")
@require_local_request
@debug_timing("research_project_agent_state")
def research_project_agent_state(project_id: str, grant_id: str):
    """Human-only emergency pause/resume; Agents cannot mutate their Grant."""
    try:
        payload = request.get_json(silent=True) or {}
        control = ResearchControlPlane(get_default_store())
        grant = control.get_grant(grant_id)
        if grant is None or str(grant.get("project_id")) != project_id:
            return jsonify({"ok": False, "code": "RESEARCH_GRANT_NOT_FOUND", "error": "Grant not found"}), 404
        result = control.set_grant_agent_state(
            grant_id,
            paused=bool(payload.get("paused", True)),
            actor_type="human",
        )
        return jsonify({"ok": True, "data": result})
    except PermissionError as exc:
        return _json_error(exc, 403)
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/engine-capabilities")
@debug_timing("research_engine_capabilities")
def research_engine_capabilities():
    return jsonify({"ok": True, "data": DefinitionRegistry.engine_capabilities()})


@app.get("/api/research/definitions")
@debug_timing("research_definitions")
def research_definitions():
    try:
        service = DefinitionRegistry(get_default_store())
        data = service.list(
            definition_type=request.args.get("definition_type", ""),
            state=request.args.get("state", ""),
            limit=int(request.args.get("limit") or 200),
        )
        return jsonify({"ok": True, "data": [item.to_dict() for item in data]})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/library")
@debug_timing("research_library_list")
def research_library_list():
    try:
        component_type = str(request.args.get("component_type") or "").upper()
        store = get_default_store()
        legacy = ResearchLibraryService(store).list(component_type=component_type)
        if component_type == "REQUIREMENTS":
            data = RequirementWorkspaceService(store).list_library_assets()
        elif component_type:
            data = legacy
        else:
            data = [item for item in legacy if item.get("component_type") != "REQUIREMENTS"]
            include_requirement_status = str(
                request.args.get("include_requirement_status") or ""
            ).lower() in {"1", "true", "yes"}
            data.extend(
                RequirementWorkspaceService(store).list_library_assets(
                    include_data_status=include_requirement_status,
                )
            )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/library/<library_asset_id>")
@debug_timing("research_library_detail")
def research_library_detail(library_asset_id: str):
    try:
        store = get_default_store()
        result = RequirementWorkspaceService(store).get_library_asset(library_asset_id)
        if result is None:
            result = ResearchLibraryService(store).get(library_asset_id)
        if result is None:
            return jsonify({"ok": False, "error": "Library asset not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/library/<library_asset_id>/usage")
@debug_timing("research_library_usage")
def research_library_usage(library_asset_id: str):
    try:
        store = get_default_store()
        workspace = RequirementWorkspaceService(store)
        data = workspace.library_usage(library_asset_id) if workspace.get_library_asset(library_asset_id) else ResearchLibraryService(store).usage(library_asset_id)
        return jsonify({"ok": True, "data": data})
    except ValueError as exc:
        return _json_error(exc, 404)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/research/library/<library_asset_id>/archive")
@require_local_request
@debug_timing("research_library_archive")
def research_library_archive(library_asset_id: str):
    try:
        result = ResearchLibraryService(get_default_store()).archive(library_asset_id)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/library/groups")
@debug_timing("library_group_list")
def library_group_list():
    try:
        asset_type = str(request.args.get("asset_type") or "").upper()
        result = LibraryGroupService(get_default_store()).list_groups(asset_type)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/library/groups")
@require_local_request
@debug_timing("library_group_create")
def library_group_create():
    try:
        payload = request.get_json(silent=True) or {}
        result = LibraryGroupService(get_default_store()).create_group(
            asset_type=str(payload.get("asset_type") or ""),
            name=str(payload.get("name") or ""),
        )
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/library/groups/<group_id>")
@require_local_request
@debug_timing("library_group_rename")
def library_group_rename(group_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = LibraryGroupService(get_default_store()).rename_group(group_id, str(payload.get("name") or ""))
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/library/groups/<group_id>")
@require_local_request
@debug_timing("library_group_delete")
def library_group_delete(group_id: str):
    try:
        result = LibraryGroupService(get_default_store()).delete_group(group_id)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/library/groups/reorder")
@require_local_request
@debug_timing("library_group_reorder")
def library_group_reorder():
    try:
        payload = request.get_json(silent=True) or {}
        result = LibraryGroupService(get_default_store()).reorder_groups(
            asset_type=str(payload.get("asset_type") or ""),
            ordered_group_ids=list(payload.get("group_ids") or []),
        )
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/library/groups/move-assets")
@require_local_request
@debug_timing("library_group_move_assets")
def library_group_move_assets():
    try:
        payload = request.get_json(silent=True) or {}
        result = LibraryGroupService(get_default_store()).move_assets(
            asset_type=str(payload.get("asset_type") or ""),
            asset_ids=list(payload.get("asset_ids") or []),
            group_id=payload.get("group_id"),
        )
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/library/groups/membership")
@debug_timing("library_group_membership")
def library_group_membership():
    try:
        asset_type = str(request.args.get("asset_type") or "").upper()
        asset_ids = [item for item in str(request.args.get("asset_ids") or "").split(",") if item]
        result = LibraryGroupService(get_default_store()).membership(asset_type, asset_ids or None)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/requirements/default")
def research_requirement_default():
    return jsonify({"ok": True, "data": default_requirement_spec(request.args.get("name") or "New Requirement")})


@app.get("/api/research/projects/<project_id>/requirements/suggestion")
@debug_timing("research_requirement_suggestion")
def research_requirement_suggestion(project_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).suggest_for_universe(
            project_id, str(request.args.get("universe_id") or "")
        )
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/requirements/reconcile")
@require_local_request
@debug_timing("research_requirement_reconcile")
def research_requirement_reconcile(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = RequirementWorkspaceService(get_default_store()).reconcile_project(
            project_id, universe_id=str(payload.get("universe_id") or "")
        )
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/requirements/script/parse")
@require_local_request
def research_requirement_script_parse():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": RequirementWorkspaceService.from_script(str(payload.get("script") or ""))})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/requirements/script/render")
@require_local_request
def research_requirement_script_render():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": RequirementWorkspaceService.to_script(dict(payload.get("spec") or payload))})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/library/requirements")
@require_local_request
def research_library_requirement_create():
    try:
        result = RequirementWorkspaceService(get_default_store()).create_library_requirement(
            request.get_json(silent=True) or {}
        )
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)


@app.patch("/api/research/library/requirements/<library_asset_id>")
@require_local_request
def research_library_requirement_update(library_asset_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).update_library_requirement(
            library_asset_id, request.get_json(silent=True) or {}
        )
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/library/requirements/<library_asset_id>/save-as")
@require_local_request
def research_library_requirement_save_as(library_asset_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).save_as_library_requirement(
            library_asset_id, request.get_json(silent=True) or {}
        )
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)


@app.delete("/api/research/library/requirements/<library_asset_id>")
@require_local_request
def research_library_requirement_archive(library_asset_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).archive_library_requirement(library_asset_id)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.get("/api/research/library/requirements/drafts")
def research_library_requirement_drafts():
    return jsonify({"ok": True, "data": RequirementWorkspaceService(get_default_store()).list_library_drafts()})


@app.post("/api/research/library/requirements/drafts")
@require_local_request
def research_library_requirement_draft_create():
    try:
        payload = request.get_json(silent=True) or {}
        result = RequirementWorkspaceService(get_default_store()).create_library_draft(
            payload, base_asset_id=str(payload.get("base_library_asset_id") or ""),
        )
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)


@app.patch("/api/research/library/requirements/drafts/<draft_id>")
@require_local_request
def research_library_requirement_draft_update(draft_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).update_library_draft(draft_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/library/requirements/drafts/<draft_id>/publish")
@require_local_request
def research_library_requirement_draft_publish(draft_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).publish_library_draft(draft_id)
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)


@app.get("/api/research/projects/<project_id>/requirements/items")
def research_requirement_items(project_id: str):
    try:
        return jsonify({"ok": True, "data": RequirementWorkspaceService(get_default_store()).list_project_items(project_id, include_derived=False)})
    except ValueError as exc:
        return _json_error(exc, 404)


@app.post("/api/research/projects/<project_id>/requirements/items")
@require_local_request
def research_requirement_item_create(project_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).create_research_requirement(project_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)


@app.patch("/api/research/projects/<project_id>/requirements/items/<ref_id>")
@require_local_request
def research_requirement_item_update(project_id: str, ref_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).update_research_requirement(project_id, ref_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.delete("/api/research/projects/<project_id>/requirements/items/<ref_id>")
@require_local_request
def research_requirement_item_remove(project_id: str, ref_id: str):
    try:
        RequirementWorkspaceService(get_default_store()).remove_project_item(project_id, ref_id)
        return jsonify({"ok": True, "data": {"removed": True}})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/projects/<project_id>/requirements/items/<ref_id>/duplicate")
@require_local_request
def research_requirement_item_duplicate(project_id: str, ref_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).duplicate_project_item(project_id, ref_id)
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/projects/<project_id>/requirements/items/<ref_id>/save-as")
@require_local_request
def research_requirement_item_save_as(project_id: str, ref_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).save_as_for_project(
            project_id, ref_id, request.get_json(silent=True) or {}
        )
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/projects/<project_id>/requirements/items/<ref_id>/replace")
@require_local_request
def research_requirement_item_replace(project_id: str, ref_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = RequirementWorkspaceService(get_default_store()).replace_project_item(
            project_id, ref_id, str(payload.get("library_asset_id") or "")
        )
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/projects/<project_id>/requirements/items/<ref_id>/publish")
@require_local_request
def research_requirement_item_publish(project_id: str, ref_id: str):
    try:
        result = RequirementWorkspaceService(get_default_store()).publish_research_item(project_id, ref_id)
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/projects/<project_id>/requirements/library-items")
@require_local_request
def research_requirement_library_item_add(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = RequirementWorkspaceService(get_default_store()).add_library_to_research(project_id, str(payload.get("library_asset_id") or ""))
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)


@app.get("/api/research/projects/<project_id>/data-status")
def research_project_data_status(project_id: str):
    try:
        return jsonify({
            "ok": True,
            "data": RequirementWorkspaceService(get_default_store()).data_status(
                project_id,
                str(request.args.get("requirement_set_id") or ""),
            ),
        })
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/research/projects/<project_id>/requirements/refresh")
@require_local_request
@debug_timing("research_effective_requirements_refresh")
def research_effective_requirements_refresh(project_id: str):
    try:
        result = RequirementWorkspaceService(
            get_default_store()
        ).refresh_effective_requirements(project_id)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/requirements/publish")
@require_local_request
@debug_timing("research_requirements_publish")
def research_requirements_publish(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = ResearchLibraryService(get_default_store()).publish_requirements(
            requirement_set_id=str(payload.get("requirement_set_id") or ""),
            project_id=project_id,
            name=str(payload.get("name") or "Research Requirements"),
        )
        return jsonify({"ok": True, "data": result}), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/research/projects/<project_id>/requirements/library-ref")
@require_local_request
@debug_timing("research_requirements_library_ref")
def research_requirements_library_ref(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = ResearchLibraryService(get_default_store()).use_requirements(
            library_asset_id=str(payload.get("library_asset_id") or ""),
            project_id=project_id,
        )
        return jsonify({"ok": True, "data": result})
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/projects/<project_id>/requirements/ref")
@debug_timing("research_requirements_ref_get")
def research_requirements_ref_get(project_id: str):
    try:
        if ResearchControlPlane(get_default_store()).get_project(project_id) is None:
            return jsonify({"ok": False, "error": "Research not found"}), 404
        result = ResearchLibraryService(get_default_store()).get_requirement_ref(project_id)
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/factor-drafts")
@debug_timing("research_factor_drafts")
def research_factor_drafts():
    try:
        data = FactorDraftService(get_default_store()).list(
            owner_project_id=request.args.get("owner_project_id", ""),
            state=request.args.get("state", ""),
            limit=int(request.args.get("limit") or 200),
        )
        return jsonify({"ok": True, "data": [item.to_dict() for item in data]})
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/factor-drafts")
@require_local_request
@debug_timing("research_factor_draft_create")
def research_factor_draft_create():
    try:
        payload = request.get_json(silent=True) or {}
        service = FactorDraftService(get_default_store())
        draft = service.create(
            dict(payload.get("document") or {}),
            created_by="local_ui_user",
            owner_project_id=str(payload.get("owner_project_id") or ""),
            library_scope=str(payload.get("library_scope") or "GLOBAL"),
        )
        return jsonify({
            "ok": True,
            "data": {
                **draft.to_dict(),
                "validation": service.inspect(draft.draft_id),
            },
        }), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/factor-drafts/validation")
@require_local_request
@debug_timing("research_factor_draft_validation_preview")
def research_factor_draft_validation_preview():
    try:
        payload = request.get_json(silent=True) or {}
        service = FactorDraftService(get_default_store())
        return jsonify({
            "ok": True,
            "data": service.inspect_project_document(
                dict(payload.get("document") or {}),
                str(payload.get("owner_project_id") or ""),
            ),
        })
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/research/factor-drafts/<draft_id>")
@require_local_request
@debug_timing("research_factor_draft_update")
def research_factor_draft_update(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        service = FactorDraftService(get_default_store())
        draft = service.update(
            draft_id,
            dict(payload.get("document") or {}),
            expected_fingerprint=str(payload.get("expected_fingerprint") or ""),
        )
        return jsonify({
            "ok": True,
            "data": {
                **draft.to_dict(),
                "validation": service.inspect(draft.draft_id),
            },
        })
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/research/factor-drafts/<draft_id>")
@require_local_request
@debug_timing("research_factor_draft_discard")
def research_factor_draft_discard(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        discarded = FactorDraftService(get_default_store()).discard(
            draft_id,
            expected_fingerprint=str(payload.get("expected_fingerprint") or ""),
        )
        return jsonify({
            "ok": True,
            "data": {
                "draft_id": discarded.draft_id,
                "state": discarded.state,
                "discarded": True,
            },
        })
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/factor-drafts/<draft_id>/validation")
@debug_timing("research_factor_draft_validation")
def research_factor_draft_validation(draft_id: str):
    try:
        return jsonify({
            "ok": True,
            "data": FactorDraftService(get_default_store()).inspect(draft_id),
        })
    except ValueError as exc:
        return _json_error(exc, 404)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/factor-drafts/<draft_id>/preview-context")
@debug_timing("research_factor_preview_context")
def research_factor_preview_context(draft_id: str):
    try:
        return jsonify({
            "ok": True,
            "data": FactorPreviewService(get_default_store()).context(draft_id),
        })
    except FactorPreviewError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except ValueError as exc:
        return _json_error(exc, 404 if "not found" in str(exc).lower() else 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/factor-drafts/<draft_id>/requirements")
@require_local_request
@debug_timing("research_factor_preview_requirements")
def research_factor_preview_requirements(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        store = get_default_store()
        result = FactorPreviewService(store).compile_requirements(
            draft_id,
            payload,
        )
        project_id = str(result["reference"]["project_id"])
        result["data_status"] = RequirementWorkspaceService(store).data_status(
            project_id,
            str(result["reference"]["requirement_set_id"]),
        )
        return jsonify({"ok": True, "data": result}), 201
    except FactorPreviewError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/factor-drafts/<draft_id>/previews")
@require_local_request
@debug_timing("research_factor_preview_create")
def research_factor_preview_create(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = FactorPreviewService(get_default_store()).create(
            draft_id,
            dict(payload),
            created_by="local_ui_user",
        )
        return jsonify({"ok": True, "data": result}), 201
    except FactorPreviewError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/factor-drafts/<draft_id>/previews/latest")
@debug_timing("research_factor_preview_latest")
def research_factor_preview_latest(draft_id: str):
    try:
        return jsonify({
            "ok": True,
            "data": FactorPreviewService(get_default_store()).latest(draft_id),
        })
    except ValueError as exc:
        return _json_error(exc, 404)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/factor-previews/<preview_id>")
@debug_timing("research_factor_preview_get")
def research_factor_preview_get(preview_id: str):
    try:
        result = FactorPreviewService(get_default_store()).get(preview_id)
        if result is None:
            return jsonify({"ok": False, "error": "factor preview not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/factor-drafts/<draft_id>/validate")
@require_local_request
@debug_timing("research_factor_draft_validate")
def research_factor_draft_validate(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        store = get_default_store()
        draft, definition = FactorDraftService(store).validate(
            draft_id,
            expected_fingerprint=str(payload.get("expected_fingerprint") or ""),
            preview_id=str(payload.get("preview_id") or ""),
            preview_fingerprint=str(payload.get("preview_fingerprint") or ""),
        )
        library_asset = None
        project_reference = None
        if draft.owner_project_id and definition.library_scope == "PROJECT":
            library_asset = ResearchLibraryService(store).publish_definition(
                definition_id=definition.definition_id,
                project_id=draft.owner_project_id,
            )
            project_reference = DefinitionRegistry(store).set_project_ref(
                project_id=draft.owner_project_id,
                slot_key=f"factor:{definition.name}",
                definition_id=definition.definition_id,
                definition_version=definition.version,
                reference_mode="PINNED",
            )
            effective_requirements = RequirementWorkspaceService(
                store
            ).refresh_effective_requirements(draft.owner_project_id)
        else:
            effective_requirements = None
        return jsonify({
            "ok": True,
            "data": {
                "draft": draft.to_dict(),
                "definition": definition.to_dict(),
                "library_asset": library_asset,
                "project_reference": project_reference,
                "effective_requirements": effective_requirements,
            },
        })
    except FactorDraftValidationError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except FactorPreviewError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/projects/<project_id>/alpha-factor-candidates")
@debug_timing("research_alpha_factor_candidates")
def research_alpha_factor_candidates(project_id: str):
    try:
        return jsonify({
            "ok": True,
            "data": AlphaFactorCandidateResolver(
                get_default_store()
            ).resolve(project_id),
        })
    except ValueError as exc:
        return _json_error(
            exc,
            404 if "not found" in str(exc).lower() else 400,
        )
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/alpha-drafts")
@debug_timing("research_alpha_drafts")
def research_alpha_drafts():
    try:
        data = AlphaDraftService(get_default_store()).list(
            owner_project_id=request.args.get("owner_project_id", ""),
            state=request.args.get("state", ""),
            limit=int(request.args.get("limit") or 200),
        )
        return jsonify({
            "ok": True,
            "data": [item.to_dict() for item in data],
        })
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/alpha-drafts")
@require_local_request
@debug_timing("research_alpha_draft_create")
def research_alpha_draft_create():
    try:
        payload = request.get_json(silent=True) or {}
        service = AlphaDraftService(get_default_store())
        draft = service.create(
            dict(payload.get("document") or {}),
            owner_project_id=str(payload.get("owner_project_id") or ""),
            library_scope=str(payload.get("library_scope") or "PROJECT"),
            client_draft_key=str(payload.get("client_draft_key") or ""),
            created_by="local_ui_user",
        )
        return jsonify({
            "ok": True,
            "data": {
                **draft.to_dict(),
                "validation": service.inspect(draft.draft_id),
            },
        }), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/alpha-drafts/validation")
@require_local_request
@debug_timing("research_alpha_draft_validation_preview")
def research_alpha_draft_validation_preview():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({
            "ok": True,
            "data": AlphaDraftService(
                get_default_store()
            ).inspect_project_document(
                dict(payload.get("document") or {}),
                str(payload.get("owner_project_id") or ""),
            ),
        })
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/research/alpha-drafts/<draft_id>")
@require_local_request
@debug_timing("research_alpha_draft_update")
def research_alpha_draft_update(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        service = AlphaDraftService(get_default_store())
        draft = service.update(
            draft_id,
            dict(payload.get("document") or {}),
            expected_fingerprint=str(
                payload.get("expected_fingerprint") or ""
            ),
        )
        return jsonify({
            "ok": True,
            "data": {
                **draft.to_dict(),
                "validation": service.inspect(draft.draft_id),
            },
        })
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/research/alpha-drafts/<draft_id>")
@require_local_request
@debug_timing("research_alpha_draft_discard")
def research_alpha_draft_discard(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        draft = AlphaDraftService(get_default_store()).discard(
            draft_id,
            expected_fingerprint=str(
                payload.get("expected_fingerprint") or ""
            ),
        )
        return jsonify({
            "ok": True,
            "data": {
                "draft_id": draft.draft_id,
                "state": draft.state,
                "discarded": True,
            },
        })
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/alpha-drafts/<draft_id>/validation")
@debug_timing("research_alpha_draft_validation")
def research_alpha_draft_validation(draft_id: str):
    try:
        return jsonify({
            "ok": True,
            "data": AlphaDraftService(
                get_default_store()
            ).inspect(draft_id),
        })
    except ValueError as exc:
        return _json_error(
            exc,
            404 if "not found" in str(exc).lower() else 400,
        )
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/alpha-drafts/<draft_id>/preview-context")
@debug_timing("research_alpha_preview_context")
def research_alpha_preview_context(draft_id: str):
    try:
        return jsonify({
            "ok": True,
            "data": AlphaPreviewService(
                get_default_store()
            ).context(draft_id),
        })
    except AlphaPreviewError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except ValueError as exc:
        return _json_error(
            exc,
            404 if "not found" in str(exc).lower() else 400,
        )
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/alpha-drafts/<draft_id>/requirements")
@require_local_request
@debug_timing("research_alpha_preview_requirements")
def research_alpha_preview_requirements(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        store = get_default_store()
        result = AlphaPreviewService(store).compile_requirements(
            draft_id,
            payload,
        )
        result["data_status"] = RequirementWorkspaceService(
            store
        ).data_status(
            str(result["reference"]["project_id"]),
            str(result["reference"]["requirement_set_id"]),
        )
        return jsonify({"ok": True, "data": result}), 201
    except AlphaPreviewError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/alpha-drafts/<draft_id>/previews")
@require_local_request
@debug_timing("research_alpha_preview_create")
def research_alpha_preview_create(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = AlphaPreviewService(get_default_store()).create(
            draft_id,
            dict(payload),
            created_by="local_ui_user",
        )
        return jsonify({"ok": True, "data": result}), 201
    except AlphaPreviewError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/alpha-drafts/<draft_id>/previews/latest")
@debug_timing("research_alpha_preview_latest")
def research_alpha_preview_latest(draft_id: str):
    try:
        return jsonify({
            "ok": True,
            "data": AlphaPreviewService(
                get_default_store()
            ).latest(draft_id),
        })
    except ValueError as exc:
        return _json_error(exc, 404)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/alpha-previews/<preview_id>")
@debug_timing("research_alpha_preview_get")
def research_alpha_preview_get(preview_id: str):
    try:
        result = AlphaPreviewService(
            get_default_store()
        ).get(preview_id)
        if result is None:
            return jsonify({
                "ok": False,
                "error": "alpha preview not found",
            }), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/alpha-drafts/<draft_id>/validate")
@require_local_request
@debug_timing("research_alpha_draft_validate")
def research_alpha_draft_validate(draft_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        store = get_default_store()
        draft, definition = AlphaDraftService(store).validate(
            draft_id,
            expected_fingerprint=str(
                payload.get("expected_fingerprint") or ""
            ),
            preview_id=str(payload.get("preview_id") or ""),
            preview_fingerprint=str(
                payload.get("preview_fingerprint") or ""
            ),
        )
        library_asset = ResearchLibraryService(
            store
        ).publish_definition(
            definition_id=definition.definition_id,
            project_id=draft.owner_project_id,
        )
        project_reference = DefinitionRegistry(
            store
        ).set_project_ref(
            project_id=draft.owner_project_id,
            slot_key=f"alpha:{definition.name}",
            definition_id=definition.definition_id,
            definition_version=definition.version,
            reference_mode="PINNED",
            library_asset_id=library_asset["library_asset_id"],
        )
        effective_requirements = RequirementWorkspaceService(
            store
        ).refresh_effective_requirements(draft.owner_project_id)
        return jsonify({
            "ok": True,
            "data": {
                "draft": draft.to_dict(),
                "definition": definition.to_dict(),
                "library_asset": library_asset,
                "project_reference": project_reference,
                "effective_requirements": effective_requirements,
            },
        })
    except AlphaDraftValidationError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except AlphaPreviewError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
            "diagnostics": exc.diagnostics,
        }), 400
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/definitions")
@require_local_request
@debug_timing("research_definition_create")
def research_definition_create():
    try:
        payload = request.get_json(silent=True) or {}
        result = DefinitionRegistry(get_default_store()).create(
            str(payload.get("definition_type") or ""),
            dict(payload.get("spec") or {}),
            state=str(payload.get("state") or "DRAFT"),
            created_by="local_ui_user",
            owner_project_id=str(payload.get("owner_project_id") or ""),
            library_scope=str(payload.get("library_scope") or "GLOBAL"),
        )
        return jsonify({"ok": True, "data": result.to_dict()}), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/definitions/<definition_id>/validate")
@require_local_request
@debug_timing("research_definition_validate")
def research_definition_validate(definition_id: str):
    try:
        result = DefinitionRegistry(get_default_store()).validate(definition_id)
        return jsonify({"ok": True, "data": result.to_dict()})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/definitions/<definition_id>/publish")
@require_local_request
@debug_timing("research_definition_publish")
def research_definition_publish(definition_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = ResearchLibraryService(get_default_store()).publish_definition(
            definition_id=definition_id,
            project_id=str(payload.get("project_id") or ""),
        )
        return jsonify({"ok": True, "data": result}), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/factors/sync-library")
@require_local_request
@debug_timing("research_project_factors_sync_library")
def research_project_factors_sync_library(project_id: str):
    try:
        assets = ResearchLibraryService(get_default_store()).ensure_project_factors(
            project_id=project_id,
        )
        return jsonify({"ok": True, "data": assets})
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/definitions/<definition_id>/impact")
@debug_timing("research_definition_impact")
def research_definition_impact(definition_id: str):
    try:
        return jsonify({"ok": True, "data": DefinitionRegistry(get_default_store()).impact(definition_id)})
    except ValueError as exc:
        return _json_error(exc, 404)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/projects/<project_id>/definition-refs")
@debug_timing("research_project_definition_refs")
def research_project_definition_refs(project_id: str):
    try:
        return jsonify({
            "ok": True,
            "data": DefinitionRegistry(get_default_store()).list_project_refs(project_id),
        })
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/research/projects/<project_id>/definition-refs/<slot_key>")
@require_local_request
@debug_timing("research_project_definition_ref_set")
def research_project_definition_ref_set(project_id: str, slot_key: str):
    try:
        payload = request.get_json(silent=True) or {}
        store = get_default_store()
        result = DefinitionRegistry(store).set_project_ref(
            project_id=project_id,
            slot_key=slot_key,
            definition_id=str(payload.get("definition_id") or ""),
            definition_version=str(payload.get("definition_version") or ""),
            reference_mode=str(payload.get("reference_mode") or "PINNED"),
            library_asset_id=str(payload.get("library_asset_id") or ""),
        )
        effective = RequirementWorkspaceService(
            store
        ).refresh_effective_requirements(project_id)
        return jsonify({
            "ok": True,
            "data": result,
            "effective_requirements": effective,
        })
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/research/projects/<project_id>/definition-refs/<slot_key>")
@require_local_request
@debug_timing("research_project_definition_ref_remove")
def research_project_definition_ref_remove(project_id: str, slot_key: str):
    try:
        payload = request.get_json(silent=True) or {}
        store = get_default_store()
        result = DefinitionRegistry(store).remove_project_ref(
            project_id=project_id,
            slot_key=slot_key,
            expected_definition_id=str(payload.get("expected_definition_id") or ""),
        )
        effective = RequirementWorkspaceService(
            store
        ).refresh_effective_requirements(project_id)
        return jsonify({
            "ok": True,
            "data": result,
            "effective_requirements": effective,
        })
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/manifest-resolver/resolve")
@require_local_request
@debug_timing("research_manifest_resolve")
def research_manifest_resolve():
    try:
        payload = request.get_json(silent=True) or {}
        result = DeterministicManifestResolver(get_default_store()).resolve(
            str(payload.get("requirement_set_id") or ""),
            source_selection_policy=payload.get("source_selection_policy") or {},
            verify_physical=bool(payload.get("verify_physical", True)),
        )
        return jsonify({"ok": True, "data": result.to_dict()})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/run-input-previews")
@debug_timing("research_run_previews")
def research_run_previews():
    try:
        data = ResearchRunPreviewService(get_default_store()).list(
            project_id=request.args.get("project_id", ""),
            limit=int(request.args.get("limit") or 100),
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/projects/<project_id>/run-input-previews")
@require_local_request
@debug_timing("research_run_preview_create")
def research_run_preview_create(project_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        result = ResearchRunPreviewService(get_default_store()).create(
            project_id, payload, created_by="local_ui_user"
        )
        return jsonify({"ok": True, "data": result}), 201
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/run-input-previews/<preview_id>")
@debug_timing("research_run_preview")
def research_run_preview(preview_id: str):
    try:
        result = ResearchRunPreviewService(get_default_store()).get(preview_id)
        if result is None:
            return jsonify({"ok": False, "code": "PREVIEW_NOT_FOUND", "error": "Preview not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/runs")
@debug_timing("research_runs_v2")
def research_runs_v2():
    try:
        data = ResearchRunService(get_default_store()).list(
            project_id=request.args.get("project_id", ""),
            run_type=request.args.get("run_type", ""),
            status=request.args.get("status", ""),
            limit=int(request.args.get("limit") or 200),
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/runs")
@require_local_request
@debug_timing("research_run_create_v2")
def research_run_create_v2():
    try:
        payload = request.get_json(silent=True) or {}
        result = ResearchRunService(get_default_store()).create(
            preview_id=str(payload.get("preview_id") or ""),
            preview_fingerprint=str(payload.get("preview_fingerprint") or ""),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            actor_id="local_ui_user",
            actor_type="HUMAN",
        )
        return jsonify({"ok": True, "data": result}), 201
    except IdempotencyConflictError as exc:
        return jsonify({"ok": False, "code": exc.code, "error": str(exc)}), 409
    except PreviewStaleError as exc:
        return jsonify({"ok": False, "code": exc.code, "error": str(exc)}), 409
    except ReadinessBlockedError as exc:
        return jsonify({"ok": False, "code": exc.code, "error": str(exc)}), 422
    except PermissionError as exc:
        code = str(exc).split(":", 1)[0]
        return jsonify({"ok": False, "code": code, "error": str(exc)}), 403
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/runs/<run_id>")
@debug_timing("research_run_v2")
def research_run_v2(run_id: str):
    try:
        result = ResearchRunService(get_default_store()).get(run_id)
        if result is None:
            return jsonify({"ok": False, "error": "Research Run not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/runs/<run_id>/result-summary")
@debug_timing("research_run_result_summary")
def research_run_result_summary(run_id: str):
    """Return a readable result summary without mutating immutable Run output."""
    try:
        store = get_default_store()
        result = ResearchRunService(store).get(run_id)
        if result is None:
            return jsonify({"ok": False, "error": "Research Run not found"}), 404

        produced_artifact_ids = {
            str(artifact_id)
            for key, values in (result.get("output") or {}).items()
            if key.startswith("produced_") and key.endswith("_artifact_ids")
            for artifact_id in (values or [])
            if str(artifact_id or "")
        }
        artifacts = [
            artifact
            for artifact in ArtifactService(store).list(limit=1000)
            if artifact.created_by_run_id == run_id
            or artifact.artifact_id in produced_artifact_ids
        ]
        artifact_items = [
            {
                "artifact_id": artifact.artifact_id,
                "artifact_type": artifact.artifact_type,
                "logical_name": artifact.logical_name,
                "version": artifact.version,
                "status": artifact.status,
                "schema_version": artifact.schema_version,
                "engine_version": artifact.engine_version,
                "row_count": int(artifact.metadata.get("row_count") or 0),
                "metadata": artifact.metadata,
            }
            for artifact in artifacts
        ]
        factor_signals = []
        for artifact in artifacts:
            if artifact.artifact_type != "FACTOR_VALUES":
                continue
            try:
                path = _resolve_artifact_content_path(artifact.content_uri)
            except ValueError:
                factor_signals.append({
                    "artifact_id": artifact.artifact_id,
                    "factor_name": artifact.logical_name,
                    "error": "Artifact content is outside the trusted DataTube storage roots",
                })
                continue
            try:
                import pyarrow.parquet as pq

                parquet = pq.ParquetFile(path)
                available = set(parquet.schema.names)
                columns = [
                    name for name in ("event_time", "available_time", "value")
                    if name in available
                ]
                rows = parquet.read(columns=columns).to_pylist()
                positive = negative = zero = missing = 0
                events = []
                for row in rows:
                    value = row.get("value")
                    if value is None:
                        missing += 1
                        continue
                    number = float(value)
                    if number > 0:
                        positive += 1
                    elif number < 0:
                        negative += 1
                    else:
                        zero += 1
                    if number:
                        events.append({
                            "event_time": row.get("event_time"),
                            "available_time": row.get("available_time"),
                            "signal": "GOLDEN_CROSS" if number > 0 else "DEATH_CROSS",
                            "value": number,
                        })
                factor_signals.append({
                    "artifact_id": artifact.artifact_id,
                    "factor_name": artifact.logical_name,
                    "operator": str(artifact.metadata.get("operator") or ""),
                    "total_rows": len(rows),
                    "positive_count": positive,
                    "negative_count": negative,
                    "zero_count": zero,
                    "missing_count": missing,
                    "event_count": positive + negative,
                    "latest_events": events[-12:],
                })
            except Exception as exc:
                factor_signals.append({
                    "artifact_id": artifact.artifact_id,
                    "factor_name": artifact.logical_name,
                    "error": str(exc),
                })

        output = dict(result.get("output") or {})
        bundle = ResearchRunService(store).get_bundle(str(result.get("bundle_id") or ""))
        frozen = dict((bundle or {}).get("canonical_payload") or {})
        closure = dict(frozen.get("input_closure") or {})
        execution_specs = dict(frozen.get("execution_specs") or {})
        registry = DefinitionRegistry(store)
        factor_definitions = []
        for ref in closure.get("factor_definitions") or []:
            definition = registry.get(
                str(ref.get("factor_definition_id") or ""),
                version=str(ref.get("version") or ""),
            )
            factor_definitions.append(definition.to_dict() if definition else dict(ref))
        alpha_definitions = []
        for ref in closure.get("alpha_definitions") or []:
            definition = registry.get(
                str(ref.get("alpha_definition_id") or ""),
                version=str(ref.get("version") or ""),
            )
            alpha_definitions.append(definition.to_dict() if definition else dict(ref))
        snapshot = UniverseService(store).get_snapshot(str(closure.get("universe_snapshot_id") or ""))
        universe = asdict(snapshot) if snapshot else {
            "universe_snapshot_id": closure.get("universe_snapshot_id"),
            "instrument_ids": closure.get("resolved_instrument_ids") or [],
        }
        product_run_type = {
            "FACTOR_EVALUATION": "FACTOR_RUN",
            "ALPHA_EVALUATION": "ALPHA_RUN",
            "RESEARCH_BACKTEST": "RESEARCH_BACKTEST",
        }.get(str(result.get("run_type") or ""), str(result.get("run_type") or ""))
        artifact_ids_by_type: dict[str, list[str]] = {}
        for item in artifact_items:
            artifact_ids_by_type.setdefault(item["artifact_type"], []).append(item["artifact_id"])
        factor_run = (
            FactorRunResultService(store).build(result)
            if product_run_type == "FACTOR_RUN"
            else None
        )
        alpha_run = (
            AlphaRunResultService(store).build(result)
            if product_run_type == "ALPHA_RUN"
            else None
        )
        research_backtest = (
            ResearchBacktestResultService(store).build(result)
            if product_run_type == "RESEARCH_BACKTEST"
            else None
        )
        legacy_hybrid = bool((alpha_run or {}).get("legacy_hybrid"))
        if product_run_type == "FACTOR_RUN":
            section_specs = (
                ("overview", "Overview", []),
                ("factor_definition", "Factor Definition", []),
                ("universe", "Universe", []),
                ("data_inputs", "Data Inputs", []),
                ("factor_output", "Factor Output", ["FACTOR_VALUES"]),
                ("coverage", "Coverage", ["FACTOR_EVALUATION"]),
                ("distribution", "Distribution", ["FACTOR_EVALUATION"]),
                ("ic_rank_ic", "IC / Rank IC", ["FACTOR_EVALUATION"]),
                ("quantile_return", "Quantile Return", ["FACTOR_EVALUATION"]),
                ("diagnostics", "Diagnostics", ["FACTOR_EVALUATION"]),
                ("logs", "Logs", []),
            )
        elif product_run_type == "ALPHA_RUN" and not legacy_hybrid:
            section_specs = (
                ("overview", "Overview", []),
                ("alpha_definition", "Alpha Definition", []),
                ("factor_inputs", "Factor Inputs", []),
                ("universe", "Universe", []),
                ("signal_rules", "Signal Rules", []),
                ("signals", "Signals", ["ALPHA_VALUES"]),
                ("ic_accuracy", "IC & Accuracy", ["ALPHA_EVALUATION"]),
                ("decay", "Decay", ["ALPHA_EVALUATION"]),
                ("turnover", "Turnover", ["ALPHA_EVALUATION"]),
                ("regime_analysis", "Regime Analysis", ["ALPHA_EVALUATION"]),
                ("diagnostics", "Diagnostics", ["ALPHA_EVALUATION"]),
                ("logs", "Logs", []),
            )
        elif product_run_type == "ALPHA_RUN" and legacy_hybrid:
            section_specs = (
                ("overview", "Overview", []),
                ("alpha_definition", "Alpha Definition", []),
                ("factor_inputs", "Factor Inputs", []),
                ("universe", "Universe", []),
                ("signal_rules", "Signal Rules", []),
                ("portfolio_rules", "Portfolio Rules", []),
                ("execution_assumptions", "Execution Assumptions", []),
                ("signals", "Signals", ["ALPHA_VALUES"]),
                ("positions", "Positions", ["POSITION_SERIES"]),
                ("trades", "Trades", ["BACKTEST_ORDERS"]),
                ("equity_curve", "Equity Curve", ["EQUITY_SERIES"]),
                ("performance_metrics", "Performance Metrics", ["BACKTEST_RESULT"]),
                ("drawdown", "Drawdown", ["DRAWDOWN_SERIES"]),
                ("diagnostics", "Diagnostics", ["ALPHA_EVALUATION"]),
                ("logs", "Logs", []),
            )
        elif product_run_type == "RESEARCH_BACKTEST":
            section_specs = (
                ("overview", "Overview", []),
                ("alpha_definition", "Alpha Lineage", []),
                ("factor_inputs", "Factor Inputs", []),
                ("universe", "Universe", []),
                ("portfolio_rules", "Portfolio Rules", []),
                ("execution_assumptions", "Execution Assumptions", []),
                ("benchmark", "Benchmark", []),
                ("signals", "Signals", ["ALPHA_VALUES"]),
                ("portfolio_targets", "Portfolio Targets", ["PORTFOLIO_TARGETS"]),
                ("positions", "Positions", ["POSITION_SERIES"]),
                ("trades", "Trades", ["BACKTEST_ORDERS"]),
                ("equity_curve", "Equity Curve", ["EQUITY_SERIES"]),
                ("performance_metrics", "Performance Metrics", ["BACKTEST_RESULT"]),
                ("drawdown", "Drawdown", ["DRAWDOWN_SERIES"]),
                ("diagnostics", "Diagnostics", []),
                ("logs", "Logs", []),
            )
        else:
            section_specs = (("overview", "Overview", []), ("logs", "Logs", []))
        sections = [
            {
                "key": key,
                "label": label,
                "artifact_ids": [
                    artifact_id
                    for artifact_type in artifact_types
                    for artifact_id in artifact_ids_by_type.get(artifact_type, [])
                ],
            }
            for key, label, artifact_types in section_specs
        ]
        effective_product_run_type = (
            "LEGACY_HYBRID_RUN" if legacy_hybrid else product_run_type
        )
        result_contract = factor_run or alpha_run or research_backtest or {}
        default_schema_versions = {
            "FACTOR_RUN": FACTOR_RUN_RESULT_SCHEMA_VERSION,
            "ALPHA_RUN": ALPHA_RUN_RESULT_SCHEMA_VERSION,
            "RESEARCH_BACKTEST": RESEARCH_BACKTEST_RESULT_SCHEMA_VERSION,
        }
        result_schema_version = str(
            result_contract.get("schema_version")
            or default_schema_versions.get(product_run_type, "research-run-result.v1")
        )
        return jsonify({
            "ok": True,
            "data": {
                "run_id": result["run_id"],
                "project_id": result["project_id"],
                "run_type": result["run_type"],
                "product_run_type": effective_product_run_type,
                "result_schema_version": result_schema_version,
                "status": result["status"],
                "bundle_id": result.get("bundle_id"),
                "started_at": result.get("started_at"),
                "finished_at": result.get("finished_at"),
                "metrics": output.get("metrics") or {},
                "run_contract": {
                    "factor_definitions": factor_definitions,
                    "alpha_definitions": alpha_definitions,
                    "factor_inputs": factor_definitions,
                    "universe": universe,
                    "data_inputs": frozen.get("manifest_descriptors") or [],
                    "evaluation_spec": execution_specs.get("evaluation_spec") or {},
                    "signal_rules": (alpha_definitions[0].get("spec") if alpha_definitions else {}) or {},
                    "portfolio_rules": execution_specs.get("portfolio_spec") or {},
                    "execution_assumptions": execution_specs.get("execution_spec") or {},
                },
                "sections": sections,
                "logs": {
                    "created_at": result.get("created_at"),
                    "queued_at": result.get("queued_at"),
                    "started_at": result.get("started_at"),
                    "finished_at": result.get("finished_at"),
                    "attempt_count": result.get("attempt_count"),
                    "error": result.get("error") or {},
                },
                "factor_signals": factor_signals,
                "factor_run": factor_run,
                "alpha_run": alpha_run,
                "research_backtest": research_backtest,
                "artifacts": artifact_items,
            },
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/runs/<run_id>/sections/<section_key>")
@debug_timing("research_run_result_section")
def research_run_result_section(run_id: str, section_key: str):
    """Read one bounded, immutable Factor, Alpha, or Research Backtest section."""
    try:
        store = get_default_store()
        run = ResearchRunService(store).get(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "Research Run not found"}), 404
        key = str(section_key or "").strip().lower()
        if run["run_type"] == "FACTOR_EVALUATION" and key in FACTOR_RUN_STRUCTURED_SECTIONS:
            data = FactorRunResultService(store).section(run, key)
            offset = max(0, int(request.args.get("offset", 0)))
            limit = max(1, min(int(request.args.get("limit", 200)), 500))
            rows = list(data.get("rows") or [])
            data["rows"] = rows[offset:offset + limit]
            data["offset"] = offset
            data["limit"] = limit
            return jsonify({"ok": True, "data": data})
        alpha_section = (
            AlphaRunResultService(store).section(run, key)
            if run["run_type"] == "ALPHA_EVALUATION" and key in ALPHA_RUN_STRUCTURED_SECTIONS
            else None
        )
        research_backtest_section = (
            ResearchBacktestResultService(store).section(run, key)
            if run["run_type"] == "RESEARCH_BACKTEST" and key in RESEARCH_BACKTEST_STRUCTURED_SECTIONS
            else None
        )
        artifact_type_by_section = {
            "factor_output": "FACTOR_VALUES",
            "factor_inputs": "FACTOR_VALUES",
            "coverage": "FACTOR_EVALUATION",
            "distribution": "FACTOR_EVALUATION",
            "ic_rank_ic": "FACTOR_EVALUATION",
            "quantile_return": "FACTOR_EVALUATION",
            "signals": "ALPHA_VALUES",
            "ic_accuracy": "ALPHA_EVALUATION",
            "decay": "ALPHA_EVALUATION",
            "turnover": "ALPHA_EVALUATION",
            "regime_analysis": "ALPHA_EVALUATION",
            "portfolio_targets": "PORTFOLIO_TARGETS",
            "positions": "POSITION_SERIES",
            "portfolio_rules": "PORTFOLIO_TARGETS",
            "trades": "BACKTEST_ORDERS",
            "equity_curve": "EQUITY_SERIES",
            "performance_metrics": "BACKTEST_RESULT",
            "drawdown": "DRAWDOWN_SERIES",
            "diagnostics": (
                "FACTOR_EVALUATION"
                if run["run_type"] == "FACTOR_EVALUATION"
                else "ALPHA_EVALUATION"
            ),
        }
        if key == "logs":
            return jsonify({"ok": True, "data": {
                "section": key,
                "rows": [{
                    "status": run.get("status"),
                    "created_at": run.get("created_at"),
                    "queued_at": run.get("queued_at"),
                    "started_at": run.get("started_at"),
                    "finished_at": run.get("finished_at"),
                    "attempt_count": run.get("attempt_count"),
                    "error": run.get("error") or {},
                }],
                "total_rows": 1,
            }})
        artifact_type = artifact_type_by_section.get(key)
        if not artifact_type:
            return jsonify({"ok": False, "error": "Run section is inline or unsupported"}), 404
        offset = max(0, int(request.args.get("offset", 0)))
        limit = max(1, min(int(request.args.get("limit", 200)), 500))
        produced_artifact_ids = {
            str(artifact_id)
            for key, values in (run.get("output") or {}).items()
            if key.startswith("produced_") and key.endswith("_artifact_ids")
            for artifact_id in (values or [])
            if str(artifact_id or "")
        }
        artifacts = [
            artifact
            for artifact in ArtifactService(store).list(artifact_type=artifact_type, limit=1000)
            if artifact.created_by_run_id == run_id
            or artifact.artifact_id in produced_artifact_ids
        ]
        rows: list[dict[str, Any]] = []
        for artifact in artifacts:
            path = _resolve_artifact_content_path(artifact.content_uri)
            import pyarrow.parquet as pq

            for row in pq.read_table(path).to_pylist():
                item = dict(row)
                if "payload_json" in item:
                    item = json.loads(item["payload_json"] or "{}")
                    record_type = str(row.get("record_type") or "")
                    if key in {"coverage", "distribution", "diagnostics"} and record_type != "SUMMARY":
                        continue
                    if key == "ic_rank_ic" and record_type not in {"SUMMARY", "IC"}:
                        continue
                    if key == "quantile_return" and record_type not in {"SUMMARY", "GROUP_RETURN"}:
                        continue
                    item = {"record_type": record_type, **item}
                else:
                    for field, value in list(item.items()):
                        if field.endswith("_json") and isinstance(value, str):
                            try:
                                item[field.removesuffix("_json")] = json.loads(value)
                                item.pop(field)
                            except json.JSONDecodeError:
                                pass
                item["_artifact_id"] = artifact.artifact_id
                rows.append(item)
        if key in {"coverage", "distribution", "diagnostics"}:
            selected = []
            for row in rows:
                if key == "coverage":
                    value = {
                        "coverage": row.get("coverage"),
                        "coverage_by_instrument": row.get("coverage_by_instrument"),
                        "valid_rows": row.get("valid_rows"),
                        "total_rows": row.get("total_rows"),
                    }
                elif key == "distribution":
                    value = {
                        "mean": row.get("mean"),
                        "std": row.get("std"),
                        "quantiles": row.get("quantiles"),
                        "missing_rate": row.get("missing_rate"),
                        "outlier_ratio_5sigma": row.get("outlier_ratio_5sigma"),
                    }
                else:
                    value = row.get("diagnostics") or []
                selected.append({
                    "_artifact_id": row.get("_artifact_id"),
                    key: value,
                })
            rows = selected
        total_rows = len(rows)
        data = {
            "section": key,
            "artifact_type": artifact_type,
            "rows": rows[offset:offset + limit],
            "total_rows": total_rows,
            "offset": offset,
            "limit": limit,
        }
        structured_section = alpha_section or research_backtest_section
        if structured_section is not None:
            data = {**structured_section, **data}
            if key in {"equity_curve", "drawdown"}:
                if len(rows) <= 300:
                    data["series"] = rows
                else:
                    last_index = len(rows) - 1
                    sample_indexes = sorted({
                        round(index * last_index / 299)
                        for index in range(300)
                    })
                    data["series"] = [rows[index] for index in sample_indexes]
        return jsonify({"ok": True, "data": data})
    except (TypeError, ValueError) as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/frozen-bundles/<bundle_id>")
@debug_timing("research_frozen_bundle")
def research_frozen_bundle(bundle_id: str):
    try:
        result = ResearchRunService(get_default_store()).get_bundle(
            bundle_id, check_current_authorization=request.args.get("check_current_authorization", "0") == "1"
        )
        if result is None:
            return jsonify({"ok": False, "error": "Frozen Bundle not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/frozen-bundles/<bundle_id>/verify")
@require_local_request
@debug_timing("research_frozen_bundle_verify")
def research_frozen_bundle_verify(bundle_id: str):
    try:
        return jsonify({"ok": True, "data": ResearchRunService(get_default_store()).verify_bundle(bundle_id)})
    except ValueError as exc:
        return _json_error(exc, 422)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/run-worker/claim")
@require_local_request
@debug_timing("research_run_worker_claim")
def research_run_worker_claim():
    try:
        payload = request.get_json(silent=True) or {}
        result = ResearchRunWorker(
            get_default_store(), worker_id=str(payload.get("worker_id") or "formal-research-worker")
        ).claim(lease_seconds=int(payload.get("lease_seconds") or 300))
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/run-worker/run-once")
@require_local_request
@debug_timing("research_run_worker_once")
def research_run_worker_once():
    try:
        dispatched = _dispatch_research_run_once()
        queued = ResearchRunService(get_default_store()).list(
            status="QUEUED", limit=100
        )
        result = {
            "status": "DISPATCHED" if dispatched else ("QUEUED" if queued else "IDLE"),
            "dispatched": dispatched,
            "queue_depth": len(queued),
        }
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/system/latency")
@debug_timing("system_latency")
def system_latency():
    try:
        settings = load_web_settings()
        sqlite_targets = [
            (
                "market_data_db",
                "行情 SQLite",
                _resolve_data_path(settings.get("sqlite_db_path"), "Data/market_data.db"),
            ),
            (
                "market_realtime_db",
                "实时市场 SQLite",
                _resolve_data_path(settings.get("market_realtime_db_path"), "Data/polymarket_realtime.db"),
            ),
            (
                "order_list_db",
                "订单 SQLite",
                _resolve_data_path(settings.get("order_list_db_path"), "Data/PolyMarketOrderList.db"),
            ),
            (
                "strategy_monitoring_db",
                "策略监控 SQLite",
                _resolve_data_path(settings.get("strategy_monitoring_db_path"), "Data/PolyMarketMonitoring.db"),
            ),
            (
                "polymarket_dictionary_db",
                "Polymarket Dictionary SQLite",
                _resolve_data_path(settings.get("polymarket_dictionary_db_path"), "Data/PolyMarketDictionary.db"),
            ),
        ]

        external: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(8, len(EXTERNAL_LATENCY_TARGETS))) as executor:
            futures = [executor.submit(_check_http_latency, target) for target in EXTERNAL_LATENCY_TARGETS]
            for future in as_completed(futures):
                external.append(future.result())
        external.sort(key=lambda item: item.get("key", ""))

        openbb_health = OpenBBProviderService(settings).health()
        external.append({
            "key": "openbb",
            "label": "OpenBB Data Provider",
            "url": openbb_health.get("base_url"),
            "group": "finance",
            "ok": bool(openbb_health.get("ok")),
            "status": "good" if openbb_health.get("ok") else ("disabled" if not openbb_health.get("enabled") else "error"),
            "latency_ms": openbb_health.get("latency_ms"),
            "http_status": openbb_health.get("http_status"),
            "error": openbb_health.get("error"),
            "enabled": openbb_health.get("enabled"),
        })
        external.sort(key=lambda item: item.get("key", ""))

        sqlite_items = [_check_sqlite_latency(key, label, path) for key, label, path in sqlite_targets]
        groups = {
            "polymarket": _group_latency_status([item for item in external if item.get("group") == "polymarket"]),
            "crypto": _group_latency_status([item for item in external if item.get("group") == "crypto"]),
            "finance": _group_latency_status([item for item in external if item.get("group") == "finance"]),
            "sqlite": _group_latency_status(sqlite_items),
        }
        return jsonify(
            {
                "ok": True,
                "data": {
                    "explanation": "latency 是服务器连接数据源并完成握手花费的时间，单位 ms；超时或失败表示当前不可用。",
                    "external": external,
                    "sqlite": sqlite_items,
                    "groups": groups,
                    "timeout_ms": 2500,
                },
            }
        )
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/settings")
@require_local_request
def get_settings():
    try:
        return jsonify({"ok": True, "data": load_public_web_settings()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/equity/crsp/imports")
@debug_timing("research_crsp_imports")
def research_crsp_imports():
    try:
        return jsonify({
            "ok": True,
            "data": CrspBulkImportService(get_default_store()).list(
                limit=int(request.args.get("limit") or 20)
            ),
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/equity/crsp/imports/<job_id>")
@debug_timing("research_crsp_import")
def research_crsp_import(job_id: str):
    try:
        job = CrspBulkImportService(get_default_store()).get(job_id)
        if job is None:
            return jsonify({"ok": False, "error": "CRSP import job not found"}), 404
        return jsonify({"ok": True, "data": job})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/equity/crsp/imports")
@require_local_request
@debug_timing("research_crsp_import_create")
def research_crsp_import_create():
    try:
        payload = request.get_json(silent=True) or {}
        source_path = str(payload.get("source_path") or "").strip()
        if not source_path:
            raise ValueError("source_path is required")
        service = CrspBulkImportService(get_default_store())
        job = service.create(
            source_path=source_path,
            source_entry=str(payload.get("source_entry") or ""),
            dataset_prefix=str(payload.get("dataset_prefix") or "crsp:ciz:full"),
            idempotency_key=str(payload.get("idempotency_key") or ""),
        )
        worker = {"started": False}
        if job["status"] in {"QUEUED", "FAILED"}:
            if job["status"] == "FAILED":
                job = service.resume(job["job_id"])
            worker = _spawn_crsp_import_worker(
                job["job_id"], chunk_rows=int(payload.get("chunk_rows") or 250_000)
            )
        return jsonify({"ok": True, "data": {"job": job, "worker": worker}}), 202
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/equity/crsp/imports/<job_id>/resume")
@require_local_request
@debug_timing("research_crsp_import_resume")
def research_crsp_import_resume(job_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        job = CrspBulkImportService(get_default_store()).resume(job_id)
        worker = {"started": False} if job["status"] == "READY" else _spawn_crsp_import_worker(
            job_id, chunk_rows=int(payload.get("chunk_rows") or 250_000)
        )
        return jsonify({"ok": True, "data": {"job": job, "worker": worker}}), 202
    except ValueError as exc:
        return _json_error(exc, 404)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/equity/sec/imports")
@debug_timing("research_sec_imports")
def research_sec_imports():
    try:
        return jsonify({
            "ok": True,
            "data": SecBulkImportService(get_default_store()).list(
                limit=int(request.args.get("limit") or 20)
            ),
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/equity/sec/imports/<job_id>")
@debug_timing("research_sec_import")
def research_sec_import(job_id: str):
    try:
        job = SecBulkImportService(get_default_store()).get(job_id)
        if job is None:
            return jsonify({"ok": False, "error": "SEC bulk import job not found"}), 404
        return jsonify({"ok": True, "data": job})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/equity/sec/imports")
@require_local_request
@debug_timing("research_sec_import_create")
def research_sec_import_create():
    try:
        payload = request.get_json(silent=True) or {}
        service = SecBulkImportService(get_default_store())
        job = service.create(
            dataset_id=str(payload.get("dataset_id") or "sec:edgar:fundamentals_pit"),
            idempotency_key=str(payload.get("idempotency_key") or ""),
        )
        worker = {"started": False}
        if job["status"] in {"QUEUED", "FAILED"}:
            if job["status"] == "FAILED":
                job = service.resume(job["job_id"])
            worker = _spawn_sec_import_worker(
                job["job_id"], target_rows=int(payload.get("target_rows") or 250_000)
            )
        return jsonify({"ok": True, "data": {"job": job, "worker": worker}}), 202
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/equity/sec/imports/<job_id>/resume")
@require_local_request
@debug_timing("research_sec_import_resume")
def research_sec_import_resume(job_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        job = SecBulkImportService(get_default_store()).resume(job_id)
        worker = {"started": False} if job["status"] == "READY" else _spawn_sec_import_worker(
            job_id, target_rows=int(payload.get("target_rows") or 250_000)
        )
        return jsonify({"ok": True, "data": {"job": job, "worker": worker}}), 202
    except ValueError as exc:
        return _json_error(exc, 404)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/equity/sec/imports/<job_id>/reuse-sources")
@require_local_request
@debug_timing("research_sec_import_reuse_sources")
def research_sec_import_reuse_sources(job_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        source_job_id = str(payload.get("source_job_id") or "").strip()
        if not source_job_id:
            raise ValueError("source_job_id is required")
        service = SecBulkImportService(get_default_store())
        service.cancel_for_recovery(job_id)
        job = service.reuse_verified_sources(job_id, source_job_id=source_job_id)
        worker = _spawn_sec_import_worker(
            job_id, target_rows=int(payload.get("target_rows") or 250_000)
        )
        return jsonify({"ok": True, "data": {"job": job, "worker": worker}}), 202
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/equity/sec/imports/<job_id>/cancel")
@require_local_request
@debug_timing("research_sec_import_cancel")
def research_sec_import_cancel(job_id: str):
    try:
        job = SecBulkImportService(get_default_store()).cancel_for_recovery(job_id)
        return jsonify({"ok": True, "data": job})
    except ValueError as exc:
        return _json_error(exc, 404)
    except Exception as exc:
        return _json_error(exc)


_REVEALABLE_SETTING_FIELDS = {
    "finnhub_api_keys",
    "active_finnhub_api_key",
    "sec_edgar_user_agent",
    "coingecko_api_key",
    "llm_api_key",
}


def _revealable_setting_value(settings: dict, field: str):
    if field in _REVEALABLE_SETTING_FIELDS:
        return settings.get(field, [] if field == "finnhub_api_keys" else "")
    prefix = "openbb_provider_credentials."
    if field.startswith(prefix):
        credential_key = field.removeprefix(prefix)
        credentials = settings.get("openbb_provider_credentials")
        if credential_key in OPENBB_CREDENTIAL_KEYS and isinstance(credentials, dict):
            return credentials.get(credential_key, "")
    raise ValueError("This setting cannot be revealed.")


@app.post("/api/settings/secrets/reveal")
@require_local_request
def reveal_setting_secret():
    try:
        payload = request.get_json(silent=True) or {}
        field = str(payload.get("field") or "").strip()
        value = _revealable_setting_value(load_web_settings(), field)
        response = jsonify({"ok": True, "data": {"field": field, "value": value}})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/data-sources")
@require_local_request
@debug_timing("data_sources_list")
def data_sources_list():
    try:
        data = DataSourceManagementService(
            load_web_settings(), base_dir=BASE_DIR
        ).describe()
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/data-sources/<source_id>/test")
@require_local_request
@debug_timing("data_source_connection_test")
def data_source_connection_test(source_id: str):
    try:
        data = DataSourceConnectionService(load_web_settings()).test(source_id)
        return jsonify({"ok": True, "data": data})
    except ValueError as exc:
        return _json_error(exc, 400)
    except DataSourceConnectionError as exc:
        return _json_error(exc, 502)
    except Exception as exc:
        return _json_error(exc, 502)


@app.get("/api/data-sources/equity/quotes")
@require_local_request
@debug_timing("data_source_equity_quotes")
def data_source_equity_quotes():
    try:
        symbols = request.args.get("symbols", "AAPL")
        data = DataSourceConnectionService(load_web_settings()).equity_quotes(symbols)
        return jsonify({"ok": True, "data": data})
    except ValueError as exc:
        return _json_error(exc, 400)
    except DataSourceConnectionError as exc:
        return _json_error(exc, 502)
    except Exception as exc:
        return _json_error(exc, 502)


@app.get("/api/data-sources/sec/company-facts/<cik>")
@require_local_request
@debug_timing("data_source_sec_company_facts")
def data_source_sec_company_facts(cik: str):
    try:
        concepts = [
            item.strip()
            for item in request.args.get("concepts", "").split(",")
            if item.strip()
        ]
        data = DataSourceConnectionService(load_web_settings()).sec_company_facts(
            cik, concepts=concepts or None
        )
        return jsonify({"ok": True, "data": data})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc, 502)


@app.put("/api/data-sources/routing")
@require_local_request
@debug_timing("data_sources_routing_update")
def data_sources_routing_update():
    try:
        payload = request.get_json(silent=True) or {}
        data = DataSourceManagementService(
            load_web_settings(), base_dir=BASE_DIR
        ).update_routing(payload)
        return jsonify({"ok": True, "data": data})
    except DataSourceRoutingConflict as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "DATA_SOURCE_VERSION_CONFLICT"}), 409
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/data-sources/openbb/activate")
@require_local_request
@debug_timing("data_sources_openbb_activate")
def data_sources_openbb_activate():
    try:
        payload = request.get_json(silent=True) or {}
        provider_id = str(payload.get("provider_id") or "").strip()
        DataSourceManagementService(
            load_web_settings(), base_dir=BASE_DIR
        ).activate_openbb_provider(provider_id)
        health = _restart_openbb_gateway()
        data_sources = DataSourceManagementService(
            load_web_settings(), base_dir=BASE_DIR
        ).describe()
        return jsonify({"ok": True, "data": {
            "provider_id": provider_id.lower().removeprefix("openbb:"),
            "health": health,
            "data_sources": data_sources,
        }})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/data-sources/openbb/reload")
@require_local_request
@debug_timing("data_sources_openbb_reload")
def data_sources_openbb_reload():
    try:
        health = _restart_openbb_gateway()
        data_sources = DataSourceManagementService(
            load_web_settings(), base_dir=BASE_DIR
        ).describe()
        return jsonify({"ok": True, "data": {
            "health": health,
            "data_sources": data_sources,
        }})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/settings")
@require_local_request
def update_settings():
    try:
        payload = request.get_json(silent=True) or {}
        save_web_settings(payload)
        return jsonify({"ok": True, "data": load_public_web_settings()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/overview")
@debug_timing("overview")
def overview():
    wallet = request.args.get("wallet", "")
    try:
        data = get_overview(wallet or None)
        data["collector"] = collector.get_state()
        data["settings"] = load_public_web_settings()
        return jsonify(data)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/dictionary")
def polymarket_dictionary_status():
    try:
        return jsonify({"ok": True, "data": get_dictionary_status()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/dictionary/update")
def polymarket_dictionary_update():
    try:
        return jsonify({"ok": True, "data": start_dictionary_refresh()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/live/polymarket/dictionary")
def live_polymarket_dictionary():
    def _sse(event_name: str, payload) -> str:
        return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def event_stream():
        last_payload = ""
        deadline = time.time() + 3600
        while time.time() < deadline:
            try:
                payload = get_dictionary_status()
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if encoded != last_payload:
                    last_payload = encoded
                    yield _sse("state", payload)
            except GeneratorExit:
                return
            except Exception as exc:
                yield _sse("error", {"error": str(exc)})
            time.sleep(1)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/polymarket/market-categories")
def polymarket_market_categories():
    force_refresh = request.args.get("refresh", "0") == "1"
    limit = request.args.get("limit", "120")
    try:
        limit_num = max(0, min(int(limit), 500))
    except ValueError:
        limit_num = 120
    try:
        data = list_market_categories(force_refresh=force_refresh, limit=limit_num)
        return jsonify({"ok": True, "count": len(data), "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph")
@debug_timing("event_graph")
def event_graph_api():
    try:
        return jsonify(build_event_graph(dict(request.args)))
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph/categories")
@debug_timing("event_graph_categories")
def event_graph_categories_api():
    try:
        limit = request.args.get("limit", "120")
        try:
            limit_num = max(1, min(int(limit), 240))
        except ValueError:
            limit_num = 120
        return jsonify({"ok": True, "data": get_event_graph_categories(limit=limit_num)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph/news/status")
@debug_timing("event_graph_news_status")
def event_graph_news_status_api():
    try:
        return jsonify({"ok": True, "data": get_event_news_status()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/news/refresh")
@debug_timing("event_graph_news_refresh")
def event_graph_news_refresh_api():
    try:
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("q") or payload.get("query") or request.args.get("q", "") or "").strip()
        limit = payload.get("limit_per_source") or payload.get("limit") or request.args.get("limit", "24")
        return jsonify({"ok": True, "data": refresh_news(query=query, limit_per_source=limit)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/news/search")
@debug_timing("event_graph_news_search")
def event_graph_news_search_api():
    try:
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("q") or payload.get("query") or request.args.get("q", "") or "").strip()
        if not query:
            return _json_error(ValueError("q is required"), 400)
        limit = payload.get("limit_per_source") or payload.get("limit") or request.args.get("limit", "30")
        return jsonify({"ok": True, "data": refresh_news(query=query, limit_per_source=limit)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph/events")
@debug_timing("event_graph_events")
def event_graph_events_api():
    try:
        query = str(request.args.get("q", "") or "").strip()
        limit = request.args.get("limit", "80")
        include_observations = str(request.args.get("include_observations", "1")).lower() not in {"0", "false", "no"}
        return jsonify({"ok": True, "data": list_news_events(q=query, limit=limit, include_observations=include_observations)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/event-graph/observations")
@debug_timing("event_graph_observations")
def event_graph_observations_api():
    try:
        event_id = str(request.args.get("event_id", "") or "").strip()
        query = str(request.args.get("q", "") or "").strip()
        limit = request.args.get("limit", "120")
        return jsonify({"ok": True, "data": list_news_observations(event_id=event_id, q=query, limit=limit)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/providers/openbb")
def openbb_provider_capabilities():
    try:
        return jsonify({"ok": True, "data": OpenBBProviderService(load_web_settings()).capabilities()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/research/data/providers/openbb/worker-status")
def openbb_worker_status():
    try:
        settings = load_web_settings()
        executor = OpenBBResearchTaskExecutor(get_default_store(), settings)
        worker = OpenBBResearchWorker(executor, "status-reader")
        return jsonify({
            "ok": True,
            "data": {
                "provider": OpenBBProviderService(settings).health(),
                "worker": worker.status(),
            },
        })
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/providers/openbb/worker/start")
@require_local_request
@debug_timing("research_openbb_worker_start")
def openbb_worker_start():
    try:
        started = _start_openbb_export_worker()
        return jsonify({"ok": True, "data": {"started": started, "running": True}}), 202
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/research/data/source-policy/resolve")
@require_local_request
def research_source_policy_resolve():
    try:
        payload = request.get_json(silent=True) or {}
        policy = SourcePolicy.from_dict(payload)
        service = SourcePolicyService(get_default_store())
        data = service.fixed(policy) if policy.mode == "FIXED" else service.compare(
            policy, price_tolerance_bps=float(payload.get("price_tolerance_bps") or 1.0)
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc, 400 if isinstance(exc, ValueError) else 500)


@app.post("/api/research/data/providers/openbb/equity/historical")
@require_local_request
def openbb_equity_historical():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": OpenBBProviderService(load_web_settings()).fetch_equity_historical(payload)})
    except Exception as exc:
        return _json_error(exc, 400 if isinstance(exc, ValueError) else 502)


@app.get("/api/research/data/providers/openbb/fred/series")
@require_local_request
def openbb_fred_series():
    try:
        return jsonify({
            "ok": True,
            "data": OpenBBProviderService(load_web_settings()).fetch_fred_series(dict(request.args)),
        })
    except Exception as exc:
        return _json_error(exc, 400 if isinstance(exc, ValueError) else 502)




@app.post("/api/event-graph/news/deduplicate")
@debug_timing("event_graph_news_deduplicate")
def event_graph_news_deduplicate_api():
    try:
        payload = request.get_json(silent=True) or {}
        dry_raw = payload.get("dry_run", request.args.get("dry_run", "0"))
        dry_run = str(dry_raw).strip().lower() in {"1", "true", "yes", "on"}
        limit = payload.get("limit") or request.args.get("limit", "500")
        return jsonify({"ok": True, "data": deduplicate_derived_events(dry_run=dry_run, limit=limit)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/markets")
def polymarket_markets():
    query = request.args.get("q", "")
    category_values = [value for value in request.args.getlist("category") if str(value or "").strip()]
    category = ",".join(category_values) if category_values else request.args.get("category", "")
    sort_by = request.args.get("sort", request.args.get("sort_by", ""))
    sort_dir = request.args.get("order", request.args.get("sort_dir", "desc"))
    force_refresh = request.args.get("refresh", "0") == "1"
    limit = request.args.get("limit", "60")
    price_filters = {
        "yes_ask": (
            request.args.get("yes_ask_min", request.args.get("ask_min")),
            request.args.get("yes_ask_max", request.args.get("ask_max")),
        ),
        "yes_bid": (
            request.args.get("yes_bid_min", request.args.get("bid_min")),
            request.args.get("yes_bid_max", request.args.get("bid_max")),
        ),
        "no_ask": (request.args.get("no_ask_min"), request.args.get("no_ask_max")),
        "no_bid": (request.args.get("no_bid_min"), request.args.get("no_bid_max")),
    }
    try:
        limit_num = max(1, min(int(limit), 200))
    except ValueError:
        limit_num = 60
    try:
        data = search_markets(
            query=query,
            category=category,
            limit=limit_num,
            force_refresh=force_refresh,
            sort_by=sort_by,
            sort_dir=sort_dir,
            price_filters=price_filters,
        )
        return jsonify({"ok": True, "count": len(data), "sort": sort_by, "order": sort_dir, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/markets/resolve")
def polymarket_market_resolve():
    query = request.args.get("q", "")
    condition_id = request.args.get("condition_id", "")
    token_id = request.args.get("token_id", "")
    force_refresh = request.args.get("refresh", "0") == "1"
    limit = request.args.get("limit", "20")
    try:
        limit_num = max(1, min(int(limit), 100))
    except ValueError:
        limit_num = 20
    try:
        data = resolve_market_selection(
            query=query,
            condition_id=condition_id,
            token_id=token_id,
            limit=limit_num,
            force_refresh=force_refresh,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/binance/markets/search")
def binance_markets_search():
    try:
        data = search_binance_markets(dict(request.args))
        return jsonify(data)
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/health")
@debug_timing("history_health")
def history_health():
    try:
        return jsonify(get_history_health())
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/storage")
@require_local_request
@debug_timing("history_storage_status")
def history_storage_status():
    try:
        return jsonify({"ok": True, "data": HistoryStorageService(base_dir=BASE_DIR).status()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/storage/coverage")
@require_local_request
@debug_timing("history_storage_coverage")
def history_storage_coverage():
    try:
        return jsonify({"ok": True, "data": HistoryStorageService(base_dir=BASE_DIR).archive_coverage()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/storage/inspect")
@require_local_request
@debug_timing("history_storage_inspect")
def history_storage_inspect():
    try:
        payload = request.get_json(silent=True) or {}
        settings = load_web_settings()
        root = payload.get("root") or settings.get("history_data_root")
        source_roots = payload.get("source_roots")
        if source_roots is None:
            source_roots = settings.get("history_data_source_roots") or []
        data = HistoryStorageService(base_dir=BASE_DIR, settings=settings).plan(root, source_roots)
        return jsonify({"ok": True, "data": data})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/storage/normalize")
@require_local_request
@debug_timing("history_storage_normalize")
def history_storage_normalize():
    try:
        payload = request.get_json(silent=True) or {}
        settings = load_web_settings()
        root = payload.get("root") or settings.get("history_data_root")
        source_roots = payload.get("source_roots")
        if source_roots is None:
            source_roots = settings.get("history_data_source_roots") or []
        data = HistoryStorageService(base_dir=BASE_DIR, settings=settings).start(root, source_roots)
        return jsonify({"ok": True, "data": data}), 202
    except ValueError as exc:
        return _json_error(exc, 400)
    except RuntimeError as exc:
        return _json_error(exc, 409)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/storage/jobs/<job_id>")
@require_local_request
@debug_timing("history_storage_job")
def history_storage_job(job_id: str):
    data = get_history_storage_job(job_id)
    if data is None:
        return jsonify({"ok": False, "error": "History Data normalization job not found"}), 404
    return jsonify({"ok": True, "data": data})


@app.get("/api/history/search")
@debug_timing("history_search")
def history_search():
    source = str(request.args.get("source") or "polymarket").strip().lower()
    limit = request.args.get("limit", "40")
    try:
        limit_num = max(1, min(int(limit), 100))
    except ValueError:
        limit_num = 40
    try:
        if source == "binance":
            data = search_binance_markets(
                {
                    "category": "crypto_spot",
                    "q": request.args.get("q", ""),
                    "quote": request.args.get("quote", "USDT"),
                    "limit": limit_num,
                    "refresh": request.args.get("refresh", "0"),
                }
            )
            rows = data.get("data") if isinstance(data, dict) else []
            for row in rows or []:
                row["history_coverage"] = get_history_coverage(
                    "binance",
                    symbol=row.get("symbol"),
                    interval=request.args.get("interval", "1m"),
                )
            return jsonify({"ok": True, "source": source, "count": len(rows or []), "data": rows or [], "meta": data.get("meta", {})})
        if source == "polymarket":
            rows = search_markets(
                query=request.args.get("q", ""),
                category=request.args.get("category", ""),
                limit=limit_num,
                force_refresh=request.args.get("refresh", "0") == "1",
                sort_by=request.args.get("sort", "volume24h"),
                sort_dir=request.args.get("order", "desc"),
            )
            for row in rows:
                token_id = str(row.get("yes_token") or row.get("token") or "").strip()
                row["history_coverage"] = get_history_coverage(
                    "polymarket",
                    condition_id=row.get("condition_id"),
                    token_id=token_id,
                )
            return jsonify({"ok": True, "source": source, "count": len(rows), "data": rows})
        return _json_error(ValueError("source must be binance or polymarket"), 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/watchlist")
@debug_timing("history_watchlist")
def history_watchlist():
    try:
        source = str(request.args.get("source") or "").strip().lower()
        return jsonify({"ok": True, "data": list_history_watchlist(source)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/watchlist")
@debug_timing("history_watchlist_add")
def history_watchlist_add():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": add_history_watchlist_item(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/history/watchlist/<int:item_id>")
@debug_timing("history_watchlist_delete")
def history_watchlist_delete(item_id: int):
    try:
        return jsonify({"ok": True, "deleted": delete_history_watchlist_item(item_id)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-cases")
@debug_timing("history_backtest_cases")
def history_backtest_cases():
    try:
        return jsonify({"ok": True, "data": list_history_backtest_cases()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-collections")
@debug_timing("history_backtest_collections")
def history_backtest_collections():
    try:
        return jsonify({"ok": True, "data": list_history_backtest_collections()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-collections")
@debug_timing("history_backtest_collection_create")
def history_backtest_collection_create():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": create_history_backtest_collection(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-cases")
@debug_timing("history_backtest_case_create")
def history_backtest_case_create():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": create_history_backtest_case(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/history/backtest-cases/<int:case_id>")
@debug_timing("history_backtest_case_delete")
def history_backtest_case_delete(case_id: int):
    try:
        return jsonify({"ok": True, "deleted": delete_history_backtest_case(case_id)})
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/history/backtest-cases/<int:case_id>")
@debug_timing("history_backtest_case_rename")
def history_backtest_case_rename(case_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": rename_history_backtest_case(case_id, payload.get("name") or payload.get("case_name") or "")})
    except ValueError as exc:
        return _json_error(exc, 400 if "required" in str(exc).lower() else 404)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-cases/evaluate")
@debug_timing("history_backtest_case_evaluate")
def history_backtest_case_evaluate():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(evaluate_backtest_case_payload(payload))
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-batches")
@debug_timing("history_backtest_batches")
def history_backtest_batches():
    try:
        limit = int(request.args.get("limit") or 50)
        return jsonify({"ok": True, "data": list_history_backtest_batches(limit=limit)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-batches")
@debug_timing("history_backtest_batch_create")
def history_backtest_batch_create():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": create_history_backtest_batch(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-batches/<batch_id>")
@debug_timing("history_backtest_batch")
def history_backtest_batch(batch_id: str):
    try:
        include_runs = str(request.args.get("include_runs", "1")).lower() not in {"0", "false", "no"}
        data = get_history_backtest_batch(batch_id, include_runs=include_runs)
        if not data:
            return _json_error(ValueError("backtest batch not found"), 404)
        return jsonify({"ok": True, "data": data})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/history/backtest-batches/<batch_id>")
@debug_timing("history_backtest_batch_delete")
def history_backtest_batch_delete(batch_id: str):
    try:
        result = delete_history_backtest_batch(batch_id)
        if not result.get("deleted_runs"):
            return _json_error(ValueError("backtest batch not found"), 404)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/history/backtest-batches/<batch_id>")
@debug_timing("history_backtest_batch_rename")
def history_backtest_batch_rename(batch_id: str):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": rename_history_backtest_batch(batch_id, payload.get("name") or payload.get("batch_name") or "")})
    except ValueError as exc:
        return _json_error(exc, 400 if "required" in str(exc).lower() else 404)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-runs")
@debug_timing("history_backtest_runs")
def history_backtest_runs():
    try:
        case_id = request.args.get("case_id")
        batch_id = request.args.get("batch_id") or ""
        return jsonify({
            "ok": True,
            "data": list_history_backtest_runs(int(case_id), batch_id=batch_id) if case_id else list_history_backtest_runs(batch_id=batch_id),
        })
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/history/backtest-runs/<int:run_id>")
@debug_timing("history_backtest_run_delete")
def history_backtest_run_delete(run_id: int):
    try:
        deleted = delete_history_backtest_run(run_id)
        if not deleted:
            return _json_error(ValueError("backtest run not found"), 404)
        return jsonify({"ok": True, "data": {"run_id": run_id, "deleted": True}})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-runs/<int:run_id>/workspace")
@debug_timing("history_backtest_run_workspace_import")
def history_backtest_run_workspace_import(run_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": import_history_backtest_run_to_workspace(run_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/history/backtest-runs/<int:run_id>")
@debug_timing("history_backtest_run_rename")
def history_backtest_run_rename(run_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": rename_history_backtest_run(run_id, payload.get("name") or payload.get("run_name") or "")})
    except ValueError as exc:
        return _json_error(exc, 400 if "required" in str(exc).lower() else 404)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-cases/<int:case_id>/runs")
@debug_timing("history_backtest_run_create")
def history_backtest_run_create(case_id: int):
    try:
        payload = {**(request.get_json(silent=True) or {}), "run_mode": "async"}
        return jsonify({"ok": True, "data": create_history_backtest_run(case_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/backtest-runs/<int:run_id>")
@debug_timing("history_backtest_run")
def history_backtest_run(run_id: int):
    try:
        equity_limit = int(request.args.get("equity_limit") or 5000)
        orders_limit = int(request.args.get("orders_limit") or 3000)
        events_limit = int(request.args.get("events_limit") or 500)
        data = get_history_backtest_run(
            run_id,
            equity_limit=equity_limit,
            orders_limit=orders_limit,
            events_limit=events_limit,
        )
        if not data:
            return _json_error(ValueError("backtest run not found"), 404)
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/backtest-runs/<int:run_id>/rerun")
@debug_timing("history_backtest_run_rerun")
def history_backtest_run_rerun(run_id: int):
    try:
        payload = {**(request.get_json(silent=True) or {}), "run_mode": "async"}
        return jsonify({"ok": True, "data": rerun_history_backtest_run(run_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/coverage")
@debug_timing("history_coverage")
def history_coverage():
    try:
        return jsonify(
            {
                "ok": True,
                "data": get_history_coverage(
                    request.args.get("source", ""),
                    symbol=request.args.get("symbol", ""),
                    interval=request.args.get("interval", "1m"),
                    condition_id=request.args.get("condition_id", ""),
                    token_id=request.args.get("token_id", ""),
                ),
            }
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/binance/download")
@debug_timing("history_binance_download")
def history_binance_download():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(download_binance_klines_range(payload))
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/history/polymarket/download")
@debug_timing("history_polymarket_download")
def history_polymarket_download():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(download_polymarket_price_history(payload))
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/history/preview")
@debug_timing("history_preview")
def history_preview():
    try:
        return jsonify(
            preview_history(
                request.args.get("source", ""),
                symbol=request.args.get("symbol", ""),
                interval=request.args.get("interval", "1m"),
                token_id=request.args.get("token_id", ""),
                limit=request.args.get("limit", "240"),
            )
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/holdings")
def polymarket_holdings():
    wallet = request.args.get("wallet", "")
    try:
        return jsonify(fetch_wallet_positions(wallet or None))
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/ledger")
@debug_timing("ledger")
def ledger_snapshot():
    try:
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify(get_ledger_snapshot(limit=limit_num))
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/capabilities")
@debug_timing("agent_capabilities")
def agent_capabilities():
    try:
        section = str(request.args.get("section", "")).strip()
        return jsonify({"ok": True, "data": agent_service.get_capabilities(section=section)})
    except Exception as exc:
        return _json_error(exc)


def _agent_query_payload() -> dict:
    payload = dict(request.args)
    categories = [value for value in request.args.getlist("category") if str(value or "").strip()]
    if categories:
        payload["categories"] = categories
    payload.setdefault("actor_type", "agent")
    payload.setdefault("actor_id", "agent_strategy_assistant")
    payload.setdefault("_endpoint", request.path)
    payload.setdefault("_method", request.method)
    return payload


def _agent_body_payload(default_type: str = "agent", default_id: str = "agent_strategy_assistant") -> dict:
    payload = request.get_json(silent=True) or {}
    payload.setdefault("actor_type", default_type)
    payload.setdefault("actor_id", default_id)
    payload.setdefault("_endpoint", request.path)
    payload.setdefault("_method", request.method)
    return payload


def _agent_research_authorize(
    payload: dict,
    project_id: str,
    operation: str,
    capability: str,
    **scope: object,
):
    actor_type = str(payload.get("actor_type") or "agent").strip().lower()
    actor_id = str(payload.get("actor_id") or "agent_strategy_assistant").strip()
    if actor_type == "agent" and actor_id == "datatube_researcher":
        raise ResearchAuthorizationError(
            "RESEARCHER_INFRASTRUCTURE_SURFACE_DENIED",
            "The Research Agent may submit semantic Experiments but may not operate DataTube internal Research objects",
        )
    agent_service.require_agent_capability(capability, actor_type)
    grant_id = str(payload.get("grant_id") or "")
    session_id = str(payload.get("session_id") or "").strip()
    if actor_type == "agent" and not session_id and not grant_id:
        raise ResearchAuthorizationError(
            "RESEARCH_SESSION_REQUIRED",
            "Agent Research writes require the canonical session_id; implicit latest-Grant fallback is disabled",
        )
    if session_id and not grant_id:
        session = ResearchAgentSessionService(get_default_store()).get(
            session_id, include_events=False, include_iterations=False
        )
        if session is None:
            raise ResearchAuthorizationError("RESEARCH_SESSION_NOT_FOUND", "Research Session does not exist")
        if str(session.get("project_id") or "") != str(project_id):
            raise ResearchAuthorizationError("RESEARCH_SESSION_SCOPE_VIOLATION", "Research Session belongs to another Project")
        if str(session.get("status") or "").upper() in {"PAUSED", "NEED_HUMAN", "BLOCKED", "COMPLETED", "FAILED", "CANCELLED"}:
            raise ResearchAuthorizationError(
                "RESEARCH_SESSION_INACTIVE", f"Research Session status is {session.get('status') or 'UNKNOWN'}"
            )
        grant_id = str(session.get("internal_grant_id") or "")
    decision = ResearchAgentAuthorization(get_default_store()).require(
        project_id,
        operation,
        grant_id=grant_id,
        **scope,
    )
    return actor_type, actor_id, decision


def _public_research_session(value: dict | None):
    if value is None:
        return None
    result = dict(value)
    result.pop("internal_grant_id", None)
    context = dict(result.get("context") or {})
    context.pop("grant_id", None)
    context.pop("grant_status", None)
    result["context"] = context
    return result


def _audit_agent_research(
    *,
    actor_type: str,
    actor_id: str,
    capability: str,
    target_type: str,
    target_id: str,
    payload: dict,
    output: dict,
) -> None:
    agent_service.audit_external_action(
        actor_type=actor_type,
        actor_id=actor_id,
        capability=capability,
        target_type=target_type,
        target_id=target_id,
        input_data=payload,
        output_data=output,
    )


def _agent_research_error(exc: Exception):
    if isinstance(exc, ResearchSemanticError):
        body: dict = {"ok": False, "code": exc.code, "error": str(exc)}
        if exc.context:
            body["context"] = exc.context
        status_code = 404 if exc.code.endswith("_NOT_FOUND") else 422
        return jsonify(body), status_code
    if isinstance(exc, ResearchAuthorizationError):
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("actor_type", "agent")
        payload.setdefault("actor_id", "agent_strategy_assistant")
        payload.setdefault("capability", "research.request.denied")
        agent_service.record_request_error(
            path=request.path,
            method=request.method,
            status_code=403,
            error=str(exc),
            payload=payload,
        )
        body: dict = {"ok": False, "code": exc.code, "error": str(exc)}
        if exc.context:
            body["context"] = exc.context
        return jsonify(body), 403
    if isinstance(exc, PermissionError):
        return _json_error(exc, 403)
    if isinstance(exc, (TypeError, ValueError)):
        return _json_error(exc, 400)
    return _json_error(exc)


def _researcher_session_view(value: dict | None) -> dict | None:
    """Expose research meaning and progress, never internal execution IR."""
    if value is None:
        return None
    contract_row = dict(value.get("research_contract") or {})
    contract = dict(contract_row.get("contract") or {})
    experiments = ResearchExperimentService(get_default_store()).list(
        str(value.get("session_id") or ""), limit=100
    )
    champion = next((item for item in experiments if item.get("decision") == "KEEP"), None)
    latest_learning = next(
        (dict(item.get("learning") or {}) for item in experiments if item.get("learning")),
        {},
    )
    public_stage = {
        "BRIEFING": "ALIGNING",
        "PLANNING": "READY_FOR_EXPERIMENT",
        "BUILDING": "EXPERIMENTING",
        "PREPARING_DATA": "EXPERIMENTING",
        "PREVIEWING": "EXPERIMENTING",
        "RUNNING": "EXPERIMENTING",
        "EVALUATING": "EVALUATING",
        "ITERATING": "DECIDING",
        "NEED_HUMAN": "NEEDS_INPUT",
        "PAUSED": "PAUSED",
        "BLOCKED": "SYSTEM_BLOCKED",
        "COMPLETED": "COMPLETED",
        "FAILED": "FAILED",
        "CANCELLED": "CANCELLED",
    }.get(str(value.get("status") or "").upper(), "READY_FOR_EXPERIMENT")
    research_plan = {
        "question": contract.get("question") or contract.get("objective") or value.get("objective"),
        "decision_supported": contract.get("decision_supported"),
        "stop_at": contract.get("stop_at"),
        "entry_mode": contract.get("entry_mode") or value.get("entry_mode"),
        "base_refs": list(contract.get("base_refs") or []),
        "scope": {
            "asset_scope": dict(contract.get("asset_scope") or {}),
            "research_period": dict(contract.get("research_period") or {}),
            "frequency": contract.get("frequency"),
            "universe_policy": dict(contract.get("universe_policy") or {}),
        },
        "evidence": {
            "profile": contract.get("evidence_profile"),
            **dict(contract.get("evaluation") or {}),
        },
        "assumptions": list(contract.get("assumptions") or []),
        "out_of_scope": list(contract.get("out_of_scope") or []),
        "alignment_hash": contract.get("alignment_hash"),
    }
    internal_status = str(value.get("status") or "").upper()
    return {
        "session_id": value.get("session_id"),
        "status": public_stage,
        "entry_mode": value.get("entry_mode"),
        "updated_at": value.get("updated_at"),
        "goal": value.get("objective"),
        "research_plan": research_plan,
        "research_contract": {
            "version": contract_row.get("contract_version"),
            "status": contract_row.get("status"),
            "contract_hash": contract_row.get("contract_hash"),
            **contract,
        } if contract else {},
        "current_champion": {
            "product_type": dict(champion.get("result") or {}).get("product_type") if champion else "",
            "experiment_id": champion.get("experiment_id") if champion else "",
            "decision_metrics": dict(dict(champion.get("result") or {}).get("decision_metrics") or {}) if champion else {},
        },
        "latest_learning": latest_learning,
        "experiment_count": len(experiments),
        "experiments": experiments,
        "pending_question": value.get("pending_question") or {},
        "actions": {
            "needs_human": internal_status == "NEED_HUMAN",
            "can_pause": internal_status not in {
                "PAUSED", "NEED_HUMAN", "BLOCKED", "COMPLETED", "FAILED", "CANCELLED"
            },
            "can_continue": internal_status in {"PAUSED", "BLOCKED"},
            "terminal": internal_status in {"COMPLETED", "FAILED", "CANCELLED"},
        },
        "limits": {
            "max_experiments": dict(contract.get("experiment_policy") or {}).get("max_experiments"),
            "used": len(experiments),
        },
    }


@app.post("/api/agent/researcher/align")
@require_local_request
@debug_timing("researcher_align")
def researcher_align():
    payload = _agent_body_payload()
    try:
        agent_service.require_agent_capability(
            "research.read", str(payload["actor_type"]).lower()
        )
        brief = normalize_research_brief(payload)
        alignment = align_research_intent(brief, payload)
        return jsonify({"ok": True, "data": alignment})
    except Exception as exc:
        return _agent_research_error(exc)


@app.get("/api/agent/researcher/library")
@debug_timing("researcher_library")
def researcher_library():
    try:
        payload = _agent_query_payload()
        agent_service.require_agent_capability("research.read", str(payload["actor_type"]).lower())
        kind = str(payload.get("kind") or "").strip().upper()
        query = str(payload.get("q") or "").strip().lower()
        asset_class = str(payload.get("asset_class") or "").strip().upper()
        frequency = str(payload.get("frequency") or "").strip().lower()
        limit = max(1, min(int(payload.get("limit") or 20), 100))
        rows = ResearchLibraryService(get_default_store()).list(
            component_type=kind,
            include_archived=False,
        )
        items = []
        for row in rows:
            content = dict(row.get("content") or {})
            spec = dict(content.get("spec") or content)
            name = str(row.get("name") or spec.get("name") or "").strip()
            meaning = str(
                spec.get("description")
                or spec.get("research_meaning")
                or content.get("description")
                or name
            ).strip()
            row_asset_class = str(
                spec.get("asset_class") or content.get("asset_class") or ""
            ).strip().upper()
            row_frequency = str(
                spec.get("frequency") or content.get("frequency") or ""
            ).strip().lower()
            haystack = f"{name} {meaning} {row.get('component_type') or ''}".lower()
            if query and query not in haystack:
                continue
            compatibility_reasons = []
            if asset_class and not row_asset_class:
                compatibility_reasons.append("ASSET_CLASS_UNKNOWN")
            elif asset_class and asset_class != row_asset_class:
                compatibility_reasons.append("ASSET_CLASS_MISMATCH")
            if frequency and not row_frequency:
                compatibility_reasons.append("FREQUENCY_UNKNOWN")
            elif frequency and frequency != row_frequency:
                compatibility_reasons.append("FREQUENCY_MISMATCH")
            if any(reason.endswith("_MISMATCH") for reason in compatibility_reasons):
                compatibility = "INCOMPATIBLE"
            elif compatibility_reasons:
                compatibility = "UNKNOWN"
            else:
                compatibility = "COMPATIBLE"
            items.append({
                "asset_ref": f"library:{row.get('library_asset_id')}",
                "kind": str(row.get("component_type") or "").upper(),
                "display_name": name,
                "version": row.get("version"),
                "research_meaning": meaning,
                "asset_class": row_asset_class,
                "frequency": row_frequency,
                "compatibility": compatibility,
                "compatibility_reasons": compatibility_reasons,
            })
            if len(items) >= limit:
                break
        return jsonify({"ok": True, "data": {"items": items, "count": len(items)}})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/researcher/start")
@require_local_request
@debug_timing("researcher_start")
def researcher_start():
    payload = _agent_body_payload()
    try:
        actor_type = str(payload["actor_type"]).lower()
        actor_id = str(payload["actor_id"])
        agent_service.require_agent_capability("research.project.create", actor_type)
        result = ResearchAgentSessionService(get_default_store()).start(
            payload, created_by="local_user", require_alignment=True
        )
        public = _researcher_session_view(result)
        _audit_agent_research(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="research.session.create",
            target_type="research_session",
            target_id=result["session_id"],
            payload=payload,
            output=public or {},
        )
        status_code = 200 if bool(result.get("idempotency_reused")) else 201
        return jsonify({"ok": True, "data": public}), status_code
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/researcher/resume")
@require_local_request
@debug_timing("researcher_resume")
def researcher_resume():
    payload = _agent_body_payload()
    try:
        payload["entry_mode"] = "RESUME"
        actor_type = str(payload["actor_type"]).lower()
        actor_id = str(payload["actor_id"])
        agent_service.require_agent_capability("research.project.create", actor_type)
        result = ResearchAgentSessionService(get_default_store()).resume(
            str(payload.get("anchor_type") or ""),
            str(payload.get("anchor_id") or ""),
            payload,
            created_by="local_user",
            require_alignment=True,
        )
        public = _researcher_session_view(result)
        _audit_agent_research(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="research.session.create",
            target_type="research_session",
            target_id=result["session_id"],
            payload=payload,
            output=public or {},
        )
        return jsonify({"ok": True, "data": public}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.get("/api/agent/researcher/sessions")
@debug_timing("researcher_sessions")
def researcher_sessions():
    try:
        payload = _agent_query_payload()
        agent_service.require_agent_capability("research.read", str(payload["actor_type"]).lower())
        service = ResearchAgentSessionService(get_default_store())
        rows = service.list(
            status=str(payload.get("status") or ""),
            limit=int(payload.get("limit") or 100),
        )
        items = []
        for row in rows:
            detail = service.get(
                str(row.get("session_id") or ""),
                include_events=False,
                include_iterations=False,
            )
            if detail:
                items.append(_researcher_session_view(detail))
        return jsonify({"ok": True, "data": items})
    except Exception as exc:
        return _agent_research_error(exc)


@app.get("/api/agent/researcher/sessions/<session_id>")
@debug_timing("researcher_status")
def researcher_status(session_id: str):
    try:
        payload = _agent_query_payload()
        agent_service.require_agent_capability("research.read", str(payload["actor_type"]).lower())
        result = ResearchAgentSessionService(get_default_store()).get(session_id)
        if result is None:
            return jsonify({"ok": False, "code": "RESEARCH_SESSION_NOT_FOUND", "error": "Research Session not found"}), 404
        return jsonify({"ok": True, "data": _researcher_session_view(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/researcher/sessions/<session_id>/status")
@require_local_request
@debug_timing("researcher_session_status")
def researcher_session_status(session_id: str):
    payload = _agent_body_payload(default_type="human", default_id="local_user")
    try:
        requested = str(payload.get("status") or "").strip().upper()
        if requested not in {"PAUSED", "CANCELLED"}:
            raise ValueError("Researcher status endpoint only accepts PAUSED or CANCELLED")
        result = ResearchAgentSessionService(get_default_store()).set_status(
            session_id,
            requested,
            message=str(payload.get("message") or ""),
            payload=dict(payload.get("progress") or {}),
        )
        return jsonify({"ok": True, "data": _researcher_session_view(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/researcher/sessions/<session_id>/continue")
@require_local_request
@debug_timing("researcher_session_continue")
def researcher_session_continue(session_id: str):
    try:
        result = ResearchAgentSessionService(get_default_store()).continue_session(session_id)
        return jsonify({"ok": True, "data": _researcher_session_view(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/researcher/sessions/<session_id>/need-human")
@require_local_request
@debug_timing("researcher_session_need_human")
def researcher_session_need_human(session_id: str):
    payload = _agent_body_payload()
    try:
        agent_service.require_agent_capability(
            "research.experiment.submit", str(payload["actor_type"]).lower()
        )
        result = ResearchAgentSessionService(get_default_store()).need_human(
            session_id,
            reason_code=str(payload.get("reason_code") or ""),
            question=str(payload.get("question") or ""),
            context=dict(payload.get("context") or {}),
        )
        return jsonify({"ok": True, "data": _researcher_session_view(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/researcher/sessions/<session_id>/answer")
@require_local_request
@debug_timing("researcher_session_answer")
def researcher_session_answer(session_id: str):
    payload = _agent_body_payload(default_type="human", default_id="local_user")
    try:
        if str(payload.get("actor_type") or "").lower() != "human":
            raise PermissionError("only a human actor can answer a Research Session question")
        result = ResearchAgentSessionService(get_default_store()).answer(session_id, payload.get("answer"))
        return jsonify({"ok": True, "data": _researcher_session_view(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/researcher/sessions/<session_id>/experiments")
@require_local_request
@debug_timing("researcher_experiment_submit")
def researcher_experiment_submit(session_id: str):
    payload = _agent_body_payload()
    try:
        actor_type = str(payload["actor_type"]).lower()
        actor_id = str(payload["actor_id"])
        agent_service.require_agent_capability("research.experiment.submit", actor_type)
        # Submission only persists and validates the Candidate. Compilation,
        # data preparation and execution are dispatched by the isolated worker
        # orchestrator so this HTTP request cannot monopolize the Web process.
        result = ResearchExperimentService(get_default_store()).submit(
            session_id, payload, advance_immediately=False
        )
        _audit_agent_research(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="research.experiment.submit",
            target_type="research_experiment",
            target_id=result["experiment_id"],
            payload=payload,
            output=result,
        )
        return jsonify({"ok": True, "data": result}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.get("/api/agent/researcher/experiments/<experiment_id>")
@debug_timing("researcher_experiment_result")
def researcher_experiment_result(experiment_id: str):
    try:
        payload = _agent_query_payload()
        agent_service.require_agent_capability("research.read", str(payload["actor_type"]).lower())
        result = ResearchExperimentService(get_default_store()).get(experiment_id)
        if result is None:
            return jsonify({"ok": False, "code": "RESEARCH_EXPERIMENT_NOT_FOUND", "error": "Experiment not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/researcher/experiments/<experiment_id>/decide")
@require_local_request
@debug_timing("researcher_experiment_decide")
def researcher_experiment_decide(experiment_id: str):
    payload = _agent_body_payload()
    try:
        actor_type = str(payload["actor_type"]).lower()
        actor_id = str(payload["actor_id"])
        agent_service.require_agent_capability("research.experiment.decide", actor_type)
        result = ResearchExperimentService(get_default_store()).decide(
            experiment_id,
            str(payload.get("decision") or ""),
            payload.get("learning") or {},
        )
        _audit_agent_research(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="research.experiment.decide",
            target_type="research_experiment",
            target_id=experiment_id,
            payload=payload,
            output=result,
        )
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _agent_research_error(exc)


@app.get("/api/agent/research/sessions")
@debug_timing("agent_research_sessions")
def agent_research_sessions():
    try:
        payload = _agent_query_payload()
        agent_service.require_agent_capability("research.read", str(payload["actor_type"]).lower())
        rows = ResearchAgentSessionService(get_default_store()).list(
            project_id=str(payload.get("project_id") or ""),
            status=str(payload.get("status") or ""),
            limit=int(payload.get("limit") or 100),
        )
        return jsonify({"ok": True, "data": [_public_research_session(item) for item in rows]})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/sessions")
@require_local_request
@debug_timing("agent_research_session_create")
def agent_research_session_create():
    payload = _agent_body_payload()
    try:
        actor_type = str(payload["actor_type"]).lower()
        actor_id = str(payload["actor_id"])
        agent_service.require_agent_capability("research.project.create", actor_type)
        mode = str(payload.get("entry_mode") or payload.get("mode") or "START").strip().upper()
        service = ResearchAgentSessionService(get_default_store())
        if mode == "START":
            result = service.start(payload, created_by="local_user")
        elif mode == "RESUME":
            result = service.resume(
                str(payload.get("anchor_type") or ""),
                str(payload.get("anchor_id") or ""),
                payload,
                created_by="local_user",
            )
        else:
            raise ValueError("entry_mode must be START or RESUME")
        public = _public_research_session(result)
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.session.create",
            target_type="research_session", target_id=result["session_id"], payload=payload, output=public,
        )
        status_code = 200 if bool(public.get("idempotency_reused")) else 201
        return jsonify({"ok": True, "data": public}), status_code
    except Exception as exc:
        return _agent_research_error(exc)


@app.get("/api/agent/research/sessions/<session_id>")
@debug_timing("agent_research_session_detail")
def agent_research_session_detail(session_id: str):
    try:
        payload = _agent_query_payload()
        agent_service.require_agent_capability("research.read", str(payload["actor_type"]).lower())
        result = ResearchAgentSessionService(get_default_store()).get(session_id)
        if result is None:
            return jsonify({"ok": False, "code": "RESEARCH_SESSION_NOT_FOUND", "error": "Research Session not found"}), 404
        return jsonify({"ok": True, "data": _public_research_session(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.get("/api/agent/research/context")
@debug_timing("agent_research_context_resolve")
def agent_research_context_resolve():
    try:
        payload = _agent_query_payload()
        agent_service.require_agent_capability("research.read", str(payload["actor_type"]).lower())
        result = ResearchContextResolver(get_default_store()).resolve(
            str(payload.get("anchor_type") or ""), str(payload.get("anchor_id") or "")
        )
        result.pop("grant_id", None)
        result.pop("grant_status", None)
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/sessions/<session_id>/status")
@require_local_request
@debug_timing("agent_research_session_status")
def agent_research_session_status(session_id: str):
    payload = _agent_body_payload()
    try:
        result = ResearchAgentSessionService(get_default_store()).set_status(
            session_id,
            str(payload.get("status") or ""),
            message=str(payload.get("message") or ""),
            payload=dict(payload.get("progress") or {}),
        )
        return jsonify({"ok": True, "data": _public_research_session(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/sessions/<session_id>/continue")
@require_local_request
@debug_timing("agent_research_session_continue")
def agent_research_session_continue(session_id: str):
    try:
        result = ResearchAgentSessionService(get_default_store()).continue_session(session_id)
        return jsonify({"ok": True, "data": _public_research_session(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/sessions/<session_id>/need-human")
@require_local_request
@debug_timing("agent_research_session_need_human")
def agent_research_session_need_human(session_id: str):
    payload = _agent_body_payload()
    try:
        agent_service.require_agent_capability(
            "research.experiment.submit", str(payload["actor_type"]).lower()
        )
        result = ResearchAgentSessionService(get_default_store()).need_human(
            session_id,
            reason_code=str(payload.get("reason_code") or ""),
            question=str(payload.get("question") or ""),
            context=dict(payload.get("context") or {}),
        )
        return jsonify({"ok": True, "data": _public_research_session(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/sessions/<session_id>/answer")
@require_local_request
@debug_timing("agent_research_session_answer")
def agent_research_session_answer(session_id: str):
    payload = _agent_body_payload(default_type="human", default_id="local_user")
    try:
        if str(payload.get("actor_type") or "").lower() != "human":
            raise PermissionError("only a human actor can answer a Research Session question")
        result = ResearchAgentSessionService(get_default_store()).answer(session_id, payload.get("answer"))
        return jsonify({"ok": True, "data": _public_research_session(result)})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/sessions/<session_id>/iterations")
@require_local_request
@debug_timing("agent_research_iteration_create")
def agent_research_iteration_create(session_id: str):
    payload = _agent_body_payload()
    try:
        result = ResearchAgentSessionService(get_default_store()).create_iteration(session_id, payload)
        return jsonify({"ok": True, "data": result}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/iterations/<iteration_id>/complete")
@require_local_request
@debug_timing("agent_research_iteration_complete")
def agent_research_iteration_complete(iteration_id: str):
    payload = _agent_body_payload()
    try:
        result = ResearchAgentSessionService(get_default_store()).complete_iteration(iteration_id, payload)
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _agent_research_error(exc)


@app.get("/api/agent/research/projects")
@debug_timing("agent_research_projects")
def agent_research_projects():
    try:
        payload = _agent_query_payload()
        agent_service.require_agent_capability("research.read", str(payload["actor_type"]).lower())
        return jsonify({
            "ok": True,
            "data": ResearchControlPlane(get_default_store()).list_projects(
                summary_state=str(payload.get("summary_state") or ""),
                limit=int(payload.get("limit") or 100),
            ),
        })
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects")
@require_local_request
@debug_timing("agent_research_project_create")
def agent_research_project_create():
    payload = _agent_body_payload()
    try:
        actor_type = str(payload["actor_type"]).lower()
        actor_id = str(payload["actor_id"])
        agent_service.require_agent_capability("research.project.create", actor_type)
        result = ResearchControlPlane(get_default_store()).create_project(
            title=str(payload.get("title") or ""),
            objective=str(payload.get("objective") or ""),
            created_by=actor_id,
        )
        result["next_required_action"] = "HUMAN_CREATE_PROJECT_RESEARCH_GRANT"
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.project.create",
            target_type="research_project", target_id=result["project_id"], payload=payload, output=result,
        )
        return jsonify({"ok": True, "data": result}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/universes")
@require_local_request
@debug_timing("agent_research_universe_create")
def agent_research_universe_create(project_id: str):
    payload = _agent_body_payload()
    try:
        shared_definition = payload.get("definition")
        requested_type = str(payload.get("universe_type") or "").strip().lower()
        if shared_definition or requested_type in {
            "instrument_set", "benchmark_set", "composite_set", "multi_leg_set"
        }:
            definition = dict(shared_definition or payload)
            shared_service = SharedUniverseService(get_default_store())
            preview = shared_service.preview(definition)
            instruments = list(preview.get("instrument_ids") or [])
            inferred_providers = set()
            for instrument_id in instruments:
                parts = str(instrument_id).split(":")
                asset_class = parts[0].lower() if parts else ""
                venue = parts[1].upper() if len(parts) > 1 else ""
                if asset_class.startswith("crypto"):
                    inferred_providers.add("BINANCE")
                elif asset_class == "equity":
                    inferred_providers.add("YFINANCE")
                elif asset_class == "macro":
                    inferred_providers.add("FRED")
                elif venue == "POLYMARKET":
                    inferred_providers.add("POLYMARKET")
            benchmark_provider = str(
                (definition.get("benchmark") or {}).get("provider") or ""
            ).strip().upper()
            if benchmark_provider:
                inferred_providers.add(benchmark_provider)
            actor_type, actor_id, decision = _agent_research_authorize(
                payload,
                project_id,
                "UNIVERSE_CREATE",
                "research.universe.create",
                providers=payload.get("providers") or sorted(inferred_providers),
                instrument_ids=instruments,
            )
            result = shared_service.create(
                definition,
                created_by=actor_id,
                project_id=project_id,
            )
            resolution = result.get("current_resolution") or {}
            legacy_snapshot_id = str(resolution.get("legacy_snapshot_id") or "")
            legacy_snapshot = UniverseService(get_default_store()).get_snapshot(
                legacy_snapshot_id
            )
            data = {
                **result,
                "universe_definition_id": (
                    legacy_snapshot.universe_definition_id
                    if legacy_snapshot else ""
                ),
                "universe_snapshot_id": legacy_snapshot_id,
                "authorization": decision.to_dict(),
            }
            _audit_agent_research(
                actor_type=actor_type,
                actor_id=actor_id,
                capability="research.universe.create",
                target_type="shared_universe",
                target_id=result["universe_id"],
                payload=payload,
                output=data,
            )
            return jsonify({"ok": True, "data": data}), 201
        parameters = dict(payload.get("parameters") or {})
        instruments = parameters.get("instrument_ids") or parameters.get("candidate_instrument_ids") or []
        if str(payload.get("universe_type") or "").strip().upper() == "HISTORICAL_EQUITY_PIT":
            instruments = ["equity:CRSP:ALL"]
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "UNIVERSE_CREATE", "research.universe.create",
            providers=payload.get("providers") or [], instrument_ids=instruments,
        )
        result = UniverseService(get_default_store()).create_definition(
            name=str(payload.get("name") or ""),
            version=str(payload.get("version") or ""),
            universe_type=str(payload.get("universe_type") or "STATIC_LIST"),
            parameters=parameters,
            selection_rule_version=str(payload.get("selection_rule_version") or "universe-engine.v1"),
            owner_project_id=project_id,
            library_scope="PROJECT",
        )
        if result.library_scope == "PROJECT" and result.owner_project_id != project_id:
            raise ResearchAuthorizationError(
                "RESEARCH_UNIVERSE_OUT_OF_SCOPE",
                "An identical Project-scoped Universe belongs to another Project",
            )
        data = {**asdict(result), "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.universe.create",
            target_type="universe_definition", target_id=result.universe_definition_id, payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/universes/<universe_definition_id>/snapshots")
@require_local_request
@debug_timing("agent_research_universe_snapshot_create")
def agent_research_universe_snapshot_create(project_id: str, universe_definition_id: str):
    payload = _agent_body_payload()
    try:
        universe = UniverseService(get_default_store()).get_definition(universe_definition_id)
        if universe is None or universe.owner_project_id not in {"", project_id}:
            raise ValueError("universe definition is not available to this Project")
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "UNIVERSE_SNAPSHOT_CREATE", "research.universe.snapshot.create",
            universe_definition_id=universe_definition_id,
            instrument_ids=(
                ["equity:CRSP:ALL"]
                if universe.universe_type == "HISTORICAL_EQUITY_PIT"
                else universe.parameters.get("instrument_ids")
                or universe.parameters.get("candidate_instrument_ids")
                or []
            ),
        )
        catalog = DatasetCatalogService(get_default_store())
        manifests = []
        for manifest_id in payload.get("manifest_ids") or []:
            manifest = catalog.get_manifest(str(manifest_id))
            if manifest is None:
                raise ValueError(f"dataset Manifest not found: {manifest_id}")
            manifests.append(manifest)
        result = UniverseService(get_default_store()).resolve_snapshot(
            universe_definition_id=universe_definition_id,
            as_of_time=str(payload.get("as_of_time") or ""),
            manifests=manifests,
        )
        data = {**asdict(result), "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.universe.snapshot.create",
            target_type="universe_snapshot", target_id=result.universe_snapshot_id, payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/definitions")
@require_local_request
@debug_timing("agent_research_definition_create")
def agent_research_definition_create(project_id: str):
    payload = _agent_body_payload()
    try:
        definition_type = str(payload.get("definition_type") or "").upper()
        operation = "FACTOR_CREATE" if definition_type == "FACTOR" else "ALPHA_CREATE"
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, operation, "research.definition.create"
        )
        registry = DefinitionRegistry(get_default_store())
        definition_spec = dict(payload.get("spec") or {})
        if definition_type == "ALPHA":
            for component in definition_spec.get("components") or []:
                ref_id = str(component.get("factor_definition_id") or "").strip()
                ref_version = str(
                    component.get("factor_version") or component.get("version") or ""
                ).strip()
                if not ref_id or not ref_version:
                    raise ValueError("Alpha components require factor_definition_id and factor_version")
                factor = registry.get(ref_id, version=ref_version)
                if factor is None:
                    raise ValueError(f"Factor definition not found: {ref_id}@{ref_version}")
                if factor.library_scope == "PROJECT" and factor.owner_project_id != project_id:
                    raise ResearchAuthorizationError(
                        "RESEARCH_DEFINITION_OUT_OF_SCOPE",
                        "Alpha may reference only Global Factors or Factors owned by this Project",
                    )
        result = registry.create(
            definition_type,
            definition_spec,
            state="DRAFT",
            created_by=actor_id,
            owner_project_id=project_id,
            library_scope="PROJECT",
        )
        if result.library_scope == "PROJECT" and result.owner_project_id != project_id:
            raise ResearchAuthorizationError(
                "RESEARCH_DEFINITION_OUT_OF_SCOPE",
                "An identical Project-scoped definition belongs to another Project",
            )
        data = {**result.to_dict(), "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.definition.create",
            target_type=f"research_{definition_type.lower()}_definition",
            target_id=result.definition_id, payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/definitions/<definition_id>/validate")
@require_local_request
@debug_timing("agent_research_definition_validate")
def agent_research_definition_validate(project_id: str, definition_id: str):
    payload = _agent_body_payload()
    try:
        registry = DefinitionRegistry(get_default_store())
        current = registry.get(definition_id)
        if current is None or current.owner_project_id != project_id or current.library_scope != "PROJECT":
            raise ResearchAuthorizationError(
                "RESEARCH_DEFINITION_OUT_OF_SCOPE",
                "Agent may validate only Project-scoped definitions it created inside this Project",
            )
        operation = "FACTOR_VALIDATE" if current.definition_type == "FACTOR" else "ALPHA_VALIDATE"
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, operation, "research.definition.validate"
        )
        result = registry.validate(definition_id)
        data = {**result.to_dict(), "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.definition.validate",
            target_type=f"research_{current.definition_type.lower()}_definition",
            target_id=definition_id, payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _agent_research_error(exc)


@app.put("/api/agent/research/projects/<project_id>/definition-refs/<slot_key>")
@require_local_request
@debug_timing("agent_research_definition_pin")
def agent_research_definition_pin(project_id: str, slot_key: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "PROJECT_PIN", "research.project.pin"
        )
        if not bool(decision.grant.get("scope", {}).get("allow_project_pin", True)):
            raise ResearchAuthorizationError("RESEARCH_PROJECT_PIN_DENIED", "Project Pin is disabled in Grant scope")
        definition = DefinitionRegistry(get_default_store()).get(str(payload.get("definition_id") or ""))
        if definition is None or (
            definition.library_scope == "PROJECT" and definition.owner_project_id != project_id
        ):
            raise ResearchAuthorizationError("RESEARCH_DEFINITION_OUT_OF_SCOPE", "Definition is outside Project scope")
        result = DefinitionRegistry(get_default_store()).set_project_ref(
            project_id=project_id,
            slot_key=slot_key,
            definition_id=definition.definition_id,
            definition_version=str(payload.get("definition_version") or ""),
            reference_mode=str(payload.get("reference_mode") or "PINNED"),
        )
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.project.pin",
            target_type="project_definition_ref", target_id=f"{project_id}:{slot_key}", payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _agent_research_error(exc)


@app.delete("/api/agent/research/projects/<project_id>/definition-refs/<slot_key>")
@require_local_request
@debug_timing("agent_research_definition_unpin")
def agent_research_definition_unpin(project_id: str, slot_key: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "PROJECT_UNPIN", "research.project.unpin"
        )
        result = DefinitionRegistry(get_default_store()).remove_project_ref(
            project_id=project_id,
            slot_key=slot_key,
            expected_definition_id=str(payload.get("expected_definition_id") or ""),
        )
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.project.unpin",
            target_type="project_definition_ref", target_id=f"{project_id}:{slot_key}", payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _agent_research_error(exc)


@app.delete("/api/agent/research/projects/<project_id>/universes/<universe_id>")
@require_local_request
@debug_timing("agent_research_universe_unbind")
def agent_research_universe_unbind(project_id: str, universe_id: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "UNIVERSE_UNBIND", "research.universe.unbind",
            universe_definition_id=universe_id,
        )
        result = SharedUniverseService(get_default_store()).remove_binding(
            project_id=project_id, universe_id=universe_id
        )
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.universe.unbind",
            target_type="shared_universe_binding", target_id=f"{project_id}:{universe_id}", payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _agent_research_error(exc)


@app.delete("/api/agent/research/projects/<project_id>/universe-ref")
@require_local_request
@debug_timing("agent_research_universe_ref_remove")
def agent_research_universe_ref_remove(project_id: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "UNIVERSE_UNBIND", "research.universe.unbind"
        )
        result = UniverseService(get_default_store()).remove_research_ref(project_id=project_id)
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.universe.unbind",
            target_type="research_universe_ref", target_id=project_id, payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _agent_research_error(exc)


@app.put("/api/agent/research/projects/<project_id>/universe-ref")
@require_local_request
@debug_timing("agent_research_universe_ref_set")
def agent_research_universe_ref_set(project_id: str):
    payload = _agent_body_payload()
    try:
        snapshot_id = str(payload.get("universe_snapshot_id") or "").strip()
        snapshot = UniverseService(get_default_store()).get_snapshot(snapshot_id)
        if snapshot is None:
            raise ValueError("universe snapshot not found")
        definition = UniverseService(get_default_store()).get_definition(
            snapshot.universe_definition_id
        )
        if definition is None or (
            definition.library_scope == "PROJECT"
            and definition.owner_project_id != project_id
        ):
            raise ResearchAuthorizationError(
                "RESEARCH_UNIVERSE_OUT_OF_SCOPE",
                "Universe Snapshot is outside Project scope",
            )
        actor_type, actor_id, decision = _agent_research_authorize(
            payload,
            project_id,
            "PROJECT_PIN",
            "research.project.pin",
            universe_definition_id=definition.universe_definition_id,
            universe_snapshot_id=snapshot_id,
        )
        if not bool(decision.grant.get("scope", {}).get("allow_project_pin", True)):
            raise ResearchAuthorizationError(
                "RESEARCH_PROJECT_PIN_DENIED",
                "Project Pin is disabled in Grant scope",
            )
        result = UniverseService(get_default_store()).set_research_ref(
            project_id=project_id,
            universe_snapshot_id=snapshot_id,
            library_asset_id=str(payload.get("library_asset_id") or ""),
        )
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="research.project.pin",
            target_type="research_universe_ref",
            target_id=project_id,
            payload=payload,
            output=data,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _agent_research_error(exc)


@app.delete("/api/agent/research/projects/<project_id>/requirements/items/<ref_id>")
@require_local_request
@debug_timing("agent_research_requirement_remove")
def agent_research_requirement_remove(project_id: str, ref_id: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "REQUIREMENT_REMOVE", "research.requirement.remove"
        )
        RequirementWorkspaceService(get_default_store()).remove_project_item(project_id, ref_id)
        data = {"removed": True, "project_id": project_id, "ref_id": ref_id, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.requirement.remove",
            target_type="project_requirement_item", target_id=f"{project_id}:{ref_id}", payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _agent_research_error(exc)


@app.delete("/api/agent/research/projects/<project_id>/library/<library_asset_id>/archive")
@require_local_request
@debug_timing("agent_research_library_archive")
def agent_research_library_archive(project_id: str, library_asset_id: str):
    """Archive a published Library asset (Universe/Factor/Alpha).

    A Library asset is not owned by a single Project, but every Agent write
    still requires a Project Research Grant, so the caller must name the
    Project whose Grant authorizes this action.
    """
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "LIBRARY_ARCHIVE", "research.library.archive"
        )
        result = ResearchLibraryService(get_default_store()).archive(library_asset_id)
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.library.archive",
            target_type="research_library_asset", target_id=library_asset_id, payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/requirement-sets")
@require_local_request
@debug_timing("agent_research_requirement_compile")
def agent_research_requirement_compile(project_id: str):
    payload = _agent_body_payload()
    try:
        context = dict(payload.get("context") or {})
        project_refs = DefinitionRegistry(get_default_store()).list_project_refs(project_id)
        pinned_factor_specs = [
            item["spec"] for item in project_refs.values()
            if item["definition_type"] == "FACTOR" and item["reference_mode"] == "PINNED"
        ]
        requested_factor_specs = payload.get("factor_specs") or pinned_factor_specs
        pinned_identities = {
            (str(item.get("name") or ""), str(item.get("version") or ""))
            for item in pinned_factor_specs
        }
        requested_identities = {
            (str(item.get("name") or ""), str(item.get("version") or ""))
            for item in requested_factor_specs
        }
        if not requested_factor_specs or not requested_identities.issubset(pinned_identities):
            raise ResearchAuthorizationError(
                "RESEARCH_DEFINITION_OUT_OF_SCOPE",
                "Requirements may use only Factors PINNED to this Project",
            )
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "REQUIREMENT_COMPILE", "research.requirement.compile",
            providers=payload.get("providers") or (
                (context.get("source_selection_policy") or {}).get("allowed_sources")
                or (context.get("source_selection_policy") or {}).get("preferred_sources")
                or ([context.get("provider")] if context.get("provider") else [])
            ),
            instrument_ids=context.get("instrument_ids") or [],
            universe_snapshot_id=str(payload.get("universe_snapshot_id") or ""),
            time_start=str(context.get("history_start") or ""),
            time_end=str(context.get("history_end") or ""),
        )
        result = RequirementCompiler(get_default_store()).compile(
            project_id=project_id,
            factor_specs=requested_factor_specs,
            universe_requirements=payload.get("universe_requirements") or [],
            evaluation_requirements=payload.get("evaluation_requirements") or [],
            backtest_requirements=payload.get("backtest_requirements") or [],
            manual_requirements=payload.get("manual_requirements") or [],
            context=context,
        )
        data = {**asdict(result), "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.requirement.compile",
            target_type="requirement_set", target_id=result.requirement_set_id, payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/run-input-previews")
@require_local_request
@debug_timing("agent_research_preview_create")
def agent_research_preview_create(project_id: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "PREVIEW_CREATE", "research.preview.create",
            providers=(payload.get("source_selection_policy") or {}).get("preferred_sources") or [],
            universe_snapshot_id=str(payload.get("universe_snapshot_id") or ""),
        )
        request_payload = {
            **payload,
            "grant_id": decision.grant["grant_id"],
            "actor_type": "AGENT",
            "actor_id": actor_id,
        }
        result = ResearchRunPreviewService(get_default_store()).create(
            project_id, request_payload, created_by=actor_id
        )
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.preview.create",
            target_type="run_inputs_preview", target_id=result["preview_id"], payload=payload, output=result,
        )
        return jsonify({"ok": True, "data": result}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/equity-monthly-panel")
@require_local_request
@debug_timing("agent_research_equity_monthly_panel")
def agent_research_equity_monthly_panel(project_id: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, decision = _agent_research_authorize(
            payload,
            project_id,
            "BACKFILL_CREATE",
            "research.backfill.create",
            universe_snapshot_id=str(payload.get("universe_snapshot_id") or ""),
            time_start=str(payload.get("start_date") or ""),
            time_end=str(payload.get("end_date") or ""),
        )
        result = EquityMonthlyResearchMaterializer(get_default_store()).materialize(
            project_id=project_id,
            universe_snapshot_id=str(payload.get("universe_snapshot_id") or ""),
            start_date=str(payload.get("start_date") or ""),
            end_date=str(payload.get("end_date") or ""),
            source_manifest_ids=payload.get("source_manifest_ids") or {},
            minimum_listing_age_days=int(payload.get("minimum_listing_age_days") or 365),
        )
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="research.backfill.create",
            target_type="equity_research_monthly_panel",
            target_id=str(result["panel_manifest_id"]),
            payload=payload,
            output=data,
        )
        return jsonify({"ok": True, "data": data}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/backfill-tasks")
@require_local_request
@debug_timing("agent_research_backfill_create")
def agent_research_backfill_create(project_id: str):
    payload = _agent_body_payload()
    try:
        symbol = str(payload.get("symbol") or "").strip().upper()
        interval = str(payload.get("interval") or "").strip().lower()
        instrument_id = str(payload.get("instrument_id") or f"crypto_spot:BINANCE:{symbol}").strip()
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "BACKFILL_CREATE", "research.backfill.create",
            providers=["BINANCE"], intervals=[interval], instrument_ids=[instrument_id],
            time_start=str(payload.get("start_time") or ""),
            time_end=str(payload.get("end_time") or ""),
        )
        workflow_run_id = str(
            payload.get("workflow_run_id")
            or f"agent-backfill:{project_id}:{symbol}:{interval}"
        ).strip()
        tasks = ResearchControlPlane(get_default_store()).compile_tasks(
            project_id=project_id,
            plan_version=int(decision.grant["plan_version"]),
            workflow_run_id=workflow_run_id,
            task_specs=[{
                "task_type": "BINANCE_BARS_BACKFILL",
                "logical_key": str(payload.get("logical_key") or f"{symbol}:{interval}"),
                "idempotency_key": str(
                    payload.get("idempotency_key")
                    or f"{workflow_run_id}:{symbol}:{interval}:{payload.get('start_time')}:{payload.get('end_time')}"
                ),
                "max_attempts": int(payload.get("max_attempts") or 5),
                "timeout_seconds": int(payload.get("timeout_seconds") or 3600),
                "input": {
                    "grant_id": decision.grant["grant_id"],
                    "symbol": symbol,
                    "interval": interval,
                    "start_time": str(payload.get("start_time") or ""),
                    "end_time": str(payload.get("end_time") or ""),
                    "requirement_id": str(payload.get("requirement_id") or ""),
                    "library_asset_id": str(payload.get("library_asset_id") or ""),
                    "page_limit": int(payload.get("page_limit") or 1000),
                    "max_pages_per_attempt": int(payload.get("max_pages_per_attempt") or 20),
                    "budget": dict(payload.get("budget") or {
                        "download_bytes": 20_000_000,
                        "runtime_seconds": 300,
                    }),
                },
            }],
        )
        result = next(item for item in tasks if item["logical_key"] == str(payload.get("logical_key") or f"{symbol}:{interval}"))
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.backfill.create",
            target_type="research_task", target_id=result["task_id"], payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/openbb-export-tasks")
@require_local_request
@debug_timing("agent_research_openbb_export_create")
def agent_research_openbb_export_create(project_id: str):
    payload = _agent_body_payload()
    try:
        symbol = str(payload.get("symbol") or "").strip().upper()
        provider = str(payload.get("provider") or "yfinance").strip().lower()
        interval = str(payload.get("interval") or "1d").strip().lower()
        instrument_id = str(payload.get("instrument_id") or "").strip()
        parts = instrument_id.split(":", 2)
        venue = str(payload.get("venue") or (parts[1] if len(parts) > 1 else "")).strip().upper()
        if not symbol or not instrument_id:
            raise ValueError("OpenBB export requires symbol and instrument_id")
        if interval != "1d":
            raise ValueError("OpenBB equity preparation currently supports 1d bars only")
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "BACKFILL_CREATE", "research.backfill.create",
            providers=[provider], intervals=[interval], instrument_ids=[instrument_id],
            time_start=str(payload.get("start_time") or ""),
            time_end=str(payload.get("end_time") or ""),
        )
        workflow_run_id = str(
            payload.get("workflow_run_id")
            or f"agent-openbb:{project_id}:{provider}:{venue}:{symbol}:{interval}"
        ).strip()
        logical_key = str(
            payload.get("logical_key") or f"{provider}:{venue}:{symbol}:{interval}"
        ).strip()
        adjustment = normalize_equity_adjustment(payload.get("adjustment"))
        start_time = str(payload.get("start_time") or "").strip()
        end_time = str(payload.get("end_time") or "").strip()
        tasks = ResearchControlPlane(get_default_store()).compile_tasks(
            project_id=project_id,
            plan_version=int(decision.grant["plan_version"]),
            workflow_run_id=workflow_run_id,
            task_specs=[{
                "task_type": "OPENBB_EQUITY_DAILY_EXPORT",
                "logical_key": logical_key,
                "idempotency_key": str(
                    payload.get("idempotency_key")
                    or f"{workflow_run_id}:{start_time}:{end_time}:{adjustment}"
                ),
                "max_attempts": int(payload.get("max_attempts") or 3),
                "timeout_seconds": int(payload.get("timeout_seconds") or 3600),
                "input": {
                    "grant_id": decision.grant["grant_id"],
                    "symbol": symbol,
                    "venue": venue,
                    "provider": provider,
                    "instrument_id": instrument_id,
                    "interval": interval,
                    "frequency": interval,
                    "start_date": start_time[:10],
                    "end_date": end_time[:10],
                    "start_time": start_time,
                    "end_time": end_time,
                    "latest_available": bool(payload.get("latest_available", False)),
                    "adjustment": adjustment,
                    "source_policy": {"mode": "FIXED", "providers": [provider]},
                    "requirement_id": str(payload.get("requirement_id") or ""),
                    "library_asset_id": str(payload.get("library_asset_id") or ""),
                    "budget": dict(payload.get("budget") or {
                        "download_bytes": 20_000_000,
                        "runtime_seconds": 300,
                    }),
                },
            }],
        )
        result = next(item for item in tasks if item["logical_key"] == logical_key)
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.backfill.create",
            target_type="research_task", target_id=result["task_id"], payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/polymarket-export-tasks")
@require_local_request
@debug_timing("agent_research_polymarket_export_create")
def agent_research_polymarket_export_create(project_id: str):
    payload = _agent_body_payload()
    try:
        instrument_id = str(payload.get("instrument_id") or "").strip()
        interval = str(payload.get("interval") or "1h").strip().lower()
        if not instrument_id.lower().startswith("polymarket_binary:polymarket:"):
            raise ValueError("Polymarket export requires an outcome instrument_id")
        actor_type, actor_id, decision = _agent_research_authorize(
            payload, project_id, "BACKFILL_CREATE", "research.backfill.create",
            providers=["POLYMARKET"], intervals=[interval], instrument_ids=[instrument_id],
            time_start=str(payload.get("start_time") or ""),
            time_end=str(payload.get("end_time") or ""),
        )
        token_id = instrument_id.split(":", 2)[2]
        workflow_run_id = str(
            payload.get("workflow_run_id")
            or f"agent-polymarket:{project_id}:{token_id}:{interval}"
        ).strip()
        logical_key = str(
            payload.get("logical_key") or f"polymarket:{token_id}:{interval}"
        ).strip()
        start_time = str(payload.get("start_time") or "").strip()
        end_time = str(payload.get("end_time") or "").strip()
        tasks = ResearchControlPlane(get_default_store()).compile_tasks(
            project_id=project_id,
            plan_version=int(decision.grant["plan_version"]),
            workflow_run_id=workflow_run_id,
            task_specs=[{
                "task_type": "POLYMARKET_PRICE_HISTORY_EXPORT",
                "logical_key": logical_key,
                "idempotency_key": str(
                    payload.get("idempotency_key")
                    or f"{workflow_run_id}:{start_time}:{end_time}"
                ),
                "max_attempts": int(payload.get("max_attempts") or 3),
                "timeout_seconds": int(payload.get("timeout_seconds") or 3600),
                "input": {
                    "grant_id": decision.grant["grant_id"],
                    "instrument_id": instrument_id,
                    "condition_id": str(payload.get("condition_id") or ""),
                    "interval": interval,
                    "start_time": start_time,
                    "end_time": end_time,
                    "latest_available": bool(payload.get("latest_available", False)),
                    "requirement_id": str(payload.get("requirement_id") or ""),
                    "library_asset_id": str(payload.get("library_asset_id") or ""),
                    "budget": dict(payload.get("budget") or {
                        "download_bytes": 20_000_000,
                        "runtime_seconds": 300,
                    }),
                },
            }],
        )
        result = next(item for item in tasks if item["logical_key"] == logical_key)
        data = {**result, "authorization": decision.to_dict()}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.backfill.create",
            target_type="research_task", target_id=result["task_id"], payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data}), 201
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/runs")
@require_local_request
@debug_timing("agent_research_run_create")
def agent_research_run_create(project_id: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, _decision = _agent_research_authorize(
            payload, project_id, "RUN_CREATE", "research.run.create"
        )
        preview = ResearchRunPreviewService(get_default_store()).get(str(payload.get("preview_id") or ""))
        if preview is None or str(preview.get("project_id")) != project_id:
            raise ResearchAuthorizationError("RESEARCH_RUN_PROJECT_MISMATCH", "Preview belongs to another Project")
        result = ResearchRunService(get_default_store()).create(
            preview_id=str(payload.get("preview_id") or ""),
            preview_fingerprint=str(payload.get("preview_fingerprint") or ""),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            actor_id=actor_id,
            actor_type="AGENT",
        )
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.run.create",
            target_type="research_run", target_id=result["run_id"], payload=payload, output=result,
        )
        return jsonify({"ok": True, "data": result}), 201
    except IdempotencyConflictError as exc:
        return jsonify({"ok": False, "code": exc.code, "error": str(exc)}), 409
    except PreviewStaleError as exc:
        return jsonify({"ok": False, "code": exc.code, "error": str(exc)}), 409
    except ReadinessBlockedError as exc:
        return jsonify({"ok": False, "code": exc.code, "error": str(exc)}), 422
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/run-worker/run-once")
@require_local_request
@debug_timing("agent_research_run_execute")
def agent_research_run_execute(project_id: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, _decision = _agent_research_authorize(
            payload, project_id, "RUN_EXECUTE", "research.run.execute"
        )
        dispatched = _dispatch_research_run_once()
        project_runs = ResearchRunService(get_default_store()).list(
            project_id=project_id, limit=20
        )
        active = next(
            (
                item for item in project_runs
                if str(item.get("status") or "").upper() in {"QUEUED", "RUNNING"}
            ),
            None,
        )
        data = active or {"status": "IDLE", "project_id": project_id}
        data = {**data, "dispatch_accepted": dispatched, "execution_mode": "DURABLE_QUEUE"}
        _audit_agent_research(
            actor_type=actor_type, actor_id=actor_id, capability="research.run.execute",
            target_type="research_run", target_id=str(data.get("run_id") or project_id), payload=payload, output=data,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _agent_research_error(exc)


@app.post("/api/agent/research/projects/<project_id>/budget-reconcile")
@require_local_request
@debug_timing("agent_research_budget_reconcile")
def agent_research_budget_reconcile(project_id: str):
    payload = _agent_body_payload()
    try:
        actor_type, actor_id, _decision = _agent_research_authorize(
            payload, project_id, "RUN_EXECUTE", "research.run.execute"
        )
        result = ResearchRunWorker.reconcile_project_runtime_budget(
            get_default_store(), project_id
        )
        _audit_agent_research(
            actor_type=actor_type,
            actor_id=actor_id,
            capability="research.run.execute",
            target_type="research_budget_ledger",
            target_id=project_id,
            payload=payload,
            output=result,
        )
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _agent_research_error(exc)


@app.get("/api/agent/market-categories")
@debug_timing("agent_market_categories")
def agent_market_categories():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_market_categories(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/markets")
@debug_timing("agent_market_search")
def agent_market_search():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_search_markets(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/markets/resolve")
@debug_timing("agent_market_resolve")
def agent_market_resolve():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_resolve_market(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/market-scan")
@debug_timing("agent_market_scan")
def agent_market_scan():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_hot_market_scan(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/market-scan/propose-strategies")
@debug_timing("agent_market_scan_propose")
def agent_market_scan_propose():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.propose_strategies_from_market_scan(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph")
@debug_timing("agent_event_graph")
def agent_event_graph_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_event_graph(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/news/status")
@debug_timing("agent_event_news_status")
def agent_event_news_status_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_event_news_status(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/events")
@debug_timing("agent_event_graph_events")
def agent_event_graph_events_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_event_graph_events(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/observations")
@debug_timing("agent_event_graph_observations")
def agent_event_graph_observations_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_event_graph_observations(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/event-graph/news/refresh")
@debug_timing("agent_event_news_refresh")
def agent_event_news_refresh_api():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_refresh_event_news(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/event-graph/news/search")
@debug_timing("agent_event_news_search")
def agent_event_news_search_api():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_search_event_news(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)


@app.post("/api/agent/event-graph/patches/validate")
@debug_timing("agent_event_graph_patch_validate")
def agent_event_graph_patch_validate_api():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_validate_event_graph_patch(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/event-graph/change-requests")
@debug_timing("agent_event_graph_change_request")
def agent_event_graph_change_request_api():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_submit_change_request(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/change-requests")
@debug_timing("agent_event_graph_change_requests_list")
def agent_event_graph_change_requests_list_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_change_requests(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/change-requests/<request_id>")
@debug_timing("agent_event_graph_change_request_detail")
def agent_event_graph_change_request_detail_api(request_id: str):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_change_request(request_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/events")
@debug_timing("agent_event_graph_core_events")
def agent_event_graph_core_events_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_events(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core")
@debug_timing("agent_event_graph_core")
def agent_event_graph_core_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/finance")
@debug_timing("agent_event_graph_core_finance")
def agent_event_graph_core_finance_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_finance_nodes(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/edges")
@debug_timing("agent_event_graph_core_edges")
def agent_event_graph_core_edges_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_edges(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/expressions")
@debug_timing("agent_event_graph_core_expressions")
def agent_event_graph_core_expressions_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_expressions(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/event-graph/core/versions")
@debug_timing("agent_event_graph_core_versions")
def agent_event_graph_core_versions_api():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_graph_core_versions(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/approve")
@debug_timing("event_graph_change_request_approve")
def event_graph_change_request_approve_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify(
            {
                "ok": True,
                "data": agent_service.human_review_event_graph_change_request(
                    request_id,
                    payload,
                    decision="approve",
                ),
            }
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/approve-and-apply")
@debug_timing("event_graph_change_request_approve_and_apply")
def event_graph_change_request_approve_and_apply_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.human_approve_and_apply_event_graph_change_request(request_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/reject")
@debug_timing("event_graph_change_request_reject")
def event_graph_change_request_reject_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify(
            {
                "ok": True,
                "data": agent_service.human_review_event_graph_change_request(
                    request_id,
                    payload,
                    decision="reject",
                ),
            }
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/request-changes")
@debug_timing("event_graph_change_request_needs_changes")
def event_graph_change_request_needs_changes_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify(
            {
                "ok": True,
                "data": agent_service.human_review_event_graph_change_request(
                    request_id,
                    payload,
                    decision="request_changes",
                ),
            }
        )
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/event-graph/change-requests/<request_id>/apply")
@debug_timing("event_graph_change_request_apply")
def event_graph_change_request_apply_api(request_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.human_apply_event_graph_change_request(request_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/dashboard")
@debug_timing("agent_dashboard")
def agent_dashboard():
    try:
        limit = request.args.get("limit", "20")
        try:
            limit_num = max(1, min(int(limit), 100))
        except ValueError:
            limit_num = 20
        return jsonify({"ok": True, "data": agent_service.dashboard(limit=limit_num)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/activity")
@debug_timing("agent_activity_list")
def agent_activity_list():
    try:
        limit = request.args.get("limit", "50")
        try:
            limit_num = max(1, min(int(limit), 200))
        except ValueError:
            limit_num = 50
        return jsonify({"ok": True, "data": agent_service.list_activity(limit=limit_num)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/activity")
@debug_timing("agent_activity_create")
def agent_activity_create():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.create_activity(payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategy-drafts")
@debug_timing("agent_drafts_list")
def agent_strategy_drafts_list():
    try:
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify({"ok": True, "data": agent_service.list_drafts(limit=limit_num, payload=_agent_query_payload())})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/strategy-drafts")
@debug_timing("agent_drafts_create")
def agent_strategy_drafts_create():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.create_draft(payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategy-drafts/<draft_id>")
@debug_timing("agent_drafts_get")
def agent_strategy_drafts_get(draft_id: str):
    try:
        result = agent_service.get_draft(draft_id, _agent_query_payload())
        if not result:
            return jsonify({"ok": False, "error": "draft not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/agent/strategy-drafts/<draft_id>")
@debug_timing("agent_drafts_update")
def agent_strategy_drafts_update(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.update_draft(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/agent/strategy-drafts/<draft_id>")
@debug_timing("agent_drafts_delete")
def agent_strategy_drafts_delete(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.delete_draft(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/strategy-drafts/<draft_id>/risk-check")
@debug_timing("agent_drafts_risk")
def agent_strategy_drafts_risk(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.risk_check(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/strategy-drafts/<draft_id>/simulate")
@debug_timing("agent_drafts_simulate")
def agent_strategy_drafts_simulate(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.simulate_draft(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/strategy-drafts/<draft_id>/submit")
@debug_timing("agent_drafts_submit")
def agent_strategy_drafts_submit(draft_id: str):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.submit_draft(draft_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/approvals")
@debug_timing("agent_approvals_list")
def agent_approvals_list():
    try:
        status = request.args.get("status", "")
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify({"ok": True, "data": agent_service.list_approvals(status=status, limit=limit_num, payload=_agent_query_payload())})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/approvals/<approval_id>")
@debug_timing("agent_approvals_get")
def agent_approvals_get(approval_id: str):
    try:
        result = agent_service.get_approval(approval_id, _agent_query_payload())
        if not result:
            return jsonify({"ok": False, "error": "approval not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/agent/approvals/<approval_id>/draft")
@debug_timing("agent_approvals_update_draft")
def agent_approvals_update_draft(approval_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.update_approval_draft(approval_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/approvals/<approval_id>/approve")
@debug_timing("agent_approvals_approve")
def agent_approvals_approve(approval_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.approve_approval(approval_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/approvals/<approval_id>/reject")
@debug_timing("agent_approvals_reject")
def agent_approvals_reject(approval_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.reject_approval(approval_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/approvals/<approval_id>/request-changes")
@debug_timing("agent_approvals_changes")
def agent_approvals_changes(approval_id: str):
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.request_changes(approval_id, payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/audit")
@debug_timing("agent_audit")
def agent_audit_list():
    try:
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify({"ok": True, "data": agent_service.list_audit(limit=limit_num, payload=_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/runs")
@debug_timing("agent_runs")
def agent_runs_list():
    try:
        limit = request.args.get("limit", "100")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 100
        return jsonify({"ok": True, "data": agent_service.list_runs(limit=limit_num, payload=_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/runs/<run_id>/steps")
@debug_timing("agent_run_steps")
def agent_run_steps_list(run_id: str):
    try:
        limit = request.args.get("limit", "200")
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 200
        return jsonify({"ok": True, "data": agent_service.list_run_steps(run_id, limit=limit_num, payload=_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


def _require_inspection_read(payload: dict) -> None:
    actor_type = str(payload.get("actor_type") or "agent").strip().lower()
    agent_service.require_agent_capability("audit.read", actor_type)


@app.get("/api/agent/inspection/traces")
@debug_timing("agent_inspection_traces")
def agent_inspection_traces_list():
    try:
        payload = _agent_query_payload()
        _require_inspection_read(payload)
        return jsonify({
            "ok": True,
            "data": inspection_service.list_traces(
                limit=request.args.get("limit", 50),
                cursor=request.args.get("cursor", ""),
                subject_type=request.args.get("subject_type", ""),
                subject_id=request.args.get("subject_id", ""),
                status=request.args.get("status", ""),
                q=request.args.get("q", ""),
                include_hidden=(
                    str(request.args.get("include_hidden", "")).lower() in {"1", "true", "yes"}
                    and str(payload.get("actor_type") or "").lower() == "human"
                ),
            ),
        })
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/inspection/traces/<trace_id>")
@debug_timing("agent_inspection_trace")
def agent_inspection_trace_detail(trace_id: str):
    try:
        payload = _agent_query_payload()
        _require_inspection_read(payload)
        return jsonify({"ok": True, "data": inspection_service.get_trace(trace_id)})
    except ValueError as exc:
        return _json_error(exc, 404 if "not found" in str(exc).lower() else 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/inspection/traces/<trace_id>/events")
@debug_timing("agent_inspection_events")
def agent_inspection_events_list(trace_id: str):
    try:
        payload = _agent_query_payload()
        _require_inspection_read(payload)
        return jsonify({
            "ok": True,
            "data": inspection_service.list_events(
                trace_id,
                limit=request.args.get("limit", 100),
                cursor=request.args.get("cursor", 0),
                event_kind=request.args.get("event_kind", ""),
                status=request.args.get("status", ""),
                severity=request.args.get("severity", ""),
                q=request.args.get("q", ""),
            ),
        })
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/inspection/traces/<trace_id>/search")
@debug_timing("agent_inspection_search")
def agent_inspection_events_search(trace_id: str):
    try:
        payload = _agent_query_payload()
        _require_inspection_read(payload)
        return jsonify({
            "ok": True,
            "data": inspection_service.search_events(
                trace_id,
                request.args.get("q", ""),
                limit=request.args.get("limit", 50),
            ),
        })
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/inspection/events/<event_id>")
@debug_timing("agent_inspection_event")
def agent_inspection_event_detail(event_id: str):
    try:
        payload = _agent_query_payload()
        _require_inspection_read(payload)
        return jsonify({"ok": True, "data": inspection_service.get_event(event_id)})
    except ValueError as exc:
        return _json_error(exc, 404 if "not found" in str(exc).lower() else 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/agent/audit")
@debug_timing("agent_audit_clear")
def agent_audit_clear():
    try:
        payload = _agent_body_payload(default_type="human", default_id="local_user")
        return jsonify({"ok": True, "data": agent_service.clear_audit(payload)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/cases")
@debug_timing("agent_backtest_cases")
def agent_backtest_cases():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_backtest_cases(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/backtests/cases")
@debug_timing("agent_backtest_case_create")
def agent_backtest_case_create():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_create_backtest_case(payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/runs")
@debug_timing("agent_backtest_runs")
def agent_backtest_runs():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_backtest_runs(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/backtests/cases/<int:case_id>/runs")
@debug_timing("agent_backtest_run_create")
def agent_backtest_run_create(case_id: int):
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_create_backtest_run(case_id, payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/runs/<int:run_id>")
@debug_timing("agent_backtest_run")
def agent_backtest_run(run_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_backtest_run(run_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 404 if "not found" in str(exc).lower() else 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/batches")
@debug_timing("agent_backtest_batches")
def agent_backtest_batches():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_backtest_batches(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/agent/backtests/batches")
@debug_timing("agent_backtest_batch_create")
def agent_backtest_batch_create():
    try:
        payload = _agent_body_payload()
        return jsonify({"ok": True, "data": agent_service.agent_create_backtest_batch(payload)}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/backtests/batches/<batch_id>")
@debug_timing("agent_backtest_batch")
def agent_backtest_batch(batch_id: str):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_backtest_batch(batch_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 404 if "not found" in str(exc).lower() else 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies")
@debug_timing("agent_strategies_list")
def agent_strategies_list():
    try:
        return jsonify({"ok": True, "data": agent_service.agent_list_strategies(_agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>")
@debug_timing("agent_strategy_detail")
def agent_strategy_detail(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>/workspace")
@debug_timing("agent_strategy_workspace")
def agent_strategy_workspace(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy_workspace(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>/usedata")
@debug_timing("agent_strategy_usedata")
def agent_strategy_usedata(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy_usedata(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>/events")
@debug_timing("agent_strategy_events")
def agent_strategy_events(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy_events(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/agent/strategies/<int:strategy_id>/state")
@debug_timing("agent_strategy_state")
def agent_strategy_state(strategy_id: int):
    try:
        return jsonify({"ok": True, "data": agent_service.agent_get_strategy_state(strategy_id, _agent_query_payload())})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies")
@debug_timing("strategies")
def polymarket_strategies():
    try:
        limit = request.args.get("limit", "30")
        sync_stats = request.args.get("sync_stats", "0") == "1"
        try:
            limit_num = max(1, min(int(limit), 500))
        except ValueError:
            limit_num = 30
        return jsonify(fetch_strategy_monitoring(limit=limit_num, sync_stats=sync_stats))
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>")
@debug_timing("strategy_detail")
def polymarket_strategy_detail(row_id: int):
    try:
        return jsonify({"ok": True, "data": fetch_strategy_detail(row_id)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/strategies/<int:row_id>")
def polymarket_strategy_update(row_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "data": update_strategy_settings(row_id, payload)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/workspace-presets")
def polymarket_workspace_presets():
    row_id = request.args.get("row_id")
    try:
        row_id_num = int(row_id) if row_id else None
    except ValueError:
        row_id_num = None
    try:
        return jsonify({"ok": True, "data": list_workspace_presets(row_id_num)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/workspace-presets")
def polymarket_workspace_presets_save():
    payload = request.get_json(silent=True) or {}
    row_id = payload.get("strategy_row_id")
    try:
        row_id_num = int(row_id) if row_id not in (None, "") else None
    except (TypeError, ValueError):
        row_id_num = None
    try:
        return jsonify({"ok": True, "data": save_workspace_preset(row_id_num, payload)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/workspace-presets/<int:preset_id>")
def polymarket_workspace_preset_detail(preset_id: int):
    try:
        return jsonify({"ok": True, "data": get_workspace_preset(preset_id)})
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/polymarket/workspace-presets/<int:preset_id>")
def polymarket_workspace_preset_delete(preset_id: int):
    try:
        return jsonify({"ok": True, "data": delete_workspace_preset(preset_id)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/workspace")
@debug_timing("workspace")
def polymarket_strategy_workspace(row_id: int):
    try:
        include_events = request.args.get("include_events", "0") == "1"
        backtest_run_id = request.args.get("backtest_run_id") or request.args.get("run_id")
        if request.args.get("source") != "backtest":
            backtest_run_id = None
        return jsonify({"ok": True, "data": get_strategy_workspace(row_id, include_events=include_events, backtest_run_id=backtest_run_id)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/usedata")
@debug_timing("strategy_usedata")
def polymarket_strategy_usedata(row_id: int):
    try:
        include_live_orderbook = str(request.args.get("live_orderbook", "1")).lower() not in {"0", "false", "no"}
        return jsonify({
            "ok": True,
            "data": get_strategy_usedata_snapshot(row_id, include_live_orderbook=include_live_orderbook),
        })
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/strategies/usedata/draft")
@debug_timing("strategy_usedata_draft")
def polymarket_strategy_usedata_draft():
    try:
        include_live_orderbook = str(request.args.get("live_orderbook", "1")).lower() not in {"0", "false", "no"}
        return jsonify({
            "ok": True,
            "data": get_strategy_usedata_draft(
                request.get_json(silent=True) or {},
                include_live_orderbook=include_live_orderbook,
            ),
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/backtest")
def polymarket_strategy_backtest(row_id: int):
    try:
        return jsonify({"ok": True, "data": get_strategy_backtest(row_id)})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/polymarket/strategies/<int:row_id>/backtest")
def polymarket_strategy_backtest_create(row_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, "data": create_strategy_backtest(row_id, payload)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/backtest/results")
def polymarket_strategy_backtest_results(row_id: int):
    try:
        run_id = request.args.get("run_id")
        return jsonify({
            "ok": True,
            "data": get_strategy_backtest_results(row_id, int(run_id) if run_id else None),
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/chart")
@debug_timing("chart")
def polymarket_strategy_chart(row_id: int):
    try:
        return jsonify({"ok": True, "data": get_strategy_chart(row_id, request.args)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/chart-history")
@debug_timing("chart_history")
def polymarket_strategy_chart_history(row_id: int):
    """Read-only history window used for seamless left-edge chart prefetch."""
    try:
        return jsonify({"ok": True, "data": get_strategy_chart(row_id, request.args)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/chart-delta")
@debug_timing("chart_delta")
def polymarket_strategy_chart_delta(row_id: int):
    try:
        return jsonify({"ok": True, "data": get_strategy_chart_delta(row_id, request.args)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/events")
@debug_timing("events")
def polymarket_strategy_events(row_id: int):
    try:
        data = list_strategy_events(row_id, request.args)
        compact = str(request.args.get("compact") or "").strip().lower() in {"1", "true", "yes", "on"}
        if compact:
            compact_items = []
            for item in data.get("data") or []:
                payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                compact_items.append({
                    key: value
                    for key, value in {
                        "id": item.get("id"),
                        "ts": item.get("ts"),
                        "event_type": item.get("event_type") or item.get("type"),
                        "event_subtype": item.get("event_subtype") or item.get("subtype"),
                        "summary": item.get("summary") or item.get("label"),
                        "severity": item.get("severity"),
                        "source": item.get("source") or item.get("env"),
                        "leg": item.get("leg", payload.get("leg", payload.get("leg_index"))),
                        "related_id": item.get("related_id") or item.get("correlation_id") or item.get("order_id") or payload.get("order_id"),
                    }.items()
                    if value not in (None, "")
                })
            data = {**data, "data": compact_items}
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/polymarket/strategies/<int:row_id>/events/<path:event_id>")
@debug_timing("event_detail")
def polymarket_strategy_event_detail(row_id: int, event_id: str):
    try:
        data = list_strategy_events(row_id, {"limit": 500})
        item = next((entry for entry in (data.get("data") or []) if str(entry.get("id") or "") == str(event_id)), None)
        if item is None:
            return jsonify({"ok": False, "error": "event not found"}), 404
        return jsonify({"ok": True, "data": item})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/live/strategies/<int:row_id>/workspace")
def live_strategy_workspace(row_id: int):
    def _sse(event_name: str, payload) -> str:
        return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def event_stream():
        last_event_ts = ""
        last_event_type = ""
        include_events = str(request.args.get("include_events") or "").strip().lower() in {"1", "true", "yes", "on"}
        deadline = time.time() + 3600
        while time.time() < deadline:
            try:
                detail = fetch_strategy_detail(
                    row_id,
                    allow_remote_positions=False,
                    allow_clob_book=False,
                )
                latest_event = None
                if include_events:
                    events_payload = list_strategy_events(row_id, {"limit": 1}, detail=detail)
                    latest_event = (events_payload.get("data") or [None])[0]

                yield _sse(
                    "summary",
                    {
                        "type": "workspace_snapshot",
                        "summary": {
                            "yes_bid": detail.get("yes_bid"),
                            "yes_ask": detail.get("yes_ask"),
                            "no_bid": detail.get("no_bid"),
                            "no_ask": detail.get("no_ask"),
                            "yes_qty": detail.get("yes_qty"),
                            "no_qty": detail.get("no_qty"),
                            "yes_avg": detail.get("yes_avg"),
                            "no_avg": detail.get("no_avg"),
                            "yes_position": detail.get("yes_position"),
                            "no_position": detail.get("no_position"),
                            "strategy_pnl": detail.get("strategy_pnl"),
                            "strategy_bankroll": detail.get("strategy_bankroll"),
                            "market_updated_at": detail.get("market_updated_at"),
                            "price_source": detail.get("price_source"),
                        },
                    },
                )

                if latest_event:
                    next_ts = str(latest_event.get("ts") or "")
                    next_type = str(latest_event.get("event_type") or latest_event.get("type") or "")
                    if next_ts != last_event_ts or next_type != last_event_type:
                        last_event_ts = next_ts
                        last_event_type = next_type
                        yield _sse("event_append", latest_event)
            except GeneratorExit:
                return
            except Exception as exc:
                yield _sse("error", {"error": str(exc)})

            time.sleep(3)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/live/strategies")
def live_strategies():
    limit = request.args.get("limit", "30")
    try:
        limit_num = max(1, min(int(limit), 200))
    except ValueError:
        limit_num = 30

    def _sse(event_name: str, payload) -> str:
        return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def event_stream():
        deadline = time.time() + 3600
        while time.time() < deadline:
            try:
                payload = fetch_strategy_monitoring(limit=limit_num, sync_stats=False)
                rows = payload.get("data", [])
                valid_modes = {"Stop", "Virtual", "Real"}

                def _machine_state_from_row(row):
                    value = row.get("machine_state") or row.get("state") or "auto"
                    return "auto" if value in valid_modes else value

                light_rows = [
                    {
                        "row_id": row.get("row_id"),
                        "strategy_id": row.get("strategy_id"),
                        "display_name": row.get("display_name"),
                        "strategy": row.get("strategy"),
                        "question": row.get("question"),
                        "condition_id": row.get("condition_id"),
                        "yes_token": row.get("yes_token"),
                        "no_token": row.get("no_token"),
                        "slug": row.get("slug"),
                        "event_slug": row.get("event_slug"),
                        "group_item_title": row.get("group_item_title"),
                        "url": row.get("url"),
                        "score": row.get("score"),
                        "yes_ask": row.get("yes_ask"),
                        "yes_bid": row.get("yes_bid"),
                        "no_ask": row.get("no_ask"),
                        "no_bid": row.get("no_bid"),
                        "yes_qty": row.get("yes_qty"),
                        "yes_avg": row.get("yes_avg"),
                        "no_qty": row.get("no_qty"),
                        "no_avg": row.get("no_avg"),
                        "strategy_bankroll": row.get("strategy_bankroll"),
                        "yes_position": row.get("yes_position"),
                        "no_position": row.get("no_position"),
                        "strategy_pnl": row.get("strategy_pnl"),
                        "strategy_code": row.get("strategy_code"),
                        "strategy_name": row.get("strategy_name"),
                        "legs_count": row.get("legs_count"),
                        "legs_snapshot": row.get("legs_snapshot"),
                        "exposure": row.get("exposure"),
                        "last_action": row.get("last_action"),
                        "last_action_type": row.get("last_action_type"),
                        "updated_at": row.get("updated_at"),
                        "recent_events": row.get("recent_events"),
                        "profit": row.get("profit"),
                        "mode": row.get("mode") or (row.get("state") if row.get("state") in valid_modes else "Stop"),
                        "state": _machine_state_from_row(row),
                        "machine_state": _machine_state_from_row(row),
                        "state_options": row.get("state_options"),
                        "is_virtual": row.get("is_virtual"),
                        "editable": row.get("editable"),
                    }
                    for row in rows
                ]
                yield _sse(
                    "rows",
                    {
                        "rows": light_rows,
                        "ok": payload.get("ok"),
                        "status": payload.get("status"),
                        "count": payload.get("count"),
                        "db_path": payload.get("db_path"),
                        "table": payload.get("table"),
                        "snapshot_db_path": payload.get("snapshot_db_path"),
                        "realtime_snapshot_db_path": payload.get("realtime_snapshot_db_path"),
                        "running_strategy_count": payload.get("running_strategy_count"),
                        "total_strategy_profit": payload.get("total_strategy_profit"),
                        "total_strategy_bankroll": payload.get("total_strategy_bankroll"),
                        "total_strategy_return_pct": payload.get("total_strategy_return_pct"),
                        "source_statuses": payload.get("source_statuses"),
                        "strategy_metrics_db_dir": payload.get("strategy_metrics_db_dir"),
                    },
                )
            except GeneratorExit:
                return
            except Exception as exc:
                yield _sse("error", {"error": str(exc)})

            time.sleep(5)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/realtime/state")
def realtime_state():
    try:
        return jsonify({"ok": True, "data": collector.get_state()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/realtime/crypto")
def realtime_crypto():
    try:
        return jsonify({"ok": True, "data": collector.get_state().get("crypto", {})})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/realtime/finance")
def realtime_finance():
    try:
        return jsonify({"ok": True, "data": collector.get_state().get("finance", {})})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/crypto/quotes")
def crypto_quotes():
    symbols = request.args.get("symbols", "BTCUSDT,ETHUSDT,SOLUSDT")
    try:
        return jsonify(fetch_crypto_quotes(symbols))
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/finance/quotes")
def finance_quotes():
    symbols = request.args.get("symbols", "AAPL,MSFT,GLD,SLV")
    try:
        api_key = load_web_settings().get("active_finnhub_api_key") or None
        return jsonify(fetch_finance_quotes(symbols, api_key or None))
    except Exception as exc:
        return _json_error(exc)


# ---------------------------------------------------------------------------
# Strategy Registry (new tables) API
# ---------------------------------------------------------------------------

@app.get("/api/strategy-codes")
def api_strategy_codes():
    try:
        return jsonify({"ok": True, "data": list_strategy_codes()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/strategy-codes/<code_name>/inputs")
def api_strategy_code_inputs(code_name: str):
    try:
        return jsonify({"ok": True, "data": get_strategy_code_inputs(code_name)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/strategy-codes/<code_name>/schemas")
def api_strategy_code_schemas(code_name: str):
    try:
        return jsonify({"ok": True, "data": get_strategy_code_schemas(code_name)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/strategy-signal-sources/library-alphas")
@debug_timing("strategy_library_alpha_sources")
def api_strategy_library_alpha_sources():
    """List immutable Library Alphas that a Strategy can pin."""
    try:
        return jsonify({"ok": True, "data": list_library_alpha_sources()})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/registry/strategies")
@debug_timing("registry_list")
def registry_strategies_list():
    try:
        return jsonify({"ok": True, "data": list_registry_strategies()})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/registry/strategies")
@debug_timing("registry_create")
def registry_strategies_create():
    try:
        payload = request.get_json(silent=True) or {}
        result = create_strategy(payload)
        return jsonify({"ok": True, "data": result}), 201
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/registry/strategies/<int:strategy_id>")
@debug_timing("registry_get")
def registry_strategies_get(strategy_id: int):
    try:
        result = get_registry_strategy(strategy_id)
        if not result:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/registry/strategies/<int:strategy_id>")
@debug_timing("registry_update")
def registry_strategies_update(strategy_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        result = update_registry_strategy(strategy_id, payload)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/registry/strategies/<int:strategy_id>/mode")
@debug_timing("registry_mode")
def registry_strategies_mode(strategy_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        new_mode = str(payload.get("mode") or payload.get("state") or "").strip()
        result = update_strategy_mode(strategy_id, new_mode)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/registry/strategies/<int:strategy_id>/state")
@debug_timing("registry_state_compat")
def registry_strategies_state(strategy_id: int):
    """Compatibility route for the old Stop/Virtual/Real endpoint."""
    try:
        payload = request.get_json(silent=True) or {}
        new_mode = str(payload.get("mode") or payload.get("state") or "").strip()
        result = update_strategy_state(strategy_id, new_mode)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/registry/strategies/<int:strategy_id>/state-store")
@debug_timing("registry_state_store_get")
def registry_strategy_state_store_get(strategy_id: int):
    try:
        strategy = get_registry_strategy(strategy_id)
        if not strategy:
            return jsonify({"ok": False, "error": "strategy not found"}), 404
        return jsonify({
            "ok": True,
            "data": {
                "strategy_id": strategy_id,
                "mode": strategy.get("mode") or strategy.get("state") or "Stop",
                **strategy_state_payload(
                    strategy.get("strategy_code") or "",
                    read_strategy_state_bundle(strategy_id),
                ),
            },
        })
    except Exception as exc:
        return _json_error(exc)


@app.patch("/api/registry/strategies/<int:strategy_id>/state-store/<namespace>")
@debug_timing("registry_state_store_patch")
def registry_strategy_state_store_patch(strategy_id: int, namespace: str):
    try:
        ns = str(namespace or "").strip().lower()
        if ns == "controls":
            ns = "user"
        if ns not in {"user", "runtime", "machine", "state"}:
            return _json_error(ValueError("namespace must be controls/user, runtime, or machine/state"), 400)
        strategy = get_registry_strategy(strategy_id)
        if not strategy:
            return jsonify({"ok": False, "error": "strategy not found"}), 404
        payload = request.get_json(silent=True) or {}
        values = payload.get("values")
        if values is None:
            values = payload.get("state", {})
        if not isinstance(values, dict):
            return _json_error(ValueError("state values must be a JSON object"), 400)
        force = bool(payload.get("force"))
        mode = strategy.get("mode") or strategy.get("state") or "Stop"
        if ns == "runtime" and mode != "Stop" and not force:
            return _json_error(
                ValueError("RuntimeState can only be edited while the strategy is Stop"),
                400,
            )
        changed = write_strategy_state_values(
            strategy_id,
            values,
            namespace=ns,
            replace=bool(payload.get("replace", True)),
            actor=str(payload.get("actor") or "user"),
            reason=str(payload.get("reason") or ""),
        )
        return jsonify({
            "ok": True,
            "changed": changed,
            "data": strategy_state_payload(
                strategy.get("strategy_code") or "",
                read_strategy_state_bundle(strategy_id),
            ),
        })
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/registry/strategies/<int:strategy_id>/state-store/<namespace>")
@debug_timing("registry_state_store_reset")
def registry_strategy_state_store_reset(strategy_id: int, namespace: str):
    try:
        ns = str(namespace or "").strip().lower()
        if ns == "controls":
            ns = "user"
        if ns not in {"user", "runtime", "machine", "state"}:
            return _json_error(ValueError("namespace must be controls/user, runtime, or machine/state"), 400)
        strategy = get_registry_strategy(strategy_id)
        if not strategy:
            return jsonify({"ok": False, "error": "strategy not found"}), 404
        force = str(request.args.get("force") or "").lower() in {"1", "true", "yes"}
        mode = strategy.get("mode") or strategy.get("state") or "Stop"
        if ns == "runtime" and mode != "Stop" and not force:
            return _json_error(
                ValueError("RuntimeState can only be reset while the strategy is Stop"),
                400,
            )
        changed = reset_strategy_state_namespace(
            strategy_id,
            ns,
            actor=str(request.args.get("actor") or "user"),
            reason=str(request.args.get("reason") or ""),
        )
        return jsonify({
            "ok": True,
            "changed": changed,
            "data": strategy_state_payload(
                strategy.get("strategy_code") or "",
                read_strategy_state_bundle(strategy_id),
            ),
        })
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/registry/strategies/<int:strategy_id>/legs")
@debug_timing("registry_legs")
def registry_strategies_legs(strategy_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        legs = payload.get("legs") or []
        result = update_strategy_legs(strategy_id, legs)
        return jsonify({"ok": True, "data": result})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.delete("/api/registry/strategies/<int:strategy_id>")
@debug_timing("registry_delete")
def registry_strategies_delete(strategy_id: int):
    try:
        ok = delete_strategy(strategy_id)
        if not ok:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/registry/strategies/<int:strategy_id>/force-flat")
@debug_timing("registry_force_flat")
def registry_strategy_force_flat(strategy_id: int):
    try:
        payload = request.get_json(silent=True) or {}
        actor = str(payload.get("actor") or "user")
        return jsonify({"ok": True, "data": force_flat_strategy(strategy_id, actor=actor)})
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)

# ---------------------------------------------------------------------------
# Virtual trading API
# ---------------------------------------------------------------------------

from services.strategy_data_source import connect as _ds_connect
from services.virtual_execution import reset_virtual_account
from services.virtual_runner import virtual_runner



@app.get("/api/virtual/strategies/<int:strategy_id>/account")
def virtual_account(strategy_id: int):
    try:
        conn = _ds_connect(readonly=True)
        row = conn.execute(
            "SELECT * FROM strategy_virtual_account WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"ok": True, "data": None})
        return jsonify({"ok": True, "data": dict(row)})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/virtual/strategies/<int:strategy_id>/positions")
def virtual_positions(strategy_id: int):
    try:
        conn = _ds_connect(readonly=True)
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_positions WHERE strategy_id = ? ORDER BY leg_index, side",
            (strategy_id,),
        ).fetchall()
        rows_v2 = conn.execute(
            "SELECT * FROM strategy_virtual_positions_v2 WHERE strategy_id = ? ORDER BY instrument_id, side",
            (strategy_id,),
        ).fetchall()
        conn.close()
        return jsonify({"ok": True, "data": [dict(r) for r in rows], "data_v2": [dict(r) for r in rows_v2]})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/virtual/strategies/<int:strategy_id>/orders")
def virtual_orders(strategy_id: int):
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = max(1, min(int(request.args.get("page_size", 50)), 200))
        offset = (page - 1) * page_size
        conn = _ds_connect(readonly=True)
        total = conn.execute(
            "SELECT COUNT(*) FROM strategy_virtual_orders WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_orders WHERE strategy_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (strategy_id, page_size, offset),
        ).fetchall()
        total_v2 = conn.execute(
            "SELECT COUNT(*) FROM strategy_virtual_orders_v2 WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()[0]
        rows_v2 = conn.execute(
            "SELECT * FROM strategy_virtual_orders_v2 WHERE strategy_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (strategy_id, page_size, offset),
        ).fetchall()
        conn.close()
        return jsonify({
            "ok": True,
            "data": [dict(r) for r in rows],
            "data_v2": [dict(r) for r in rows_v2],
            "total": total,
            "total_v2": total_v2,
            "page": page,
            "page_size": page_size,
        })
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/virtual/strategies/<int:strategy_id>/events")
def virtual_events(strategy_id: int):
    try:
        event_type = request.args.get("event_type", "")
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
        conn = _ds_connect(readonly=True)
        if event_type:
            rows = conn.execute(
                "SELECT * FROM strategy_virtual_events WHERE strategy_id = ? AND event_type = ? ORDER BY id DESC LIMIT ?",
                (strategy_id, event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM strategy_virtual_events WHERE strategy_id = ? ORDER BY id DESC LIMIT ?",
                (strategy_id, limit),
            ).fetchall()
        conn.close()
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/virtual/strategies/<int:strategy_id>/ticks")
def virtual_ticks(strategy_id: int):
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
        conn = _ds_connect(readonly=True)
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_ticks WHERE strategy_id = ? ORDER BY tick_id DESC LIMIT ?",
            (strategy_id, limit),
        ).fetchall()
        conn.close()
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    except Exception as exc:
        return _json_error(exc)


@app.post("/api/virtual/strategies/<int:strategy_id>/reset")
def virtual_reset(strategy_id: int):
    try:
        from services.strategy_registry_service import get_strategy as _get_stg
        stg = _get_stg(strategy_id)
        if not stg:
            return jsonify({"ok": False, "error": "strategy not found"}), 404
        initial_cash = float(stg.get("strategy_bankroll") or 0.0)
        if initial_cash <= 0:
            initial_cash = sum(
                float((leg or {}).get("budget_cap") or 0.0)
                for leg in (stg.get("legs") or [])
            )
        reset_virtual_account(strategy_id, initial_cash)
        return jsonify({"ok": True})
    except Exception as exc:
        return _json_error(exc)


# ============================================================================
# Resource Configuration API (User-Configurable Memory Budgets)
# ============================================================================

@app.get("/api/resource-config")
@debug_timing("resource_config_get")
def get_resource_config():
    """Get current resource configuration and real-time snapshot"""
    try:
        from services.data_platform.resource_config_service import ResourceConfigService
        service = ResourceConfigService(get_default_store())
        config = service.get()
        snapshot = service.get_runtime_snapshot()
        return jsonify({
            "ok": True,
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
    except Exception as exc:
        return _json_error(exc)


@app.put("/api/resource-config")
@require_local_request
@debug_timing("resource_config_update")
def update_resource_config():
    """Update user resource configuration"""
    try:
        from services.data_platform.resource_config_service import ResourceConfigService
        payload = request.get_json(silent=True) or {}
        service = ResourceConfigService(get_default_store())

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
            "ok": True,
            "message": "资源配置已更新",
            "config": {
                "user_research_budget_mb": updated.user_research_budget_mb,
                "user_config_mode": updated.user_config_mode,
            },
        })
    except ValueError as exc:
        return _json_error(exc, 400)
    except Exception as exc:
        return _json_error(exc)


@app.get("/api/resource-config/snapshot")
@debug_timing("resource_snapshot")
def get_resource_snapshot():
    """Real-time resource snapshot (for dashboard polling)"""
    try:
        from services.data_platform.resource_config_service import ResourceConfigService
        service = ResourceConfigService(get_default_store())
        snapshot = service.get_runtime_snapshot()
        return jsonify({"ok": True, "data": snapshot})
    except Exception as exc:
        return _json_error(exc)


if __name__ == "__main__":
    ws_market_sync.start()
    collector.start()
    virtual_runner.start()
    event_news_scheduler.start()
    try:
        backtest_recovery = recover_backtest_queue()
        if backtest_recovery.get("queued") or backtest_recovery.get("interrupted"):
            print(
                "[BACKTEST][RECOVERY] "
                f"queued={len(backtest_recovery.get('queued') or [])} "
                f"interrupted={len(backtest_recovery.get('interrupted') or [])}",
                flush=True,
            )
        recovery = ResearchExperimentService(
            get_default_store(), isolate_experiment_execution=True
        ).quarantine_interrupted()
        if recovery.get("count"):
            print(
                f"[RESEARCH-EXPERIMENT][RECOVERY] quarantined={recovery['count']}",
                flush=True,
            )
        _start_requirement_maintenance()
        _start_research_run_orchestrator()
        _start_research_experiment_orchestrator()
    except Exception as exc:
        # Keep the UI/diagnostic API available, but never resume Experiments
        # when restart recovery itself could not prove the old worker is gone.
        print(
            f"[RESEARCH-EXPERIMENT][RECOVERY-ERR] orchestrator disabled: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
