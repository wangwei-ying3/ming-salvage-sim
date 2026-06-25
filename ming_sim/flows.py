"""固定月度财政流与数值/经济/派系 delta 应用。L6。"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from ming_sim.constants import TURN_UNIT
from ming_sim.db import GameDB
from ming_sim.models import GameState


_FISCAL_BASIS_LABELS = {
    "population": "人口",
    "registered_land": "在册田亩",
    "hidden_land": "隐田",
    "guan_min_tian": "官民田",
    "wang_tian": "藩王庄田",
    "huang_tian": "皇庄",
}


def _basis_total(db: GameDB, basis: str) -> int:
    """动态税基合计。人口单位=万人；田地单位=万亩。"""
    if basis in {"population", "registered_land", "hidden_land"}:
        row = db.conn.execute(f"SELECT SUM({basis}) AS total FROM regions").fetchone()
        return int(row["total"] or 0)
    if basis in {"guan_min_tian", "wang_tian", "huang_tian"}:
        row = db.conn.execute(
            f"SELECT SUM(COALESCE(json_extract(fiscal, '$.{basis}'), 0)) AS total FROM regions"
        ).fetchone()
        return int(row["total"] or 0)
    return 0


def _dynamic_basis_tax_amount(basis_total: int, rate_value: int, rate_unit: str, collect_rate: int) -> int:
    """税基单位是万人/万亩。rate_unit 含“毫”时 rate_value=毫/人(亩)/年，否则按两/人(亩)/年。"""
    unit_divisor = 10000 if "毫" in str(rate_unit or "") else 1
    return round(basis_total * rate_value / unit_divisor / 12 * collect_rate / 100)


# ── 省级财政计算 ──────────────────────────────────────────────────────────────
# 田赋/皇庄亩率（毫/亩/年）走 fiscal_config（田赋亩率_base / 皇庄亩率_base），可后台调/随改革变。
# 田赋省可在 fiscal.tian_fu_li 覆盖全局。史实锚点：田赋250毫≈0.025两/亩(全国1560万/年)，
# 皇庄32毫≈0.0032两/亩(两京2500万亩≈8万/月，子粒银量级)。内帑第一大来源是金花银而非皇庄。


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

    田赋/辽饷按官民田×亩率实时计算：
      tax_per_turn = 该省田赋账面月额（万两）        ← 不再是四税合计，就是田赋
      fiscal.liao_xiang_li = 辽饷亩率（毫/亩/年）      ← 按官民田摊派
    盐税/商税不按亩，仍读 fiscal.salt_tax / commerce_tax 账面应征月额。
    四税各乘综合到账率 eff（辽饷再受皇威折扣）。皇庄地租单独走内库（huang_tian×租率）。

    返回 (国库月收合计, 内库月收合计, 明细列表)。
    """
    rows = db.conn.execute(
        "SELECT id, name, unrest, gentry_resistance, fiscal FROM regions"
    ).fetchall()
    if not rows:
        raise SystemExit("calc_province_fiscal: regions 表无数据，中止。")

    cfg = db.get_fiscal_config()
    tian_fu_li_global = int(cfg.get("田赋亩率_base", 250))   # 毫/亩/年
    huang_li = int(cfg.get("皇庄亩率_base", 32))             # 毫/亩/年
    wei = state.metrics.get("皇威", 58)

    guo_ku_total = 0
    nei_ku_total = 0
    details: List[Dict] = []

    for row in rows:
        region_id    = str(row["id"])
        name         = str(row["name"])
        unrest       = int(row["unrest"])
        gentry       = int(row["gentry_resistance"])
        fiscal: dict = json.loads(row["fiscal"] or "{}")

        guan_min_tian = fiscal.get("guan_min_tian", 0)
        huang_tian   = fiscal.get("huang_tian", 0)
        liao_xiang_li = fiscal.get("liao_xiang_li", 0)
        salt_tax     = fiscal.get("salt_tax", 0)
        commerce_tax = fiscal.get("commerce_tax", 0)

        # 田赋账面月额 = 官民田万亩 × 田赋亩率(毫/亩/年) / 10000 / 12。
        # 省可在 fiscal.tian_fu_li 覆盖全局亩率（江南重赋/边地轻赋/罢田赋=0）。
        tian_fu_li = int(fiscal.get("tian_fu_li", tian_fu_li_global))
        tian_fu_base = round(guan_min_tian * tian_fu_li / 10000 / 12)

        # 辽饷账面月额 = 官民田万亩 × 辽饷亩率(毫/亩/年) / 10000 / 12。
        # 这样清丈后 guan_min_tian 增减会自动牵动辽饷摊派。
        liao_xiang_base = round(guan_min_tian * int(liao_xiang_li or 0) / 10000 / 12)

        # 综合到账率（单一系数，上限1.0，改革后可接近满额）
        eff = _province_efficiency(fiscal, gentry, unrest)

        # 辽饷受皇威额外折扣（皇威低→地方截留多）
        liao_eff = eff * (0.5 + wei / 200)
        liao_eff = max(0.10, min(1.00, liao_eff))

        # 四税各乘综合到账率（田赋/辽饷账面按田亩摊成月额，再乘 eff）
        tian_fu  = round(tian_fu_base * eff)
        liao     = round(liao_xiang_base * liao_eff)
        salt     = round(salt_tax     * eff)
        commerce = round(commerce_tax * eff)

        # 皇庄 → 内库：huang_tian万亩 × 皇庄亩率(毫/亩/年) / 10000 / 12。
        # 皇室直辖田征收力强，不吃官民田那套士绅瞒报/腐败折扣（不乘 eff）。
        # 亩率走 fiscal_config.皇庄亩率_base(默认32毫≈0.0032两/亩)；皇庄_rate 整体倍率在 compute_budget_lines 施加。
        huang_income = round(huang_tian * huang_li / 10000 / 12)

        province_guo = tian_fu + liao + salt + commerce
        guo_ku_total += province_guo
        nei_ku_total += huang_income

        details.append({
            "region_id":       region_id,
            "name":            name,
            "田赋":            tian_fu,
            "田赋账面":         tian_fu_base,
            "辽饷":            liao,
            "盐税":            salt,
            "商税":            commerce,
            "皇庄":            huang_income,
            "province_total":  province_guo,
            "efficiency":      round(eff, 3),
        })

    return guo_ku_total, nei_ku_total, details


