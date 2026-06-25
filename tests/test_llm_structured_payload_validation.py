import pytest

from ming_sim.llm_contract import LLMContractError
from ming_sim.simulation import validate_structured_extraction_payload


def test_unknown_top_level_field_is_rejected():
    with pytest.raises(LLMContractError, match="unknown top-level"):
        validate_structured_extraction_payload(
            {
                "metric_delta": {"民心": 1},
                "unexpected": {},
            },
            module="internal",
        )


def test_unknown_nested_field_in_region_delta_is_rejected():
    with pytest.raises(LLMContractError, match="region_delta"):
        validate_structured_extraction_payload(
            {
                "region_delta": {
                    "shaanxi": {
                        "unrest": 1,
                        "not_a_region_field": 9,
                    }
                }
            },
            module="internal",
        )


def test_unknown_nested_field_in_army_delta_is_rejected():
    with pytest.raises(LLMContractError, match="army_delta"):
        validate_structured_extraction_payload(
            {
                "army_delta": {
                    "guanning": {
                        "morale": -2,
                        "not_an_army_field": 1,
                    }
                }
            },
            module="military_external",
        )


def test_partial_valid_and_invalid_payload_is_rejected_whole():
    payload = {
        "metric_delta": {"民心": 1},
        "region_delta": {"shaanxi": {"public_support": 2, "bad_nested": -9}},
    }

    with pytest.raises(LLMContractError, match="bad_nested"):
        validate_structured_extraction_payload(payload, module="internal")


def test_valid_minimal_payload_passes():
    payload = {
        "metric_delta": {"民心": 1},
        "region_delta": {"shaanxi": {"public_support": 2, "reason": "relief"}},
        "economy_moves": [{"account": "国库", "delta": -1, "category": "relief", "reason": "grain"}],
    }

    assert validate_structured_extraction_payload(payload, module="internal") is payload


def test_sanitized_payload_with_illegal_structure_is_still_rejected():
    sanitized_payload = {
        "army_delta": {"guanning": {"morale": -1}},
        "power_updates": {"houjin": {"military_strength": 3, "unknown_power_field": 5}},
    }

    with pytest.raises(LLMContractError, match="power_updates"):
        validate_structured_extraction_payload(sanitized_payload, module="military_external")
