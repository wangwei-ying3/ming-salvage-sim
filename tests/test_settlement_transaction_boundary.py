import pytest

import ming_sim.decree as decree


class RecordingTransaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.events.append("tx_enter")
        self.db.in_tx = True
        return self.db

    def __exit__(self, exc_type, exc, tb):
        self.db.events.append("tx_rollback" if exc_type else "tx_commit")
        self.db.in_tx = False
        return False


class RecordingDB:
    def __init__(self):
        self.events = []
        self.in_tx = False

    def transaction(self):
        return RecordingTransaction(self)

    def save_turn_report(self, state, narrative):
        self.events.append(("save_turn_report", self.in_tx))

    def save_turn_extraction(self, state, **kwargs):
        self.events.append(("save_turn_extraction", self.in_tx))

    def mark_directives_issued(self, state):
        self.events.append(("mark_directives_issued", self.in_tx))

    def save_state(self, state):
        self.events.append(("save_state", self.in_tx))


class DummyState:
    def __init__(self):
        self.turn = 12
        self.year = 1628
        self.period = 1
        self.ended = False
        self.ending_status = ""

    def next_period(self):
        self.turn += 1


def _call_settle(db, state):
    return decree._settle_after_narrative(
        state,
        db,
        agno_db=object(),
        llm_config=object(),
        decree_text="edict",
        narrative="narrative",
        simulator_payload={},
        relevant_memories=[],
        secret_orders=[],
        before_turn=12,
        _emit=lambda kind, data: None,
    )


def _patch_common(monkeypatch):
    monkeypatch.setattr(decree, "EXTRACTION_MODULES", ["internal"])
    monkeypatch.setattr(decree, "build_extractor_shared_context", lambda *a, **k: {})
    monkeypatch.setattr(decree, "create_json_sanitizer_agent", lambda *a, **k: object())
    monkeypatch.setattr(decree, "create_score_extractor_module_agent", lambda *a, **k: object())
    monkeypatch.setattr(decree, "create_chapter_memory_agent", lambda *a, **k: object())
    monkeypatch.setattr(decree, "record_chapter_memory", lambda *a, **k: None)
    monkeypatch.setattr(decree, "create_minister_recap_agent", lambda *a, **k: object())
    monkeypatch.setattr(decree, "record_minister_recaps", lambda *a, **k: None)
    monkeypatch.setattr(decree, "apply_issue_inertia_and_ongoing", lambda *a, **k: None)
    monkeypatch.setattr(decree, "clear_gated_legacies", lambda *a, **k: None)
    monkeypatch.setattr(
        decree,
        "victory_status",
        lambda *a, **k: {"status": decree.ENDING_ONGOING, "summary": ""},
    )


def test_apply_score_failure_rolls_back_transaction(monkeypatch):
    _patch_common(monkeypatch)
    db = RecordingDB()
    state = DummyState()

    monkeypatch.setattr(
        decree,
        "extract_scores_by_modules_with_agno",
        lambda *a, **k: ({"metrics": {"legitimacy": 1}}, "extractor output", "extractor input"),
    )

    def fail_apply(*args, **kwargs):
        db.events.append(("apply_score_extraction", db.in_tx))
        raise RuntimeError("reducer failed")

    monkeypatch.setattr(decree, "apply_score_extraction", fail_apply)

    with pytest.raises(RuntimeError, match="reducer failed"):
        _call_settle(db, state)

    assert db.events == [
        "tx_enter",
        ("apply_score_extraction", True),
        "tx_rollback",
    ]


def test_apply_score_success_commits_transaction(monkeypatch):
    _patch_common(monkeypatch)
    db = RecordingDB()
    state = DummyState()

    monkeypatch.setattr(
        decree,
        "extract_scores_by_modules_with_agno",
        lambda *a, **k: ({"metrics": {"legitimacy": 1}}, "extractor output", "extractor input"),
    )

    def apply(*args, **kwargs):
        db.events.append(("apply_score_extraction", db.in_tx))
        return {"issue_summary": {"advances": []}}

    monkeypatch.setattr(decree, "apply_score_extraction", apply)

    _call_settle(db, state)

    assert db.events[:5] == [
        "tx_enter",
        ("apply_score_extraction", True),
        ("save_turn_report", True),
        ("save_turn_extraction", True),
        "tx_commit",
    ]


def test_extractor_failure_does_not_enter_transaction(monkeypatch):
    _patch_common(monkeypatch)
    db = RecordingDB()
    state = DummyState()

    def fail_extract(*args, **kwargs):
        db.events.append(("extractor", db.in_tx))
        raise RuntimeError("extractor failed")

    def apply(*args, **kwargs):
        db.events.append(("apply_score_extraction", db.in_tx))
        return {"issue_summary": {"advances": []}}

    monkeypatch.setattr(decree, "extract_scores_by_modules_with_agno", fail_extract)
    monkeypatch.setattr(decree, "apply_score_extraction", apply)

    _call_settle(db, state)

    assert "tx_enter" not in db.events
    assert ("apply_score_extraction", False) not in db.events
    assert ("save_turn_report", False) in db.events
    assert ("save_turn_extraction", False) in db.events
