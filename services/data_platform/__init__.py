"""Foundational metadata services for DataTube's quantitative research layer.

The package is intentionally additive.  It owns research metadata and frozen
dataset manifests, while the existing realtime, VirtualRunner, and strategy
tables remain unchanged until a later adapter is ready.
"""

from .catalog_service import DatasetCatalogService
from .data_client import FrozenManifestData
from .data_capability_service import ResearchDataCapabilityService
from .input_candidate_resolver import (
    INPUT_CANDIDATE_SCHEMA_VERSION,
    FactorInputCandidateResolver,
)
from .backtest_contract import (
    BacktestCapabilities,
    BacktestExecutionSpec,
    ExecutionSpec,
    ExistingBacktestAdapter,
    CURRENT_HISTORY_BACKTEST_CAPABILITIES,
    audit_execution_spec,
)
from .binance_history_adapter import BinanceHistoryAdapter
from .canonical_bars import CanonicalBarsCommitter
from .canonical_dataset import CanonicalDatasetCommitter
from .polymarket_history import PolymarketHistoryPreparer
from .polymarket_task_executor import (
    POLYMARKET_EXPORT_TASK_TYPE,
    PolymarketResearchTaskExecutor,
    PolymarketResearchWorker,
)
from .openbb_history_adapter import OpenBBEquityHistoryAdapter
from .us_equity_archive import (
    DailySnapshotEquityImporter,
    scan_us_equity_archive,
    write_archive_inventory,
)
from .equity_security_master import EquitySecurityMasterService
from .crsp_ciz import CRSP_CIZ_NORMALIZER_VERSION, CrspCizNormalizer, EquityDataQualityGate
from .crsp_bulk_import import BULK_IMPORT_VERSION, CrspBulkImportService, run_crsp_import_job
from .sec_bulk_import import SEC_BULK_IMPORT_VERSION, SecBulkImportService, run_sec_bulk_import_job
from .sec_pit import FundamentalPointInTimeView, SEC_PIT_NORMALIZER_VERSION, SecPointInTimeNormalizer
from .equity_field_resolver import FIELD_CONTRACTS, EquityFieldResolver
from .equity_universe import HistoricalEquityUniverseService
from .openbb_task_executor import OPENBB_EXPORT_TASK_TYPE, OpenBBResearchTaskExecutor, OpenBBResearchWorker
from .factor_alpha import AlphaComponent, AlphaEngine, AlphaSpec, FactorEngine, FactorSpec
from .factor_pack import (
    ALPHA158_NO_VWAP_DISPLAY_NAME,
    ALPHA158_NO_VWAP_FACTOR_COUNT,
    ALPHA158_NO_VWAP_PACK_ID,
    FactorPackDefinition,
    FactorPackMemberSpec,
    FactorPackRegistry,
)
from .factor_formula import FactorFormulaCompilation, FactorFormulaCompiler, FormulaDiagnostic
from .factor_engine_v4 import (
    FACTOR_ENGINE_V4_CODE_HASH,
    FACTOR_ENGINE_V4_VERSION,
    FACTOR_GRAPH_CONTRACT_VERSION,
    FactorEngineV4,
    FactorGraphCompiler,
    FactorGraphSpec,
)
from .artifact_service import ArtifactService, ResearchArtifactMaterializer, content_hash_for_rows
from .instrument_registry import InstrumentRegistry, make_instrument_id
from .models import (
    DataRequirement,
    RequirementDependencyLink,
    RequirementSet,
    DatasetCatalogEntry,
    DatasetManifest,
    DatasetPartition,
    Instrument,
    UniverseDefinition,
    UniverseSnapshot,
)
from .universe_v2 import (
    UNIVERSE_ENGINE_VERSION,
    UNIVERSE_MEMBERSHIP_SCHEMA_VERSION,
    UNIVERSE_V2_COMPILED_SCHEMA_VERSION,
    UNIVERSE_V2_SCHEMA_VERSION,
    UniverseFieldContract,
    UniverseFieldRequirement,
    UniverseFieldRegistry,
    UniverseMembershipEngine,
    UniverseV2Compiler,
    universe_v2_capabilities,
)
from .requirement_service import DataRequirementService
from .requirement_compiler import REQUIREMENT_COMPILER_VERSION, RequirementCompiler
from .requirement_workspace_service import RequirementWorkspaceService, default_requirement_spec, normalize_requirement_spec
from .requirement_maintenance_service import RequirementMaintenanceService
from .input_bundle import ResearchInputBundleService
from .resolved_plan import ResolvedDataPlanService
from .research_backtest import RESEARCH_BACKTEST_CAPABILITIES, ResearchBacktestProvider, ResearchBacktestResult
from .portfolio import PortfolioEngine, PortfolioSpec
from .evaluation import AlphaEvaluator, EvaluationResult, EvaluationSpec, FactorEvaluator, FutureReturnBuilder
from .binance_backfill import (
    BINANCE_BACKFILL_TASK_TYPE,
    BinanceBackfillJobService,
    BinanceBackfillTaskExecutor,
    BinanceBackfillWorker,
    BinanceGapDetector,
)
from .provenance_service import ManifestProvenanceService, request_hash, sanitized_request
from .research_control_plane import ResearchControlPlane
from .run_contracts import (
    PREVIEW_FINGERPRINT_SCHEMA_VERSION,
    READINESS_RULE_VERSION,
    REASON_CODE_CATALOG_VERSION,
    BundleInputClosure,
    BundleInputMode,
    BundleIntegrityStatus,
    BundleLifecycleStatus,
    BundleReuseStatus,
    FrozenBundleStatus,
    HistoricalAuthorizationEvidence,
    IdempotencyConflictError,
    PreviewFingerprint,
    ReadinessCheck,
    ReadinessDimension,
    ReadinessDimensionResult,
    ReadinessReport,
    ReadinessStatus,
    RemediationCode,
    ResearchReasonCode,
    aggregate_readiness_status,
    build_preview_fingerprint,
    enforce_idempotent_run_request,
    is_preview_stale,
)
from .source_policy import SourcePolicy, SourcePolicyService
from .store import DataPlatformStore, get_default_store
from .universe_service import UniverseService
from .shared_universe_service import (
    SharedUniverseService,
    UniverseConflictError,
    UniverseResolutionError,
    UniverseSharedImpactError,
)
from .definition_registry import DefinitionRegistry, ResearchDefinition
from .factor_draft import (
    FACTOR_DRAFT_SCHEMA_VERSION,
    FACTOR_EDITOR_DOCUMENT_VERSION,
    FactorDraft,
    FactorDraftService,
    FactorDraftValidationError,
)
from .factor_preview import (
    FACTOR_PREVIEW_SCHEMA_VERSION,
    FactorPreviewError,
    FactorPreviewService,
)
from .factor_definition_executor import FactorDefinitionExecutor
from .alpha_factor_candidates import (
    ALPHA_FACTOR_CANDIDATE_SCHEMA_VERSION,
    AlphaFactorCandidateResolver,
)
from .alpha_draft import (
    ALPHA_DRAFT_SCHEMA_VERSION,
    ALPHA_EDITOR_DOCUMENT_VERSION,
    AlphaDraft,
    AlphaDraftService,
    AlphaDraftValidationError,
)
from .alpha_preview import (
    ALPHA_PREVIEW_SCHEMA_VERSION,
    AlphaPreviewError,
    AlphaPreviewService,
)
from .research_authoring_audit import ResearchAuthoringAudit
from .library_service import ResearchLibraryService
from .library_group_service import LibraryGroupService
from .manifest_resolver import (
    MANIFEST_RESOLVER_VERSION,
    SOURCE_SELECTION_POLICY_VERSION,
    DeterministicManifestResolver,
    ManifestResolution,
)
from .run_preview_service import ResearchRunPreviewService, SUPPORTED_RESEARCH_RUN_TYPES
from .research_run_service import (
    FormalResearchRunExecutor,
    PreviewStaleError,
    ReadinessBlockedError,
    ResearchRunService,
    ResearchRunWorker,
)
from .factor_pack_result_service import (
    FACTOR_PACK_RESULT_SCHEMA_VERSION,
    FactorPackRunResultService,
)
from .research_agent_authorization import (
    DEFAULT_RESEARCH_OPERATIONS,
    AuthorizationDecision,
    ResearchAgentAuthorization,
    ResearchAuthorizationError,
)
from .research_context_resolver import ResearchContextResolver, SUPPORTED_ANCHORS
from .research_agent_session import (
    DEFAULT_SESSION_POLICY,
    ITERATION_DECISIONS,
    ITERATION_STATES,
    NEED_HUMAN_REASONS,
    SESSION_STATES,
    ResearchAgentSessionService,
    normalize_research_brief,
)
from .research_semantics import (
    ALIGNED_RESEARCH_INTENT_SCHEMA_VERSION,
    CANDIDATE_SPEC_SCHEMA_VERSION,
    EVIDENCE_PROFILES,
    RESEARCH_CONTRACT_SCHEMA_VERSION,
    RESEARCH_RESULT_SCHEMA_VERSION,
    RESEARCHER_AVAILABLE_STOP_AT,
    RUN_TYPE_TO_STOP_AT,
    STOP_AT_TO_RUN_TYPE,
    ResearchContractService,
    ResearchSemanticError,
    align_research_intent,
    build_research_contract,
    infer_research_stop_at,
    normalize_candidate,
)
from .research_experiment_service import (
    EXPERIMENT_STATES,
    RESEARCH_DECISIONS,
    ResearchExperimentService,
)
from .partition_planner import ResearchPartitionPlanner, PartitionPlan, ResearchPartitionStrategy
from .checkpoint_manager import CheckpointManager, PartitionCheckpoint
from .partition_executor import PartitionedResearchExecutor

