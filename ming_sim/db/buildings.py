"""buildings / building_logs / technologies + 部门(复用 offices)：建筑/科技/衙门增删与报表。

_BuildingsMixin：拆自原 db.py，方法体逐字未改。"""

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


class _BuildingsMixin:
    # ── 建筑 ──────────────────────────────────────────────────────────────────

    def tech_unlocked(self, tech_name: str) -> bool:
        """通用前置科技判定：tech_name 空→True；否则 technologies 表按 name 命中（已研成）。
        weapon/troop/building 三处门控共用。"""
        tech = str(tech_name or "").strip()
        if not tech:
            return True
        return self.conn.execute(
            "SELECT 1 FROM technologies WHERE name=?", (tech,)
        ).fetchone() is not None

    def add_building(
        self,
        state: GameState,
        region_id: str,
        name: str,
        category: str,
        *,
        level: int = 1,
        condition: int = 60,
        maintenance: int = 0,
        risk: int = 30,
        output_metric: str = "",
        output_amount: int = 0,
        status: str = "",
        origin: str = "decree",
        requires_tech: str = "",
    ) -> str:
        """运行时新立建筑（玩家诏书）。category / output_metric 走白名单硬校验，违规 ValueError。
        requires_tech 非空且未研成 → ValueError（预设建筑的科技门控；AI 新建可留空放行）。"""
        if category not in BUILDING_CATEGORIES:
            raise ValueError(f"建筑 category 非法 '{category}'，白名单 {BUILDING_CATEGORIES}")
        if output_metric not in BUILDING_OUTPUT_METRICS:
            raise ValueError(f"建筑 output_metric 非法 '{output_metric}'，白名单 {BUILDING_OUTPUT_METRICS}")
        if not self.tech_unlocked(requires_tech):
            raise ValueError(f"建筑「{name}」前置科技「{requires_tech}」未研成，不可新立")
        if self.conn.execute("SELECT 1 FROM regions WHERE id = ?", (region_id,)).fetchone() is None:
            raise ValueError(f"建筑 region_id 引用未入库地区 '{region_id}'")
        base = re.sub(r"[^a-z0-9]+", "", (region_id or "rgn").lower()) or "rgn"
        seq = self.conn.execute(
            "SELECT COUNT(*) FROM buildings WHERE region_id = ?", (region_id,)
        ).fetchone()[0]
        building_id = f"{base}_b{int(seq) + 1}"
        while self.conn.execute("SELECT 1 FROM buildings WHERE id = ?", (building_id,)).fetchone():
            seq += 1
            building_id = f"{base}_b{int(seq) + 1}"
        self.conn.execute(
            """
            INSERT INTO buildings
            (id, region_id, name, category, level, condition, maintenance, risk,
             output_metric, output_amount, status, origin, requires_tech, created_turn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                building_id,
                region_id,
                name.strip()[:60] or "无名建筑",
                category,
                max(1, min(5, int(level))),
                max(0, min(100, int(condition))),
                max(0, int(maintenance)),
                max(0, min(100, int(risk))),
                output_metric,
                max(0, int(output_amount)),
                status.strip()[:160] or "新立，尚在筹建。",
                origin,
                str(requires_tech or "").strip(),
                state.turn,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO building_logs
            (turn, year, period, building_id, field, old_value, new_value, delta, reason, actor)
            VALUES (?, ?, ?, ?, 'create', '', ?, NULL, ?, '档房')
            """,
            (state.turn, state.year, state.period, building_id, name.strip()[:60], "诏书新立建筑"),
        )
        self.commit()
        return building_id

    def remove_building(self, state: GameState, building_id: str, reason: str = "") -> bool:
        """拆除/废止建筑（issue 失败或撤销结案）。返回是否真删了一行。"""
        row = self.conn.execute("SELECT name FROM buildings WHERE id = ?", (building_id,)).fetchone()
        if row is None:
            return False
        self.conn.execute(
            """
            INSERT INTO building_logs
            (turn, year, period, building_id, field, old_value, new_value, delta, reason, actor)
            VALUES (?, ?, ?, ?, 'remove', ?, '', NULL, ?, '档房')
            """,
            (state.turn, state.year, state.period, building_id,
             str(row["name"]), (reason or "建筑废止").strip()[:80]),
        )
        self.conn.execute("DELETE FROM buildings WHERE id = ?", (building_id,))
        self.commit()
        return True

    def apply_building_deltas(
        self,
        state: GameState,
        event: Event,
        edict_id: int | None,
        actor: str,
        building_deltas: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        """改既有建筑。仿 apply_army_deltas。供 issue effect 落地复用。"""
        changes: List[Dict[str, object]] = []
        valid_fields = set(BUILDING_SCORE_FIELDS + BUILDING_QUANTITY_FIELDS + BUILDING_TEXT_FIELDS)
        for building_id, raw_changes in building_deltas.items():
            row = self.conn.execute("SELECT * FROM buildings WHERE id = ?", (building_id,)).fetchone()
            if row is None:
                print(f"[WARN] building_delta 引用未入库建筑 '{building_id}' → 跳过")
                continue
            reason = str(raw_changes.get("reason") or event.title).strip()[:80]
            for field, value in raw_changes.items():
                if field == "reason":
                    continue
                if field not in valid_fields:
                    print(f"[WARN] building_delta 引用非法字段 '{field}' → 跳过")
                    continue
                old_value = row[field]
                if field in BUILDING_SCORE_FIELDS:
                    new_value = max(0, min(100, int(old_value) + int(value)))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                elif field == "level":
                    new_value = max(1, min(5, int(old_value) + int(value)))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field in ("maintenance", "output_amount"):
                    new_value = max(0, int(old_value) + int(value))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field == "output_metric":
                    text_value = str(value).strip()
                    if text_value not in BUILDING_OUTPUT_METRICS:
                        print(f"[WARN] building_delta output_metric 非法 '{text_value}' → 跳过")
                        continue
                    if text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                elif field in BUILDING_TEXT_FIELDS:
                    text_value = str(value).strip()[:160]
                    if not text_value or text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                else:
                    print(f"[WARN] building_delta 未处理字段 '{field}' → 跳过")
                    continue
                self.conn.execute(
                    f"UPDATE buildings SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, building_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO building_logs
                    (turn, year, period, building_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn, state.year, state.period, building_id, field,
                        str(old_value), str(stored_new), log_delta, reason,
                        event.id, edict_id, actor,
                    ),
                )
                changes.append({
                    "building": row["name"],
                    "field": field,
                    "label": BUILDING_FIELD_LABELS.get(field, field),
                    "old": old_value,
                    "new": stored_new,
                    "delta": log_delta,
                    "reason": reason,
                })
        self.commit()
        return changes

    def buildings_report(self, region_id: str = "") -> str:
        """月末奏报 / web 用建筑盘面摘要。region_id 为空取全国。"""
        if region_id:
            rows = self.conn.execute(
                "SELECT * FROM buildings WHERE region_id = ? ORDER BY category, name", (region_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM buildings ORDER BY region_id, category, name"
            ).fetchall()
        if not rows:
            return "（暂无建筑在册）"
        lines: List[str] = []
        for r in rows:
            metric = str(r["output_metric"])
            if metric:
                out = f"产出{metric}{r['output_amount']}"
            else:
                out = "无结算产出"
            lines.append(
                f"{r['name']}（{r['category']}·{r['region_id']}）等级{r['level']}，"
                f"完好{r['condition']}，维护{r['maintenance']}{MONEY_UNIT}/{TURN_UNIT}，"
                f"风险{r['risk']}，{out}。{r['status']}"
            )
        return "\n".join(lines)

    def building_payload(self, region_id: str = "") -> List[Dict[str, object]]:
        """建筑结构化清单，供 web。region_id 为空取全国。"""
        if region_id:
            rows = self.conn.execute(
                "SELECT * FROM buildings WHERE region_id = ? ORDER BY category, name", (region_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM buildings ORDER BY region_id, category, name"
            ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "region_id": str(r["region_id"]),
                "name": str(r["name"]),
                "category": str(r["category"]),
                "level": int(r["level"]),
                "condition": int(r["condition"]),
                "maintenance": int(r["maintenance"]),
                "risk": int(r["risk"]),
                "output_metric": str(r["output_metric"]),
                "output_amount": int(r["output_amount"]),
                "status": str(r["status"]),
                "origin": str(r["origin"]),
            }
            for r in rows
        ]

    def building_detail(self, name_or_id: str) -> str:
        key = (name_or_id or "").strip()
        row = self.conn.execute(
            "SELECT * FROM buildings WHERE id = ? OR name = ?", (key, key)
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM buildings WHERE name LIKE ?", (f"%{key}%",)
            ).fetchone()
        if row is None:
            raise ValueError(f"未找到建筑 '{name_or_id}'")
        metric = str(row["output_metric"])
        out = f"产出{metric}{row['output_amount']}/{TURN_UNIT}" if metric else "无结算产出"
        return (
            f"{row['name']}（{row['category']}，{row['region_id']}，{row['origin']}）："
            f"等级{row['level']}，完好{row['condition']}，"
            f"维护{row['maintenance']}{MONEY_UNIT}/{TURN_UNIT}，风险{row['risk']}，{out}。\n"
            f"{row['status']}"
        )

    # ── 科技实体（technologies）─────────────────────────────────────────────
    # 科技无月度产出：表只作「已解锁科技清单」展示+查重，研发进度由其 issue 的 bar 承载，
    # 结案才落一行（=已解锁）。一次性数值走 issue effect_on_resolve；预设科技额外挂永久 legacy。

    def add_technology(
        self, state: GameState, name: str, category: str, *,
        effect_summary: str = "", status: str = "", origin: str = "issue",
    ) -> str:
        """解锁科技：落 technologies 一行。同名已存在则直接返回旧 id（查重，不重复落）。"""
        nm = name.strip()[:60] or "无名科技"
        existing = self.conn.execute("SELECT id FROM technologies WHERE name = ?", (nm,)).fetchone()
        if existing is not None:
            return str(existing["id"])
        seq = self.conn.execute("SELECT COUNT(*) FROM technologies").fetchone()[0]
        tech_id = f"tech_{int(seq) + 1}"
        while self.conn.execute("SELECT 1 FROM technologies WHERE id = ?", (tech_id,)).fetchone():
            seq += 1
            tech_id = f"tech_{int(seq) + 1}"
        self.conn.execute(
            """
            INSERT INTO technologies (id, name, category, effect_summary, status, origin, created_turn)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tech_id, nm, category.strip()[:20] or "科技",
             effect_summary.strip()[:160], status.strip()[:160] or "已解锁。",
             origin, state.turn),
        )
        self.commit()
        return tech_id

    def technology_payload(self) -> List[Dict[str, object]]:
        """已解锁科技清单，供 simulator/extractor/web。"""
        rows = self.conn.execute(
            "SELECT id, name, category, effect_summary, status, origin FROM technologies ORDER BY created_turn, id"
        ).fetchall()
        return [
            {
                "id": str(r["id"]), "name": str(r["name"]), "category": str(r["category"]),
                "effect_summary": str(r["effect_summary"]), "status": str(r["status"]),
                "origin": str(r["origin"]),
            }
            for r in rows
        ]

    # ── 部门（复用 offices 表插行）──────────────────────────────────────────
    # 新设衙门 office_type 唯一；可被 characters.office_type 引用、可派大臣（默认 _base skill）。
    # 预设衙门额外挂永久 legacy（见 issues._apply_issue_departments）。

    def add_department(
        self, name: str, *, skills: Optional[List[str]] = None, tools: Optional[List[str]] = None,
        authority_scope: str = "", power: int = 50, responsibility: int = 50,
        corruption_risk: int = 30, origin: str = "issue",
    ) -> str:
        """新设衙门：往 offices 插一行（office_type=部门名）。已存在则跳过、返回原名。"""
        office_type = name.strip()[:40]
        if not office_type:
            raise ValueError("add_department: 部门名为空")
        if self.conn.execute("SELECT 1 FROM offices WHERE office_type = ?", (office_type,)).fetchone():
            return office_type  # 已存在，查重跳过
        self.conn.execute(
            """
            INSERT INTO offices
            (office_type, skills, tools, authority_scope, power, responsibility, corruption_risk, origin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (office_type,
             json.dumps(skills or [], ensure_ascii=False),
             json.dumps(tools or [], ensure_ascii=False),
             authority_scope.strip()[:200],
             max(0, min(100, int(power))),
             max(0, min(100, int(responsibility))),
             max(0, min(100, int(corruption_risk))),
             origin),
        )
        self.commit()
        return office_type

    def department_payload(self) -> List[Dict[str, object]]:
        """玩家新设衙门清单（origin='issue'），供 simulator/extractor。预设六部内阁不重复喂。"""
        rows = self.conn.execute(
            "SELECT office_type, authority_scope, power, responsibility, corruption_risk "
            "FROM offices WHERE origin = 'issue' AND trim(authority_scope) <> '' ORDER BY office_type"
        ).fetchall()
        return [
            {
                "name": str(r["office_type"]), "authority_scope": str(r["authority_scope"]),
                "power": int(r["power"]), "responsibility": int(r["responsibility"]),
                "corruption_risk": int(r["corruption_risk"]),
            }
            for r in rows
        ]
