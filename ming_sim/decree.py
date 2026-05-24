"""诏书生成与回合结算：拟诏、推演落库、无诏推进。L7。

纯逻辑（无 input()）；resolve_directives 的 print 是诊断输出，非交互。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable, Dict, List, Optional

from agno.db.sqlite import SqliteDb

from ming_sim.agents import (
    create_decree_writer_agent,
    create_json_sanitizer_agent,
    create_score_extractor_agent,
    create_season_simulator_agent,
)
from ming_sim.constants import TURN_UNIT
from ming_sim.context import victory_status
from ming_sim.db import GameDB
from ming_sim.exceptions import LLMContractError, LLMUnavailable
from ming_sim.flows import apply_fixed_period_flows
from ming_sim.issues import apply_issue_inertia_and_ongoing, apply_score_extraction
from ming_sim.llm_model import extract_agent_text
from ming_sim.models import GameState, LLMConfig
from ming_sim.simulation import extract_scores_with_agno, simulate_season_with_agno
from ming_sim.token_stats import tlog


def write_decree_with_agno(
    llm_config: LLMConfig,
    agno_db: SqliteDb,
    state: GameState,
    directives: List[sqlite3.Row],
) -> str:
    if not directives:
        raise LLMContractError("无草案不能拟诏。")
    payload = {
        "turn": {"year": state.year, "period": state.period, "turn": state.turn},
        "directives": [
            {
                "text": row["text"],
            }
            for row in directives
        ],
        "instruction": "合并成一份正式诏书正文。",
    }
    try:
        agent = create_decree_writer_agent(llm_config, agno_db)
        text = extract_agent_text(agent.run(json.dumps(payload, ensure_ascii=False, sort_keys=True)))
    except LLMUnavailable:
        raise
    except Exception as error:
        raise LLMUnavailable(f"拟诏失败：{error}") from error
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
    on_event: Optional[Callable[[str, str], None]] = None,
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

    # 2) 推演 agent: 写邸报
    tlog("结算 2/4 推演 agent（月末邸报）")
    _emit("stage", "推演月末邸报")
    previous_narrative = db.previous_turn_summary(state) or ""
    simulator = create_season_simulator_agent(llm_config, agno_db, state=state, db=db)
    try:
        narrative = simulate_season_with_agno(
            simulator, state, db, decree_text, directives_brief, previous_narrative,
            fixed_flows=fixed_flows,
            deaths_this_turn=deaths_this_turn,
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
    _emit("stage", "抽取数值结算")
    extractor = create_score_extractor_agent(llm_config, agno_db)
    sanitizer = create_json_sanitizer_agent(llm_config, agno_db)
    extractor_input = ""
    extractor_output = ""
    try:
        extracted, extractor_output, extractor_input = extract_scores_with_agno(
            extractor, db, state, narrative, decree_text=decree_text, sanitizer=sanitizer
        )
    except Exception as exc:
        print(f"[WARN] 结算抽取失败：{exc}；本{TURN_UNIT}数值不变。")
        extracted = {}
        extractor_output = f"[抽取失败] {exc}"

    tlog("结算 4/4 落库 + inertia/ongoing")
    _emit("stage", "落库与事项推进")
    applied = apply_score_extraction(db, state, extracted)

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

    # 5) 落 inertia + ongoing (未被本月 issue_advances 触动的)
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
