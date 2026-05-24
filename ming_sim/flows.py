"""固定月度财政流与数值/经济/派系 delta 应用。L6。"""

from __future__ import annotations

from typing import Dict, List, Optional

from ming_sim.constants import TURN_UNIT
from ming_sim.db import GameDB
from ming_sim.models import GameState, monthly_amount

ISSUE_METRIC_KEYS = {"民心", "皇威"}
ISSUE_METRIC_LOCK_CAPS = {
    "民心": 8, "皇威": 5,
}

ARMY_SALARY_PRIORITY = [
    "guanning", "xuan_da", "jizhen", "shanhaiguan", "jingying",
    "denglaiz", "dongjiang", "shaanxi", "nanjing", "fujian", "guangdong", "xinar",
]


def _apply_metric_dict(
    state: GameState, metric_delta: Dict[str, object], caps: Optional[Dict[str, int]] = None
) -> Dict[str, int]:
    applied: Dict[str, int] = {}
    for key, val in (metric_delta or {}).items():
        if key not in ISSUE_METRIC_KEYS:
            continue
        try:
            d = int(val)
        except (TypeError, ValueError):
            continue
        if caps and key in caps:
            cap = caps[key]
            if d > cap:
                d = cap
            elif d < -cap:
                d = -cap
        if d == 0:
            continue
        state.metrics[key] = int(state.metrics.get(key, 0)) + d
        applied[key] = applied.get(key, 0) + d
    return applied


def _apply_economy_list(
    db: GameDB, state: GameState, economy: List[Dict[str, object]]
) -> List[Dict[str, object]]:
    applied: List[Dict[str, object]] = []
    for move in economy or []:
        account = str(move.get("account") or "")
        if account not in ("国库", "内库"):
            continue
        try:
            delta = int(move.get("delta") or 0)
        except (TypeError, ValueError):
            continue
        category = str(move.get("category") or move.get("reason") or "事项")[:40]
        reason = str(move.get("reason") or "")[:80]
        actual = db.record_issue_economy_move(state, account, delta, category, reason)
        if actual:
            applied.append({"account": account, "delta": actual, "reason": reason})
    return applied


