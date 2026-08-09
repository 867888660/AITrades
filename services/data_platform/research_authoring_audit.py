from __future__ import annotations

import uuid
from typing import Any

from .store import utc_now


class ResearchAuthoringAudit:
    """Write compact authoring facts without storing documents, values, or secrets."""

    @staticmethod
    def record(
        conn: Any,
        *,
        object_type: str,
        object_id: str,
        project_id: str,
        operation: str,
        before_fingerprint: str = "",
        after_fingerprint: str = "",
        status: str = "SUCCEEDED",
        stable_code: str = "",
        actor_type: str = "USER",
        actor_id: str = "local_ui_user",
        duration_ms: int = 0,
    ) -> None:
        project_id = str(project_id or "").strip()
        if not project_id:
            return
        conn.execute(
            """
            INSERT INTO research_authoring_events(
                event_id,object_type,object_id,project_id,
                actor_type,actor_id,operation,status,stable_code,
                before_fingerprint,after_fingerprint,duration_ms,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"authoring_event_{uuid.uuid4().hex}",
                str(object_type or "").strip().upper(),
                str(object_id or "").strip(),
                project_id,
                str(actor_type or "USER").strip().upper(),
                str(actor_id or "local_ui_user").strip(),
                str(operation or "").strip().upper(),
                str(status or "SUCCEEDED").strip().upper(),
                str(stable_code or "").strip(),
                str(before_fingerprint or "").strip(),
                str(after_fingerprint or "").strip(),
                max(0, int(duration_ms)),
                utc_now(),
            ),
        )
