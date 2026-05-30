"""GameSession：CLI 与 Web 共用的统一回合流转层。L8。

不含 input()/print()——只持有状态、跑底层逻辑、返回 dataclass。
召见对话的 tool 截获、拟旨 draft 流转、诏书结算都收在这里，
CLI 和 Web 各自只做 I/O 包装。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from ming_sim.agents import bind_content as _bind_agents, create_memory_retrieval_agent
from ming_sim.agents import parse_agent_json, run_agent_text
from ming_sim.constants import TURN_UNIT
from ming_sim.content import GameContent
from ming_sim.context import (
    bind_content as _bind_context,
    character_from_name,
    match_minister_from_text,
    victory_status,
)
from ming_sim.db import GameDB, infer_office_type_from_office, normalize_office
from ming_sim.decree import advance_without_edict, resolve_directives, write_decree_with_agno
from ming_sim.issues import bind_content as _bind_issues
from ming_sim.llm_model import create_agno_db, extract_agent_text, verify_llm_available
from ming_sim.models import Character, CourtContext, GameState, LLMConfig
from ming_sim.paths import user_data_path
from ming_sim.registry import MinisterRegistry, bind_content as _bind_registry
from ming_sim.skills import bind_content as _bind_skills


AUTO_SAVE_PREFIX = "auto_"
AUTO_SAVE_KEEP_TURNS = 3  # 每个 campaign 保留最近 N 个 turn 的全部自动存档（每 turn 含 begin + preresolve）


def prune_auto_saves(saves_dir: str, campaign_id: str, keep_turns: int = AUTO_SAVE_KEEP_TURNS) -> None:
    """清理自动存档：只清同一个 campaign_id，按 turn 分组，绝不碰手动存档。"""
    import os as _os
    import re as _re

    if not _os.path.isdir(saves_dir):
        return
    legacy_auto = _re.compile(rf"^{_re.escape(AUTO_SAVE_PREFIX)}\d{{4}}_\d{{2}}_t\d{{4}}_.+\.db$")
    for f in _os.listdir(saves_dir):
        if legacy_auto.match(f):
            try:
                _os.remove(_os.path.join(saves_dir, f))
            except OSError:
                pass
    campaign_id = (campaign_id or "").strip()
    if not campaign_id:
        return
    buckets: Dict[int, List[str]] = {}
    for f in _os.listdir(saves_dir):
        if not (f.startswith(f"{AUTO_SAVE_PREFIX}{campaign_id}_") and f.endswith(".db")):
            continue
        m = _re.search(r"_t(\d+)_", f)
        if not m:
            continue
        buckets.setdefault(int(m.group(1)), []).append(f)
    keep = max(1, int(keep_turns or 1))
    keep_turn_nums = set(sorted(buckets.keys(), reverse=True)[:keep])
    for turn_num, files in buckets.items():
        if turn_num in keep_turn_nums:
            continue
        for stale in files:
            try:
                _os.remove(_os.path.join(saves_dir, stale))
            except OSError:
                pass


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
    appointed_minister: str = ""   # 吏部本轮铨选新任的人物姓名（已可召见）
    registered_minister: str = ""  # 名册外史实/用户确认人物建档后可召见
    displaced_minister: str = ""   # 因新任腾缺被罢黜（dismissed）的原任者姓名
    refresh_ministers: List[str] = field(default_factory=list)
    secret_order_id: int = 0       # 本轮新建密令 id（0=未下密令）


@dataclass
class TurnSnapshot:
    year: int
    period: int
    turn: int
    phase: str
    metrics: Dict[str, int]
    deaths_this_turn: List[Dict[str, str]] = field(default_factory=list)
    previous_summary: str = ""


def _find_candidate_by_name(content: GameContent, name: str) -> Optional[str]:
    """后宫 candidate 升格时，extractor 输出的称呼（如'李氏雪凝'）可能与原名（'李雪凝'）
    不完全一致。在 content.characters 里找：精确匹配 → aliases 含 name → name 含原名/原名含 name。
    返回 content.characters 里的原始 key，找不到返回 None。
    只对 office_type='后宫' 且 status='candidate' 的人物做匹配。"""
    # 精确匹配
    if name in content.characters:
        c = content.characters[name]
        if c.office_type == "后宫" and c.status == "candidate":
            return name
    # aliases 匹配 & 子串匹配
    for key, c in content.characters.items():
        if c.office_type != "后宫" or c.status != "candidate":
            continue
        if name in (c.aliases or []):
            return key
        # 子串匹配（直接）
        if key in name or name in key:
            return key
    return None


def _find_existing_minister(content: GameContent, name: str) -> Optional[str]:
    """铨选查重：拟任者是否已在册（非 candidate）。精确名 → aliases 命中。
    不做子串互含——'李标' vs '标' 那种巧合会误拒同义改写。
    后宫人物不在此查（走 _find_candidate_by_name）。返回在册原始 key，无则 None。"""
    if name in content.characters:
        c = content.characters[name]
        if c.office_type != "后宫" and c.status != "candidate" and c.power_id == "ming":
            return name
    for key, c in content.characters.items():
        if c.office_type == "后宫" or c.status == "candidate" or c.power_id != "ming":
            continue
        if name in (c.aliases or []):
            return key
    return None


def apply_appointment(
    db: GameDB,
    state: GameState,
    content: GameContent,
    registry: Optional[MinisterRegistry],
    data: Dict[str, object],
) -> Tuple[str, str]:
    """诏书任命/吏部铨选共用落地：建档入库 + 注册 Agent，本回合即可召见。
    LLM（吏部 propose_appointment 或档房 appointments 三道闸）已判过史实合理性；
    代码端只做姓名查重与字段兜底，不做历史校验。
    返回 (新任者姓名, 被腾缺罢黜者姓名)；任一无则该位留空串。
    payload 不合法、重名、approved=false 则返回 ("", "")。

    职位替换：data["replaces"] 填现任者姓名时，把其 status 改 dismissed 腾缺
    （由吏部 LLM 判定占缺者，代码端不做职位字面校验，符合无 fallback 约束）。

    后宫纳妃：data 含 office_type="后宫" 时走后宫路径——office 记称号（贵妃/嫔/才人等），
    faction 留空（填"后宫"），注册 Agent 以 consort_agent_prompt 为底。

    candidate 升格：若 name 能匹配现有 candidate（含 aliases/子串），
    走 UPDATE（保留原 style/skills/portrait_id），不新建记录。
    """
    if not data:
        return ("", "")
    if "approved" in data and not bool(data.get("approved")):
        return ("", "")
    name = str(data.get("name") or "").strip()
    office = str(data.get("office") or "").strip()
    if not name or not office:
        return ("", "")
    is_consort = str(data.get("office_type") or "").strip() == "后宫"
    # 朝臣多职统一逗号分隔（后宫记称号，不动）；与 db 层 normalize_office 同源。
    if not is_consort:
        office = normalize_office(office)
    office_type = "后宫" if is_consort else infer_office_type_from_office(office, str(data.get("office_type") or "待铨").strip())

    # ── 后宫 candidate 升格路径 ──────────────────────────────────────
    if is_consort:
        original_key = _find_candidate_by_name(content, name)
        if original_key is not None:
            # 升格：UPDATE DB 里的记录，保留原 style/skills/portrait_id
            character = content.characters[original_key]
            character.office = office
            character.faction = "后宫"
            character.status = "active"
            # 若还没有 portrait_id，补分配
            if not character.portrait_id:
                character.portrait_id = db.next_pool_portrait_id("consort_pool_")
            db.conn.execute(
                """UPDATE characters SET office=?, office_type='后宫', faction='后宫',
                   status='active', status_reason='诏书册封', status_changed_turn=?,
                   portrait_id=CASE WHEN portrait_id='' THEN ? ELSE portrait_id END
                   WHERE name=?""",
                (office, state.turn, character.portrait_id, original_key),
            )
            db.conn.execute(
                """INSERT INTO character_offices (character_name, office_title, office_type, source)
                   VALUES (?, ?, '后宫', '诏书册封')
                   ON CONFLICT(character_name) DO UPDATE SET
                       office_title=excluded.office_title,
                       office_type=excluded.office_type,
                       source=excluded.source,
                       updated_at=CURRENT_TIMESTAMP""",
                (original_key, office),
            )
            db.conn.commit()
            # 若 extractor 用了新称呼，在 content 里建别名指向原对象
            if name != original_key:
                content.characters[name] = character
            if registry is not None:
                registry.register(character)
            return (original_key, "")  # 返回原始 key，保持一致

    # ── 普通路径查重：精确名 + aliases 命中即拒，不重复建档 ──────────
    if not is_consort:
        existing = _find_existing_minister(content, name)
        if existing is not None:
            return ("", "")
    elif name in content.characters and content.characters[name].status != "candidate":
        return ("", "")

    # ── 职位替换：腾缺现任者 → dismissed ───────────────────────────
    displaced = ""
    replaces = str(data.get("replaces") or "").strip()
    if not is_consort and replaces and replaces in content.characters:
        old = content.characters[replaces]
        if old.status == "active":
            db.set_character_status(
                state, replaces, "dismissed",
                reason=f"{office}改授{name}，原任去职",
            )
            old.status = "dismissed"
            displaced = replaces

    faction = "后宫" if is_consort else str(data.get("faction") or "中立").strip()
    if not is_consort and faction not in content.factions:
        faction = "中立"
    character = Character(
        name=name,
        office=office,
        office_type=office_type,
        faction=faction,
        aliases=[],
        personal_skills=[],
        loyalty=60, ability=55, integrity=60, courage=50,
        style="新入宫闱" if is_consort else "新任未详",
        power_id="ming",
        status="active",
    )
    content.characters[name] = character
    db.add_character(state, character)
    # add_character 已写入并分配 portrait_id，回写到内存对象
    row = db.conn.execute(
        "SELECT portrait_id FROM characters WHERE name=?", (name,)
    ).fetchone()
    if row:
        character.portrait_id = str(row["portrait_id"])
    if registry is not None:
        registry.register(character)
    return (name, displaced)


def _sync_offices_from_db_impl(content: GameContent, db: "GameDB") -> None:
    """启动/读档时以 DB characters 表重建内存人物表。
    DB 是持久化真相；不要在这里修写 DB。"""
    rows = db.conn.execute(
        """
        SELECT name, office, office_type, faction, aliases, personal_skills,
               loyalty, ability, integrity, courage, style,
               birth_year, historical_death_year, historical_death_month,
               debut_year, debut_month, status, portrait_id, power_id, location,
               summary
        FROM characters
        """
    ).fetchall()
    characters: Dict[str, Character] = {}
    for row in rows:
        name = row["name"]
        office_type = infer_office_type_from_office(row["office"], row["office_type"])
        import json as _json

        try:
            aliases = _json.loads(row["aliases"] or "[]")
        except (TypeError, ValueError):
            aliases = []
        if not isinstance(aliases, list):
            aliases = []
        try:
            personal_skills = _json.loads(row["personal_skills"] or "[]")
        except (TypeError, ValueError):
            personal_skills = []
        if not isinstance(personal_skills, list):
            personal_skills = []
        characters[name] = Character(
            name=name,
            office=row["office"],
            office_type=office_type,
            faction=row["faction"],
            aliases=[str(item) for item in aliases if str(item).strip()],
            personal_skills=[str(item) for item in personal_skills if str(item).strip()],
            loyalty=int(row["loyalty"]),
            ability=int(row["ability"]),
            integrity=int(row["integrity"]),
            courage=int(row["courage"]),
            style=row["style"],
            birth_year=int(row["birth_year"]),
            historical_death_year=int(row["historical_death_year"]),
            historical_death_month=int(row["historical_death_month"]),
            debut_year=int(row["debut_year"]),
            debut_month=int(row["debut_month"]),
            status=row["status"],
            power_id=row["power_id"],
            location=row["location"],
            portrait_id=row["portrait_id"],
            summary=row["summary"],
        )
    content.characters = characters


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
        _sync_offices_from_db_impl(self.content, self.db)
        self.agno_db = create_agno_db(db_path)
        self.state = self.db.load_state(start_ym)
        self.deaths_this_turn: List[Dict[str, str]] = []
        self.debuts_this_turn: List[Dict[str, str]] = []
        self.power_renames_this_turn: List[Dict[str, object]] = []
        self.previous_summary = ""
        self.registry: Optional[MinisterRegistry] = None
        self.temporary_characters: Dict[str, Character] = {}
        self.last_decree = ""
        self.last_report = ""
        self._begun = False

    # ── 回合生命周期 ──────────────────────────────────────────────────────

    def begin_turn(self) -> TurnSnapshot:
        """加载/刷新本回合：历史卒、上回合奏报、重建 registry。幂等。"""
        self.state = self.db.load_state()
        self.deaths_this_turn = self.db.apply_historical_deaths(self.state)
        self.debuts_this_turn = self.db.apply_historical_debuts(self.state)
        self.power_renames_this_turn = self.db.apply_historical_power_renames(self.state)
        _sync_offices_from_db_impl(self.content, self.db)
        self.previous_summary = self.db.previous_turn_summary(self.state) or ""
        context = CourtContext(state=self.state, db=self.db, previous_summary=self.previous_summary)
        self.registry = MinisterRegistry(self.llm_config, self.agno_db, context)
        self.last_decree = ""
        self.last_report = ""
        if self.state.turn_phase not in (TurnPhase.SUMMONING.value, TurnPhase.REVIEWING.value):
            self.state.turn_phase = TurnPhase.SUMMONING.value
            self.db.save_state(self.state)
        self._begun = True
        self.auto_save("begin")
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
        # 状态以 DB 为准（历史卒/登场/罢黜均落 DB）；offstage 未登场者不进名单。
        views: List[MinisterView] = []
        for c in self.content.characters.values():
            if getattr(c, "power_id", "ming") != "ming":
                continue
            status, _ = self.db.get_character_status(c.name)
            if status == "offstage":
                continue
            views.append(MinisterView(
                name=c.name, office=c.office, office_type=c.office_type,
                faction=c.faction, status=status,
            ))
        return views

    def _character(self, name: str) -> Character:
        if name in self.temporary_characters:
            return self.temporary_characters[name]
        return character_from_name(name)

    def _retrieve_memories_for_message(self, message: str) -> str:
        """用轻量 retrieval agent 从皇帝输入提关键词，检索相关旧事注入message头部。"""
        from ming_sim.token_stats import tlog
        try:
            import json
            tlog(f"[MEM-IO/chat-memory-retrieval/INPUT] ({len(message)}字): {message!r}")
            retrieval_agent = create_memory_retrieval_agent(self.llm_config, self.agno_db)
            raw = run_agent_text(retrieval_agent, message, tag="chat-memory-retrieval")
            tlog(f"[MEM-IO/chat-memory-retrieval/OUTPUT] ({len(raw)}字):\n{raw}")
            kw_data = parse_agent_json(raw, "对话记忆检索")
            keywords: list = []
            for field in ("characters", "regions", "armies", "powers", "keywords"):
                keywords.extend(str(k) for k in (kw_data.get(field) or []) if k)
            if not keywords:
                tlog("[MEM-IO/chat-memory-retrieval/INJECT] keywords=[] 跳过注入")
                return message
            memories = self.db.get_memories_by_keywords(keywords, turn=self.state.turn, limit=6)
            if not memories:
                tlog(f"[MEM-IO/chat-memory-retrieval/INJECT] keywords={keywords} 无命中")
                return message
            tlog(f"[chat/memory-retrieval] keywords={keywords} hit={len(memories)}")
            tlog(f"[MEM-IO/chat-memory-retrieval/INJECT] full={json.dumps(memories, ensure_ascii=False)}")
            lines = ["【相关旧事】"]
            for m in memories:
                lines.append(
                    f"- #{m['id']} {m['year']}年{m['period']}月 {m['subject_id']}："
                    f"{m['title']}。起因：{m['cause']}。结果：{m['outcome']}。"
                )
            new_msg = "\n".join(lines) + "\n\n" + message
            tlog(f"[MEM-IO/chat-memory-retrieval/FINAL-MSG] ({len(new_msg)}字):\n{new_msg}")
            return new_msg
        except Exception as exc:
            tlog(f"[chat/memory-retrieval] 失败跳过：{exc}")
            return message

    def _temporary_character(self, name: str) -> Character:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("临时召见姓名不能为空。")
        existing = self.temporary_characters.get(clean_name)
        if existing is not None:
            return existing
        character = Character(
            name=clean_name,
            office="御前临时召见",
            office_type="临时召见",
            faction="未定",
            aliases=[clean_name],
            personal_skills=[],
            loyalty=50,
            ability=50,
            integrity=50,
            courage=50,
            style="身份未详，奉旨临时入殿",
            power_id="ming",
            status="active",
            summary="此人未入本局人物档，奉旨临时召对。若史实有官职/身份，照实奏对；若无，亦不得编造。所属势力、现任差遣以本人据实交代为准。",
        )
        self.temporary_characters[clean_name] = character
        if self.registry is not None:
            self.registry.register_runtime(character)
        return character

    def summon_character(
        self,
        name_or_text: str,
        current: Optional[Character] = None,
        allow_temporary: bool = True,
    ) -> Tuple[Character, bool]:
        """召见人物：优先匹配正式名册；匹配不到则创建运行时临时人物。返回 (人物, 是否临时)。"""
        target = match_minister_from_text(name_or_text, current)
        if target is not None:
            return (target, False)
        clean_name = str(name_or_text or "").strip()
        if clean_name in self.content.characters:
            return (self.content.characters[clean_name], False)
        if not allow_temporary:
            raise ValueError(f"人物未建档：{clean_name}")
        return (self._temporary_character(clean_name), True)

    def can_summon(self, character: Character) -> Tuple[bool, str]:
        if character.name in self.temporary_characters:
            return (True, "")
        status, reason = self.db.get_character_status(character.name)
        if status == "active":
            return (True, "")
        label = {
            "offstage": "尚未登场",
            "dismissed": "已罢黜",
            "imprisoned": "下狱",
            "exiled": "流放",
            "retired": "致仕",
            "dead": "已故",
        }.get(status, status)
        return (False, f"{character.name}{label}，无法召见。" + (reason or ""))

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
        # 注入动态上下文（年月、朝会盘面、上回合旧事、自己名下密令），
        # 确保 system prompt 静态部分跨月不变、前缀缓存命中。
        # 这些信息原本在 create_minister_agent 的 instructions 末尾，现移至 user message。
        from ming_sim.registry import build_court_brief, build_memory_brief, build_secret_order_brief
        reg_context = self.registry.context
        month_block_parts = [
            f"当前为 {self.state.year} 年 {self.state.period} 月（第 {self.state.turn} 回合）。"
            "作答涉及时序（某事多久前、某人是否已亡、某限期是否到）时以此为准。",
            f"本{TURN_UNIT}朝会盘面：{build_court_brief(reg_context)}",
        ]
        mem = build_memory_brief(character, reg_context)
        if mem:
            month_block_parts.append(mem)
        sec = build_secret_order_brief(character, reg_context)
        if sec:
            month_block_parts.append(sec)
        dynamic_context = "\n\n".join(month_block_parts)
        augmented = self._retrieve_memories_for_message(message)
        # 本回合已核定草案随大臣议事滚动累加，agent system 在月初冻结拿不到——
        # 每次 chat 前置实时 draft_line 到 user message 头，确保大臣看得到兄弟大臣最新动作。
        draft_line = self.registry.build_draft_line()
        if draft_line and draft_line != "无":
            augmented = f"【本{TURN_UNIT}已核定草案】{draft_line}\n\n{augmented}"
        augmented = f"{dynamic_context}\n\n{augmented}"
        run_output = agent.run(augmented)
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
                if next_name:
                    try:
                        target, _is_temporary = self.summon_character(next_name, character, allow_temporary=False)
                    except ValueError:
                        target = None
                    if target is not None:
                        ok, _reason = self.can_summon(target)
                        if ok:
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
                        actor=character.name, notes=f"由{character.name}拟旨入档", status="pending",
                    )
                    result.proposed_directive = DirectiveView(
                        id=directive_id, text=draft_text, status="pending",
                        source="大臣拟旨", notes=f"由{character.name}拟旨入档",
                    )
            elif tool_name == "propose_appointment" or tool_result.startswith("__pending_appointment__"):
                payload = tool_result.removeprefix("__pending_appointment__").strip()
                appointed, displaced = self._apply_appointment(payload, character)
                if appointed:
                    result.appointed_minister = appointed
                    result.refresh_ministers.append(appointed)
                if displaced:
                    result.displaced_minister = displaced
                    result.refresh_ministers.append(displaced)
            elif tool_name == "register_unlisted_person" or tool_result.startswith("__pending_unlisted_person__"):
                payload = tool_result.removeprefix("__pending_unlisted_person__").strip()
                registered, summon_after = self._apply_unlisted_person_registration(payload)
                if registered:
                    result.registered_minister = registered
                    result.refresh_ministers.append(registered)
                    if summon_after:
                        result.court_action = "summon"
                        result.next_minister = registered
            elif tool_name == "issue_secret_order" or tool_result.startswith("__secret_order_registered__") or tool_result.startswith("__secret_order__"):
                if tool_result.startswith("__secret_order_registered__"):
                    # 工具已直接落库，提取 order_id
                    try:
                        order_id = int(tool_result.split("__")[3])
                    except Exception:
                        order_id = 0
                else:
                    payload = tool_result.removeprefix("__secret_order__").strip()
                    order_id = self._apply_secret_order(payload, character.name)
                if order_id:
                    result.secret_order_id = order_id
            # report_secret_order_progress / submit_secret_order_for_review 工具内部直接落库，session 无需拦截
            # 密令结案 done/failed 由月末推演 + extractor 写入，不再走大臣工具
        return result

    def _apply_appointment(self, payload: str, appointer: Character) -> Tuple[str, str]:
        """吏部 propose_appointment 落地：建档入库 + 注册 Agent，本回合即可召见。
        吏部尚书 LLM 已判过史实合理性；代码端只做姓名查重与字段兜底，不做历史校验。
        返回 (新任者姓名, 被腾缺罢黜者姓名)；payload 不合法或重名则返回 ("", "")。"""
        import json as _json
        try:
            data = _json.loads(payload) if payload else {}
        except (ValueError, TypeError):
            return ("", "")
        return apply_appointment(self.db, self.state, self.content, self.registry, data)

    def _apply_unlisted_person_registration(self, payload: str) -> Tuple[str, bool]:
        """登记史实未预设/用户确认背景的人物，进入本局正式可召见人物池。"""
        import json as _json
        try:
            data = _json.loads(payload) if payload else {}
        except (ValueError, TypeError):
            return ("", False)
        if not isinstance(data, dict):
            return ("", False)
        name = str(data.get("name") or "").strip()
        office = str(data.get("office") or "").strip()
        office_type = str(data.get("office_type") or "").strip()
        if not name or not office or not office_type:
            return ("", False)
        aliases_raw = data.get("aliases") or []
        aliases = [str(alias).strip() for alias in aliases_raw if str(alias).strip()] if isinstance(aliases_raw, list) else []
        if _find_existing_minister(self.content, name) is not None:
            return ("", False)
        for alias in aliases:
            if _find_existing_minister(self.content, alias) is not None:
                return ("", False)
        faction = str(data.get("faction") or "中立").strip()
        if faction not in self.content.factions:
            faction = "中立"
        source_kind = str(data.get("source") or "historical").strip()
        if source_kind == "historical":
            source_label = "史实人物补档"
            style = "史实补档，待召对细察"
            loyalty = 62
        elif source_kind == "user_confirmed":
            source_label = "皇帝确认背景补档"
            style = "陛下点名，底细待察"
            loyalty = 60
        else:
            source_label = "名册外人物补档"
            style = "名册外补档，待召对细察"
            loyalty = 60
        character = Character(
            name=name,
            office=office,
            office_type=office_type,
            faction=faction,
            aliases=aliases,
            personal_skills=[],
            loyalty=loyalty,
            ability=55,
            integrity=60,
            courage=55,
            style=style,
            power_id="ming",
            status="active",
            summary=str(data.get("summary") or "").strip(),
        )
        self.content.characters[name] = character
        self.db.add_character(self.state, character, source=source_label)
        row = self.db.conn.execute(
            "SELECT portrait_id FROM characters WHERE name=?", (name,)
        ).fetchone()
        if row:
            character.portrait_id = str(row["portrait_id"])
        if self.registry is not None:
            self.registry.register(character)
        self.temporary_characters.pop(name, None)
        return (name, bool(data.get("summon_after", True)))

    def _apply_secret_order(self, payload: str, minister_name: str) -> int:
        """issue_secret_order 哨兵落库，返回新建密令 id（失败返回 0）。"""
        import json as _json
        try:
            data = _json.loads(payload) if payload else {}
        except (ValueError, TypeError):
            return 0
        if not isinstance(data, dict):
            return 0
        title = str(data.get("title") or "").strip()[:20]
        content = str(data.get("content") or "").strip()
        if not title or not content:
            return 0
        tags_raw = data.get("tags") or []
        tags = [str(k).strip() for k in tags_raw if str(k).strip()] if isinstance(tags_raw, list) else []
        assignee = str(data.get("assignee") or "").strip() or minister_name
        try:
            deadline = max(0, min(int(data.get("deadline_months") or 0), 36))
        except (TypeError, ValueError):
            deadline = 0
        print(f"[secret_order] 截获密令 minister={minister_name} assignee={assignee} title={title!r} tags={tags}")
        return self.db.create_secret_order(self.state, assignee, title, content, tags, deadline_months=deadline)

    def _apply_close_secret_order(self, payload: str) -> None:
        """report_secret_order_result 哨兵落库。"""
        import json as _json
        try:
            data = _json.loads(payload) if payload else {}
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        order_id = int(data.get("order_id") or 0)
        status = str(data.get("status") or "")
        result = str(data.get("result") or "")
        if order_id and status in {"done", "failed"}:
            print(f"[secret_order] 结案 id={order_id} status={status} result={result!r}")
            self.db.close_secret_order(order_id, status, result, self.state.turn)

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
        decree = write_decree_with_agno(self.llm_config, self.agno_db, self.state, directives, db=self.db)
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
        # 结算前先存一份：LLM 推演有可能崩，留个回滚锚点
        self.auto_save("preresolve")
        decree_text = decree or self.last_decree or write_decree_with_agno(
            self.llm_config, self.agno_db, self.state, directives, db=self.db
        )
        report = resolve_directives(
            self.state, self.db, self.agno_db, self.llm_config,
            directives, decree_text, deaths_this_turn=self.deaths_this_turn,
            debuts_this_turn=self.debuts_this_turn,
            on_event=on_event,
            content=self.content, registry=self.registry,
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
        self.state.turn_phase = TurnPhase.SUMMONING.value
        self.db.save_state(self.state)

    def victory(self) -> Dict[str, object]:
        return victory_status(self.db, self.state)

    def auto_save(self, tag: str) -> Optional[str]:
        """每回合 begin/end 自动热备一份。每个 campaign 保留最近 AUTO_SAVE_KEEP_TURNS 个回合，旧的删。
        文件名 auto_<campaign_id>_<year>_<period>_<turn>_<tag>.db；prune 只动同 campaign 的自动档，
        不碰用户手动存档。失败静默（自动存档不应阻断游戏）。"""
        try:
            import os as _os
            saves_dir = user_data_path("saves", "_keep")  # 确保父目录建好
            saves_dir = _os.path.dirname(saves_dir)
            campaign_id = (self.db.kv_get("campaign_id") or "").strip()
            if not campaign_id:
                campaign_id = uuid.uuid4().hex[:12]
                self.db.kv_set("campaign_id", campaign_id)
            fname = (
                f"{AUTO_SAVE_PREFIX}{campaign_id}_{self.state.year:04d}_"
                f"{self.state.period:02d}_t{self.state.turn:04d}_{tag}.db"
            )
            target = _os.path.join(saves_dir, fname)
            self.db.backup_to(target)
            prune_auto_saves(saves_dir, campaign_id)
            return target
        except Exception:
            return None

    def close(self) -> None:
        self.db.close()
