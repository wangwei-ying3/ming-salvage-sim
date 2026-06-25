import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

from ming_sim.db import GameDB


@pytest.fixture
def db_path():
    path = Path(tempfile.gettempdir()) / f"ming_tx_helper_{uuid.uuid4().hex}.db"
    yield path
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(f"{path}{suffix}").unlink()
        except FileNotFoundError:
            pass
        except PermissionError:
            pass


@pytest.fixture
def db(db_path):
    game_db = GameDB(str(db_path))
    game_db.conn.execute(
        "CREATE TABLE tx_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL)"
    )
    game_db.conn.commit()
    try:
        yield game_db
    finally:
        game_db.close()


def _insert(db, label: str) -> None:
    db.conn.execute("INSERT INTO tx_probe (label) VALUES (?)", (label,))


def _external_labels(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [
            row[0]
            for row in conn.execute("SELECT label FROM tx_probe ORDER BY id").fetchall()
        ]
    finally:
        conn.close()


def test_transaction_success_commits(db, db_path):
    with db.transaction():
        _insert(db, "committed")

    assert _external_labels(db_path) == ["committed"]
    assert db.conn.in_transaction is False


def test_transaction_exception_rolls_back(db, db_path):
    with pytest.raises(RuntimeError, match="boom"):
        with db.transaction():
            _insert(db, "rolled-back")
            raise RuntimeError("boom")

    assert _external_labels(db_path) == []
    assert db.conn.in_transaction is False


def test_nested_savepoint_success_commits_with_outer(db, db_path):
    with db.transaction():
        _insert(db, "outer")
        with db.transaction():
            _insert(db, "inner")

    assert _external_labels(db_path) == ["outer", "inner"]


def test_nested_savepoint_failure_rolls_back_inner_and_outer_can_continue(db, db_path):
    with db.transaction():
        _insert(db, "outer-before")
        with pytest.raises(ValueError, match="inner"):
            with db.transaction():
                _insert(db, "inner")
                raise ValueError("inner")
        _insert(db, "outer-after")

    assert _external_labels(db_path) == ["outer-before", "outer-after"]


def test_commit_guard_inside_transaction_does_not_prematurely_commit(db, db_path):
    with db.transaction():
        _insert(db, "pending")
        db.commit()

        assert _external_labels(db_path) == []

    assert _external_labels(db_path) == ["pending"]


def test_commit_guard_outside_transaction_commits(db, db_path):
    _insert(db, "outside")
    db.commit()

    assert _external_labels(db_path) == ["outside"]
