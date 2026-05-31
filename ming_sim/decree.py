"""诏书生成与回合结算：拟诏、推演落库、无诏推进。L7。

纯逻辑（无 input()）；resolve_directives 的 print 是诊断输出，非交互。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Callable, Dict, List, Optional

from agno.db.sqlite import SqliteDb

from ming_sim.agents import (
    create_chat_memory_agent,
    create_decree_writer_agent,
    create_json_sanitizer_agent,
    create_memory_extractor_agent,
    create_memory_retrieval_agent,
    create_score_extractor_module_agent,
    create_season_simulator_agent,
    parse_agent_json,
    run_agent_text,
)
from ming_sim.constants import TURN_UNIT
from ming_sim.context import victory_status
from ming_sim.db import GameDB
from ming_sim.exceptions import LLMContractError, LLMUnavailable
from ming_sim.flows import apply_fixed_period_flows, apply_natural_disasters
from ming_sim.issues import apply_issue_inertia_and_ongoing, apply_score_extraction, auto_trigger_seed_issues, clear_gated_legacies, advance_event_phases, record_event_narrative
from ming_sim.llm_model import extract_agent_text, llm_unavailable_from_error
from ming_sim.models import GameState, LLMConfig
from ming_sim.memories import (
    extract_all_chat_memories,
    extract_event_memories_with_agent,
    record_event_memories_from_resolution,
)
from ming_sim.simulation import (
    EXTRACTION_MODULES,
    build_simulator_payload,
    build_extractor_shared_context,
    extract_scores_by_modules_with_agno,
    simulate_season_with_payload,
)
from ming_sim.token_stats import tlog

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
        text = extract_agent_text(agent.run(json.dumps(payload, ensure_ascii=False, sort_keys=True)))
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
    db.record_log(state, message)
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
) -> str:
    """on_event(kind, data): 推演过程实时回调。
    kind ∈ {stage, thinking, text}；stage 携带阶段名，thinking/text 携带增量片段。

    cheat_directive: 作弊控制台（Ctrl+~）下的强制结算指令。非空时拼到当期邸报最前面
    一起喂给 extractor，按字面当既成事实落库。唯一入口——只此一处写入标记前缀（见
    CHEAT_NARRATIVE_PREFIX），别处不得复用。
    """
    def _emit(kind: str, data: str) -> None:
        if on_event:
            on_event(kind, data)

    if not directives:
        advance_without_edict(state, db)
        return f"本{TURN_UNIT}未颁正式诏书。"

    before_turn = state.turn

    # 1) 收集草案精简信息(不调 LLM,只保留正文与可选来源备注)
    directives_brief: List[Dict[str, object]] = []
    for row in directives:
        item: Dict[str, object] = {"directive_text": str(row["text"])}
        notes = str(row["notes"] or "").strip()
        if notes:
            item["source_note"] = notes
        directives_brief.append(item)

    # 1.5) 固定月度财政 tick（田赋/辽饷/军饷等，在 LLM 推演前落账）
    tlog("结算 1/4 固定月度财政 tick")
    _emit("stage", "固定月度财政入账")
    fixed_flows = apply_fixed_period_flows(db, state)

    # 1.6) 季度天灾判定
    try:
        apply_natural_disasters(db, state)
    except Exception as exc:
        tlog(f"[disaster] 跳过：{exc}")

    # 1.7) 程序硬触发：标了 auto_trigger 的 seed 情势，gate 达标即由程序直接立项，
    #      绕过 LLM 因果判定。放在 simulator 前，使硬立的 issue 当回合即进盘面被邸报叙述。
    auto_triggered = auto_trigger_seed_issues(state, db)
    if auto_triggered:
        tlog(f"[AUTO-TRIGGER] 本回合程序硬立项 {len(auto_triggered)} 条：{[t.get('title') for t in auto_triggered]}")

    # 1.8) 记忆检索：从诏书提取实体词，召回相关历史记忆注入推演
    relevant_memories: List[Dict] = []
    secret_orders_for_sim: list = []  # try 外初始化：检索失败也不能让后续 NameError
    try:
        _emit("stage", "检索相关历史记忆")
        retrieval_agent = create_memory_retrieval_agent(llm_config, agno_db)
        retrieval_input = json.dumps({
            "decree_text": decree_text,
            "directives": [d["directive_text"] for d in directives_brief],
            "active_issues": [{"id": r["id"], "title": r["title"]} for r in db.list_active_issues()],
        }, ensure_ascii=False)
        tlog(f"[MEM-IO/memory-retrieval/INPUT] ({len(retrieval_input)}字):\n{retrieval_input}")
        raw_keywords = run_agent_text(retrieval_agent, retrieval_input, tag="memory-retrieval")
        tlog(f"[MEM-IO/memory-retrieval/OUTPUT] ({len(raw_keywords)}字):\n{raw_keywords}")
        kw_data = parse_agent_json(raw_keywords, "记忆检索")
        keywords: List[str] = []
        for field in ("characters", "regions", "armies", "powers", "keywords"):
            keywords.extend(str(k) for k in (kw_data.get(field) or []) if k)

        # tags查（普通，带expiry过滤）
        tag_memories = db.get_memories_by_keywords(keywords, turn=state.turn, limit=10)

        # 时间查（有year/period时，ignore_expiry查历史记忆）
        time_memories: List[Dict] = []
        ref_year = kw_data.get("year")
        ref_period = kw_data.get("period")
        if ref_year and ref_period:
            ref_turn = (int(ref_year) - 1627) * 12 + (int(ref_period) - 10) + 1
            time_memories = db.get_memories_by_keywords(
                keywords, turn=ref_turn, limit=5, ignore_expiry=True
            )
            tlog(f"[memory/retrieval] 时间查 {ref_year}年{ref_period}月=turn{ref_turn} hit={len(time_memories)}")

        # 合并去重，time_memories优先（在前）
        seen_ids: set = set()
        relevant_memories = []
        for m in time_memories + tag_memories:
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                relevant_memories.append(m)

        relevant_memories = relevant_memories[:12]
        mem_summary = [(m.get("subject_id","?"), m.get("title","?")) for m in relevant_memories]
        tlog(f"[memory/retrieval] keywords={keywords}")
        tlog(f"[memory/retrieval] total={len(relevant_memories)} items={mem_summary}")
        tlog(f"[MEM-IO/memory-retrieval/INJECT] full={json.dumps(relevant_memories, ensure_ascii=False)}")
    except Exception as exc:
        tlog(f"[memory/retrieval] 失败，跳过：{exc}")

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
            secret_orders_for_sim.append({
                "id": int(o["id"]),
                "minister_name": o["minister_name"],
                "title": o["title"],
                "content": o["content"][:120],
                "status": o["status"],
                "turn_issued": o.get("turn_issued") or 0,
                "due_turn": o.get("due_turn") or 0,
                "progress": o.get("result") or "",      # 承办人聊天里存的当前进展
                "sim_note": o.get("sim_note") or "",     # 上轮推演写的副作用
            })
        n_active = sum(1 for o in active_orders if o["status"] == "active")
        n_pending = sum(1 for o in active_orders if o["status"] == "pending_review")
        tlog(f"[secret_order] 注入推演 active={n_active} pending_review={n_pending}"
             + (f" titles={[o['title'] for o in active_orders]}" if active_orders else ""))
    except Exception as exc:
        tlog(f"[secret_order] 注入失败，跳过：{exc}")

    # 1.9) 军事/外交指令程序化结算
    try:
        _process_military_diplomacy_directives(db, state, directives_brief)
    except Exception as exc:
        tlog(f"[military-diplomacy] 跳过：{exc}")

    # 2) 推演 agent: 写邸报
    tlog("结算 2/4 推演 agent（月末邸报）")
    _emit("stage", "推演月末邸报")
    previous_narrative = db.previous_turn_summary(state) or ""
    simulator_payload = build_simulator_payload(
        state, db, decree_text, directives_brief, previous_narrative,
        fixed_flows=fixed_flows,
        deaths_this_turn=deaths_this_turn,
        debuts_this_turn=debuts_this_turn,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders_for_sim,
    )
    simulator = create_season_simulator_agent(
        llm_config, agno_db, state=state, db=db, simulator_payload=simulator_payload
    )
    try:
        narrative, simulator_payload = simulate_season_with_payload(
            simulator, state, db, decree_text, directives_brief, previous_narrative,
            fixed_flows=fixed_flows,
            deaths_this_turn=deaths_this_turn,
            debuts_this_turn=debuts_this_turn,
            relevant_memories=relevant_memories,
            secret_orders=secret_orders_for_sim,
            simulator_payload=simulator_payload,
            on_thinking=lambda c: _emit("thinking", c),
            on_text=lambda c: _emit("text", c),
        )
    except Exception as exc:
        print(f"[WARN] 推演 agent 失败：{exc}；本{TURN_UNIT}用简化邸报兜底，跳过 LLM 结算。")
        narrative = (
            f"奉天承运皇帝诏曰：本{TURN_UNIT}推演 agent 被服务方拦截，无完整邸报。"
            f"已颁诏书：\n{decree_text}\n"
            f"固定收支已落账，事项 inertia 自然漂移；本{TURN_UNIT}无新立 issue。"
        )
        # 跳过 extractor，避免连锁失败
        db.record_log(state, narrative[:1200])
        db.save_turn_report(state, narrative)
        db.save_turn_extraction(
            state, decree_text=decree_text, narrative=narrative,
            extractor_output=f"[推演 agent 失败] {exc}；本回合跳过 extractor。",
        )
        apply_issue_inertia_and_ongoing(db, state, touched_ids=set())
        for name in clear_gated_legacies(db, state):
            db.record_log(state, f"帝国修正消除：{name}")
        db.mark_directives_issued(state)
        state.next_period()
        db.save_state(state)
        return f"\n本{TURN_UNIT}颁布诏书：\n" + decree_text + "\n\n" + narrative

    # 2.5) 作弊强制项：拼到邸报最前面一起喂 extractor（唯一入口）。
    #      落库前文/turn_report 仍用原始 narrative，effective 版只进 extractor 与留痕。
    cheat = (cheat_directive or "").strip()
    if cheat:
        effective_narrative = CHEAT_NARRATIVE_PREFIX + cheat + "\n\n" + narrative
        tlog(f"[CHEAT] 强制结算项注入 extractor（{len(cheat)}字）：{cheat[:200]}")
    else:
        effective_narrative = narrative

    # 3) 结算 agent: 读邸报抽 JSON
    tlog("结算 3/4 结算 agent（抽 JSON）")
    _emit("stage", "数值推演结算")
    extractor_shared_context = build_extractor_shared_context(
        db, state, effective_narrative, decree_text,
        relevant_memories=relevant_memories,
        secret_orders=secret_orders_for_sim,
    )
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
    sanitizer = create_json_sanitizer_agent(llm_config, agno_db)
    extractor_input = ""
    extractor_output = ""
    try:
        extracted, extractor_output, extractor_input = extract_scores_by_modules_with_agno(
            extractors, db, state, effective_narrative, decree_text=decree_text, sanitizer=sanitizer,
            relevant_memories=relevant_memories,
            secret_orders=secret_orders_for_sim,
        )
    except Exception as exc:
        print(f"[WARN] 结算抽取失败：{exc}；本{TURN_UNIT}数值不变。")
        extracted = {}
        extractor_output = f"[抽取失败] {exc}"

    tlog("结算 4/4 落库 + inertia/ongoing")
    _emit("stage", "落库与事项推进")
    # 机械安全网
    try:
        _enforce_directive_mechanics(directives_brief, extracted, content=content)
    except Exception as exc:
        tlog(f"[enforce] 跳过：{exc}")
    applied = apply_score_extraction(db, state, extracted, content=content, registry=registry)

    # 4) 把 narrative 与诏书写入 turn_logs 作下月前文
    db.record_log(state, narrative[:1200])
    db.save_turn_report(state, narrative)
    # 推演链原始输入/输出留痕，事后可追「该立的 issue 为何没立」。
    db.save_turn_extraction(
        state,
        decree_text=decree_text,
        narrative=effective_narrative,  # 留痕含作弊段，便于事后追「为何这么落库」
        extractor_input=extractor_input,
        extractor_output=extractor_output,
    )

    # 5a) 规则版事件记忆：从已落库 applied 直接写（任免/issue/region/army/faction）
    _emit("stage", "规则记忆落库")
    try:
        record_event_memories_from_resolution(
            db, state, directives, decree_text, narrative, extractor_output, applied,
        )
    except Exception as exc:
        tlog(f"[memory/fallback] 跳过：{exc}")

    # 5b) LLM 版事件记忆：从诏书+邸报+applied 提取细节人物动向
    _emit("stage", "LLM 抽取事件记忆")
    try:
        mem_agent = create_memory_extractor_agent(llm_config, agno_db)
        extract_event_memories_with_agent(
            mem_agent, db, state, directives,
            decree_text, narrative, extractor_output, applied,
        )
    except Exception as exc:
        tlog(f"[memory-extractor] 跳过：{exc}")

    # 5c) 对话记忆提取（event_memory，chat_message 来源）
    _emit("stage", "提取对话记忆")
    try:
        chat_mem_agent = create_chat_memory_agent(llm_config, agno_db)
        extract_all_chat_memories(chat_mem_agent, db, state)
    except Exception as exc:
        tlog(f"[chat-memory] 跳过：{exc}")

    # 6) 落 inertia + ongoing (未被本月 issue_advances 触动的)
    touched_ids = set()
    for adv in applied.get("issue_summary", {}).get("advances", []) or []:
        touched_ids.add(int(adv.get("issue_id") or 0))
    apply_issue_inertia_and_ongoing(db, state, touched_ids=touched_ids)

    # 事件叙事记录
    try:
        record_event_narrative(db, state, applied, decree_text=decree_text, narrative=narrative)
    except Exception as exc:
        tlog(f"[event-narrative] 跳过：{exc}")

    # 事件阶段推进
    try:
        phase_advances = advance_event_phases(db, state)
        if phase_advances:
            tlog(f"[event-phase] 阶段推进 {len(phase_advances)} 条")
    except Exception as exc:
        tlog(f"[event-phase] 跳过：{exc}")

    # 7) 开局负面帝国修正：本月若达成消除条件即清除（程序判定，不靠 LLM/时长）
    cleared = clear_gated_legacies(db, state)
    for name in cleared:
        db.record_log(state, f"帝国修正消除：{name}")

    outcome = applied.get("victory_status") or victory_status(db, state)
    if isinstance(outcome, dict) and outcome.get("status") != "ongoing":
        db.record_log(state, f"结局判定：{outcome.get('summary', '')}")

    db.mark_directives_issued(state)
    state.next_period()
    db.save_state(state)
    assert state.turn == before_turn + 1

    ending = ""
    if isinstance(outcome, dict) and outcome.get("status") != "ongoing":
        label = "大明战胜后金/大清" if outcome.get("status") == "ming_victory" else "大明败于后金/大清"
        ending = f"\n\n【结局】{label}：{outcome.get('summary', '')}"
    full_report = f"\n本{TURN_UNIT}颁布诏书：\n" + decree_text + "\n\n" + narrative + ending
    return full_report


# ════════════════════════════════════════════════════════════════
# 军事/外交指令程序化结算 + 机械安全网
# ════════════════════════════════════════════════════════════════

def _find_name(text: str, known_names: List[str]) -> Optional[str]:
    """从文本中匹配已知人物名（长名优先）。"""
    for name in known_names:
        if len(name) >= 2 and name in text:
            return name
    return None


def _process_military_diplomacy_directives(
    db: GameDB,
    state: GameState,
    directives_brief: List[Dict[str, object]],
) -> None:
    """军事/外交指令程序化结算：在 LLM 推演前更新军队/外部势力状态。
    每个类别有固定公式；LLM 在此基础上写叙事。"""
    armies = {r["id"]: r for r in db.conn.execute("SELECT * FROM armies").fetchall()}
    army_names: Dict[str, str] = {}
    for r in armies.values():
        army_names[r["name"]] = r["id"]
    powers = {r["id"]: r for r in db.conn.execute("SELECT * FROM powers").fetchall()}
    power_names: Dict[str, str] = {}
    for r in powers.values():
        power_names[r["name"]] = r["id"]

    for d in directives_brief:
        text = str(d.get("directive_text", ""))
        if not text.strip():
            continue
        category = str(d.get("category", "")).strip()
        if not category:
            if re.search(r'出征|出兵|进剿|讨伐|伐金|伐清', text):
                category = "military_attack"
            elif re.search(r'防守|守备|固防|戒严', text):
                category = "military_defend"
            elif re.search(r'调防|移镇|换防|移兵', text):
                category = "military_relocate"
            elif re.search(r'招抚|招安|纳降|收编', text):
                category = "military_recruit"
        if not category:
            if re.search(r'遣使|出使|借兵|请援', text):
                category = "diplomacy_envoy"
            elif re.search(r'议和|和谈|讲和', text):
                category = "diplomacy_peace"
            elif re.search(r'联姻|和亲|下嫁', text):
                category = "diplomacy_marriage"
            elif re.search(r'册封|封贡|封王', text):
                category = "diplomacy_invest"
        if not category:
            continue
        d["category"] = category
        target_army = None
        for name, aid in army_names.items():
            if name in text:
                target_army = armies[aid]
                break
        target_power = None
        for name, pid in power_names.items():
            if name in text:
                target_power = powers[pid]
                break
        if category == "military_attack" and target_army:
            db.apply_army_deltas(state, None, None, "兵部·出征", {
                target_army["id"]: {"supply": -15, "morale": -5, "mobility": -10},
            })
            db.record_issue_economy_move(state, "国库", -12, "行军支饷", text[:40])
        elif category == "military_defend" and target_army:
            db.apply_army_deltas(state, None, None, "兵部·防守", {
                target_army["id"]: {"morale": 5, "equipment": -3},
            })
            db.record_issue_economy_move(state, "国库", -5, "防务调拨", text[:40])
        elif category == "military_relocate" and target_army:
            db.apply_army_deltas(state, None, None, "兵部·调防", {
                target_army["id"]: {"mobility": -10, "supply": -8},
            })
            db.record_issue_economy_move(state, "国库", -8, "移镇转运", text[:40])
        elif category == "military_recruit" and target_army:
            db.apply_army_deltas(state, None, None, "兵部·招抚", {
                target_army["id"]: {"manpower": 3000, "morale": -3},
            })
            db.record_issue_economy_move(state, "国库", -6, "招抚安插", text[:40])
        elif category == "diplomacy_envoy" and target_power:
            db.conn.execute(
                "UPDATE powers SET leverage = MAX(0, MIN(100, leverage + 5)), "
                "last_action = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (f"明廷遣使：{text[:30]}", target_power["id"]),
            )
            db.conn.commit()
            db.record_issue_economy_move(state, "国库", -3, "使节支费", text[:40])
        elif category == "diplomacy_peace" and target_power:
            db.conn.execute(
                "UPDATE powers SET satisfaction = MAX(0, MIN(100, satisfaction + 10)), "
                "stance = '谈判中', last_action = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (f"明廷议和：{text[:30]}", target_power["id"]),
            )
            db.conn.commit()
            state.metrics["皇威"] = max(0, int(state.metrics.get("皇威", 0)) - 3)
            db.record_issue_economy_move(state, "国库", -5, "议和犒赏", text[:40])
        elif category == "diplomacy_marriage" and target_power:
            db.conn.execute(
                "UPDATE powers SET stance = '摇摆', satisfaction = MAX(0, MIN(100, satisfaction + 15)), "
                "leverage = MAX(0, MIN(100, leverage + 8)), last_action = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (f"明廷联姻：{text[:30]}", target_power["id"]),
            )
            db.conn.commit()
            db.record_issue_economy_move(state, "国库", -10, "和亲嫁妆", text[:40])
        elif category == "diplomacy_invest" and target_power:
            db.conn.execute(
                "UPDATE powers SET satisfaction = MAX(0, MIN(100, satisfaction + 8)), "
                "leverage = MAX(0, MIN(100, leverage + 3)), last_action = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (f"明廷册封：{text[:30]}", target_power["id"]),
            )
            db.conn.commit()
            state.metrics["皇威"] = min(100, int(state.metrics.get("皇威", 0)) + 2)


def _enforce_directive_mechanics(
    directives_brief: List[Dict[str, object]],
    extracted: Dict[str, object],
    content=None,
) -> None:
    """机械安全网：一锤子指令（抄家/罢官/下狱）不被 LLM 遗漏。"""
    if not directives_brief:
        return
    known_names: List[str] = []
    if content is not None:
        known_names = sorted(getattr(content, "characters", {}).keys(),
                             key=lambda n: -len(n))
    for d in directives_brief:
        text = str(d.get("directive_text", ""))
        if not text.strip():
            continue
        if re.search(r'抄家|查抄|籍没|抄没|罚没|没入', text):
            existing = [
                m for m in extracted.get("economy_moves", [])
                if isinstance(m, dict) and ("抄" in str(m.get("reason", "")) or m.get("category") == "查抄")
            ]
            if not existing:
                extracted.setdefault("economy_moves", []).append({
                    "account": "国库", "delta": 20, "category": "查抄",
                    "reason": "诏命查抄入官",
                })
            has_status = any(
                isinstance(s, dict) and s.get("status") in ("imprisoned", "dead")
                for s in extracted.get("character_status_changes", [])
            )
            if not has_status:
                target = _find_name(text, known_names)
                if target:
                    extracted.setdefault("character_status_changes", []).append({
                        "name": target, "status": "imprisoned",
                        "reason": "诏命查抄下狱",
                    })
        if re.search(r'罢官|革职|削籍|罢黜|免官|夺职|去职', text):
            has_dismiss = any(
                isinstance(s, dict) and s.get("status") == "dismissed"
                for s in extracted.get("character_status_changes", [])
            )
            if not has_dismiss:
                target = _find_name(text, known_names)
                if target:
                    extracted.setdefault("character_status_changes", []).append({
                        "name": target, "status": "dismissed",
                        "reason": "诏命罢黜",
                    })
        if re.search(r'下狱|拿问|逮问|缉拿|拿下|收监', text):
            has_imprison = any(
                isinstance(s, dict) and s.get("status") == "imprisoned"
                for s in extracted.get("character_status_changes", [])
            )
            if not has_imprison:
                target = _find_name(text, known_names)
                if target:
                    extracted.setdefault("character_status_changes", []).append({
                        "name": target, "status": "imprisoned",
                        "reason": "诏命缉拿下狱",
                    })
