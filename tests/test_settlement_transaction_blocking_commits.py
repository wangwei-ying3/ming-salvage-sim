import json
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

from ming_sim.constants import BUILDING_CATEGORIES, ECONOMY_ACCOUNTS
from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.flows import _apply_economy_list
from ming_sim.issues import _apply_character_location, _displace_duplicate_offices
from ming_sim.models import Character, Event, GameState


@pytest.fixture
def db_path():
    path = Path(tempfile.gettempdir()) / f"ming_blocking_commit_{uuid.uuid4().hex}.db"
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
        yield db, state, content
    finally:
        db.close()


def _scalar(path: Path, sql: str, params: tuple[object, ...] = ()) -> object:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _row(path: Path, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(sql, params).fetchone()
        assert row is not None
        return row
    finally:
        conn.close()


def _raise_after_write(db: GameDB, write) -> None:
    with pytest.raises(RuntimeError, match="force rollback"):
        with db.transaction():
            write()
            raise RuntimeError("force rollback")


def _first_faction(db: GameDB) -> str:
    return str(db.conn.execute("SELECT name FROM factions ORDER BY name LIMIT 1").fetchone()["name"])


def _first_class_key(db: GameDB) -> str:
    row = db.conn.execute(
        "SELECT name, region_id FROM classes ORDER BY region_id, name LIMIT 1"
    ).fetchone()
    name = str(row["name"])
    region_id = str(row["region_id"] or "")
    return f"{name}@{region_id}" if region_id else name


def _first_active_character(db: GameDB) -> str:
    return str(
        db.conn.execute(
            "SELECT name FROM characters WHERE status='active' AND power_id='ming' ORDER BY name LIMIT 1"
        ).fetchone()["name"]
    )


def _two_active_characters(db: GameDB) -> tuple[str, str]:
    rows = db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND power_id='ming' ORDER BY name LIMIT 2"
    ).fetchall()
    assert len(rows) >= 2
    return str(rows[0]["name"]), str(rows[1]["name"])


def _other_power(db: GameDB) -> str:
    row = db.conn.execute("SELECT id FROM powers WHERE id <> 'ming' ORDER BY id LIMIT 1").fetchone()
    assert row is not None
    return str(row["id"])


def test_faction_and_class_adjustments_roll_back_inside_transaction(game, db_path):
    db, _state, _content = game
    faction = _first_faction(db)
    class_key = _first_class_key(db)
    class_name, class_region = (class_key.split("@", 1) + [""])[:2] if "@" in class_key else (class_key, "")
    before_faction = int(_scalar(db_path, "SELECT satisfaction FROM factions WHERE name=?", (faction,)))
    before_class = int(
        _scalar(
            db_path,
            "SELECT satisfaction FROM classes WHERE name=? AND region_id=?",
            (class_name, class_region),
        )
    )

    def write():
        db.adjust_factions({faction: {"satisfaction": 1}})
        db.adjust_classes({class_key: {"satisfaction": 1}})

    _raise_after_write(db, write)

    assert _scalar(db_path, "SELECT satisfaction FROM factions WHERE name=?", (faction,)) == before_faction
    assert (
        _scalar(
            db_path,
            "SELECT satisfaction FROM classes WHERE name=? AND region_id=?",
            (class_name, class_region),
        )
        == before_class
    )


def test_economy_salary_arrears_rolls_back_inside_transaction(game, db_path):
    db, state, _content = game
    army_id = "guanning"
    db.conn.execute("UPDATE armies SET arrears=10 WHERE id=?", (army_id,))
    db.conn.commit()

    def write():
        _apply_economy_list(
            db,
            state,
            [
                {
                    "account": ECONOMY_ACCOUNTS[0],
                    "delta": -4,
                    "category": "tx-arrears",
                    "purpose": "补饷",
                    "target_kind": "army",
                    "target_id": army_id,
                    "reason": "tx arrears probe",
                }
            ],
        )

    _raise_after_write(db, write)

    assert _scalar(db_path, "SELECT arrears FROM armies WHERE id=?", (army_id,)) == 10
    assert _scalar(db_path, "SELECT COUNT(*) FROM economy_ledger WHERE category='tx-arrears'") == 0


