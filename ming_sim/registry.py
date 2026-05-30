"""大臣 Agent 创建与注册表，朝会动态上下文 court_brief。L6。

通过 bind_content() 注入 GameContent。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.skills import Skills
from agno.skills.loaders.local import LocalSkills

from ming_sim.constants import TURN_UNIT
from ming_sim.content import GameContent
from ming_sim.context import character_context_with_db
from ming_sim.models import Character, CourtContext, LLMConfig
from ming_sim.llm_model import create_chat_model
from ming_sim.token_stats import tlog
from ming_sim.tools import build_minister_tools

_content: Optional[GameContent] = None
_agent_skills: Optional[Skills] = None


def bind_content(content: GameContent) -> None:
    global _content
    _content = content


def _ctx() -> GameContent:
    if _content is None:
        raise RuntimeError("registry.bind_content() 未调用：GameContent 未注入。")
    return _content


def _skills() -> Skills:
    global _agent_skills
    if _agent_skills is None:
        _agent_skills = Skills([LocalSkills(".agno_skills", validate=False)])
    return _agent_skills


def build_court_brief(context: CourtContext) -> str:
    """每回合精简上下文：仅含回合 + 核心数值 + 在办事项 + 钱粮一句话。
    地区/军队/派系/事项详情靠大臣按需调 tool 查（list_regions, inspect_memorial 等）。
    """
    metrics = context.state.metrics
    money_line = (
        f"国库{metrics.get('国库', 0)}万两，内库{metrics.get('内库', 0)}万两。"
    )
    score_line = "；".join(
        f"{k}{metrics[k]}"
        for k in ("民心", "皇威")
        if k in metrics
    )
    issues = context.db.list_active_issues()
    issue_lines: List[str] = []
    for row in issues[:10]:
        kind_tag = "系统" if row["kind"] == "situation" else "玩家"
        bar = int(row["bar_value"])
        # 注意：bar_good_meaning/bar_bad_meaning 是进度条「两端」的含义，
        # 不是当前状态。当前 bar 值才是进度，满 100 才算到 good 端。
        issue_lines.append(
            f"#{row['id']}[{kind_tag}]{row['title']}"
            f"（进度{bar}/100；满100={row['bar_good_meaning']}，跌0={row['bar_bad_meaning']}）"
        )
    issues_brief = "；".join(issue_lines) if issue_lines else "无"
    return (
        f"本{TURN_UNIT}：{context.state.year}年{context.state.period}月（第{context.state.turn}回合）。"
        f"钱粮：{money_line}国势：{score_line}。"
        f"在办事项：{issues_brief}。"
        f"势力：{context.db.power_report(exclude_self=True)}。"
        f"朝堂派系（满意度/影响力均为当前实值，据此判断各派当前强弱，不要凭印象推断）：{context.db.faction_report()}。"
        f"详情请按需调工具查（list_regions/list_armies/inspect_memorial/check_treasury 等）。"
    )


def build_memory_brief(character: Character, context: CourtContext) -> str:
    tlog(f"[MEM-IO/memory-brief/INPUT] minister={character.name} turn={context.state.turn} window=3 limit=30")
    memories = context.db.get_recent_event_memories(
        turn=context.state.turn,
        window=3,
        limit=30,
    )
    if not memories:
        tlog(f"[memory/brief] {character.name} no memories")
        return ""
    tlog(f"[memory/brief] {character.name} inject={len(memories)} ids={','.join(str(m['id']) for m in memories)}")
    lines = ["【上回合旧事记忆】"]
    for memory in memories:
        lines.append(
            f"- #{memory['id']} {memory['year']}年{memory['period']}月:{memory['title']}。"
            f"起因：{memory['cause']}。经过：{memory['process']}。结果：{memory['outcome']}。"
        )
    brief = "\n".join(lines)
    tlog(f"[MEM-IO/memory-brief/OUTPUT] minister={character.name} ({len(brief)}字):\n{brief}")
    return brief


def build_secret_order_brief(character: Character, context: CourtContext) -> str:
    """本大臣名下进行中密令的提醒——只列编号+标题+本月推进了没，不泄具体进展。
    详情由大臣自己调 report_secret_order_progress 查（同时可写进展）。非承办人不提示。"""
    try:
        orders = context.db.get_active_secret_orders_for_minister(character.name)
    except Exception:
        return ""
    if not orders:
        return ""
    lines = [
        "【你身上还在办的密令】",
        "★ 皇帝问进度时调 `report_secret_order_progress(order_id, progress=本月新一步进展100字内)`：自动落档 + 返回历史时间线。一个月只能推一步。",
        "★ 皇帝催办/加急时调 `rush_secret_order(order_id, deadline_months=1/3/0, reason=催办缘由)`：1=下月核议，3=三月内核议，0=本月即核。",
        "★ 自认任务办到位时调 `submit_secret_order_for_review(order_id, claim=自述办结陈词200字内)`：转入待核议状态，等推演月末判 done/failed。",
        "★ progress / claim 写具体事实：派谁去、查到什么、摸到哪一层、下一步指向谁。空话「待实据到手」不算。",
        "★ 大臣无权直接判 done/failed——结案权全归推演。提交后该月不再可推进。",
        "在册密令：",
    ]
    for o in orders:
        status = o.get("status", "active")
        if status == "pending_review":
            tag = "⏳ 已提交待核议（本月不再可动，等推演月末定夺）"
        else:
            advanced = context.db._has_secret_order_period_line(
                int(o["id"]), "result", context.state.year, context.state.period
            )
            tag = "✅ 本月已推进" if advanced else "⚠️ 本月尚未推进"
        due_turn = int(o.get("due_turn") or 0)
        due_text = f"；御限剩 {max(0, due_turn - int(context.state.turn))} 月" if due_turn else ""
        lines.append(f"  - #{o['id']}「{o['title']}」 {tag}{due_text}")
        content_brief = (o.get("content") or "")[:80].replace("\n", " ")
        if content_brief:
            lines.append(f"    （任务摘要：{content_brief}…）")
    return "\n".join(lines)


def _make_cultivate_tool(character: Character, context: CourtContext):
    """生成后宫调教 tool，绑定到当前妃嫔。"""
    name = character.name

    def cultivate_consort(skill: str = "", trait: str = "") -> str:
        """皇帝调教妃嫔，为其新增技能或改变性格。skill：新增技能名（如"书法精通"），可为空；trait：新增性格词（如"更加温婉"），可为空。效果永久生效，下次召见时体现在人物描述中。"""
        context.db.cultivate_consort(
            name, context.state.turn, skill=skill.strip(), trait=trait.strip()
        )
        parts = []
        if skill.strip():
            parts.append(f"习得技能「{skill.strip()}」")
        if trait.strip():
            parts.append(f"性情添了「{trait.strip()}」")
        if not parts:
            return "未指定技能或性格，调教无效。"
        return "已记录：" + "、".join(parts) + "。下次召见时将体现。"

    return cultivate_consort


# 后宫预设立绘池：编号 → 该图的人物身份/气质（LLM 据此为秀女配图，确保人图一致）。
# 与 web/public/portraits/consort_pool_<N>.png 及 docs/portrait-prompts.md 文末清单对应。
CONSORT_POOL_IDENTITIES: Dict[int, str] = {
    1: "满洲格格——英武明艳，关外贵女，骑射出身",
    2: "江湖卖艺女——活泼灵动，杂耍歌舞，市井出身",
    3: "女侠——飒爽英姿，习武佩剑，江湖出身",
    4: "江南名妓——才情风流，诗词歌赋，秦淮出身",
    5: "才女画师——洒脱灵动，丹青妙笔，书香出身",
    6: "棋待诏——冷静知性，精于围棋，弈林出身",
    7: "道姑——仙气飘逸，修道清修，方外出身",
    8: "忧郁美人——秋色清愁，沉静寡言，文士门第",
    9: "波斯商女——异域浓彩，丝路而来，西域胡商之女",
    10: "琴师——清雅文艺，善抚瑶琴，乐坊出身",
    11: "娇艳贵女——明丽妩媚，雍容华贵，勋贵门第",
    12: "女医——温柔聪慧，通晓医药，杏林出身",
    13: "东厂女探——暗黑魅惑，身手不凡，厂卫出身",
    14: "端庄贵妇——母仪雍容，知书达礼，名门嫡女",
    15: "茶道名媛——温润恬静，精于茶艺，士绅之家",
    16: "南洋舶来——海岛风情，远渡而来，南洋舶商之女",
}


def _make_select_consort_tool(context: CourtContext):
    """生成选妃呈名单 tool，挂在司礼监/礼部大臣上。
    秀女由 LLM 据预设立绘池的身份现场拟就，tool 落库为待选采女（status=candidate），
    人设与所选立绘一致。只立候选不册封——皇帝看中后另下诏册封，走 candidate 升格路径。"""

    def _pool_used() -> set:
        rows = context.db.conn.execute(
            "SELECT portrait_id FROM characters WHERE portrait_id LIKE 'consort_pool_%'"
        ).fetchall()
        used = set()
        for r in rows:
            try:
                used.add(int(str(r["portrait_id"]).replace("consort_pool_", "")))
            except ValueError:
                pass
        return used

    def present_consort_candidates(consorts_json: str = "") -> str:
        """呈上待选秀女名单。

        可用立绘身份（portrait 编号→身份；已被占用的编号不要再选，先以空参调用可查当前可用编号）：
        {POOL_TABLE}

        consorts_json：JSON 数组字符串，3-5 人。每名秀女对象：
          {{"portrait": 4, "name": "柳如烟", "style": "才情风流",
            "skills": ["诗词","琵琶"], "summary": "秦淮名妓，色艺双绝", "faction": "中宫"}}
        """
        content = _ctx()
        try:
            raw = json.loads(consorts_json) if isinstance(consorts_json, str) and consorts_json.strip() else consorts_json
        except (json.JSONDecodeError, TypeError):
            return "（拟选名单格式有误，请以 JSON 数组重拟，每名含 portrait/name/style/skills/summary。）"
        if isinstance(raw, dict):
            raw = raw.get("consorts") or raw.get("candidates") or [raw]
        if not isinstance(raw, list) or not raw:
            free = sorted(set(CONSORT_POOL_IDENTITIES) - _pool_used())
            table = "\n".join(f"  {i}：{CONSORT_POOL_IDENTITIES[i]}" for i in free)
            return ("（尚未拟出秀女。请按下列可用立绘配人，回传 JSON 数组：\n"
                    + table + "\n每名含 portrait/name/style/skills/summary。）")

        existing_names = set(content.characters.keys())
        used = _pool_used()
        chosen: List[tuple[Character, int]] = []
        for item in raw[:6]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in existing_names:
                continue
            try:
                pid = int(item.get("portrait"))
            except (TypeError, ValueError):
                continue
            if pid not in CONSORT_POOL_IDENTITIES or pid in used:
                continue  # 编号非法或已占用，跳过
            skills = item.get("skills") or []
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.replace("、", ",").split(",") if s.strip()]
            consort = Character(
                name=name,
                office="采女（待选）",
                office_type="后宫",
                faction=str(item.get("faction") or "中宫"),
                aliases=[],
                personal_skills=[str(s).strip() for s in skills if str(s).strip()],
                loyalty=int(item.get("loyalty") or 60),
                ability=int(item.get("ability") or 55),
                integrity=int(item.get("integrity") or 60),
                courage=int(item.get("courage") or 50),
                style=str(item.get("style") or "温婉"),
                power_id="ming",
                status="candidate",
                summary=str(item.get("summary") or "").strip(),
                portrait_id=f"consort_pool_{pid}",  # 显式指定，add_character 不再自动分配
            )
            context.db.add_character(context.state, consort)
            content.characters[name] = consort
            existing_names.add(name)
            used.add(pid)
            chosen.append((consort, pid))

        if not chosen:
            free = sorted(set(CONSORT_POOL_IDENTITIES) - _pool_used())
            return ("（拟选的秀女或重名、或立绘编号非法/已占用，未能立为候选。"
                    f"当前可用立绘编号：{free}，请重拟。）")

        lines = ["臣等已为陛下物色数名待选采女，恭呈御览："]
        for idx, (c, _pid) in enumerate(chosen, 1):
            tags = "、".join(c.personal_skills) if c.personal_skills else "—"
            summary = (c.summary or "").strip()
            if len(summary) > 50:
                summary = summary[:50] + "…"
            lines.append(
                f"{idx}. {c.name}　性情：{c.style or '—'}　特质：{tags}"
                + (f"　{summary}" if summary else "")
            )
        lines.append("陛下若有中意者，可降诏册封其位份，即可入宫。")
        return "\n".join(lines)

    # 池身份表烤进 docstring（全 16 槽静态，不含动态 free 列表 → 工具 schema 跨回合不变，保缓存）。
    pool_table = "\n".join(f"  {i}：{CONSORT_POOL_IDENTITIES[i]}" for i in sorted(CONSORT_POOL_IDENTITIES))
    present_consort_candidates.__doc__ = present_consort_candidates.__doc__.replace("{POOL_TABLE}", pool_table)
    return present_consort_candidates


def create_minister_agent(
    character: Character,
    llm_config: LLMConfig,
    context: CourtContext,
    agno_db: SqliteDb,
    session_id: Optional[str] = None,
) -> Agent:
    # temperature 0.6：保留人物个性，但收敛发挥——少在拟旨里夹带题外私货。
    model = create_chat_model(llm_config, temperature=0.6, top_p=0.9)
    # 缓存策略：instructions 全部静态化（仅依赖 character，不依赖每月 state/events）。
    # game_world / minister_agent prompt、character 档案 跨月完全相同 → DeepSeek 前缀缓存命中。
    # 每月动态上下文（钱粮、奏报、地区、军队、派系）由 MinisterRegistry 在 agent 创建后通过首轮
    # user message 喂入，不污染 system prompt。
    c = _ctx()
    is_consort = character.office_type == "后宫"
    if is_consort:
        # 从 DB 取调教记录
        cultivated = context.db.get_consort_traits(character.name)
        extra_skills_str = ("、".join(cultivated["extra_skills"])) if cultivated["extra_skills"] else ""
        extra_traits_str = ("、".join(cultivated["extra_traits"])) if cultivated["extra_traits"] else ""
        cultivate_desc = ""
        if extra_skills_str:
            cultivate_desc += f"经皇帝调教后习得：{extra_skills_str}。"
        if extra_traits_str:
            cultivate_desc += f"性情逐渐变化：{extra_traits_str}。"
        instructions = [
            c.game_world_prompt,
            c.consort_agent_prompt,
            f"你当前扮演：{character.name}，{character.office}，性格{character.style}，"
            f"人物特质：{'、'.join(character.personal_skills)}。个人简介：{character.summary}"
            + (f"\n{cultivate_desc}" if cultivate_desc else ""),
            f"你与皇帝的对话在后宫寝殿；同一回合复召时接续此前对话，不要重置记忆。",
            f"当前为 {context.state.year} 年 {context.state.period} 月。",
        ]
        tools = [_make_cultivate_tool(character, context)]
    else:
        # 缓存策略：system prompt 仅含静态部分（game_world_prompt / minister_agent_prompt / character_context），
        # 跨月完全相同 → 前缀缓存命中。
        # 动态上下文（年月、朝会盘面、上回合旧事、密令）作为 user message 传入，不污染静态前缀。
        instructions = [
            c.game_world_prompt,
            c.minister_agent_prompt,
            f"你当前扮演：{character_context_with_db(character, context.db)}。",
            f"你与皇帝的多轮对话会持续到本{TURN_UNIT}退朝；同一{TURN_UNIT}复召时要接续此前奏对，不要重置记忆。",
        ]
        tools = build_minister_tools(character, context)
        # 司礼监（内官管后宫）与礼部（议礼册封）可奉旨选妃：现场拟就秀女名单呈御览。
        if character.office_type in ("司礼监", "礼部"):
            tools.append(_make_select_consort_tool(context))
    # 动态上下文通过 Agent.run() 的 user message 传入
    return Agent(
        name=character.name,
        id=f"minister-{character.name}",
        session_id=session_id or f"minister-{character.name}-turn-{context.state.turn}",
        db=agno_db,
        model=model,
        instructions=instructions,
        tools=tools,
        skills=_skills() if not is_consort else None,
        add_history_to_context=True,
        num_history_runs=6,
        tool_call_limit=5,
        markdown=False,
    )


class MinisterRegistry:
    def __init__(
        self,
        llm_config: LLMConfig,
        agno_db: SqliteDb,
        context: CourtContext,
    ) -> None:
        self.llm_config = llm_config
        self.agno_db = agno_db
        self.context = context
        self.agents: Dict[str, Agent] = {}
        characters = _ctx().characters
        self.session_ids: Dict[str, str] = {
            name: f"minister-{name}-turn-{context.state.turn}"
            for name in characters
        }
        for character in characters.values():
            self.agents[character.name] = self._create(character)

    def _create(self, character: Character) -> Agent:
        return create_minister_agent(
            character,
            self.llm_config,
            self.context,
            self.agno_db,
            session_id=self.session_ids[character.name],
        )

    def build_draft_line(self) -> str:
        """实时查本回合已核定草案。供 GameSession.chat 每轮前置进 user message。"""
        draft_rows = self.context.db.list_directives(self.context.state, statuses=("draft",))
        if not draft_rows:
            return "无"
        return "；".join(
            f"#{r['id']} {r['text'][:40]}{'…' if len(r['text']) > 40 else ''}"
            for r in draft_rows
        )

    def get(self, character: Character) -> Agent:
        return self.agents[character.name]

    def refresh(self, character_name: str) -> None:
        character = _ctx().characters.get(character_name)
        if character is None:
            return
        self.agents[character.name] = self._create(character)

    def register(self, character: Character) -> None:
        """运行时新建人物（吏部铨选任命）后注册其 Agent，使本回合即可召见。"""
        self.session_ids[character.name] = (
            f"minister-{character.name}-turn-{self.context.state.turn}"
        )
        self.agents[character.name] = self._create(character)

    def register_runtime(self, character: Character) -> None:
        """注册不入正式名册的临时召见人物。"""
        self.session_ids[character.name] = (
            f"temporary-{character.name}-turn-{self.context.state.turn}"
        )
        self.agents[character.name] = self._create(character)
