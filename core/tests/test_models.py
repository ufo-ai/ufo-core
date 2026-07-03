from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from anthropic.types.raw_message_delta_event import Delta
from openai.types.chat import chat_completion_chunk
from openai.types.completion_usage import CompletionUsage, PromptTokensDetails

from selfhost.models.anthropic import MAX_EMPTY_PROVIDER_RETRIES as ANTHROPIC_MAX_EMPTY_RETRIES
from selfhost.models.anthropic import MAX_PROVIDER_RETRIES as ANTHROPIC_MAX_RETRIES
from selfhost.models.anthropic import AnthropicClient, anthropic_sdk_client
from selfhost.models.interface import (
    Message,
    ModelEvent,
    ModelRequest,
    ModelResponseTruncated,
    TextDelta,
)
from selfhost.models.openai import MAX_EMPTY_PROVIDER_RETRIES as OPENAI_MAX_EMPTY_RETRIES
from selfhost.models.openai import MAX_PROVIDER_RETRIES as OPENAI_MAX_RETRIES
from selfhost.models.openai import OpenAIClient, openai_sdk_client
from selfhost.schema.records import Usage

REQUEST = ModelRequest(
    model="claude-opus-4-8",
    system="be terse",
    messages=(Message(role="user", content="hi"),),
    max_tokens=64,
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


async def collect(client: AnthropicClient | OpenAIClient) -> list[ModelEvent]:
    return [event async for event in client.complete(REQUEST)]


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
    events = await collect(AnthropicClient(client=anthropic_sdk(create)))
    assert events == [
        TextDelta(text="Hel"),
        TextDelta(text="lo"),
        Usage(input_tokens=100, output_tokens=42, cache_read_tokens=11, cache_write_tokens=7),
    ]


async def test_anthropic_stream_without_usage_raises() -> None:
    create = ScriptedCreate(([anthropic_message_start(), anthropic_text("x")], None))
    with pytest.raises(RuntimeError, match="model stream produced no usage"):
        await collect(AnthropicClient(client=anthropic_sdk(create)))


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
    events = await collect(OpenAIClient(client=openai_sdk(create)))
    assert events == [
        TextDelta(text="a"),
        TextDelta(text="b"),
        Usage(input_tokens=10, output_tokens=5, cache_read_tokens=4, cache_write_tokens=0),
    ]


async def test_openai_usage_without_cached_tokens_reads_zero() -> None:
    create = ScriptedCreate(([openai_text("a"), openai_usage(prompt=3, completion=2)], None))
    events = await collect(OpenAIClient(client=openai_sdk(create)))
    assert events[-1] == Usage(input_tokens=3, output_tokens=2)


async def test_openai_stream_without_usage_raises() -> None:
    create = ScriptedCreate(([openai_text("a")], None))
    with pytest.raises(RuntimeError, match="model stream produced no usage"):
        await collect(OpenAIClient(client=openai_sdk(create)))


@dataclass(frozen=True)
class ProviderHarness:
    build: Callable[[ScriptedCreate], AnthropicClient | OpenAIClient]
    error_type: type[Exception]
    max_retries: int
    ok_events: Callable[[], list[object]]
    partial_events: Callable[[], list[object]]


PROVIDERS = [
    pytest.param(
        ProviderHarness(
            build=lambda create: AnthropicClient(client=anthropic_sdk(create)),
            error_type=anthropic.APIStatusError,
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
            build=lambda create: OpenAIClient(client=openai_sdk(create)),
            error_type=openai.APIStatusError,
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
    create = ScriptedCreate(
        provider_error(harness.error_type, status), (harness.ok_events(), None)
    )
    events = await collect(harness.build(create))
    assert create.calls == 2
    assert events[0] == TextDelta(text="ok")
    assert isinstance(events[-1], Usage)


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_client_error_does_not_retry(harness: ProviderHarness) -> None:
    create = ScriptedCreate(
        provider_error(harness.error_type, 400), (harness.ok_events(), None)
    )
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
        await collect(AnthropicClient(client=anthropic_sdk(create)))


async def test_openai_truncation_raises() -> None:
    create = ScriptedCreate(
        (
            [openai_text("cut of"), openai_finish("length"), openai_usage(prompt=1, completion=9)],
            None,
        )
    )
    with pytest.raises(ModelResponseTruncated):
        await collect(OpenAIClient(client=openai_sdk(create)))


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
    events = await collect(AnthropicClient(client=anthropic_sdk(create)))
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
    events = await collect(OpenAIClient(client=openai_sdk(create)))
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
    events = await collect(AnthropicClient(client=anthropic_sdk(create)))
    assert create.calls == ANTHROPIC_MAX_EMPTY_RETRIES + 1
    assert events == [Usage(input_tokens=1, output_tokens=0)]


async def test_openai_persistent_empty_degrades_to_empty() -> None:
    empty = ([openai_finish("stop"), openai_usage(prompt=1, completion=0)], None)
    create = ScriptedCreate(*([empty] * (OPENAI_MAX_EMPTY_RETRIES + 1)))
    events = await collect(OpenAIClient(client=openai_sdk(create)))
    assert create.calls == OPENAI_MAX_EMPTY_RETRIES + 1
    assert events == [Usage(input_tokens=1, output_tokens=0)]


def test_sdk_client_factories_disable_sdk_retries() -> None:
    anthropic_sdk = anthropic_sdk_client("key")
    openai_sdk = openai_sdk_client("key")
    assert anthropic_sdk.max_retries == 0
    assert openai_sdk.max_retries == 0
