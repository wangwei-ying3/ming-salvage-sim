"""Issue 系统：候选事件、issue 立项/推进/结案、tracker 输出落地、inertia 漂移。L6。

通过 bind_content() 注入 GameContent（取 EVENTS/SEED_EVENTS/EVENT_BY_ID）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, List, Optional

from ming_sim.constants import (
    TURN_UNIT, REGION_SCORE_FIELDS, ARMY_SCORE_FIELDS, FISCAL_SCORE_FIELDS,
    REGION_FIELD_ALIASES, ARMY_FIELD_ALIASES,
)
from ming_sim.content import GameContent
from ming_sim.context import victory_status
from ming_sim.db import GameDB, infer_office_type_from_office, normalize_office
from ming_sim.flows import (
    ISSUE_METRIC_KEYS,
    ISSUE_METRIC_LOCK_CAPS,
    _apply_class_dict,
    _apply_economy_list,
    _apply_faction_dict,
    _apply_metric_dict,
)
from ming_sim.models import Event, EventPhase, GameState

_content: Optional[GameContent] = None

# 给建筑/地区落库做 event 关联用的占位事件（issue 结案触发的副作用无真实 event）。
_ISSUE_PSEUDO_EVENT = Event(
    id="issue_resolution", title="局势结案", kind="月末", summary="",
    urgency=0, severity=0, credibility=100, interests=[], audiences=[],
)


def bind_content(content: GameContent) -> None:
    global _content
    _content = content


def _ctx() -> GameContent:
    if _content is None:
        raise RuntimeError("issues.bind_content() 未调用：GameContent 未注入。")
    return _content


def _apply_issue_buildings(
    db: GameDB,
    state: GameState,
    ops: object,
    pseudo_event: Event,
    reason: str,
) -> List[Dict[str, object]]:
    """落地 issue effect 里的 buildings 段：建筑随局势结案而新建/改数值/废止。

    每项 op 一个 dict，`action` ∈ create/modify/remove：
      - create：`region_id`/`name`/`category` 必填，其余可选（level/condition/maintenance/risk/output_metric/output_amount/status）
      - modify：`building_id` 必填 + 增量字段（走 apply_building_deltas）
      - remove：`building_id` 必填
    建筑的新建/变更唯一入口——不存在顶层 building_delta/new_buildings。
    """
    applied: List[Dict[str, object]] = []
    if not isinstance(ops, list):
        return applied
    for op in ops:
        if not isinstance(op, dict):
            continue
        action = str(op.get("action") or "").lower()
        try:
            if action == "create":
                bid = db.add_building(
                    state,
                    region_id=str(op.get("region_id") or ""),
                    name=str(op.get("name") or ""),
                    category=str(op.get("category") or ""),
                    level=int(op.get("level", 1)),
                    condition=int(op.get("condition", 60)),
                    maintenance=int(op.get("maintenance", 0)),
                    risk=int(op.get("risk", 30)),
                    output_metric=str(op.get("output_metric") or ""),
                    output_amount=int(op.get("output_amount", 0)),
                    status=str(op.get("status") or ""),
                    origin="issue",
                )
                applied.append({"action": "create", "building_id": bid,
                                 "name": str(op.get("name") or "")})
            elif action == "modify":
                bid = str(op.get("building_id") or "")
                fields = {k: v for k, v in op.items()
                          if k not in ("action", "building_id")}
                fields.setdefault("reason", reason)
                ch = db.apply_building_deltas(state, pseudo_event, None, "档房", {bid: fields})
                applied.append({"action": "modify", "building_id": bid, "changes": ch})
            elif action == "remove":
                bid = str(op.get("building_id") or "")
                ok = db.remove_building(state, bid, reason=reason)
                applied.append({"action": "remove", "building_id": bid, "removed": ok})
            else:
                print(f"[WARN] issue effect buildings: action 非法 '{action}'，跳过。")
        except Exception as exc:
            print(f"[WARN] issue effect buildings 落库失败：{exc}；op={op}")
    return applied


def issue_to_payload(row: sqlite3.Row, recent_advances: List[sqlite3.Row]) -> Dict[str, object]:
    """喂给推演 agent 的事项精简视图：状态、进度、效果、最近一次推进。"""
    keys = row.keys() if hasattr(row, "keys") else []
    resolve_cond = row["resolve_condition"] if "resolve_condition" in keys else ""
    fail_cond = row["fail_condition"] if "fail_condition" in keys else ""
    return {
        "issue_id": int(row["id"]),
        "kind": row["kind"],
        "title": row["title"],
        "状态": row["stage_text"],
        "进度": int(row["bar_value"]),
        "局势走向": int(row["inertia"]),
        f"当前每{TURN_UNIT}效果": json.loads(row["ongoing_effects"] or "{}"),
        "失败效果": json.loads(row["effect_on_fail"] or "{}"),
        "成功效果": json.loads(row["effect_on_resolve"] or "{}"),
        "结案条件": resolve_cond or "(未填)",
        "失败条件": fail_cond or "(未填)",
        "cancellable": row["cancellable"],
        f"上{TURN_UNIT}推进": (
            {
                "delta_bar": int(recent_advances[0]["delta_bar"]),
                "narrative": recent_advances[0]["narrative"],
            }
            if recent_advances else None
        ),
    }


def _spawned_event_refs(db: GameDB) -> set:
    refs: set = set()
    for r in db.conn.execute("SELECT origin_ref FROM issues WHERE origin_kind='event_pool'").fetchall():
        if r["origin_ref"]:
            refs.add(r["origin_ref"])
    for r in db.conn.execute("SELECT event_id FROM event_triggers").fetchall():
        if r["event_id"]:
            refs.add(r["event_id"])
    return refs


def _event_window_open(ev: Event, state: GameState) -> bool:
    """Return True when the current date is inside an event's optional trigger window."""
    if ev.trigger_year > 0:
        if state.year < ev.trigger_year:
            return False
        if state.year == ev.trigger_year and ev.trigger_month > 0 and state.period < ev.trigger_month:
            return False
    if ev.trigger_end_year > 0:
        if state.year > ev.trigger_end_year:
            return False
        if state.year == ev.trigger_end_year and ev.trigger_end_month > 0 and state.period > ev.trigger_end_month:
            return False
    return True


_GATE_AGG_FUNCS = {
    "max": max,
    "min": min,
    "sum": sum,
    "avg": lambda xs: sum(xs) // max(1, len(xs)),
}


def _eval_gate_key(key: str, metrics: Dict[str, int], db: GameDB) -> Optional[int]:
    """把 gate key 解析成一个 int 值。形式：
      - 'metric_name'                           → metrics[key]
      - 'region.<id>.<field>'                   → regions 表
      - 'region.<id1>|<id2>|.<field>.<agg>'     → 多省聚合 (max/min/avg/sum)
      - 'army.<id>.<field>' / 多军 + agg
      - 'building.<id>.<field>' / 多建筑 + agg
      - 'power.<id>.<field>' / 多 + agg
      - 'class.<name>.<field>'                  → classes 表全国汇总 (region_id='')
      - 'class.<name>@<region>.<field>'         → classes 表省级
      - 'class.<name>@<r1>|<r2>|.<field>.<agg>' → 多省同阶级聚合
    解析失败/数据缺失返回 None（gate 视为不通过，由调用方处理）。
    """
    if "." not in key:
        if key in metrics:
            return int(metrics[key])
        return None
    parts = key.split(".")
    table = parts[0]
    if table not in ("region", "army", "building", "power", "class", "faction"):
        return None
    # 末段可能是 agg，先抽出
    agg = None
    if parts[-1] in _GATE_AGG_FUNCS:
        agg = parts[-1]
        parts = parts[:-1]
    if len(parts) < 3:
        return None
    field = parts[-1]
    id_segment = ".".join(parts[1:-1])
    if table == "class" and "@" in id_segment and "|" in id_segment.split("@", 1)[1]:
        # 简写：class.<name>@<r1>|<r2>|<r3>.<field> → 展开成 [name@r1, name@r2, name@r3]
        cname, rest = id_segment.split("@", 1)
        ids = [f"{cname}@{r}" for r in rest.split("|") if r]
    else:
        ids = id_segment.split("|") if "|" in id_segment else [id_segment]
        ids = [x for x in ids if x]
    if not ids:
        return None
    # class 表的 id 是 name 或 name@region；其它表 id 就是行 id
    values: List[int] = []
    for cid in ids:
        row = None
        if table == "region":
            row = db.conn.execute(f"SELECT {field} FROM regions WHERE id = ?", (cid,)).fetchone()
        elif table == "army":
            row = db.conn.execute(f"SELECT {field} FROM armies WHERE id = ?", (cid,)).fetchone()
        elif table == "building":
            row = db.conn.execute(f"SELECT {field} FROM buildings WHERE id = ?", (cid,)).fetchone()
        elif table == "power":
            row = db.conn.execute(f"SELECT {field} FROM powers WHERE id = ?", (cid,)).fetchone()
        elif table == "faction":
            # factions 表主键是 name（中文，如 阉党），field 取 leverage/satisfaction
            row = db.conn.execute(f"SELECT {field} FROM factions WHERE name = ?", (cid,)).fetchone()
        elif table == "class":
            if "@" in cid:
                cname, rid = cid.split("@", 1)
            else:
                cname, rid = cid, ""
            row = db.conn.execute(
                f"SELECT {field} FROM classes WHERE name = ? AND region_id = ?",
                (cname, rid),
            ).fetchone()
        if row is None:
            return None
        try:
            values.append(int(row[0]))
        except (TypeError, ValueError):
            return None
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    if agg is None:
        # 多 id 但没指明聚合 → 默认 min（最严苛，要全部满足）
        agg = "min"
    return _GATE_AGG_FUNCS[agg](values)


