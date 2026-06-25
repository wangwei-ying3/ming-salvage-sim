"""armies / army_logs：军队盘面、名册、增量、从推演新建军。

_ArmiesMixin：拆自原 db.py，方法体逐字未改。"""

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
from ming_sim.content import GameContent, canon_troop_name, normalize_troop_composition
from ming_sim.matching import match_army_id_from_text, match_region_id_from_text
from ming_sim.models import Army, Event, GameState, monthly_amount, period_label
from ming_sim.token_stats import tlog
from ming_sim.db._helpers import (
    normalize_office, infer_office_type_from_office,
    _compact_lookup_text, _normalize_power_id,
    COURT_OFFICE_TYPES, MINISTRY_OFFICE_TYPES,
)


class _ArmiesMixin:
    def _army_troop_composition(self, row: sqlite3.Row | Dict[str, object]) -> Dict[str, int]:
        spec = self.content.troop_cost
        raw = row["troop_composition"] if "troop_composition" in row.keys() else "{}"  # type: ignore[attr-defined]
        try:
            data = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            out: Dict[str, int] = {}
            for key, value in data.items():
                troop = canon_troop_name(str(key).strip(), spec)
                try:
                    amount = int(value)
                except (TypeError, ValueError):
                    amount = 0
                if troop and amount > 0:
                    out[troop] = out.get(troop, 0) + amount
            if out:
                return out
        troop_type = str(row["troop_type"] or "")
        manpower = int(row["manpower"] or 0)
        return {canon_troop_name(troop_type, spec): manpower} if troop_type and manpower > 0 else {}

    def _composition_text(self, composition: Dict[str, int]) -> str:
        return "、".join(k for k, v in composition.items() if int(v) > 0)

    def _gate_troop_composition(self, composition: Dict[str, int]) -> Tuple[Dict[str, int], List[str]]:
        """对已归一的 composition 做科技门控：预设兵种未解锁（troop_unlocked=False）的，
        兵力并入 default_tier（不断游戏）。runtime/AI 新兵种 troop_unlocked 默认放行，原样保留。
        体现「预设超前兵种须先研成对应科技才能编」。

        装备维度不在此门控——「升级须有对应实物装备、按持械量定升级人数」由 AI（simulator/extractor）
        软判（提示词讲规则+例子，payload 喂 held_arms），程序只信任 AI 输出的 composition。

        返回 (门控后 composition, 人话说明清单)——说明透出到回合明细，让玩家知道程序代为降级了。"""
        default_tier = str(self.content.troop_cost.get("default_tier") or "非正规步兵")
        gated: Dict[str, int] = {}
        notes: List[str] = []
        for troop, amount in composition.items():
            if amount <= 0:
                continue
            if self.troop_unlocked(troop):
                gated[troop] = gated.get(troop, 0) + amount
            else:
                req = self._troop_required_tech(troop)
                req_hint = f"需先研成「{req}」" if req else "前置科技未解锁"
                note = f"{troop}{amount}兵{req_hint}，暂并入「{default_tier}」"
                print(f"[WARN] {note}")
                notes.append(note)
                gated[default_tier] = gated.get(default_tier, 0) + amount
        return gated, notes

    def _troop_required_tech(self, tier_name: str) -> str:
        """该兵种的前置科技名（未注册/无门槛→空串）。供门控判定与说明用。"""
        row = self.conn.execute(
            "SELECT requires_tech FROM troop_tiers WHERE name=?", (str(tier_name or ""),)).fetchone()
        return str(row["requires_tech"] or "").strip() if row else ""

    def _apply_composition_delta(self, composition: Dict[str, int], troop_type: str, delta: int) -> Dict[str, int]:
        # 扩/裁编兵力增量归到哪个兵种：troop_hint（番号/兵种串）归一到固定兵种名（闭集），
        # 不再硬编码「步兵/骑兵/海军/空军」基类（那些不在 troop_cost 闭集里）。
        troop = canon_troop_name(str(troop_type or ""), self.content.troop_cost)
        new_comp = dict(composition)
        new_amount = max(0, int(new_comp.get(troop, 0)) + int(delta))
        if new_amount:
            new_comp[troop] = new_amount
        else:
            new_comp.pop(troop, None)
        return new_comp

    def army_rows(self, limit: int | None = None, danger_order: bool = False) -> List[sqlite3.Row]:
        # arrears 是累计欠饷万两，须按 maintenance 归一成"欠饷月数*10"再加权（0-100 量级）
        # CASE 兼容 SQLite（无标量 MIN/LEAST）：maintenance=0 视为 0；归一后截至 100。
        arrears_norm = (
            "CASE "
            "WHEN maintenance_per_turn IS NULL OR maintenance_per_turn = 0 THEN 0 "
            "WHEN arrears * 10 / maintenance_per_turn > 100 THEN 100 "
            "ELSE arrears * 10 / maintenance_per_turn "
            "END"
        )
        order = (
            f"({arrears_norm} + (100 - supply) + (100 - morale) + (100 - loyalty) + (100 - training)) DESC, name"
            if danger_order
            else "theater, name"
        )
        sql = f"""
            SELECT *
            FROM armies
            WHERE active = 1
            ORDER BY {order}
        """
        params: Tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return self.conn.execute(sql, params).fetchall()

    def army_payload(self, limit: int | None = None, danger_order: bool = False) -> List[Dict[str, object]]:
        payload: List[Dict[str, object]] = []
        for row in self.army_rows(limit=limit, danger_order=danger_order):
            composition = self._army_troop_composition(row)
            payload.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "station": row["station"],
                    "theater": row["theater"],
                    "commander": row["commander"],
                    "controller": row["controller"],
                    "troop_type": row["troop_type"],
                    "troop_composition": composition,
                    "manpower": int(row["manpower"]),
                    "maintenance_per_turn": int(row["maintenance_per_turn"]),
                    "supply": int(row["supply"]),
                    "morale": int(row["morale"]),
                    "training": int(row["training"]),
                    "equipment": int(row["equipment"]),
                    "arrears": int(row["arrears"]),
                    "mobility": int(row["mobility"]),
                    "loyalty": int(row["loyalty"]),
                    "status": row["status"],
                    "owner_power": row["owner_power"],
                }
            )
        return payload

    def army_report(self, limit: int = 5) -> str:
        rows = self.army_rows(limit=limit, danger_order=True)
        if not rows:
            return "军队尚未建档。"
        total_manpower = self.conn.execute("SELECT SUM(manpower) AS total FROM armies WHERE active = 1").fetchone()
        total_maintenance = self.conn.execute("SELECT SUM(maintenance_per_turn) AS total FROM armies WHERE active = 1").fetchone()
        parts = []
        for row in rows:
            maint = int(row["maintenance_per_turn"]) or 0
            arr = int(row["arrears"]) or 0
            if maint > 0 and arr > 0:
                months = arr / maint
                arr_text = f"欠饷{arr}万两（约{months:.1f}月军饷）"
            else:
                arr_text = f"欠饷{arr}万两"
            parts.append(
                f"{row['name']}：驻{row['station']}，兵{row['manpower']}，"
                f"饷{format_money(monthly_amount(maint))} /{TURN_UNIT}，补给{row['supply']}、"
                f"士气{row['morale']}、{arr_text}，{row['status']}"
            )
        return (
            f"军队警讯：{'；'.join(parts)}。"
            f"建档兵力合计{int(total_manpower['total'] or 0)}人，账面{TURN_UNIT}维护费{format_money(monthly_amount(int(total_maintenance['total'] or 0)))}。"
        )

    def army_detail(self, raw_name: str) -> str:
        army_id = self._match_current_army_id(raw_name)
        if army_id is None:
            raise ValueError(f"未找到军队：{raw_name}")
        row = self.conn.execute("SELECT * FROM armies WHERE id = ?", (army_id,)).fetchone()
        if row is None:
            raise ValueError(f"军队未入库：{raw_name}")
        maint = int(row["maintenance_per_turn"]) or 0
        arr = int(row["arrears"]) or 0
        if maint > 0 and arr > 0:
            months = arr / maint
            arr_text = f"欠饷{arr}万两（约{months:.1f}月军饷）"
        else:
            arr_text = f"欠饷{arr}万两"
        return (
            f"{row['name']}：驻扎地{row['station']}，统帅{row['commander']}，"
            f"兵种{row['troop_type']}，人数{row['manpower']}人，"
            f"维护费{format_money(monthly_amount(maint))} /{TURN_UNIT}，补给{row['supply']}，"
            f"士气{row['morale']}，训练{row['training']}，装备{row['equipment']}，"
            f"{arr_text}，机动{row['mobility']}，忠诚{row['loyalty']}。"
            f"状态：{row['status']}"
        )

    def army_roster(self, filter_names: Optional[List[str]] = None, index_only: bool = False) -> str:
        """全军名册。filter_names 非空则只返回指定军队；index_only=True 只返回 id+军名索引。"""
        rows = self.conn.execute(
            "SELECT * FROM armies WHERE active = 1 ORDER BY owner_power='ming' DESC, theater, name"
        ).fetchall()
        if filter_names:
            wanted_ids = {
                matched
                for name in filter_names
                for matched in [self._match_current_army_id(str(name))]
                if matched
            }
            rows = [
                r for r in rows
                if r["id"] in wanted_ids or r["name"] in filter_names or r["id"] in filter_names
            ]
        if index_only:
            # 大臣 system 只给可查询对象名，完整欠饷/补给/士气等现值由 query_army_roster 按需查。
            own: List[str] = []
            other: List[str] = []
            for row in rows:
                if str(row["owner_power"]) == "ming":
                    own.append(f"{row['id']}|{row['name']}")
                else:
                    other.append(f"{row['id']}|{row['name']}")
            if not own and not other:
                return ""
            out = [
                "【全军名册索引（仅列可查询军名；谈某军欠饷/补给/士气/状态前先调 query_army_roster 查完整信息）】",
            ]
            if own:
                out.append("大明各军（列序＝id|军名）：")
                out.extend(own)
            if other:
                out.append("敌对/外藩军（可见情报，列序＝id|军名）：")
                out.extend(other)
            return "\n".join(out)
        if not rows:
            return ""
        own: List[str] = []
        other: List[str] = []
        for row in rows:
            maint = int(row["maintenance_per_turn"]) or 0
            arr = int(row["arrears"]) or 0
            # 全按月度：maintenance_per_turn 就是月饷，不除 3（别被 monthly_amount 命名误导）。
            monthly_pay = maint
            months = f"{arr / monthly_pay:.1f}" if monthly_pay > 0 and arr > 0 else "0"
            if str(row["owner_power"]) == "ming":
                # 列序见表头。兵力/月饷/欠饷单位万两；补给…忠诚为 0-100。
                own.append(
                    "|".join(str(x) for x in (
                        row["name"], row["station"], row["commander"], row["troop_type"],
                        row["manpower"], monthly_pay, row["supply"], row["morale"],
                        row["training"], row["equipment"], row["mobility"], row["loyalty"],
                        arr, months, row["status"],
                    ))
                )
            else:
                other.append(
                    "|".join(str(x) for x in (
                        row["name"], row["owner_power"], row["station"],
                        row["commander"], row["troop_type"], row["manpower"], row["status"],
                    ))
                )
        out = [
            "【全军名册（现状以此为准，谈某军欠饷/补给/士气直接据此；欠饷万两为精确累计值，非抽象分）】",
            "大明各军（| 分隔，列序＝军名|驻地|统帅|兵种|兵力|月饷万两|补给|士气|训练|装备|机动|忠诚|欠饷万两|欠饷月数|状态；补给…忠诚为0-100）：",
            *own,
        ]
        if other:
            out.append("敌对/外藩军（可见情报，列序＝军名|势力|驻地|统帅|兵种|兵力|状态）：")
            out.extend(other)
        return "\n".join(out)

    def turn_army_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT al.*, a.name AS army_name
            FROM army_logs al
            JOIN armies a ON a.id = al.army_id
            WHERE al.turn = ?
            ORDER BY al.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}军队盘面无明确变化。"
        parts = []
        for row in rows:
            label = ARMY_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['army_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                if row["field"] == "manpower":
                    parts.append(f"{row['army_name']}{label}{sign}{int(delta)}人（{row['reason']}）")
                elif row["field"] == "maintenance_per_turn":
                    parts.append(f"{row['army_name']}{label}{format_money_delta(int(delta))}（{row['reason']}）")
                else:
                    parts.append(f"{row['army_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def apply_army_deltas(
        self,
        state: GameState,
        event: Event,
        edict_id: int | None,
        actor: str,
        army_deltas: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        changes: List[Dict[str, object]] = []
        for army_id, raw_changes in army_deltas.items():
            lookup_id = str(army_id)
            row = self.conn.execute("SELECT * FROM armies WHERE id = ?", (lookup_id,)).fetchone()
            if row is None:
                matched_id = self._match_current_army_id(lookup_id)
                if matched_id:
                    lookup_id = matched_id
                    row = self.conn.execute("SELECT * FROM armies WHERE id = ?", (lookup_id,)).fetchone()
            if row is None:
                print(f"[WARN] army_delta 引用未入库军队 '{army_id}' → 跳过")
                continue
            army_id = lookup_id
            reason = str(raw_changes.get("reason") or raw_changes.get("原因") or event.title).strip()[:80]
            _valid_army_fields = set(ARMY_SCORE_FIELDS + ARMY_QUANTITY_FIELDS + ARMY_TEXT_FIELDS)
            # 规范化键，便于「兵力变了但军费没给」时同比例补一笔军费增量。
            norm_changes: Dict[str, object] = {
                ARMY_FIELD_ALIASES.get(str(k).strip(), str(k).strip()): v for k, v in raw_changes.items()
            }
            # 扩编/裁撤只给了 manpower 增量、漏了 maintenance_per_turn → 按兵种单价补军费增量
            # （单价×兵力增量，单价＝troop_cost.json 按 troop_type 匹配的兵种档）。
            # 否则兵力涨/缩了军费原地不动（提示词要求两者同给，这里兜底防 LLM 漏写）。
            if "manpower" in norm_changes and "maintenance_per_turn" not in norm_changes:
                try:
                    mp_delta = int(norm_changes["manpower"])
                except (TypeError, ValueError):
                    mp_delta = 0
                if mp_delta:
                    troop_hint = str(norm_changes.get("troop_type") or row["troop_type"] or "")
                    derived = self.content.troop_maintenance_delta(troop_hint, mp_delta)
                    if derived:
                        raw_changes = {**raw_changes, "maintenance_per_turn": derived}
            composition = self._army_troop_composition(row)
            for raw_field, value in raw_changes.items():
                field = ARMY_FIELD_ALIASES.get(str(raw_field).strip(), str(raw_field).strip())
                if field == "reason":
                    continue
                if field not in _valid_army_fields:
                    print(f"[WARN] army_delta 引用非法字段 '{raw_field}' → 跳过")
                    continue
                old_value = row[field]
                if field == "arrears":
                    # arrears 单位=累计欠饷万两，无上限，按需累加。
                    # 正常情况由 flows 唯一变更；此处兜底允许 extractor 在战损/裁军等
                    # 非现金原因下写入（提示词已禁，但保留兜底以防 LLM 越界不至于截断）。
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                elif field in ARMY_SCORE_FIELDS:
                    delta = int(value)
                    # 遗产百分比修正：该军该字段若有 active 遗产修正符，先放大/缩小 delta
                    net_pct = int(((self.legacy_modifiers(state).get("armies") or {})
                                   .get(army_id) or {}).get(field, 0) or 0)
                    if net_pct:
                        delta = self.apply_legacy_pct(delta, net_pct)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field == "manpower":
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    troop_hint = str(norm_changes.get("troop_type") or row["troop_type"] or "")
                    composition = self._apply_composition_delta(composition, troop_hint, actual_delta)
                    stored_new = new_value
                    log_delta = actual_delta
                elif field == "maintenance_per_turn":
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field in ARMY_TEXT_FIELDS:
                    if field == "troop_composition":
                        old_comp = self._army_troop_composition(row)
                        # extractor 给的 composition key 可能是脏名/番号 → 归一到固定兵种名（闭集）；
                        # 再过科技门控（ming 军：未解锁的超前兵种并入 default_tier）。
                        next_comp = normalize_troop_composition(value, troop_cost=self.content.troop_cost)
                        gate_notes: List[str] = []
                        if str(row["owner_power"]) == "ming":
                            next_comp, gate_notes = self._gate_troop_composition(next_comp)
                        if not next_comp or next_comp == old_comp:
                            continue
                        new_manpower = sum(next_comp.values())
                        new_maintenance = self.content.troop_maintenance_total(next_comp) if str(row["owner_power"]) == "ming" else int(row["maintenance_per_turn"])
                        new_troop_type = self._composition_text(next_comp)
                        old_text = json.dumps(old_comp, ensure_ascii=False)
                        new_text = json.dumps(next_comp, ensure_ascii=False)
                        self.conn.execute(
                            "UPDATE armies SET troop_composition=?, troop_type=?, manpower=?, maintenance_per_turn=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (new_text, new_troop_type, new_manpower, new_maintenance, army_id),
                        )
                        self.conn.execute(
                            """
                            INSERT INTO army_logs
                            (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                            """,
                            (state.turn, state.year, state.period, army_id, field, old_text, new_text, reason, event.id, edict_id, actor),
                        )
                        change_entry = {
                            "army": row["name"],
                            "field": field,
                            "label": ARMY_FIELD_LABELS.get(field, field),
                            "old": old_comp,
                            "new": next_comp,
                            "delta": None,
                            "reason": reason,
                        }
                        if gate_notes:
                            # 程序因科技门控代为降级了部分兵种，透出到明细让玩家知情
                            change_entry["note"] = "；".join(gate_notes)
                        changes.append(change_entry)
                        continue
                    text_value = str(value).strip()[:160]
                    if field == "owner_power":
                        text_value = _normalize_power_id(self.conn, text_value)
                        valid_powers = {r["id"] for r in self.conn.execute("SELECT id FROM powers").fetchall()}
                        if text_value not in valid_powers:
                            print(f"[WARN] army_delta 归属 '{value}' 未在 powers → 跳过")
                            continue
                    if not text_value or text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                else:
                    print(f"[WARN] army_delta 未处理字段 '{field}' → 跳过")
                    continue
                self.conn.execute(
                    f"UPDATE armies SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, army_id),
                )
                if field == "manpower":
                    self.conn.execute(
                        "UPDATE armies SET troop_composition=?, troop_type=? WHERE id=?",
                        (json.dumps(composition, ensure_ascii=False), self._composition_text(composition), army_id),
                    )
                self.conn.execute(
                    """
                    INSERT INTO army_logs
                    (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn,
                        state.year,
                        state.period,
                        army_id,
                        field,
                        str(old_value),
                        str(stored_new),
                        log_delta,
                        reason,
                        event.id,
                        edict_id,
                        actor,
                    ),
                )
                changes.append(
                    {
                        "army": row["name"],
                        "field": field,
                        "label": ARMY_FIELD_LABELS.get(field, field),
                        "old": old_value,
                        "new": stored_new,
                        "delta": log_delta,
                        "reason": reason,
                    }
                )
            # 本军本轮全部字段落库后：若 manpower 已归 0 且仍 active，自动撤销番号。
            # 兵尽即除——置 status='撤销'/active=0，清零维护费与欠饷（空壳不再吃饷累欠），
            # 行留库可被「收复/重建」事件复活。任何把兵打到 0 的来源（裁撤/战损/叛逃）统一收口于此。
            changes.extend(self._disband_if_empty(state, army_id, event, edict_id, actor, reason))
        self.commit()
        return changes

    def _match_current_army_id(self, raw_name: str) -> Optional[str]:
        """按当前数据库名册匹配军队，支持运行中改番号后的新名称。"""
        value = str(raw_name or "").strip()
        if not value:
            return None
        row = self.conn.execute(
            "SELECT id FROM armies WHERE id = ? OR name = ?",
            (value, value),
        ).fetchone()
        if row is not None:
            return str(row["id"])
        army_objs: Dict[str, Army] = {}
        for item in self.army_payload():
            try:
                army_objs[str(item["id"])] = Army(**item)
            except TypeError:
                continue
        return match_army_id_from_text(value, army_objs)

    def _disband_if_empty(
        self,
        state: GameState,
        army_id: str,
        event: Event,
        edict_id: int | None,
        actor: str,
        reason: str,
    ) -> List[Dict[str, object]]:
        """兵尽（manpower=0）则软删除番号：status='撤销'、active=0、维护费/欠饷清零。
        已 active=0 的不重复处理。返回追加的变更日志项。"""
        row = self.conn.execute(
            "SELECT name, manpower, active, maintenance_per_turn, arrears, status FROM armies WHERE id = ?",
            (army_id,),
        ).fetchone()
        if row is None or int(row["manpower"]) != 0 or int(row["active"]) == 0:
            return []
        disband_reason = (reason or f"{event.title}：兵尽番号裁撤").strip()[:80]
        extra: List[Dict[str, object]] = []
        # active / status 翻转 + 维护费、欠饷清零，逐项写 army_logs 留审计。
        field_updates = [
            ("active", int(row["active"]), 0),
            ("maintenance_per_turn", int(row["maintenance_per_turn"]), 0),
            ("arrears", int(row["arrears"]), 0),
        ]
        for field, old_v, new_v in field_updates:
            if old_v == new_v:
                continue
            self.conn.execute(
                f"UPDATE armies SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_v, army_id),
            )
            self._log_army_field(state, army_id, field, old_v, new_v, new_v - old_v, disband_reason, event, edict_id, actor)
        old_status = str(row["status"])
        if old_status != "撤销":
            self.conn.execute(
                "UPDATE armies SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("撤销", army_id),
            )
            self._log_army_field(state, army_id, "status", old_status, "撤销", None, disband_reason, event, edict_id, actor)
            extra.append({
                "army": str(row["name"]),
                "field": "status",
                "label": ARMY_FIELD_LABELS.get("status", "status"),
                "old": old_status,
                "new": "撤销",
                "delta": None,
                "reason": disband_reason,
            })
        return extra

    def _log_army_field(
        self, state: GameState, army_id: str, field: str,
        old_value: object, new_value: object, delta: int | None,
        reason: str, event: Event, edict_id: int | None, actor: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO army_logs
            (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.turn, state.year, state.period, army_id, field,
                str(old_value), str(new_value), delta, reason,
                event.id, edict_id, actor,
            ),
        )

    def _reactivate_if_refilled(
        self, state: GameState, army_id: str, item: Dict[str, object],
        reason: str, event: Event, actor: str,
    ) -> None:
        """已撤销番号（active=0）补兵满员后翻回 active=1，并把状态从「撤销」改成本次叙事状态。"""
        row = self.conn.execute(
            "SELECT manpower, active, status FROM armies WHERE id = ?", (army_id,)
        ).fetchone()
        if row is None or int(row["active"]) == 1 or int(row["manpower"]) <= 0:
            return
        new_status = str(item.get("status") or "重建").strip()[:160] or "重建"
        self.conn.execute(
            "UPDATE armies SET active = 1, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, army_id),
        )
        self._log_army_field(state, army_id, "active", 0, 1, 1, reason, event, None, actor)
        self._log_army_field(state, army_id, "status", str(row["status"]), new_status, None, reason, event, None, actor)

    def create_armies_from_extraction(
        self,
        state: GameState,
        new_armies: List[Dict[str, object]],
        actor: str = "档房",
    ) -> List[Dict[str, object]]:
        """据 extractor 输出建新军队。owner_power 必须是已知 power。

        既有军扩编/裁撤只能走 army_delta 的显式增减量；new_armies 的 manpower
        是新军初值，重复 id/name 不能自动并入旧军，否则会把现有人数当增兵导致翻倍。
        """
        valid_powers = {r["id"] for r in self.conn.execute("SELECT id FROM powers").fetchall()}
        created: List[Dict[str, object]] = []
        for raw in new_armies:
            if not isinstance(raw, dict):
                continue
            item = {POWER_FIELD_ALIASES.get(k, k) if False else k: v for k, v in raw.items()}
            # 规范键：复用 ARMY_FIELD_ALIASES（兼容中文）
            from ming_sim.constants import ARMY_FIELD_ALIASES as _AA
            item = {_AA.get(str(k).strip(), str(k).strip()): v for k, v in raw.items()}
            aid = str(item.get("id") or "").strip()
            if not aid:
                print(f"[WARN] new_armies 缺 id → 跳过: {raw}")
                continue
            owner = _normalize_power_id(self.conn, item.get("owner_power") or "ming") or "ming"
            if owner not in valid_powers:
                print(f"[WARN] new_armies owner_power '{owner}' 未在 powers → 跳过 {aid}")
                continue
            name = str(item.get("name") or aid).strip()
            # 查重：同 id 或 同 name → 跳过。既有军扩编必须走 army_delta 的显式增量，
            # 不能把新建军队初值当扩军增量并入旧军。
            existing = self.conn.execute(
                "SELECT id, name FROM armies WHERE id = ? OR name = ?", (aid, name)
            ).fetchone()
            if existing is not None:
                print(
                    f"[WARN] new_armies 重复 id/name '{aid}'/'{name}'，"
                    f"已存在 '{existing['id']}'/'{existing['name']}' → 跳过"
                )
                continue
            # 必填字段
            try:
                manpower = int(item["manpower"])
            except (KeyError, TypeError, ValueError):
                print(f"[WARN] new_armies '{aid}' 缺 manpower → 跳过")
                continue
            # 新建军 composition key / troop_type 都归一到固定兵种名（闭集），杜绝脏名入库
            troop_composition = normalize_troop_composition(
                item.get("troop_composition"),
                fallback_troop_type=str(item.get("troop_type") or ""),
                fallback_manpower=manpower,
                troop_cost=self.content.troop_cost,
            )
            gate_notes: List[str] = []
            if owner == "ming":
                troop_composition, gate_notes = self._gate_troop_composition(troop_composition)
            if troop_composition:
                manpower = sum(troop_composition.values())
            maintenance = self.content.troop_maintenance_total(troop_composition) if owner == "ming" else int(item.get("maintenance_per_turn") or 0)
            def _score(field: str, default: int = 50) -> int:
                try:
                    return max(0, min(100, int(item.get(field, default))))
                except (TypeError, ValueError):
                    return default
            def _arrears_init() -> int:
                # arrears 单位=累计欠饷万两，无上限；新军默认 0
                try:
                    return max(0, int(item.get("arrears", 0)))
                except (TypeError, ValueError):
                    return 0
            commander = str(item.get("commander") or "")
            row = (
                aid,
                name,
                str(item.get("station") or ""),
                str(item.get("theater") or ""),
                commander,
                str(item.get("controller") or commander),
                self._composition_text(troop_composition) or str(item.get("troop_type") or ""),
                json.dumps(troop_composition, ensure_ascii=False),
                max(0, manpower),
                max(0, maintenance),
                _score("supply"),
                _score("morale"),
                _score("training"),
                _score("equipment"),
                _arrears_init(),
                _score("mobility"),
                _score("loyalty"),
                str(item.get("status") or "新立"),
                owner,
            )
            try:
                self.conn.execute(
                    """
                    INSERT INTO armies
                    (id, name, station, theater, commander, controller, troop_type, troop_composition, manpower,
                     maintenance_per_turn, supply, morale, training, equipment, arrears,
                     mobility, loyalty, status, owner_power)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
            except sqlite3.IntegrityError as exc:
                print(f"[WARN] new_armies INSERT 失败 '{aid}': {exc}")
                continue
            reason = str(item.get("reason") or item.get("status") or "新立军队")[:80]
            self.conn.execute(
                """
                INSERT INTO army_logs
                (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                VALUES (?, ?, ?, ?, 'created', '', ?, ?, ?, 'season', NULL, ?)
                """,
                (state.turn, state.year, state.period, aid, str(manpower), manpower, reason, actor),
            )
            created_entry = {
                "army": name,
                "id": aid,
                "owner_power": owner,
                "manpower": manpower,
                "created": True,
                "reason": reason,
            }
            if gate_notes:
                created_entry["note"] = "；".join(gate_notes)
            created.append(created_entry)
        self.commit()
        return created
