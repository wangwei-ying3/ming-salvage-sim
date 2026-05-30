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
import threading
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ming_sim.constants import ROOT_DIR
from ming_sim.paths import bundled_path, user_data_path, user_data_dir
from ming_sim.exceptions import ExitGame, LLMUnavailable
from ming_sim.llm_config import (
    load_llm_config,
    load_runtime_llm,
    normalize_openai_base_url,
    save_runtime_llm,
)
from ming_sim.llm_model import extract_agent_text, verify_llm_available
from ming_sim.llm_contract import fail_if_llm_error
from ming_sim.issues import _format_issue_ongoing
from ming_sim.session import GameSession
from ming_sim.session import AUTO_SAVE_PREFIX
from ming_sim.skills import available_skill_ids, skill_display_name, skill_source_labels
from ming_sim.context import match_minister_from_text
from ming_sim.flows import calc_province_fiscal
from ming_sim.exceptions import LLMContractError  # noqa: F401  (保留：供错误处理)
from ming_sim.models import Character, LLMConfig, monthly_amount

WEB_DIST = bundled_path("web", "dist")
# 用户上传的自定义立绘存档级目录（不随 build 清空，git 可忽略）。
# frozen 模式落 ~/.ming_sim/uploads/portraits/，源码模式落 <repo>/data/uploads/portraits/。
UPLOAD_PORTRAIT_DIR = user_data_path("uploads", "portraits")
# 自定义立绘 portrait_id 前缀；前端据此解析到 /portraits/custom/<name>.png。
CUSTOM_PORTRAIT_PREFIX = "custom:"
ALLOWED_PORTRAIT_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_PORTRAIT_BYTES = 8 * 1024 * 1024  # 8MB 上限


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
        advanced_model=config.advanced_model,
        advanced_base_url=config.advanced_base_url,
        advanced_api_key=config.advanced_api_key,
    )
    try:
        verify_llm_available(advanced_config)
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


class ChatRequest(BaseModel):
    message: str


class DirectiveRequest(BaseModel):
    text: str
    notes: str = ""


class SecretOrderRequest(BaseModel):
    title: str
    content: str
    tags: List[str] = []
    deadline_months: int = 0


