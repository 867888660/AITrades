from __future__ import annotations

import json
from typing import Any

from .store import DataPlatformStore


SUPPORTED_ANCHORS = {
    "SESSION",
    "PROJECT",
    "RUN",
    "PREVIEW",
    "BUNDLE",
    "FACTOR_DEFINITION",
    "ALPHA_DEFINITION",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


class ResearchContextResolver:
    """Resolve a resume anchor into an unambiguous research context graph."""

    def __init__(self, store: DataPlatformStore):
        self.store = store

    def resolve(self, anchor_type: str, anchor_id: str) -> dict[str, Any]:
        kind = _clean(anchor_type).upper()
        anchor_id = _clean(anchor_id)
        if kind not in SUPPORTED_ANCHORS:
            raise ValueError(f"unsupported research anchor type: {kind or 'EMPTY'}")
        if not anchor_id:
            raise ValueError("anchor_id is required")

        with self.store.connection() as conn:
            project_ids: list[str] = []
            anchor: dict[str, Any] = {}
            baseline_run_id = ""
            baseline_bundle_id = ""

            if kind == "SESSION":
                row = conn.execute(
                    "SELECT * FROM research_agent_sessions WHERE session_id=?", (anchor_id,)
                ).fetchone()
                if row:
                    anchor = dict(row)
                    project_ids = [str(row["project_id"])]
                    baseline_run_id = str(row["current_branch_head_run_id"] or row["original_baseline_run_id"] or "")
            elif kind == "PROJECT":
                row = conn.execute("SELECT * FROM research_projects WHERE project_id=?", (anchor_id,)).fetchone()
                if row:
                    anchor = dict(row)
                    project_ids = [anchor_id]
            elif kind == "RUN":
                row = conn.execute("SELECT * FROM research_runs_v2 WHERE run_id=?", (anchor_id,)).fetchone()
                if row:
                    anchor = dict(row)
                    project_ids = [str(row["project_id"])]
                    baseline_run_id = anchor_id
                    baseline_bundle_id = str(row["bundle_id"] or "")
            elif kind == "PREVIEW":
                row = conn.execute(
                    "SELECT * FROM research_run_previews WHERE preview_id=?", (anchor_id,)
                ).fetchone()
                if row:
                    anchor = dict(row)
                    project_ids = [str(row["project_id"])]
                    linked = conn.execute(
                        "SELECT run_id, bundle_id FROM research_runs_v2 WHERE preview_id=? ORDER BY created_at DESC LIMIT 1",
                        (anchor_id,),
                    ).fetchone()
                    if linked:
                        baseline_run_id, baseline_bundle_id = str(linked[0]), str(linked[1])
            elif kind == "BUNDLE":
                row = conn.execute(
                    "SELECT * FROM frozen_research_bundles WHERE bundle_id=?", (anchor_id,)
                ).fetchone()
                if row:
                    anchor = dict(row)
                    project_ids = [str(row["project_id"])]
                    baseline_run_id = str(row["run_id"] or "")
                    baseline_bundle_id = anchor_id
            else:
                expected = "FACTOR" if kind == "FACTOR_DEFINITION" else "ALPHA"
                definition = conn.execute(
                    "SELECT * FROM research_definitions WHERE definition_id=? AND definition_type=?",
                    (anchor_id, expected),
                ).fetchone()
                if definition:
                    anchor = dict(definition)
                    rows = conn.execute(
                        "SELECT DISTINCT project_id FROM project_definition_refs WHERE definition_id=? ORDER BY project_id",
                        (anchor_id,),
                    ).fetchall()
                    project_ids = [str(item[0]) for item in rows]

            if not anchor:
                return {
                    "resolution_status": "NOT_FOUND",
                    "anchor_type": kind,
                    "anchor_id": anchor_id,
                    "candidates": [],
                }
            if not project_ids:
                return {
                    "resolution_status": "AMBIGUOUS",
                    "reason_code": "ANCHOR_HAS_NO_PROJECT",
                    "anchor_type": kind,
                    "anchor_id": anchor_id,
                    "candidates": [],
                }
            if len(project_ids) > 1:
                candidates = [self._project_summary(conn, item) for item in project_ids]
                return {
                    "resolution_status": "AMBIGUOUS",
                    "reason_code": "ANCHOR_REFERENCED_BY_MULTIPLE_PROJECTS",
                    "anchor_type": kind,
                    "anchor_id": anchor_id,
                    "candidates": candidates,
                }
            result = self._context_for_project(
                conn,
                project_ids[0],
                anchor_type=kind,
                anchor_id=anchor_id,
                baseline_run_id=baseline_run_id,
                baseline_bundle_id=baseline_bundle_id,
            )
            if kind == "SESSION":
                iterations = conn.execute(
                    "SELECT * FROM research_agent_iterations WHERE session_id=? ORDER BY sequence",
                    (anchor_id,),
                ).fetchall()
                result["source_session_id"] = anchor_id
                result["experiment_history"] = [
                    {
                        "iteration_id": str(item["iteration_id"]),
                        "sequence": int(item["sequence"]),
                        "control_run_id": str(item["control_run_id"] or ""),
                        "candidate_run_id": str(item["candidate_run_id"] or ""),
                        "decision": str(item["decision"] or ""),
                        "decision_reason": str(item["decision_reason"] or ""),
                    }
                    for item in iterations
                ]
            return result

    def _context_for_project(
        self,
        conn: Any,
        project_id: str,
        *,
        anchor_type: str,
        anchor_id: str,
        baseline_run_id: str = "",
        baseline_bundle_id: str = "",
    ) -> dict[str, Any]:
        project = conn.execute("SELECT * FROM research_projects WHERE project_id=?", (project_id,)).fetchone()
        refs = conn.execute(
            "SELECT * FROM project_definition_refs WHERE project_id=? ORDER BY definition_type, slot_key",
            (project_id,),
        ).fetchall()
        snapshots = conn.execute(
            """
            SELECT universe_snapshot_id
            FROM research_universe_refs
            WHERE project_id=?
            ORDER BY updated_at DESC
            """,
            (project_id,),
        ).fetchall()
        latest_preview = conn.execute(
            "SELECT preview_id FROM research_run_previews WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        latest_run = conn.execute(
            "SELECT run_id, bundle_id FROM research_runs_v2 WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        recent_runs = conn.execute(
            "SELECT run_id FROM research_runs_v2 WHERE project_id=? ORDER BY created_at DESC LIMIT 20",
            (project_id,),
        ).fetchall()
        recent_previews = conn.execute(
            "SELECT preview_id FROM research_run_previews WHERE project_id=? ORDER BY created_at DESC LIMIT 20",
            (project_id,),
        ).fetchall()
        requirement_ref = conn.execute(
            "SELECT requirement_set_id FROM research_requirement_refs WHERE project_id=?",
            (project_id,),
        ).fetchone()
        artifacts = conn.execute(
            "SELECT artifact_id FROM research_artifacts WHERE project_id=? ORDER BY created_at DESC LIMIT 50",
            (project_id,),
        ).fetchall()
        grants = conn.execute(
            "SELECT grant_id, status FROM approval_grants WHERE project_id=? ORDER BY approved_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        factor_ids = [str(row["definition_id"]) for row in refs if str(row["definition_type"]).upper() == "FACTOR"]
        alpha_ids = [str(row["definition_id"]) for row in refs if str(row["definition_type"]).upper() == "ALPHA"]
        if not baseline_run_id and latest_run:
            baseline_run_id = str(latest_run[0])
            baseline_bundle_id = str(latest_run[1])
        return {
            "resolution_status": "RESOLVED",
            "anchor_type": anchor_type,
            "anchor_id": anchor_id,
            "project_id": project_id,
            "project": dict(project) if project else {},
            "baseline_run_id": baseline_run_id,
            "baseline_bundle_id": baseline_bundle_id,
            "universe_snapshot_ids": [str(row[0]) for row in snapshots],
            "factor_definition_ids": factor_ids,
            "alpha_definition_ids": alpha_ids,
            "latest_preview_id": str(latest_preview[0]) if latest_preview else "",
            "requirement_set_id": str(requirement_ref[0]) if requirement_ref else "",
            "recent_preview_ids": [str(row[0]) for row in recent_previews],
            "recent_run_ids": [str(row[0]) for row in recent_runs],
            "artifact_ids": [str(row[0]) for row in artifacts],
            "grant_id": str(grants[0]) if grants else "",
            "grant_status": str(grants[1]) if grants else "",
        }

    @staticmethod
    def _project_summary(conn: Any, project_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT project_id, title, objective, summary_state, updated_at FROM research_projects WHERE project_id=?",
            (project_id,),
        ).fetchone()
        return dict(row) if row else {"project_id": project_id}
