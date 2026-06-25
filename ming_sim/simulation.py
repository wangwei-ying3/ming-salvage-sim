"""月末推演与打分提取：跑 simulator/extractor agent。L7。"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from agno.agent import Agent

from ming_sim.agents import parse_agent_json, run_agent_stream_text, run_agent_text
from ming_sim.constants import (
    ARMY_FIELD_ALIASES,
    ARMY_QUANTITY_FIELDS,
    ARMY_SCORE_FIELDS,
    ARMY_TEXT_FIELDS,
    FISCAL_QUANTITY_FIELDS,
    FISCAL_SCORE_FIELDS,
    POWER_FIELD_ALIASES,
    REGION_FIELD_ALIASES,
    REGION_QUANTITY_FIELDS,
    REGION_SCORE_FIELDS,
    REGION_TEXT_FIELDS,
)
from ming_sim.context import historical_anchor_for_month, victory_status
from ming_sim.db import GameDB
from ming_sim.exceptions import LLMContractError
from ming_sim.issues import gather_candidate_events, issue_to_payload
from ming_sim.models import GameState
from ming_sim.token_stats import tlog


REGION_FISCAL_FIELD_NOTE = (
    "地区财政字段字典：registered_land=在册田亩万亩（黄册登记，含官民田+藩王庄田+皇庄）；"
    "hidden_land=隐田万亩（账外逃赋田，清丈可转入官民田）；"
    "guan_min_tian=官民田万亩（可征田赋，田赋入国库）；"
    "wang_tian=藩王庄田万亩（宗藩免税庄田，通常不入田赋，侵占民田会压低官民田/形成隐田）；"
    "huang_tian=皇庄万亩（皇室直辖，地租入内库）；"
    "tian_fu_li=本省田赋亩率，单位毫/亩/年，省值覆盖全局田赋亩率；"
    "liao_xiang_li=本省辽饷亩率，单位毫/亩/年，按官民田摊派；salt_tax=盐税账面应征月额万两；"
    "commerce_tax=商税账面应征月额万两；corruption=腐败度0-100；"
    "grain_output=粮食年产万石；grain_stock=可调余粮万石。"
    "清丈口径：查出隐田时，优先写 hidden_land 下降、registered_land 上升、guan_min_tian 上升；"
    "若清查藩王/皇庄侵占，按叙事把 wang_tian 或 huang_tian 转出，并同步转入 guan_min_tian 或 hidden_land。"
)


PAYLOAD_TABLE_NOTE = (
    "盘面表在本输入开头以 TSV 给出，首行是中文列名；"
    "buildings 仅列 编号/地区/建筑，technologies 仅列 编号/科技；"
    "armies 不含状态列；regions 不含在册田/隐田/月税/士绅阻力/军事压力/状态，"
    "也不含财政JSON。"
)


_FISCAL_CONFIG_PROMPT_EXCLUDE = {
    # 这些 dynamic 残留项不再直接参与月结：田赋/辽饷/盐税/商税走省级字段，
    # 皇庄_base 已废。别喂给 extractor 当可调 fiscal_config 科目。
    "田赋_rate",
    "辽饷_base", "辽饷_rate",
    "盐税_base", "盐税_rate",
    "商税_base", "商税_rate",
    "皇庄_base",
}


def _prompt_fiscal_config(db: GameDB) -> Dict[str, int]:
    cfg = db.get_fiscal_config()
    return {k: v for k, v in cfg.items() if k not in _FISCAL_CONFIG_PROMPT_EXCLUDE}


def _fiscal_reference_lines(db: GameDB) -> List[Dict[str, object]]:
    cfg = db.get_fiscal_config()
    rows: List[Dict[str, object]] = []
    for item in db.iter_budget_items():
        base_key = str(item["key"])
        stem = base_key[:-5] if base_key.endswith("_base") else base_key
        rate_key = f"{stem}_rate"
        base = int(cfg.get(base_key, 0))
        rate = int(cfg.get(rate_key, 100))
        amount = round(base * rate / 100)
        rows.append({
            "display": str(item["display"]),
            "base_key": base_key,
            "rate_key": rate_key,
            "base": base,
            "rate": rate,
            "monthly_amount": amount,
            "formula": f"{base}×{rate}%={amount}万/月",
            "direction": str(item["direction"]),
            "account": str(item["account"]),
        })
    return rows


def _economy_system_brief(db: GameDB) -> str:
    lines = [
        "经济体系口径：财政固定月收支按 fiscal_config 的 base×rate/100 计算，base 是月度万两基准，rate 是实收/实发比例%。",
        "写财政制度变化必须先看当前公式；不得把满额100%、当前rate、目标月额混用。",
        "若诏令说“减至/压至每月X万两”，这是目标月额，程序应反推 rate；若说“每月减X万两”，这是月额增减；若说“削三成/减30%”，这是按当前月额比例削减；若说“实发率降到X%”，才是 rate 设为X。",
        "同段叙事若同时出现互相算不通的数字（例如“压至三十万”又说“实减仅二万”），视为执行受阻或叙事矛盾，不写财政制度变化，只写派系/阶级/局势后果。",
    ]
    for row in _fiscal_reference_lines(db):
        if row["display"] in {"宗室禄米", "百官俸禄"}:
            lines.append(
                f"当前{row['display']}：{row['base_key']}={row['base']}万，"
                f"{row['rate_key']}={row['rate']}%，月支={row['formula']}。"
            )
    return "\n".join(lines)


def _load_hitl_min_decisions() -> int:
    """全局玩法设置：本回合 simulator 最多应产出的决策点数（0=关闭 HITL 注入）。失败回落 1。"""
    try:
        from ming_sim.llm_config import load_runtime_game
        return int(load_runtime_game().get("hitl_min_decisions", 1))
    except Exception:
        return 1


def _load_issue_log_limit() -> int:
    """全局玩法设置：每条 active 局势注入推演的最近推进日志条数（0=不带日志）。"""
    try:
        from ming_sim.llm_config import load_runtime_game
        return int(load_runtime_game().get("issue_log_limit", 6))
    except Exception:
        return 6


TOP_LEVEL_ALIASES = {
    "国势变化": "metric_delta",
    "钱粮收支": "economy_moves",
    "财政制度变化": "fiscal_changes",
    "新立月度收支": "fiscal_creates",
    "裁撤月度收支": "fiscal_removes",
    "派系变化": "faction_delta",
    "阶级变化": "class_delta",
    "地区变化": "region_delta",
    "军队变化": "army_delta",
    "势力变化": "power_updates",
    "建军": "new_armies",
    "新建军队": "new_armies",
    "军备变化": "arms_changes",
    "外交态度": "world_advance",
    "四方动向": "world_advance",
    "局势推进": "issue_advances",
    "新立局势": "new_issues",
    "撤销局势": "cancels",
    "结案局势": "close_issues",
    "人物变化": "character_changes",
    "人事变更": "office_changes",
    "人物状态变化": "character_status_changes",
    "人物易主": "character_power_changes",
    "后宫册封": "appointments",
    "密令进度": "secret_order_updates",
    "密令副作用": "secret_order_updates",  # 兼容旧 prompt / 旧日志
    "密令结案": "secret_order_closes",
    "崇祯结局": "emperor_fate",
}
# 反译（内部英文 key → 中文顶层标签）。多个中文别名映同一英文 key 时（如
# 密令进度/密令副作用 都 → secret_order_updates），dict 反转会取“最后写入”的别名，
# 易把现行规范名覆盖成旧兼容名。这里显式钉死“现行规范名”，不依赖别名声明顺序。
_CANONICAL_TOP_LABELS = {
    "secret_order_updates": "密令进度",   # 非旧名“密令副作用”
    "new_armies": "新建军队",             # 非旧名“建军”
    "world_advance": "四方动向",          # 非旧名“外交态度”
}
TOP_LEVEL_LABELS = {value: key for key, value in TOP_LEVEL_ALIASES.items()}
TOP_LEVEL_LABELS.update(_CANONICAL_TOP_LABELS)

ITEM_FIELD_ALIASES = {
    "account": "account", "账户": "account",
    "direction": "direction", "方向": "direction",
    "display": "display", "显示名": "display", "名称": "display",
    "init_value": "init_value", "初值": "init_value", "初始值": "init_value",
    "delta": "delta", "增量": "delta",
    "unit": "unit", "单位": "unit",
    "category": "category", "分类": "category",
    "reason": "reason", "原因": "reason",
    "purpose": "purpose", "用途": "purpose",
    "target_kind": "target_kind", "目标类型": "target_kind",
    "target_id": "target_id", "目标编号": "target_id", "目标id": "target_id",
    "key": "key", "键": "key",
    "issue_id": "issue_id", "局势编号": "issue_id",
    "delta_bar": "delta_bar", "进度增量": "delta_bar",
    "budget_spent": "budget_spent", "专款支取": "budget_spent", "专款花费": "budget_spent",
    "stage_text": "stage_text", "阶段": "stage_text",
    "narrative": "narrative", "叙述": "narrative",
    "inertia_delta": "inertia_delta", "惯性增量": "inertia_delta",
    "origin_kind": "origin_kind", "来源类型": "origin_kind",
    "id": "id", "编号": "id",
    "kind": "kind", "类型": "kind",
    "tags": "tags", "题材": "tags",
    "title": "title", "标题": "title",
    "assignee": "assignee", "承办人": "assignee", "主责大臣": "assignee", "负责人": "assignee",
    "bar_value": "bar_value", "当前进度": "bar_value",
    "expected_months": "expected_months", "预计月数": "expected_months",
    "resolve_condition": "resolve_condition", "解决条件": "resolve_condition",
    "fail_condition": "fail_condition", "失败条件": "fail_condition",
    "ongoing_effects": "ongoing_effects", "持续效果": "ongoing_effects",
    "effect_on_resolve": "effect_on_resolve", "解决效果": "effect_on_resolve",
    "effect_on_fail": "effect_on_fail", "失败效果": "effect_on_fail",
    "cancellable": "cancellable", "可撤销": "cancellable",
    "metrics": "metrics", "国势": "metrics",
    "economy": "economy", "钱粮": "economy",
    "factions": "factions", "派系": "factions",
    "buildings": "buildings", "建筑": "buildings",
    "departments": "departments", "部门": "departments", "新设部门": "departments",
    "technologies": "technologies", "科技实体": "technologies", "新解锁科技": "technologies",
    "authority_scope": "authority_scope", "职掌": "authority_scope",
    "responsibility": "responsibility", "职责": "responsibility",
    "corruption_risk": "corruption_risk", "贪腐风险": "corruption_risk",
    "effect_summary": "effect_summary", "效果摘要": "effect_summary",
    # 帝国修正（旧称遗产）子字段
    "legacy": "legacy", "帝国修正": "legacy", "遗产": "legacy",
    "duration": "duration", "时长": "duration",
    "modifiers": "modifiers", "修正": "modifiers",
    "narrative_hint": "narrative_hint", "叙事提示": "narrative_hint",
    # 帝国修正的 regions/armies 维度块（值是 {entity_id: {field: pct}}，原样透传）
    "regions": "regions", "地区": "regions",
    "armies": "armies", "军队": "armies",
    "action": "action", "动作": "action",
    "region_id": "region_id", "地区编号": "region_id",
    "building_id": "building_id", "建筑编号": "building_id",
    "category": "category", "类别": "category",
    "level": "level", "等级": "level",
    "condition": "condition", "完好": "condition",
    "maintenance": "maintenance", "维护费": "maintenance",
    "risk": "risk", "风险": "risk",
    "output_metric": "output_metric", "产出去向": "output_metric",
    "output_amount": "output_amount", "产出量": "output_amount",
    "applied_cost": "applied_cost", "已付代价": "applied_cost",
    "name": "name", "姓名": "name", "名称": "name",
    "new_office": "new_office", "新官职": "new_office",
    "new_office_type": "new_office_type", "新官署类别": "new_office_type",
    "faction": "faction", "派系": "faction",
    "status": "status", "状态": "status",
    "location": "location", "所在地": "location", "当前所在": "location",
    "office": "office", "位号": "office", "官职": "office",
    "office_type": "office_type", "官署类别": "office_type",
    "approved": "approved", "准许": "approved",
    "order_id": "order_id", "密令编号": "order_id",
    "sim_note": "sim_note", "推演备注": "sim_note",
    "result": "result", "结果": "result",
    "mode": "mode", "口径": "mode", "方式": "mode",
    "value": "value", "数值": "value", "目标": "value", "目标值": "value",
    "target": "value", "target_value": "value",
    "amount": "value", "月额": "value", "月支": "value",
    "formula": "formula", "公式": "formula", "计税公式": "formula",
    "basis": "basis", "计税依据": "basis", "税基": "basis",
    "rate_unit": "rate_unit", "税率单位": "rate_unit",
    "stance": "stance", "立场": "stance",
    "action": "action", "行动": "action",
    "impact": "impact", "影响": "impact",
    "intent": "intent", "意图": "intent",
    "satisfaction": "satisfaction", "满意": "satisfaction",
    "leverage": "leverage", "影响力": "leverage", "势力": "leverage",
    # new_armies 子字段（建军）
    "owner_power": "owner_power", "归属": "owner_power", "所属": "owner_power",
    # character_power_changes 子字段（人物易主）
    "new_power": "new_power", "新势力": "new_power",
    "station": "station", "驻扎地": "station", "驻地": "station",
    "theater": "theater", "战区": "theater",
    "commander": "commander", "统帅": "commander", "统将": "commander", "主将": "commander",
    "controller": "controller", "主管": "controller",
    "troop_type": "troop_type", "兵种": "troop_type",
    "manpower": "manpower", "人数": "manpower", "兵力": "manpower",
    "maintenance_per_turn": "maintenance_per_turn", "维护费": "maintenance_per_turn", "军费": "maintenance_per_turn",
    "supply": "supply", "补给": "supply", "粮饷": "supply",
    "morale": "morale", "士气": "morale",
    "training": "training", "训练": "training",
    "equipment": "equipment", "装备": "equipment",
    "arrears": "arrears", "欠饷": "arrears",
    "mobility": "mobility", "机动": "mobility",
    "loyalty": "loyalty", "忠诚": "loyalty",
}
ITEM_FIELD_LABELS = {
    "account": "账户",
    "delta": "增量",
    "category": "分类",
    "reason": "原因",
    "key": "键",
    "issue_id": "局势编号",
    "delta_bar": "进度增量",
    "stage_text": "阶段",
    "narrative": "叙述",
    "inertia_delta": "惯性增量",
    "origin_kind": "来源类型",
    "id": "编号",
    "kind": "类型",
    "title": "标题",
    "assignee": "承办人",
    "bar_value": "当前进度",
    "expected_months": "预计月数",
    "resolve_condition": "解决条件",
    "fail_condition": "失败条件",
    "ongoing_effects": "持续效果",
    "effect_on_resolve": "解决效果",
    "effect_on_fail": "失败效果",
    "cancellable": "可撤销",
    "metrics": "国势",
    "economy": "钱粮",
    "factions": "派系",
    "buildings": "建筑",
    "legacy": "帝国修正",
    "duration": "时长",
    "modifiers": "修正",
    "narrative_hint": "叙事提示",
    "action": "动作",
    "region_id": "地区编号",
    "level": "等级",
    "condition": "完好",
    "maintenance": "维护费",
    "risk": "风险",
    "output_metric": "产出去向",
    "output_amount": "产出量",
    "applied_cost": "已付代价",
    "name": "姓名",
    "new_office": "新官职",
    "new_office_type": "新官署类别",
    "faction": "派系",
    "status": "状态",
    "office": "位号",
    "office_type": "官署类别",
    "approved": "准许",
    "order_id": "密令编号",
    "sim_note": "推演备注",
    "result": "结果",
    "stance": "立场",
    "impact": "影响",
    "intent": "意图",
    "satisfaction": "满意",
    "leverage": "影响力",
}


def _table(rows: List[Dict[str, object]], cols: List[str]) -> Dict[str, object]:
    """array-of-dicts → header + 二维数组。省掉每行重复的 key，体积约为 dict 形式的 1/3。"""
    return {
        "cols": cols,
        "rows": [[r.get(c) for c in cols] for r in rows],
    }


def _auto_table(rows: List[Dict[str, object]]) -> Dict[str, object]:
    """同 _table，但自动取首行 keys。空列表返回空 cols/rows。"""
    if not rows:
        return {"cols": [], "rows": []}
    cols = list(rows[0].keys())
    return _table(rows, cols)


def _project_table(rows: List[Dict[str, object]], cols: List[str]) -> Dict[str, object]:
    """按指定列裁剪二维表；空表仍保留表头，给模型稳定前缀。"""
    return _table([{col: row.get(col, "") for col in cols} for row in rows], cols)


def _building_prompt_table(db: GameDB) -> Dict[str, object]:
    return _project_table(db.building_payload(), ["id", "region_id", "name"])


def _technology_prompt_table(db: GameDB) -> Dict[str, object]:
    return _project_table(db.technology_payload(), ["id", "name"])


def _preset_catalog(db: GameDB) -> Dict[str, object]:
    """喂给 simulator/extractor 的预设清单（key→name），让 LLM 命中预设时填对 key。
    只给 key/name；modifiers 由程序按 key 挂，不进 payload。"""
    return {
        "departments": [{"key": p.key, "name": p.name} for p in db.content.preset_departments.values()],
        "technologies": [{"key": p.key, "name": p.name} for p in db.content.preset_technologies.values()],
        "note": "新设衙门/科技若是清单内预设，部门/科技 create 填对应 key，程序自动挂永久国家修正；清单外自创不填 key。",
    }




def build_simulator_payload(
    state: GameState,
    db: GameDB,
    decree_text: str,
    previous_narrative: str,
    deaths_this_turn: Optional[List[Dict[str, str]]] = None,
    debuts_this_turn: Optional[List[Dict[str, str]]] = None,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
    structured_directives: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, object]:
    active = db.list_active_issues()
    assignee_names = sorted({
        str(r["assignee"] or "").strip()
        for r in active
        if "assignee" in r.keys() and str(r["assignee"] or "").strip()
    })
    issue_log_limit = _load_issue_log_limit()
    issues_payload = [
        issue_to_payload(row, db.list_issue_advances(int(row["id"]))[-issue_log_limit:] if issue_log_limit > 0 else [])
        for row in active
    ]
    assignee_names = sorted({
        str(row["assignee"] or "").strip()
        for row in active
        if "assignee" in row.keys() and str(row["assignee"] or "").strip()
    })
    issue_assignees = _auto_table([
        dict(r) for r in db.conn.execute(
            """
            SELECT name,office,office_type,status,ability,loyalty,integrity,courage,
                   diplomacy,martial,stewardship,intrigue,learning,
                   faction,personal_skills,style
            FROM characters
            WHERE name IN ({})
            ORDER BY name
            """.format(",".join("?" for _ in assignee_names)),
            assignee_names,
        ).fetchall()
    ]) if assignee_names else _auto_table([])
    # 帝国修正不进 simulator payload：它是纯机械的百分比修正符，由落账层自动放大/缩小增量，不进叙事。
    candidate_events = [
        {
            "id": ev.id,
            "title": ev.title,
            "kind": ev.kind,
            "summary": ev.summary,
            "event_type": ev.event_type,
            "interests": ev.interests,
            "audiences": ev.audiences,
            "urgency": int(ev.urgency),
            "severity": int(ev.severity),
            "credibility": int(ev.credibility),
            "is_historical": bool(ev.is_historical),
            "resolve_condition": ev.resolve_condition,
            "fail_condition": ev.fail_condition,
            "precondition": ev.precondition,
            "trigger_gate": ev.trigger_gate,
            "require": ev.require,
        }
        for ev in gather_candidate_events(state, db)
    ]
    region_rows = []
    for r in db.conn.execute(
        "SELECT name,kind,population,public_support,unrest,natural_disaster,"
        "human_disaster,controlled_by "
        "FROM regions ORDER BY id"
    ).fetchall():
        row = dict(r)
        region_rows.append(row)
    army_rows = [
        dict(r) for r in db.conn.execute(
            "SELECT name,station,theater,commander,controller,troop_type,manpower,"
            "maintenance_per_turn,supply,morale,training,equipment,arrears,mobility,"
            "loyalty,owner_power FROM armies WHERE active = 1 ORDER BY id"
        ).fetchall()
    ]
    court_roster = _auto_table([
        dict(r) for r in db.conn.execute(
            "SELECT name,office,office_type,faction,status,power_id,location FROM characters "
            "WHERE status!='offstage' AND office_type!='后宫' ORDER BY rowid"
        ).fetchall()
    ])
    return {
        "year": state.year,
        "period": state.period,
        "decree_text": decree_text,
        "structured_directives": structured_directives or [],
        "current_state": dict(state.metrics),
        "treasury_brief": db.treasury_report(state),
        "factions_brief": db.faction_report(),
        "classes_brief": db.class_report(),
        "powers_brief": db.power_report(exclude_self=True),
        "active_issues": issues_payload,
        "issue_assignees": issue_assignees,
        "candidate_events": candidate_events,
        "historical_anchor": historical_anchor_for_month(state.year, state.period),
        "victory_status": victory_status(db, state),
        "regions": _auto_table(region_rows),
        "armies": _auto_table(army_rows),
        # 每军每兵种实际持有军械件数 {军名:{兵种名:{武器名:件数}}}——AI 据此判「哪个兵种够装备多少人升级」
        # （兵种升级须有对应实物装备；拨给火炮队的火炮才能让火炮队升级，拨错兵种不算）。
        "army_held_arms": db.army_held_arms_all(),
        "buildings": _building_prompt_table(db),
        "departments": _auto_table(db.department_payload()),
        "technologies": _technology_prompt_table(db),
        "preset_catalog": _preset_catalog(db),
        "court_roster": court_roster,
        "deaths_this_turn": deaths_this_turn or [],
        "debuts_this_turn": debuts_this_turn or [],
        "relevant_memories": relevant_memories or [],
        "secret_orders": secret_orders or [],
        # HITL：本回合 simulator 最多应产出的重大决策点数（全局玩法设置，0=关闭 HITL 注入）。
        "hitl_min_decisions": _load_hitl_min_decisions(),
        "data_note": PAYLOAD_TABLE_NOTE + " 其余字段在本 JSON。secret_orders 独立于 relevant_memories。",
    }


def simulate_season_with_agno(
    agent: Agent,
    state: GameState,
    db: GameDB,
    decree_text: str,
    previous_narrative: str,
    deaths_this_turn: Optional[List[Dict[str, str]]] = None,
    debuts_this_turn: Optional[List[Dict[str, str]]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    on_text: Optional[Callable[[str], None]] = None,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
    structured_directives: Optional[List[Dict[str, object]]] = None,
) -> str:
    """推演 agent: 全量盘面塞 user payload，无 tool。"""
    narrative, _payload = simulate_season_with_payload(
        agent,
        state,
        db,
        decree_text,
        previous_narrative,
        deaths_this_turn=deaths_this_turn,
        debuts_this_turn=debuts_this_turn,
        on_thinking=on_thinking,
        on_text=on_text,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders,
        structured_directives=structured_directives,
    )
    return narrative


def simulate_season_with_payload(
    agent: Agent,
    state: GameState,
    db: GameDB,
    decree_text: str,
    previous_narrative: str,
    deaths_this_turn: Optional[List[Dict[str, str]]] = None,
    debuts_this_turn: Optional[List[Dict[str, str]]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    on_text: Optional[Callable[[str], None]] = None,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
    structured_directives: Optional[List[Dict[str, object]]] = None,
    simulator_payload: Optional[Dict[str, object]] = None,
) -> tuple[str, Dict[str, object]]:
    """推演 agent，同时返回本次推演 user payload，供 extractor 复用缓存前缀。"""
    payload = simulator_payload or build_simulator_payload(
        state, db, decree_text, previous_narrative,
        deaths_this_turn=deaths_this_turn,
        debuts_this_turn=debuts_this_turn,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders,
        structured_directives=structured_directives,
    )
    raw = run_agent_stream_text(
        agent,
        json.dumps({"instruction": "请根据 system 中的 simulator_payload 写本月月末奏章。"}, ensure_ascii=False),
        tag="simulator",
        on_thinking=on_thinking,
        on_text=on_text,
    )
    return raw.strip(), payload


EXTRACTION_MODULES = ("internal", "military_external", "issues", "personnel_secret")

EMPTY_EXTRACTION: Dict[str, object] = {
    "metric_delta": {},
    "economy_moves": [],
    "faction_delta": {},
    "class_delta": {},
    "region_delta": {},
    "army_delta": {},
    "new_armies": [],
    "arms_changes": {},
    "power_updates": {},
    "world_advance": {},
    "issue_advances": [],
    "new_issues": [],
    "cancels": [],
    "close_issues": [],
    "fiscal_changes": [],
    "fiscal_creates": [],
    "fiscal_removes": [],
    "office_changes": [],
    "character_changes": [],
    "appointments": [],
    "character_status_changes": [],
    "character_power_changes": [],
    "secret_order_updates": [],
    "secret_order_closes": [],
    "emperor_fate": None,  # 崇祯结局：abdicate(退位/禅让)/suicide(自尽/殉国)/null(无)
}

MODULE_FIELDS: Dict[str, set[str]] = {
    "internal": {"metric_delta", "economy_moves", "faction_delta", "class_delta", "region_delta", "fiscal_changes", "fiscal_creates", "fiscal_removes"},
    "military_external": {"army_delta", "new_armies", "arms_changes", "power_updates", "world_advance"},
    "issues": {"issue_advances", "new_issues", "cancels", "close_issues"},
    "personnel_secret": {
        "character_changes", "office_changes", "character_status_changes",
        "character_power_changes", "appointments",
        "secret_order_updates", "secret_order_closes", "emperor_fate",
    },
}


def _extractor_context_payload(
    db: GameDB,
    state: GameState,
    narrative: str,
    decree_text: str,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, object]:
    active = db.list_active_issues()

    issues_brief = [
        {
            "issue_id": int(r["id"]),
            "title": r["title"],
            "bar_value": int(r["bar_value"]),
            "inertia": int(r["inertia"]),
            "stage_text": r["stage_text"],
            "cancellable": r["cancellable"],
            "resolve_condition": (r["resolve_condition"] if "resolve_condition" in r.keys() else "") or "(未填)",
            "fail_condition": (r["fail_condition"] if "fail_condition" in r.keys() else "") or "(未填)",
            "assignee": (r["assignee"] if "assignee" in r.keys() else "") or "",
        }
        for r in active
    ]
    region_rows = [
        dict(r) for r in db.conn.execute(
            "SELECT id,name,kind,population,public_support,unrest,natural_disaster,"
            "human_disaster,controlled_by,"
            "json_extract(fiscal,'$.guan_min_tian') as guan_min_tian,"
            "json_extract(fiscal,'$.wang_tian') as wang_tian,"
            "json_extract(fiscal,'$.huang_tian') as huang_tian,"
            "json_extract(fiscal,'$.tian_fu_li') as tian_fu_li,"
            "json_extract(fiscal,'$.liao_xiang_li') as liao_xiang_li,"
            "json_extract(fiscal,'$.salt_tax') as salt_tax,"
            "json_extract(fiscal,'$.commerce_tax') as commerce_tax,"
            "json_extract(fiscal,'$.grain_output') as grain_output,"
            "json_extract(fiscal,'$.grain_stock') as grain_stock,"
            "json_extract(fiscal,'$.corruption') as corruption FROM regions ORDER BY id"
        ).fetchall()
    ]
    army_rows = [
        dict(r) for r in db.conn.execute(
            "SELECT id,name,station,theater,commander,controller,troop_type,manpower,"
            "maintenance_per_turn,supply,morale,training,equipment,arrears,mobility,"
            "loyalty FROM armies WHERE active = 1 ORDER BY id"
        ).fetchall()
    ]
    active_ministers = [
        dict(r) for r in db.conn.execute(
            "SELECT name,office,office_type,faction,power_id,location FROM characters WHERE status='active' ORDER BY rowid"
        ).fetchall()
    ]
    offstage_ministers = [
        dict(r) for r in db.conn.execute(
            "SELECT name,office,faction,power_id,location,debut_year,debut_month "
            "FROM characters WHERE status='offstage' ORDER BY name"
        ).fetchall()
    ]
    return {
        "turn": {"year": state.year, "period": state.period, "turn": state.turn},
        "narrative": narrative,
        "decree_text": decree_text,
        "active_issues": issues_brief,
        "candidate_events": [{"id": ev.id, "title": ev.title} for ev in gather_candidate_events(state, db)],
        "current_state": dict(state.metrics),
        "factions": db.faction_report(),
        "classes": db.class_report(),
        "powers": _auto_table(db.power_payload()),
        "regions": _auto_table(region_rows),
        "armies": _auto_table(army_rows),
        "buildings": _building_prompt_table(db),
        "departments": _auto_table(db.department_payload()),
        "technologies": _technology_prompt_table(db),
        "preset_catalog": _preset_catalog(db),
        "active_ministers": _auto_table(active_ministers),
        "offstage_ministers": _auto_table(offstage_ministers),
        "class_names": [r["name"] for r in db.conn.execute("SELECT DISTINCT name FROM classes ORDER BY name").fetchall()],
        "power_ids": [str(r["id"]) for r in db.conn.execute("SELECT id FROM powers").fetchall()],
        "fiscal_config": _prompt_fiscal_config(db),
        "region_fiscal_field_note": REGION_FISCAL_FIELD_NOTE,
        "economy_system": _economy_system_brief(db),
        "fiscal_reference": _fiscal_reference_lines(db),
        "relevant_memories": relevant_memories or [],
        "secret_orders": secret_orders or [],
        "_format_note": PAYLOAD_TABLE_NOTE + " extractor_context 中未剔除的补充表为 header+二维数组（cols 列名 + rows 数据）。",
    }


def _extractor_compat_payload(base: Dict[str, object]) -> Dict[str, object]:
    return {
        "turn": base["turn"],
        "narrative": base["narrative"],
        "decree_text": base["decree_text"],
        "active_issues": base["active_issues"],
        "candidate_events": base["candidate_events"],
        "current_state": base["current_state"],
        "factions": base["factions"],
        "classes": base["classes"],
        "powers": base["powers"],
        "regions": base["regions"],
        "armies": base["armies"],
        "buildings": base["buildings"],
        "active_ministers": base["active_ministers"],
        "offstage_ministers": base["offstage_ministers"],
        "class_names": base["class_names"],
        "power_ids": base["power_ids"],
        "fiscal_config": base["fiscal_config"],
        "region_fiscal_field_note": base["region_fiscal_field_note"],
        "economy_system": base["economy_system"],
        "fiscal_reference": base["fiscal_reference"],
        "relevant_memories": base["relevant_memories"],
        "secret_orders": base["secret_orders"],
        "_format_note": base["_format_note"],
    }


# module 模式专用：simulator_payload 已在同一 system 前缀给出全量盘面，这些字段同名同格式
# 重复，从补充上下文里剔除，省掉约一半 extractor system 体积。
_MODULE_DROP_FIELDS = (
    # 同名同格式，simulator_payload 已全量给出
    "regions", "armies", "army_held_arms", "buildings", "departments", "technologies", "preset_catalog", "current_state",
    "active_issues", "candidate_events", "decree_text",
    "relevant_memories", "secret_orders",
    # 异名但同源：simulator_payload 已有等价视图，extractor prompt（score_extractor_shared.md:17、
    # personnel_secret.md:5）明确指向从 simulator_payload 读，这里的副本是死字段。
    #   active_ministers → court_roster TSV（在朝大臣）
    #   powers → powers_brief（势力态势叙述）；new_power 合法集另有 power_ids
    #   factions → factions_brief；faction_delta key 是 7 个固定枚举，写死在 prompt
    #   classes → classes_brief；class_delta key 取 class_names + simulator_payload.regions 编号列
    "active_ministers", "powers", "factions", "classes",
)


def build_extractor_shared_context(
    db: GameDB,
    state: GameState,
    narrative: str,
    decree_text: str,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, object]:
    """供模块 extractor 放入 system 前缀的共同结算补充上下文。

    盘面（regions/armies/buildings/current_state/active_issues/candidate_events…）
    已由同 system 前缀的 simulator_payload 全量给出，这里剔除重复，只留 extractor 独有的
    校验集（class_names/power_ids/fiscal_config）+ offstage_ministers
    （court_roster 不含离场，任命查重需要）+ turn/narrative。在朝大臣/势力/派系/阶级
    （active_ministers/powers/factions/classes）也剔除——simulator_payload 已有等价视图，
    extractor prompt 指向从那读。"""
    base = _extractor_context_payload(
        db, state, narrative, decree_text,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders,
    )
    compat = _extractor_compat_payload(base)
    slim = {k: v for k, v in compat.items() if k not in _MODULE_DROP_FIELDS}
    slim["_dedup_note"] = (
        "盘面、诏书、在朝大臣、势力/派系/阶级态势已在 system 的 simulator_payload 中给出"
        "（盘面表 regions/armies/buildings 走 TSV；court_roster 即在朝大臣；"
        "powers_brief/factions_brief/classes_brief 即势力/派系/阶级），抽取时直接读 simulator_payload。"
        "本 extractor_context 只补：class_names/power_ids/fiscal_config 等校验元数据；"
        "地区/军队合法 id 直接取 simulator_payload 的 regions/armies 表编号列；"
        "fiscal_config、region_fiscal_field_note、economy_system/fiscal_reference（当前财政公式/月额）、"
        "offstage_ministers（离场名册，court_roster 不含，任命查重用）。"
        "局势 ongoing_effects 的常态月支由程序自动落账，extractor 不需要读取或重复抽取。"
    )
    return slim


def _payload_for_module(
    base: Dict[str, object],
    module: str,
) -> Dict[str, object]:
    _ = base
    if module not in MODULE_FIELDS:
        raise ValueError(f"未知 extractor module: {module}")
    return {
        "module": module,
        "module_allowed_fields": sorted(MODULE_FIELDS[module]),
        "instruction": "盘面（regions/armies/buildings/current_state/active_issues/candidate_events 等）看 system 的 simulator_payload；地区/军队合法 id 取 regions/armies 表编号列，extractor_context 只补 class_names/power_ids 等校验元数据。只输出当前模块允许的中文顶层字段 JSON object。",
    }


def _canonical_item_fields(value: object) -> object:
    if isinstance(value, list):
        return [_canonical_item_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        ITEM_FIELD_ALIASES.get(str(key).strip(), str(key).strip()): _canonical_item_fields(val)
        for key, val in value.items()
    }


def _canonicalize_extraction(data: Dict[str, object]) -> Dict[str, object]:
    canonical: Dict[str, object] = {}
    for raw_key, value in data.items():
        key = TOP_LEVEL_ALIASES.get(str(raw_key).strip(), str(raw_key).strip())
        canonical[key] = _canonical_item_fields(value)
    return canonical


_MAPPING_FIELDS = {
    "metric_delta",
    "faction_delta",
    "class_delta",
    "region_delta",
    "army_delta",
    "arms_changes",
    "power_updates",
    "world_advance",
}

_LIST_FIELDS = {
    "economy_moves",
    "new_armies",
    "issue_advances",
    "new_issues",
    "cancels",
    "close_issues",
    "fiscal_changes",
    "fiscal_creates",
    "fiscal_removes",
    "office_changes",
    "character_changes",
    "appointments",
    "character_status_changes",
    "character_power_changes",
    "secret_order_updates",
    "secret_order_closes",
}

_LIST_OF_MAPPING_FIELDS = _LIST_FIELDS - {"cancels", "close_issues"}

_REGION_DELTA_FIELDS = set(
    REGION_SCORE_FIELDS
    + REGION_QUANTITY_FIELDS
    + REGION_TEXT_FIELDS
    + FISCAL_SCORE_FIELDS
    + FISCAL_QUANTITY_FIELDS
) | {"reason"}
_ARMY_DELTA_FIELDS = set(ARMY_SCORE_FIELDS + ARMY_QUANTITY_FIELDS + ARMY_TEXT_FIELDS) | {"reason"}
_POWER_UPDATE_FIELDS = {"leverage", "military_strength", "supply", "reason"}


def _llm_contract_error(scope: str, message: str) -> None:
    raise LLMContractError(f"{scope}: {message}")


def _require_mapping(value: object, scope: str) -> Dict[str, object]:
    if not isinstance(value, dict):
        _llm_contract_error(scope, "expected object")
    return value


def _require_list(value: object, scope: str) -> List[object]:
    if not isinstance(value, list):
        _llm_contract_error(scope, "expected array")
    return value


def _validate_delta_mapping(
    value: object,
    *,
    scope: str,
    aliases: Dict[str, str],
    allowed_fields: set[str],
) -> None:
    container = _require_mapping(value, scope)
    for item_id, raw_delta in container.items():
        item_scope = f"{scope}.{item_id}"
        delta = _require_mapping(raw_delta, item_scope)
        for raw_field in delta:
            field = aliases.get(str(raw_field).strip(), str(raw_field).strip())
            if field not in allowed_fields:
                _llm_contract_error(item_scope, f"unknown nested field {raw_field!r}")


def validate_structured_extraction_payload(
    payload: Dict[str, object],
    *,
    module: str,
) -> Dict[str, object]:
    """Fail closed before sanitizer/reducer accepts LLM structured payloads."""
    if module not in MODULE_FIELDS:
        _llm_contract_error("structured extraction", f"unknown module {module!r}")
    if not isinstance(payload, dict):
        _llm_contract_error("structured extraction", "expected top-level object")

    canonical = _canonicalize_extraction(payload)
    allowed_top_level = MODULE_FIELDS[module]
    unknown_top_level = sorted(set(canonical) - allowed_top_level)
    if unknown_top_level:
        _llm_contract_error(
            "structured extraction",
            f"unknown top-level field(s): {', '.join(unknown_top_level)}",
        )

    for key, value in canonical.items():
        if key in _MAPPING_FIELDS:
            _require_mapping(value, key)
        elif key in _LIST_FIELDS:
            items = _require_list(value, key)
            if key in _LIST_OF_MAPPING_FIELDS:
                for index, item in enumerate(items):
                    _require_mapping(item, f"{key}[{index}]")
        elif key == "emperor_fate" and value is not None and not isinstance(value, str):
            _llm_contract_error(key, "expected string or null")

    if "region_delta" in canonical:
        _validate_delta_mapping(
            canonical["region_delta"],
            scope="region_delta",
            aliases=REGION_FIELD_ALIASES,
            allowed_fields=_REGION_DELTA_FIELDS,
        )
    if "army_delta" in canonical:
        _validate_delta_mapping(
            canonical["army_delta"],
            scope="army_delta",
            aliases=ARMY_FIELD_ALIASES,
            allowed_fields=_ARMY_DELTA_FIELDS,
        )
    if "power_updates" in canonical:
        _validate_delta_mapping(
            canonical["power_updates"],
            scope="power_updates",
            aliases=POWER_FIELD_ALIASES,
            allowed_fields=_POWER_UPDATE_FIELDS,
        )

    return payload


def _localized_item_fields(value: object, parent_key: str = "") -> object:
    if isinstance(value, list):
        return [_localized_item_fields(item, parent_key) for item in value]
    if not isinstance(value, dict):
        return value
    localized: Dict[str, object] = {}
    for key, val in value.items():
        key_str = str(key)
        if parent_key in {"world_advance", "后金", "蒙古", "朝鲜", "流寇"} and key_str == "action":
            label = "行动"
        else:
            label = ITEM_FIELD_LABELS.get(key_str, key_str)
        localized[label] = _localized_item_fields(val, key_str)
    return localized


def _localized_extraction(data: Dict[str, object]) -> Dict[str, object]:
    return {
        TOP_LEVEL_LABELS.get(str(key), str(key)): _localized_item_fields(value, str(key))
        for key, value in data.items()
    }


def _sanitize_module_output(
    module: str,
    data: Dict[str, object],
    fiscal_config: Optional[Dict[str, int]] = None,
) -> Dict[str, object]:
    allowed = MODULE_FIELDS[module]
    empty = {k: v for k, v in EMPTY_EXTRACTION.items() if k in allowed}
    if not isinstance(data, dict):
        return empty
    data = _canonicalize_extraction(data)
    cleaned = dict(empty)
    for key in allowed:
        if key in data:
            cleaned[key] = data[key]
    if module == "internal":
        cleaned["economy_moves"] = _clean_economy_moves(cleaned.get("economy_moves"))
        cleaned["fiscal_changes"] = _clean_fiscal_changes(
            cleaned.get("fiscal_changes"),
            fiscal_config=fiscal_config,
        )
        cleaned["fiscal_creates"] = _clean_fiscal_creates(cleaned.get("fiscal_creates"))
        cleaned["fiscal_removes"] = _clean_fiscal_removes(cleaned.get("fiscal_removes"))
    if module == "military_external":
        cleaned["world_advance"] = _clean_world_advance(cleaned.get("world_advance"))
    return cleaned


_PERSONNEL_NAME_FIELDS = (
    "character_changes",
    "office_changes",
    "character_status_changes",
    "character_power_changes",
)


def filter_unmentioned_personnel_changes(
    extracted: Dict[str, object],
    *,
    decree_text: str = "",
    narrative: str = "",
) -> Dict[str, object]:
    """Drop personnel changes whose name is absent from this turn's decree/report text."""
    if not isinstance(extracted, dict):
        return {}
    source_text = f"{decree_text}\n{narrative}"
    if not source_text.strip():
        return dict(extracted)

    cleaned = dict(extracted)
    for field in _PERSONNEL_NAME_FIELDS:
        items = cleaned.get(field)
        if not isinstance(items, list):
            continue
        kept: List[object] = []
        for item in items:
            if not isinstance(item, dict):
                kept.append(item)
                continue
            name = str(item.get("name") or item.get("姓名") or "").strip()
            if not name or name in source_text:
                kept.append(item)
            else:
                print(f"[WARN] extractor {field}: '{name}' 未见于本回合诏书/邸报，已过滤。")
        cleaned[field] = kept
    return cleaned


