"""LLM 模型与 Agno DB 工厂、agent 输出文本提取。L2。"""

from __future__ import annotations

from typing import Dict, Optional

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIChat

from ming_sim.exceptions import LLMUnavailable
from ming_sim.llm_config import (
    is_dashscope_base_url,
    is_deepseek_base_url,
    provider_extra_body,
    supports_openai_reasoning_effort,
)
from ming_sim.llm_contract import fail_if_llm_error
from ming_sim.models import LLMConfig
from ming_sim.token_stats import install_token_stats_patch


def create_chat_model(
    llm_config: LLMConfig,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    enable_thinking: bool = False,
    thinking_budget: Optional[int] = None,
    top_p: Optional[float] = None,
    force_json_output: bool = False,
) -> OpenAIChat:
    install_token_stats_patch()
    extra_body = provider_extra_body(llm_config.base_url)
    if enable_thinking and is_dashscope_base_url(llm_config.base_url):
        # 推演/评估类 agent 需要深思,开 qwen thinking
        extra_body = {"enable_thinking": True}
        if thinking_budget is not None:
            extra_body["thinking_budget"] = int(thinking_budget)
    elif enable_thinking and is_deepseek_base_url(llm_config.base_url):
        extra_body = {}  # deepseek-v4 默认深思,清掉 disabled
    kwargs: Dict[str, object] = {
        "id": llm_config.model,
        "api_key": llm_config.api_key,
        "base_url": llm_config.base_url,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "role_map": {"system": "system", "user": "user", "assistant": "assistant", "tool": "tool"},
        "extra_body": extra_body,
    }
    if top_p is not None:
        kwargs["top_p"] = top_p
    if force_json_output:
        # qwen / OpenAI 兼容协议：强制输出标准 JSON 字符串
        # 走 extra_body 透传给 dashscope；prompt 里必须含 "json" 字样
        if extra_body is None:
            extra_body = {}
        extra_body["response_format"] = {"type": "json_object"}
        kwargs["extra_body"] = extra_body
    if supports_openai_reasoning_effort(llm_config.model):
        kwargs["reasoning_effort"] = "medium" if enable_thinking else "minimal"
    return OpenAIChat(**kwargs)


def create_agno_db(sqlite_path: str) -> SqliteDb:
    return SqliteDb(
        db_file=sqlite_path,
        session_table="agno_sessions",
        memory_table="agno_memories",
    )


def extract_agent_text(run_output: object) -> str:
    content = getattr(run_output, "content", None)
    if content is not None:
        text = str(content).strip()
    else:
        text = str(run_output).strip()
    fail_if_llm_error(text, "LLM 调用")
    return text


def verify_llm_available(llm_config: LLMConfig) -> None:
    agent = Agent(
        name="LLM连通性检查",
        id="llm-smoke-test",
        session_id="llm-smoke-test",
        model=create_chat_model(llm_config, temperature=0, max_tokens=8),
        instructions=["只输出 ok。"],
        markdown=False,
    )
    try:
        raw = extract_agent_text(agent.run("输出 ok"))
    except Exception as error:
        raise LLMUnavailable(f"LLM 连通性检查失败：{error}") from error
    fail_if_llm_error(raw, "LLM 连通性检查")
    if "ok" not in raw.lower():
        raise LLMUnavailable(f"LLM 连通性检查返回异常：{raw[:120]}")
