import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.issues import apply_score_extraction
from ming_sim.models import Event, GameState


@pytest.fixture
def db_path():
    path = Path(tempfile.gettempdir()) / f"ming_settlement_tx_{uuid.uuid4().hex}.db"
    yield path
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(f"{path}{suffix}").unlink()
        except FileNotFoundError:
            pass
        except PermissionError:
            pass


@pytest.fixture
def game(db_path):
    content = GameContent.load()
    db = GameDB(str(db_path), content)
    db.seed_static_data()
    state = GameState()
    try:
        yield db, state
    finally:
        db.close()


def _external_scalar(path: Path, sql: str, params: tuple[object, ...] = ()) -> object:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _first_region_id(db: GameDB) -> str:
    return str(db.conn.execute("SELECT id FROM regions ORDER BY id LIMIT 1").fetchone()["id"])


def test_new_army_rolls_back_when_later_region_delta_fails(game, db_path):
    db, state = game
    region_id = _first_region_id(db)
    army_id = f"tx_probe_army_{uuid.uuid4().hex[:8]}"

    payload = {
        "new_armies": [
            {
                "id": army_id,
                "name": "rollback probe army",
                "owner_power": "ming",
                "manpower": 1000,
                "reason": "rollback probe",
            }
        ],
        "region_delta": {
            region_id: {"not_a_region_field": 1, "reason": "force reducer failure"}
        },
    }

    with pytest.raises(RuntimeError, match="region_delta reducer failed"):
        with db.transaction():
            apply_score_extraction(db, state, payload)

    assert _external_scalar(db_path, "SELECT COUNT(*) FROM armies WHERE id=?", (army_id,)) == 0


def test_region_delta_rolls_back_when_later_army_delta_fails(game, db_path):
    db, state = game
    region_id = _first_region_id(db)
    army_id = "guanning"
    before = int(
        _external_scalar(db_path, "SELECT public_support FROM regions WHERE id=?", (region_id,))
    )

    payload = {
        "region_delta": {region_id: {"public_support": 1, "reason": "rollback probe"}},
        "army_delta": {army_id: {"morale": "not-an-int", "reason": "force reducer failure"}},
    }

    with pytest.raises(RuntimeError, match="army_delta reducer failed"):
        with db.transaction():
            apply_score_extraction(db, state, payload)

    after = int(
        _external_scalar(db_path, "SELECT public_support FROM regions WHERE id=?", (region_id,))
    )
    assert after == before


def test_turn_report_and_extraction_roll_back_inside_transaction(game, db_path):
    db, state = game

    with pytest.raises(RuntimeError, match="trace failure"):
        with db.transaction():
            db.save_turn_report(state, "rollback report")
            db.save_turn_extraction(
                state,
                decree_text="edict",
                narrative="narrative",
                extractor_input="input",
                extractor_output="output",
            )
            raise RuntimeError("trace failure")

    assert _external_scalar(db_path, "SELECT COUNT(*) FROM turn_reports WHERE turn=?", (state.turn,)) == 0
    assert _external_scalar(db_path, "SELECT COUNT(*) FROM turn_extractions WHERE turn=?", (state.turn,)) == 0


def test_settlement_helper_still_commits_outside_managed_transaction(game, db_path):
    db, state = game
    region_id = _first_region_id(db)
    before = int(
        _external_scalar(db_path, "SELECT public_support FROM regions WHERE id=?", (region_id,))
    )
    event = Event(
        id="tx_probe",
        title="transaction compatibility probe",
        kind="test",
        summary="",
        urgency=0,
        severity=0,
        credibility=100,
        interests=[],
        audiences=[],
    )

    db.apply_region_deltas(
        state,
        event,
        None,
        "test",
        {region_id: {"public_support": 1, "reason": "outside tx commit probe"}},
    )

    after = int(
        _external_scalar(db_path, "SELECT public_support FROM regions WHERE id=?", (region_id,))
    )
    assert after == before + 1
