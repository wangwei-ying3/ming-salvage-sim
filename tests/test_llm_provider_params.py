from ming_sim.llm_model import create_chat_model
from ming_sim.models import LLMConfig


def _cfg(model: str, base_url: str = "https://api.deepseek.com/v1", thinking_level: str = "") -> LLMConfig:
    return LLMConfig(
        api_key="test-key",
        base_url=base_url,
        model=model,
        thinking_level=thinking_level,
    )


def test_deepseek_v4_thinking_enabled_uses_supported_params():
    model = create_chat_model(_cfg("deepseek-v4-pro", thinking_level="max"), enable_thinking=True)

    assert model.extra_body == {"thinking": {"type": "enabled"}}
    assert model.reasoning_effort == "max"


def test_deepseek_legacy_model_does_not_receive_v4_thinking_field():
    model = create_chat_model(_cfg("deepseek-chat"), enable_thinking=False)

    assert model.extra_body is None
    assert model.reasoning_effort is None


def test_advanced_smoke_uses_thinking_enabled_path(monkeypatch):
    import web_app

    calls = []

    def fake_verify(config, enable_thinking=False):
        calls.append((config.model, config.base_url, enable_thinking))

    monkeypatch.setattr(web_app, "verify_llm_available", fake_verify)

    config = LLMConfig(
        api_key="main-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        advanced_model="deepseek-v4-pro",
        advanced_base_url="https://api.deepseek.com/v1",
        advanced_api_key="advanced-key",
        advanced_thinking_level="high",
    )

    web_app._verify_llm_configs_or_raise(config)

    assert calls == [
        ("gpt-4o-mini", "https://api.openai.com/v1", False),
        ("deepseek-v4-pro", "https://api.deepseek.com/v1", True),
    ]
