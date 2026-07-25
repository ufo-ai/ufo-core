import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import anthropic
import httpx
import openai
import pytest
from anthropic.types.raw_message_delta_event import Delta
from openai.types.chat import chat_completion_chunk
from openai.types.completion_usage import CompletionUsage, PromptTokensDetails

from ufo.config import BlobConfig, Config, DatabaseConfig, ModelsConfig
from ufo.ext.manifest import Manifest
from ufo.models.anthropic import MAX_EMPTY_PROVIDER_RETRIES as ANTHROPIC_MAX_EMPTY_RETRIES
from ufo.models.anthropic import MAX_PROVIDER_RETRIES as ANTHROPIC_MAX_RETRIES
from ufo.models.anthropic import AnthropicClient, anthropic_sdk_client
from ufo.models.catalog import core_model_specs
from ufo.models.interface import (
    IMAGE_OMITTED_TEXT,
    ImageBlock,
    ImageSource,
    Message,
    ModelEvent,
    ModelRefusal,
    ModelRequest,
    ModelResponseTruncated,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    trim_images,
)
from ufo.models.openai import MAX_EMPTY_PROVIDER_RETRIES as OPENAI_MAX_EMPTY_RETRIES
from ufo.models.openai import MAX_PROVIDER_RETRIES as OPENAI_MAX_RETRIES
from ufo.models.openai import OpenAIClient, openai_sdk_client, responses_request
from ufo.models.pricing import ModelPrice
from ufo.models.registry import model_registry
from ufo.models.spec import ModelSpec, ReasoningSupport
from ufo.schema.records import Usage
from ufo.workspace import ws

REQUEST = ModelRequest(
    model="claude-opus-4-8",
    system="be terse",
    messages=(Message(role="user", content="hi"),),
    max_tokens=64,
)

_REASONS = ReasoningSupport(supported=True, tools_with_reasoning=True)
_PRICE = ModelPrice(0, 0, 0, 0)
ANTHROPIC_SPEC = ModelSpec(
    id="claude-opus-4-8",
    provider="anthropic",
    client=lambda spec, key: AnthropicClient(client=anthropic_sdk_client(key), spec=spec),
    price=_PRICE,
    knowledge_cutoff="2026-01",
    context_window=200_000,
    reasoning=_REASONS,
    api_surface="chat",
)
OPENAI_SPEC = ModelSpec(
    id="gpt-5.5",
    provider="openai",
    client=lambda spec, key: OpenAIClient(client=openai_sdk_client(key), spec=spec),
    price=_PRICE,
    knowledge_cutoff="2025-12",
    context_window=272_000,
    reasoning=_REASONS,
    api_surface="chat",
)


class ScriptedCreate:
    """Plays the SDK stream factory: one scripted outcome per call.

    An Exception outcome raises at create time; an (events, tail) outcome returns a
    stream that yields the events then raises tail if it is not None.
    """

    def __init__(self, *outcomes: Exception | tuple[list[object], Exception | None]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self, **kwargs: object) -> AsyncIterator[object]:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        events, tail = outcome
        return _scripted_stream(events, tail)


async def _scripted_stream(events: list[object], tail: Exception | None) -> AsyncIterator[object]:
    for event in events:
        yield event
    if tail is not None:
        raise tail


def anthropic_sdk(create: ScriptedCreate) -> SimpleNamespace:
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def openai_sdk(create: ScriptedCreate) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def anthropic_message_start(
    input_tokens: int = 0, cache_read: int = 0, cache_write: int = 0
) -> anthropic.types.RawMessageStartEvent:
    return anthropic.types.RawMessageStartEvent(
        type="message_start",
        message=anthropic.types.Message(
            id="msg_test",
            type="message",
            role="assistant",
            model="claude-opus-4-8",
            content=[],
            stop_reason=None,
            stop_sequence=None,
            usage=anthropic.types.Usage(
                input_tokens=input_tokens,
                output_tokens=0,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_write,
            ),
        ),
    )


def anthropic_text(text: str) -> anthropic.types.RawContentBlockDeltaEvent:
    return anthropic.types.RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=0,
        delta=anthropic.types.TextDelta(type="text_delta", text=text),
    )


def anthropic_output(
    output_tokens: int, stop_reason: str | None = None
) -> anthropic.types.RawMessageDeltaEvent:
    return anthropic.types.RawMessageDeltaEvent(
        type="message_delta",
        delta=Delta(stop_reason=stop_reason),
        usage=anthropic.types.MessageDeltaUsage(output_tokens=output_tokens),
    )


