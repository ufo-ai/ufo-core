"""The OpenRouter model-provider extension: id->slug projection, the streaming ModelClient
(reasoning budget, dead-provider re-route, truncation), and the registry seam that selects it and
prices its slugs. The client is driven against a scripted OpenAI-SDK stub — a fake stands in for the
SDK; the ModelEvents and recorded request kwargs are what the tests assert, never the stub."""

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
import selfhost_ext_openrouter as openrouter
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta
from openai.types.completion_usage import CompletionUsage

from selfhost.config import BlobConfig, Config, DatabaseConfig
from selfhost.models.interface import Message, ModelRequest, ModelResponseTruncated, TextDelta
from selfhost.models.registry import model_registry
from selfhost.schema.records import Usage

REQUEST = ModelRequest(
    model="google/gemini-2.5-pro",
    system="be terse",
    messages=(Message(role="user", content="hi"),),
    max_tokens=64,
)


def _chunk(
    content: str | None = None,
    finish: str | None = None,
    provider: str | None = None,
    usage: CompletionUsage | None = None,
) -> ChatCompletionChunk:
    choices = (
        [Choice(index=0, finish_reason=finish, delta=ChoiceDelta(content=content))]
        if content is not None or finish is not None
        else []
    )
    fields: dict[str, object] = {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "x",
        "choices": choices,
    }
    if usage is not None:
        fields["usage"] = usage
    if provider is not None:
        fields["provider"] = provider
    return ChatCompletionChunk(**fields)


def _usage(prompt: int, completion: int) -> CompletionUsage:
    return CompletionUsage(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
    )


class ScriptedCreate:
    """Plays the SDK stream factory: one scripted chunk list per call, recording the kwargs sent."""

    def __init__(self, *streams: list[ChatCompletionChunk]) -> None:
        self.streams = list(streams)
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> AsyncIterator[ChatCompletionChunk]:
        self.calls.append(kwargs)
        return _aiter(self.streams[len(self.calls) - 1])


async def _aiter(chunks: list[ChatCompletionChunk]) -> AsyncIterator[ChatCompletionChunk]:
    for chunk in chunks:
        yield chunk


def _client(create: ScriptedCreate) -> openrouter.OpenRouterModelClient:
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return openrouter.OpenRouterModelClient(client=sdk)


def test_openrouter_slug_maps_bare_ids_and_passes_slugs_through() -> None:
    assert openrouter.openrouter_slug("google/gemini-2.5-pro") == "google/gemini-2.5-pro"
    assert openrouter.openrouter_slug("gpt-5.4") == "openai/gpt-5.4"
    assert openrouter.openrouter_slug("claude-opus-4-8") == "anthropic/claude-opus-4-8"
    assert openrouter.openrouter_slug("grok-2") == "grok-2"


async def test_complete_streams_text_then_usage_with_the_reasoning_budget() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(3, 2))]
    )
    events = [event async for event in _client(create).complete(REQUEST)]
    assert events[0] == TextDelta(text="ok")
    assert events[-1] == Usage(input_tokens=3, output_tokens=2)
    kwargs = create.calls[0]
    assert kwargs["model"] == "google/gemini-2.5-pro"
    assert kwargs["extra_body"] == {"reasoning": {"effort": "high"}}
    assert kwargs["stream_options"] == {"include_usage": True}


async def test_dead_provider_completion_reroutes_excluding_that_provider() -> None:
    dead = [_chunk(finish="stop", provider="deadco"), _chunk(usage=_usage(1, 0))]
    good = [_chunk(content="recovered"), _chunk(finish="stop"), _chunk(usage=_usage(2, 3))]
    create = ScriptedCreate(dead, good)
    events = [event async for event in _client(create).complete(REQUEST)]
    assert len(create.calls) == 2
    assert events[0] == TextDelta(text="recovered")
    assert events[-1] == Usage(input_tokens=2, output_tokens=3)
    assert create.calls[1]["extra_body"]["provider"] == {"ignore": ["deadco"]}


async def test_length_finish_raises_truncated() -> None:
    create = ScriptedCreate(
        [_chunk(content="cut"), _chunk(finish="length"), _chunk(usage=_usage(1, 9))]
    )
    with pytest.raises(ModelResponseTruncated):
        [event async for event in _client(create).complete(REQUEST)]


def test_manifest_registers_a_catch_all_model_provider_with_slug_prices() -> None:
    manifest = openrouter.manifest()
    assert len(manifest.models) == 1
    provider = manifest.models[0]
    assert provider.matches("anything/at-all") is True
    assert dict(provider.prices)["z-ai/glm-5.2"].output == 3_000_000


def test_model_client_factory_requires_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(openrouter.OPENROUTER_API_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match=openrouter.OPENROUTER_API_KEY_ENV):
        openrouter._model_client("google/gemini-2.5-pro")


def test_registry_selects_openrouter_and_prices_its_slug(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV, "sk-openrouter-test")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )
    registry = model_registry(config, (openrouter.manifest(),))
    client = registry.client_for("google/gemini-2.5-pro")
    assert isinstance(client, openrouter.OpenRouterModelClient)
    priced = registry.pricing.micro_usd(
        "google/gemini-2.5-pro", Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    )
    assert priced == 11_000_000
