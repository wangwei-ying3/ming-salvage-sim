"""GameSession：CLI 与 Web 共用的统一回合流转层。L8。

不含 input()/print()——只持有状态、跑底层逻辑、返回 dataclass。
召见对话的 tool 截获、拟旨 draft 流转、诏书结算都收在这里，
CLI 和 Web 各自只做 I/O 包装。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from ming_sim.agents import bind_content as _bind_agents
from ming_sim.content import GameContent
from ming_sim.context import (
    bind_content as _bind_context,
    character_from_name,
    match_minister_from_text,
    victory_status,
)
from ming_sim.db import GameDB
from ming_sim.decree import advance_without_edict, resolve_directives, write_decree_with_agno
from ming_sim.issues import bind_content as _bind_issues
from ming_sim.llm_model import create_agno_db, extract_agent_text, verify_llm_available
from ming_sim.models import Character, CourtContext, GameState, LLMConfig
from ming_sim.registry import MinisterRegistry, bind_content as _bind_registry
from ming_sim.skills import bind_content as _bind_skills


class TurnPhase(str, Enum):
    SUMMONING = "summoning"   # 召见中：召见、对话、大臣拟旨产 pending
    REVIEWING = "reviewing"   # 核定草案：增删改、确认/驳回 pending、写诏书
    ISSUED = "issued"         # 已颁诏：resolve 完成，待 end_turn


@dataclass
class DirectiveView:
    id: int
    text: str
    status: str          # pending | draft | issued | rejected | deleted
    source: str
    notes: str
    actor: str = ""


@dataclass
class MinisterView:
    name: str
    office: str
    office_type: str
    faction: str
    status: str


@dataclass
class ChatTurnResult:
    answer: str
    court_action: str = ""   # "" | dismiss | summon | court_break | handled
    next_minister: str = ""
    proposed_directive: Optional[DirectiveView] = None
    refresh_ministers: List[str] = field(default_factory=list)


@dataclass
class TurnSnapshot:
    year: int
    period: int
    turn: int
    phase: str
    metrics: Dict[str, int]
    deaths_this_turn: List[Dict[str, str]] = field(default_factory=list)
    previous_summary: str = ""


def _bind_all_content(content: GameContent) -> None:
    """把 GameContent 注入所有 bind_content 模块。GameSession 启动时调一次。"""
    _bind_skills(content)
    _bind_context(content)
    _bind_agents(content)
    _bind_registry(content)
    _bind_issues(content)


class GameSession:
    """一局游戏的核心状态机。CLI / Web 都通过它驱动回合。"""

    def __init__(
        self,
        db_path: str,
        llm_config: LLMConfig,
        content: Optional[GameContent] = None,
        verify_llm: bool = True,
        start_ym: str = "",
    ) -> None:
        self.content = content if content is not None else GameContent.load()
        _bind_all_content(self.content)
        self.llm_config = llm_config
        if verify_llm:
            verify_llm_available(llm_config)
        self.db = GameDB(db_path, content=self.content)
        self.db.seed_static_data()
        self.agno_db = create_agno_db(db_path)
        self.state = self.db.load_state(start_ym)
        self.deaths_this_turn: List[Dict[str, str]] = []
        self.previous_summary = ""
        self.registry: Optional[MinisterRegistry] = None
        self.last_decree = ""
        self.last_report = ""
        self._begun = False

    # ── 回合生命周期 ──────────────────────────────────────────────────────

    def begin_turn(self) -> TurnSnapshot:
        """加载/刷新本回合：历史卒、上回合奏报、重建 registry。幂等。"""
        self.state = self.db.load_state()
        self.deaths_this_turn = self.db.apply_historical_deaths(self.state)
        self.previous_summary = self.db.previous_turn_summary(self.state) or ""
        context = CourtContext(state=self.state, db=self.db, previous_summary=self.previous_summary)
        self.registry = MinisterRegistry(self.llm_config, self.agno_db, context)
        self.last_decree = ""
        self.last_report = ""
        if self.state.turn_phase not in (TurnPhase.SUMMONING.value, TurnPhase.REVIEWING.value):
            self.state.turn_phase = TurnPhase.SUMMONING.value
            self.db.save_state(self.state)
        self._begun = True
        return self.turn_snapshot()

    def current_phase(self) -> TurnPhase:
        return TurnPhase(self.state.turn_phase)

    def _set_phase(self, phase: TurnPhase) -> None:
        self.state.turn_phase = phase.value
        self.db.save_state(self.state)

    def turn_snapshot(self) -> TurnSnapshot:
        return TurnSnapshot(
            year=self.state.year,
            period=self.state.period,
            turn=self.state.turn,
            phase=self.state.turn_phase,
            metrics=dict(self.state.metrics),
            deaths_this_turn=list(self.deaths_this_turn),
            previous_summary=self.previous_summary,
        )

    def end_turn(self) -> None:
        """回合结束（resolve 已推进 state.turn）；阶段回 summoning。"""
        self.state.turn_phase = TurnPhase.SUMMONING.value
        self.db.save_state(self.state)

    # ── 召见阶段 ──────────────────────────────────────────────────────────

    def list_ministers(self) -> List[MinisterView]:
        return [
            MinisterView(
                name=c.name, office=c.office, office_type=c.office_type,
                faction=c.faction, status=c.status,
            )
            for c in self.content.characters.values()
        ]

    def _character(self, name: str) -> Character:
        return character_from_name(name)

    def chat(self, minister_name: str, message: str) -> ChatTurnResult:
        """与大臣对话一轮，统一处理 court tool 截获。
        大臣 propose_directive 产生的草案以 status='pending' 入库，
        作为 proposed_directive 返回，确认/驳回由调用方下达。"""
        if self.registry is None:
            raise RuntimeError("GameSession.begin_turn() 未调用。")
        character = self._character(minister_name)
        # 控制指令（退下/换人/技能）由 CLI 层 parse_court_command 处理；
        # GameSession.chat 只负责与 agent 对话与 tool 截获。
        agent = self.registry.get(character)
        run_output = agent.run(message)
        answer = extract_agent_text(run_output)
        result = ChatTurnResult(answer=answer)
        for tool_exec in getattr(run_output, "tools", None) or []:
            tool_name = getattr(tool_exec, "tool_name", "")
            tool_result = str(getattr(tool_exec, "result", "") or "")
            if tool_name == "dismiss_minister" or tool_result == "__dismiss__":
                result.court_action = "dismiss"
            elif tool_name == "summon_minister" or tool_result.startswith("__summon__"):
                next_name = tool_result.removeprefix("__summon__").strip()
                if next_name not in self.content.characters:
                    args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                    next_name = args.get("name", "")
                target = match_minister_from_text(next_name, character) if next_name else None
                if target is not None:
                    result.court_action = "summon"
                    result.next_minister = target.name
            elif tool_name == "propose_directive" or tool_result.startswith("__pending_directive__"):
                draft_text = tool_result.removeprefix("__pending_directive__").strip()
                if not draft_text:
                    args = getattr(tool_exec, "tool_args", {}) or {}
                    draft_text = (args.get("decree_text") or "").strip()
                if draft_text:
                    directive_id = self.db.add_directive(
                        self.state, None, draft_text, "大臣拟旨",
                        notes=f"由{character.name}拟旨入档", status="pending",
                    )
                    result.proposed_directive = DirectiveView(
                        id=directive_id, text=draft_text, status="pending",
                        source="大臣拟旨", notes=f"由{character.name}拟旨入档",
                    )
        return result

    # ── 拟旨 / 草案阶段 ───────────────────────────────────────────────────

    def list_directives(self, include_pending: bool = True) -> List[DirectiveView]:
        statuses = ("pending", "draft") if include_pending else ("draft",)
        rows = self.db.list_directives(self.state, statuses=statuses)
        return [
            DirectiveView(
                id=int(r["id"]), text=str(r["text"]), status=str(r["status"]),
                source=str(r["source"] or ""), notes=str(r["notes"] or ""),
                actor=str(r["actor"] or ""),
            )
            for r in rows
        ]

    def confirm_directive(self, directive_id: int) -> None:
        self.db.confirm_directive(directive_id)

    def reject_directive(self, directive_id: int) -> None:
        self.db.reject_directive(directive_id)

    def add_directive(self, text: str, notes: str = "") -> DirectiveView:
        directive_id = self.db.add_directive(self.state, None, text, "手动新增", notes=notes)
        return DirectiveView(id=directive_id, text=text, status="draft",
                             source="手动新增", notes=notes)

    def update_directive(self, directive_id: int, text: str) -> None:
        self.db.update_directive_text(directive_id, text)

    def delete_directive(self, directive_id: int) -> None:
        self.db.delete_directive(directive_id)

    def pending_count(self) -> int:
        return self.db.count_pending_directives(self.state)

    # ── 诏书阶段 ──────────────────────────────────────────────────────────

    def enter_review(self) -> None:
        self._set_phase(TurnPhase.REVIEWING)

    def back_to_summoning(self) -> None:
        self._set_phase(TurnPhase.SUMMONING)

    def write_decree(self) -> str:
        """生成诏书。要求无 pending 残留、≥1 条 draft。"""
        if self.pending_count() > 0:
            raise ValueError(f"尚有 {self.pending_count()} 道大臣拟旨待陛下核定（准/驳），不能颁诏。")
        directives = self.db.list_directives(self.state, statuses=("draft",))
        if not directives:
            raise ValueError("无草案不能拟诏。")
        decree = write_decree_with_agno(self.llm_config, self.agno_db, self.state, directives)
        self.last_decree = decree
        return decree

    def resolve_turn(self, decree: str = "", on_event=None) -> str:
        """颁诏并推演本回合。要求无 pending 残留、≥1 条 draft。

        on_event(kind, data): 推演过程实时回调，透传给 resolve_directives。
        """
        if self.pending_count() > 0:
            raise ValueError(f"尚有 {self.pending_count()} 道大臣拟旨待陛下核定（准/驳），不能颁诏。")
        directives = self.db.list_directives(self.state, statuses=("draft",))
        if not directives:
            raise ValueError("网页/CLI 端不允许跳过回合：至少一条草案才能颁诏。")
        decree_text = decree or self.last_decree or write_decree_with_agno(
            self.llm_config, self.agno_db, self.state, directives
        )
        report = resolve_directives(
            self.state, self.db, self.agno_db, self.llm_config,
            directives, decree_text, deaths_this_turn=self.deaths_this_turn,
            on_event=on_event,
        )
        self.last_report = report
        self.last_decree = decree_text
        # resolve_directives 已 next_period + save_state；阶段标 issued
        self.state.turn_phase = TurnPhase.ISSUED.value
        self.db.save_state(self.state)
        return report

    def advance_without_decree(self) -> None:
        """CLI 退朝无草案：仅财政 tick + 推进。"""
        advance_without_edict(self.state, self.db)

    def victory(self) -> Dict[str, object]:
        return victory_status(self.db, self.state)

    def close(self) -> None:
        self.db.close()
