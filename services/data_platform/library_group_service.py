from __future__ import annotations

import uuid
from typing import Any

from .store import DataPlatformStore, utc_now

ASSET_TYPES = {"UNIVERSE", "FACTOR", "ALPHA", "REQUIREMENTS"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _asset_type(value: Any) -> str:
    asset_type = _clean(value).upper()
    if asset_type not in ASSET_TYPES:
        raise ValueError(f"unknown asset_type: {value!r}")
    return asset_type


class LibraryGroupService:
    """User-managed Groups layered on top of Library assets.

    Groups never own assets and never affect asset lifecycle: deleting a
    Group only removes rows from ``library_group_members``, which drops the
    affected assets back into the implicit "Ungrouped" view.
    """

    def __init__(self, store: DataPlatformStore):
        self.store = store

    def list_groups(self, asset_type: str) -> list[dict[str, Any]]:
        asset_type = _asset_type(asset_type)
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT g.group_id, g.asset_type, g.name, g.sort_order, g.created_at, g.updated_at,
                       COUNT(m.asset_id) AS asset_count
                FROM library_groups g
                LEFT JOIN library_group_members m ON m.group_id = g.group_id
                WHERE g.asset_type = ?
                GROUP BY g.group_id
                ORDER BY g.sort_order, g.created_at
                """,
                (asset_type,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def create_group(self, *, asset_type: str, name: str) -> dict[str, Any]:
        asset_type = _asset_type(asset_type)
        name = _clean(name)
        if not name:
            raise ValueError("group name is required")
        if name.lower() == "ungrouped":
            raise ValueError('"Ungrouped" is reserved for the default group')
        now = utc_now()
        group_id = f"libgroup_{uuid.uuid4().hex}"
        with self.store.transaction(immediate=True) as conn:
            if conn.execute(
                "SELECT 1 FROM library_groups WHERE asset_type=? AND name=?",
                (asset_type, name),
            ).fetchone():
                raise ValueError(f'a Group named "{name}" already exists')
            next_order = int(conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM library_groups WHERE asset_type=?",
                (asset_type,),
            ).fetchone()[0])
            conn.execute(
                """
                INSERT INTO library_groups(group_id, asset_type, name, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (group_id, asset_type, name, next_order, now, now),
            )
        result = self.get_group(group_id)
        if result is None:
            raise RuntimeError("failed to create Group")
        return result

    def rename_group(self, group_id: str, name: str) -> dict[str, Any]:
        group_id = _clean(group_id)
        name = _clean(name)
        if not name:
            raise ValueError("group name is required")
        if name.lower() == "ungrouped":
            raise ValueError('"Ungrouped" is reserved for the default group')
        with self.store.transaction(immediate=True) as conn:
            group = conn.execute(
                "SELECT asset_type FROM library_groups WHERE group_id=?", (group_id,),
            ).fetchone()
            if group is None:
                raise ValueError("Group not found")
            clash = conn.execute(
                "SELECT 1 FROM library_groups WHERE asset_type=? AND name=? AND group_id<>?",
                (group["asset_type"], name, group_id),
            ).fetchone()
            if clash:
                raise ValueError(f'a Group named "{name}" already exists')
            conn.execute(
                "UPDATE library_groups SET name=?, updated_at=? WHERE group_id=?",
                (name, utc_now(), group_id),
            )
        result = self.get_group(group_id)
        if result is None:
            raise RuntimeError("failed to rename Group")
        return result

    def delete_group(self, group_id: str) -> dict[str, Any]:
        group_id = _clean(group_id)
        with self.store.transaction(immediate=True) as conn:
            group = conn.execute(
                "SELECT * FROM library_groups WHERE group_id=?", (group_id,),
            ).fetchone()
            if group is None:
                raise ValueError("Group not found")
            moved = conn.execute(
                "SELECT COUNT(*) FROM library_group_members WHERE group_id=?", (group_id,),
            ).fetchone()[0]
            conn.execute("DELETE FROM library_group_members WHERE group_id=?", (group_id,))
            conn.execute("DELETE FROM library_groups WHERE group_id=?", (group_id,))
        return {"group_id": group_id, "name": str(group["name"]), "assets_moved_to_ungrouped": int(moved)}

    def reorder_groups(self, asset_type: str, ordered_group_ids: list[str]) -> list[dict[str, Any]]:
        asset_type = _asset_type(asset_type)
        with self.store.transaction(immediate=True) as conn:
            existing = {
                str(row["group_id"])
                for row in conn.execute(
                    "SELECT group_id FROM library_groups WHERE asset_type=?", (asset_type,),
                ).fetchall()
            }
            ordered = [_clean(group_id) for group_id in ordered_group_ids]
            if set(ordered) != existing:
                raise ValueError("ordered_group_ids must list every Group for this asset_type exactly once")
            now = utc_now()
            for index, group_id in enumerate(ordered):
                conn.execute(
                    "UPDATE library_groups SET sort_order=?, updated_at=? WHERE group_id=?",
                    (index, now, group_id),
                )
        return self.list_groups(asset_type)

    def move_assets(self, *, asset_type: str, asset_ids: list[str], group_id: str | None) -> dict[str, Any]:
        asset_type = _asset_type(asset_type)
        asset_ids = [_clean(asset_id) for asset_id in asset_ids if _clean(asset_id)]
        if not asset_ids:
            raise ValueError("asset_ids is required")
        group_id = _clean(group_id) if group_id else ""
        with self.store.transaction(immediate=True) as conn:
            if group_id:
                group = conn.execute(
                    "SELECT asset_type FROM library_groups WHERE group_id=?", (group_id,),
                ).fetchone()
                if group is None:
                    raise ValueError("Group not found")
                if str(group["asset_type"]) != asset_type:
                    raise ValueError("Group belongs to a different asset type")
                now = utc_now()
                for asset_id in asset_ids:
                    conn.execute(
                        """
                        INSERT INTO library_group_members(asset_type, asset_id, group_id, created_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(asset_type, asset_id) DO UPDATE SET group_id=excluded.group_id
                        """,
                        (asset_type, asset_id, group_id, now),
                    )
            else:
                for asset_id in asset_ids:
                    conn.execute(
                        "DELETE FROM library_group_members WHERE asset_type=? AND asset_id=?",
                        (asset_type, asset_id),
                    )
        return {"asset_type": asset_type, "group_id": group_id or None, "moved": len(asset_ids)}

    def membership(self, asset_type: str, asset_ids: list[str] | None = None) -> dict[str, str]:
        asset_type = _asset_type(asset_type)
        query = "SELECT asset_id, group_id FROM library_group_members WHERE asset_type=?"
        params: list[Any] = [asset_type]
        cleaned_ids = [_clean(asset_id) for asset_id in (asset_ids or []) if _clean(asset_id)]
        if cleaned_ids:
            query += f" AND asset_id IN ({','.join('?' for _ in cleaned_ids)})"
            params.extend(cleaned_ids)
        with self.store.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return {str(row["asset_id"]): str(row["group_id"]) for row in rows}

    def get_group(self, group_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT g.group_id, g.asset_type, g.name, g.sort_order, g.created_at, g.updated_at,
                       COUNT(m.asset_id) AS asset_count
                FROM library_groups g
                LEFT JOIN library_group_members m ON m.group_id = g.group_id
                WHERE g.group_id = ?
                GROUP BY g.group_id
                """,
                (_clean(group_id),),
            ).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {
            "group_id": str(row["group_id"]),
            "asset_type": str(row["asset_type"]),
            "name": str(row["name"]),
            "sort_order": int(row["sort_order"]),
            "asset_count": int(row["asset_count"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
