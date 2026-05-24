"""LLM 提供商配置：base_url 规范化、提供商检测、LLMConfig 加载。L1。"""

from __future__ import annotations

import getpass
import json
import os
from typing import Dict, Optional

from ming_sim.constants import ROOT_DIR
from ming_sim.models import LLMConfig

RUNTIME_LLM_PATH = os.path.join(ROOT_DIR, "data", "runtime_llm.json")


def normalize_openai_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def is_deepseek_base_url(base_url: str) -> bool:
    return "deepseek.com" in base_url.lower()


def is_dashscope_base_url(base_url: str) -> bool:
    return "dashscope" in base_url.lower() or "aliyuncs" in base_url.lower()


def provider_extra_body(base_url: str) -> Optional[Dict[str, object]]:
    if is_deepseek_base_url(base_url):
        return {"thinking": {"type": "disabled"}}
    if is_dashscope_base_url(base_url):
        return {"enable_thinking": False}
    return None


def supports_openai_reasoning_effort(model: str) -> bool:
    model_id = model.lower()
    return model_id.startswith(("o1", "o3", "o4", "gpt-5"))


def load_llm_config(base_url: str, model: str, api_key: str = "") -> LLMConfig:
    api_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not api_key:
        api_key = getpass.getpass("请输入 API key（不会保存，回车取消）：").strip()
    if not api_key:
        raise SystemExit("未提供 API key，无法使用 LLM。")
    return LLMConfig(api_key=api_key, base_url=normalize_openai_base_url(base_url), model=model)


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
    return {k: str(data.get(k, "") or "") for k in ("base_url", "model", "api_key")}


def save_runtime_llm(base_url: str, model: str, api_key: str) -> None:
    """写 data/runtime_llm.json。明文存盘——按用户选择。"""
    os.makedirs(os.path.dirname(RUNTIME_LLM_PATH), exist_ok=True)
    payload = {
        "base_url": (base_url or "").strip(),
        "model": (model or "").strip(),
        "api_key": (api_key or "").strip(),
    }
    with open(RUNTIME_LLM_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
