"""诏书生成与回合结算：拟诏、推演落库、无诏推进。L7。

纯逻辑（无 input()）；resolve_directives 的 print 是诊断输出，非交互。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from agno.db.sqlite import SqliteDb

from ming_sim.agents import (
    _dump_llm_messages,
    create_chapter_memory_agent,
    create_decree_writer_agent,
    create_ending_summary_agent,
    create_json_sanitizer_agent,
    create_minister_recap_agent,
    create_score_extractor_module_agent,
    create_season_simulator_agent,
    run_agent_stream_text,
    run_agent_text,
)
from ming_sim.constants import TURN_UNIT
from ming_sim.context import ENDING_LABELS, ENDING_ONGOING, ENDING_TIMEOUT, victory_status
from ming_sim.db import GameDB
from ming_sim.exceptions import LLMContractError, LLMUnavailable
from ming_sim.flows import apply_fixed_period_flows
from ming_sim.issues import apply_issue_inertia_and_ongoing, apply_score_extraction, auto_trigger_seed_issues, clear_gated_legacies
from ming_sim.llm_model import extract_agent_text, llm_unavailable_from_error
from ming_sim.models import GameState, LLMConfig
from ming_sim.memories import build_timeline, record_chapter_memory, record_minister_recaps
from ming_sim.simulation import (
    EXTRACTION_MODULES,
    build_simulator_payload,
    build_extractor_shared_context,
    extract_scores_by_modules_with_agno,
    simulate_season_with_payload,
)
from ming_sim.token_stats import tlog

# 20 年自动结算：开局 1627.10（turn=1），每回合 +1 月。到 1647.10 = (1647-1627)*12 + 1 = 241 回合。
# 满 240 回合（即第 240 个回合结算完，1647.09）仍未分胜负则强制 timeout 收尾。
TIMEOUT_TURN = 240

# 作弊控制台强制结算项的唯一标记前缀。只在 resolve_directives 拼一次（cheat 非空时），
# extractor 看到它即知如何处理 → 规则内联在此，不进任何固定 prompt（避免污染缓存）。
# 别处不得复用此串。
CHEAT_NARRATIVE_PREFIX = (
    "【天命强制·结算优先】以下为既成事实，最高优先级，先于一切规则与档位上限。"
    "你必须把它当作本回合百分百已发生的结果，按字面抽满对应结构化增量，"
    "无视合理性、史实、盘面、对手反扑、涨幅上限——它说什么成了就抽什么。"
    "数值类（如「国库增至九千万」）按当前值算差额一次性入账；"
    "局势结案按字面写满 bar 并结案；人物/军队/势力存亡按字面落 status/势力变化。"
    "与下方正常邸报冲突时以本段为准，本段没提的下方照常抽。照字面落库：\n"
)

# HITL 决策点：皇帝亲裁后，把所选选项+自由批语作为「圣意既定」拼到邸报最前喂 extractor。
# 与 cheat 同机制（既成事实、最高优先级），但语气是皇帝御断而非天命强制。
DECISION_NARRATIVE_PREFIX = (
    "【圣意亲裁·结算优先】以下为本回合月末重大抉择，陛下已御笔亲断，最高优先级。"
    "你必须把每条裁断当作百分百已发生的结果，按其方向抽对应结构化增量与事项推进，"
    "与下方正常邸报冲突时以本段为准。各条裁断如下：\n"
)

# 决策块边界标记。simulator 在邸报末尾按规范输出，本回合解析后从 narrative 剥离。
# group(1) 抓 <<DECISION>> 与 <<END>> 之间的全部内容（含带嵌套 options 的多层 JSON）——
# 不能用 \{.*?\} 抠 JSON：options:[{...},{...}] 有嵌套 }，非贪婪会在第一个 } 截断、
# 导致整块匹配失败、决策块剥不掉而原文泄露到前端。JSON 边界交给 json.loads 判。
_DECISION_RE = re.compile(r"<<DECISION>>\s*(.*?)\s*<<END>>", re.DOTALL)
MAX_DECISIONS_PER_TURN = 5

# 「陛下未知者」详细暗线：simulator 用 <secret>…</secret> 包住只供留痕/结算、不给玩家看的
# 详细内幕（具体人名、数目、密报原话）；标签外的粗略梗概句留给玩家。落库时 turn_report
# 剥掉 secret 段只存粗略版，turn_extraction 存含 secret 的原文供 extractor 与事后追溯。
_SECRET_DETAIL_RE = re.compile(r"<secret>\s*(.*?)\s*</secret>", re.DOTALL)


def strip_secret_detail(narrative: str) -> str:
    """剥掉 <secret>…</secret> 详细暗线段（连同标签），得到给玩家看的粗略版邸报。
    标签成对缺失/残缺也安全：仅删成对匹配段，多余裸标签一并清掉防泄漏。"""
    if not narrative:
        return narrative
    clean = _SECRET_DETAIL_RE.sub("", narrative)
    # 兜底：模型偶尔漏闭合标签，裸 <secret>/</secret> 一律删掉，绝不让标签字面泄露给玩家。
    clean = clean.replace("<secret>", "").replace("</secret>", "")
    # 删 secret 段后可能留下多余空行/行尾空格，收一下。
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def parse_decision_blocks(narrative: str) -> tuple[str, List[Dict[str, object]]]:
    """[已废弃，保留代码供回退/参考——HITL 链路随 resolve_directives 一并停用]

    新模块化 prompt 不产出 <<DECISION>> 块（用户决策：「直接输出邸报+json，暂时不
    考虑亲裁机制」），本函数与 resolve_decisions_phase2/awaiting_decision 分支
    自然停用，调用点仍在 resolve_directives 旧路径里、保留不删。

    从邸报抽 <<DECISION>>...<<END>> JSON 块，返回 (剥离后的干净邸报, 决策列表)。
    每块须含 title/context/options（2-3 项，每项 label + 可选 hint）。
    解析失败的块直接丢弃（连同标记一起剥离），不抛断——无决策块视作普通回合。
    最多取 MAX_DECISIONS_PER_TURN 条，超出忽略。
    """
    decisions: List[Dict[str, object]] = []
    for m in _DECISION_RE.finditer(narrative or ""):
        if len(decisions) >= MAX_DECISIONS_PER_TURN:
            break
        try:
            obj = json.loads(m.group(1))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        title = str(obj.get("title") or "").strip()
        raw_opts = obj.get("options")
        if not title or not isinstance(raw_opts, list):
            continue
        options: List[Dict[str, str]] = []
        for o in raw_opts:
            if not isinstance(o, dict):
                continue
            label = str(o.get("label") or "").strip()
            if not label:
                continue
            options.append({"label": label, "hint": str(o.get("hint") or "").strip()})
        if len(options) < 2:  # 至少给 2 个选项才算有效抉择
            continue
        decisions.append({
            "title": title,
            "context": str(obj.get("context") or "").strip(),
            "options": options[:3],
        })
    clean = _DECISION_RE.sub("", narrative or "").strip()
    return clean, decisions


@dataclass
class ResolveResult:
    """resolve phase1 的返回。awaiting=True 时表示需皇帝亲裁，已存决策点暂停，
    report 为空、回合未推进；调用方据此置 awaiting_decision 态弹窗。
    awaiting=False 时 report 为完整结算报告（含诏书+邸报+结局），回合已推进。"""
    awaiting: bool
    report: str = ""
    decisions: List[Dict[str, object]] = field(default_factory=list)
    # awaiting=True 时随 decisions 带回 phase1 已算好的推演上下文（decree/narrative/
    # simulator_payload/密令/记忆）。调用方（GameSession）存进程内存，phase2 原样回传续跑。
    # 不落库——决策暂停期间进程重启即丢，按既定行为重跑推演。
    resolve_ctx: Dict[str, object] = field(default_factory=dict)


def write_decree_with_agno(
    llm_config: LLMConfig,
    agno_db: SqliteDb,
    state: GameState,
    directives: List[sqlite3.Row],
    db: Optional[GameDB] = None,
) -> str:
    if not directives:
        raise LLMContractError("无草案不能拟诏。")
    # 已办结密令的 result 作为实质证据清单注入——皇帝下旨拿人/定罪时可引为依据。
    closed_evidence: List[Dict[str, object]] = []
    if db is not None:
        try:
            for o in db.list_secret_orders(status="done"):
                if o.get("result"):
                    closed_evidence.append({
                        "id": int(o["id"]), "title": o["title"],
                        "assignee": o["minister_name"], "evidence": o["result"],
                    })
        except Exception:
            closed_evidence = []
    payload = {
        "turn": {"year": state.year, "period": state.period, "turn": state.turn},
        "directives": [
            {
                "text": row["text"],
            }
            for row in directives
        ],
        "closed_secret_orders": closed_evidence,
        "instruction": "合并成一份正式诏书正文。closed_secret_orders 是已办结密令查得的实证，"
                       "若草案据某密令查办之事拿人定罪，可在诏书里引该实证为据，使罪名落到实处。",
    }
    try:
        agent = create_decree_writer_agent(llm_config, agno_db)
        run_output = agent.run(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        _dump_llm_messages(run_output, "拟诏", agent=agent)
        text = extract_agent_text(run_output)
    except LLMUnavailable:
        raise
    except Exception as error:
        raise llm_unavailable_from_error(error, "拟诏") from error
    if not text.strip():
        raise LLMContractError("拟诏输出为空。")
    return text.strip()


def advance_without_edict(state: GameState, db: GameDB) -> None:
    apply_fixed_period_flows(db, state)
    message = f"本{TURN_UNIT}退朝未下正式圣旨，诸事仍待来{TURN_UNIT}处置。"
    print("\n" + message)
    state.next_period()
    db.save_state(state)


def resolve_directives(
    state: GameState,
    db: GameDB,
    agno_db: SqliteDb,
    llm_config: LLMConfig,
    directives: List[sqlite3.Row],
    decree_text: str,
    deaths_this_turn: Optional[List[Dict[str, str]]] = None,
    debuts_this_turn: Optional[List[Dict[str, str]]] = None,
    on_event: Optional[Callable[[str, str], None]] = None,
    content=None,
    registry=None,
    cheat_directive: str = "",
    structured_directives: Optional[List[Dict[str, object]]] = None,
) -> ResolveResult:
    """两段式结算 phase1（GameSession.resolve_turn 的主链）：跑固定财政 tick →
    simulator 写**一整篇**月末邸报 → 解析 HITL 决策点。数值抽取在
    _settle_after_narrative / resolve_decisions_phase2 里另起 4 次 score_extractor
    调用，各自重读同一篇邸报抽自己那部分字段。

    on_event(kind, data): 推演过程实时回调。
    kind ∈ {stage, thinking, text}；stage 携带阶段名，thinking/text 携带增量片段。

    cheat_directive: 作弊控制台（Ctrl+~）下的强制结算指令。非空时拼到当期邸报最前面
    一起喂给 extractor，按字面当既成事实落库。唯一入口——只此一处写入标记前缀（见
    CHEAT_NARRATIVE_PREFIX），别处不得复用。

    返回 ResolveResult：simulator 邸报含决策点 → 存上下文+决策点暂停（awaiting=True，
    回合未推进）；无决策点 → 直接续跑 extractor 结算，返回完整报告（awaiting=False）。
    """
    def _emit(kind: str, data: str) -> None:
        if on_event:
            on_event(kind, data)

    before_turn = state.turn

    # 草案内容已由拟诏合并进 decree_text，simulator 只读 decree_text，不再单传逐条草案。

    # 1) 固定月度财政 tick（田赋/辽饷/军饷等，在 LLM 推演前落账）
    tlog("结算 1/4 固定月度财政 tick")
    _emit("stage", "固定月度财政入账")
    apply_fixed_period_flows(db, state)  # 落账副作用；明细不再进 simulator payload（欠饷哗变走前置事件/issue）

    # 1.6) 程序硬触发：标了 auto_trigger 的 seed 情势，gate 达标即由程序直接立项，
    #      绕过 LLM 因果判定。放在 simulator 前，使硬立的 issue 当回合即进盘面被邸报叙述。
    auto_triggered = auto_trigger_seed_issues(state, db)
    if auto_triggered:
        tlog(f"[AUTO-TRIGGER] 本回合程序硬立项 {len(auto_triggered)} 条：{[t.get('title') for t in auto_triggered]}")

    # 1.8) 历史脉络：取近几回合章节记忆注入推演（章节记忆取代旧的关键词原子检索）。
    relevant_memories: List[Dict] = []
    secret_orders_for_sim: list = []  # try 外初始化：检索失败也不能让后续 NameError
    try:
        _emit("stage", "回顾近来朝局")
        # state.turn 此刻仍是本回合（尚未 next_period），章节记忆存的是 turn-1 及更早的已结算回合。
        relevant_memories = db.list_chapter_memories(upto_turn=state.turn, recent=6)
        tlog(f"[memory/chapters] inject={len(relevant_memories)} upto_turn={state.turn}")
    except Exception as exc:
        tlog(f"[memory/chapters] 失败，跳过：{exc}")

    # 密令期限：到期 active 自动转 pending_review，保证本月核议一锤定音。
    try:
        due_orders = db.auto_submit_due_secret_orders(state)
        if due_orders:
            tlog(f"[secret_order] 到期送核议 {due_orders}")
    except Exception as exc:
        tlog(f"[secret_order] 到期送核议失败，跳过：{exc}")

    # 密令注入推演：active + pending_review 都要进（pending_review 需推演本月核议判 done/failed）
    try:
        active_orders = (
            db.list_secret_orders(status="active")
            + db.list_secret_orders(status="pending_review")
        )[:20]
        for o in active_orders:
            secret_orders_for_sim.append(db.secret_order_sim_payload(o))
        n_active = sum(1 for o in active_orders if o["status"] == "active")
        n_pending = sum(1 for o in active_orders if o["status"] == "pending_review")
        tlog(f"[secret_order] 注入推演 active={n_active} pending_review={n_pending}"
             + (f" titles={[o['title'] for o in active_orders]}" if active_orders else ""))
    except Exception as exc:
        tlog(f"[secret_order] 注入失败，跳过：{exc}")

    # 2) 推演 agent: 写邸报
    tlog("结算 2/4 推演 agent（月末邸报）")
    _emit("stage", "推演月末邸报")
    previous_narrative = db.previous_turn_summary(state) or ""
    simulator_payload = build_simulator_payload(
        state, db, decree_text, previous_narrative,
        deaths_this_turn=deaths_this_turn,
        debuts_this_turn=debuts_this_turn,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders_for_sim,
        structured_directives=structured_directives or [],
    )
    simulator = create_season_simulator_agent(
        llm_config, agno_db, state=state, db=db, simulator_payload=simulator_payload
    )
    try:
        narrative, simulator_payload = simulate_season_with_payload(
            simulator, state, db, decree_text, previous_narrative,
            deaths_this_turn=deaths_this_turn,
            debuts_this_turn=debuts_this_turn,
            relevant_memories=relevant_memories,
            secret_orders=secret_orders_for_sim,
            structured_directives=structured_directives or [],
            simulator_payload=simulator_payload,
            on_thinking=lambda c: _emit("thinking", c),
            on_text=lambda c: _emit("text", c),
        )
    except LLMUnavailable:
        # 超时（chunk 间隔超 20s）/ 连通失败等 LLM 不可用：不静默兜底推进回合，
        # 直接冒泡到 worker → SSE error，提示用户「异常了」，由用户自行读档重来。
        # 此时回合未 next_period（preresolve 自动存档即结算前状态），读档干净回滚。
        raise
    except Exception as exc:
        print(f"[WARN] 推演 agent 失败：{exc}；本{TURN_UNIT}用简化邸报兜底，跳过 LLM 结算。")
        narrative = (
            f"奉天承运皇帝诏曰：本{TURN_UNIT}推演 agent 被服务方拦截，无完整邸报。"
            f"已颁诏书：\n{decree_text}\n"
            f"固定收支已落账，事项 inertia 自然漂移；本{TURN_UNIT}无新立 issue。"
        )
        # 跳过 extractor，避免连锁失败
        db.save_turn_report(state, narrative)
        db.save_turn_extraction(
            state, decree_text=decree_text, narrative=narrative,
            extractor_output=f"[推演 agent 失败] {exc}；本回合跳过 extractor。",
        )
        apply_issue_inertia_and_ongoing(db, state, touched_ids=set())
        clear_gated_legacies(db, state)
        db.mark_directives_issued(state)
        state.next_period()
        db.save_state(state)
        return ResolveResult(
            awaiting=False,
            report=f"\n本{TURN_UNIT}颁布诏书：\n" + decree_text + "\n\n" + narrative,
        )

    # 2.4) HITL 决策点：从邸报抽 <<DECISION>> 块。有 → 存上下文+决策点，暂停等皇帝亲裁。
    #      剥离后的干净邸报落库/展示；决策点选完由 resolve_decisions_phase2 续跑结算。
    narrative, decisions = parse_decision_blocks(narrative)
    if decisions:
        tlog(f"[HITL] 检测到 {len(decisions)} 个决策点，暂停等皇帝亲裁：{[d['title'] for d in decisions]}")
        # 决策点 + 推演上下文随 ResolveResult 带回，由 GameSession 存进程内存（不落库）。
        # 给每个决策点补 idx/status，对齐旧 pending_decisions 行结构（前端/回写按 idx 寻址）。
        decisions = [
            {**d, "idx": i, "status": "pending", "choice": {}}
            for i, d in enumerate(decisions)
        ]
        resolve_ctx = {
            "decree_text": decree_text,
            "narrative": narrative,
            "simulator_payload": simulator_payload,
            "secret_orders": secret_orders_for_sim,
            "relevant_memories": relevant_memories,
        }
        return ResolveResult(awaiting=True, decisions=decisions, resolve_ctx=resolve_ctx)

    # 无决策点：透明续跑结算（cheat 仍可叠加）。
    report = _settle_after_narrative(
        state, db, agno_db, llm_config, decree_text, narrative,
        simulator_payload=simulator_payload,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders_for_sim,
        before_turn=before_turn, _emit=_emit,
        content=content, registry=registry,
        cheat_directive=cheat_directive,
    )
    return ResolveResult(awaiting=False, report=report)


def _settle_after_narrative(
    state: GameState,
    db: GameDB,
    agno_db: SqliteDb,
    llm_config: LLMConfig,
    decree_text: str,
    narrative: str,
    simulator_payload: Dict[str, object],
    relevant_memories: List[Dict],
    secret_orders: list,
    before_turn: int,
    _emit: Callable[[str, str], None],
    content=None,
    registry=None,
    cheat_directive: str = "",
    decision_directive: str = "",
) -> str:
    """两段式结算 phase2：邸报已定（已剥离决策块），跑 4 个 score_extractor 抽取 →
    落库 → 章节记忆 → 结局判定 → 回合推进。
    cheat_directive / decision_directive 各自拼到 effective_narrative 最前喂 extractor。"""
    secret_orders_for_sim = secret_orders
    # 2.5) 作弊强制项 + 圣意亲裁：拼到邸报最前面一起喂 extractor（唯一入口）。
    #      落库前文/turn_report 仍用原始 narrative，effective 版只进 extractor 与留痕。
    effective_narrative = narrative
    decision = (decision_directive or "").strip()
    if decision:
        effective_narrative = DECISION_NARRATIVE_PREFIX + decision + "\n\n" + effective_narrative
        tlog(f"[HITL] 圣意亲裁注入 extractor（{len(decision)}字）：{decision[:200]}")
    cheat = (cheat_directive or "").strip()
    if cheat:
        effective_narrative = CHEAT_NARRATIVE_PREFIX + cheat + "\n\n" + effective_narrative
        tlog(f"[CHEAT] 强制结算项注入 extractor（{len(cheat)}字）：{cheat[:200]}")

    # 3) 结算 agent: 读邸报抽 JSON
    tlog("结算 3/4 结算 agent（抽 JSON）")
    _emit("stage", "数值推演结算")
    extractor_shared_context = build_extractor_shared_context(
        db, state, effective_narrative, decree_text,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders_for_sim,
    )
    sanitizer = create_json_sanitizer_agent(llm_config, agno_db)
    extractor_input = ""
    extractor_output = ""
    extraction_failed = False
    try:
        tlog("结算 3/4 抽取（模块 module）")
        extractors = {
            module: create_score_extractor_module_agent(
                llm_config,
                agno_db,
                module,
                simulator_payload=simulator_payload,
                supplemental_context=extractor_shared_context,
            )
            for module in EXTRACTION_MODULES
        }
        extracted, extractor_output, extractor_input = extract_scores_by_modules_with_agno(
            extractors, db, state, effective_narrative, decree_text=decree_text, sanitizer=sanitizer,
            relevant_memories=relevant_memories,
            secret_orders=secret_orders_for_sim,
        )
    except Exception as exc:
        print(f"[WARN] 结算抽取失败：{exc}；本{TURN_UNIT}数值不变。")
        extracted = {}
        extraction_failed = True
        extractor_output = f"[抽取失败] {exc}"

    tlog("结算 4/4 落库 + inertia/ongoing")
    _emit("stage", "落库与事项推进")
    # 4) 月末邸报落库（下月作前文）。turn_report 给玩家看：剥掉「陛下未知者」<secret> 详细暗线，
    #    只留粗略梗概；详细版只进 extraction 留痕 + 已喂过 extractor。
    if extraction_failed:
        applied = {"issue_summary": {"advances": []}}
        db.save_turn_report(state, strip_secret_detail(narrative))
        db.save_turn_extraction(
            state,
            decree_text=decree_text,
            narrative=effective_narrative,
            extractor_input=extractor_input,
            extractor_output=extractor_output,
        )
    else:
        with db.transaction():
            applied = apply_score_extraction(
                db, state, extracted, content=content, registry=registry,
                llm_config=llm_config, agno_db=agno_db,
            )
            db.save_turn_report(state, strip_secret_detail(narrative))
            # 推演链原始输入/输出留痕，事后可追「该立的 issue 为何没立」。含 secret 详细暗线。
            db.save_turn_extraction(
                state,
                decree_text=decree_text,
                narrative=effective_narrative,  # 留痕含作弊段 + secret 详细，便于事后追「为何这么落库」
                extractor_input=extractor_input,
                extractor_output=extractor_output,
            )

    # 5) 章节记忆：LLM 把本回合诏书+邸报+落库效果浓缩成一段叙事章节，落 event_memories
    #    （chapter_summary）。失败有保底拼接，不抛断。
    _emit("stage", "记起居注")
    try:
        chapter_agent = create_chapter_memory_agent(llm_config, agno_db)
        record_chapter_memory(chapter_agent, db, state, decree_text, narrative, applied)
    except Exception as exc:
        tlog(f"[chapter-memory] 跳过：{exc}")

    # 5.5) 大臣私人对话纪要：把本回合被召见过的大臣各自的奏对压成一段私人纪要，落 event_memories
    #      （minister_recap）。月内历史交 agno session 自管（含 tool 痕迹），跨月靠这条补失忆，
    #      月初注入该大臣 system。失败/为空不抛断（铁律）。
    try:
        recap_agent = create_minister_recap_agent(llm_config, agno_db)
        record_minister_recaps(recap_agent, db, state)
    except Exception as exc:
        tlog(f"[minister-recap] 跳过：{exc}")

    # 6) 落 inertia + ongoing (未被本月 issue_advances 触动的)
    touched_ids = set()
    for adv in applied.get("issue_summary", {}).get("advances", []) or []:
        touched_ids.add(int(adv.get("issue_id") or 0))
    apply_issue_inertia_and_ongoing(db, state, touched_ids=touched_ids)

    # 7) 开局负面帝国修正：本月若达成消除条件即清除（程序判定，不靠 LLM/时长）
    clear_gated_legacies(db, state)

    # 8) 结局判定：叙事型（退位/自尽，applied 已带）→ 数值型（京畿失守）→ 到期型（20 年/240 回合）。
    #    state.turn 此刻仍是刚结算完的本回合（next_period 之前）。
    #    结局只触发一次：已 ended 的存档继续推进时不重判、不重生总评（省 token、不反复弹页）。
    outcome = None
    ended = False
    ending_text = ""
    if not state.ended:
        outcome = applied.get("victory_status") or victory_status(db, state)
        if (
            isinstance(outcome, dict)
            and outcome.get("status") == ENDING_ONGOING
            and state.turn >= TIMEOUT_TURN
        ):
            outcome = {
                "status": ENDING_TIMEOUT,
                "summary": "崇祯在位二十载，朝局至此尘埃落定，是中兴、是苟延、还是衰亡，自有史评。",
            }

        ended = isinstance(outcome, dict) and outcome.get("status") != ENDING_ONGOING
        if ended:
            # 章节记忆（含本回合）已落库，国史编纂官读全程生成结局总评。
            ending_text = _generate_ending_summary(db, state, llm_config, agno_db, outcome, _emit)
            state.ended = True
            state.ending_status = str(outcome.get("status") or "")

    db.mark_directives_issued(state)
    state.next_period()
    db.save_state(state)
    assert state.turn == before_turn + 1

    ending = ""
    if ended:
        label = ENDING_LABELS.get(str(outcome.get("status")), "结局")
        ending = f"\n\n【结局·{label}】{outcome.get('summary', '')}"
        if ending_text:
            ending += "\n\n" + ending_text
    if decree_text.strip().startswith(f"本{TURN_UNIT}无新诏"):
        full_report = f"\n本{TURN_UNIT}无新诏，承办推进：\n" + decree_text + "\n\n" + narrative + ending
    else:
        full_report = f"\n本{TURN_UNIT}颁布诏书：\n" + decree_text + "\n\n" + narrative + ending
    return full_report


def _format_decision_directive(decisions: List[Dict[str, object]]) -> str:
    """把皇帝已裁的决策点拼成喂 extractor 的「圣意亲裁」正文。
    每条：标题 + 所选选项 label/hint + 自由批语。未裁的跳过。"""
    lines: List[str] = []
    for i, d in enumerate(decisions, 1):
        choice = d.get("choice") or {}
        if not isinstance(choice, dict):
            continue
        label = str(choice.get("label") or "").strip()
        note = str(choice.get("note") or "").strip()
        if not label and not note:
            continue
        title = str(d.get("title") or f"抉择{i}").strip()
        seg = f"{i}. 【{title}】陛下御断：{label or '（未选预设项）'}"
        hint = str(choice.get("hint") or "").strip()
        if hint:
            seg += f"（倾向：{hint}）"
        if note:
            seg += f"。朱批：{note}"
        lines.append(seg)
    return "\n".join(lines)


def resolve_decisions_phase2(
    state: GameState,
    db: GameDB,
    agno_db: SqliteDb,
    llm_config: LLMConfig,
    resolve_ctx: Dict[str, object],
    decisions: List[Dict[str, object]],
    on_event: Optional[Callable[[str, str], None]] = None,
    content=None,
    registry=None,
    cheat_directive: str = "",
) -> str:
    """[已废弃，保留代码供回退/参考——HITL 链路随 resolve_directives 一并停用]

    旧 phase2：皇帝亲裁完，用 phase1 带回的进程内存上下文（resolve_ctx）+ 已裁决策点
    （decisions，含 choice）续跑结算。两者由 GameSession 持有并传入（不落库，重启即丢）。
    新模块化路径不产出决策点、awaiting 恒为 False，本函数不再被 resolve_turn 调用，
    GameSession.submit_decisions 仍引用它（保留不删，避免遗留态报错）。
    返回完整结算报告。"""
    def _emit(kind: str, data: str) -> None:
        if on_event:
            on_event(kind, data)

    if not resolve_ctx:
        raise LLMContractError("无待决推演上下文，无法续跑结算（phase1 未暂停或已结算）。")
    decision_directive = _format_decision_directive(decisions)
    before_turn = state.turn
    sp = resolve_ctx.get("simulator_payload")
    sec = resolve_ctx.get("secret_orders")
    mem = resolve_ctx.get("relevant_memories")
    report = _settle_after_narrative(
        state, db, agno_db, llm_config,
        decree_text=str(resolve_ctx.get("decree_text", "")),
        narrative=str(resolve_ctx.get("narrative", "")),
        simulator_payload=sp if isinstance(sp, dict) else {},
        relevant_memories=mem if isinstance(mem, list) else [],
        secret_orders=sec if isinstance(sec, list) else [],
        before_turn=before_turn, _emit=_emit,
        content=content, registry=registry,
        cheat_directive=cheat_directive,
        decision_directive=decision_directive,
    )
    return report


def _generate_ending_summary(
    db: GameDB,
    state: GameState,
    llm_config: LLMConfig,
    agno_db: SqliteDb,
    outcome: Dict[str, object],
    _emit: Callable[[str, str], None],
) -> str:
    """国史编纂官读全部章节记忆生成结局总评，落库 ending_summary（含逐回合时间线）。
    LLM 失败时用章节拼保底总评。返回总评正文（也已落库）。"""
    chapters = db.list_chapter_memories(upto_turn=state.turn)
    timeline = build_timeline(db, upto_turn=state.turn)
    summary_text = ""
    try:
        _emit("stage", "国史编纂结局总评")
        ending_agent = create_ending_summary_agent(llm_config, agno_db)
        payload = {
            "ending": {"status": outcome.get("status"), "summary": outcome.get("summary")},
            "chapters": chapters,
            "final_state": {
                "year": state.year, "period": state.period, "turn": state.turn,
                "metrics": dict(state.metrics),
            },
        }
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=False)
        tlog(f"[ending-summary/INPUT] chapters={len(chapters)} ({len(payload_json)}字)")
        summary_text = run_agent_text(ending_agent, payload_json, tag="ending-summary").strip()
        tlog(f"[ending-summary/OUTPUT] ({len(summary_text)}字)")
    except Exception as exc:
        tlog(f"[ending-summary] LLM 失败，走保底：{exc}")

    if not summary_text:
        bits = [str(outcome.get("summary") or "")]
        for c in chapters[-6:]:
            body = (c.get("body") or "").strip()
            if body:
                bits.append(f"{c['year']}年{c['period']}月：{body}")
        summary_text = "\n".join(b for b in bits if b)

    try:
        db.save_ending_summary(
            state, str(outcome.get("status") or ""), summary_text, timeline,
        )
    except Exception as exc:
        tlog(f"[ending-summary] 落库失败：{exc}")
    return summary_text
