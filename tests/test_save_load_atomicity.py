import os
import shutil
import sqlite3
import tempfile
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import web_app


class FakeDB:
    def __init__(self, path: Path):
        self.path = str(path)
        self.closed = False

    def backup_to(self, target_path: str) -> None:
        os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
        src = sqlite3.connect(self.path)
        dst = sqlite3.connect(target_path)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, path: Path, token: str = "session"):
        self.db = FakeDB(path)
        self.llm_config = SimpleNamespace(token=token)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self.db.close()


class RecordingLock:
    def __init__(self):
        self.entered = 0
        self.exited = 0
        self._lock = threading.RLock()

    def __enter__(self):
        self.entered += 1
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited += 1
        self._lock.release()
        return False


@pytest.fixture
def work_dir():
    root = Path(tempfile.gettempdir()) / f"ming_save_load_atomicity_{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _make_db(path: Path, marker: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE game_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                year INTEGER NOT NULL,
                period INTEGER NOT NULL,
                turn INTEGER NOT NULL,
                turn_phase TEXT NOT NULL DEFAULT 'summoning',
                ended INTEGER NOT NULL DEFAULT 0,
                ending_status TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE metrics (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            CREATE TABLE kv_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE characters (
                name TEXT PRIMARY KEY,
                office TEXT NOT NULL DEFAULT '',
                office_type TEXT NOT NULL DEFAULT '',
                faction TEXT NOT NULL DEFAULT '',
                personal_skills TEXT NOT NULL DEFAULT '',
                loyalty INTEGER NOT NULL DEFAULT 50,
                ability INTEGER NOT NULL DEFAULT 50,
                integrity INTEGER NOT NULL DEFAULT 50,
                courage INTEGER NOT NULL DEFAULT 50,
                style TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute(
            "INSERT INTO game_state(id, year, period, turn, turn_phase, ended, ending_status) VALUES (1, 1627, 10, 1, 'summoning', 0, '')"
        )
        conn.execute("INSERT INTO kv_store(key, value) VALUES ('marker', ?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _marker(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key='marker'").fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def _make_game(work_dir: Path, monkeypatch):
    main_db = work_dir / "main.db"
    saves_dir = work_dir / "saves"
    saves_dir.mkdir()
    _make_db(main_db, "main")

    game = web_app.WebGame.__new__(web_app.WebGame)
    game.db_path = str(main_db)
    game.session = FakeSession(main_db, "old")
    game._state_lock = threading.RLock()
    game.chat_history = {}
    game.favorites = set()
    monkeypatch.setattr(game, "saves_dir", lambda: str(saves_dir))
    return game, main_db, saves_dir


def test_corrupt_save_db_does_not_overwrite_main_db(work_dir, monkeypatch):
    game, main_db, saves_dir = _make_game(work_dir, monkeypatch)
    (saves_dir / "bad.db").write_bytes(b"not a sqlite database")

    with pytest.raises(HTTPException) as exc:
        game.load_save("bad")

    assert exc.value.status_code == 400
    assert _marker(main_db) == "main"
    assert game.session.closed is False


def test_candidate_validation_failure_does_not_close_current_session(work_dir, monkeypatch):
    game, main_db, saves_dir = _make_game(work_dir, monkeypatch)
    invalid = saves_dir / "invalid.db"
    conn = sqlite3.connect(invalid)
    try:
        conn.execute("CREATE TABLE kv_store (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(HTTPException) as exc:
        game.load_save("invalid")

    assert exc.value.status_code == 400
    assert _marker(main_db) == "main"
    assert game.session.closed is False


def test_rebuild_failure_rolls_main_db_back_to_previous_state(work_dir, monkeypatch):
    game, main_db, saves_dir = _make_game(work_dir, monkeypatch)
    _make_db(saves_dir / "new.db", "new")

    def fail_rebuild(_llm_config):
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(game, "_rebuild_session", fail_rebuild)

    with pytest.raises(HTTPException) as exc:
        game.load_save("new")

    assert exc.value.status_code == 500
    assert _marker(main_db) == "main"


def test_successful_load_replaces_main_db_and_rebuilds_session(work_dir, monkeypatch):
    game, main_db, saves_dir = _make_game(work_dir, monkeypatch)
    _make_db(saves_dir / "new.db", "new")
    original_session = game.session
    rebuilt = []

    def rebuild(_llm_config):
        rebuilt.append(True)
        game.session = FakeSession(main_db, "rebuilt")

    monkeypatch.setattr(game, "_rebuild_session", rebuild)

    game.load_save("new")

    assert _marker(main_db) == "new"
    assert original_session.closed is True
    assert rebuilt == [True]
    assert game.session.llm_config.token == "rebuilt"


def test_load_save_enters_shared_state_lock(work_dir, monkeypatch):
    game, main_db, saves_dir = _make_game(work_dir, monkeypatch)
    _make_db(saves_dir / "new.db", "new")
    lock = RecordingLock()
    game._state_lock = lock
    monkeypatch.setattr(game, "_rebuild_session", lambda _llm_config: setattr(game, "session", FakeSession(main_db, "rebuilt")))

    game.load_save("new")

    assert lock.entered == 1
    assert lock.exited == 1
