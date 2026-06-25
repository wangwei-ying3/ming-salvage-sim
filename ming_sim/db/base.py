"""底层连接 + 通用工具：__init__、建列/查行、行转 dict、close/backup。

_BaseMixin。"""

from __future__ import annotations

from contextlib import contextmanager
import json
import re
import sqlite3
from collections.abc import Iterator
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
from ming_sim.matching import match_army_id_from_text, match_region_id_from_text
from ming_sim.models import Event, GameState, monthly_amount, period_label
from ming_sim.token_stats import tlog
from ming_sim.db._helpers import (
    normalize_office, infer_office_type_from_office,
    _compact_lookup_text, _normalize_power_id,
    COURT_OFFICE_TYPES, MINISTRY_OFFICE_TYPES,
)


class _BaseMixin:
    def __init__(self, path: str, content: Optional[GameContent] = None):
        self.path = path
        # 静态设定来源。过渡期 content 可省略，省略时自行加载；
        # 步骤7 起由 GameSession 统一传入同一份 GameContent。
        self.content = content if content is not None else GameContent.load()
        # check_same_thread=False：流式颁诏在 worker 线程跑 resolve_turn，
        # 复用同一 GameDB 连接。游戏单写者、无并发写，跨线程安全。
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._transaction_depth = 0
        self._transaction_savepoint_seq = 0
        # 遗产修正符缓存：legacy_modifiers 在落账热路径被频繁调用，缓存聚合结果，
        # 仅在 active 遗产集变化（insert_legacy / expire_legacies）时失效。
        self._legacy_mod_cache: Optional[Dict[str, object]] = None
        self.init_schema()

    def commit(self) -> None:
        """Commit immediately unless a managed transaction is active."""
        if self._transaction_depth > 0:
            return
        self.conn.commit()

    @contextmanager
    def transaction(self) -> Iterator["_BaseMixin"]:
        """Managed transaction with nested SAVEPOINT support."""
        if self._transaction_depth == 0:
            self.conn.execute("BEGIN")
            self._transaction_depth = 1
            try:
                yield self
            except Exception:
                self.conn.rollback()
                raise
            else:
                self.conn.commit()
            finally:
                self._transaction_depth = 0
            return

        self._transaction_savepoint_seq += 1
        savepoint = f"ming_tx_{self._transaction_savepoint_seq}"
        previous_depth = self._transaction_depth
        self.conn.execute(f"SAVEPOINT {savepoint}")
        self._transaction_depth = previous_depth + 1
        try:
            yield self
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        finally:
            self._transaction_depth = previous_depth

    def ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def table_has_rows(self, table: str) -> bool:
        row = self.conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        return row is not None

    def _row_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    def _table_exists(self, table: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

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
