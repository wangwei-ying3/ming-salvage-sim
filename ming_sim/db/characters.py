"""characters / character_offices / offices / skill_grants / consort_traits：身份、任免、史实登离场、调教、技能授权。

_CharactersMixin：拆自原 db.py，方法体逐字未改。"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from ming_sim.assets import format_money, format_money_delta
from ming_sim.constants import (
    ARMY_FIELD_ALIASES, ARMY_FIELD_LABELS, ARMY_QUANTITY_FIELDS, ARMY_SCORE_FIELDS, ARMY_TEXT_FIELDS,
    BUILDING_CATEGORIES, BUILDING_FIELD_LABELS, BUILDING_OUTPUT_METRICS,
    BUILDING_QUANTITY_FIELDS, BUILDING_SCORE_FIELDS, BUILDING_TEXT_FIELDS,
    ECONOMY_ACCOUNTS, POWER_FIELD_LABELS, POWER_SCORE_FIELDS,
    POWER_FIELD_ALIASES, POWER_TEXT_FIELDS, MONEY_UNIT, REGION_FIELD_LABELS, REGION_QUANTITY_FIELDS,
    FISCAL_SCORE_FIELDS, REGION_FIELD_ALIASES, REGION_SCORE_FIELDS, REGION_TEXT_FIELDS, TURN_UNIT,
)
from ming_sim.content import GameContent
from ming_sim.llm_config import load_runtime_game
from ming_sim.matching import match_army_id_from_text, match_region_id_from_text
from ming_sim.models import Event, GameState, monthly_amount, period_label
from ming_sim.token_stats import tlog
from ming_sim.db._helpers import (
    normalize_office, infer_office_type_from_office,
    _compact_lookup_text, _normalize_power_id,
    COURT_OFFICE_TYPES, MINISTRY_OFFICE_TYPES,
)


class _CharactersMixin:
    def init_office_grants(self) -> None:
        """从 skills.json office_court_grants seed offices.court_grant_json（court 授权 blob）。

        版本号 `office_grant_version` 走 kv_store（铁律，见 CLAUDE.md）：
        - cur >= json：啥都不做（玩家/后台运行时改过的授权神圣不动）。
        - cur <  json：把 JSON 授权整体刷进 offices 表（无行则补建），kv_set 推版本号。
        改授权 = 改 skills.json 并升 __office_grant_version；运行时临时改 = 直接 UPDATE offices.court_grant_json。
        """
        json_ver = int(self.content.office_grant_version)
        cur = int(self.kv_get("office_grant_version") or 0)
        if cur >= json_ver:
            return
        for office_type, grant in self.content.office_court_grants.items():
            blob = json.dumps(grant, ensure_ascii=False)
            exists = self.conn.execute(
                "SELECT 1 FROM offices WHERE office_type = ?", (office_type,)
            ).fetchone()
            if exists:
                self.conn.execute(
                    "UPDATE offices SET court_grant_json = ? WHERE office_type = ?",
                    (blob, office_type),
                )
            else:
                self.conn.execute(
                    "INSERT INTO offices (office_type, skills, tools, authority_scope, power, responsibility, corruption_risk, court_grant_json) "
                    "VALUES (?, '[]', '[]', '', 50, 50, 30, ?)",
                    (office_type, blob),
                )
        self.kv_set("office_grant_version", str(json_ver))
        self.conn.commit()

    def get_office_court_grant(self, office_type: str) -> Dict[str, object]:
        """读 offices.court_grant_json（court 授权 blob）。无行/空返回 {}。
        court tool 挂载 / agno skill 注入 / 前端 chip 全走此入口，不读 content（DB 是运行时唯一真相）。"""
        row = self.conn.execute(
            "SELECT court_grant_json FROM offices WHERE office_type = ?", (office_type,)
        ).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["court_grant_json"] or "{}")
        except (ValueError, TypeError):
            return {}

    def set_character_status(
        self,
        state: GameState,
        name: str,
        status: str,
        reason: str = "",
    ) -> None:
        """改人物状态：active/offstage/dismissed/imprisoned/exiled/retired/dead。
        大臣走 characters 表；后宫（consorts）走内存对象 + consort_traits 备档。"""
        valid = {"active", "offstage", "dismissed", "imprisoned", "exiled", "retired", "dead"}
        if status not in valid:
            raise ValueError(f"character status 非法：{status}")
        # 去职（下狱/革职/流放/致仕/死）即削职：清空 characters.office 与 office_type，
        # 原职仍留在 character_offices 备档可追溯。复职（active/offstage）不动职。
        # 归属看 power_id（仍是 ming），起复授官不受 office_type 清空影响。
        ousted = status in {"dismissed", "imprisoned", "exiled", "retired", "dead"}
        if ousted:
            self.conn.execute(
                "UPDATE characters SET status=?, status_reason=?, status_changed_turn=?, office='', office_type='' WHERE name=?",
                (status, reason[:200], state.turn, name),
            )
        else:
            self.conn.execute(
                "UPDATE characters SET status=?, status_reason=?, status_changed_turn=? WHERE name=?",
                (status, reason[:200], state.turn, name),
            )
        self.commit()

    def get_character_status(self, name: str) -> Tuple[str, str]:
        row = self.conn.execute(
            "SELECT status, status_reason FROM characters WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            return ("active", "")
        return (row["status"], row["status_reason"] or "")

    def apply_character_power_changes(
        self,
        changes: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        """据 extractor 输出改人物 power_id（降将/叛臣/归正）。new_power 须为合法 power id。"""
        applied: List[Dict[str, object]] = []
        if not isinstance(changes, list):
            return applied
        valid_powers = {r["id"] for r in self.conn.execute("SELECT id FROM powers").fetchall()}
        for raw in changes:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or raw.get("姓名") or "").strip()
            new_power = _normalize_power_id(self.conn, raw.get("new_power") or raw.get("新势力") or raw.get("归属") or "")
            reason = str(raw.get("reason") or raw.get("原因") or "")[:120]
            if not name or not new_power:
                print(f"[WARN] character_power_changes 缺 name/new_power → 跳过: {raw}")
                continue
            if new_power not in valid_powers:
                print(f"[WARN] character_power_changes new_power '{new_power}' 未在 powers → 跳过 {name}")
                continue
            row = self.conn.execute(
                "SELECT power_id FROM characters WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                print(f"[WARN] character_power_changes 人物 '{name}' 未入库 → 跳过")
                continue
            old_power = row["power_id"] or "ming"
            if old_power == new_power:
                continue
            self.conn.execute(
                "UPDATE characters SET power_id = ? WHERE name = ?",
                (new_power, name),
            )
            applied.append({"name": name, "old_power": old_power, "new_power": new_power, "reason": reason})
        self.commit()
        return applied

    def set_character_office(
        self,
        name: str,
        office: str,
        office_type: str = "",
        source: str = "诏书调任",
    ) -> None:
        """既有官员调任/升迁：改 characters.office（office_type 给空则不动），
        同步 character_offices 备档。状态不变（仍 active）。"""
        office = normalize_office(office)
        # 归属判据看 power_id，不看 office_type——去职者 office_type 已清空，
        # 但仍是大明臣属（power_id='ming'），起复授官不应被误拒。
        row = self.conn.execute(
            "SELECT office_type FROM characters WHERE name=? AND power_id='ming'", (name,)
        ).fetchone()
        if row is None:
            raise ValueError(f"{name}不属大明朝廷，不能授予大明官职")
        current_type = row["office_type"] or ""
        eff_type = infer_office_type_from_office(office, office_type or current_type)
        if office_type or eff_type != current_type:
            self.conn.execute(
                "UPDATE characters SET office=?, office_type=? WHERE name=?",
                (office, eff_type, name),
            )
        else:
            self.conn.execute(
                "UPDATE characters SET office=? WHERE name=?",
                (office, name),
            )
        self.conn.execute(
            """
            INSERT INTO character_offices (character_name, office_title, office_type, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(character_name) DO UPDATE SET
                office_title = excluded.office_title,
                office_type = excluded.office_type,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (name, office, eff_type, source),
        )
        self.commit()
        if name in self.content.characters:
            self.content.characters[name].office = office
            self.content.characters[name].office_type = eff_type

    def apply_historical_deaths(self, state: GameState) -> List[Dict[str, str]]:
        """月初 tick：只有仍 active 的人到点自然死。被玩家提前罢/狱/流/杀的不走此分支。
        只打讣闻、改 status=dead，不动派系/metric。是否升级 issue 由 LLM 看本月邸报判断。
        返回 [{name, office, faction}] 喂给 simulator 当月上下文。
        """
        rows = self.conn.execute(
            """SELECT name, office, faction, historical_death_year, historical_death_month
               FROM characters
               WHERE status = 'active' AND historical_death_year > 0"""
        ).fetchall()
        died: List[Dict[str, str]] = []
        for r in rows:
            year = int(r["historical_death_year"])
            month = int(r["historical_death_month"] or 0)
            triggered = state.year > year or (
                state.year == year and (month == 0 or state.period >= month)
            )
            if not triggered:
                continue
            name = r["name"]
            self.set_character_status(state, name, "dead", f"历史卒于 {year}年{month or '?'}月")
            died.append({
                "name": name,
                "office": r["office"] or "重臣",
                "faction": r["faction"] or "",
            })
        return died

    def apply_historical_debuts(self, state: GameState) -> List[Dict[str, str]]:
        """月初 tick：offstage 人物到历史登场年月，自动转 active 并发"起用"讯息。
        debut_year=0 视为开局即在场（不会处于 offstage）。
        返回 [{name, office, faction}] 喂给 simulator 当月上下文，由 LLM 写进邸报。
        """
        rows = self.conn.execute(
            """SELECT name, office, faction, debut_year, debut_month
               FROM characters
               WHERE status = 'offstage' AND debut_year > 0"""
        ).fetchall()
        debuted: List[Dict[str, str]] = []
        for r in rows:
            year = int(r["debut_year"])
            month = int(r["debut_month"] or 0)
            triggered = state.year > year or (
                state.year == year and (month == 0 or state.period >= month)
            )
            if not triggered:
                continue
            name = r["name"]
            self.set_character_status(state, name, "active", f"历史登场 {year}年{month or '?'}月")
            debuted.append({
                "name": name,
                "office": r["office"] or "重臣",
                "faction": r["faction"] or "",
            })
        return debuted

    def apply_historical_power_renames(self, state: GameState) -> List[Dict[str, object]]:
        """月初 tick：历史国号/称谓变化。稳定 id 不变，只改展示名与别名。"""
        changes: List[Dict[str, object]] = []
        if state.year > 1636 or (state.year == 1636 and state.period >= 4):
            changed = self.apply_power_rename(
                state,
                "houjin",
                "大清",
                aliases="后金，清，大清",
                reason="皇太极称帝，改国号大清",
                status="皇太极称帝改国号大清，建元崇德，整合满蒙汉诸部南向争明",
                last_action="皇太极称帝改国号大清",
            )
            if changed:
                changes.append(changed)
        return changes

    # ── 后宫调教 ──────────────────────────────────────────────────────────

    def get_consort_traits(self, name: str) -> dict:
        """返回 {extra_skills: [...], extra_traits: [...]}，不存在时返回空。"""
        row = self.conn.execute(
            "SELECT extra_skills, extra_traits FROM consort_traits WHERE name=?", (name,)
        ).fetchone()
        if not row:
            return {"extra_skills": [], "extra_traits": []}
        skills = [s.strip() for s in row["extra_skills"].split("，") if s.strip()]
        traits = [t.strip() for t in row["extra_traits"].split("，") if t.strip()]
        return {"extra_skills": skills, "extra_traits": traits}

    def cultivate_consort(self, name: str, turn: int, skill: str = "", trait: str = "") -> dict:
        """追加技能或性格词，去重后持久化。返回最新值。"""
        current = self.get_consort_traits(name)
        skills = current["extra_skills"]
        traits = current["extra_traits"]
        if skill and skill not in skills:
            skills.append(skill)
        if trait and trait not in traits:
            traits.append(trait)
        self.conn.execute(
            """INSERT INTO consort_traits(name, extra_skills, extra_traits, updated_turn)
               VALUES(?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 extra_skills=excluded.extra_skills,
                 extra_traits=excluded.extra_traits,
                 updated_turn=excluded.updated_turn,
                 updated_at=CURRENT_TIMESTAMP""",
            (name, "，".join(skills), "，".join(traits), turn),
        )
        self.conn.commit()
        return {"extra_skills": skills, "extra_traits": traits}

    def next_pool_portrait_id(self, prefix: str = "minister_pool_") -> str:
        """分配下一个预设头像 ID（顺序递增，不循环）。
        minister_pool: 60 个槽；consort_pool: 20 个槽。
        实际可用槽位 = web/public/portraits/<prefix><N>.png 真存在的编号集合（中途删图会跳过缺号）。
        全部用完后再回退到递增（前端 onError fallback 占位符）。"""
        rows = self.conn.execute(
            "SELECT portrait_id FROM characters WHERE portrait_id LIKE ?",
            (prefix + "%",),
        ).fetchall()
        used = set()
        for r in rows:
            try:
                used.add(int(r["portrait_id"].replace(prefix, "")))
            except ValueError:
                pass
        # 扫真实存在的图编号（frozen 模式走 _MEIPASS，源码走 <repo>/web/public/portraits）
        from pathlib import Path
        from ming_sim.paths import bundled_path
        portraits_dir = Path(bundled_path("web", "public", "portraits"))
        available: set[int] = set()
        if portraits_dir.is_dir():
            for p in portraits_dir.glob(f"{prefix}*.png"):
                suffix = p.stem[len(prefix):]
                if suffix.isdigit():
                    available.add(int(suffix))
        free = sorted(available - used)
        if free:
            return f"{prefix}{free[0]}"
        # 真实图全用完：递增分配，但跳过已知中途缺号（如手动删过的 consort_pool_14）。
        # 编号上限：available 最大值 + 缺号集；超出后继续递增（前端 onError fallback 占位符）。
        max_known = max(available, default=0)
        missing = {n for n in range(1, max_known + 1) if n not in available}
        n = 1
        while n in used or n in missing:
            n += 1
        return f"{prefix}{n}"

    def random_portrait_id_from_folder(self, folder: str, fallback_prefix: str = "minister_pool_") -> str:
        """从 web/public/portraits/<folder>/ 随机分配通用立绘。

        大臣池使用目录池（如 minister_pool/foo.png -> portrait_id=minister_pool/foo）；
        历史人物专属 minister_<姓名>.png 不参与随机。优先不重复，池图用完后允许复用。
        """
        from pathlib import Path
        from ming_sim.paths import bundled_path

        folder = folder.strip().strip("/")
        if not folder:
            return self.next_pool_portrait_id(fallback_prefix)

        portraits_dir = Path(bundled_path("web", "public", "portraits", folder))
        files = sorted(p for p in portraits_dir.glob("*.png") if p.is_file()) if portraits_dir.is_dir() else []
        if not files:
            return self.next_pool_portrait_id(fallback_prefix)

        rows = self.conn.execute(
            "SELECT portrait_id FROM characters WHERE portrait_id LIKE ?",
            (folder + "/%",),
        ).fetchall()
        used = {str(r["portrait_id"]) for r in rows}
        all_ids = [f"{folder}/{p.stem}" for p in files]
        free = [pid for pid in all_ids if pid not in used]
        return random.choice(free or all_ids)

    def set_portrait_id(self, name: str, portrait_id: str) -> None:
        """改某人物的头像标识（如皇帝上传自定义立绘后落库）。"""
        self.conn.execute(
            "UPDATE characters SET portrait_id=? WHERE name=?",
            (portrait_id, name),
        )
        self.conn.commit()

    def add_character(self, state: GameState, character: "Character", source: str = "") -> None:
        """运行时新建人物（吏部任命/皇帝点名）。已存在同名则不动，避免覆盖既有状态。"""
        existing = self.conn.execute(
            "SELECT name FROM characters WHERE name=?", (character.name,)
        ).fetchone()
        if existing is not None:
            return
        character.office = normalize_office(character.office)
        character.office_type = infer_office_type_from_office(character.office, character.office_type)
        if character.office_type != "后宫":
            character_limit = int(load_runtime_game().get("character_limit", 120))
            current_count = int(self.conn.execute(
                "SELECT COUNT(*) FROM characters WHERE archived = 0 AND office_type <> '后宫'"
            ).fetchone()[0] or 0)
            if current_count >= character_limit:
                raise ValueError(
                    f"本局朝臣人物已达上限 {character_limit} 人；朝臣越多，大臣名册与推演上下文 token 消耗越高。"
                    "可在游戏设置中调高人物上限，或先归档非在朝的运行时朝臣。"
                )
        # 若没有专属 portrait_id，按 office_type 分配预设池头像
        portrait_id = character.portrait_id
        if not portrait_id:
            if character.office_type == "后宫":
                # 后宫沿用编号池 consort_pool_<N>.png。
                portrait_id = self.next_pool_portrait_id("consort_pool_")
            else:
                # 大臣使用目录随机池 web/public/portraits/minister_pool/*.png。
                portrait_id = self.random_portrait_id_from_folder("minister_pool")
        source_label = source or ("吏部铨选任命" if character.office_type != "后宫" else "诏书纳妃")
        office_source = source or ("吏部任命" if character.office_type != "后宫" else "诏书纳妃")
        self.conn.execute(
            """
            INSERT INTO characters
            (name, office, office_type, faction, aliases, personal_skills, loyalty, ability, integrity, courage, style,
             diplomacy, martial, stewardship, intrigue, learning,
             birth_year, historical_death_year, historical_death_month, debut_year, debut_month,
             status, status_reason, status_changed_turn, portrait_id, power_id, location, summary, origin, archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                character.name,
                character.office,
                character.office_type,
                character.faction,
                json.dumps(character.aliases, ensure_ascii=False),
                json.dumps(character.personal_skills, ensure_ascii=False),
                character.loyalty,
                character.ability,
                character.integrity,
                character.courage,
                character.style,
                character.diplomacy,
                character.martial,
                character.stewardship,
                character.intrigue,
                character.learning,
                character.birth_year,
                character.historical_death_year,
                character.historical_death_month,
                character.debut_year,
                character.debut_month,
                character.status,
                source_label,
                state.turn,
                portrait_id,
                getattr(character, "power_id", "ming") or "ming",
                getattr(character, "location", "") or "",
                getattr(character, "summary", "") or "",
                "runtime",
                0,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO character_offices (character_name, office_title, office_type, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(character_name) DO UPDATE SET
                office_title = excluded.office_title,
                office_type = excluded.office_type,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (character.name, character.office, character.office_type, office_source),
        )
        self.commit()

    def archive_runtime_character(self, state: GameState, name: str) -> Dict[str, object]:
        """归档运行时人物：保留 DB 记录与历史，但从正式名册/推演中移除。"""
        row = self.conn.execute(
            "SELECT name, status, origin, archived FROM characters WHERE name=?",
            (name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"未找到人物：{name}")
        if int(row["archived"] or 0):
            return {"archived": True, "name": name}
        if str(row["origin"] or "preset") == "preset":
            raise ValueError("预设人物不能归档。")
        if str(row["status"] or "active") == "active":
            raise ValueError("在朝人物不能归档，请先罢黜、下狱、流放或致仕。")
        self.conn.execute(
            """
            UPDATE characters
            SET archived=1,
                status_reason=CASE
                    WHEN trim(status_reason) <> '' THEN status_reason || '；已归档，不再进入推演'
                    ELSE '已归档，不再进入推演'
                END,
                status_changed_turn=?
            WHERE name=?
            """,
            (state.turn, name),
        )
        self.conn.execute(
            "UPDATE secret_orders SET status='cancelled', turn_closed=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE minister_name=? AND status IN ('active', 'pending_review')",
            (state.turn, name),
        )
        self.conn.commit()
        return {"archived": True, "name": name}

    def restore_archived_character(self, name: str) -> Dict[str, object]:
        """恢复已归档的运行时人物，使其重新进入正式名册/推演。"""
        row = self.conn.execute(
            "SELECT name, origin, archived, office_type FROM characters WHERE name=?",
            (name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"未找到人物：{name}")
        if str(row["origin"] or "preset") == "preset":
            raise ValueError("预设人物不能执行归档恢复。")
        if not int(row["archived"] or 0):
            return {"archived": False, "name": name}
        if str(row["office_type"] or "") != "后宫":
            character_limit = int(load_runtime_game().get("character_limit", 120))
            current_count = int(self.conn.execute(
                "SELECT COUNT(*) FROM characters WHERE archived = 0 AND office_type <> '后宫'"
            ).fetchone()[0] or 0)
            if current_count >= character_limit:
                raise ValueError(
                    f"本局朝臣人物已达上限 {character_limit} 人；恢复后也会进入名册与推演上下文。"
                    "请先归档其他运行时朝臣，或在游戏设置中调高人物上限。"
                )
        self.conn.execute(
            """
            UPDATE characters
            SET archived=0,
                status_reason=replace(replace(status_reason, '；已归档，不再进入推演', ''), '已归档，不再进入推演', '')
            WHERE name=?
            """,
            (name,),
        )
        self.conn.commit()
        return {"archived": False, "name": name}

    def grant_skill(self, state: GameState, character_name: str, skill_id: str, granted_by: str = "皇帝") -> bool:
        exists = self.conn.execute(
            """
            SELECT 1 FROM skill_grants
            WHERE character_name = ? AND skill_id = ? AND active = 1
            LIMIT 1
            """,
            (character_name, skill_id),
        ).fetchone()
        if exists:
            return False
        self.conn.execute(
            """
            INSERT INTO skill_grants (character_name, skill_id, granted_by, source_turn, active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (character_name, skill_id, granted_by, state.turn),
        )
        self.conn.commit()
        return True

    def revoke_skill(self, character_name: str, skill_id: str) -> bool:
        cursor = self.conn.execute(
            """
            UPDATE skill_grants
            SET active = 0
            WHERE character_name = ? AND skill_id = ? AND active = 1
            """,
            (character_name, skill_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def active_skill_grants(self, character_name: str) -> List[str]:
        rows = self.conn.execute(
            """
            SELECT skill_id FROM skill_grants
            WHERE character_name = ? AND active = 1
            ORDER BY id
            """,
            (character_name,),
        ).fetchall()
        return [str(row["skill_id"]) for row in rows]