# 固定月度收支科目目录现走数据驱动：db.iter_budget_items() 从 fiscal_config 读
# budget_role=fixed 的 base 项（account/direction/display）。加新税源只改 content/fiscal_config.json。
# 税收/皇庄走 calc_province_fiscal（动态），军饷走 SUM(maint)。这是「定额预算」唯一定义，
# flows 落账 / UI budget_payload / db.treasury_budget_summary 三处共用 compute_budget_lines，禁止各自重算。


def compute_budget_lines(db: GameDB, state: GameState) -> Dict[str, Dict[str, list]]:
    """唯一定额预算源。返回 {"国库":{"income":[{name,amount,note}],"expense":[...]},"内库":{...}}。
    税收/皇庄＝calc_province_fiscal 动态值；军饷＝SUM(明军 maint)；建筑＝按 condition 折产/维护；
    其余＝fiscal_config base×rate（全月值）。三处调用方据此各取所需，不重算。"""
    cfg = db.get_fiscal_config()
    gk_tax, nk_huang, _ = calc_province_fiscal(state, db)
    army_total = db.conn.execute(
        "SELECT SUM(maintenance_per_turn) FROM armies WHERE owner_power='ming' AND active = 1"
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
    # 皇庄＝calc_province_fiscal 按省 huang_tian×租率 实算总额（开局仅北直隶≈20万），
    # 再乘 fiscal_config.皇庄_rate 作整体倍率（改革/没收可调）。皇庄_base 已废，不再叠加。
    huang_total = round(nk_huang * cfg.get("皇庄_rate", 100) / 100)
    budget["内库"]["income"].append(
        {"name": "皇庄", "amount": int(huang_total), "note": "各省皇庄月地租（huang_tian×租率，皇室直辖不吃折扣）"}
    )
    for item in db.iter_budget_items():
        base_key = str(item["key"])
        rate_key = base_key[:-5] + "_rate"  # 去 _base 换 _rate
        base = int(cfg.get(base_key, 0))
        rate = int(cfg.get(rate_key, 100))
        formula_kind = str(item.get("formula") or "")
        basis = str(item.get("basis") or "")
        basis_total = _basis_total(db, basis) if formula_kind == "per_basis" else 0
        if formula_kind == "per_basis" and basis_total > 0:
            amount = _dynamic_basis_tax_amount(basis_total, base, str(item.get("rate_unit") or ""), rate)
            basis_label = _FISCAL_BASIS_LABELS.get(basis, basis)
            unit_divisor_text = "/10000" if "毫" in str(item.get("rate_unit") or "") else ""
            formula_text = f"{basis_label}{basis_total}×{base}{unit_divisor_text}/12×{rate}%"
            note = str(item.get("note") or "")
            if item.get("rate_unit"):
                note = f"{note}（{item['rate_unit']}）" if note else str(item["rate_unit"])
        else:
            amount = round(base * rate / 100)
            formula_text = f"{base}万×{rate}%"
            note = str(item.get("note") or "")
        budget[str(item["account"])][str(item["direction"])].append(
            {
                "name": str(item["display"]),
                "amount": int(amount),
                "note": note,
                "base_key": base_key,
                "rate_key": rate_key,
                "base": base,
                "rate": rate,
                "formula": formula_text,
                "formula_kind": formula_kind,
                "basis": basis,
                "basis_total": basis_total,
                "rate_unit": str(item.get("rate_unit") or ""),
            }
        )

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
        db.commit()
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
            delta = float(move.get("delta") or 0)
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
            budget = int(abs(delta))
            spent = _auto_pay_arrears_by_priority(db, state, account, budget, category, reason)
            applied.append({"account": account, "delta": -spent, "reason": reason})
            continue
        if purpose == "补饷" and target_kind == "army" and delta < 0 and raw_target_id:
            row = db.conn.execute(
                "SELECT id, name, arrears FROM armies WHERE id = ?", (raw_target_id,)
            ).fetchone()
            if row is None:
                # army_id 拼错 → 退化为按优先级散
                budget = int(abs(delta))
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
            actual_pay = min(int(abs(delta)), current_arrears)
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
                db.commit()
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


def apply_annual_grain_flows(db: GameDB, state: GameState) -> List[Dict[str, object]]:
    """12月年度粮食结算：年产入仓，再按人口扣全年口粮。"""
    if int(state.period) != 12:
        return []

    cfg = db.get_fiscal_config()
    try:
        per_capita = int(cfg.get("人均年耗粮_base", 3))
    except (TypeError, ValueError):
        per_capita = 3
    per_capita = max(0, per_capita)

    rows = db.conn.execute(
        "SELECT id, name, population, fiscal FROM regions"
    ).fetchall()
    flows: List[Dict[str, object]] = []
    for row in rows:
        region_id = str(row["id"])
        name = str(row["name"])
        population = int(row["population"] or 0)
        try:
            fiscal: dict = json.loads(row["fiscal"] or "{}")
        except (TypeError, json.JSONDecodeError):
            fiscal = {}

        old_stock = int(fiscal.get("grain_stock") or 0)
        grain_output = int(fiscal.get("grain_output") or 0)
        annual_consumption = round(population * per_capita)
        raw_stock = old_stock + grain_output - annual_consumption
        new_stock = max(0, raw_stock)
        actual_delta = new_stock - old_stock
        # 缺口（万石）：年产+存粮不抵口粮的部分，>0 即闹饥荒，喂给人口结算扣减员。
        grain_shortfall = max(0, -raw_stock)

        fiscal["grain_stock"] = new_stock
        db.conn.execute(
            "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(fiscal, ensure_ascii=False), region_id),
        )

        reason = f"年度粮食结算：年产入仓{grain_output}万石，人口耗粮{annual_consumption}万石"
        if actual_delta != 0:
            db.conn.execute(
                """
                INSERT INTO region_logs
                (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '户部')
                """,
                (
                    state.turn, state.year, state.period, region_id,
                    "grain_stock", str(old_stock), str(new_stock), actual_delta, reason,
                ),
            )

        flows.append({
            "dir": "grain", "region": region_id, "name": name, "output": grain_output,
            "consumption": annual_consumption, "delta": actual_delta, "stock": new_stock,
            "shortfall": grain_shortfall,
        })

    db.conn.commit()
    return flows


def apply_annual_population_flows(
    db: GameDB, state: GameState, grain_flows: List[Dict[str, object]]
) -> List[Dict[str, object]]:
    """12月年度人口结算：各省人口按基础增长率 + 民心/灾荒修正逐年涨跌。

    紧随 apply_annual_grain_flows 之后跑，复用其算出的本年缺粮（闹饥荒→负增长）。
    增长率全走 fiscal_config（人口增长率系统，单位‰/年），改默认值改 JSON 升 schema_version。
    """
    if int(state.period) != 12:
        return []

    cfg = db.get_fiscal_config()

    def _rate(key: str, default: int) -> float:
        try:
            return int(cfg.get(key, default)) / 1000.0  # 设定按千分率(‰)存，落地除以1000
        except (TypeError, ValueError):
            return default / 1000.0

    base_rate = _rate("人口基础增长率_base", 8)        # +0.8%/年 平年自然增长
    support_bonus = _rate("人口民心增益_base", 4)      # 民心>60 时 +0.4%
    support_penalty = _rate("人口民心减损_base", 4)    # 民心<40 时 -0.4%
    unrest_penalty = _rate("人口动乱减损_base", 6)     # 动乱>50 时 -0.6%
    famine_penalty = _rate("人口饥荒减损_base", 10)    # 本年缺粮 额外 -1.0%

    shortfall_by_region = {
        str(f.get("region")): int(f.get("shortfall") or 0)
        for f in grain_flows
        if f.get("dir") == "grain"
    }

    rows = db.conn.execute(
        "SELECT id, name, population, public_support, unrest FROM regions"
    ).fetchall()
    flows: List[Dict[str, object]] = []
    for row in rows:
        region_id = str(row["id"])
        name = str(row["name"])
        population = int(row["population"] or 0)
        if population <= 0:
            continue

        support = int(row["public_support"] or 0)
        unrest = int(row["unrest"] or 0)
        shortfall = shortfall_by_region.get(region_id, 0)

        rate = base_rate
        if support > 60:
            rate += support_bonus
        elif support < 40:
            rate -= support_penalty
        if unrest > 50:
            rate -= unrest_penalty
        if shortfall > 0:
            rate -= famine_penalty

        new_population = max(0, round(population * (1 + rate)))
        actual_delta = new_population - population
        if actual_delta == 0:
            continue

        db.conn.execute(
            "UPDATE regions SET population = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_population, region_id),
        )
        reason = (
            f"年度人口结算：增长率{rate * 100:+.1f}%"
            f"（民心{support}/动乱{unrest}"
            f"{'/缺粮' if shortfall > 0 else ''}）"
        )
        db.conn.execute(
            """
            INSERT INTO region_logs
            (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '户部')
            """,
            (
                state.turn, state.year, state.period, region_id,
                "population", str(population), str(new_population), actual_delta, reason,
            ),
        )
        flows.append({
            "dir": "population", "region": name, "rate": round(rate * 100, 1),
            "old": population, "new": new_population, "delta": actual_delta,
        })

    db.conn.commit()
    return flows


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
    # 武器型号 id 集合：output_metric 命中则该建筑产械入军备总库（见下分支）。
    weapon_ids = {str(r["id"]) for r in db.conn.execute("SELECT id FROM weapons").fetchall()}
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
        elif metric in weapon_ids:
            # 军械建筑产武器入总库（免费，料钱已含在建筑维护费）。前置科技未解锁则空转不产。
            if produced > 0 and db.weapon_unlocked(metric):
                new_qty = db.add_arms_stock(state, metric, produced, source="building",
                                            reason=f"{name}{TURN_UNIT}产械")
                flows.append({"dir": "arms", "weapon": metric, "category": "建筑产械",
                              "building": name, "amount": produced, "stock": new_qty})

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
    grain_flows = apply_annual_grain_flows(db, state)
    flows.extend(grain_flows)
    flows.extend(apply_annual_population_flows(db, state, grain_flows))
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
