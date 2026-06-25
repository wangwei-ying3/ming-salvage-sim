import pytest

import ming_sim.issues as issues
from ming_sim.issues import apply_score_extraction


@pytest.fixture(autouse=True)
def _no_issue_tracker(monkeypatch):
    monkeypatch.setattr(
        issues,
        "apply_issue_tracker_output",
        lambda *args, **kwargs: {
            "advances": [],
            "new_issues": [],
            "closes": [],
            "cancels": [],
            "touched_ids": [],
        },
    )
    monkeypatch.setattr(issues, "victory_status", lambda db, state: {"status": "ongoing"})


class _State:
    year = 1628
    period = "spring"
    turn = 1

    def __init__(self):
        self.metrics = {}
        self.clamped = False

    def clamp(self):
        self.clamped = True


class _ReducerDB:
    def __init__(self, *, fail_region=False, fail_army=False):
        self.fail_region = fail_region
        self.fail_army = fail_army
        self.calls = []

    def legacy_modifiers(self, state):
        return {}

    def create_armies_from_extraction(self, state, items, actor=""):
        self.calls.append("new_armies")
        return []

    def apply_region_deltas(self, state, event, building, source, deltas):
        self.calls.append("region_delta")
        if self.fail_region:
            raise RuntimeError("region boom")
        return [{"ok": "region"}]

    def apply_army_deltas(self, state, event, building, source, deltas):
        self.calls.append("army_delta")
        if self.fail_army:
            raise RuntimeError("army boom")
        return [{"ok": "army"}]

    def apply_arms_stock_deltas(self, state, deltas):
        self.calls.append("arms_changes")
        return [{"ok": "arms"}]

    def apply_power_deltas(self, state, deltas):
        self.calls.append("power_updates")
        return [{"ok": "power"}]

    def list_active_issues(self):
        return []

    def apply_character_power_changes(self, items):
        self.calls.append("character_power_changes")
        return []


def test_region_delta_failure_raises_and_stops_later_reducers():
    db = _ReducerDB(fail_region=True)

    with pytest.raises(RuntimeError, match="region_delta reducer failed"):
        apply_score_extraction(
            db,
            _State(),
            {
                "region_delta": {"shaanxi": {"public_support": 1}},
                "army_delta": {"guanning": {"morale": 1}},
                "arms_changes": {"matchlock": 1},
                "power_updates": {"houjin": {"military_strength": 1}},
            },
        )

    assert db.calls == ["region_delta"]


def test_army_delta_failure_raises_and_stops_later_reducers():
    db = _ReducerDB(fail_army=True)

    with pytest.raises(RuntimeError, match="army_delta reducer failed"):
        apply_score_extraction(
            db,
            _State(),
            {
                "region_delta": {"shaanxi": {"public_support": 1}},
                "army_delta": {"guanning": {"morale": 1}},
                "arms_changes": {"matchlock": 1},
                "power_updates": {"houjin": {"military_strength": 1}},
            },
        )

    assert db.calls == ["region_delta", "army_delta"]


def test_valid_minimal_payload_still_applies_and_returns_summary():
    db = _ReducerDB()
    state = _State()

    applied = apply_score_extraction(
        db,
        state,
        {
            "region_delta": {"shaanxi": {"public_support": 1}},
            "army_delta": {"guanning": {"morale": 1}},
        },
    )

    assert applied["region_changes"] == [{"ok": "region"}]
    assert applied["army_changes"] == [{"ok": "army"}]
    assert db.calls == ["region_delta", "army_delta", "character_power_changes"]
    assert state.clamped is True
