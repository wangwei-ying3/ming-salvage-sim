"""secret_orders：密令立项、进展、催办、到期自动核议、按关键词检索。

_SecretOrdersMixin：拆自原 db.py，方法体逐字未改。"""

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
from ming_sim.llm_config import load_runtime_game
from ming_sim.matching import match_army_id_from_text, match_region_id_from_text
from ming_sim.models import Event, GameState, monthly_amount, period_label
from ming_sim.token_stats import tlog
from ming_sim.db._helpers import (
    normalize_office, infer_office_type_from_office,
    _compact_lookup_text, _normalize_power_id,
    COURT_OFFICE_TYPES, MINISTRY_OFFICE_TYPES,
)


class _SecretOrdersMixin:
    # ----- secret_orders（密令系统）-----

    def _validate_secret_order_assignee(self, minister_name: str) -> None:
        name = (minister_name or "").strip()
        if not name:
            raise ValueError("密令承办人不能为空。")
        row = self.conn.execute(
            "SELECT name, office, office_type, status, status_reason, power_id FROM characters WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"密令承办人「{name}」未入朝廷人物名册，请先召见或登记此人。")
        if (row["power_id"] or "ming") != "ming":
            raise ValueError(f"密令承办人「{name}」不属大明朝廷，不能承办密令。")
        status = row["status"] or "active"
        if status != "active":
            reason = row["status_reason"] or ""
            raise ValueError(f"密令承办人「{name}」当前状态为 {status}，不能承办密令。{reason}".strip())
        office_type = (row["office_type"] or "").strip()
        office = (row["office"] or "").strip()
        if office_type == "后宫":
            raise ValueError(f"密令承办人「{name}」为后宫人物，不能承办朝廷密令。")
        if office_type == "candidate" or not office:
            raise ValueError(f"密令承办人「{name}」尚无可任事官职，不能承办密令。")

    @staticmethod
    def _compact_secret_order_lines(text: object, max_chars: int = 80) -> List[Dict[str, object]]:
        """把 result/sim_note 的按月长文本压成给推演看的短时间线。"""
        out: List[Dict[str, object]] = []
        for idx, raw in enumerate(str(text or "").split("\n"), start=1):
            line = raw.strip()
            if not line:
                continue
            m = re.match(r"^(〔[^〕]+〕)(.*)$", line)
            time_text = m.group(1) if m else ""
            narrative = (m.group(2) if m else line).strip()
            if len(narrative) > max_chars:
                narrative = narrative[:max_chars].rstrip() + "..."
            out.append({"id": idx, "time": time_text, "narrative": narrative})
        return out

    def secret_order_sim_payload(self, order: Dict[str, object]) -> Dict[str, object]:
        """构造注入 simulator 的密令轻量 payload；不影响数据库原文与前端展示。"""
        return {
            "id": int(order["id"]),
            "minister_name": order["minister_name"],
            "title": order["title"],
            "content": str(order.get("content") or "")[:120],
            "status": order["status"],
            "turn_issued": order.get("turn_issued") or 0,
            "due_turn": order.get("due_turn") or 0,
            "progress": self._compact_secret_order_lines(order.get("result") or ""),
            "sim_note": self._compact_secret_order_lines(order.get("sim_note") or ""),
        }

    def create_secret_order(
        self,
        state: GameState,
        minister_name: str,
        title: str,
        content: str,
        tags: List[str],
        importance: int = 4,
        deadline_months: int = 0,
    ) -> int:
        minister_name = (minister_name or "").strip()
        self._validate_secret_order_assignee(minister_name)
        game_settings = load_runtime_game()
        total_limit = max(1, int(game_settings.get("secret_order_total_limit", 5) or 5))
        person_limit = max(1, int(game_settings.get("secret_order_person_limit", 1) or 1))
        active_count = self.conn.execute(
            "SELECT COUNT(*) FROM secret_orders WHERE status='active'"
        ).fetchone()[0]
        if active_count >= total_limit:
            raise ValueError(f"进行中密令已达上限（{total_limit}条），请先结案或撤销部分密令再下新令。当前：{active_count} 条。")
        # 单个承办人同时进行中的密令数量受游戏设置约束。
        assignee_active_count = self.conn.execute(
            "SELECT COUNT(*) FROM secret_orders WHERE status='active' AND minister_name = ?",
            (minister_name,),
        ).fetchone()[0]
        if assignee_active_count >= person_limit:
            assignee_active = self.conn.execute(
                "SELECT id, title FROM secret_orders WHERE status='active' AND minister_name = ? ORDER BY id ASC LIMIT 1",
                (minister_name,),
            ).fetchone()
            existing = (
                f"〔#{int(assignee_active['id'])} {assignee_active['title']}〕"
                if assignee_active is not None else ""
            )
            raise ValueError(
                f"{minister_name}名下已有进行中密令 {assignee_active_count} 条{existing}，"
                f"单个承办人同时进行中的密令上限为 {person_limit} 条，请先结案/撤销部分密令再下新令。"
            )
        tags_json = json.dumps(tags, ensure_ascii=False)
        deadline = max(0, min(int(deadline_months or 0), 36))
        due_turn = int(state.turn) + deadline if deadline else 0
        cur = self.conn.execute(
            """
            INSERT INTO secret_orders
                (turn_issued, due_turn, year_issued, period_issued, minister_name, title, content, tags, importance, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (state.turn, due_turn, state.year, state.period, minister_name, title[:20], content, tags_json, importance),
        )
        self.conn.commit()
        tlog(f"[secret_order] create id={cur.lastrowid} minister={minister_name} title={title[:20]}")
        return cur.lastrowid  # type: ignore[return-value]

    def list_secret_orders(
        self,
        status: Optional[str] = None,
        minister_name: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if minister_name:
            clauses.append("minister_name = ?")
            params.append(minister_name)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM secret_orders {where} ORDER BY id DESC",
            params,
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "turn_issued": int(r["turn_issued"]),
                "due_turn": int(r["due_turn"] if "due_turn" in r.keys() else 0),
                "year_issued": int(r["year_issued"]),
                "period_issued": int(r["period_issued"]),
                "minister_name": r["minister_name"],
                "title": r["title"],
                "content": r["content"],
                "tags": json.loads(r["tags"] or "[]"),
                "importance": int(r["importance"]),
                "status": r["status"],
                "result": r["result"] or "",
                "sim_note": (r["sim_note"] if "sim_note" in r.keys() else "") or "",
                "turn_closed": r["turn_closed"],
            }
            for r in rows
        ]

    def get_active_secret_orders_for_minister(self, minister_name: str) -> List[Dict[str, object]]:
        """返回该大臣名下未结案密令（active + pending_review）。done/failed 已结案不再返回。"""
        active = self.list_secret_orders(status="active", minister_name=minister_name)
        pending = self.list_secret_orders(status="pending_review", minister_name=minister_name)
        return active + pending

    def close_secret_order(self, order_id: int, status: str, result: str, turn_closed: int) -> None:
        self.conn.execute(
            """
            UPDATE secret_orders
            SET status = ?, result = ?, turn_closed = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, result, turn_closed, int(order_id)),
        )
        self.commit()
        tlog(f"[secret_order] close id={order_id} status={status}")

    def delete_secret_order(self, order_id: int) -> bool:
        """彻底删除一条密令记录（用于清掉重复/误下的密令）。删成功返回 True，不存在返回 False。"""
        row = self.conn.execute(
            "SELECT id FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if row is None:
            return False
        self.conn.execute("DELETE FROM secret_orders WHERE id = ?", (int(order_id),))
        self.conn.commit()
        tlog(f"[secret_order] delete id={order_id}")
        return True

    def submit_secret_order_for_review(self, order_id: int, claim: str, year: int, period: int) -> bool:
        """大臣提交密令待推演核议：active → pending_review。
        claim 按月戳追加进 result 时间线（与 progress 同列，但带 "[提交核议]" 标记），
        让推演看时同时知道大臣自述。仅 active 状态可提交。"""
        row = self.conn.execute(
            "SELECT status FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if not row or row["status"] != "active":
            return False
        stamp = f"〔{period_label(year, period)}〕[提交核议] "
        note = (claim or "").strip()
        prev = self.conn.execute(
            "SELECT result FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()["result"] or ""
        lines = [ln for ln in prev.split("\n") if ln.strip()]
        lines.append(f"{stamp}{note[:300]}")
        self.conn.execute(
            """
            UPDATE secret_orders
            SET status = 'pending_review', result = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            ("\n".join(lines), int(order_id)),
        )
        self.conn.commit()
        tlog(f"[secret_order] submit_for_review id={order_id} claim={note[:60]!r}")
        return True

    def _has_secret_order_period_line(self, order_id: int, column: str, year: int, period: int) -> bool:
        """本年月该列是否已有一行（用于一回合一步闸门）。"""
        stamp = f"〔{period_label(year, period)}〕"
        row = self.conn.execute(
            f"SELECT {column} AS v FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if row is None:
            return False
        return any(ln.startswith(stamp) for ln in str(row["v"] or "").split("\n"))

    def _append_secret_order_line(
        self, order_id: int, column: str, note: str, year: int, period: int,
        reject_if_same_period: bool = False,
    ) -> bool:
        """把一条带年月戳的进展/副作用追加进密令的 result/sim_note，存成历史时间线。
        reject_if_same_period=True 时，本年月已有行则拒写（返回 False，用于一回合一步）；
        否则同年月再写替换当月行。不同年月一律新增。返回是否实际写入。"""
        assert column in ("result", "sim_note")
        stamp = f"〔{period_label(year, period)}〕"
        row = self.conn.execute(
            f"SELECT {column} AS v FROM secret_orders WHERE id = ? AND status = 'active'",
            (int(order_id),),
        ).fetchone()
        if row is None:
            return False  # 已结案或不存在，不追加
        lines = [ln for ln in str(row["v"] or "").split("\n") if ln.strip()]
        if reject_if_same_period and any(ln.startswith(stamp) for ln in lines):
            return False  # 本回合已推过一步，拒
        lines = [ln for ln in lines if not ln.startswith(stamp)]  # 去掉当月旧行
        lines.append(f"{stamp}{note.strip()}")
        # 按〔年月〕戳排序，保证时间线顺序（同月替换后不致错位）
        def _stamp_key(ln: str):
            import re as _re
            m = _re.match(r"〔(\d+)年(\d+)月〕", ln)
            return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        lines.sort(key=_stamp_key)
        self.conn.execute(
            f"UPDATE secret_orders SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("\n".join(lines), int(order_id)),
        )
        self.commit()
        return True

    def update_secret_order_progress(
        self, order_id: int, progress_note: str, year: int = 0, period: int = 0
    ) -> bool:
        """承办人推进一步：按年月追加进 result 历史时间线，不改 status。
        同月再报则替换当月行（修改最新进度，不叠加多条）。"""
        ok = self._append_secret_order_line(
            order_id, "result", progress_note, year, period, reject_if_same_period=False
        )
        tlog(f"[secret_order] progress id={order_id} ok={ok} note={progress_note[:40]!r}")
        return ok

    def update_secret_order_sim_note(
        self, order_id: int, sim_note: str, year: int = 0, period: int = 0
    ) -> None:
        """推演写密令进度（含风险/风声），按年月追加进 sim_note 历史时间线，
        不动 result/status。同月再写替换（推演每月一次）。与承办人进展分列。"""
        self._append_secret_order_line(order_id, "sim_note", sim_note, year, period)
        tlog(f"[secret_order] sim_note id={order_id} note={sim_note[:40]!r}")

    def rush_secret_order(
        self,
        order_id: int,
        state: GameState,
        deadline_months: int = 1,
        reason: str = "",
    ) -> Dict[str, object]:
        """缩短 active 密令期限。deadline_months<=0 表示本月立即送核议。"""
        row = self.conn.execute(
            "SELECT id, title, status, result, due_turn FROM secret_orders WHERE id = ?",
            (int(order_id),),
        ).fetchone()
        if row is None:
            raise ValueError("密令不存在")
        if row["status"] != "active":
            raise ValueError(f"当前状态 {row['status']}，不能催办")
        try:
            months = max(0, min(int(deadline_months or 0), 36))
        except (TypeError, ValueError):
            months = 1
        target_turn = int(state.turn) + months
        old_due = int(row["due_turn"] or 0)
        stamp = f"〔{period_label(state.year, state.period)}〕"
        why = (reason or "").strip()[:120] or "奉旨加急"
        prev = row["result"] or ""
        lines = [ln for ln in prev.split("\n") if ln.strip()]
        if months <= 0:
            lines.append(f"{stamp}[奉旨即核] {why}；本月即移交密旨核议。")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET status = 'pending_review', due_turn = ?, result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(state.turn), "\n".join(lines), int(order_id)),
            )
            status = "pending_review"
            due_turn = int(state.turn)
        else:
            due_turn = target_turn if old_due <= 0 else min(old_due, target_turn)
            lines.append(f"{stamp}[奉旨加急] {why}；御限改为 {months} 个月内核议。")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET due_turn = ?, result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (due_turn, "\n".join(lines), int(order_id)),
            )
            status = "active"
        self.conn.commit()
        tlog(f"[secret_order] rush id={order_id} old_due={old_due} due={due_turn} status={status}")
        return {"id": int(order_id), "title": row["title"], "status": status, "due_turn": due_turn}

    def get_secret_order(self, order_id: int) -> Optional[Dict[str, object]]:
        """单查一条密令（任意状态），给承办人查进度工具用。不存在返回 None。"""
        r = self.conn.execute(
            "SELECT * FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if not r:
            return None
        return {
            "id": int(r["id"]), "minister_name": r["minister_name"],
            "title": r["title"], "content": r["content"],
            "status": r["status"], "result": r["result"] or "",
            "sim_note": (r["sim_note"] if "sim_note" in r.keys() else "") or "",
            "turn_issued": int(r["turn_issued"]),
            "due_turn": int(r["due_turn"] if "due_turn" in r.keys() else 0),
            "turn_closed": r["turn_closed"],
        }

    def auto_submit_due_secret_orders(self, state: GameState) -> List[Dict[str, object]]:
        """把到期 active 密令自动转入 pending_review，保证当月推演必须给终判。"""
        rows = self.conn.execute(
            """
            SELECT id, title, result FROM secret_orders
            WHERE status = 'active' AND due_turn > 0 AND due_turn <= ?
            ORDER BY id
            """,
            (int(state.turn),),
        ).fetchall()
        submitted: List[Dict[str, object]] = []
        for row in rows:
            stamp = f"〔{period_label(state.year, state.period)}〕[期限届满] "
            note = "御限已至，移交月末密旨核议；据既有查办、风声与盘面定成败。"
            prev = row["result"] or ""
            lines = [ln for ln in prev.split("\n") if ln.strip()]
            if not any("[期限届满]" in ln for ln in lines):
                lines.append(f"{stamp}{note}")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET status = 'pending_review', result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("\n".join(lines), int(row["id"])),
            )
            submitted.append({"id": int(row["id"]), "title": row["title"]})
        if rows:
            self.conn.commit()
            tlog(f"[secret_order] auto_submit_due count={len(submitted)} ids={[x['id'] for x in submitted]}")
        return submitted

    def get_secret_orders_by_keywords(
        self, keywords: List[str], limit: int = 5, current_turn: int = 0
    ) -> List[Dict[str, object]]:
        """检索进行中（active）密令，tags LIKE 匹配，供推演 secret_orders 字段注入。
        完结/失败密令靠 event_memory（chat_message 来源）进入 relevant_memories，不在此返回。"""
        if not keywords:
            return self.list_secret_orders(status="active")[:limit]
        like_clauses = " OR ".join(["tags LIKE ?" for _ in keywords])
        like_params = [f"%{k}%" for k in keywords]
        rows = self.conn.execute(
            f"""
            SELECT * FROM secret_orders
            WHERE status = 'active' AND ({like_clauses})
            ORDER BY importance DESC, id DESC
            LIMIT ?
            """,
            like_params + [limit],
        ).fetchall()
        if not rows:
            return self.list_secret_orders(status="active")[:limit]
        return [
            {
                "id": int(r["id"]),
                "turn_issued": int(r["turn_issued"]),
                "year_issued": int(r["year_issued"]),
                "period_issued": int(r["period_issued"]),
                "minister_name": r["minister_name"],
                "title": r["title"],
                "content": r["content"],
                "tags": json.loads(r["tags"] or "[]") if isinstance(r["tags"], str) else (r["tags"] or []),
                "importance": int(r["importance"]),
                "status": r["status"],
                "result": r["result"] or "",
            }
            for r in rows
        ]