def _eval_gate_key_str(key: str, db: GameDB) -> Optional[str]:
    """取一个文本型字段值（如 region.<id>.controlled_by → 'ming'/'houjin'）。
    仅支持单 id 的 region/army/power 文本字段；解析失败返回 None。
    """
    parts = key.split(".")
    if len(parts) != 3:
        return None
    table, cid, field = parts
    sql = {
        "region": f"SELECT {field} FROM regions WHERE id = ?",
        "army": f"SELECT {field} FROM armies WHERE id = ?",
        "power": f"SELECT {field} FROM powers WHERE id = ?",
    }.get(table)
    if sql is None:
        return None
    row = db.conn.execute(sql, (cid,)).fetchone()
    if row is None:
        return None
    return str(row[0])


def _gate_passed(gate: Dict[str, str], metrics: Dict[str, int], db: GameDB) -> bool:
    """trigger_gate 全部条件满足才返回 True。条件形如 '<=240'（数值）或 '==ming'（文本相等）。
    key 形式见 _eval_gate_key。
    """
    for key, cond in gate.items():
        cond = cond.strip()
        # 文本相等：==<word> / !=<word>（RHS 非纯数字）
        sm = re.match(r"^(==|!=)\s*(.+)$", cond)
        if sm and not re.match(r"^-?\d+$", sm.group(2).strip()):
            sop, sval = sm.group(1), sm.group(2).strip()
            cur = _eval_gate_key_str(key, db)
            if cur is None:
                return False
            if sop == "==" and cur != sval:
                return False
            if sop == "!=" and cur == sval:
                return False
            continue
        m = re.match(r"^(>=|<=|>|<|==)\s*(-?\d+)$", cond)
        if not m:
            return False
        op, num = m.group(1), int(m.group(2))
        val = _eval_gate_key(key, metrics, db)
        if val is None:
            return False
        if op == ">=" and not val >= num:
            return False
        if op == "<=" and not val <= num:
            return False
        if op == ">" and not val > num:
            return False
        if op == "<" and not val < num:
            return False
        if op == "==" and not val == num:
            return False
    return True


def gather_candidate_events(state: GameState, db: GameDB) -> List[Event]:
    """程序筛选：历史锚定事件按 trigger 时间到点、seed 情势按 trigger_gate 达标，
    都排除已触发过的。返回的候选清单交推演 agent 因果判定是否真触发。"""
    c = _ctx()
    spawned = _spawned_event_refs(db)
    candidates: List[Event] = []
    # 历史锚定 EVENTS：到点（含错过补出）即进候选
    for ev in c.events:
        if ev.id in spawned or ev.trigger_year <= 0:
            continue
        if not _event_window_open(ev, state):
            continue
        candidates.append(ev)
    # seed 情势：trigger_gate 阈值达标即进候选
    for ev in c.seed_events:
        if ev.id in spawned:
            continue
        # auto_trigger 事件只能由程序硬触发，绝不进 LLM 候选池
        if ev.auto_trigger:
            continue
        if not _event_window_open(ev, state):
            continue
        if _gate_passed(ev.trigger_gate, state.metrics, db):
            candidates.append(ev)
    return candidates


def auto_trigger_seed_issues(state: GameState, db: GameDB) -> List[Dict[str, object]]:
    """程序硬触发：seed_events 中标了 auto_trigger 的，trigger_gate 达标即由程序直接
    立 issue，绕过 LLM 因果判定（不进候选池等 extractor 决定）。event_to_issue 自带去重，
    已触发过返回 None 自动跳过。返回本回合硬触发的清单（供日志/邸报告知）。

    放在结算链 simulator 之前调用，使硬立的 issue 当回合即进盘面、被邸报叙述。"""
    c = _ctx()
    triggered: List[Dict[str, object]] = []
    for ev in c.seed_events:
        if not ev.auto_trigger:
            continue
        # trigger_gate 为空 = 开局即立的局势，只由 seed_opening_crises 立一次，绝不在此重立。
        # （空 gate 会被 _gate_passed 判为恒真，必须显式排除，否则每回合都试图重立。）
        if not ev.trigger_gate:
            continue
        if not _event_window_open(ev, state):
            continue
        if not _gate_passed(ev.trigger_gate, state.metrics, db):
            continue
        if ev.event_type != "situation":
            # 非 situation（node/ending）不转 issue，仅记触发避免重复
            if db.find_any_issue_by_origin("event_pool", ev.id) is None:
                db.mark_event_triggered(state, ev.id)
                triggered.append({"id": ev.id, "title": ev.title, "kind": ev.event_type})
            continue
        issue_id = event_to_issue(db, state, ev)
        if issue_id is not None:
            triggered.append({"id": ev.id, "title": ev.title, "issue_id": issue_id})
            print(f"[AUTO-TRIGGER] gate 达标硬立项 #{issue_id} {ev.title}（{ev.trigger_gate}）")
    return triggered


def _bar_ascii(value: int, width: int = 20) -> str:
    value = max(0, min(100, int(value)))
    pos = int(round(value / 100 * (width - 1)))
    return "●" + ("━" * pos) + "○" + ("━" * (width - 1 - pos))


def _format_issue_ongoing(ongoing_raw: str) -> str:
    """简短描述每月固定影响。"""
    try:
        eff = json.loads(ongoing_raw or "{}")
    except Exception:
        return ""
    parts: List[str] = []
    metrics = eff.get("metrics") or {}
    for key, val in metrics.items():
        if isinstance(val, (int, float)) and val:
            parts.append(f"{key}{'+' if val > 0 else ''}{int(val)}")
    for econ in eff.get("economy") or []:
        if isinstance(econ, dict):
            delta = econ.get("delta")
            acc = econ.get("account")
            if isinstance(delta, (int, float)) and delta and acc:
                parts.append(f"{acc}{'+' if delta > 0 else ''}{int(delta)}万")
    return "、".join(parts)


def _format_inertia(inertia: int) -> str:
    if inertia > 0:
        return f"自然推进 +{inertia}/{TURN_UNIT}"
    if inertia < 0:
        return f"自然恶化 {inertia}/{TURN_UNIT}"
    return "势均力敌"


def show_active_issues(db: GameDB) -> None:
    issues = db.list_active_issues()
    if not issues:
        return
    initiatives = [i for i in issues if i["kind"] == "initiative"]
    situations = [i for i in issues if i["kind"] == "situation"][:12]
    print(f"─── 待办事项 (系统 {len(situations)}/12  玩家 {len(initiatives)}/10) ───")

    def _print_row(row, label: str) -> None:
        bar = _bar_ascii(int(row["bar_value"]))
        print(f"{label} #{row['id']} {row['title']}")
        print(f"  {row['bar_bad_meaning']:6s} {bar} {row['bar_good_meaning']:6s}  bar={int(row['bar_value']):3d}  {row['stage_text']}")
        inertia = int(row["inertia"])
        ongoing_txt = _format_issue_ongoing(row["ongoing_effects"] or "{}")
        line_parts = [_format_inertia(inertia)]
        if ongoing_txt:
            line_parts.append(f"每{TURN_UNIT}固定：{ongoing_txt}")
        print(f"  {' | '.join(line_parts)}")

    for row in situations:
        cancel_tag = "不可撤" if row["cancellable"] == "never" else ("唯由进度" if row["cancellable"] == "by_progress" else "可撤旨")
        _print_row(row, f"[系统/{cancel_tag}]")
    for row in initiatives:
        _print_row(row, "[玩家/可撤旨]")
    print()


