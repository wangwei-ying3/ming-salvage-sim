"""设定加载：把 content/*.json 与 content/prompts/*.md 收进 GameContent。L2。

GameContent.load() 显式调用——模块导入本身不读盘、无副作用。
设定文件是唯一来源（CLAUDE.md），代码不硬编码副本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from ming_sim.assets import (
    int_field,
    load_json_asset,
    load_text_asset,
    require_dict,
    require_list,
    str_field,
    string_list,
)
from ming_sim.constants import BUILDING_CATEGORIES, BUILDING_OUTPUT_METRICS
from ming_sim.models import (
    Army,
    Building,
    Character,
    Event,
    ExternalPower,
    Faction,
    Region,
    SocialClass,
)


# --- 单项加载器（保留原签名，便于复用与单测）---

def load_character_content() -> Tuple[Dict[str, Faction], Dict[str, Character]]:
    data = require_dict(load_json_asset("characters.json"), "characters.json")
    factions: Dict[str, Faction] = {}
    for idx, raw in enumerate(require_list(data.get("factions"), "characters.json.factions"), 1):
        item = require_dict(raw, f"characters.json.factions[{idx}]")
        name = str_field(item, "name", f"characters.json.factions[{idx}]")
        factions[name] = Faction(
            name=name,
            satisfaction=int_field(item, "satisfaction", f"characters.json.factions[{idx}]"),
            leverage=int_field(item, "leverage", f"characters.json.factions[{idx}]"),
            agenda=str_field(item, "agenda", f"characters.json.factions[{idx}]"),
        )

    characters: Dict[str, Character] = {}
    for idx, raw in enumerate(require_list(data.get("characters"), "characters.json.characters"), 1):
        item = require_dict(raw, f"characters.json.characters[{idx}]")
        name = str_field(item, "name", f"characters.json.characters[{idx}]")
        characters[name] = Character(
            name=name,
            office=str_field(item, "office", f"characters.json.characters[{idx}]"),
            office_type=str_field(item, "office_type", f"characters.json.characters[{idx}]"),
            faction=str_field(item, "faction", f"characters.json.characters[{idx}]"),
            aliases=string_list(item.get("aliases", []), f"characters.json.characters[{idx}].aliases"),
            personal_skills=string_list(item.get("personal_skills"), f"characters.json.characters[{idx}].personal_skills"),
            loyalty=int_field(item, "loyalty", f"characters.json.characters[{idx}]"),
            ability=int_field(item, "ability", f"characters.json.characters[{idx}]"),
            integrity=int_field(item, "integrity", f"characters.json.characters[{idx}]"),
            courage=int_field(item, "courage", f"characters.json.characters[{idx}]"),
            style=str_field(item, "style", f"characters.json.characters[{idx}]"),
            birth_year=int(item.get("birth_year") or 0),
            historical_death_year=int(item.get("historical_death_year") or 0),
            historical_death_month=int(item.get("historical_death_month") or 0),
            current_office=str(item.get("current_office") or ""),
            imprisoned_cause=str(item.get("imprisoned_cause") or ""),
            death_cause=str(item.get("death_cause") or ""),
            status=str(item.get("status") or "active"),
            east_bureau_bound=bool(item.get("east_bureau_bound") or False),
        )

    if not factions or not characters:
        raise SystemExit("characters.json 必须至少定义一个派系和一个人物。")
    return factions, characters


def load_event_content(filename: str = "events.json") -> List[Event]:
    events: List[Event] = []
    for idx, raw in enumerate(require_list(load_json_asset(filename), filename), 1):
        item = require_dict(raw, f"{filename}[{idx}]")
        event_type = str(item.get("event_type") or "situation")
        if event_type not in ("situation", "node", "ending"):
            raise SystemExit(
                f"{filename}[{idx}] event_type 非法：{event_type!r}（仅 situation/node/ending）。"
            )
        gate_raw = item.get("trigger_gate") or {}
        if not isinstance(gate_raw, dict):
            raise SystemExit(f"{filename}[{idx}] trigger_gate 必须是对象（key→比较式）。")
        trigger_gate: Dict[str, str] = {}
        # key 形式见 issues._eval_gate_key：metric 名、region.<id>.<field>、army.<id>.<field>、
        # building.<id>.<field>、external.<id>.<field>、class.<name>[@<region>].<field>，
        # 多 id 用 | 分隔时末段 .<agg>(max/min/avg/sum)。
        # 这里只校验比较式格式，key 形式由求值器在 runtime 校验（id/field 存不存在）。
        for mk, mv in gate_raw.items():
            cond = str(mv).strip()
            if not re.match(r"^(>=|<=|>|<|==)\s*-?\d+$", cond):
                raise SystemExit(
                    f"{filename}[{idx}] trigger_gate['{mk}'] 非法：{cond!r}（应形如 '<=240' / '>=34'）。"
                )
            trigger_gate[str(mk)] = cond
        events.append(
            Event(
                id=str_field(item, "id", f"{filename}[{idx}]"),
                title=str_field(item, "title", f"{filename}[{idx}]"),
                kind=str_field(item, "kind", f"{filename}[{idx}]"),
                summary=str_field(item, "summary", f"{filename}[{idx}]"),
                urgency=int_field(item, "urgency", f"{filename}[{idx}]"),
                severity=int_field(item, "severity", f"{filename}[{idx}]"),
                credibility=int_field(item, "credibility", f"{filename}[{idx}]"),
                interests=string_list(item.get("interests"), f"{filename}[{idx}].interests"),
                audiences=string_list(item.get("audiences"), f"{filename}[{idx}].audiences"),
                resolve_condition=str(item.get("resolve_condition") or ""),
                fail_condition=str(item.get("fail_condition") or ""),
                trigger_year=int(item.get("trigger_year") or 0),
                trigger_month=int(item.get("trigger_month") or 0),
                precondition=str(item.get("precondition") or ""),
                event_type=event_type,
                trigger_gate=trigger_gate,
            )
        )
    if not events:
        raise SystemExit(f"{filename} 必须至少定义一个事件。")
    return events


def load_region_content() -> Dict[str, Region]:
    data = require_dict(load_json_asset("regions.json"), "regions.json")
    regions: Dict[str, Region] = {}
    for idx, raw in enumerate(require_list(data.get("regions"), "regions.json.regions"), 1):
        item = require_dict(raw, f"regions.json.regions[{idx}]")
        region_id = str_field(item, "id", f"regions.json.regions[{idx}]")
        regions[region_id] = Region(
            id=region_id,
            name=str_field(item, "name", f"regions.json.regions[{idx}]"),
            kind=str_field(item, "kind", f"regions.json.regions[{idx}]"),
            population=int_field(item, "population", f"regions.json.regions[{idx}]"),
            public_support=int_field(item, "public_support", f"regions.json.regions[{idx}]"),
            unrest=int_field(item, "unrest", f"regions.json.regions[{idx}]"),
            natural_disaster=str_field(item, "natural_disaster", f"regions.json.regions[{idx}]"),
            human_disaster=str_field(item, "human_disaster", f"regions.json.regions[{idx}]"),
            registered_land=int_field(item, "registered_land", f"regions.json.regions[{idx}]"),
            hidden_land=int_field(item, "hidden_land", f"regions.json.regions[{idx}]"),
            tax_per_turn=int_field(item, "tax_per_turn", f"regions.json.regions[{idx}]"),
            grain_security=int_field(item, "grain_security", f"regions.json.regions[{idx}]"),
            gentry_resistance=int_field(item, "gentry_resistance", f"regions.json.regions[{idx}]"),
            military_pressure=int_field(item, "military_pressure", f"regions.json.regions[{idx}]"),
            status=str_field(item, "status", f"regions.json.regions[{idx}]"),
        )
    if not regions:
        raise SystemExit("regions.json 必须至少定义一个地区。")
    return regions


def load_army_content() -> Dict[str, Army]:
    data = require_dict(load_json_asset("armies.json"), "armies.json")
    armies: Dict[str, Army] = {}
    for idx, raw in enumerate(require_list(data.get("armies"), "armies.json.armies"), 1):
        item = require_dict(raw, f"armies.json.armies[{idx}]")
        army_id = str_field(item, "id", f"armies.json.armies[{idx}]")
        armies[army_id] = Army(
            id=army_id,
            name=str_field(item, "name", f"armies.json.armies[{idx}]"),
            station=str_field(item, "station", f"armies.json.armies[{idx}]"),
            theater=str_field(item, "theater", f"armies.json.armies[{idx}]"),
            commander=str_field(item, "commander", f"armies.json.armies[{idx}]"),
            controller=str_field(item, "controller", f"armies.json.armies[{idx}]"),
            troop_type=str_field(item, "troop_type", f"armies.json.armies[{idx}]"),
            manpower=int_field(item, "manpower", f"armies.json.armies[{idx}]"),
            maintenance_per_turn=int_field(item, "maintenance_per_turn", f"armies.json.armies[{idx}]"),
            supply=int_field(item, "supply", f"armies.json.armies[{idx}]"),
            morale=int_field(item, "morale", f"armies.json.armies[{idx}]"),
            training=int_field(item, "training", f"armies.json.armies[{idx}]"),
            equipment=int_field(item, "equipment", f"armies.json.armies[{idx}]"),
            arrears=int_field(item, "arrears", f"armies.json.armies[{idx}]"),
            mobility=int_field(item, "mobility", f"armies.json.armies[{idx}]"),
            loyalty=int_field(item, "loyalty", f"armies.json.armies[{idx}]"),
            status=str_field(item, "status", f"armies.json.armies[{idx}]"),
        )
    if not armies:
        raise SystemExit("armies.json 必须至少定义一支军队。")
    return armies


def load_building_content() -> Dict[str, Building]:
    data = require_dict(load_json_asset("buildings.json"), "buildings.json")
    buildings: Dict[str, Building] = {}
    for idx, raw in enumerate(require_list(data.get("buildings"), "buildings.json.buildings"), 1):
        item = require_dict(raw, f"buildings.json.buildings[{idx}]")
        ctx = f"buildings.json.buildings[{idx}]"
        building_id = str_field(item, "id", ctx)
        category = str_field(item, "category", ctx)
        if category not in BUILDING_CATEGORIES:
            raise SystemExit(f"{ctx}: category '{category}' 不在白名单 {BUILDING_CATEGORIES}。")
        output_metric = str(item.get("output_metric") or "")
        if output_metric not in BUILDING_OUTPUT_METRICS:
            raise SystemExit(f"{ctx}: output_metric '{output_metric}' 不在白名单 {BUILDING_OUTPUT_METRICS}。")
        buildings[building_id] = Building(
            id=building_id,
            region_id=str_field(item, "region_id", ctx),
            name=str_field(item, "name", ctx),
            category=category,
            level=int_field(item, "level", ctx),
            condition=int_field(item, "condition", ctx),
            maintenance=int_field(item, "maintenance", ctx),
            risk=int_field(item, "risk", ctx),
            output_metric=output_metric,
            output_amount=int_field(item, "output_amount", ctx),
            status=str_field(item, "status", ctx),
        )
    if not buildings:
        raise SystemExit("buildings.json 必须至少定义一座建筑。")
    return buildings


def load_class_content() -> Dict[str, SocialClass]:
    """阶级人口设定。key = "name@region_id"（region_id 为空则 key="name"）。"""
    data = require_dict(load_json_asset("classes.json"), "classes.json")
    classes: Dict[str, SocialClass] = {}
    for idx, raw in enumerate(require_list(data.get("classes"), "classes.json.classes"), 1):
        item = require_dict(raw, f"classes.json.classes[{idx}]")
        name = str_field(item, "name", f"classes.json.classes[{idx}]")
        region_id = str(item.get("region_id") or "").strip()
        key = f"{name}@{region_id}" if region_id else name
        if key in classes:
            raise SystemExit(f"classes.json 重复条目：{key}")
        classes[key] = SocialClass(
            name=name,
            region_id=region_id,
            population=int_field(item, "population", f"classes.json.classes[{idx}]"),
            satisfaction=int_field(item, "satisfaction", f"classes.json.classes[{idx}]"),
            leverage=int_field(item, "leverage", f"classes.json.classes[{idx}]"),
            agenda=str_field(item, "agenda", f"classes.json.classes[{idx}]"),
        )
    if not classes:
        raise SystemExit("classes.json 必须至少定义一个阶级条目。")
    return classes


def load_external_powers() -> Dict[str, ExternalPower]:
    data = load_json_asset("external_powers.json")
    raw = require_dict(data, "external_powers.json")
    powers_raw = require_list(raw.get("powers"), "external_powers.json::powers")
    powers: Dict[str, ExternalPower] = {}
    for item in powers_raw:
        entry = require_dict(item, "external_powers.json::powers[item]")
        pid = str_field(entry, "id", "external_powers.json::powers[item].id")
        powers[pid] = ExternalPower(
            id=pid,
            name=str_field(entry, "name", "external_powers.json::powers[item].name"),
            leader=str_field(entry, "leader", "external_powers.json::powers[item].leader"),
            stance=str_field(entry, "stance", "external_powers.json::powers[item].stance"),
            leverage=int_field(entry, "leverage", "external_powers.json::powers[item].leverage"),
            satisfaction=int_field(entry, "satisfaction", "external_powers.json::powers[item].satisfaction"),
            military_strength=int(entry.get("military_strength", entry.get("strength", 55))),
            cohesion=int(entry.get("cohesion", 55)),
            supply=int(entry.get("supply", 55)),
            agenda=str_field(entry, "agenda", "external_powers.json::powers[item].agenda"),
            status=str_field(entry, "status", "external_powers.json::powers[item].status"),
            last_action=str(entry.get("last_action") or "尚无新动").strip() or "尚无新动",
        )
    return powers


def dict_of_string_lists(value: object, path: str) -> Dict[str, List[str]]:
    data = require_dict(value, path)
    return {str(key): string_list(item, f"{path}.{key}") for key, item in data.items()}


def dict_of_strings(value: object, path: str) -> Dict[str, str]:
    data = require_dict(value, path)
    output: Dict[str, str] = {}
    for key, item in data.items():
        if not isinstance(item, str):
            raise SystemExit(f"设定字段应为字符串：{path}.{key}")
        output[str(key)] = item
    return output


def load_skill_content() -> Tuple[
    Dict[str, List[str]],
    Dict[str, Dict[str, object]],
    Dict[str, List[str]],
    Dict[str, List[str]],
    List[str],
    Dict[str, str],
    Dict[str, List[str]],
    Dict[str, str],
    Set[str],
    Dict[str, Dict[str, object]],
]:
    data = require_dict(load_json_asset("skills.json"), "skills.json")
    office_skills_data = dict_of_string_lists(data.get("office_skills"), "skills.json.office_skills")
    skill_catalog = {
        str(key): require_dict(value, f"skills.json.skill_catalog.{key}")
        for key, value in require_dict(data.get("skill_catalog"), "skills.json.skill_catalog").items()
    }
    office_default_skills = dict_of_string_lists(data.get("office_default_skills"), "skills.json.office_default_skills")
    personal_skill_ids = dict_of_string_lists(data.get("personal_skill_ids"), "skills.json.personal_skill_ids")
    common_skills = string_list(data.get("common_skills"), "skills.json.common_skills")
    skill_descriptions = dict_of_strings(data.get("skill_descriptions"), "skills.json.skill_descriptions")
    grant_keywords = dict_of_string_lists(data.get("grant_keywords"), "skills.json.grant_keywords")
    directive_keywords = dict_of_strings(data.get("directive_keywords"), "skills.json.directive_keywords")
    directive_skill_ids = set(string_list(data.get("directive_skill_ids"), "skills.json.directive_skill_ids"))

    office_definitions: Dict[str, Dict[str, object]] = {}
    for office_type, raw in require_dict(data.get("office_definitions"), "skills.json.office_definitions").items():
        item = require_dict(raw, f"skills.json.office_definitions.{office_type}")
        skills_ref = str(item.get("skills_ref") or office_type)
        office_definitions[str(office_type)] = {
            "skills": office_skills_data.get(skills_ref, []),
            "tools": string_list(item.get("tools"), f"skills.json.office_definitions.{office_type}.tools"),
            "authority_scope": str_field(item, "authority_scope", f"skills.json.office_definitions.{office_type}"),
            "power": int_field(item, "power", f"skills.json.office_definitions.{office_type}"),
            "responsibility": int_field(item, "responsibility", f"skills.json.office_definitions.{office_type}"),
            "corruption_risk": int_field(item, "corruption_risk", f"skills.json.office_definitions.{office_type}"),
        }

    for skill_id in common_skills:
        if skill_id not in skill_catalog:
            raise SystemExit(f"common_skills 引用了未定义 skill：{skill_id}")
    for mapping_name, mapping in {
        "office_default_skills": office_default_skills,
        "personal_skill_ids": personal_skill_ids,
        "grant_keywords": grant_keywords,
    }.items():
        for key, skill_ids in mapping.items():
            for skill_id in skill_ids:
                if skill_id not in skill_catalog:
                    raise SystemExit(f"{mapping_name}.{key} 引用了未定义 skill：{skill_id}")
    for keyword, skill_id in directive_keywords.items():
        if skill_id not in skill_catalog:
            raise SystemExit(f"directive_keywords.{keyword} 引用了未定义 skill：{skill_id}")

    return (
        office_skills_data,
        skill_catalog,
        office_default_skills,
        personal_skill_ids,
        common_skills,
        skill_descriptions,
        grant_keywords,
        directive_keywords,
        directive_skill_ids,
        office_definitions,
    )


@dataclass
class GameContent:
    """游戏全部静态设定。GameContent.load() 一次性读盘填充。

    替代原 main.py 的模块级全局量（FACTIONS/CHARACTERS/EVENTS/...），
    根治 `import main` 即读盘的副作用。
    """

    factions: Dict[str, Faction] = field(default_factory=dict)
    characters: Dict[str, Character] = field(default_factory=dict)
    events: List[Event] = field(default_factory=list)
    seed_events: List[Event] = field(default_factory=list)
    event_by_id: Dict[str, Event] = field(default_factory=dict)
    regions: Dict[str, Region] = field(default_factory=dict)
    armies: Dict[str, Army] = field(default_factory=dict)
    buildings: Dict[str, Building] = field(default_factory=dict)
    faction_metrics: Tuple[str, ...] = ()
    external_powers: Dict[str, ExternalPower] = field(default_factory=dict)
    classes: Dict[str, SocialClass] = field(default_factory=dict)

    # skill 体系（load_skill_content 十元组）
    office_skills: Dict[str, List[str]] = field(default_factory=dict)
    skill_catalog: Dict[str, Dict[str, object]] = field(default_factory=dict)
    office_default_skills: Dict[str, List[str]] = field(default_factory=dict)
    personal_skill_ids: Dict[str, List[str]] = field(default_factory=dict)
    common_skills: List[str] = field(default_factory=list)
    skill_descriptions: Dict[str, str] = field(default_factory=dict)
    grant_keywords: Dict[str, List[str]] = field(default_factory=dict)
    directive_keywords: Dict[str, str] = field(default_factory=dict)
    directive_skill_ids: Set[str] = field(default_factory=set)
    office_definitions: Dict[str, Dict[str, object]] = field(default_factory=dict)
    skill_tool_templates: Dict[str, str] = field(default_factory=dict)

    # 提示词
    game_world_prompt: str = ""
    minister_agent_prompt: str = ""
    decree_writer_prompt: str = ""
    season_simulator_prompt: str = ""
    score_extractor_prompt: str = ""
    game_summarizer_prompt: str = ""

    @classmethod
    def load(cls) -> "GameContent":
        factions, characters = load_character_content()
        events = load_event_content("events.json")
        seed_events = load_event_content("seed_events.json")
        regions = load_region_content()
        armies = load_army_content()
        buildings = load_building_content()
        external_powers = load_external_powers()
        classes = load_class_content()
        (
            office_skills_data,
            skill_catalog,
            office_default_skills,
            personal_skill_ids,
            common_skills,
            skill_descriptions,
            grant_keywords,
            directive_keywords,
            directive_skill_ids,
            office_definitions,
        ) = load_skill_content()
        return cls(
            factions=factions,
            characters=characters,
            events=events,
            seed_events=seed_events,
            event_by_id={ev.id: ev for ev in (*events, *seed_events)},
            regions=regions,
            armies=armies,
            buildings=buildings,
            faction_metrics=tuple(factions.keys()),
            external_powers=external_powers,
            classes=classes,
            office_skills=office_skills_data,
            skill_catalog=skill_catalog,
            office_default_skills=office_default_skills,
            personal_skill_ids=personal_skill_ids,
            common_skills=common_skills,
            skill_descriptions=skill_descriptions,
            grant_keywords=grant_keywords,
            directive_keywords=directive_keywords,
            directive_skill_ids=directive_skill_ids,
            office_definitions=office_definitions,
            skill_tool_templates=dict_of_strings(load_json_asset("skill_tools.json"), "skill_tools.json"),
            game_world_prompt=load_text_asset("prompts/game_world.md"),
            minister_agent_prompt=load_text_asset("prompts/minister_agent.md"),
            decree_writer_prompt=load_text_asset("prompts/decree_writer.md"),
            season_simulator_prompt=load_text_asset("prompts/season_simulator.md"),
            score_extractor_prompt=load_text_asset("prompts/score_extractor.md"),
            game_summarizer_prompt=load_text_asset("prompts/game_summarizer.md"),
        )