def openai_text(text: str | None) -> chat_completion_chunk.ChatCompletionChunk:
    return chat_completion_chunk.ChatCompletionChunk(
        id="chunk_test",
        object="chat.completion.chunk",
        created=0,
        model="gpt-5",
        choices=[
            chat_completion_chunk.Choice(
                index=0,
                finish_reason=None,
                delta=chat_completion_chunk.ChoiceDelta(content=text),
            )
        ],
    )


def openai_finish(reason: str) -> chat_completion_chunk.ChatCompletionChunk:
    return chat_completion_chunk.ChatCompletionChunk(
        id="chunk_test",
        object="chat.completion.chunk",
        created=0,
        model="gpt-5",
        choices=[
            chat_completion_chunk.Choice(
                index=0,
                finish_reason=reason,
                delta=chat_completion_chunk.ChoiceDelta(content=None),
            )
        ],
    )


def openai_usage(
    prompt: int = 0, completion: int = 0, cached: int | None = None
) -> chat_completion_chunk.ChatCompletionChunk:
    details = PromptTokensDetails(cached_tokens=cached) if cached is not None else None
    return chat_completion_chunk.ChatCompletionChunk(
        id="chunk_test",
        object="chat.completion.chunk",
        created=0,
        model="gpt-5",
        choices=[],
        usage=CompletionUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            prompt_tokens_details=details,
        ),
    )


def provider_error(
    error_type: type[Exception], status: int, retry_after: str | None = "0"
) -> Exception:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    response = httpx.Response(
        status_code=status,
        headers=headers,
        request=httpx.Request("POST", "https://provider.invalid/v1"),
    )
    return error_type("provider error", response=response, body=None)


def provider_timeout(timeout_type: type[Exception]) -> Exception:
    return timeout_type(request=httpx.Request("POST", "https://provider.invalid/v1"))


def zero_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ufo.models.anthropic.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr("ufo.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)


async def collect(client: AnthropicClient | OpenAIClient) -> list[ModelEvent]:
    return [event async for event in client.complete(REQUEST)]


class CapturingCreate(ScriptedCreate):
    """A ScriptedCreate that also records the kwargs the client sent — the provider request."""

    def __init__(self, *outcomes: Exception | tuple[list[object], Exception | None]) -> None:
        super().__init__(*outcomes)
        self.kwargs: dict[str, object] = {}

    async def __call__(self, **kwargs: object) -> AsyncIterator[object]:
        self.kwargs = kwargs
        return await super().__call__(**kwargs)


IMAGE_REQUEST = ModelRequest(
    model="claude-opus-4-8",
    system="be terse",
    max_tokens=64,
    messages=(
        Message(
            role="user",
            content=(
                TextBlock(text="look at this"),
                ImageBlock(source=ImageSource(media_type="image/png", data="AAAA")),
            ),
        ),
        Message(
            role="assistant",
            content=(ToolUseBlock(id="t1", name="read", input={"file_path": "chart.png"}),),
        ),
        Message(
            role="user",
            content=(
                ToolResultBlock(
                    tool_use_id="t1",
                    content=(
                        TextBlock(text="chart.png"),
                        ImageBlock(source=ImageSource(media_type="image/jpeg", data="BBBB")),
                    ),
                ),
            ),
        ),
    ),
)


async def test_anthropic_request_carries_image_and_tool_result_images() -> None:
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC).complete(
        IMAGE_REQUEST
    ):
        pass
    messages = create.kwargs["messages"]
    assert messages[0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }
    assert messages[2]["content"][0]["content"] == [
        {"type": "text", "text": "chart.png"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "BBBB"}},
    ]


async def test_anthropic_caches_tools_system_and_growing_conversation() -> None:
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    request = REQUEST.model_copy(
        update={
            "tools": (
                ToolSchema(name="first", description="one", input_schema={"type": "object"}),
                ToolSchema(name="last", description="two", input_schema={"type": "object"}),
            )
        }
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC).complete(
        request
    ):
        pass
    assert create.kwargs["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert create.kwargs["system"] == [
        {
            "type": "text",
            "text": "be terse",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    tools = create.kwargs["tools"]
    assert "cache_control" not in tools[0]
    assert tools[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


async def test_openai_request_carries_image_url_and_lifts_tool_result_images() -> None:
    create = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    async for _ in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(
        IMAGE_REQUEST
    ):
        pass
    messages = create.kwargs["messages"]
    assert {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}} in messages[1][
        "content"
    ]
    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert tool_messages[0]["content"] == "chart.png"
    assert messages[-1] == {
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}}],
    }


