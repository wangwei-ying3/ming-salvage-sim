"""turn_reports / turn_extractions / ending_summary / turn_directives：回合产物与诏书草案。
（HITL 决策点上下文已改走 GameSession 进程内存，不再落库。）

_TurnsMixin：拆自原 db.py，方法体逐字未改。"""

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


class _TurnsMixin:
    def save_turn_report(self, state: GameState, report: str) -> None:
        """每回合月末奏报单独存档（turn_reports）。"""
        self.conn.execute(
            """
            INSERT INTO turn_reports (turn, year, period, report)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(turn) DO UPDATE SET
                year = excluded.year,
                period = excluded.period,
                report = excluded.report
            """,
            (state.turn, state.year, state.period, report),
        )
        self.commit()

    def get_turn_report(self, turn: int) -> str:
        row = self.conn.execute(
            "SELECT report FROM turn_reports WHERE turn = ?",
            (turn,),
        ).fetchone()
        return (row["report"] if row else "") or ""

    # ── 结局总结 ──

    def save_ending_summary(
        self, state: GameState, ending_status: str, summary: str, timeline: List[Dict[str, object]]
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO ending_summary (turn, year, period, ending_status, summary, timeline)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn) DO UPDATE SET
                year = excluded.year, period = excluded.period,
                ending_status = excluded.ending_status,
                summary = excluded.summary, timeline = excluded.timeline
            """,
            (
                state.turn, state.year, state.period, str(ending_status or ""),
                str(summary or ""), json.dumps(timeline or [], ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def get_ending_summary(self) -> Optional[Dict[str, object]]:
        """取最近一条结局总结（单库一局，按 turn 取最大）。无则 None。"""
        row = self.conn.execute(
            "SELECT turn, year, period, ending_status, summary, timeline "
            "FROM ending_summary ORDER BY turn DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        try:
            timeline = json.loads(row["timeline"] or "[]")
        except Exception:
            timeline = []
        return {
            "turn": int(row["turn"]),
            "year": int(row["year"]),
            "period": int(row["period"]),
            "ending_status": row["ending_status"],
            "summary": row["summary"] or "",
            "timeline": timeline,
        }

    def list_archived_turns(self) -> List[Dict[str, object]]:
        """所有已存档回合（turn_reports/turn_extractions/turn_directives 任一有数据）。
        返回按 turn 升序的元信息列表，每项含 turn/year/period 与各来源是否存在。"""
        rows = self.conn.execute(
            """
            SELECT t.turn AS turn,
                   MAX(t.year) AS year,
                   MAX(t.period) AS period,
                   MAX(t.has_report) AS has_report,
                   MAX(t.has_extraction) AS has_extraction,
                   MAX(t.has_directive) AS has_directive
            FROM (
                SELECT turn, year, period, 1 AS has_report, 0 AS has_extraction, 0 AS has_directive
                FROM turn_reports
                UNION ALL
                SELECT turn, year, period, 0, 1, 0 FROM turn_extractions
                UNION ALL
                SELECT turn, year, period, 0, 0, 1 FROM turn_directives
                WHERE status = 'issued'
                UNION ALL
                SELECT turn, year, period, 0, 0, 1 FROM turn_structured_directives
                WHERE status = 'issued'
            ) AS t
            GROUP BY t.turn
            ORDER BY t.turn
            """
        ).fetchall()
        return [
            {
                "turn": int(r["turn"]),
                "year": int(r["year"]),
                "period": int(r["period"]),
                "has_report": bool(r["has_report"]),
                "has_extraction": bool(r["has_extraction"]),
                "has_directive": bool(r["has_directive"]),
            }
            for r in rows
        ]

    def list_directives_by_turn(self, turn: int) -> List[Dict[str, object]]:
        """读某回合已颁诏（issued）草案，按 id 升序。"""
        rows = self.conn.execute(
            """
            SELECT d.id, d.turn, d.year, d.period, d.event_id, d.actor,
                   d.skill_id, d.text, d.source, d.status, d.notes,
                   d.created_at, d.updated_at,
                   e.title AS event_title
            FROM turn_directives d
            LEFT JOIN events e ON e.id = d.event_id
            WHERE d.turn = ? AND d.status = 'issued'
            ORDER BY d.id
            """,
            (int(turn),),
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "turn": int(r["turn"]),
                "year": int(r["year"]),
                "period": int(r["period"]),
                "event_id": r["event_id"] or "",
                "event_title": r["event_title"] or "",
                "actor": r["actor"] or "",
                "skill_id": r["skill_id"] or "",
                "text": r["text"] or "",
                "source": r["source"] or "",
                "status": r["status"] or "",
                "notes": r["notes"] or "",
                "created_at": r["created_at"] or "",
                "updated_at": r["updated_at"] or "",
            }
            for r in rows
        ]

    def save_turn_extraction(
        self,
        state: GameState,
        decree_text: str = "",
        narrative: str = "",
        extractor_input: str = "",
        extractor_output: str = "",
    ) -> None:
        """推演链原始输入/输出留痕（turn_extractions），事后可追可重放。"""
        self.conn.execute(
            """
            INSERT INTO turn_extractions
                (turn, year, period, decree_text, narrative, extractor_input, extractor_output)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn) DO UPDATE SET
                year = excluded.year,
                period = excluded.period,
                decree_text = excluded.decree_text,
                narrative = excluded.narrative,
                extractor_input = excluded.extractor_input,
                extractor_output = excluded.extractor_output
            """,
            (state.turn, state.year, state.period, decree_text, narrative,
             extractor_input, extractor_output),
        )
        self.commit()

    def get_turn_extraction(self, turn: int) -> Optional[Dict[str, object]]:
        """读 turn_extractions 一行；extractor_output JSON 解析失败时原样回字符串。"""
        row = self.conn.execute(
            "SELECT turn, year, period, decree_text, narrative, extractor_input, extractor_output "
            "FROM turn_extractions WHERE turn = ?",
            (int(turn),),
        ).fetchone()
        if row is None:
            return None
        def _parse(text: str) -> object:
            text = (text or "").strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except Exception:
                pass
            # LLM 多输出一个 }，顶层提前关闭，trailing 是被截出的字段。
            # 去掉多余 }，接回 trailing（trailing 本身以顶层 } 结尾）。
            try:
                dec = json.JSONDecoder()
                obj, end = dec.raw_decode(text)
                trailing = text[end:].strip()
                if trailing.startswith(","):
                    prefix = text[:end].rstrip()
                    if prefix.endswith("}"):
                        fixed = prefix[:-1] + trailing
                        try:
                            return json.loads(fixed)
                        except Exception:
                            pass
                return obj
            except Exception:
                pass
            return text
        return {
            "turn": int(row["turn"]),
            "year": int(row["year"]),
            "period": int(row["period"]),
            "decree_text": row["decree_text"] or "",
            "narrative": row["narrative"] or "",
            "extractor_input": _parse(row["extractor_input"] or ""),
            "extractor_output": _parse(row["extractor_output"] or ""),
        }

    def add_directive(
        self,
        state: GameState,
        event: Event | None,
        text: str,
        source: str,
        actor: str = "",
        skill_id: str = "",
        notes: str = "",
        status: str = "draft",
    ) -> int:
        # status: 'draft'=已确认颁诏候选；'pending'=大臣拟旨待皇帝核定。
        cursor = self.conn.execute(
            """
            INSERT INTO turn_directives
            (turn, year, period, event_id, actor, skill_id, text, source, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (state.turn, state.year, state.period, event.id if event else "",
             actor, skill_id, text, source, status, notes),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_directives(
        self, state: GameState, statuses: Tuple[str, ...] = ("draft",)
    ) -> List[sqlite3.Row]:
        # 默认只取 draft（颁诏候选）；UI 列表传 ('pending','draft') 一起取，前端按 status 分区。
        placeholders = ",".join("?" for _ in statuses)
        return self.conn.execute(
            f"""
            SELECT d.*, e.title AS event_title
            FROM turn_directives d
            LEFT JOIN events e ON e.id = d.event_id
            WHERE d.turn = ? AND d.status IN ({placeholders})
            ORDER BY d.id
            """,
            (state.turn, *statuses),
        ).fetchall()

    def confirm_directive(self, directive_id: int) -> None:
        """大臣拟旨经皇帝核定：pending → draft（进入颁诏候选池）。"""
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'draft', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (directive_id,),
        )
        self.conn.commit()

    def reject_directive(self, directive_id: int) -> None:
        """皇帝驳回大臣拟旨：pending → rejected。"""
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'rejected', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (directive_id,),
        )
        self.conn.commit()

    def delete_latest_pending_directive_by_actor(self, actor: str, turn: int) -> int:
        """删该大臣本回合最后一道仍 pending（未准未驳）的拟旨。撤回召对时连带删。
        返回被删的 directive_id；无可删返回 0。已准(draft)/已驳的不动。"""
        row = self.conn.execute(
            "SELECT id FROM turn_directives "
            "WHERE turn = ? AND actor = ? AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (int(turn), actor),
        ).fetchone()
        if row is None:
            return 0
        directive_id = int(row["id"])
        self.conn.execute("DELETE FROM turn_directives WHERE id = ?", (directive_id,))
        self.conn.commit()
        return directive_id

    def count_pending_directives(self, state: GameState) -> int:
        """本回合待核定（pending）的大臣拟旨数。颁诏前须为 0。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM turn_directives WHERE turn = ? AND status = 'pending'",
            (state.turn,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def add_structured_directive(self, state: GameState, directive: Dict[str, object]) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO turn_structured_directives
            (turn, year, period, template_id, category, title, fields_json, compiled_text, settlement_hint, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                state.turn,
                state.year,
                state.period,
                str(directive.get("template_id") or ""),
                str(directive.get("category") or ""),
                str(directive.get("title") or ""),
                json.dumps(directive.get("fields") or {}, ensure_ascii=False, sort_keys=True),
                str(directive.get("compiled_text") or ""),
                str(directive.get("settlement_hint") or ""),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def _structured_directive_payload(self, row: sqlite3.Row) -> Dict[str, object]:
        try:
            fields = json.loads(row["fields_json"] or "{}")
        except Exception:
            fields = {}
        return {
            "id": int(row["id"]),
            "turn": int(row["turn"]),
            "year": int(row["year"]),
            "period": int(row["period"]),
            "template_id": row["template_id"] or "",
            "category": row["category"] or "",
            "title": row["title"] or "",
            "fields": fields if isinstance(fields, dict) else {},
            "compiled_text": row["compiled_text"] or "",
            "settlement_hint": row["settlement_hint"] or "",
            "status": row["status"] or "",
        }

    def list_structured_directives(
        self, state: GameState, statuses: Tuple[str, ...] = ("draft",)
    ) -> List[Dict[str, object]]:
        placeholders = ",".join("?" for _ in statuses)
        rows = self.conn.execute(
            f"""
            SELECT * FROM turn_structured_directives
            WHERE turn = ? AND status IN ({placeholders})
            ORDER BY id
            """,
            (state.turn, *statuses),
        ).fetchall()
        return [self._structured_directive_payload(row) for row in rows]

    def list_structured_directives_by_turn(self, turn: int) -> List[Dict[str, object]]:
        rows = self.conn.execute(
            """
            SELECT * FROM turn_structured_directives
            WHERE turn = ? AND status = 'issued'
            ORDER BY id
            """,
            (int(turn),),
        ).fetchall()
        return [self._structured_directive_payload(row) for row in rows]

    def update_structured_directive(self, directive_id: int, directive: Dict[str, object]) -> None:
        self.conn.execute(
            """
            UPDATE turn_structured_directives
            SET template_id = ?,
                category = ?,
                title = ?,
                fields_json = ?,
                compiled_text = ?,
                settlement_hint = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'draft'
            """,
            (
                str(directive.get("template_id") or ""),
                str(directive.get("category") or ""),
                str(directive.get("title") or ""),
                json.dumps(directive.get("fields") or {}, ensure_ascii=False, sort_keys=True),
                str(directive.get("compiled_text") or ""),
                str(directive.get("settlement_hint") or ""),
                int(directive_id),
            ),
        )
        self.conn.commit()

    def delete_structured_directive(self, directive_id: int) -> None:
        self.conn.execute(
            """
            UPDATE turn_structured_directives
            SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'draft'
            """,
            (int(directive_id),),
        )
        self.conn.commit()

    def update_directive_text(self, directive_id: int, text: str) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET text = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (text, directive_id),
        )
        self.conn.commit()

    def update_directive(
        self,
        directive_id: int,
        event: Event,
        actor: str,
        skill_id: str,
        text: str,
        notes: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET event_id = ?,
                actor = ?,
                skill_id = ?,
                text = ?,
                notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (event.id, actor, skill_id, text, notes, directive_id),
        )
        self.conn.commit()

    def delete_directive(self, directive_id: int) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (directive_id,),
        )
        self.conn.commit()

    def mark_directives_issued(self, state: GameState) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'issued', updated_at = CURRENT_TIMESTAMP
            WHERE turn = ? AND status = 'draft'
            """,
            (state.turn,),
        )
        self.conn.execute(
            """
            UPDATE turn_structured_directives
            SET status = 'issued', updated_at = CURRENT_TIMESTAMP
            WHERE turn = ? AND status = 'draft'
            """,
            (state.turn,),
        )
        self.conn.commit()