class DirectivePatch(BaseModel):
    text: Optional[str] = None
    notes: Optional[str] = None


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
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        api_key = os.environ.get("OPENAI_API_KEY", "")
        advanced_model = os.environ.get("OPENAI_ADVANCED_MODEL", "")
        advanced_base_url = os.environ.get("OPENAI_ADVANCED_BASE_URL", "")
        advanced_api_key = os.environ.get("OPENAI_ADVANCED_API_KEY", "")
        timeout_seconds = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "180") or 180)
        # 菜单写的 runtime_llm.json 优先于 env，让"在网页里改的配置"重启后仍生效。
        runtime = load_runtime_llm()
        base_url = runtime.get("base_url") or base_url
        model = runtime.get("model") or model
        api_key = runtime.get("api_key") or api_key
        advanced_model = runtime.get("advanced_model") or advanced_model
        advanced_base_url = runtime.get("advanced_base_url") or advanced_base_url
        advanced_api_key = runtime.get("advanced_api_key") or advanced_api_key
        max_tokens = int(runtime.get("max_tokens") or 8000)
        timeout_seconds = float(runtime.get("timeout_seconds") or timeout_seconds)
        if not api_key:
            raise LLMUnavailable("未配 API key，请先到设置页填写。")
        random.seed(int(os.environ.get("MING_SIM_SEED", "7")))
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        if fresh:
            _delete_sqlite_db_files_or_raise(db_path)
        adv_base = (advanced_base_url or "").strip()
        llm_config = LLMConfig(
            api_key=api_key,
            base_url=normalize_openai_base_url(base_url),
            model=model,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            advanced_model=(advanced_model or "").strip(),
            advanced_base_url=normalize_openai_base_url(adv_base) if adv_base else "",
            advanced_api_key=(advanced_api_key or "").strip(),
        )
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
        self.favorites: set = json.loads(_fav_raw) if _fav_raw else _DEFAULT_FAVORITES
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
        self.favorites = json.loads(_fav_raw) if _fav_raw else _DEFAULT_FAVORITES
        if not _fav_raw:
            self.db.kv_set("favorites", json.dumps(sorted(self.favorites)))

    def apply_llm_config(
        self,
        base_url: str,
        model: str,
        api_key: str,
        max_tokens: int = 0,
        timeout_seconds: float = 0,
        advanced_model: Optional[str] = None,
        advanced_base_url: Optional[str] = None,
        advanced_api_key: Optional[str] = None,
    ) -> LLMConfig:
        base = normalize_openai_base_url(base_url.strip() or self.session.llm_config.base_url)
        new_model = model.strip() or self.session.llm_config.model
        new_key = api_key.strip() or self.session.llm_config.api_key
        new_max = max_tokens if max_tokens > 0 else self.session.llm_config.max_tokens
        new_timeout = timeout_seconds if timeout_seconds > 0 else self.session.llm_config.timeout_seconds
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
        new_config = LLMConfig(
            api_key=new_key,
            base_url=base,
            model=new_model,
            max_tokens=new_max,
            timeout_seconds=new_timeout,
            advanced_model=new_advanced,
            advanced_base_url=new_adv_base,
            advanced_api_key=new_adv_key,
        )
        _verify_llm_configs_or_raise(new_config)
        save_runtime_llm(
            new_config.base_url,
            new_config.model,
            new_config.api_key,
            new_config.max_tokens,
            new_config.timeout_seconds,
            new_config.advanced_model,
            new_config.advanced_base_url,
            new_config.advanced_api_key,
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
    def public_character(self, character: Character) -> Dict[str, Any]:
        status, status_reason = self.db.get_character_status(character.name)
        status_label = _STATUS_LABEL_WEB.get(status, "在朝" if status == "active" else status)
        office = character.office  # 去职者已被清空，可能为空串
        # summary 不含官职（卡片/详情已单独显 office），避免重复
        summary = f"{character.faction}一系，行事{character.style}。"
        power_row = self.db.conn.execute(
            "SELECT power_id FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        power_id = (power_row["power_id"] if power_row else None) or getattr(character, "power_id", "ming") or "ming"
        return {
            "name": character.name,
            "office": office,
            "office_type": character.office_type,
            "faction": character.faction,
            "style": character.style,
            "status": status,
            "status_reason": status_reason,
            "status_label": status_label,
            "summary": summary,
            "portrait_id": character.portrait_id,
            "power_id": power_id,
            "skills": [
                {
                    "id": skill_id,
                    "name": skill_display_name(skill_id),
                    "sources": skill_source_labels(character, skill_id, self.db),
                    "description": self.content.skill_descriptions.get(skill_id, ""),
                }
                for skill_id in available_skill_ids(character, self.db)
            ],
            "favorite": character.name in self.favorites,
        }

    def character_power_id(self, character: Character) -> str:
        row = self.db.conn.execute(
            "SELECT power_id FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        return (row["power_id"] if row else None) or getattr(character, "power_id", "ming") or "ming"

    def directive_payload(self, row) -> Dict[str, Any]:
        return {
            "id": int(row["id"]),
            "event_id": row["event_id"] or "",
            "event_title": (row["event_title"] if "event_title" in row.keys() else "") or "",
            "actor": row["actor"] or "",
            "skill_id": row["skill_id"] or "",
            "skill_name": skill_display_name(str(row["skill_id"] or "")),
            "text": row["text"],
            "source": row["source"],
            "status": row["status"],
            "notes": row["notes"],
            "authority": row["notes"] or "",
        }

    def directive_rows(self):
        # 颁诏候选 = draft；UI 列表含 pending
        return self.db.list_directives(self.state, statuses=("pending", "draft"))

    def map_nodes(self) -> List[Dict[str, Any]]:
        region_positions = {
            "beizhili": (66, 30), "nanzhili": (70, 41), "shandong": (71, 38.5),
            "shanxi": (57, 30), "henan": (58, 46), "shaanxi": (51, 38),
            "zhejiang": (73.7, 57.9), "jiangxi": (67, 55), "huguang": (59, 59),
            "sichuan": (57, 52), "fujian": (73.2, 65.1), "guangdong": (62.5, 73.6),
            "guangxi": (53.9, 69.6), "yunnan": (47, 69), "guizhou": (52, 56),
            "liaodong": (72.8, 25.5), "dongjiang_area": (78, 31),
            "shenyang_liaoyang": (75.4, 24.2), "jianzhou": (82, 8),
            "korea": (84, 31), "mongol_chahar": (63, 17), "nurgan": (74, 1.8),
            "taiwan": (78, 67),
        }
        theater_positions = {
            "liaodong": (72.8, 25.5), "dongjiang": (78, 31),
            "xuan_da": (60, 20), "shanhaiguan": (69.5, 27.7),
        }
        armies = self.db.army_payload(danger_order=True)
        nodes: List[Dict[str, Any]] = []
        for region in self.db.region_payload():
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
                "resolve_condition": row["resolve_condition"] or "",
                "fail_condition": row["fail_condition"] or "",
                "ongoing_text": _format_issue_ongoing(str(row["ongoing_effects"] or "{}")),
                "effect_on_resolve": dict(json.loads(str(row["effect_on_resolve"] or "{}"))),
                "effect_on_fail": dict(json.loads(str(row["effect_on_fail"] or "{}"))),
            })
        return payloads

    def budget_payload(self) -> Dict[str, Any]:
        cfg = self.db.get_fiscal_config()
        army_total = self.db.conn.execute("SELECT SUM(maintenance_per_turn) FROM armies").fetchone()[0] or 0

        def rated(base: int, rate_key: str) -> int:
            return monthly_amount(round(int(base) * cfg.get(rate_key, 100) / 100))

        # 用动态省级财政模型预测本月国库收入（与实际结算算法一致）
        guo_income_est, _nei_income_est, _province_details = calc_province_fiscal(self.state, self.db)

        budget = {
            "国库": {
                "balance": int(self.state.metrics["国库"]),
                "income": [
                    {"name": "田赋辽饷盐商", "amount": int(guo_income_est), "note": "各省田赋+辽饷+盐税+商税（按腐败度/士绅阻力/民变动态折算）"},
                ],
                "expense": [
                    {"name": "各军军饷", "amount": int(army_total), "note": "各军月度维护/军饷合计"},
                    {"name": "宗室禄米", "amount": rated(cfg.get("宗室禄米_base", 80), "宗室禄米_rate"), "note": "诸藩宗室月禄米"},
                    {"name": "百官俸禄", "amount": rated(cfg.get("官俸_base", 35), "官俸_rate"), "note": "在京百官月俸禄"},
                    {"name": "工部", "amount": rated(cfg.get("工程_base", 22), "工程_rate"), "note": "工部月维护支出"},
                    {"name": "赈灾备用", "amount": rated(cfg.get("赈灾_base", 25), "赈灾_rate"), "note": "制度性赈灾预留"},
                ],
            },
            "内库": {
                "balance": int(self.state.metrics["内库"]),
                "income": [
                    {"name": "皇庄", "amount": rated(cfg.get("皇庄_base", 60), "皇庄_rate"), "note": "皇庄地租月上缴"},
                    {"name": "织造", "amount": rated(cfg.get("织造_base", 35), "织造_rate"), "note": "苏杭织造局月上缴"},
                    {"name": "矿税", "amount": rated(cfg.get("矿税_base", 10), "矿税_rate"), "note": "矿税残余"},
                ],
                "expense": [
                    {"name": "宫廷开支", "amount": rated(cfg.get("宫廷_base", 18), "宫廷_rate"), "note": "皇室日常用度"},
                    {"name": "内廷俸禄", "amount": rated(cfg.get("内廷俸_base", 12), "内廷俸_rate"), "note": "太监宫女俸禄"},
                    {"name": "妃嫔供奉", "amount": rated(cfg.get("妃嫔_base", 8), "妃嫔_rate"), "note": "后宫妃嫔月供奉"},
                ],
            },
        }
        # 建筑产出/维护并入内库固定栏（按当前 condition 折算的月预算）
        building_rows = self.db.conn.execute(
            "SELECT name, category, condition, maintenance, output_metric, output_amount FROM buildings"
        ).fetchall()
        bld_produce_by_acc: dict[str, int] = {"国库": 0, "内库": 0}
        bld_maintain_by_acc: dict[str, int] = {"国库": 0, "内库": 0}
        for br in building_rows:
            cond = max(0, min(100, int(br["condition"])))
            out_acc = str(br["output_metric"] or "")
            if out_acc in ("国库", "内库") and br["output_amount"]:
                bld_produce_by_acc[out_acc] += round(int(br["output_amount"]) * cond / 100)
            maint_acc = "内库" if str(br["category"] or "") == "内廷" else "国库"
            bld_maintain_by_acc[maint_acc] += max(0, int(br["maintenance"]))
        for acc in ("国库", "内库"):
            if bld_produce_by_acc[acc] > 0:
                budget[acc]["income"].append(
                    {"name": "建筑产出", "amount": bld_produce_by_acc[acc], "note": "建筑月产出"}
                )
            if bld_maintain_by_acc[acc] > 0:
                budget[acc]["expense"].append(
                    {"name": "建筑维护", "amount": bld_maintain_by_acc[acc], "note": "建筑月维护"}
                )
        for account in budget.values():
            income_total = sum(int(item["amount"]) for item in account["income"])
            expense_total = sum(int(item["amount"]) for item in account["expense"])
            account["income_total"] = income_total
            account["expense_total"] = expense_total
            account["net"] = income_total - expense_total
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

    def state_payload(self) -> Dict[str, Any]:
        directives = [self.directive_payload(row) for row in self.directive_rows()]
        return {
            "turn": {"year": self.state.year, "period": self.state.period,
                     "turn": self.state.turn, "phase": self.state.turn_phase},
            "metrics": self.state.metrics,
            "previous_summary": self.previous_summary,
            "treasury": self.db.treasury_report(self.state),
            "issues": self.issue_payloads(),
            "closed_this_turn": self.closed_this_turn_payloads(),
            "budget": self.budget_payload(),
            "region_warning": self.db.region_report(limit=5),
            "army_warning": self.db.army_report(limit=5),
            "power_warning": self.db.power_report(exclude_self=True),
            "powers": self.db.power_payload(),
            "victory_status": self.session.victory(),
            "events": [],
            "regions": self.db.region_payload(),
            "armies": self.db.army_payload(),
            "map_nodes": self.map_nodes(),
            "ministers": [
                self.public_character(c)
                for c in self.content.characters.values()
                if c.office_type != "后宫" and self.character_power_id(c) == "ming"
            ],
            "consorts": [
                self.public_character(c)
                for c in self.content.characters.values()
                if c.office_type == "后宫" and c.status == "active" and self.character_power_id(c) == "ming"
            ],
            "directives": directives,
            "pending_count": self.session.pending_count(),
            "last_decree": self.last_decree,
            "last_report": self.last_report,
        }

    # ── 聊天 ──────────────────────────────────────────────────────────────
    def _chat_payload(
        self,
        minister_name: str,
        answer: str,
        court_action: str = "",
        next_minister: str = "",
        proposed_directive: Optional[Dict[str, Any]] = None,
        appointed_minister: str = "",
        registered_minister: str = "",
        displaced_minister: str = "",
        secret_order_id: int = 0,
    ) -> Dict[str, Any]:
        character = self.session._character(minister_name)
        self.chat_history[minister_name].append({"role": "minister", "content": answer})
        if minister_name not in self.session.temporary_characters:
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
            "directives": [self.directive_payload(row) for row in self.directive_rows()],
            "suggestions": self.suggestions_for(character),
        }

    def chat(self, minister_name: str, message: str) -> Dict[str, Any]:
        if minister_name not in self.content.characters and minister_name not in self.session.temporary_characters:
            raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
        text = message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="问话不能为空。")
        self.chat_history.setdefault(minister_name, []).append({"role": "user", "content": text})
        if minister_name not in self.session.temporary_characters:
            self.db.append_chat_message(minister_name, self.state.turn, "user", text)
        result = self.session.chat(minister_name, text)
        proposed = None
        if result.proposed_directive is not None:
            d = result.proposed_directive
            proposed = {"id": d.id, "text": d.text, "status": d.status, "notes": d.notes}
        return self._chat_payload(
            minister_name, result.answer,
            court_action=result.court_action, next_minister=result.next_minister,
            proposed_directive=proposed, appointed_minister=result.appointed_minister,
            registered_minister=result.registered_minister,
            displaced_minister=result.displaced_minister,
            secret_order_id=result.secret_order_id,
        )

    def chat_stream(self, minister_name: str, message: str) -> Iterator[Dict[str, Any]]:
        if minister_name not in self.content.characters and minister_name not in self.session.temporary_characters:
            yield {"type": "error", "message": f"未找到大臣：{minister_name}"}
            return
        text = message.strip()
        if not text:
            yield {"type": "error", "message": "问话不能为空。"}
            return
        self.chat_history.setdefault(minister_name, []).append({"role": "user", "content": text})
        if minister_name not in self.session.temporary_characters:
            self.db.append_chat_message(minister_name, self.state.turn, "user", text)
        character = self.session._character(minister_name)
        chunks: List[str] = []
        try:
            agent = self.session.registry.get(character)
            run_output = None
            stream = agent.run(text, stream=True, stream_events=True, yield_run_output=True)
            for event in stream:
                content = getattr(event, "content", None)
                event_name = getattr(event, "event", "")
                if event_name == "RunContent" and content:
                    delta = str(content)
                    chunks.append(delta)
                    yield {"type": "delta", "content": delta}
                if type(event).__name__ in ("RunOutput", "RunCompletedEvent"):
                    run_output = event
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
                    # 密令结案不再走大臣工具，由月末推演 + extractor 写入
            payload = self._chat_payload(
                minister_name, answer, court_action=court_action, next_minister=next_minister,
                proposed_directive=proposed, appointed_minister=appointed,
                registered_minister=registered,
                displaced_minister=displaced,
                secret_order_id=secret_order_id,
            )
            yield {"type": "done", "payload": payload}
        except Exception as error:
            if isinstance(error, LLMUnavailable):
                yield {"type": "error", "detail": _llm_error_detail(error)}
            else:
                yield {"type": "error", "message": str(error)}

    def suggestions_for(self, character: Character) -> List[Dict[str, str]]:
        suggestions = [
            {"label": "问在办事项", "text": "当前在办的事项里，哪几件轻重缓急最该先理？"},
            {"label": "问阻力", "text": "眼下推进朝政，最大的阻力来自哪一方？"},
            {"label": "拟旨", "text": "拟旨如下：", "prefix": True},
            {"label": "下密令", "text": "密令如下：", "prefix": True},
        ]
        skill_ids = set(available_skill_ids(character, self.db))
        if "check_treasury" in skill_ids:
            suggestions.insert(1, {"label": "查钱粮", "text": "太仓和内库实数如何？本月哪些钱最急？"})
        if "check_military" in skill_ids or "front_line_plan" in skill_ids or "strategic_review" in skill_ids:
            suggestions.insert(1, {"label": "查驻军", "text": "查一下关宁军、京营和陕西边军的士气、欠饷与补给。"})
        if "secret_investigation" in skill_ids:
            suggestions.insert(1, {"label": "密查", "text": "哪些账册和人物最该先密查？"})
        return suggestions[:6]


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
    """扫存档目录，独立于 WebGame 实例（菜单页无 game 也要能列）。"""
    saves_dir = user_data_path("saves")
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(saves_dir):
        return out
    campaign_id = _main_db_campaign_id()
    for fname in sorted(os.listdir(saves_dir)):
        if not fname.endswith(".db"):
            continue
        if not _save_visible_for_campaign(fname, campaign_id):
            continue
        full = os.path.join(saves_dir, fname)
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