async def test_openai_request_sends_max_completion_tokens() -> None:
    """Reasoning-tier OpenAI models reject the legacy `max_tokens` parameter outright, so the
    budget must ride `max_completion_tokens` — the judge model pin surfaced this live."""
    create = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    async for _ in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(
        IMAGE_REQUEST
    ):
        pass
    assert create.kwargs["max_completion_tokens"] == IMAGE_REQUEST.max_tokens
    assert "max_tokens" not in create.kwargs


def _images(count: int, base: int = 0) -> tuple[ImageBlock, ...]:
    return tuple(
        ImageBlock(source=ImageSource(media_type="image/png", data=str(base + i)))
        for i in range(count)
    )


def test_trim_images_enforces_per_message_cap() -> None:
    trimmed = trim_images((Message(role="user", content=_images(22)),))
    content = trimmed[0].content
    kept = [block for block in content if isinstance(block, ImageBlock)]
    dropped = [block for block in content if isinstance(block, TextBlock)]
    assert len(kept) == 20
    assert kept[0].source.data == "2"
    assert [block.text for block in dropped] == [IMAGE_OMITTED_TEXT, IMAGE_OMITTED_TEXT]


def test_trim_images_enforces_request_cap() -> None:
    messages = tuple(
        Message(role="user", content=_images(20, base=group * 20)) for group in range(6)
    )
    trimmed = trim_images(messages)
    total = sum(
        1 for message in trimmed for block in message.content if isinstance(block, ImageBlock)
    )
    assert total == 100
    assert all(isinstance(block, TextBlock) for block in trimmed[0].content)


def test_trim_images_enforces_request_byte_budget() -> None:
    """Twenty dimension-bounded scans can still sum past the provider's request size cap (the
    observed 413), so kept images also spend a byte budget, newest first."""
    six_mb = "x" * (6 * 1024 * 1024)
    messages = (
        Message(
            role="user",
            content=tuple(
                ImageBlock(source=ImageSource(media_type="image/png", data=six_mb))
                for _ in range(4)
            ),
        ),
    )
    trimmed = trim_images(messages)
    kept = [block for block in trimmed[0].content if isinstance(block, ImageBlock)]
    dropped = [block for block in trimmed[0].content if isinstance(block, TextBlock)]
    assert len(kept) == 3
    assert [block.text for block in dropped] == [IMAGE_OMITTED_TEXT]
    assert trimmed[0].content[0] == dropped[0]


def test_trim_images_trims_images_nested_in_a_tool_result() -> None:
    result = ToolResultBlock(tool_use_id="t1", content=_images(22))
    trimmed = trim_images((Message(role="user", content=(result,)),))
    parts = trimmed[0].content[0].content
    kept = [part for part in parts if isinstance(part, ImageBlock)]
    dropped = [part for part in parts if isinstance(part, TextBlock)]
    assert len(kept) == 20 and len(dropped) == 2
    assert kept[0].source.data == "2"


async def test_anthropic_maps_deltas_then_single_usage() -> None:
    create = ScriptedCreate(
        (
            [
                anthropic_message_start(input_tokens=100, cache_read=11, cache_write=7),
                anthropic_text("Hel"),
                anthropic_text("lo"),
                anthropic_output(42),
                anthropic.types.RawMessageStopEvent(type="message_stop"),
            ],
            None,
        )
    )
    events = await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))
    assert events == [
        TextDelta(text="Hel"),
        TextDelta(text="lo"),
        Usage(input_tokens=100, output_tokens=42, cache_read_tokens=11, cache_write_tokens=7),
    ]


async def test_anthropic_stream_without_usage_raises() -> None:
    create = ScriptedCreate(([anthropic_message_start(), anthropic_text("x")], None))
    with pytest.raises(RuntimeError, match="model stream produced no usage"):
        await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))


