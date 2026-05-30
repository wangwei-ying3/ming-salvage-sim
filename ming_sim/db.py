"""GameDB：所有 SQLite 持久化。L3。

init_schema 建表，seed_static_data 从 GameContent 初始化静态盘面。
GameDB 持有 self.content（GameContent），seed 类方法从中读人物/地区/军队等。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

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
from ming_sim.matching import match_army_id_from_text, match_region_id_from_text
from ming_sim.models import Event, GameState, monthly_amount, period_label
from ming_sim.token_stats import tlog


def normalize_office(office: str) -> str:
    """官职多职统一为半角逗号分隔：旧「兼/兼掌/兼署」与全角「，」「、」一律归一逗号，
    去空分项、去重、保序。是 office 字段落库的唯一规范化入口——所有写 characters.office
    的路径都过它，保证去重/顶缺时能按逗号分项匹配。"""
    s = (office or "").strip()
    if not s:
        return ""
    s = s.replace("兼掌", ",").replace("兼署", ",").replace("兼", ",")
    s = s.replace("，", ",").replace("、", ",")
    seen: set = set()
    parts: List[str] = []
    for p in (x.strip() for x in s.split(",")):
        if p and p not in seen:
            seen.add(p)
            parts.append(p)
    return ",".join(parts)


COURT_OFFICE_TYPES = {"内阁", "吏部", "户部", "礼部", "兵部", "刑部", "工部"}
MINISTRY_OFFICE_TYPES = {"吏部", "户部", "礼部", "兵部", "刑部", "工部"}


def infer_office_type_from_office(office: str, current_type: str = "") -> str:
    """用 office 文本校正 office_type，避免旧标签把无实职人物塞进内阁/六部。"""
    kind = (current_type or "").strip()
    if kind == "后宫":
        return kind
    text = normalize_office(office)
    if not text:
        return "待铨" if kind in COURT_OFFICE_TYPES or not kind else kind

    if re.search(r"内阁|大学士|首辅|次辅", text):
        return "内阁"
    for ministry in MINISTRY_OFFICE_TYPES:
        if ministry in text and re.search(r"尚书|侍郎|郎中|员外郎|主事|给事中", text):
            return ministry

    if re.search(r"司礼监|秉笔太监|掌印太监|随堂太监", text):
        return "司礼监"
    if re.search(r"东厂|提督东厂", text):
        return "东厂"
    if re.search(r"锦衣卫|北镇抚司|镇抚司|都指挥使|千户", text):
        return "锦衣卫"
    if re.search(r"都察院|都御史|御史|巡按", text):
        return "都察院"
    if re.search(r"翰林院|翰林|编修|检讨|庶吉士|詹事", text):
        return "翰林院"
    if re.search(r"总督|巡抚|布政使|按察使|参政|知府|知县|兵备道|督粮", text):
        return "地方"
    if re.search(r"督师|经略|总兵|副总兵|游击|参将|守备|山海关|辽东|蓟辽|东江|大同|宣大", text):
        return "边镇"

    return "待铨" if kind in COURT_OFFICE_TYPES or not kind else kind


class GameDB:
    def __init__(self, path: str, content: Optional[GameContent] = None):
        self.path = path
        # 静态设定来源。过渡期 content 可省略，省略时自行加载；
        # 步骤7 起由 GameSession 统一传入同一份 GameContent。
        self.content = content if content is not None else GameContent.load()
        # check_same_thread=False：流式颁诏在 worker 线程跑 resolve_turn，
        # 复用同一 GameDB 连接。游戏单写者、无并发写，跨线程安全。
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS game_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                turn INTEGER NOT NULL,
                turn_phase TEXT NOT NULL DEFAULT 'summoning'
            );

            CREATE TABLE IF NOT EXISTS metrics (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS offices (
                office_type TEXT PRIMARY KEY,
                skills TEXT NOT NULL,
                tools TEXT NOT NULL,
                authority_scope TEXT NOT NULL,
                power INTEGER NOT NULL,
                responsibility INTEGER NOT NULL,
                corruption_risk INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS characters (
                name TEXT PRIMARY KEY,
                office TEXT NOT NULL,
                office_type TEXT NOT NULL,
                faction TEXT NOT NULL,
                personal_skills TEXT NOT NULL,
                loyalty INTEGER NOT NULL,
                ability INTEGER NOT NULL,
                integrity INTEGER NOT NULL,
                courage INTEGER NOT NULL,
                style TEXT NOT NULL,
                birth_year INTEGER NOT NULL DEFAULT 0,
                historical_death_year INTEGER NOT NULL DEFAULT 0,
                historical_death_month INTEGER NOT NULL DEFAULT 0,
                debut_year INTEGER NOT NULL DEFAULT 0,
                debut_month INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                status_reason TEXT NOT NULL DEFAULT '',
                status_changed_turn INTEGER NOT NULL DEFAULT 0,
                power_id TEXT NOT NULL DEFAULT 'ming',
                location TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS character_offices (
                character_name TEXT PRIMARY KEY,
                office_title TEXT NOT NULL,
                office_type TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(character_name) REFERENCES characters(name),
                FOREIGN KEY(office_type) REFERENCES offices(office_type)
            );

            CREATE TABLE IF NOT EXISTS factions (
                name TEXT PRIMARY KEY,
                satisfaction INTEGER NOT NULL,
                leverage INTEGER NOT NULL,
                agenda TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS powers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                leader TEXT NOT NULL,
                stance TEXT NOT NULL,
                leverage INTEGER NOT NULL,
                satisfaction INTEGER NOT NULL,
                military_strength INTEGER NOT NULL,
                cohesion INTEGER NOT NULL,
                supply INTEGER NOT NULL,
                agenda TEXT NOT NULL,
                status TEXT NOT NULL,
                last_action TEXT NOT NULL DEFAULT '',
                aliases TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS power_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                power_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                delta INTEGER,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(power_id) REFERENCES powers(id)
            );

            CREATE TABLE IF NOT EXISTS power_name_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                power_id TEXT NOT NULL,
                old_name TEXT NOT NULL,
                new_name TEXT NOT NULL,
                old_aliases TEXT NOT NULL DEFAULT '',
                new_aliases TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(power_id) REFERENCES powers(id)
            );

            CREATE TABLE IF NOT EXISTS regions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                population INTEGER NOT NULL,
                public_support INTEGER NOT NULL,
                unrest INTEGER NOT NULL,
                natural_disaster TEXT NOT NULL,
                human_disaster TEXT NOT NULL,
                registered_land INTEGER NOT NULL,
                hidden_land INTEGER NOT NULL,
                tax_per_turn INTEGER NOT NULL,
                grain_security INTEGER NOT NULL,
                gentry_resistance INTEGER NOT NULL,
                military_pressure INTEGER NOT NULL,
                status TEXT NOT NULL,
                controlled_by TEXT NOT NULL DEFAULT 'ming',
                fiscal TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(controlled_by) REFERENCES powers(id)
            );

            CREATE TABLE IF NOT EXISTS region_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                region_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                delta INTEGER,
                reason TEXT NOT NULL,
                event_id TEXT,
                edict_id INTEGER,
                actor TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(region_id) REFERENCES regions(id)
            );

            CREATE TABLE IF NOT EXISTS armies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                station TEXT NOT NULL,
                theater TEXT NOT NULL,
                commander TEXT NOT NULL,
                controller TEXT NOT NULL,
                troop_type TEXT NOT NULL,
                manpower INTEGER NOT NULL,
                maintenance_per_turn INTEGER NOT NULL,
                supply INTEGER NOT NULL,
                morale INTEGER NOT NULL,
                training INTEGER NOT NULL,
                equipment INTEGER NOT NULL,
                arrears INTEGER NOT NULL,
                mobility INTEGER NOT NULL,
                loyalty INTEGER NOT NULL,
                status TEXT NOT NULL,
                owner_power TEXT NOT NULL DEFAULT 'ming',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(owner_power) REFERENCES powers(id)
            );

            CREATE TABLE IF NOT EXISTS army_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                army_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                delta INTEGER,
                reason TEXT NOT NULL,
                event_id TEXT,
                edict_id INTEGER,
                actor TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(army_id) REFERENCES armies(id)
            );

            CREATE TABLE IF NOT EXISTS buildings (
                id TEXT PRIMARY KEY,
                region_id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                level INTEGER NOT NULL,
                condition INTEGER NOT NULL,
                maintenance INTEGER NOT NULL,
                risk INTEGER NOT NULL,
                output_metric TEXT NOT NULL DEFAULT '',
                output_amount INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                origin TEXT NOT NULL DEFAULT 'preset',
                created_turn INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(region_id) REFERENCES regions(id)
            );

            CREATE TABLE IF NOT EXISTS building_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                building_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                delta INTEGER,
                reason TEXT NOT NULL,
                event_id TEXT,
                edict_id INTEGER,
                actor TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(building_id) REFERENCES buildings(id)
            );

            CREATE TABLE IF NOT EXISTS economy_accounts (
                account TEXT PRIMARY KEY,
                metric_key TEXT NOT NULL UNIQUE,
                balance INTEGER NOT NULL,
                note TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS economy_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                account TEXT NOT NULL,
                delta INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                category TEXT NOT NULL,
                reason TEXT NOT NULL,
                event_id TEXT,
                edict_id INTEGER,
                actor TEXT,
                purpose TEXT,
                target_kind TEXT,
                target_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(account) REFERENCES economy_accounts(account)
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                urgency INTEGER NOT NULL,
                severity INTEGER NOT NULL,
                credibility INTEGER NOT NULL,
                interests TEXT NOT NULL,
                audiences TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_triggers (
                event_id TEXT PRIMARY KEY,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'simulation',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );

            CREATE TABLE IF NOT EXISTS turn_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS turn_reports (
                turn INTEGER PRIMARY KEY,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                report TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- 推演链每个 agent 的原始输入/输出留痕，每回合一行，便于事后追查。
            CREATE TABLE IF NOT EXISTS turn_extractions (
                turn INTEGER PRIMARY KEY,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                decree_text TEXT NOT NULL DEFAULT '',
                narrative TEXT NOT NULL DEFAULT '',
                extractor_input TEXT NOT NULL DEFAULT '',
                extractor_output TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            -- 召对聊天记录持久化，每条消息一行，进程重启不丢。
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                minister_name TEXT NOT NULL,
                turn INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_minister
                ON chat_messages(minister_name, id);
            CREATE INDEX IF NOT EXISTS idx_chat_messages_turn
                ON chat_messages(turn);

            CREATE TABLE IF NOT EXISTS secret_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_issued INTEGER NOT NULL,
                due_turn INTEGER NOT NULL DEFAULT 0,
                year_issued INTEGER NOT NULL,
                period_issued INTEGER NOT NULL,
                minister_name TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                importance INTEGER NOT NULL DEFAULT 4,
                status TEXT NOT NULL DEFAULT 'active',
                result TEXT NOT NULL DEFAULT '',
                sim_note TEXT NOT NULL DEFAULT '',
                turn_closed INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_secret_orders_minister
                ON secret_orders(minister_name, status);
            CREATE INDEX IF NOT EXISTS idx_secret_orders_turn
                ON secret_orders(turn_issued, status);
            CREATE INDEX IF NOT EXISTS idx_secret_orders_status
                ON secret_orders(status);

            CREATE TABLE IF NOT EXISTS skill_grants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_name TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                granted_by TEXT NOT NULL,
                source_turn INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(character_name) REFERENCES characters(name)
            );

            CREATE TABLE IF NOT EXISTS emperor_skills (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tree TEXT NOT NULL,
                tier INTEGER NOT NULL,
                desc TEXT NOT NULL,
                cost INTEGER NOT NULL,
                unlock TEXT NOT NULL,
                prereq TEXT NOT NULL DEFAULT '',
                effect TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS emperor_skill_grants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id TEXT NOT NULL,
                acquired_turn INTEGER NOT NULL,
                acquired_year INTEGER NOT NULL,
                acquired_period INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(skill_id) REFERENCES emperor_skills(id)
            );

            CREATE TABLE IF NOT EXISTS turn_directives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                event_id TEXT,
                actor TEXT,
                skill_id TEXT,
                text TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id),
                FOREIGN KEY(actor) REFERENCES characters(name)
            );

            CREATE INDEX IF NOT EXISTS idx_economy_ledger_turn
            ON economy_ledger(turn, account);

            CREATE TABLE IF NOT EXISTS fiscal_config (
                key   TEXT PRIMARY KEY,
                value INTEGER NOT NULL,
                kind  TEXT NOT NULL,
                note  TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_turn_directives_turn
            ON turn_directives(turn, status);

            CREATE INDEX IF NOT EXISTS idx_region_logs_turn
            ON region_logs(turn, region_id);

            CREATE INDEX IF NOT EXISTS idx_army_logs_turn
            ON army_logs(turn, army_id);

            CREATE INDEX IF NOT EXISTS idx_building_logs_turn
            ON building_logs(turn, building_id);

            CREATE INDEX IF NOT EXISTS idx_power_logs_turn
            ON power_logs(turn, power_id);

            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                origin_kind TEXT NOT NULL DEFAULT '',
                origin_ref TEXT NOT NULL DEFAULT '',
                origin_turn INTEGER NOT NULL,
                bar_value INTEGER NOT NULL DEFAULT 40,
                bar_good_meaning TEXT NOT NULL DEFAULT '已平',
                bar_bad_meaning TEXT NOT NULL DEFAULT '失控',
                inertia INTEGER NOT NULL DEFAULT 0,
                phase TEXT NOT NULL DEFAULT '起',
                stage_text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                severity INTEGER NOT NULL DEFAULT 50,
                region_hint TEXT NOT NULL DEFAULT '',
                faction_hint TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                ongoing_effects TEXT NOT NULL DEFAULT '{}',
                cancellable TEXT NOT NULL DEFAULT 'never',
                cancel_cost TEXT NOT NULL DEFAULT '{}',
                effect_on_resolve TEXT NOT NULL DEFAULT '{}',
                effect_on_fail TEXT NOT NULL DEFAULT '{}',
                resolve_condition TEXT NOT NULL DEFAULT '',
                fail_condition TEXT NOT NULL DEFAULT '',
                resolution_summary TEXT NOT NULL DEFAULT '',
                last_advance_turn INTEGER NOT NULL DEFAULT 0,
                closed_turn INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS issue_advances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id INTEGER NOT NULL,
                turn INTEGER NOT NULL,
                trigger_kind TEXT NOT NULL,
                trigger_ref TEXT NOT NULL DEFAULT '',
                delta_bar INTEGER NOT NULL DEFAULT 0,
                from_value INTEGER NOT NULL DEFAULT 0,
                to_value INTEGER NOT NULL DEFAULT 0,
                from_stage_text TEXT NOT NULL DEFAULT '',
                to_stage_text TEXT NOT NULL DEFAULT '',
                narrative TEXT NOT NULL DEFAULT '',
                metric_delta TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(issue_id) REFERENCES issues(id)
            );

            CREATE INDEX IF NOT EXISTS idx_issues_active
            ON issues(kind, status, severity DESC);

            CREATE INDEX IF NOT EXISTS idx_issue_advances_issue
            ON issue_advances(issue_id, turn);

            CREATE TABLE IF NOT EXISTS classes (
                name TEXT NOT NULL,
                region_id TEXT NOT NULL DEFAULT '',
                population INTEGER NOT NULL,
                satisfaction INTEGER NOT NULL,
                leverage INTEGER NOT NULL,
                agenda TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (name, region_id)
            );

            CREATE INDEX IF NOT EXISTS idx_classes_region
            ON classes(region_id, name);

            CREATE TABLE IF NOT EXISTS event_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                cause TEXT NOT NULL DEFAULT '',
                process TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                sentiment TEXT NOT NULL DEFAULT 'neutral',
                importance INTEGER NOT NULL DEFAULT 3,
                tags TEXT NOT NULL DEFAULT '[]',
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                expires_turn INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(subject_type, subject_id, event_type, source_kind, source_id)
            );

            CREATE INDEX IF NOT EXISTS idx_event_memories_subject
            ON event_memories(subject_type, subject_id, turn);

            CREATE INDEX IF NOT EXISTS idx_event_memories_turn
            ON event_memories(turn, importance);

            CREATE INDEX IF NOT EXISTS idx_event_memories_expiry
            ON event_memories(expires_turn, turn);


            CREATE TABLE IF NOT EXISTS event_memory_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                excerpt TEXT NOT NULL DEFAULT '',
                locator TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(memory_id) REFERENCES event_memories(id) ON DELETE CASCADE,
                UNIQUE(memory_id, source_kind, source_id, locator)
            );

            CREATE INDEX IF NOT EXISTS idx_event_memory_sources_memory
            ON event_memory_sources(memory_id);

            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        for column, definition in {
            "military_strength": "INTEGER NOT NULL DEFAULT 50",
            "cohesion": "INTEGER NOT NULL DEFAULT 50",
            "supply": "INTEGER NOT NULL DEFAULT 50",
            "last_action": "TEXT NOT NULL DEFAULT ''",
            "kind": "TEXT NOT NULL DEFAULT '敌国'",
            "aliases": "TEXT NOT NULL DEFAULT ''",
        }.items():
            self.ensure_column("powers", column, definition)
        self.ensure_column("armies", "owner_power", "TEXT NOT NULL DEFAULT 'ming'")
        self.ensure_column("regions", "controlled_by", "TEXT NOT NULL DEFAULT 'ming'")
        self.ensure_column("characters", "power_id", "TEXT NOT NULL DEFAULT 'ming'")
        self.ensure_column("characters", "location", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("issues", "resolve_condition", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("issues", "fail_condition", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "birth_year", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "historical_death_year", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "historical_death_month", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "debut_year", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "debut_month", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "status", "TEXT NOT NULL DEFAULT 'active'")
        self.ensure_column("characters", "status_reason", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "status_changed_turn", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "portrait_id", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "court_role", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "summary", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "aliases", "TEXT NOT NULL DEFAULT '[]'")
        # 步骤7：回合阶段（旧库迁移，schema 升级非 fallback）
        self.ensure_column("game_state", "turn_phase", "TEXT NOT NULL DEFAULT 'summoning'")
        # 密令推演副作用列（result 留给承办人进展，sim_note 给推演写泄漏/反弹，互不覆盖）
        self.ensure_column("secret_orders", "sim_note", "TEXT NOT NULL DEFAULT ''")
        # 密令期限：0=无硬期限；到 due_turn 时自动转入待核议，由推演当月判 done/failed。
        self.ensure_column("secret_orders", "due_turn", "INTEGER NOT NULL DEFAULT 0")
        # economy_ledger 支出结构化标签：仅 extractor 抽出的 economy_moves 填这三列；
        # flows 月固定支出与所有收入留 NULL。purpose 受控枚举见 constants.ECONOMY_PURPOSES。
        self.ensure_column("economy_ledger", "purpose", "TEXT")
        self.ensure_column("economy_ledger", "target_kind", "TEXT")
        self.ensure_column("economy_ledger", "target_id", "TEXT")
        # 后宫调教记录
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS consort_traits (
                name TEXT PRIMARY KEY,
                extra_skills TEXT NOT NULL DEFAULT '',
                extra_traits TEXT NOT NULL DEFAULT '',
                updated_turn INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()
        self.init_fiscal_config()

    def init_fiscal_config(self) -> None:
        # base/rate 单位为季度万两/%。flows.py 用 monthly_amount(x)=round(x/3) 换月度。
        # 开局目标：国库月净 ~-13 万、内库月净 ~+18 万；玩家主要破局点：
        # 查隐田(田赋_rate↑)、加商税/钞关(商税_base↑)、查盐课(盐税_base↑)、减宗室(宗室禄米_rate↓)。
        rows = [
            ("田赋_rate",    68,  "rate", "各省田赋实收率%，账面845×此率/100。可升至 85（查隐田+清丈）"),
            ("辽饷_base",   130,  "base", "辽东加派季额，万两"),
            ("辽饷_rate",   100,  "rate", "辽饷实收率%"),
            ("盐税_base",    70,  "base", "两淮两浙盐引季度定额，万两。查私盐可拉到 120"),
            ("盐税_rate",   100,  "rate", "盐税实收率%"),
            ("商税_base",    22,  "base", "各地关卡店税季额，万两。加钞关/抽分可拉到 60"),
            ("商税_rate",   100,  "rate", "商税实收率%"),
            ("宗室禄米_base",360,  "base", "诸藩宗室禄米季度账面额，万两。史实万历末已达千万/年；削藩可降到180"),
            ("宗室禄米_rate", 55,  "rate", "宗室禄米实发率%。史实崇祯初实发不足6成；削藩可降到30~40"),
            ("官俸_base",    75,  "base", "在京百官俸禄季额，万两（含地方折色）"),
            ("官俸_rate",   100,  "rate", "官俸发放率%"),
            ("工程_base",    15,  "base", "工部季度维护支出，万两"),
            ("工程_rate",   100,  "rate", "工程维护率%"),
            ("赈灾_base",    15,  "base", "制度性赈灾备用，万两"),
            ("赈灾_rate",   100,  "rate", "赈灾拨付率%"),
            ("皇庄_base",    60,  "base", "皇庄地租季度上缴内库，万两"),
            ("皇庄_rate",   100,  "rate", "皇庄收益率%"),
            ("织造_base",    35,  "base", "苏杭织造局季度上缴内库，万两"),
            ("织造_rate",   100,  "rate", "织造收益率%"),
            ("矿税_base",    10,  "base", "矿税残余季额，万两"),
            ("矿税_rate",   100,  "rate", "矿税实收率%"),
            ("宫廷_base",    22,  "base", "皇室日常用度季额，万两"),
            ("宫廷_rate",   100,  "rate", "宫廷开支率%"),
            ("内廷俸_base",  15,  "base", "太监宫女俸禄季额，万两"),
            ("内廷俸_rate", 100,  "rate", "内廷俸禄率%"),
            ("妃嫔_base",    10,  "base", "后宫妃嫔季度供奉，万两"),
            ("妃嫔_rate",   100,  "rate", "妃嫔供奉率%"),
        ]
        # schema 版本：旧 DB 用 INSERT OR IGNORE 保留玩家中途的 set_fiscal_config 改动；
        # 默认值整体重平衡时升 SCHEMA_VERSION，旧库走 UPDATE 路径全量覆盖。
        SCHEMA_VERSION = 5
        cur_ver_row = self.conn.execute(
            "SELECT value FROM fiscal_config WHERE key = '__schema_version'"
        ).fetchone()
        cur_ver = int(cur_ver_row["value"]) if cur_ver_row else 0
        if cur_ver < SCHEMA_VERSION:
            for key, value, kind, note in rows:
                self.conn.execute(
                    "INSERT INTO fiscal_config (key, value, kind, note) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, note=excluded.note",
                    (key, value, kind, note),
                )
            self.conn.execute(
                "INSERT INTO fiscal_config (key, value, kind, note) VALUES "
                "('__schema_version', ?, 'meta', '财政默认值大版本号，升即重置玩家未改动的默认值') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )
        else:
            self.conn.executemany(
                "INSERT OR IGNORE INTO fiscal_config (key, value, kind, note) VALUES (?, ?, ?, ?)",
                rows,
            )
        self.conn.commit()

    def get_fiscal_config(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT key, value FROM fiscal_config WHERE key NOT LIKE '\\_\\_%' ESCAPE '\\'"
        ).fetchall()
        return {str(r["key"]): int(r["value"]) for r in rows}

    def set_fiscal_config(self, key: str, value: int) -> None:
        self.conn.execute(
            "UPDATE fiscal_config SET value = ? WHERE key = ?", (value, key)
        )
        self.conn.commit()

    def ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def table_has_rows(self, table: str) -> bool:
        row = self.conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        return row is not None

    def seed_static_data(self) -> None:
        if not self.table_has_rows("offices"):
            for office_type, definition in self.content.office_definitions.items():
                self.conn.execute(
                    """
                    INSERT INTO offices
                    (office_type, skills, tools, authority_scope, power, responsibility, corruption_risk)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        office_type,
                        json.dumps(definition["skills"], ensure_ascii=False),
                        json.dumps(definition["tools"], ensure_ascii=False),
                        str(definition["authority_scope"]),
                        int(definition["power"]),
                        int(definition["responsibility"]),
                        int(definition["corruption_risk"]),
                    ),
                )

        if not self.table_has_rows("characters"):
            for character in self.content.characters.values():
                office = normalize_office(character.office)
                office_type = infer_office_type_from_office(office, character.office_type)
                self.conn.execute(
                    """
                    INSERT INTO characters
                    (name, office, office_type, faction, aliases, personal_skills, loyalty, ability, integrity, courage, style,
                     birth_year, historical_death_year, historical_death_month, debut_year, debut_month,
                     status, status_reason, status_changed_turn, portrait_id, power_id, location, summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        character.name,
                        office,
                        office_type,
                        character.faction,
                        json.dumps(character.aliases, ensure_ascii=False),
                        json.dumps(character.personal_skills, ensure_ascii=False),
                        character.loyalty,
                        character.ability,
                        character.integrity,
                        character.courage,
                        character.style,
                        character.birth_year,
                        character.historical_death_year,
                        character.historical_death_month,
                        character.debut_year,
                        character.debut_month,
                        character.status,
                        "",
                        0,
                        character.portrait_id,
                        character.power_id,
                        character.location,
                        character.summary,
                    ),
                )
        if not self.table_has_rows("character_offices"):
            for row in self.conn.execute("SELECT name, office, office_type FROM characters").fetchall():
                self.conn.execute(
                    """
                    INSERT INTO character_offices (character_name, office_title, office_type, source)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row["name"], row["office"], row["office_type"], "存档迁移"),
                )

        if not self.table_has_rows("factions"):
            for faction in self.content.factions.values():
                self.conn.execute(
                    """
                    INSERT INTO factions (name, satisfaction, leverage, agenda)
                    VALUES (?, ?, ?, ?)
                    """,
                    (faction.name, faction.satisfaction, faction.leverage, faction.agenda),
                )
        if not self.table_has_rows("classes"):
            for cls in self.content.classes.values():
                self.conn.execute(
                    """
                    INSERT INTO classes (name, region_id, population, satisfaction, leverage, agenda)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (cls.name, cls.region_id, cls.population, cls.satisfaction, cls.leverage, cls.agenda),
                )
        if not self.table_has_rows("powers"):
            for power in self.content.powers.values():
                self.conn.execute(
                    """
                    INSERT INTO powers
                    (id, name, kind, leader, stance, leverage, satisfaction, military_strength,
                     cohesion, supply, agenda, status, last_action, aliases)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        power.id,
                        power.name,
                        power.kind,
                        power.leader,
                        power.stance,
                        power.leverage,
                        power.satisfaction,
                        power.military_strength,
                        power.cohesion,
                        power.supply,
                        power.agenda,
                        power.status,
                        power.last_action,
                        power.aliases,
                    ),
                )
        if not self.table_has_rows("regions"):
            for region in self.content.regions.values():
                self.conn.execute(
                    """
                    INSERT INTO regions
                    (id, name, kind, population, public_support, unrest, natural_disaster, human_disaster,
                     registered_land, hidden_land, tax_per_turn, grain_security, gentry_resistance,
                     military_pressure, status, controlled_by, fiscal)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        region.id,
                        region.name,
                        region.kind,
                        region.population,
                        region.public_support,
                        region.unrest,
                        region.natural_disaster,
                        region.human_disaster,
                        region.registered_land,
                        region.hidden_land,
                        region.tax_per_turn,
                        region.grain_security,
                        region.gentry_resistance,
                        region.military_pressure,
                        region.status,
                        region.controlled_by,
                        json.dumps(region.fiscal, ensure_ascii=False),
                    ),
                )
        is_fresh_armies_seed = not self.table_has_rows("armies")
        if is_fresh_armies_seed:
            for army in self.content.armies.values():
                self.conn.execute(
                    """
                    INSERT INTO armies
                    (id, name, station, theater, commander, controller, troop_type, manpower,
                     maintenance_per_turn, supply, morale, training, equipment, arrears,
                     mobility, loyalty, status, owner_power)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        army.id,
                        army.name,
                        army.station,
                        army.theater,
                        army.commander,
                        army.controller,
                        army.troop_type,
                        army.manpower,
                        army.maintenance_per_turn,
                        army.supply,
                        army.morale,
                        army.training,
                        army.equipment,
                        army.arrears,
                        army.mobility,
                        army.loyalty,
                        army.status,
                        army.owner_power,
                    ),
                )
        if not self.table_has_rows("buildings"):
            for building in self.content.buildings.values():
                self.conn.execute(
                    """
                    INSERT INTO buildings
                    (id, region_id, name, category, level, condition, maintenance, risk,
                     output_metric, output_amount, status, origin, created_turn)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preset', 0)
                    """,
                    (
                        building.id,
                        building.region_id,
                        building.name,
                        building.category,
                        building.level,
                        building.condition,
                        building.maintenance,
                        building.risk,
                        building.output_metric,
                        building.output_amount,
                        building.status,
                    ),
                )
        if not self.table_has_rows("events"):
            for event in (*self.content.events, *self.content.seed_events):
                self.conn.execute(
                    """
                    INSERT INTO events
                    (id, title, kind, summary, urgency, severity, credibility, interests, audiences)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.title,
                        event.kind,
                        event.summary,
                        event.urgency,
                        event.severity,
                        event.credibility,
                        json.dumps(event.interests, ensure_ascii=False),
                        json.dumps(event.audiences, ensure_ascii=False),
                    ),
                )
        self._migrate_arrears_unit_to_silver(is_fresh_armies_seed)
        self.conn.commit()

    def _migrate_arrears_unit_to_silver(self, is_fresh_armies_seed: bool) -> None:
        """一次性迁移：armies.arrears 从 0-100 抽象分换成累计欠饷万两。
        旧档按 arrears * maintenance_per_turn / 25 估算（粗略：旧分数 ≈ 4 倍欠饷月数）。

        区分新老档：
        - 新档（is_fresh_armies_seed=True）：armies 由本版 seed_armies 刚刚写入，arrears
          已经是万两。直接打 version=1，跳过换算。
        - 老档（is_fresh_armies_seed=False）：armies 表早已存在数据；若 fiscal_config 中
          无 __arrears_unit_version 标记，说明从未跑过本迁移 → 走换算逻辑。
        """
        ARREARS_UNIT_VERSION = 1
        row = self.conn.execute(
            "SELECT value FROM fiscal_config WHERE key = '__arrears_unit_version'"
        ).fetchone()
        cur = int(row["value"]) if row else 0
        if cur >= ARREARS_UNIT_VERSION:
            return
        if not is_fresh_armies_seed:
            # 真老档：换算分数 → 万两
            self.conn.execute(
                "UPDATE armies SET arrears = CAST(arrears * maintenance_per_turn / 25.0 AS INTEGER) "
                "WHERE maintenance_per_turn > 0"
            )
        # 无论新老档，都把 version 打上，下次启动直接跳过
        self.conn.execute(
            "INSERT INTO fiscal_config (key, value, kind, note) VALUES "
            "('__arrears_unit_version', ?, 'meta', 'arrears 单位由 0-100 分迁至累计欠饷万两的版本号') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, note = excluded.note",
            (ARREARS_UNIT_VERSION,),
        )

    def has_state(self) -> bool:
        row = self.conn.execute("SELECT 1 FROM game_state WHERE id = 1").fetchone()
        return row is not None

    def save_state(self, state: GameState) -> None:
        self.conn.execute(
            """
            INSERT INTO game_state (id, year, period, turn, turn_phase)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET year = excluded.year, period = excluded.period,
                turn = excluded.turn, turn_phase = excluded.turn_phase
            """,
            (state.year, state.period, state.turn, state.turn_phase),
        )
        for key, value in state.metrics.items():
            self.conn.execute(
                """
                INSERT INTO metrics (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
        self.sync_economy_accounts(state)
        self.conn.commit()

    def load_state(self, start_ym: str = "") -> GameState:
        row = self.conn.execute("SELECT year, period, turn, turn_phase FROM game_state WHERE id = 1").fetchone()
        if row is None:
            state = GameState()
            if start_ym:
                try:
                    y_str, m_str = start_ym.split(".")
                    y, m = int(y_str), int(m_str)
                except (ValueError, AttributeError):
                    raise SystemExit(f"--start-ym 格式非法：{start_ym!r}，应为 YYYY.MM（如 1629.04）。")
                if not (1627 <= y <= 1644 and 1 <= m <= 12):
                    raise SystemExit(f"--start-ym 超范围：{start_ym!r}，年须 1627-1644、月 1-12。")
                state.turn = (y - 1627) * 12 + (m - 10) + 1
                state.year, state.period = y, m
                print(f"[调试] 跳到 {y}年{m}月起手（turn={state.turn}）。")
            self.save_state(state)
            self.ensure_opening_ledger(state)
            self.seed_opening_crises(state)
            self.seed_opening_gazette(state)
            return state
        metrics = {
            metric["key"]: int(metric["value"])
            for metric in self.conn.execute("SELECT key, value FROM metrics").fetchall()
        }
        state = GameState(
            year=int(row["year"]), period=int(row["period"]), turn=int(row["turn"]),
            turn_phase=str(row["turn_phase"] or "summoning"),
        )
        if metrics:
            # 只接当前 GameState 默认 dict 里有的 key，避免旧 DB 残留废弃 metric 灌入。
            valid_keys = set(state.metrics.keys())
            state.metrics.update({k: v for k, v in metrics.items() if k in valid_keys})
        account_rows = self.conn.execute("SELECT account, balance FROM economy_accounts").fetchall()
        for account in account_rows:
            account_name = str(account["account"])
            balance = int(account["balance"])
            state.metrics[account_name] = balance
        self.sync_economy_accounts(state)
        self.ensure_opening_ledger(state)
        self.conn.commit()
        return state

    def sync_economy_accounts(self, state: GameState) -> None:
        notes = {
            "国库": "朝廷公开财政，用于军饷、赈济、官俸和工程。",
            "内库": "皇帝可直接调度的钱物，用于救急、密支和政治缓冲。",
        }
        for account in ECONOMY_ACCOUNTS:
            self.conn.execute(
                """
                INSERT INTO economy_accounts (account, metric_key, balance, note)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account) DO UPDATE SET
                    balance = excluded.balance,
                    note = excluded.note,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (account, account, int(state.metrics[account]), notes[account]),
            )

    def ensure_opening_ledger(self, state: GameState) -> None:
        for account in ECONOMY_ACCOUNTS:
            exists = self.conn.execute(
                "SELECT 1 FROM economy_ledger WHERE account = ? LIMIT 1",
                (account,),
            ).fetchone()
            if exists:
                continue
            balance = int(state.metrics[account])
            self.conn.execute(
                """
                INSERT INTO economy_ledger
                (turn, year, period, account, delta, balance_after, category, reason, actor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (state.turn, state.year, state.period, account, balance, balance, "期初", "登基初始账册", "内阁"),
            )
        self.conn.commit()

    def seed_opening_gazette(self, state: GameState) -> None:
        """新档塞一份「即位前一月」邸报（turn=state.turn-1），让大臣首回合即可经 read_past_report
        查到开局朝局速览，不必凭空臆议。已存在则不覆盖。文本来自 content/opening_gazette.md。"""
        prev_turn = state.turn - 1
        prev_year, prev_period = state.year, state.period - 1
        if prev_period < 1:
            prev_period = 12
            prev_year -= 1
        exists = self.conn.execute(
            "SELECT 1 FROM turn_reports WHERE turn = ?",
            (prev_turn,),
        ).fetchone()
        if exists is not None:
            return
        from pathlib import Path
        from ming_sim.paths import bundled_path
        gazette_path = Path(bundled_path("content", "opening_gazette.md"))
        if not gazette_path.is_file():
            return
        text = gazette_path.read_text(encoding="utf-8").strip()
        if not text:
            return
        self.conn.execute(
            "INSERT INTO turn_reports (turn, year, period, report) VALUES (?, ?, ?, ?)",
            (prev_turn, prev_year, prev_period, text),
        )
        self.conn.commit()

    def seed_opening_crises(self, state: GameState) -> None:
        """新档首次进入时塞 1627 即位三大危机为 active situation issue。"""
        from ming_sim.assets import load_json_asset
        raw = load_json_asset("opening_crises.json")
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            origin_ref = str(item.get("id") or "")
            if origin_ref and self.find_any_issue_by_origin("opening", origin_ref) is not None:
                continue
            try:
                self.insert_issue(
                    state,
                    kind=str(item.get("kind") or "situation"),
                    title=str(item.get("title") or ""),
                    origin_kind="opening",
                    origin_ref=origin_ref,
                    bar_value=int(item.get("bar_value", 25)),
                    bar_good_meaning=str(item.get("bar_good_meaning") or "已平"),
                    bar_bad_meaning=str(item.get("bar_bad_meaning") or "失控"),
                    inertia=int(item.get("inertia") or 0),
                    stage_text=str(item.get("stage_text") or ""),
                    severity=int(item.get("severity") or 60),
                    region_hint=str(item.get("region_hint") or ""),
                    faction_hint=str(item.get("faction_hint") or ""),
                    tags=list(item.get("tags") or []),
                    ongoing_effects=dict(item.get("ongoing_effects") or {}),
                    cancellable="never",
                    effect_on_resolve=dict(item.get("effect_on_resolve") or {}),
                    effect_on_fail=dict(item.get("effect_on_fail") or {}),
                    resolve_condition=str(item.get("resolve_condition") or ""),
                    fail_condition=str(item.get("fail_condition") or ""),
                )
            except Exception as exc:
                print(f"[WARN] opening crisis 落库失败：{exc}；跳过 {item.get('title')}")

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
        # 去职（下狱/革职/流放/致仕/死）即削职：清空 characters.office，
        # 原职仍留在 character_offices 备档可追溯。复职（active/offstage）不动 office。
        ousted = status in {"dismissed", "imprisoned", "exiled", "retired", "dead"}
        if ousted:
            self.conn.execute(
                "UPDATE characters SET status=?, status_reason=?, status_changed_turn=?, office='' WHERE name=?",
                (status, reason[:200], state.turn, name),
            )
        else:
            self.conn.execute(
                "UPDATE characters SET status=?, status_reason=?, status_changed_turn=? WHERE name=?",
                (status, reason[:200], state.turn, name),
            )
        self.conn.commit()

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
            new_power = str(raw.get("new_power") or raw.get("新势力") or "").strip()
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
        self.conn.commit()
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
        current_type = (
            self.conn.execute(
                "SELECT office_type FROM characters WHERE name=? AND power_id='ming'", (name,)
            ).fetchone() or {"office_type": ""}
        )["office_type"]
        if not current_type:
            raise ValueError(f"{name}不属大明朝廷，不能授予大明官职")
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
        self.conn.commit()
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
        # 若没有专属 portrait_id，按 office_type 分配预设池头像
        portrait_id = character.portrait_id
        if not portrait_id:
            prefix = "consort_pool_" if character.office_type == "后宫" else "minister_pool_"
            portrait_id = self.next_pool_portrait_id(prefix)
        source_label = source or ("吏部铨选任命" if character.office_type != "后宫" else "诏书纳妃")
        office_source = source or ("吏部任命" if character.office_type != "后宫" else "诏书纳妃")
        self.conn.execute(
            """
            INSERT INTO characters
            (name, office, office_type, faction, aliases, personal_skills, loyalty, ability, integrity, courage, style,
             birth_year, historical_death_year, historical_death_month, debut_year, debut_month,
             status, status_reason, status_changed_turn, portrait_id, power_id, location, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        self.conn.commit()

    def record_economy_moves(
        self,
        state: GameState,
        event: Event,
        edict_id: int,
        actor: str,
        moves: List[Dict[str, object]],
    ) -> None:
        if not moves:
            self.sync_economy_accounts(state)
            self.conn.commit()
            return
        for move in moves:
            self.conn.execute(
                """
                INSERT INTO economy_ledger
                (turn, year, period, account, delta, balance_after, category, reason, event_id, edict_id, actor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.turn,
                    state.year,
                    state.period,
                    str(move["account"]),
                    int(move["delta"]),
                    int(move["balance_after"]),
                    str(move["category"]),
                    str(move["reason"]),
                    event.id,
                    edict_id,
                    actor,
                ),
            )
        self.sync_economy_accounts(state)
        self.conn.commit()

    def treasury_budget_summary(self) -> str:
        cfg = self.get_fiscal_config()
        land_base = self.conn.execute("SELECT SUM(tax_per_turn) FROM regions").fetchone()[0] or 0
        army_total = self.conn.execute("SELECT SUM(maintenance_per_turn) FROM armies").fetchone()[0] or 0
        gk_in = (
            round(land_base * cfg.get("田赋_rate", 68) / 100)
            + round(cfg.get("辽饷_base", 130) * cfg.get("辽饷_rate", 100) / 100)
            + round(cfg.get("盐税_base", 70) * cfg.get("盐税_rate", 100) / 100)
            + round(cfg.get("商税_base", 22) * cfg.get("商税_rate", 100) / 100)
        )
        gk_out = (
            int(army_total)
            + round(cfg.get("宗室禄米_base", 100) * cfg.get("宗室禄米_rate", 70) / 100)
            + round(cfg.get("官俸_base", 35) * cfg.get("官俸_rate", 100) / 100)
            + round(cfg.get("工程_base", 15) * cfg.get("工程_rate", 100) / 100)
            + round(cfg.get("赈灾_base", 15) * cfg.get("赈灾_rate", 100) / 100)
        )
        nk_in = (
            round(cfg.get("皇庄_base", 60) * cfg.get("皇庄_rate", 100) / 100)
            + round(cfg.get("织造_base", 35) * cfg.get("织造_rate", 100) / 100)
            + round(cfg.get("矿税_base", 10) * cfg.get("矿税_rate", 100) / 100)
        )
        nk_out = (
            round(cfg.get("宫廷_base", 22) * cfg.get("宫廷_rate", 100) / 100)
            + round(cfg.get("内廷俸_base", 15) * cfg.get("内廷俸_rate", 100) / 100)
            + round(cfg.get("妃嫔_base", 10) * cfg.get("妃嫔_rate", 100) / 100)
        )
        # 建筑：maintenance/output_amount 已是月值，不过 monthly_amount。
        # 维护按 category 分账：内廷扣内库，其它扣国库。产出按 output_metric 走。
        build_rows = self.conn.execute(
            "SELECT category, condition, maintenance, output_metric, output_amount FROM buildings"
        ).fetchall()
        b_gk_out = b_nk_out = b_gk_in = b_nk_in = 0
        for r in build_rows:
            maint = max(0, int(r["maintenance"]))
            if str(r["category"]) == "内廷":
                b_nk_out += maint
            else:
                b_gk_out += maint
            metric = str(r["output_metric"])
            cond = max(0, min(100, int(r["condition"])))
            out = max(0, int(r["output_amount"]))
            produced = round(out * cond / 100) if metric and out else 0
            if metric == "国库":
                b_gk_in += produced
            elif metric == "内库":
                b_nk_in += produced

        gk_net = monthly_amount(gk_in) + b_gk_in - monthly_amount(gk_out) - b_gk_out
        nk_net = monthly_amount(nk_in) + b_nk_in - monthly_amount(nk_out) - b_nk_out
        return (
            f"{TURN_UNIT}度预算基准：国库入{format_money(monthly_amount(gk_in) + b_gk_in)}"
            f"（田赋+辽饷+盐税+商税+建筑产出{format_money(b_gk_in)}）"
            f"出{format_money(monthly_amount(gk_out) + b_gk_out)}"
            f"（军饷{format_money(monthly_amount(int(army_total)))}+宗室+官俸+补给+建筑维护{format_money(b_gk_out)}）"
            f"净{format_money_delta(gk_net)}；"
            f"内库入{format_money(monthly_amount(nk_in) + b_nk_in)}"
            f"出{format_money(monthly_amount(nk_out) + b_nk_out)}"
            f"（内廷维护{format_money(b_nk_out)}）"
            f"净{format_money_delta(nk_net)}。"
        )

    def treasury_report(self, state: GameState, limit: int = 6) -> str:
        account_rows = self.conn.execute(
            "SELECT account, balance FROM economy_accounts ORDER BY account DESC"
        ).fetchall()
        if not account_rows:
            account_text = f"国库{format_money(state.metrics['国库'])}，内库{format_money(state.metrics['内库'])}"
        else:
            account_text = "，".join(f"{row['account']}{format_money(int(row['balance']))}" for row in account_rows)

        period_rows = self.conn.execute(
            """
            SELECT account,
                   SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS income,
                   SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END) AS expense
            FROM economy_ledger
            WHERE turn = ?
            GROUP BY account
            ORDER BY account DESC
            """,
            (state.turn,),
        ).fetchall()
        period_text = "；".join(
            f"{row['account']}入{format_money(int(row['income'] or 0))}出{format_money(int(row['expense'] or 0))}"
            for row in period_rows
        )
        if not period_text:
            period_text = f"本{TURN_UNIT}尚无新账"

        ledger_rows = self.conn.execute(
            """
            SELECT year, period, account, delta, category, reason, actor
            FROM economy_ledger
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        recent = []
        for row in reversed(ledger_rows):
            delta = int(row["delta"])
            sign = "+" if delta > 0 else ""
            recent.append(
                f"{period_label(int(row['year']), int(row['period']))} {row['account']}{sign}{format_money(delta)} {row['category']}：{row['reason']}"
            )
        recent_text = "；".join(recent) if recent else "未见流水"
        budget = self.treasury_budget_summary()
        return f"{budget}账面：{account_text}。本{TURN_UNIT}收支：{period_text}。近账：{recent_text}。"

    def faction_satisfaction(self, faction: str) -> int:
        row = self.conn.execute("SELECT satisfaction FROM factions WHERE name = ?", (faction,)).fetchone()
        return int(row["satisfaction"]) if row else 50

    def faction_leverage(self, faction: str) -> int:
        row = self.conn.execute("SELECT leverage FROM factions WHERE name = ?", (faction,)).fetchone()
        return int(row["leverage"]) if row else 50

    def faction_report(self) -> str:
        rows = self.conn.execute(
            "SELECT name, satisfaction, leverage, agenda FROM factions ORDER BY name"
        ).fetchall()
        if not rows:
            return "派系未建档。"
        return "；".join(
            f"{row['name']}满意{row['satisfaction']}、势力{row['leverage']}，所求：{row['agenda']}"
            for row in rows
        )

    def class_rows(self, region_id: str = "") -> List[sqlite3.Row]:
        """region_id="" 取全国汇总行；其它取该省切片。"""
        return self.conn.execute(
            "SELECT name, region_id, population, satisfaction, leverage, agenda "
            "FROM classes WHERE region_id = ? ORDER BY name",
            (region_id,),
        ).fetchall()

    def class_report(self) -> str:
        """全国汇总 + 各省紧张切片（sat<=30 且 lev>=60）。"""
        national = self.class_rows("")
        if not national:
            return "阶级未建档。"
        head = "；".join(
            f"{row['name']}满意{row['satisfaction']}、势力{row['leverage']}（{row['agenda']}）"
            for row in national
        )
        hot = self.conn.execute(
            """
            SELECT c.name, c.region_id, c.satisfaction, c.leverage, r.name AS region_name
            FROM classes c
            LEFT JOIN regions r ON r.id = c.region_id
            WHERE c.region_id <> '' AND c.satisfaction <= 30 AND c.leverage >= 60
            ORDER BY c.satisfaction ASC, c.leverage DESC
            """
        ).fetchall()
        if not hot:
            return f"阶级总览：{head}。各省阶级暂无高压预警。"
        warn = "；".join(
            f"{row['region_name'] or row['region_id']} {row['name']}满意{row['satisfaction']}/势力{row['leverage']}"
            for row in hot
        )
        return f"阶级总览：{head}。高压预警：{warn}。"

    def adjust_classes(self, deltas: Dict[str, Dict[str, int]]) -> None:
        """deltas 结构：{ key: {satisfaction: +/-N, leverage: +/-N} }
        key 形式：'农民' (全国) 或 '农民@shaanxi' (省级)。
        """
        for key, fields in deltas.items():
            if not fields:
                continue
            if "@" in key:
                name, region_id = key.split("@", 1)
            else:
                name, region_id = key, ""
            row = self.conn.execute(
                "SELECT satisfaction, leverage FROM classes WHERE name = ? AND region_id = ?",
                (name.strip(), region_id.strip()),
            ).fetchone()
            if not row:
                continue
            sat = int(row["satisfaction"]) + int(fields.get("satisfaction", 0) or 0)
            lev = int(row["leverage"]) + int(fields.get("leverage", 0) or 0)
            sat = max(0, min(100, sat))
            lev = max(0, min(100, lev))
            self.conn.execute(
                "UPDATE classes SET satisfaction = ?, leverage = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE name = ? AND region_id = ?",
                (sat, lev, name.strip(), region_id.strip()),
            )
        self.conn.commit()

    def power_rows(self, exclude_self: bool = False) -> List[sqlite3.Row]:
        where = "WHERE id != 'ming'" if exclude_self else ""
        return self.conn.execute(
            f"""
            SELECT *
            FROM powers
            {where}
            ORDER BY CASE id
                WHEN 'ming' THEN 0
                WHEN 'houjin' THEN 1
                WHEN 'mongol' THEN 2
                WHEN 'korea' THEN 3
                WHEN 'japan' THEN 4
                WHEN 'dutch' THEN 5
                WHEN 'bandits' THEN 6
                ELSE 9
            END, name
            """
        ).fetchall()

    def power_payload(self, exclude_self: bool = False) -> List[Dict[str, object]]:
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "leader": row["leader"],
                "stance": row["stance"],
                "leverage": int(row["leverage"]),
                "satisfaction": int(row["satisfaction"]),
                "military_strength": int(row["military_strength"]),
                "cohesion": int(row["cohesion"]),
                "supply": int(row["supply"]),
                "agenda": row["agenda"],
                "status": row["status"],
                "last_action": row["last_action"],
                "aliases": row["aliases"],
            }
            for row in self.power_rows(exclude_self=exclude_self)
        ]

    def power_report(self, exclude_self: bool = True) -> str:
        rows = self.power_rows(exclude_self=exclude_self)
        if not rows:
            return "势力未建档。"
        return "；".join(
            f"{row['name']}（{row['leader']}）：{row['stance']}，威望{row['leverage']}、"
            f"实力{row['military_strength']}、经济{row['supply']}，"
            f"{row['status']}；近动：{row['last_action'] or '尚无新动'}"
            for row in rows
        )

    def apply_power_deltas(
        self,
        state: GameState,
        updates: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        allowed_fields = {"leverage", "military_strength", "supply"}
        changes: List[Dict[str, object]] = []
        for power_id, raw_changes in updates.items():
            if power_id == "ming":
                print("[WARN] power_updates 不再处理大明自身 → 跳过")
                continue
            row = self.conn.execute("SELECT * FROM powers WHERE id = ?", (power_id,)).fetchone()
            if row is None:
                print(f"[WARN] power_updates 引用未入库势力 '{power_id}' → 跳过")
                continue
            reason = str(
                raw_changes.get("reason")
                or raw_changes.get("原因")
                or raw_changes.get("last_action")
                or raw_changes.get("近动")
                or "势力推演"
            ).strip()[:120]
            for raw_field, value in raw_changes.items():
                field = POWER_FIELD_ALIASES.get(str(raw_field).strip(), str(raw_field).strip())
                if field == "reason":
                    continue
                if field not in allowed_fields:
                    print(f"[WARN] power_updates 只允许 威望/实力/经济，'{raw_field}' → 跳过")
                    continue
                old_value = row[field]
                delta = int(value)
                new_value = max(0, min(100, int(old_value) + delta))
                actual_delta = new_value - int(old_value)
                if actual_delta == 0:
                    continue
                stored_new: object = new_value
                log_delta: int | None = actual_delta
                self.conn.execute(
                    f"UPDATE powers SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, power_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO power_logs
                    (turn, year, period, power_id, field, old_value, new_value, delta, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn,
                        state.year,
                        state.period,
                        power_id,
                        field,
                        str(old_value),
                        str(stored_new),
                        log_delta,
                        reason,
                    ),
                )
                changes.append({
                    "power": row["name"],
                    "field": field,
                    "label": POWER_FIELD_LABELS.get(field, field),
                    "old": old_value,
                    "new": stored_new,
                    "delta": log_delta,
                    "reason": reason,
                })
        self.conn.commit()
        return changes

    def apply_power_rename(
        self,
        state: GameState,
        power_id: str,
        new_name: str,
        *,
        reason: str,
        aliases: str = "",
        status: str = "",
        last_action: str = "",
    ) -> Dict[str, object] | None:
        """Rename a power while keeping its stable id for references.

        Used for dynastic/name changes such as houjin 后金 -> 大清.
        """
        power_id = str(power_id or "").strip()
        new_name = str(new_name or "").strip()
        if not power_id or not new_name:
            return None
        row = self.conn.execute("SELECT * FROM powers WHERE id = ?", (power_id,)).fetchone()
        if row is None:
            print(f"[WARN] power_rename 引用未入库势力 '{power_id}' → 跳过")
            return None
        old_name = str(row["name"] or "")
        old_aliases = str(row["aliases"] or "")
        merged_aliases = [x.strip() for x in (aliases or old_aliases).replace("，", ",").split(",") if x.strip()]
        for alias in (old_name, new_name):
            if alias and alias not in merged_aliases:
                merged_aliases.append(alias)
        new_aliases = "，".join(merged_aliases)
        new_status = str(status or row["status"] or "")[:200]
        new_last_action = str(last_action or reason or row["last_action"] or "")[:200]
        if old_name == new_name and old_aliases == new_aliases and row["status"] == new_status and row["last_action"] == new_last_action:
            return None
        self.conn.execute(
            """
            UPDATE powers
            SET name=?, aliases=?, status=?, last_action=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (new_name, new_aliases, new_status, new_last_action, power_id),
        )
        self.conn.execute(
            """
            INSERT INTO power_name_logs
            (turn, year, period, power_id, old_name, new_name, old_aliases, new_aliases, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (state.turn, state.year, state.period, power_id, old_name, new_name, old_aliases, new_aliases, reason[:200]),
        )
        self.conn.commit()
        return {
            "power_id": power_id,
            "old_name": old_name,
            "new_name": new_name,
            "old_aliases": old_aliases,
            "new_aliases": new_aliases,
            "reason": reason,
        }

    def turn_power_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT pl.*, p.name AS power_name
            FROM power_logs pl
            JOIN powers p ON p.id = pl.power_id
            WHERE pl.turn = ?
            ORDER BY pl.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}势力无明确变化。"
        parts = []
        for row in rows:
            label = POWER_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['power_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                parts.append(f"{row['power_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def region_rows(self, limit: int | None = None, danger_order: bool = False) -> List[sqlite3.Row]:
        order = (
            "(unrest + military_pressure + gentry_resistance + (100 - public_support)) DESC, name"
            if danger_order
            else "kind DESC, name"
        )
        sql = f"""
            SELECT *
            FROM regions
            ORDER BY {order}
        """
        params: Tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return self.conn.execute(sql, params).fetchall()

    def region_payload(self, limit: int | None = None, danger_order: bool = False) -> List[Dict[str, object]]:
        payload: List[Dict[str, object]] = []
        for row in self.region_rows(limit=limit, danger_order=danger_order):
            payload.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "population": int(row["population"]),
                    "public_support": int(row["public_support"]),
                    "unrest": int(row["unrest"]),
                    "natural_disaster": row["natural_disaster"],
                    "human_disaster": row["human_disaster"],
                    "registered_land": int(row["registered_land"]),
                    "hidden_land": int(row["hidden_land"]),
                    "tax_per_turn": int(row["tax_per_turn"]),
                    "grain_security": int(row["grain_security"]),
                    "gentry_resistance": int(row["gentry_resistance"]),
                    "military_pressure": int(row["military_pressure"]),
                    "status": row["status"],
                    "controlled_by": row["controlled_by"],
                }
            )
        return payload

    def region_report(self, limit: int = 5) -> str:
        rows = self.region_rows(limit=limit, danger_order=True)
        if not rows:
            return "地区尚未建档。"
        total_tax = self.conn.execute("SELECT SUM(tax_per_turn) AS total FROM regions").fetchone()
        total_tax_value = int(total_tax["total"] or 0)
        parts = []
        for row in rows:
            parts.append(
                f"{row['name']}：民心{row['public_support']}、动乱{row['unrest']}、"
                f"粮食{row['grain_security']}万石、税{format_money(monthly_amount(int(row['tax_per_turn'])))}/{TURN_UNIT}，{row['status']}"
            )
        return f"地区警讯：{'；'.join(parts)}。两京十三省账面{TURN_UNIT}税合计{format_money(monthly_amount(total_tax_value))}。"

    def region_detail(self, raw_name: str) -> str:
        region_id = match_region_id_from_text(raw_name, self.content.regions)
        if region_id is None:
            raise ValueError(f"未找到地区：{raw_name}")
        row = self.conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
        if row is None:
            raise ValueError(f"地区未入库：{raw_name}")
        return (
            f"{row['name']}（{row['kind']}）：人口{row['population']}万人，"
            f"民心{row['public_support']}，动乱{row['unrest']}，粮食{row['grain_security']}万石，"
            f"田亩{row['registered_land']}万亩，隐田{row['hidden_land']}万亩，"
            f"账面税收{format_money(monthly_amount(int(row['tax_per_turn'])))}/{TURN_UNIT}，"
            f"士绅阻力{row['gentry_resistance']}，军事压力{row['military_pressure']}。"
            f"天灾：{row['natural_disaster']}；人祸：{row['human_disaster']}；状态：{row['status']}"
        )

    def turn_region_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT rl.*, r.name AS region_name
            FROM region_logs rl
            JOIN regions r ON r.id = rl.region_id
            WHERE rl.turn = ?
            ORDER BY rl.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}地区盘面无明确变化。"
        parts = []
        for row in rows:
            label = REGION_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['region_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                parts.append(f"{row['region_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def apply_region_deltas(
        self,
        state: GameState,
        event: Event,
        edict_id: int | None,
        actor: str,
        region_deltas: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        changes: List[Dict[str, object]] = []
        for region_id, raw_changes in region_deltas.items():
            row = self.conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
            if row is None:
                print(f"[WARN] region_delta 引用未入库地区 '{region_id}' → 跳过")
                continue
            reason = str(raw_changes.get("reason") or raw_changes.get("原因") or event.title).strip()[:80]
            for raw_field, value in raw_changes.items():
                field = REGION_FIELD_ALIASES.get(str(raw_field).strip(), str(raw_field).strip())
                if field == "reason":
                    continue
                # 先判字段合法，再取值：非法字段直接报清楚。
                all_direct = REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS + REGION_TEXT_FIELDS
                if field not in all_direct and field not in FISCAL_SCORE_FIELDS:
                    raise LLMContractError(
                        f"{TURN_UNIT}末执行评估引用了非法地区字段：'{raw_field}'（地区 '{region_id}'）。"
                        f"合法字段：{all_direct + FISCAL_SCORE_FIELDS}"
                    )

                # ── fiscal JSON 子字段（corruption 等）────────────────────────
                if field in FISCAL_SCORE_FIELDS:
                    fiscal: dict = json.loads(str(row["fiscal"] or "{}"))
                    old_value = fiscal.get(field, 50)
                    delta = int(value)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    fiscal[field] = new_value
                    self.conn.execute(
                        "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (json.dumps(fiscal, ensure_ascii=False), region_id),
                    )
                    self.conn.execute(
                        """
                        INSERT INTO region_logs
                        (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (state.turn, state.year, state.period, region_id,
                         field, str(old_value), str(new_value), actual_delta,
                         reason, event.id, edict_id, actor),
                    )
                    changes.append({
                        "region": row["name"], "field": field,
                        "label": REGION_FIELD_LABELS.get(field, field),
                        "old": old_value, "new": new_value,
                        "delta": actual_delta, "reason": reason,
                    })
                    continue

                # ── 直接列字段 ────────────────────────────────────────────────
                old_value = row[field]
                if field in REGION_SCORE_FIELDS:
                    delta = int(value)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                elif field in REGION_QUANTITY_FIELDS:
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                else:  # REGION_TEXT_FIELDS
                    text_value = str(value).strip()[:160]
                    if not text_value or text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                self.conn.execute(
                    f"UPDATE regions SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, region_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO region_logs
                    (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn, state.year, state.period, region_id,
                        field, str(old_value), str(stored_new), log_delta,
                        reason, event.id, edict_id, actor,
                    ),
                )
                changes.append(
                    {
                        "region": row["name"], "field": field,
                        "label": REGION_FIELD_LABELS.get(field, field),
                        "old": old_value, "new": stored_new,
                        "delta": log_delta, "reason": reason,
                    }
                )

                # ── 收复触发：controlled_by 由非 ming → ming，覆盖 on_restore 预置 ──
                if (
                    field == "controlled_by"
                    and str(stored_new) == "ming"
                    and str(old_value) != "ming"
                ):
                    extra = self._apply_on_restore(state, region_id, event, edict_id, actor, reason)
                    changes.extend(extra)
        self.conn.commit()
        return changes

    def _apply_on_restore(
        self,
        state: GameState,
        region_id: str,
        event: Event,
        edict_id: int | None,
        actor: str,
        reason: str,
    ) -> List[Dict[str, object]]:
        """收复瞬间用 region.on_restore 覆盖主字段，记 region_logs。"""
        region_def = self.content.regions.get(region_id)
        if region_def is None or not region_def.on_restore:
            return []
        preset = region_def.on_restore
        row = self.conn.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
        if row is None:
            return []
        all_direct = REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS + REGION_TEXT_FIELDS
        out: List[Dict[str, object]] = []
        for raw_field, value in preset.items():
            if raw_field == "fiscal":
                if not isinstance(value, dict):
                    continue
                fiscal = json.loads(str(row["fiscal"] or "{}"))
                for sub_field, sub_val in value.items():
                    if sub_field not in FISCAL_SCORE_FIELDS:
                        continue
                    old_sub = fiscal.get(sub_field, 0)
                    new_sub = int(sub_val)
                    if int(old_sub) == new_sub:
                        continue
                    fiscal[sub_field] = new_sub
                    self.conn.execute(
                        "INSERT INTO region_logs (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (state.turn, state.year, state.period, region_id,
                         sub_field, str(old_sub), str(new_sub), new_sub - int(old_sub),
                         f"收复重置：{reason}", event.id, edict_id, actor),
                    )
                    out.append({
                        "region": row["name"], "field": sub_field,
                        "label": REGION_FIELD_LABELS.get(sub_field, sub_field),
                        "old": old_sub, "new": new_sub,
                        "delta": new_sub - int(old_sub), "reason": f"收复重置：{reason}",
                    })
                self.conn.execute(
                    "UPDATE regions SET fiscal = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (json.dumps(fiscal, ensure_ascii=False), region_id),
                )
                continue
            if raw_field == "controlled_by":
                continue  # 控制权已写完，跳过
            if raw_field not in all_direct:
                continue
            old_val = row[raw_field]
            if raw_field in (REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS):
                new_val: object = int(value)
            else:
                new_val = str(value)
            if str(old_val) == str(new_val):
                continue
            self.conn.execute(
                f"UPDATE regions SET {raw_field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_val, region_id),
            )
            log_delta = (int(new_val) - int(old_val)) if raw_field in (REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS) else None
            self.conn.execute(
                "INSERT INTO region_logs (turn, year, period, region_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (state.turn, state.year, state.period, region_id,
                 raw_field, str(old_val), str(new_val), log_delta,
                 f"收复重置：{reason}", event.id, edict_id, actor),
            )
            out.append({
                "region": row["name"], "field": raw_field,
                "label": REGION_FIELD_LABELS.get(raw_field, raw_field),
                "old": old_val, "new": new_val,
                "delta": log_delta, "reason": f"收复重置：{reason}",
            })
        return out

    def army_rows(self, limit: int | None = None, danger_order: bool = False) -> List[sqlite3.Row]:
        # arrears 是累计欠饷万两，须按 maintenance 归一成"欠饷月数*10"再加权（0-100 量级）
        # CASE 兼容 SQLite（无标量 MIN/LEAST）：maintenance=0 视为 0；归一后截至 100。
        arrears_norm = (
            "CASE "
            "WHEN maintenance_per_turn IS NULL OR maintenance_per_turn = 0 THEN 0 "
            "WHEN arrears * 10 / maintenance_per_turn > 100 THEN 100 "
            "ELSE arrears * 10 / maintenance_per_turn "
            "END"
        )
        order = (
            f"({arrears_norm} + (100 - supply) + (100 - morale) + (100 - loyalty) + (100 - training)) DESC, name"
            if danger_order
            else "theater, name"
        )
        sql = f"""
            SELECT *
            FROM armies
            ORDER BY {order}
        """
        params: Tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return self.conn.execute(sql, params).fetchall()

    def army_payload(self, limit: int | None = None, danger_order: bool = False) -> List[Dict[str, object]]:
        payload: List[Dict[str, object]] = []
        for row in self.army_rows(limit=limit, danger_order=danger_order):
            payload.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "station": row["station"],
                    "theater": row["theater"],
                    "commander": row["commander"],
                    "controller": row["controller"],
                    "troop_type": row["troop_type"],
                    "manpower": int(row["manpower"]),
                    "maintenance_per_turn": int(row["maintenance_per_turn"]),
                    "supply": int(row["supply"]),
                    "morale": int(row["morale"]),
                    "training": int(row["training"]),
                    "equipment": int(row["equipment"]),
                    "arrears": int(row["arrears"]),
                    "mobility": int(row["mobility"]),
                    "loyalty": int(row["loyalty"]),
                    "status": row["status"],
                    "owner_power": row["owner_power"],
                }
            )
        return payload

    def army_report(self, limit: int = 5) -> str:
        rows = self.army_rows(limit=limit, danger_order=True)
        if not rows:
            return "军队尚未建档。"
        total_manpower = self.conn.execute("SELECT SUM(manpower) AS total FROM armies").fetchone()
        total_maintenance = self.conn.execute("SELECT SUM(maintenance_per_turn) AS total FROM armies").fetchone()
        parts = []
        for row in rows:
            maint = int(row["maintenance_per_turn"]) or 0
            arr = int(row["arrears"]) or 0
            if maint > 0 and arr > 0:
                months = arr / maint
                arr_text = f"欠饷{arr}万两（约{months:.1f}月军饷）"
            else:
                arr_text = f"欠饷{arr}万两"
            parts.append(
                f"{row['name']}：驻{row['station']}，兵{row['manpower']}，"
                f"饷{format_money(monthly_amount(maint))} /{TURN_UNIT}，补给{row['supply']}、"
                f"士气{row['morale']}、{arr_text}，{row['status']}"
            )
        return (
            f"军队警讯：{'；'.join(parts)}。"
            f"建档兵力合计{int(total_manpower['total'] or 0)}人，账面{TURN_UNIT}维护费{format_money(monthly_amount(int(total_maintenance['total'] or 0)))}。"
        )

    def army_detail(self, raw_name: str) -> str:
        army_id = match_army_id_from_text(raw_name, self.content.armies)
        if army_id is None:
            raise ValueError(f"未找到军队：{raw_name}")
        row = self.conn.execute("SELECT * FROM armies WHERE id = ?", (army_id,)).fetchone()
        if row is None:
            raise ValueError(f"军队未入库：{raw_name}")
        maint = int(row["maintenance_per_turn"]) or 0
        arr = int(row["arrears"]) or 0
        if maint > 0 and arr > 0:
            months = arr / maint
            arr_text = f"欠饷{arr}万两（约{months:.1f}月军饷）"
        else:
            arr_text = f"欠饷{arr}万两"
        return (
            f"{row['name']}：驻扎地{row['station']}，统帅{row['commander']}，"
            f"兵种{row['troop_type']}，人数{row['manpower']}人，"
            f"维护费{format_money(monthly_amount(maint))} /{TURN_UNIT}，补给{row['supply']}，"
            f"士气{row['morale']}，训练{row['training']}，装备{row['equipment']}，"
            f"{arr_text}，机动{row['mobility']}，忠诚{row['loyalty']}。"
            f"状态：{row['status']}"
        )

    def turn_army_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT al.*, a.name AS army_name
            FROM army_logs al
            JOIN armies a ON a.id = al.army_id
            WHERE al.turn = ?
            ORDER BY al.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}军队盘面无明确变化。"
        parts = []
        for row in rows:
            label = ARMY_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['army_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                if row["field"] == "manpower":
                    parts.append(f"{row['army_name']}{label}{sign}{int(delta)}人（{row['reason']}）")
                elif row["field"] == "maintenance_per_turn":
                    parts.append(f"{row['army_name']}{label}{format_money_delta(int(delta))}（{row['reason']}）")
                else:
                    parts.append(f"{row['army_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def apply_army_deltas(
        self,
        state: GameState,
        event: Event,
        edict_id: int | None,
        actor: str,
        army_deltas: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        changes: List[Dict[str, object]] = []
        for army_id, raw_changes in army_deltas.items():
            row = self.conn.execute("SELECT * FROM armies WHERE id = ?", (army_id,)).fetchone()
            if row is None:
                print(f"[WARN] army_delta 引用未入库军队 '{army_id}' → 跳过")
                continue
            reason = str(raw_changes.get("reason") or raw_changes.get("原因") or event.title).strip()[:80]
            _valid_army_fields = set(ARMY_SCORE_FIELDS + ARMY_QUANTITY_FIELDS + ARMY_TEXT_FIELDS)
            for raw_field, value in raw_changes.items():
                field = ARMY_FIELD_ALIASES.get(str(raw_field).strip(), str(raw_field).strip())
                if field == "reason":
                    continue
                if field not in _valid_army_fields:
                    print(f"[WARN] army_delta 引用非法字段 '{raw_field}' → 跳过")
                    continue
                old_value = row[field]
                if field == "arrears":
                    # arrears 单位=累计欠饷万两，无上限，按需累加。
                    # 正常情况由 flows 唯一变更；此处兜底允许 extractor 在战损/裁军等
                    # 非现金原因下写入（提示词已禁，但保留兜底以防 LLM 越界不至于截断）。
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                elif field in ARMY_SCORE_FIELDS:
                    delta = int(value)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field == "manpower":
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field == "maintenance_per_turn":
                    delta = int(value)
                    new_value = max(0, int(old_value) + delta)
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field in ARMY_TEXT_FIELDS:
                    text_value = str(value).strip()[:160]
                    if not text_value or text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                else:
                    print(f"[WARN] army_delta 未处理字段 '{field}' → 跳过")
                    continue
                self.conn.execute(
                    f"UPDATE armies SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, army_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO army_logs
                    (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn,
                        state.year,
                        state.period,
                        army_id,
                        field,
                        str(old_value),
                        str(stored_new),
                        log_delta,
                        reason,
                        event.id,
                        edict_id,
                        actor,
                    ),
                )
                changes.append(
                    {
                        "army": row["name"],
                        "field": field,
                        "label": ARMY_FIELD_LABELS.get(field, field),
                        "old": old_value,
                        "new": stored_new,
                        "delta": log_delta,
                        "reason": reason,
                    }
                )
        self.conn.commit()
        return changes

    def create_armies_from_extraction(
        self,
        state: GameState,
        new_armies: List[Dict[str, object]],
        actor: str = "档房",
    ) -> List[Dict[str, object]]:
        """据 extractor 输出建新军队。同 id/name 已存在 → 把 manpower 当扩军增量。owner_power 必须是已知 power。"""
        valid_powers = {r["id"] for r in self.conn.execute("SELECT id FROM powers").fetchall()}
        created: List[Dict[str, object]] = []
        for raw in new_armies:
            if not isinstance(raw, dict):
                continue
            item = {POWER_FIELD_ALIASES.get(k, k) if False else k: v for k, v in raw.items()}
            # 规范键：复用 ARMY_FIELD_ALIASES（兼容中文）
            from ming_sim.constants import ARMY_FIELD_ALIASES as _AA
            item = {_AA.get(str(k).strip(), str(k).strip()): v for k, v in raw.items()}
            aid = str(item.get("id") or "").strip()
            if not aid:
                print(f"[WARN] new_armies 缺 id → 跳过: {raw}")
                continue
            owner = str(item.get("owner_power") or "ming").strip() or "ming"
            if owner not in valid_powers:
                print(f"[WARN] new_armies owner_power '{owner}' 未在 powers → 跳过 {aid}")
                continue
            name = str(item.get("name") or aid).strip()
            # 查重：同 id 或 同 name → 转 manpower 扩军增量
            existing = self.conn.execute(
                "SELECT id, name FROM armies WHERE id = ? OR name = ?", (aid, name)
            ).fetchone()
            if existing is not None:
                manpower = item.get("manpower")
                if manpower is None:
                    print(f"[WARN] new_armies 重复 id/name '{aid}' 且无 manpower → 跳过")
                    continue
                try:
                    delta = int(manpower)
                except (TypeError, ValueError):
                    print(f"[WARN] new_armies '{aid}' manpower 非整数 → 跳过")
                    continue
                if delta == 0:
                    continue
                reason = str(item.get("reason") or item.get("status") or "扩军")[:80]
                pseudo_event = type("E", (), {"id": "season", "title": reason})()
                self.apply_army_deltas(
                    state, pseudo_event, None, actor, {existing["id"]: {"manpower": delta, "reason": reason}}
                )
                created.append({"army": existing["name"], "manpower_added": delta, "merged_into_existing": True})
                continue
            # 必填字段
            try:
                manpower = int(item["manpower"])
                maintenance = int(item["maintenance_per_turn"])
            except (KeyError, TypeError, ValueError):
                print(f"[WARN] new_armies '{aid}' 缺 manpower/maintenance_per_turn → 跳过")
                continue
            def _score(field: str, default: int = 50) -> int:
                try:
                    return max(0, min(100, int(item.get(field, default))))
                except (TypeError, ValueError):
                    return default
            def _arrears_init() -> int:
                # arrears 单位=累计欠饷万两，无上限；新军默认 0
                try:
                    return max(0, int(item.get("arrears", 0)))
                except (TypeError, ValueError):
                    return 0
            commander = str(item.get("commander") or "")
            row = (
                aid,
                name,
                str(item.get("station") or ""),
                str(item.get("theater") or ""),
                commander,
                str(item.get("controller") or commander),
                str(item.get("troop_type") or ""),
                max(0, manpower),
                max(0, maintenance),
                _score("supply"),
                _score("morale"),
                _score("training"),
                _score("equipment"),
                _arrears_init(),
                _score("mobility"),
                _score("loyalty"),
                str(item.get("status") or "新立"),
                owner,
            )
            try:
                self.conn.execute(
                    """
                    INSERT INTO armies
                    (id, name, station, theater, commander, controller, troop_type, manpower,
                     maintenance_per_turn, supply, morale, training, equipment, arrears,
                     mobility, loyalty, status, owner_power)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
            except sqlite3.IntegrityError as exc:
                print(f"[WARN] new_armies INSERT 失败 '{aid}': {exc}")
                continue
            reason = str(item.get("reason") or item.get("status") or "新立军队")[:80]
            self.conn.execute(
                """
                INSERT INTO army_logs
                (turn, year, period, army_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                VALUES (?, ?, ?, ?, 'created', '', ?, ?, ?, 'season', NULL, ?)
                """,
                (state.turn, state.year, state.period, aid, str(manpower), manpower, reason, actor),
            )
            created.append({
                "army": name,
                "id": aid,
                "owner_power": owner,
                "manpower": manpower,
                "created": True,
                "reason": reason,
            })
        self.conn.commit()
        return created

    # ── 建筑 ──────────────────────────────────────────────────────────────────

    def add_building(
        self,
        state: GameState,
        region_id: str,
        name: str,
        category: str,
        *,
        level: int = 1,
        condition: int = 60,
        maintenance: int = 0,
        risk: int = 30,
        output_metric: str = "",
        output_amount: int = 0,
        status: str = "",
        origin: str = "decree",
    ) -> str:
        """运行时新立建筑（玩家诏书）。category / output_metric 走白名单硬校验，违规 ValueError。"""
        if category not in BUILDING_CATEGORIES:
            raise ValueError(f"建筑 category 非法 '{category}'，白名单 {BUILDING_CATEGORIES}")
        if output_metric not in BUILDING_OUTPUT_METRICS:
            raise ValueError(f"建筑 output_metric 非法 '{output_metric}'，白名单 {BUILDING_OUTPUT_METRICS}")
        if self.conn.execute("SELECT 1 FROM regions WHERE id = ?", (region_id,)).fetchone() is None:
            raise ValueError(f"建筑 region_id 引用未入库地区 '{region_id}'")
        base = re.sub(r"[^a-z0-9]+", "", (region_id or "rgn").lower()) or "rgn"
        seq = self.conn.execute(
            "SELECT COUNT(*) FROM buildings WHERE region_id = ?", (region_id,)
        ).fetchone()[0]
        building_id = f"{base}_b{int(seq) + 1}"
        while self.conn.execute("SELECT 1 FROM buildings WHERE id = ?", (building_id,)).fetchone():
            seq += 1
            building_id = f"{base}_b{int(seq) + 1}"
        self.conn.execute(
            """
            INSERT INTO buildings
            (id, region_id, name, category, level, condition, maintenance, risk,
             output_metric, output_amount, status, origin, created_turn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                building_id,
                region_id,
                name.strip()[:60] or "无名建筑",
                category,
                max(1, min(5, int(level))),
                max(0, min(100, int(condition))),
                max(0, int(maintenance)),
                max(0, min(100, int(risk))),
                output_metric,
                max(0, int(output_amount)),
                status.strip()[:160] or "新立，尚在筹建。",
                origin,
                state.turn,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO building_logs
            (turn, year, period, building_id, field, old_value, new_value, delta, reason, actor)
            VALUES (?, ?, ?, ?, 'create', '', ?, NULL, ?, '档房')
            """,
            (state.turn, state.year, state.period, building_id, name.strip()[:60], "诏书新立建筑"),
        )
        self.conn.commit()
        return building_id

    def remove_building(self, state: GameState, building_id: str, reason: str = "") -> bool:
        """拆除/废止建筑（issue 失败或撤销结案）。返回是否真删了一行。"""
        row = self.conn.execute("SELECT name FROM buildings WHERE id = ?", (building_id,)).fetchone()
        if row is None:
            return False
        self.conn.execute(
            """
            INSERT INTO building_logs
            (turn, year, period, building_id, field, old_value, new_value, delta, reason, actor)
            VALUES (?, ?, ?, ?, 'remove', ?, '', NULL, ?, '档房')
            """,
            (state.turn, state.year, state.period, building_id,
             str(row["name"]), (reason or "建筑废止").strip()[:80]),
        )
        self.conn.execute("DELETE FROM buildings WHERE id = ?", (building_id,))
        self.conn.commit()
        return True

    def apply_building_deltas(
        self,
        state: GameState,
        event: Event,
        edict_id: int | None,
        actor: str,
        building_deltas: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        """改既有建筑。仿 apply_army_deltas。供 issue effect 落地复用。"""
        changes: List[Dict[str, object]] = []
        valid_fields = set(BUILDING_SCORE_FIELDS + BUILDING_QUANTITY_FIELDS + BUILDING_TEXT_FIELDS)
        for building_id, raw_changes in building_deltas.items():
            row = self.conn.execute("SELECT * FROM buildings WHERE id = ?", (building_id,)).fetchone()
            if row is None:
                print(f"[WARN] building_delta 引用未入库建筑 '{building_id}' → 跳过")
                continue
            reason = str(raw_changes.get("reason") or event.title).strip()[:80]
            for field, value in raw_changes.items():
                if field == "reason":
                    continue
                if field not in valid_fields:
                    print(f"[WARN] building_delta 引用非法字段 '{field}' → 跳过")
                    continue
                old_value = row[field]
                if field in BUILDING_SCORE_FIELDS:
                    new_value = max(0, min(100, int(old_value) + int(value)))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                elif field == "level":
                    new_value = max(1, min(5, int(old_value) + int(value)))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field in ("maintenance", "output_amount"):
                    new_value = max(0, int(old_value) + int(value))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new = new_value
                    log_delta = actual_delta
                elif field == "output_metric":
                    text_value = str(value).strip()
                    if text_value not in BUILDING_OUTPUT_METRICS:
                        print(f"[WARN] building_delta output_metric 非法 '{text_value}' → 跳过")
                        continue
                    if text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                elif field in BUILDING_TEXT_FIELDS:
                    text_value = str(value).strip()[:160]
                    if not text_value or text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                else:
                    print(f"[WARN] building_delta 未处理字段 '{field}' → 跳过")
                    continue
                self.conn.execute(
                    f"UPDATE buildings SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, building_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO building_logs
                    (turn, year, period, building_id, field, old_value, new_value, delta, reason, event_id, edict_id, actor)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.turn, state.year, state.period, building_id, field,
                        str(old_value), str(stored_new), log_delta, reason,
                        event.id, edict_id, actor,
                    ),
                )
                changes.append({
                    "building": row["name"],
                    "field": field,
                    "label": BUILDING_FIELD_LABELS.get(field, field),
                    "old": old_value,
                    "new": stored_new,
                    "delta": log_delta,
                    "reason": reason,
                })
        self.conn.commit()
        return changes

    def buildings_report(self, region_id: str = "") -> str:
        """月末奏报 / web 用建筑盘面摘要。region_id 为空取全国。"""
        if region_id:
            rows = self.conn.execute(
                "SELECT * FROM buildings WHERE region_id = ? ORDER BY category, name", (region_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM buildings ORDER BY region_id, category, name"
            ).fetchall()
        if not rows:
            return "（暂无建筑在册）"
        lines: List[str] = []
        for r in rows:
            metric = str(r["output_metric"])
            if metric:
                out = f"产出{metric}{r['output_amount']}"
            else:
                out = "无结算产出"
            lines.append(
                f"{r['name']}（{r['category']}·{r['region_id']}）等级{r['level']}，"
                f"完好{r['condition']}，维护{r['maintenance']}{MONEY_UNIT}/{TURN_UNIT}，"
                f"风险{r['risk']}，{out}。{r['status']}"
            )
        return "\n".join(lines)

    def building_payload(self, region_id: str = "") -> List[Dict[str, object]]:
        """建筑结构化清单，供 web。region_id 为空取全国。"""
        if region_id:
            rows = self.conn.execute(
                "SELECT * FROM buildings WHERE region_id = ? ORDER BY category, name", (region_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM buildings ORDER BY region_id, category, name"
            ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "region_id": str(r["region_id"]),
                "name": str(r["name"]),
                "category": str(r["category"]),
                "level": int(r["level"]),
                "condition": int(r["condition"]),
                "maintenance": int(r["maintenance"]),
                "risk": int(r["risk"]),
                "output_metric": str(r["output_metric"]),
                "output_amount": int(r["output_amount"]),
                "status": str(r["status"]),
                "origin": str(r["origin"]),
            }
            for r in rows
        ]

    def building_detail(self, name_or_id: str) -> str:
        key = (name_or_id or "").strip()
        row = self.conn.execute(
            "SELECT * FROM buildings WHERE id = ? OR name = ?", (key, key)
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM buildings WHERE name LIKE ?", (f"%{key}%",)
            ).fetchone()
        if row is None:
            raise ValueError(f"未找到建筑 '{name_or_id}'")
        metric = str(row["output_metric"])
        out = f"产出{metric}{row['output_amount']}/{TURN_UNIT}" if metric else "无结算产出"
        return (
            f"{row['name']}（{row['category']}，{row['region_id']}，{row['origin']}）："
            f"等级{row['level']}，完好{row['condition']}，"
            f"维护{row['maintenance']}{MONEY_UNIT}/{TURN_UNIT}，风险{row['risk']}，{out}。\n"
            f"{row['status']}"
        )

    def adjust_factions(self, deltas: Dict[str, object]) -> None:
        for faction, val in deltas.items():
            if isinstance(val, dict):
                sat_d = int(val.get("satisfaction") or 0)
                lev_d = int(val.get("leverage") or 0)
            else:
                try:
                    sat_d = int(val)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                lev_d = 0
            if sat_d == 0 and lev_d == 0:
                continue
            row = self.conn.execute(
                "SELECT satisfaction, leverage FROM factions WHERE name = ?", (faction,)
            ).fetchone()
            if not row:
                continue
            new_sat = max(0, min(100, int(row["satisfaction"]) + sat_d))
            new_lev = max(0, min(100, int(row["leverage"]) + lev_d))
            self.conn.execute(
                "UPDATE factions SET satisfaction = ?, leverage = ? WHERE name = ?",
                (new_sat, new_lev, faction),
            )
        self.conn.commit()

    def turn_economy_summary(self, turn: int) -> str:
        rows = self.conn.execute(
            """
            SELECT account,
                   SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS income,
                   SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END) AS expense,
                   SUM(delta) AS net
            FROM economy_ledger
            WHERE turn = ? AND category <> '期初'
            GROUP BY account
            ORDER BY account DESC
            """,
            (turn,),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}无新增收支。"
        parts = []
        for row in rows:
            income = int(row["income"] or 0)
            expense = int(row["expense"] or 0)
            net = int(row["net"] or 0)
            parts.append(
                f"{row['account']}收入{format_money(income)}、支出{format_money(expense)}、净变{format_money_delta(net)}"
            )
        return "；".join(parts) + "。"

    def treasury_ledger(self, account: str, turns: int = 6) -> str:
        """查国库或内库最近 N 回合流水明细。"""
        rows = self.conn.execute(
            """
            SELECT turn, year, period, delta, balance_after, category, reason, actor
            FROM economy_ledger
            WHERE account = ? AND category <> '期初'
            ORDER BY id DESC
            LIMIT ?
            """,
            (account, turns * 20),
        ).fetchall()
        if not rows:
            return f"{account}无流水记录。"
        lines = [f"【{account}近{turns}回合流水（最新在前）】"]
        for r in rows:
            sign = "+" if int(r["delta"]) > 0 else ""
            lines.append(
                f"{r['year']}年{r['period']}月（turn{r['turn']}）"
                f" {sign}{format_money_delta(int(r['delta']))} → 余{format_money(int(r['balance_after']))} "
                f"[{r['category']}] {r['reason']}"
                + (f"（{r['actor']}）" if r["actor"] else "")
            )
        return "\n".join(lines)

    def previous_turn_summary(self, state: GameState) -> str:
        previous_turn = state.turn - 1
        # turn=0 是开局即位邸报（seed_opening_gazette 落库）；turn<0 才算未登基前。
        if previous_turn < 0:
            return f"登基伊始，尚无上{TURN_UNIT}回奏。"

        # 上回合奏报单独存在 turn_reports，直接取。
        report = self.get_turn_report(previous_turn)
        if report:
            return report
        if previous_turn == 0:
            return f"登基伊始，尚无上{TURN_UNIT}回奏。"

        logs = self.conn.execute(
            "SELECT message FROM turn_logs WHERE turn = ? ORDER BY id",
            (previous_turn,),
        ).fetchall()
        if not logs:
            return f"上{TURN_UNIT}未见正式记录。"

        lines = [
            f"上{TURN_UNIT}回顾：",
            f"钱粮：{self.turn_economy_summary(previous_turn)}",
            f"地区：{self.turn_region_summary(previous_turn)}",
            f"军队：{self.turn_army_summary(previous_turn)}",
            f"势力：{self.turn_power_summary(previous_turn)}",
        ]
        return "\n".join(lines)

    def record_log(self, state: GameState, message: str) -> None:
        self.conn.execute(
            "INSERT INTO turn_logs (turn, year, period, message) VALUES (?, ?, ?, ?)",
            (state.turn, state.year, state.period, message),
        )
        self.conn.commit()

    def append_chat_message(self, minister_name: str, turn: int, role: str, content: str) -> None:
        """召对聊天单条消息落库（chat_messages）。"""
        self.conn.execute(
            "INSERT INTO chat_messages (minister_name, turn, role, content) VALUES (?, ?, ?, ?)",
            (minister_name, turn, role, content),
        )
        self.conn.commit()

    def load_all_chat_history(self) -> Dict[str, List[Dict[str, str]]]:
        """读出全部召对记录，按大臣分组，供进程启动时恢复内存缓存。"""
        rows = self.conn.execute(
            "SELECT minister_name, role, content FROM chat_messages ORDER BY id"
        ).fetchall()
        history: Dict[str, List[Dict[str, str]]] = {}
        for row in rows:
            history.setdefault(row["minister_name"], []).append(
                {"role": row["role"], "content": row["content"]}
            )
        return history

    # ----- event memories（渐进式记忆：摘要卡 + 来源摘录） -----

    def upsert_event_memory(
        self,
        state: GameState,
        subject_type: str,
        subject_id: str,
        event_type: str,
        title: str,
        cause: str = "",
        process: str = "",
        outcome: str = "",
        sentiment: str = "neutral",
        importance: int = 3,
        tags: Optional[List[str]] = None,
        source_kind: str = "system",
        source_id: str = "",
        expires_turn: Optional[int] = None,
    ) -> int:
        """写入/更新一张事件记忆摘要卡，按主体+类型+来源去重。"""
        subject_type = (subject_type or "").strip()
        subject_id = (subject_id or "").strip()
        event_type = (event_type or "").strip()
        source_kind = (source_kind or "system").strip()
        source_id = str(source_id or "").strip()
        if not subject_type or not subject_id or not event_type or not source_id:
            return 0
        importance = max(1, min(5, int(importance or 3)))
        if expires_turn is None:
            # 按重要度自动衰减；importance=5 永久保留（None）
            _ttl = {1: 6, 2: 12, 3: 24, 4: 48}
            ttl = _ttl.get(importance)
            if ttl is not None:
                expires_turn = int(state.turn) + ttl
        clean_tags = []
        for tag in tags or []:
            t = str(tag).strip()
            if t and t not in clean_tags:
                clean_tags.append(t[:40])
        existed = self.conn.execute(
            """
            SELECT id FROM event_memories
            WHERE subject_type=? AND subject_id=? AND event_type=? AND source_kind=? AND source_id=?
            """,
            (subject_type, subject_id, event_type, source_kind, source_id),
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO event_memories
                (subject_type, subject_id, turn, year, period, event_type, title,
                 cause, process, outcome, sentiment, importance, tags,
                 source_kind, source_id, expires_turn)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_type, subject_id, event_type, source_kind, source_id)
            DO UPDATE SET
                turn = excluded.turn,
                year = excluded.year,
                period = excluded.period,
                title = excluded.title,
                cause = excluded.cause,
                process = excluded.process,
                outcome = excluded.outcome,
                sentiment = excluded.sentiment,
                importance = excluded.importance,
                tags = excluded.tags,
                expires_turn = excluded.expires_turn,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                subject_type, subject_id, state.turn, state.year, state.period,
                event_type, str(title or "")[:40], str(cause or "")[:80],
                str(process or "")[:80], str(outcome or "")[:80],
                sentiment if sentiment in {"positive", "neutral", "negative", "mixed"} else "neutral",
                importance, json.dumps(clean_tags, ensure_ascii=False),
                source_kind, source_id, expires_turn,
            ),
        )
        row = self.conn.execute(
            """
            SELECT id FROM event_memories
            WHERE subject_type=? AND subject_id=? AND event_type=? AND source_kind=? AND source_id=?
            """,
            (subject_type, subject_id, event_type, source_kind, source_id),
        ).fetchone()
        self.conn.commit()
        action = "更新" if existed else "保存"
        tlog(
            f"[memory/{action}] #{int(row['id']) if row else '?'} "
            f"{subject_type}:{subject_id} {event_type}《{str(title or '')[:24]}》"
            f" imp={importance} src={source_kind}:{source_id}"
        )
        tlog(
            f"[MEM-IO/db.upsert/BODY] #{int(row['id']) if row else '?'} "
            f"title={str(title or '')!r} cause={str(cause or '')!r} "
            f"process={str(process or '')!r} outcome={str(outcome or '')!r} "
            f"sentiment={sentiment} tags={clean_tags} expires_turn={expires_turn}"
        )
        return int(row["id"]) if row else 0

    def add_event_memory_source(
        self,
        memory_id: int,
        source_kind: str,
        source_id: str,
        excerpt: str = "",
        locator: Optional[Dict[str, object]] = None,
    ) -> None:
        if not memory_id:
            return
        locator_json = json.dumps(locator or {}, ensure_ascii=False, sort_keys=True)
        self.conn.execute(
            """
            INSERT INTO event_memory_sources
                (memory_id, source_kind, source_id, excerpt, locator)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(memory_id, source_kind, source_id, locator)
            DO UPDATE SET
                excerpt = excluded.excerpt,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(memory_id), str(source_kind or "system"), str(source_id or ""),
                str(excerpt or "")[:200], locator_json,
            ),
        )
        self.conn.commit()
        tlog(
            f"[memory/source] memory=#{int(memory_id)} {source_kind}:{source_id} "
            f"excerpt={str(excerpt or '')[:48]}"
        )

    def prune_event_memories_for_turn(self, turn: int, per_subject: int = 3) -> None:
        """同一主体同回合只保留若干高价值摘要卡，避免记忆膨胀。"""
        rows = self.conn.execute(
            """
            SELECT id, subject_type, subject_id, importance, updated_at
            FROM event_memories
            WHERE turn = ?
            ORDER BY subject_type, subject_id, importance DESC, id DESC
            """,
            (int(turn),),
        ).fetchall()
        seen: Dict[Tuple[str, str], int] = {}
        delete_ids: List[int] = []
        for row in rows:
            key = (row["subject_type"], row["subject_id"])
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > per_subject:
                delete_ids.append(int(row["id"]))
        if delete_ids:
            placeholders = ",".join("?" for _ in delete_ids)
            self.conn.execute(f"DELETE FROM event_memory_sources WHERE memory_id IN ({placeholders})", delete_ids)
            self.conn.execute(f"DELETE FROM event_memories WHERE id IN ({placeholders})", delete_ids)
            self.conn.commit()
            tlog(f"[memory/prune] turn={turn} deleted={delete_ids}")

    def get_relevant_event_memories(
        self,
        character_name: str,
        faction: str,
        office_type: str,
        turn: int,
        limit: int = 5,
        ignore_expiry: bool = False,
    ) -> List[Dict[str, object]]:
        """召见前取少量相关旧事摘要；纯结构化检索，不走向量库。
        ignore_expiry=True 时按历史时点查，不受 expires_turn 过滤。
        """
        active_issues = self.list_active_issues()
        active_issue_tags: List[str] = []
        for issue in active_issues[:12]:
            active_issue_tags.append(f"#{int(issue['id'])}")
            if issue["title"]:
                active_issue_tags.append(str(issue["title"])[:20])
        tag_needles = [character_name, faction, office_type] + active_issue_tags
        expiry_clause = "" if ignore_expiry else "AND (expires_turn IS NULL OR expires_turn >= ?)"
        params: list = [int(turn)]
        if not ignore_expiry:
            params.append(int(turn))
        params += [character_name, faction, f"%{character_name}%", f"%{faction}%", f"%{office_type}%"]
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM event_memories
            WHERE turn <= ?
              {expiry_clause}
              AND (
                (subject_type='character' AND subject_id=?)
                OR (subject_type='faction' AND subject_id=?)
                OR (subject_type='court' AND importance>=4)
                OR tags LIKE ?
                OR tags LIKE ?
                OR tags LIKE ?
              )
            """,
            params,
        ).fetchall()
        scored: List[Tuple[int, sqlite3.Row, List[str]]] = []
        for row in rows:
            age = max(0, int(turn) - int(row["turn"]))
            if int(row["importance"]) <= 1 and not (
                row["subject_type"] == "character" and row["subject_id"] == character_name and age <= 3
            ):
                continue
            try:
                tags = json.loads(row["tags"] or "[]")
            except Exception:
                tags = []
            tag_matches = [t for t in tag_needles if t and any(str(t) in str(tag) or str(tag) in str(t) for tag in tags)]
            exact = row["subject_type"] == "character" and row["subject_id"] == character_name
            active_hit = any(str(t).startswith("#") or t in active_issue_tags for t in tag_matches)
            score = (
                int(row["importance"]) * 10
                + (20 if exact else 0)
                + len(tag_matches) * 4
                + max(0, 10 - age)
                + (12 if active_hit else 0)
            )
            scored.append((score, row, tag_matches))
        scored.sort(key=lambda item: (item[0], int(item[1]["turn"]), int(item[1]["id"])), reverse=True)
        result: List[Dict[str, object]] = []
        for _score, row, _matches in scored[:limit]:
            result.append({
                "id": int(row["id"]),
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "turn": int(row["turn"]),
                "year": int(row["year"]),
                "period": int(row["period"]),
                "event_type": row["event_type"],
                "title": row["title"],
                "cause": row["cause"],
                "process": row["process"],
                "outcome": row["outcome"],
                "sentiment": row["sentiment"],
                "importance": int(row["importance"]),
                "tags": json.loads(row["tags"] or "[]"),
            })
        if result:
            ids = ",".join(str(item["id"]) for item in result)
            tlog(f"[memory/recall] {character_name} hit={len(result)} ids={ids}")
            tlog(f"[MEM-IO/db.recall/OUTPUT] {character_name} full={json.dumps(result, ensure_ascii=False)}")
        else:
            tlog(f"[memory/recall] {character_name} hit=0")
        return result

    def get_recent_event_memories(
        self,
        turn: int,
        window: int = 5,
        limit: int = 100,
    ) -> List[Dict[str, object]]:
        """取近 window 回合内所有 event_memories，按 turn/id 升序，上限 limit 条。"""
        since = max(1, turn - window + 1)
        rows = self.conn.execute(
            """
            SELECT id, subject_type, subject_id, turn, year, period,
                   event_type, title, cause, process, outcome, sentiment, importance, tags
            FROM event_memories
            WHERE turn >= ? AND turn <= ?
            ORDER BY turn ASC, id ASC
            LIMIT ?
            """,
            (since, turn, limit),
        ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": int(row["id"]),
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "turn": int(row["turn"]),
                "year": int(row["year"]),
                "period": int(row["period"]),
                "event_type": row["event_type"],
                "title": row["title"],
                "cause": row["cause"],
                "process": row["process"],
                "outcome": row["outcome"],
                "sentiment": row["sentiment"],
                "importance": int(row["importance"]),
                "tags": json.loads(row["tags"] or "[]"),
            })
        tlog(f"[memory/recent] turn={turn} window={window} hit={len(result)}")
        if result:
            tlog(f"[MEM-IO/db.recent/OUTPUT] turn={turn} window={window} full={json.dumps(result, ensure_ascii=False)}")
        return result

    def get_memories_by_keywords(
        self,
        keywords: List[str],
        turn: int,
        limit: int = 10,
        ignore_expiry: bool = False,
    ) -> List[Dict[str, object]]:
        """推演前按关键词集合检索相关记忆，供 simulator/extractor 注入。

        keywords 来自 memory_retrieval agent 抽取的人名/地区/军队/势力/操作词。
        每个词对 tags JSON 做 LIKE 匹配，命中任一词即入候选，按 importance+时效评分。
        ignore_expiry=True 时按历史时点查，不受 expires_turn 过滤。
        """
        if not keywords:
            return []
        active_issue_tags = [
            f"#{int(r['id'])}"
            for r in self.conn.execute(
                "SELECT id FROM issues WHERE status='active'"
            ).fetchall()
        ]
        needles = list(dict.fromkeys([k for k in keywords if k] + active_issue_tags))
        like_clauses = " OR ".join(["tags LIKE ?" for _ in needles])
        like_params = [f"%{n}%" for n in needles]
        expiry_clause = "" if ignore_expiry else "AND (expires_turn IS NULL OR expires_turn >= ?)"
        base_params: list = [int(turn)]
        if not ignore_expiry:
            base_params.append(int(turn))

        rows = self.conn.execute(
            f"""
            SELECT * FROM event_memories
            WHERE turn <= ?
              {expiry_clause}
              AND ({like_clauses})
            ORDER BY importance DESC, turn DESC
            LIMIT ?
            """,
            base_params + like_params + [limit * 3],
        ).fetchall()

        scored: List[tuple] = []
        for row in rows:
            age = max(0, int(turn) - int(row["turn"]))
            try:
                tags = json.loads(row["tags"] or "[]")
            except Exception:
                tags = []
            hit_count = sum(
                1 for n in needles
                if any(n in str(t) or str(t) in n for t in tags)
            )
            score = int(row["importance"]) * 10 + hit_count * 5 + max(0, 8 - age)
            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        result = []
        for _score, row in scored[:limit]:
            result.append({
                "id": int(row["id"]),
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "turn": int(row["turn"]),
                "year": int(row["year"]),
                "period": int(row["period"]),
                "title": row["title"],
                "cause": row["cause"],
                "outcome": row["outcome"],
                "importance": int(row["importance"]),
                "tags": json.loads(row["tags"] or "[]"),
                "source_kind": row["source_kind"],  # 演算记忆 vs 大臣记忆
            })
        tlog(f"[memory/keywords] needles={len(needles)} hit={len(result)}")
        tlog(f"[MEM-IO/db.keywords/INPUT] keywords={keywords} turn={turn} ignore_expiry={ignore_expiry} needles={needles}")
        if result:
            tlog(f"[MEM-IO/db.keywords/OUTPUT] full={json.dumps(result, ensure_ascii=False)}")
        return result

    def event_memory_detail(self, memory_id: int) -> str:
        tlog(f"[memory/detail] request=#{int(memory_id)}")
        memory = self.conn.execute(
            "SELECT * FROM event_memories WHERE id = ?",
            (int(memory_id),),
        ).fetchone()
        if memory is None:
            return f"未找到旧事记忆 #{memory_id}。"
        sources = self.conn.execute(
            """
            SELECT source_kind, source_id, excerpt, locator
            FROM event_memory_sources
            WHERE memory_id = ?
            ORDER BY id
            """,
            (int(memory_id),),
        ).fetchall()
        header = (
            f"旧事 #{memory['id']}：{memory['year']}年{memory['period']}月，{memory['title']}。"
            f"起因：{memory['cause']}。经过：{memory['process']}。结果：{memory['outcome']}。"
        )
        if not sources:
            return header + "\n未存原始摘录。"
        lines = [header, "来源摘录："]
        for idx, row in enumerate(sources, 1):
            locator = row["locator"] or "{}"
            lines.append(
                f"{idx}. [{row['source_kind']}:{row['source_id']}] {row['excerpt']}"
                + (f"（定位 {locator}）" if locator and locator != "{}" else "")
            )
        out = "\n".join(lines)
        tlog(f"[MEM-IO/db.detail/OUTPUT] #{memory_id} ({len(out)}字):\n{out}")
        return out

    def save_turn_report(self, state: GameState, report: str) -> None:
        """每回合月末奏报单独存档（turn_reports），与 turn_logs 日志解耦。"""
        self.conn.execute(
            """
            INSERT INTO turn_reports (turn, year, period, report)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(turn) DO UPDATE SET
                year = excluded.year,
                period = excluded.period,
                report = excluded.report
            """,
            (state.turn, state.year, state.period, report),
        )
        self.conn.commit()

    def get_turn_report(self, turn: int) -> str:
        row = self.conn.execute(
            "SELECT report FROM turn_reports WHERE turn = ?",
            (turn,),
        ).fetchone()
        return (row["report"] if row else "") or ""

    def list_archived_turns(self) -> List[Dict[str, object]]:
        """所有已存档回合（turn_reports/turn_extractions/turn_directives 任一有数据）。
        返回按 turn 升序的元信息列表，每项含 turn/year/period 与各来源是否存在。"""
        rows = self.conn.execute(
            """
            SELECT t.turn AS turn,
                   MAX(t.year) AS year,
                   MAX(t.period) AS period,
                   MAX(t.has_report) AS has_report,
                   MAX(t.has_extraction) AS has_extraction,
                   MAX(t.has_directive) AS has_directive
            FROM (
                SELECT turn, year, period, 1 AS has_report, 0 AS has_extraction, 0 AS has_directive
                FROM turn_reports
                UNION ALL
                SELECT turn, year, period, 0, 1, 0 FROM turn_extractions
                UNION ALL
                SELECT turn, year, period, 0, 0, 1 FROM turn_directives
                WHERE status = 'issued'
            ) AS t
            GROUP BY t.turn
            ORDER BY t.turn
            """
        ).fetchall()
        return [
            {
                "turn": int(r["turn"]),
                "year": int(r["year"]),
                "period": int(r["period"]),
                "has_report": bool(r["has_report"]),
                "has_extraction": bool(r["has_extraction"]),
                "has_directive": bool(r["has_directive"]),
            }
            for r in rows
        ]

    def list_directives_by_turn(self, turn: int) -> List[Dict[str, object]]:
        """读某回合已颁诏（issued）草案，按 id 升序。"""
        rows = self.conn.execute(
            """
            SELECT d.id, d.turn, d.year, d.period, d.event_id, d.actor,
                   d.skill_id, d.text, d.source, d.status, d.notes,
                   d.created_at, d.updated_at,
                   e.title AS event_title
            FROM turn_directives d
            LEFT JOIN events e ON e.id = d.event_id
            WHERE d.turn = ? AND d.status = 'issued'
            ORDER BY d.id
            """,
            (int(turn),),
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "turn": int(r["turn"]),
                "year": int(r["year"]),
                "period": int(r["period"]),
                "event_id": r["event_id"] or "",
                "event_title": r["event_title"] or "",
                "actor": r["actor"] or "",
                "skill_id": r["skill_id"] or "",
                "text": r["text"] or "",
                "source": r["source"] or "",
                "status": r["status"] or "",
                "notes": r["notes"] or "",
                "created_at": r["created_at"] or "",
                "updated_at": r["updated_at"] or "",
            }
            for r in rows
        ]

    def save_turn_extraction(
        self,
        state: GameState,
        decree_text: str = "",
        narrative: str = "",
        extractor_input: str = "",
        extractor_output: str = "",
    ) -> None:
        """推演链原始输入/输出留痕（turn_extractions），事后可追可重放。"""
        self.conn.execute(
            """
            INSERT INTO turn_extractions
                (turn, year, period, decree_text, narrative, extractor_input, extractor_output)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn) DO UPDATE SET
                year = excluded.year,
                period = excluded.period,
                decree_text = excluded.decree_text,
                narrative = excluded.narrative,
                extractor_input = excluded.extractor_input,
                extractor_output = excluded.extractor_output
            """,
            (state.turn, state.year, state.period, decree_text, narrative,
             extractor_input, extractor_output),
        )
        self.conn.commit()

    def get_turn_extraction(self, turn: int) -> Optional[Dict[str, object]]:
        """读 turn_extractions 一行；extractor_output JSON 解析失败时原样回字符串。"""
        row = self.conn.execute(
            "SELECT turn, year, period, decree_text, narrative, extractor_input, extractor_output "
            "FROM turn_extractions WHERE turn = ?",
            (int(turn),),
        ).fetchone()
        if row is None:
            return None
        def _parse(text: str) -> object:
            text = (text or "").strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except Exception:
                pass
            # LLM 多输出一个 }，顶层提前关闭，trailing 是被截出的字段。
            # 去掉多余 }，接回 trailing（trailing 本身以顶层 } 结尾）。
            try:
                dec = json.JSONDecoder()
                obj, end = dec.raw_decode(text)
                trailing = text[end:].strip()
                if trailing.startswith(","):
                    prefix = text[:end].rstrip()
                    if prefix.endswith("}"):
                        fixed = prefix[:-1] + trailing
                        try:
                            return json.loads(fixed)
                        except Exception:
                            pass
                return obj
            except Exception:
                pass
            return text
        return {
            "turn": int(row["turn"]),
            "year": int(row["year"]),
            "period": int(row["period"]),
            "decree_text": row["decree_text"] or "",
            "narrative": row["narrative"] or "",
            "extractor_input": _parse(row["extractor_input"] or ""),
            "extractor_output": _parse(row["extractor_output"] or ""),
        }

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

    def add_directive(
        self,
        state: GameState,
        event: Event | None,
        text: str,
        source: str,
        actor: str = "",
        skill_id: str = "",
        notes: str = "",
        status: str = "draft",
    ) -> int:
        # status: 'draft'=已确认颁诏候选；'pending'=大臣拟旨待皇帝核定。
        cursor = self.conn.execute(
            """
            INSERT INTO turn_directives
            (turn, year, period, event_id, actor, skill_id, text, source, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (state.turn, state.year, state.period, event.id if event else "",
             actor, skill_id, text, source, status, notes),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_directives(
        self, state: GameState, statuses: Tuple[str, ...] = ("draft",)
    ) -> List[sqlite3.Row]:
        # 默认只取 draft（颁诏候选）；UI 列表传 ('pending','draft') 一起取，前端按 status 分区。
        placeholders = ",".join("?" for _ in statuses)
        return self.conn.execute(
            f"""
            SELECT d.*, e.title AS event_title
            FROM turn_directives d
            LEFT JOIN events e ON e.id = d.event_id
            WHERE d.turn = ? AND d.status IN ({placeholders})
            ORDER BY d.id
            """,
            (state.turn, *statuses),
        ).fetchall()

    def confirm_directive(self, directive_id: int) -> None:
        """大臣拟旨经皇帝核定：pending → draft（进入颁诏候选池）。"""
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'draft', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (directive_id,),
        )
        self.conn.commit()

    def reject_directive(self, directive_id: int) -> None:
        """皇帝驳回大臣拟旨：pending → rejected。"""
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'rejected', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (directive_id,),
        )
        self.conn.commit()

    def count_pending_directives(self, state: GameState) -> int:
        """本回合待核定（pending）的大臣拟旨数。颁诏前须为 0。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM turn_directives WHERE turn = ? AND status = 'pending'",
            (state.turn,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def update_directive_text(self, directive_id: int, text: str) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET text = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (text, directive_id),
        )
        self.conn.commit()

    def update_directive(
        self,
        directive_id: int,
        event: Event,
        actor: str,
        skill_id: str,
        text: str,
        notes: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET event_id = ?,
                actor = ?,
                skill_id = ?,
                text = ?,
                notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (event.id, actor, skill_id, text, notes, directive_id),
        )
        self.conn.commit()

    def delete_directive(self, directive_id: int) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (directive_id,),
        )
        self.conn.commit()

    def mark_directives_issued(self, state: GameState) -> None:
        self.conn.execute(
            """
            UPDATE turn_directives
            SET status = 'issued', updated_at = CURRENT_TIMESTAMP
            WHERE turn = ? AND status = 'draft'
            """,
            (state.turn,),
        )
        self.conn.commit()

    # ----- issues (双类事项 + 双向进度条) -----

    def _derive_issue_phase(self, bar: int) -> str:
        if bar <= 0:
            return "终"
        if bar < 30:
            return "起"
        if bar < 70:
            return "中"
        if bar < 100:
            return "终前"
        return "终"

    def list_active_issues(self, kind: str | None = None) -> List[sqlite3.Row]:
        sql = "SELECT * FROM issues WHERE status = 'active'"
        args: List[object] = []
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY severity DESC, id ASC"
        return self.conn.execute(sql, args).fetchall()

    def list_closed_issues_at(self, closed_turn: int) -> List[sqlite3.Row]:
        """指定 turn 关闭（resolved / failed / dropped）的 issue。"""
        return self.conn.execute(
            "SELECT * FROM issues WHERE closed_turn = ? AND status IN ('resolved','failed','dropped') ORDER BY id",
            (int(closed_turn),),
        ).fetchall()

    def count_active_initiatives(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM issues WHERE kind='initiative' AND status='active'"
        ).fetchone()
        return int(row["n"] or 0)

    def find_active_issue_by_origin(self, origin_kind: str, origin_ref: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM issues WHERE origin_kind=? AND origin_ref=? AND status='active' LIMIT 1",
            (origin_kind, origin_ref),
        ).fetchone()

    def find_any_issue_by_origin(self, origin_kind: str, origin_ref: str) -> sqlite3.Row | None:
        """查任意状态（含 resolved/failed/dropped）的同源 issue，用于 spawn 去重。"""
        return self.conn.execute(
            "SELECT * FROM issues WHERE origin_kind=? AND origin_ref=? LIMIT 1",
            (origin_kind, origin_ref),
        ).fetchone()

    def has_event_triggered(self, event_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM event_triggers WHERE event_id=? LIMIT 1",
            (event_id,),
        ).fetchone()
        return row is not None

    def mark_event_triggered(self, state: GameState, event_id: str, source: str = "simulation") -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO event_triggers (event_id, turn, year, period, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, state.turn, state.year, state.period, source),
        )
        self.conn.commit()

    def insert_issue(
        self,
        state: GameState,
        *,
        kind: str,
        title: str,
        origin_kind: str = "",
        origin_ref: str = "",
        bar_value: int = 40,
        bar_good_meaning: str = "已平",
        bar_bad_meaning: str = "失控",
        inertia: int = 0,
        stage_text: str = "",
        severity: int = 50,
        region_hint: str = "",
        faction_hint: str = "",
        tags: List[str] | None = None,
        ongoing_effects: Dict[str, object] | None = None,
        cancellable: str = "never",
        cancel_cost: Dict[str, object] | None = None,
        effect_on_resolve: Dict[str, object] | None = None,
        effect_on_fail: Dict[str, object] | None = None,
        resolve_condition: str = "",
        fail_condition: str = "",
    ) -> int:
        if kind not in ("situation", "initiative"):
            raise ValueError(f"issue kind 非法：{kind}")
        if cancellable not in ("decree", "never", "by_progress"):
            raise ValueError(f"cancellable 非法：{cancellable}")
        bar_value = max(0, min(100, int(bar_value)))
        phase = self._derive_issue_phase(bar_value)
        cur = self.conn.execute(
            """
            INSERT INTO issues (
                kind, title, origin_kind, origin_ref, origin_turn,
                bar_value, bar_good_meaning, bar_bad_meaning, inertia,
                phase, stage_text, status, severity, region_hint, faction_hint,
                tags, ongoing_effects, cancellable, cancel_cost,
                effect_on_resolve, effect_on_fail, resolve_condition, fail_condition,
                last_advance_turn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind, title, origin_kind, origin_ref, state.turn,
                bar_value, bar_good_meaning, bar_bad_meaning, int(inertia),
                phase, stage_text, int(severity), region_hint, faction_hint,
                json.dumps(tags or [], ensure_ascii=False),
                json.dumps(ongoing_effects or {}, ensure_ascii=False),
                cancellable,
                json.dumps(cancel_cost or {}, ensure_ascii=False),
                json.dumps(effect_on_resolve or {}, ensure_ascii=False),
                json.dumps(effect_on_fail or {}, ensure_ascii=False),
                resolve_condition, fail_condition,
                state.turn,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def advance_issue(
        self,
        state: GameState,
        issue_id: int,
        *,
        trigger_kind: str,
        trigger_ref: str = "",
        delta_bar: int = 0,
        stage_text: str = "",
        narrative: str = "",
        metric_delta: Dict[str, int] | None = None,
        inertia_delta: int = 0,
    ) -> sqlite3.Row | None:
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None
        # 崩坏能力由 effect_on_fail 是否非空判定：有崩坏效果=会崩坏（bar 能到 0、failed 终结）；
        # 空=不会崩坏（天灾/正面机遇等不可控或无失败态局势，bar 下限钳到 1，永不 failed，
        # 只靠 ongoing_effects 每月持续流血）。
        can_collapse = bool(json.loads(row["effect_on_fail"] or "{}"))
        floor = 0 if can_collapse else 1
        # clamp single advance
        delta_bar = max(-50, min(50, int(delta_bar)))
        from_value = int(row["bar_value"])
        to_value = max(floor, min(100, from_value + delta_bar))
        actual_delta = to_value - from_value
        from_stage_text = row["stage_text"]
        to_stage_text = stage_text or from_stage_text
        new_phase = self._derive_issue_phase(to_value)
        new_status = row["status"]
        closed_turn = row["closed_turn"]
        if to_value >= 100:
            new_status = "resolved"
            closed_turn = state.turn
        elif to_value <= 0 and can_collapse:
            new_status = "failed"
            closed_turn = state.turn
        # inertia 可被本次行动改变（钳到 -10..+10 五档区间）
        new_inertia = int(row["inertia"]) + int(inertia_delta)
        new_inertia = max(-10, min(10, new_inertia))
        self.conn.execute(
            """
            UPDATE issues SET bar_value=?, phase=?, stage_text=?, status=?, inertia=?,
                              closed_turn=?, last_advance_turn=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (to_value, new_phase, to_stage_text, new_status, new_inertia, closed_turn, state.turn, issue_id),
        )
        self.conn.execute(
            """
            INSERT INTO issue_advances (
                issue_id, turn, trigger_kind, trigger_ref,
                delta_bar, from_value, to_value,
                from_stage_text, to_stage_text, narrative, metric_delta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue_id, state.turn, trigger_kind, trigger_ref,
                actual_delta, from_value, to_value,
                from_stage_text, to_stage_text, narrative,
                json.dumps(metric_delta or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    def close_issue(
        self,
        state: GameState,
        issue_id: int,
        *,
        reason: str,
        narrative: str = "",
    ) -> sqlite3.Row | None:
        """LLM 主动通知收尾。reason 必须是 'resolved' 或 'failed'。不看 bar 门槛。"""
        if reason not in ("resolved", "failed"):
            raise ValueError(f"close_issue reason 非法：{reason}")
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None
        # 不可崩坏局势（effect_on_fail 空：天灾/不可控灾害）没有「失败终结」态——LLM 误判 failed
        # 时拒绝结案，留 active 继续靠 ongoing_effects 流血，只能靠 resolved（赈济平息）收尾。
        if reason == "failed" and not json.loads(row["effect_on_fail"] or "{}"):
            print(f"[INFO] close_issue 已拒：issue {issue_id}（{row['title']}）无 effect_on_fail，不可崩坏，保持 active。")
            return None
        from_value = int(row["bar_value"])
        # resolved → 抬到 100；failed → 压到 0；用于 inertia/UI 一眼看懂
        to_value = 100 if reason == "resolved" else 0
        actual_delta = to_value - from_value
        from_stage_text = row["stage_text"]
        to_stage_text = narrative or from_stage_text
        new_phase = self._derive_issue_phase(to_value)
        self.conn.execute(
            """
            UPDATE issues SET bar_value=?, phase=?, stage_text=?, status=?,
                              closed_turn=?, last_advance_turn=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (to_value, new_phase, to_stage_text, reason, state.turn, state.turn, issue_id),
        )
        self.conn.execute(
            """
            INSERT INTO issue_advances (
                issue_id, turn, trigger_kind, trigger_ref,
                delta_bar, from_value, to_value,
                from_stage_text, to_stage_text, narrative, metric_delta
            ) VALUES (?, ?, 'close', ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                issue_id, state.turn, reason,
                actual_delta, from_value, to_value,
                from_stage_text, to_stage_text, narrative,
            ),
        )
        self.conn.commit()
        return self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    def cancel_issue(
        self,
        state: GameState,
        issue_id: int,
        *,
        narrative: str = "",
        applied_cost: Dict[str, object] | None = None,
    ) -> sqlite3.Row | None:
        row = self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None
        self.conn.execute(
            "UPDATE issues SET status='dropped', closed_turn=?, last_advance_turn=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (state.turn, state.turn, issue_id),
        )
        self.conn.execute(
            """
            INSERT INTO issue_advances (
                issue_id, turn, trigger_kind, delta_bar,
                from_value, to_value, narrative, metric_delta
            ) VALUES (?, ?, 'cancel', 0, ?, ?, ?, ?)
            """,
            (
                issue_id, state.turn,
                int(row["bar_value"]), int(row["bar_value"]),
                narrative,
                json.dumps(applied_cost or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return self.conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    def list_recent_issue_advances(self, issue_id: int, limit: int = 3) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM issue_advances WHERE issue_id=? ORDER BY id DESC LIMIT ?",
            (issue_id, limit),
        ).fetchall()

    def record_issue_economy_move(
        self,
        state: GameState,
        account: str,
        delta: int,
        category: str,
        reason: str,
        purpose: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
    ) -> int:
        """记一笔经济流水到 economy_ledger，同步更新 metrics[account]。

        purpose/target_kind/target_id 仅对 extractor 抽出的 economy_moves（自由拨款）填，
        flows 月固定支出与所有收入一律 None。受控枚举见 constants.ECONOMY_PURPOSES。
        """
        before = int(state.metrics[account])
        after = max(0, before + int(delta))
        actual = after - before
        if actual == 0:
            return 0
        state.metrics[account] = after
        self.conn.execute(
            """
            INSERT INTO economy_ledger
            (turn, year, period, account, delta, balance_after, category, reason,
             event_id, edict_id, actor, purpose, target_kind, target_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '事项推演', ?, ?, ?)
            """,
            (state.turn, state.year, state.period, account, actual, after,
             category, reason, purpose, target_kind, target_id),
        )
        self.sync_economy_accounts(state)
        self.conn.commit()
        return actual

    def kv_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv_store(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, value),
        )
        self.conn.commit()

    # ----- secret_orders（密令系统）-----

    def create_secret_order(
        self,
        state: GameState,
        minister_name: str,
        title: str,
        content: str,
        tags: List[str],
        importance: int = 4,
        deadline_months: int = 0,
    ) -> int:
        active_count = self.conn.execute(
            "SELECT COUNT(*) FROM secret_orders WHERE status='active'"
        ).fetchone()[0]
        if active_count >= 20:
            raise ValueError(f"进行中密令已达上限（20条），请先结案部分密令再下新令。当前：{active_count} 条。")
        tags_json = json.dumps(tags, ensure_ascii=False)
        deadline = max(0, min(int(deadline_months or 0), 36))
        due_turn = int(state.turn) + deadline if deadline else 0
        cur = self.conn.execute(
            """
            INSERT INTO secret_orders
                (turn_issued, due_turn, year_issued, period_issued, minister_name, title, content, tags, importance, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (state.turn, due_turn, state.year, state.period, minister_name, title[:20], content, tags_json, importance),
        )
        self.conn.commit()
        tlog(f"[secret_order] create id={cur.lastrowid} minister={minister_name} title={title[:20]}")
        return cur.lastrowid  # type: ignore[return-value]

    def list_secret_orders(
        self,
        status: Optional[str] = None,
        minister_name: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if minister_name:
            clauses.append("minister_name = ?")
            params.append(minister_name)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM secret_orders {where} ORDER BY id DESC",
            params,
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "turn_issued": int(r["turn_issued"]),
                "due_turn": int(r["due_turn"] if "due_turn" in r.keys() else 0),
                "year_issued": int(r["year_issued"]),
                "period_issued": int(r["period_issued"]),
                "minister_name": r["minister_name"],
                "title": r["title"],
                "content": r["content"],
                "tags": json.loads(r["tags"] or "[]"),
                "importance": int(r["importance"]),
                "status": r["status"],
                "result": r["result"] or "",
                "sim_note": (r["sim_note"] if "sim_note" in r.keys() else "") or "",
                "turn_closed": r["turn_closed"],
            }
            for r in rows
        ]

    def get_active_secret_orders_for_minister(self, minister_name: str) -> List[Dict[str, object]]:
        """返回该大臣名下未结案密令（active + pending_review）。done/failed 已结案不再返回。"""
        active = self.list_secret_orders(status="active", minister_name=minister_name)
        pending = self.list_secret_orders(status="pending_review", minister_name=minister_name)
        return active + pending

    def close_secret_order(self, order_id: int, status: str, result: str, turn_closed: int) -> None:
        self.conn.execute(
            """
            UPDATE secret_orders
            SET status = ?, result = ?, turn_closed = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, result, turn_closed, int(order_id)),
        )
        self.conn.commit()
        tlog(f"[secret_order] close id={order_id} status={status}")

    def submit_secret_order_for_review(self, order_id: int, claim: str, year: int, period: int) -> bool:
        """大臣提交密令待推演核议：active → pending_review。
        claim 按月戳追加进 result 时间线（与 progress 同列，但带 "[提交核议]" 标记），
        让推演看时同时知道大臣自述。仅 active 状态可提交。"""
        row = self.conn.execute(
            "SELECT status FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if not row or row["status"] != "active":
            return False
        stamp = f"〔{period_label(year, period)}〕[提交核议] "
        note = (claim or "").strip()
        prev = self.conn.execute(
            "SELECT result FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()["result"] or ""
        lines = [ln for ln in prev.split("\n") if ln.strip()]
        lines.append(f"{stamp}{note[:300]}")
        self.conn.execute(
            """
            UPDATE secret_orders
            SET status = 'pending_review', result = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            ("\n".join(lines), int(order_id)),
        )
        self.conn.commit()
        tlog(f"[secret_order] submit_for_review id={order_id} claim={note[:60]!r}")
        return True

    def _has_secret_order_period_line(self, order_id: int, column: str, year: int, period: int) -> bool:
        """本年月该列是否已有一行（用于一回合一步闸门）。"""
        stamp = f"〔{period_label(year, period)}〕"
        row = self.conn.execute(
            f"SELECT {column} AS v FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if row is None:
            return False
        return any(ln.startswith(stamp) for ln in str(row["v"] or "").split("\n"))

    def _append_secret_order_line(
        self, order_id: int, column: str, note: str, year: int, period: int,
        reject_if_same_period: bool = False,
    ) -> bool:
        """把一条带年月戳的进展/副作用追加进密令的 result/sim_note，存成历史时间线。
        reject_if_same_period=True 时，本年月已有行则拒写（返回 False，用于一回合一步）；
        否则同年月再写替换当月行。不同年月一律新增。返回是否实际写入。"""
        assert column in ("result", "sim_note")
        stamp = f"〔{period_label(year, period)}〕"
        row = self.conn.execute(
            f"SELECT {column} AS v FROM secret_orders WHERE id = ? AND status = 'active'",
            (int(order_id),),
        ).fetchone()
        if row is None:
            return False  # 已结案或不存在，不追加
        lines = [ln for ln in str(row["v"] or "").split("\n") if ln.strip()]
        if reject_if_same_period and any(ln.startswith(stamp) for ln in lines):
            return False  # 本回合已推过一步，拒
        lines = [ln for ln in lines if not ln.startswith(stamp)]  # 去掉当月旧行
        lines.append(f"{stamp}{note.strip()}")
        # 按〔年月〕戳排序，保证时间线顺序（同月替换后不致错位）
        def _stamp_key(ln: str):
            import re as _re
            m = _re.match(r"〔(\d+)年(\d+)月〕", ln)
            return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        lines.sort(key=_stamp_key)
        self.conn.execute(
            f"UPDATE secret_orders SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("\n".join(lines), int(order_id)),
        )
        self.conn.commit()
        return True

    def update_secret_order_progress(
        self, order_id: int, progress_note: str, year: int = 0, period: int = 0
    ) -> bool:
        """承办人推进一步：按年月追加进 result 历史时间线，不改 status。
        一回合只能推一步——本回合已有进展行则拒（返回 False），不覆盖、不叠加。"""
        ok = self._append_secret_order_line(
            order_id, "result", progress_note, year, period, reject_if_same_period=True
        )
        tlog(f"[secret_order] progress id={order_id} ok={ok} note={progress_note[:40]!r}")
        return ok

    def update_secret_order_sim_note(
        self, order_id: int, sim_note: str, year: int = 0, period: int = 0
    ) -> None:
        """推演写密令副作用（泄漏/反弹等），按年月追加进 sim_note 历史时间线，
        不动 result/status。同月再写替换（推演每月一次）。与承办人进展分列。"""
        self._append_secret_order_line(order_id, "sim_note", sim_note, year, period)
        tlog(f"[secret_order] sim_note id={order_id} note={sim_note[:40]!r}")

    def rush_secret_order(
        self,
        order_id: int,
        state: GameState,
        deadline_months: int = 1,
        reason: str = "",
    ) -> Dict[str, object]:
        """缩短 active 密令期限。deadline_months<=0 表示本月立即送核议。"""
        row = self.conn.execute(
            "SELECT id, title, status, result, due_turn FROM secret_orders WHERE id = ?",
            (int(order_id),),
        ).fetchone()
        if row is None:
            raise ValueError("密令不存在")
        if row["status"] != "active":
            raise ValueError(f"当前状态 {row['status']}，不能催办")
        try:
            months = max(0, min(int(deadline_months or 0), 36))
        except (TypeError, ValueError):
            months = 1
        target_turn = int(state.turn) + months
        old_due = int(row["due_turn"] or 0)
        stamp = f"〔{period_label(state.year, state.period)}〕"
        why = (reason or "").strip()[:120] or "奉旨加急"
        prev = row["result"] or ""
        lines = [ln for ln in prev.split("\n") if ln.strip()]
        if months <= 0:
            lines.append(f"{stamp}[奉旨即核] {why}；本月即移交密旨核议。")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET status = 'pending_review', due_turn = ?, result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(state.turn), "\n".join(lines), int(order_id)),
            )
            status = "pending_review"
            due_turn = int(state.turn)
        else:
            due_turn = target_turn if old_due <= 0 else min(old_due, target_turn)
            lines.append(f"{stamp}[奉旨加急] {why}；御限改为 {months} 个月内核议。")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET due_turn = ?, result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (due_turn, "\n".join(lines), int(order_id)),
            )
            status = "active"
        self.conn.commit()
        tlog(f"[secret_order] rush id={order_id} old_due={old_due} due={due_turn} status={status}")
        return {"id": int(order_id), "title": row["title"], "status": status, "due_turn": due_turn}

    def get_secret_order(self, order_id: int) -> Optional[Dict[str, object]]:
        """单查一条密令（任意状态），给承办人查进度工具用。不存在返回 None。"""
        r = self.conn.execute(
            "SELECT * FROM secret_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
        if not r:
            return None
        return {
            "id": int(r["id"]), "minister_name": r["minister_name"],
            "title": r["title"], "content": r["content"],
            "status": r["status"], "result": r["result"] or "",
            "sim_note": (r["sim_note"] if "sim_note" in r.keys() else "") or "",
            "turn_issued": int(r["turn_issued"]),
            "due_turn": int(r["due_turn"] if "due_turn" in r.keys() else 0),
            "turn_closed": r["turn_closed"],
        }

    def auto_submit_due_secret_orders(self, state: GameState) -> List[Dict[str, object]]:
        """把到期 active 密令自动转入 pending_review，保证当月推演必须给终判。"""
        rows = self.conn.execute(
            """
            SELECT id, title, result FROM secret_orders
            WHERE status = 'active' AND due_turn > 0 AND due_turn <= ?
            ORDER BY id
            """,
            (int(state.turn),),
        ).fetchall()
        submitted: List[Dict[str, object]] = []
        for row in rows:
            stamp = f"〔{period_label(state.year, state.period)}〕[期限届满] "
            note = "御限已至，移交月末密旨核议；据既有查办、风声与盘面定成败。"
            prev = row["result"] or ""
            lines = [ln for ln in prev.split("\n") if ln.strip()]
            if not any("[期限届满]" in ln for ln in lines):
                lines.append(f"{stamp}{note}")
            self.conn.execute(
                """
                UPDATE secret_orders
                SET status = 'pending_review', result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("\n".join(lines), int(row["id"])),
            )
            submitted.append({"id": int(row["id"]), "title": row["title"]})
        if rows:
            self.conn.commit()
            tlog(f"[secret_order] auto_submit_due count={len(submitted)} ids={[x['id'] for x in submitted]}")
        return submitted

    def get_secret_orders_by_keywords(
        self, keywords: List[str], limit: int = 5, current_turn: int = 0
    ) -> List[Dict[str, object]]:
        """检索进行中（active）密令，tags LIKE 匹配，供推演 secret_orders 字段注入。
        完结/失败密令靠 event_memory（chat_message 来源）进入 relevant_memories，不在此返回。"""
        if not keywords:
            return self.list_secret_orders(status="active")[:limit]
        like_clauses = " OR ".join(["tags LIKE ?" for _ in keywords])
        like_params = [f"%{k}%" for k in keywords]
        rows = self.conn.execute(
            f"""
            SELECT * FROM secret_orders
            WHERE status = 'active' AND ({like_clauses})
            ORDER BY importance DESC, id DESC
            LIMIT ?
            """,
            like_params + [limit],
        ).fetchall()
        if not rows:
            return self.list_secret_orders(status="active")[:limit]
        return [
            {
                "id": int(r["id"]),
                "turn_issued": int(r["turn_issued"]),
                "year_issued": int(r["year_issued"]),
                "period_issued": int(r["period_issued"]),
                "minister_name": r["minister_name"],
                "title": r["title"],
                "content": r["content"],
                "tags": json.loads(r["tags"] or "[]") if isinstance(r["tags"], str) else (r["tags"] or []),
                "importance": int(r["importance"]),
                "status": r["status"],
                "result": r["result"] or "",
            }
            for r in rows
        ]

    # ----- chat_messages 补充查询 -----

    def get_chat_messages_for_turn(self, turn: int) -> Dict[str, List[Dict[str, str]]]:
        """查当月所有召对，按大臣分组，供 chat_memory agent 按人提取。"""
        rows = self.conn.execute(
            "SELECT minister_name, role, content FROM chat_messages WHERE turn = ? ORDER BY id",
            (int(turn),),
        ).fetchall()
        result: Dict[str, List[Dict[str, str]]] = {}
        for row in rows:
            result.setdefault(row["minister_name"], []).append(
                {"role": row["role"], "content": row["content"]}
            )
        return result

    def close(self) -> None:
        self.conn.close()

    def backup_to(self, target_path: str) -> None:
        """SQLite backup API 热备到 target_path。不需关闭主连接。"""
        import os as _os
        _os.makedirs(_os.path.dirname(target_path) or ".", exist_ok=True)
        dest = sqlite3.connect(target_path)
        try:
            self.conn.commit()
            self.conn.backup(dest)
        finally:
            dest.close()
