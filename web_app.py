#!/usr/bin/env python3
"""FastAPI web entry for Ming Salvage Sim.

薄壳：路由调 ming_sim.session.GameSession（与 CLI 共用同一流转层）。
拟旨 draft 待确认：大臣 propose_directive → pending → 前端 准/驳。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ming_sim.constants import ROOT_DIR
from ming_sim.paths import bundled_path, user_data_path, user_data_dir
from ming_sim import scenario_active
from ming_sim.content import load_character_content, load_event_content
from ming_sim.exceptions import ExitGame, LLMUnavailable
from ming_sim.llm_config import (
    load_llm_config,
    load_runtime_game,
    load_runtime_llm,
    normalize_openai_base_url,
    normalize_thinking_level,
    save_runtime_game,
    save_runtime_llm,
)
from agno.agent import Agent

from ming_sim.agents import _dump_llm_messages, build_simulator_context
from ming_sim.llm_model import create_agno_db, create_chat_model, extract_agent_text, verify_llm_available
from ming_sim.llm_contract import fail_if_llm_error
from ming_sim.issues import _format_issue_ongoing
from ming_sim.session import GameSession
from ming_sim.session import AUTO_SAVE_PREFIX
from ming_sim.context import character_context_with_db, match_minister_from_text
from ming_sim.constants import TURN_UNIT, BUILDING_CATEGORIES
from ming_sim.flows import ARMY_SALARY_PRIORITY, calc_province_fiscal, compute_budget_lines
from ming_sim.simulation import build_simulator_payload
from ming_sim.directives import (
    StructuredDirectiveError,
    load_directive_templates,
)
from ming_sim.exceptions import LLMContractError  # noqa: F401  (保留：供错误处理)
from ming_sim.models import Character, LLMConfig
from ming_sim.registry import _BASE_SKILLS
from ming_sim.tools import build_minister_tools
from ming_sim.token_stats import record_stream_metrics
from ming_sim import steam_events

WEB_DIST = bundled_path("web", "dist")
# 用户上传的自定义立绘存档级目录（不随 build 清空，git 可忽略）。
# frozen 模式落 ~/.ming_sim/uploads/portraits/，源码模式落 <repo>/data/uploads/portraits/。
UPLOAD_PORTRAIT_DIR = user_data_path("uploads", "portraits")
# 自定义立绘 portrait_id 前缀；前端据此解析到 /portraits/custom/<name>.png。
CUSTOM_PORTRAIT_PREFIX = "custom:"
ALLOWED_PORTRAIT_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_PORTRAIT_BYTES = 8 * 1024 * 1024  # 8MB 上限

_AGNO_SKILL_DESCRIPTIONS = {
    "memory-recall": "查阅既往邸报与起居注，补足旧事来龙去脉。",
    "decree-drafting": "把已定处置整理成圣旨草案并入档。",
    "secret-order": "下达、催办、追问密令，并记入密档。",
    "summon": "传召朝臣，必要时补登名册外人物。",
    "tax-adjust": "户部调税事项，立为可追踪政务。",
    "consort-selection": "奉旨遴选后宫人选，呈候选名单。",
    "court-roster": "查阅在朝人物名册。",
    "army-roster": "查阅军队名册。",
}

_TOOL_LABELS = {
    "list_memorials": "查看在办事项",
    "inspect_memorial": "查看事项细节",
    "list_regions": "查看地区警讯",
    "inspect_region": "查看地区详情",
    "list_buildings": "查看建筑",
    "inspect_building": "查看建筑详情",
    "estimate_resistance": "估算阻力",
    "read_past_report": "查阅旧邸报",
    "search_memories": "检索起居注",
    "inspect_treasury_ledger": "查钱库流水",
    "propose_directive": "拟旨入档",
    "secret_order": "密令",
    "dismiss_minister": "退朝",
    "summon_minister": "传召大臣",
    "register_unlisted_person": "登记名册外人物",
    "query_court_roster": "查询朝臣名册",
    "query_army_roster": "查询军队名册",
    "propose_appointment": "吏部铨选",
    "check_treasury": "查国库",
    "adjust_tax": "调税",
    "present_consort_candidates": "选妃呈名单",
}

_RUNTIME_SKILL_LABELS = {
    "memory-recall": "旧事记忆",
    "decree-drafting": "拟旨入档",
    "secret-order": "密令",
    "summon": "召见传人",
    "tax-adjust": "调税",
    "consort-selection": "选妃",
    "court-roster": "朝臣名册查询",
    "army-roster": "军队名册查询",
}

_TOOL_DESCRIPTIONS = {
    "list_memorials": "查看当前在办的所有事项。",
    "inspect_memorial": "查看某条在办事项的细节。",
    "list_regions": "查看两京十三省最危险地区和账面月税。",
    "inspect_region": "查看某一地区人口、民心、动乱、天灾、人祸、田亩和税收。",
    "list_buildings": "查看全国在册建筑的等级、完好、维护费与产出。",
    "inspect_building": "查看某座建筑的类别、等级、完好、维护费、风险与产出。",
    "estimate_resistance": "估算某条在办事项下旨推动时的主要阻力。",
    "read_past_report": "查阅既往邸报，了解此前朝局走向。",
    "search_memories": "检索起居注章节旧事。",
    "inspect_treasury_ledger": "查国库或内库的历史流水明细。",
    "propose_directive": "把已定处置方案拟成圣旨草稿呈阅。",
    "secret_order": "处理密令下达、进展、结案与催办。",
    "dismiss_minister": "结束本次召见。",
    "summon_minister": "传召另一位大臣入殿。",
    "register_unlisted_person": "登记名册外人物，使其进入本局可召见人物池。",
    "query_court_roster": "查询朝臣名册。",
    "query_army_roster": "查询军队名册。",
    "propose_appointment": "吏部铨选拟任。",
    "check_treasury": "查国库、内库、收支和欠账。",
    "adjust_tax": "奏请调整税额，立为可追踪调税事项。",
    "present_consort_candidates": "奉旨选妃，呈候选名单。",
}

_PLAYER_TEXT_REPLACEMENTS = (
    ("Agno", ""),
    ("agno", ""),
    ("Tool", "工具"),
    ("tool", "工具"),
    ("skill", "能力"),
    ("issue", "事项"),
    ("slot", "事项序号"),
    ("list_memorials", "在办事项清单"),
    ("decree_text", "圣旨正文"),
    ("action", "处置"),
    ("order_id", "密令编号"),
    ("tags_json", "关键词"),
    ("aliases_json", "别名"),
    ("deadline_months", "期限"),
    ("summon_after", "随后传召"),
)


def _player_text(text: str) -> str:
    out = str(text or "").strip()
    for old, new in _PLAYER_TEXT_REPLACEMENTS:
        out = out.replace(old, new)
    return out.replace("（）", "").strip()

# resolve/fail_condition 同时喂 extractor（需 input.factions/leverage 等技术 key）与展示给玩家。
# 展示前把技术词替换成中文，原文不动（LLM 仍读原文判定）。按长键先替，避免子串误伤。
_CONDITION_DISPLAY_REPLACEMENTS = [
    ("input.factions", "派系盘面"),
    ("input.classes", "阶级盘面"),
    ("input.regions", "地区盘面"),
    ("input.armies", "军队盘面"),
    ("input.current_state", "国势盘面"),
    ("region.", "地区："),
    ("army.", "军队："),
    ("faction.", "派系："),
    ("class.", "阶级："),
    ("power.", "势力："),
    ("maintenance_per_turn", "月饷"),
    ("registered_land", "已册田亩"),
    ("hidden_land", "隐田"),
    ("tax_per_turn", "月税"),
    ("public_support", "民心"),
    ("grain_output", "粮食年产"),
    ("grain_stock", "存粮"),
    ("unrest", "动乱"),
    ("gentry_resistance", "士绅阻力"),
    ("military_pressure", "边防压力"),
    ("supply", "补给"),
    ("morale", "士气"),
    ("training", "操练"),
    ("equipment", "军械"),
    ("arrears", "欠饷"),
    ("mobility", "机动"),
    ("loyalty", "忠诚"),
    ("controlled_by", "归属"),
    ("leverage", "影响力"),
    ("satisfaction", "满意度"),
    ("resolved", "达成"),
    ("failed", "失败"),
    ("region ", "地区 "),
    ("shenyang_liaoyang", "沈阳辽阳"),
    ("dongjiang_area", "东江海域"),
    ("mongol_chahar", "察哈尔蒙古"),
    ("beizhili", "北直隶"),
    ("nanzhili", "南直隶"),
    ("shandong", "山东"),
    ("shanxi", "山西"),
    ("henan", "河南"),
    ("shaanxi", "陕西"),
    ("zhejiang", "浙江"),
    ("jiangxi", "江西"),
    ("huguang", "湖广"),
    ("sichuan", "四川"),
    ("fujian", "福建"),
    ("guangdong", "广东"),
    ("guangxi", "广西"),
    ("yunnan", "云南"),
    ("guizhou", "贵州"),
    ("liaodong", "辽东"),
    ("dongjiang", "东江"),
    ("xuan_da", "宣大"),
    ("guanning", "关宁军"),
    ("jingying", "京营"),
    ("jizhen", "蓟镇"),
    ("houjin", "后金"),
    ("ming", "大明"),
    (".max", "最高值"),
    (".min", "最低值"),
    (".sum", "合计"),
    (".avg", "均值"),
    ("|", "、"),
    (".", "·"),
]


def _humanize_condition(text: str) -> str:
    """把结案/失败条件里的技术 key 替换成玩家可读中文（仅用于展示）。"""
    if not text:
        return text
    for src, dst in _CONDITION_DISPLAY_REPLACEMENTS:
        text = text.replace(src, dst)
    return text


_LEGACY_GATE_FIELD_LABELS = {
    "leverage": "影响力",
    "satisfaction": "满意度",
    "controlled_by": "归属",
    "hidden_land": "隐田",
    "gentry_resistance": "士绅阻力",
    "public_support": "民心",
    "unrest": "动乱",
    "military_pressure": "边防压力",
    "tax_per_turn": "税收",
    "morale": "士气",
    "training": "训练",
    "loyalty": "忠诚",
    "supply": "补给",
    "equipment": "装备",
}

_LEGACY_GATE_AGG_LABELS = {
    "max": "最高",
    "min": "最低",
    "sum": "合计",
    "avg": "平均",
}

_LEGACY_GATE_VALUE_LABELS = {
    "ming": "大明",
    "houjin": "后金",
    "bandits": "流寇",
}


def _legacy_gate_subject(raw_key: str, content: Any) -> str:
    parts = raw_key.split(".")
    if len(parts) < 3:
        return _humanize_condition(raw_key)
    scope, raw_ids, field = parts[0], parts[1], parts[2]
    agg = parts[3] if len(parts) > 3 else ""
    ids = [item for item in raw_ids.split("|") if item]
    if scope == "region":
        names = [getattr(content.regions.get(item), "name", item) for item in ids]
    elif scope == "faction":
        names = ids
    elif scope == "army":
        names = [getattr(content.armies.get(item), "name", item) for item in ids]
    else:
        names = ids
    entity = "、".join(str(name) for name in names)
    field_label = _LEGACY_GATE_FIELD_LABELS.get(field, _humanize_condition(field))
    agg_label = _LEGACY_GATE_AGG_LABELS.get(agg, "")
    return f"{entity}{field_label}{agg_label}"


def _humanize_legacy_gate(gate: Dict[str, str], content: Any) -> str:
    """把开局帝国修正的 clear_gate 转为中文展示文案。"""
    clauses: List[str] = []
    for raw_key, raw_expr in gate.items():
        subject = _legacy_gate_subject(str(raw_key), content)
        expr = str(raw_expr).strip()
        match = re.match(r"^(<=|>=|==|!=|<|>)\s*(.+)$", expr)
        if not match:
            clauses.append(f"{subject}达到 {expr}")
            continue
        op, value = match.groups()
        value = _LEGACY_GATE_VALUE_LABELS.get(value.strip(), value.strip())
        op_label = {
            "<=": "≤",
            ">=": "≥",
            "==": "为",
            "!=": "不为",
            "<": "<",
            ">": ">",
        }.get(op, op)
        clauses.append(f"{subject}{op_label}{value}")
    return "；".join(clauses)


def _legacy_effect_entity_name(scope: str, entity_id: str, content: Any) -> str:
    if scope == "regions":
        return str(getattr(content.regions.get(entity_id), "name", entity_id))
    if scope == "armies":
        return str(getattr(content.armies.get(entity_id), "name", entity_id))
    return entity_id


def _legacy_pct(value: int) -> str:
    return f"{'+' if value > 0 else ''}{value}%"


def _humanize_legacy_effect(modifiers: Dict[str, Any], content: Any) -> str:
    """把 legacy modifiers 转为中文展示，避免前端露出 nanzhili/guanning 等内部 id。"""
    parts: List[str] = []
    for account in ("国库", "内库", "民心", "皇威"):
        value = modifiers.get(account)
        if isinstance(value, (int, float)):
            parts.append(f"{account}{_legacy_pct(int(value))}")
    for scope in ("regions", "armies"):
        block = modifiers.get(scope)
        if not isinstance(block, dict):
            continue
        for entity_id, fields in block.items():
            if not isinstance(fields, dict):
                continue
            entity_name = _legacy_effect_entity_name(scope, str(entity_id), content)
            for field, value in fields.items():
                if not isinstance(value, (int, float)):
                    continue
                field_label = _LEGACY_GATE_FIELD_LABELS.get(str(field), _humanize_condition(str(field)))
                parts.append(f"{entity_name}{field_label}{_legacy_pct(int(value))}")
    return "、".join(parts)


def _delete_sqlite_db_files_or_raise(db_path: str) -> None:
    """删除 SQLite 主库及 WAL/SHM；失败时阻断重开，避免误读旧档。"""
    for suffix in ("", "-wal", "-shm"):
        target = db_path + suffix
        if not os.path.exists(target):
            continue
        if not os.path.isfile(target):
            raise HTTPException(
                status_code=500,
                detail=f"重开失败：无法清理主库文件 {target}，它不是普通文件。请检查该路径后再重试。",
            )
        try:
            os.remove(target)
        except PermissionError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"重开失败：权限不足，无法删除主库文件 {target}。"
                    "请关闭占用该文件的程序，或用管理员权限运行游戏后重试。"
                ),
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"重开失败：无法删除主库文件 {target}。系统返回：{exc}。"
                    "请确认没有其他游戏进程占用该文件；若是权限问题，请用管理员权限运行游戏后重试。"
                ),
            ) from exc


def _verify_llm_configs_or_raise(config: LLMConfig) -> None:
    """校验主模型；若配置了 advanced_model，也用其实际 base/key 单独校验。"""
    try:
        verify_llm_available(config)
    except LLMUnavailable as e:
        raise HTTPException(status_code=400, detail=_llm_error_detail(e, "主模型连通性检查失败：")) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_llm_error_detail(e, "主模型连通性检查失败：")) from None

    advanced_model = (config.advanced_model or "").strip()
    if not advanced_model:
        return
    advanced_config = LLMConfig(
        api_key=(config.advanced_api_key or "").strip() or config.api_key,
        base_url=(config.advanced_base_url or "").strip() or config.base_url,
        model=advanced_model,
        max_tokens=config.max_tokens,
        timeout_seconds=config.timeout_seconds,
        connect_timeout_seconds=config.connect_timeout_seconds,
        read_timeout_seconds=config.read_timeout_seconds,
        thinking_level=config.advanced_thinking_level,
        advanced_model=config.advanced_model,
        advanced_base_url=config.advanced_base_url,
        advanced_api_key=config.advanced_api_key,
        advanced_thinking_level=config.advanced_thinking_level,
    )
    try:
        verify_llm_available(advanced_config, enable_thinking=True)
    except LLMUnavailable as e:
        raise HTTPException(status_code=400, detail=_llm_error_detail(e, "高级模型连通性检查失败：")) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_llm_error_detail(e, "高级模型连通性检查失败：")) from None


def _llm_error_detail(exc: Exception, prefix: str = "") -> Dict[str, Any]:
    message = f"{prefix}{exc.message if hasattr(exc, 'message') else str(exc)}"
    return {
        "code": getattr(exc, "code", "llm_error"),
        "message": message,
        "provider_message": getattr(exc, "provider_message", str(exc)),
        "status_code": getattr(exc, "status_code", None),
    }


def _build_llm_config_from_runtime() -> LLMConfig:
    """按 runtime_llm.json（优先）+ env 组装 LLMConfig。无 API key 抛 LLMUnavailable。
    WebGame.__init__ 与剧本生成端点共用，避免重复。"""
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    advanced_model = os.environ.get("OPENAI_ADVANCED_MODEL", "")
    advanced_base_url = os.environ.get("OPENAI_ADVANCED_BASE_URL", "")
    advanced_api_key = os.environ.get("OPENAI_ADVANCED_API_KEY", "")
    thinking_level = os.environ.get("OPENAI_THINKING_LEVEL", "")
    advanced_thinking_level = os.environ.get("OPENAI_ADVANCED_THINKING_LEVEL", "")
    timeout_seconds = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "180") or 180)
    connect_timeout_seconds = float(os.environ.get("OPENAI_CONNECT_TIMEOUT_SECONDS", "60") or 60)
    read_timeout_seconds = float(os.environ.get("OPENAI_READ_TIMEOUT_SECONDS", "120") or 120)
    # 菜单写的 runtime_llm.json 优先于 env，让「在网页里改的配置」重启后仍生效。
    runtime = load_runtime_llm()
    base_url = runtime.get("base_url") or base_url
    model = runtime.get("model") or model
    api_key = runtime.get("api_key") or api_key
    thinking_level = runtime.get("thinking_level") or thinking_level
    advanced_model = runtime.get("advanced_model") or advanced_model
    advanced_base_url = runtime.get("advanced_base_url") or advanced_base_url
    advanced_api_key = runtime.get("advanced_api_key") or advanced_api_key
    advanced_thinking_level = runtime.get("advanced_thinking_level") or advanced_thinking_level
    max_tokens = int(runtime.get("max_tokens") or 8000)
    timeout_seconds = float(runtime.get("timeout_seconds") or timeout_seconds)
    connect_timeout_seconds = float(runtime.get("connect_timeout_seconds") or connect_timeout_seconds)
    read_timeout_seconds = float(runtime.get("read_timeout_seconds") or read_timeout_seconds)
    if not api_key:
        raise LLMUnavailable("未配 API key，请先到设置页填写。")
    adv_base = (advanced_base_url or "").strip()
    return LLMConfig(
        api_key=api_key,
        base_url=normalize_openai_base_url(base_url),
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        thinking_level=normalize_thinking_level(thinking_level),
        advanced_model=(advanced_model or "").strip(),
        advanced_base_url=normalize_openai_base_url(adv_base) if adv_base else "",
        advanced_api_key=(advanced_api_key or "").strip(),
        advanced_thinking_level=normalize_thinking_level(advanced_thinking_level),
    )


class ChatRequest(BaseModel):
    message: str


class CourtChatRequest(BaseModel):
    message: str
    ministers: List[str] = Field(default_factory=list)


class CourtChatSummaryMessage(BaseModel):
    role: str
    speaker: str
    content: str


class CourtChatSummaryRequest(BaseModel):
    messages: List[CourtChatSummaryMessage] = Field(default_factory=list)


class DirectiveRequest(BaseModel):
    text: str
    notes: str = ""


class StructuredDirectiveRequest(BaseModel):
    template_id: str
    fields: Dict[str, Any] = Field(default_factory=dict)


class SecretOrderRequest(BaseModel):
    title: str
    content: str
    tags: List[str] = []
    deadline_months: int = 0


class DirectivePatch(BaseModel):
    text: Optional[str] = None
    notes: Optional[str] = None


class ScenarioCreateRequest(BaseModel):
    name: str
    description: str = ""
    # 来源：""/"blank"=空白；"__default__"=复制默认（崇祯元年）；其余=复制该剧本 id 的三件套。
    copy_from: str = ""
    from_default: bool = False  # 兼容旧字段：True 等价 copy_from="__default__"


class ScenarioManifestPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ScenarioFilePut(BaseModel):
    # 整个文件的 JSON：characters → 对象，events/seed_events → 数组。
    content: Any


class ScenarioGenerateRequest(BaseModel):
    prompt: str
    name: str = ""
    description: str = ""
    files: Optional[List[str]] = None  # 默认三文件全生成


class WebGame:
    """Web 端会话包装：持一个 GameSession + 网页专属态（聊天历史、收藏）。"""

    def __init__(self, fresh: bool = False) -> None:
        """实例化 = 真正进入游戏。无 API key 直接抛 LLMUnavailable。
        fresh=True：先清空主 DB（新游戏）再建 session。"""
        db_path = os.environ.get("MING_SIM_DB", "")
        # 默认存到用户数据目录（frozen=~/.ming_sim/ming_sim.db；源码=<repo>/data/ming_sim.db）。
        if not db_path:
            db_path = user_data_path("ming_sim.db")
        elif not os.path.isabs(db_path):
            db_path = str(user_data_dir() / db_path)
        llm_config = _build_llm_config_from_runtime()
        random.seed(int(os.environ.get("MING_SIM_SEED", "7")))
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        if fresh:
            _delete_sqlite_db_files_or_raise(db_path)
        self.session = GameSession(db_path, llm_config)
        self.session.begin_turn()
        # 召对记录持久化在 chat_messages 表，启动时恢复进内存缓存。
        self.chat_history: Dict[str, List[Dict[str, str]]] = {
            name: [] for name in self.session.content.characters
        }
        for name, msgs in self.db.load_all_chat_history().items():
            self.chat_history.setdefault(name, []).extend(msgs)
        _DEFAULT_FAVORITES = {"王承恩", "曹化淳", "李若琏", "魏忠贤", "田尔耕"}
        _fav_raw = self.db.kv_get("favorites")
        self.favorites: set = set(json.loads(_fav_raw)) if _fav_raw else set(_DEFAULT_FAVORITES)
        if not _fav_raw:
            self.db.kv_set("favorites", json.dumps(sorted(self.favorites)))

    # ── 存档管理 ─────────────────────────────────────────────────────────
    def saves_dir(self) -> str:
        return user_data_path("saves")

    def list_saves(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        campaign_id = (self.db.kv_get("campaign_id") or "").strip()
        for fname in sorted(os.listdir(self.saves_dir())):
            if not fname.endswith(".db"):
                continue
            if not _save_visible_for_campaign(fname, campaign_id):
                continue
            full = os.path.join(self.saves_dir(), fname)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out.append({
                "name": fname[:-3],
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
        out.sort(key=lambda x: x["mtime"], reverse=True)
        return out

    def _safe_save_name(self, name: str) -> str:
        cleaned = "".join(c for c in name.strip() if c.isalnum() or c in "._-")
        if not cleaned or cleaned.startswith("."):
            raise HTTPException(status_code=400, detail="存档名非法。仅允许字母/数字/._- ")
        return cleaned

    def save_to(self, name: str) -> Dict[str, Any]:
        safe = self._safe_save_name(name)
        target = os.path.join(self.saves_dir(), f"{safe}.db")
        self.db.backup_to(target)
        return {"name": safe, "path": target}

    def delete_save(self, name: str) -> None:
        safe = self._safe_save_name(name)
        target = os.path.join(self.saves_dir(), f"{safe}.db")
        if not os.path.isfile(target):
            raise HTTPException(status_code=404, detail="存档不存在。")
        os.remove(target)

    def reset_game(self) -> None:
        """全清主 DB：关连接 → 删 sqlite 主/wal/shm → 重建空 session。
        存档目录不动。"""
        try:
            self.session.close()
        except Exception:
            pass
        _delete_sqlite_db_files_or_raise(self.db_path)
        self._rebuild_session(self.session.llm_config)

    def load_save(self, name: str) -> None:
        """从存档热替换主 DB：备份当前 → 拷源到主 DB → 重建 session。"""
        safe = self._safe_save_name(name)
        source = os.path.join(self.saves_dir(), f"{safe}.db")
        if not os.path.isfile(source):
            raise HTTPException(status_code=404, detail="存档不存在。")
        # 先关闭当前 session 的 DB 连接，避免 Windows/某些平台上的 file lock。
        try:
            self.session.close()
        except Exception:
            pass
        # 用 sqlite backup 把存档拷回主路径
        import sqlite3 as _sqlite3
        src_conn = _sqlite3.connect(source)
        dst_conn = _sqlite3.connect(self.db_path)
        try:
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()
        self._rebuild_session(self.session.llm_config)

    def _rebuild_session(self, llm_config: LLMConfig) -> None:
        """用新 llm_config（或换完 DB 后）重建 GameSession + 内存缓存。"""
        verify_llm_available(llm_config)
        self.session = GameSession(self.db_path, llm_config)
        self.session.begin_turn()
        self.chat_history = {name: [] for name in self.session.content.characters}
        for name, msgs in self.db.load_all_chat_history().items():
            self.chat_history.setdefault(name, []).extend(msgs)
        _DEFAULT_FAVORITES = {"王承恩", "曹化淳", "李若琏", "魏忠贤", "田尔耕"}
        _fav_raw = self.db.kv_get("favorites")
        self.favorites = set(json.loads(_fav_raw)) if _fav_raw else set(_DEFAULT_FAVORITES)
        if not _fav_raw:
            self.db.kv_set("favorites", json.dumps(sorted(self.favorites)))

    def apply_llm_config(
        self,
        base_url: str,
        model: str,
        api_key: str,
        max_tokens: int = 0,
        timeout_seconds: float = 0,
        connect_timeout_seconds: float = 0,
        read_timeout_seconds: float = 0,
        thinking_level: Optional[str] = None,
        advanced_model: Optional[str] = None,
        advanced_base_url: Optional[str] = None,
        advanced_api_key: Optional[str] = None,
        advanced_thinking_level: Optional[str] = None,
    ) -> LLMConfig:
        base = normalize_openai_base_url(base_url.strip() or self.session.llm_config.base_url)
        new_model = model.strip() or self.session.llm_config.model
        new_key = api_key.strip() or self.session.llm_config.api_key
        new_max = max_tokens if max_tokens > 0 else self.session.llm_config.max_tokens
        new_timeout = timeout_seconds if timeout_seconds > 0 else self.session.llm_config.timeout_seconds
        new_connect = connect_timeout_seconds if connect_timeout_seconds > 0 else self.session.llm_config.connect_timeout_seconds
        new_read = read_timeout_seconds if read_timeout_seconds > 0 else self.session.llm_config.read_timeout_seconds
        if thinking_level is None:
            new_thinking_level = self.session.llm_config.thinking_level
        else:
            new_thinking_level = normalize_thinking_level(thinking_level)
        # advanced_* = None 表示不动；传空串表示显式清空。
        if advanced_model is None:
            new_advanced = self.session.llm_config.advanced_model
        else:
            new_advanced = advanced_model.strip()
        if advanced_base_url is None:
            new_adv_base = self.session.llm_config.advanced_base_url
        else:
            adv_base_in = advanced_base_url.strip()
            new_adv_base = normalize_openai_base_url(adv_base_in) if adv_base_in else ""
        if advanced_api_key is None:
            new_adv_key = self.session.llm_config.advanced_api_key
        else:
            new_adv_key = advanced_api_key.strip()
        if advanced_thinking_level is None:
            new_adv_thinking_level = self.session.llm_config.advanced_thinking_level
        else:
            new_adv_thinking_level = normalize_thinking_level(advanced_thinking_level)
        new_config = LLMConfig(
            api_key=new_key,
            base_url=base,
            model=new_model,
            max_tokens=new_max,
            timeout_seconds=new_timeout,
            connect_timeout_seconds=new_connect,
            read_timeout_seconds=new_read,
            thinking_level=new_thinking_level,
            advanced_model=new_advanced,
            advanced_base_url=new_adv_base,
            advanced_api_key=new_adv_key,
            advanced_thinking_level=new_adv_thinking_level,
        )
        _verify_llm_configs_or_raise(new_config)
        save_runtime_llm(
            new_config.base_url,
            new_config.model,
            new_config.api_key,
            new_config.max_tokens,
            new_config.timeout_seconds,
            new_config.connect_timeout_seconds,
            new_config.read_timeout_seconds,
            new_config.thinking_level,
            new_config.advanced_model,
            new_config.advanced_base_url,
            new_config.advanced_api_key,
            new_config.advanced_thinking_level,
        )
        self.session.llm_config = new_config
        # 重建 registry 让大臣 Agent 用新配置
        self.session.begin_turn()
        return new_config

    # ── 便捷属性 ──────────────────────────────────────────────────────────
    @property
    def db(self):
        return self.session.db

    @property
    def state(self):
        return self.session.state

    @property
    def content(self):
        return self.session.content

    @property
    def previous_summary(self) -> str:
        return self.session.previous_summary

    @property
    def last_decree(self) -> str:
        return self.session.last_decree

    @property
    def last_report(self) -> str:
        return self.session.last_report

    def refresh_turn(self) -> None:
        self.session.begin_turn()

    # ── 自定义立绘 ────────────────────────────────────────────────────────
    def find_character(self, name: str) -> Optional[Character]:
        return self.content.characters.get(name)

    def set_custom_portrait(self, name: str, portrait_id: str) -> None:
        """落库并回写内存：把某人物 portrait_id 指向自定义立绘。"""
        self.db.set_portrait_id(name, portrait_id)
        character = self.content.characters.get(name)
        if character is not None:
            character.portrait_id = portrait_id

    # ── 序列化 ────────────────────────────────────────────────────────────
    def _runtime_query_flags(self) -> tuple[bool, bool]:
        active_char_count = sum(
            1 for ch in self.content.characters.values()
            if ch.office_type != "后宫"
            and getattr(ch, "power_id", "ming") == "ming"
            and self.db.get_character_status(ch.name)[0] != "offstage"
        )
        army_count = self.db.conn.execute("SELECT COUNT(*) FROM armies WHERE active = 1").fetchone()[0]
        return active_char_count > 100, army_count > 30

    def _runtime_skill_payloads(self, character: Character) -> List[Dict[str, Any]]:
        use_roster_tool, use_army_tool = self._runtime_query_flags()
        grant = self.db.get_office_court_grant(character.office_type)
        agno_skill_ids = list(_BASE_SKILLS)
        for skill_id in list(grant.get("agno_skills") or []):
            if skill_id not in agno_skill_ids:
                agno_skill_ids.append(str(skill_id))
        if use_roster_tool and "court-roster" not in agno_skill_ids:
            agno_skill_ids.append("court-roster")
        if use_army_tool and "army-roster" not in agno_skill_ids:
            agno_skill_ids.append("army-roster")

        context = self.session.registry.context
        tools = build_minister_tools(
            character,
            context,
            use_roster_tool=use_roster_tool,
            use_army_tool=use_army_tool,
        )
        if "present_consort_candidates" in (grant.get("court_tools") or []):
            def present_consort_candidates() -> str:
                """奉旨选妃，呈候选名单。"""
                return ""
            tools.append(present_consort_candidates)

        payloads: List[Dict[str, Any]] = []
        for skill_id in agno_skill_ids:
            skill_name = _RUNTIME_SKILL_LABELS.get(skill_id, "可用能力")
            skill_description = _AGNO_SKILL_DESCRIPTIONS.get(skill_id, "")
            payloads.append({
                "id": skill_id,
                "name": _player_text(skill_name),
                "kind": "agno_skill",
                "sources": ["运行时加载"],
                "description": _player_text(skill_description),
            })
        seen_tools: set[str] = set()
        for tool in tools:
            tool_id = getattr(tool, "__name__", str(tool))
            if tool_id in seen_tools:
                continue
            seen_tools.add(tool_id)
            tool_name = _TOOL_LABELS.get(tool_id, "可用工具")
            tool_description = _TOOL_DESCRIPTIONS.get(tool_id, "")
            payloads.append({
                "id": tool_id,
                "name": _player_text(tool_name),
                "kind": "tool",
                "sources": ["运行时挂载"],
                "description": _player_text(tool_description),
            })
        return payloads

    def public_character(self, character: Character) -> Dict[str, Any]:
        status, status_reason = self.db.get_character_status(character.name)
        status_label = _STATUS_LABEL_WEB.get(status, "在朝" if status == "active" else status)
        office = character.office  # 去职者已被清空，可能为空串
        # summary 不含官职（卡片/详情已单独显 office），避免重复
        summary = f"{character.faction}一系，行事{character.style}。"
        meta_row = self.db.conn.execute(
            "SELECT power_id, origin, archived FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        power_id = (meta_row["power_id"] if meta_row else None) or getattr(character, "power_id", "ming") or "ming"
        origin = (meta_row["origin"] if meta_row else None) or "preset"
        archived = bool(int((meta_row["archived"] if meta_row else 0) or 0))
        return {
            "name": character.name,
            "office": office,
            "office_type": character.office_type,
            "faction": character.faction,
            "aliases": character.aliases,
            "personal_skills": character.personal_skills,
            "loyalty": character.loyalty,
            "ability": character.ability,
            "integrity": character.integrity,
            "courage": character.courage,
            "diplomacy": character.diplomacy,
            "martial": character.martial,
            "stewardship": character.stewardship,
            "intrigue": character.intrigue,
            "learning": character.learning,
            "style": character.style,
            "location": character.location,
            "birth_year": character.birth_year,
            "historical_death_year": character.historical_death_year,
            "historical_death_month": character.historical_death_month,
            "debut_year": character.debut_year,
            "debut_month": character.debut_month,
            "status": status,
            "status_reason": status_reason,
            "status_label": status_label,
            "summary": summary,
            "description": character.summary,
            "portrait_id": character.portrait_id,
            "power_id": power_id,
            "origin": origin,
            "archived": archived,
            "skills": self._runtime_skill_payloads(character),
            "favorite": character.name in self.favorites,
        }

    def character_power_id(self, character: Character) -> str:
        row = self.db.conn.execute(
            "SELECT power_id FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        return (row["power_id"] if row else None) or getattr(character, "power_id", "ming") or "ming"

    def directive_payload(self, row) -> Dict[str, Any]:
        skill_id = str(row["skill_id"] or "")
        return {
            "id": int(row["id"]),
            "event_id": row["event_id"] or "",
            "event_title": (row["event_title"] if "event_title" in row.keys() else "") or "",
            "actor": row["actor"] or "",
            "skill_id": skill_id,
            "skill_name": _TOOL_LABELS.get(skill_id) or _RUNTIME_SKILL_LABELS.get(skill_id) or skill_id,
            "text": row["text"],
            "source": row["source"],
            "status": row["status"],
            "notes": row["notes"],
            "authority": row["notes"] or "",
        }

    def _character_from_db_row(self, row) -> Character:
        try:
            aliases = json.loads(row["aliases"] or "[]")
        except (TypeError, json.JSONDecodeError):
            aliases = []
        try:
            personal_skills = json.loads(row["personal_skills"] or "[]")
        except (TypeError, json.JSONDecodeError):
            personal_skills = []
        if not isinstance(aliases, list):
            aliases = []
        if not isinstance(personal_skills, list):
            personal_skills = []
        return Character(
            name=str(row["name"]),
            office=str(row["office"] or ""),
            office_type=str(row["office_type"] or ""),
            faction=str(row["faction"] or "中立"),
            aliases=[str(item) for item in aliases if str(item).strip()],
            personal_skills=[str(item) for item in personal_skills if str(item).strip()],
            loyalty=int(row["loyalty"] or 50),
            ability=int(row["ability"] or 50),
            integrity=int(row["integrity"] or 50),
            courage=int(row["courage"] or 50),
            style=str(row["style"] or ""),
            power_id=str(row["power_id"] or "ming"),
            diplomacy=int(row["diplomacy"] or 50),
            martial=int(row["martial"] or 50),
            stewardship=int(row["stewardship"] or 50),
            intrigue=int(row["intrigue"] or 50),
            learning=int(row["learning"] or 50),
            location=str(row["location"] or ""),
            birth_year=int(row["birth_year"] or 0),
            historical_death_year=int(row["historical_death_year"] or 0),
            historical_death_month=int(row["historical_death_month"] or 0),
            debut_year=int(row["debut_year"] or 0),
            debut_month=int(row["debut_month"] or 0),
            status=str(row["status"] or "active"),
            summary=str(row["summary"] or ""),
            portrait_id=str(row["portrait_id"] or ""),
        )

    def archived_character_payloads(self) -> List[Dict[str, Any]]:
        rows = self.db.conn.execute(
            """
            SELECT name, office, office_type, faction, aliases, personal_skills,
                   loyalty, ability, integrity, courage, style,
                   diplomacy, martial, stewardship, intrigue, learning,
                   birth_year, historical_death_year, historical_death_month,
                   debut_year, debut_month, status, portrait_id, power_id, location,
                   summary
            FROM characters
            WHERE archived=1
            ORDER BY name
            """
        ).fetchall()
        return [self.public_character(self._character_from_db_row(row)) for row in rows]

    def directive_rows(self):
        # 颁诏候选 = draft；UI 列表含 pending
        return self.db.list_directives(self.state, statuses=("pending", "draft"))

    def regions_payload(self) -> List[Dict[str, object]]:
        regions = self.db.region_payload()
        _, _, details = calc_province_fiscal(self.state, self.db)
        by_region = {str(d["region_id"]): d for d in details}
        for region in regions:
            detail = by_region.get(str(region["id"]))
            if not detail:
                continue
            region["tax_actual"] = int(detail["province_total"])
            region["tax_per_turn"] = int(detail["田赋账面"])   # 田赋账面=官民田×亩率，覆盖库里旧列
            region["tax_efficiency"] = float(detail["efficiency"])
            region["tax_breakdown"] = {
                "田赋": int(detail["田赋"]),
                "辽饷": int(detail["辽饷"]),
                "盐税": int(detail["盐税"]),
                "商税": int(detail["商税"]),
                "皇庄": int(detail["皇庄"]),
            }
        return regions

    def map_nodes(self, regions: Optional[List[Dict[str, object]]] = None) -> List[Dict[str, Any]]:
        region_positions = {
            "beizhili": (55.5, 41.2), "nanzhili": (70, 41), "shandong": (56.8, 47.9),
            "shanxi": (48.8, 45.2), "henan": (58, 46), "shaanxi": (51, 38),
            "zhejiang": (73.7, 57.9), "jiangxi": (67, 55), "huguang": (59, 59),
            "sichuan": (57, 52), "fujian": (73.2, 65.1), "guangdong": (62.5, 73.6),
            "guangxi": (53.9, 69.6), "yunnan": (47, 69), "guizhou": (52, 56),
            "liaodong": (61.0, 37.6), "dongjiang_area": (68.9, 43.7),
            "shenyang_liaoyang": (61.3, 39.6), "jianzhou": (64.6, 31.0),
            "korea": (67.0, 44.8), "mongol_chahar": (47.0, 31.0), "nurgan": (58.2, 21.2),
            "outer_mongolia": (43.0, 24.0), "western_regions": (25.0, 40.0),
            "tibet": (31.0, 57.0), "amur_frontier": (70.0, 24.0),
            "japan": (83.0, 49.0), "southwest_frontier": (45.0, 75.0),
            "taiwan": (78, 67),
        }
        theater_positions = {
            "liaodong": (57.76, 42.21), "dongjiang": (63.95, 42.39),
            "xuan_da": (50.49, 40.08), "shanhaiguan": (55.52, 42.84),
        }
        armies = self.db.army_payload(danger_order=True)
        nodes: List[Dict[str, Any]] = []
        for region in regions or self.regions_payload():
            x, y = region_positions.get(str(region["id"]), (50, 50))
            stationed = [a for a in armies if self._army_belongs_to_region(a, region)]
            buildings = self.db.building_payload(str(region["id"]))
            risk = int(region["unrest"]) + int(region["military_pressure"]) + (100 - int(region["public_support"]))
            node_kind = "region" if str(region.get("controlled_by") or "ming") == "ming" else "external"
            nodes.append({"id": region["id"], "kind": node_kind, "x": x, "y": y, "region": region, "armies": stationed, "buildings": buildings, "risk": risk})
        for node_id, (x, y) in theater_positions.items():
            stationed = [a for a in armies if self._army_belongs_to_theater(a, node_id)]
            if stationed:
                nodes.append({"id": node_id, "kind": "theater", "x": x, "y": y, "label": self._theater_label(node_id), "armies": stationed, "risk": 120})
        return nodes

    def _army_belongs_to_region(self, army: Dict[str, Any], region: Dict[str, Any]) -> bool:
        station = str(army["station"])
        region_name = str(region["name"])
        return (
            str(region["id"]) in station
            or region_name in station
            or station in region_name
            or any(part.strip() and part.strip() in station for part in region_name.replace("／", "/").split("/"))
        )

    def _army_belongs_to_theater(self, army: Dict[str, Any], theater_id: str) -> bool:
        text = f"{army['id']} {army['name']} {army['station']} {army['theater']}"
        mapping = {
            "liaodong": ("辽东", "宁锦", "关宁"),
            "dongjiang": ("东江", "皮岛"),
            "xuan_da": ("宣大", "宣府", "大同"),
            "shanhaiguan": ("山海关",),
        }
        return any(word in text for word in mapping.get(theater_id, ()))

    def _theater_label(self, theater_id: str) -> str:
        return {
            "liaodong": "辽东 / 宁锦",
            "dongjiang": "东江镇",
            "xuan_da": "宣大",
            "shanhaiguan": "山海关",
        }[theater_id]

    def closed_this_turn_payloads(self) -> List[Dict[str, Any]]:
        """上回合（resolve 后 state.turn 已 +1）关闭的 issue。"""
        target_turn = max(0, int(self.state.turn) - 1)
        out: List[Dict[str, Any]] = []
        for row in self.db.list_closed_issues_at(target_turn):
            status = str(row["status"])
            effect_key = "effect_on_resolve" if status == "resolved" else "effect_on_fail"
            try:
                effect = json.loads(str(row[effect_key] or "{}"))
            except Exception:
                effect = {}
            out.append({
                "id": int(row["id"]),
                "kind": row["kind"],
                "title": row["title"],
                "status": status,
                "bar_value": int(row["bar_value"]),
                "bar_good_meaning": row["bar_good_meaning"],
                "bar_bad_meaning": row["bar_bad_meaning"],
                "closed_turn": int(row["closed_turn"] or 0),
                "stage_text": row["stage_text"],
                "effect": effect,
            })
        return out

    def issue_payloads(self) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        for row in self.db.list_active_issues():
            payloads.append({
                "id": int(row["id"]),
                "kind": row["kind"],
                "title": row["title"],
                "bar_value": int(row["bar_value"]),
                "bar_good_meaning": row["bar_good_meaning"],
                "bar_bad_meaning": row["bar_bad_meaning"],
                "phase": row["phase"],
                "stage_text": row["stage_text"],
                "severity": int(row["severity"]),
                "tags": list(json.loads(str(row["tags"] or "[]"))),
                "inertia": int(row["inertia"] or 0),
                "resolve_condition": _humanize_condition(row["resolve_condition"] or ""),
                "fail_condition": _humanize_condition(row["fail_condition"] or ""),
                "ongoing_text": _format_issue_ongoing(str(row["ongoing_effects"] or "{}")),
                "effect_on_resolve": dict(json.loads(str(row["effect_on_resolve"] or "{}"))),
                "effect_on_fail": dict(json.loads(str(row["effect_on_fail"] or "{}"))),
                "origin_kind": (row["origin_kind"] if "origin_kind" in row.keys() else "") or "",
                "origin_ref": (row["origin_ref"] if "origin_ref" in row.keys() else "") or "",
                "is_manual": bool(row["is_manual"]) if "is_manual" in row.keys() else False,
                "duration_turns": int(row["duration_turns"] or 0) if "duration_turns" in row.keys() else 0,
                "goal": (row["goal"] if "goal" in row.keys() else "") or "",
                "assignee": (row["assignee"] if "assignee" in row.keys() else "") or "",
                "budget_pool": round(float(row["budget_pool"] or 0), 1) if "budget_pool" in row.keys() else 0.0,
                "budget_source": (row["budget_source"] if "budget_source" in row.keys() else "") or "",
                "death_authority": bool(row["death_authority"]) if "death_authority" in row.keys() else False,
                "origin_turn": int(row["origin_turn"] or 0),
            })
        return payloads

    def legacies_payload(self) -> List[Dict[str, Any]]:
        """现行帝国修正（长期百分比修正符），给状态栏小条用。"""
        out: List[Dict[str, Any]] = []
        opening_clear_text = {
            leg.key: leg.clear_narrative
            for leg in self.content.opening_legacies
            if leg.clear_narrative
        }
        for row in self.db.list_active_legacies(self.state):
            try:
                eff = json.loads(str(row["modifiers"] or "{}"))
            except Exception:
                eff = {}
            try:
                clear_gate = json.loads(str(row["clear_gate"] or "{}"))
            except Exception:
                clear_gate = {}
            remaining_months = self.db.legacy_remaining_months(row, self.state)
            clear_condition = opening_clear_text.get(str(row["legacy_key"] or ""), "")
            if not clear_condition and clear_gate:
                clear_condition = _humanize_legacy_gate(clear_gate, self.content)
            elif clear_condition and clear_gate:
                clear_condition = f"{clear_condition}（{_humanize_legacy_gate(clear_gate, self.content)}）"
            if not clear_condition:
                clear_condition = "无固定消除条件" if remaining_months < 0 else f"再过 {remaining_months} 月自然消退"
            out.append({
                "id": int(row["id"]),
                "name": row["name"],
                "narrative_hint": row["narrative_hint"],
                "modifiers": eff,
                "effect_text": _humanize_legacy_effect(eff, self.content),
                "remaining_months": remaining_months,
                "clear_condition": clear_condition,
            })
        return out

    def budget_payload(self) -> Dict[str, Any]:
        # 唯一定额源：flows.compute_budget_lines（与实际落账 / 大臣 treasury_budget_summary 三处统一）。
        budget = compute_budget_lines(self.db, self.state)
        legacy_mods = self.db.legacy_modifiers(self.state)

        def _annotate_amounts(account_name: str, items: list[Dict[str, Any]]) -> None:
            net_pct = int(legacy_mods.get(account_name, 0) or 0)
            for item in items:
                base_amount = int(item["amount"])
                item["base_amount"] = base_amount
                if item["name"] == "建筑产出":
                    item["amount"] = sum(
                        self.db.apply_legacy_pct(
                            round(int(row["output_amount"]) * max(0, min(100, int(row["condition"]))) / 100),
                            net_pct,
                        )
                        for row in self.db.conn.execute(
                            "SELECT condition, output_amount FROM buildings WHERE output_metric = ?",
                            (account_name,),
                        ).fetchall()
                    )
                else:
                    item["amount"] = self.db.apply_legacy_pct(base_amount, net_pct)

        budget["国库"]["balance"] = int(self.state.metrics["国库"])
        budget["内库"]["balance"] = int(self.state.metrics["内库"])
        for account_name, account in budget.items():
            _annotate_amounts(str(account_name), account["income"])
            net_pct = int(legacy_mods.get(str(account_name), 0) or 0)
            for item in account["expense"]:
                base_amount = int(item["amount"])
                item["base_amount"] = base_amount
                if item["name"] == "各军军饷":
                    item["amount"] = sum(
                        abs(self.db.apply_legacy_pct(-int(row["maintenance_per_turn"]), net_pct))
                        for row in self.db.conn.execute(
                            "SELECT maintenance_per_turn FROM armies WHERE owner_power='ming' AND maintenance_per_turn>0"
                        ).fetchall()
                    )
                elif item["name"] == "建筑维护":
                    item["amount"] = sum(
                        abs(self.db.apply_legacy_pct(-int(row["maintenance"]), net_pct))
                        for row in self.db.conn.execute(
                            """
                            SELECT maintenance FROM buildings
                            WHERE maintenance > 0
                              AND CASE WHEN category='内廷' THEN '内库' ELSE '国库' END = ?
                            """,
                            (str(account_name),),
                        ).fetchall()
                    )
                else:
                    item["amount"] = abs(self.db.apply_legacy_pct(-base_amount, net_pct))
            income_total = sum(int(item["amount"]) for item in account["income"])
            expense_total = sum(int(item["amount"]) for item in account["expense"])
            base_income_total = sum(int(item["base_amount"]) for item in account["income"])
            base_expense_total = sum(int(item["base_amount"]) for item in account["expense"])
            account["income_total"] = income_total
            account["expense_total"] = expense_total
            account["net"] = income_total - expense_total
            account["base_income_total"] = base_income_total
            account["base_expense_total"] = base_expense_total
            account["base_net"] = base_income_total - base_expense_total
            account["modifier_pct"] = int(legacy_mods.get(str(account_name), 0) or 0)
            # 各军军饷预估实发：固定收支只是「应发」预算，真账（flows.apply_fixed_period_flows）
            # 受国库余额约束——逐军按 ARMY_SALARY_PRIORITY 发 min(应发, max(0,国库剩余))，国库见底
            # 后续军队全欠。这里复刻同一约束，给军饷行标上「预计实发」，免得 HUD 把账面应发当实扣。
            salary_item = next(
                (it for it in account["expense"] if it["name"] == "各军军饷"), None
            )
            if salary_item is not None:
                net_pct = int(legacy_mods.get(str(account_name), 0) or 0)
                # 发饷时国库可用 = 余额 + 本月固定净收入（发饷在固定收支落账之后）
                available = max(
                    0,
                    int(account["balance"]) + income_total - (expense_total - int(salary_item["amount"])),
                )
                # 口径与应发行（maintenance_per_turn>0，无 active 过滤）一致，
                # 也与真账 flows.apply_fixed_period_flows 的逐军循环一致。
                army_map = {
                    str(r["id"]): abs(self.db.apply_legacy_pct(-int(r["maintenance_per_turn"]), net_pct))
                    for r in self.db.conn.execute(
                        "SELECT id, maintenance_per_turn FROM armies "
                        "WHERE owner_power='ming' AND maintenance_per_turn>0"
                    ).fetchall()
                }
                ordered_ids = [k for k in ARMY_SALARY_PRIORITY if k in army_map]
                ordered_ids += [k for k in army_map if k not in ARMY_SALARY_PRIORITY]
                paid_estimate = 0
                for aid in ordered_ids:
                    pay = min(army_map[aid], available)
                    paid_estimate += pay
                    available -= pay
                salary_item["paid_estimate"] = int(paid_estimate)
        # 本月入账（上月末结算）：上月末 LLM 推演 + 固定财政 tick 落的 ledger
        # 时序上 state.turn 在结算末尾 +1 进入新月，所以"本月可见的入账"是 cur_turn - 1 的 ledger。
        # 语义对齐玩家直觉："上月末抄家/清丈的钱，算这个月的收入"。
        # 过滤掉固定收支（已在上方"固定收入/固定支出"展示），只列一次性流水
        # （清丈追缴、抄家、赈济临支、亏空压力等 LLM 推演产物）。
        FIXED_CATEGORIES = {
            # 国库固定（category 以 ledger 实际写入值为准）
            "田赋辽饷盐商", "田赋", "辽饷", "盐税", "商税",
            "各军军饷", "宗室禄米", "百官俸禄", "工部", "赈灾备用",
            # 内库固定
            "皇庄", "织造", "矿税",
            "宫廷开支", "内廷俸禄", "妃嫔供奉",
            # 建筑（每月固定 tick）
            "建筑产出", "建筑维护",
            # 开局初始账册
            "期初",
        }
        cur_turn = int(self.state.turn)
        rows = self.db.conn.execute(
            "SELECT id, account, delta, balance_after, category, reason "
            "FROM economy_ledger WHERE turn = ? ORDER BY id",
            (cur_turn - 1,),
        ).fetchall()
        for name, account in budget.items():
            movements = [
                {
                    "delta": int(r["delta"]),
                    "balance_after": int(r["balance_after"]),
                    "category": str(r["category"] or ""),
                    "reason": str(r["reason"] or ""),
                }
                for r in rows
                if str(r["account"]) == name
                and str(r["category"] or "") not in FIXED_CATEGORIES
            ]
            account["movements"] = movements
            account["movements_total"] = sum(m["delta"] for m in movements)
        return budget

    def ending_payload(self) -> Optional[Dict[str, Any]]:
        """结局已触发时返回 {status,label,summary,timeline}，否则 None。"""
        if not self.state.ended:
            return None
        from ming_sim.context import ENDING_LABELS
        row = self.db.get_ending_summary() or {}
        return {
            "status": self.state.ending_status,
            "label": ENDING_LABELS.get(self.state.ending_status, "结局"),
            "summary": row.get("summary", ""),
            "timeline": row.get("timeline", []),
        }

    def _armies_with_arms(self) -> list:
        """军队盘面 + 各军持有武器明细（army_arms），供军队抽屉展示。"""
        armies = self.db.army_payload()
        for a in armies:
            a["arms"] = self.db.army_arms_payload(str(a["id"]))
        return armies

    def state_payload(self) -> Dict[str, Any]:
        directives = [self.directive_payload(row) for row in self.directive_rows()]
        regions = self.regions_payload()
        return {
            "turn": {"year": self.state.year, "period": self.state.period,
                     "turn": self.state.turn, "phase": self.state.turn_phase},
            "metrics": self.state.metrics,
            "previous_summary": self.previous_summary,
            "treasury": self.db.treasury_report(self.state),
            "issues": self.issue_payloads(),
            "max_decree_issues": int(load_runtime_game().get("max_decree_issues", 10)),
            "issue_log_limit": int(load_runtime_game().get("issue_log_limit", 6)),
            "legacies": self.legacies_payload(),
            "closed_this_turn": self.closed_this_turn_payloads(),
            "budget": self.budget_payload(),
            "region_warning": self.db.region_report(limit=5),
            "army_warning": self.db.army_report(limit=5),
            "power_warning": self.db.power_report(exclude_self=True),
            "powers": self.db.power_payload(),
            "victory_status": self.session.victory(),
            "ending": self.ending_payload(),
            "events": [],
            "regions": regions,
            "armies": self._armies_with_arms(),
            "arms_stock": self.db.arms_stock_payload(),
            "departments": self.db.department_payload(),
            "technologies": self.db.technology_payload(),
            "preset_trees": _preset_tree_payload(self),
            # 兵种单价表 {兵种名: per_kilo}，前端兵种月饷据此算，消除前端硬编码表（单一来源 troop_cost.json）。
            "troop_rates": {
                str(t.get("tier") or ""): float(t.get("per_kilo") or 0.0)
                for t in (self.content.troop_cost.get("tiers") or [])
            },
            "map_nodes": self.map_nodes(regions),
            "ministers": [
                self.public_character(c)
                for c in self.content.characters.values()
                if c.office_type != "后宫" and self.character_power_id(c) == "ming"
            ],
            "archived_ministers": [
                c for c in self.archived_character_payloads()
                if c.get("office_type") != "后宫" and (c.get("power_id") or "ming") == "ming"
            ],
            "consorts": [
                self.public_character(c)
                for c in self.content.characters.values()
                if c.office_type == "后宫" and c.status == "active" and self.character_power_id(c) == "ming"
            ],
            "directives": directives,
            "structured_directives": self.session.list_structured_directives(),
            "pending_count": self.session.pending_count(),
            "pending_decisions": (
                self.session.pending_decisions()
                if self.state.turn_phase == "awaiting_decision" else []
            ),
            "last_decree": self.last_decree,
            "last_report": self.last_report,
        }

    # ── 聊天 ──────────────────────────────────────────────────────────────
    def _persistent_chat_minister(self, minister_name: str) -> bool:
        return minister_name not in self.session.temporary_characters

    def _chat_payload(
        self,
        minister_name: str,
        answer: str,
        user_text: str = "",
        court_action: str = "",
        next_minister: str = "",
        proposed_directive: Optional[Dict[str, Any]] = None,
        appointed_minister: str = "",
        registered_minister: str = "",
        displaced_minister: str = "",
        secret_order_id: int = 0,
        tax_issue_id: int = 0,
        tax_adjusted: str = "",
    ) -> Dict[str, Any]:
        character = self.session._character(minister_name)
        # 召对答完整轮一起落库（user+minister）+ 进内存缓存。中途退出走不到这里 → 不落库。
        # chat_messages 现在只供前端展示 / 页面刷新恢复 / 撤回比对，不再喂 LLM——喂 LLM 的历史
        # 全走 agno 每月一个 session 自管（含 tool 痕迹）。
        if user_text:
            self.chat_history.setdefault(minister_name, []).append({"role": "user", "content": user_text})
        self.chat_history.setdefault(minister_name, []).append({"role": "minister", "content": answer})
        if minister_name not in self.session.temporary_characters:
            if user_text:
                self.db.append_chat_message(minister_name, self.state.turn, "user", user_text)
            self.db.append_chat_message(minister_name, self.state.turn, "minister", answer)
        return {
            "minister": minister_name,
            "answer": answer,
            "history": self.chat_history[minister_name],
            "court_action": court_action,
            "next_minister": next_minister,
            "proposed_directive": proposed_directive,
            "appointed_minister": appointed_minister,
            "registered_minister": registered_minister,
            "displaced_minister": displaced_minister,
            "secret_order_id": secret_order_id or 0,
            "tax_issue_id": tax_issue_id or 0,
            "tax_adjusted": tax_adjusted or "",
            "directives": [self.directive_payload(row) for row in self.directive_rows()],
            "pending_count": self.session.pending_count(),
            "suggestions": self.suggestions_for(character),
        }

    def chat(self, minister_name: str, message: str) -> Dict[str, Any]:
        if minister_name not in self.content.characters and minister_name not in self.session.temporary_characters:
            raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
        text = message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="问话不能为空。")
        # 召对答完才落库（user+minister 一起，见 _chat_payload）；中途退出/异常整轮不落库。
        # 喂 LLM 的历史走 agno 每月一个 session 自管（含 tool 痕迹）；chat_messages 仅供前端展示。
        result = self.session.chat(minister_name, text)
        proposed = None
        if result.proposed_directive is not None:
            d = result.proposed_directive
            proposed = {"id": d.id, "text": d.text, "status": d.status, "notes": d.notes}
        return self._chat_payload(
            minister_name, result.answer, user_text=text,
            court_action=result.court_action, next_minister=result.next_minister,
            proposed_directive=proposed, appointed_minister=result.appointed_minister,
            registered_minister=result.registered_minister,
            displaced_minister=result.displaced_minister,
            secret_order_id=result.secret_order_id,
            tax_issue_id=result.tax_issue_id,
            tax_adjusted=result.tax_adjusted,
        )

    def chat_stream(self, minister_name: str, message: str) -> Iterator[Dict[str, Any]]:
        if minister_name not in self.content.characters and minister_name not in self.session.temporary_characters:
            yield {"type": "error", "message": f"未找到大臣：{minister_name}"}
            return
        text = message.strip()
        if not text:
            yield {"type": "error", "message": "问话不能为空。"}
            return
        # 召对答完才落库（_chat_payload 整轮一起写）；流式中途退出＝前端中断，整轮不落库。
        character = self.session._character(minister_name)
        chunks: List[str] = []
        try:
            agent = self.session.registry.get(character)
            # 喂 LLM 的历史走 agno 每月一个 session 自管（含 tool 痕迹），不再前置自管历史。
            # 本回合已核定草案是跨 agent 信息（agno 单 session 给不了），每轮前置进 user message
            # 头，与 GameSession.chat 保持一致，确保大臣看得到兄弟大臣最新动作。
            run_input: Any = text
            draft_line = self.session.registry.build_draft_line()
            if draft_line and draft_line != "无":
                run_input = f"【本{TURN_UNIT}已核定草案】{draft_line}\n\n{text}"
            run_output = None
            stream = agent.run(
                run_input, stream=True, stream_events=True, yield_run_output=True,
            )
            for event in stream:
                content = getattr(event, "content", None)
                event_name = getattr(event, "event", "")
                if event_name == "RunContent" and content:
                    delta = str(content)
                    chunks.append(delta)
                    yield {"type": "delta", "content": delta}
                if type(event).__name__ in ("RunOutput", "RunCompletedEvent"):
                    run_output = event
            # 流式跑完补 dump：流式 run_output(RunCompletedEvent)常无 .messages，
            # 传 agent= 让 _dump_llm_messages 走 agent.get_last_run_output() fallback 取 system/user。
            _dump_llm_messages(run_output, f"大臣对话/{minister_name}", agent=agent)
            answer = "".join(chunks).strip()
            fail_if_llm_error(answer, "LLM 调用")
            if not answer and run_output is not None:
                answer = extract_agent_text(run_output)
            if not answer:
                raise LLMUnavailable("LLM 调用失败：流式回复为空。")
            # 截 propose_directive：入 pending；截 propose_appointment：吏部铨选建档
            proposed = None
            appointed = ""
            registered = ""
            court_action = ""
            next_minister = ""
            displaced = ""
            secret_order_id = 0
            tax_issue_id = 0
            tax_adjusted = ""
            if run_output is not None:
                for tool_exec in getattr(run_output, "tools", None) or []:
                    res = str(getattr(tool_exec, "result", "") or "")
                    tool_name = getattr(tool_exec, "tool_name", "")
                    if tool_name == "propose_directive" or res.startswith("__pending_directive__"):
                        draft_text = res.removeprefix("__pending_directive__").strip()
                        if not draft_text:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            draft_text = (args.get("decree_text") or "").strip()
                        if draft_text:
                            did = self.db.add_directive(
                                self.state, None, draft_text, "大臣拟旨",
                                notes=f"由{character.name}拟旨入档", status="pending",
                            )
                            proposed = {"id": did, "text": draft_text, "status": "pending",
                                        "notes": f"由{character.name}拟旨入档"}
                    elif tool_name == "propose_appointment" or res.startswith("__pending_appointment__"):
                        payload_json = res.removeprefix("__pending_appointment__").strip()
                        if not payload_json:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            payload_json = json.dumps(args, ensure_ascii=False)
                        appointed, displaced = self.session._apply_appointment(payload_json, character)
                    elif tool_name == "register_unlisted_person" or res.startswith("__pending_unlisted_person__"):
                        payload_json = res.removeprefix("__pending_unlisted_person__").strip()
                        if not payload_json:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            payload_json = json.dumps(args, ensure_ascii=False)
                        registered, summon_after = self.session._apply_unlisted_person_registration(payload_json)
                        if registered and summon_after:
                            court_action = "summon"
                            next_minister = registered
                    elif tool_name == "summon_minister" or res.startswith("__summon__"):
                        target_name = res.removeprefix("__summon__").strip()
                        if not target_name:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            target_name = args.get("name", "")
                        if target_name:
                            try:
                                target, _is_temporary = self.session.summon_character(
                                    target_name, character, allow_temporary=False
                                )
                            except ValueError:
                                target = None
                            if target is not None:
                                ok, _reason = self.session.can_summon(target)
                                if ok:
                                    court_action = "summon"
                                    next_minister = target.name
                    elif tool_name == "dismiss_minister" or res == "__dismiss__":
                        court_action = "dismiss"
                    elif tool_name == "issue_secret_order" or res.startswith("__secret_order_registered__") or res.startswith("__secret_order__"):
                        if res.startswith("__secret_order_registered__"):
                            try:
                                secret_order_id = int(res.split("__")[3])
                            except Exception:
                                secret_order_id = 0
                        else:
                            payload_json = res.removeprefix("__secret_order__").strip()
                            if not payload_json:
                                args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                                payload_json = json.dumps(args, ensure_ascii=False)
                            secret_order_id = self.session._apply_secret_order(payload_json, minister_name)
                    elif tool_name == "adjust_tax" or res.startswith("__adjust_tax__"):
                        payload_json = res.removeprefix("__adjust_tax__").strip()
                        if not payload_json:
                            args = getattr(tool_exec, "arguments", {}) or getattr(tool_exec, "tool_args", {}) or {}
                            payload_json = json.dumps(args, ensure_ascii=False)
                        tax_issue_id, tax_adjusted = self.session._apply_tax_adjust_issue(payload_json, character)
                    # 密令结案不再走大臣工具，由月末推演 + extractor 写入
            payload = self._chat_payload(
                minister_name, answer, user_text=text,
                court_action=court_action, next_minister=next_minister,
                proposed_directive=proposed, appointed_minister=appointed,
                registered_minister=registered,
                displaced_minister=displaced,
                secret_order_id=secret_order_id,
                tax_issue_id=tax_issue_id,
                tax_adjusted=tax_adjusted,
            )
            yield {"type": "done", "payload": payload}
        except Exception as error:
            if isinstance(error, LLMUnavailable):
                yield {"type": "error", "detail": _llm_error_detail(error)}
            else:
                yield {"type": "error", "message": str(error)}

    def undo_last_chat(self, minister_name: str) -> Dict[str, Any]:
        """撤回该大臣本回合最后一轮召对发言：删存档行 + 裁 agno 末轮 + 重载内存缓存。"""
        if minister_name not in self.content.characters and minister_name not in self.session.temporary_characters:
            raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
        if self.state.turn_phase not in ("summoning", "reviewing"):
            raise HTTPException(status_code=409, detail="本回合已进入颁诏结算，不能撤回召对。")
        if not self._persistent_chat_minister(minister_name):
            raise HTTPException(status_code=409, detail="临时召见人物暂不支持撤回。")
        revoked = self.session.revoke_last_chat(minister_name)
        if not revoked:
            raise HTTPException(status_code=404, detail="本回合没有可撤回的召对。")
        # DB 为真相：重载内存缓存（chat_history），避免与已删的存档行不一致。
        self.chat_history = {name: [] for name in self.session.content.characters}
        for name, msgs in self.db.load_all_chat_history().items():
            self.chat_history.setdefault(name, []).extend(msgs)
        character = self.session._character(minister_name)
        return {
            "minister": minister_name,
            "history": self.chat_history.get(minister_name, []),
            "directives": [self.directive_payload(row) for row in self.directive_rows()],
            "pending_count": self.session.pending_count(),
            "suggestions": self.suggestions_for(character),
        }

    def court_chat_history_payload(self) -> Dict[str, Any]:
        history = self.db.load_court_chat_history(self.state.turn)
        return {
            "turn": self.state.turn,
            "year": self.state.year,
            "period": self.state.period,
            "history": history,
        }

    def _court_chat_agent(
        self,
        simulator_payload: Dict[str, object],
        tools: Optional[List[Any]] = None,
        tool_call_limit: int = 20,
    ) -> Agent:
        # Keep the first two instruction blocks byte-identical with the season simulator
        # prefix so provider-side prefix caching can reuse the full board context.
        simulator_context = build_simulator_context(simulator_payload)
        return Agent(
            name="朝会群臣",
            id="court-chat",
            session_id=f"court-chat-turn-{self.state.turn}",
            db=self.session.agno_db,
            model=create_chat_model(
                self.session.llm_config,
                temperature=0.65,
                top_p=0.9,
                max_tokens=max(1800, self.session.llm_config.max_tokens),
            ),
            instructions=[
                self.content.game_world_prompt,
                simulator_context,
                self.content.court_chat_agent_prompt,
            ],
            tools=tools or [],
            tool_call_limit=tool_call_limit,
            # Court chat already injects monthly history explicitly in the user
            # payload. Letting Agno add session history again makes the conclusion
            # run resend the full debate that we also pass in debate_lines.
            add_history_to_context=False,
            markdown=False,
        )

    def _court_simulator_payload(self) -> Dict[str, object]:
        return build_simulator_payload(
            self.state,
            self.db,
            decree_text="",
            previous_narrative=self.previous_summary or "",
            relevant_memories=self.db.list_chapter_memories(upto_turn=self.state.turn, recent=6),
            secret_orders=[
                self.db.secret_order_sim_payload(o)
                for o in (
                    self.db.list_secret_orders(status="active")
                    + self.db.list_secret_orders(status="pending_review")
                )[:20]
            ],
        )

    def _court_chat_payload(self, text: str, roster: List[Character], history: List[Dict[str, str]]) -> str:
        roster_lines = [
            "- " + character_context_with_db(c, self.db)
            for c in roster
        ]

        def clean_history_content(value: object) -> str:
            content = str(value or "")
            content = re.sub(r"\s*<<<臣:([^>\n]+)>>+\s*", r"\n\1：", content)
            return content.strip()

        history_lines = [
            f"{m.get('speaker', '')}：{clean_history_content(m.get('content', ''))}"
            for m in history[-16:]
        ]
        payload = {
            "note": "完整游戏盘面在 system 的 simulator_payload 前缀中；本 user payload 只补朝会差异信息。",
            "court_chat_history": history_lines or ["无"],
            "factions_brief": self.db.faction_report(),
            "present_ministers": roster_lines,
            "emperor_message": text,
            "instruction": "请按系统规定的 <<<臣:大臣姓名>>> 分隔符协议，组织多位在场大臣依次奏对。",
        }
        return "【朝会群聊输入】\n" + json.dumps(payload, ensure_ascii=False, indent=2)

    def _parse_court_conclusion(self, raw: str) -> Dict[str, Any]:
        decision_match = re.search(r"<<DECISION>>\s*(\{.*?\})\s*<<END>>", raw or "", re.DOTALL)
        conclusion = re.sub(r"<<DECISION>>.*?<<END>>", "", raw or "", flags=re.DOTALL).strip()
        option_lines: List[str] = []
        if decision_match:
            try:
                decision_obj = json.loads(decision_match.group(1))
            except json.JSONDecodeError:
                decision_obj = None
            if isinstance(decision_obj, dict):
                for item in decision_obj.get("options") or []:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("label") or "").strip()
                    if label:
                        option_lines.append(label)
        return {"content": conclusion, "options": option_lines[:3]}

    def _court_conclusion_prompt(
        self,
        debate_lines: str,
        *,
        configured_rounds: Optional[int] = None,
        actual_speech_count: Optional[int] = None,
        actual_speakers: Optional[List[str]] = None,
        visible_only: bool = False,
    ) -> str:
        source_label = "玩家屏幕上已经显示出来的廷辩" if visible_only else "刚才整场廷辩"
        guard = (
            "严禁引用、推断或补充任何未列出的后续发言、后台历史、月度朝会历史或模型记忆；"
            "不要让任何大臣继续奏对，不要使用 <<<臣:姓名>>> 分隔符。\n"
            if visible_only else ""
        )
        body_fields = (
            "1 已出现的主张；2 已出现的分歧；3 已出现的可行章程；4 仍需皇帝裁断之处。"
            "若材料不足，就明确说材料尚不足，只归纳已见内容。"
            if visible_only else
            "1 今日主张；2 责任归属；3 阻力/风险；4 下一步可下旨的章程，"
            "语气像司礼监/内阁把廷议结果呈给皇帝；正文不要分隔符、不要 JSON、不要旁白。"
        )
        option_rule = (
            "options 给 1-2 个『继续追问/暂缓裁断/命某部补报』这类可执行方案。"
            if visible_only else
            "options 须 2-3 项且彼此互斥（选了一项即否定他项的路子），"
        )
        meta = ""
        if configured_rounds is not None and actual_speech_count is not None and actual_speakers is not None:
            meta = (
                f"设置轮数：{configured_rounds}；实际发言段数：{actual_speech_count}；"
                f"实际发言人：{'、'.join(actual_speakers)}。\n"
            )
        return (
            f"【朝会结论】现在必须根据{source_label}给皇帝一个明确结论。"
            f"{guard}"
            f"先输出 120-220 字结论正文：须含 {body_fields}\n"
            "正文之后，另起一行输出一个 <<DECISION>>{...}<<END>> JSON 块，"
            "给皇帝 2-3 个可直接拍板的完整方案（不是结论摘要，也不是只言片语）：\n"
            '{"title":"≤12字本场抉择名","context":"为何此刻必须皇帝亲断（30-60字）",'
            '"options":[{"label":"完整可下旨的方案，须含谁来办、办什么、用什么资源/章程，能直接当圣旨正文用","hint":"此案倾向性后果，不写具体数值"}]}\n'
            f"{option_rule}"
            "label 必须是一句完整可执行的旨意（如「命户部即拨银十万两解陕赈灾，由毕自严督办，三月内回奏」），"
            "不得是半句话、不得是「一、命...」这类编号片段、不得需要拼接前文才能懂。\n"
            "若廷辩已有共识，options 给该方案及其一两个变体（轻重/缓急/经办人不同）；"
            "若仍冲突，options 须覆盖各派真实主张，不得各打五十大板含糊带过。\n"
            f"{meta}"
            f"{'屏幕可见发言' if visible_only else '刚才廷辩'}：\n{debate_lines}"
        )

    def _run_court_conclusion(self, agent: Agent, prompt: str, runner: Any) -> Dict[str, Any]:
        fallback = (
            "本轮朝议虽未尽合，然争点已明：请陛下择一条主线定旨，并限期各部回奏。"
            '\n<<DECISION>>{"title":"朝议未决","context":"群臣争执未休，需陛下亲断方向。",'
            '"options":[{"label":"命内阁会同各部三日内拟出统一章程具奏，逾期问责","hint":"稳妥但费时，恐误事机"},'
            '{"label":"陛下当殿点将拍板，责成一人专办，馀者协办","hint":"决断果敢，然恐压服不服，留下后患"}]}<<END>>'
        )
        conclusion_raw = runner(prompt) or fallback
        parsed = self._parse_court_conclusion(conclusion_raw)
        if not parsed["content"]:
            parsed["content"] = "屏幕可见发言尚不足以形成完整朝议结论。"
        return {
            "type": "conclusion",
            "role": "conclusion",
            "speaker": "朝议结论",
            "content": parsed["content"],
            "options": parsed["options"],
        }

    def _active_court_ministers(self, requested: List[str]) -> List[Character]:
        selected: List[Character] = []
        seen: set[str] = set()
        source_names = requested or [
            c.name for c in self.content.characters.values()
            if c.office_type != "后宫" and self.character_power_id(c) == "ming"
        ]
        for name in source_names:
            c = self.content.characters.get(name)
            if c is None or c.name in seen:
                continue
            if self.character_power_id(c) != "ming":
                continue
            status, _reason = self.db.get_character_status(c.name)
            if status != "active":
                continue
            if not (c.office or "").strip():
                continue
            selected.append(c)
            seen.add(c.name)
        return selected

    def court_chat_stream(self, message: str, ministers: List[str]) -> Iterator[Dict[str, Any]]:
        text = message.strip()
        if not text:
            yield {"type": "error", "message": "朝会发言不能为空。"}
            return
        roster = self._active_court_ministers(ministers)
        if not roster:
            yield {"type": "error", "message": "朝堂当前没有可参与朝议的大臣。"}
            return

        history = self.db.load_court_chat_history(self.state.turn)
        self.db.append_court_chat_message(self.state.turn, "emperor", "皇帝", text)
        try:
            simulator_payload = self._court_simulator_payload()
            prompt = self._court_chat_payload(text, roster, history)
            agent = self._court_chat_agent(simulator_payload)
            allowed = {c.name for c in roster}
            fallback_speaker = roster[0].name
            replies: List[Dict[str, str]] = []
            emitted_speakers: List[str] = []
            current_role = "minister"
            current_speaker = ""
            current_content: List[str] = []
            all_chunks: List[str] = []
            delimiter_re = re.compile(r"<<<(臣|帝):([^>\n]+)>>+")
            pending_text = ""
            court_chat_delta_delay = max(0.0, float(os.environ.get("MING_COURT_CHAT_DELTA_DELAY", "0.09") or "0"))

            def clean_court_chat_fragment(value: str) -> str:
                text_value = re.sub(r"\s*<<<臣:[^>\n]+>>+\s*", "", str(value or ""))
                text_value = re.sub(r"\s*<<<帝:[^>\n]+>>+\s*", "", text_value)
                text_value = re.sub(r"^\s*>+\s*", "", text_value)
                text_value = re.sub(r"\s*>+\s*$", "", text_value)
                return text_value

            def flush_text(text_part: str) -> Iterator[Dict[str, Any]]:
                nonlocal current_role, current_speaker, current_content
                text_part = clean_court_chat_fragment(text_part)
                if not text_part:
                    return
                if not current_speaker:
                    current_role = "minister"
                    current_speaker = fallback_speaker
                    yield {"type": "speaker", "role": current_role, "speaker": current_speaker}
                chunk_size = 2
                for start in range(0, len(text_part), chunk_size):
                    chunk = text_part[start:start + chunk_size]
                    if not chunk:
                        continue
                    current_content.append(chunk)
                    yield {"type": "delta", "role": current_role, "speaker": current_speaker, "content": chunk}
                    if court_chat_delta_delay:
                        time.sleep(court_chat_delta_delay)

            def finish_current() -> None:
                nonlocal current_role, current_speaker, current_content
                content = clean_court_chat_fragment("".join(current_content)).strip()
                if current_speaker and content:
                    replies.append({"role": current_role, "speaker": current_speaker, "content": content})
                current_role = "minister"
                current_speaker = ""
                current_content = []

            def emit_pending(force: bool = False) -> Iterator[Dict[str, Any]]:
                """Parse minister delimiters incrementally without leaking delimiters as speech."""
                nonlocal current_role, current_speaker, current_content, pending_text
                while pending_text:
                    match = delimiter_re.search(pending_text)
                    if match:
                        before = pending_text[:match.start()]
                        if before:
                            for item in flush_text(before):
                                yield item
                        finish_current()
                        role_marker = match.group(1).strip()
                        speaker = match.group(2).strip()
                        current_role = "emperor" if role_marker == "帝" else "minister"
                        current_speaker = "皇帝" if current_role == "emperor" else (speaker if speaker in allowed else fallback_speaker)
                        current_content = []
                        yield {"type": "speaker", "role": current_role, "speaker": current_speaker}
                        pending_text = pending_text[match.end():]
                        continue

                    minister_marker = pending_text.find("<<<臣:")
                    emperor_marker = pending_text.find("<<<帝:")
                    marker_candidates = [idx for idx in (minister_marker, emperor_marker) if idx >= 0]
                    marker_start = min(marker_candidates) if marker_candidates else -1
                    if marker_start >= 0:
                        if marker_start:
                            before = pending_text[:marker_start]
                            pending_text = pending_text[marker_start:]
                            for item in flush_text(before):
                                yield item
                            continue
                        if force:
                            broken = pending_text
                            pending_text = ""
                            for item in flush_text(broken):
                                yield item
                        break

                    keep = 5
                    if force or len(pending_text) <= keep:
                        if force:
                            text_part = pending_text
                            pending_text = ""
                            for item in flush_text(text_part):
                                yield item
                        break
                    emit_part = pending_text[:-keep]
                    pending_text = pending_text[-keep:]
                    for item in flush_text(emit_part):
                        yield item

            run_output = None
            def record_court_stream_metrics(output: Any, tag: str) -> None:
                if output is None:
                    return
                metrics = getattr(output, "metrics", None)
                model_id = getattr(getattr(agent, "model", None), "id", None) or "stream"
                record_stream_metrics(str(model_id), metrics, caller_tag=tag)

            def run_court_prompt(run_prompt: str) -> Iterator[Dict[str, Any]]:
                nonlocal pending_text, run_output
                metrics_recorded = False
                stream = agent.run(run_prompt, stream=True, stream_events=True, yield_run_output=True)
                for event in stream:
                    content = getattr(event, "content", None)
                    event_name = getattr(event, "event", "")
                    if event_name == "RunContent" and content:
                        delta = str(content)
                        all_chunks.append(delta)
                        pending_text += delta
                        for item in emit_pending():
                            yield item
                    if type(event).__name__ in ("RunOutput", "RunCompletedEvent"):
                        run_output = event
                        if not metrics_recorded:
                            record_court_stream_metrics(run_output, "court-chat")
                            metrics_recorded = True

            def run_agent_text(run_prompt: str) -> str:
                chunks: List[str] = []
                text_run_output = None
                metrics_recorded = False
                stream = agent.run(run_prompt, stream=True, stream_events=True, yield_run_output=True)
                for event in stream:
                    content = getattr(event, "content", None)
                    event_name = getattr(event, "event", "")
                    if event_name == "RunContent" and content:
                        chunks.append(str(content))
                    if type(event).__name__ in ("RunOutput", "RunCompletedEvent"):
                        text_run_output = event
                        if not metrics_recorded:
                            record_court_stream_metrics(text_run_output, "court-chat/conclusion")
                            metrics_recorded = True
                return "".join(chunks).strip()

            def run_directed_speech(speaker: str, run_prompt: str) -> Iterator[Dict[str, Any]]:
                nonlocal run_output
                pieces: List[str] = []
                metrics_recorded = False
                emitted_speakers.append(speaker)
                yield {"type": "speaker", "speaker": speaker}
                stream = agent.run(run_prompt, stream=True, stream_events=True, yield_run_output=True)
                for event in stream:
                    content = getattr(event, "content", None)
                    event_name = getattr(event, "event", "")
                    if event_name == "RunContent" and content:
                        delta = re.sub(r"\s*<<<臣:[^>\n]+>>+\s*", "", str(content))
                        if not delta:
                            continue
                        pieces.append(delta)
                        for start in range(0, len(delta), 4):
                            yield {"type": "delta", "speaker": speaker, "content": delta[start:start + 4]}
                    if type(event).__name__ in ("RunOutput", "RunCompletedEvent"):
                        run_output = event
                        if not metrics_recorded:
                            record_court_stream_metrics(run_output, "court-chat")
                            metrics_recorded = True
                content = "".join(pieces).strip()
                if not content and run_output is not None:
                    content = re.sub(r"\s*<<<臣:[^>\n]+>>+\s*", "", extract_agent_text(run_output)).strip()
                    if content:
                        for start in range(0, len(content), 4):
                            yield {"type": "delta", "speaker": speaker, "content": content[start:start + 4]}
                if content:
                    replies.append({"role": "minister", "speaker": speaker, "content": content})

            drama_beats = [
                {
                    "name": "开场立场",
                    "goal": "先亮出此人基于官署、派系和私利最自然的立场；若要得罪人，必须有职责或派系理由。",
                    "move": "可开门见山，也可先避险再暗扣责任；不要为了热闹硬骂。",
                },
                {
                    "name": "立场碰撞",
                    "goal": "回应上一段具体话头。若对方敌派/异衙门/要自己担责，可以明驳；若同派或利益相近，就补台、转移矛头或加条件。",
                    "move": "冲突方式按人物选择：明驳、暗讽、补台夺责、表面赞同实则设限、转锅地方/胥吏/敌派。",
                },
                {
                    "name": "反咬自辩",
                    "goal": "若本段人物被前文压责，就自辩或反咬；若未被压责，就替同派找台阶或把责任推向更安全对象。",
                    "move": "允许承认小错换大局，或用账册、限期、具结把压力转给别人。",
                },
                {
                    "name": "党争加压",
                    "goal": "若人物所属派系与争点有关，把派系利益自然带入；不相关则不要硬扣党争帽子。",
                    "move": "可借清查、台谏、旧案、阉党余波、东林清议、阁部争权来施压，但必须贴合人物立场。",
                },
                {
                    "name": "章程逼宫",
                    "goal": "提出或修正可执行章程，并让章程服务自己的官署/派系利益：谁出银、谁押发、谁查账、几日回奏、失败谁担责。",
                    "move": "高能力者给细章程；低胆略者给模糊章程；低清廉者把查账范围避开自己的利益链。",
                },
                {
                    "name": "最后冲突",
                    "goal": "若仍未一致，基于本人物立场把冲突整理成皇帝可拍板的方案；若已有一致，就顺势收束但保留本派条件。",
                    "move": "可以二择其一，也可以提出折中，但要看人物是否有能力和胆略承担。",
                },
            ]

            game_settings = load_runtime_game()
            configured_rounds = max(1, min(8, int(game_settings.get("court_chat_debate_rounds", 3) or 3)))
            target_min_speeches = max(len(roster), configured_rounds * max(3, min(len(roster), 6)))
            target_max_speeches = max(target_min_speeches + min(len(roster), 6), len(roster) + configured_rounds * 4)
            script_prompt = (
                "【朝会整场剧本】\n"
                f"{prompt}\n"
                f"本局设置为 {configured_rounds} 轮交锋，目标约 {target_min_speeches}-{target_max_speeches} 段。\n"
                f"在场大臣：{'、'.join(c.name for c in roster)}。\n"
                "请靠提示词范例里的默会知识排一整场廷议：谁发难、谁打断、谁回嘴、谁暗伤、谁补台、谁逼皇帝裁断，由你按人物画像和盘面决定。\n"
                "所有被召入大臣都应参与；至少两人要二次发言形成回嘴。若皇帝发言含【皇帝插话，打断当前廷议并扭转话题】，立刻围绕新御问转向。\n"
                "不要替皇帝写台词；不要输出分析或旁白；不要输出朝议结论。只按 <<<臣:姓名>>> 分隔符输出群臣台词。"
            )

            for item in run_court_prompt(script_prompt):
                yield item
            for item in emit_pending(force=True):
                yield item
            finish_current()

            _dump_llm_messages(run_output, "朝会聊天室", agent=agent)
            if not replies:
                yield {"type": "error", "message": "朝会未能形成有效廷辩，请换一组大臣或重试。"}
                return
            missing_speakers = [c.name for c in roster if c.name not in {r["speaker"] for r in replies}]
            if missing_speakers:
                append_prompt = (
                    "【朝会补场】刚才整场剧本漏掉了部分已召入朝会的大臣。"
                    "请只让这些缺席者各补一段，接住前文火候，像临场插话，不要像独立奏疏。"
                    "只按 <<<臣:姓名>>> 输出台词，不要结论或旁白。\n"
                    f"缺席大臣：{'、'.join(missing_speakers)}。\n"
                    f"近期廷辩：\n" + "\n".join(f"{r['speaker']}：{r['content']}" for r in replies[-10:])
                )
                pending_text = ""
                for item in run_court_prompt(append_prompt):
                    yield item
                for item in emit_pending(force=True):
                    yield item
                finish_current()
            for reply in replies:
                self.db.append_court_chat_message(
                    self.state.turn,
                    reply["role"],
                    reply["speaker"],
                    reply["content"],
                )
                yield {"type": "reply", **reply}
            debate_lines = "\n".join(f"{r['speaker']}：{r['content']}" for r in replies[-18:])
            conclusion_prompt = self._court_conclusion_prompt(
                debate_lines,
                configured_rounds=configured_rounds,
                actual_speech_count=len(replies),
                actual_speakers=list(dict.fromkeys(r["speaker"] for r in replies)),
            )
            conclusion_item = self._run_court_conclusion(agent, conclusion_prompt, run_agent_text)
            self.db.append_court_chat_message(self.state.turn, "conclusion", "朝议结论", conclusion_item["content"])
            yield conclusion_item
            yield {"type": "done", "payload": self.court_chat_history_payload()}
            return
        except Exception as error:
            if isinstance(error, LLMUnavailable):
                yield {"type": "error", "detail": _llm_error_detail(error)}
            else:
                yield {"type": "error", "message": str(error)}

    def court_chat_summary(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        visible_lines: List[str] = []
        for message in messages[-40:]:
            role = str(message.get("role") or "")
            speaker = str(message.get("speaker") or "").strip() or ("皇帝" if role == "emperor" else "未知")
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            if role not in {"emperor", "minister", "conclusion"}:
                continue
            visible_lines.append(f"{speaker}：{content}")
        if not visible_lines:
            raise HTTPException(status_code=400, detail="当前屏幕没有可总结的朝会内容。")

        simulator_payload = self._court_simulator_payload()
        agent = self._court_chat_agent(simulator_payload)
        prompt = self._court_conclusion_prompt("\n".join(visible_lines), visible_only=True)

        def run_visible_summary(run_prompt: str) -> str:
            run_output = agent.run(run_prompt)
            _dump_llm_messages(run_output, "朝会可见总结", agent=agent)
            return extract_agent_text(run_output).strip()

        conclusion_item = self._run_court_conclusion(agent, prompt, run_visible_summary)
        self.db.append_court_chat_message(self.state.turn, "conclusion", "朝议结论", conclusion_item["content"])
        return {
            "role": "conclusion",
            "speaker": "朝议结论",
            "content": conclusion_item["content"],
            "options": conclusion_item["options"],
        }

    def suggestions_for(self, character: Character) -> List[Dict[str, str]]:
        suggestions = [
            {"label": "问在办事项", "text": "当前在办的事项里，哪几件轻重缓急最该先理？"},
            {"label": "问阻力", "text": "眼下推进朝政，最大的阻力来自哪一方？"},
            {"label": "拟旨", "text": "拟旨如下：", "prefix": True},
            {"label": "下密令", "text": "密令如下：", "prefix": True},
        ]
        extra_suggestions: List[Dict[str, str]] = []
        runtime_ids = {item["id"] for item in self._runtime_skill_payloads(character)}
        # office 专属快捷话术 chip 走 offices.court_grant_json(DB 唯一真相，seed 自 skills.json)，
        # 固定开头话术硬触发对应 agno skill。加新 office chip 改 JSON 升版本，运行时改直接 UPDATE DB。
        _grant = self.db.get_office_court_grant(character.office_type)
        for chip in (_grant.get("chips") or []):
            extra_suggestions.append(dict(chip))
        if "check_treasury" in runtime_ids:
            extra_suggestions.append({"label": "查钱粮", "text": "太仓和内库实数如何？本月哪些钱最急？"})
        if "query_army_roster" in runtime_ids:
            extra_suggestions.append({"label": "查驻军", "text": "查一下关宁军、京营和陕西边军的士气、欠饷与补给。"})
        if "secret_order" in runtime_ids:
            extra_suggestions.append({"label": "密查", "text": "哪些账册和人物最该先密查？"})
        return suggestions + extra_suggestions


def sse_event(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


web_game: Optional[WebGame] = None  # 懒加载：菜单页点「新游戏/继续/加载存档」才实例化
app = FastAPI(title="Ming Salvage MVP Web")


def get_game() -> WebGame:
    """游戏路由统一入口。未开局 → 409 让前端跳回菜单页。"""
    if web_game is None:
        raise HTTPException(status_code=409, detail="尚未开局，请回菜单选择新游戏/继续/加载存档。")
    return web_game


def _save_visible_for_campaign(fname: str, campaign_id: str) -> bool:
    if not fname.startswith(AUTO_SAVE_PREFIX):
        return True
    campaign_id = (campaign_id or "").strip()
    return bool(campaign_id and fname.startswith(f"{AUTO_SAVE_PREFIX}{campaign_id}_"))


# 自动存档文件名：auto_<campaign_id>_<year>_<period>_t<turn>_<tag>.db
_AUTO_SAVE_RE = re.compile(
    rf"^{re.escape(AUTO_SAVE_PREFIX)}(?P<cid>[0-9a-f]+)_"
    r"(?P<year>\d{4})_(?P<period>\d{2})_t(?P<turn>\d{4})_(?P<tag>\w+)$"
)

_AUTO_TAG_LABEL = {"begin": "月初", "preresolve": "结算前"}


def _parse_save_name(name: str) -> Dict[str, Any]:
    """把存档名解析成元信息。自动档归到对应 campaign，手动档 campaign_id 留空。"""
    m = _AUTO_SAVE_RE.match(name)
    if not m:
        return {"campaign_id": "", "kind": "manual", "label": name}
    year = int(m.group("year"))
    period = int(m.group("period"))
    turn = int(m.group("turn"))
    tag = m.group("tag")
    tag_label = _AUTO_TAG_LABEL.get(tag, tag)
    return {
        "campaign_id": m.group("cid"),
        "kind": "auto",
        "year": year,
        "period": period,
        "turn": turn,
        "tag": tag,
        "label": f"{year}年{period}月 · 第{turn}回合 · {tag_label}",
    }


def _main_db_campaign_id() -> str:
    db_path = os.environ.get("MING_SIM_DB", "") or user_data_path("ming_sim.db")
    if not os.path.isabs(db_path):
        db_path = str(user_data_dir() / db_path)
    if not os.path.isfile(db_path):
        return ""
    try:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT value FROM kv_store WHERE key='campaign_id'").fetchone()
            return str(row[0]).strip() if row and row[0] else ""
        finally:
            conn.close()
    except Exception:
        return ""


def _scan_saves() -> List[Dict[str, Any]]:
    """扫存档目录，独立于 WebGame 实例（菜单页无 game 也要能列）。
    不再按 campaign 过滤——所有局的存档都列出，由前端按局分组。"""
    saves_dir = user_data_path("saves")
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(saves_dir):
        return out
    for fname in sorted(os.listdir(saves_dir)):
        if not fname.endswith(".db"):
            continue
        name = fname[:-3]
        full = os.path.join(saves_dir, fname)
        try:
            st = os.stat(full)
        except OSError:
            continue
        meta = _parse_save_name(name)
        out.append({
            "name": name,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            **meta,
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def _scan_campaigns() -> List[Dict[str, Any]]:
    """把存档按局（campaign_id）分组，当前主 DB 的局标 current=True。
    手动存档（无 campaign_id）归到一个 manual 组。每组按 mtime 倒序，组也按最新档倒序。"""
    saves = _scan_saves()
    cur_campaign = _main_db_campaign_id()
    groups: Dict[str, Dict[str, Any]] = {}
    for s in saves:
        cid = s.get("campaign_id") or ""
        key = cid or "__manual__"
        group = groups.get(key)
        if group is None:
            group = {
                "campaign_id": cid,
                "kind": "manual" if not cid else "auto",
                "current": bool(cid) and cid == cur_campaign,
                "saves": [],
                "latest_mtime": 0,
            }
            groups[key] = group
        group["saves"].append(s)
        group["latest_mtime"] = max(group["latest_mtime"], s["mtime"])
    out = list(groups.values())
    # 当前局置顶，其余按最新档时间倒序；手动组排最后。
    out.sort(key=lambda g: (
        0 if g["current"] else (2 if g["kind"] == "manual" else 1),
        -g["latest_mtime"],
    ))
    return out


def _has_main_db() -> bool:
    """主 DB 文件是否存在 → 决定「继续」按钮可不可点。"""
    db_path = os.environ.get("MING_SIM_DB", "") or user_data_path("ming_sim.db")
    if not os.path.isabs(db_path):
        db_path = str(user_data_dir() / db_path)
    return os.path.isfile(db_path)


# ---- 自定义剧本（scenarios）----

_SCENARIO_FILES = {
    "characters": "characters.json",
    "events": "events.json",
    "seed_events": "seed_events.json",
}


def _scenario_slug(raw: str) -> str:
    """剧本 id 净化：仅留字母/数字/中文/._-，拒绝以 . 开头。空则报错。"""
    cleaned = "".join(c for c in (raw or "").strip() if c.isalnum() or c in "._-")
    if not cleaned or cleaned.startswith("."):
        raise HTTPException(status_code=400, detail="剧本名非法（仅允许字母/数字/汉字/._-）。")
    return cleaned


def _scenario_dir(scenario_id: str) -> str:
    return str(scenario_active.scenarios_root() / scenario_id)


def _scenario_files_present(scenario_dir: str) -> Dict[str, bool]:
    return {
        key: os.path.isfile(os.path.join(scenario_dir, fname))
        for key, fname in _SCENARIO_FILES.items()
    }


def _read_manifest(scenario_id: str) -> Dict[str, Any]:
    scenario_dir = _scenario_dir(scenario_id)
    path = os.path.join(scenario_dir, "manifest.json")
    data: Dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data["id"] = scenario_id
    data.setdefault("name", scenario_id)
    data.setdefault("description", "")
    data.setdefault("source", "manual")
    data["files"] = _scenario_files_present(scenario_dir)  # 以盘上真相为准
    return data


def _write_manifest(scenario_id: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
    scenario_dir = _scenario_dir(scenario_id)
    os.makedirs(scenario_dir, exist_ok=True)
    manifest = dict(manifest)
    manifest["id"] = scenario_id
    manifest["files"] = _scenario_files_present(scenario_dir)
    manifest["updated"] = int(time.time())
    manifest.setdefault("created", manifest["updated"])
    with open(os.path.join(scenario_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    return manifest


def _validate_scenario_dir(scenario_dir: str) -> Optional[str]:
    """把激活目录临时指向 scenario_dir，跑真 loader 校验（与游戏实际加载字节一致）。
    返回中文错误串或 None（通过）。只校验目录里实际存在的文件（部分覆盖）。"""
    with scenario_active.override(scenario_dir):
        try:
            if os.path.isfile(os.path.join(scenario_dir, "characters.json")):
                load_character_content()
            if os.path.isfile(os.path.join(scenario_dir, "events.json")):
                load_event_content("events.json")
            if os.path.isfile(os.path.join(scenario_dir, "seed_events.json")):
                load_event_content("seed_events.json")
        except SystemExit as exc:
            return str(exc)
    return None


def _scan_scenarios() -> List[Dict[str, Any]]:
    root = scenario_active.scenarios_root()
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        if name.startswith("."):  # 跳过 .gen-tmp-* 等临时目录
            continue
        if not os.path.isdir(os.path.join(root, name)):
            continue
        out.append(_read_manifest(name))
    out.sort(key=lambda m: m.get("updated", 0), reverse=True)
    return out


def _active_scenario_manifest() -> Optional[Dict[str, Any]]:
    sid = scenario_active.active_scenario_id()
    if not sid:
        return None
    if not os.path.isdir(_scenario_dir(sid)):
        return None
    return _read_manifest(sid)


@app.get("/api/scenarios")
async def api_scenarios_list() -> Dict[str, Any]:
    return {"scenarios": _scan_scenarios(), "active_id": scenario_active.active_scenario_id()}


def _read_scenario_full(sid: str) -> Dict[str, Any]:
    """读一份剧本的完整内容：{manifest, characters, events, seed_events}（缺则 null）。"""
    scenario_dir = _scenario_dir(sid)
    out: Dict[str, Any] = {"manifest": _read_manifest(sid)}
    for key, fname in _SCENARIO_FILES.items():
        path = os.path.join(scenario_dir, fname)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    out[key] = json.load(fh)
            except (OSError, json.JSONDecodeError):
                out[key] = None
        else:
            out[key] = None
    return out


@app.get("/api/scenarios/{scenario_id}")
async def api_scenario_get(scenario_id: str) -> Dict[str, Any]:
    sid = _scenario_slug(scenario_id)
    if not os.path.isdir(_scenario_dir(sid)):
        raise HTTPException(status_code=404, detail="剧本不存在。")
    return _read_scenario_full(sid)


@app.post("/api/scenarios")
async def api_scenario_create(request: ScenarioCreateRequest) -> Dict[str, Any]:
    name = (request.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="剧本名不能为空。")
    sid = _scenario_slug(f"{name}_{int(time.time())}")
    scenario_dir = _scenario_dir(sid)
    if os.path.exists(scenario_dir):
        raise HTTPException(status_code=409, detail="剧本已存在。")
    os.makedirs(scenario_dir)
    # 来源解析：copy_from 优先；兼容旧 from_default。
    copy_from = (request.copy_from or "").strip()
    if not copy_from and request.from_default:
        copy_from = "__default__"
    if copy_from and copy_from != "blank":
        if copy_from == "__default__":
            src_dir = bundled_path("content")
        else:
            src_dir = _scenario_dir(_scenario_slug(copy_from))
            if not os.path.isdir(src_dir):
                shutil.rmtree(scenario_dir, ignore_errors=True)
                raise HTTPException(status_code=404, detail="复制来源剧本不存在。")
        # 直接复制三件套文件（自包含，不走 fallback）。
        for fname in _SCENARIO_FILES.values():
            src = os.path.join(src_dir, fname)
            if os.path.isfile(src):
                shutil.copyfile(src, os.path.join(scenario_dir, fname))
    manifest = _write_manifest(sid, {
        "name": name,
        "description": (request.description or "").strip(),
        "source": "manual",
    })
    return {"manifest": manifest, "active_id": scenario_active.active_scenario_id()}


@app.put("/api/scenarios/{scenario_id}/manifest")
async def api_scenario_patch_manifest(scenario_id: str, request: ScenarioManifestPatch) -> Dict[str, Any]:
    sid = _scenario_slug(scenario_id)
    if not os.path.isdir(_scenario_dir(sid)):
        raise HTTPException(status_code=404, detail="剧本不存在。")
    manifest = _read_manifest(sid)
    if request.name is not None and request.name.strip():
        manifest["name"] = request.name.strip()
    if request.description is not None:
        manifest["description"] = request.description.strip()
    return {"manifest": _write_manifest(sid, manifest)}


@app.put("/api/scenarios/{scenario_id}/{file_key}")
async def api_scenario_put_file(scenario_id: str, file_key: str, request: ScenarioFilePut) -> Dict[str, Any]:
    sid = _scenario_slug(scenario_id)
    fname = _SCENARIO_FILES.get(file_key)
    if not fname:
        raise HTTPException(status_code=400, detail="文件类型非法（characters/events/seed_events）。")
    scenario_dir = _scenario_dir(sid)
    if not os.path.isdir(scenario_dir):
        raise HTTPException(status_code=404, detail="剧本不存在。")
    # 先写临时文件 + 临时目录校验，过了再落正式文件，避免半写坏档。
    tmp_dir = os.path.join(scenario_dir, f".validate-{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp_dir)
    try:
        with open(os.path.join(tmp_dir, fname), "w", encoding="utf-8") as fh:
            json.dump(request.content, fh, ensure_ascii=False, indent=1)
        err = _validate_scenario_dir(tmp_dir)
        if err:
            raise HTTPException(status_code=422, detail=f"设定校验未过：{err}")
        shutil.move(os.path.join(tmp_dir, fname), os.path.join(scenario_dir, fname))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return {"manifest": _read_manifest(sid)}


@app.delete("/api/scenarios/{scenario_id}")
async def api_scenario_delete(scenario_id: str) -> Dict[str, Any]:
    sid = _scenario_slug(scenario_id)
    scenario_dir = _scenario_dir(sid)
    if not os.path.isdir(scenario_dir):
        raise HTTPException(status_code=404, detail="剧本不存在。")
    if scenario_active.active_scenario_id() == sid:
        scenario_active.set_active_scenario(None)  # 删的是激活剧本 → 清指针
    shutil.rmtree(scenario_dir, ignore_errors=True)
    return {"scenarios": _scan_scenarios(), "active_id": scenario_active.active_scenario_id()}


@app.post("/api/scenarios/{scenario_id}/activate")
async def api_scenario_activate(scenario_id: str) -> Dict[str, Any]:
    sid = _scenario_slug(scenario_id)
    scenario_dir = _scenario_dir(sid)
    if not os.path.isdir(scenario_dir):
        raise HTTPException(status_code=404, detail="剧本不存在。")
    err = _validate_scenario_dir(scenario_dir)
    if err:
        raise HTTPException(status_code=422, detail=f"剧本校验未过，无法激活：{err}")
    scenario_active.set_active_scenario(sid)
    # 仅写指针，不动正在跑的局；下次「开始游戏/继续」时 GameContent.load() 自然拾取。
    return {"active_id": sid, "scenarios": _scan_scenarios()}


@app.post("/api/scenarios/deactivate")
async def api_scenario_deactivate() -> Dict[str, Any]:
    scenario_active.set_active_scenario(None)
    return {"active_id": "", "scenarios": _scan_scenarios()}


@app.post("/api/scenarios/generate")
async def api_scenario_generate(request: ScenarioGenerateRequest) -> Dict[str, Any]:
    """LLM 生成剧本内容（不落盘），返回供前端编辑。分文件逐个生成。"""
    prompt = (request.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="请填写剧本构思。")
    targets = request.files or ["characters", "events", "seed_events"]
    targets = [t for t in targets if t in _SCENARIO_FILES]
    if not targets:
        raise HTTPException(status_code=400, detail="未指定要生成的文件。")
    try:
        llm_config = _build_llm_config_from_runtime()
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))

    # 生成 agent 需要 content 已 bind（菜单态可能尚无 GameSession）。
    from ming_sim import agents as _agents
    from ming_sim.content import GameContent
    _agents.bind_content(GameContent.load())

    def _run() -> Dict[str, Any]:
        result: Dict[str, Any] = {"status": {}}
        for target in targets:
            try:
                content = _agents.generate_scenario_file(llm_config, None, target, prompt)
                result[target] = content
                result["status"][target] = "generated"
            except Exception as exc:  # 单文件失败不拖累其它
                result[target] = None
                result["status"][target] = f"failed: {exc}"
        return result

    result = await asyncio.to_thread(_run)

    # 写临时目录跑校验，把警告连同内容一起回（不自动激活、不落正式盘）。
    tmp_dir = str(scenario_active.scenarios_root() / f".gen-tmp-{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        for target in targets:
            if result.get(target) is None:
                continue
            with open(os.path.join(tmp_dir, _SCENARIO_FILES[target]), "w", encoding="utf-8") as fh:
                json.dump(result[target], fh, ensure_ascii=False)
        result["validation"] = _validate_scenario_dir(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return result


# ---- 剧本对话式编辑（coding-agent 模式）----

def _scenario_editor_agno_db():
    """剧本编辑会话的 agno 历史库，存 scenarios/.scenario-editor.db。
    菜单态也可用、跨重启持久；各剧本按 session_id 隔离，共用一个库文件。"""
    return create_agno_db(str(scenario_active.scenarios_root() / ".scenario-editor.db"))


def _scenario_counts_brief(scenario_dir: str) -> str:
    from ming_sim.scenario_tools import build_scenario_editor_tools
    tools = {t.__name__: t for t in build_scenario_editor_tools(scenario_dir)}
    return tools["list_current"]("summary")


def _scenario_chat_stream(scenario_id: str, message: str) -> Iterator[Dict[str, Any]]:
    """剧本编辑对话：流式 yield delta，终态拼 changes + 回读整份剧本 + 软校验。"""
    sid = _scenario_slug(scenario_id)
    scenario_dir = _scenario_dir(sid)
    if not os.path.isdir(scenario_dir):
        yield {"type": "error", "message": "剧本不存在。"}
        return
    text = (message or "").strip()
    if not text:
        yield {"type": "error", "message": "请输入指令。"}
        return
    try:
        llm_config = _build_llm_config_from_runtime()
    except LLMUnavailable as exc:
        yield {"type": "error", "detail": _llm_error_detail(exc)}
        return

    from ming_sim import agents as _agents
    from ming_sim.content import GameContent
    from ming_sim.scenario_tools import build_scenario_editor_tools, _EDIT_TOOL_NAMES
    _agents.bind_content(GameContent.load())  # 菜单态可能无 GameSession

    agno_db = _scenario_editor_agno_db()
    tools = build_scenario_editor_tools(scenario_dir)
    agent = _agents.create_scenario_editor_agent(llm_config, agno_db, sid, tools)
    run_input = f"【当前剧本概况】{_scenario_counts_brief(scenario_dir)}\n\n{text}"

    chunks: List[str] = []
    run_output = None
    try:
        stream = agent.run(run_input, stream=True, stream_events=True, yield_run_output=True)
        for event in stream:
            content = getattr(event, "content", None)
            if getattr(event, "event", "") == "RunContent" and content:
                delta = str(content)
                chunks.append(delta)
                yield {"type": "delta", "content": delta}
            if type(event).__name__ in ("RunOutput", "RunCompletedEvent"):
                run_output = event
        _dump_llm_messages(run_output, f"剧本编辑/{sid}", agent=agent)
        reply = "".join(chunks).strip()
        fail_if_llm_error(reply, "LLM 调用")
        if not reply and run_output is not None:
            reply = extract_agent_text(run_output)

        changes: List[Dict[str, str]] = []
        if run_output is not None:
            for te in getattr(run_output, "tools", None) or []:
                tname = getattr(te, "tool_name", "")
                if tname in _EDIT_TOOL_NAMES:
                    changes.append({"tool": tname, "result": str(getattr(te, "result", "") or "")})

        yield {"type": "done", "payload": {
            "reply": reply or "（已处理。）",
            "changes": changes,
            "scenario": _read_scenario_full(sid),
            "validation": _validate_scenario_dir(scenario_dir),
        }}
    except LLMUnavailable as exc:
        yield {"type": "error", "detail": _llm_error_detail(exc)}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"剧本编辑失败：{exc}"}


@app.post("/api/scenarios/{scenario_id}/chat/stream")
async def api_scenario_chat_stream(scenario_id: str, request: ChatRequest) -> StreamingResponse:
    async def generate() -> AsyncIterator[str]:
        for item in _scenario_chat_stream(scenario_id, request.message):
            item_type = str(item.get("type", "message"))
            if item_type == "delta":
                yield sse_event("delta", {"content": item.get("content", "")})
            elif item_type == "done":
                yield sse_event("done", item.get("payload", {}))
            elif item_type == "error":
                yield sse_event("error", item.get("detail") or {"message": item.get("message", "未知错误")})
            await asyncio.sleep(0)
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/menu/status")
async def api_menu_status() -> Dict[str, Any]:
    """菜单页状态：API key 是否配好、上次主 DB 是否存在、存档列表。"""
    runtime = load_runtime_llm()
    has_api_key = bool(runtime.get("api_key") or os.environ.get("OPENAI_API_KEY"))
    return {
        "has_api_key": has_api_key,
        "has_running_game": web_game is not None,
        "has_main_db": _has_main_db(),
        "saves": _scan_saves(),
        "campaigns": _scan_campaigns(),
        "current_campaign": _main_db_campaign_id(),
        "game_settings": load_runtime_game(),
        "active_scenario": _active_scenario_manifest(),
        "llm": {
            "base_url": runtime.get("base_url") or os.environ.get("OPENAI_BASE_URL", ""),
            "model": runtime.get("model") or os.environ.get("OPENAI_MODEL", ""),
            "has_api_key": has_api_key,
            "max_tokens": int(runtime.get("max_tokens") or 8000),
            "timeout_seconds": float(runtime.get("timeout_seconds") or os.environ.get("OPENAI_TIMEOUT_SECONDS", "180") or 180),
            "connect_timeout_seconds": float(runtime.get("connect_timeout_seconds") or os.environ.get("OPENAI_CONNECT_TIMEOUT_SECONDS", "60") or 60),
            "read_timeout_seconds": float(runtime.get("read_timeout_seconds") or os.environ.get("OPENAI_READ_TIMEOUT_SECONDS", "120") or 120),
            "thinking_level": runtime.get("thinking_level") or os.environ.get("OPENAI_THINKING_LEVEL", ""),
            "advanced_model": runtime.get("advanced_model") or os.environ.get("OPENAI_ADVANCED_MODEL", ""),
            "advanced_base_url": runtime.get("advanced_base_url") or os.environ.get("OPENAI_ADVANCED_BASE_URL", ""),
            "has_advanced_api_key": bool(runtime.get("advanced_api_key") or os.environ.get("OPENAI_ADVANCED_API_KEY")),
            "advanced_thinking_level": runtime.get("advanced_thinking_level") or os.environ.get("OPENAI_ADVANCED_THINKING_LEVEL", ""),
        },
    }


@app.post("/api/menu/new_game")
async def api_menu_new_game() -> Dict[str, Any]:
    """开始新游戏：清主 DB → 新建 WebGame。"""
    global web_game
    if web_game is not None:
        try:
            web_game.session.close()
        except Exception:
            pass
        web_game = None
    try:
        web_game = WebGame(fresh=True)
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    except SystemExit as exc:
        raise HTTPException(status_code=500, detail=f"当前剧本文件损坏：{exc}，请到自定义剧本修正或停用。")
    return steam_events.with_events(
        {"state": web_game.state_payload()},
        [steam_events.add_stat(steam_events.STAT_RUNS_STARTED)],
    )


@app.post("/api/menu/continue")
async def api_menu_continue() -> Dict[str, Any]:
    """继续：用上次主 DB 启动 WebGame。"""
    global web_game
    if not _has_main_db():
        raise HTTPException(status_code=404, detail="无上次进度可继续，请先新游戏或加载存档。")
    try:
        web_game = WebGame(fresh=False)
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    except SystemExit as exc:
        raise HTTPException(status_code=500, detail=f"当前剧本文件损坏：{exc}，请到自定义剧本修正或停用。")
    return {"state": web_game.state_payload()}


@app.post("/api/menu/load_save/{name}")
async def api_menu_load_save(name: str) -> Dict[str, Any]:
    """从存档启动：先启动空 WebGame（fresh）→ 调 load_save 热替换主 DB。"""
    global web_game
    try:
        web_game = WebGame(fresh=False)  # 先有 session 才能 load_save
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    except SystemExit as exc:
        raise HTTPException(status_code=500, detail=f"当前剧本文件损坏：{exc}，请到自定义剧本修正或停用。")
    web_game.load_save(name)
    return {"state": web_game.state_payload()}


@app.delete("/api/menu/saves/{name}")
async def api_menu_delete_save(name: str) -> Dict[str, Any]:
    """菜单页删存档：不依赖 WebGame 实例，直接删文件系统里的 <name>.db。
    与 WebGame.delete_save 同名校验，返回刷新后的 campaigns。"""
    cleaned = "".join(c for c in name.strip() if c.isalnum() or c in "._-")
    if not cleaned or cleaned.startswith("."):
        raise HTTPException(status_code=400, detail="存档名非法。仅允许字母/数字/._- ")
    target = os.path.join(user_data_path("saves"), f"{cleaned}.db")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="存档不存在。")
    os.remove(target)
    return {"saves": _scan_saves(), "campaigns": _scan_campaigns()}


@app.get("/api/menu/debug/launcher_log")
async def api_menu_debug_launcher_log() -> Dict[str, Any]:
    """主菜单调试：读取 launcher.log 最近内容，便于 .app 双击模式排障。"""
    data_dir = user_data_dir()
    log_path = data_dir / "launcher.log"
    if not log_path.exists():
        return {
            "data_dir": str(data_dir),
            "log_path": str(log_path),
            "exists": False,
            "content": "",
        }
    max_bytes = 120_000
    with open(log_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size > max_bytes:
            f.seek(-max_bytes, os.SEEK_END)
            raw = f.read()
            content = raw.decode("utf-8", errors="replace")
            content = f"（仅显示最近 {max_bytes} 字节，完整日志见文件）\n\n{content}"
        else:
            f.seek(0)
            content = f.read().decode("utf-8", errors="replace")
    return {
        "data_dir": str(data_dir),
        "log_path": str(log_path),
        "exists": True,
        "content": content,
    }


@app.post("/api/menu/debug/open_data_dir")
async def api_menu_debug_open_data_dir() -> Dict[str, Any]:
    """用系统文件管理器打开用户数据/存档目录。"""
    data_dir = user_data_dir()
    try:
        if os.name == "nt":
            os.startfile(str(data_dir))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(data_dir)])
        else:
            subprocess.Popen(["xdg-open", str(data_dir)])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"打开目录失败：{exc}")
    return {"ok": True, "data_dir": str(data_dir)}


@app.post("/api/menu/exit_to_menu")
async def api_menu_exit() -> Dict[str, Any]:
    """退回菜单：关 session 但不删 DB。"""
    global web_game
    if web_game is not None:
        try:
            web_game.session.close()
        except Exception:
            pass
        web_game = None
    return {"ok": True}


@app.post("/api/menu/shutdown")
async def api_menu_shutdown() -> Dict[str, Any]:
    """退出整个游戏：关 session + 终止服务进程。前端收响应后自行关页面。"""
    import os as _os
    import signal as _signal
    import threading as _threading
    global web_game
    if web_game is not None:
        try:
            web_game.session.close()
        except Exception:
            pass
        web_game = None
    # 先返回响应，再异步终止进程。SIGTERM 在 *nix 走优雅退出；
    # Windows 无完整 SIGTERM 语义（pywebview 主线程也不收信号），直接 os._exit 兜底。
    def _kill_later() -> None:
        import sys as _sys
        import time as _time
        _time.sleep(0.3)
        if _sys.platform == "win32":
            _os._exit(0)
        else:
            _os.kill(_os.getpid(), _signal.SIGTERM)
    _threading.Thread(target=_kill_later, daemon=True).start()
    return {"ok": True}


class LlmSetupRequest(BaseModel):
    base_url: str
    model: str
    api_key: str
    max_tokens: int = 8000
    timeout_seconds: float = 180
    connect_timeout_seconds: float = 60
    read_timeout_seconds: float = 120
    thinking_level: str = ""
    advanced_model: str = ""
    advanced_base_url: str = ""
    advanced_api_key: str = ""
    advanced_thinking_level: str = ""


@app.post("/api/menu/llm")
async def api_menu_save_llm(request: LlmSetupRequest) -> Dict[str, Any]:
    """菜单页保存 LLM 配置：先发起轻量聊天校验，通过后才落盘。"""
    base_url = (request.base_url or "").strip()
    model = (request.model or "").strip()
    api_key = (request.api_key or "").strip()
    advanced_model = (request.advanced_model or "").strip()
    adv_base_in = (request.advanced_base_url or "").strip()
    advanced_base_url = normalize_openai_base_url(adv_base_in) if adv_base_in else ""
    advanced_api_key = (request.advanced_api_key or "").strip()
    max_tokens = request.max_tokens if request.max_tokens > 0 else 8000
    timeout_seconds = request.timeout_seconds if request.timeout_seconds > 0 else 180
    connect_timeout_seconds = request.connect_timeout_seconds if request.connect_timeout_seconds > 0 else 60
    read_timeout_seconds = request.read_timeout_seconds if request.read_timeout_seconds > 0 else 120
    thinking_level = normalize_thinking_level(request.thinking_level)
    advanced_thinking_level = normalize_thinking_level(request.advanced_thinking_level)
    if not (base_url and model):
        raise HTTPException(status_code=400, detail="base_url / model 不能为空。")
    if not api_key:
        existing = load_runtime_llm()
        api_key = existing.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key 未配置，请填写。")
    # advanced_api_key 留空：复用已存的（避免覆盖成空）。
    if advanced_model and not advanced_api_key:
        existing = load_runtime_llm()
        advanced_api_key = existing.get("advanced_api_key") or os.environ.get("OPENAI_ADVANCED_API_KEY", "")
    normalized_base_url = normalize_openai_base_url(base_url)
    config = LLMConfig(
        api_key=api_key,
        base_url=normalized_base_url,
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        thinking_level=thinking_level,
        advanced_model=advanced_model,
        advanced_base_url=advanced_base_url,
        advanced_api_key=advanced_api_key,
        advanced_thinking_level=advanced_thinking_level,
    )
    try:
        _verify_llm_configs_or_raise(config)
    except HTTPException:
        raise
    except LLMUnavailable as exc:
        raise HTTPException(status_code=400, detail=_llm_error_detail(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"code": "llm_validation_failed", "message": str(exc)}) from None
    save_runtime_llm(
        normalized_base_url,
        model,
        api_key,
        max_tokens,
        timeout_seconds,
        connect_timeout_seconds,
        read_timeout_seconds,
        thinking_level,
        advanced_model,
        advanced_base_url,
        advanced_api_key,
        advanced_thinking_level,
    )
    return {
        "ok": True,
        "llm": {
            "base_url": normalized_base_url,
            "model": model,
            "has_api_key": True,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
            "connect_timeout_seconds": connect_timeout_seconds,
            "read_timeout_seconds": read_timeout_seconds,
            "thinking_level": thinking_level,
            "advanced_model": advanced_model,
            "advanced_base_url": advanced_base_url,
            "has_advanced_api_key": bool(advanced_api_key),
            "advanced_thinking_level": advanced_thinking_level,
        },
    }


class GameSettingsRequest(BaseModel):
    # HITL 每回合最多决策点数，0-5。0=关闭 HITL 注入。
    hitl_min_decisions: int = 1
    # 朝会聊天室 ReAct 交锋轮数，未形成结论前继续驱动 agent；默认 3。
    court_chat_debate_rounds: int = 3
    # 朝会流式输出速度档位，1-5。默认 3。
    court_chat_stream_speed: int = 3
    # decree 来源 active 局势同时进行上限；默认 10，调高增加推演 token 消耗。
    max_decree_issues: int = 10
    # 每条 active 局势注入推演的最近推进日志条数。0=不带推进日志。
    issue_log_limit: int = 6
    # 单个承办人同时进行中的密令上限；默认 1。
    secret_order_person_limit: int = 1
    # 全朝同时进行中的密令总上限；默认 5。
    secret_order_total_limit: int = 5
    # 本局朝臣人物建档上限；后宫不计入。默认 120，调高会增加名册/推演 token 消耗。
    character_limit: int = 120
    # 大臣 / 推演 / 结算三个核心 agent 的采样参数。
    minister_temperature: float = 0.6
    minister_top_p: float = 0.9
    simulator_temperature: float = 0.5
    simulator_top_p: float = 0.5
    extractor_temperature: float = 0.1
    extractor_top_p: float = 0.1


@app.get("/api/menu/game_settings")
async def api_menu_game_settings() -> Dict[str, Any]:
    """读全局玩法设置。"""
    return {"game_settings": load_runtime_game()}


@app.post("/api/menu/game_settings")
async def api_menu_save_game_settings(request: GameSettingsRequest) -> Dict[str, Any]:
    """保存全局玩法设置（runtime_game.json）。立即对下一回合推演生效。"""
    saved = save_runtime_game(
        request.hitl_min_decisions,
        request.court_chat_debate_rounds,
        request.court_chat_stream_speed,
        request.max_decree_issues,
        request.issue_log_limit,
        request.secret_order_person_limit,
        request.secret_order_total_limit,
        request.character_limit,
        request.minister_temperature,
        request.minister_top_p,
        request.simulator_temperature,
        request.simulator_top_p,
        request.extractor_temperature,
        request.extractor_top_p,
    )
    return {"ok": True, "game_settings": saved}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/game/state")
async def api_state() -> Dict[str, Any]:
    return get_game().state_payload()


@app.get("/api/secret_orders")
async def api_secret_orders(status: str = "") -> Dict[str, Any]:
    """列出密令。status 为空返回全部，否则按 active/done/failed 过滤。"""
    orders = get_game().db.list_secret_orders(status=status or None)
    return {"orders": orders}


@app.delete("/api/secret_orders/{order_id}")
async def api_delete_secret_order(order_id: int) -> Dict[str, Any]:
    """删除一条密令记录（清掉重复/误下的密令）。"""
    ok = get_game().db.delete_secret_order(order_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到密令 #{order_id}")
    print(f"[secret_order/api] 删除密令 id={order_id}")
    return {"deleted": True, "order_id": order_id}


class ManualIssueEntity(BaseModel):
    """走满 100 落成的实体固定字段（玩家在前端按题材填）。kind ∈ building/department/technology。"""
    kind: str = ""               # building / department / technology
    name: str = ""               # 实体名（不填则用局势标题）
    # 建筑用：
    region_id: str = ""          # 所在省份（玩家下拉选）
    category: str = "民生"        # 建筑类别：财政/军事/民生/科技/交通/内廷
    maintenance: int = 1         # 月维护费（万两）
    output_metric: str = ""      # 产出去向（可空）
    output_amount: int = 0       # 产出量
    # 部门用：
    authority_scope: str = ""    # 职权范围
    power: int = 50              # 权力值
    # 科技用：
    effect_summary: str = ""     # 效果摘要
    # 预设池用：kind=department/technology 时可传 key，后端会用预设字段覆盖立项字段。
    preset_key: str = ""


class ManualIssueCreateRequest(BaseModel):
    # 名称（局势标题），必填。
    title: str = ""
    # 目标：皇帝给该局势定的方向意图，喂给推演逐月推进。立项后锁定不可改。
    goal: str = ""
    # 承办人：主责大臣姓名。推演按其职掌、能力、状态自动推进或恶化。
    assignee: str = ""
    # 分类（题材），落 tags；与 ISSUE_THEMES 对齐。
    tags: list[str] = []
    # 走满后落成的实体固定字段（玩家填）。组装成 effect_on_resolve 立项即预埋，走满直接落地。
    entity: Optional[ManualIssueEntity] = None


class ManualIssueUpdateRequest(BaseModel):
    # 注意：goal 立项后锁定，不在此可改。
    title: Optional[str] = None
    assignee: Optional[str] = None


class IssueAssigneeUpdateRequest(BaseModel):
    assignee: str = ""


class IssueAuthorizationRequest(BaseModel):
    budget_add: float = 0      # 本次追加专款万两（累加进 budget_pool）
    budget_source: str = ""    # 专款出库：'国库' / '内库' / ''(不改)
    death_authority: bool = False  # 专断之权（生杀权）开关


def _build_manual_resolve_effect(entity: "ManualIssueEntity | None", title: str) -> dict:
    """把玩家填的实体固定字段组装成 effect_on_resolve（走满 100 时由结算链落实体）。
    建筑→buildings、部门→departments、科技→technologies。entity 空或 kind 不识别则返回 {}。"""
    if entity is None or not str(entity.kind or "").strip():
        return {}
    name = (entity.name or "").strip() or title[:60]
    k = str(entity.kind).strip().lower()
    if k == "building":
        cat = entity.category if entity.category in BUILDING_CATEGORIES else "民生"
        return {"buildings": [{
            "action": "create", "region_id": (entity.region_id or "").strip(),
            "name": name, "category": cat,
            "maintenance": max(0, int(entity.maintenance or 0)),
            "output_metric": (entity.output_metric or "").strip(),
            "output_amount": max(0, int(entity.output_amount or 0)),
        }]}
    if k == "department":
        preset_key = (entity.preset_key or "").strip()
        if preset_key:
            return {"departments": [{"action": "create", "key": preset_key}]}
        return {"departments": [{
            "action": "create", "name": name,
            "authority_scope": (entity.authority_scope or "").strip(),
            "power": max(0, min(100, int(entity.power or 50))),
        }]}
    if k == "technology":
        preset_key = (entity.preset_key or "").strip()
        if preset_key:
            return {"technologies": [{"action": "create", "key": preset_key}]}
        return {"technologies": [{
            "action": "create", "name": name, "category": "科技",
            "effect_summary": (entity.effect_summary or "").strip(),
        }]}
    return {}


def _technology_names_by_preset_key(game: "WebGame") -> dict[str, str]:
    return {key: preset.name for key, preset in game.content.preset_technologies.items()}


def _unlocked_preset_technology_keys(game: "WebGame") -> set[str]:
    names_by_key = _technology_names_by_preset_key(game)
    rows = game.db.conn.execute("SELECT id, name FROM technologies").fetchall()
    unlocked: set[str] = set()
    for row in rows:
        raw_id = str(row["id"] or "")
        if raw_id.startswith("preset_"):
            unlocked.add(raw_id.removeprefix("preset_"))
        nm = str(row["name"] or "")
        for key, name in names_by_key.items():
            if nm == name:
                unlocked.add(key)
    return unlocked


def _unlocked_preset_department_keys(game: "WebGame") -> set[str]:
    rows = game.db.conn.execute("SELECT office_type FROM offices").fetchall()
    office_names = {str(row["office_type"] or "") for row in rows}
    return {
        key
        for key, preset in game.content.preset_departments.items()
        if preset.name in office_names
    }


def _preset_tree_payload(game: "WebGame") -> dict:
    tech_unlocked = _unlocked_preset_technology_keys(game)
    dept_unlocked = _unlocked_preset_department_keys(game)

    def tech_item(key: str, preset: Any) -> dict:
        reqs = list(getattr(preset, "requires", []) or [])
        return {
            "key": key,
            "name": preset.name,
            "category": preset.category,
            "effect_summary": preset.effect_summary,
            "expected_months": preset.expected_months,
            "bar_value": preset.bar_value,
            "requires": reqs,
            "unlocked": key in tech_unlocked,
            "available": all(req in tech_unlocked for req in reqs),
        }

    def dept_item(key: str, preset: Any) -> dict:
        reqs = list(getattr(preset, "requires", []) or [])
        return {
            "key": key,
            "name": preset.name,
            "category": preset.category,
            "effect_summary": preset.effect_summary,
            "authority_scope": preset.authority_scope,
            "power": preset.power,
            "expected_months": preset.expected_months,
            "bar_value": preset.bar_value,
            "requires": reqs,
            "unlocked": key in dept_unlocked,
            "available": all(req in dept_unlocked for req in reqs),
        }

    return {
        "technologies": [tech_item(k, p) for k, p in game.content.preset_technologies.items()],
        "departments": [dept_item(k, p) for k, p in game.content.preset_departments.items()],
    }


def _manual_preset_override(game: "WebGame", entity: "ManualIssueEntity | None") -> dict:
    if entity is None:
        return {}
    kind = str(entity.kind or "").strip().lower()
    key = str(entity.preset_key or "").strip()
    if not key:
        return {}
    if kind == "technology":
        preset = game.content.preset_technologies.get(key)
        if preset is None:
            raise HTTPException(status_code=400, detail=f"未知预设科技：{key}")
        unlocked = _unlocked_preset_technology_keys(game)
        if key in unlocked:
            raise HTTPException(status_code=400, detail=f"科技「{preset.name}」已经研成。")
        missing = [game.content.preset_technologies[r].name for r in preset.requires if r not in unlocked and r in game.content.preset_technologies]
        if missing:
            raise HTTPException(status_code=400, detail=f"前置科技未完成：{'、'.join(missing)}。")
        return {
            "title": preset.name,
            "tags": [preset.theme],
            "bar_value": preset.bar_value,
            "stage_text": preset.stage_text,
            "resolve_condition": preset.resolve_condition,
            "fail_condition": preset.fail_condition,
            "effect_on_resolve": dict(preset.effect_on_resolve),
            "effect_on_fail": dict(preset.effect_on_fail),
            "goal": preset.resolve_condition,
        }
    if kind == "department":
        preset = game.content.preset_departments.get(key)
        if preset is None:
            raise HTTPException(status_code=400, detail=f"未知预设衙门：{key}")
        unlocked = _unlocked_preset_department_keys(game)
        if key in unlocked:
            raise HTTPException(status_code=400, detail=f"衙门「{preset.name}」已经设立。")
        missing = [game.content.preset_departments[r].name for r in preset.requires if r not in unlocked and r in game.content.preset_departments]
        if missing:
            raise HTTPException(status_code=400, detail=f"前置衙门未设立：{'、'.join(missing)}。")
        return {
            "title": preset.name,
            "tags": [preset.theme],
            "bar_value": preset.bar_value,
            "stage_text": preset.stage_text,
            "resolve_condition": preset.resolve_condition,
            "fail_condition": preset.fail_condition,
            "effect_on_resolve": dict(preset.effect_on_resolve),
            "effect_on_fail": dict(preset.effect_on_fail),
            "goal": preset.resolve_condition,
        }
    raise HTTPException(status_code=400, detail="预设 key 仅支持科技或政治衙门。")


@app.post("/api/issues/manual")
async def api_create_manual_issue(request: ManualIssueCreateRequest) -> Dict[str, Any]:
    """皇帝手动新建一条 decree 局势。按题材填的实体固定字段立项即预埋进 effect_on_resolve，
    走满 100 直接落成该实体（建筑/部门/科技），不依赖大模型现填。goal 立项后锁定不可改。"""
    game = get_game()
    preset_override = _manual_preset_override(game, request.entity)
    title = str(preset_override.get("title") or request.title).strip()
    if not title:
        raise HTTPException(status_code=400, detail="名称（标题）不能为空")
    max_n = int(load_runtime_game().get("max_decree_issues", 10))
    cur = game.db.count_active_decree_issues()
    if cur >= max_n:
        raise HTTPException(
            status_code=409,
            detail=f"decree 来源局势已达上限（{max_n} 条），请先撤销部分局势，或在主菜单游戏设置调高上限。当前：{cur} 条。",
        )
    tags = [str(t).strip() for t in (preset_override.get("tags") or request.tags or []) if str(t).strip()]
    resolve_effect = dict(preset_override.get("effect_on_resolve") or _build_manual_resolve_effect(request.entity, title))
    # 建筑预埋校验省份：玩家选的 region_id 必须是大明控制的省（不能建到外部势力地盘）。
    bld = (resolve_effect.get("buildings") or [{}])[0] if resolve_effect.get("buildings") else None
    if bld is not None:
        rid = str(bld.get("region_id") or "").strip()
        row = game.db.conn.execute("SELECT controlled_by FROM regions WHERE id=?", (rid,)).fetchone() if rid else None
        if row is None:
            raise HTTPException(status_code=400, detail=f"请为该建筑选择一个有效省份（当前：{rid or '未选'}）。")
        if str(row["controlled_by"]) != "ming":
            raise HTTPException(status_code=400, detail="只能在大明控制的省份营建，不能建到外部势力地盘。")
    issue_id = game.db.insert_issue(
        game.session.state,
        kind="situation",
        title=title[:60],
        origin_kind="decree",
        origin_ref="manual",
        bar_value=int(preset_override.get("bar_value") or 40),
        stage_text=str(preset_override.get("stage_text") or title)[:160],
        tags=tags,
        cancellable="decree",
        effect_on_resolve=resolve_effect,
        effect_on_fail=dict(preset_override.get("effect_on_fail") or {}),
        resolve_condition=str(preset_override.get("resolve_condition") or ""),
        fail_condition=str(preset_override.get("fail_condition") or ""),
        is_manual=True,
        duration_turns=0,
        goal=str(request.goal or preset_override.get("goal") or "").strip(),
        assignee=str(request.assignee or "").strip(),
    )
    print(f"[issue/api] 手动新建局势 id={issue_id} title={title!r} tags={tags} 预埋effect={resolve_effect}")
    return {"id": issue_id, "title": title, "duration_turns": 0}


@app.patch("/api/issues/manual/{issue_id}")
async def api_update_manual_issue(issue_id: int, request: ManualIssueUpdateRequest) -> Dict[str, Any]:
    """改手动 decree 局势：名称 / 承办人。goal 立项后锁定，不可改。"""
    ok = get_game().db.update_manual_issue(
        issue_id, title=request.title, assignee=request.assignee
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到可改的手动局势 #{issue_id}（仅手动新建且进行中的可改）")
    print(f"[issue/api] 改手动局势 id={issue_id} title={request.title!r} assignee={request.assignee!r}")
    return {"updated": True, "id": issue_id}


@app.patch("/api/issues/{issue_id}/assignee")
async def api_update_issue_assignee(issue_id: int, request: IssueAssigneeUpdateRequest) -> Dict[str, Any]:
    """改任意 active 局势的承办人。"""
    ok = get_game().db.update_issue_assignee(issue_id, request.assignee)
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到可改的在办局势 #{issue_id}")
    print(f"[issue/api] 改局势承办人 id={issue_id} assignee={request.assignee!r}")
    return {"updated": True, "id": issue_id, "assignee": request.assignee.strip()}


@app.post("/api/issues/{issue_id}/authorization")
async def api_set_issue_authorization(issue_id: int, request: IssueAuthorizationRequest) -> Dict[str, Any]:
    """给 active 局势的承办人设授权：追加专款（指定出库）、开/关生杀权。
    承办人此后每月自主从专款推进，不必皇帝再下圣旨。"""
    result = get_game().db.set_issue_authorization(
        issue_id,
        budget_add=request.budget_add,
        budget_source=request.budget_source,
        death_authority=request.death_authority,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"未找到可改的在办局势 #{issue_id}")
    print(f"[issue/api] 设局势授权 id={issue_id} 追加={request.budget_add}万两 出库={request.budget_source!r} "
          f"生杀权={request.death_authority} → 池余={result['budget_pool']}")
    return {"updated": True, "id": issue_id, **result}


@app.delete("/api/issues/manual/{issue_id}")
async def api_delete_manual_issue(issue_id: int) -> Dict[str, Any]:
    """删除手动 decree 局势（仅手动新建的可删）。"""
    ok = get_game().db.delete_manual_issue(issue_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到可删的手动局势 #{issue_id}（仅手动新建的可删）")
    print(f"[issue/api] 删除手动局势 id={issue_id}")
    return {"deleted": True, "id": issue_id}


@app.get("/api/turn_extraction")
async def api_turn_extraction(turn: int = -1) -> Dict[str, Any]:
    """读 turn_extractions：默认上一回合（state.turn-1，因 resolve 已 next_period）。"""
    if turn < 0:
        turn = max(1, int(get_game().state.turn) - 1)
    data = get_game().db.get_turn_extraction(turn)
    if data is None:
        return {"turn": turn, "exists": False}
    data["exists"] = True
    return data


@app.get("/api/history/turns")
async def api_history_turns() -> Dict[str, Any]:
    """已存档回合列表（turn_reports / turn_extractions / 已颁诏 turn_directives 并集）。"""
    return {"turns": get_game().db.list_archived_turns()}


@app.get("/api/history/turn/{turn}")
async def api_history_turn(turn: int) -> Dict[str, Any]:
    """某回合历史聚合：邸报奏报 + 诏书 + 已颁草案 + extractor 输入/输出。"""
    db = get_game().db
    report = db.get_turn_report(turn)
    extraction = db.get_turn_extraction(turn)
    directives = db.list_directives_by_turn(turn)
    if not report and extraction is None and not directives:
        return {"turn": turn, "exists": False}
    decree_text = ""
    if extraction is not None:
        decree_text = str(extraction.get("decree_text") or "")
        extraction["exists"] = True
    return {
        "turn": turn,
        "exists": True,
        "year": extraction["year"] if extraction else (directives[0]["year"] if directives else 0),
        "period": extraction["period"] if extraction else (directives[0]["period"] if directives else 0),
        "report": report,
        "decree_text": decree_text,
        "directives": directives,
        "extraction": extraction,
    }


@app.get("/api/map")
async def api_map() -> Dict[str, Any]:
    return {"nodes": get_game().map_nodes()}


@app.get("/api/buildings")
async def api_buildings(region_id: str = "") -> Dict[str, Any]:
    return {"buildings": get_game().db.building_payload(region_id)}


@app.post("/api/favorites/{minister_name}")
async def api_add_favorite(minister_name: str) -> Dict[str, Any]:
    if minister_name not in get_game().content.characters:
        raise HTTPException(status_code=404, detail=f"未找到：{minister_name}")
    row = get_game().db.conn.execute("SELECT archived FROM characters WHERE name=?", (minister_name,)).fetchone()
    if row is not None and int(row["archived"] or 0):
        raise HTTPException(status_code=409, detail=f"{minister_name}已归档，请先恢复。")
    get_game().favorites.add(minister_name)
    get_game().db.kv_set("favorites", json.dumps(sorted(get_game().favorites)))
    return {"favorites": sorted(get_game().favorites)}


@app.delete("/api/favorites/{minister_name}")
async def api_remove_favorite(minister_name: str) -> Dict[str, Any]:
    get_game().favorites.discard(minister_name)
    get_game().db.kv_set("favorites", json.dumps(sorted(get_game().favorites)))
    return {"favorites": sorted(get_game().favorites)}


@app.post("/api/ministers/{minister_name}/archive")
async def api_archive_minister(minister_name: str) -> Dict[str, Any]:
    game = get_game()
    try:
        result = game.db.archive_runtime_character(game.session.state, minister_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    game.content.characters.pop(minister_name, None)
    game.session.temporary_characters.pop(minister_name, None)
    game.chat_history.pop(minister_name, None)
    game.favorites.discard(minister_name)
    game.db.kv_set("favorites", json.dumps(sorted(game.favorites)))
    if getattr(game.session, "registry", None) is not None:
        game.session.registry.agents.pop(minister_name, None)
        game.session.registry.session_ids.pop(minister_name, None)
    return {"ok": True, **result}


@app.post("/api/ministers/{minister_name}/restore")
async def api_restore_minister(minister_name: str) -> Dict[str, Any]:
    game = get_game()
    try:
        result = game.db.restore_archived_character(minister_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    row = game.db.conn.execute(
        """
        SELECT name, office, office_type, faction, aliases, personal_skills,
               loyalty, ability, integrity, courage, style,
               diplomacy, martial, stewardship, intrigue, learning,
               birth_year, historical_death_year, historical_death_month,
               debut_year, debut_month, status, portrait_id, power_id, location,
               summary
        FROM characters
        WHERE name=? AND archived=0
        """,
        (minister_name,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未找到已恢复人物：{minister_name}")
    character = game._character_from_db_row(row)
    game.content.characters[character.name] = character
    game.chat_history.setdefault(character.name, [])
    if getattr(game.session, "registry", None) is not None:
        game.session.registry.session_ids.setdefault(
            character.name,
            f"minister-{character.name}-turn-{game.session.state.turn}",
        )
        game.session.registry.agents.pop(character.name, None)
    return {"ok": True, "minister": game.public_character(character), **result}


_STATUS_LABEL_WEB = {
    "active": "在朝", "offstage": "尚未登场", "dead": "已殁", "dismissed": "已罢黜",
    "imprisoned": "下狱", "exiled": "流放", "retired": "致仕",
}


def _require_chat_capable_minister(minister_name: str) -> None:
    if minister_name in get_game().session.temporary_characters:
        return
    if minister_name not in get_game().content.characters:
        raise HTTPException(status_code=404, detail=f"未找到人物：{minister_name}")
    archived_row = get_game().db.conn.execute("SELECT archived FROM characters WHERE name=?", (minister_name,)).fetchone()
    if archived_row is not None and int(archived_row["archived"] or 0):
        raise HTTPException(status_code=409, detail=f"{minister_name}已归档，请先恢复。")
    if get_game().character_power_id(get_game().content.characters[minister_name]) != "ming":
        raise HTTPException(status_code=409, detail=f"{minister_name}不属大明朝廷，无法召见。")
    status, reason = get_game().db.get_character_status(minister_name)
    if status in {"dead", "offstage"}:
        label = _STATUS_LABEL_WEB.get(status, status)
        detail = f"{minister_name}已{label}，无法召见。" + (reason or "")
        raise HTTPException(status_code=409, detail=detail.strip())


@app.get("/api/ministers/{minister_name}/chat")
async def api_chat_history(minister_name: str) -> Dict[str, Any]:
    if minister_name not in get_game().content.characters and minister_name not in get_game().session.temporary_characters:
        raise HTTPException(status_code=404, detail=f"未找到人物：{minister_name}")
    archived_row = get_game().db.conn.execute("SELECT archived FROM characters WHERE name=?", (minister_name,)).fetchone()
    if archived_row is not None and int(archived_row["archived"] or 0):
        raise HTTPException(status_code=409, detail=f"{minister_name}已归档，请先恢复。")
    character = get_game().session._character(minister_name)
    return {
        "minister": get_game().public_character(character),
        "history": get_game().chat_history.get(minister_name, []),
        "suggestions": get_game().suggestions_for(character) if character.status == "active" else [],
    }


@app.post("/api/ministers/{minister_name}/secret_order")
async def api_create_secret_order(minister_name: str, request: SecretOrderRequest) -> Dict[str, Any]:
    """皇帝直接下达密令，不经 LLM，直接落库。"""
    game = get_game()
    character = game.session.content.characters.get(minister_name)
    if not character:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
    if game.character_power_id(character) != "ming":
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=f"{minister_name}不属大明朝廷，无法下达密令。")
    title = request.title.strip()[:20]
    content = request.content.strip()
    if not title or not content:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="title 和 content 不能为空")
    try:
        order_id = game.db.create_secret_order(
            game.session.state, minister_name, title, content, request.tags, deadline_months=request.deadline_months
        )
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=str(exc)) from None
    print(f"[secret_order/api] 直接落库 minister={minister_name} title={title!r} id={order_id}")
    return {"order_id": order_id, "minister_name": minister_name, "title": title, "status": "active"}


@app.post("/api/ministers/{minister_name}/chat")
async def api_chat(minister_name: str, request: ChatRequest) -> Dict[str, Any]:
    _require_chat_capable_minister(minister_name)
    return get_game().chat(minister_name, request.message)


@app.post("/api/ministers/{minister_name}/chat/stream")
async def api_chat_stream(minister_name: str, request: ChatRequest) -> StreamingResponse:
    _require_chat_capable_minister(minister_name)
    async def generate() -> AsyncIterator[str]:
        for item in get_game().chat_stream(minister_name, request.message):
            item_type = str(item.get("type", "message"))
            if item_type == "delta":
                yield sse_event("delta", {"content": item.get("content", "")})
            elif item_type == "done":
                yield sse_event("done", item.get("payload", {}))
            elif item_type == "error":
                yield sse_event("error", item.get("detail") or {"message": item.get("message", "流式回复失败。")})
            await asyncio.sleep(0)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/ministers/{minister_name}/chat/undo")
async def api_chat_undo(minister_name: str) -> Dict[str, Any]:
    return get_game().undo_last_chat(minister_name)


@app.get("/api/court_chat")
async def api_court_chat_history() -> Dict[str, Any]:
    return get_game().court_chat_history_payload()


@app.post("/api/court_chat/stream")
async def api_court_chat_stream(request: CourtChatRequest) -> StreamingResponse:
    game = get_game()
    async def generate() -> AsyncIterator[str]:
        for item in game.court_chat_stream(request.message, request.ministers):
            item_type = str(item.get("type", "message"))
            if item_type == "reply":
                yield sse_event("reply", {
                    "role": item.get("role", "minister"),
                    "speaker": item.get("speaker", ""),
                    "content": item.get("content", ""),
                })
            elif item_type == "conclusion":
                yield sse_event("conclusion", {
                    "role": "conclusion",
                    "speaker": item.get("speaker", "朝议结论"),
                    "content": item.get("content", ""),
                    "options": item.get("options", []),
                })
            elif item_type == "speaker":
                yield sse_event("speaker", {"speaker": item.get("speaker", "")})
            elif item_type == "delta":
                yield sse_event("delta", {
                    "speaker": item.get("speaker", ""),
                    "content": item.get("content", ""),
                })
            elif item_type == "done":
                yield sse_event("done", item.get("payload", {}))
            elif item_type == "error":
                yield sse_event("error", item.get("detail") or {"message": item.get("message", "朝会回复失败。")})
            await asyncio.sleep(0)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/court_chat")
async def api_court_chat_stream_alias(request: CourtChatRequest) -> StreamingResponse:
    return await api_court_chat_stream(request)


@app.post("/api/court_chat/summary")
async def api_court_chat_summary(request: CourtChatSummaryRequest) -> Dict[str, Any]:
    messages = [
        {"role": m.role, "speaker": m.speaker, "content": m.content}
        for m in request.messages
    ]
    return get_game().court_chat_summary(messages)


@app.post("/api/directives")
async def api_create_directive(request: DirectiveRequest) -> Dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空。")
    dv = get_game().session.add_directive(request.text.strip(), notes=request.notes)
    return {
        "directive": {"id": dv.id, "text": dv.text, "status": dv.status},
        "directives": [get_game().directive_payload(item) for item in get_game().directive_rows()],
    }


@app.get("/api/structured_directives/templates")
async def api_structured_directive_templates() -> Dict[str, Any]:
    return {"templates": load_directive_templates()}


@app.post("/api/structured_directives")
async def api_create_structured_directive(request: StructuredDirectiveRequest) -> Dict[str, Any]:
    try:
        get_game().session.add_structured_directive(request.template_id, request.fields)
    except StructuredDirectiveError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"structured_directives": get_game().session.list_structured_directives()}


@app.patch("/api/structured_directives/{directive_id}")
async def api_update_structured_directive(directive_id: int, request: StructuredDirectiveRequest) -> Dict[str, Any]:
    try:
        get_game().session.update_structured_directive(directive_id, request.template_id, request.fields)
    except StructuredDirectiveError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"structured_directives": get_game().session.list_structured_directives()}


@app.delete("/api/structured_directives/{directive_id}")
async def api_delete_structured_directive(directive_id: int) -> Dict[str, Any]:
    get_game().session.delete_structured_directive(directive_id)
    return {"structured_directives": get_game().session.list_structured_directives()}


@app.patch("/api/directives/{directive_id}")
async def api_update_directive(directive_id: int, request: DirectivePatch) -> Dict[str, Any]:
    rows = get_game().directive_rows()
    row = next((item for item in rows if int(item["id"]) == directive_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到草案。")
    text = request.text if request.text is not None else str(row["text"])
    if not text.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空。")
    get_game().session.update_directive(directive_id, text.strip())
    return {"directives": [get_game().directive_payload(item) for item in get_game().directive_rows()]}


@app.delete("/api/directives/{directive_id}")
async def api_delete_directive(directive_id: int) -> Dict[str, Any]:
    get_game().session.delete_directive(directive_id)
    return {"directives": [get_game().directive_payload(item) for item in get_game().directive_rows()]}


@app.post("/api/directives/{directive_id}/confirm")
async def api_confirm_directive(directive_id: int) -> Dict[str, Any]:
    """大臣拟旨经皇帝核定：pending → draft。"""
    get_game().session.confirm_directive(directive_id)
    return {
        "directives": [get_game().directive_payload(item) for item in get_game().directive_rows()],
        "pending_count": get_game().session.pending_count(),
    }


@app.post("/api/directives/{directive_id}/reject")
async def api_reject_directive(directive_id: int) -> Dict[str, Any]:
    """皇帝驳回大臣拟旨：pending → rejected。"""
    get_game().session.reject_directive(directive_id)
    return {
        "directives": [get_game().directive_payload(item) for item in get_game().directive_rows()],
        "pending_count": get_game().session.pending_count(),
    }


class WriteDecreeRequest(BaseModel):
    force: bool = False


@app.post("/api/decree/write")
async def api_write_decree(request: WriteDecreeRequest = WriteDecreeRequest()) -> Dict[str, Any]:
    try:
        existing = (get_game().session.last_decree or "").strip()
        decree = get_game().session.write_decree() if request.force or not existing else existing
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"decree": decree}


class EditDecreeRequest(BaseModel):
    decree: str


@app.patch("/api/decree")
async def api_edit_decree(body: EditDecreeRequest) -> Dict[str, Any]:
    """皇帝手动改定诏书正文（拟诏后、颁诏前）。"""
    try:
        decree = get_game().session.set_decree(body.decree)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"decree": decree}


class IssueDecreeRequest(BaseModel):
    # 作弊控制台（Ctrl+~）下的强制结算项；一次性，颁诏即用。普通颁诏留空。
    cheat: str = ""


@app.post("/api/decree/issue")
async def api_issue_decree(body: IssueDecreeRequest = IssueDecreeRequest()) -> Dict[str, Any]:
    """非流式颁诏（保留兼容）。前端默认走 /api/decree/issue/stream。"""
    game = get_game()
    was_ended = bool(game.state.ended)
    issued_decree = bool(
        game.session.db.list_directives(game.state, statuses=("draft",))
        or game.session.db.list_structured_directives(game.state, statuses=("draft",))
    )
    try:
        result = game.session.resolve_turn(cheat_directive=body.cheat)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    decree = game.session.last_decree
    if result.awaiting:
        # 决策点暂停：回合未结算，返回决策点让前端弹窗；不刷新、不计 steam。
        return {"decree": decree, "awaiting_decision": True,
                "decisions": result.decisions, "state": game.state_payload()}
    report = result.report
    game.refresh_turn()
    events = [
        steam_events.add_stat(steam_events.STAT_TURNS_PLAYED),
        steam_events.set_stat(steam_events.STAT_MAX_TURN_REACHED, int(game.state.turn)),
    ]
    if issued_decree:
        events.insert(0, steam_events.add_stat(steam_events.STAT_DECREES_ISSUED))
    if not was_ended and game.state.ended:
        events.append(steam_events.add_stat(steam_events.STAT_ENDINGS_REACHED))
    return steam_events.with_events({"decree": decree, "report": report, "state": game.state_payload()}, events)


@app.post("/api/decree/issue/stream")
async def api_issue_decree_stream(body: IssueDecreeRequest = IssueDecreeRequest()) -> StreamingResponse:
    """流式颁诏：推演过程（阶段/思考/正文）实时 SSE 推给前端。

    resolve_turn 是阻塞的同步调用，且 on_event 是 push 式回调。
    用 worker 线程跑 resolve_turn，回调把事件投进 Queue；
    async generator 从 Queue 拉事件转成 SSE。
    """
    ev_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()

    def on_event(kind: str, data: str) -> None:
        ev_queue.put((kind, data))

    def worker() -> None:
        try:
            game = get_game()
            was_ended = bool(game.state.ended)
            issued_decree = bool(
                game.session.db.list_directives(game.state, statuses=("draft",))
                or game.session.db.list_structured_directives(game.state, statuses=("draft",))
            )
            result = game.session.resolve_turn(on_event=on_event, cheat_directive=body.cheat)
            decree = game.session.last_decree
            if result.awaiting:
                # 决策点暂停：邸报已流式推完，再推 decisions 让前端弹窗；本回合未结算、不刷新、不计 steam。
                ev_queue.put(("__decisions__", {
                    "decree": decree,
                    "decisions": result.decisions,
                    "state": game.state_payload(),
                }))
                return
            report = result.report
            game.refresh_turn()
            events = [
                steam_events.add_stat(steam_events.STAT_TURNS_PLAYED),
                steam_events.set_stat(steam_events.STAT_MAX_TURN_REACHED, int(game.state.turn)),
            ]
            if issued_decree:
                events.insert(0, steam_events.add_stat(steam_events.STAT_DECREES_ISSUED))
            if not was_ended and game.state.ended:
                events.append(steam_events.add_stat(steam_events.STAT_ENDINGS_REACHED))
            ev_queue.put(("__done__", {
                "decree": decree,
                "report": report,
                "state": game.state_payload(),
                "steam_events": events,
            }))
        except ValueError as e:
            ev_queue.put(("__error__", str(e)))
        except Exception as e:  # noqa: BLE001
            ev_queue.put(("__error__", _llm_error_detail(e) if isinstance(e, LLMUnavailable) else str(e)))

    async def generate() -> AsyncIterator[str]:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        loop = asyncio.get_running_loop()
        while True:
            kind, data = await loop.run_in_executor(None, ev_queue.get)
            if kind == "__done__":
                yield sse_event("done", data)
                break
            if kind == "__decisions__":
                yield sse_event("decisions", data)
                break
            if kind == "__error__":
                yield sse_event("error", data if isinstance(data, dict) else {"message": data})
                break
            # stage / thinking / text
            yield sse_event(kind, {"content": data})

    return StreamingResponse(generate(), media_type="text/event-stream")


class ResolveDecisionsRequest(BaseModel):
    # 皇帝亲裁结果：按决策点 idx 顺序，每项 {label, hint?, note?}。
    choices: List[Dict[str, Any]] = []
    cheat: str = ""


@app.post("/api/decree/resolve_decisions/stream")
async def api_resolve_decisions_stream(body: ResolveDecisionsRequest) -> StreamingResponse:
    """皇帝亲裁完决策点，流式跑 phase2 结算（extractor→落库→结局）。
    与 issue/stream 同结构：worker 跑 submit_decisions，SSE 推 stage/text + done。"""
    ev_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()

    def on_event(kind: str, data: str) -> None:
        ev_queue.put((kind, data))

    def worker() -> None:
        try:
            game = get_game()
            was_ended = bool(game.state.ended)
            report = game.session.submit_decisions(
                body.choices, on_event=on_event, cheat_directive=body.cheat
            )
            decree = game.session.last_decree
            game.refresh_turn()
            events = [
                steam_events.add_stat(steam_events.STAT_DECREES_ISSUED),
                steam_events.add_stat(steam_events.STAT_TURNS_PLAYED),
                steam_events.set_stat(steam_events.STAT_MAX_TURN_REACHED, int(game.state.turn)),
            ]
            if not was_ended and game.state.ended:
                events.append(steam_events.add_stat(steam_events.STAT_ENDINGS_REACHED))
            ev_queue.put(("__done__", {
                "decree": decree,
                "report": report,
                "state": game.state_payload(),
                "steam_events": events,
            }))
        except ValueError as e:
            ev_queue.put(("__error__", str(e)))
        except Exception as e:  # noqa: BLE001
            ev_queue.put(("__error__", _llm_error_detail(e) if isinstance(e, LLMUnavailable) else str(e)))

    async def generate() -> AsyncIterator[str]:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        loop = asyncio.get_running_loop()
        while True:
            kind, data = await loop.run_in_executor(None, ev_queue.get)
            if kind == "__done__":
                yield sse_event("done", data)
                break
            if kind == "__error__":
                yield sse_event("error", data if isinstance(data, dict) else {"message": data})
                break
            yield sse_event(kind, {"content": data})

    return StreamingResponse(generate(), media_type="text/event-stream")


class SaveCreateRequest(BaseModel):
    name: str


class LLMConfigRequest(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    max_tokens: int = 0
    timeout_seconds: float = 0
    connect_timeout_seconds: float = 0
    read_timeout_seconds: float = 0
    thinking_level: str = "__keep__"
    # None=不动，""=显式清空，其他=覆写。pydantic v1 默认 None 走不进来；用 sentinel "__keep__"
    advanced_model: str = "__keep__"
    advanced_base_url: str = "__keep__"
    advanced_api_key: str = "__keep__"
    advanced_thinking_level: str = "__keep__"


@app.get("/api/consorts/candidates")
async def api_consort_candidates() -> Dict[str, Any]:
    """返回 status=candidate 的待选秀女，供选妃事件展示。"""
    candidates = [
        get_game().public_character(c)
        for c in get_game().content.characters.values()
        if c.office_type == "后宫" and c.status == "candidate" and get_game().character_power_id(c) == "ming"
    ]
    return {"candidates": candidates}


@app.post("/api/consorts/{name}/select")
async def api_select_consort(name: str) -> Dict[str, Any]:
    """皇帝选中某秀女，转 active 并赋予初始位份。"""
    game = get_game()
    consort = game.content.characters.get(name)
    if consort is None or consort.office_type != "后宫":
        raise HTTPException(status_code=404, detail=f"未找到候选秀女：{name}")
    if consort.status != "candidate":
        raise HTTPException(status_code=409, detail=f"{name} 当前状态为 {consort.status}，不可再选。")
    game.db.set_character_office(name, "嫔", "后宫", source="皇帝选妃")
    game.db.set_character_status(game.state, name, "active", "皇帝选中入宫")
    consort.office = "嫔"
    consort.office_type = "后宫"
    consort.status = "active"
    # 同步进 registry（新增 agent）
    game.session.registry.register(consort)
    game.chat_history.setdefault(name, [])
    return {"selected": game.public_character(consort)}


@app.get("/api/saves")
async def api_list_saves() -> Dict[str, Any]:
    return {"saves": get_game().list_saves()}


@app.post("/api/saves")
async def api_create_save(request: SaveCreateRequest) -> Dict[str, Any]:
    info = get_game().save_to(request.name)
    return steam_events.with_events(
        {"save": info, "saves": get_game().list_saves()},
        [steam_events.add_stat(steam_events.STAT_SAVES_CREATED)],
    )


@app.delete("/api/saves/{name}")
async def api_delete_save(name: str) -> Dict[str, Any]:
    get_game().delete_save(name)
    return {"saves": get_game().list_saves()}


@app.post("/api/saves/{name}/load")
async def api_load_save(name: str) -> Dict[str, Any]:
    get_game().load_save(name)
    return {"state": get_game().state_payload()}


@app.post("/api/game/reset")
async def api_reset_game() -> Dict[str, Any]:
    """清空主 DB 重开新局。存档目录保留。"""
    get_game().reset_game()
    return steam_events.with_events(
        {"state": get_game().state_payload()},
        [steam_events.add_stat(steam_events.STAT_RUNS_STARTED)],
    )


@app.get("/api/llm/config")
async def api_get_llm_config() -> Dict[str, Any]:
    """读当前生效的 LLM 配置。api_key 不回传明文，只回是否已设置。"""
    cfg = get_game().session.llm_config
    saved = load_runtime_llm()
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "timeout_seconds": cfg.timeout_seconds,
        "connect_timeout_seconds": cfg.connect_timeout_seconds,
        "read_timeout_seconds": cfg.read_timeout_seconds,
        "thinking_level": cfg.thinking_level,
        "advanced_model": cfg.advanced_model,
        "advanced_base_url": cfg.advanced_base_url,
        "has_advanced_api_key": bool(cfg.advanced_api_key),
        "advanced_thinking_level": cfg.advanced_thinking_level,
        "has_api_key": bool(cfg.api_key),
        "persisted": {
            "base_url": saved.get("base_url", ""),
            "model": saved.get("model", ""),
            "has_api_key": bool(saved.get("api_key", "")),
            "max_tokens": int(saved.get("max_tokens") or 8000),
            "timeout_seconds": float(saved.get("timeout_seconds") or 180),
            "connect_timeout_seconds": float(saved.get("connect_timeout_seconds") or 60),
            "read_timeout_seconds": float(saved.get("read_timeout_seconds") or 120),
            "thinking_level": saved.get("thinking_level", ""),
            "advanced_model": saved.get("advanced_model", ""),
            "advanced_base_url": saved.get("advanced_base_url", ""),
            "has_advanced_api_key": bool(saved.get("advanced_api_key", "")),
            "advanced_thinking_level": saved.get("advanced_thinking_level", ""),
        },
    }


@app.post("/api/llm/config")
async def api_set_llm_config(request: LLMConfigRequest) -> Dict[str, Any]:
    thinking_level = None if request.thinking_level == "__keep__" else request.thinking_level
    advanced = None if request.advanced_model == "__keep__" else request.advanced_model
    adv_base = None if request.advanced_base_url == "__keep__" else request.advanced_base_url
    adv_key = None if request.advanced_api_key == "__keep__" else request.advanced_api_key
    adv_thinking = None if request.advanced_thinking_level == "__keep__" else request.advanced_thinking_level
    try:
        cfg = get_game().apply_llm_config(
            request.base_url,
            request.model,
            request.api_key,
            request.max_tokens,
            request.timeout_seconds,
            request.connect_timeout_seconds,
            request.read_timeout_seconds,
            thinking_level=thinking_level,
            advanced_model=advanced,
            advanced_base_url=adv_base,
            advanced_api_key=adv_key,
            advanced_thinking_level=adv_thinking,
        )
    except LLMUnavailable as e:
        raise HTTPException(status_code=400, detail=_llm_error_detail(e)) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_llm_error_detail(e)) from None
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "timeout_seconds": cfg.timeout_seconds,
        "connect_timeout_seconds": cfg.connect_timeout_seconds,
        "read_timeout_seconds": cfg.read_timeout_seconds,
        "thinking_level": cfg.thinking_level,
        "advanced_model": cfg.advanced_model,
        "advanced_base_url": cfg.advanced_base_url,
        "has_advanced_api_key": bool(cfg.advanced_api_key),
        "advanced_thinking_level": cfg.advanced_thinking_level,
        "has_api_key": bool(cfg.api_key),
    }


# ── 自定义立绘上传/读取 ──────────────────────────────────────────────────────
# content_type → 存盘扩展名。一人一图，上传新图会顶掉旧扩展名的文件。
_PORTRAIT_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _find_portrait_file(name: str) -> Optional[str]:
    """找该人物已存在的自定义立绘文件（任一扩展名），无则 None。"""
    for ext in _PORTRAIT_EXT.values():
        path = os.path.join(UPLOAD_PORTRAIT_DIR, f"{name}.{ext}")
        if os.path.exists(path):
            return path
    return None


@app.post("/api/consorts/{name}/portrait")
async def api_upload_portrait(name: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    # 只接受已存在的人物名 → 集合固定，杜绝路径穿越/任意写。
    character = get_game().find_character(name)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该人物")
    ext = _PORTRAIT_EXT.get(file.content_type or "")
    if ext is None:
        raise HTTPException(status_code=400, detail="仅支持 PNG/JPEG/WebP 图片")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_PORTRAIT_BYTES:
        raise HTTPException(status_code=400, detail="图片过大（上限 8MB）")
    os.makedirs(UPLOAD_PORTRAIT_DIR, exist_ok=True)
    # 先清掉该人物的旧图（可能扩展名不同），再写新图。
    old = _find_portrait_file(name)
    if old is not None:
        os.remove(old)
    with open(os.path.join(UPLOAD_PORTRAIT_DIR, f"{name}.{ext}"), "wb") as fh:
        fh.write(data)
    get_game().set_custom_portrait(name, f"{CUSTOM_PORTRAIT_PREFIX}{name}")
    return {"name": name, "portrait_id": f"{CUSTOM_PORTRAIT_PREFIX}{name}"}


@app.delete("/api/consorts/{name}/portrait")
async def api_delete_portrait(name: str) -> Dict[str, Any]:
    character = get_game().find_character(name)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该人物")
    old = _find_portrait_file(name)
    if old is not None:
        os.remove(old)
    # 复位 portrait_id：清空 → 前端回落到池图（add/seed 时会按 office_type 再分配）。
    get_game().set_custom_portrait(name, "")
    return {"name": name, "portrait_id": ""}


@app.get("/api/court_layout")
async def api_get_court_layout() -> Dict[str, Any]:
    val = get_game().db.kv_get("court_layout")
    return {"layout": val or "{}"}


@app.post("/api/court_layout")
async def api_set_court_layout(body: Dict[str, Any]) -> Dict[str, Any]:
    get_game().db.kv_set("court_layout", body.get("layout", "{}"))
    return {"ok": True}


@app.get("/portraits/custom/{name}")
async def api_get_portrait(name: str):
    path = _find_portrait_file(name)
    if path is None:
        raise HTTPException(status_code=404, detail="无自定义立绘")
    return FileResponse(path)


# ── 调试台：直接读写核心表 ─────────────────────────────────────
@app.get("/api/admin/tables")
async def api_admin_tables() -> Dict[str, Any]:
    return {"tables": list(get_game().db.ADMIN_TABLES.keys())}


@app.get("/api/admin/table/{table}")
async def api_admin_table(table: str) -> Dict[str, Any]:
    db = get_game().db
    try:
        return {
            "table": table,
            "pk": db.admin_check_table(table),
            "columns": db.admin_columns(table),
            "rows": db.admin_rows(table),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/table/{table}/upsert")
async def api_admin_upsert(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    game = get_game()
    try:
        row = game.db.admin_upsert(table, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 同步当前回合内存 state，否则改动要到下回合 begin_turn 才生效。
    st = game.state
    if table == "metrics" and row.get("key") in st.metrics:
        st.metrics[row["key"]] = int(row["value"])
    elif table == "game_state":
        st.year, st.period, st.turn = int(row["year"]), int(row["period"]), int(row["turn"])
    return {"row": row}


@app.post("/api/admin/table/{table}/delete")
async def api_admin_delete(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    pk_value = payload.get("pk_value")
    if pk_value in (None, ""):
        raise HTTPException(status_code=400, detail="缺 pk_value")
    try:
        return {"deleted": get_game().db.admin_delete(table, pk_value)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin")
async def admin_page():
    return HTMLResponse(_ADMIN_HTML)


if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


_ADMIN_HTML = """<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>调试台 · 核心表增删改查</title>
<style>
  :root{--bg:#1b1712;--panel:#26211a;--line:#3a3228;--txt:#e8dcc6;--accent:#c8a35a;--danger:#b5503f;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,"PingFang SC",monospace}
  header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  header h1{font-size:16px;margin:0 12px 0 0;color:var(--accent)}
  .tab{padding:5px 12px;border:1px solid var(--line);background:var(--panel);color:var(--txt);border-radius:4px;cursor:pointer}
  .tab.active{background:var(--accent);color:#1b1712;font-weight:600}
  #bar{padding:8px 16px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center}
  button.act{padding:5px 12px;border:1px solid var(--accent);background:transparent;color:var(--accent);border-radius:4px;cursor:pointer}
  button.act:hover{background:var(--accent);color:#1b1712}
  #wrap{overflow:auto;height:calc(100vh - 110px)}
  table{border-collapse:collapse;width:100%;font-size:13px}
  th,td{border:1px solid var(--line);padding:4px 6px;text-align:left;white-space:nowrap}
  th{position:sticky;top:0;background:var(--panel);color:var(--accent);z-index:1}
  th.pk{color:#e8c87a}
  td input{width:100%;min-width:90px;background:#15110c;border:1px solid var(--line);color:var(--txt);padding:3px 5px;border-radius:3px;font:13px monospace}
  td input:focus{border-color:var(--accent);outline:none}
  tr.dirty td{background:#2e2718}
  td.ops{white-space:nowrap}
  .sm{padding:3px 8px;font-size:12px;border-radius:3px;cursor:pointer;border:1px solid var(--line);background:var(--panel);color:var(--txt)}
  .sm.save{border-color:var(--accent);color:var(--accent)}
  .sm.del{border-color:var(--danger);color:var(--danger)}
  #msg{margin-left:auto;color:#9c8c6a;font-size:12px}
  .hint{color:#6f6552;font-size:12px}
</style></head><body>
<header><h1>调试台 · 直改核心表</h1><span id="tabs"></span></header>
<div id="bar">
  <button class="act" id="addBtn">+ 新增行</button>
  <button class="act" id="reload">↻ 重载</button>
  <span class="hint">改格变黄→点行尾「存」。新增行须填主键才能存。删除不可撤销。</span>
  <span id="msg"></span>
</div>
<div id="wrap"><table id="grid"></table></div>
<script>
let cur=null, cols=[], pk=null, rows=[];
const $=s=>document.querySelector(s), msg=t=>{$("#msg").textContent=t;};
async function jget(u){const r=await fetch(u);if(!r.ok)throw new Error((await r.json()).detail||r.status);return r.json();}
async function jpost(u,b){const r=await fetch(u,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(b)});if(!r.ok)throw new Error((await r.json()).detail||r.status);return r.json();}
async function init(){
  const tabs=(await jget("/api/admin/tables")).tables;
  $("#tabs").innerHTML=tabs.map(t=>`<span class="tab" data-t="${t}">${t}</span>`).join("");
  document.querySelectorAll(".tab").forEach(e=>e.onclick=()=>load(e.dataset.t));
  load(tabs[0]);
}
async function load(t){
  cur=t; msg("加载…");
  document.querySelectorAll(".tab").forEach(e=>e.classList.toggle("active",e.dataset.t===t));
  const d=await jget("/api/admin/table/"+t);
  cols=d.columns; pk=d.pk; rows=d.rows; render(); msg(rows.length+" 行");
}
function render(){
  const g=$("#grid");
  const head="<tr>"+cols.map(c=>`<th class="${c.pk?'pk':''}">${c.name}${c.pk?' 🔑':''}<br><span class="hint">${c.type}</span></th>`).join("")+"<th>操作</th></tr>";
  g.innerHTML=head+rows.map((r,i)=>rowHtml(r,i)).join("");
  g.querySelectorAll("input").forEach(inp=>inp.oninput=()=>inp.closest("tr").classList.add("dirty"));
  g.querySelectorAll(".save").forEach(b=>b.onclick=()=>saveRow(+b.dataset.i));
  g.querySelectorAll(".del").forEach(b=>b.onclick=()=>delRow(+b.dataset.i));
}
function rowHtml(r,i){
  const tds=cols.map(c=>{
    const v=r[c.name]==null?"":r[c.name];
    return `<td><input data-c="${c.name}" value="${String(v).replace(/"/g,'&quot;')}"></td>`;
  }).join("");
  return `<tr data-i="${i}">${tds}<td class="ops"><button class="sm save" data-i="${i}">存</button> <button class="sm del" data-i="${i}">删</button></td></tr>`;
}
function readRow(i){
  const tr=document.querySelector(`tr[data-i="${i}"]`), o={};
  tr.querySelectorAll("input").forEach(inp=>{
    const c=cols.find(x=>x.name===inp.dataset.c); let v=inp.value;
    if(v===""){o[inp.dataset.c]=null;return;}
    if(c && /INT/i.test(c.type)) v=parseInt(v,10);
    o[inp.dataset.c]=v;
  });
  return o;
}
async function saveRow(i){
  try{
    const body=readRow(i);
    if(body[pk]==null||body[pk]===""){msg("⚠ 主键 "+pk+" 不能空");return;}
    const d=await jpost(`/api/admin/table/${cur}/upsert`,body);
    rows[i]=d.row; render(); msg("✓ 已存 "+body[pk]);
  }catch(e){msg("✗ "+e.message);}
}
async function delRow(i){
  const key=rows[i][pk];
  if(key!=null&&key!==""&&!confirm(`删除 ${cur} 行：${pk}=${key} ？不可撤销`))return;
  try{
    if(key==null||key===""){rows.splice(i,1);render();msg("已移除未存行");return;}
    const d=await jpost(`/api/admin/table/${cur}/delete`,{pk_value:key});
    rows.splice(i,1); render(); msg("✓ 删 "+d.deleted+" 行");
  }catch(e){msg("✗ "+e.message);}
}
$("#addBtn").onclick=()=>{const o={};cols.forEach(c=>o[c.name]=null);rows.unshift(o);render();msg("新增空行，填主键后点存");};
$("#reload").onclick=()=>load(cur);
init().catch(e=>msg("初始化失败:"+e.message));
</script></body></html>"""
