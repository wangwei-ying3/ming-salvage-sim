"""powers / power_logs / power_name_logs：外部势力盘面、增量、改名、回合摘要。

_PowersMixin：拆自原 db.py，方法体逐字未改。"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from ming_sim.assets import format_money, format_money_delta
from ming_sim.constants import (
    ARMY_FIELD_ALIASES, ARMY_FIELD_LABELS, ARMY_QUANTITY_FIELDS, ARMY_SCORE_FIELDS, ARMY_TEXT_FIELDS,
    BUILDING_CATEGORIES, BUILDING_FIELD_LABELS, BUILDING_OUTPUT_METRICS,
    BUILDING_QUANTITY_FIELDS, BUILDING_SCORE_FIELDS, BUILDING_TEXT_FIELDS,
    ECONOMY_ACCOUNTS, POWER_FIELD_LABELS, POWER_SCORE_FIELDS,
    POWER_FIELD_ALIASES, POWER_TEXT_FIELDS, MONEY_UNIT, REGION_FIELD_LABELS, REGION_QUANTITY_FIELDS,
    FISCAL_SCORE_FIELDS, REGION_FIELD_ALIASES, REGION_SCORE_FIELDS, REGION_TEXT_FIELDS, TURN_UNIT,
)
from ming_sim.content import GameContent
from ming_sim.matching import match_army_id_from_text, match_region_id_from_text
from ming_sim.models import Event, GameState, monthly_amount, period_label
from ming_sim.token_stats import tlog
from ming_sim.db._helpers import (
    normalize_office, infer_office_type_from_office,
    _compact_lookup_text, _normalize_power_id,
    COURT_OFFICE_TYPES, MINISTRY_OFFICE_TYPES,
)


class _PowersMixin:
    def power_rows(self, exclude_self: bool = False) -> List[sqlite3.Row]:
        where = "WHERE id != 'ming'" if exclude_self else ""
        return self.conn.execute(
            f"""
            SELECT *
            FROM powers
            {where}
            ORDER BY CASE id
                WHEN 'ming' THEN 0
                WHEN 'houjin' THEN 1
                WHEN 'mongol' THEN 2
                WHEN 'korea' THEN 3
                WHEN 'japan' THEN 4
                WHEN 'dutch' THEN 5
                WHEN 'bandits' THEN 6
                ELSE 9
            END, name
            """
        ).fetchall()

    def power_payload(self, exclude_self: bool = False) -> List[Dict[str, object]]:
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "leader": row["leader"],
                "stance": row["stance"],
                "leverage": int(row["leverage"]),
                "satisfaction": int(row["satisfaction"]),
                "military_strength": int(row["military_strength"]),
                "cohesion": int(row["cohesion"]),
                "supply": int(row["supply"]),
                "agenda": row["agenda"],
                "status": row["status"],
                "last_action": row["last_action"],
                "aliases": row["aliases"],
            }
            for row in self.power_rows(exclude_self=exclude_self)
        ]

    def power_report(self, exclude_self: bool = True) -> str:
        rows = self.power_rows(exclude_self=exclude_self)
        if not rows:
            return "势力未建档。"
        return "；".join(
            f"{row['name']}（{row['leader']}）：{row['stance']}，威望{row['leverage']}、"
            f"实力{row['military_strength']}、经济{row['supply']}，"
            f"{row['status']}；近动：{row['last_action'] or '尚无新动'}"
            for row in rows
        )

    def apply_power_deltas(
        self,
        state: GameState,
        updates: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        allowed_fields = {"leverage", "military_strength", "supply"}
        changes: List[Dict[str, object]] = []
        for power_id, raw_changes in updates.items():
            if power_id == "ming":
                print("[WARN] power_updates 不再处理大明自身 → 跳过")
                continue
            row = self.conn.execute("SELECT * FROM powers WHERE id = ?", (power_id,)).fetchone()
            if row is None:
                print(f"[WARN] power_updates 引用未入库势力 '{power_id}' → 跳过")
                continue
            reason = str(
                raw_changes.get("reason")
                or raw_changes.get("原因")
                or raw_changes.get("last_action")
                or raw_changes.get("近动")
                or "势力推演"
            ).strip()[:120]
            for raw_field, value in raw_changes.items():
                field = POWER_FIELD_ALIASES.get(str(raw_field).strip(), str(raw_field).strip())
                if field == "reason":
                    continue
                if field not in allowed_fields:
                    print(f"[WARN] power_updates 只允许 威望/实力/经济，'{raw_field}' → 跳过")
                    continue
                old_value = row[field]
                delta = int(value)
                new_value = max(0, min(100, int(old_value) + delta))
                actual_delta = new_value - int(old_value)
                if actual_delta == 0:
                    continue
                stored_new: object = new_value
                log_delta: int | None = actual_delta
                self.conn.execute(
                    f"UPDATE powers SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, power_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO power_logs
                    (turn, year, period, power_id, field, old_value, new_value, delta, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn,
                        state.year,
                        state.period,
                        power_id,
                        field,
                        str(old_value),
                        str(stored_new),
                        log_delta,
                        reason,
                    ),
                )
                changes.append({
                    "power": row["name"],
                    "field": field,
                    "label": POWER_FIELD_LABELS.get(field, field),
                    "old": old_value,
                    "new": stored_new,
                    "delta": log_delta,
                    "reason": reason,
                })
        self.commit()
        return changes

    def apply_power_rename(
        self,
        state: GameState,
        power_id: str,
        new_name: str,
        *,
        reason: str,
        aliases: str = "",
        status: str = "",
        last_action: str = "",
    ) -> Dict[str, object] | None:
        """Rename a power while keeping its stable id for references.

        Used for dynastic/name changes such as houjin 后金 -> 大清.
        """
        power_id = str(power_id or "").strip()
        new_name = str(new_name or "").strip()
        if not power_id or not new_name:
            return None
        row = self.conn.execute("SELECT * FROM powers WHERE id = ?", (power_id,)).fetchone()
        if row is None:
            print(f"[WARN] power_rename 引用未入库势力 '{power_id}' → 跳过")
            return None
        old_name = str(row["name"] or "")
        old_aliases = str(row["aliases"] or "")
        merged_aliases = [x.strip() for x in (aliases or old_aliases).replace("，", ",").split(",") if x.strip()]
        for alias in (old_name, new_name):
            if alias and alias not in merged_aliases:
                merged_aliases.append(alias)
        new_aliases = "，".join(merged_aliases)
        new_status = str(status or row["status"] or "")[:200]
        new_last_action = str(last_action or reason or row["last_action"] or "")[:200]
        if old_name == new_name and old_aliases == new_aliases and row["status"] == new_status and row["last_action"] == new_last_action:
            return None
        self.conn.execute(
            """
            UPDATE powers
            SET name=?, aliases=?, status=?, last_action=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (new_name, new_aliases, new_status, new_last_action, power_id),
        )
        self.conn.execute(
            """
            INSERT INTO power_name_logs
            (turn, year, period, power_id, old_name, new_name, old_aliases, new_aliases, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (state.turn, state.year, state.period, power_id, old_name, new_name, old_aliases, new_aliases, reason[:200]),
        )
        self.conn.commit()
        return {
            "power_id": power_id,
            "old_name": old_name,
            "new_name": new_name,
            "old_aliases": old_aliases,
            "new_aliases": new_aliases,
            "reason": reason,
        }

    def turn_power_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT pl.*, p.name AS power_name
            FROM power_logs pl
            JOIN powers p ON p.id = pl.power_id
            WHERE pl.turn = ?
            ORDER BY pl.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}势力无明确变化。"
        parts = []
        for row in rows:
            label = POWER_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['power_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                parts.append(f"{row['power_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def power_display_name(self, power_id: str) -> str:
        """power_id → 显示名（如 houjin→后金）。缺则回退 id。"""
        row = self.conn.execute(
            "SELECT name FROM powers WHERE id = ?", (str(power_id),)
        ).fetchone()
        return str(row["name"]) if row else str(power_id)
