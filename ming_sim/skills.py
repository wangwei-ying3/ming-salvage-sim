"""Skill 体系查询：技能来源、可用技能、技能卡渲染、skill_tool 模板。L4。

通过 bind_content() 注入 GameContent（过渡期）；步骤7 起由 GameSession 统一注入。
"""

from __future__ import annotations

from typing import List, Optional

from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.models import Character

_content: Optional[GameContent] = None


def bind_content(content: GameContent) -> None:
    """注入静态设定。main.py / GameSession 启动时调用一次。"""
    global _content
    _content = content


def _ctx() -> GameContent:
    if _content is None:
        raise RuntimeError("skills.bind_content() 未调用：GameContent 未注入。")
    return _content


def office_skills(office_type: str) -> List[str]:
    return _ctx().office_skills.get(office_type, ["奏对", "拟办"])


def available_skill_ids(character: Character, db: Optional[GameDB] = None) -> List[str]:
    c = _ctx()
    skill_ids = list(c.common_skills)
    skill_ids.extend(c.office_default_skills.get(character.office_type, []))
    skill_ids.extend(c.personal_skill_ids.get(character.name, []))
    if db is not None:
        skill_ids.extend(db.active_skill_grants(character.name))
    seen: set = set()
    unique = []
    for skill_id in skill_ids:
        if skill_id in seen:
            continue
        seen.add(skill_id)
        unique.append(skill_id)
    return unique


def available_skill_names(character: Character, db: Optional[GameDB] = None) -> str:
    names = []
    for skill_id in available_skill_ids(character, db):
        definition = _ctx().skill_catalog.get(skill_id, {"name": skill_id, "kind": "未知"})
        names.append(f"{definition['name']}[{definition['kind']}]")
    return "，".join(names)


def skill_display_name(skill_id: str) -> str:
    return str(_ctx().skill_catalog.get(skill_id, {}).get("name", skill_id))


def skill_source_labels(character: Character, skill_id: str, db: Optional[GameDB] = None) -> List[str]:
    c = _ctx()
    labels: List[str] = []
    if skill_id in c.common_skills:
        labels.append("通用")
    if skill_id in c.office_default_skills.get(character.office_type, []):
        labels.append(f"{character.office_type}官职")
    if skill_id in c.personal_skill_ids.get(character.name, []):
        labels.append("个人")
    if db is not None and skill_id in db.active_skill_grants(character.name):
        labels.append("皇帝授权")
    if not labels:
        labels.append(str(c.skill_catalog.get(skill_id, {}).get("kind", "未知")))
    return labels


def skill_summary_line(character: Character, skill_id: str, db: Optional[GameDB] = None) -> str:
    c = _ctx()
    labels = "/".join(skill_source_labels(character, skill_id, db))
    description = c.skill_descriptions.get(skill_id, "暂无说明。")
    tool_flag = "可生成指令" if skill_id in c.directive_skill_ids else "可奏对查询"
    return f"- {skill_display_name(skill_id)}（{labels}，{tool_flag}）：{description}"


def print_skill_card(character: Character, db: Optional[GameDB] = None) -> None:
    print(f"\n技能卡：{character.name}（{character.office}，{character.faction}）")
    print(f"属性：忠诚{character.loyalty} | 能力{character.ability} | 清廉{character.integrity} | 胆略{character.courage} | 风格：{character.style}")
    for skill_id in available_skill_ids(character, db):
        print(skill_summary_line(character, skill_id, db))
    granted = db.active_skill_grants(character.name) if db is not None else []
    if granted:
        print("当前额外授权：" + "、".join(skill_display_name(skill_id) for skill_id in granted))
    else:
        print("当前额外授权：无")


def print_all_skill_cards(db: Optional[GameDB] = None) -> None:
    c = _ctx()
    print("\n通用 skill：")
    for skill_id in c.common_skills:
        print(f"- {skill_display_name(skill_id)}：{c.skill_descriptions.get(skill_id, '暂无说明。')}")
    print("\n官职 skill：")
    for office_type, skill_ids in c.office_default_skills.items():
        names = "、".join(skill_display_name(skill_id) for skill_id in skill_ids)
        print(f"- {office_type}：{names}")
    print("\n人物专长：")
    for name, skill_ids in c.personal_skill_ids.items():
        names = "、".join(skill_display_name(skill_id) for skill_id in skill_ids)
        print(f"- {name}：{names}")
    if db is not None:
        print("\n当前可召见人物 skill 概览：")
        for character in c.characters.values():
            office_names = "、".join(
                skill_display_name(skill_id)
                for skill_id in available_skill_ids(character, db)
                if skill_id not in c.common_skills
            )
            print(f"- {character.name}（{character.office}）：{office_names or '仅通用 skill'}")


def skill_template(template_id: str, **values: object) -> str:
    template = _ctx().skill_tool_templates.get(template_id)
    if template is None:
        raise SystemExit(f"skill_tools.json 缺少模板：{template_id}")
    return template.format(**values)