async def test_openai_maps_deltas_then_single_usage() -> None:
    create = ScriptedCreate(
        (
            [
                openai_text(None),
                openai_text("a"),
                openai_text("b"),
                openai_usage(prompt=10, completion=5, cached=4),
            ],
            None,
        )
    )
    events = await collect(OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC))
    assert events == [
        TextDelta(text="a"),
        TextDelta(text="b"),
        Usage(input_tokens=6, output_tokens=5, cache_read_tokens=4, cache_write_tokens=0),
    ]


async def test_openai_usage_without_cached_tokens_reads_zero() -> None:
    create = ScriptedCreate(([openai_text("a"), openai_usage(prompt=3, completion=2)], None))
    events = await collect(OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC))
    assert events[-1] == Usage(input_tokens=3, output_tokens=2)


async def test_openai_stream_without_usage_raises() -> None:
    create = ScriptedCreate(([openai_text("a")], None))
    with pytest.raises(RuntimeError, match="model stream produced no usage"):
        await collect(OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC))


@dataclass(frozen=True)
class ProviderHarness:
    build: Callable[[ScriptedCreate], AnthropicClient | OpenAIClient]
    error_type: type[Exception]
    timeout_type: type[Exception]
    max_retries: int
    ok_events: Callable[[], list[object]]
    partial_events: Callable[[], list[object]]


PROVIDERS = [
    pytest.param(
        ProviderHarness(
            build=lambda create: AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC),
            error_type=anthropic.APIStatusError,
            timeout_type=anthropic.APITimeoutError,
            max_retries=ANTHROPIC_MAX_RETRIES,
            ok_events=lambda: [
                anthropic_message_start(input_tokens=1),
                anthropic_text("ok"),
                anthropic_output(1),
            ],
            partial_events=lambda: [
                anthropic_message_start(input_tokens=1),
                anthropic_text("partial"),
            ],
        ),
        id="anthropic",
    ),
    pytest.param(
        ProviderHarness(
            build=lambda create: OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC),
            error_type=openai.APIStatusError,
            timeout_type=openai.APITimeoutError,
            max_retries=OPENAI_MAX_RETRIES,
            ok_events=lambda: [openai_text("ok"), openai_usage(prompt=1, completion=1)],
            partial_events=lambda: [openai_text("partial")],
        ),
        id="openai",
    ),
]


@pytest.mark.parametrize("harness", PROVIDERS)
@pytest.mark.parametrize("status", [429, 500])
async def test_retryable_status_retries_then_succeeds(
    harness: ProviderHarness, status: int
) -> None:
    create = ScriptedCreate(provider_error(harness.error_type, status), (harness.ok_events(), None))
    events = await collect(harness.build(create))
    assert create.calls == 2
    assert events[0] == TextDelta(text="ok")
    assert isinstance(events[-1], Usage)


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_client_error_does_not_retry(harness: ProviderHarness) -> None:
    create = ScriptedCreate(provider_error(harness.error_type, 400), (harness.ok_events(), None))
    with pytest.raises(harness.error_type):
        await collect(harness.build(create))
    assert create.calls == 1


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_retries_exhaust_after_max(harness: ProviderHarness) -> None:
    errors = [provider_error(harness.error_type, 429) for _ in range(harness.max_retries + 1)]
    create = ScriptedCreate(*errors)
    with pytest.raises(harness.error_type):
        await collect(harness.build(create))
    assert create.calls == harness.max_retries + 1


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_no_retry_after_first_yield(harness: ProviderHarness) -> None:
    create = ScriptedCreate(
        (harness.partial_events(), provider_error(harness.error_type, 500)),
        (harness.ok_events(), None),
    )
    received = []
    with pytest.raises(harness.error_type):
        async for event in harness.build(create).complete(REQUEST):
            received.append(event)
    assert received == [TextDelta(text="partial")]
    assert create.calls == 1


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_timeout_retries_then_succeeds(
    harness: ProviderHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    zero_backoff(monkeypatch)
    create = ScriptedCreate(provider_timeout(harness.timeout_type), (harness.ok_events(), None))
    events = await collect(harness.build(create))
    assert create.calls == 2
    assert events[0] == TextDelta(text="ok")
    assert isinstance(events[-1], Usage)


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_timeout_exhaustion_logs_and_reraises(
    harness: ProviderHarness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    zero_backoff(monkeypatch)
    first = provider_timeout(harness.timeout_type)
    create = ScriptedCreate(
        first, *(provider_timeout(harness.timeout_type) for _ in range(harness.max_retries))
    )
    with caplog.at_level(logging.INFO), pytest.raises(harness.timeout_type) as raised:
        await collect(harness.build(create))
    assert create.calls == harness.max_retries + 1
    assert type(raised.value) is type(first)
    assert str(raised.value) == str(first)
    assert any(record.getMessage() == "model.provider_timeout" for record in caplog.records)
    retry_logs = [r for r in caplog.records if r.getMessage() == "model.provider_timeout_retry"]
    assert len(retry_logs) == harness.max_retries


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_timeout_after_first_yield_does_not_retry(harness: ProviderHarness) -> None:
    create = ScriptedCreate(
        (harness.partial_events(), provider_timeout(harness.timeout_type)),
        (harness.ok_events(), None),
    )
    received = []
    with pytest.raises(harness.timeout_type):
        async for event in harness.build(create).complete(REQUEST):
            received.append(event)
    assert received == [TextDelta(text="partial")]
    assert create.calls == 1


def stream_read_timeout() -> httpx.ReadTimeout:
    return httpx.ReadTimeout(
        "read timed out", request=httpx.Request("POST", "https://provider.invalid/v1")
    )


async def test_anthropic_iteration_timeout_before_first_event_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zero_backoff(monkeypatch)
    create = ScriptedCreate(
        ([], stream_read_timeout()),
        (
            [
                anthropic_message_start(input_tokens=1),
                anthropic_text("ok"),
                anthropic_output(1),
            ],
            None,
        ),
    )
    events = await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))
    assert create.calls == 2
    assert events[0] == TextDelta(text="ok")
    assert isinstance(events[-1], Usage)


