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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ming_sim.constants import ROOT_DIR
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
from ming_sim.skills import available_skill_ids, skill_display_name, skill_source_labels
from ming_sim.context import match_minister_from_text
from ming_sim.exceptions import LLMContractError  # noqa: F401  (保留：供错误处理)
from ming_sim.models import Character, LLMConfig, monthly_amount

WEB_DIST = os.path.join(ROOT_DIR, "web", "dist")


class ChatRequest(BaseModel):
    message: str


class DirectiveRequest(BaseModel):
    text: str
    notes: str = ""


class DirectivePatch(BaseModel):
    text: Optional[str] = None
    notes: Optional[str] = None


class WebGame:
    """Web 端会话包装：持一个 GameSession + 网页专属态（聊天历史、收藏）。"""

    def __init__(self) -> None:
        db_path = os.environ.get("MING_SIM_DB", "data/ming_sim.db")
        # 相对路径锚到项目根，避免 uvicorn 工作目录不同导致连错（甚至新建空）DB。
        if not os.path.isabs(db_path):
            db_path = os.path.join(ROOT_DIR, db_path)
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        api_key = os.environ.get("OPENAI_API_KEY", "")
        # 菜单写的 runtime_llm.json 优先于 env，让"在网页里改的配置"重启后仍生效。
        runtime = load_runtime_llm()
        base_url = runtime.get("base_url") or base_url
        model = runtime.get("model") or model
        api_key = runtime.get("api_key") or api_key
        random.seed(int(os.environ.get("MING_SIM_SEED", "7")))
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        llm_config = load_llm_config(base_url, model, api_key)
        self.session = GameSession(db_path, llm_config)
        self.session.begin_turn()
        # 召对记录持久化在 chat_messages 表，启动时恢复进内存缓存。
        self.chat_history: Dict[str, List[Dict[str, str]]] = {
            name: [] for name in self.session.content.characters
        }
        for name, msgs in self.db.load_all_chat_history().items():
            self.chat_history.setdefault(name, []).extend(msgs)
        self.favorites: set = set()

    # ── 存档管理 ─────────────────────────────────────────────────────────
    def saves_dir(self) -> str:
        path = os.path.join(ROOT_DIR, "data", "saves")
        os.makedirs(path, exist_ok=True)
        return path

    def list_saves(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for fname in sorted(os.listdir(self.saves_dir())):
            if not fname.endswith(".db"):
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

    def reset_game(self) -> Dict[str, Any]:
        """一键重置：清空所有存档 + 主数据库，重建全新开局"""
        import shutil
        # 关闭当前 session 连接
        try:
            self.session.close()
        except Exception:
            pass
        # 删除所有存档文件
        saves_path = self.saves_dir()
        if os.path.isdir(saves_path):
            for f in os.listdir(saves_path):
                if f.endswith('.db'):
                    try:
                        os.remove(os.path.join(saves_path, f))
                    except Exception:
                        pass
        # 删除主数据库
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                shutil.rmtree(self.db_path, ignore_errors=True)
        # 重新初始化
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._rebuild_session(self.session.llm_config)
        return {"message": "游戏已重置", "state": self.state_payload()}

    def _rebuild_session(self, llm_config: LLMConfig) -> None:
        """用新 llm_config（或换完 DB 后）重建 GameSession + 内存缓存。"""
        verify_llm_available(llm_config)
        self.session = GameSession(self.db_path, llm_config)
        self.session.begin_turn()
        self.chat_history = {name: [] for name in self.session.content.characters}
        for name, msgs in self.db.load_all_chat_history().items():
            self.chat_history.setdefault(name, []).extend(msgs)
        self.favorites = set()

    def apply_llm_config(self, base_url: str, model: str, api_key: str) -> LLMConfig:
        base = normalize_openai_base_url(base_url.strip() or self.session.llm_config.base_url)
        new_model = model.strip() or self.session.llm_config.model
        new_key = api_key.strip() or self.session.llm_config.api_key
        new_config = LLMConfig(api_key=new_key, base_url=base, model=new_model)
        verify_llm_available(new_config)
        save_runtime_llm(new_config.base_url, new_config.model, new_config.api_key)
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

    # ── 序列化 ────────────────────────────────────────────────────────────
    def public_character(self, character: Character) -> Dict[str, Any]:
        return {
            "name": character.name,
            "office": character.office,
            "office_type": character.office_type,
            "faction": character.faction,
            "style": character.style,
            "summary": f"{character.office}，{character.faction}一系，行事{character.style}。",
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
            nodes.append({"id": region["id"], "kind": "region", "x": x, "y": y, "region": region, "armies": stationed, "buildings": buildings, "risk": risk})
        for node_id, (x, y) in theater_positions.items():
            stationed = [a for a in armies if self._army_belongs_to_theater(a, node_id)]
            if stationed:
                nodes.append({"id": node_id, "kind": "theater", "x": x, "y": y, "label": self._theater_label(node_id), "armies": stationed, "risk": 120})
        # 外部势力地标：纯标签，无 region/army 数据，不可点开 intel。
        external_labels = {
            "ext_jianzhou": ("建州", 82, 8), "ext_chosen": ("朝鲜", 81, 31),
            "ext_taiwan": ("台湾", 76, 70), "ext_japan": ("日本", 92, 42),
            "ext_mongol": ("蒙古", 63, 17), "ext_tibet": ("乌斯藏", 30, 52),
            "ext_jiaozhi": ("交趾", 55, 79),
        }
        for node_id, (label, x, y) in external_labels.items():
            nodes.append({"id": node_id, "kind": "external", "x": x, "y": y, "label": label, "armies": [], "risk": 0})
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
        land_base = self.db.conn.execute("SELECT SUM(tax_per_turn) FROM regions").fetchone()[0] or 0
        army_total = self.db.conn.execute("SELECT SUM(maintenance_per_turn) FROM armies").fetchone()[0] or 0

        def rated(base: int, rate_key: str) -> int:
            return monthly_amount(round(int(base) * cfg.get(rate_key, 100) / 100))

        budget = {
            "国库": {
                "balance": int(self.state.metrics["国库"]),
                "income": [
                    {"name": "田赋", "amount": rated(int(land_base), "田赋_rate"), "note": "两京十三省田赋实收"},
                    {"name": "辽饷", "amount": rated(cfg.get("辽饷_base", 130), "辽饷_rate"), "note": "辽东专项加派实收"},
                    {"name": "盐税", "amount": rated(cfg.get("盐税_base", 55), "盐税_rate"), "note": "两淮两浙盐引定额"},
                    {"name": "商税", "amount": rated(cfg.get("商税_base", 8), "商税_rate"), "note": "各地关卡店税汇总"},
                ],
                "expense": [
                    {"name": "各军军饷", "amount": monthly_amount(int(army_total)), "note": "各军月度维护/军饷合计"},
                    {"name": "宗室禄米", "amount": rated(cfg.get("宗室禄米_base", 80), "宗室禄米_rate"), "note": "诸藩宗室月禄米"},
                    {"name": "百官俸禄", "amount": rated(cfg.get("官俸_base", 35), "官俸_rate"), "note": "在京百官月俸禄"},
                    {"name": "工部", "amount": rated(cfg.get("工程_base", 22), "工程_rate"), "note": "工部月维护支出"},
                    {"name": "赈灾备用", "amount": rated(cfg.get("赈灾_base", 25), "赈灾_rate"), "note": "制度性赈灾预留"},
                    {"name": "九边补给", "amount": rated(cfg.get("九边补给_base", 130), "九边补给_rate"), "note": "九边月粮草补给"},
                ],
            },
            "内库": {
                "balance": int(self.state.metrics["内库"]),
                "income": [
                    {"name": "皇庄", "amount": rated(cfg.get("皇庄_base", 18), "皇庄_rate"), "note": "皇庄地租月上缴"},
                    {"name": "织造", "amount": rated(cfg.get("织造_base", 12), "织造_rate"), "note": "苏杭织造局月上缴"},
                    {"name": "矿税", "amount": rated(cfg.get("矿税_base", 5), "矿税_rate"), "note": "矿税残余"},
                ],
                "expense": [
                    {"name": "宫廷开支", "amount": rated(cfg.get("宫廷_base", 18), "宫廷_rate"), "note": "皇室日常用度"},
                    {"name": "内廷俸禄", "amount": rated(cfg.get("内廷俸_base", 12), "内廷俸_rate"), "note": "太监宫女俸禄"},
                    {"name": "妃嫔供奉", "amount": rated(cfg.get("妃嫔_base", 8), "妃嫔_rate"), "note": "后宫妃嫔月供奉"},
                ],
            },
        }
        for account in budget.values():
            income_total = sum(int(item["amount"]) for item in account["income"])
            expense_total = sum(int(item["amount"]) for item in account["expense"])
            account["income_total"] = income_total
            account["expense_total"] = expense_total
            account["net"] = income_total - expense_total
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
            "external_power_warning": self.db.external_power_report(),
            "external_powers": self.db.external_power_payload(),
            "victory_status": self.session.victory(),
            "events": [],
            "regions": self.db.region_payload(),
            "armies": self.db.army_payload(),
            "map_nodes": self.map_nodes(),
            "ministers": [self.public_character(c) for c in self.content.characters.values()],
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
    ) -> Dict[str, Any]:
        character = self.content.characters[minister_name]
        self.chat_history[minister_name].append({"role": "minister", "content": answer})
        self.db.append_chat_message(minister_name, self.state.turn, "minister", answer)
        return {
            "minister": minister_name,
            "answer": answer,
            "history": self.chat_history[minister_name],
            "court_action": court_action,
            "next_minister": next_minister,
            "proposed_directive": proposed_directive,
            "directives": [self.directive_payload(row) for row in self.directive_rows()],
            "suggestions": self.suggestions_for(character),
        }

    def chat(self, minister_name: str, message: str) -> Dict[str, Any]:
        if minister_name not in self.content.characters:
            raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
        text = message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="问话不能为空。")
        self.chat_history.setdefault(minister_name, []).append({"role": "user", "content": text})
        self.db.append_chat_message(minister_name, self.state.turn, "user", text)
        result = self.session.chat(minister_name, text)
        proposed = None
        if result.proposed_directive is not None:
            d = result.proposed_directive
            proposed = {"id": d.id, "text": d.text, "status": d.status, "notes": d.notes}
        return self._chat_payload(
            minister_name, result.answer,
            court_action=result.court_action, next_minister=result.next_minister,
            proposed_directive=proposed,
        )

    def chat_stream(self, minister_name: str, message: str) -> Iterator[Dict[str, Any]]:
        if minister_name not in self.content.characters:
            yield {"type": "error", "message": f"未找到大臣：{minister_name}"}
            return
        text = message.strip()
        if not text:
            yield {"type": "error", "message": "问话不能为空。"}
            return
        self.chat_history.setdefault(minister_name, []).append({"role": "user", "content": text})
        self.db.append_chat_message(minister_name, self.state.turn, "user", text)
        character = self.content.characters[minister_name]
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
            # 截 propose_directive：入 pending
            proposed = None
            if run_output is not None:
                for tool_exec in getattr(run_output, "tools", None) or []:
                    res = str(getattr(tool_exec, "result", "") or "")
                    if res.startswith("__pending_directive__"):
                        draft_text = res.removeprefix("__pending_directive__").strip()
                        if not draft_text:
                            args = getattr(tool_exec, "tool_args", {}) or {}
                            draft_text = (args.get("decree_text") or "").strip()
                        if draft_text:
                            did = self.db.add_directive(
                                self.state, None, draft_text, "大臣拟旨",
                                notes=f"由{character.name}拟旨入档", status="pending",
                            )
                            proposed = {"id": did, "text": draft_text, "status": "pending",
                                        "notes": f"由{character.name}拟旨入档"}
            payload = self._chat_payload(minister_name, answer, proposed_directive=proposed)
            yield {"type": "done", "payload": payload}
        except Exception as error:
            yield {"type": "error", "message": str(error)}

    def suggestions_for(self, character: Character) -> List[Dict[str, str]]:
        suggestions = [
            {"label": "问在办事项", "text": "当前在办的事项里，哪几件轻重缓急最该先理？"},
            {"label": "问阻力", "text": "眼下推进朝政，最大的阻力来自哪一方？"},
            {"label": "请拟旨", "text": "替朕拟一道处理当务之急的旨。"},
        ]
        skill_ids = set(available_skill_ids(character, self.db))
        if "check_treasury" in skill_ids:
            suggestions.insert(1, {"label": "查钱粮", "text": "太仓和内库实数如何？本月哪些钱最急？"})
        if "check_military" in skill_ids or "front_line_plan" in skill_ids or "strategic_review" in skill_ids:
            suggestions.insert(1, {"label": "查驻军", "text": "查一下关宁军、京营和陕西边军的士气、欠饷与补给。"})
        if "secret_investigation" in skill_ids:
            suggestions.insert(1, {"label": "密查", "text": "哪些账册和人物最该先密查？"})
        if "mobilize_troops" in skill_ids:
            suggestions.append({"label": "登记军令", "text": f"命{character.name}整军守备，限本月以实数回奏。"})
        else:
            suggestions.append({"label": "登记指令", "text": f"命{character.name}查明当前要务实情并具实回奏。"})
        return suggestions[:6]


