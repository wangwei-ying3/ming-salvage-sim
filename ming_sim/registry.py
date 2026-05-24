"""大臣 Agent 创建与注册表，朝会动态上下文 court_brief。L6。

通过 bind_content() 注入 GameContent。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from agno.agent import Agent
from agno.db.sqlite import SqliteDb

from ming_sim.constants import TURN_UNIT
from ming_sim.content import GameContent
from ming_sim.context import character_context_with_db
from ming_sim.models import Character, CourtContext, LLMConfig
from ming_sim.llm_model import create_chat_model
from ming_sim.tools import build_minister_tools

_content: Optional[GameContent] = None


def bind_content(content: GameContent) -> None:
    global _content
    _content = content


def _ctx() -> GameContent:
    if _content is None:
        raise RuntimeError("registry.bind_content() 未调用：GameContent 未注入。")
    return _content


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
        f"外部势力：{context.db.external_power_report()}。"
        f"详情请按需调工具查（list_regions/list_armies/inspect_memorial/check_treasury 等）。"
    )


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
    instructions = [
        c.game_world_prompt,
        c.minister_agent_prompt,
        f"你当前扮演：{character_context_with_db(character, context.db)}。",
        f"你与皇帝的多轮对话会持续到本{TURN_UNIT}退朝；同一{TURN_UNIT}复召时要接续此前奏对，不要重置记忆。",
        f"进殿前，皇帝会先把本{TURN_UNIT}奏报、钱粮、地区、军队和派系态势作为一段 JSON 上下文喂给你；"
        "你只需简短回一句臣已知会，然后等皇帝问话。所有动态数据均可随时通过工具复查。",
        "【退殿规则】只有皇帝的输入中明确出现退下指令（如：退下、好去办吧、朕知道了卿退下等），"
        "才可调 dismiss_minister() 结束本次对话；皇帝要召见别人（如：传袁崇焕来）才可调 summon_minister(name)。"
        "禁止因自己的角色扮演台词（如写了退场动作）而主动调这两个 tool。等皇帝指令，不要自行判断。",
    ]
    return Agent(
        name=character.name,
        id=f"minister-{character.name}",
        session_id=session_id or f"minister-{character.name}-turn-{context.state.turn}",
        db=agno_db,
        model=model,
        instructions=instructions,
        tools=build_minister_tools(character, context),
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
        self.briefed: set = set()  # 已喂过本月 court_brief 的大臣
        characters = _ctx().characters
        self.session_ids: Dict[str, str] = {
            name: f"minister-{name}-turn-{context.state.turn}"
            for name in characters
        }
        self._court_brief: str = build_court_brief(context)
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

    def _brief_if_needed(self, character: Character) -> None:
        """首次召见时把本月动态上下文作为 user message 喂给大臣（不进 system prompt → 不破前缀缓存）。"""
        if character.name in self.briefed:
            return
        agent = self.agents[character.name]
        prompt = (
            f"本{TURN_UNIT}朝会初始化上下文（钱粮、奏报、地区、军队、派系等，进殿前请知会，不需详细回奏）：\n"
            f"{self._court_brief}\n\n"
            "请简短回一句“臣已知会”，然后等皇帝问话。"
        )
        try:
            agent.run(prompt)
        except Exception:
            pass
        self.briefed.add(character.name)

    def get(self, character: Character) -> Agent:
        self._brief_if_needed(character)
        return self.agents[character.name]

    def refresh(self, character_name: str) -> None:
        character = _ctx().characters.get(character_name)
        if character is None:
            return
        self.briefed.discard(character_name)
        self.agents[character.name] = self._create(character)