def apply_fixed_period_flows(db: GameDB, state: GameState) -> List[Dict[str, object]]:
    """月度财政 tick：把原月度固定收支按月切分，在 LLM 推演前落账。"""
    cfg = db.get_fiscal_config()
    flows: List[Dict[str, object]] = []

    def _income(account: str, amount: int, category: str, reason: str) -> None:
        if amount <= 0:
            return
        actual = db.record_issue_economy_move(state, account, amount, category, reason)
        flows.append({"dir": "income", "account": account, "amount": actual,
                      "category": category, "reason": reason})

    def _expense(account: str, amount: int, category: str, reason: str) -> None:
        if amount <= 0:
            return
        actual = db.record_issue_economy_move(state, account, -amount, category, reason)
        flows.append({"dir": "expense", "account": account, "amount": abs(actual),
                      "category": category, "reason": reason})

    # ── 国库收入 ──────────────────────────────────────────────────────────────
    land_base = db.conn.execute("SELECT SUM(tax_per_turn) FROM regions").fetchone()[0]
    if land_base is None:
        raise SystemExit("fiscal_tick: regions 表无数据，中止。")
    _income("国库", monthly_amount(round(int(land_base) * cfg["田赋_rate"] / 100)), "田赋", f"两京十三省田赋{TURN_UNIT}实收")
    _income("国库", monthly_amount(round(cfg["辽饷_base"] * cfg["辽饷_rate"] / 100)), "辽饷", f"辽东专项加派{TURN_UNIT}实收")
    _income("国库", monthly_amount(round(cfg["盐税_base"] * cfg["盐税_rate"] / 100)), "盐税", f"两淮两浙盐引{TURN_UNIT}定额")
    _income("国库", monthly_amount(round(cfg["商税_base"] * cfg["商税_rate"] / 100)), "商税", f"各地关卡店税{TURN_UNIT}汇总")

    # ── 国库支出（非军饷）────────────────────────────────────────────────────
    _expense("国库", monthly_amount(round(cfg["宗室禄米_base"] * cfg["宗室禄米_rate"] / 100)), "宗室禄米", f"诸藩宗室{TURN_UNIT}禄米")
    _expense("国库", monthly_amount(round(cfg["官俸_base"]     * cfg["官俸_rate"]     / 100)), "百官俸禄", f"在京百官{TURN_UNIT}俸禄")
    _expense("国库", monthly_amount(round(cfg["工程_base"]     * cfg["工程_rate"]     / 100)), "工部",     f"工部{TURN_UNIT}维护支出")
    _expense("国库", monthly_amount(round(cfg["赈灾_base"]     * cfg["赈灾_rate"]     / 100)), "赈灾备用", f"制度性{TURN_UNIT}赈灾预留")
    _expense("国库", monthly_amount(round(cfg["九边补给_base"]  * cfg["九边补给_rate"] / 100)), "九边补给", f"九边{TURN_UNIT}粮草补给")

    # ── 内库收入 ──────────────────────────────────────────────────────────────
    _income("内库", monthly_amount(round(cfg["皇庄_base"]  * cfg["皇庄_rate"]  / 100)), "皇庄",   f"皇庄地租{TURN_UNIT}上缴")
    _income("内库", monthly_amount(round(cfg["织造_base"]  * cfg["织造_rate"]  / 100)), "织造",   f"苏杭织造局{TURN_UNIT}上缴")
    _income("内库", monthly_amount(round(cfg["矿税_base"]  * cfg["矿税_rate"]  / 100)), "矿税",   "矿税残余")

    # ── 内库支出 ──────────────────────────────────────────────────────────────
    _expense("内库", monthly_amount(round(cfg["宫廷_base"]   * cfg["宫廷_rate"]   / 100)), "宫廷开支", f"皇室{TURN_UNIT}用度")
    _expense("内库", monthly_amount(round(cfg["内廷俸_base"] * cfg["内廷俸_rate"] / 100)), "内廷俸禄", f"太监宫女{TURN_UNIT}俸禄")
    _expense("内库", monthly_amount(round(cfg["妃嫔_base"]   * cfg["妃嫔_rate"]   / 100)), "妃嫔供奉", f"后宫妃嫔{TURN_UNIT}供奉")

    # ── 各军军饷（按优先级，能发多少发多少，不足挂 arrears）─────────────────
    army_rows_raw = db.conn.execute(
        "SELECT id, name, maintenance_per_turn, arrears, morale FROM armies"
    ).fetchall()
    if not army_rows_raw:
        raise SystemExit("fiscal_tick: armies 表无数据，中止。")
    army_map = {str(r["id"]): r for r in army_rows_raw}
    ordered = [army_map[k] for k in ARMY_SALARY_PRIORITY if k in army_map]
    ordered += [r for r in army_rows_raw if str(r["id"]) not in ARMY_SALARY_PRIORITY]

    for row in ordered:
        army_id = str(row["id"])
        name = str(row["name"])
        needed = monthly_amount(int(row["maintenance_per_turn"]))
        if needed <= 0:
            continue
        available = max(0, int(state.metrics["国库"]))
        pay = min(needed, available)
        shortfall = needed - pay

        if pay > 0:
            db.record_issue_economy_move(state, "国库", -pay, "各军军饷", f"{name}{TURN_UNIT}军饷")

        if shortfall > 0:
            arrears_delta = max(1, round(15 * shortfall / needed))
            morale_delta = -max(1, round(8 * shortfall / needed))
        else:
            arrears_delta = -2
            morale_delta = 0

        old_arrears = int(row["arrears"])
        old_morale = int(row["morale"])
        new_arrears = max(0, min(100, old_arrears + arrears_delta))
        new_morale = max(0, min(100, old_morale + morale_delta))

        db.conn.execute(
            "UPDATE armies SET arrears = ?, morale = ? WHERE id = ?",
            (new_arrears, new_morale, army_id),
        )
        db.conn.executemany(
            """INSERT INTO army_logs
               (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '户部')""",
            [
                (state.turn, state.year, state.period, army_id,
                 "arrears", str(old_arrears), str(new_arrears), new_arrears - old_arrears,
                 f"{TURN_UNIT}军饷{'欠发' if shortfall > 0 else '足额'}"),
                (state.turn, state.year, state.period, army_id,
                 "morale", str(old_morale), str(new_morale), new_morale - old_morale,
                 f"{TURN_UNIT}军饷{'欠发' if shortfall > 0 else '足额'}"),
            ],
        )
        db.conn.commit()

        flows.append({
            "dir": "expense", "account": "国库", "category": "各军军饷",
            "army": name, "needed": needed, "paid": pay, "shortfall": shortfall,
            "arrears_delta": new_arrears - old_arrears,
            "morale_delta": new_morale - old_morale,
        })

    # ── 建筑：固定产出 + 固定维护（纯程序化，不调 LLM）─────────────────────────
    # buildings 表 maintenance/output_amount 已是月值，不过 monthly_amount。
    # 产出按 condition/100 折算；国库/内库走 economy_ledger，民心/皇威直改量表。
    building_rows = db.conn.execute(
        "SELECT id, name, condition, maintenance, output_metric, output_amount FROM buildings"
    ).fetchall()
    for row in building_rows:
        bid = str(row["id"])
        name = str(row["name"])
        condition = max(0, min(100, int(row["condition"])))
        maintenance = max(0, int(row["maintenance"]))
        metric = str(row["output_metric"])
        out_base = max(0, int(row["output_amount"]))
        produced = round(out_base * condition / 100) if metric and out_base else 0

        if metric in ("国库", "内库"):
            if produced > 0:
                db.record_issue_economy_move(state, metric, produced, "建筑产出", f"{name}{TURN_UNIT}产出")
                flows.append({"dir": "income", "account": metric, "category": "建筑产出",
                              "building": name, "amount": produced})
        elif metric in ("民心", "皇威"):
            if produced > 0:
                before = int(state.metrics.get(metric, 0))
                state.metrics[metric] = max(0, min(100, before + produced))
                flows.append({"dir": "score", "metric": metric, "category": "建筑产出",
                              "building": name, "amount": state.metrics[metric] - before})

        if maintenance > 0:
            paid = db.record_issue_economy_move(state, "国库", -maintenance, "建筑维护",
                                                f"{name}{TURN_UNIT}维护费")
            flows.append({"dir": "expense", "account": "国库", "category": "建筑维护",
                          "building": name, "needed": maintenance, "paid": abs(paid),
                          "shortfall": maintenance - abs(paid)})

    return flows