def test_character_updates_and_new_character_roll_back_inside_transaction(game, db_path):
    db, state, content = game
    name, displaced_name = _two_active_characters(db)
    other_power = _other_power(db)
    new_name = f"tx-character-{uuid.uuid4().hex[:8]}"
    db.conn.execute(
        "UPDATE characters SET office='tx-old-office', location='', status='active', power_id='ming' WHERE name=?",
        (name,),
    )
    db.conn.execute(
        "UPDATE characters SET office='tx-probe首辅', status='active', power_id='ming' WHERE name=?",
        (displaced_name,),
    )
    db.conn.commit()

    def write():
        db.set_character_office(name, "tx-probe首辅", "", source="tx probe")
        _apply_character_location(db, content, name, "tx-location")
        _displace_duplicate_offices(db, content, name, "tx-probe首辅")
        db.set_character_status(state, name, "dismissed", "tx status")
        db.apply_character_power_changes([{"name": name, "new_power": other_power, "reason": "tx power"}])
        db.add_character(
            state,
            Character(
                name=new_name,
                office="tx office",
                office_type="",
                faction="tx",
                aliases=[],
                personal_skills=[],
                loyalty=50,
                ability=50,
                integrity=50,
                courage=50,
                style="tx",
                power_id="ming",
                portrait_id="minister_pool_1",
            ),
            source="tx probe",
        )

    _raise_after_write(db, write)

    row = _row(
        db_path,
        "SELECT office, location, status, power_id FROM characters WHERE name=?",
        (name,),
    )
    assert row["office"] == "tx-old-office"
    assert row["location"] == ""
    assert row["status"] == "active"
    assert row["power_id"] == "ming"
    assert (
        _scalar(db_path, "SELECT office FROM characters WHERE name=?", (displaced_name,))
        == "tx-probe首辅"
    )
    assert _scalar(db_path, "SELECT COUNT(*) FROM characters WHERE name=?", (new_name,)) == 0


def test_secret_order_updates_roll_back_inside_transaction(game, db_path):
    db, state, _content = game
    active_id = db.conn.execute(
        """
        INSERT INTO secret_orders
            (turn_issued, due_turn, year_issued, period_issued, minister_name, title, content, tags, status)
        VALUES (?, 0, ?, ?, 'tx-minister-a', 'tx-active', 'content', '[]', 'active')
        """,
        (state.turn, state.year, state.period),
    ).lastrowid
    pending_id = db.conn.execute(
        """
        INSERT INTO secret_orders
            (turn_issued, due_turn, year_issued, period_issued, minister_name, title, content, tags, status)
        VALUES (?, 0, ?, ?, 'tx-minister-b', 'tx-pending', 'content', '[]', 'pending_review')
        """,
        (state.turn, state.year, state.period),
    ).lastrowid
    db.conn.commit()

    def write():
        db.update_secret_order_sim_note(int(active_id), "tx sim note", state.year, state.period)
        db.close_secret_order(int(pending_id), "done", "tx result", state.turn)

    _raise_after_write(db, write)

    assert _scalar(db_path, "SELECT sim_note FROM secret_orders WHERE id=?", (active_id,)) == ""
    pending = _row(db_path, "SELECT status, result FROM secret_orders WHERE id=?", (pending_id,))
    assert pending["status"] == "pending_review"
    assert pending["result"] == ""


def test_building_technology_and_department_effects_roll_back_inside_transaction(game, db_path):
    db, state, _content = game
    category = next(iter(BUILDING_CATEGORIES))
    event = Event(
        id="tx-building",
        title="tx building",
        kind="test",
        summary="",
        urgency=0,
        severity=0,
        credibility=100,
        interests=[],
        audiences=[],
    )
    existing_id = db.add_building(
        state,
        "beizhili",
        "tx-existing-building",
        category,
        condition=50,
        status="existing",
    )
    db.conn.commit()

    def write():
        db.add_building(state, "beizhili", "tx-new-building", category, condition=60)
        db.apply_building_deltas(
            state,
            event,
            None,
            "tx",
            {existing_id: {"condition": 5, "reason": "tx modify"}},
        )
        db.remove_building(state, existing_id, reason="tx remove")
        db.add_technology(state, "tx-technology", "tx")
        db.add_department("tx-department", authority_scope="tx")

    _raise_after_write(db, write)

    assert _scalar(db_path, "SELECT COUNT(*) FROM buildings WHERE name='tx-new-building'") == 0
    existing = _row(db_path, "SELECT condition FROM buildings WHERE id=?", (existing_id,))
    assert int(existing["condition"]) == 50
    assert _scalar(db_path, "SELECT COUNT(*) FROM technologies WHERE name='tx-technology'") == 0
    assert _scalar(db_path, "SELECT COUNT(*) FROM offices WHERE office_type='tx-department'") == 0


def test_representative_helpers_still_commit_outside_managed_transaction(game, db_path):
    db, state, _content = game
    faction = _first_faction(db)
    character = _first_active_character(db)
    before_faction = int(_scalar(db_path, "SELECT satisfaction FROM factions WHERE name=?", (faction,)))

    db.adjust_factions({faction: {"satisfaction": 1}})
    db.set_character_status(state, character, "dismissed", "outside tx")
    db.add_department("tx-outside-department", authority_scope="tx")

    assert _scalar(db_path, "SELECT satisfaction FROM factions WHERE name=?", (faction,)) == before_faction + 1
    assert _scalar(db_path, "SELECT status FROM characters WHERE name=?", (character,)) == "dismissed"
    assert _scalar(db_path, "SELECT COUNT(*) FROM offices WHERE office_type='tx-outside-department'") == 1
