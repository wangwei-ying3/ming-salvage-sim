"""LLM 提供商配置：base_url 规范化、提供商检测、LLMConfig 加载。L1。"""

from __future__ import annotations

import getpass
import json
import os
from typing import Dict, Optional

from ming_sim.models import LLMConfig
from ming_sim.paths import user_data_path

RUNTIME_LLM_PATH = user_data_path("runtime_llm.json")
RUNTIME_GAME_PATH = user_data_path("runtime_game.json")

# 游戏玩法设置默认值（全局，跨局共享）。
GAME_SETTINGS_DEFAULTS = {
    "hitl_min_decisions": 1,  # 每回合 simulator 最多产出的重大决策点数（0=关闭 HITL 注入）
    "court_chat_debate_rounds": 3,  # 朝会聊天室未形成结论前最多驱动几轮 ReAct 交锋。
    "court_chat_stream_speed": 3,  # 朝会流式输出速度档位，1=慢，5=快。
    "max_decree_issues": 10,  # decree 来源 active 局势同时进行上限。调高会增加推演 token 消耗。
    "issue_log_limit": 6,  # 每条 active 局势注入推演的最近推进日志条数。
    "secret_order_person_limit": 1,  # 单个承办人同时进行中的密令上限。
    "secret_order_total_limit": 5,  # 全朝同时进行中的密令总上限。
    "character_limit": 120,  # 本局朝臣人物建档上限；后宫不计入。调高会增加名册/推演 token 消耗。
    "minister_temperature": 0.6,  # 大臣 agent 采样温度。
    "minister_top_p": 0.9,  # 大臣 agent nucleus sampling。
    "simulator_temperature": 0.5,  # 推演 agent 采样温度。
    "simulator_top_p": 0.5,  # 推演 agent nucleus sampling。
    "extractor_temperature": 0.1,  # 结算 extractor agent 采样温度。
    "extractor_top_p": 0.1,  # 结算 extractor agent nucleus sampling。
}


def _clamp_float(value: object, default: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, parsed))


def normalize_openai_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def is_deepseek_base_url(base_url: str) -> bool:
    return "deepseek.com" in base_url.lower()


def is_deepseek_v4_model(model: str) -> bool:
    return (model or "").strip().lower().startswith("deepseek-v4")


def is_dashscope_base_url(base_url: str) -> bool:
    return "dashscope" in base_url.lower() or "aliyuncs" in base_url.lower()


def is_minimax_base_url(base_url: str) -> bool:
    lowered = base_url.lower()
    return "minimaxi.com" in lowered or "minimax.io" in lowered


def normalize_deepseek_reasoning_effort(level: str) -> str:
    normalized = (level or "").strip().lower()
    if normalized in {"max", "xhigh"}:
        return "max"
    if normalized in {"high", "medium", "low"}:
        return "high"
    return ""


def provider_extra_body(
    base_url: str,
    model: str = "",
    enable_thinking: bool = False,
    thinking_level: str = "",
    thinking_budget: Optional[int] = None,
) -> Optional[Dict[str, object]]:
    if is_deepseek_base_url(base_url):
        if not is_deepseek_v4_model(model):
            return None
        return {"thinking": {"type": "enabled" if enable_thinking else "disabled"}}
    if is_dashscope_base_url(base_url):
        if enable_thinking:
            body: Dict[str, object] = {"enable_thinking": True}
            if thinking_budget is not None:
                body["thinking_budget"] = int(thinking_budget)
            return body
        return {"enable_thinking": False}
    if is_minimax_base_url(base_url):
        thinking_type = (thinking_level or ("adaptive" if enable_thinking else "disabled")).strip().lower()
        if thinking_type not in {"adaptive", "disabled"}:
            thinking_type = "adaptive" if enable_thinking else "disabled"
        if enable_thinking:
            return {"thinking": {"type": thinking_type}, "reasoning_split": True}
        return {"thinking": {"type": "disabled"}}
    return None


def supports_openai_reasoning_effort(model: str) -> bool:
    model_id = model.lower()
    return model_id.startswith(("o1", "o3", "o4", "gpt-5"))


def normalize_thinking_level(level: str) -> str:
    return (level or "").strip()


