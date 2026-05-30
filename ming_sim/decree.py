"""诏书生成与回合结算：拟诏、推演落库、无诏推进。L7。

纯逻辑（无 input()）；resolve_directives 的 print 是诊断输出，非交互。
"""

from __future__ import annotations

import json
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
from ming_sim.flows import apply_fixed_period_flows
from ming_sim.issues import apply_issue_inertia_and_ongoing, apply_score_extraction
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
) -> str:
    """on_event(kind, data): 推演过程实时回调。
    kind ∈ {stage, thinking, text}；stage 携带阶段名，thinking/text 携带增量片段。
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

    # ── 皇帝技能点补给（每回合+1，皇威>=50时+2）─────────────────────────────────
    hw_val = state.metrics.get("皇威", 0)
    gain = 2 if hw_val >= 50 else 1
    old_pts = state.metrics.get("skill_points", 0)
    state.metrics["skill_points"] = old_pts + gain
    db.conn.execute(
        "INSERT OR REPLACE INTO metrics (key, value) VALUES ('skill_points', ?)",
        (state.metrics["skill_points"],),
    )
    db.conn.commit()

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
        db.mark_directives_issued(state)
        state.next_period()
        db.save_state(state)
        return f"\n本{TURN_UNIT}颁布诏书：\n" + decree_text + "\n\n" + narrative

    # 3) 结算 agent: 读邸报抽 JSON
    tlog("结算 3/4 结算 agent（抽 JSON）")
    _emit("stage", "数值推演结算")
    extractor_shared_context = build_extractor_shared_context(
        db, state, narrative, decree_text,
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
            extractors, db, state, narrative, decree_text=decree_text, sanitizer=sanitizer,
            relevant_memories=relevant_memories,
            secret_orders=secret_orders_for_sim,
        )
    except Exception as exc:
        print(f"[WARN] 结算抽取失败：{exc}；本{TURN_UNIT}数值不变。")
        extracted = {}
        extractor_output = f"[抽取失败] {exc}"

    tlog("结算 4/4 落库 + inertia/ongoing")
    _emit("stage", "落库与事项推进")
    applied = apply_score_extraction(db, state, extracted, content=content, registry=registry)

    # 4) 把 narrative 与诏书写入 turn_logs 作下月前文
    db.record_log(state, narrative[:1200])
    db.save_turn_report(state, narrative)
    # 推演链原始输入/输出留痕，事后可追「该立的 issue 为何没立」。
    db.save_turn_extraction(
        state,
        decree_text=decree_text,
        narrative=narrative,
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