def event_to_issue(db: GameDB, state: GameState, ev: Event) -> Optional[int]:
    """把一个预设 event（EVENTS / SEED_EVENTS）落成一条 situation issue。供推演判定触发后调用。

    去重分两类：
    - 无 trigger_gate（开局局势）：查任意状态同源 issue，立过则永不重立。
    - 有 trigger_gate（条件触发危机）：只查 active 同源 issue，结案/撤销后 gate 再达标可重新触发。
    """
    if ev.trigger_gate:
        if db.find_active_issue_by_origin("event_pool", ev.id) is not None:
            return None
    else:
        if db.find_any_issue_by_origin("event_pool", ev.id) is not None:
            return None
    # 初值由 severity 推一个偏中性的 bar
    bar = max(20, min(60, 50 - int(ev.severity / 5)))
    # 默认 ongoing + inertia 五档（+10/+5/0/-5/-10），按 kind 取
    ongoing: Dict[str, object] = {}
    inertia = -5
    # 终结一锤子永久数值：达成（bar→100）落 effect_on_resolve，崩坏（bar→0 或 LLM 判失败）落
    # effect_on_fail。与 ongoing 过程效果区分——过程是每月漂移，终结是定局后的永久民心/皇威增减。
    polarity = "neg"  # neg=负面危机（平息回血/崩坏重创）；pos=正面机遇（把握加成/错失轻微）
    # 5 个原 metric（边防/民变/党争/执行/瞒报）已废除，ongoing_effects 按 kind 改用
    # 民心/皇威 或留空让 LLM 在推进时自定。结构性影响由 region/army/external/class delta 承担。
    if ev.kind in ("天灾", "灾情", "饥荒"):
        ongoing = {"metrics": {"民心": -2}, "economy": [{"account": "国库", "delta": -8, "category": "赈济损耗", "reason": ev.title}]}
        inertia = -10
    elif ev.kind in ("人祸", "兵变", "流寇", "民变", "抗税"):
        ongoing = {"metrics": {"民心": -2}}
        inertia = -10
    elif ev.kind in ("外族", "边事"):
        ongoing = {"metrics": {"皇威": -1}}
        inertia = -5
    elif ev.kind in ("党争", "朝议"):
        ongoing = {}
        inertia = -5
    elif ev.kind in ("丰收", "祥瑞", "民和"):
        ongoing = {"metrics": {"民心": 2}}
        inertia = +10
        polarity = "pos"
    elif ev.kind in ("友邦", "归附", "盟约"):
        ongoing = {"metrics": {"皇威": 1}}
        inertia = +5
        polarity = "pos"
    elif ev.kind in ("良策", "试点", "献宝", "科技"):
        inertia = +5
        polarity = "pos"
    elif ev.kind in ("战机", "敌乱"):
        ongoing = {"metrics": {"皇威": 1}}
        inertia = +10
        polarity = "pos"
    effect_resolve, effect_fail = _situation_terminal_effects(ev.kind, int(ev.severity), polarity)
    # 精调字段优先：合并自 opening_crises 的手调危机带 bar/ongoing/effect/meaning，直接用其值；
    # 缺省（0/空）则用上面按 severity/kind 推导的默认。
    if ev.bar_value:
        bar = ev.bar_value
    if ev.ongoing_effects:
        ongoing = ev.ongoing_effects
    if ev.issue_inertia:
        inertia = ev.issue_inertia
    if ev.effect_on_resolve:
        effect_resolve = ev.effect_on_resolve
    if ev.effect_on_fail:
        effect_fail = ev.effect_on_fail
    try:
        return db.insert_issue(
            state,
            kind="situation",
            title=ev.title,
            origin_kind="event_pool",
            origin_ref=ev.id,
            bar_value=bar,
            bar_good_meaning=ev.bar_good_meaning or "已平",
            bar_bad_meaning=ev.bar_bad_meaning or "失控",
            inertia=inertia,
            stage_text=ev.stage_text or ev.summary[:80],
            severity=int(ev.severity),
            region_hint=ev.region_hint,
            faction_hint=",".join(ev.interests[:2]),
            tags=ev.issue_tags or [ev.kind],
            ongoing_effects=ongoing,
            cancellable="never",
            effect_on_resolve=effect_resolve,
            effect_on_fail=effect_fail,
            resolve_condition=ev.resolve_condition,
            fail_condition=ev.fail_condition,
        )
    except Exception as exc:
        print(f"[WARN] 事件 {ev.title} 立项失败：{exc}；跳过。")
        return None


# 会崩坏的局势：人为可控、有明确「彻底失败」时刻——镇压不住/边镇沦陷/朝局崩坏。
# 它们 bar 能跌到 0、status 转 failed 终结，落 effect_on_fail 一锤子永久重创。
# 不在此集合的（天灾/饥荒等不可控天象、正面机遇）无失败态：bar 下限 1、永不 failed、
# effect_on_fail 留空，伤害全靠 ongoing_effects 持续累积。db.advance_issue 据 effect_on_fail
# 是否非空来判能否崩坏，故此处「会崩坏」与「非空 fail effect」必须一致。
_COLLAPSIBLE_KINDS = frozenset({
    "人祸", "兵变", "流寇", "民变", "抗税", "党争", "朝议", "外族", "边事",
})