def load_llm_config(
    base_url: str,
    model: str,
    api_key: str = "",
    timeout_seconds: float = 180.0,
    connect_timeout_seconds: float = 60.0,
    read_timeout_seconds: float = 120.0,
    thinking_level: str = "",
    advanced_model: str = "",
    advanced_base_url: str = "",
    advanced_api_key: str = "",
    advanced_thinking_level: str = "",
) -> LLMConfig:
    api_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not api_key:
        api_key = getpass.getpass("请输入 API key（不会保存，回车取消）：").strip()
    if not api_key:
        raise SystemExit("未提供 API key，无法使用 LLM。")
    adv_base = (advanced_base_url or "").strip()
    return LLMConfig(
        api_key=api_key,
        base_url=normalize_openai_base_url(base_url),
        model=model,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        thinking_level=normalize_thinking_level(thinking_level or os.environ.get("OPENAI_THINKING_LEVEL", "")),
        advanced_model=(advanced_model or "").strip(),
        advanced_base_url=normalize_openai_base_url(adv_base) if adv_base else "",
        advanced_api_key=(advanced_api_key or "").strip(),
        advanced_thinking_level=normalize_thinking_level(
            advanced_thinking_level or os.environ.get("OPENAI_ADVANCED_THINKING_LEVEL", "")
        ),
    )


# 角色 → 用 advanced model 还是 main model。
# 推演 / 打分 是回合结算的核心叙事 + 结构化抽取，最吃模型能力，单独走 advanced。
# 其余 agent（大臣对话、诏书润色、记忆检索、JSON 修复、聊天记忆抽取）保持 main，省钱保缓存。
_ADVANCED_ROLES = frozenset({"simulator", "extractor"})


def for_role(cfg: LLMConfig, role: str) -> LLMConfig:
    """按 agent 角色派生 LLMConfig：advanced 角色用 advanced_model（若已配），其余用 main model。
    advanced_model 为空时返回原 cfg（无任何替换）。"""
    if role in _ADVANCED_ROLES and (cfg.advanced_model or "").strip():
        adv_base = (cfg.advanced_base_url or "").strip() or cfg.base_url
        adv_key = (cfg.advanced_api_key or "").strip() or cfg.api_key
        return LLMConfig(
            api_key=adv_key,
            base_url=adv_base,
            model=cfg.advanced_model.strip(),
            max_tokens=cfg.max_tokens,
            timeout_seconds=cfg.timeout_seconds,
            connect_timeout_seconds=cfg.connect_timeout_seconds,
            read_timeout_seconds=cfg.read_timeout_seconds,
            thinking_level=cfg.advanced_thinking_level,
            advanced_model=cfg.advanced_model,
            advanced_base_url=cfg.advanced_base_url,
            advanced_api_key=cfg.advanced_api_key,
            advanced_thinking_level=cfg.advanced_thinking_level,
        )
    return cfg


