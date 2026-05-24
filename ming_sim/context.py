"""上下文生成与文本匹配：历史锚点、胜负判定、地区/军队/事件模糊匹配、
人物/事件上下文串、给 LLM 的 state_context。L4。

通过 bind_content() 注入 GameContent（过渡期）。
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from ming_sim.constants import ECONOMY_ACCOUNTS, TURN_UNIT
from ming_sim.assets import format_money, format_money_delta
from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.exceptions import LLMContractError
from ming_sim.models import Army, Character, Event, GameState, Region
from ming_sim.skills import available_skill_names, office_skills

_content: Optional[GameContent] = None


def bind_content(content: GameContent) -> None:
    global _content
    _content = content


def _ctx() -> GameContent:
    if _content is None:
        raise RuntimeError("context.bind_content() 未调用：GameContent 未注入。")
    return _content


def historical_anchor_for_month(year: int, month: int) -> Dict[str, object]:
    """给 LLM 的历史护栏：关键历史事变必须出现，但玩家可改变走向和结果。"""
    anchors = {
        (1626, 9): "努尔哈赤已死于宁远败后不久，后金内部围绕汗位重排，皇太极取得主动。",
        (1626, 10): "皇太极继后金汗位，改元天聪；此事在游戏开局前已成定局，不可改写为尚未登基。",
        (1627, 1): "丁卯之役：后金攻朝鲜，朝鲜被迫与后金缔结兄弟之盟，但仍暗中倾明。",
        (1629, 10): "己巳之变历史窗口开启：皇太极可能绕道蒙古、蓟镇入塞，威胁遵化、京师。",
        (1629, 11): "己巳之变最危险阶段：若蓟镇、宣大、京营、关宁勤王失措，后金兵锋可逼近北京城下。",
        (1630, 1): "己巳之变余波：辽东督师、京畿防务与勤王军功过会引发朝廷追责。",
        (1632, 5): "皇太极西征林丹汗及察哈尔体系的历史压力上升，蒙古各部可能倒向后金。",
        (1635, 4): "察哈尔衰败后，后金收编蒙古部众、获得传国玉玺一类政治资源的窗口临近。",
        (1636, 4): "皇太极历史上会改国号为大清、称帝；若后金仍强盛且未被明军压制，应发生称帝建制。",
        (1637, 1): "丙子之役后朝鲜可能彻底臣服清；若明朝未能牵制辽东，朝鲜倾明空间会急剧缩小。",
        (1642, 3): "松锦决战历史压力：若关宁、锦州、宁远供给和士气长期恶化，辽东主力可能遭毁灭性打击。",
    }
    note = anchors.get((year, month), "")
    return {
        "date": f"{year}年{month}月",
        "note": note or f"本{TURN_UNIT}无硬性历史锚点，但外部势力仍需按其利益自行推进。",
        "must_respect": bool(note),
    }


def victory_status(db: GameDB, state: GameState) -> Dict[str, object]:
    """胜负判定：原依赖 边防/民变 metric，已删；改用 external_powers / armies / regions / classes
    直接数据。
    - 后金综合 = leverage + military_strength + cohesion + supply
    - 明辽东防线综合 = 关宁(morale+supply+training+equipment-arrears) + 山海关同式 + (100 - beizhili.military_pressure)
    - 北方民变综合 = 陕晋豫三省 unrest 平均；农民阶级三省 sat 平均（低=惨）
    """
    houjin = db.conn.execute("SELECT * FROM external_powers WHERE id = 'houjin'").fetchone()
    if houjin is None:
        return {"status": "ongoing", "summary": "后金未建档。"}
    treasury = int(state.metrics.get("国库", 0))
    guanning = db.conn.execute("SELECT * FROM armies WHERE id = 'guanning'").fetchone()
    shanhaiguan = db.conn.execute("SELECT * FROM armies WHERE id = 'shanhaiguan'").fetchone()
    beizhili = db.conn.execute("SELECT * FROM regions WHERE id = 'beizhili'").fetchone()
    houjin_power = int(houjin["leverage"]) + int(houjin["military_strength"]) + int(houjin["cohesion"]) + int(houjin["supply"])
    ming_front = 0
    if guanning:
        ming_front += int(guanning["morale"]) + int(guanning["supply"]) + int(guanning["training"]) + int(guanning["equipment"]) - int(guanning["arrears"])
    if shanhaiguan:
        ming_front += int(shanhaiguan["morale"]) + int(shanhaiguan["supply"]) + int(shanhaiguan["training"]) + int(shanhaiguan["equipment"]) - int(shanhaiguan["arrears"])
    if beizhili:
        ming_front += max(0, 100 - int(beizhili["military_pressure"]))
    # 京畿压力 = beizhili.military_pressure（高=后金兵临城下）
    beizhili_pressure = int(beizhili["military_pressure"]) if beizhili else 0
    # 北方三省民变综合
    north_rows = db.conn.execute(
        "SELECT unrest FROM regions WHERE id IN ('shaanxi','shanxi','henan')"
    ).fetchall()
    north_unrest_avg = sum(int(r["unrest"]) for r in north_rows) // max(1, len(north_rows)) if north_rows else 0
    # 北方三省农民阶级 sat
    peasant_rows = db.conn.execute(
        "SELECT satisfaction FROM classes WHERE name='农民' AND region_id IN ('shaanxi','shanxi','henan')"
    ).fetchall()
    peasant_sat_avg = sum(int(r["satisfaction"]) for r in peasant_rows) // max(1, len(peasant_rows)) if peasant_rows else 50

    if (houjin_power <= 120 and ming_front >= 300 and beizhili_pressure <= 35) or (houjin_power <= 80 and ming_front >= 340):
        return {
            "status": "ming_victory",
            "summary": "后金兵势、内聚与粮饷已被压到低位，关宁与山海关形成稳定优势，辽东威胁被解除。",
        }
    if int(houjin["leverage"]) >= 92 and int(houjin["military_strength"]) >= 82 and beizhili_pressure >= 85:
        return {
            "status": "qing_victory",
            "summary": "后金/清兵势压倒辽东与京畿，京畿告急，大明已失去扭转清势的窗口。",
        }
    if treasury <= 0 and north_unrest_avg >= 85 and peasant_sat_avg <= 12 and beizhili_pressure >= 70:
        return {
            "status": "qing_victory",
            "summary": "国库断绝、陕晋豫三省民变与辽东边患同涨，大明无力同时支撑内剿与辽东防线。",
        }
    return {
        "status": "ongoing",
        "summary": f"对清战局未决：后金综合{houjin_power}，明辽东防线综合{ming_front}，京畿压力{beizhili_pressure}，北方民变{north_unrest_avg}。",
    }


# 地区/军队名称匹配实现在 matching.py；此处提供绑定 GameContent 的便捷封装。
from ming_sim.matching import army_aliases, compact_name, region_aliases  # noqa: E402,F401
from ming_sim.matching import match_army_id_from_text as _match_army
from ming_sim.matching import match_region_id_from_text as _match_region


def match_region_id_from_text(text: str) -> Optional[str]:
    return _match_region(text, _ctx().regions)


def match_army_id_from_text(text: str) -> Optional[str]:
    return _match_army(text, _ctx().armies)


def state_context(state: GameState) -> str:
    parts = []
    for key, value in state.metrics.items():
        if key in ECONOMY_ACCOUNTS:
            parts.append(f"{key}{format_money(value)}")
        else:
            parts.append(f"{key}{value}")
    return "，".join(parts)


def parse_json_dict(raw: str) -> Dict[str, int]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LLMContractError(f"数据库中的数值变化 JSON 已损坏：{raw[:200]}") from error
    if not isinstance(data, dict):
        raise LLMContractError(f"数据库中的数值变化不是 object：{raw[:200]}")
    parsed: Dict[str, int] = {}
    for key, value in data.items():
        try:
            parsed[str(key)] = int(value)
        except (TypeError, ValueError) as error:
            raise LLMContractError(f"数据库中的数值变化字段不是整数：{key}={value}") from error
    return parsed


def format_metric_delta(delta: Dict[str, int]) -> str:
    if not delta:
        return "核心数值无明显变化"
    parts = []
    for key, value in delta.items():
        if key in ECONOMY_ACCOUNTS:
            parts.append(f"{key}{format_money_delta(value)}")
        else:
            sign = "+" if value > 0 else ""
            parts.append(f"{key}{sign}{value}")
    return "数值变化：" + "；".join(parts)


def character_context(character: Character) -> str:
    return (
        f"{character.name}，{character.office}，职位类型：{character.office_type}，派系：{character.faction}，"
        f"个人skills：{', '.join(character.personal_skills)}，"
        f"职位skills：{', '.join(office_skills(character.office_type))}，"
        f"忠诚{character.loyalty}，能力{character.ability}，清廉{character.integrity}，"
        f"胆略{character.courage}，风格：{character.style}"
    )


def character_context_with_db(character: Character, db: GameDB) -> str:
    return character_context(character) + f"，当前可用技能：{available_skill_names(character, db)}"


def event_context(event: Event) -> str:
    return (
        f"{event.title}。类型：{event.kind}。奏报：{event.summary} "
        f"紧急{event.urgency}，严重{event.severity}，可信{event.credibility}。"
        f"牵涉利益：{', '.join(event.interests)}。"
    )


def first_character() -> Character:
    try:
        return next(iter(_ctx().characters.values()))
    except StopIteration as error:
        raise SystemExit("characters.json 至少需要一个人物。") from error


def first_character_name() -> str:
    return first_character().name


def character_from_name(name: object) -> Character:
    value = str(name or "")
    character = _ctx().characters.get(value)
    if character is None:
        raise LLMContractError(f"人物未建档：{value}")
    return character


def match_minister_from_text(text: str, current: Optional[Character] = None) -> Optional[Character]:
    cleaned = text.strip()
    if not cleaned:
        return None
    matches = []
    for character in _ctx().characters.values():
        if current is not None and character.name == current.name:
            continue
        if (
            character.name in cleaned
            or character.office in cleaned
            or character.office_type in cleaned
            or character.faction in cleaned
            or any(alias in cleaned for alias in character.aliases)
            or any(skill in cleaned for skill in character.personal_skills)
        ):
            matches.append(character)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        exact = [character for character in matches if character.name in cleaned]
        if len(exact) == 1:
            return exact[0]
    return None
