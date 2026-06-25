"""Issue 系统：候选事件、issue 立项/推进/结案、tracker 输出落地、inertia 漂移。L6。

通过 bind_content() 注入 GameContent（取 EVENTS/SEED_EVENTS/EVENT_BY_ID）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Callable, Dict, List, Optional

from agno.agent import Agent
from agno.db.sqlite import SqliteDb

from ming_sim.agents import parse_agent_json, run_agent_text
from ming_sim.constants import (
    TURN_UNIT, REGION_SCORE_FIELDS, ARMY_SCORE_FIELDS, FISCAL_SCORE_FIELDS,
    REGION_FIELD_ALIASES, ARMY_FIELD_ALIASES, BUILDING_CATEGORIES,
)
from ming_sim.content import GameContent
from ming_sim.context import victory_status
from ming_sim.db import GameDB, infer_office_type_from_office, normalize_office
from ming_sim.gating import evaluate_gate
from ming_sim.flows import (
    ISSUE_METRIC_KEYS,
    ISSUE_METRIC_LOCK_CAPS,
    _apply_class_dict,
    _apply_economy_list,
    _apply_faction_dict,
    _apply_metric_dict,
)
from ming_sim.llm_model import create_chat_model
from ming_sim.models import Event, GameState, LLMConfig

_content: Optional[GameContent] = None

# 给建筑/地区落库做 event 关联用的占位事件（issue 结案触发的副作用无真实 event）。
_ISSUE_PSEUDO_EVENT = Event(
    id="issue_resolution", title="局势结案", kind="月末", summary="",
    urgency=0, severity=0, credibility=100, interests=[], audiences=[],
)

_ZONGSHI_STIPEND_BASE_KEY = "宗室禄米_base"
_ZONGSHI_STIPEND_RATE_KEY = "宗室禄米_rate"


def bind_content(content: GameContent) -> None:
    global _content
    _content = content


def _ctx() -> GameContent:
    if _content is None:
        raise RuntimeError("issues.bind_content() 未调用：GameContent 未注入。")
    return _content


def _apply_zongshi_stipend_backlash(
    db: GameDB,
    old_amount: int,
    new_amount: int,
    applied_factions: Dict[str, object],
    applied_classes: Dict[str, Dict[str, int]],
) -> None:
    """宗室禄米削减是政治动作：即使 extractor 漏写反噬，也要落到盘面。"""
    cut = max(0, old_amount - new_amount)
    if cut <= 0:
        return
    if cut >= 20:
        sat_delta, lev_delta = -12, 4
    elif cut >= 10:
        sat_delta, lev_delta = -8, 2
    else:
        sat_delta, lev_delta = -4, 1
    reason = f"宗室禄米月支削减{cut}万两，宗藩抗疏"
    faction_delta = {"宗室": {"satisfaction": sat_delta, "leverage": lev_delta}}
    class_delta = {"宗藩": {"satisfaction": sat_delta, "leverage": lev_delta}}
    for key, val in _apply_faction_dict(db, faction_delta).items():
        if key in applied_factions and isinstance(applied_factions[key], dict) and isinstance(val, dict):
            merged = dict(applied_factions[key])
            merged["satisfaction"] = int(merged.get("satisfaction") or 0) + int(val.get("satisfaction") or 0)
            merged["leverage"] = int(merged.get("leverage") or 0) + int(val.get("leverage") or 0)
            applied_factions[key] = merged
        else:
            applied_factions[key] = val
    for key, val in _apply_class_dict(db, class_delta).items():
        if key in applied_classes:
            merged = dict(applied_classes[key])
            merged["satisfaction"] = int(merged.get("satisfaction") or 0) + int(val.get("satisfaction") or 0)
            merged["leverage"] = int(merged.get("leverage") or 0) + int(val.get("leverage") or 0)
            applied_classes[key] = merged
        else:
            applied_classes[key] = val


def _fiscal_monthly_amount(cfg: Dict[str, int], stem: str) -> int:
    base = int(cfg.get(f"{stem}_base", 0))
    rate = int(cfg.get(f"{stem}_rate", 100))
    return max(0, round(base * rate / 100))


def _normalize_fiscal_change_mode(raw: object) -> str:
    text = str(raw or "").strip().lower()
    aliases = {
        "": "delta_value",
        "delta": "delta_value",
        "delta_value": "delta_value",
        "增量": "delta_value",
        "增减原始值": "delta_value",
        "set": "set_value",
        "set_value": "set_value",
        "target": "set_value",
        "设为": "set_value",
        "设为原始值": "set_value",
        "delta_amount": "delta_amount",
        "amount_delta": "delta_amount",
        "月额增减": "delta_amount",
        "月支增减": "delta_amount",
        "set_amount": "set_amount",
        "amount": "set_amount",
        "target_amount": "set_amount",
        "月额设为": "set_amount",
        "月支设为": "set_amount",
        "减到月额": "set_amount",
        "scale_amount": "scale_amount",
        "percent_amount": "scale_amount",
        "月额按比例增减": "scale_amount",
        "月支按比例增减": "scale_amount",
        "削减百分比": "scale_amount",
    }
    return aliases.get(text, text)


def _apply_fiscal_change(db: GameDB, change: Dict[str, object]) -> Optional[Dict[str, object]]:
    key = str(change.get("key") or "").strip()
    if not key:
        return None
    cfg = db.get_fiscal_config()
    mode = _normalize_fiscal_change_mode(change.get("mode"))
    value_raw = change.get("value", change.get("delta"))
    try:
        value = float(value_raw)
    except (TypeError, ValueError):
        return None

    stem = db._stem_of(key)
    current = cfg.get(key)
    base_key = f"{stem}_base"
    rate_key = f"{stem}_rate"
    old_amount = _fiscal_monthly_amount(cfg, stem)

    if mode in {"delta_value", "set_value"}:
        if current is None:
            print(f"[WARN] fiscal_changes: 未知 key '{key}'，跳过。")
            return None
        new_val = max(0, round(value if mode == "set_value" else current + value))
        applied_key = key
    elif mode in {"delta_amount", "set_amount", "scale_amount"}:
        if base_key not in cfg:
            print(f"[WARN] fiscal_changes: 未知月额科目 '{key}'，跳过。")
            return None
        if mode == "set_amount":
            target_amount = value
        elif mode == "delta_amount":
            target_amount = old_amount + value
        else:
            target_amount = old_amount * (1 + value / 100)
        target_amount = max(0, target_amount)
        if rate_key in cfg and int(cfg.get(base_key, 0)) > 0:
            applied_key = rate_key
            current = int(cfg[rate_key])
            new_val = max(0, round(target_amount * 100 / int(cfg[base_key])))
        else:
            applied_key = base_key
            current = int(cfg.get(base_key, 0))
            new_val = max(0, round(target_amount))
    else:
        print(f"[WARN] fiscal_changes: 未知 mode '{mode}'，跳过。")
        return None

    db.set_fiscal_config(applied_key, new_val)
    new_cfg = db.get_fiscal_config()
    new_amount = _fiscal_monthly_amount(new_cfg, stem)

    if stem in db._DYNAMIC_REGION_FIELD or stem == "田赋":
        ratio = (new_val / current) if current and current > 0 else (1.0 if new_val == 0 else 0.0)
        if stem == "田赋":
            db.scale_tian_fu(ratio)
        else:
            db.apply_dynamic_fiscal_scale(stem, ratio)

    return {
        "key": applied_key,
        "requested_key": key,
        "mode": mode,
        "value": value,
        "old": current,
        "new": new_val,
        "old_amount": old_amount,
        "new_amount": new_amount,
        "delta": new_val - int(current or 0),
        "reason": str(change.get("reason") or ""),
    }


_CHARACTER_STATUS_ALIASES = {
    "dismissed": "dismissed",
    "已罢黜": "dismissed",
    "罢黜": "dismissed",
    "罢官": "dismissed",
    "革职": "dismissed",
    "革去": "dismissed",
    "免职": "dismissed",
    "削职": "dismissed",
    "去职": "dismissed",
    "imprisoned": "imprisoned",
    "已下狱": "imprisoned",
    "下狱": "imprisoned",
    "入狱": "imprisoned",
    "收监": "imprisoned",
    "系狱": "imprisoned",
    "囚禁": "imprisoned",
    "exiled": "exiled",
    "已流放": "exiled",
    "流放": "exiled",
    "发配": "exiled",
    "充军": "exiled",
    "谪戍": "exiled",
    "retired": "retired",
    "已致仕": "retired",
    "致仕": "retired",
    "告老": "retired",
    "乞休": "retired",
    "勒令致仕": "retired",
    "归里": "retired",
    "dead": "dead",
    "死亡": "dead",
    "身故": "dead",
    "身死": "dead",
    "已故": "dead",
    "病故": "dead",
    "卒": "dead",
    "死": "dead",
    "处死": "dead",
    "赐死": "dead",
    "斩首": "dead",
    "offstage": "offstage",
    "离场": "offstage",
    "不再登场": "offstage",
    "退场": "offstage",
    "隐退": "offstage",
}


def _normalize_character_status(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = raw.lower()
    if key in _CHARACTER_STATUS_ALIASES:
        return _CHARACTER_STATUS_ALIASES[key]
    compact = re.sub(r"[\s　`'\"“”‘’、/／|，,。；;：:（）()【】\[\]{}<>《》-]+", "", raw)
    return _CHARACTER_STATUS_ALIASES.get(compact, "")


def _apply_character_location(db: GameDB, content, name: str, location: object) -> str:
    loc = str(location or "").strip()[:40]
    if not name or not loc:
        return ""
    db.conn.execute("UPDATE characters SET location=? WHERE name=?", (loc, name))
    db.commit()
    if content is not None and name in content.characters:
        content.characters[name].location = loc
    return loc


def _split_character_changes(extracted: Dict[str, object]) -> None:
    for item in extracted.get("character_changes") or []:
        if not isinstance(item, dict):
            continue
        if item.get("new_power"):
            extracted.setdefault("character_power_changes", []).append(item)
        if item.get("status"):
            extracted.setdefault("character_status_changes", []).append(item)
        if item.get("new_office") or item.get("office"):
            extracted.setdefault("office_changes", []).append(item)


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
                    requires_tech=str(op.get("requires_tech") or ""),
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
            # 程序拒绝了这次新建（含科技门控未解锁）→ 透出到明细让玩家知情，而非静默吞掉
            applied.append({
                "action": action or "create",
                "name": str(op.get("name") or ""),
                "rejected": True,
                "note": str(exc)[:120],
            })
    return applied


def _attach_preset_legacy(
    db: GameDB, state: GameState, preset: object, source_issue_id: Optional[int],
) -> bool:
    """预设部门/科技带 modifiers 非空时，挂一条永久 legacy（duration=-1）。返回是否挂了。
    modifiers 已在 content json 预校验，直接落库；非预设（preset=None）不挂。"""
    if preset is None:
        return False
    modifiers = getattr(preset, "modifiers", None)
    if not isinstance(modifiers, dict) or not modifiers:
        return False
    try:
        db.insert_legacy(
            state,
            name=getattr(preset, "name", "预设修正"),
            modifiers=modifiers,
            narrative_hint=getattr(preset, "effect_summary", ""),
            duration_months=-1,  # 永久
            source_issue_id=source_issue_id,
        )
        return True
    except Exception as exc:
        print(f"[WARN] 预设 legacy 落库失败：{exc}；preset={getattr(preset,'key','?')}")
        return False


def _strip_legacy_if(effect: Dict[str, object], drop: bool) -> Dict[str, object]:
    """drop=True 时返回剔掉 `帝国修正`/`legacy` 段的 effect 副本（预设已自动挂修正，防双倍）。"""
    if not drop or not isinstance(effect, dict):
        return effect
    return {k: v for k, v in effect.items() if k not in ("legacy", "帝国修正")}


def _find_preset_key(ni: Dict[str, object]) -> tuple:
    """从新立 issue 的 effect_on_resolve.departments/technologies create op 里找预设 key。
    返回 (scope, preset)；未命中返回 (None, None)。scope ∈ 'departments'/'technologies'。"""
    eff = ni.get("effect_on_resolve")
    if not isinstance(eff, dict):
        return (None, None)
    ctx = _ctx()
    for scope, pool in (("departments", ctx.preset_departments), ("technologies", ctx.preset_technologies)):
        ops = eff.get(scope)
        if not isinstance(ops, list):
            continue
        for op in ops:
            if isinstance(op, dict) and str(op.get("action") or "").lower() == "create":
                preset = pool.get(str(op.get("key") or "").strip())
                if preset is not None:
                    return (scope, preset)
    return (None, None)


def _preset_override_new_issue(ni: Dict[str, object]) -> Dict[str, object]:
    """LLM 新立 issue 若命中预设（effect 里带预设 key），用预设覆盖 issue 字段保证统一。
    未命中返回原 ni。覆盖：题材/标题/预计月数/起步进度/阶段/解决条件/失败条件/effect_on_resolve/effect_on_fail。
    名称按预设填；研发条件与一次性效果按预设（含 departments|technologies:create op 以便结案落实体挂修正）。"""
    scope, preset = _find_preset_key(ni)
    if preset is None:
        return ni
    merged = dict(ni)
    merged["kind"] = "initiative"
    merged["title"] = preset.name
    merged["tags"] = preset.theme
    merged["expected_months"] = preset.expected_months
    merged["bar_value"] = preset.bar_value
    merged["stage_text"] = preset.stage_text
    merged["resolve_condition"] = preset.resolve_condition
    merged["fail_condition"] = preset.fail_condition
    merged["effect_on_resolve"] = dict(preset.effect_on_resolve)
    merged["effect_on_fail"] = dict(preset.effect_on_fail)
    return merged


def _unmet_tech_prereqs(db: GameDB, ni: Dict[str, object]) -> List[str]:
    """命中预设科技的 new_issue：检查 preset.requires 前置科技链是否全已研成。
    requires 存前置科技 key → 转预设 name → 查 technologies 表。返回未满足的前置科技名（空=全满足）。"""
    scope, preset = _find_preset_key(ni)
    if scope != "technologies" or preset is None:
        return []
    requires = getattr(preset, "requires", None) or []
    if not requires:
        return []
    pool = _ctx().preset_technologies
    unmet: List[str] = []
    for req_key in requires:
        req_preset = pool.get(str(req_key).strip())
        req_name = req_preset.name if req_preset is not None else str(req_key)
        if not db.tech_unlocked(req_name):
            unmet.append(req_name)
    return unmet


def _apply_issue_departments(
    db: GameDB, state: GameState, ops: object, reason: str,
    source_issue_id: Optional[int] = None,
) -> bool:
    """落地 issue effect 里的 departments 段：新设衙门随局势结案落 offices 表。
    op：{action:create, key?, name, authority_scope?, power?, ...}。命中预设池且 modifiers 非空 → 挂永久 legacy。
    返回是否挂了预设 legacy（供调用方覆盖、忽略 LLM 自写的帝国修正，防双倍）。
    """
    attached = False
    if not isinstance(ops, list):
        return attached
    presets = _ctx().preset_departments
    for op in ops:
        if not isinstance(op, dict):
            continue
        action = str(op.get("action") or "").lower()
        try:
            if action == "create":
                key = str(op.get("key") or "").strip()
                preset = presets.get(key)
                # 预设优先：命中则用预设字段，未命中用 LLM 给的字段（表外自创，无 legacy）
                if preset is not None:
                    db.add_department(
                        preset.name, authority_scope=preset.authority_scope,
                        power=preset.power, responsibility=preset.responsibility,
                        corruption_risk=preset.corruption_risk, origin="issue",
                    )
                    attached = _attach_preset_legacy(db, state, preset, source_issue_id) or attached
                else:
                    db.add_department(
                        str(op.get("name") or ""),
                        authority_scope=str(op.get("authority_scope") or ""),
                        power=int(op.get("power", 50)),
                        responsibility=int(op.get("responsibility", 50)),
                        corruption_risk=int(op.get("corruption_risk", 30)),
                        origin="issue",
                    )
            else:
                print(f"[WARN] issue effect departments: action '{action}' 暂不支持（仅 create），跳过。")
        except Exception as exc:
            print(f"[WARN] issue effect departments 落库失败：{exc}；op={op}")
    return attached


def _apply_issue_technologies(
    db: GameDB, state: GameState, ops: object, reason: str,
    source_issue_id: Optional[int] = None,
) -> bool:
    """落地 issue effect 里的 technologies 段：科技随研发结案落 technologies 表（无月度产出）。
    op：{action:create, key?, name, category?, effect_summary?}。命中预设池且 modifiers 非空 → 挂永久 legacy。
    返回是否挂了预设 legacy（供调用方覆盖、忽略 LLM 自写的帝国修正，防双倍）。
    """
    attached = False
    if not isinstance(ops, list):
        return attached
    presets = _ctx().preset_technologies
    for op in ops:
        if not isinstance(op, dict):
            continue
        action = str(op.get("action") or "").lower()
        try:
            if action == "create":
                key = str(op.get("key") or "").strip()
                preset = presets.get(key)
                if preset is not None:
                    db.add_technology(
                        state, preset.name, preset.category,
                        effect_summary=preset.effect_summary, origin="issue",
                    )
                    attached = _attach_preset_legacy(db, state, preset, source_issue_id) or attached
                else:
                    db.add_technology(
                        state, str(op.get("name") or ""),
                        str(op.get("category") or "科技"),
                        effect_summary=str(op.get("effect_summary") or ""),
                        origin="issue",
                    )
            else:
                print(f"[WARN] issue effect technologies: action '{action}' 暂不支持（仅 create），跳过。")
        except Exception as exc:
            print(f"[WARN] issue effect technologies 落库失败：{exc}；op={op}")
    return attached


def _apply_issue_fiscal(db: GameDB, state: GameState, ops: object, reason: str) -> None:
    """落地 issue effect 里的 fiscal 段：调税 issue 结案时真改 region.fiscal。
    op：{tax:田赋/辽饷/盐税/商税, ratio:倍率, region_id?:省id(空=全国), region_name?}。
    田赋走 scale_tian_fu，辽饷/盐税/商税走 apply_dynamic_fiscal_scale。
    这是户部 adjust_tax 立项的唯一落库点——issue 成功才改账，失败/搁浅不动税。
    """
    if not isinstance(ops, list):
        return
    for op in ops:
        if not isinstance(op, dict):
            continue
        tax = str(op.get("tax") or "")
        if tax not in ("田赋", "辽饷", "盐税", "商税"):
            print(f"[WARN] issue effect fiscal: 税种非法 '{tax}'，跳过。op={op}")
            continue
        try:
            ratio = float(op.get("ratio"))
        except (TypeError, ValueError):
            print(f"[WARN] issue effect fiscal: ratio 非数字，跳过。op={op}")
            continue
        if ratio < 0:
            continue
        region_id = str(op.get("region_id") or "").strip()
        try:
            if tax == "田赋":
                touched = db.scale_tian_fu(ratio, region_id)
            else:
                touched = db.apply_dynamic_fiscal_scale(tax, ratio, region_id)
            scope = op.get("region_name") or ("全国" if not region_id else region_id)
            print(f"[issue_fiscal] {scope}{tax}×{ratio} 落库（{touched}省）：{reason}")
        except Exception as exc:
            print(f"[WARN] issue effect fiscal 落库失败：{exc}；op={op}")


def _compact_issue_log(text: object, max_chars: int = 80) -> str:
    """压缩写入 issue_advances / 推演 payload 的单条事项日志。"""
    note = re.sub(r"\s+", " ", str(text or "").strip())
    if len(note) > max_chars:
        note = note[:max_chars].rstrip() + "..."
    return note


def _load_max_decree_issues() -> int:
    try:
        from ming_sim.llm_config import load_runtime_game
        return int(load_runtime_game().get("max_decree_issues", 10))
    except Exception:
        return 10


def make_issue_log_compactor(
    llm_config: Optional[LLMConfig] = None,
    agno_db: Optional[SqliteDb] = None,
) -> Callable[[object], str]:
    """返回 issue 短日志压缩器：优先 LLM 保义压缩，失败时硬截断兜底。"""
    if llm_config is None:
        return _compact_issue_log

    agent = Agent(
        name="事项日志誊清书办",
        id="issue-log-compactor",
        model=create_chat_model(
            llm_config,
            temperature=0.0,
            top_p=0.7,
            max_tokens=120,
            enable_thinking=False,
            force_json_output=True,
        ),
        instructions=[
            "你只做事项推进日志压缩。输入是一段较长的局势推进/结案/撤销叙述；"
            "请保留本回合造成进度变化的关键事实，去掉旧背景、解释、修辞和机制词。"
            "只输出合法 JSON：{\"log\":\"...\"}。log 必须是中文单句，80字内。"
        ],
        add_history_to_context=False,
        markdown=False,
    )

    def _compress(text: object) -> str:
        original = re.sub(r"\s+", " ", str(text or "").strip())
        if len(original) <= 80:
            return original
        try:
            raw = run_agent_text(
                agent,
                json.dumps({"text": original}, ensure_ascii=False),
                tag="issue-log-compact",
            )
            data = parse_agent_json(raw)
            log = str(data.get("log") or "").strip() if isinstance(data, dict) else ""
            if log:
                return _compact_issue_log(log)
        except Exception as exc:
            print(f"[WARN] issue log LLM 压缩失败，改用截断：{exc}")
        return _compact_issue_log(original)

    return _compress


def issue_to_payload(row: sqlite3.Row, advances: List[sqlite3.Row]) -> Dict[str, object]:
    """喂给推演 agent 的事项精简视图：状态、进度、效果、完整推进日志。"""
    def _advance_time(a: sqlite3.Row) -> Dict[str, object]:
        turn = int(a["turn"])
        keys = a.keys() if hasattr(a, "keys") else []
        year = int(a["year"] or 0) if "year" in keys else 0
        period = int(a["period"] or 0) if "period" in keys else 0
        if year <= 0 or period <= 0:
            month_index = 1627 * 12 + 10 + max(0, turn - 1)
            year = month_index // 12
            period = month_index % 12
            if period == 0:
                year -= 1
                period = 12
        return {"turn": turn, "year": year, "period": period, "time": f"{year}年{period}月"}

    keys = row.keys() if hasattr(row, "keys") else []
    resolve_cond = row["resolve_condition"] if "resolve_condition" in keys else ""
    fail_cond = row["fail_condition"] if "fail_condition" in keys else ""
    goal = (row["goal"] if "goal" in keys else "") or ""
    assignee = (row["assignee"] if "assignee" in keys else "") or ""
    timeline = [
        {
            "id": int(a["id"]),
            **_advance_time(a),
            "narrative": _compact_issue_log(a["narrative"]),
        }
        for a in advances
    ]
    latest = timeline[-1] if timeline else None
    payload = {
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
        "推进日志": timeline,
        f"上{TURN_UNIT}推进": (
            {"turn": latest["turn"], "narrative": latest["narrative"]}
            if latest else None
        ),
    }
    if goal.strip():
        payload["目标"] = goal.strip()
    if assignee.strip():
        payload["承办人"] = assignee.strip()
    # 承办人授权：皇帝批了专款/生杀权后，承办人本月可自主推进（不必再下圣旨）。
    budget_pool = float((row["budget_pool"] if "budget_pool" in keys else 0) or 0)
    budget_source = str((row["budget_source"] if "budget_source" in keys else "") or "")
    death_authority = int((row["death_authority"] if "death_authority" in keys else 0) or 0)
    if budget_pool > 0 and budget_source in ("国库", "内库"):
        payload["专款余额"] = round(budget_pool, 1)
        payload["专款出库"] = budget_source
    if death_authority:
        payload["专断之权"] = True
    return payload


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


def gather_candidate_events(state: GameState, db: GameDB) -> List[Event]:
    """程序筛选：历史锚定事件按 trigger 时间到点、seed 情势按 trigger_gate 达标，
    都排除已触发过的。返回的候选清单交推演 agent 因果判定是否真触发。"""
    c = _ctx()
    spawned = _spawned_event_refs(db)
    candidates: List[Event] = []
    # 历史锚定 EVENTS：到点（含错过补出）即进候选；有 require 则须程序求值通过（可证伪前提）。
    for ev in c.events:
        if ev.id in spawned or ev.trigger_year <= 0:
            continue
        if not _event_window_open(ev, state):
            continue
        # require 不满足 → 跳过该 node（如袁崇焕不在辽东则斩毛文龙不浮现）。
        # 无 require（历史既定型）短路，行为不变。
        if ev.require and not evaluate_gate(ev.require, state.metrics, db):
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
        if evaluate_gate(ev.trigger_gate, state.metrics, db):
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
        # （空 gate 会被 evaluate_gate 判为恒真，必须显式排除，否则每回合都试图重立。）
        if not ev.trigger_gate:
            continue
        if not _event_window_open(ev, state):
            continue
        if not evaluate_gate(ev.trigger_gate, state.metrics, db):
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
        assignee = str((row["assignee"] if "assignee" in row.keys() else "") or "").strip()
        if assignee:
            line_parts.append(f"承办：{assignee}")
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
    # resolve 效果按 kind/severity 推缺省（所有 situation 都有达成回血）；
    # fail 效果不推断——崩坏与否一律由事件 JSON 显式声明（effect_on_fail 非空才会崩）。
    effect_resolve = _situation_resolve_effect(ev.kind, int(ev.severity), polarity)
    effect_fail: Dict[str, object] = {}
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


def _situation_resolve_effect(kind: str, severity: int, polarity: str) -> Dict[str, object]:
    """situation 达成（bar→100）的一锤子永久回血/加成。所有 situation 都有。
    按 severity 推量级（轻 50 / 中 65 / 重 80），民心/皇威由 kind 倾向决定
    （边事/外族偏皇威，灾害/民变偏民心，余者两者兼得）。

    失败效果（effect_on_fail）不在此推断——崩坏与否一律由事件 JSON 显式声明
    （effect_on_fail 非空=会崩坏，空=不崩，只靠 ongoing_effects 持续流血）。
    见 db.advance_issue 的 can_collapse 判定。"""
    mag = 1 if severity < 55 else (2 if severity < 70 else 3)
    if kind in ("外族", "边事", "友邦", "归附", "盟约", "战机", "敌乱"):
        axis = "皇威"
    elif kind in ("天灾", "灾情", "饥荒", "人祸", "兵变", "流寇", "民变", "抗税", "丰收", "祥瑞", "民和"):
        axis = "民心"
    else:
        axis = "both"

    amount = (3 if polarity == "neg" else 4) * mag
    if axis == "both":
        half = max(1, abs(amount) // 2)
        s = 1 if amount > 0 else -1
        metrics = {"民心": s * half, "皇威": s * half}
    else:
        metrics = {axis: amount}
    return {"metrics": metrics}


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


ISSUE_THEMES = ("工程", "科技", "政治", "军事", "民生", "经济", "文化", "其他")


def _normalize_issue_tags(raw: object) -> list:
    """题材枚举归一到 tags：LLM 可能给标量字符串（题材）、列表或缺省。
    取首个落在 ISSUE_THEMES 的词放表首；非枚举词原样保留为附加 tag；都没有则不强填。"""
    if raw is None:
        items = []
    elif isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = [str(raw)]
    items = [s.strip() for s in items if s and str(s).strip()]
    theme = next((s for s in items if s in ISSUE_THEMES), None)
    rest = [s for s in items if s != theme]
    return ([theme] if theme else []) + rest


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


# 题材(tags)→ 落地实体类型。决定走满结案时该落 建筑/部门/科技 哪一种。
_THEME_ENTITY = {
    "工程": "buildings",
    "科技": "technologies",
    "政治": "departments",
}
# 工程类局势走满兜底落建筑时的默认 category（大模型没给时用）。
_FALLBACK_BUILDING_CATEGORY = "民生"


def _synth_resolve_effect_for_issue(db: GameDB, row: sqlite3.Row) -> Dict[str, object]:
    """大模型漏填实体时的兜底：按 issue 的 tags 题材，自动造一个最简实体落地段。
    工程→建筑(region_hint 或默认京师 beizhili)、科技→科技。政治类改革/政策不自动落部门；
    只有 effect_on_resolve 明确写 departments:create 或命中预设部门时才新设衙门。
    名称用 issue 标题。这是「推进时发现没有 effect_on_resolve 就生成一个」的程序保底。"""
    try:
        tags = json.loads(row["tags"] or "[]")
    except Exception:
        tags = []
    entity = None
    for t in tags:
        if str(t) in _THEME_ENTITY:
            entity = _THEME_ENTITY[str(t)]
            break
    if entity is None:
        return {}
    title = str(row["title"] or "新立实体")[:60]
    if entity == "buildings":
        region = str(row["region_hint"] or "").strip()
        if not region or db.conn.execute("SELECT 1 FROM regions WHERE id=?", (region,)).fetchone() is None:
            region = "beizhili"  # 京师，必存在，作兜底选址
        return {"buildings": [{"action": "create", "region_id": region, "name": title,
                               "category": _FALLBACK_BUILDING_CATEGORY, "maintenance": 1}]}
    if entity == "technologies":
        return {"technologies": [{"action": "create", "name": title, "category": "科技", "effect_summary": ""}]}
    return {}


def _effect_has_entity(effect: Dict[str, object]) -> bool:
    """effect 里是否已含建筑/部门/科技实体段（大模型已现填则不兜底）。"""
    for seg in ("buildings", "departments", "technologies"):
        if isinstance(effect.get(seg), list) and effect.get(seg):
            return True
    return False


def _issue_baseline_delta(row: sqlite3.Row) -> int:
    """自动兜底用的本月基准推进量；承办人只改百分比，不直接给点数。"""
    bar = int(row["bar_value"] or 0)
    inertia = abs(int(row["inertia"] or 0))
    duration = int((row["duration_turns"] if "duration_turns" in row.keys() else 0) or 0)
    if inertia > 0:
        return max(3, min(12, inertia))
    if duration > 0:
        return max(3, min(12, round(max(1, 100 - bar) / max(1, duration))))
    return 6


_ASSIGNEE_PCT_BASES = {
    "ability": 1.6,
    "loyalty": 0.6,
    "integrity": 0.5,
    "courage": 0.4,
}


_ASSIGNEE_DOMAIN_KEYWORDS = {
    "stewardship": ("税", "赋", "饷", "银", "钱", "粮", "库", "财政", "财赋", "清丈", "盐", "商", "屯田", "漕", "工", "厂", "营造", "工程"),
    "martial": ("军", "兵", "边", "辽", "饷", "练", "营", "将", "火器", "城防", "剿", "战", "守"),
    "diplomacy": ("士林", "民心", "舆", "名望", "安抚", "劝", "赈", "科举", "教化", "盟", "使", "贡", "抚", "议和", "外", "蒙古", "朝鲜", "后金", "女真"),
    "learning": ("学", "书院", "历", "法", "器", "工艺", "技术", "火器", "制造", "译", "医"),
    "intrigue": ("情报", "密", "缉", "查", "案", "党", "阉", "锦衣", "东厂", "反间", "侦"),
}


def _issue_domain(row: sqlite3.Row) -> str:
    text = " ".join(
        str((row[key] if key in row.keys() else "") or "")
        for key in ("title", "kind", "goal", "stage_text", "bar_good_meaning", "bar_bad_meaning")
    )
    for domain, keywords in _ASSIGNEE_DOMAIN_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return domain
    return "stewardship"


def _assignee_net_pct(
    ability: int,
    loyalty: int,
    integrity: int,
    courage: int,
    domain_score: int = 50,
) -> int:
    """承办人百分比修正：50 为 0%；同帝国修正一样用带符号 net_pct 套基准增量。"""
    pct = (
        (domain_score - 50) * 1.3
        + (ability - 50) * 0.7
        + (loyalty - 50) * _ASSIGNEE_PCT_BASES["loyalty"]
        + (integrity - 50) * _ASSIGNEE_PCT_BASES["integrity"]
        + (courage - 50) * _ASSIGNEE_PCT_BASES["courage"]
    )
    return max(-80, min(80, round(pct)))


def _apply_assignee_pct(base_delta: int, net_pct: int) -> int:
    delta = round(GameDB.apply_legacy_pct(float(base_delta), int(net_pct)))
    if delta == 0:
        return 1 if base_delta >= 0 else -1
    return delta


def _auto_issue_delta_by_assignee(db: GameDB, row: sqlite3.Row) -> tuple[int, str]:
    """LLM 漏抽/填 0 时的兜底：基准进度按帝国修正同款百分比公式折算。"""
    assignee = str((row["assignee"] if "assignee" in row.keys() else "") or "").strip()
    inertia = int(row["inertia"] or 0)
    base_delta = _issue_baseline_delta(row)
    if assignee:
        ch = db.conn.execute(
            """
            SELECT name, office, office_type, status, ability, loyalty, integrity, courage,
                   diplomacy, martial, stewardship, intrigue, learning
            FROM characters WHERE name=?
            """,
            (assignee,),
        ).fetchone()
        if ch is None:
            net_pct = -40
            return _apply_assignee_pct(base_delta, net_pct), f"承办人{assignee}不在名册，承办修正{net_pct}%，责任无着，本月误期。"
        if str(ch["status"]) != "active":
            net_pct = -50
            return _apply_assignee_pct(base_delta, net_pct), f"承办人{assignee}已非在朝，承办修正{net_pct}%，事项无人实办。"
        ability = int(ch["ability"] or 50)
        loyalty = int(ch["loyalty"] or 50)
        integrity = int(ch["integrity"] or 50)
        courage = int(ch["courage"] or 50)
        domain = _issue_domain(row)
        net_pct = _assignee_net_pct(ability, loyalty, integrity, courage, int(ch[domain] or 50))
        delta = _apply_assignee_pct(base_delta, net_pct)
        if net_pct >= 50:
            tone = "才具卓异，调度有方"
        elif net_pct >= 25:
            tone = "能力出众，督办有力"
        elif net_pct >= 0:
            tone = "尚能胜任，诸司按令"
        elif net_pct >= -25:
            tone = "才具平平，进度打折"
        elif net_pct >= -50:
            tone = "能力不足，勉强维持"
        else:
            tone = "庸懦误事，部下推诿"
        return delta, f"承办人{assignee}{tone}，基准{base_delta}按承办修正{net_pct}%折算。"
    if inertia > 0:
        return min(3, max(1, inertia)), "无专责承办，仍循既有势头小幅推进。"
    if inertia < 0:
        return max(-3, min(-1, inertia)), "无专责承办，局势按旧患自然转坏。"
    return -1, "无专责承办，文移空转一月，事项轻微误期。"


def _ensure_issue_monthly_motion(
    db: GameDB,
    state: GameState,
    touched_ids: set,
    applied_advances: List[Dict[str, object]],
    compact_log: Callable[[object], str],
) -> None:
    """保证每条 active issue 每月有非 0 推进。模型漏条/填 0 时由程序补一条轻微变化。"""
    for row in db.list_active_issues():
        issue_id = int(row["id"])
        if issue_id in touched_ids:
            continue
        delta, narrative = _auto_issue_delta_by_assignee(db, row)
        if delta == 0:
            delta = -1
        new_row = db.advance_issue(
            state,
            issue_id,
            trigger_kind="auto_assignee",
            delta_bar=delta,
            stage_text=str(row["stage_text"] or "")[:120],
            narrative=compact_log(narrative),
            metric_delta={},
        )
        if new_row is None:
            continue
        touched_ids.add(issue_id)
        applied_advances.append({
            "issue_id": issue_id,
            "title": new_row["title"],
            "from_value": int(new_row["bar_value"]) - delta,
            "to_value": int(new_row["bar_value"]),
            "stage_text": new_row["stage_text"],
            "status": new_row["status"],
            "narrative": narrative,
            "auto_assignee": True,
        })


def apply_issue_tracker_output(
    db: GameDB,
    state: GameState,
    tracker_output: Dict[str, object],
    log_compactor: Optional[Callable[[object], str]] = None,
) -> Dict[str, object]:
    compact_log = log_compactor or _compact_issue_log
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
        base_delta_bar = int(adv.get("delta_bar") or 0)
        issue_row = db.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if issue_row is None or issue_row["status"] != "active":
            continue
        # 承办人专款支取：本{TURN_UNIT}从该 issue 专款里花了多少（extractor 抽自邸报），
        # 实扣指定库+减池（spend_issue_budget 内按库余额/池余额 clamp）。先于进度处理，
        # 因为「只花钱无进度增量」（base_delta_bar==0）的情形也要落账扣款。
        try:
            budget_spent = float(adv.get("budget_spent") or 0)
        except (TypeError, ValueError):
            budget_spent = 0.0
        budget_spent_actual = 0.0
        if budget_spent > 0:
            budget_spent_actual = db.spend_issue_budget(state, issue_id, budget_spent)
        if base_delta_bar == 0:
            continue
        # 承办人影响已在推演侧（season_simulator 先叙事再定档）一次性计入档位，
        # extractor 抽出的 delta_bar 即推演官按承办人专业能力+盘面推过的最终量；
        # 这里不再叠第二道承办系数（否则档位区间被冲破，如 normal 落成 +11），只做 ±50 clamp。
        delta_bar = max(-50, min(50, int(base_delta_bar)))
        inertia_delta = int(adv.get("inertia_delta") or 0)
        stage_text = str(adv.get("stage_text") or "")[:120]
        narrative = compact_log(adv.get("narrative") or "")
        # 推进推到 100/0 即结案——extractor 在本条推进里同时现填终结效果（含 buildings/departments/
        # technologies 实体段）。issue 立项时自带 effect 的（预设部门/科技局势）用预设为底，现填覆盖；
        # 手动/自建局势立项时 effect 为空，实体全靠这里现填。推进与结案合一，无需 close_issues 再写一遍。
        adv_resolve_effect = adv.get("effect_on_resolve") if isinstance(adv.get("effect_on_resolve"), dict) else None
        adv_fail_effect = adv.get("effect_on_fail") if isinstance(adv.get("effect_on_fail"), dict) else None
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
        building_ops: List[Dict[str, object]] = []
        # 终结结算：bar 自然推到 100/0 触发的 resolved/failed，与 close_issues 一样落终结效果（含建筑）
        if new_row["status"] == "resolved":
            # 预设为底 + 本条推进现填覆盖（issue 立项时已带实体的用预设；空的用现填）。
            effect = json.loads(new_row["effect_on_resolve"] or "{}")
            if adv_resolve_effect:
                effect = {**effect, **adv_resolve_effect}
            # 兜底：工程/科技/政治题材局势走满，却没人填实体段（大模型漏填）→ 程序按 tags 自动造一个最简实体，
            # 保证「目标办成必落实体」不落空。大模型已现填的不动。
            if not _effect_has_entity(effect):
                synth = _synth_resolve_effect_for_issue(db, new_row)
                if synth:
                    effect = {**effect, **synth}
                    print(f"[issue] 局势#{issue_id} 结案兜底落实体（大模型未现填）：{synth}")
            _apply_metric_dict(state, effect.get("metrics") or {}, db=db)
            _apply_economy_list(db, state, effect.get("economy") or [])
            _apply_faction_dict(db, effect.get("factions") or {})
            building_ops = _apply_issue_buildings(db, state, effect.get("buildings"), _ISSUE_PSEUDO_EVENT, f"局势#{issue_id}结案")
            _preset_lg = _apply_issue_departments(db, state, effect.get("departments"), f"局势#{issue_id}结案", issue_id)
            _preset_lg = _apply_issue_technologies(db, state, effect.get("technologies"), f"局势#{issue_id}结案", issue_id) or _preset_lg
            _apply_issue_fiscal(db, state, effect.get("fiscal"), f"局势#{issue_id}结案")
            _spawn_legacy_from_effect(db, state, _strip_legacy_if(effect, _preset_lg), issue_id, str(new_row["title"]))
        elif new_row["status"] == "failed":
            effect = json.loads(new_row["effect_on_fail"] or "{}")
            if adv_fail_effect:
                effect = {**effect, **adv_fail_effect}
            _apply_metric_dict(state, effect.get("metrics") or {}, db=db)
            _apply_economy_list(db, state, effect.get("economy") or [])
            _apply_faction_dict(db, effect.get("factions") or {})
            building_ops = _apply_issue_buildings(db, state, effect.get("buildings"), _ISSUE_PSEUDO_EVENT, f"局势#{issue_id}失败")
            _spawn_legacy_from_effect(db, state, effect, issue_id, str(new_row["title"]))
        applied_advances.append({
            "issue_id": issue_id,
            "title": new_row["title"],
            "from_value": int(new_row["bar_value"]) - delta_bar,
            "to_value": int(new_row["bar_value"]),
            "base_delta_bar": base_delta_bar,
            "delta_bar": delta_bar,
            "stage_text": new_row["stage_text"],
            "status": new_row["status"],
            "narrative": narrative,
            **({"building_ops": building_ops} if building_ops else {}),
            **({"budget_spent": round(budget_spent_actual, 1)} if budget_spent_actual > 0 else {}),
        })

    # 2) new_issues：接两种来源——
    #    decree     —— 玩家诏书强推，由 LLM 给字段新立 issue
    #    event_pool —— 预设事件（EVENTS/SEED_EVENTS）被推演判定触发，按预设 event 立 issue
    #    其它来源一律拒。
    initiative_active = db.count_active_initiatives()
    decree_active = db.count_active_decree_issues()
    max_decree_issues = _load_max_decree_issues()
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
        # 命中预设（effect 带预设 key）→ 程序用预设覆盖 issue 字段，保证条件/难度/效果统一
        ni = _preset_override_new_issue(ni)
        title = str(ni.get("title") or title)
        kind = str(ni.get("kind") or "initiative")
        if decree_active >= max_decree_issues:
            applied_new.append({
                "title": title,
                "rejected": True,
                "reason": f"decree 来源局势已达上限（{max_decree_issues} 条）",
            })
            continue
        if kind == "initiative" and initiative_active >= 10:
            applied_new.append({"title": title, "rejected": True, "reason": "已有十事在办，朝廷分身乏术，难再添新工。"})
            continue
        unmet = _unmet_tech_prereqs(db, ni)
        if unmet:
            reason_txt = f"前置科技未成：需先研成「{'、'.join(unmet)}」方可立此事"
            print(f"[INFO] new_issue 已拒：'{title}'（{reason_txt}）")
            applied_new.append({"title": title, "rejected": True, "reason": reason_txt})
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
                # 非预设（诏书所立 + 玩家手动）一律无自动漂移：inertia 存 0，进度全由大模型每月给的增量推动。
                # 预设事件池来源在别处按设计走势立项，不经此 new_issues 路径。
                inertia=0,
                stage_text=str(ni.get("stage_text") or "")[:120],
                severity=int(ni.get("severity") or 50),
                region_hint=str(ni.get("region_hint") or ""),
                faction_hint=str(ni.get("faction_hint") or ""),
                tags=_normalize_issue_tags(ni.get("tags")),
                ongoing_effects=dict(ni.get("ongoing_effects") or {}),
                cancellable=_normalize_cancellable(ni.get("cancellable")),
                cancel_cost=dict(ni.get("cancel_cost") or {}),
                effect_on_resolve=dict(ni.get("effect_on_resolve") or {}),
                effect_on_fail=dict(ni.get("effect_on_fail") or {}),
                resolve_condition=str(ni.get("resolve_condition") or "")[:300],
                fail_condition=str(ni.get("fail_condition") or "")[:300],
                assignee=str(ni.get("assignee") or ""),
            )
            decree_active += 1
            if kind == "initiative":
                initiative_active += 1
            applied_new.append({"issue_id": issue_id, "kind": kind, "title": title, "rejected": False})
        except Exception as exc:
            print(f"[WARN] new_issue 落库失败：{exc}；跳过 {title}")

    # 3) closes —— 已废弃顶层字段：达成/失败一律由 `advances` 把进度推到 100/0 自动结算（见上自动结案分支），
    #    实体/帝国修正在那条推进项的 effect_on_resolve/effect_on_fail 现填。这里只为兼容老存档/偶发输出而读，
    #    但不再主动结案（防止与 advances 自动结案重复落地）；新提示词已不输出 close_issues。
    applied_closes: List[Dict[str, object]] = []

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
                delta_bar=-2,
                stage_text=row["stage_text"],
                narrative=compact_log(cn.get("narrative") or "陛下欲罢，然此事非诏可消。"),
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
            narrative=compact_log(cn.get("narrative") or ""),
            applied_cost=cost if isinstance(cost, dict) else {},
        )
        touched_ids.add(issue_id)
        applied_cancels.append({"issue_id": issue_id, "rejected": False, "title": row["title"]})

    _ensure_issue_monthly_motion(db, state, touched_ids, applied_advances, compact_log)

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
    db.commit()
    return displaced


def _raise_reducer_failure(reducer_name: str, exc: Exception) -> None:
    raise RuntimeError(f"{reducer_name} reducer failed: {exc}") from exc


def apply_score_extraction(
    db: GameDB,
    state: GameState,
    extracted: Dict[str, object],
    content=None,
    registry=None,
    llm_config: Optional[LLMConfig] = None,
    agno_db: Optional[SqliteDb] = None,
) -> Dict[str, object]:
    """落地结算 agent 输出的 JSON 到 state 与 db。

    content/registry：若传入则处理 `appointments`——把诏书任命的新人建档入朝。
    缺省则跳过（向后兼容老调用）。"""
    extracted = dict(extracted or {})
    _split_character_changes(extracted)

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
            _raise_reducer_failure("new_armies", exc)
    if isinstance(region_deltas_raw, dict) and region_deltas_raw:
        try:
            region_changes = db.apply_region_deltas(state, pseudo_event, None, "档房", region_deltas_raw)
        except Exception as exc:
            _raise_reducer_failure("region_delta", exc)
    if isinstance(army_deltas_raw, dict) and army_deltas_raw:
        try:
            army_changes = db.apply_army_deltas(state, pseudo_event, None, "档房", army_deltas_raw)
        except Exception as exc:
            _raise_reducer_failure("army_delta", exc)

    # 注：建筑的新建/变更/废止不走顶层字段，全由 issue 的 effect_on_resolve /
    #     effect_on_fail 里的 `buildings` 段在局势结案时落地（见 _apply_issue_buildings）。

    # 4.5) arms_changes：军备总库叙事性增减（缴获/炸毁/采购）。建筑稳定月产由 flows 唯一变更，
    #      extractor 不重复抽建筑产出；未列型号由 weapon_meta 动态归 tier 注册。
    arms_changes_raw = extracted.get("arms_changes") or {}
    arms_changes: List[Dict[str, object]] = []
    if isinstance(arms_changes_raw, dict) and arms_changes_raw:
        try:
            arms_changes = db.apply_arms_stock_deltas(state, arms_changes_raw)
        except Exception as exc:
            _raise_reducer_failure("arms_changes", exc)

    # 5) power_updates：非明势力三项简表（威望/实力/经济）落库
    power_updates_raw = extracted.get("power_updates") or {}
    power_changes: List[Dict[str, object]] = []
    if isinstance(power_updates_raw, dict) and power_updates_raw:
        try:
            power_changes = db.apply_power_deltas(state, power_updates_raw)
        except Exception as exc:
            _raise_reducer_failure("power_updates", exc)

    # 6) issue_advances / new_issues / close_issues / cancels (复用旧 tracker 落地)
    issue_log_compactor = make_issue_log_compactor(llm_config, agno_db)
    issue_summary = apply_issue_tracker_output(db, state, {
        "advances": extracted.get("issue_advances") or [],
        "new_issues": extracted.get("new_issues") or [],
        "close_issues": extracted.get("close_issues") or [],
        "cancels": extracted.get("cancels") or [],
    }, log_compactor=issue_log_compactor)

    # 6.4) fiscal_removes：推演彻底裁撤 fiscal_config 中现存的月固定收支项，优先级最高。
    #      田赋/辽饷/盐税/商税停征走 regions.fiscal 字段变化，不走删 base+rate。
    applied_fiscal_removes: List[Dict[str, object]] = []
    for remove in extracted.get("fiscal_removes") or []:
        key = str(remove.get("key") or "")
        if not key:
            continue
        removed_key = db.remove_fiscal_item(key)
        if removed_key is None:
            print(f"[WARN] fiscal_removes: '{key}' 不存在，跳过裁撤。")
            continue
        applied_fiscal_removes.append({
            "key": removed_key, "reason": str(remove.get("reason") or ""),
        })

    # 6.5) fiscal_creates：推演凭空新立月固定收支项（税是其一种）。先于 fiscal_changes，
    #      使同{月}「新立关税 + 立即调率」可一气落地。
    applied_fiscal_creates: List[Dict[str, object]] = []
    for create in extracted.get("fiscal_creates") or []:
        key = str(create.get("key") or "")
        account = str(create.get("account") or "")
        direction = str(create.get("direction") or "")
        if not key or account not in ("国库", "内库") or direction not in ("income", "expense"):
            continue
        try:
            init_value = int(create.get("init_value") or 0)
        except (TypeError, ValueError):
            init_value = 0
        display = str(create.get("display") or "")
        new_key = db.create_fiscal_item(
            key, account, direction, display, init_value,
            note=str(create.get("reason") or "")[:120],
            formula=str(create.get("formula") or ""),
            basis=str(create.get("basis") or ""),
            rate_unit=str(create.get("rate_unit") or ""),
        )
        if new_key is None:
            print(f"[WARN] fiscal_creates: '{key}' 已存在或非法，跳过新立。")
            continue
        applied_fiscal_creates.append({
            "key": new_key, "account": account, "direction": direction,
            "display": display, "init_value": max(0, init_value),
            "reason": str(create.get("reason") or ""),
        })

    # 7) fiscal_changes：调整月度固定收支系数
    applied_fiscal: List[Dict[str, object]] = []
    for change in extracted.get("fiscal_changes") or []:
        if not isinstance(change, dict):
            continue
        applied = _apply_fiscal_change(db, change)
        if not applied:
            continue
        applied_fiscal.append(applied)
        if db._stem_of(str(applied["key"])) == "宗室禄米":
            _apply_zongshi_stipend_backlash(
                db, int(applied["old_amount"]), int(applied["new_amount"]),
                applied_factions, applied_classes,
            )

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
    for item in extracted.get("character_status_changes") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        raw_status = str(item.get("status") or "").strip()
        status = _normalize_character_status(raw_status)
        reason = str(item.get("reason") or "").strip()
        if not name or not status:
            applied_status_changes.append({
                "name": name, "status": raw_status, "rejected": True,
                "reason": "name 空 或 status 非白名单（可写罢黜/下狱/流放/致仕/身故/离场）",
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
            new_location = _apply_character_location(db, content, name, item.get("location"))
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
                ch.office_type = ""
        applied_status_changes.append({
            "name": name, "status": status, "reason": reason,
            **({"location": new_location} if new_location else {}),
        })

    # 9b) character_power_changes：人物易主（降将/叛臣/归正）
    applied_power_changes: List[Dict[str, object]] = []
    try:
        applied_power_changes = db.apply_character_power_changes(
            extracted.get("character_power_changes") or []
        )
    except Exception as exc:
        _raise_reducer_failure("character_power_changes", exc)

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
                new_location = _apply_character_location(db, content, name, item.get("location"))
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
                **({"location": new_location} if new_location else {}),
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
            new_location = _apply_character_location(db, content, appointed, item.get("location"))
            applied_office_changes.append({
                "name": appointed, "new_office": new_office,
                "kind": "appoint", "displaced": displaced, "reason": reason,
                **({"location": new_location} if new_location else {}),
            })
        else:
            applied_office_changes.append({
                "name": name, "new_office": new_office, "rejected": True,
                "kind": "appoint",
                "reason": f"建档失败（查重/字段不合）；原 status={cur_status or '不在册'}",
            })

    # 11) secret_order_updates：推演写 active 密令进度到 sim_note。结案不走这里。
    applied_secret_orders: List[Dict[str, object]] = []
    for item in extracted.get("secret_order_updates") or []:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("order_id")
        sim_note = re.sub(r"\s+", " ", str(item.get("sim_note") or item.get("result") or "").strip())
        if len(sim_note) > 80:
            sim_note = sim_note[:80].rstrip() + "..."
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
            print(f"[secret_order] 推演进度 id={real_id} note={sim_note[:60]!r}")
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
        "arms_changes": arms_changes,
        "power_changes": power_changes,
        "issue_summary": issue_summary,
        "world_advance": extracted.get("world_advance") or {},
        "fiscal_changes": applied_fiscal,
        "fiscal_creates": applied_fiscal_creates,
        "fiscal_removes": applied_fiscal_removes,
        "appointments": applied_appointments,
        "character_status_changes": applied_status_changes,
        "character_power_changes": applied_power_changes,
        "office_changes": applied_office_changes,
        "secret_order_progress": applied_secret_orders,
        "secret_order_updates": applied_secret_orders,  # 兼容旧调用方
        "secret_order_closes": applied_secret_closes,
        "victory_status": _resolve_victory(db, state, extracted),
    }


def _resolve_victory(db: GameDB, state: GameState, extracted: Dict[str, object]) -> Dict[str, object]:
    """结局判定：叙事型（崇祯退位/自尽，extractor 抽 emperor_fate）优先于数值型（京畿失守）。
    20 年到期（timeout）在 decree 结局收口判，不在此。"""
    fate = extracted.get("emperor_fate")
    if fate in ("abdicate", "suicide"):
        if fate == "abdicate":
            return {"status": "emperor_abdicate", "summary": "崇祯帝退位逊国，大明皇统中绝。"}
        return {"status": "emperor_suicide", "summary": "崇祯帝自尽殉国，煤山一缢，大明社稷俱亡。"}
    return victory_status(db, state)


def apply_issue_inertia_and_ongoing(
    db: GameDB,
    state: GameState,
    touched_ids: Optional[set] = None,
) -> None:
    # inertia 是每月自然漂移基础量，对所有进行中 issue 都生效（含本月被 advance 触动的）。
    # advance 的 delta_bar 是皇帝本月实旨推动的额外量，与 inertia 叠加，互不顶替。
    _ = touched_ids  # 保留入参不破坏调用方；inertia 漂移不再按它跳过
    # 手动 decree 局势到期自动撤销（无奖励），先于漂移处理，撤掉的不再参与本月漂移。
    expired_manual = db.expire_due_manual_issues(state)
    if expired_manual:
        print(f"[issue] 手动局势到期撤销 ids={expired_manual}")
    active = db.list_active_issues()
    # 累计单月 metric 落账，用于上限 clamp
    period_metric_acc: Dict[str, int] = {}

    for row in active:
        issue_id = int(row["id"])
        bar = int(row["bar_value"])
        inertia = int(row["inertia"])

        # 1) inertia 漂移：每月对进行中 issue 走一格。
        #    非预设（诏书所立 + 玩家手动）issue 立项时 inertia 即存 0（见 new_issues 落库），
        #    自然不漂；只有预设事件池来源带非 0 inertia，按其设计走势漂移。
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
                    _apply_issue_departments(db, state, effect.get("departments"), f"局势#{issue_id}结案", issue_id)
                    _apply_issue_technologies(db, state, effect.get("technologies"), f"局势#{issue_id}结案", issue_id)
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
                    issue_id, turn, year, period, trigger_kind, delta_bar,
                    from_value, to_value, narrative, metric_delta
                ) VALUES (?, ?, ?, ?, 'ongoing', 0, ?, ?, ?, ?)
                """,
                (
                    issue_id, state.turn, state.year, state.period, bar, bar,
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
        if evaluate_gate(gate, state.metrics, db):
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
        passed = evaluate_gate(leg.clear_gate, state.metrics, db)
        existing = db.conn.execute(
            "SELECT id FROM legacies WHERE legacy_key=? AND status='active'",
            (leg.key,),
        ).fetchone()
        if existing is not None:
            db.conn.execute(
                """UPDATE legacies
                   SET name=?, modifiers=?, narrative_hint=?, clear_gate=?
                   WHERE legacy_key=? AND status='active'""",
                (
                    leg.name,
                    json.dumps(leg.modifiers, ensure_ascii=False),
                    leg.narrative_hint,
                    json.dumps(leg.clear_gate, ensure_ascii=False),
                    leg.key,
                ),
            )
            db.conn.commit()
            db._legacy_mod_cache = None
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