def _situation_terminal_effects(kind: str, severity: int, polarity: str):
    """situation 终结一锤子永久效果。按 severity 推量级（轻 50 / 中 65 / 重 80）。
    resolve：达成（bar→100）落永久回血/加成，所有 situation 都有。
    fail：仅「会崩坏」局势（_COLLAPSIBLE_KINDS）有，崩坏（bar→0）落永久重创，幅度重于回血。
    民心/皇威由 kind 倾向决定（边事/外族偏皇威，灾害/民变偏民心，余者两者兼得）。"""
    mag = 1 if severity < 55 else (2 if severity < 70 else 3)
    if kind in ("外族", "边事", "友邦", "归附", "盟约", "战机", "敌乱"):
        axis = "皇威"
    elif kind in ("天灾", "灾情", "饥荒", "人祸", "兵变", "流寇", "民变", "抗税", "丰收", "祥瑞", "民和"):
        axis = "民心"
    else:
        axis = "both"

    def _metrics(amount: int) -> Dict[str, int]:
        if axis == "both":
            half = max(1, abs(amount) // 2)
            s = 1 if amount > 0 else -1
            return {"民心": s * half, "皇威": s * half}
        return {axis: amount}

    resolve_amt = (3 if polarity == "neg" else 4) * mag
    effect_resolve = {"metrics": _metrics(resolve_amt)}
    effect_fail = {"metrics": _metrics(-5 * mag)} if kind in _COLLAPSIBLE_KINDS else {}
    return effect_resolve, effect_fail


def _normalize_cancellable(raw: object) -> str:
    """LLM 偶发臆造 cancellable 值（by_policy 之类），归一到合法白名单。"""
    val = str(raw or "").strip().lower()
    if val in ("decree", "never", "by_progress"):
        return val
    # 常见臆造映射
    if val in ("by_policy", "policy"):
        return "decree"
    if val in ("none", "no", "false"):
        return "never"
    if val in ("yes", "true", "auto"):
        return "by_progress"
    return "by_progress"  # 默认


def _compute_inertia(ni: Dict[str, object]) -> int:
    """从 expected_months 算 inertia；兼容旧 inertia 直接填的写法。"""
    em_raw = ni.get("expected_months")
    if em_raw is not None:
        try:
            em = int(em_raw)
        except (TypeError, ValueError):
            em = 0
        if em != 0:
            inertia = round(100 / em)
            return max(-10, min(10, inertia))
    # 兼容旧字段
    return max(-10, min(10, int(ni.get("inertia") or 0)))


# 离散时长档：LLM 只能给这几档（防乱填）；映射到月。
_LEGACY_DURATION_MONTHS = {"1年": 12, "2年": 24, "永久": -1}
_LEGACY_ACCOUNT_KEYS = ("国库", "内库", "民心", "皇威")  # 全局可被 % 修正的四项
_LEGACY_PCT_CAP = 5  # 单条帝国修正对某维度的百分比上限，防幅度过大


def _clamp_pct(v: object) -> Optional[int]:
    try:
        pct = int(v)
    except (TypeError, ValueError):
        return None
    if pct == 0:
        return None
    return max(-_LEGACY_PCT_CAP, min(_LEGACY_PCT_CAP, pct))


def _spawn_legacy_from_effect(
    db: GameDB,
    state: GameState,
    effect: Dict[str, object],
    issue_id: int,
    issue_title: str,
) -> Optional[Dict[str, object]]:
    """结案 effect 里若带 legacy（帝国修正）段，落 legacies 表。返回落地摘要供日志。
    legacy schema:
      {"name": str,
       "duration": "1年"|"2年"|"永久",
       "modifiers": {                         # 各维度带符号百分比修正符
         "国库": +10, "内库": -5,                    # 账户增量
         "regions": {"shaanxi": {"unrest": -20}},   # 地区分数字段（仅 REGION_SCORE_FIELDS）
         "armies":  {"jizhou": {"morale": 15}}      # 军队分数字段（仅 ARMY_SCORE_FIELDS）
       },
       "narrative_hint": str}
    各 pct 带符号整数；落账时同维度累加，base>=0 ×(1+net/100)、base<0 ×(1-net/100)。
    缺字段/非法档/空 effect 一律跳过（不抛断）；地区/军队非法字段或不存在 id 由落账层忽略。
    """
    legacy = effect.get("legacy")
    if not isinstance(legacy, dict):
        return None
    name = str(legacy.get("name") or "").strip() or f"{issue_title}遗留"
    dur_key = str(legacy.get("duration") or "2年").strip()
    duration = _LEGACY_DURATION_MONTHS.get(dur_key)
    if duration is None:
        print(f"[WARN] legacy 时长档非法 '{dur_key}'，按 2年 处理。")
        duration = 24
    raw_eff = legacy.get("modifiers") or {}
    modifiers: Dict[str, object] = {}
    if isinstance(raw_eff, dict):
        for k in _LEGACY_ACCOUNT_KEYS:
            pct = _clamp_pct(raw_eff.get(k))
            if pct is not None:
                modifiers[k] = pct
        for scope, allowed, aliases in (
            ("regions", REGION_SCORE_FIELDS + FISCAL_SCORE_FIELDS, REGION_FIELD_ALIASES),
            ("armies", ARMY_SCORE_FIELDS, ARMY_FIELD_ALIASES),
        ):
            block = raw_eff.get(scope)
            if not isinstance(block, dict):
                continue
            scope_out: Dict[str, Dict[str, int]] = {}
            for entity_id, fields in block.items():
                if not isinstance(fields, dict):
                    continue
                fields_out: Dict[str, int] = {}
                for raw_field, v in fields.items():
                    field = aliases.get(str(raw_field).strip(), str(raw_field).strip())
                    if field not in allowed:
                        print(f"[INFO] legacy '{name}' {scope} 字段 '{raw_field}' 非法/不可修正，跳过。")
                        continue
                    pct = _clamp_pct(v)
                    if pct is not None:
                        fields_out[field] = pct
                if fields_out:
                    scope_out[str(entity_id)] = fields_out
            if scope_out:
                modifiers[scope] = scope_out
    if not modifiers:
        print(f"[INFO] legacy '{name}' 无有效 modifiers，跳过。")
        return None
    new_id = db.insert_legacy(
        state,
        name=name,
        modifiers=modifiers,
        narrative_hint=str(legacy.get("narrative_hint") or "")[:200],
        duration_months=duration,
        source_issue_id=issue_id,
    )
    summary = {
        "legacy_id": new_id, "name": name,
        "duration_months": duration, "modifiers": modifiers,
    }
    dur_label = "永久" if duration < 0 else f"{duration}月"
    print(f"[帝国修正] 局势#{issue_id}「{issue_title}」落「{name}」({dur_label}) {modifiers}")
    return summary


def apply_issue_tracker_output(
    db: GameDB,
    state: GameState,
    tracker_output: Dict[str, object],
) -> Dict[str, object]:
    touched_ids: set = set()
    applied_advances: List[Dict[str, object]] = []
    applied_new: List[Dict[str, object]] = []
    applied_cancels: List[Dict[str, object]] = []
    event_by_id = _ctx().event_by_id

    # 1) advances
    for adv in tracker_output.get("advances", []) or []:
        try:
            issue_id = int(adv.get("issue_id"))
        except (TypeError, ValueError):
            continue
        delta_bar = int(adv.get("delta_bar") or 0)
        inertia_delta = int(adv.get("inertia_delta") or 0)
        stage_text = str(adv.get("stage_text") or "")[:120]
        narrative = str(adv.get("narrative") or "")[:400]
        metric_delta_raw = adv.get("metric_delta") or {}
        applied_metrics = _apply_metric_dict(state, metric_delta_raw if isinstance(metric_delta_raw, dict) else {}, db=db)
        new_row = db.advance_issue(
            state, issue_id,
            trigger_kind="decree",
            delta_bar=delta_bar,
            stage_text=stage_text,
            narrative=narrative,
            metric_delta=applied_metrics,
            inertia_delta=inertia_delta,
        )
        if new_row is None:
            continue
        touched_ids.add(issue_id)
        # 终结结算：bar 自然推到 100/0 触发的 resolved/failed，与 close_issues 一样落终结效果（含建筑）
        if new_row["status"] == "resolved":
            effect = json.loads(new_row["effect_on_resolve"] or "{}")
            _apply_metric_dict(state, effect.get("metrics") or {}, db=db)
            _apply_economy_list(db, state, effect.get("economy") or [])
            _apply_faction_dict(db, effect.get("factions") or {})
            _apply_issue_buildings(db, state, effect.get("buildings"), _ISSUE_PSEUDO_EVENT, f"局势#{issue_id}结案")
            _spawn_legacy_from_effect(db, state, effect, issue_id, str(new_row["title"]))
        elif new_row["status"] == "failed":
            effect = json.loads(new_row["effect_on_fail"] or "{}")
            _apply_metric_dict(state, effect.get("metrics") or {}, db=db)
            _apply_economy_list(db, state, effect.get("economy") or [])
            _apply_faction_dict(db, effect.get("factions") or {})
            _apply_issue_buildings(db, state, effect.get("buildings"), _ISSUE_PSEUDO_EVENT, f"局势#{issue_id}失败")
            _spawn_legacy_from_effect(db, state, effect, issue_id, str(new_row["title"]))
        applied_advances.append({
            "issue_id": issue_id,
            "title": new_row["title"],
            "from_value": int(new_row["bar_value"]) - delta_bar,
            "to_value": int(new_row["bar_value"]),
            "stage_text": new_row["stage_text"],
            "status": new_row["status"],
            "narrative": narrative,
        })

    # 2) new_issues：接两种来源——
    #    decree     —— 玩家诏书强推，由 LLM 给字段新立 issue
    #    event_pool —— 预设事件（EVENTS/SEED_EVENTS）被推演判定触发，按预设 event 立 issue
    #    其它来源一律拒。
    initiative_active = db.count_active_initiatives()
    for ni in tracker_output.get("new_issues", []) or []:
        title = str(ni.get("title") or "")
        origin_kind = str(ni.get("origin_kind") or "").lower()
        if origin_kind == "event_pool":
            # 预设事件触发：id 必须是真实预设 event，照预设字段立 issue（不用 LLM 给的字段）
            event_id = str(ni.get("id") or ni.get("origin_ref") or "").strip()
            ev = event_by_id.get(event_id)
            if ev is None:
                print(f"[INFO] new_issue 已拒：event_pool id={event_id!r} 非预设事件，疑似臆造。")
                applied_new.append({"title": title or event_id, "rejected": True, "reason": "event_pool id 非预设事件"})
                continue
            if getattr(ev, "auto_trigger", False):
                # auto_trigger 事件只能程序硬触发，LLM 不准从候选池立项
                print(f"[INFO] new_issue 已拒：event {event_id} 标了 auto_trigger，只能程序硬触发。")
                applied_new.append({"title": ev.title, "rejected": True, "reason": "auto_trigger 事件仅程序可触发"})
                continue
            if ev.event_type != "situation":
                db.mark_event_triggered(state, ev.id)
                print(f"[INFO] new_issue 已拒：事件 {event_id} 为 {ev.event_type}，不转 issue。")
                applied_new.append({"title": ev.title, "rejected": False, "reason": f"event_type={ev.event_type} 已记为触发"})
                continue
            issue_id = event_to_issue(db, state, ev)
            if issue_id is None:
                applied_new.append({"title": ev.title, "rejected": True, "reason": "事件已触发过或落库失败"})
            else:
                applied_new.append({"issue_id": issue_id, "kind": "situation", "title": ev.title, "rejected": False})
            continue
        if origin_kind != "decree":
            print(f"[INFO] new_issue 已拒：'{title}'（origin_kind={origin_kind!r}，仅接 decree / event_pool）。")
            applied_new.append({"title": title, "rejected": True, "reason": "来源非 decree/event_pool 不许新立"})
            continue
        kind = str(ni.get("kind") or "initiative")
        if kind == "initiative" and initiative_active >= 10:
            applied_new.append({"title": title, "rejected": True, "reason": "已有十事在办，朝廷分身乏术，难再添新工。"})
            continue
        try:
            issue_id = db.insert_issue(
                state,
                kind=kind,
                title=title[:60] or "无名事项",
                origin_kind="decree",
                origin_ref=str(ni.get("origin_ref") or ""),
                bar_value=int(ni.get("bar_value", 25)),
                bar_good_meaning=str(ni.get("bar_good_meaning") or "已成"),
                bar_bad_meaning=str(ni.get("bar_bad_meaning") or "废止"),
                inertia=_compute_inertia(ni),
                stage_text=str(ni.get("stage_text") or "")[:120],
                severity=int(ni.get("severity") or 50),
                region_hint=str(ni.get("region_hint") or ""),
                faction_hint=str(ni.get("faction_hint") or ""),
                tags=list(ni.get("tags") or []),
                ongoing_effects=dict(ni.get("ongoing_effects") or {}),
                cancellable=_normalize_cancellable(ni.get("cancellable")),
                cancel_cost=dict(ni.get("cancel_cost") or {}),
                effect_on_resolve=dict(ni.get("effect_on_resolve") or {}),
                effect_on_fail=dict(ni.get("effect_on_fail") or {}),
                resolve_condition=str(ni.get("resolve_condition") or "")[:300],
                fail_condition=str(ni.get("fail_condition") or "")[:300],
            )
            if kind == "initiative":
                initiative_active += 1
            applied_new.append({"issue_id": issue_id, "kind": kind, "title": title, "rejected": False})
        except Exception as exc:
            print(f"[WARN] new_issue 落库失败：{exc}；跳过 {title}")

    # 3) closes（LLM 主动结案/失败，不看 bar 门槛）
    applied_closes: List[Dict[str, object]] = []
    for cl in tracker_output.get("close_issues", []) or []:
        try:
            issue_id = int(cl.get("issue_id"))
        except (TypeError, ValueError):
            continue
        reason = str(cl.get("reason") or "").strip().lower()
        if reason not in ("resolved", "failed"):
            print(f"[WARN] close_issues: reason 非法 '{reason}'，跳过 issue {issue_id}")
            continue
        narrative = str(cl.get("narrative") or "")[:400]
        try:
            new_row = db.close_issue(state, issue_id, reason=reason, narrative=narrative)
        except Exception as exc:
            print(f"[WARN] close_issue 落库失败：{exc}；跳过 issue {issue_id}")
            continue
        if new_row is None:
            continue
        touched_ids.add(issue_id)
        # 终结效果：以 issue 立项时预设的 effect 为底，叠加 LLM 在本次结案项 cl 里现给的 effect。
        # 现给优先——event_pool 预设 issue（如阉党之祸）立项时 effect 多为空，帝国修正只能结案当下给。
        if reason == "resolved":
            effect = json.loads(new_row["effect_on_resolve"] or "{}")
            cl_effect = cl.get("effect_on_resolve")
        else:
            effect = json.loads(new_row["effect_on_fail"] or "{}")
            cl_effect = cl.get("effect_on_fail")
        if isinstance(cl_effect, dict):
            # 浅合并：metrics/economy/factions/buildings/legacy 等顶层段，现给覆盖预设
            effect = {**effect, **cl_effect}
        _apply_metric_dict(state, effect.get("metrics") or {}, db=db)
        _apply_economy_list(db, state, effect.get("economy") or [])
        _apply_faction_dict(db, effect.get("factions") or {})
        building_ops = _apply_issue_buildings(
            db, state, effect.get("buildings"),
            _ISSUE_PSEUDO_EVENT, f"局势#{issue_id}{'结案' if reason == 'resolved' else '失败'}",
        )
        _spawn_legacy_from_effect(db, state, effect, issue_id, str(new_row["title"]))
        applied_closes.append({
            "issue_id": issue_id,
            "title": new_row["title"],
            "reason": reason,
            "narrative": narrative,
            "building_ops": building_ops,
        })

    # 4) cancels
    for cn in tracker_output.get("cancels", []) or []:
        try:
            issue_id = int(cn.get("issue_id"))
        except (TypeError, ValueError):
            continue
        row = db.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            continue
        if row["cancellable"] != "decree":
            # 不可撤：当作 advance 处理（皇威 -2）
            db.advance_issue(
                state, issue_id,
                trigger_kind="decree",
                delta_bar=0,
                stage_text=row["stage_text"],
                narrative=str(cn.get("narrative") or "陛下欲罢，然此事非诏可消。")[:400],
                metric_delta={"皇威": -2},
            )
            state.metrics["皇威"] = max(0, int(state.metrics.get("皇威", 0)) - 2)
            touched_ids.add(issue_id)
            applied_cancels.append({"issue_id": issue_id, "rejected": True, "title": row["title"]})
            continue
        # 可撤：应用 applied_cost
        cost = cn.get("applied_cost") or {}
        if isinstance(cost, dict):
            _apply_metric_dict(state, cost.get("metrics") or {}, db=db)
            _apply_economy_list(db, state, cost.get("economy") or [])
            _apply_faction_dict(db, cost.get("factions") or {})
        db.cancel_issue(
            state, issue_id,
            narrative=str(cn.get("narrative") or "")[:400],
            applied_cost=cost if isinstance(cost, dict) else {},
        )
        touched_ids.add(issue_id)
        applied_cancels.append({"issue_id": issue_id, "rejected": False, "title": row["title"]})

    state.clamp()
    return {
        "advances": applied_advances,
        "new_issues": applied_new,
        "closes": applied_closes,
        "cancels": applied_cancels,
        "touched_ids": sorted(touched_ids),
    }


# 独占实职关键词：office 分项以此结尾者视为「一人一缺」，须顶替去重。
# 群体职（大学士/侍郎/郎中/主事/御史/翰林等）不在内，可多员并存。
_EXCLUSIVE_OFFICE_SUFFIXES = (
    "首辅", "次辅", "尚书", "总督", "巡抚", "总兵", "督师", "经略", "提督",
)


def _is_exclusive_office(part: str) -> bool:
    """office 单个分项是否独占实职。南京XX为留都缺，与京职互不冲突，单独算一缺。"""
    return any(part.endswith(suf) for suf in _EXCLUSIVE_OFFICE_SUFFIXES)


def _displace_duplicate_offices(
    db: GameDB, content: Optional[GameContent], new_holder: str, new_office: str
) -> List[str]:
    """新任者 new_holder 拿到 new_office 后，把其中每个独占实职分项从其他 active 官员
    office 里剔除，避免双缺官。返回被腾出的 (旧任者:职) 描述列表。
    纯按 office 文字匹配——不依赖 court_role，对存量档同样生效。"""
    new_parts = [p for p in normalize_office(new_office).split(",") if _is_exclusive_office(p)]
    if not new_parts:
        return []
    displaced: List[str] = []
    rows = db.conn.execute(
        "SELECT name, office FROM characters WHERE status='active' AND power_id='ming' AND name!=?",
        (new_holder,),
    ).fetchall()
    for row in rows:
        holder_parts = [p.strip() for p in str(row["office"]).split(",") if p.strip()]
        kept = [p for p in holder_parts if p not in new_parts]
        if len(kept) == len(holder_parts):
            continue  # 此人不占同名独缺
        for lost in (p for p in holder_parts if p in new_parts):
            displaced.append(f"{row['name']}:{lost}")
        new_holder_office = ",".join(kept)
        db.conn.execute(
            "UPDATE characters SET office=? WHERE name=?",
            (new_holder_office, row["name"]),
        )
        if content is not None and row["name"] in content.characters:
            content.characters[row["name"]].office = new_holder_office
    db.conn.commit()
    return displaced


def apply_score_extraction(
    db: GameDB,
    state: GameState,
    extracted: Dict[str, object],
    content=None,
    registry=None,
) -> Dict[str, object]:
    """落地结算 agent 输出的 JSON 到 state 与 db。

    content/registry：若传入则处理 `appointments`——把诏书任命的新人建档入朝。
    缺省则跳过（向后兼容老调用）。"""
    # 1) metric_delta
    applied_metric = _apply_metric_dict(state, extracted.get("metric_delta") or {}, db=db)
    # 2) economy_moves
    applied_economy = _apply_economy_list(db, state, extracted.get("economy_moves") or [])
    # 3) faction_delta + class_delta（朝堂派系 + 社会阶级；联动靠 LLM，不在代码做）
    applied_factions = _apply_faction_dict(db, extracted.get("faction_delta") or {})
    applied_classes = _apply_class_dict(db, extracted.get("class_delta") or {})
    # 4) new_armies → region_delta / army_delta (复用旧 db 方法)
    region_deltas_raw = extracted.get("region_delta") or {}
    army_deltas_raw = extracted.get("army_delta") or {}
    new_armies_raw = extracted.get("new_armies") or []

    pseudo_event = Event(
        id="season",
        title="月末整体推演",
        kind="月末",
        summary="",
        urgency=0,
        severity=0,
        credibility=100,
        interests=[],
        audiences=[],
    )
    region_changes: List[Dict[str, object]] = []
    army_changes: List[Dict[str, object]] = []
    created_armies: List[Dict[str, object]] = []
    # 先建军：避免同回合 army_delta 引用新军被跳过
    if isinstance(new_armies_raw, list) and new_armies_raw:
        try:
            created_armies = db.create_armies_from_extraction(state, new_armies_raw, actor="档房")
        except Exception as exc:
            print(f"[WARN] new_armies 落库失败：{exc}")
    if isinstance(region_deltas_raw, dict) and region_deltas_raw:
        try:
            region_changes = db.apply_region_deltas(state, pseudo_event, None, "档房", region_deltas_raw)
        except Exception as exc:
            print(f"[WARN] region_delta 落库失败：{exc}")
    if isinstance(army_deltas_raw, dict) and army_deltas_raw:
        try:
            army_changes = db.apply_army_deltas(state, pseudo_event, None, "档房", army_deltas_raw)
        except Exception as exc:
            print(f"[WARN] army_delta 落库失败：{exc}")

    # 注：建筑的新建/变更/废止不走顶层字段，全由 issue 的 effect_on_resolve /
    #     effect_on_fail 里的 `buildings` 段在局势结案时落地（见 _apply_issue_buildings）。

    # 5) power_updates：非明势力三项简表（威望/实力/经济）落库
    power_updates_raw = extracted.get("power_updates") or {}
    power_changes: List[Dict[str, object]] = []
    if isinstance(power_updates_raw, dict) and power_updates_raw:
        try:
            power_changes = db.apply_power_deltas(state, power_updates_raw)
        except Exception as exc:
            print(f"[WARN] power_updates 落库失败：{exc}")

    # 6) issue_advances / new_issues / close_issues / cancels (复用旧 tracker 落地)
    issue_summary = apply_issue_tracker_output(db, state, {
        "advances": extracted.get("issue_advances") or [],
        "new_issues": extracted.get("new_issues") or [],
        "close_issues": extracted.get("close_issues") or [],
        "cancels": extracted.get("cancels") or [],
    })

    # 7) fiscal_changes：调整月度固定收支系数
    applied_fiscal: List[Dict[str, object]] = []
    for change in extracted.get("fiscal_changes") or []:
        key = str(change.get("key") or "")
        try:
            delta = int(change.get("delta") or 0)
        except (TypeError, ValueError):
            continue
        if not key or delta == 0:
            continue
        current = db.get_fiscal_config().get(key)
        if current is None:
            print(f"[WARN] fiscal_changes: 未知 key '{key}'，跳过。")
            continue
        new_val = max(0, current + delta)
        db.set_fiscal_config(key, new_val)
        applied_fiscal.append({
            "key": key, "old": current, "new": new_val, "delta": delta,
            "reason": str(change.get("reason") or ""),
        })

    # 8) appointments：仅收「后宫纳妃」（office_type=后宫）。朝臣的新任/调任已统一
    #    并入 office_changes（section 10），LLM 误把朝臣塞这里的项一律转去 office_changes 处理。
    applied_appointments: List[Dict[str, object]] = []
    spillover_office_changes: List[Dict[str, object]] = []
    if content is not None:
        from ming_sim.session import apply_appointment  # 延迟导入避循环
        for item in extracted.get("appointments") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("office_type") or "").strip() != "后宫":
                # 朝臣项转交 office_changes：name + new_office 形态
                spillover_office_changes.append({
                    "name": str(item.get("name") or ""),
                    "new_office": str(item.get("office") or ""),
                    "faction": str(item.get("faction") or "中立"),
                    "reason": str(item.get("reason") or ""),
                })
                continue
            name, displaced = apply_appointment(db, state, content, registry, item)
            if name:
                applied_appointments.append({
                    "name": name,
                    "office": str(item.get("office") or ""),
                    "faction": str(item.get("faction") or "中立"),
                    "reason": str(item.get("reason") or ""),
                    "displaced": displaced,
                })
            else:
                rejected_name = str(item.get("name") or "").strip()
                if rejected_name:
                    applied_appointments.append({
                        "name": rejected_name,
                        "office": str(item.get("office") or ""),
                        "rejected": True,
                        "reason": str(item.get("reason") or ""),
                        "approved": bool(item.get("approved", True)),
                    })

    # 9) character_status_changes：LLM 判定的既有大臣去向（罢/狱/流/致仕/死）
    applied_status_changes: List[Dict[str, object]] = []
    valid_status = {"dismissed", "imprisoned", "exiled", "retired", "dead", "offstage"}
    for item in extracted.get("character_status_changes") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        reason = str(item.get("reason") or "").strip()
        if not name or status not in valid_status:
            applied_status_changes.append({
                "name": name, "status": status, "rejected": True,
                "reason": "name 空 或 status 非白名单",
            })
            continue
        if content is not None and name not in content.characters:
            applied_status_changes.append({
                "name": name, "status": status, "rejected": True,
                "reason": "非既有大臣或妃嫔（新任走 appointments）",
            })
            continue
        cur_status, _ = db.get_character_status(name)
        if cur_status != "active":
            applied_status_changes.append({
                "name": name, "status": status, "rejected": True,
                "reason": f"当前非 active（{cur_status}）",
            })
            continue
        try:
            db.set_character_status(state, name, status, reason)
        except Exception as exc:
            applied_status_changes.append({
                "name": name, "status": status, "rejected": True, "reason": f"落库失败：{exc}",
            })
            continue
        # 同步 content 内存对象：去职即削职，与 db 清空 office 保持一致
        if content is not None and name in content.characters:
            ch = content.characters[name]
            ch.status = status
            if status in {"dismissed", "imprisoned", "exiled", "retired", "dead"}:
                ch.office = ""
        applied_status_changes.append({
            "name": name, "status": status, "reason": reason,
        })

    # 9b) character_power_changes：人物易主（降将/叛臣/归正）
    applied_power_changes: List[Dict[str, object]] = []
    try:
        applied_power_changes = db.apply_character_power_changes(
            extracted.get("character_power_changes") or []
        )
    except Exception as exc:
        print(f"[WARN] character_power_changes 落库失败：{exc}")

    # 10) office_changes：朝臣官职变更——统一吃「新任（建档）」与「调任（改职）」。
    #     extractor 不再分新任/调任，代码按 name 在不在册自判：
    #       在册且未死 → 任命/调任；不在册 → 建新档。
    #     后宫纳妃仍走 appointments（语义不同，见 section 8）。
    applied_office_changes: List[Dict[str, object]] = []
    if content is not None:
        from ming_sim.session import apply_appointment  # 延迟导入避循环
    # office_changes 本体 + 从 appointments 转来的朝臣项（spillover）
    office_change_items = list(extracted.get("office_changes") or []) + spillover_office_changes
    for item in office_change_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        new_office = str(item.get("new_office") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not name or not new_office:
            applied_office_changes.append({
                "name": name, "new_office": new_office, "rejected": True,
                "reason": "name 或 new_office 空",
            })
            continue
        in_roster = content is not None and name in content.characters
        cur_status = db.get_character_status(name)[0] if in_roster else ""
        if in_roster:
            if cur_status == "dead":
                applied_office_changes.append({
                    "name": name, "new_office": new_office, "rejected": True,
                    "reason": "人物已故，不能重新启用",
                })
                continue
            # ── 在册任命/调任：改回 active 并授官 ──
            new_type = str(item.get("new_office_type") or "").strip()
            old_office = content.characters[name].office
            try:
                if cur_status != "active":
                    db.set_character_status(state, name, "active", reason[:200] or "诏书任命")
                db.set_character_office(name, new_office, new_type, source=reason[:60] or "诏书调任")
            except Exception as exc:
                applied_office_changes.append({
                    "name": name, "new_office": new_office, "rejected": True,
                    "reason": f"落库失败：{exc}",
                })
                continue
            # 独缺顶替兜底：按 office 文字去重。新任者拿到的每个独占实职分项，
            # 从其他 active 官员 office 里剔除同名分项（LLM 已判去重，此处仅防漏抽旧任者出现双缺官）。
            displaced_parts = _displace_duplicate_offices(db, content, name, new_office)
            ch = content.characters[name]
            ch.status = "active"
            ch.office = normalize_office(new_office)
            ch.office_type = infer_office_type_from_office(ch.office, new_type or ch.office_type)
            if registry is not None:
                registry.refresh(name)
            applied_office_changes.append({
                "name": name, "old_status": cur_status, "old_office": old_office, "new_office": new_office,
                "kind": "transfer", "reason": reason,
                **({"displaced": displaced_parts} if displaced_parts else {}),
            })
            continue
        # ── 新任：建新档（apply_appointment 对在册者会拒，故仅不在册走到这）──
        if content is None:
            continue
        appt = {
            "name": name, "office": new_office,
            "faction": str(item.get("faction") or "中立"),
            "reason": reason, "approved": True,
        }
        appointed, displaced = apply_appointment(db, state, content, registry, appt)
        if appointed:
            applied_office_changes.append({
                "name": appointed, "new_office": new_office,
                "kind": "appoint", "displaced": displaced, "reason": reason,
            })
        else:
            applied_office_changes.append({
                "name": name, "new_office": new_office, "rejected": True,
                "kind": "appoint",
                "reason": f"建档失败（查重/字段不合）；原 status={cur_status or '不在册'}",
            })

    # 11) secret_order_updates：推演写 active 密令副作用（泄漏/反弹）到 sim_note。结案不走这里。
    applied_secret_orders: List[Dict[str, object]] = []
    for item in extracted.get("secret_order_updates") or []:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("order_id")
        sim_note = str(item.get("sim_note") or item.get("result") or "").strip()
        if raw_id is None or not sim_note:
            applied_secret_orders.append({"order_id": raw_id, "rejected": True,
                                          "reason": "order_id 或 sim_note 缺失"})
            continue
        try:
            real_id = int(raw_id)
        except (TypeError, ValueError):
            applied_secret_orders.append({"order_id": raw_id, "rejected": True, "reason": "order_id 非整数"})
            continue
        try:
            db.update_secret_order_sim_note(
                real_id, sim_note, year=state.year, period=state.period
            )
            print(f"[secret_order] 推演副作用 id={real_id} note={sim_note[:60]!r}")
            applied_secret_orders.append({"order_id": real_id, "sim_note": sim_note})
        except Exception as exc:
            applied_secret_orders.append({"order_id": real_id, "rejected": True, "reason": str(exc)})

    # 12) secret_order_closes：推演给 pending_review 密令最终判定（done/failed），落库结案。
    applied_secret_closes: List[Dict[str, object]] = []
    for item in extracted.get("secret_order_closes") or []:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("order_id")
        status = str(item.get("status") or "").strip().lower()
        result_text = str(item.get("result") or "").strip()
        if status not in {"done", "failed"}:
            applied_secret_closes.append({"order_id": raw_id, "rejected": True,
                                          "reason": f"status 必须 done/failed，得到 {status!r}"})
            continue
        if raw_id is None or not result_text:
            applied_secret_closes.append({"order_id": raw_id, "rejected": True,
                                          "reason": "order_id 或 result 缺失"})
            continue
        try:
            real_id = int(raw_id)
        except (TypeError, ValueError):
            applied_secret_closes.append({"order_id": raw_id, "rejected": True, "reason": "order_id 非整数"})
            continue
        # 仅 pending_review 状态才允许结案；active 不能跳级，done/failed 已结案不重复
        order = db.get_secret_order(real_id)
        if order is None:
            applied_secret_closes.append({"order_id": real_id, "rejected": True, "reason": "密令不存在"})
            continue
        if order["status"] != "pending_review":
            applied_secret_closes.append({"order_id": real_id, "rejected": True,
                                          "reason": f"当前状态 {order['status']}，非 pending_review，不予结案"})
            continue
        try:
            db.close_secret_order(real_id, status, result_text, state.turn)
            print(f"[secret_order] 推演结案 id={real_id} status={status} result={result_text[:60]!r}")
            applied_secret_closes.append({"order_id": real_id, "status": status, "result": result_text})
        except Exception as exc:
            applied_secret_closes.append({"order_id": real_id, "rejected": True, "reason": str(exc)})

    state.clamp()
    return {
        "metric_delta": applied_metric,
        "economy_moves": applied_economy,
        "faction_delta": applied_factions,
        "class_delta": applied_classes,
        "region_changes": region_changes,
        "army_changes": army_changes,
        "created_armies": created_armies,
        "power_changes": power_changes,
        "issue_summary": issue_summary,
        "world_advance": extracted.get("world_advance") or {},
        "fiscal_changes": applied_fiscal,
        "appointments": applied_appointments,
        "character_status_changes": applied_status_changes,
        "character_power_changes": applied_power_changes,
        "office_changes": applied_office_changes,
        "secret_order_updates": applied_secret_orders,
        "secret_order_closes": applied_secret_closes,
        "victory_status": victory_status(db, state),
    }


def apply_issue_inertia_and_ongoing(
    db: GameDB,
    state: GameState,
    touched_ids: Optional[set] = None,
) -> None:
    # inertia 是每月自然漂移基础量，对所有进行中 issue 都生效（含本月被 advance 触动的）。
    # advance 的 delta_bar 是皇帝本月实旨推动的额外量，与 inertia 叠加，互不顶替。
    _ = touched_ids  # 保留入参不破坏调用方；inertia 漂移不再按它跳过
    active = db.list_active_issues()
    # 累计单月 metric 落账，用于上限 clamp
    period_metric_acc: Dict[str, int] = {}

    for row in active:
        issue_id = int(row["id"])
        bar = int(row["bar_value"])
        inertia = int(row["inertia"])

        # 1) inertia 漂移：每月对所有进行中 issue 都走一格
        if inertia != 0:
            new_bar = max(0, min(100, bar + inertia))
            actual = new_bar - bar
            if actual != 0:
                new_row = db.advance_issue(
                    state, issue_id,
                    trigger_kind="inertia",
                    delta_bar=actual,
                    stage_text=row["stage_text"],
                    narrative="局势自有其势，本月按其本然推移。",
                    metric_delta={},
                )
                if new_row is None:
                    continue
                if new_row["status"] == "resolved":
                    effect = json.loads(new_row["effect_on_resolve"] or "{}")
                    _apply_metric_dict(state, effect.get("metrics") or {}, db=db)
                    _apply_economy_list(db, state, effect.get("economy") or [])
                    _apply_faction_dict(db, effect.get("factions") or {})
                    _apply_issue_buildings(db, state, effect.get("buildings"), _ISSUE_PSEUDO_EVENT, f"局势#{issue_id}结案")
                    continue
                elif new_row["status"] == "failed":
                    effect = json.loads(new_row["effect_on_fail"] or "{}")
                    _apply_metric_dict(state, effect.get("metrics") or {}, db=db)
                    _apply_economy_list(db, state, effect.get("economy") or [])
                    _apply_faction_dict(db, effect.get("factions") or {})
                    _apply_issue_buildings(db, state, effect.get("buildings"), _ISSUE_PSEUDO_EVENT, f"局势#{issue_id}失败")
                    continue
                row = db.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
                if row is None:
                    continue
                bar = int(row["bar_value"])

        # 2) ongoing_effects：bar 高时折扣
        ongoing = json.loads(row["ongoing_effects"] or "{}")
        if not ongoing:
            continue
        # 折扣系数：bar 越高（越好）越少扣
        # bar=0~40 → 100%, bar=40~80 → 60%, bar=80~100 → 30%
        if bar >= 80:
            scale = 0.3
        elif bar >= 40:
            scale = 0.6
        else:
            scale = 1.0

        # metrics
        metric_part: Dict[str, int] = {}
        for k, v in (ongoing.get("metrics") or {}).items():
            if k not in ISSUE_METRIC_KEYS:
                continue
            try:
                raw = int(v)
            except (TypeError, ValueError):
                continue
            scaled = int(round(raw * scale))
            if scaled == 0:
                continue
            cap = ISSUE_METRIC_LOCK_CAPS.get(k, 5)
            already = period_metric_acc.get(k, 0)
            remaining = cap - abs(already)
            if remaining <= 0:
                continue
            if scaled > 0:
                allowed = min(scaled, remaining)
            else:
                allowed = max(scaled, -remaining)
            if allowed == 0:
                continue
            state.metrics[k] = int(state.metrics.get(k, 0)) + allowed
            period_metric_acc[k] = already + allowed
            metric_part[k] = allowed

        # economy
        economy_part = _apply_economy_list(db, state, ongoing.get("economy") or [])

        if metric_part or economy_part:
            db.conn.execute(
                """
                INSERT INTO issue_advances (
                    issue_id, turn, trigger_kind, delta_bar,
                    from_value, to_value, narrative, metric_delta
                ) VALUES (?, ?, 'ongoing', 0, ?, ?, ?, ?)
                """,
                (
                    issue_id, state.turn, bar, bar,
                    f"持续效果落账 (折扣 {int(scale*100)}%)",
                    json.dumps({"metrics": metric_part, "economy": economy_part}, ensure_ascii=False),
                ),
            )
            db.conn.commit()

    state.clamp()


# ── 开局负面帝国修正：不立 issue、不进推演，靠 clear_gate 程序判定消除 ──────────────

def clear_gated_legacies(db: GameDB, state: GameState) -> List[str]:
    """每月调一次：取所有 active 且带 clear_gate 的 legacy，gate 达标即置 'cleared'。
    返回被消除的 legacy 名称列表（供叙事/提示用，不强制使用）。"""
    rows = db.conn.execute(
        "SELECT id, name, clear_gate, narrative_hint FROM legacies "
        "WHERE status='active' AND clear_gate != '' AND clear_gate != '{}'"
    ).fetchall()
    cleared: List[str] = []
    for row in rows:
        try:
            gate = json.loads(str(row["clear_gate"] or "{}"))
        except (ValueError, TypeError):
            gate = {}
        if not gate:
            continue
        if _gate_passed(gate, state.metrics, db):
            db.conn.execute("UPDATE legacies SET status='cleared' WHERE id=?", (int(row["id"]),))
            cleared.append(str(row["name"]))
    if cleared:
        db.conn.commit()
        db._legacy_mod_cache = None  # active 集变了，修正符缓存失效
    return cleared


def sync_opening_legacies(db: GameDB, state: GameState) -> None:
    """开局负面帝国修正落库/校准。新档与读档都调（在 session.__init__ load_state 之后）：
    - 已达 clear_gate：不补；若残留 active 则置 cleared。
    - 未达标：该 legacy_key 不存在 active 行则 insert（永久 duration=-1，仅靠 gate 消除）。
    一个函数覆盖新档（全补）/旧档（补缺）/达标档（不补/清残）。"""
    for leg in _ctx().opening_legacies:
        passed = _gate_passed(leg.clear_gate, state.metrics, db)
        existing = db.conn.execute(
            "SELECT id FROM legacies WHERE legacy_key=? AND status='active'",
            (leg.key,),
        ).fetchone()
        if passed:
            if existing is not None:
                db.conn.execute(
                    "UPDATE legacies SET status='cleared' WHERE legacy_key=? AND status='active'",
                    (leg.key,),
                )
                db.conn.commit()
                db._legacy_mod_cache = None
            continue
        # 未达标且无 active 行 → 补上
        if existing is None:
            db.insert_legacy(
                state,
                name=leg.name,
                modifiers=leg.modifiers,
                narrative_hint=leg.narrative_hint,
                duration_months=-1,
                clear_gate=leg.clear_gate,
                legacy_key=leg.key,
            )


# ════════════════════════════════════════════════════════════════
# 事件阶段推进 + 叙事收件箱
# ════════════════════════════════════════════════════════════════

def advance_event_phases(db, state):
    """检查 active issue 是否有预设阶段需要推进。"""
    c = _ctx()
    active = db.list_active_issues()
    advanced = []
    for row in active:
        origin_ref = str(row["origin_ref"] or "")
        if not origin_ref:
            continue
        ev = c.event_by_id.get(origin_ref)
        if ev is None or not ev.phases:
            continue
        try:
            current_idx = int(str(row["phase"] or "0"))
        except (TypeError, ValueError):
            current_idx = 0
        if current_idx >= len(ev.phases) - 1:
            continue
        last_advance = int(row["last_advance_turn"] or 0)
        turns_in_phase = state.turn - last_advance
        current_phase = ev.phases[current_idx]
        if turns_in_phase < current_phase.duration_min:
            continue
        if turns_in_phase < current_phase.duration_max and current_phase.advance_condition:
            continue
        next_idx = current_idx + 1
        next_phase = ev.phases[next_idx]
        phase_label = str(next_phase.title or next_phase.id)
        narrative = str(next_phase.narrative or "")
        db.conn.execute(
            "UPDATE issues SET phase=?, stage_text=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(next_idx), narrative[:200] if narrative else phase_label, int(row["id"])),
        )
        db.conn.commit()
        db.advance_issue(
            state, int(row["id"]),
            trigger_kind="phase",
            delta_bar=0,
            stage_text=narrative[:200] or phase_label,
            narrative=f"事件推进至「{phase_label}」：{narrative}" if narrative else f"事件推进至「{phase_label}」",
            metric_delta={},
        )
        advanced.append({"issue_id": int(row["id"]), "title": str(row["title"]),
                         "phase_from": str(current_idx), "phase_to": str(next_idx),
                         "phase_label": phase_label, "narrative": narrative})
    return advanced


def record_event_narrative(db, state, applied, decree_text="", narrative=""):
    """将本回合事件变化写入 event_narratives 表。"""
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS event_narratives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            turn INTEGER NOT NULL, year INTEGER NOT NULL, period INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'system', title TEXT NOT NULL DEFAULT '',
            narrative TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
            ref_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    issue_summary = applied.get("issue_summary") or {}
    for ni in issue_summary.get("new_issues", []):
        if ni.get("rejected"):
            continue
        title = str(ni.get("title") or "")
        db.conn.execute(
            "INSERT INTO event_narratives (turn, year, period, kind, title, narrative) VALUES (?, ?, ?, 'new_issue', ?, ?)",
            (state.turn, state.year, state.period, title, f"新局势：{title}"),
        )
    for cl in issue_summary.get("closes", []):
        title = str(cl.get("title") or "")
        reason = str(cl.get("reason") or "resolved")
        label = "已结案" if reason == "resolved" else "已崩坏"
        narrative_text = str(cl.get("narrative") or "")
        db.conn.execute(
            "INSERT INTO event_narratives (turn, year, period, kind, title, narrative) VALUES (?, ?, ?, 'close_issue', ?, ?)",
            (state.turn, state.year, state.period, title, f"{label}：{narrative_text}"),
        )
    db.conn.commit()


def get_event_inbox(db, limit=20):
    """获取事件叙事收件箱。"""
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS event_narratives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            turn INTEGER NOT NULL, year INTEGER NOT NULL, period INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'system', title TEXT NOT NULL DEFAULT '',
            narrative TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
            ref_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    rows = db.conn.execute(
        "SELECT * FROM event_narratives ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [{
        "id": int(r["id"]), "turn": int(r["turn"]), "year": int(r["year"]),
        "period": int(r["period"]), "kind": str(r["kind"]), "title": str(r["title"]),
        "narrative": str(r["narrative"]), "source": str(r["source"]),
    } for r in rows]
