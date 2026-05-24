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
    ARMY_FIELD_LABELS, ARMY_QUANTITY_FIELDS, ARMY_SCORE_FIELDS, ARMY_TEXT_FIELDS,
    BUILDING_CATEGORIES, BUILDING_FIELD_LABELS, BUILDING_OUTPUT_METRICS,
    BUILDING_QUANTITY_FIELDS, BUILDING_SCORE_FIELDS, BUILDING_TEXT_FIELDS,
    ECONOMY_ACCOUNTS, EXTERNAL_POWER_FIELD_LABELS, EXTERNAL_POWER_SCORE_FIELDS,
    EXTERNAL_POWER_TEXT_FIELDS, MONEY_UNIT, REGION_FIELD_LABELS, REGION_QUANTITY_FIELDS,
    REGION_SCORE_FIELDS, REGION_TEXT_FIELDS, TURN_UNIT,
)
from ming_sim.content import GameContent
from ming_sim.matching import match_army_id_from_text, match_region_id_from_text
from ming_sim.models import Event, GameState, monthly_amount, period_label

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
                status TEXT NOT NULL DEFAULT 'active',
                status_reason TEXT NOT NULL DEFAULT '',
                status_changed_turn INTEGER NOT NULL DEFAULT 0
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

            CREATE TABLE IF NOT EXISTS external_powers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
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
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS external_power_logs (
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
                FOREIGN KEY(power_id) REFERENCES external_powers(id)
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
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

            CREATE INDEX IF NOT EXISTS idx_external_power_logs_turn
            ON external_power_logs(turn, power_id);

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
            """
        )
        for column, definition in {
            "military_strength": "INTEGER NOT NULL DEFAULT 50",
            "cohesion": "INTEGER NOT NULL DEFAULT 50",
            "supply": "INTEGER NOT NULL DEFAULT 50",
            "last_action": "TEXT NOT NULL DEFAULT ''",
        }.items():
            self.ensure_column("external_powers", column, definition)
        self.ensure_column("issues", "resolve_condition", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("issues", "fail_condition", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "birth_year", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "historical_death_year", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "historical_death_month", "INTEGER NOT NULL DEFAULT 0")
        self.ensure_column("characters", "status", "TEXT NOT NULL DEFAULT 'active'")
        self.ensure_column("characters", "status_reason", "TEXT NOT NULL DEFAULT ''")
        self.ensure_column("characters", "status_changed_turn", "INTEGER NOT NULL DEFAULT 0")
        # 步骤7：回合阶段（旧库迁移，schema 升级非 fallback）
        self.ensure_column("game_state", "turn_phase", "TEXT NOT NULL DEFAULT 'summoning'")
        self.conn.commit()
        self.init_fiscal_config()

    def init_fiscal_config(self) -> None:
        rows = [
            ("田赋_rate",    65,  "rate", "各省田赋实收率%，账面845×此率/100"),
            ("辽饷_base",   130,  "base", "辽东加派月额，万两"),
            ("辽饷_rate",    60,  "rate", "辽饷实收率%，地方截留严重"),
            ("盐税_base",    55,  "base", "两淮两浙盐引月度定额，万两"),
            ("盐税_rate",   100,  "rate", "盐税实收率%"),
            ("商税_base",     8,  "base", "各地关卡店税月额，万两"),
            ("商税_rate",   100,  "rate", "商税实收率%"),
            ("宗室禄米_base", 80,  "base", "诸藩宗室禄米月度实发额，万两"),
            ("宗室禄米_rate",100,  "rate", "宗室禄米发放率%"),
            ("官俸_base",    35,  "base", "在京百官俸禄月额，万两"),
            ("官俸_rate",   100,  "rate", "官俸发放率%"),
            ("工程_base",    22,  "base", "工部月度维护支出，万两"),
            ("工程_rate",   100,  "rate", "工程维护率%"),
            ("赈灾_base",    25,  "base", "制度性赈灾备用，万两"),
            ("赈灾_rate",   100,  "rate", "赈灾拨付率%"),
            ("九边补给_base",130,  "base", "九边粮草月度补给（非军饷），万两"),
            ("九边补给_rate",100,  "rate", "九边补给执行率%"),
            ("皇庄_base",    18,  "base", "皇庄地租月度上缴内库，万两"),
            ("皇庄_rate",   100,  "rate", "皇庄收益率%"),
            ("织造_base",    12,  "base", "苏杭织造局月度上缴内库，万两"),
            ("织造_rate",   100,  "rate", "织造收益率%"),
            ("矿税_base",     5,  "base", "矿税残余月额，万两"),
            ("矿税_rate",   100,  "rate", "矿税实收率%"),
            ("宫廷_base",    18,  "base", "皇室日常用度月额，万两"),
            ("宫廷_rate",   100,  "rate", "宫廷开支率%"),
            ("内廷俸_base",  12,  "base", "太监宫女俸禄月额，万两"),
            ("内廷俸_rate", 100,  "rate", "内廷俸禄率%"),
            ("妃嫔_base",     8,  "base", "后宫妃嫔月度供奉，万两"),
            ("妃嫔_rate",   100,  "rate", "妃嫔供奉率%"),
        ]
        self.conn.executemany(
            "INSERT OR IGNORE INTO fiscal_config (key, value, kind, note) VALUES (?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def get_fiscal_config(self) -> Dict[str, int]:
        rows = self.conn.execute("SELECT key, value FROM fiscal_config").fetchall()
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

    def seed_static_data(self) -> None:
        for office_type, definition in self.content.office_definitions.items():
            self.conn.execute(
                """
                INSERT OR REPLACE INTO offices
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
        for character in self.content.characters.values():
            existing = self.conn.execute(
                "SELECT status, status_reason, status_changed_turn FROM characters WHERE name=?",
                (character.name,)
            ).fetchone()
            keep_status = existing["status"] if existing else character.status
            keep_reason = existing["status_reason"] if existing else ""
            keep_turn = existing["status_changed_turn"] if existing else 0
            self.conn.execute(
                """
                INSERT OR REPLACE INTO characters
                (name, office, office_type, faction, personal_skills, loyalty, ability, integrity, courage, style,
                 birth_year, historical_death_year, historical_death_month, status, status_reason, status_changed_turn)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    character.name,
                    character.office,
                    character.office_type,
                    character.faction,
                    json.dumps(character.personal_skills + character.aliases, ensure_ascii=False),
                    character.loyalty,
                    character.ability,
                    character.integrity,
                    character.courage,
                    character.style,
                    character.birth_year,
                    character.historical_death_year,
                    character.historical_death_month,
                    keep_status,
                    keep_reason,
                    keep_turn,
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
                (character.name, character.office, character.office_type, "初始设定"),
            )
        for faction in self.content.factions.values():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO factions (name, satisfaction, leverage, agenda)
                VALUES (?, ?, ?, ?)
                """,
                (faction.name, faction.satisfaction, faction.leverage, faction.agenda),
            )
        for cls in self.content.classes.values():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO classes (name, region_id, population, satisfaction, leverage, agenda)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (cls.name, cls.region_id, cls.population, cls.satisfaction, cls.leverage, cls.agenda),
            )
        for power in self.content.external_powers.values():
            self.conn.execute(
                """
                INSERT INTO external_powers
                (id, name, leader, stance, leverage, satisfaction, military_strength,
                 cohesion, supply, agenda, status, last_action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    power.id,
                    power.name,
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
                ),
            )
        for region in self.content.regions.values():
            self.conn.execute(
                """
                INSERT INTO regions
                (id, name, kind, population, public_support, unrest, natural_disaster, human_disaster,
                 registered_land, hidden_land, tax_per_turn, grain_security, gentry_resistance,
                 military_pressure, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    updated_at = CURRENT_TIMESTAMP
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
                ),
            )
        for army in self.content.armies.values():
            self.conn.execute(
                """
                INSERT INTO armies
                (id, name, station, theater, commander, controller, troop_type, manpower,
                 maintenance_per_turn, supply, morale, training, equipment, arrears,
                 mobility, loyalty, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    station = excluded.station,
                    theater = excluded.theater,
                    commander = excluded.commander,
                    controller = excluded.controller,
                    troop_type = excluded.troop_type,
                    updated_at = CURRENT_TIMESTAMP
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
                ),
            )
        for building in self.content.buildings.values():
            self.conn.execute(
                """
                INSERT INTO buildings
                (id, region_id, name, category, level, condition, maintenance, risk,
                 output_metric, output_amount, status, origin, created_turn)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preset', 0)
                ON CONFLICT(id) DO UPDATE SET
                    region_id = excluded.region_id,
                    name = excluded.name,
                    category = excluded.category,
                    updated_at = CURRENT_TIMESTAMP
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
        for event in (*self.content.events, *self.content.seed_events):
            self.conn.execute(
                """
                INSERT OR REPLACE INTO events
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
        self.conn.commit()

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
                state.turn = (y - 1627) * 12 + (m - 12) + 1
                state.year, state.period = y, m
                print(f"[调试] 跳到 {y}年{m}月起手（turn={state.turn}）。")
            self.save_state(state)
            self.ensure_opening_ledger(state)
            self.seed_opening_crises(state)
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
        """改人物状态：active/dismissed/imprisoned/exiled/retired/dead。"""
        valid = {"active", "dismissed", "imprisoned", "exiled", "retired", "dead"}
        if status not in valid:
            raise ValueError(f"character status 非法：{status}")
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
            round(land_base * cfg.get("田赋_rate", 65) / 100)
            + round(cfg.get("辽饷_base", 130) * cfg.get("辽饷_rate", 60) / 100)
            + round(cfg.get("盐税_base", 55) * cfg.get("盐税_rate", 100) / 100)
            + round(cfg.get("商税_base", 8) * cfg.get("商税_rate", 100) / 100)
        )
        gk_out = (
            int(army_total)
            + round(cfg.get("宗室禄米_base", 80) * cfg.get("宗室禄米_rate", 100) / 100)
            + round(cfg.get("官俸_base", 35) * cfg.get("官俸_rate", 100) / 100)
            + round(cfg.get("工程_base", 22) * cfg.get("工程_rate", 100) / 100)
            + round(cfg.get("赈灾_base", 25) * cfg.get("赈灾_rate", 100) / 100)
            + round(cfg.get("九边补给_base", 130) * cfg.get("九边补给_rate", 100) / 100)
        )
        nk_in = (
            round(cfg.get("皇庄_base", 18) * cfg.get("皇庄_rate", 100) / 100)
            + round(cfg.get("织造_base", 12) * cfg.get("织造_rate", 100) / 100)
            + round(cfg.get("矿税_base", 5) * cfg.get("矿税_rate", 100) / 100)
        )
        nk_out = (
            round(cfg.get("宫廷_base", 18) * cfg.get("宫廷_rate", 100) / 100)
            + round(cfg.get("内廷俸_base", 12) * cfg.get("内廷俸_rate", 100) / 100)
            + round(cfg.get("妃嫔_base", 8) * cfg.get("妃嫔_rate", 100) / 100)
        )
        gk_net = gk_in - gk_out
        nk_net = nk_in - nk_out
        return (
            f"{TURN_UNIT}度预算基准：国库入{format_money(monthly_amount(gk_in))}（田赋+辽饷+盐税+商税）"
            f"出{format_money(monthly_amount(gk_out))}（军饷{format_money(monthly_amount(int(army_total)))}+宗室+官俸+补给）"
            f"净{format_money_delta(monthly_amount(gk_net))}；"
            f"内库入{format_money(monthly_amount(nk_in))}出{format_money(monthly_amount(nk_out))}净{format_money_delta(monthly_amount(nk_net))}。"
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

    def external_power_rows(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM external_powers
            ORDER BY CASE id
                WHEN 'houjin' THEN 1
                WHEN 'mongol' THEN 2
                WHEN 'korea' THEN 3
                WHEN 'bandits' THEN 4
                ELSE 9
            END, name
            """
        ).fetchall()

    def external_power_payload(self) -> List[Dict[str, object]]:
        return [
            {
                "id": row["id"],
                "name": row["name"],
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
            }
            for row in self.external_power_rows()
        ]

    def external_power_report(self) -> str:
        rows = self.external_power_rows()
        if not rows:
            return "外部势力未建档。"
        return "；".join(
            f"{row['name']}（{row['leader']}）：{row['stance']}，威胁{row['leverage']}、"
            f"兵势{row['military_strength']}、内聚{row['cohesion']}、粮饷{row['supply']}，"
            f"{row['status']}；近动：{row['last_action'] or '尚无新动'}"
            for row in rows
        )

    def apply_external_power_deltas(
        self,
        state: GameState,
        updates: Dict[str, Dict[str, object]],
    ) -> List[Dict[str, object]]:
        changes: List[Dict[str, object]] = []
        for power_id, raw_changes in updates.items():
            row = self.conn.execute("SELECT * FROM external_powers WHERE id = ?", (power_id,)).fetchone()
            if row is None:
                print(f"[WARN] external_power_updates 引用未入库势力 '{power_id}' → 跳过")
                continue
            reason = str(raw_changes.get("reason") or raw_changes.get("last_action") or "外部势力推演").strip()[:120]
            for field, value in raw_changes.items():
                if field == "reason":
                    continue
                if field not in set(EXTERNAL_POWER_SCORE_FIELDS + EXTERNAL_POWER_TEXT_FIELDS):
                    print(f"[WARN] external_power_updates 引用非法字段 '{field}' → 跳过")
                    continue
                old_value = row[field]
                if field in EXTERNAL_POWER_SCORE_FIELDS:
                    delta = int(value)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
                else:
                    text_value = str(value).strip()[:220]
                    if not text_value or text_value == str(old_value):
                        continue
                    stored_new = text_value
                    log_delta = None
                self.conn.execute(
                    f"UPDATE external_powers SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (stored_new, power_id),
                )
                self.conn.execute(
                    """
                    INSERT INTO external_power_logs
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
                    "label": EXTERNAL_POWER_FIELD_LABELS.get(field, field),
                    "old": old_value,
                    "new": stored_new,
                    "delta": log_delta,
                    "reason": reason,
                })
        self.conn.commit()
        return changes

    def turn_external_power_summary(self, turn: int, limit: int = 10) -> str:
        rows = self.conn.execute(
            """
            SELECT epl.*, ep.name AS power_name
            FROM external_power_logs epl
            JOIN external_powers ep ON ep.id = epl.power_id
            WHERE epl.turn = ?
            ORDER BY epl.id
            LIMIT ?
            """,
            (turn, limit),
        ).fetchall()
        if not rows:
            return f"本{TURN_UNIT}外部势力无明确变化。"
        parts = []
        for row in rows:
            label = EXTERNAL_POWER_FIELD_LABELS.get(str(row["field"]), str(row["field"]))
            delta = row["delta"]
            if delta is None:
                parts.append(f"{row['power_name']}{label}改为{row['new_value']}（{row['reason']}）")
            else:
                sign = "+" if int(delta) > 0 else ""
                parts.append(f"{row['power_name']}{label}{sign}{int(delta)}（{row['reason']}）")
        return "；".join(parts) + "。"

    def region_rows(self, limit: int | None = None, danger_order: bool = False) -> List[sqlite3.Row]:
        order = (
            "(unrest + military_pressure + gentry_resistance + (100 - public_support) + (100 - grain_security)) DESC, name"
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
                f"粮食{row['grain_security']}、税{format_money(monthly_amount(int(row['tax_per_turn'])))}/{TURN_UNIT}，{row['status']}"
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
            f"民心{row['public_support']}，动乱{row['unrest']}，粮食{row['grain_security']}，"
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
            reason = str(raw_changes.get("reason") or event.title).strip()[:80]
            for field, value in raw_changes.items():
                if field == "reason":
                    continue
                # 先判字段合法，再取 row[field]：非法字段直接报清楚，
                # 不让 sqlite3.Row 抛误导性的 "No item with that key"。
                if field not in REGION_SCORE_FIELDS and field not in REGION_QUANTITY_FIELDS and field not in REGION_TEXT_FIELDS:
                    raise LLMContractError(
                        f"{TURN_UNIT}末执行评估引用了非法地区字段：'{field}'（地区 '{region_id}'）。"
                        f"合法字段：{REGION_SCORE_FIELDS + REGION_QUANTITY_FIELDS + REGION_TEXT_FIELDS}"
                    )
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
                        state.turn,
                        state.year,
                        state.period,
                        region_id,
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
                        "region": row["name"],
                        "field": field,
                        "label": REGION_FIELD_LABELS.get(field, field),
                        "old": old_value,
                        "new": stored_new,
                        "delta": log_delta,
                        "reason": reason,
                    }
                )
        self.conn.commit()
        return changes

    def army_rows(self, limit: int | None = None, danger_order: bool = False) -> List[sqlite3.Row]:
        order = (
            "(arrears + (100 - supply) + (100 - morale) + (100 - loyalty) + (100 - training)) DESC, name"
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
            parts.append(
                f"{row['name']}：驻{row['station']}，兵{row['manpower']}，"
                f"饷{format_money(monthly_amount(int(row['maintenance_per_turn'])))} /{TURN_UNIT}，补给{row['supply']}、"
                f"士气{row['morale']}、欠饷{row['arrears']}，{row['status']}"
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
        return (
            f"{row['name']}：驻扎地{row['station']}，战区{row['theater']}，统将{row['commander']}，"
            f"主管{row['controller']}，兵种{row['troop_type']}，人数{row['manpower']}人，"
            f"维护费{format_money(monthly_amount(int(row['maintenance_per_turn'])))} /{TURN_UNIT}，补给{row['supply']}，"
            f"士气{row['morale']}，训练{row['training']}，装备{row['equipment']}，"
            f"欠饷{row['arrears']}，机动{row['mobility']}，忠诚{row['loyalty']}。"
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
            reason = str(raw_changes.get("reason") or event.title).strip()[:80]
            _valid_army_fields = set(ARMY_SCORE_FIELDS + ARMY_QUANTITY_FIELDS + ARMY_TEXT_FIELDS)
            for field, value in raw_changes.items():
                if field == "reason":
                    continue
                if field not in _valid_army_fields:
                    print(f"[WARN] army_delta 引用非法字段 '{field}' → 跳过")
                    continue
                old_value = row[field]
                if field in ARMY_SCORE_FIELDS:
                    delta = int(value)
                    new_value = max(0, min(100, int(old_value) + delta))
                    actual_delta = new_value - int(old_value)
                    if actual_delta == 0:
                        continue
                    stored_new: object = new_value
                    log_delta: int | None = actual_delta
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

    def adjust_factions(self, deltas: Dict[str, int]) -> None:
        for faction, delta in deltas.items():
            if not delta:
                continue
            current = self.faction_satisfaction(faction)
            value = max(0, min(100, current + delta))
            self.conn.execute(
                "UPDATE factions SET satisfaction = ? WHERE name = ?",
                (value, faction),
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

    def previous_turn_summary(self, state: GameState) -> str:
        previous_turn = state.turn - 1
        if previous_turn < 1:
            return f"登基伊始，尚无上{TURN_UNIT}回奏。"

        # 上回合奏报单独存在 turn_reports，直接取。
        report = self.get_turn_report(previous_turn)
        if report:
            return report

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
            f"外部势力：{self.turn_external_power_summary(previous_turn)}",
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
            text = text or ""
            if not text.strip():
                return None
            try:
                return json.loads(text)
            except Exception:
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
        # clamp single advance
        delta_bar = max(-50, min(50, int(delta_bar)))
        from_value = int(row["bar_value"])
        to_value = max(0, min(100, from_value + delta_bar))
        actual_delta = to_value - from_value
        from_stage_text = row["stage_text"]
        to_stage_text = stage_text or from_stage_text
        new_phase = self._derive_issue_phase(to_value)
        new_status = row["status"]
        closed_turn = row["closed_turn"]
        if to_value >= 100:
            new_status = "resolved"
            closed_turn = state.turn
        elif to_value <= 0:
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
    ) -> int:
        before = int(state.metrics[account])
        after = max(0, before + int(delta))
        actual = after - before
        if actual == 0:
            return 0
        state.metrics[account] = after
        self.conn.execute(
            """
            INSERT INTO economy_ledger
            (turn, year, period, account, delta, balance_after, category, reason, event_id, edict_id, actor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '事项推演')
            """,
            (state.turn, state.year, state.period, account, actual, after, category, reason),
        )
        self.sync_economy_accounts(state)
        self.conn.commit()
        return actual

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