def _has_main_db() -> bool:
    """主 DB 文件是否存在 → 决定「继续」按钮可不可点。"""
    db_path = os.environ.get("MING_SIM_DB", "") or user_data_path("ming_sim.db")
    if not os.path.isabs(db_path):
        db_path = str(user_data_dir() / db_path)
    return os.path.isfile(db_path)


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
        "llm": {
            "base_url": runtime.get("base_url") or os.environ.get("OPENAI_BASE_URL", ""),
            "model": runtime.get("model") or os.environ.get("OPENAI_MODEL", ""),
            "has_api_key": has_api_key,
            "max_tokens": int(runtime.get("max_tokens") or 8000),
            "advanced_model": runtime.get("advanced_model") or os.environ.get("OPENAI_ADVANCED_MODEL", ""),
            "advanced_base_url": runtime.get("advanced_base_url") or os.environ.get("OPENAI_ADVANCED_BASE_URL", ""),
            "has_advanced_api_key": bool(runtime.get("advanced_api_key") or os.environ.get("OPENAI_ADVANCED_API_KEY")),
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
    return {"state": web_game.state_payload()}


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
    return {"state": web_game.state_payload()}


@app.post("/api/menu/load_save/{name}")
async def api_menu_load_save(name: str) -> Dict[str, Any]:
    """从存档启动：先启动空 WebGame（fresh）→ 调 load_save 热替换主 DB。"""
    global web_game
    try:
        web_game = WebGame(fresh=False)  # 先有 session 才能 load_save
    except LLMUnavailable as exc:
        raise HTTPException(status_code=412, detail=_llm_error_detail(exc))
    web_game.load_save(name)
    return {"state": web_game.state_payload()}


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
    advanced_model: str = ""
    advanced_base_url: str = ""
    advanced_api_key: str = ""


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
        advanced_model=advanced_model,
        advanced_base_url=advanced_base_url,
        advanced_api_key=advanced_api_key,
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
        advanced_model,
        advanced_base_url,
        advanced_api_key,
    )
    return {
        "ok": True,
        "llm": {
            "base_url": normalized_base_url,
            "model": model,
            "has_api_key": True,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
            "advanced_model": advanced_model,
            "advanced_base_url": advanced_base_url,
            "has_advanced_api_key": bool(advanced_api_key),
        },
    }
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
    get_game().favorites.add(minister_name)
    get_game().db.kv_set("favorites", json.dumps(sorted(get_game().favorites)))
    return {"favorites": sorted(get_game().favorites)}


