"""固定月度财政流与数值/经济/派系 delta 应用。L6。"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from ming_sim.constants import TURN_UNIT
from ming_sim.db import GameDB
from ming_sim.models import GameState


# ── 省级财政计算 ──────────────────────────────────────────────────────────────

# 皇庄增量租率：没收藩王庄田转皇庄后，每万亩每月增加内库收入（万两）
# 基准皇庄收入走 fiscal_config.皇庄_base；此常数只用于增量计算
_HUANG_TIAN_RENT_PER_WAN_MU = 0.57  # ≈ 20万两/月 ÷ 35万亩


def _province_transport_ratio(fiscal: dict, unrest: int) -> float:
    """解运比（保留函数签名，返回1.0；实际损耗已并入 _province_efficiency）。"""
    return 1.0


def _province_collection_rate(gentry_resistance: int, unrest: int) -> float:
    """实收率（保留函数签名，返回1.0；实际损耗已并入 _province_efficiency）。"""
    return 1.0


def _province_efficiency(fiscal: dict, gentry_resistance: int, unrest: int) -> float:
    """综合到账率：士绅阻力 + 腐败度 + 民变三因子决定税银实际到账比例。
    上限 1.0（现代化/彻底改革后可接近满额），下限 0.05（完全失控）。
    开局典型值：富省~0.25，贫乱省~0.15。
    改革路径：清查士绅→gentry↓，整治贪腐→corruption↓，赈灾→unrest↓，效率可升至0.60+。
    """
    corruption = fiscal.get("corruption", 50)
    rate = (1.0
            - gentry_resistance / 100 * 0.55
            - corruption        / 100 * 0.45
            - max(0, unrest - 20) / 100 * 0.30)
    return max(0.05, min(1.00, rate))


def calc_province_fiscal(
    state: GameState,
    db: GameDB,
) -> Tuple[int, int, List[Dict]]:
    """按省计算月度财政收入。

    tax_per_turn 是省级校准月税基准（含田赋+辽饷+盐税+商税合计）。
    fiscal JSON 里的税种细分用于拆比例；动态系数（tr/cr）乘在总量上。
    皇庄地租单独走内库，基准来自 fiscal.huang_tian × 租率。

    返回 (国库月收合计, 内库月收合计, 明细列表)。
    """
    rows = db.conn.execute(
        "SELECT id, name, unrest, gentry_resistance, tax_per_turn, fiscal FROM regions"
    ).fetchall()
    if not rows:
        raise SystemExit("calc_province_fiscal: regions 表无数据，中止。")

    wei = state.metrics.get("皇威", 58)

    guo_ku_total = 0
    nei_ku_total = 0
    details: List[Dict] = []

    for row in rows:
        region_id    = str(row["id"])
        name         = str(row["name"])
        unrest       = int(row["unrest"])
        gentry       = int(row["gentry_resistance"])
        tax_base     = int(row["tax_per_turn"])   # 省级月税基准（万两）
        fiscal: dict = json.loads(row["fiscal"] or "{}")

        huang_tian   = fiscal.get("huang_tian", 0)
        liao_xiang   = fiscal.get("liao_xiang", 0)
        salt_tax     = fiscal.get("salt_tax", 0)
        commerce_tax = fiscal.get("commerce_tax", 0)

        # 综合到账率（单一系数，上限1.0，改革后可接近满额）
        eff = _province_efficiency(fiscal, gentry, unrest)

        # 辽饷受皇威额外折扣（皇威低→地方截留多）
        liao_eff = eff * (0.5 + wei / 200)
        liao_eff = max(0.10, min(1.00, liao_eff))

        # 全部税种统一乘综合到账率
        liao     = round(liao_xiang   * liao_eff)
        salt     = round(salt_tax     * eff)
        commerce = round(commerce_tax * eff)
        tian_fu_base = max(0, tax_base - liao_xiang - salt_tax - commerce_tax)
        tian_fu  = round(tian_fu_base * eff)

        # 皇庄 → 内库
        # 基准由 fiscal_config.皇庄_base 统一覆盖（已校准）；
        # huang_tian 字段用于记录没收藩王庄田后的增量：
        #   增量月收 = 新增万亩 × _HUANG_TIAN_RENT_PER_WAN_MU
        # 只有北直隶有 huang_tian > 0，增量=0（开局无新增），后续没收时才>基准
        huang_income = 0  # 开局皇庄收入走 fiscal_config，此处不重复计算

        province_guo = tian_fu + liao + salt + commerce
        guo_ku_total += province_guo
        nei_ku_total += huang_income

        details.append({
            "region_id":       region_id,
            "name":            name,
            "田赋":            tian_fu,
            "辽饷":            liao,
            "盐税":            salt,
            "商税":            commerce,
            "皇庄":            huang_income,
            "province_total":  province_guo,
            "efficiency":      round(eff, 3),
        })

    return guo_ku_total, nei_ku_total, details


# 固定月度收支项定义：(account, dir, name, base_key, rate_key, note)。
# 税收/皇庄走 calc_province_fiscal（动态），军饷走 SUM(maint)，其余按 fiscal_config base×rate。
# 全月度：base 皆月值，不再 /3。这是「定额预算」唯一定义，flows 落账 / UI budget_payload /
# db.treasury_budget_summary 三处共用 compute_budget_lines，禁止再各自重算。
_FIXED_BUDGET_ITEMS = [
    ("国库", "expense", "宗室禄米", "宗室禄米_base", "宗室禄米_rate", "诸藩宗室月禄米"),
    ("国库", "expense", "百官俸禄", "官俸_base", "官俸_rate", "在京百官月俸禄"),
    ("国库", "expense", "工部", "工程_base", "工程_rate", "工部月维护支出"),
    ("国库", "expense", "赈灾备用", "赈灾_base", "赈灾_rate", "制度性赈灾预留"),
    ("内库", "income", "织造", "织造_base", "织造_rate", "苏杭织造局月上缴"),
    ("内库", "income", "矿税", "矿税_base", "矿税_rate", "矿税残余"),
    ("内库", "expense", "宫廷开支", "宫廷_base", "宫廷_rate", "皇室日常用度"),
    ("内库", "expense", "内廷俸禄", "内廷俸_base", "内廷俸_rate", "太监宫女月俸禄"),
    ("内库", "expense", "妃嫔供奉", "妃嫔_base", "妃嫔_rate", "后宫妃嫔月供奉"),
]


def compute_budget_lines(db: GameDB, state: GameState) -> Dict[str, Dict[str, list]]:
    """唯一定额预算源。返回 {"国库":{"income":[{name,amount,note}],"expense":[...]},"内库":{...}}。
    税收/皇庄＝calc_province_fiscal 动态值；军饷＝SUM(明军 maint)；建筑＝按 condition 折产/维护；
    其余＝fiscal_config base×rate（全月值）。三处调用方据此各取所需，不重算。"""
    cfg = db.get_fiscal_config()
    gk_tax, nk_huang, _ = calc_province_fiscal(state, db)
    army_total = db.conn.execute(
        "SELECT SUM(maintenance_per_turn) FROM armies WHERE owner_power='ming'"
    ).fetchone()[0] or 0

    budget: Dict[str, Dict[str, list]] = {
        "国库": {"income": [], "expense": []},
        "内库": {"income": [], "expense": []},
    }
    budget["国库"]["income"].append(
        {"name": "田赋辽饷盐商", "amount": int(gk_tax),
         "note": "各省田赋+辽饷+盐税+商税（按腐败度/士绅阻力/民变动态折算）"}
    )
    budget["国库"]["expense"].append(
        {"name": "各军军饷", "amount": int(army_total), "note": "各军月度维护/军饷合计"}
    )
    # 皇庄＝fiscal_config 基准（开局校准月额）＋ calc_province_fiscal 的没收藩田增量（开局 0）。
    huang_base = round(int(cfg.get("皇庄_base", 20)) * cfg.get("皇庄_rate", 100) / 100)
    budget["内库"]["income"].append(
        {"name": "皇庄", "amount": int(huang_base + nk_huang), "note": "皇庄月地租（基准+没收藩田增量）"}
    )
    for account, direction, name, base_key, rate_key, note in _FIXED_BUDGET_ITEMS:
        amount = round(int(cfg.get(base_key, 0)) * cfg.get(rate_key, 100) / 100)
        budget[account][direction].append({"name": name, "amount": int(amount), "note": note})

    # 建筑：按当前 condition 折算月产出/维护。内廷类维护扣内库，余扣国库；产出按 output_metric。
    bld_in = {"国库": 0, "内库": 0}
    bld_out = {"国库": 0, "内库": 0}
    for r in db.conn.execute(
        "SELECT category, condition, maintenance, output_metric, output_amount FROM buildings"
    ).fetchall():
        cond = max(0, min(100, int(r["condition"])))
        metric = str(r["output_metric"] or "")
        if metric in ("国库", "内库") and r["output_amount"]:
            bld_in[metric] += round(int(r["output_amount"]) * cond / 100)
        maint_acc = "内库" if str(r["category"] or "") == "内廷" else "国库"
        bld_out[maint_acc] += max(0, int(r["maintenance"]))
    for acc in ("国库", "内库"):
        if bld_in[acc] > 0:
            budget[acc]["income"].append({"name": "建筑产出", "amount": bld_in[acc], "note": "建筑月产出"})
        if bld_out[acc] > 0:
            budget[acc]["expense"].append({"name": "建筑维护", "amount": bld_out[acc], "note": "建筑月维护"})
    return budget


ISSUE_METRIC_KEYS = {"民心", "皇威"}
ISSUE_METRIC_LOCK_CAPS = {
    "民心": 8, "皇威": 5,
}

ARMY_SALARY_PRIORITY = [
    "guanning", "xuan_da", "jizhen", "shanhaiguan", "jingying",
    "denglaiz", "dongjiang", "shaanxi", "nanjing", "fujian", "guangdong", "xinar",
]


def _apply_metric_dict(
    state: GameState, metric_delta: Dict[str, object], caps: Optional[Dict[str, int]] = None,
    db: Optional[GameDB] = None,
) -> Dict[str, int]:
    # 传 db 时，民心/皇威 增量先过帝国修正 %（base>=0 ×(1+net/100)，base<0 ×(1-net/100)），再夹 cap。
    mods = db.legacy_modifiers(state) if db is not None else {}
    applied: Dict[str, int] = {}
    for key, val in (metric_delta or {}).items():
        if key not in ISSUE_METRIC_KEYS:
            continue
        try:
            d = int(val)
        except (TypeError, ValueError):
            continue
        net_pct = int(mods.get(key, 0) or 0)
        if net_pct and db is not None:
            d = db.apply_legacy_pct(d, net_pct)
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


def _auto_pay_arrears_by_priority(
    db: GameDB, state: GameState, account: str, budget: int, category: str, reason: str,
) -> int:
    """LLM 补饷 economy_move 没指定 target 时的兜底：按 ARMY_SALARY_PRIORITY 顺序
    分配 budget 给有 arrears 的明军，每军按 arrears 上限扣，扣完 budget 为止。
    返回实际花出去的总额（万两）。"""
    if budget <= 0:
        return 0
    rows = db.conn.execute(
        "SELECT id, name, arrears FROM armies "
        "WHERE owner_power='ming' AND maintenance_per_turn>0 AND arrears>0"
    ).fetchall()
    army_map = {str(r["id"]): r for r in rows}
    ordered = [army_map[k] for k in ARMY_SALARY_PRIORITY if k in army_map]
    ordered += [r for r in rows if str(r["id"]) not in ARMY_SALARY_PRIORITY]
    spent = 0
    remaining = budget
    for row in ordered:
        if remaining <= 0:
            break
        army_id = str(row["id"])
        name = str(row["name"])
        current_arrears = int(row["arrears"])
        if current_arrears <= 0:
            continue
        pay = min(current_arrears, remaining)
        actual = db.record_issue_economy_move(
            state, account, -pay, category,
            f"{reason}（按优先级分给{name}{pay}万两）",
            purpose="补饷", target_kind="army", target_id=army_id,
        )
        if not actual:
            continue
        new_arrears = max(0, current_arrears + actual)
        db.conn.execute(
            "UPDATE armies SET arrears = ? WHERE id = ?", (new_arrears, army_id)
        )
        db.conn.execute(
            """INSERT INTO army_logs
               (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '诏拨补饷')""",
            (state.turn, state.year, state.period, army_id, "arrears",
             str(current_arrears), str(new_arrears), new_arrears - current_arrears,
             f"诏拨补饷{abs(actual)}万两（按优先级）"),
        )
        db.conn.commit()
        spent += abs(actual)
        remaining -= abs(actual)
    return spent


def _apply_economy_list(
    db: GameDB, state: GameState, economy: List[Dict[str, object]]
) -> List[Dict[str, object]]:
    """落 extractor 抽出的 economy_moves 到 economy_ledger。

    支持结构化字段：
    - purpose='补饷' + target_kind='army' + target_id=army_id
      → 走"按 arrears 上限扣"路径：实际扣 = min(|delta|, 该军 arrears 万两)；
        同步把 armies.arrears 减掉 actual_pay；多余的钱留在 account 不扣。
    - 其它（purpose='其它' 或 NULL）：按常规扣账（现状）。

    LLM 写非法 purpose / 找不到 target_id → 退化为'其它'常规扣账。
    """
    from ming_sim.constants import ECONOMY_PURPOSES, ECONOMY_TARGET_KINDS, TURN_UNIT as _TU
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
        raw_purpose = str(move.get("purpose") or "").strip()
        raw_target_kind = str(move.get("target_kind") or "").strip()
        raw_target_id = str(move.get("target_id") or "").strip()
        # 校验枚举；非法值退化为"其它"常规扣账
        purpose = raw_purpose if raw_purpose in ECONOMY_PURPOSES else None
        target_kind = raw_target_kind if raw_target_kind in ECONOMY_TARGET_KINDS else None

        # ── 补饷分发：按 arrears 上限扣 + 同步减 armies.arrears ───────────────
        # purpose=补饷 但缺 target_kind/target_id → 按 ARMY_SALARY_PRIORITY 优先级
        # 自动散到各军（每军按 arrears 上限扣，扣完 budget 为止）。
        if purpose == "补饷" and delta < 0 and (target_kind != "army" or not raw_target_id):
            budget = abs(delta)
            spent = _auto_pay_arrears_by_priority(db, state, account, budget, category, reason)
            applied.append({"account": account, "delta": -spent, "reason": reason})
            continue
        if purpose == "补饷" and target_kind == "army" and delta < 0 and raw_target_id:
            row = db.conn.execute(
                "SELECT id, name, arrears FROM armies WHERE id = ?", (raw_target_id,)
            ).fetchone()
            if row is None:
                # army_id 拼错 → 退化为按优先级散
                budget = abs(delta)
                spent = _auto_pay_arrears_by_priority(db, state, account, budget, category, reason)
                applied.append({"account": account, "delta": -spent, "reason": reason})
                continue
            current_arrears = int(row["arrears"])
            if current_arrears <= 0:
                # 该军已无欠饷，不扣
                applied.append({
                    "account": account, "delta": 0,
                    "reason": f"{row['name']}已无欠饷，{abs(delta)}万两未拨"
                })
                continue
            actual_pay = min(abs(delta), current_arrears)
            actual = db.record_issue_economy_move(
                state, account, -actual_pay, category, reason,
                purpose="补饷", target_kind="army", target_id=str(row["id"]),
            )
            if actual:
                # 同步减 arrears
                new_arrears = max(0, current_arrears + actual)  # actual<0, 加=减
                db.conn.execute(
                    "UPDATE armies SET arrears = ? WHERE id = ?", (new_arrears, row["id"])
                )
                db.conn.execute(
                    """INSERT INTO army_logs
                       (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '诏拨补饷')""",
                    (state.turn, state.year, state.period, row["id"], "arrears",
                     str(current_arrears), str(new_arrears), new_arrears - current_arrears,
                     f"诏拨补饷{abs(actual)}万两"),
                )
                db.conn.commit()
                applied.append({"account": account, "delta": actual, "reason": reason})
            continue

        # ── 常规扣账（其它/无 purpose）─────────────────────────────────────────
        actual = db.record_issue_economy_move(
            state, account, delta, category, reason,
            purpose=purpose or "其它" if delta < 0 else None,
            target_kind=None, target_id=None,
        )
        if actual:
            applied.append({"account": account, "delta": actual, "reason": reason})
    return applied


def apply_fixed_period_flows(db: GameDB, state: GameState) -> List[Dict[str, object]]:
    """月度财政 tick：固定收支（compute_budget_lines 定额）+ 军饷逐军 + 建筑逐项落账，LLM 推演前完成。"""
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

    # ── 固定收支落账（税/皇庄/宗室/官俸/织造…全走唯一定额源 compute_budget_lines）──
    # 军饷与建筑另有逐项落账逻辑（arrears/condition），故下面跳过这两类，仅落其余定额项。
    budget = compute_budget_lines(db, state)
    _SKIP = {"各军军饷", "建筑产出", "建筑维护"}
    for account in ("国库", "内库"):
        for it in budget[account]["income"]:
            if it["name"] in _SKIP:
                continue
            _income(account, int(it["amount"]), it["name"], f"{it['name']}{TURN_UNIT}入")
        for it in budget[account]["expense"]:
            if it["name"] in _SKIP:
                continue
            _expense(account, int(it["amount"]), it["name"], f"{it['name']}{TURN_UNIT}支")

    # ── 各军军饷（按优先级，先发当月、余额抵旧欠；不足挂 arrears 累计万两）──
    # arrears 字段语义=累计欠饷万两（整数，无上限）。flows 是唯一变更点：
    #   缺口 → arrears += 缺口；当月足额且仍有国库余 → arrears -= 抵欠（不下穿 0）。
    # 拨饷诏书走 economy_moves 加钱进国库，下月自动抵旧欠。extractor 禁写 arrears。
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
        needed = int(row["maintenance_per_turn"])
        if needed <= 0:
            continue
        available = max(0, int(state.metrics["国库"]))
        pay_current = min(needed, available)
        shortfall = needed - pay_current

        old_arrears = int(row["arrears"])
        old_morale = int(row["morale"])

        # 月固定军饷只发当月，不主动还旧欠。旧欠累积拖着，等玩家下旨拨饷才清。
        if pay_current > 0:
            db.record_issue_economy_move(
                state, "国库", -pay_current, "各军军饷", f"{name}{TURN_UNIT}军饷"
            )

        new_arrears = max(0, old_arrears + shortfall)
        if shortfall > 0:
            morale_delta = -max(1, round(8 * shortfall / needed))
        elif old_arrears == 0:
            morale_delta = +2     # 长期足额且无旧欠：缓慢恢复
        else:
            morale_delta = 0      # 当月发足但仍有旧欠：不奖励也不惩罚
        new_morale = max(0, min(100, old_morale + morale_delta))

        db.conn.execute(
            "UPDATE armies SET arrears = ?, morale = ? WHERE id = ?",
            (new_arrears, new_morale, army_id),
        )
        if shortfall > 0:
            reason_tag = f"{TURN_UNIT}军饷欠发{shortfall}万两"
        else:
            reason_tag = f"{TURN_UNIT}军饷足额"
        db.conn.executemany(
            """INSERT INTO army_logs
               (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '户部')""",
            [
                (state.turn, state.year, state.period, army_id,
                 "arrears", str(old_arrears), str(new_arrears), new_arrears - old_arrears,
                 reason_tag),
                (state.turn, state.year, state.period, army_id,
                 "morale", str(old_morale), str(new_morale), new_morale - old_morale,
                 reason_tag),
            ],
        )
        db.conn.commit()

        flows.append({
            "dir": "expense", "account": "国库", "category": "各军军饷",
            "army": name, "needed": needed, "paid": pay_current,
            "shortfall": shortfall,
            "arrears_delta": new_arrears - old_arrears,
            "morale_delta": new_morale - old_morale,
        })

    # ── 建筑：固定产出 + 固定维护（纯程序化，不调 LLM）─────────────────────────
    # buildings 表 maintenance/output_amount 已是月值，不过 monthly_amount。
    # 产出按 condition/100 折算；output_metric 按建筑自报去向落（国库/内库/民心/皇威）。
    # 维护按 category 分账：内廷类(皇庄/织造/御窑等) 扣内库；其它(财政/军事/民生/科技/交通) 扣国库。
    building_rows = db.conn.execute(
        "SELECT id, name, category, condition, maintenance, output_metric, output_amount FROM buildings"
    ).fetchall()
    for row in building_rows:
        bid = str(row["id"])
        name = str(row["name"])
        category = str(row["category"])
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
            maint_account = "内库" if category == "内廷" else "国库"
            paid = db.record_issue_economy_move(state, maint_account, -maintenance, "建筑维护",
                                                f"{name}{TURN_UNIT}维护费")
            flows.append({"dir": "expense", "account": maint_account, "category": "建筑维护",
                          "building": name, "needed": maintenance, "paid": abs(paid),
                          "shortfall": maintenance - abs(paid)})

    # 帝国修正（旧称遗产）不在此自我落账：它作为百分比修正符，由 record_issue_economy_move /
    # apply_region_deltas / apply_army_deltas 在每笔增量落账时按维度净 pct 放大/缩小。
    # 因此上面的固定收支（田赋/军饷/建筑产出）已自动被修正，无需独立 tick，否则会重复计。
    return flows


def _apply_faction_dict(db: GameDB, faction_delta: Dict[str, object]) -> Dict[str, object]:
    """支持两种格式：
    - 旧格式：{"阉党": -10}  → 仅 satisfaction 增量
    - 新格式：{"阉党": {"satisfaction": -10, "leverage": -15}}
    """
    cleaned: Dict[str, object] = {}
    for key, val in (faction_delta or {}).items():
        if isinstance(val, dict):
            entry: Dict[str, int] = {}
            for fname in ("satisfaction", "leverage"):
                raw = val.get(fname)
                if raw is None:
                    continue
                try:
                    d = int(raw)
                except (TypeError, ValueError):
                    continue
                if d != 0:
                    entry[fname] = d
            if entry:
                cleaned[str(key)] = entry
        else:
            try:
                d = int(val)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if d != 0:
                cleaned[str(key)] = d
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


# ════════════════════════════════════════════════════════════════
# 季度天灾系统
# ════════════════════════════════════════════════════════════════

_quarter_triggered: set = set()
_last_quarter: int = -1


def _season_pool_for_quarter(q: int) -> list:
    """返回季度的灾害池：[(名称, grain_security_delta, unrest_delta, pop_delta), ...]"""
    if q == 0:    # 春
        return [("洪涝", -18, 8, -2), ("蝗灾", -22, 12, -2)]
    elif q == 1:  # 夏
        return [("洪涝", -18, 8, -2), ("瘟疫", -5, 5, -2), ("蝗灾", -22, 12, -2)]
    elif q == 2:  # 秋
        return [("旱灾", -12, 8, -1), ("蝗灾", -18, 10, -2)]
    else:          # 冬
        return [("雪灾", -10, 5, -1), ("旱灾", -12, 8, -1)]


def apply_natural_disasters(db: GameDB, state: GameState) -> None:
    """季度天灾系统：每月判定，同季度同名天灾不重复，季度切换自动清除。"""
    import random
    global _quarter_triggered, _last_quarter
    month = int(state.period)
    q = (month - 3) // 3 if month >= 3 else 3
    if q != _last_quarter:
        db.conn.execute("UPDATE regions SET natural_disaster = ''")
        db.conn.commit()
        _quarter_triggered.clear()
        _last_quarter = q
    pool = [(d, g, u, p) for d, g, u, p in _season_pool_for_quarter(q)
            if d not in _quarter_triggered]
    if not pool:
        return
    if random.random() > 0.20:
        return
    dtype, grain_delta, unrest_delta, pop_delta = random.choice(pool)
    _quarter_triggered.add(dtype)
    rows = db.conn.execute("SELECT id, name FROM regions").fetchall()
    if not rows:
        return
    high_risk = {"shaanxi", "shanxi", "henan", "beizhili", "shandong"}
    region_ids = [str(r["id"]) for r in rows]
    probs = [3.0 if rid in high_risk else 1.0 for rid in region_ids]
    total = sum(probs)
    if total <= 0:
        return
    r = random.uniform(0, total)
    cumulative = 0.0
    chosen_rid = region_ids[0]
    for i, p in enumerate(probs):
        cumulative += p
        if r <= cumulative:
            chosen_rid = region_ids[i]
            break
    row = db.conn.execute("SELECT * FROM regions WHERE id=?", (chosen_rid,)).fetchone()
    if not row:
        return
    db.conn.execute(
        "UPDATE regions SET natural_disaster = ?, grain_security = MAX(0, grain_security + ?), "
        "unrest = unrest + ? WHERE id = ?",
        (dtype, grain_delta, unrest_delta, chosen_rid),
    )
    if pop_delta:
        db.conn.execute(
            "UPDATE regions SET population = MAX(0, population + ?) WHERE id = ?",
            (pop_delta, chosen_rid),
        )
    db.conn.commit()
    db.record_log(state, f"天灾预警：{row['name']}发生{dtype}，粮产受损，民心浮动。")