def _clean_world_advance(raw: object) -> Dict[str, str]:
    """Keep diplomacy as a compact power -> stance KV, tolerating the old verbose shape."""
    cleaned: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return cleaned
    for raw_key, raw_value in raw.items():
        key = str(raw_key).strip()
        if not key or key == "summary":
            continue
        if isinstance(raw_value, dict):
            value = (
                raw_value.get("stance")
                or raw_value.get("立场")
                or raw_value.get("attitude")
                or raw_value.get("态度")
                or ""
            )
        else:
            value = raw_value
        text = str(value).strip()
        if not text or text == "无新动":
            continue
        cleaned[key] = text[:40]
    return cleaned


_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _parse_cn_int(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return int(float(text))
    total = section = number = 0
    unit_seen = False
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in "十百千":
            unit_seen = True
            section += (number or 1) * {"十": 10, "百": 100, "千": 1000}[ch]
            number = 0
        elif ch == "万":
            unit_seen = True
            total += (section + number or 1) * 10000
            section = number = 0
        else:
            return None
    return total + section + number if unit_seen or number else None


def _economy_delta_in_wanliang(item: Dict[str, object]) -> float | None:
    raw_delta = item.get("delta")
    try:
        delta = float(raw_delta)
    except (TypeError, ValueError):
        return None
    unit = str(item.get("unit") or "").strip()
    text = " ".join(str(item.get(key) or "") for key in ("reason", "category"))
    combined = f"{unit} {text}"
    if re.search(r"(?<!万)两", combined) and not re.search(r"万两", combined):
        sign = -1 if delta < 0 else 1
        amount = abs(delta)
        match = re.search(r"([负\-]?[零〇一二两三四五六七八九十百千万\d.]+)\s*两", text)
        if match:
            raw_amount = match.group(1)
            parsed = _parse_cn_int(raw_amount.lstrip("负-"))
            if parsed is not None:
                amount = float(parsed)
                if raw_amount.startswith(("负", "-")):
                    sign = -1
        return sign * amount / 10000
    return delta


def _clean_economy_moves(raw: object) -> List[Dict[str, object]]:
    cleaned: List[Dict[str, object]] = []
    if not isinstance(raw, list):
        return cleaned
    for item in raw:
        if not isinstance(item, dict):
            continue
        item = _canonical_item_fields(item)
        if not isinstance(item, dict):
            continue
        account = str(item.get("account") or "").strip()
        if account not in {"国库", "内库"}:
            continue
        if "delta" not in item:
            continue
        delta = _economy_delta_in_wanliang(item)
        if delta is None:
            continue
        if delta == 0:
            continue
        entry: Dict[str, object] = {
            "account": account,
            "delta": delta,
            "category": str(item.get("category") or item.get("reason") or "事项")[:40],
            "reason": str(item.get("reason") or "")[:80],
        }
        purpose = str(item.get("purpose") or "").strip()
        if purpose:
            entry["purpose"] = purpose
        target_kind = str(item.get("target_kind") or "").strip()
        if target_kind:
            entry["target_kind"] = target_kind
        target_id = str(item.get("target_id") or "").strip()
        if target_id:
            entry["target_id"] = target_id
        cleaned.append(entry)
    return cleaned


def _clean_fiscal_changes(
    raw: object,
    fiscal_config: Optional[Dict[str, int]] = None,
) -> List[Dict[str, object]]:
    cleaned: List[Dict[str, object]] = []
    if not isinstance(raw, list):
        return cleaned
    for item in raw:
        if not isinstance(item, dict):
            continue
        item = _canonical_item_fields(item)
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        mode = str(item.get("mode") or "").strip()
        if not mode and key.startswith("宗室禄米_"):
            continue
        reason = str(item.get("reason") or "")[:120]
        if _fiscal_reason_has_conflicting_amount(key, reason, fiscal_config or {}):
            continue
        value_obj = item.get("value") if "value" in item else item.get("delta")
        if value_obj is None:
            continue
        try:
            value = float(value_obj)
        except (TypeError, ValueError):
            continue
        if value == 0 and (mode or "delta_value") != "set_value":
            continue
        entry: Dict[str, object] = {
            "key": key,
            "reason": reason,
        }
        if mode:
            entry["mode"] = mode
        if "value" in item:
            entry["value"] = value
        else:
            entry["delta"] = value
        cleaned.append(entry)
    return cleaned


_MONEY_CN_UNIT_RE = re.compile(r"([一二两三四五六七八九十百千万零〇\d.]+)\s*万?两")


def _cn_money_to_wan(text: str) -> Optional[float]:
    raw = text.strip()
    if not raw:
        return None
    if raw.endswith("万"):
        raw = raw[:-1]
    try:
        return float(raw)
    except ValueError:
        pass
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw in digits:
        return float(digits[raw])
    if raw == "十":
        return 10.0
    if raw.endswith("十") and raw[:-1] in digits:
        return float(digits[raw[:-1]] * 10)
    if "百" in raw:
        left, right = raw.split("百", 1)
        hundreds = digits.get(left, 1 if left == "" else 0)
        tail = _cn_money_to_wan(right) if right else 0
        return float(hundreds * 100 + int(tail or 0))
    if "十" in raw:
        left, right = raw.split("十", 1)
        tens = digits.get(left, 1 if left == "" else 0)
        ones = digits.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    return None


def _extract_money_after(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern + r"[^，。；、\d一二两三四五六七八九十百千万零〇]{0,16}" + _MONEY_CN_UNIT_RE.pattern, text)
    if not m:
        return None
    return _cn_money_to_wan(m.group(1))


def _fiscal_reason_has_conflicting_amount(key: str, reason: str, cfg: Dict[str, int]) -> bool:
    if not key.startswith("宗室禄米_"):
        return False
    current = round(int(cfg.get("宗室禄米_base", 0)) * int(cfg.get("宗室禄米_rate", 100)) / 100)
    target = _extract_money_after(r"(?:总额压至|总额减至|压至|减至|降至|定为)", reason)
    actual_cut = _extract_money_after(r"(?:实际减少|实际减|实减|仅减|只减)(?:仅|只|约|仅约)?", reason)
    if target is None or actual_cut is None:
        return False
    implied_cut = current - target
    return abs(implied_cut - actual_cut) > 2


_DIRECTION_NORMALIZE = {
    "income": "income", "收": "income", "收入": "income", "进账": "income",
    "expense": "expense", "支": "expense", "支出": "expense", "出账": "expense",
}


_FISCAL_CREATE_FORMULA_ALIASES = {
    "": "",
    "fixed": "",
    "固定月额": "",
    "per_basis": "per_basis",
    "按税基": "per_basis",
    "按计税依据": "per_basis",
    "按人头": "per_basis",
    "按田亩": "per_basis",
}


_FISCAL_CREATE_BASIS_ALIASES = {
    "population": "population",
    "人口": "population",
    "人丁": "population",
    "丁口": "population",
    "人头": "population",
    "registered_land": "registered_land",
    "在册田亩": "registered_land",
    "田亩": "registered_land",
    "登记田亩": "registered_land",
    "guan_min_tian": "guan_min_tian",
    "官民田": "guan_min_tian",
    "民田": "guan_min_tian",
    "wang_tian": "wang_tian",
    "藩王庄田": "wang_tian",
    "藩田": "wang_tian",
    "huang_tian": "huang_tian",
    "皇庄": "huang_tian",
    "hidden_land": "hidden_land",
    "隐田": "hidden_land",
}


def _clean_fiscal_creates(raw: object) -> List[Dict[str, object]]:
    """LLM 推演中凭空新立的月固定收支项（税是其一种）。完全放开——
    只做枚举校验：account∈{国库,内库}、direction∈{income,expense}、key 非空。
    税种／数值由 LLM 全权裁夺，代码不预设白名单。
    """
    cleaned: List[Dict[str, object]] = []
    if not isinstance(raw, list):
        return cleaned
    for item in raw:
        if not isinstance(item, dict):
            continue
        item = _canonical_item_fields(item)
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        account = str(item.get("account") or "").strip()
        if account not in ("国库", "内库"):
            continue
        direction = _DIRECTION_NORMALIZE.get(str(item.get("direction") or "").strip())
        if direction is None:
            continue
        try:
            init_value = int(item.get("init_value") or 0)
        except (TypeError, ValueError):
            init_value = 0
        display = str(item.get("display") or "").strip() or key.replace("_base", "")
        formula = _FISCAL_CREATE_FORMULA_ALIASES.get(str(item.get("formula") or "").strip())
        if formula is None:
            formula = ""
        basis = _FISCAL_CREATE_BASIS_ALIASES.get(str(item.get("basis") or "").strip(), "")
        if formula == "per_basis" and not basis:
            continue
        entry = {
            "key": key,
            "account": account,
            "direction": direction,
            "display": display,
            "init_value": max(0, init_value),
            "reason": str(item.get("reason") or "")[:120],
        }
        if formula:
            entry["formula"] = formula
            entry["basis"] = basis
            entry["rate_unit"] = str(item.get("rate_unit") or "")[:40]
        cleaned.append(entry)
    return cleaned


def _clean_fiscal_removes(raw: object) -> List[Dict[str, object]]:
    """LLM 推演中彻底裁撤 fiscal_config 现存月固定收支项。删项只需 key。
    田赋/辽饷/盐税/商税停征走地区财政字段，不走 fiscal_removes。
    """
    cleaned: List[Dict[str, object]] = []
    if not isinstance(raw, list):
        return cleaned
    for item in raw:
        if not isinstance(item, dict):
            continue
        item = _canonical_item_fields(item)
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        cleaned.append({
            "key": key,
            "reason": str(item.get("reason") or "")[:120],
        })
    return cleaned


def _merge_module_outputs(outputs: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    merged = dict(EMPTY_EXTRACTION)
    for module in EXTRACTION_MODULES:
        for key, val in outputs.get(module, {}).items():
            merged[key] = val
    return merged


def extract_scores_by_modules_with_agno(
    agents: Dict[str, Agent],
    db: GameDB,
    state: GameState,
    narrative: str,
    decree_text: str = "",
    sanitizer: Optional[Agent] = None,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
) -> tuple[Dict[str, object], str, str]:
    """四模块结算 extractor：内政财政、军务外势、局势、人事密令。

    调度：串行跑第 1 个模块把共享 system 前缀（game_world+simulator_context+extractor 契约）
    的 prompt 缓存建好（cache_creation），后 3 个模块再并行打过去——它们 system 前缀与第 1 个
    相同，在缓存 TTL 内并发命中，省墙钟又省 cache_creation。先暖后并发，不让 4 个一起 miss。"""
    base_payload = _extractor_context_payload(
        db, state, narrative, decree_text,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders,
    )
    fiscal_cfg = base_payload.get("fiscal_config") if isinstance(base_payload.get("fiscal_config"), dict) else None
    module_inputs: Dict[str, object] = {}
    # sanitizer 是共享单实例，解析失败（小概率）才用；并行下加锁串行化这一步，避免并发调用同一 agent。
    sanitizer_lock = threading.Lock()

    def _run_one(module: str) -> Dict[str, object]:
        agent = agents[module]
        payload = _payload_for_module(base_payload, module)
        module_inputs[module] = payload  # 各线程写不同 key，dict 单键赋值原子（GIL），安全
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=False)
        tlog(f"[extractor/{module}] user payload total={len(payload_json)} chars (~{len(payload_json)//1.5:.0f} tok)")
        raw = run_agent_text(agent, payload_json, tag=f"extractor/{module}")
        try:
            parsed = parse_agent_json(raw, f"结算抽取-{module}")
        except Exception as parse_err:
            if sanitizer is None:
                raise
            tlog(f"[extractor/{module}] 主输出解析失败：{parse_err}；调 sanitizer 重整")
            with sanitizer_lock:
                cleaned = run_agent_text(sanitizer, raw, tag=f"sanitizer/{module}")
            parsed = parse_agent_json(cleaned, f"结算抽取-{module}（sanitizer）")
        validate_structured_extraction_payload(parsed, module=module)
        return _sanitize_module_output(module, parsed, fiscal_config=fiscal_cfg)

    module_outputs: Dict[str, Dict[str, object]] = {}
    first, rest = EXTRACTION_MODULES[0], EXTRACTION_MODULES[1:]
    # 1) 串行暖缓存：第 1 个模块单独跑，建好共享 system 前缀的 prompt 缓存。
    tlog(f"[extractor] 串行暖缓存 module={first}")
    module_outputs[first] = _run_one(first)
    # 2) 并行：后 3 个模块共用已暖好的前缀缓存，并发命中。
    if rest:
        tlog(f"[extractor] 并行抽取 modules={list(rest)}")
        with ThreadPoolExecutor(max_workers=len(rest)) as pool:
            futures = {pool.submit(_run_one, m): m for m in rest}
            for fut in as_completed(futures):
                m = futures[fut]
                module_outputs[m] = fut.result()  # 子线程异常在此重抛，与串行同行为
    merged = _merge_module_outputs(module_outputs)
    merged = filter_unmentioned_personnel_changes(
        merged,
        decree_text=decree_text,
        narrative=narrative,
    )
    localized_merged = _localized_extraction(merged)
    trace_input = {
        "mode": "modular",
        "system_context_note": "模块 agent 的 system instructions 先注入稳定 game_world，再注入 simulator_payload 以复用推演缓存，随后是 extractor 公共契约、extractor_context 与模块提示词；module payload 只含模块名和允许字段。",
        "extractor_context": _extractor_compat_payload(base_payload),
        "modules": module_inputs,
    }
    return (
        merged,
        json.dumps(localized_merged, ensure_ascii=False, sort_keys=False),
        json.dumps(trace_input, ensure_ascii=False, sort_keys=False),
    )
