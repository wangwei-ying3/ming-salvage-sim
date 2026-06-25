import pytest

from ming_sim.directives import StructuredDirectiveError, compile_structured_directive, load_directive_templates
from ming_sim.simulation import build_simulator_payload


def test_all_directive_templates_compile_without_placeholders():
    for template in load_directive_templates():
        fields = {
            spec["key"]: (spec.get("options") or [f"测试{spec['label']}"])[0]
            for spec in template["fields"]
        }
        directive = compile_structured_directive(template["id"], fields)
        assert directive["template_id"] == template["id"]
        assert "{" not in directive["compiled_text"]
        assert "}" not in directive["compiled_text"]
        assert directive["fields"]


def test_structured_directive_requires_fields():
    with pytest.raises(StructuredDirectiveError):
        compile_structured_directive("money_grain_transfer", {})


class _DummyState:
    year = 1627
    period = 10
    turn = 1
    metrics = {}


class _DummyDB:
    content = type("Content", (), {"preset_departments": {}, "preset_technologies": {}})()

    def list_active_issues(self):
        return []

    def conn_execute_rows(self, sql, args=()):
        return []

    @property
    def conn(self):
        class Rows(list):
            def fetchall(self):
                return []

        class Conn:
            def execute(self, sql, args=()):
                return Rows()

        return Conn()

    def treasury_report(self, state):
        return ""

    def faction_report(self):
        return ""

    def class_report(self):
        return ""

    def power_report(self, exclude_self=True):
        return ""

    def department_payload(self):
        return []

    def technology_payload(self):
        return []

    def building_payload(self):
        return []

    def army_held_arms_all(self):
        return {}


def test_simulator_payload_includes_structured_directives(monkeypatch):
    db = _DummyDB()
    monkeypatch.setattr("ming_sim.simulation.gather_candidate_events", lambda state, db: [])
    monkeypatch.setattr("ming_sim.simulation.victory_status", lambda db, state: {})
    directive = compile_structured_directive(
        "personnel_change",
        {"person": "徐光启", "action": "起复", "office": "礼部尚书"},
    )
    payload = build_simulator_payload(
        _DummyState(),
        db,  # type: ignore[arg-type]
        "本月无新诏。",
        "",
        structured_directives=[directive],
    )
    assert payload["structured_directives"][0]["template_id"] == "personnel_change"
