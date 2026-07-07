"""Provider wire-format tests for LLMClient — groq / openai / nebius.

These mock the provider SDK's client object (never real network access) to
verify each provider branch builds the request correctly (message shape,
force_json handling) and parses the response back into a plain string.
Sampling/model-quality is out of scope here; that's what
test_reasoning_methods.py's scripted-response tests cover.

Also documents a real, current gap relevant to the "test all providers"
ask: LLMClient only implements groq / openai / nebius. Anthropic and Gemini
are declared in ModelRegistry but raise NotImplementedError for text
(LLM-mode) calls — see llm_client.py's _call_anthropic/_call_gemini.
"""
from types import SimpleNamespace

import pytest

from robo_reason_reasoning.FoundationClients.src.llm_client import LLMClient


def _fake_openai_style_response(content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    """Mimics the OpenAI-SDK-shaped response object used by groq/openai/nebius."""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeChatCompletions:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeSDKClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(response))


@pytest.fixture
def make_client(monkeypatch):
    """Builds an LLMClient for the given provider/model with its SDK client
    replaced by a fake that records the call and returns a scripted response.
    """
    def _factory(provider: str, model_name: str, response_content: str = "hello"):
        response = _fake_openai_style_response(response_content)
        fake_sdk_client = _FakeSDKClient(response)
        monkeypatch.setattr(
            LLMClient, "_initialize_client", lambda self: fake_sdk_client
        )
        client = LLMClient(model_name=f"{provider}/{model_name}")
        return client, fake_sdk_client.chat.completions

    return _factory


@pytest.mark.parametrize("provider,model_name", [
    ("groq", "openai-oss-120b"),
    ("openai", "gpt-4o-mini"),
    ("nebius", "qwen3-2.5-70b"),
])
def test_provider_returns_message_content(make_client, provider, model_name):
    client, completions = make_client(provider, model_name, response_content="the answer")
    result = client(user_message="hi", system_message="sys")
    assert result == "the answer"
    assert completions.last_kwargs["model"] == client.model_name
    assert completions.last_kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]


@pytest.mark.parametrize("provider,model_name", [
    ("groq", "openai-oss-120b"),
    ("openai", "gpt-4o-mini"),
    ("nebius", "qwen3-2.5-70b"),
])
def test_force_json_sets_response_format(make_client, provider, model_name):
    client, completions = make_client(provider, model_name)
    client(user_message="hi", system_message="sys", force_json=True)
    assert completions.last_kwargs["response_format"] == {"type": "json_object"}


def test_groq_sets_response_format_when_schema_provided(make_client):
    from robo_reason_reasoning.extraction_classes import UR5Action

    client, completions = make_client("groq", "openai-oss-120b")
    client(user_message="hi", system_message="sys", force_json=True, forced_json_schema=UR5Action)

    assert completions.last_kwargs["response_format"]["type"] == "json_schema"
    assert completions.last_kwargs["response_format"]["json_schema"]["name"] == "UR5Action"


def test_usage_metrics_are_recorded_after_a_call(make_client):
    client, _ = make_client("groq", "openai-oss-120b")
    client(user_message="hi", system_message="sys")
    usage = client.get_total_usage()
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5
    assert usage["total_tokens"] == 15


def test_unknown_provider_raises_value_error(monkeypatch):
    with pytest.raises(ValueError):
        LLMClient(model_name="not-a-real-provider/some-model")


@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
def test_anthropic_and_gemini_are_not_implemented_for_text_mode(monkeypatch, provider):
    """LLMClient's ModelRegistry lists anthropic/gemini models, but the LLM
    (text) call path for both raises NotImplementedError — only
    groq/openai/nebius are actually wired up today. Relevant to the "test
    all providers" ask: there are only 3 providers to test in LLM mode, not
    5.
    """
    monkeypatch.setattr(LLMClient, "_initialize_client", lambda self: SimpleNamespace())
    client = LLMClient(model_name=f"{provider}/some-model")
    with pytest.raises(NotImplementedError):
        client(user_message="hi", system_message="sys")
