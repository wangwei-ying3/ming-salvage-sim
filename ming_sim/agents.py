"""Agno Agent 执行与工厂（非大臣类）：run_agent_*、parse_agent_json、
诏书润色/月末推演/打分提取/JSON 修复 agent。L5。

通过 bind_content() 注入 GameContent（取提示词）。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

from agno.agent import Agent
from agno.db.sqlite import SqliteDb

from ming_sim.assets import strip_json_fence
from ming_sim.content import GameContent
from ming_sim.exceptions import LLMContractError, LLMUnavailable
from ming_sim.llm_contract import abort_llm_contract, fail_if_llm_error
from ming_sim.llm_model import create_chat_model, extract_agent_text
from ming_sim.models import GameState, LLMConfig
from ming_sim.token_stats import record_stream_metrics, tlog

_content: Optional[GameContent] = None


def bind_content(content: GameContent) -> None:
    global _content
    _content = content


def _ctx() -> GameContent:
    if _content is None:
        raise RuntimeError("agents.bind_content() 未调用：GameContent 未注入。")
    return _content


def run_agent_text(agent: Agent, prompt: str, tag: str) -> str:
    """非流式跑 agent，返回最终完整文本。
    extractor/sanitizer 这类要严格 JSON 的场合用——避免流式 buffer 把 LLM 偶发重发段累加成畸形。"""
    tlog(f"[{tag}] 开始非流式推演（等待完整响应）")
    t0 = time.monotonic()
    output = agent.run(prompt)
    text = extract_agent_text(output)
    tlog(f"[{tag}] 完成，{len(text)} 字，用时 {time.monotonic() - t0:.1f}s")
    return text


def run_agent_stream_text(
    agent: Agent,
    prompt: str,
    tag: str,
    on_thinking: Optional[Callable[[str], None]] = None,
    on_text: Optional[Callable[[str], None]] = None,
) -> str:
    """流式跑 agent，按事件实时打到 stdout（带毫秒时间戳），最终返回拼合后的纯文本。

    on_thinking(chunk): 每次思考片段到达时回调（可选）。
    on_text(chunk): 每次正文增量到达时回调（可选）。
    """
    tlog(f"[{tag}] 开始流式推演（首字到达前可能等几秒）")
    pieces: List[str] = []
    final_output = None
    last_print = time.monotonic()
    chunk_buf: List[str] = []
    chars_since_flush = 0
    try:
        stream = agent.run(prompt, stream=True, stream_events=True)
    except TypeError:
        tlog(f"[{tag}] 当前 agno 不支持 stream，退回普通 run")
        text = extract_agent_text(agent.run(prompt))
        if on_text:
            on_text(text)
        return text

    reasoning_buf: List[str] = []
    reasoning_chars_since_flush = 0
    reasoning_last_print = time.monotonic()
    tool_calls = 0
    for event in stream:
        ev_type = type(event).__name__
        # 工具调用事件：记日志 + 把「正在查 X」作为思考片段推给前端
        if ev_type == "ToolCallStartedEvent":
            tool = getattr(event, "tool", None)
            tname = getattr(tool, "tool_name", "?") if tool else "?"
            targs = getattr(tool, "tool_args", {}) if tool else {}
            tool_calls += 1
            tlog(f"[{tag}/工具] 调用 {tname}({targs})")
            if on_thinking:
                on_thinking(f"\n〔查阅 {tname} {targs}〕\n")
            continue
        if ev_type == "ToolCallCompletedEvent":
            continue
        rdelta = getattr(event, "reasoning_content", None)
        if isinstance(rdelta, str) and rdelta:
            reasoning_buf.append(rdelta)
            reasoning_chars_since_flush += len(rdelta)
            now = time.monotonic()
            if reasoning_chars_since_flush >= 120 or (now - reasoning_last_print) >= 1.5:
                merged = "".join(reasoning_buf)
                tlog(f"[{tag}/思考] {merged.replace(chr(10), ' ⏎ ')[-200:]}")
                if on_thinking:
                    on_thinking(merged)
                reasoning_buf.clear()
                reasoning_chars_since_flush = 0
                reasoning_last_print = now
        is_terminal = (
            (hasattr(event, "is_final") and getattr(event, "is_final", False))
            or ev_type in ("RunOutput", "RunCompletedEvent")
        )
        if ev_type == "RunErrorEvent":
            raise LLMUnavailable(f"{tag} 流式调用失败：{getattr(event, 'content', None)}")
        if is_terminal:
            final_output = event
            continue
        delta = getattr(event, "content", None)
        if isinstance(delta, str) and delta:
            pieces.append(delta)
            chunk_buf.append(delta)
            chars_since_flush += len(delta)
            if on_text:
                on_text(delta)
            now = time.monotonic()
            if chars_since_flush >= 80 or (now - last_print) >= 1.0:
                merged = "".join(chunk_buf).replace("\n", " ⏎ ")
                tlog(f"[{tag}] …{merged[-160:]}")
                chunk_buf.clear()
                chars_since_flush = 0
                last_print = now

    if reasoning_buf:
        merged = "".join(reasoning_buf)
        tlog(f"[{tag}/思考] {merged.replace(chr(10), ' ⏎ ')[-200:]}")
        if on_thinking:
            on_thinking(merged)
    if chunk_buf:
        merged = "".join(chunk_buf).replace("\n", " ⏎ ")
        tlog(f"[{tag}] …{merged[-160:]}")

    streamed = "".join(pieces).strip()
    if streamed:
        text = streamed
        fail_if_llm_error(text, "LLM 调用")
    elif final_output is not None:
        text = extract_agent_text(final_output)
    else:
        abort_llm_contract(tag, "流式无内容且无终结事件", "")
    tlog(f"[{tag}] 完成，{len(text)} 字，工具调用 {tool_calls} 次")
    # 流式 openai response 无 .usage，monkeypatch 抓不到；从终结事件 metrics 补记 token。
    if final_output is not None:
        metrics = getattr(final_output, "metrics", None)
        model_id = getattr(getattr(agent, "model", None), "id", None) or "stream"
        record_stream_metrics(str(model_id), metrics, caller_tag=tag)
    return text


def parse_agent_json(raw: str, stage: str) -> Dict[str, Any]:
    text = strip_json_fence(raw)
    # 试 1：原文直解
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    # 试 2：截 {...} 最外层再解
    if data is None:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            abort_llm_contract(stage, "没有返回 JSON object", raw)
        snippet = text[start : end + 1]
        try:
            data = json.loads(snippet)
        except json.JSONDecodeError:
            data = None
        # 试 3：净化 control char（\r\v\f\x00-\x1f 等）后再解
        if data is None:
            cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", snippet)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                data = None
        # 试 4：截取首个合法平衡的 {...} 子串（防 LLM 重发拼接）
        if data is None:
            depth = 0
            in_str = False
            esc = False
            best_end = -1
            for i, ch in enumerate(snippet):
                if esc:
                    esc = False
                    continue
                if ch == "\\" and in_str:
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        best_end = i
                        break
            if best_end > 0:
                first_block = snippet[: best_end + 1]
                first_block = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", first_block)
                try:
                    data = json.loads(first_block)
                except json.JSONDecodeError as error:
                    raise LLMContractError(
                        f"{stage} 输出不是合法 JSON：{error}\n原始输出：{raw[:800]}"
                    ) from error
            else:
                raise LLMContractError(
                    f"{stage} 输出不是合法 JSON\n原始输出：{raw[:800]}"
                )
    if not isinstance(data, dict):
        abort_llm_contract(stage, "顶层必须是 JSON object", raw)
    return data


def create_decree_writer_agent(llm_config: LLMConfig, agno_db: SqliteDb) -> Agent:
    return Agent(
        name="诏书润色官",
        id="decree-writer",
        session_id="decree-writer",
        db=agno_db,
        model=create_chat_model(llm_config, temperature=0.3, top_p=0.9, max_tokens=900),
        instructions=[_ctx().game_world_prompt, _ctx().decree_writer_prompt],
        add_history_to_context=False,
        markdown=False,
    )


def create_season_simulator_agent(
    llm_config: LLMConfig,
    agno_db: SqliteDb,
    state: Optional[GameState] = None,
    db: Optional[object] = None,
) -> Agent:
    """月末推演日讲官。

    全量盘面（地区/军队/建筑/派系/阶级/外部势力）由 simulate_season_with_agno 单发塞进
    payload，推演官不再挂只读 tool —— 去掉 tool round-trip 后每月 prompt 从 ~69k 降到 ~12k。
    state/db 参数保留兼容旧调用，已不使用。
    """
    del state, db  # 全量盘面走 payload，不再建 tool
    return Agent(
        name="月末推演日讲官",
        id="season-simulator",
        session_id="season-simulator",
        db=agno_db,
        model=create_chat_model(llm_config, temperature=0.9, top_p=0.95, max_tokens=4500, enable_thinking=True),
        instructions=[_ctx().game_world_prompt, _ctx().season_simulator_prompt],
        add_history_to_context=False,
        markdown=False,
    )


def create_score_extractor_agent(llm_config: LLMConfig, agno_db: SqliteDb) -> Agent:
    # 走 OpenAI 兼容 response_format=json_object（deepseek/qwen 均支持），
    # API 层保证输出合法 JSON——免去 sanitizer/parser 多级容错负担。
    return Agent(
        name="档房书办",
        id="score-extractor",
        session_id="score-extractor",
        db=agno_db,
        model=create_chat_model(
            llm_config,
            temperature=0.1,
            top_p=0.7,
            max_tokens=8000,
            enable_thinking=False,
            force_json_output=True,
        ),
        instructions=[_ctx().game_world_prompt, _ctx().score_extractor_prompt],
        add_history_to_context=False,
        markdown=False,
    )


JSON_SANITIZER_PROMPT = (
    "你是 JSON 修复匠。下面给你一段被污染的 JSON（可能混了思考过程、```json fence、注释、尾随逗号、"
    "重复字段、Markdown 标题等），请只输出**修复后的合法 JSON 字符串**，不要加任何解释、前后缀或 fence。\n"
    "保持原数据结构与字段不变，只做语法清理。若彻底无法识别为 JSON，请尝试抽取里面最像 JSON 的那一段。\n"
    "请按照 json 格式输出。"
)


def create_json_sanitizer_agent(llm_config: LLMConfig, agno_db: SqliteDb) -> Agent:
    """非思考 + response_format=json_object 的 fallback 整理器。"""
    return Agent(
        name="JSON 修复匠",
        id="json-sanitizer",
        session_id="json-sanitizer",
        db=agno_db,
        model=create_chat_model(
            llm_config,
            temperature=0.0,
            top_p=0.7,
            max_tokens=4000,
            enable_thinking=False,
            force_json_output=True,
        ),
        instructions=[JSON_SANITIZER_PROMPT],
        add_history_to_context=False,
        markdown=False,
    )
