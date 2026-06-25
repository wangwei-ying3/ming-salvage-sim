"""财政：fiscal_config 预算目录 + economy_accounts/ledger 账目，预算/收支/账本摘要。

_FiscalMixin：拆自原 db.py，方法体逐字未改。"""

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


class _FiscalMixin:
    # dynamic 税科目 → regions.fiscal 子字段映射。dynamic 税实收走 calc_province_fiscal
    # 读 region.fiscal（不读 fiscal_config 的 base），调额必须改各省 fiscal 字段才真生效。
    #   田赋＝regions.tax_per_turn（官民田×田赋亩率的月额），裁撤走 scale_tian_fu 缩放；
    #   皇庄收入＝各省 huang_tian×租率（flows 实算），调额改 huang_tian 或租率，不在本表。
    _DYNAMIC_REGION_FIELD = {
        "辽饷": "liao_xiang_li", "盐税": "salt_tax", "商税": "commerce_tax",
    }

    def init_fiscal_config(self) -> None:
        """从 content/fiscal_config.json（self.content.fiscal_items）seed 财政科目目录。

        base/rate 单位为【月度】万两/%。科目目录与元数据全走 JSON 设定（铁律：设定走 JSON）；
        加新税源只改 JSON 加两行（base+rate）并升 schema_version，零 Python。

        ── 版本迁移策略（铁律：fiscal_config 只在建库时整体 seed 一次）──
        每个库带 `__schema_version`。本函数按它与 JSON schema_version 比对，分三种走法：

        - `cur == 0`（全新库，无版本行）：整体 seed JSON 全表 → 版本号置 JSON 版。仅此一次。
        - `cur < json`（老档升版）：逐版跑 `_FISCAL_MIGRATIONS[cur+1 .. json]` 的差量动作，
          每版只动那版真正变的 key，**未声明的 key 一律不碰**（玩家削减/裁撤全保留），
          跑完把版本号推到该版。默认迁移＝只 INSERT 老档缺的 key（不覆盖既有值、不复活已删项）。
        - `cur >= json`：**啥都不做**。已是最新，玩家状态神圣。

        ⇒ 玩家裁撤的科目读档后保持删除（不再被旧 INSERT OR IGNORE 复活）。
           JSON 加新税种【必须】同步升 schema_version，否则老档拿不到（CLAUDE.md 已要求）。
        """
        items = list(self.content.fiscal_items)
        if not items or "__schema_version" not in items[0]:
            raise SystemExit("init_fiscal_config: fiscal_items 缺 __schema_version 头，中止。")
        schema_version = int(items[0]["__schema_version"])
        rows = items[1:]

        def _meta(rec: Dict[str, object]) -> tuple:
            return (
                str(rec["key"]), int(rec["value"]), str(rec["kind"]), str(rec["note"]),
                str(rec.get("budget_role", "fixed")),
                str(rec.get("account", "")), str(rec.get("direction", "")),
                str(rec.get("display", "")), int(rec.get("order", 9999)),
                str(rec.get("formula", "")), str(rec.get("basis", "")), str(rec.get("rate_unit", "")),
            )

        cols = (
            "(key, value, kind, note, budget_role, account, direction, display, "
            "sort_order, formula, basis, rate_unit)"
        )

        def _seed_missing() -> None:
            """老档升版的默认迁移：只补 JSON 有、库里没有的 key（不覆盖既有值、不复活已删项）。"""
            self.conn.executemany(
                f"INSERT OR IGNORE INTO fiscal_config {cols} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_meta(rec) for rec in rows],
            )

        json_by_key = {str(rec["key"]): rec for rec in rows}

        def _refresh_fiscal_metadata() -> None:
            """补齐 fiscal_config 的预算目录元数据；不改玩家调过的 value。"""
            self.conn.executemany(
                "UPDATE fiscal_config SET note = ?, budget_role = ?, account = ?, "
                "direction = ?, display = ?, sort_order = ?, formula = ?, basis = ?, "
                "rate_unit = ? WHERE key = ?",
                [
                    (
                        str(rec["note"]),
                        str(rec.get("budget_role", "fixed")),
                        str(rec.get("account", "")),
                        str(rec.get("direction", "")),
                        str(rec.get("display", "")),
                        int(rec.get("order", 9999)),
                        str(rec.get("formula", "")),
                        str(rec.get("basis", "")),
                        str(rec.get("rate_unit", "")),
                        str(rec["key"]),
                    )
                    for rec in rows
                ],
            )

        def _migrate_fiscal_v6_monthly() -> None:
            """v5 及以前 fiscal_config 的 *_base 是季度额；v6 起统一改为月额。"""
            for rec in rows:
                key = str(rec["key"])
                if str(rec.get("kind")) != "base":
                    continue
                row = self.conn.execute(
                    "SELECT value FROM fiscal_config WHERE key = ?", (key,)
                ).fetchone()
                if row is None:
                    continue
                old_value = int(row["value"])
                new_value = max(0, (old_value + 1) // 3)
                self.conn.execute(
                    "UPDATE fiscal_config SET value = ? WHERE key = ?",
                    (new_value, key),
                )
            _seed_missing()
            _refresh_fiscal_metadata()

        def _migrate_fiscal_v7_defaults() -> None:
            """v7 月额校准：仅把仍等于 v6 折月默认值的科目推进到当前默认。"""
            v6_defaults = {
                "辽饷_base": 44,
                "盐税_base": 24,
                "商税_base": 8,
                "皇庄_base": 20,
                "矿税_base": 4,
                "织造_base": 12,
                "宗室禄米_base": 120,
                "官俸_base": 25,
                "工程_base": 5,
                "赈灾_base": 5,
                "宫廷_base": 8,
                "内廷俸_base": 5,
                "妃嫔_base": 4,
            }
            _seed_missing()
            for key, old_default in v6_defaults.items():
                rec = json_by_key.get(key)
                if rec is None:
                    continue
                row = self.conn.execute(
                    "SELECT value FROM fiscal_config WHERE key = ?", (key,)
                ).fetchone()
                if row is not None and int(row["value"]) == old_default:
                    self.conn.execute(
                        "UPDATE fiscal_config SET value = ? WHERE key = ?",
                        (int(rec["value"]), key),
                    )
            _refresh_fiscal_metadata()

        def _migrate_fiscal_v12_remove_obsolete_dynamic() -> None:
            """v12：田赋/辽饷/盐税/商税改由省级 fiscal 字段表达，删除旧目录残留。"""
            obsolete_keys = (
                "田赋_rate",
                "辽饷_base", "辽饷_rate",
                "盐税_base", "盐税_rate",
                "商税_base", "商税_rate",
                "皇庄_base",
            )
            self.conn.execute(
                f"DELETE FROM fiscal_config WHERE key IN ({','.join('?' for _ in obsolete_keys)})",
                obsolete_keys,
            )
            _seed_missing()
            _refresh_fiscal_metadata()

        def _migrate_fiscal_v13_remove_disaster_population_penalty() -> None:
            """v13：natural_disaster/human_disaster 只作叙事字段，不再自动扣年度人口。"""
            self.conn.execute(
                "DELETE FROM fiscal_config WHERE key = ?",
                ("人口灾荒减损_base",),
            )
            _seed_missing()
            _refresh_fiscal_metadata()

        # 每版迁移：从 N-1 → N，只动那版真正变的东西。键＝目标版本号 N。
        # 不在表里的版本步走默认 _seed_missing（只补缺 key）。将来要改某 key 默认 / 删某 key /
        # 加新 key，就在这里登记一条 lambda，只动那一项，别动其它——这样玩家改过的全保住。
        _FISCAL_MIGRATIONS: "Dict[int, Any]" = {
            6: _migrate_fiscal_v6_monthly,
            7: _migrate_fiscal_v7_defaults,
            # v11：新增人口增长率系统的 6 个 _base 科目，默认迁移只补缺 key 即可。
            11: _seed_missing,
            # v12：裁掉已转入 regions.fiscal/公式实算的旧 dynamic 目录键。
            12: _migrate_fiscal_v12_remove_obsolete_dynamic,
            # v13：移除由天灾/人祸文本非空触发的人口硬扣。
            13: _migrate_fiscal_v13_remove_disaster_population_penalty,
            # 8: lambda: self._add_fiscal_key("关税_base", ...),   # 例：将来加新税
        }

        cur_ver_row = self.conn.execute(
            "SELECT value FROM fiscal_config WHERE key = '__schema_version'"
        ).fetchone()
        cur_ver = int(cur_ver_row["value"]) if cur_ver_row else 0

        if cur_ver >= schema_version:
            return  # 已最新，玩家状态神圣，碰都不碰

        if cur_ver == 0:
            existing_count = self.conn.execute("SELECT COUNT(*) FROM fiscal_config").fetchone()[0]
            if existing_count:
                # 更早的旧档没有 __schema_version，但已有季度额 key；当作 v5 逐版迁移。
                for v in range(6, schema_version + 1):
                    (_FISCAL_MIGRATIONS.get(v) or _seed_missing)()
            else:
                # 全新库：整体 seed 一次。
                self.conn.executemany(
                    f"INSERT INTO fiscal_config {cols} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [_meta(rec) for rec in rows],
                )
        else:
            # 老档升版：逐版跑差量；未登记的版本步只补缺 key。
            for v in range(cur_ver + 1, schema_version + 1):
                (_FISCAL_MIGRATIONS.get(v) or _seed_missing)()

        self.conn.execute(
            "INSERT INTO fiscal_config (key, value, kind, note) VALUES "
            "('__schema_version', ?, 'meta', '财政默认值大版本号；老档升版逐版迁移，只动差量') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (schema_version,),
        )
        self.conn.commit()

    def iter_budget_items(self) -> "List[Dict[str, object]]":
        """返回 budget_role=fixed 的 base 科目（含 account/direction/display/sort_order）。

        flows.compute_budget_lines 据此动态生成固定收支预算行——加新税源不必改代码。
        每项配套的 *_rate 由调用方按 stem 自取（rate 项 budget_role 同 fixed 但 kind=rate，
        不在本列表里）。dynamic 项（田赋/辽饷/盐税/商税/皇庄）走省级公式，这里不返回。
        """
        rows = self.conn.execute(
            "SELECT key, account, direction, display, note, sort_order, formula, basis, rate_unit FROM fiscal_config "
            "WHERE budget_role = 'fixed' AND kind = 'base' AND key LIKE '%\\_base' ESCAPE '\\' "
            "ORDER BY sort_order, key"
        ).fetchall()
        return [
            {
                "key": str(r["key"]),
                "account": str(r["account"]),
                "direction": str(r["direction"]),
                "display": str(r["display"]),
                "note": str(r["note"] or ""),
                "formula": str(r["formula"] or ""),
                "basis": str(r["basis"] or ""),
                "rate_unit": str(r["rate_unit"] or ""),
            }
            for r in rows
        ]

    def get_fiscal_config(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT key, value FROM fiscal_config WHERE key NOT LIKE '\\_\\_%' ESCAPE '\\'"
        ).fetchall()
        return {str(r["key"]): int(r["value"]) for r in rows}

    def set_fiscal_config(self, key: str, value: int) -> None:
        self.conn.execute(
            "UPDATE fiscal_config SET value = ? WHERE key = ?", (value, key)
        )
        self.commit()

    def create_fiscal_item(
        self,
        key: str,
        account: str,
        direction: str,
        display: str,
        init_value: int,
        note: str = "",
        formula: str = "",
        basis: str = "",
        rate_unit: str = "",
    ) -> Optional[str]:
        """LLM 推演中凭空新立一个月固定收支项（budget_role=fixed）。

        落 base+rate 两行：`<stem>_base`=init_value、`<stem>_rate`=100。
        既存 base key 直接返回 None（不覆盖，由 fiscal_changes 调增量）。
        返回新建的 base key；冲突或非法返回 None。元数据走 fixed 预算目录，
        flows.iter_budget_items 下{月}起自动遍历落账——零代码加新税种／新月俸。
        """
        stem = key[:-5] if key.endswith("_base") else key
        if not stem:
            return None
        base_key = f"{stem}_base"
        rate_key = f"{stem}_rate"
        exists = self.conn.execute(
            "SELECT 1 FROM fiscal_config WHERE key = ?", (base_key,)
        ).fetchone()
        if exists is not None:
            return None
        sort_order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM fiscal_config"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO fiscal_config "
            "(key, value, kind, budget_role, account, direction, display, sort_order, note, formula, basis, rate_unit) "
            "VALUES (?, ?, 'base', 'fixed', ?, ?, ?, ?, ?, ?, ?, ?)",
            (base_key, max(0, init_value), account, direction, display, sort_order, note, formula, basis, rate_unit),
        )
        self.conn.execute(
            "INSERT INTO fiscal_config "
            "(key, value, kind, budget_role, account, direction, display, sort_order, note, formula, basis, rate_unit) "
            "VALUES (?, 100, 'rate', 'fixed', ?, ?, ?, ?, ?, ?, ?, ?)",
            (rate_key, account, direction, display, sort_order, f"{display}实收率%", "", "", ""),
        )
        self.commit()
        return base_key

    def _stem_of(self, key: str) -> str:
        if key.endswith("_base") or key.endswith("_rate"):
            return key[:-5]
        return key

    def apply_dynamic_fiscal_scale(self, stem: str, ratio: float, region_id: str = "") -> int:
        """按 ratio 缩放 regions.fiscal 中该 dynamic 税字段（辽饷亩率/盐税月额/商税月额）。

        ratio=0 即彻底罢废（字段归零）；0<ratio<1 即按比例削减。田赋走 scale_tian_fu。
        region_id 为空＝全国所有省；填省 id＝仅该省定向调。
        返回被改动的省数。皇庄不在此（走 fiscal_config）。命中映射外的 stem 返回 0。
        """
        field = self._DYNAMIC_REGION_FIELD.get(stem)
        if field is None:
            return 0
        if region_id:
            rows = self.conn.execute("SELECT id, fiscal FROM regions WHERE id = ?", (region_id,)).fetchall()
        else:
            rows = self.conn.execute("SELECT id, fiscal FROM regions").fetchall()
        touched = 0
        for row in rows:
            fiscal: dict = json.loads(str(row["fiscal"] or "{}"))
            old = int(fiscal.get(field, 0) or 0)
            if old <= 0:
                continue
            new = max(0, round(old * ratio))
            if new == old:
                continue
            fiscal[field] = new
            self.conn.execute(
                "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(fiscal, ensure_ascii=False), str(row["id"])),
            )
            touched += 1
        if touched:
            self.commit()
        return touched

    # 可按「绝对万两增量」全国摊派的省级税字段（额是万两/月，能直接加减）。
    # 辽饷/田赋是「亩率·毫」不在此（绝对万两加它没意义，调它们走 scale_*）。
    _ABS_DELTA_REGION_FIELD = {"盐税": "salt_tax", "商税": "commerce_tax"}

    def apply_dynamic_fiscal_delta(self, stem: str, total_delta: int, region_id: str = "") -> int:
        """把「全国/某省 商税或盐税 月额 +X/-X 万两」落到 regions.fiscal。

        - region_id 填省 id：该省字段直接 += total_delta（下限 0）。
        - region_id 为空＝全国：把 total_delta 按**各省现有占比**摊派到每省（占比大的多摊），
          末位省吃掉四舍五入余数，保证摊派总和恰为 total_delta；全省皆 0 时按省数均摊。
        玩家「加征商税十万」不分省即走全国分支——数据仍逐省落库（实收只认 regions.fiscal），
        却不必让玩家/LLM 指定省份。返回被改动的省数。仅 商税/盐税 适用；其余 stem 返回 0。
        """
        field = self._ABS_DELTA_REGION_FIELD.get(stem)
        if field is None or total_delta == 0:
            return 0
        if region_id:
            row = self.conn.execute("SELECT fiscal FROM regions WHERE id = ?", (region_id,)).fetchone()
            if row is None:
                return 0
            fiscal: dict = json.loads(str(row["fiscal"] or "{}"))
            old = int(fiscal.get(field, 0) or 0)
            new = max(0, old + int(total_delta))
            if new == old:
                return 0
            fiscal[field] = new
            self.conn.execute(
                "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(fiscal, ensure_ascii=False), region_id),
            )
            self.commit()
            return 1
        # 全国按占比摊派
        rows = self.conn.execute("SELECT id, fiscal FROM regions").fetchall()
        provinces = []
        for r in rows:
            f = json.loads(str(r["fiscal"] or "{}"))
            provinces.append((str(r["id"]), f, int(f.get(field, 0) or 0)))
        base_total = sum(v for _, _, v in provinces)
        n = len(provinces)
        if n == 0:
            return 0
        # 分配整数增量：按占比取整，余数累加到末位，确保 sum 恰为 total_delta
        allocs: list[int] = []
        running = 0
        for i, (_rid, _f, cur) in enumerate(provinces):
            if i == n - 1:
                alloc = int(total_delta) - running  # 末位吃余数
            elif base_total > 0:
                alloc = round(int(total_delta) * cur / base_total)
            else:
                alloc = round(int(total_delta) / n)  # 全 0 时均摊
            running += alloc
            allocs.append(alloc)
        touched = 0
        for (rid, f, cur), alloc in zip(provinces, allocs):
            if alloc == 0:
                continue
            new = max(0, cur + alloc)
            if new == cur:
                continue
            f[field] = new
            self.conn.execute(
                "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(f, ensure_ascii=False), rid),
            )
            touched += 1
        if touched:
            self.commit()
        return touched

    def scale_tian_fu(self, ratio: float, region_id: str = "") -> int:
        """田赋＝官民田×田赋亩率(fiscal_config 全局 或 省 fiscal.tian_fu_li 覆盖)。按 ratio 缩放亩率。
        region_id 为空＝全国：每省写 tian_fu_li = 全局亩率×ratio（一刀切覆盖，抹平省差异）。
        region_id 填省 id＝单省定向：按该省**现有**亩率×ratio 缩放（保住江南重赋/边瘠轻赋的基差）。
        ratio=0 即罢田赋（辽饷/盐/商各走自己字段不受影响）。返回被改动的省数。"""
        cfg = self.get_fiscal_config()
        global_li = int(cfg.get("田赋亩率_base", 250))
        if region_id:
            rows = self.conn.execute("SELECT id, fiscal FROM regions WHERE id = ?", (region_id,)).fetchall()
        else:
            rows = self.conn.execute("SELECT id, fiscal FROM regions").fetchall()
        touched = 0
        for row in rows:
            fiscal: dict = json.loads(str(row["fiscal"] or "{}"))
            cur_li = int(fiscal.get("tian_fu_li", global_li))
            # 全国：统一覆盖为全局×ratio；单省：按该省现值×ratio（保住省差异）。
            new_li = max(0, round((cur_li if region_id else global_li) * ratio))
            if cur_li == new_li:
                continue
            fiscal["tian_fu_li"] = new_li
            self.conn.execute(
                "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(fiscal, ensure_ascii=False), str(row["id"])),
            )
            touched += 1
        if touched:
            self.commit()
        return touched

    def remove_fiscal_item(self, key: str) -> Optional[str]:
        """彻底裁撤一个月固定收支项（罢税/裁俸）：删 base+rate 两行。

        只对 fiscal_config 中仍存在的月固定收支项生效；删目录条目即停止逐月落账。
        v12 后田赋/辽饷/盐税/商税不再是 fiscal_config 科目，停征应改 regions.fiscal
        的 tian_fu_li / liao_xiang_li / salt_tax / commerce_tax。
        删不存在的项返回 None。返回被删的 base key（按 stem 归一）。
        """
        stem = self._stem_of(key)
        if not stem:
            return None
        base_key = f"{stem}_base"
        rate_key = f"{stem}_rate"
        # 存在性查 base 或 rate 任一；v12 后田赋/三税通常已不在 fiscal_config。
        exists = self.conn.execute(
            "SELECT 1 FROM fiscal_config WHERE key IN (?, ?)", (base_key, rate_key)
        ).fetchone()
        if exists is None:
            return None
        self.conn.execute(
            "DELETE FROM fiscal_config WHERE key IN (?, ?)", (base_key, rate_key)
        )
        # 老档兼容：若 v12 前的 dynamic 税残留仍被删到，同步归零省级字段。
        if stem in self._DYNAMIC_REGION_FIELD:
            self.apply_dynamic_fiscal_scale(stem, 0.0)
        elif stem == "田赋":
            self.scale_tian_fu(0.0)
        self.commit()
        return base_key

    def record_economy_moves(
        self,
        state: GameState,
        event: Event,
        edict_id: int,
        actor: str,
        moves: List[Dict[str, object]],
    ) -> None:
        if not moves:
            self.sync_economy_accounts(state)
            self.conn.commit()
            return
        for move in moves:
            self.conn.execute(
                """
                INSERT INTO economy_ledger
                (turn, year, period, account, delta, balance_after, category, reason, event_id, edict_id, actor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.turn,
                    state.year,
                    state.period,
                    str(move["account"]),
                    float(move["delta"]),
                    float(move["balance_after"]),
                    str(move["category"]),
                    str(move["reason"]),
                    event.id,
                    edict_id,
                    actor,
                ),
            )
        self.sync_economy_accounts(state)
        self.conn.commit()

    def treasury_budget_summary(self, state: "GameState | None" = None) -> str:
        # 三套口径统一：直接调 flows.compute_budget_lines（唯一定额源），此处只负责拼文本。
        from ming_sim.flows import compute_budget_lines  # 局部 import 避免与 flows 顶层循环依赖
        st = state if state is not None else self.load_state("")
        budget = compute_budget_lines(self, st)

        def _sum(acc: str, direction: str) -> int:
            return sum(int(it["amount"]) for it in budget[acc][direction])

        def _amt(acc: str, direction: str, name: str) -> int:
            return sum(int(it["amount"]) for it in budget[acc][direction] if it["name"] == name)

        gk_in, gk_out = _sum("国库", "income"), _sum("国库", "expense")
        nk_in, nk_out = _sum("内库", "income"), _sum("内库", "expense")
        gk_net, nk_net = gk_in - gk_out, nk_in - nk_out
        # 净额已由程序减好，并直接给出盈/亏结论——LLM 不得再自行加减，照此结论叙事/推进。
        gk_verdict = "本月固定收支盈余（净＞0）" if gk_net > 0 else (
            "本月固定收支亏空（净＜0）" if gk_net < 0 else "本月固定收支持平（净＝0）")
        return (
            f"【国库本{TURN_UNIT}固定收支结论：{gk_verdict}，净{format_money_delta(gk_net)}】"
            f"（明细：入{format_money(gk_in)}"
            f"〔田赋+辽饷+盐税+商税+建筑产出{format_money(_amt('国库', 'income', '建筑产出'))}〕"
            f"出{format_money(gk_out)}"
            f"〔军饷{format_money(_amt('国库', 'expense', '各军军饷'))}+宗室+官俸+补给+"
            f"建筑维护{format_money(_amt('国库', 'expense', '建筑维护'))}〕"
            f"，此明细仅供参考，盈亏以上方结论为准，勿再自算）；"
            f"内库本{TURN_UNIT}净{format_money_delta(nk_net)}"
            f"（入{format_money(nk_in)}出{format_money(nk_out)}"
            f"〔内廷维护{format_money(_amt('内库', 'expense', '建筑维护'))}〕）。"
        )

    def treasury_report(self, state: GameState, limit: int = 6) -> str:
        account_rows = self.conn.execute(
            "SELECT account, balance FROM economy_accounts ORDER BY account DESC"
        ).fetchall()
        if not account_rows:
            account_text = f"国库{format_money(state.metrics['国库'])}，内库{format_money(state.metrics['内库'])}"
        else:
            account_text = "，".join(f"{row['account']}{format_money(row['balance'])}" for row in account_rows)

        period_rows = self.conn.execute(
            """
            SELECT account,
                   SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS income,
                   SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END) AS expense
            FROM economy_ledger
            WHERE turn = ?
            GROUP BY account
            ORDER BY account DESC
            """,
            (state.turn,),
        ).fetchall()
        period_text = "；".join(
            f"{row['account']}入{format_money(row['income'] or 0)}出{format_money(row['expense'] or 0)}"
            for row in period_rows
        )
        if not period_text:
            period_text = f"本{TURN_UNIT}尚无新账"

        ledger_rows = self.conn.execute(
            """
            SELECT year, period, account, delta, category, reason, actor
            FROM economy_ledger
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        recent = []
        for row in reversed(ledger_rows):
            delta = float(row["delta"])
            sign = "+" if delta > 0 else ""
            recent.append(
                f"{period_label(int(row['year']), int(row['period']))} {row['account']}{sign}{format_money(delta)} {row['category']}：{row['reason']}"
            )
        recent_text = "；".join(recent) if recent else "未见流水"
        budget = self.treasury_budget_summary(state)
        return f"{budget}账面：{account_text}。本{TURN_UNIT}收支：{period_text}。近账：{recent_text}。"

    def treasury_ledger(self, account: str, turns: int = 6) -> str:
        """查国库或内库最近 N 回合流水明细。"""
        rows = self.conn.execute(
            """
            SELECT turn, year, period, delta, balance_after, category, reason, actor
            FROM economy_ledger
            WHERE account = ? AND category <> '期初'
            ORDER BY id DESC
            LIMIT ?
            """,
            (account, turns * 20),
        ).fetchall()
        if not rows:
            return f"{account}无流水记录。"
        lines = [f"【{account}近{turns}回合流水（最新在前）】"]
        for r in rows:
            sign = "+" if float(r["delta"]) > 0 else ""
            lines.append(
                f"{r['year']}年{r['period']}月（turn{r['turn']}）"
                f" {sign}{format_money_delta(r['delta'])} → 余{format_money(r['balance_after'])} "
                f"[{r['category']}] {r['reason']}"
                + (f"（{r['actor']}）" if r["actor"] else "")
            )
        return "\n".join(lines)

    def turn_economy_summary(self, turn: int) -> str:
        rows = self.conn.execute(
            """
            SELECT account,
                   SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS income,
                   SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END) AS expense,
                   SUM(delta) AS net
            FROM economy_ledger
            WHERE turn = ? AND category <> '期初'
            GROUP BY account
            ORDER BY account DESC
            """,
            (turn,),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}无新增收支。"
        parts = []
        for row in rows:
            income = int(row["income"] or 0)
            expense = int(row["expense"] or 0)
            net = int(row["net"] or 0)
            parts.append(
                f"{row['account']}收入{format_money(income)}、支出{format_money(expense)}、净变{format_money_delta(net)}"
            )
        return "；".join(parts) + "。"
