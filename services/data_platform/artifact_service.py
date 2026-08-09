from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import ResearchArtifact
from .store import BASE_DIR, DataPlatformStore, json_dumps


ARTIFACT_TYPES = {
    "DATASET_MANIFEST",
    "UNIVERSE_SNAPSHOT",
    "FACTOR_VALUES",
    "FACTOR_EVALUATION",
    "ALPHA_VALUES",
    "ALPHA_EVALUATION",
    "PORTFOLIO_TARGETS",
    "POSITION_SERIES",
    "EQUITY_SERIES",
    "DRAWDOWN_SERIES",
    "BACKTEST_ORDERS",
    "BACKTEST_RESULT",
    "RESEARCH_REPORT",
    "RESEARCH_INPUT_BUNDLE",
    "RESOLVED_DATA_PLAN",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_name(value: str) -> str:
    result = "".join(char if char.isalnum() or char in {".", "-", "_"} else "_" for char in value)
    return result.strip("._") or "artifact"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_hash_for_rows(
    rows: Iterable[dict[str, Any]],
    *,
    schema_version: str,
    identity_context: Optional[dict[str, Any]] = None,
) -> str:
    serialized = sorted(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        for row in rows
    )
    digest = hashlib.sha256()
    # Preserve the original v1 hash material when no identity context is
    # supplied. New artifacts include their complete semantic identity so a
    # code or engine change cannot accidentally reuse an old artifact.
    digest.update(b'{"rows":[')
    for index, item in enumerate(serialized):
        if index:
            digest.update(b",")
        digest.update(item.encode("utf-8"))
    digest.update(b'],"schema_version":')
    digest.update(json_dumps(schema_version).encode("utf-8"))
    if identity_context:
        digest.update(b',"identity_context":')
        digest.update(json_dumps(identity_context).encode("utf-8"))
    digest.update(b"}")
    return digest.hexdigest()


class ArtifactService:
    """Immutable research artifact metadata and lineage service."""

    def __init__(self, store: DataPlatformStore):
        self.store = store

    def create(
        self,
        *,
        artifact_type: str,
        logical_name: str,
        content_uri: str,
        content_hash: str,
        schema_version: str,
        project_id: str = "",
        status: str = "READY",
        created_by_run_id: str = "",
        created_by_task_id: str = "",
        spec_hash: str = "",
        engine_version: str = "",
        code_hash: str = "",
        metadata: Optional[dict[str, Any]] = None,
        dependencies: Iterable[dict[str, str]] = (),
    ) -> ResearchArtifact:
        artifact_type = _clean(artifact_type).upper()
        logical_name = _clean(logical_name)
        content_uri = _clean(content_uri)
        content_hash = _clean(content_hash)
        schema_version = _clean(schema_version)
        if not artifact_type or not logical_name or not content_uri or not content_hash or not schema_version:
            raise ValueError("artifact_type, logical_name, content_uri, content_hash, and schema_version are required")
        if artifact_type not in ARTIFACT_TYPES:
            raise ValueError(f"unsupported research artifact type: {artifact_type}")
        metadata = dict(metadata or {})
        spec_hash = _clean(spec_hash)
        engine_version = _clean(engine_version)
        code_hash = _clean(code_hash)
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT artifact_id FROM research_artifacts WHERE artifact_type = ? AND logical_name = ? AND content_hash = ?",
                (artifact_type, logical_name, content_hash),
            ).fetchone()
            if existing:
                artifact_id = str(existing[0])
            else:
                version_row = conn.execute(
                    "SELECT COALESCE(MAX(artifact_version), 0) + 1 FROM research_artifacts WHERE artifact_type = ? AND logical_name = ?",
                    (artifact_type, logical_name),
                ).fetchone()
                version = int(version_row[0])
                artifact_id = f"artifact_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO research_artifacts(
                        artifact_id, project_id, artifact_type, logical_name,
                        artifact_version, status, content_uri, content_hash,
                        schema_version, created_by_run_id, created_by_task_id,
                        spec_hash, engine_version, code_hash, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        _clean(project_id),
                        artifact_type,
                        logical_name,
                        version,
                        _clean(status).upper() or "READY",
                        content_uri,
                        content_hash,
                        schema_version,
                        _clean(created_by_run_id),
                        _clean(created_by_task_id),
                        spec_hash,
                        engine_version,
                        code_hash,
                        json_dumps(metadata),
                        now,
                    ),
                )
            for dependency in dependencies:
                parent_id = _clean(dependency.get("parent_id"))
                parent_type = _clean(dependency.get("parent_type"))
                dependency_type = _clean(dependency.get("dependency_type") or "INPUT")
                if not parent_id or not parent_type:
                    raise ValueError("artifact dependencies require parent_id and parent_type")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO artifact_dependencies(
                        child_artifact_id, parent_id, parent_type, dependency_type
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (artifact_id, parent_id, parent_type, dependency_type),
                )
        return self.get(artifact_id)  # type: ignore[return-value]

    def get(self, artifact_id: str) -> Optional[ResearchArtifact]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_artifacts WHERE artifact_id = ?",
                (_clean(artifact_id),),
            ).fetchone()
        return self._from_row(row) if row else None

    def list(self, *, artifact_type: str = "", logical_name: str = "", limit: int = 200) -> list[ResearchArtifact]:
        clauses = []
        params: list[Any] = []
        if _clean(artifact_type):
            clauses.append("artifact_type = ?")
            params.append(_clean(artifact_type).upper())
        if _clean(logical_name):
            clauses.append("logical_name = ?")
            params.append(_clean(logical_name))
        params.append(max(1, min(int(limit), 1000)))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_artifacts{where} ORDER BY created_at DESC, artifact_version DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def dependencies(self, artifact_id: str) -> list[dict[str, str]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT parent_id, parent_type, dependency_type
                FROM artifact_dependencies
                WHERE child_artifact_id = ?
                ORDER BY parent_type, parent_id, dependency_type
                """,
                (_clean(artifact_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def dependencies_many(self, artifact_ids: Iterable[str]) -> dict[str, list[dict[str, str]]]:
        ids = [str(item).strip() for item in artifact_ids if str(item).strip()]
        if not ids:
            return {}
        result = {artifact_id: [] for artifact_id in ids}
        with self.store.connection() as conn:
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT child_artifact_id, parent_id, parent_type, dependency_type
                    FROM artifact_dependencies
                    WHERE child_artifact_id IN ({placeholders})
                    ORDER BY child_artifact_id, parent_type, parent_id, dependency_type
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    child_id = str(item.pop("child_artifact_id"))
                    result.setdefault(child_id, []).append(item)
        return result

    def pin(self, artifact_id: str, *, owner_type: str, owner_id: str, reason: str = "") -> None:
        with self.store.transaction(immediate=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM research_artifacts WHERE artifact_id = ?",
                (_clean(artifact_id),),
            ).fetchone()
            if not exists:
                raise ValueError(f"artifact not found: {artifact_id}")
            conn.execute(
                """
                INSERT OR REPLACE INTO artifact_pins(
                    artifact_id, pin_owner_type, pin_owner_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (_clean(artifact_id), _clean(owner_type), _clean(owner_id), _clean(reason), _now()),
            )

    @staticmethod
    def _from_row(row: Any) -> ResearchArtifact:
        return ResearchArtifact(
            artifact_id=str(row["artifact_id"]),
            project_id=str(row["project_id"]),
            artifact_type=str(row["artifact_type"]),
            logical_name=str(row["logical_name"]),
            version=int(row["artifact_version"]),
            status=str(row["status"]),
            content_uri=str(row["content_uri"]),
            content_hash=str(row["content_hash"]),
            schema_version=str(row["schema_version"]),
            created_by_run_id=str(row["created_by_run_id"]),
            created_by_task_id=str(row["created_by_task_id"]),
            spec_hash=str(row["spec_hash"]),
            engine_version=str(row["engine_version"]),
            code_hash=str(row["code_hash"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=str(row["created_at"]),
        )


class ResearchArtifactMaterializer:
    """Write Factor/Alpha rows to immutable Parquet and register lineage."""

    def __init__(self, store: DataPlatformStore, *, root: str | Path | None = None):
        self.store = store
        self.root = Path(root or (BASE_DIR / "storage"))
        self.artifacts = ArtifactService(store)

    def materialize_rows(
        self,
        *,
        artifact_type: str,
        logical_name: str,
        rows: Iterable[dict[str, Any]],
        schema_version: str,
        output_folder: str,
        dependencies: Iterable[dict[str, str]] = (),
        project_id: str = "",
        created_by_run_id: str = "",
        created_by_task_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
        spec_hash: str = "",
        engine_version: str = "",
        code_hash: str = "",
        identity_context: Optional[dict[str, Any]] = None,
        allow_empty: bool = False,
        empty_columns: Iterable[str] = (),
    ) -> ResearchArtifact:
        row_list = [dict(row) for row in rows]
        if not row_list and not allow_empty:
            raise ValueError("cannot materialize an empty artifact")
        content_hash = content_hash_for_rows(
            row_list,
            schema_version=schema_version,
            identity_context=identity_context,
        )
        output_dir = self.root / _safe_name(output_folder) / f"{_safe_name(logical_name)}"
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"content-{content_hash[:20]}.parquet"
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("Artifact materialization requires pyarrow") from exc
        if target.exists():
            existing_rows = pq.ParquetFile(target).read().to_pylist()
            existing_hash = content_hash_for_rows(
                existing_rows,
                schema_version=schema_version,
                identity_context=identity_context,
            )
            if existing_hash != content_hash:
                raise ValueError(f"immutable artifact content mismatch: {target}")
        else:
            temp = output_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
            table = pa.Table.from_pylist(row_list) if row_list else pa.table({
                str(column): pa.array([], type=pa.string()) for column in empty_columns
            })
            if not table.schema.names:
                raise ValueError("empty artifacts require at least one declared column")
            pq.write_table(table, temp, compression="zstd")
            try:
                temp.rename(target)
            except FileExistsError:
                temp.unlink(missing_ok=True)
                existing_rows = pq.ParquetFile(target).read().to_pylist()
                if content_hash_for_rows(
                    existing_rows,
                    schema_version=schema_version,
                    identity_context=identity_context,
                ) != content_hash:
                    raise ValueError(f"immutable artifact content mismatch: {target}")
        artifact_metadata = dict(metadata or {})
        artifact_metadata["physical_checksum"] = f"sha256:{_sha256_file(target)}"
        artifact_metadata["file_size"] = target.stat().st_size
        content_uri = target.relative_to(BASE_DIR).as_posix() if target.is_relative_to(BASE_DIR) else str(target)
        return self.artifacts.create(
            artifact_type=artifact_type,
            logical_name=logical_name,
            content_uri=content_uri,
            content_hash=content_hash,
            schema_version=schema_version,
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            created_by_task_id=created_by_task_id,
            spec_hash=spec_hash,
            engine_version=engine_version,
            code_hash=code_hash,
            metadata=artifact_metadata,
            dependencies=dependencies,
        )

    def materialize_factor(
        self,
        *,
        spec: Any,
        values_by_instrument: dict[str, list[dict[str, Any]]],
        dataset_manifest_ids: Iterable[str],
        universe_snapshot_id: str = "",
        project_id: str = "",
        created_by_run_id: str = "",
    ) -> ResearchArtifact:
        dataset_manifest_ids = sorted({str(item) for item in dataset_manifest_ids})
        rows = [row for instrument_rows in values_by_instrument.values() for row in instrument_rows]
        spec_hash = str(getattr(spec, "spec_hash", "") or "")
        engine_version = str(getattr(spec, "engine_version", "") or "")
        code_hash = str(getattr(spec, "code_hash", "") or "")
        identity_context = {
            "factor_spec_hash": spec_hash,
            "dataset_manifest_ids": dataset_manifest_ids,
            "universe_snapshot_id": str(universe_snapshot_id or ""),
            "engine_version": engine_version,
            "code_hash": code_hash,
            "schema_version": "factor-values.v2",
        }
        dependencies = [
            {"parent_id": manifest_id, "parent_type": "DATASET_MANIFEST", "dependency_type": "INPUT_DATASET"}
            for manifest_id in dataset_manifest_ids
        ]
        if universe_snapshot_id:
            dependencies.append({
                "parent_id": str(universe_snapshot_id),
                "parent_type": "UNIVERSE_SNAPSHOT",
                "dependency_type": "INPUT_UNIVERSE",
            })
        return self.materialize_rows(
            artifact_type="FACTOR_VALUES",
            logical_name=str(spec.name),
            rows=rows,
            schema_version="factor-values.v2",
            output_folder="factors",
            dependencies=dependencies,
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            spec_hash=spec_hash,
            engine_version=engine_version,
            code_hash=code_hash,
            identity_context=identity_context,
            metadata={
                "factor_name": str(spec.name),
                "factor_version": str(spec.version),
                "operator": str(spec.operator),
                "input_field": str(spec.input_field),
                "window": int(spec.window),
                "instrument_count": len(values_by_instrument),
                "row_count": len(rows),
                "universe_snapshot_id": str(universe_snapshot_id or ""),
                "factor_spec": spec.to_dict() if hasattr(spec, "to_dict") else {},
                "artifact_fingerprint": hashlib.sha256(json_dumps(identity_context).encode("utf-8")).hexdigest(),
            },
        )

    def materialize_alpha(
        self,
        *,
        spec: Any,
        signals: Iterable[dict[str, Any]],
        factor_artifact_ids: Iterable[str],
        dataset_manifest_ids: Iterable[str] = (),
        universe_snapshot_id: str = "",
        project_id: str = "",
        created_by_run_id: str = "",
    ) -> ResearchArtifact:
        factor_artifact_ids = [str(item) for item in factor_artifact_ids]
        dataset_manifest_ids = [str(item) for item in dataset_manifest_ids]
        spec_hash = str(getattr(spec, "spec_hash", "") or "")
        engine_version = str(getattr(spec, "engine_version", "") or "")
        code_hash = str(getattr(spec, "code_hash", "") or "")
        rows: list[dict[str, Any]] = []
        for signal in signals:
            event_time = signal.get("as_of_time")
            scores = signal.get("scores") if isinstance(signal.get("scores"), dict) else {}
            weights = signal.get("weights") if isinstance(signal.get("weights"), dict) else {}
            for instrument_id, score in sorted(scores.items()):
                rows.append({
                    "instrument_id": str(instrument_id),
                    "as_of_time": event_time,
                    "available_time": signal.get("available_time") or event_time,
                    "alpha_name": str(spec.name),
                    "alpha_version": str(spec.version),
                    "score": score,
                    "rank": (signal.get("ranks") or {}).get(instrument_id),
                    "percentile": (signal.get("percentiles") or {}).get(instrument_id),
                    "target_weight": weights.get(instrument_id, 0.0),
                    "coverage": signal.get("coverage"),
                    "universe_snapshot_id": signal.get("universe_snapshot_id") or universe_snapshot_id,
                    "quality_status": signal.get("quality_status") or "PASS",
                })
        dependencies = [
            {"parent_id": str(artifact_id), "parent_type": "RESEARCH_ARTIFACT", "dependency_type": "INPUT_FACTOR"}
            for artifact_id in factor_artifact_ids
        ]
        dependencies.extend(
            {"parent_id": str(manifest_id), "parent_type": "DATASET_MANIFEST", "dependency_type": "INPUT_DATASET"}
            for manifest_id in dataset_manifest_ids
        )
        if universe_snapshot_id:
            dependencies.append({
                "parent_id": str(universe_snapshot_id),
                "parent_type": "UNIVERSE_SNAPSHOT",
                "dependency_type": "INPUT_UNIVERSE",
            })
        identity_context = {
            "alpha_spec_hash": spec_hash,
            "factor_artifact_ids": sorted(factor_artifact_ids),
            "dataset_manifest_ids": sorted(dataset_manifest_ids),
            "universe_snapshot_id": str(universe_snapshot_id or ""),
            "engine_version": engine_version,
            "code_hash": code_hash,
            "schema_version": "alpha-output.v2",
        }
        return self.materialize_rows(
            artifact_type="ALPHA_VALUES",
            logical_name=str(spec.name),
            rows=rows,
            schema_version="alpha-output.v2",
            output_folder="alphas",
            dependencies=dependencies,
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            spec_hash=spec_hash,
            engine_version=engine_version,
            code_hash=code_hash,
            identity_context=identity_context,
            metadata={
                "alpha_name": str(spec.name),
                "alpha_version": str(spec.version),
                "row_count": len(rows),
                "factor_artifact_ids": list(factor_artifact_ids),
                "universe_snapshot_id": str(universe_snapshot_id or ""),
                "alpha_spec": spec.to_dict() if hasattr(spec, "to_dict") else {},
                "artifact_fingerprint": hashlib.sha256(json_dumps(identity_context).encode("utf-8")).hexdigest(),
            },
        )

    def materialize_portfolio_targets(
        self,
        *,
        spec: Any,
        targets: Iterable[dict[str, Any]],
        alpha_artifact_id: str,
        universe_snapshot_id: str,
        project_id: str = "",
        created_by_run_id: str = "",
    ) -> ResearchArtifact:
        target_list = [dict(item) for item in targets]
        rows: list[dict[str, Any]] = []
        for target in target_list:
            weights = target.get("weights") if isinstance(target.get("weights"), dict) else {}
            scores = target.get("raw_scores") if isinstance(target.get("raw_scores"), dict) else {}
            eligible_scores = target.get("eligible_scores") if isinstance(target.get("eligible_scores"), dict) else {}
            for instrument_id in sorted(set(weights) | set(scores)):
                rows.append({
                    "instrument_id": str(instrument_id),
                    "as_of_time": target.get("as_of_time"),
                    "available_time": target.get("available_time") or target.get("as_of_time"),
                    "raw_score": scores.get(instrument_id),
                    "target_weight": weights.get(instrument_id, 0.0),
                    "selected": instrument_id in weights,
                    "eligible": instrument_id in eligible_scores,
                    "target_state": target.get("target_state") or ("INVESTED" if weights else "FLAT"),
                    "selection_reason": target.get("selection_reason") or "",
                    "universe_snapshot_id": target.get("universe_snapshot_id") or universe_snapshot_id,
                    "portfolio_spec_hash": str(getattr(spec, "spec_hash", "") or ""),
                })
        identity_context = {
            "portfolio_spec_hash": str(getattr(spec, "spec_hash", "") or ""),
            "alpha_artifact_id": str(alpha_artifact_id),
            "universe_snapshot_id": str(universe_snapshot_id),
            "engine_version": str(getattr(spec, "engine_version", "") or ""),
            "code_hash": str(getattr(spec, "code_hash", "") or ""),
            "schema_version": "portfolio-targets.v1",
        }
        return self.materialize_rows(
            artifact_type="PORTFOLIO_TARGETS",
            logical_name=f"{getattr(spec, 'selection_method', 'portfolio')}_{getattr(spec, 'top_n', '')}",
            rows=rows,
            schema_version="portfolio-targets.v1",
            output_folder="portfolio_targets",
            dependencies=[
                {"parent_id": str(alpha_artifact_id), "parent_type": "RESEARCH_ARTIFACT", "dependency_type": "INPUT_ALPHA"},
                {"parent_id": str(universe_snapshot_id), "parent_type": "UNIVERSE_SNAPSHOT", "dependency_type": "INPUT_UNIVERSE"},
            ],
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            spec_hash=identity_context["portfolio_spec_hash"],
            engine_version=identity_context["engine_version"],
            code_hash=identity_context["code_hash"],
            identity_context=identity_context,
            metadata={
                "portfolio_spec": spec.to_dict() if hasattr(spec, "to_dict") else {},
                "target_count": len(target_list),
                "invested_target_count": sum(1 for item in target_list if item.get("weights")),
                "flat_target_count": sum(1 for item in target_list if not item.get("weights")),
                "row_count": len(rows),
                "universe_snapshot_id": str(universe_snapshot_id),
                "artifact_fingerprint": hashlib.sha256(json_dumps(identity_context).encode("utf-8")).hexdigest(),
            },
        )

    def materialize_backtest(
        self,
        *,
        logical_name: str,
        result: Any,
        portfolio_target_artifact_id: str,
        project_id: str = "",
        created_by_run_id: str = "",
    ) -> dict[str, ResearchArtifact]:
        metrics = dict(result.metrics)
        identity_context = {
            "dataset_manifest_ids": list(result.dataset_manifest_ids),
            "universe_snapshot_ids": list(result.universe_snapshot_ids),
            "factor_artifact_ids": list(result.factor_artifact_ids),
            "alpha_artifact_ids": list(result.alpha_artifact_ids),
            "portfolio_target_artifact_id": str(portfolio_target_artifact_id),
            "portfolio_spec_hash": metrics.get("portfolio_spec_hash", ""),
            "execution_spec_hash": metrics.get("execution_spec_hash", ""),
            "engine_version": metrics.get("engine_version", ""),
            "code_hash": metrics.get("code_hash", ""),
            "random_seed": metrics.get("random_seed", 0),
            "input_bundle_id": str(getattr(result, "input_bundle_id", "") or ""),
        }
        common_dependencies = [
            {"parent_id": str(portfolio_target_artifact_id), "parent_type": "RESEARCH_ARTIFACT", "dependency_type": "INPUT_PORTFOLIO_TARGETS"},
        ]
        common_dependencies.extend(
            {"parent_id": str(item), "parent_type": "DATASET_MANIFEST", "dependency_type": "INPUT_DATASET"}
            for item in result.dataset_manifest_ids
        )
        if getattr(result, "input_bundle_id", ""):
            common_dependencies.append({
                "parent_id": str(result.input_bundle_id),
                "parent_type": "RESEARCH_INPUT_BUNDLE",
                "dependency_type": "INPUT_BUNDLE",
            })
        common_dependencies.extend(
            {"parent_id": str(item), "parent_type": "UNIVERSE_SNAPSHOT", "dependency_type": "INPUT_UNIVERSE"}
            for item in result.universe_snapshot_ids
        )
        equity_rows = [dict(item) for item in getattr(result, "equity_curve", ()) or ()]
        position_rows: list[dict[str, Any]] = []
        compact_equity_rows: list[dict[str, Any]] = []
        for point in equity_rows:
            event_time = point.get("event_time")
            equity = point.get("equity")
            positions = point.get("positions") if isinstance(point.get("positions"), dict) else {}
            position_values = point.get("position_values") if isinstance(point.get("position_values"), dict) else {}
            position_weights = point.get("position_weights") if isinstance(point.get("position_weights"), dict) else {}
            for instrument_id in sorted(set(positions) | set(position_values) | set(position_weights)):
                position_rows.append({
                    "event_time": event_time,
                    "instrument_id": str(instrument_id),
                    "quantity": positions.get(instrument_id, 0.0),
                    "market_value": position_values.get(instrument_id, 0.0),
                    "actual_weight": position_weights.get(instrument_id, 0.0),
                    "equity": equity,
                })
            compact_equity_rows.append({
                "event_time": event_time,
                "equity": equity,
                "cash": point.get("cash"),
                "cash_ratio": point.get("cash_ratio"),
                "gross_exposure": point.get("gross_exposure"),
            })
        drawdown_rows = [
            dict(item)
            for item in getattr(result, "drawdown_curve", ()) or ()
        ]
        positions_artifact = self.materialize_rows(
            artifact_type="POSITION_SERIES",
            logical_name=logical_name,
            rows=position_rows,
            schema_version="position-series.v1",
            output_folder="alpha_runs/positions",
            dependencies=common_dependencies,
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            spec_hash=str(metrics.get("portfolio_spec_hash") or ""),
            engine_version=str(metrics.get("engine_version") or ""),
            code_hash=str(metrics.get("code_hash") or ""),
            identity_context={**identity_context, "artifact_type": "POSITION_SERIES"},
            metadata={"row_count": len(position_rows), **identity_context},
            allow_empty=True,
            empty_columns=("event_time", "instrument_id", "quantity", "market_value", "actual_weight", "equity"),
        )
        equity_artifact = self.materialize_rows(
            artifact_type="EQUITY_SERIES",
            logical_name=logical_name,
            rows=compact_equity_rows,
            schema_version="equity-series.v1",
            output_folder="alpha_runs/equity",
            dependencies=common_dependencies,
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            spec_hash=str(metrics.get("execution_spec_hash") or ""),
            engine_version=str(metrics.get("engine_version") or ""),
            code_hash=str(metrics.get("code_hash") or ""),
            identity_context={**identity_context, "artifact_type": "EQUITY_SERIES"},
            metadata={"row_count": len(compact_equity_rows), **identity_context},
            allow_empty=True,
            empty_columns=("event_time", "equity", "cash", "cash_ratio", "gross_exposure"),
        )
        drawdown_artifact = self.materialize_rows(
            artifact_type="DRAWDOWN_SERIES",
            logical_name=logical_name,
            rows=drawdown_rows,
            schema_version="drawdown-series.v1",
            output_folder="alpha_runs/drawdown",
            dependencies=common_dependencies,
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            spec_hash=str(metrics.get("execution_spec_hash") or ""),
            engine_version=str(metrics.get("engine_version") or ""),
            code_hash=str(metrics.get("code_hash") or ""),
            identity_context={**identity_context, "artifact_type": "DRAWDOWN_SERIES"},
            metadata={"row_count": len(drawdown_rows), **identity_context},
            allow_empty=True,
            empty_columns=("event_time", "equity", "peak_equity", "peak_time", "drawdown", "underwater_bars"),
        )
        orders_artifact = self.materialize_rows(
            artifact_type="BACKTEST_ORDERS",
            logical_name=logical_name,
            rows=[dict(item) for item in result.orders],
            schema_version="backtest-orders.v1",
            output_folder="backtests/orders",
            dependencies=common_dependencies,
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            spec_hash=str(metrics.get("execution_spec_hash") or ""),
            engine_version=str(metrics.get("engine_version") or ""),
            code_hash=str(metrics.get("code_hash") or ""),
            identity_context={**identity_context, "artifact_type": "BACKTEST_ORDERS"},
            metadata={"order_count": len(result.orders), **identity_context},
            allow_empty=True,
            empty_columns=(
                "order_id", "event_time", "signal_time", "signal_available_time",
                "instrument_id", "side", "quantity", "reference_price", "fill_price",
                "fee", "slippage_cost", "target_weight", "gross_value", "reason",
            ),
        )
        result_dependencies = list(common_dependencies)
        result_dependencies.extend([
            {
                "parent_id": orders_artifact.artifact_id,
                "parent_type": "RESEARCH_ARTIFACT",
                "dependency_type": "BACKTEST_ORDERS",
            },
            {
                "parent_id": positions_artifact.artifact_id,
                "parent_type": "RESEARCH_ARTIFACT",
                "dependency_type": "POSITIONS",
            },
            {
                "parent_id": equity_artifact.artifact_id,
                "parent_type": "RESEARCH_ARTIFACT",
                "dependency_type": "EQUITY",
            },
            {
                "parent_id": drawdown_artifact.artifact_id,
                "parent_type": "RESEARCH_ARTIFACT",
                "dependency_type": "DRAWDOWN",
            },
        ])
        result_artifact = self.materialize_rows(
            artifact_type="BACKTEST_RESULT",
            logical_name=logical_name,
            rows=[{
                "metrics_json": json_dumps(metrics),
                "execution_spec_json": json_dumps(result.execution_spec),
                "dataset_manifest_ids_json": json_dumps(list(result.dataset_manifest_ids)),
                "universe_snapshot_ids_json": json_dumps(list(result.universe_snapshot_ids)),
                "factor_artifact_ids_json": json_dumps(list(result.factor_artifact_ids)),
                "alpha_artifact_ids_json": json_dumps(list(result.alpha_artifact_ids)),
            }],
            schema_version="backtest-result.v1",
            output_folder="backtests/results",
            dependencies=result_dependencies,
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            spec_hash=_clean(metrics.get("execution_spec_hash")),
            engine_version=_clean(metrics.get("engine_version")),
            code_hash=_clean(metrics.get("code_hash")),
            identity_context={**identity_context, "artifact_type": "BACKTEST_RESULT"},
            metadata={"metrics": metrics, **identity_context},
        )
        self.artifacts.pin(
            result_artifact.artifact_id,
            owner_type="BACKTEST_RESULT",
            owner_id=result_artifact.artifact_id,
            reason="published immutable research result",
        )
        return {
            "positions": positions_artifact,
            "equity": equity_artifact,
            "drawdown": drawdown_artifact,
            "orders": orders_artifact,
            "result": result_artifact,
        }

    def materialize_evaluation(
        self,
        *,
        logical_name: str,
        result: Any,
        spec: Any,
        input_artifact_id: str,
        dataset_manifest_ids: Iterable[str] = (),
        universe_snapshot_id: str = "",
        project_id: str = "",
        created_by_run_id: str = "",
    ) -> ResearchArtifact:
        evaluation_type = _clean(getattr(result, "evaluation_type", "")).upper()
        if evaluation_type not in {"FACTOR_EVALUATION", "ALPHA_EVALUATION"}:
            raise ValueError(f"unsupported evaluation type: {evaluation_type}")
        manifest_ids = sorted({str(item) for item in dataset_manifest_ids if str(item)})
        spec_hash = str(getattr(spec, "spec_hash", "") or "")
        engine_version = str(getattr(spec, "engine_version", "") or "")
        code_hash = str(getattr(spec, "code_hash", "") or "")
        identity_context = {
            "evaluation_type": evaluation_type,
            "evaluation_spec_hash": spec_hash,
            "input_artifact_id": str(input_artifact_id),
            "dataset_manifest_ids": manifest_ids,
            "universe_snapshot_id": str(universe_snapshot_id or ""),
            "engine_version": engine_version,
            "code_hash": code_hash,
            "schema_version": "evaluation-records.v1",
        }
        dependencies = [{
            "parent_id": str(input_artifact_id),
            "parent_type": "RESEARCH_ARTIFACT",
            "dependency_type": "INPUT_FACTOR" if evaluation_type == "FACTOR_EVALUATION" else "INPUT_ALPHA",
        }]
        dependencies.extend(
            {"parent_id": manifest_id, "parent_type": "DATASET_MANIFEST", "dependency_type": "INPUT_DATASET"}
            for manifest_id in manifest_ids
        )
        if universe_snapshot_id:
            dependencies.append({
                "parent_id": str(universe_snapshot_id),
                "parent_type": "UNIVERSE_SNAPSHOT",
                "dependency_type": "INPUT_UNIVERSE",
            })
        rows = result.artifact_rows()
        return self.materialize_rows(
            artifact_type=evaluation_type,
            logical_name=logical_name,
            rows=rows,
            schema_version="evaluation-records.v1",
            output_folder="evaluations",
            dependencies=dependencies,
            project_id=project_id,
            created_by_run_id=created_by_run_id,
            spec_hash=spec_hash,
            engine_version=engine_version,
            code_hash=code_hash,
            identity_context=identity_context,
            metadata={
                "evaluation_spec": spec.to_dict() if hasattr(spec, "to_dict") else {},
                "summary": dict(result.summary),
                "observation_count": len(result.observations),
                "ic_count": len(result.ic_series),
                "group_return_count": len(result.group_return_series),
                "stability_count": len(result.stability_series),
                "universe_snapshot_id": str(universe_snapshot_id or ""),
                "artifact_fingerprint": hashlib.sha256(json_dumps(identity_context).encode("utf-8")).hexdigest(),
            },
        )