def sse_event(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


web_game = WebGame()
app = FastAPI(title="Ming Salvage MVP Web")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/game/state")
async def api_state() -> Dict[str, Any]:
    return web_game.state_payload()


@app.get("/api/turn_extraction")
async def api_turn_extraction(turn: int = -1) -> Dict[str, Any]:
    """读 turn_extractions：默认上一回合（state.turn-1，因 resolve 已 next_period）。"""
    if turn < 0:
        turn = max(1, int(web_game.state.turn) - 1)
    data = web_game.db.get_turn_extraction(turn)
    if data is None:
        return {"turn": turn, "exists": False}
    data["exists"] = True
    return data


@app.get("/api/history/turns")
async def api_history_turns() -> Dict[str, Any]:
    """已存档回合列表（turn_reports / turn_extractions / 已颁诏 turn_directives 并集）。"""
    return {"turns": web_game.db.list_archived_turns()}


@app.get("/api/history/turn/{turn}")
async def api_history_turn(turn: int) -> Dict[str, Any]:
    """某回合历史聚合：邸报奏报 + 诏书 + 已颁草案 + extractor 输入/输出。"""
    db = web_game.db
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
    return {"nodes": web_game.map_nodes()}


@app.get("/api/buildings")
async def api_buildings(region_id: str = "") -> Dict[str, Any]:
    return {"buildings": web_game.db.building_payload(region_id)}


@app.get("/api/ministers")
async def api_ministers(group: str = "全部") -> Dict[str, Any]:
    ministers = [web_game.public_character(c) for c in web_game.content.characters.values()]
    if group == "内阁":
        ministers = [item for item in ministers if item["office_type"] == "内阁"]
    elif group == "六部":
        ministers = [item for item in ministers if item["office_type"] in {"吏部", "户部", "礼部", "兵部", "刑部", "工部"}]
    elif group == "收藏":
        ministers = [item for item in ministers if item["name"] in web_game.favorites]
    return {"group": group, "ministers": ministers}


@app.post("/api/favorites/{minister_name}")
async def api_add_favorite(minister_name: str) -> Dict[str, Any]:
    if minister_name not in web_game.content.characters:
        raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
    web_game.favorites.add(minister_name)
    return {"favorites": sorted(web_game.favorites)}


@app.delete("/api/favorites/{minister_name}")
async def api_remove_favorite(minister_name: str) -> Dict[str, Any]:
    web_game.favorites.discard(minister_name)
    return {"favorites": sorted(web_game.favorites)}


@app.get("/api/ministers/{minister_name}/chat")
async def api_chat_history(minister_name: str) -> Dict[str, Any]:
    if minister_name not in web_game.content.characters:
        raise HTTPException(status_code=404, detail=f"未找到大臣：{minister_name}")
    character = web_game.content.characters[minister_name]
    return {
        "minister": web_game.public_character(character),
        "history": web_game.chat_history.get(minister_name, []),
        "suggestions": web_game.suggestions_for(character),
    }


@app.post("/api/ministers/{minister_name}/chat")
async def api_chat(minister_name: str, request: ChatRequest) -> Dict[str, Any]:
    return web_game.chat(minister_name, request.message)


@app.post("/api/ministers/{minister_name}/chat/stream")
async def api_chat_stream(minister_name: str, request: ChatRequest) -> StreamingResponse:
    async def generate() -> AsyncIterator[str]:
        for item in web_game.chat_stream(minister_name, request.message):
            item_type = str(item.get("type", "message"))
            if item_type == "delta":
                yield sse_event("delta", {"content": item.get("content", "")})
            elif item_type == "done":
                yield sse_event("done", item.get("payload", {}))
            elif item_type == "error":
                yield sse_event("error", {"message": item.get("message", "流式回复失败。")})
            await asyncio.sleep(0)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/directives")
async def api_create_directive(request: DirectiveRequest) -> Dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空。")
    dv = web_game.session.add_directive(request.text.strip(), notes=request.notes)
    return {
        "directive": {"id": dv.id, "text": dv.text, "status": dv.status},
        "directives": [web_game.directive_payload(item) for item in web_game.directive_rows()],
    }


@app.patch("/api/directives/{directive_id}")
async def api_update_directive(directive_id: int, request: DirectivePatch) -> Dict[str, Any]:
    rows = web_game.directive_rows()
    row = next((item for item in rows if int(item["id"]) == directive_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到草案。")
    text = request.text if request.text is not None else str(row["text"])
    if not text.strip():
        raise HTTPException(status_code=400, detail="指令内容不能为空。")
    web_game.session.update_directive(directive_id, text.strip())
    return {"directives": [web_game.directive_payload(item) for item in web_game.directive_rows()]}


@app.delete("/api/directives/{directive_id}")
async def api_delete_directive(directive_id: int) -> Dict[str, Any]:
    web_game.session.delete_directive(directive_id)
    return {"directives": [web_game.directive_payload(item) for item in web_game.directive_rows()]}


@app.post("/api/directives/{directive_id}/confirm")
async def api_confirm_directive(directive_id: int) -> Dict[str, Any]:
    """大臣拟旨经皇帝核定：pending → draft。"""
    web_game.session.confirm_directive(directive_id)
    return {
        "directives": [web_game.directive_payload(item) for item in web_game.directive_rows()],
        "pending_count": web_game.session.pending_count(),
    }


@app.post("/api/directives/{directive_id}/reject")
async def api_reject_directive(directive_id: int) -> Dict[str, Any]:
    """皇帝驳回大臣拟旨：pending → rejected。"""
    web_game.session.reject_directive(directive_id)
    return {
        "directives": [web_game.directive_payload(item) for item in web_game.directive_rows()],
        "pending_count": web_game.session.pending_count(),
    }


@app.post("/api/decree/write")
async def api_write_decree() -> Dict[str, Any]:
    try:
        decree = web_game.session.write_decree()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"decree": decree}


@app.post("/api/decree/issue")
async def api_issue_decree() -> Dict[str, Any]:
    """非流式颁诏（保留兼容）。前端默认走 /api/decree/issue/stream。"""
    try:
        report = web_game.session.resolve_turn()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    decree = web_game.session.last_decree
    web_game.refresh_turn()
    return {"decree": decree, "report": report, "state": web_game.state_payload()}


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
            report = web_game.session.resolve_turn(on_event=on_event)
            decree = web_game.session.last_decree
            web_game.refresh_turn()
            ev_queue.put(("__done__", {
                "decree": decree,
                "report": report,
                "state": web_game.state_payload(),
            }))
        except ValueError as e:
            ev_queue.put(("__error__", str(e)))
        except Exception as e:  # noqa: BLE001
            ev_queue.put(("__error__", str(e)))

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
                yield sse_event("error", {"message": data})
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


@app.get("/api/saves")
async def api_list_saves() -> Dict[str, Any]:
    return {"saves": web_game.list_saves()}


@app.post("/api/saves")
async def api_create_save(request: SaveCreateRequest) -> Dict[str, Any]:
    info = web_game.save_to(request.name)
    return {"save": info, "saves": web_game.list_saves()}


@app.delete("/api/saves/{name}")
async def api_delete_save(name: str) -> Dict[str, Any]:
    web_game.delete_save(name)
    return {"saves": web_game.list_saves()}


@app.post("/api/saves/{name}/load")
async def api_load_save(name: str) -> Dict[str, Any]:
    web_game.load_save(name)
    return {"state": web_game.state_payload()}


@app.post("/api/game/reset")
async def api_reset_game() -> Dict[str, Any]:
    try:
        return web_game.reset_game()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重置失败：{e}")


@app.get("/api/llm/config")
async def api_get_llm_config() -> Dict[str, Any]:
    """读当前生效的 LLM 配置。api_key 不回传明文，只回是否已设置。"""
    cfg = web_game.session.llm_config
    saved = load_runtime_llm()
    return {
        "base_url": cfg.base_url,
        "model": cfg.model,
        "has_api_key": bool(cfg.api_key),
        "persisted": {
            "base_url": saved.get("base_url", ""),
            "model": saved.get("model", ""),
            "has_api_key": bool(saved.get("api_key", "")),
        },
    }


@app.post("/api/llm/config")
async def api_set_llm_config(request: LLMConfigRequest) -> Dict[str, Any]:
    try:
        cfg = web_game.apply_llm_config(request.base_url, request.model, request.api_key)
    except LLMUnavailable as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"base_url": cfg.base_url, "model": cfg.model, "has_api_key": bool(cfg.api_key)}


if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
