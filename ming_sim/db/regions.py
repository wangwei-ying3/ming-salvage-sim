"""regions / region_logs / classes：两京十三省与阶级，增量、明细、回合摘要、撤回恢复。

_RegionsMixin：拆自原 db.py，方法体逐字未改。"""

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
    FISCAL_QUANTITY_FIELDS, FISCAL_SCORE_FIELDS, REGION_FIELD_ALIASES, REGION_SCORE_FIELDS, REGION_TEXT_FIELDS, TURN_UNIT,
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


class _RegionsMixin:
    def region_rows(self, limit: int | None = None, danger_order: bool = False) -> List[sqlite3.Row]:
        order = (
            "(unrest + military_pressure + gentry_resistance + (100 - public_support)) DESC, name"
            if danger_order
            else "kind DESC, name"
        )
        sql = f"""
            SELECT *
            FROM regions
            ORDER BY {order}
        """
        params: Tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return self.conn.execute(sql, params).fetchall()

    def region_payload(self, limit: int | None = None, danger_order: bool = False) -> List[Dict[str, object]]:
        payload: List[Dict[str, object]] = []
        for row in self.region_rows(limit=limit, danger_order=danger_order):
            try:
                fiscal = json.loads(str(row["fiscal"] or "{}"))
            except Exception:
                fiscal = {}
            payload.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "population": int(row["population"]),
                    "public_support": int(row["public_support"]),
                    "unrest": int(row["unrest"]),
                    "natural_disaster": row["natural_disaster"],
                    "human_disaster": row["human_disaster"],
                    "registered_land": int(row["registered_land"]),
                    "hidden_land": int(row["hidden_land"]),
                    "tax_per_turn": int(row["tax_per_turn"]),
                    "fiscal": fiscal,
                    "grain_output": int(fiscal.get("grain_output") or 0),
                    "grain_stock": int(fiscal.get("grain_stock") or 0),
                    "gentry_resistance": int(row["gentry_resistance"]),
                    "military_pressure": int(row["military_pressure"]),
                    "status": row["status"],
                    "controlled_by": row["controlled_by"],
                }
            )
        return payload

    def region_report(self, limit: int = 5) -> str:
        rows = self.region_rows(limit=limit, danger_order=True)
        if not rows:
            return "地区尚未建档。"
        total_tax = self.conn.execute("SELECT SUM(tax_per_turn) AS total FROM regions").fetchone()
        total_tax_value = int(total_tax["total"] or 0)
        parts = []
        for row in rows:
            try:
                fiscal = json.loads(str(row["fiscal"] or "{}"))
            except Exception:
                fiscal = {}
            held = ""
            if str(row["controlled_by"]) != "ming":
                held = f"【已为{self.power_display_name(row['controlled_by'])}所据】"
            parts.append(
                f"{row['name']}{held}：民心{row['public_support']}、动乱{row['unrest']}、"
                f"粮食年产{int(fiscal.get('grain_output') or 0)}万石、"
                f"可调余粮{int(fiscal.get('grain_stock') or 0)}万石、"
                f"田赋{format_money(monthly_amount(int(row['tax_per_turn'])))}/{TURN_UNIT}，{row['status']}"
            )
        return f"地区警讯：{'；'.join(parts)}。两京十三省田赋账面合计{format_money(monthly_amount(total_tax_value))}/{TURN_UNIT}（不含辽饷/盐/商）。"

    def region_detail(self, raw_name: str) -> str:
        region_id = match_region_id_from_text(raw_name, self.content.regions)
        if region_id is None:
            raise ValueError(f"未找到地区：{raw_name}")
        row = self.conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
        if row is None:
            raise ValueError(f"地区未入库：{raw_name}")
        held = ""
        if str(row["controlled_by"]) != "ming":
            held = f"，控制权：已为{self.power_display_name(row['controlled_by'])}所据（非大明辖治）"
        try:
            fiscal = json.loads(str(row["fiscal"] or "{}"))
        except Exception:
            fiscal = {}
        return (
            f"{row['name']}（{row['kind']}）{held}：人口{row['population']}万人，"
            f"民心{row['public_support']}，动乱{row['unrest']}，"
            f"粮食年产{int(fiscal.get('grain_output') or 0)}万石，可调余粮{int(fiscal.get('grain_stock') or 0)}万石，"
            f"田亩{row['registered_land']}万亩，隐田{row['hidden_land']}万亩，"
            f"田赋账面{format_money(monthly_amount(int(row['tax_per_turn'])))}/{TURN_UNIT}（另有辽饷/盐/商各计），"
            f"士绅阻力{row['gentry_resistance']}，军事压力{row['military_pressure']}。"
            f"天灾：{row['natural_disaster']}；人祸：{row['human_disaster']}；状态：{row['status']}"
        )

    def turn_region_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT rl.*, r.name AS region_name
            FROM region_logs rl
            JOIN regions r ON r.id = rl.region_id
            WHERE rl.turn = ?
            ORDER BY rl.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}地区盘面无明确变化。"
        parts = []
        for row in rows:
            label = REGION_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['region_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                parts.append(f"{row['region_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def apply_region_deltas(
        self,
        state: GameState,
        event: Event,
        edict_id: int | None,
        actor: str,
        region_deltas: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        changes: List[Dict[str, object]] = []
        # 特殊 key「全国/__all__」：玩家不分省的商税/盐税加征减征，按各省现有占比摊到每省 fiscal。
        _NATIONWIDE_KEYS = {"全国", "__all__", "all", "nationwide"}
        _NATIONWIDE_FIELD_STEM = {"commerce_tax": "商税", "salt_tax": "盐税",
                                  "商税基数": "商税", "盐税基数": "盐税"}
        for region_id, raw_changes in region_deltas.items():
            if str(region_id).strip() in _NATIONWIDE_KEYS:
                reason = str(raw_changes.get("reason") or raw_changes.get("原因") or event.title).strip()[:80]
                for raw_field, value in raw_changes.items():
                    stem = _NATIONWIDE_FIELD_STEM.get(str(raw_field).strip())
                    if stem is None:
                        continue  # 全国 key 只摊商税/盐税绝对额；其余字段需具体省份，忽略
                    try:
                        delta = int(value)
                    except (TypeError, ValueError):
                        continue
                    touched = self.apply_dynamic_fiscal_delta(stem, delta)
                    if touched:
                        changes.append({"region_id": "全国", "field": stem,
                                        "delta": delta, "touched": touched, "reason": reason})
                continue
            row = self.conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
            if row is None:
                print(f"[WARN] region_delta 引用未入库地区 '{region_id}' → 跳过")
                continue
            reason = str(raw_changes.get("reason") or raw_changes.get("原因") or event.title).strip()[:80]
            for raw_field, value in raw_changes.items():
                field = REGION_FIELD_ALIASES.get(str(raw_field).strip(), str(raw_field).strip())
                if field == "reason":
                    continue
                # 先判字段合法，再取值：非法字段直接报清楚。
                all_direct = REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS + REGION_TEXT_FIELDS
                if field not in all_direct and field not in FISCAL_SCORE_FIELDS and field not in FISCAL_QUANTITY_FIELDS:
                    raise LLMContractError(
                        f"{TURN_UNIT}末执行评估引用了非法地区字段：'{raw_field}'（地区 '{region_id}'）。"
                        f"合法字段：{all_direct + FISCAL_SCORE_FIELDS + FISCAL_QUANTITY_FIELDS}"
                    )

                # ── fiscal JSON 子字段（corruption 等）────────────────────────
                if field in FISCAL_SCORE_FIELDS or field in FISCAL_QUANTITY_FIELDS:
                    current_row = self.conn.execute(
                        "SELECT fiscal FROM regions WHERE id = ?", (region_id,)
                    ).fetchone()
                    fiscal: dict = json.loads(str((current_row or row)["fiscal"] or "{}"))
                    old_value = fiscal.get(field, 50 if field in FISCAL_SCORE_FIELDS else 0)
                    delta = int(value)
                    if field in FISCAL_SCORE_FIELDS:
                        # 帝国修正：该地区该字段若有 active 修正符，先放大/缩小 delta
                        net_pct = int(((self.legacy_modifiers(state).get("regions") or {})
                                       .get(region_id) or {}).get(field, 0) or 0)
                        if net_pct:
                            delta = self.apply_legacy_pct(delta, net_pct)
                        new_value = max(0, min(100, int(old_value) + delta))
                    else:
                        new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    fiscal[field] = new_value
                    self.conn.execute(
                        "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (json.dumps(fiscal, ensure_ascii=False), region_id),
                    )
                    self.conn.execute(
                        """
                        INSERT INTO region_logs
                        (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (state.turn, state.year, state.period, region_id,
                         field, str(old_value), str(new_value), actual_delta,
                         reason, event.id, edict_id, actor),
                    )
                    changes.append({
                        "region": row["name"], "field": field,
                        "label": REGION_FIELD_LABELS.get(field, field),
                        "old": old_value, "new": new_value,
                        "delta": actual_delta, "reason": reason,
                    })
                    continue

                # ── 直接列字段 ────────────────────────────────────────────────
                old_value = row[field]
                if field in REGION_SCORE_FIELDS:
                    delta = int(value)
                    # 遗产百分比修正：该地区该字段若有 active 遗产修正符，先放大/缩小 delta
                    net_pct = int(((self.legacy_modifiers(state).get("regions") or {})
                                   .get(region_id) or {}).get(field, 0) or 0)
                    if net_pct:
                        delta = self.apply_legacy_pct(delta, net_pct)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                elif field in REGION_QUANTITY_FIELDS:
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                else:  # REGION_TEXT_FIELDS
                    text_value = str(value).strip()[:160]
                    if field == "controlled_by":
                        # 先归一化（中文名/别名/大小写 → 标准 power id），与 armies.py 同。
                        text_value = _normalize_power_id(self.conn, text_value) or text_value
                    if not text_value or text_value in ("None", "null") or text_value == str(old_value):
                        continue
                    if field == "controlled_by":
                        valid_powers = {r[0] for r in self.conn.execute("SELECT id FROM powers")}
                        if text_value not in valid_powers:
                            print(f"[WARN] controlled_by 非法值 '{text_value}'（地区 '{region_id}'）→ 跳过")
                            continue
                    stored_new = text_value
                    log_delta = None
                self.conn.execute(
                    f"UPDATE regions SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, region_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO region_logs
                    (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn, state.year, state.period, region_id,
                        field, str(old_value), str(stored_new), log_delta,
                        reason, event.id, edict_id, actor,
                    ),
                )
                changes.append(
                    {
                        "region": row["name"], "field": field,
                        "label": REGION_FIELD_LABELS.get(field, field),
                        "old": old_value, "new": stored_new,
                        "delta": log_delta, "reason": reason,
                    }
                )

                # ── 收复触发：controlled_by 由非 ming → ming，覆盖 on_restore 预置 ──
                if (
                    field == "controlled_by"
                    and str(stored_new) == "ming"
                    and str(old_value) != "ming"
                ):
                    extra = self._apply_on_restore(state, region_id, event, edict_id, actor, reason)
                    changes.extend(extra)
        self.commit()
        return changes

    def _apply_on_restore(
        self,
        state: GameState,
        region_id: str,
        event: Event,
        edict_id: int | None,
        actor: str,
        reason: str,
    ) -> List[Dict[str, object]]:
        """收复瞬间用 region.on_restore 覆盖主字段，记 region_logs。"""
        region_def = self.content.regions.get(region_id)
        if region_def is None or not region_def.on_restore:
            return []
        preset = region_def.on_restore
        row = self.conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
        if row is None:
            return []
        all_direct = REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS + REGION_TEXT_FIELDS
        out: List[Dict[str, object]] = []
        for raw_field, value in preset.items():
            if raw_field == "fiscal":
                if not isinstance(value, dict):
                    continue
                fiscal = json.loads(str(row["fiscal"] or "{}"))
                for sub_field, sub_val in value.items():
                    if sub_field not in FISCAL_SCORE_FIELDS:
                        continue
                    old_sub = fiscal.get(sub_field, 0)
                    new_sub = int(sub_val)
                    if int(old_sub) == new_sub:
                        continue
                    fiscal[sub_field] = new_sub
                    self.conn.execute(
                        "INSERT INTO region_logs (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (state.turn, state.year, state.period, region_id,
                         sub_field, str(old_sub), str(new_sub), new_sub - int(old_sub),
                         f"收复重置：{reason}", event.id, edict_id, actor),
                    )
                    out.append({
                        "region": row["name"], "field": sub_field,
                        "label": REGION_FIELD_LABELS.get(sub_field, sub_field),
                        "old": old_sub, "new": new_sub,
                        "delta": new_sub - int(old_sub), "reason": f"收复重置：{reason}",
                    })
                self.conn.execute(
                    "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(fiscal, ensure_ascii=False), region_id),
                )
                continue
            if raw_field == "controlled_by":
                continue  # 控制权已写完，跳过
            if raw_field not in all_direct:
                continue
            old_val = row[raw_field]
            if raw_field in (REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS):
                new_val: object = int(value)
            else:
                new_val = str(value)
            if str(old_val) == str(new_val):
                continue
            self.conn.execute(
                f"UPDATE regions SET {raw_field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_val, region_id),
            )
            log_delta = (int(new_val) - int(old_val)) if raw_field in (REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS) else None
            self.conn.execute(
                "INSERT INTO region_logs (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (state.turn, state.year, state.period, region_id,
                 raw_field, str(old_val), str(new_val), log_delta,
                 f"收复重置：{reason}", event.id, edict_id, actor),
            )
            out.append({
                "region": row["name"], "field": raw_field,
                "label": REGION_FIELD_LABELS.get(raw_field, raw_field),
                "old": old_val, "new": new_val,
                "delta": log_delta, "reason": f"收复重置：{reason}",
            })
        return out

    def class_rows(self, region_id: str = "") -> List[sqlite3.Row]:
        """region_id="" 取全国汇总行；其它取该省切片。"""
        return self.conn.execute(
            "SELECT name, region_id, population, satisfaction, leverage, agenda "
            "FROM classes WHERE region_id = ? ORDER BY name",
            (region_id,),
        ).fetchall()

    def class_report(self) -> str:
        """全国汇总 + 各省紧张切片（sat<=30 且 lev>=60）。"""
        national = self.class_rows("")
        if not national:
            return "阶级未建档。"
        head = "；".join(
            f"{row['name']}满意{row['satisfaction']}、势力{row['leverage']}（{row['agenda']}）"
            for row in national
        )
        hot = self.conn.execute(
            """
            SELECT c.name, c.region_id, c.satisfaction, c.leverage, r.name AS region_name
            FROM classes c
            LEFT JOIN regions r ON r.id = c.region_id
            WHERE c.region_id <> '' AND c.satisfaction <= 30 AND c.leverage >= 60
            ORDER BY c.satisfaction ASC, c.leverage DESC
            """
        ).fetchall()
        if not hot:
            return f"阶级总览：{head}。各省阶级暂无高压预警。"
        warn = "；".join(
            f"{row['region_name'] or row['region_id']} {row['name']}满意{row['satisfaction']}/势力{row['leverage']}"
            for row in hot
        )
        return f"阶级总览：{head}。高压预警：{warn}。"

    def adjust_classes(self, deltas: Dict[str, Dict[str, int]]) -> None:
        """deltas 结构：{ key: {satisfaction: +/-N, leverage: +/-N} }
        key 形式：'农民' (全国) 或 '农民@shaanxi' (省级)。
        """
        for key, fields in deltas.items():
            if not fields:
                continue
            if "@" in key:
                name, region_id = key.split("@", 1)
            else:
                name, region_id = key, ""
            row = self.conn.execute(
                "SELECT satisfaction, leverage FROM classes WHERE name = ? AND region_id = ?",
                (name.strip(), region_id.strip()),
            ).fetchone()
            if not row:
                continue
            sat = int(row["satisfaction"]) + int(fields.get("satisfaction", 0) or 0)
            lev = int(row["leverage"]) + int(fields.get("leverage", 0) or 0)
            sat = max(0, min(100, sat))
            lev = max(0, min(100, lev))
            self.conn.execute(
                "UPDATE classes SET satisfaction = ?, leverage = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE name = ? AND region_id = ?",
                (sat, lev, name.strip(), region_id.strip()),
            )
        self.conn.commit()