def load_runtime_llm() -> Dict[str, str]:
    """读 data/runtime_llm.json。缺/坏返回空 dict。"""
    if not os.path.isfile(RUNTIME_LLM_PATH):
        return {}
    try:
        with open(RUNTIME_LLM_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {
        k: str(data.get(k, "") or "")
        for k in (
            "base_url",
            "model",
            "api_key",
            "thinking_level",
            "advanced_model",
            "advanced_base_url",
            "advanced_api_key",
            "advanced_thinking_level",
        )
    }
    if "max_tokens" in data:
        out["max_tokens"] = str(data["max_tokens"])
    if "timeout_seconds" in data:
        out["timeout_seconds"] = str(data["timeout_seconds"])
    if "connect_timeout_seconds" in data:
        out["connect_timeout_seconds"] = str(data["connect_timeout_seconds"])
    if "read_timeout_seconds" in data:
        out["read_timeout_seconds"] = str(data["read_timeout_seconds"])
    return out


def load_runtime_game() -> Dict[str, object]:
    """读 data/runtime_game.json（全局玩法设置）。缺/坏字段回落默认。"""
    data: Dict[str, object] = {}
    if os.path.isfile(RUNTIME_GAME_PATH):
        try:
            with open(RUNTIME_GAME_PATH, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    out: Dict[str, object] = dict(GAME_SETTINGS_DEFAULTS)
    try:
        out["hitl_min_decisions"] = max(0, min(5, int(data.get("hitl_min_decisions", out["hitl_min_decisions"]))))
    except (TypeError, ValueError):
        pass
    # 探针/测试可用环境变量 HITL_MIN_DECISIONS 覆盖（不污染 runtime_game.json）；
    # 设 0 即关掉决策点暂停，让结算单步直通。env 优先于 JSON 文件。
    if os.environ.get("HITL_MIN_DECISIONS"):
        try:
            out["hitl_min_decisions"] = max(0, min(5, int(os.environ["HITL_MIN_DECISIONS"])))
        except (TypeError, ValueError):
            pass
    try:
        out["court_chat_debate_rounds"] = max(1, min(8, int(data.get("court_chat_debate_rounds", out["court_chat_debate_rounds"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["court_chat_stream_speed"] = max(1, min(5, int(data.get("court_chat_stream_speed", out["court_chat_stream_speed"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["max_decree_issues"] = max(1, min(30, int(data.get("max_decree_issues", out["max_decree_issues"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["issue_log_limit"] = max(0, min(50, int(data.get("issue_log_limit", out["issue_log_limit"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["secret_order_person_limit"] = max(1, min(10, int(data.get("secret_order_person_limit", out["secret_order_person_limit"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["secret_order_total_limit"] = max(1, min(50, int(data.get("secret_order_total_limit", out["secret_order_total_limit"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["character_limit"] = max(40, min(300, int(data.get("character_limit", out["character_limit"]))))
    except (TypeError, ValueError):
        pass
    for key in (
        "minister_temperature",
        "minister_top_p",
        "simulator_temperature",
        "simulator_top_p",
        "extractor_temperature",
        "extractor_top_p",
    ):
        out[key] = _clamp_float(data.get(key, out[key]), float(out[key]), 0.0, 1.0)
    return out


def agent_sampling_settings(agent_key: str) -> tuple[float, float]:
    """读取 agent temperature/top_p。agent_key: minister / simulator / extractor。"""
    settings = load_runtime_game()
    default_temperature = float(GAME_SETTINGS_DEFAULTS.get(f"{agent_key}_temperature", 0.2))
    default_top_p = float(GAME_SETTINGS_DEFAULTS.get(f"{agent_key}_top_p", 0.2))
    return (
        _clamp_float(settings.get(f"{agent_key}_temperature"), default_temperature, 0.0, 1.0),
        _clamp_float(settings.get(f"{agent_key}_top_p"), default_top_p, 0.0, 1.0),
    )


def save_runtime_game(
    hitl_min_decisions: int,
    court_chat_debate_rounds: int = 3,
    court_chat_stream_speed: int = 3,
    max_decree_issues: int = 10,
    issue_log_limit: int = 6,
    secret_order_person_limit: int = 1,
    secret_order_total_limit: int = 5,
    character_limit: int = 120,
    minister_temperature: float = 0.6,
    minister_top_p: float = 0.9,
    simulator_temperature: float = 0.5,
    simulator_top_p: float = 0.5,
    extractor_temperature: float = 0.1,
    extractor_top_p: float = 0.1,
) -> Dict[str, object]:
    """写 data/runtime_game.json。各项 clamp 到合法区间。返回落盘后的设置。"""
    os.makedirs(os.path.dirname(RUNTIME_GAME_PATH), exist_ok=True)
    payload = {
        "hitl_min_decisions": max(0, min(5, int(hitl_min_decisions))),
        "court_chat_debate_rounds": max(1, min(8, int(court_chat_debate_rounds))),
        "court_chat_stream_speed": max(1, min(5, int(court_chat_stream_speed))),
        "max_decree_issues": max(1, min(30, int(max_decree_issues))),
        "issue_log_limit": max(0, min(50, int(issue_log_limit))),
        "secret_order_person_limit": max(1, min(10, int(secret_order_person_limit))),
        "secret_order_total_limit": max(1, min(50, int(secret_order_total_limit))),
        "character_limit": max(40, min(300, int(character_limit))),
        "minister_temperature": _clamp_float(minister_temperature, 0.6, 0.0, 1.0),
        "minister_top_p": _clamp_float(minister_top_p, 0.9, 0.0, 1.0),
        "simulator_temperature": _clamp_float(simulator_temperature, 0.5, 0.0, 1.0),
        "simulator_top_p": _clamp_float(simulator_top_p, 0.5, 0.0, 1.0),
        "extractor_temperature": _clamp_float(extractor_temperature, 0.1, 0.0, 1.0),
        "extractor_top_p": _clamp_float(extractor_top_p, 0.1, 0.0, 1.0),
    }
    with open(RUNTIME_GAME_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return payload


def save_runtime_llm(
    base_url: str,
    model: str,
    api_key: str,
    max_tokens: int = 8000,
    timeout_seconds: float = 180.0,
    connect_timeout_seconds: float = 60.0,
    read_timeout_seconds: float = 120.0,
    thinking_level: str = "",
    advanced_model: str = "",
    advanced_base_url: str = "",
    advanced_api_key: str = "",
    advanced_thinking_level: str = "",
) -> None:
    """写 data/runtime_llm.json。明文存盘——按用户选择。"""
    os.makedirs(os.path.dirname(RUNTIME_LLM_PATH), exist_ok=True)
    payload = {
        "base_url": (base_url or "").strip(),
        "model": (model or "").strip(),
        "api_key": (api_key or "").strip(),
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "connect_timeout_seconds": connect_timeout_seconds,
        "read_timeout_seconds": read_timeout_seconds,
        "thinking_level": normalize_thinking_level(thinking_level),
        "advanced_model": (advanced_model or "").strip(),
        "advanced_base_url": (advanced_base_url or "").strip(),
        "advanced_api_key": (advanced_api_key or "").strip(),
        "advanced_thinking_level": normalize_thinking_level(advanced_thinking_level),
    }
    with open(RUNTIME_LLM_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
