"""月末推演与打分提取：跑 simulator/extractor agent。L7。"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable, Dict, List, Optional

from agno.agent import Agent

from ming_sim.agents import parse_agent_json, run_agent_stream_text, run_agent_text
from ming_sim.context import historical_anchor_for_month, victory_status
from ming_sim.db import GameDB
from ming_sim.issues import gather_candidate_events, issue_to_payload
from ming_sim.models import Event, GameState
from ming_sim.token_stats import tlog


# ── 月末阈值危机事件检测 ───────────────────────────────────────────────────────

# 自动生成的阈值危机事件前缀（真实事件 ID 从 content.event_by_id 查）
_THRESHOLD_CRISIS_PREFIX = "threshold_critical_"


def _inject_threshold_crisis_events(db: GameDB, state: GameState) -> List[Event]:
    """检查省份 unrest > 80 或 国库 < 50，注入阈值危机候选事件。
    这些事件不写入 event_triggers，仅进入 candidate_events 列表，
    由 simulator agent 判定是否触发/转 issue。
    """
    injected: List[Event] = []

    # 1) 省份 unrest 超标
    region_rows = db.conn.execute(
        "SELECT id, name, unrest FROM regions WHERE unrest > 80"
    ).fetchall()
    for row in region_rows:
        region_id = str(row["id"])
        name = str(row["name"])
        unrest = int(row["unrest"])
        ev = Event(
            id=f"{_THRESHOLD_CRISIS_PREFIX}unrest_{region_id}",
            title=f"【阈值危机】{name}民变激化（unrest={unrest}）",
            kind="threshold_crisis",
            summary=f"{name}民怨沸腾，民变指数已突破 80，大明统治根基动摇。",
            urgency=90,
            severity=85,
            credibility=100,
            interests=["朝堂", "地方"],
            audiences=["皇帝", "内阁"],
            trigger_gate={"metrics.国威": ">0"},  # 始终通过，纯粹阈值触发
            event_type="situation",
        )
        injected.append(ev)

    # 2) 国库不足
    treasury = state.metrics.get("国库", 0)
    if treasury < 50:
        ev = Event(
            id=f"{_THRESHOLD_CRISIS_PREFIX}treasury",
            title=f"【阈值危机】国库空虚（仅剩{treasury}万两）",
            kind="threshold_crisis",
            summary=f"国库存银不足 50 万两，军队欠饷、官俸告急，朝廷财政濒临崩溃。",
            urgency=95,
            severity=90,
            credibility=100,
            interests=["朝堂", "军队", "宗室"],
            audiences=["皇帝", "户部"],
            trigger_gate={"metrics.国库": "<50"},
            event_type="situation",
        )
        injected.append(ev)

    return injected


TOP_LEVEL_ALIASES = {
    "国势变化": "metric_delta",
    "钱粮收支": "economy_moves",
    "财政制度变化": "fiscal_changes",
    "派系变化": "faction_delta",
    "阶级变化": "class_delta",
    "地区变化": "region_delta",
    "军队变化": "army_delta",
    "势力变化": "power_updates",
    "建军": "new_armies",
    "新建军队": "new_armies",
    "外交态度": "world_advance",
    "四方动向": "world_advance",
    "局势推进": "issue_advances",
    "新立局势": "new_issues",
    "撤销局势": "cancels",
    "结案局势": "close_issues",
    "人事变更": "office_changes",
    "人物状态变化": "character_status_changes",
    "人物易主": "character_power_changes",
    "后宫册封": "appointments",
    "密令副作用": "secret_order_updates",
    "密令结案": "secret_order_closes",
}
TOP_LEVEL_LABELS = {value: key for key, value in TOP_LEVEL_ALIASES.items()}

ITEM_FIELD_ALIASES = {
    "account": "account", "账户": "account",
    "delta": "delta", "增量": "delta",
    "category": "category", "分类": "category",
    "reason": "reason", "原因": "reason",
    "purpose": "purpose", "用途": "purpose",
    "target_kind": "target_kind", "目标类型": "target_kind",
    "target_id": "target_id", "目标编号": "target_id", "目标id": "target_id",
    "key": "key", "键": "key",
    "issue_id": "issue_id", "局势编号": "issue_id",
    "delta_bar": "delta_bar", "进度增量": "delta_bar",
    "stage_text": "stage_text", "阶段": "stage_text",
    "narrative": "narrative", "叙述": "narrative",
    "inertia_delta": "inertia_delta", "惯性增量": "inertia_delta",
    "origin_kind": "origin_kind", "来源类型": "origin_kind",
    "id": "id", "编号": "id",
    "kind": "kind", "类型": "kind",
    "title": "title", "标题": "title",
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
    "action": "action", "动作": "action",
    "region_id": "region_id", "地区编号": "region_id",
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
    "office": "office", "位号": "office", "官职": "office",
    "office_type": "office_type", "官署类别": "office_type",
    "approved": "approved", "准许": "approved",
    "order_id": "order_id", "密令编号": "order_id",
    "sim_note": "sim_note", "推演备注": "sim_note",
    "result": "result", "结果": "result",
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




def build_simulator_payload(
    state: GameState,
    db: GameDB,
    decree_text: str,
    directives_brief: List[Dict[str, object]],
    previous_narrative: str,
    fixed_flows: Optional[List[Dict[str, object]]] = None,
    deaths_this_turn: Optional[List[Dict[str, str]]] = None,
    debuts_this_turn: Optional[List[Dict[str, str]]] = None,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, object]:
    active = db.list_active_issues()
    issues_payload = [
        issue_to_payload(row, db.list_recent_issue_advances(int(row["id"]), 1))
        for row in active
    ]
    candidate_events = [
        {
            "id": ev.id,
            "title": ev.title,
            "kind": ev.kind,
            "summary": ev.summary,
            "interests": ev.interests,
            "is_historical": ev.trigger_year > 0,
            "resolve_condition": ev.resolve_condition,
            "fail_condition": ev.fail_condition,
            "precondition": ev.precondition,
        }
        for ev in gather_candidate_events(state, db)
    ]
    region_rows = [
        dict(r) for r in db.conn.execute(
            "SELECT name,kind,population,public_support,unrest,natural_disaster,"
            "human_disaster,registered_land,hidden_land,tax_per_turn,grain_security,"
            "gentry_resistance,military_pressure,status,"
            "json_extract(fiscal,'$.corruption') as corruption FROM regions ORDER BY id"
        ).fetchall()
    ]
    army_rows = [
        dict(r) for r in db.conn.execute(
            "SELECT name,station,theater,commander,controller,troop_type,manpower,"
            "maintenance_per_turn,supply,morale,training,equipment,arrears,mobility,"
            "loyalty,status,owner_power FROM armies ORDER BY id"
        ).fetchall()
    ]
    court_roster = [
        dict(r) for r in db.conn.execute(
            "SELECT name,office,office_type,faction,status,power_id,location FROM characters "
            "WHERE status!='offstage' AND office_type!='后宫' ORDER BY rowid"
        ).fetchall()
    ]
    return {
        "year": state.year,
        "period": state.period,
        "decree_text": decree_text,
        "directives": directives_brief,
        "current_state": dict(state.metrics),
        "treasury_brief": db.treasury_report(state),
        "factions_brief": db.faction_report(),
        "classes_brief": db.class_report(),
        "powers_brief": db.power_report(exclude_self=True),
        "active_issues": issues_payload,
        "candidate_events": candidate_events,
        "previous_narrative_tail": previous_narrative[-1500:] if previous_narrative else "",
        "historical_anchor": historical_anchor_for_month(state.year, state.period),
        "victory_status": victory_status(db, state),
        "regions": _auto_table(region_rows),
        "armies": _auto_table(army_rows),
        "buildings": _auto_table(db.building_payload()),
        "court_roster": court_roster,
        "fixed_flows": fixed_flows or [],
        "deaths_this_turn": deaths_this_turn or [],
        "debuts_this_turn": debuts_this_turn or [],
        "relevant_memories": relevant_memories or [],
        "secret_orders": secret_orders or [],
        "data_note": "regions/armies/buildings 均为 header+二维数组（cols 列名 + rows 数据）。secret_orders 为皇帝密令列表，独立于 relevant_memories，每条含 id/minister_name/title/content/status/result 字段。",
    }


def simulate_season_with_agno(
    agent: Agent,
    state: GameState,
    db: GameDB,
    decree_text: str,
    directives_brief: List[Dict[str, object]],
    previous_narrative: str,
    fixed_flows: Optional[List[Dict[str, object]]] = None,
    deaths_this_turn: Optional[List[Dict[str, str]]] = None,
    debuts_this_turn: Optional[List[Dict[str, str]]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    on_text: Optional[Callable[[str], None]] = None,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
) -> str:
    """推演 agent: 全量盘面塞 user payload，无 tool。"""
    narrative, _payload = simulate_season_with_payload(
        agent,
        state,
        db,
        decree_text,
        directives_brief,
        previous_narrative,
        fixed_flows=fixed_flows,
        deaths_this_turn=deaths_this_turn,
        debuts_this_turn=debuts_this_turn,
        on_thinking=on_thinking,
        on_text=on_text,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders,
    )
    return narrative


def simulate_season_with_payload(
    agent: Agent,
    state: GameState,
    db: GameDB,
    decree_text: str,
    directives_brief: List[Dict[str, object]],
    previous_narrative: str,
    fixed_flows: Optional[List[Dict[str, object]]] = None,
    deaths_this_turn: Optional[List[Dict[str, str]]] = None,
    debuts_this_turn: Optional[List[Dict[str, str]]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    on_text: Optional[Callable[[str], None]] = None,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
    simulator_payload: Optional[Dict[str, object]] = None,
) -> tuple[str, Dict[str, object]]:
    """推演 agent，同时返回本次推演 user payload，供 extractor 复用缓存前缀。"""
    payload = simulator_payload or build_simulator_payload(
        state, db, decree_text, directives_brief, previous_narrative,
        fixed_flows=fixed_flows,
        deaths_this_turn=deaths_this_turn,
        debuts_this_turn=debuts_this_turn,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders,
    )
    user_message = (
        f"【本回合年月】{state.year} 年 {state.period} 月（第 {state.turn} 回合）。"
        f"邸报抬头与正文涉及年月时以此为准。\n"
        + "【本回合推演输入 simulator_payload】\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=False)
    )
    raw = run_agent_stream_text(
        agent,
        user_message,
        tag="simulator",
        on_thinking=on_thinking,
        on_text=on_text,
    )
    return raw.strip(), payload


def extract_scores_with_agno(
    agent: Agent,
    db: GameDB,
    state: GameState,
    narrative: str,
    decree_text: str = "",
    sanitizer: Optional[Agent] = None,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
) -> tuple[Dict[str, object], str, str]:
    """结算 agent: 读邸报抽 JSON。"""
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
        }
        for r in active
    ]
    region_ids = [r["id"] for r in db.conn.execute("SELECT id FROM regions").fetchall()]
    army_ids = [r["id"] for r in db.conn.execute("SELECT id FROM armies").fetchall()]
    candidate_events = [
        {"id": ev.id, "title": ev.title}
        for ev in gather_candidate_events(state, db)
    ]
    region_rows = [
        dict(r) for r in db.conn.execute(
            "SELECT id,name,kind,population,public_support,unrest,natural_disaster,"
            "human_disaster,registered_land,hidden_land,tax_per_turn,grain_security,"
            "gentry_resistance,military_pressure,status,"
            "json_extract(fiscal,'$.corruption') as corruption FROM regions ORDER BY id"
        ).fetchall()
    ]
    army_rows = [
        dict(r) for r in db.conn.execute(
            "SELECT id,name,station,theater,commander,controller,troop_type,manpower,"
            "maintenance_per_turn,supply,morale,training,equipment,arrears,mobility,"
            "loyalty,status FROM armies ORDER BY id"
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
    payload = {
        "turn": {"year": state.year, "period": state.period, "turn": state.turn},
        "narrative": narrative,
        "decree_text": decree_text,
        "active_issues": issues_brief,
        "candidate_events": candidate_events,
        "current_state": dict(state.metrics),
        "factions": db.faction_report(),
        "classes": db.class_report(),
        "powers": _auto_table(db.power_payload()),
        "regions": _auto_table(region_rows),
        "armies": _auto_table(army_rows),
        "buildings": _auto_table(db.building_payload()),
        "active_ministers": _auto_table(active_ministers),
        "offstage_ministers": _auto_table(offstage_ministers),
        "region_ids": region_ids,
        "army_ids": army_ids,
        "class_names": [r["name"] for r in db.conn.execute("SELECT DISTINCT name FROM classes ORDER BY name").fetchall()],
        "power_ids": [str(r["id"]) for r in db.conn.execute("SELECT id FROM powers").fetchall()],
        "fiscal_config": db.get_fiscal_config(),
        "relevant_memories": relevant_memories or [],
        "secret_orders": secret_orders or [],
        "_format_note": "regions/armies/buildings/powers/active_ministers/offstage_ministers 均为 header+二维数组（cols 列名 + rows 数据）。secret_orders 独立字段，含 id/minister_name/title/content/status/result。",
    }
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=False)
    tlog(f"[extractor] user payload total={len(payload_json)} chars (~{len(payload_json)//1.5:.0f} tok)")
    raw = run_agent_text(agent, payload_json, tag="extractor")
    try:
        return parse_agent_json(raw, "结算抽取"), raw, payload_json
    except Exception as parse_err:
        if sanitizer is None:
            raise
        tlog(f"[extractor] 主输出解析失败：{parse_err}；调 sanitizer 重整")
        cleaned = run_agent_text(sanitizer, raw, tag="sanitizer")
        # 留痕用原始 raw（sanitizer 前），追查时能看到 extractor 真实吐了什么。
        return parse_agent_json(cleaned, "结算抽取（sanitizer）"), raw, payload_json


EXTRACTION_MODULES = ("internal", "military_external", "issues", "personnel_secret")

EMPTY_EXTRACTION: Dict[str, object] = {
    "metric_delta": {},
    "economy_moves": [],
    "faction_delta": {},
    "class_delta": {},
    "region_delta": {},
    "army_delta": {},
    "new_armies": [],
    "power_updates": {},
    "world_advance": {},
    "issue_advances": [],
    "new_issues": [],
    "cancels": [],
    "close_issues": [],
    "fiscal_changes": [],
    "office_changes": [],
    "appointments": [],
    "character_status_changes": [],
    "character_power_changes": [],
    "secret_order_updates": [],
    "secret_order_closes": [],
}

MODULE_FIELDS: Dict[str, set[str]] = {
    "internal": {"metric_delta", "economy_moves", "faction_delta", "class_delta", "region_delta", "fiscal_changes"},
    "military_external": {"army_delta", "new_armies", "power_updates", "world_advance"},
    "issues": {"issue_advances", "new_issues", "cancels", "close_issues"},
    "personnel_secret": {
        "office_changes", "character_status_changes", "character_power_changes", "appointments",
        "secret_order_updates", "secret_order_closes",
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
        }
        for r in active
    ]
    region_rows = [
        dict(r) for r in db.conn.execute(
            "SELECT id,name,kind,population,public_support,unrest,natural_disaster,"
            "human_disaster,registered_land,hidden_land,tax_per_turn,grain_security,"
            "gentry_resistance,military_pressure,status,"
            "json_extract(fiscal,'$.corruption') as corruption FROM regions ORDER BY id"
        ).fetchall()
    ]
    army_rows = [
        dict(r) for r in db.conn.execute(
            "SELECT id,name,station,theater,commander,controller,troop_type,manpower,"
            "maintenance_per_turn,supply,morale,training,equipment,arrears,mobility,"
            "loyalty,status FROM armies ORDER BY id"
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
        "buildings": _auto_table(db.building_payload()),
        "active_ministers": _auto_table(active_ministers),
        "offstage_ministers": _auto_table(offstage_ministers),
        "region_ids": [r["id"] for r in db.conn.execute("SELECT id FROM regions").fetchall()],
        "army_ids": [r["id"] for r in db.conn.execute("SELECT id FROM armies").fetchall()],
        "class_names": [r["name"] for r in db.conn.execute("SELECT DISTINCT name FROM classes ORDER BY name").fetchall()],
        "power_ids": [str(r["id"]) for r in db.conn.execute("SELECT id FROM powers").fetchall()],
        "fiscal_config": db.get_fiscal_config(),
        "relevant_memories": relevant_memories or [],
        "secret_orders": secret_orders or [],
        "_format_note": "regions/armies/buildings/powers/active_ministers/offstage_ministers 均为 header+二维数组（cols 列名 + rows 数据）。",
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
        "region_ids": base["region_ids"],
        "army_ids": base["army_ids"],
        "class_names": base["class_names"],
        "power_ids": base["power_ids"],
        "fiscal_config": base["fiscal_config"],
        "relevant_memories": base["relevant_memories"],
        "secret_orders": base["secret_orders"],
        "_format_note": base["_format_note"],
    }


def build_extractor_shared_context(
    db: GameDB,
    state: GameState,
    narrative: str,
    decree_text: str,
    relevant_memories: Optional[List[Dict[str, object]]] = None,
    secret_orders: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, object]:
    """供模块 extractor 放入 system 前缀的共同结算补充上下文。"""
    base = _extractor_context_payload(
        db, state, narrative, decree_text,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders,
    )
    return _extractor_compat_payload(base)


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
        "instruction": "simulator_payload 与 extractor_context 已在 system 中给出。只输出当前模块允许的中文顶层字段 JSON object。",
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


def _sanitize_module_output(module: str, data: Dict[str, object]) -> Dict[str, object]:
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
        cleaned["fiscal_changes"] = _clean_fiscal_changes(cleaned.get("fiscal_changes"))
    if module == "military_external":
        cleaned["world_advance"] = _clean_world_advance(cleaned.get("world_advance"))
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
        try:
            delta = int(item.get("delta"))
        except (TypeError, ValueError):
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


def _clean_fiscal_changes(raw: object) -> List[Dict[str, object]]:
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
        if not key or "delta" not in item:
            continue
        try:
            delta = int(item.get("delta"))
        except (TypeError, ValueError):
            continue
        if delta == 0:
            continue
        cleaned.append({
            "key": key,
            "delta": delta,
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
    """四模块结算 extractor：内政财政、军务外势、局势、人事密令。"""
    base_payload = _extractor_context_payload(
        db, state, narrative, decree_text,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders,
    )
    module_outputs: Dict[str, Dict[str, object]] = {}
    module_raw: Dict[str, str] = {}
    module_inputs: Dict[str, object] = {}
    for module in EXTRACTION_MODULES:
        agent = agents[module]
        payload = _payload_for_module(base_payload, module)
        module_inputs[module] = payload
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=False)
        tlog(f"[extractor/{module}] user payload total={len(payload_json)} chars (~{len(payload_json)//1.5:.0f} tok)")
        raw = run_agent_text(agent, payload_json, tag=f"extractor/{module}")
        module_raw[module] = raw
        try:
            parsed = parse_agent_json(raw, f"结算抽取-{module}")
        except Exception as parse_err:
            if sanitizer is None:
                raise
            tlog(f"[extractor/{module}] 主输出解析失败：{parse_err}；调 sanitizer 重整")
            cleaned = run_agent_text(sanitizer, raw, tag=f"sanitizer/{module}")
            parsed = parse_agent_json(cleaned, f"结算抽取-{module}（sanitizer）")
        module_outputs[module] = _sanitize_module_output(module, parsed)
    merged = _merge_module_outputs(module_outputs)
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