__all__ = [
    "DataPlatformStore",
    "BacktestCapabilities",
    "BacktestExecutionSpec",
    "ExecutionSpec",
    "BinanceHistoryAdapter",
    "CanonicalBarsCommitter",
    "CanonicalDatasetCommitter",
    "PolymarketHistoryPreparer",
    "POLYMARKET_EXPORT_TASK_TYPE",
    "PolymarketResearchTaskExecutor",
    "PolymarketResearchWorker",
    "OpenBBEquityHistoryAdapter",
    "DailySnapshotEquityImporter",
    "scan_us_equity_archive",
    "write_archive_inventory",
    "EquitySecurityMasterService",
    "CRSP_CIZ_NORMALIZER_VERSION",
    "CrspCizNormalizer",
    "EquityDataQualityGate",
    "SEC_PIT_NORMALIZER_VERSION",
    "SecPointInTimeNormalizer",
    "FundamentalPointInTimeView",
    "FIELD_CONTRACTS",
    "EquityFieldResolver",
    "HistoricalEquityUniverseService",
    "OPENBB_EXPORT_TASK_TYPE",
    "OpenBBResearchTaskExecutor",
    "OpenBBResearchWorker",
    "AlphaComponent",
    "AlphaEngine",
    "AlphaSpec",
    "ArtifactService",
    "CURRENT_HISTORY_BACKTEST_CAPABILITIES",
    "DataRequirement",
    "RequirementDependencyLink",
    "RequirementSet",
    "RequirementCompiler",
    "RequirementWorkspaceService",
    "RequirementMaintenanceService",
    "default_requirement_spec",
    "normalize_requirement_spec",
    "REQUIREMENT_COMPILER_VERSION",
    "ResearchInputBundleService",
    "ResolvedDataPlanService",
    "DatasetCatalogService",
    "BULK_IMPORT_VERSION",
    "CrspBulkImportService",
    "run_crsp_import_job",
    "SEC_BULK_IMPORT_VERSION",
    "SecBulkImportService",
    "run_sec_bulk_import_job",
    "DatasetCatalogEntry",
    "DatasetManifest",
    "DatasetPartition",
    "DataRequirementService",
    "FrozenManifestData",
    "ResearchDataCapabilityService",
    "INPUT_CANDIDATE_SCHEMA_VERSION",
    "FactorInputCandidateResolver",
    "FactorEngine",
    "FactorSpec",
    "FactorPackDefinition",
    "FactorPackMemberSpec",
    "FactorPackRegistry",
    "ALPHA158_NO_VWAP_DISPLAY_NAME",
    "ALPHA158_NO_VWAP_FACTOR_COUNT",
    "ALPHA158_NO_VWAP_PACK_ID",
    "FactorFormulaCompilation",
    "FactorFormulaCompiler",
    "FormulaDiagnostic",
    "FACTOR_ENGINE_V4_CODE_HASH",
    "FACTOR_ENGINE_V4_VERSION",
    "FACTOR_GRAPH_CONTRACT_VERSION",
    "FactorEngineV4",
    "FactorGraphCompiler",
    "FactorGraphSpec",
    "ExistingBacktestAdapter",
    "RESEARCH_BACKTEST_CAPABILITIES",
    "ResearchBacktestProvider",
    "ResearchBacktestResult",
    "ResearchArtifactMaterializer",
    "PortfolioEngine",
    "PortfolioSpec",
    "AlphaEvaluator",
    "EvaluationResult",
    "EvaluationSpec",
    "FactorEvaluator",
    "FutureReturnBuilder",
    "BINANCE_BACKFILL_TASK_TYPE",
    "BinanceBackfillJobService",
    "BinanceBackfillTaskExecutor",
    "BinanceBackfillWorker",
    "BinanceGapDetector",
    "ManifestProvenanceService",
    "request_hash",
    "sanitized_request",
    "ResearchControlPlane",
    "PREVIEW_FINGERPRINT_SCHEMA_VERSION",
    "READINESS_RULE_VERSION",
    "REASON_CODE_CATALOG_VERSION",
    "BundleInputClosure",
    "BundleInputMode",
    "BundleIntegrityStatus",
    "BundleLifecycleStatus",
    "BundleReuseStatus",
    "FrozenBundleStatus",
    "HistoricalAuthorizationEvidence",
    "IdempotencyConflictError",
    "PreviewFingerprint",
    "ReadinessCheck",
    "ReadinessDimension",
    "ReadinessDimensionResult",
    "ReadinessReport",
    "ReadinessStatus",
    "RemediationCode",
    "ResearchReasonCode",
    "aggregate_readiness_status",
    "build_preview_fingerprint",
    "enforce_idempotent_run_request",
    "is_preview_stale",
    "SourcePolicy",
    "SourcePolicyService",
    "content_hash_for_rows",
    "Instrument",
    "InstrumentRegistry",
    "UniverseDefinition",
    "UNIVERSE_ENGINE_VERSION",
    "UNIVERSE_MEMBERSHIP_SCHEMA_VERSION",
    "UNIVERSE_V2_COMPILED_SCHEMA_VERSION",
    "UNIVERSE_V2_SCHEMA_VERSION",
    "UniverseFieldContract",
    "UniverseFieldRequirement",
    "UniverseFieldRegistry",
    "UniverseMembershipEngine",
    "UniverseV2Compiler",
    "universe_v2_capabilities",
    "UniverseService",
    "SharedUniverseService",
    "UniverseConflictError",
    "UniverseResolutionError",
    "UniverseSharedImpactError",
    "UniverseSnapshot",
    "get_default_store",
    "make_instrument_id",
    "audit_execution_spec",
    "DefinitionRegistry",
    "ResearchDefinition",
    "FACTOR_DRAFT_SCHEMA_VERSION",
    "FACTOR_EDITOR_DOCUMENT_VERSION",
    "FactorDraft",
    "FactorDraftService",
    "FactorDraftValidationError",
    "FACTOR_PREVIEW_SCHEMA_VERSION",
    "FactorPreviewError",
    "FactorPreviewService",
    "FactorDefinitionExecutor",
    "ALPHA_FACTOR_CANDIDATE_SCHEMA_VERSION",
    "AlphaFactorCandidateResolver",
    "ALPHA_DRAFT_SCHEMA_VERSION",
    "ALPHA_EDITOR_DOCUMENT_VERSION",
    "AlphaDraft",
    "AlphaDraftService",
    "AlphaDraftValidationError",
    "ALPHA_PREVIEW_SCHEMA_VERSION",
    "AlphaPreviewError",
    "AlphaPreviewService",
    "ResearchAuthoringAudit",
    "ResearchLibraryService",
    "LibraryGroupService",
    "MANIFEST_RESOLVER_VERSION",
    "SOURCE_SELECTION_POLICY_VERSION",
    "DeterministicManifestResolver",
    "ManifestResolution",
    "ResearchRunPreviewService",
    "SUPPORTED_RESEARCH_RUN_TYPES",
    "PreviewStaleError",
    "ReadinessBlockedError",
    "ResearchRunService",
    "ResearchRunWorker",
    "FormalResearchRunExecutor",
    "FACTOR_PACK_RESULT_SCHEMA_VERSION",
    "FactorPackRunResultService",
    "DEFAULT_RESEARCH_OPERATIONS",
    "AuthorizationDecision",
    "ResearchAgentAuthorization",
    "ResearchAuthorizationError",
    "ResearchContextResolver",
    "SUPPORTED_ANCHORS",
    "DEFAULT_SESSION_POLICY",
    "ITERATION_DECISIONS",
    "ITERATION_STATES",
    "NEED_HUMAN_REASONS",
    "SESSION_STATES",
    "ResearchAgentSessionService",
    "normalize_research_brief",
    "ALIGNED_RESEARCH_INTENT_SCHEMA_VERSION",
    "CANDIDATE_SPEC_SCHEMA_VERSION",
    "EVIDENCE_PROFILES",
    "RESEARCH_CONTRACT_SCHEMA_VERSION",
    "RESEARCH_RESULT_SCHEMA_VERSION",
    "RESEARCHER_AVAILABLE_STOP_AT",
    "RUN_TYPE_TO_STOP_AT",
    "STOP_AT_TO_RUN_TYPE",
    "ResearchContractService",
    "ResearchSemanticError",
    "align_research_intent",
    "build_research_contract",
    "infer_research_stop_at",
    "normalize_candidate",
    "EXPERIMENT_STATES",
    "RESEARCH_DECISIONS",
    "ResearchExperimentService",
    "ResearchPartitionPlanner",
    "PartitionPlan",
    "ResearchPartitionStrategy",
    "CheckpointManager",
    "PartitionCheckpoint",
    "PartitionedResearchExecutor",
]
