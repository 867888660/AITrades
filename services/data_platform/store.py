from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from services.history_storage_service import get_data_platform_metadata_db_path


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_METADATA_DB = get_data_platform_metadata_db_path()
_DEFAULT_STORE: "DataPlatformStore | None" = None
_DEFAULT_STORE_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DataPlatformStore:
    """SQLite control-plane store for research metadata.

    This store deliberately contains no market bars or factor values.  Those
    belong in Parquet (or another immutable artifact store); SQLite only tracks
    identity, requirements, catalog state, and manifest lineage.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or DEFAULT_METADATA_DB).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.transaction(immediate=True) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS data_platform_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_version INTEGER PRIMARY KEY,
                    migration_name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS instrument_registry (
                    instrument_id TEXT PRIMARY KEY,
                    asset_class TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    native_symbol TEXT NOT NULL,
                    display_symbol TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    underlying_id TEXT NOT NULL DEFAULT '',
                    base_asset TEXT NOT NULL DEFAULT '',
                    quote_asset TEXT NOT NULL DEFAULT '',
                    currency TEXT NOT NULL DEFAULT '',
                    condition_id TEXT NOT NULL DEFAULT '',
                    market_id TEXT NOT NULL DEFAULT '',
                    event_id TEXT NOT NULL DEFAULT '',
                    outcome_side TEXT NOT NULL DEFAULT '',
                    listing_time TEXT,
                    delisting_time TEXT,
                    timezone TEXT NOT NULL DEFAULT 'UTC',
                    trading_calendar TEXT NOT NULL DEFAULT '24x7',
                    tick_size REAL,
                    lot_size REAL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS instrument_aliases (
                    source TEXT NOT NULL,
                    source_symbol TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source, source_symbol),
                    FOREIGN KEY (instrument_id) REFERENCES instrument_registry(instrument_id)
                );

                CREATE TABLE IF NOT EXISTS data_requirements (
                    requirement_id TEXT PRIMARY KEY,
                    owner_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL DEFAULT '',
                    target_type TEXT NOT NULL,
                    instrument_list_json TEXT NOT NULL DEFAULT '[]',
                    data_type TEXT NOT NULL,
                    frequency TEXT NOT NULL DEFAULT '',
                    fields_json TEXT NOT NULL DEFAULT '[]',
                    history_mode TEXT NOT NULL,
                    history_start TEXT,
                    history_end TEXT,
                    lookback_value INTEGER,
                    lookback_unit TEXT NOT NULL DEFAULT '',
                    refresh_mode TEXT NOT NULL,
                    refresh_interval_seconds INTEGER,
                    auto_backfill INTEGER NOT NULL DEFAULT 1,
                    usage_level TEXT NOT NULL DEFAULT 'RESEARCH',
                    priority INTEGER NOT NULL DEFAULT 50,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    requirement_fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dataset_catalog (
                    dataset_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    frequency TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    last_complete_time TEXT,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    gap_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    quality_status TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    latest_manifest_id TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dataset_manifests (
                    manifest_id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    dataset_fingerprint TEXT NOT NULL,
                    manifest_version INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    committed_at TEXT,
                    UNIQUE(dataset_fingerprint, manifest_version),
                    FOREIGN KEY (dataset_id) REFERENCES dataset_catalog(dataset_id)
                );

                CREATE TABLE IF NOT EXISTS dataset_partitions (
                    partition_id TEXT PRIMARY KEY,
                    manifest_id TEXT NOT NULL,
                    partition_key TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    file_uri TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    checksum TEXT NOT NULL DEFAULT '',
                    min_event_time TEXT,
                    max_event_time TEXT,
                    quality_status TEXT NOT NULL,
                    UNIQUE(manifest_id, partition_key, file_uri),
                    FOREIGN KEY (manifest_id) REFERENCES dataset_manifests(manifest_id)
                );

                CREATE INDEX IF NOT EXISTS idx_instrument_aliases_instrument
                    ON instrument_aliases(instrument_id);
                CREATE INDEX IF NOT EXISTS idx_requirements_owner
                    ON data_requirements(owner_type, owner_id, status);
                CREATE INDEX IF NOT EXISTS idx_catalog_lookup
                    ON dataset_catalog(instrument_id, data_type, frequency, status);
                CREATE INDEX IF NOT EXISTS idx_manifest_dataset
                    ON dataset_manifests(dataset_id, manifest_version DESC);
                CREATE INDEX IF NOT EXISTS idx_partitions_manifest
                    ON dataset_partitions(manifest_id, start_time, end_time);

                CREATE TABLE IF NOT EXISTS research_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL DEFAULT '',
                    artifact_type TEXT NOT NULL,
                    logical_name TEXT NOT NULL,
                    artifact_version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'READY',
                    content_uri TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    created_by_run_id TEXT NOT NULL DEFAULT '',
                    created_by_task_id TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(artifact_type, logical_name, artifact_version),
                    UNIQUE(artifact_type, logical_name, content_hash)
                );

                CREATE TABLE IF NOT EXISTS artifact_dependencies (
                    child_artifact_id TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    parent_type TEXT NOT NULL,
                    dependency_type TEXT NOT NULL,
                    PRIMARY KEY(child_artifact_id, parent_id, dependency_type),
                    FOREIGN KEY (child_artifact_id) REFERENCES research_artifacts(artifact_id)
                );

                CREATE TABLE IF NOT EXISTS artifact_pins (
                    artifact_id TEXT NOT NULL,
                    pin_owner_type TEXT NOT NULL,
                    pin_owner_id TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(artifact_id, pin_owner_type, pin_owner_id),
                    FOREIGN KEY (artifact_id) REFERENCES research_artifacts(artifact_id)
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_logical_name
                    ON research_artifacts(artifact_type, logical_name, artifact_version DESC);
                CREATE INDEX IF NOT EXISTS idx_artifact_dependencies_parent
                    ON artifact_dependencies(parent_id, parent_type);
                CREATE INDEX IF NOT EXISTS idx_artifact_pins_artifact
                    ON artifact_pins(artifact_id);

                CREATE TABLE IF NOT EXISTS research_projects (
                    project_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    summary_state TEXT NOT NULL DEFAULT 'PLANNING',
                    current_plan_version INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL DEFAULT 'local_user',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS research_plans (
                    plan_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    plan_stage TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    plan_hash TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT 'local_user',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, plan_version, plan_stage),
                    FOREIGN KEY (project_id) REFERENCES research_projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS approval_grants (
                    grant_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    scope_json TEXT NOT NULL DEFAULT '{}',
                    budgets_json TEXT NOT NULL DEFAULT '{}',
                    approved_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    expires_at TEXT,
                    FOREIGN KEY (project_id) REFERENCES research_projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS approval_budget_counters (
                    grant_id TEXT PRIMARY KEY,
                    reserved_runs INTEGER NOT NULL DEFAULT 0,
                    consumed_runs INTEGER NOT NULL DEFAULT 0,
                    reserved_download_bytes INTEGER NOT NULL DEFAULT 0,
                    consumed_download_bytes INTEGER NOT NULL DEFAULT 0,
                    reserved_runtime_seconds INTEGER NOT NULL DEFAULT 0,
                    consumed_runtime_seconds INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (grant_id) REFERENCES approval_grants(grant_id)
                );

                CREATE TABLE IF NOT EXISTS approval_budget_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    grant_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    runs INTEGER NOT NULL DEFAULT 0,
                    download_bytes INTEGER NOT NULL DEFAULT 0,
                    runtime_seconds INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'RESERVED',
                    created_at TEXT NOT NULL,
                    consumed_at TEXT,
                    released_at TEXT,
                    UNIQUE(grant_id, idempotency_key),
                    FOREIGN KEY (grant_id) REFERENCES approval_grants(grant_id)
                );

                CREATE TABLE IF NOT EXISTS research_tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    workflow_run_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    logical_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    priority INTEGER NOT NULL DEFAULT 50,
                    idempotency_key TEXT NOT NULL,
                    input_json TEXT NOT NULL DEFAULT '{}',
                    expected_output_types_json TEXT NOT NULL DEFAULT '[]',
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    timeout_seconds INTEGER NOT NULL DEFAULT 3600,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(workflow_run_id, idempotency_key),
                    FOREIGN KEY (project_id) REFERENCES research_projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS research_task_dependencies (
                    task_id TEXT NOT NULL,
                    depends_on_task_id TEXT NOT NULL,
                    PRIMARY KEY(task_id, depends_on_task_id),
                    FOREIGN KEY (task_id) REFERENCES research_tasks(task_id),
                    FOREIGN KEY (depends_on_task_id) REFERENCES research_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS research_task_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'RUNNING',
                    worker_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(task_id, attempt_number),
                    FOREIGN KEY (task_id) REFERENCES research_tasks(task_id)
                );

                CREATE INDEX IF NOT EXISTS idx_research_projects_state
                    ON research_projects(summary_state, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_plans_project
                    ON research_plans(project_id, plan_version DESC, plan_stage);
                CREATE INDEX IF NOT EXISTS idx_approval_grants_project
                    ON approval_grants(project_id, plan_version DESC, status);
                CREATE INDEX IF NOT EXISTS idx_research_tasks_ready
                    ON research_tasks(status, priority DESC, created_at);
                CREATE INDEX IF NOT EXISTS idx_research_tasks_workflow
                    ON research_tasks(workflow_run_id, status);
                CREATE INDEX IF NOT EXISTS idx_research_task_attempts_task
                    ON research_task_attempts(task_id, attempt_number DESC);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (3, "research_control_plane_baseline", utc_now()),
            )
            self._apply_migrations(conn)
            version_row = conn.execute("SELECT MAX(migration_version) FROM schema_migrations").fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO data_platform_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(int(version_row[0] or 3))),
            )

    @staticmethod
    def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        applied = {
            int(row[0])
            for row in conn.execute("SELECT migration_version FROM schema_migrations").fetchall()
        }
        if 4 not in applied:
            artifact_columns = self._column_names(conn, "research_artifacts")
            if "spec_hash" not in artifact_columns:
                conn.execute("ALTER TABLE research_artifacts ADD COLUMN spec_hash TEXT NOT NULL DEFAULT ''")
            if "engine_version" not in artifact_columns:
                conn.execute("ALTER TABLE research_artifacts ADD COLUMN engine_version TEXT NOT NULL DEFAULT ''")
            if "code_hash" not in artifact_columns:
                conn.execute("ALTER TABLE research_artifacts ADD COLUMN code_hash TEXT NOT NULL DEFAULT ''")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS universe_definitions (
                    universe_definition_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    universe_type TEXT NOT NULL,
                    parameters_json TEXT NOT NULL DEFAULT '{}',
                    selection_rule_version TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    UNIQUE(name, version)
                );

                CREATE TABLE IF NOT EXISTS universe_snapshots (
                    universe_snapshot_id TEXT PRIMARY KEY,
                    universe_definition_id TEXT NOT NULL,
                    as_of_time TEXT NOT NULL,
                    actual_instrument_ids_json TEXT NOT NULL,
                    selection_inputs_json TEXT NOT NULL DEFAULT '{}',
                    selection_rule_version TEXT NOT NULL,
                    dataset_manifest_ids_json TEXT NOT NULL DEFAULT '[]',
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (universe_definition_id)
                        REFERENCES universe_definitions(universe_definition_id)
                );

                CREATE INDEX IF NOT EXISTS idx_universe_definitions_name
                    ON universe_definitions(name, version);
                CREATE INDEX IF NOT EXISTS idx_universe_snapshots_definition
                    ON universe_snapshots(universe_definition_id, as_of_time DESC);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (4, "universe_and_artifact_identity", utc_now()),
            )
        if 5 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dataset_manifest_provenance (
                    manifest_id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    gateway TEXT NOT NULL,
                    upstream_provider TEXT NOT NULL,
                    original_publisher TEXT NOT NULL DEFAULT '',
                    endpoint TEXT NOT NULL,
                    gateway_version TEXT NOT NULL DEFAULT '',
                    provider_version TEXT NOT NULL DEFAULT '',
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    source_policy_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (manifest_id) REFERENCES dataset_manifests(manifest_id),
                    UNIQUE(dataset_id, request_hash, manifest_id)
                );
                CREATE INDEX IF NOT EXISTS idx_manifest_provenance_dataset
                    ON dataset_manifest_provenance(dataset_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_manifest_provenance_provider
                    ON dataset_manifest_provenance(gateway, upstream_provider, created_at DESC);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (5, "dataset_manifest_provenance", utc_now()),
            )
        if 6 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS binance_backfill_jobs (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    requirement_id TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    effective_end_time TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'READY',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    source_request_hash TEXT NOT NULL,
                    cursor_time TEXT,
                    page_limit INTEGER NOT NULL DEFAULT 1000,
                    pages_completed INTEGER NOT NULL DEFAULT 0,
                    rows_fetched INTEGER NOT NULL DEFAULT 0,
                    rows_stored INTEGER NOT NULL DEFAULT 0,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    next_retry_at TEXT,
                    manifest_commit_status TEXT NOT NULL DEFAULT 'PENDING',
                    manifest_id TEXT,
                    dataset_id TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    last_error_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (task_id) REFERENCES research_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS binance_backfill_pages (
                    job_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    request_start_time TEXT NOT NULL,
                    request_end_time TEXT NOT NULL,
                    response_start_time TEXT,
                    response_end_time TEXT,
                    fetched_count INTEGER NOT NULL DEFAULT 0,
                    stored_count INTEGER NOT NULL DEFAULT 0,
                    response_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, page_number),
                    FOREIGN KEY (job_id) REFERENCES binance_backfill_jobs(job_id)
                );

                CREATE INDEX IF NOT EXISTS idx_binance_backfill_status
                    ON binance_backfill_jobs(status, next_retry_at, updated_at);
                CREATE INDEX IF NOT EXISTS idx_binance_backfill_task
                    ON binance_backfill_jobs(task_id, status);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (6, "binance_backfill_jobs", utc_now()),
            )
        if 7 not in applied:
            requirement_columns = self._column_names(conn, "data_requirements")
            for column, declaration in {
                "adjustment": "TEXT NOT NULL DEFAULT 'NONE'",
                "time_semantics": "TEXT NOT NULL DEFAULT 'BAR_END_AVAILABLE_TIME'",
                "point_in_time_policy": "TEXT NOT NULL DEFAULT 'AS_OF'",
                "quality_policy": "TEXT NOT NULL DEFAULT 'STRICT'",
                "source_policy": "TEXT NOT NULL DEFAULT 'FIXED'",
            }.items():
                if column not in requirement_columns:
                    conn.execute(f"ALTER TABLE data_requirements ADD COLUMN {column} {declaration}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS requirement_sets (
                    requirement_set_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    set_version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'RESOLVED',
                    compiler_version TEXT NOT NULL,
                    source_specs_json TEXT NOT NULL DEFAULT '[]',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    superseded_by_id TEXT,
                    UNIQUE(project_id, set_version)
                );

                CREATE TABLE IF NOT EXISTS requirement_set_items (
                    requirement_set_id TEXT NOT NULL,
                    requirement_id TEXT NOT NULL,
                    requirement_json TEXT NOT NULL,
                    origin_kind TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1,
                    removable INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(requirement_set_id, requirement_id),
                    FOREIGN KEY (requirement_set_id) REFERENCES requirement_sets(requirement_set_id),
                    FOREIGN KEY (requirement_id) REFERENCES data_requirements(requirement_id)
                );

                CREATE TABLE IF NOT EXISTS requirement_dependency_links (
                    requirement_set_id TEXT NOT NULL,
                    requirement_id TEXT NOT NULL,
                    origin_type TEXT NOT NULL,
                    origin_id TEXT NOT NULL,
                    origin_version TEXT NOT NULL DEFAULT '',
                    dependency_path_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(requirement_set_id, requirement_id, origin_type, origin_id, origin_version),
                    FOREIGN KEY (requirement_set_id, requirement_id)
                        REFERENCES requirement_set_items(requirement_set_id, requirement_id)
                );

                CREATE INDEX IF NOT EXISTS idx_requirement_sets_project
                    ON requirement_sets(project_id, set_version DESC);
                CREATE INDEX IF NOT EXISTS idx_requirement_links_origin
                    ON requirement_dependency_links(origin_type, origin_id, origin_version);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (7, "requirement_sets_and_dependencies", utc_now()),
            )
        if 8 not in applied:
            grant_columns = self._column_names(conn, "approval_grants")
            if "grant_version" not in grant_columns:
                conn.execute("ALTER TABLE approval_grants ADD COLUMN grant_version INTEGER NOT NULL DEFAULT 1")
            if "policy_version" not in grant_columns:
                conn.execute("ALTER TABLE approval_grants ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'research_policy.v1'")

            catalog_columns = self._column_names(conn, "dataset_catalog")
            for column, declaration in {
                "fields_json": "TEXT NOT NULL DEFAULT '[]'",
                "adjustment": "TEXT NOT NULL DEFAULT 'NONE'",
                "time_semantics": "TEXT NOT NULL DEFAULT 'BAR_END_AVAILABLE_TIME'",
                "point_in_time_policy": "TEXT NOT NULL DEFAULT 'AS_OF'",
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if column not in catalog_columns:
                    conn.execute(f"ALTER TABLE dataset_catalog ADD COLUMN {column} {declaration}")

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_definitions (
                    definition_id TEXT PRIMARY KEY,
                    definition_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'DRAFT',
                    spec_json TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT 'local_user',
                    created_at TEXT NOT NULL,
                    validated_at TEXT,
                    superseded_by_id TEXT,
                    archived_at TEXT,
                    UNIQUE(definition_type, name, version),
                    UNIQUE(definition_type, definition_id, version)
                );

                CREATE TABLE IF NOT EXISTS factor_drafts (
                    draft_id TEXT PRIMARY KEY,
                    owner_project_id TEXT NOT NULL DEFAULT '',
                    library_scope TEXT NOT NULL DEFAULT 'GLOBAL',
                    document_json TEXT NOT NULL DEFAULT '{}',
                    draft_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'DRAFT',
                    validated_definition_id TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT 'local_ui_user',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    validated_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_factor_drafts_project_state
                ON factor_drafts(owner_project_id, state, updated_at);

                CREATE TABLE IF NOT EXISTS project_definition_refs (
                    project_id TEXT NOT NULL,
                    slot_key TEXT NOT NULL,
                    definition_type TEXT NOT NULL,
                    definition_id TEXT NOT NULL,
                    definition_version TEXT NOT NULL,
                    reference_mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, slot_key),
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
                    FOREIGN KEY(definition_id) REFERENCES research_definitions(definition_id)
                );

                CREATE TABLE IF NOT EXISTS research_run_previews (
                    preview_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    run_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    preview_fingerprint TEXT NOT NULL,
                    definition_closure_json TEXT NOT NULL,
                    data_resolution_closure_json TEXT NOT NULL,
                    execution_closure_json TEXT NOT NULL,
                    authorization_closure_json TEXT NOT NULL,
                    readiness_json TEXT NOT NULL,
                    resolver_output_json TEXT NOT NULL,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL DEFAULT 'local_user',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, preview_fingerprint),
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS frozen_research_bundles (
                    bundle_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    source_preview_id TEXT NOT NULL,
                    source_preview_fingerprint TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL DEFAULT 'FROZEN',
                    integrity_status TEXT NOT NULL DEFAULT 'VERIFIED',
                    reuse_status TEXT NOT NULL DEFAULT 'ALLOWED',
                    reuse_reason_code TEXT NOT NULL DEFAULT '',
                    canonical_payload_json TEXT NOT NULL,
                    bundle_hash TEXT NOT NULL UNIQUE,
                    historical_authorization_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
                    FOREIGN KEY(source_preview_id) REFERENCES research_run_previews(preview_id)
                );

                CREATE TABLE IF NOT EXISTS research_runs_v2 (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    run_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'QUEUED',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    preview_id TEXT NOT NULL,
                    preview_fingerprint TEXT NOT NULL,
                    bundle_id TEXT NOT NULL UNIQUE,
                    reservation_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL DEFAULT 'local_user',
                    actor_type TEXT NOT NULL DEFAULT 'HUMAN',
                    priority INTEGER NOT NULL DEFAULT 50,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    input_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}',
                    error_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    queued_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
                    FOREIGN KEY(preview_id) REFERENCES research_run_previews(preview_id),
                    FOREIGN KEY(bundle_id) REFERENCES frozen_research_bundles(bundle_id),
                    FOREIGN KEY(reservation_id) REFERENCES approval_budget_reservations(reservation_id)
                );

                CREATE TABLE IF NOT EXISTS research_run_outbox (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    FOREIGN KEY(run_id) REFERENCES research_runs_v2(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_research_definitions_library
                    ON research_definitions(definition_type, state, name, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_project_definition_refs
                    ON project_definition_refs(project_id, definition_type, reference_mode);
                CREATE INDEX IF NOT EXISTS idx_research_run_previews_project
                    ON research_run_previews(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_runs_v2_queue
                    ON research_runs_v2(status, priority DESC, queued_at);
                CREATE INDEX IF NOT EXISTS idx_research_runs_v2_project
                    ON research_runs_v2(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_run_outbox_status
                    ON research_run_outbox(status, created_at);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (8, "research_run_semantics_v2", utc_now()),
            )
        if 9 not in applied:
            preview_columns = self._column_names(conn, "research_run_previews")
            if "request_json" not in preview_columns:
                conn.execute("ALTER TABLE research_run_previews ADD COLUMN request_json TEXT NOT NULL DEFAULT '{}'")
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (9, "research_preview_request_identity", utc_now()),
            )
        if 10 not in applied:
            definition_columns = self._column_names(conn, "research_definitions")
            if "owner_project_id" not in definition_columns:
                conn.execute("ALTER TABLE research_definitions ADD COLUMN owner_project_id TEXT NOT NULL DEFAULT ''")
            if "library_scope" not in definition_columns:
                conn.execute("ALTER TABLE research_definitions ADD COLUMN library_scope TEXT NOT NULL DEFAULT 'GLOBAL'")
            universe_columns = self._column_names(conn, "universe_definitions")
            if "owner_project_id" not in universe_columns:
                conn.execute("ALTER TABLE universe_definitions ADD COLUMN owner_project_id TEXT NOT NULL DEFAULT ''")
            if "library_scope" not in universe_columns:
                conn.execute("ALTER TABLE universe_definitions ADD COLUMN library_scope TEXT NOT NULL DEFAULT 'GLOBAL'")
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_research_definitions_scope
                    ON research_definitions(library_scope, owner_project_id, definition_type, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_universe_definitions_scope
                    ON universe_definitions(library_scope, owner_project_id, created_at DESC);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (10, "project_scoped_research_libraries", utc_now()),
            )
        if 11 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_library_assets (
                    library_asset_id TEXT PRIMARY KEY,
                    component_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    asset_version INTEGER NOT NULL,
                    source_object_id TEXT NOT NULL,
                    source_object_version TEXT NOT NULL DEFAULT '',
                    content_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    published_from_project_id TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    UNIQUE(component_type, name, asset_version),
                    UNIQUE(component_type, source_object_id),
                    FOREIGN KEY(published_from_project_id) REFERENCES research_projects(project_id)
                );
                CREATE TABLE IF NOT EXISTS research_universe_refs (
                    project_id TEXT PRIMARY KEY,
                    universe_snapshot_id TEXT NOT NULL,
                    library_asset_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
                    FOREIGN KEY(universe_snapshot_id) REFERENCES universe_snapshots(universe_snapshot_id),
                    FOREIGN KEY(library_asset_id) REFERENCES research_library_assets(library_asset_id)
                );
                CREATE TABLE IF NOT EXISTS research_requirement_refs (
                    project_id TEXT PRIMARY KEY,
                    requirement_set_id TEXT NOT NULL,
                    library_asset_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
                    FOREIGN KEY(requirement_set_id) REFERENCES requirement_sets(requirement_set_id),
                    FOREIGN KEY(library_asset_id) REFERENCES research_library_assets(library_asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_research_universe_refs_snapshot
                    ON research_universe_refs(universe_snapshot_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_library_assets_current
                    ON research_library_assets(component_type, name, asset_version DESC);
                CREATE INDEX IF NOT EXISTS idx_research_requirement_refs_set
                    ON research_requirement_refs(requirement_set_id, updated_at DESC);
                """
            )
            ref_columns = self._column_names(conn, "project_definition_refs")
            if "library_asset_id" not in ref_columns:
                conn.execute("ALTER TABLE project_definition_refs ADD COLUMN library_asset_id TEXT")
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (11, "research_library_assets", utc_now()),
            )
        if 12 not in applied:
            project_columns = self._column_names(conn, "research_projects")
            if "archived_at" not in project_columns:
                conn.execute("ALTER TABLE research_projects ADD COLUMN archived_at TEXT")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS requirement_definitions (
                    requirement_definition_id TEXT PRIMARY KEY,
                    owner_project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    definition_version INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'DRAFT',
                    spec_json TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    source_library_asset_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    validated_at TEXT,
                    archived_at TEXT,
                    UNIQUE(owner_project_id, name, definition_version),
                    FOREIGN KEY(owner_project_id) REFERENCES research_projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS library_requirement_drafts (
                    draft_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_library_asset_id TEXT,
                    base_asset_version INTEGER,
                    state TEXT NOT NULL DEFAULT 'DRAFT',
                    spec_json TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS library_requirement_assets (
                    library_asset_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    asset_version INTEGER NOT NULL,
                    spec_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    source_draft_id TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    UNIQUE(name, asset_version),
                    UNIQUE(source_draft_id),
                    FOREIGN KEY(source_draft_id) REFERENCES library_requirement_drafts(draft_id)
                );

                CREATE TABLE IF NOT EXISTS project_requirement_items (
                    ref_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    origin_type TEXT NOT NULL,
                    requirement_definition_id TEXT,
                    library_asset_id TEXT,
                    source_object_id TEXT NOT NULL DEFAULT '',
                    overrides_json TEXT NOT NULL DEFAULT '{}',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
                    FOREIGN KEY(requirement_definition_id) REFERENCES requirement_definitions(requirement_definition_id),
                    FOREIGN KEY(library_asset_id) REFERENCES library_requirement_assets(library_asset_id)
                );

                CREATE INDEX IF NOT EXISTS idx_requirement_definitions_project
                    ON requirement_definitions(owner_project_id, archived_at, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_library_requirement_assets_current
                    ON library_requirement_assets(name, asset_version DESC);
                CREATE INDEX IF NOT EXISTS idx_project_requirement_items_project
                    ON project_requirement_items(project_id, sort_order, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_project_requirement_library_unique
                    ON project_requirement_items(project_id, library_asset_id)
                    WHERE library_asset_id IS NOT NULL;
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (12, "requirement_workspaces_and_project_archiving", utc_now()),
            )
        if 13 not in applied:
            asset_columns = self._column_names(conn, "library_requirement_assets")
            if "updated_at" not in asset_columns:
                conn.execute("ALTER TABLE library_requirement_assets ADD COLUMN updated_at TEXT")
            if "archived_at" not in asset_columns:
                conn.execute("ALTER TABLE library_requirement_assets ADD COLUMN archived_at TEXT")
            conn.execute(
                "UPDATE library_requirement_assets SET updated_at=published_at WHERE updated_at IS NULL OR updated_at=''"
            )
            now = utc_now()
            local_rows = conn.execute(
                """SELECT DISTINCT d.*
                   FROM requirement_definitions d
                   JOIN project_requirement_items r
                     ON r.requirement_definition_id=d.requirement_definition_id
                   WHERE d.archived_at IS NULL AND r.origin_type='RESEARCH'"""
            ).fetchall()
            for row in local_rows:
                source_asset_id = str(row["source_library_asset_id"] or "")
                asset = conn.execute(
                    "SELECT library_asset_id, content_hash FROM library_requirement_assets WHERE library_asset_id=?",
                    (source_asset_id,),
                ).fetchone() if source_asset_id else None
                if asset is None or str(asset["content_hash"]) != str(row["spec_hash"]):
                    draft_id = f"library_requirement_migration_{uuid.uuid4().hex}"
                    asset_id = f"library_requirement_{uuid.uuid4().hex}"
                    version = int(conn.execute(
                        "SELECT COALESCE(MAX(asset_version), 0) + 1 FROM library_requirement_assets WHERE name=?",
                        (str(row["name"]),),
                    ).fetchone()[0])
                    conn.execute(
                        """INSERT INTO library_requirement_drafts(
                               draft_id, name, base_library_asset_id, base_asset_version,
                               state, spec_json, spec_hash, created_at, updated_at
                           ) VALUES (?, ?, NULL, NULL, 'PUBLISHED', ?, ?, ?, ?)""",
                        (draft_id, str(row["name"]), str(row["spec_json"]), str(row["spec_hash"]), now, now),
                    )
                    conn.execute(
                        """INSERT INTO library_requirement_assets(
                               library_asset_id, name, asset_version, spec_json, content_hash,
                               source_draft_id, published_at, updated_at, archived_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                        (asset_id, str(row["name"]), version, str(row["spec_json"]),
                         str(row["spec_hash"]), draft_id, now, now),
                    )
                else:
                    asset_id = str(asset["library_asset_id"])
                refs = conn.execute(
                    """SELECT ref_id, project_id
                       FROM project_requirement_items
                       WHERE requirement_definition_id=?""",
                    (str(row["requirement_definition_id"]),),
                ).fetchall()
                for ref in refs:
                    existing = conn.execute(
                        """SELECT ref_id FROM project_requirement_items
                           WHERE project_id=? AND library_asset_id=? AND ref_id<>?""",
                        (str(ref["project_id"]), asset_id, str(ref["ref_id"])),
                    ).fetchone()
                    if existing:
                        # Both rows resolve to the same shared Requirement. Keep the
                        # existing reference and remove only the redundant relation.
                        conn.execute(
                            "DELETE FROM project_requirement_items WHERE ref_id=?",
                            (str(ref["ref_id"]),),
                        )
                    else:
                        conn.execute(
                            """UPDATE project_requirement_items
                               SET origin_type='LIBRARY', requirement_definition_id=NULL,
                                   library_asset_id=?, source_object_id=?, updated_at=?
                               WHERE ref_id=?""",
                            (asset_id, asset_id, now, str(ref["ref_id"])),
                        )
                conn.execute(
                    "UPDATE requirement_definitions SET archived_at=?, updated_at=? WHERE requirement_definition_id=?",
                    (now, now, str(row["requirement_definition_id"])),
                )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (13, "shared_library_requirements", now),
            )
        if 14 not in applied:
            now = utc_now()
            duplicate_names = conn.execute(
                """SELECT lower(name) AS name_key
                   FROM library_requirement_assets
                   WHERE archived_at IS NULL
                   GROUP BY lower(name)
                   HAVING COUNT(*) > 1"""
            ).fetchall()
            for duplicate in duplicate_names:
                assets = conn.execute(
                    """SELECT a.*,
                              (SELECT COUNT(*) FROM project_requirement_items r
                               JOIN research_projects p ON p.project_id=r.project_id
                               WHERE r.library_asset_id=a.library_asset_id
                                 AND p.archived_at IS NULL) AS usage_count
                       FROM library_requirement_assets a
                       WHERE lower(a.name)=? AND a.archived_at IS NULL
                       ORDER BY usage_count DESC, COALESCE(a.updated_at, a.published_at) DESC""",
                    (str(duplicate["name_key"]),),
                ).fetchall()
                for position, asset in enumerate(assets[1:], start=1):
                    if int(asset["usage_count"] or 0) == 0:
                        conn.execute(
                            "UPDATE library_requirement_assets SET archived_at=?, updated_at=? WHERE library_asset_id=?",
                            (now, now, str(asset["library_asset_id"])),
                        )
                        continue
                    spec = json.loads(str(asset["spec_json"]))
                    unique_name = f"{asset['name']} (Legacy {position})"
                    spec["name"] = unique_name
                    spec_json = json_dumps(spec)
                    spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
                    conn.execute(
                        """UPDATE library_requirement_assets
                           SET name=?, spec_json=?, content_hash=?, updated_at=?
                           WHERE library_asset_id=?""",
                        (unique_name, spec_json, spec_hash, now, str(asset["library_asset_id"])),
                    )
                    conn.execute(
                        """UPDATE library_requirement_drafts
                           SET name=?, spec_json=?, spec_hash=?, updated_at=?
                           WHERE draft_id=?""",
                        (unique_name, spec_json, spec_hash, now, str(asset["source_draft_id"])),
                    )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (14, "deduplicate_shared_requirement_names", now),
            )
        if 15 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS shared_universes (
                    universe_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    universe_type TEXT NOT NULL,
                    current_revision_id TEXT,
                    status TEXT NOT NULL DEFAULT 'VALID',
                    owner_id TEXT NOT NULL DEFAULT 'local_user',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    legacy_library_asset_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                );

                CREATE TABLE IF NOT EXISTS shared_universe_revisions (
                    revision_id TEXT PRIMARY KEY,
                    universe_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    canonical_definition_json TEXT NOT NULL,
                    semantic_hash TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT 'local_user',
                    created_at TEXT NOT NULL,
                    parent_revision_id TEXT,
                    change_summary TEXT NOT NULL DEFAULT '',
                    legacy_definition_id TEXT UNIQUE,
                    UNIQUE(universe_id, revision_number),
                    FOREIGN KEY(universe_id) REFERENCES shared_universes(universe_id),
                    FOREIGN KEY(parent_revision_id) REFERENCES shared_universe_revisions(revision_id)
                );

                CREATE TABLE IF NOT EXISTS research_universe_bindings_v2 (
                    binding_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    universe_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'REFERENCE',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    requirements_stale_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    removed_at TEXT,
                    UNIQUE(project_id, universe_id),
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
                    FOREIGN KEY(universe_id) REFERENCES shared_universes(universe_id)
                );

                CREATE TABLE IF NOT EXISTS shared_universe_resolutions (
                    resolution_id TEXT PRIMARY KEY,
                    universe_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    resolved_at TEXT NOT NULL,
                    instrument_ids_json TEXT NOT NULL DEFAULT '[]',
                    instrument_tuples_json TEXT NOT NULL DEFAULT '[]',
                    member_count INTEGER NOT NULL DEFAULT 0,
                    combination_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    errors_json TEXT NOT NULL DEFAULT '[]',
                    legacy_snapshot_id TEXT UNIQUE,
                    FOREIGN KEY(universe_id) REFERENCES shared_universes(universe_id),
                    FOREIGN KEY(revision_id) REFERENCES shared_universe_revisions(revision_id)
                );

                CREATE INDEX IF NOT EXISTS idx_shared_universes_active
                    ON shared_universes(archived_at, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_shared_universe_revisions_history
                    ON shared_universe_revisions(universe_id, revision_number DESC);
                CREATE INDEX IF NOT EXISTS idx_research_universe_bindings_project
                    ON research_universe_bindings_v2(project_id, is_active, role);
                CREATE INDEX IF NOT EXISTS idx_research_universe_bindings_usage
                    ON research_universe_bindings_v2(universe_id, is_active, updated_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_research_primary_universe
                    ON research_universe_bindings_v2(project_id)
                    WHERE is_active=1 AND role='PRIMARY';
                CREATE INDEX IF NOT EXISTS idx_shared_universe_resolutions_revision
                    ON shared_universe_resolutions(universe_id, revision_id, resolved_at DESC);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (15, "shared_universe_revisions_and_bindings", utc_now()),
            )
        if 16 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS factor_drafts (
                    draft_id TEXT PRIMARY KEY,
                    owner_project_id TEXT NOT NULL DEFAULT '',
                    library_scope TEXT NOT NULL DEFAULT 'GLOBAL',
                    document_json TEXT NOT NULL DEFAULT '{}',
                    draft_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'DRAFT',
                    validated_definition_id TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT 'local_ui_user',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    validated_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_factor_drafts_project_state
                ON factor_drafts(owner_project_id, state, updated_at);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (16, "factor_draft_documents", utc_now()),
            )
        if 17 not in applied:
            factor_draft_columns = self._column_names(conn, "factor_drafts")
            for column, declaration in {
                "latest_preview_id": "TEXT NOT NULL DEFAULT ''",
                "latest_preview_fingerprint": "TEXT NOT NULL DEFAULT ''",
                "previewed_draft_fingerprint": "TEXT NOT NULL DEFAULT ''",
                "previewed_at": "TEXT",
            }.items():
                if column not in factor_draft_columns:
                    conn.execute(f"ALTER TABLE factor_drafts ADD COLUMN {column} {declaration}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS factor_previews (
                    preview_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    draft_fingerprint TEXT NOT NULL,
                    preview_fingerprint TEXT NOT NULL UNIQUE,
                    universe_snapshot_id TEXT NOT NULL,
                    universe_fingerprint TEXT NOT NULL,
                    time_start TEXT NOT NULL,
                    time_end TEXT NOT NULL,
                    manifest_ids_json TEXT NOT NULL,
                    manifest_hashes_json TEXT NOT NULL,
                    input_bindings_json TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    values_json TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL DEFAULT '[]',
                    validated_definition_id TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT 'local_ui_user',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(draft_id) REFERENCES factor_drafts(draft_id),
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
                    FOREIGN KEY(universe_snapshot_id) REFERENCES universe_snapshots(universe_snapshot_id)
                );

                CREATE INDEX IF NOT EXISTS idx_factor_previews_draft_created
                ON factor_previews(draft_id, created_at DESC);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (17, "factor_preview_lifecycle", utc_now()),
            )
        if 18 not in applied:
            requirement_columns = self._column_names(conn, "data_requirements")
            if "source_selection_policy_json" not in requirement_columns:
                conn.execute(
                    "ALTER TABLE data_requirements "
                    "ADD COLUMN source_selection_policy_json TEXT NOT NULL DEFAULT '{}'"
                )
            resolution_columns = self._column_names(conn, "shared_universe_resolutions")
            for column, declaration in {
                "instrument_weights_json": "TEXT NOT NULL DEFAULT '{}'",
                "resolution_metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if column not in resolution_columns:
                    conn.execute(
                        f"ALTER TABLE shared_universe_resolutions ADD COLUMN {column} {declaration}"
                    )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (18, "source_policy_and_weighted_universes", utc_now()),
            )
        if 19 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpha_drafts (
                    draft_id TEXT PRIMARY KEY,
                    owner_project_id TEXT NOT NULL,
                    library_scope TEXT NOT NULL DEFAULT 'PROJECT'
                        CHECK (library_scope='PROJECT'),
                    client_draft_key TEXT NOT NULL DEFAULT '',
                    document_json TEXT NOT NULL DEFAULT '{}',
                    draft_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'DRAFT'
                        CHECK (state IN ('DRAFT','VALIDATED','DISCARDED')),
                    validated_definition_id TEXT NOT NULL DEFAULT '',
                    latest_preview_id TEXT NOT NULL DEFAULT '',
                    latest_preview_fingerprint TEXT NOT NULL DEFAULT '',
                    previewed_draft_fingerprint TEXT NOT NULL DEFAULT '',
                    previewed_at TEXT,
                    created_by TEXT NOT NULL DEFAULT 'local_ui_user',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    validated_at TEXT,
                    FOREIGN KEY(owner_project_id) REFERENCES research_projects(project_id)
                );

                CREATE INDEX IF NOT EXISTS idx_alpha_drafts_project_state
                ON alpha_drafts(owner_project_id, state, updated_at);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_drafts_client_key
                ON alpha_drafts(owner_project_id, client_draft_key)
                WHERE client_draft_key != '';

                CREATE TABLE IF NOT EXISTS alpha_previews (
                    preview_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    draft_fingerprint TEXT NOT NULL,
                    dependency_fingerprint TEXT NOT NULL,
                    preview_fingerprint TEXT NOT NULL UNIQUE,
                    universe_snapshot_id TEXT NOT NULL,
                    universe_fingerprint TEXT NOT NULL,
                    requirement_set_id TEXT NOT NULL,
                    time_start TEXT NOT NULL,
                    time_end TEXT NOT NULL,
                    factor_refs_json TEXT NOT NULL,
                    manifest_ids_json TEXT NOT NULL,
                    manifest_hashes_json TEXT NOT NULL,
                    input_bindings_json TEXT NOT NULL,
                    factor_engine_closure_json TEXT NOT NULL,
                    alpha_engine_version TEXT NOT NULL,
                    alpha_code_hash TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    values_json TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL DEFAULT '[]',
                    validated_definition_id TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT 'local_ui_user',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(draft_id) REFERENCES alpha_drafts(draft_id),
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id),
                    FOREIGN KEY(universe_snapshot_id) REFERENCES universe_snapshots(universe_snapshot_id),
                    FOREIGN KEY(requirement_set_id) REFERENCES requirement_sets(requirement_set_id)
                );

                CREATE INDEX IF NOT EXISTS idx_alpha_previews_draft_created
                ON alpha_previews(draft_id, created_at DESC);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (19, "alpha_draft_and_preview_lifecycle", utc_now()),
            )
        if 20 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_authoring_events (
                    event_id TEXT PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stable_code TEXT NOT NULL DEFAULT '',
                    before_fingerprint TEXT NOT NULL DEFAULT '',
                    after_fingerprint TEXT NOT NULL DEFAULT '',
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id)
                );

                CREATE INDEX IF NOT EXISTS idx_research_authoring_events_project
                ON research_authoring_events(project_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_research_authoring_events_object
                ON research_authoring_events(object_type, object_id, created_at DESC);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (20, "research_authoring_audit", utc_now()),
            )
        if 21 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    entry_mode TEXT NOT NULL,
                    anchor_type TEXT NOT NULL DEFAULT '',
                    anchor_id TEXT NOT NULL DEFAULT '',
                    resolution_status TEXT NOT NULL DEFAULT 'RESOLVED',
                    status TEXT NOT NULL DEFAULT 'BRIEFING',
                    objective TEXT NOT NULL,
                    brief_json TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    original_baseline_run_id TEXT NOT NULL DEFAULT '',
                    current_branch_head_run_id TEXT NOT NULL DEFAULT '',
                    active_iteration_id TEXT NOT NULL DEFAULT '',
                    session_policy_json TEXT NOT NULL DEFAULT '{}',
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    pending_question_json TEXT NOT NULL DEFAULT '{}',
                    resume_state TEXT NOT NULL DEFAULT '',
                    internal_grant_id TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT 'local_user',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES research_projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS research_agent_iterations (
                    iteration_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PLANNED',
                    control_run_id TEXT NOT NULL DEFAULT '',
                    candidate_run_id TEXT NOT NULL DEFAULT '',
                    hypothesis_json TEXT NOT NULL DEFAULT '{}',
                    intervention_set_json TEXT NOT NULL DEFAULT '[]',
                    controlled_variables_json TEXT NOT NULL DEFAULT '[]',
                    change_set_json TEXT NOT NULL DEFAULT '{}',
                    invalidation_plan_json TEXT NOT NULL DEFAULT '{}',
                    metrics_before_json TEXT NOT NULL DEFAULT '{}',
                    metrics_after_json TEXT NOT NULL DEFAULT '{}',
                    comparison_json TEXT NOT NULL DEFAULT '{}',
                    decision TEXT NOT NULL DEFAULT '',
                    decision_reason TEXT NOT NULL DEFAULT '',
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES research_agent_sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS research_agent_session_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    iteration_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES research_agent_sessions(session_id)
                );

                CREATE INDEX IF NOT EXISTS idx_research_agent_sessions_updated
                ON research_agent_sessions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_agent_sessions_project
                ON research_agent_sessions(project_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_research_agent_iterations_session
                ON research_agent_iterations(session_id, sequence DESC);
                CREATE INDEX IF NOT EXISTS idx_research_agent_events_session
                ON research_agent_session_events(session_id, created_at DESC);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (21, "research_agent_sessions", utc_now()),
            )
        if 22 not in applied:
            library_asset_columns = self._column_names(conn, "research_library_assets")
            if "archived_at" not in library_asset_columns:
                conn.execute("ALTER TABLE research_library_assets ADD COLUMN archived_at TEXT")
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (22, "research_library_assets_archive", utc_now()),
            )
        if 23 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS library_groups (
                    group_id TEXT PRIMARY KEY,
                    asset_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(asset_type, name)
                );

                CREATE TABLE IF NOT EXISTS library_group_members (
                    asset_type TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (asset_type, asset_id),
                    FOREIGN KEY(group_id) REFERENCES library_groups(group_id)
                );

                CREATE INDEX IF NOT EXISTS idx_library_group_members_group
                    ON library_group_members(group_id);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (23, "library_groups", utc_now()),
            )
        if 24 not in applied:
            session_columns = self._column_names(conn, "research_agent_sessions")
            if "idempotency_key" not in session_columns:
                conn.execute(
                    "ALTER TABLE research_agent_sessions "
                    "ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_research_agent_sessions_idempotency
                ON research_agent_sessions(idempotency_key)
                WHERE idempotency_key <> ''
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (24, "research_agent_session_idempotency", utc_now()),
            )
        if 25 not in applied:
            existing_keys = {
                str(row[0])
                for row in conn.execute(
                    "SELECT idempotency_key FROM research_agent_sessions "
                    "WHERE idempotency_key <> ''"
                ).fetchall()
            }
            legacy_rows = conn.execute(
                """
                SELECT s.session_id, s.brief_json, s.created_by, p.title
                FROM research_agent_sessions AS s
                LEFT JOIN research_projects AS p ON p.project_id = s.project_id
                WHERE s.entry_mode='START' AND s.idempotency_key=''
                ORDER BY s.created_at ASC, s.session_id ASC
                """
            ).fetchall()
            for row in legacy_rows:
                try:
                    brief = json.loads(str(row["brief_json"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    brief = {}
                fingerprint = hashlib.sha256(
                    json_dumps(
                        {
                            "brief": brief if isinstance(brief, dict) else {},
                            "created_by": str(row["created_by"] or "local_user").strip()
                            or "local_user",
                            "title": str(row["title"] or "").strip(),
                        }
                    ).encode("utf-8")
                ).hexdigest()
                key = f"research-start:auto:{fingerprint}"
                if key in existing_keys:
                    continue
                conn.execute(
                    "UPDATE research_agent_sessions SET idempotency_key=? WHERE session_id=?",
                    (key, str(row["session_id"])),
                )
                existing_keys.add(key)
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (25, "research_agent_session_idempotency_backfill", utc_now()),
            )
        if 26 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS equity_security_master (
                    security_id TEXT PRIMARY KEY,
                    permno INTEGER UNIQUE,
                    permco INTEGER,
                    cik TEXT NOT NULL DEFAULT '',
                    cusip TEXT NOT NULL DEFAULT '',
                    issuer_name TEXT NOT NULL DEFAULT '',
                    security_name TEXT NOT NULL DEFAULT '',
                    security_type TEXT NOT NULL DEFAULT '',
                    share_type TEXT NOT NULL DEFAULT '',
                    share_class TEXT NOT NULL DEFAULT '',
                    primary_exchange TEXT NOT NULL DEFAULT '',
                    currency TEXT NOT NULL DEFAULT 'USD',
                    country TEXT NOT NULL DEFAULT 'US',
                    valid_from TEXT,
                    valid_to TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS equity_security_aliases (
                    security_id TEXT NOT NULL,
                    alias_type TEXT NOT NULL,
                    alias_value TEXT NOT NULL,
                    valid_from TEXT NOT NULL DEFAULT '',
                    valid_to TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (
                        security_id, alias_type, alias_value, valid_from, source
                    ),
                    FOREIGN KEY (security_id)
                        REFERENCES equity_security_master(security_id)
                );

                CREATE INDEX IF NOT EXISTS idx_equity_security_alias_lookup
                    ON equity_security_aliases(alias_type, alias_value, valid_from, valid_to);
                CREATE INDEX IF NOT EXISTS idx_equity_security_master_permco
                    ON equity_security_master(permco);
                CREATE INDEX IF NOT EXISTS idx_equity_security_master_cik
                    ON equity_security_master(cik);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (26, "equity_security_master", utc_now()),
            )
        if 27 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS crsp_import_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    source_path TEXT NOT NULL,
                    source_entry TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    dataset_prefix TEXT NOT NULL,
                    normalizer_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rows_processed INTEGER NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    output_counts_json TEXT NOT NULL DEFAULT '{}',
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    staging_root TEXT NOT NULL,
                    manifests_json TEXT NOT NULL DEFAULT '{}',
                    worker_id TEXT NOT NULL DEFAULT '',
                    heartbeat_at TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS crsp_import_partitions (
                    job_id TEXT NOT NULL,
                    dataset_key TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    partition_key TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    row_count INTEGER NOT NULL,
                    file_uri TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    min_event_time TEXT,
                    max_event_time TEXT,
                    quality_status TEXT NOT NULL DEFAULT 'PASS',
                    PRIMARY KEY(job_id, dataset_key, chunk_index),
                    FOREIGN KEY(job_id) REFERENCES crsp_import_jobs(job_id)
                );

                CREATE INDEX IF NOT EXISTS idx_crsp_import_jobs_status
                    ON crsp_import_jobs(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_crsp_import_partitions_job
                    ON crsp_import_partitions(job_id, dataset_key, chunk_index);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (27, "crsp_resumable_bulk_import", utc_now()),
            )
        if 28 not in applied:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sec_security_links (
                    link_id TEXT PRIMARY KEY,
                    security_id TEXT NOT NULL,
                    permno INTEGER NOT NULL,
                    permco INTEGER,
                    cik TEXT NOT NULL,
                    valid_from TEXT NOT NULL DEFAULT '',
                    valid_to TEXT NOT NULL DEFAULT '',
                    sec_ticker TEXT NOT NULL,
                    sec_exchange TEXT NOT NULL DEFAULT '',
                    evidence_type TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    source_version TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(security_id, cik, valid_from, source_version),
                    FOREIGN KEY(security_id) REFERENCES equity_security_master(security_id)
                );

                CREATE INDEX IF NOT EXISTS idx_sec_security_links_cik
                    ON sec_security_links(cik, valid_from, valid_to, status);
                CREATE INDEX IF NOT EXISTS idx_sec_security_links_permco
                    ON sec_security_links(permco, cik, status);

                CREATE TABLE IF NOT EXISTS sec_bulk_import_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    staging_root TEXT NOT NULL,
                    source_urls_json TEXT NOT NULL,
                    source_files_json TEXT NOT NULL DEFAULT '{}',
                    source_fingerprint TEXT NOT NULL DEFAULT '',
                    mapping_report_json TEXT NOT NULL DEFAULT '{}',
                    quality_report_json TEXT NOT NULL DEFAULT '{}',
                    entry_index INTEGER NOT NULL DEFAULT 0,
                    company_count INTEGER NOT NULL DEFAULT 0,
                    mapped_company_count INTEGER NOT NULL DEFAULT 0,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    partition_count INTEGER NOT NULL DEFAULT 0,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    worker_id TEXT NOT NULL DEFAULT '',
                    heartbeat_at TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS sec_bulk_import_partitions (
                    job_id TEXT NOT NULL,
                    partition_index INTEGER NOT NULL,
                    partition_key TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    row_count INTEGER NOT NULL,
                    file_uri TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    min_event_time TEXT,
                    max_event_time TEXT,
                    quality_status TEXT NOT NULL DEFAULT 'PASS',
                    PRIMARY KEY(job_id, partition_index),
                    FOREIGN KEY(job_id) REFERENCES sec_bulk_import_jobs(job_id)
                );

                CREATE INDEX IF NOT EXISTS idx_sec_bulk_jobs_status
                    ON sec_bulk_import_jobs(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_sec_bulk_partitions_job
                    ON sec_bulk_import_partitions(job_id, partition_index);
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_version, migration_name, applied_at) VALUES (?, ?, ?)",
                (28, "sec_authoritative_mapping_and_bulk_pit", utc_now()),
            )

    @staticmethod
    def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        return dict(row) if row is not None else None


def get_default_store() -> DataPlatformStore:
    """Return the additive metadata store used by the research platform."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        with _DEFAULT_STORE_LOCK:
            if _DEFAULT_STORE is None:
                _DEFAULT_STORE = DataPlatformStore()
    return _DEFAULT_STORE
