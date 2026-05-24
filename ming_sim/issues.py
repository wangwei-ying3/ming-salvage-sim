"""Issue 系统：候选事件、issue 立项/推进/结案、tracker 输出落地、inertia 漂移。L6。

通过 bind_content() 注入 GameContent（取 EVENTS/SEED_EVENTS/EVENT_BY_ID）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, List, Optional

from ming_sim.constants import TURN_UNIT
from ming_sim.content import GameContent
from ming_sim.context import victory_status
from ming_sim.db import GameDB
from ming_sim.flows import (
    ISSUE_METRIC_KEYS,
    ISSUE_METRIC_LOCK_CAPS,
    _apply_class_dict,
    _apply_economy_list,
    _apply_faction_dict,
    _apply_metric_dict,
)
from ming_sim.models import Event, GameState

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
    return refs


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
      - 'external.<id>.<field>' / 多 + agg
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
    if table not in ("region", "army", "building", "external", "class"):
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
        elif table == "external":
            row = db.conn.execute(f"SELECT {field} FROM external_powers WHERE id = ?", (cid,)).fetchone()
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


def _gate_passed(gate: Dict[str, str], metrics: Dict[str, int], db: GameDB) -> bool:
    """trigger_gate 全部条件满足才返回 True。条件形如 '<=240'。
    key 形式见 _eval_gate_key。
    """
    for key, cond in gate.items():
        m = re.match(r"^(>=|<=|>|<|==)\s*(-?\d+)$", cond.strip())
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
        if state.year < ev.trigger_year:
            continue
        if state.year == ev.trigger_year and ev.trigger_month > 0 and state.period < ev.trigger_month:
            continue
        candidates.append(ev)
    # seed 情势：trigger_gate 阈值达标即进候选
    for ev in c.seed_events:
        if ev.id in spawned:
            continue
        if _gate_passed(ev.trigger_gate, state.metrics, db):
            candidates.append(ev)
    return candidates


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
    """把一个预设 event（EVENTS / SEED_EVENTS）落成一条 situation issue。
    供推演判定触发后调用。已触发过（origin_ref 命中）则跳过返回 None。"""
    if db.find_any_issue_by_origin("event_pool", ev.id) is not None:
        return None
    # 初值由 severity 推一个偏中性的 bar
    bar = max(20, min(60, 50 - int(ev.severity / 5)))
    # 默认 ongoing + inertia 五档（+10/+5/0/-5/-10），按 kind 取
    ongoing: Dict[str, object] = {}
    inertia = -5
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
    elif ev.kind in ("友邦", "归附", "盟约"):
        ongoing = {"metrics": {"皇威": 1}}
        inertia = +5
    elif ev.kind in ("良策", "试点", "献宝", "科技"):
        inertia = +5
    elif ev.kind in ("战机", "敌乱"):
        ongoing = {"metrics": {"皇威": 1}}
        inertia = +10
    try:
        return db.insert_issue(
            state,
            kind="situation",
            title=ev.title,
            origin_kind="event_pool",
            origin_ref=ev.id,
            bar_value=bar,
            bar_good_meaning="已平",
            bar_bad_meaning="失控",
            inertia=inertia,
            stage_text=ev.summary[:80],
            severity=int(ev.severity),
            region_hint="",
            faction_hint=",".join(ev.interests[:2]),
            tags=[ev.kind],
            ongoing_effects=ongoing,
            cancellable="never",
            resolve_condition=ev.resolve_condition,
            fail_condition=ev.fail_condition,
        )
    except Exception as exc:
        print(f"[WARN] 事件 {ev.title} 立项失败：{exc}；跳过。")
        return None


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
        applied_metrics = _apply_metric_dict(state, metric_delta_raw if isinstance(metric_delta_raw, dict) else {})
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
        # 终结结算
        if new_row["status"] == "resolved":
            effect = json.loads(new_row["effect_on_resolve"] or "{}")
            _apply_metric_dict(state, effect.get("metrics") or {})
            _apply_economy_list(db, state, effect.get("economy") or [])
            _apply_faction_dict(db, effect.get("factions") or {})
        elif new_row["status"] == "failed":
            effect = json.loads(new_row["effect_on_fail"] or "{}")
            _apply_metric_dict(state, effect.get("metrics") or {})
            _apply_economy_list(db, state, effect.get("economy") or [])
            _apply_faction_dict(db, effect.get("factions") or {})
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
            if ev.event_type != "situation":
                print(f"[INFO] new_issue 已拒：事件 {event_id} 为 {ev.event_type}，不转 issue。")
                applied_new.append({"title": ev.title, "rejected": True, "reason": f"event_type={ev.event_type} 不立 issue"})
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
        # 终结效果
        if reason == "resolved":
            effect = json.loads(new_row["effect_on_resolve"] or "{}")
        else:
            effect = json.loads(new_row["effect_on_fail"] or "{}")
        _apply_metric_dict(state, effect.get("metrics") or {})
        _apply_economy_list(db, state, effect.get("economy") or [])
        _apply_faction_dict(db, effect.get("factions") or {})
        building_ops = _apply_issue_buildings(
            db, state, effect.get("buildings"),
            _ISSUE_PSEUDO_EVENT, f"局势#{issue_id}{'结案' if reason == 'resolved' else '失败'}",
        )
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
            _apply_metric_dict(state, cost.get("metrics") or {})
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


def apply_score_extraction(
    db: GameDB,
    state: GameState,
    extracted: Dict[str, object],
) -> Dict[str, object]:
    """落地结算 agent 输出的 JSON 到 state 与 db。"""
    # 1) metric_delta
    applied_metric = _apply_metric_dict(state, extracted.get("metric_delta") or {})
    # 2) economy_moves
    applied_economy = _apply_economy_list(db, state, extracted.get("economy_moves") or [])
    # 3) faction_delta + class_delta（朝堂派系 + 社会阶级；联动靠 LLM，不在代码做）
    applied_factions = _apply_faction_dict(db, extracted.get("faction_delta") or {})
    applied_classes = _apply_class_dict(db, extracted.get("class_delta") or {})
    # 4) region_delta / army_delta (复用旧 db 方法)
    region_deltas_raw = extracted.get("region_delta") or {}
    army_deltas_raw = extracted.get("army_delta") or {}
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

    # 5) external_power_updates：后金/蒙古/朝鲜/流寇等外部势力落库
    external_updates_raw = extracted.get("external_power_updates") or {}
    external_changes: List[Dict[str, object]] = []
    if isinstance(external_updates_raw, dict) and external_updates_raw:
        try:
            external_changes = db.apply_external_power_deltas(state, external_updates_raw)
        except Exception as exc:
            print(f"[WARN] external_power_updates 落库失败：{exc}")

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

    state.clamp()
    return {
        "metric_delta": applied_metric,
        "economy_moves": applied_economy,
        "faction_delta": applied_factions,
        "class_delta": applied_classes,
        "region_changes": region_changes,
        "army_changes": army_changes,
        "external_changes": external_changes,
        "issue_summary": issue_summary,
        "world_advance": extracted.get("world_advance") or {},
        "fiscal_changes": applied_fiscal,
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
                db.advance_issue(
                    state, issue_id,
                    trigger_kind="inertia",
                    delta_bar=actual,
                    stage_text=row["stage_text"],
                    narrative="局势自有其势，本月按其本然推移。",
                    metric_delta={},
                )
                row = db.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
                if row is None or row["status"] != "active":
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
