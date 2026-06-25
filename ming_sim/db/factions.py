"""factions：朝堂派系满意度/影响力读写与报表。

_FactionsMixin：拆自原 db.py，方法体逐字未改。"""

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


class _FactionsMixin:
    def faction_satisfaction(self, faction: str) -> int:
        row = self.conn.execute("SELECT satisfaction FROM factions WHERE name = ?", (faction,)).fetchone()
        return int(row["satisfaction"]) if row else 50

    def faction_leverage(self, faction: str) -> int:
        row = self.conn.execute("SELECT leverage FROM factions WHERE name = ?", (faction,)).fetchone()
        return int(row["leverage"]) if row else 50

    def faction_report(self) -> str:
        rows = self.conn.execute(
            "SELECT name, satisfaction, leverage, agenda FROM factions ORDER BY name"
        ).fetchall()
        if not rows:
            return "派系未建档。"
        return "；".join(
            f"{row['name']}满意{row['satisfaction']}、势力{row['leverage']}，所求：{row['agenda']}"
            for row in rows
        )

    def adjust_factions(self, deltas: Dict[str, object]) -> None:
        for faction, val in deltas.items():
            if isinstance(val, dict):
                sat_d = int(val.get("satisfaction") or 0)
                lev_d = int(val.get("leverage") or 0)
            else:
                try:
                    sat_d = int(val)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                lev_d = 0
            if sat_d == 0 and lev_d == 0:
                continue
            row = self.conn.execute(
                "SELECT satisfaction, leverage FROM factions WHERE name = ?", (faction,)
            ).fetchone()
            if not row:
                continue
            new_sat = max(0, min(100, int(row["satisfaction"]) + sat_d))
            new_lev = max(0, min(100, int(row["leverage"]) + lev_d))
            self.conn.execute(
                "UPDATE factions SET satisfaction = ?, leverage = ? WHERE name = ?",
                (new_sat, new_lev, faction),
            )
        self.commit()