@app.delete("/api/favorites/{minister_name}")
async def api_remove_favorite(minister_name: str) -> Dict[str, Any]:
    get_game().favorites.discard(minister_name)
    get_game().db.kv_set("favorites", json.dumps(sorted(get_game().favorites)))
    return {"favorites": sorted(get_game().favorites)}


_STATUS_LABEL_WEB = {
    "active": "在朝", "offstage": "尚未登场", "dead": "已殁", "dismissed": "已罢黜",
    "imprisoned": "下狱", "exiled": "流放", "retired": "致仕",
}


def _require_active_minister(minister_name: str) -> None:
    if minister_name in get_game().session.temporary_characters:
        return
    if minister_name not in get_game().content.characters:
        raise HTTPException(status_code=404, detail=f"未找到人物：{minister_name}")
    if get_game().character_power_id(get_game().content.characters[minister_name]) != "ming":
        raise HTTPException(status_code=409, detail=f"{minister_name}不属大明朝廷，无法召见。")
    status, reason = get_game().db.get_character_status(minister_name)
    if status != "active":
        label = _STATUS_LABEL_WEB.get(status, status)
        detail = f"{minister_name}已{label}，无法召见。" + (reason or "")
        raise HTTPException(status_code=409, detail=detail.strip())


@app.get("/api/ministers/{minister_name}/chat")
async def api_chat_history(minister_name: str) -> Dict[str, Any]:
    _require_active_minister(minister_name)
    character = get_game().session._character(minister_name)
    return {
        "minister": get_game().public_character(character),
        "history": get_game().chat_history.get(minister_name, []),
        "suggestions": get_game().suggestions_for(character),
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
    order_id = game.db.create_secret_order(
        game.session.state, minister_name, title, content, request.tags, deadline_months=request.deadline_months
    )
    print(f"[secret_order/api] 直接落库 minister={minister_name} title={title!r} id={order_id}")
    return {"order_id": order_id, "minister_name": minister_name, "title": title, "status": "active"}


@app.post("/api/ministers/{minister_name}/chat")
async def api_chat(minister_name: str, request: ChatRequest) -> Dict[str, Any]:
    _require_active_minister(minister_name)
    return get_game().chat(minister_name, request.message)


@app.post("/api/ministers/{minister_name}/chat/stream")
async def api_chat_stream(minister_name: str, request: ChatRequest) -> StreamingResponse:
    _require_active_minister(minister_name)
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


@app.post("/api/directives")
async def api_create_directive(request: DirectiveRequest) -> Dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空。")
    dv = get_game().session.add_directive(request.text.strip(), notes=request.notes)
    return {
        "directive": {"id": dv.id, "text": dv.text, "status": dv.status},
        "directives": [get_game().directive_payload(item) for item in get_game().directive_rows()],
    }


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


@app.post("/api/decree/write")
async def api_write_decree() -> Dict[str, Any]:
    try:
        decree = get_game().session.write_decree()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"decree": decree}


