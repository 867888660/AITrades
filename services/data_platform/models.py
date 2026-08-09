from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    asset_class: str
    venue: str
    market_type: str
    native_symbol: str
    display_symbol: str = ""
    display_name: str = ""
    underlying_id: str = ""
    base_asset: str = ""
    quote_asset: str = ""
    currency: str = ""
    condition_id: str = ""
    market_id: str = ""
    event_id: str = ""
    outcome_side: str = ""
    listing_time: Optional[str] = None
    delisting_time: Optional[str] = None
    timezone: str = "UTC"
    trading_calendar: str = "24x7"
    tick_size: Optional[float] = None
    lot_size: Optional[float] = None
    status: str = "ACTIVE"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataRequirement:
    requirement_id: str
    owner_type: str
    owner_id: str
    target_type: str
    instrument_ids: Tuple[str, ...]
    data_type: str
    frequency: str
    fields: Tuple[str, ...]
    history_mode: str
    history_start: Optional[str]
    history_end: Optional[str]
    lookback_value: Optional[int]
    lookback_unit: str
    refresh_mode: str
    refresh_interval_seconds: Optional[int]
    auto_backfill: bool
    usage_level: str
    priority: int
    status: str
    requirement_fingerprint: str
    adjustment: str = "NONE"
    time_semantics: str = "BAR_END_AVAILABLE_TIME"
    point_in_time_policy: str = "AS_OF"
    quality_policy: str = "STRICT"
    source_policy: str = "FIXED"
    source_selection_policy: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequirementDependencyLink:
    requirement_id: str
    origin_type: str
    origin_id: str
    origin_version: str
    dependency_path: Tuple[str, ...]


@dataclass(frozen=True)
class RequirementSet:
    requirement_set_id: str
    project_id: str
    version: int
    status: str
    compiler_version: str
    fingerprint: str
    source_specs: Tuple[Dict[str, Any], ...]
    context: Dict[str, Any]
    requirements: Tuple[DataRequirement, ...] = ()
    dependency_links: Tuple[RequirementDependencyLink, ...] = ()
    created_at: str = ""
    superseded_by_id: Optional[str] = None


@dataclass(frozen=True)
class DatasetCatalogEntry:
    dataset_id: str
    instrument_id: str
    data_type: str
    frequency: str
    source: str
    start_time: Optional[str]
    end_time: Optional[str]
    last_complete_time: Optional[str]
    row_count: int
    gap_count: int
    status: str
    quality_status: str
    schema_version: str
    storage_path: str
    latest_manifest_id: Optional[str] = None
    updated_at: Optional[str] = None
    fields: Tuple[str, ...] = ()
    adjustment: str = "NONE"
    time_semantics: str = "BAR_END_AVAILABLE_TIME"
    point_in_time_policy: str = "AS_OF"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetPartition:
    partition_id: str
    manifest_id: str
    partition_key: str
    start_time: Optional[str]
    end_time: Optional[str]
    row_count: int
    file_uri: str
    file_size: int
    checksum: str
    min_event_time: Optional[str]
    max_event_time: Optional[str]
    quality_status: str


@dataclass(frozen=True)
class DatasetManifest:
    manifest_id: str
    dataset_id: str
    dataset_fingerprint: str
    version: int
    schema_version: str
    status: str
    manifest_hash: str
    created_at: str
    committed_at: Optional[str]
    partitions: Tuple[DatasetPartition, ...] = ()


@dataclass(frozen=True)
class UniverseDefinition:
    universe_definition_id: str
    name: str
    version: str
    universe_type: str
    parameters: Dict[str, Any]
    selection_rule_version: str
    fingerprint: str
    status: str = "ACTIVE"
    created_at: str = ""
    owner_project_id: str = ""
    library_scope: str = "GLOBAL"


@dataclass(frozen=True)
class UniverseSnapshot:
    universe_snapshot_id: str
    universe_definition_id: str
    as_of_time: str
    actual_instrument_ids: Tuple[str, ...]
    selection_inputs: Dict[str, Any]
    selection_rule_version: str
    dataset_manifest_ids: Tuple[str, ...]
    fingerprint: str
    created_at: str = ""


@dataclass(frozen=True)
class ResearchArtifact:
    artifact_id: str
    project_id: str
    artifact_type: str
    logical_name: str
    version: int
    status: str
    content_uri: str
    content_hash: str
    schema_version: str
    created_by_run_id: str
    created_by_task_id: str
    spec_hash: str = ""
    engine_version: str = ""
    code_hash: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
