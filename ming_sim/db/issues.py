"""issues / issue_advances / event_triggers / legacies：事项立项推进结案 + 帝国长期修正符。

_IssuesMixin：拆自原 db.py，方法体逐字未改。"""

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


class _IssuesMixin:
    # ----- issues (双类事项 + 双向进度条) -----

    def _derive_issue_phase(self, bar: int) -> str:
        if bar <= 0:
            return "终"
        if bar < 30:
            return "起"
        if bar < 70:
            return "中"
        if bar < 100:
            return "终前"
        return "终"

    def list_active_issues(self, kind: str | None = None) -> List[sqlite3.Row]:
        sql = "SELECT * FROM issues WHERE status = 'active'"
        args: List[object] = []
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY severity DESC, id ASC"
        return self.conn.execute(sql, args).fetchall()

    def list_closed_issues_at(self, closed_turn: int) -> List[sqlite3.Row]:
        """指定 turn 关闭（resolved / failed / dropped）的 issue。"""
        return self.conn.execute(
            "SELECT * FROM issues WHERE closed_turn = ? AND status IN ('resolved','failed','dropped') ORDER BY id",
            (int(closed_turn),),
        ).fetchall()

    def count_active_initiatives(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM issues WHERE kind='initiative' AND status='active'"
        ).fetchone()
        return int(row["n"] or 0)

    def find_active_issue_by_origin(self, origin_kind: str, origin_ref: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM issues WHERE origin_kind=? AND origin_ref=? AND status='active' LIMIT 1",
            (origin_kind, origin_ref),
        ).fetchone()

    def find_any_issue_by_origin(self, origin_kind: str, origin_ref: str) -> sqlite3.Row | None:
        """查任意状态（含 resolved/failed/dropped）的同源 issue，用于 spawn 去重。"""
        return self.conn.execute(
            "SELECT * FROM issues WHERE origin_kind=? AND origin_ref=? LIMIT 1",
            (origin_kind, origin_ref),
        ).fetchone()

    def has_event_triggered(self, event_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM event_triggers WHERE event_id=? LIMIT 1",
            (event_id,),
        ).fetchone()
        return row is not None

    def mark_event_triggered(self, state: GameState, event_id: str, source: str = "simulation") -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO event_triggers (event_id, turn, year, period, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, state.turn, state.year, state.period, source),
        )
        self.commit()

    def insert_issue(
        self,
        state: GameState,
        *,
        kind: str,
        title: str,
        origin_kind: str = "",
        origin_ref: str = "",
        bar_value: int = 40,
        bar_good_meaning: str = "已平",
        bar_bad_meaning: str = "失控",
        inertia: int = 0,
        stage_text: str = "",
        severity: int = 50,
        region_hint: str = "",
        faction_hint: str = "",
        tags: List[str] | None = None,
        ongoing_effects: Dict[str, object] | None = None,
        cancellable: str = "never",
        cancel_cost: Dict[str, object] | None = None,
        effect_on_resolve: Dict[str, object] | None = None,
        effect_on_fail: Dict[str, object] | None = None,
        resolve_condition: str = "",
        fail_condition: str = "",
        is_manual: bool = False,
        duration_turns: int = 0,
        goal: str = "",
        assignee: str = "",
    ) -> int:
        if kind not in ("situation", "initiative"):
            raise ValueError(f"issue kind 非法：{kind}")
        if cancellable not in ("decree", "never", "by_progress"):
            raise ValueError(f"cancellable 非法：{cancellable}")
        bar_value = max(0, min(100, int(bar_value)))
        phase = self._derive_issue_phase(bar_value)
        cur = self.conn.execute(
            """
            INSERT INTO issues (
                kind, title, origin_kind, origin_ref, origin_turn,
                bar_value, bar_good_meaning, bar_bad_meaning, inertia,
                phase, stage_text, status, severity, region_hint, faction_hint,
                tags, ongoing_effects, cancellable, cancel_cost,
                effect_on_resolve, effect_on_fail, resolve_condition, fail_condition,
                assignee, last_advance_turn, is_manual, duration_turns, goal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind, title, origin_kind, origin_ref, state.turn,
                bar_value, bar_good_meaning, bar_bad_meaning, int(inertia),
                phase, stage_text, int(severity), region_hint, faction_hint,
                json.dumps(tags or [], ensure_ascii=False),
                json.dumps(ongoing_effects or {}, ensure_ascii=False),
                cancellable,
                json.dumps(cancel_cost or {}, ensure_ascii=False),
                json.dumps(effect_on_resolve or {}, ensure_ascii=False),
                json.dumps(effect_on_fail or {}, ensure_ascii=False),
                resolve_condition, fail_condition,
                (assignee or "").strip(),
                state.turn,
                1 if is_manual else 0,
                max(0, int(duration_turns or 0)),
                (goal or "").strip(),
            ),
        )
        self.commit()
        return int(cur.lastrowid)

    # ----- 玩家手动管理的 decree 局势（is_manual=1）-----

    def count_active_manual_issues(self) -> int:
        """当前进行中的手动 decree 局势条数（用于上限校验）。"""
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM issues WHERE status='active' AND is_manual=1"
        ).fetchone()[0])

    def count_active_decree_issues(self) -> int:
        """当前进行中的 decree 来源局势条数（手动 + LLM 从诏书抽取）。"""
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM issues WHERE status='active' AND origin_kind='decree'"
        ).fetchone()[0])

    def get_manual_issue(self, issue_id: int) -> sqlite3.Row | None:
        """取一条手动局势（任意状态）；非手动返回 None。"""
        row = self.conn.execute(
            "SELECT * FROM issues WHERE id=? AND is_manual=1", (int(issue_id),)
        ).fetchone()
        return row

    def update_manual_issue(
        self,
        issue_id: int,
        *,
        title: str | None = None,
        duration_turns: int | None = None,
        goal: str | None = None,
        assignee: str | None = None,
    ) -> bool:
        """改手动局势：名称(title) / 持续回合数(duration_turns) / 目标(goal)。
        仅 is_manual=1 且 active 可改。返回是否实际更新。"""
        row = self.conn.execute(
            "SELECT id, status, is_manual FROM issues WHERE id=?", (int(issue_id),)
        ).fetchone()
        if row is None or int(row["is_manual"]) != 1 or row["status"] != "active":
            return False
        sets, params = [], []
        if title is not None and title.strip():
            sets.append("title=?")
            params.append(title.strip()[:60])
        if duration_turns is not None:
            sets.append("duration_turns=?")
            params.append(max(0, int(duration_turns)))
        if goal is not None:
            sets.append("goal=?")
            params.append(goal.strip())
        if assignee is not None:
            sets.append("assignee=?")
            params.append(assignee.strip())
        if not sets:
            return False
        sets.append("updated_at=CURRENT_TIMESTAMP")
        params.append(int(issue_id))
        self.conn.execute(
            f"UPDATE issues SET {', '.join(sets)} WHERE id=?", params
        )
        self.conn.commit()
        return True

    def update_issue_goal(self, issue_id: int, goal: str) -> bool:
        """改任意 active 局势的目标(goal)——皇帝给该局势定的推进意图，对所有 issue 开放（不限手动）。
        仅 active 可改。返回是否实际更新。"""
        row = self.conn.execute(
            "SELECT id, status FROM issues WHERE id=?", (int(issue_id),)
        ).fetchone()
        if row is None or row["status"] != "active":
            return False
        self.conn.execute(
            "UPDATE issues SET goal=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            ((goal or "").strip(), int(issue_id)),
        )
        self.conn.commit()
        return True

    def update_issue_assignee(self, issue_id: int, assignee: str) -> bool:
        """改任意 active 局势的承办人。空字符串表示未指定。"""
        row = self.conn.execute(
            "SELECT id, status FROM issues WHERE id=?", (int(issue_id),)
        ).fetchone()
        if row is None or row["status"] != "active":
            return False
        self.conn.execute(
            "UPDATE issues SET assignee=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            ((assignee or "").strip(), int(issue_id)),
        )
        self.conn.commit()
        return True

    def set_issue_authorization(
        self,
        issue_id: int,
        budget_add: float,
        budget_source: str,
        death_authority: bool,
    ) -> Optional[Dict[str, object]]:
        """给 active 局势的承办人设授权：追加专款（累加进 budget_pool）、定出库、开/关生杀权。
        budget_add 是本次追加额（万两，可为 0 表示只改出库/生杀权）。空 budget_source 不动原出库。
        返回 {budget_pool, budget_source, death_authority}；issue 非 active 返回 None。"""
        row = self.conn.execute(
            "SELECT id, status, budget_pool, budget_source FROM issues WHERE id=?",
            (int(issue_id),),
        ).fetchone()
        if row is None or row["status"] != "active":
            return None
        new_pool = max(0.0, float(row["budget_pool"] or 0) + float(budget_add or 0))
        src = (budget_source or "").strip()
        new_src = src if src in ("国库", "内库") else (str(row["budget_source"] or "") if not src else "")
        self.conn.execute(
            "UPDATE issues SET budget_pool=?, budget_source=?, death_authority=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (round(new_pool, 4), new_src, 1 if death_authority else 0, int(issue_id)),
        )
        self.conn.commit()
        return {
            "budget_pool": round(new_pool, 1),
            "budget_source": new_src,
            "death_authority": bool(death_authority),
        }

    def spend_issue_budget(self, state, issue_id: int, amount: float) -> float:
        """承办人本月从该 issue 专款支取：从其 budget_source 指定库扣 amount（走 economy_ledger），
        库不足按实扣（同军饷「有多少扣多少」），并把实扣额从 budget_pool 减去。
        返回实扣万两（>=0）。无专款/无出库/amount<=0 返回 0。"""
        amount = float(amount or 0)
        if amount <= 0:
            return 0.0
        row = self.conn.execute(
            "SELECT id, status, budget_pool, budget_source FROM issues WHERE id=?",
            (int(issue_id),),
        ).fetchone()
        if row is None or row["status"] != "active":
            return 0.0
        pool = float(row["budget_pool"] or 0)
        account = str(row["budget_source"] or "")
        if pool <= 0 or account not in ("国库", "内库"):
            return 0.0
        # 按库余额 clamp（同军饷：record_issue_economy_move 不 clamp，会把库扣成负，故调用前夹）。
        available = max(0.0, float(state.metrics.get(account, 0)))
        want = min(amount, pool, available)
        if want <= 0:
            return 0.0
        actual = abs(self.record_issue_economy_move(
            state, account, -want, "局势专款支取", f"局势#{int(issue_id)}承办人本月支取",
        ))
        if actual <= 0:
            return 0.0
        new_pool = max(0.0, pool - actual)
        self.conn.execute(
            "UPDATE issues SET budget_pool=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (round(new_pool, 4), int(issue_id)),
        )
        self.commit()
        return actual

    def delete_manual_issue(self, issue_id: int) -> bool:
        """彻底删除一条手动局势（连带其 issue_advances）。仅 is_manual=1 可删。"""
        row = self.conn.execute(
            "SELECT id, is_manual FROM issues WHERE id=?", (int(issue_id),)
        ).fetchone()
        if row is None or int(row["is_manual"]) != 1:
            return False
        self.conn.execute("DELETE FROM issue_advances WHERE issue_id=?", (int(issue_id),))
        self.conn.execute("DELETE FROM issues WHERE id=?", (int(issue_id),))
        self.conn.commit()
        return True

    def expire_due_manual_issues(self, state: GameState) -> List[int]:
        """到期手动局势自动撤销（status='dropped'，无奖励）。返回被撤销的 id 列表。
        本函数在结算链里跑在 state.next_period() 之前——结算「第 N 回合」时 state.turn 仍为 N。
        「持续 N 回合」＝新建回合 origin_turn 起，活满 N 个回合，第 origin_turn+N 回合结算时撤销：
        判定 state.turn - origin_turn >= duration_turns。例：第1回合新建、持续2回合 → 活过第1、2
        回合，第3回合结算（state.turn=3，3-1>=2）到期撤销。"""
        rows = self.conn.execute(
            """
            SELECT id, origin_turn, duration_turns FROM issues
            WHERE status='active' AND is_manual=1 AND duration_turns>0
            """
        ).fetchall()
        expired: List[int] = []
        for r in rows:
            if int(state.turn) - int(r["origin_turn"]) >= int(r["duration_turns"]):
                self.conn.execute(
                    """
                    UPDATE issues SET status='dropped', closed_turn=?,
                        last_advance_turn=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (int(state.turn), int(state.turn), int(r["id"])),
                )
                expired.append(int(r["id"]))
        if expired:
            self.conn.commit()
        return expired

    def advance_issue(
        self,
        state: GameState,
        issue_id: int,
        *,
        trigger_kind: str,
        trigger_ref: str = "",
        delta_bar: int = 0,
        stage_text: str = "",
        narrative: str = "",
        metric_delta: Dict[str, int] | None = None,
        inertia_delta: int = 0,
    ) -> sqlite3.Row | None:
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None
        narrative = re.sub(r"\s+", " ", str(narrative or "").strip())
        if len(narrative) > 80:
            narrative = narrative[:80].rstrip() + "..."
        # 崩坏能力由 effect_on_fail 是否非空判定：有崩坏效果=会崩坏（bar 能到 0、failed 终结）；
        # 空=不会崩坏（天灾/正面机遇等不可控或无失败态局势，bar 下限钳到 1，永不 failed，
        # 只靠 ongoing_effects 每月持续流血）。
        can_collapse = bool(json.loads(row["effect_on_fail"] or "{}"))
        floor = 0 if can_collapse else 1
        # clamp single advance
        delta_bar = max(-50, min(50, int(delta_bar)))
        from_value = int(row["bar_value"])
        to_value = max(floor, min(100, from_value + delta_bar))
        actual_delta = to_value - from_value
        from_stage_text = row["stage_text"]
        to_stage_text = stage_text or from_stage_text
        new_phase = self._derive_issue_phase(to_value)
        new_status = row["status"]
        closed_turn = row["closed_turn"]
        if to_value >= 100:
            new_status = "resolved"
            closed_turn = state.turn
        elif to_value <= 0 and can_collapse:
            new_status = "failed"
            closed_turn = state.turn
        # inertia 可被本次行动改变（钳到 -10..+10 五档区间）
        new_inertia = int(row["inertia"]) + int(inertia_delta)
        new_inertia = max(-10, min(10, new_inertia))
        self.conn.execute(
            """
            UPDATE issues SET bar_value=?, phase=?, stage_text=?, status=?, inertia=?,
                              closed_turn=?, last_advance_turn=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (to_value, new_phase, to_stage_text, new_status, new_inertia, closed_turn, state.turn, issue_id),
        )
        self.conn.execute(
            """
            INSERT INTO issue_advances (
                issue_id, turn, year, period, trigger_kind, trigger_ref,
                delta_bar, from_value, to_value,
                from_stage_text, to_stage_text, narrative, metric_delta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue_id, state.turn, state.year, state.period, trigger_kind, trigger_ref,
                actual_delta, from_value, to_value,
                from_stage_text, to_stage_text, narrative,
                json.dumps(metric_delta or {}, ensure_ascii=False),
            ),
        )
        self.commit()
        return self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    def close_issue(
        self,
        state: GameState,
        issue_id: int,
        *,
        reason: str,
        narrative: str = "",
    ) -> sqlite3.Row | None:
        """LLM 主动通知收尾。reason 必须是 'resolved' 或 'failed'。不看 bar 门槛。"""
        if reason not in ("resolved", "failed"):
            raise ValueError(f"close_issue reason 非法：{reason}")
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None
        narrative = re.sub(r"\s+", " ", str(narrative or "").strip())
        if len(narrative) > 80:
            narrative = narrative[:80].rstrip() + "..."
        # 不可崩坏局势（effect_on_fail 空：天灾/不可控灾害）没有「失败终结」态——LLM 误判 failed
        # 时拒绝结案，留 active 继续靠 ongoing_effects 流血，只能靠 resolved（赈济平息）收尾。
        if reason == "failed" and not json.loads(row["effect_on_fail"] or "{}"):
            print(f"[INFO] close_issue 已拒：issue {issue_id}（{row['title']}）无 effect_on_fail，不可崩坏，保持 active。")
            return None
        from_value = int(row["bar_value"])
        # resolved → 抬到 100；failed → 压到 0；用于 inertia/UI 一眼看懂
        to_value = 100 if reason == "resolved" else 0
        actual_delta = to_value - from_value
        from_stage_text = row["stage_text"]
        to_stage_text = narrative or from_stage_text
        new_phase = self._derive_issue_phase(to_value)
        self.conn.execute(
            """
            UPDATE issues SET bar_value=?, phase=?, stage_text=?, status=?,
                              closed_turn=?, last_advance_turn=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (to_value, new_phase, to_stage_text, reason, state.turn, state.turn, issue_id),
        )
        self.conn.execute(
            """
            INSERT INTO issue_advances (
                issue_id, turn, year, period, trigger_kind, trigger_ref,
                delta_bar, from_value, to_value,
                from_stage_text, to_stage_text, narrative, metric_delta
            ) VALUES (?, ?, ?, ?, 'close', ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                issue_id, state.turn, state.year, state.period, reason,
                actual_delta, from_value, to_value,
                from_stage_text, to_stage_text, narrative,
            ),
        )
        self.conn.commit()
        return self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    def cancel_issue(
        self,
        state: GameState,
        issue_id: int,
        *,
        narrative: str = "",
        applied_cost: Dict[str, object] | None = None,
    ) -> sqlite3.Row | None:
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None
        narrative = re.sub(r"\s+", " ", str(narrative or "").strip())
        if len(narrative) > 80:
            narrative = narrative[:80].rstrip() + "..."
        self.conn.execute(
            "UPDATE issues SET status='dropped', closed_turn=?, last_advance_turn=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (state.turn, state.turn, issue_id),
        )
        self.conn.execute(
            """
            INSERT INTO issue_advances (
                issue_id, turn, year, period, trigger_kind, delta_bar,
                from_value, to_value, narrative, metric_delta
            ) VALUES (?, ?, ?, ?, 'cancel', 0, ?, ?, ?, ?)
            """,
            (
                issue_id, state.turn, state.year, state.period,
                int(row["bar_value"]), int(row["bar_value"]),
                narrative,
                json.dumps(applied_cost or {}, ensure_ascii=False),
            ),
        )
        self.commit()
        return self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    def list_recent_issue_advances(self, issue_id: int, limit: int = 3) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM issue_advances WHERE issue_id=? ORDER BY id DESC LIMIT ?",
            (issue_id, limit),
        ).fetchall()

    def list_issue_advances(self, issue_id: int) -> List[sqlite3.Row]:
        """取某事项全部推进日志，按发生顺序返回，供月末推演回看完整脉络。"""
        return self.conn.execute(
            "SELECT * FROM issue_advances WHERE issue_id=? ORDER BY turn ASC, year ASC, period ASC, id ASC",
            (issue_id,),
        ).fetchall()

    def record_issue_economy_move(
        self,
        state: GameState,
        account: str,
        delta: int,
        category: str,
        reason: str,
        purpose: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
    ) -> int:
        """记一笔经济流水到 economy_ledger，同步更新 metrics[account]。

        purpose/target_kind/target_id 仅对 extractor 抽出的 economy_moves（自由拨款）填，
        flows 月固定支出与所有收入一律 None。受控枚举见 constants.ECONOMY_PURPOSES。

        遗产修正：account 上若有 active 遗产百分比修正符，先按 apply_legacy_pct 放大/缩小 delta
        再落账（base>=0 ×(1+net/100)，base<0 ×(1-net/100)）。修正折进本笔流水，不另立账行。
        category=='局势遗产' 时不再二次修正（避免自乘，且当前已无该类调用）。
        """
        if category != "局势遗产":
            net_pct = int(self.legacy_modifiers(state).get(account, 0) or 0)  # type: ignore[arg-type]
            if net_pct:
                delta = self.apply_legacy_pct(float(delta), net_pct)
        before = float(state.metrics[account])
        after = round(before + float(delta), 4)
        actual = round(after - before, 4)
        if actual == 0:
            return 0
        state.metrics[account] = after
        self.conn.execute(
            """
            INSERT INTO economy_ledger
            (turn, year, period, account, delta, balance_after, category, reason,
             event_id, edict_id, actor, purpose, target_kind, target_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '事项推演', ?, ?, ?)
            """,
            (state.turn, state.year, state.period, account, actual, after,
             category, reason, purpose, target_kind, target_id),
        )
        self.sync_economy_accounts(state)
        self.commit()
        return actual

    # ── 帝国修正（legacies 表）：结案留下的长期百分比修正符，落账层放大/缩小增量 ────
    def insert_legacy(
        self,
        state: GameState,
        *,
        name: str,
        modifiers: Dict[str, object],
        narrative_hint: str = "",
        duration_months: int = 24,
        source_issue_id: int | None = None,
        clear_gate: Dict[str, str] | None = None,
        legacy_key: str = "",
    ) -> int:
        """结案产生持续修正符。start_month=当前绝对月，duration_months=-1 为永久。
        clear_gate 非空时：靠程序按 gating.evaluate_gate 判定消除（见 issues.clear_gated_legacies），与时长无关。"""
        start_month = int(state.year) * 12 + int(state.period)
        cur = self.conn.execute(
            """INSERT INTO legacies
               (name, source_issue_id, modifiers, narrative_hint,
                start_month, duration_months, status, clear_gate, legacy_key)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(name)[:60], source_issue_id,
                json.dumps(modifiers, ensure_ascii=False),
                str(narrative_hint)[:200],
                start_month, int(duration_months),
                json.dumps(clear_gate or {}, ensure_ascii=False),
                str(legacy_key)[:60],
            ),
        )
        self.commit()
        self._legacy_mod_cache = None  # active 集变了，修正符缓存失效
        return int(cur.lastrowid)

    def list_active_legacies(self, state: GameState) -> List[sqlite3.Row]:
        """当前仍生效的帝国修正，顺手把已到期的失活。"""
        self.expire_legacies(state)
        return self.conn.execute(
            "SELECT * FROM legacies WHERE status='active' ORDER BY id"
        ).fetchall()

    def expire_legacies(self, state: GameState) -> List[int]:
        """到期失活：当前月 >= start_month + duration_months（永久 -1 永不到期）。"""
        now = int(state.year) * 12 + int(state.period)
        rows = self.conn.execute(
            "SELECT id, start_month, duration_months FROM legacies WHERE status='active'"
        ).fetchall()
        expired: List[int] = []
        for r in rows:
            dur = int(r["duration_months"])
            if dur < 0:
                continue
            if now >= int(r["start_month"]) + dur:
                expired.append(int(r["id"]))
        if expired:
            self.conn.executemany(
                "UPDATE legacies SET status='expired' WHERE id=?",
                [(i,) for i in expired],
            )
            self.commit()
            self._legacy_mod_cache = None  # active 集变了，修正符缓存失效
        return expired

    def legacy_remaining_months(self, row: sqlite3.Row, state: GameState) -> int:
        """剩余月数；-1=永久。"""
        dur = int(row["duration_months"])
        if dur < 0:
            return -1
        now = int(state.year) * 12 + int(state.period)
        return max(0, int(row["start_month"]) + dur - now)

    def legacy_modifiers(self, state: GameState) -> Dict[str, object]:
        """聚合所有 active 遗产的百分比修正符，同维度累加（A 方案）。返回：
        {
          "国库": net_pct, "内库": net_pct, "民心": net_pct, "皇威": net_pct,
          "regions": {region_id: {field: net_pct, ...}, ...},
          "armies":  {army_id:  {field: net_pct, ...}, ...},
        }
        net_pct 为带符号整数百分比；落账时 base>=0 用 ×(1+net/100)，base<0 用 ×(1-net/100)。
        结果缓存，active 遗产集变化时由 insert_legacy/expire_legacies 清空。
        """
        # expire 可能改变 active 集 → 先跑（其内部会在有变动时清缓存）
        self.expire_legacies(state)
        if self._legacy_mod_cache is not None:
            return self._legacy_mod_cache
        agg: Dict[str, object] = {"国库": 0, "内库": 0, "民心": 0, "皇威": 0, "regions": {}, "armies": {}}
        for lg in self.conn.execute(
            "SELECT modifiers FROM legacies WHERE status='active' ORDER BY id"
        ).fetchall():
            try:
                eff = json.loads(str(lg["modifiers"] or "{}"))
            except Exception:
                continue
            for acc in ("国库", "内库", "民心", "皇威"):
                v = eff.get(acc)
                if isinstance(v, (int, float)):
                    agg[acc] = int(agg[acc]) + int(v)
            for scope in ("regions", "armies"):
                block = eff.get(scope)
                if not isinstance(block, dict):
                    continue
                dst = agg[scope]  # type: ignore[assignment]
                for entity_id, fields in block.items():
                    if not isinstance(fields, dict):
                        continue
                    bucket = dst.setdefault(str(entity_id), {})  # type: ignore[union-attr]
                    for field, pct in fields.items():
                        if isinstance(pct, (int, float)):
                            bucket[str(field)] = int(bucket.get(str(field), 0)) + int(pct)
        self._legacy_mod_cache = agg
        return agg

    @staticmethod
    def apply_legacy_pct(base: float, net_pct: int) -> float:
        """遗产百分比修正：base>=0 → base×(1+net/100)；base<0 → base×(1-net/100)。net=0 原样。"""
        if net_pct == 0 or base == 0:
            return base
        factor = (1 + net_pct / 100.0) if base >= 0 else (1 - net_pct / 100.0)
        return round(base * factor, 4)