@app.post("/api/decree/issue")
async def api_issue_decree() -> Dict[str, Any]:
    """非流式颁诏（保留兼容）。前端默认走 /api/decree/issue/stream。"""
    try:
        report = get_game().session.resolve_turn()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    decree = get_game().session.last_decree
    get_game().refresh_turn()
    return {"decree": decree, "report": report, "state": get_game().state_payload()}


@app.post("/api/decree/issue/stream")
async def api_issue_decree_stream() -> StreamingResponse:
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
            report = get_game().session.resolve_turn(on_event=on_event)
            decree = get_game().session.last_decree
            get_game().refresh_turn()
            ev_queue.put(("__done__", {
                "decree": decree,
                "report": report,
                "state": get_game().state_payload(),
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
            # stage / thinking / text
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
    # None=不动，""=显式清空，其他=覆写。pydantic v1 默认 None 走不进来；用 sentinel "__keep__"
    advanced_model: str = "__keep__"
    advanced_base_url: str = "__keep__"
    advanced_api_key: str = "__keep__"


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
    return {"save": info, "saves": get_game().list_saves()}


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
    return {"state": get_game().state_payload()}


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
        "advanced_model": cfg.advanced_model,
        "advanced_base_url": cfg.advanced_base_url,
        "has_advanced_api_key": bool(cfg.advanced_api_key),
        "has_api_key": bool(cfg.api_key),
        "persisted": {
            "base_url": saved.get("base_url", ""),
            "model": saved.get("model", ""),
            "has_api_key": bool(saved.get("api_key", "")),
            "max_tokens": int(saved.get("max_tokens") or 8000),
            "timeout_seconds": float(saved.get("timeout_seconds") or 180),
            "advanced_model": saved.get("advanced_model", ""),
            "advanced_base_url": saved.get("advanced_base_url", ""),
            "has_advanced_api_key": bool(saved.get("advanced_api_key", "")),
        },
    }


@app.post("/api/llm/config")
async def api_set_llm_config(request: LLMConfigRequest) -> Dict[str, Any]:
    advanced = None if request.advanced_model == "__keep__" else request.advanced_model
    adv_base = None if request.advanced_base_url == "__keep__" else request.advanced_base_url
    adv_key = None if request.advanced_api_key == "__keep__" else request.advanced_api_key
    try:
        cfg = get_game().apply_llm_config(
            request.base_url,
            request.model,
            request.api_key,
            request.max_tokens,
            request.timeout_seconds,
            advanced_model=advanced,
            advanced_base_url=adv_base,
            advanced_api_key=adv_key,
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
        "advanced_model": cfg.advanced_model,
        "advanced_base_url": cfg.advanced_base_url,
        "has_advanced_api_key": bool(cfg.advanced_api_key),
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


# ── 皇帝技能树 API ──────────────────────────────────────────────

@app.get("/api/emperor/skills")
async def api_emperor_skills() -> Dict[str, Any]:
    """返回所有皇帝技能（含已学习状态）+ 当前技能点 + 已学技能列表。"""
    game = get_game()
    db = game.db
    state = game.state
    skill_points_row = db.conn.execute(
        "SELECT value FROM metrics WHERE key = 'skill_points'"
    ).fetchone()
    skill_points = int(skill_points_row["value"]) if skill_points_row else 0
    learned = {row["skill_id"] for row in db.conn.execute(
        "SELECT skill_id FROM emperor_skills"
    ).fetchall()}
    all_skills = game.content.emperor_skills
    trees: Dict[str, List[Dict[str, Any]]] = {}
    for s in all_skills:
        sid = s.id
        req = s.prereq
        cost = s.cost
        # 解锁阈值：从 unlock 字符串 "皇威>=20" 提整数
        unlock_val = int(s.unlock.replace("皇威>=", "") or "20")
        unlocked = state.metrics.get("皇威", 0) >= unlock_val
        can_learn = (
            unlocked
            and sid not in learned
            and (not req or req in learned)
            and skill_points >= cost
        )
        item = {
            "id": sid, "name": s.name, "tree": s.tree,
            "desc": s.desc, "cost": cost,
            "unlock": s.unlock, "req": req,
            "learned": sid in learned,
            "unlocked": unlocked, "can_learn": can_learn,
        }
        trees.setdefault(s.tree, []).append(item)
    return {"trees": trees, "skill_points": skill_points, "learned": list(learned)}


class LearnSkillBody(BaseModel):
    skill_id: str


@app.post("/api/emperor/skills/{skill_id}/learn")
async def api_learn_skill(skill_id: str, body: LearnSkillBody) -> Dict[str, Any]:
    """学习指定技能：扣技能点 + 写入 emperor_skills 表。"""
    game = get_game()
    db = game.db
    state = game.state
    skill_points_row = db.conn.execute(
        "SELECT value FROM metrics WHERE key = 'skill_points'"
    ).fetchone()
    skill_points = int(skill_points_row["value"]) if skill_points_row else 0
    all_skills = {s.id: s for s in game.content.emperor_skills}
    if skill_id not in all_skills:
        raise HTTPException(status_code=404, detail=f"技能 {skill_id} 不存在")
    s = all_skills[skill_id]
    if skill_points < s.cost:
        raise HTTPException(status_code=400, detail=f"技能点不足（需要{s.cost}点，当前{skill_points}点）")
    learned = {row["skill_id"] for row in db.conn.execute(
        "SELECT skill_id FROM emperor_skills"
    ).fetchall()}
    if skill_id in learned:
        raise HTTPException(status_code=400, detail="该技能已学习")
    if s.prereq and s.prereq not in learned:
        raise HTTPException(status_code=400, detail=f"需先学习前置技能 {s.prereq}")
    new_points = skill_points - s.cost
    db.conn.execute(
        "INSERT OR REPLACE INTO metrics (key, value) VALUES ('skill_points', ?)",
        (new_points,),
    )
    db.conn.execute(
        "INSERT OR IGNORE INTO emperor_skills (skill_id, learned_turn) VALUES (?, ?)",
        (skill_id, state.turn),
    )
    db.conn.commit()
    return {"ok": True, "skill_id": skill_id, "skill_points": new_points}


@app.get("/portraits/custom/{name}")
async def api_get_portrait(name: str):
    path = _find_portrait_file(name)
    if path is None:
        raise HTTPException(status_code=404, detail="无自定义立绘")
    return FileResponse(path)


if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