def _apply_faction_dict(db: GameDB, faction_delta: Dict[str, object]) -> Dict[str, int]:
    cleaned: Dict[str, int] = {}
    for key, val in (faction_delta or {}).items():
        try:
            d = int(val)
        except (TypeError, ValueError):
            continue
        if d == 0:
            continue
        cleaned[key] = d
    if cleaned:
        db.adjust_factions(cleaned)
    return cleaned


def _apply_class_dict(db: GameDB, class_delta: Dict[str, object]) -> Dict[str, Dict[str, int]]:
    """class_delta 结构：{ '农民@shaanxi': {'satisfaction': -5, 'leverage': +3}, '士绅': {...} }
    key 不带 @ 默认全国汇总。字段只接 satisfaction / leverage 增量。
    """
    cleaned: Dict[str, Dict[str, int]] = {}
    for key, fields in (class_delta or {}).items():
        if not isinstance(fields, dict):
            continue
        entry: Dict[str, int] = {}
        for fname in ("satisfaction", "leverage"):
            raw = fields.get(fname)
            if raw is None:
                continue
            try:
                d = int(raw)
            except (TypeError, ValueError):
                continue
            if d == 0:
                continue
            entry[fname] = d
        if entry:
            cleaned[str(key)] = entry
    if cleaned:
        db.adjust_classes(cleaned)
    return cleaned
