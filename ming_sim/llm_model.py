"""LLM 模型与 Agno DB 工厂、agent 输出文本提取。L2。"""

from __future__ import annotations

from typing import Dict, Optional

import httpx
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIChat
from openai import APIConnectionError, APIStatusError, APITimeoutError

from ming_sim.exceptions import LLMUnavailable
from ming_sim.llm_config import (
    is_deepseek_base_url,
    is_deepseek_v4_model,
    normalize_deepseek_reasoning_effort,
    provider_extra_body,
    supports_openai_reasoning_effort,
)
from ming_sim.llm_contract import fail_if_llm_error
from ming_sim.models import LLMConfig
from ming_sim.token_stats import install_token_stats_patch


def _extract_provider_error(error: Exception) -> tuple[str, str, int | None]:
    code = getattr(error, "code", None) or type(error).__name__
    message = str(error)
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if response is not None:
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            inner = payload.get("error")
            if isinstance(inner, dict):
                code = inner.get("code") or inner.get("type") or code
                message = inner.get("message") or message
            else:
                code = payload.get("code") or payload.get("type") or code
                message = payload.get("message") or payload.get("detail") or message
    return str(code), str(message), int(status_code) if status_code is not None else None


def llm_unavailable_from_error(error: Exception, stage: str = "LLM 连通性检查") -> LLMUnavailable:
    provider_code, provider_message, status_code = _extract_provider_error(error)
    if isinstance(error, APITimeoutError):
        code = "llm_timeout"
    elif isinstance(error, APIConnectionError):
        code = "llm_connection_error"
    elif isinstance(error, APIStatusError):
        code = f"llm_http_{status_code or 'error'}"
    else:
        code = "llm_error"
    return LLMUnavailable(
        f"{stage}失败：{provider_message}",
        code=code,
        provider_message=provider_message,
        status_code=status_code,
    )


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
    extra_body = provider_extra_body(
        llm_config.base_url,
        llm_config.model,
        enable_thinking=enable_thinking,
        thinking_level=llm_config.thinking_level,
        thinking_budget=thinking_budget,
    )
    kwargs: Dict[str, object] = {
        "id": llm_config.model,
        "api_key": llm_config.api_key,
        "base_url": llm_config.base_url,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # read_timeout_seconds：流式下「两次 socket 读之间」的最大间隔，即 chunk 间隔超此判卡死，
        # 抛 httpx.ReadTimeout → openai APITimeoutError → 上层包成 LLMUnavailable 提示用户。
        # 持续吐字不误伤；总超时仍用 timeout_seconds 兜底。标量 timeout 对流式几乎不触发，故必须分项。
        # 三者均可在 runtime_llm.json / 系统配置里调（默认 180/10/20）。
        "timeout": httpx.Timeout(
            llm_config.timeout_seconds,
            connect=llm_config.connect_timeout_seconds,
            read=llm_config.read_timeout_seconds,
        ),
        # 0：超时立即冒泡，不静默重试（重试会让「卡住」再多等一整轮才报错）。
        "max_retries": 0,
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
    if is_deepseek_base_url(llm_config.base_url) and is_deepseek_v4_model(llm_config.model) and enable_thinking:
        effort = normalize_deepseek_reasoning_effort(llm_config.thinking_level)
        if effort:
            kwargs["reasoning_effort"] = effort
    elif supports_openai_reasoning_effort(llm_config.model):
        kwargs["reasoning_effort"] = llm_config.thinking_level or ("medium" if enable_thinking else "minimal")
    return OpenAIChat(**kwargs)


def create_agno_db(sqlite_path: str) -> SqliteDb:
    return SqliteDb(
        db_file=sqlite_path,
        session_table="agno_sessions",
        memory_table="agno_memories",
    )


def extract_agent_text(run_output: object) -> str:
    missing = object()
    content = getattr(run_output, "content", missing)
    if content is missing:
        text = str(run_output).strip()
    elif content is None:
        text = ""
    else:
        text = str(content).strip()
    fail_if_llm_error(text, "LLM 调用")
    return text


def verify_llm_available(llm_config: LLMConfig, enable_thinking: bool = False) -> None:
    """检查 LLM 是否可用：调用成功（HTTP 200，不抛异常）即算通过，不校验返回内容。"""
    agent = Agent(
        name="LLM连通性检查",
        id="llm-smoke-test",
        session_id="llm-smoke-test",
        model=create_chat_model(llm_config, temperature=0, max_tokens=8, enable_thinking=enable_thinking),
        instructions=["只输出 ok。"],
        markdown=False,
    )
    try:
        extract_agent_text(agent.run("输出 ok"))
    except LLMUnavailable:
        raise
    except Exception as error:
        raise llm_unavailable_from_error(error) from error