def anthropic_mid_stream_error() -> anthropic.APIStatusError:
    """A mid-stream `error` SSE event surfaces as an APIStatusError raised during iteration on the
    already-200 stream response, so its status_code is 200 — not the logical 5xx of the fault."""
    body = {"type": "error", "error": {"type": "api_error", "message": "Internal server error"}}
    response = httpx.Response(
        status_code=200, request=httpx.Request("POST", "https://provider.invalid/v1")
    )
    return anthropic.APIStatusError(f"{body}", response=response, body=body)


async def test_anthropic_mid_stream_server_error_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zero_backoff(monkeypatch)
    create = ScriptedCreate(
        ([anthropic_message_start(input_tokens=1)], anthropic_mid_stream_error()),
        (
            [
                anthropic_message_start(input_tokens=1),
                anthropic_text("ok"),
                anthropic_output(1),
            ],
            None,
        ),
    )
    events = await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))
    assert create.calls == 2
    assert events[0] == TextDelta(text="ok")
    assert isinstance(events[-1], Usage)


async def test_anthropic_iteration_timeout_after_first_event_raises() -> None:
    create = ScriptedCreate(
        (
            [anthropic_message_start(input_tokens=1), anthropic_text("partial")],
            stream_read_timeout(),
        ),
        (
            [
                anthropic_message_start(input_tokens=1),
                anthropic_text("ok"),
                anthropic_output(1),
            ],
            None,
        ),
    )
    received = []
    with pytest.raises(httpx.ReadTimeout):
        async for event in AnthropicClient(
            client=anthropic_sdk(create), spec=ANTHROPIC_SPEC
        ).complete(REQUEST):
            received.append(event)
    assert received == [TextDelta(text="partial")]
    assert create.calls == 1


async def test_anthropic_truncation_raises() -> None:
    create = ScriptedCreate(
        (
            [
                anthropic_message_start(input_tokens=1),
                anthropic_text("cut of"),
                anthropic_output(9, stop_reason="max_tokens"),
            ],
            None,
        )
    )
    with pytest.raises(ModelResponseTruncated):
        await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))


async def test_anthropic_refusal_raises() -> None:
    create = ScriptedCreate(
        (
            [
                anthropic_message_start(input_tokens=1),
                anthropic_output(1, stop_reason="refusal"),
            ],
            None,
        )
    )
    with pytest.raises(ModelRefusal):
        await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))
    assert create.calls == 1


async def test_openai_truncation_raises() -> None:
    create = ScriptedCreate(
        (
            [openai_text("cut of"), openai_finish("length"), openai_usage(prompt=1, completion=9)],
            None,
        )
    )
    with pytest.raises(ModelResponseTruncated):
        await collect(OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC))


