from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable

from .artifact_service import ArtifactService
from .catalog_service import DatasetCatalogService
from .data_client import FrozenManifestData
from .store import BASE_DIR, DataPlatformStore, json_dumps
from services.history_storage_service import get_data_platform_storage_root


class ResearchInputBundleService:
    """Freeze the existing Manifest/Universe/Policy graph as one Artifact."""

    def __init__(self, store: DataPlatformStore, output_root: str | Path | None = None):
        self.store = store
        self.artifacts = ArtifactService(store)
        self.catalog = DatasetCatalogService(store)
        self.output_root = Path(output_root or get_data_platform_storage_root() / "research_artifacts" / "input_bundles")

    def create(self, *, project_id: str, logical_name: str, manifest_ids: Iterable[str],
               universe_snapshot_id: str = "", requirement_set_id: str = "",
               resolved_plan_id: str = "", policy_versions: dict[str, Any] | None = None,
               compiler_version: str = "", canonicalizer_version: str = ""):
        manifests = sorted({str(item).strip() for item in manifest_ids if str(item).strip()})
        if not project_id or not logical_name or not manifests:
            raise ValueError("project_id, logical_name, and manifest_ids are required")
        descriptors = []
        dependencies = []
        for manifest_id in manifests:
            manifest = self.catalog.get_manifest(manifest_id)
            if manifest is None or manifest.status != "READY":
                raise ValueError(f"bundle requires READY Manifest: {manifest_id}")
            FrozenManifestData(self.store, manifest_id).verify()
            descriptors.append({"manifest_id": manifest_id, "manifest_hash": manifest.manifest_hash,
                                "dataset_id": manifest.dataset_id, "schema_version": manifest.schema_version})
            dependencies.append({"parent_id": manifest_id, "parent_type": "DATASET_MANIFEST", "dependency_type": "INPUT_DATA"})
        payload = {"project_id": project_id, "manifests": descriptors,
                   "universe_snapshot_id": universe_snapshot_id, "requirement_set_id": requirement_set_id,
                   "resolved_plan_id": resolved_plan_id, "policy_versions": policy_versions or {},
                   "compiler_version": compiler_version, "canonicalizer_version": canonicalizer_version}
        content_hash = hashlib.sha256(json_dumps(payload).encode()).hexdigest()
        if universe_snapshot_id:
            dependencies.append({"parent_id": universe_snapshot_id, "parent_type": "UNIVERSE_SNAPSHOT", "dependency_type": "INPUT_UNIVERSE"})
        if requirement_set_id:
            dependencies.append({"parent_id": requirement_set_id, "parent_type": "REQUIREMENT_SET", "dependency_type": "INPUT_REQUIREMENTS"})
        if resolved_plan_id:
            dependencies.append({"parent_id": resolved_plan_id, "parent_type": "RESOLVED_DATA_PLAN", "dependency_type": "INPUT_PLAN"})
        self.output_root.mkdir(parents=True, exist_ok=True)
        path = self.output_root / f"sha256-{content_hash}.json"
        if not path.exists():
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        artifact = self.artifacts.create(artifact_type="RESEARCH_INPUT_BUNDLE", logical_name=logical_name,
            content_uri=str(path), content_hash=content_hash, schema_version="research_input_bundle.v1",
            project_id=project_id, spec_hash=content_hash, metadata=payload, dependencies=dependencies)
        self.artifacts.pin(artifact.artifact_id, owner_type="RESEARCH_PROJECT", owner_id=project_id,
                           reason="frozen research input")
        return artifact

    def verify(self, artifact_id: str) -> dict[str, Any]:
        artifact = self.artifacts.get(artifact_id)
        if not artifact or artifact.artifact_type != "RESEARCH_INPUT_BUNDLE":
            raise ValueError("research input bundle not found")
        path = Path(artifact.content_uri)
        payload = json.loads(path.read_text(encoding="utf-8"))
        actual = hashlib.sha256(json_dumps(payload).encode()).hexdigest()
        if actual != artifact.content_hash:
            raise ValueError("research input bundle content hash mismatch")
        for item in payload["manifests"]:
            FrozenManifestData(self.store, item["manifest_id"]).verify()
        return {"artifact_id": artifact_id, "bundle_hash": actual, "manifest_count": len(payload["manifests"]), "status": "READY"}