async def test_anthropic_empty_completion_retries_then_succeeds() -> None:
    create = ScriptedCreate(
        (
            [anthropic_message_start(input_tokens=1), anthropic_output(0, stop_reason="end_turn")],
            None,
        ),
        (
            [
                anthropic_message_start(input_tokens=2),
                anthropic_text("recovered"),
                anthropic_output(3, stop_reason="end_turn"),
            ],
            None,
        ),
    )
    events = await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))
    assert create.calls == 2
    assert events == [
        TextDelta(text="recovered"),
        Usage(input_tokens=2, output_tokens=3),
    ]


async def test_openai_empty_completion_retries_then_succeeds() -> None:
    create = ScriptedCreate(
        ([openai_finish("stop"), openai_usage(prompt=1, completion=0)], None),
        (
            [openai_text("recovered"), openai_finish("stop"), openai_usage(prompt=2, completion=3)],
            None,
        ),
    )
    events = await collect(OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC))
    assert create.calls == 2
    assert events == [
        TextDelta(text="recovered"),
        Usage(input_tokens=2, output_tokens=3),
    ]


async def test_anthropic_persistent_empty_degrades_to_empty() -> None:
    empty = (
        [anthropic_message_start(input_tokens=1), anthropic_output(0, stop_reason="end_turn")],
        None,
    )
    create = ScriptedCreate(*([empty] * (ANTHROPIC_MAX_EMPTY_RETRIES + 1)))
    events = await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))
    assert create.calls == ANTHROPIC_MAX_EMPTY_RETRIES + 1
    assert events == [Usage(input_tokens=1, output_tokens=0)]


async def test_openai_persistent_empty_degrades_to_empty() -> None:
    empty = ([openai_finish("stop"), openai_usage(prompt=1, completion=0)], None)
    create = ScriptedCreate(*([empty] * (OPENAI_MAX_EMPTY_RETRIES + 1)))
    events = await collect(OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC))
    assert create.calls == OPENAI_MAX_EMPTY_RETRIES + 1
    assert events == [Usage(input_tokens=1, output_tokens=0)]


def test_sdk_client_factories_disable_sdk_retries() -> None:
    anthropic_sdk = anthropic_sdk_client("key")
    openai_sdk = openai_sdk_client("key")
    assert anthropic_sdk.max_retries == 0
    assert openai_sdk.max_retries == 0


async def test_anthropic_default_request_enables_adaptive_thinking_at_high_effort() -> None:
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC).complete(
        REQUEST
    ):
        pass
    assert create.kwargs["thinking"] == {"type": "adaptive"}
    assert create.kwargs["output_config"] == {"effort": "high"}


async def test_anthropic_reasoning_off_omits_the_thinking_block() -> None:
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC).complete(
        REQUEST.model_copy(update={"reasoning": "off"})
    ):
        pass
    assert "thinking" not in create.kwargs
    assert "output_config" not in create.kwargs


async def test_openai_default_request_carries_reasoning_effort() -> None:
    create = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    async for _ in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(REQUEST):
        pass
    assert create.kwargs["reasoning_effort"] == "high"


async def test_openai_reasoning_off_omits_reasoning_effort() -> None:
    create = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    async for _ in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(
        REQUEST.model_copy(update={"reasoning": "medium"})
    ):
        pass
    assert create.kwargs["reasoning_effort"] == "medium"
    off = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    async for _ in OpenAIClient(client=openai_sdk(off), spec=OPENAI_SPEC).complete(
        REQUEST.model_copy(update={"reasoning": "off"})
    ):
        pass
    assert "reasoning_effort" not in off.kwargs


def _config(tmp_path: Path, models: ModelsConfig | None = None) -> Config:
    return Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
        models=models or ModelsConfig(),
    )


def test_registry_resolves_auto_to_the_configured_default(tmp_path: Path) -> None:
    registry = model_registry(_config(tmp_path), ())
    assert registry.resolve("auto") == "claude-opus-5"
    assert registry.resolve("claude-sonnet-5") == "claude-sonnet-5"


def test_catalog_registers_opus_5_with_its_long_context_window(tmp_path: Path) -> None:
    registry = model_registry(_config(tmp_path), ())
    spec = registry.spec("claude-opus-5")
    assert spec.provider == "anthropic"
    assert spec.context_window == 1_000_000
    assert spec.knowledge_cutoff == "2026-05"
    assert spec.price.input == 5_000_000
    assert spec.price.output == 25_000_000


def test_registry_resolves_auto_to_an_overridden_default(tmp_path: Path) -> None:
    registry = model_registry(_config(tmp_path, ModelsConfig(auto_model="claude-sonnet-5")), ())
    assert registry.resolve("auto") == "claude-sonnet-5"


def test_registry_spec_is_keyed_by_exact_id_and_fails_loud(tmp_path: Path) -> None:
    registry = model_registry(_config(tmp_path), ())
    assert registry.spec("gpt-5.6-terra").api_surface == "responses"
    with pytest.raises(ValueError, match="no model registered for id 'nope'"):
        registry.spec("nope")


def test_registry_rejects_two_specs_for_one_id(tmp_path: Path) -> None:
    clash = core_model_specs("ANTHROPIC_API_KEY", "OPENAI_API_KEY")[0]
    dup = Manifest(name="dup", version="1", models=(clash,))
    with pytest.raises(ValueError, match="two model specs registered for id"):
        model_registry(_config(tmp_path), (dup,))


async def test_registry_builds_core_clients_from_their_specs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anthropic_wire = object()
    openai_wire = object()
    monkeypatch.setattr("ufo.models.catalog.anthropic_sdk_client", lambda key: anthropic_wire)
    monkeypatch.setattr("ufo.models.catalog.openai_sdk_client", lambda key: openai_wire)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    registry = model_registry(_config(tmp_path), ())
    with ws(uuid4()):
        anthropic_client = await registry.client_for("claude-opus-4-8")
        openai_client = await registry.client_for("gpt-5.4")
    assert isinstance(anthropic_client, AnthropicClient)
    assert anthropic_client.client is anthropic_wire
    assert anthropic_client.spec is registry.spec("claude-opus-4-8")
    assert isinstance(openai_client, OpenAIClient)
    assert openai_client.client is openai_wire
    assert openai_client.spec is registry.spec("gpt-5.4")


def test_responses_request_carries_tools_and_reasoning_together() -> None:
    """The #568 fix: a responses-surface model renders `tools` and reasoning in ONE legal request —
    the shape gpt-5.6-terra accepts, where /v1/chat/completions would 400."""
    request = REQUEST.model_copy(
        update={
            "model": "gpt-5.6-terra",
            "reasoning": "high",
            "tools": (ToolSchema(name="t", description="d", input_schema={"type": "object"}),),
        }
    )
    kwargs = responses_request(request)
    assert kwargs["reasoning"] == {"effort": "high"}
    assert [tool["name"] for tool in kwargs["tools"]] == ["t"]


async def test_chat_drops_reasoning_with_tools_when_the_model_forbids_the_pair() -> None:
    spec = ModelSpec(
        id="gpt-chat-only",
        provider="openai",
        client=lambda spec, key: OpenAIClient(client=openai_sdk_client(key), spec=spec),
        price=_PRICE,
        knowledge_cutoff="2025-12",
        context_window=272_000,
        reasoning=ReasoningSupport(supported=True, tools_with_reasoning=False),
        api_surface="chat",
    )
    create = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    request = REQUEST.model_copy(
        update={
            "reasoning": "medium",
            "tools": (ToolSchema(name="t", description="d", input_schema={"type": "object"}),),
        }
    )
    async for _ in OpenAIClient(client=openai_sdk(create), spec=spec).complete(request):
        pass
    assert "tools" in create.kwargs
    assert "reasoning_effort" not in create.kwargs


async def test_anthropic_drops_reasoning_with_tools_when_the_model_forbids_the_pair() -> None:
    spec = replace(
        ANTHROPIC_SPEC,
        reasoning=ReasoningSupport(supported=True, tools_with_reasoning=False),
    )
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    request = REQUEST.model_copy(
        update={
            "tools": (ToolSchema(name="t", description="d", input_schema={"type": "object"}),),
        }
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=spec).complete(request):
        pass
    assert "tools" in create.kwargs
    assert "thinking" not in create.kwargs
    assert "output_config" not in create.kwargs


async def test_anthropic_enables_parallel_tool_use() -> None:
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    request = REQUEST.model_copy(
        update={
            "tools": (ToolSchema(name="first", description="one", input_schema={"type": "object"}),)
        }
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC).complete(
        request
    ):
        pass
    assert create.kwargs["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": False}
    bare = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    async for _ in AnthropicClient(client=anthropic_sdk(bare), spec=ANTHROPIC_SPEC).complete(
        REQUEST
    ):
        pass
    assert "tool_choice" not in bare.kwargs


def test_model_request_rejects_a_forced_choice_that_names_no_offered_tool() -> None:
    with pytest.raises(ValueError, match="names no offered tool"):
        ModelRequest(
            model="m",
            system="s",
            messages=(),
            max_tokens=1,
            tool_choice="finish",
            reasoning="off",
        )


def test_model_request_forced_choice_requires_reasoning_off() -> None:
    tool = ToolSchema(name="finish", description="d", input_schema={"type": "object"})
    with pytest.raises(ValueError, match="reasoning off"):
        ModelRequest(
            model="m", system="s", messages=(), max_tokens=1, tools=(tool,), tool_choice="finish"
        )
    forced = ModelRequest(
        model="m",
        system="s",
        messages=(),
        max_tokens=1,
        tools=(tool,),
        tool_choice="finish",
        reasoning="off",
    )
    assert forced.tool_choice == "finish"


async def test_anthropic_forced_tool_choice_compels_the_named_tool() -> None:
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    request = REQUEST.model_copy(
        update={
            "tools": (
                ToolSchema(name="finish", description="one", input_schema={"type": "object"}),
            ),
            "tool_choice": "finish",
            "reasoning": "off",
        }
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC).complete(
        request
    ):
        pass
    assert create.kwargs["tool_choice"] == {
        "type": "tool",
        "name": "finish",
        "disable_parallel_tool_use": True,
    }
    assert "thinking" not in create.kwargs


async def test_openai_forced_tool_choice_compels_the_named_tool() -> None:
    create = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    request = REQUEST.model_copy(
        update={
            "tools": (
                ToolSchema(name="finish", description="one", input_schema={"type": "object"}),
            ),
            "tool_choice": "finish",
            "reasoning": "off",
        }
    )
    async for _ in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(request):
        pass
    assert create.kwargs["parallel_tool_calls"] is False
    assert create.kwargs["tool_choice"] == {"type": "function", "function": {"name": "finish"}}


async def test_openai_enables_parallel_tool_calls() -> None:
    create = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    request = REQUEST.model_copy(
        update={
            "tools": (ToolSchema(name="first", description="one", input_schema={"type": "object"}),)
        }
    )
    async for _ in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(request):
        pass
    assert create.kwargs["parallel_tool_calls"] is True
    bare = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    async for _ in OpenAIClient(client=openai_sdk(bare), spec=OPENAI_SPEC).complete(REQUEST):
        pass
    assert "parallel_tool_calls" not in bare.kwargs


@pytest.mark.parametrize("cutoff", ["February 2026", "2026-13", "2026-00", "26-02"])
def test_model_spec_rejects_a_non_machine_date_cutoff(cutoff: str) -> None:
    with pytest.raises(ValueError, match="is not YYYY-MM"):
        ModelSpec(
            id="x",
            provider="openai",
            client=lambda spec, key: OpenAIClient(client=openai_sdk_client(key), spec=spec),
            price=_PRICE,
            knowledge_cutoff=cutoff,
            context_window=1,
            reasoning=_REASONS,
            api_surface="chat",
        )


def test_model_spec_rejects_tools_with_reasoning_without_support() -> None:
    with pytest.raises(ValueError, match="tools_with_reasoning without reasoning support"):
        ModelSpec(
            id="x",
            provider="openai",
            client=lambda spec, key: OpenAIClient(client=openai_sdk_client(key), spec=spec),
            price=_PRICE,
            knowledge_cutoff="2026-02",
            context_window=1,
            reasoning=ReasoningSupport(supported=False, tools_with_reasoning=True),
            api_surface="chat",
        )


async def test_chat_omits_reasoning_when_the_model_does_not_support_it() -> None:
    spec = ModelSpec(
        id="no-reason",
        provider="openai",
        client=lambda spec, key: OpenAIClient(client=openai_sdk_client(key), spec=spec),
        price=_PRICE,
        knowledge_cutoff="2026-01",
        context_window=1,
        reasoning=ReasoningSupport(supported=False, tools_with_reasoning=False),
        api_surface="chat",
    )
    create = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    async for _ in OpenAIClient(client=openai_sdk(create), spec=spec).complete(
        REQUEST.model_copy(update={"reasoning": "medium"})
    ):
        pass
    assert "reasoning_effort" not in create.kwargs
