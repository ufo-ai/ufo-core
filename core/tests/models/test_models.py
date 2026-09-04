import base64
import json
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

from ufo.config import (
    BlobConfig,
    Config,
    DatabaseConfig,
    ModelsConfig,
)
from ufo.harness.models.anthropic import MAX_EMPTY_PROVIDER_RETRIES as ANTHROPIC_MAX_EMPTY_RETRIES
from ufo.harness.models.anthropic import MAX_PROVIDER_RETRIES as ANTHROPIC_MAX_RETRIES
from ufo.harness.models.anthropic import (
    OAUTH_SYSTEM_PREFIX,
    PROVIDER_PARK_THRESHOLD_SECONDS,
    AnthropicClient,
    anthropic_sdk_client,
)
from ufo.harness.models.catalog import core_model_specs
from ufo.harness.models.interface import (
    IMAGE_OMITTED_TEXT,
    ImageBlock,
    ImageSource,
    Message,
    ModelAccountRateLimited,
    ModelEvent,
    ModelRefusal,
    ModelRequest,
    ModelResponseTruncated,
    ModelStreamStart,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    trim_images,
)
from ufo.harness.models.openai import (
    CHATGPT_AUTH_CLAIM,
    CODEX_ACCOUNT_HEADER,
    CODEX_STREAM_ACCEPT,
    OpenAIClient,
    openai_messages,
    openai_sdk_client,
    responses_input,
)
from ufo.harness.models.openai import MAX_EMPTY_PROVIDER_RETRIES as OPENAI_MAX_EMPTY_RETRIES
from ufo.harness.models.openai import MAX_PROVIDER_RETRIES as OPENAI_MAX_RETRIES
from ufo.harness.models.pricing import ModelPrice
from ufo.harness.models.registry import ServingModel, model_registry
from ufo.harness.models.spec import ModelSpec, ReasoningSupport, RepeatedToolCompaction
from ufo.harness.rounds import ModelRetryAfter, ModelStreamInterrupted
from ufo.runtime.access.credentials import CredentialValueInvalid
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.workspace import ws
from ufo.schema.records import Usage

REQUEST = ModelRequest(
    model="claude-opus-4-8",
    system="be terse",
    messages=(Message(role="user", content="hi"),),
    max_tokens=64,
    conversation_cache_ttl="5m",
)

_REASONS = ReasoningSupport(supported=True, tools_with_reasoning=True)
_PRICE = ModelPrice(0, 0, 0, 0, 0)
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
    input_tokens: int = 0,
    cache_read: int = 0,
    cache_write_5m: int = 0,
    cache_write_1h: int = 0,
    cache_creation_detail: bool = True,
) -> anthropic.types.RawMessageStartEvent:
    cache_creation = (
        anthropic.types.CacheCreation(
            ephemeral_5m_input_tokens=cache_write_5m,
            ephemeral_1h_input_tokens=cache_write_1h,
        )
        if cache_creation_detail
        else None
    )
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
                cache_creation_input_tokens=cache_write_5m + cache_write_1h,
                cache_creation=cache_creation,
            ),
        ),
    )


def anthropic_text(text: str) -> anthropic.types.RawContentBlockDeltaEvent:
    return anthropic.types.RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=0,
        delta=anthropic.types.TextDelta(type="text_delta", text=text),
    )


def anthropic_thinking(text: str, signature: str, index: int = 0) -> list[object]:
    """One whole thinking block as the provider streams it: an empty start block, the reasoning
    text in deltas, the signature that authenticates it, then the close."""
    return [
        anthropic.types.RawContentBlockStartEvent(
            type="content_block_start",
            index=index,
            content_block=anthropic.types.ThinkingBlock(type="thinking", thinking="", signature=""),
        ),
        anthropic.types.RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=index,
            delta=anthropic.types.ThinkingDelta(type="thinking_delta", thinking=text),
        ),
        anthropic.types.RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=index,
            delta=anthropic.types.SignatureDelta(type="signature_delta", signature=signature),
        ),
        anthropic.types.RawContentBlockStopEvent(type="content_block_stop", index=index),
    ]


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
    prompt: int = 0,
    completion: int = 0,
    cached: int | None = None,
    cache_write: int | None = None,
) -> chat_completion_chunk.ChatCompletionChunk:
    details = (
        PromptTokensDetails(cached_tokens=cached, cache_write_tokens=cache_write)
        if cached is not None or cache_write is not None
        else None
    )
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
    monkeypatch.setattr("ufo.harness.models.anthropic.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr("ufo.harness.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)


async def collect(
    client: AnthropicClient | OpenAIClient, request: ModelRequest = REQUEST
) -> list[ModelEvent]:
    return [
        event async for event in client.complete(request) if not isinstance(event, ModelStreamStart)
    ]


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
    conversation_cache_ttl="5m",
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


@pytest.mark.parametrize("ttl", ["1h", "5m"])
async def test_anthropic_caches_tools_system_and_growing_conversation(ttl: str) -> None:
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    request = REQUEST.model_copy(
        update={
            "conversation_cache_ttl": ttl,
            "tools": (
                ToolSchema(name="first", description="one", input_schema={"type": "object"}),
                ToolSchema(name="last", description="two", input_schema={"type": "object"}),
            ),
        }
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC).complete(
        request
    ):
        pass
    system_cache = {"type": "ephemeral", "ttl": "1h"}
    conversation_cache = {"type": "ephemeral", "ttl": ttl}
    assert create.kwargs["cache_control"] == conversation_cache
    assert create.kwargs["system"] == [
        {"type": "text", "text": "be terse", "cache_control": system_cache}
    ]
    tools = create.kwargs["tools"]
    assert "cache_control" not in tools[0]
    assert tools[1]["cache_control"] == system_cache


async def test_an_oauth_credential_leads_with_the_system_block_it_is_granted_under() -> None:
    """A member's Anthropic OAuth token is granted for Claude Code, and the API answers a request
    from it only when that identity leads the system blocks — measured: without it the same token
    is refused, with it the agent's own prompt rides as the second block and is not lost. An API
    key carries no such block, so a workspace running on one is unchanged."""
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    async for _ in AnthropicClient(
        client=anthropic_sdk(create), spec=ANTHROPIC_SPEC, oauth=True
    ).complete(REQUEST):
        pass

    assert create.kwargs["system"] == [
        {"type": "text", "text": OAUTH_SYSTEM_PREFIX},
        {"type": "text", "text": "be terse", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]


CHATGPT_ACCOUNT = "acct-8f2"


def chatgpt_token(claims: object) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.signature"


CHATGPT_TOKEN = chatgpt_token({CHATGPT_AUTH_CLAIM: {"chatgpt_account_id": CHATGPT_ACCOUNT}})


def core_openai_spec() -> ModelSpec:
    specs = core_model_specs("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
    return next(spec for spec in specs if spec.id == "gpt-5.5")


def test_a_chatgpt_account_token_is_served_by_the_codex_backend() -> None:
    """A member who signs in with OpenAI holds a ChatGPT account token, not a platform key, and
    api.openai.com refuses it. The token names the account it was issued for, so one slot carries
    whichever the member connected and that claim routes the client to the backend that answers for
    it, under the account, originator and beta headers that backend requires."""
    spec = core_openai_spec()
    client = spec.client(spec, CHATGPT_TOKEN)
    assert isinstance(client, OpenAIClient)
    assert client.codex
    assert str(client.client.base_url) == "https://chatgpt.com/backend-api/codex/"
    assert client.client.auth_headers == {"Authorization": f"Bearer {CHATGPT_TOKEN}"}
    headers = client.client.default_headers
    assert CODEX_ACCOUNT_HEADER == "chatgpt-account-id"
    assert CHATGPT_AUTH_CLAIM == "https://api.openai.com/auth"
    assert headers["chatgpt-account-id"] == CHATGPT_ACCOUNT
    assert headers["Accept"] == "text/event-stream"
    assert CODEX_STREAM_ACCEPT == "text/event-stream"
    assert headers["originator"] == "ufo"
    assert headers["OpenAI-Beta"] == "responses=experimental"


async def test_the_codex_backend_is_called_on_responses_whatever_the_spec_declares() -> None:
    """That backend serves `/responses` alone. Which host a member's credential reaches is not a
    fact any model's spec can carry — the same spec serves both — so a codex client calls the
    Responses surface for a chat-surface model rather than a 404 the turn cannot read."""
    chat = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    responses = CapturingCreate(RuntimeError("codex responses surface"))
    sdk = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat)),
        responses=SimpleNamespace(create=responses),
    )
    assert OPENAI_SPEC.api_surface == "chat"
    with pytest.raises(RuntimeError, match="codex responses surface"):
        async for _ in OpenAIClient(client=sdk, spec=OPENAI_SPEC, codex=True).complete(REQUEST):
            pass
    assert chat.calls == 0
    assert responses.calls == 1


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
                anthropic_message_start(
                    input_tokens=100, cache_read=11, cache_write_5m=7, cache_write_1h=13
                ),
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
        Usage(
            input_tokens=100,
            output_tokens=42,
            cache_read_tokens=11,
            cache_write_5m_tokens=7,
            cache_write_1h_tokens=13,
        ),
    ]


async def test_anthropic_yields_the_whole_reasoning_sequence_once_the_stream_closes() -> None:
    """Both reasoning kinds ride one list in the provider's block order: the redacted block arrives
    whole on its start event, each thinking block on its close, and a round that interleaves them
    must be echoed back in that same order or the provider rejects the sequence."""
    create = ScriptedCreate(
        (
            [
                anthropic_message_start(input_tokens=100),
                *anthropic_thinking("weigh ", "sig-1"),
                anthropic.types.RawContentBlockStartEvent(
                    type="content_block_start",
                    index=1,
                    content_block=anthropic.types.RedactedThinkingBlock(
                        type="redacted_thinking", data="ZW5jcnlwdGVk"
                    ),
                ),
                anthropic.types.RawContentBlockStopEvent(type="content_block_stop", index=1),
                *anthropic_thinking("then decide", "sig-2", index=2),
                anthropic_text("ok"),
                anthropic_output(42),
            ],
            None,
        )
    )
    events = await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))
    assert events == [
        TextDelta(text="ok"),
        ThinkingBlock(thinking="weigh ", signature="sig-1"),
        RedactedThinkingBlock(data="ZW5jcnlwdGVk"),
        ThinkingBlock(thinking="then decide", signature="sig-2"),
        Usage(input_tokens=100, output_tokens=42),
    ]


async def test_anthropic_thinking_block_closing_unsigned_fails_loud() -> None:
    """The provider signs every thinking block; one that closes unsigned is a malformed stream.
    Persisted, it would ride the transcript into every later request of the conversation — a block
    the provider rejects — so the round fails here instead."""
    start, text_delta, _, stop = anthropic_thinking("half a thought", "never-delivered")
    create = ScriptedCreate(
        (
            [anthropic_message_start(input_tokens=1), start, text_delta, stop, anthropic_output(1)],
            None,
        )
    )
    with pytest.raises(RuntimeError, match="signature"):
        await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))


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
                openai_usage(prompt=10, completion=5, cached=4, cache_write=3),
            ],
            None,
        )
    )
    spec = replace(
        OPENAI_SPEC,
        price=ModelPrice(0, 0, 0, 0, 0, cache_write_30m=1),
    )
    events = await collect(OpenAIClient(client=openai_sdk(create), spec=spec))
    assert events == [
        TextDelta(text="a"),
        TextDelta(text="b"),
        Usage(
            input_tokens=3,
            output_tokens=5,
            cache_read_tokens=4,
            cache_write_30m_tokens=3,
        ),
    ]


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
    partial_usage: Usage | None


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
            partial_usage=Usage(input_tokens=1),
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
            partial_usage=None,
        ),
        id="openai",
    ),
]


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_provider_clients_mark_stream_start_before_visible_output(
    harness: ProviderHarness,
) -> None:
    create = ScriptedCreate((harness.ok_events(), None))
    events = [event async for event in harness.build(create).complete(REQUEST)]
    assert events[0] == ModelStreamStart()
    assert events[1] == TextDelta(text="ok")


@pytest.mark.parametrize("harness", PROVIDERS)
@pytest.mark.parametrize("status", [429, 500])
async def test_retryable_status_retries_then_succeeds(
    harness: ProviderHarness,
    status: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    emitted: list[tuple[str, dict[str, str]]] = []

    def meter(name: str, **dimensions: str) -> None:
        emitted.append((name, dimensions))

    monkeypatch.setattr("ufo.harness.models.anthropic.emit_metric", meter)
    monkeypatch.setattr("ufo.harness.models.openai.emit_metric", meter)
    create = ScriptedCreate(provider_error(harness.error_type, status), (harness.ok_events(), None))
    client = harness.build(create)
    with caplog.at_level(logging.INFO):
        events = await collect(client)
    assert create.calls == 2
    assert events[0] == TextDelta(text="ok")
    assert isinstance(events[-1], Usage)
    assert emitted == [
        (
            "model_provider_retry_total",
            {"provider": client.spec.provider, "model": REQUEST.model, "kind": "status"},
        )
    ]
    retry = next(
        record for record in caplog.records if record.getMessage() == "model.provider_status_retry"
    )
    assert retry.ufo["status_code"] == status


async def test_anthropic_defers_a_long_retry_for_a_delivering_child() -> None:
    wait = PROVIDER_PARK_THRESHOLD_SECONDS + 1
    create = ScriptedCreate(
        provider_error(anthropic.APIStatusError, 429, str(wait)),
        ([anthropic_message_start(), anthropic_text("late"), anthropic_output(1)], None),
    )
    request = REQUEST.model_copy(update={"defer_long_retry": True})
    client = AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC)

    with pytest.raises(ModelRetryAfter) as raised:
        await collect(client, request)

    assert raised.value.seconds == wait
    assert create.calls == 1


async def test_anthropic_keeps_a_long_retry_inside_a_foreground_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wait = PROVIDER_PARK_THRESHOLD_SECONDS + 1
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("ufo.harness.models.anthropic.asyncio.sleep", sleep)
    create = ScriptedCreate(
        provider_error(anthropic.APIStatusError, 429, str(wait)),
        ([anthropic_message_start(), anthropic_text("ok"), anthropic_output(1)], None),
    )
    client = AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC)

    events = await collect(client)

    assert slept == [wait]
    assert events[0] == TextDelta(text="ok")
    assert create.calls == 2


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_client_error_does_not_retry(
    harness: ProviderHarness, caplog: pytest.LogCaptureFixture
) -> None:
    create = ScriptedCreate(provider_error(harness.error_type, 400), (harness.ok_events(), None))
    client = harness.build(create)
    with caplog.at_level(logging.INFO), pytest.raises(harness.error_type):
        await collect(client)
    assert create.calls == 1
    failure = next(
        record for record in caplog.records if record.getMessage() == "model.provider_status_error"
    )
    assert failure.ufo == {
        "provider": client.spec.provider,
        "model": REQUEST.model,
        "attempts": 1,
        "status_code": 400,
    }


async def test_bedrock_retry_is_attributed_to_bedrock(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    emitted: list[tuple[str, dict[str, str]]] = []

    def meter(name: str, **dimensions: str) -> None:
        emitted.append((name, dimensions))

    monkeypatch.setattr("ufo.harness.models.anthropic.emit_metric", meter)
    create = ScriptedCreate(
        provider_error(anthropic.APIStatusError, 429),
        (
            [
                anthropic_message_start(input_tokens=1),
                anthropic_text("ok"),
                anthropic_output(1),
            ],
            None,
        ),
    )
    client = AnthropicClient(
        client=anthropic_sdk(create), spec=replace(ANTHROPIC_SPEC, provider="bedrock")
    )
    with caplog.at_level(logging.INFO):
        await collect(client)
    assert emitted == [
        (
            "model_provider_retry_total",
            {"provider": "bedrock", "model": REQUEST.model, "kind": "status"},
        )
    ]
    retry = next(
        record for record in caplog.records if record.getMessage() == "model.provider_status_retry"
    )
    assert retry.ufo["provider"] == "bedrock"


KEYED_ANTHROPIC_SPEC = replace(
    ANTHROPIC_SPEC, key_slot="anthropic_api_key", key_env="ANTHROPIC_API_KEY"
)
KEYED_OPENAI_SPEC = replace(OPENAI_SPEC, key_slot="openai_api_key", key_env="OPENAI_API_KEY")
REJECTED_KEY_CLIENTS = [
    pytest.param(
        lambda create: AnthropicClient(client=anthropic_sdk(create), spec=KEYED_ANTHROPIC_SPEC),
        anthropic.APIStatusError,
        r"model 'claude-opus-4-8' key was rejected by the provider: env UFO_ANTHROPIC_API_KEY "
        r"\(or ANTHROPIC_API_KEY\) or the workspace's 'anthropic_api_key' BYOK slot holds a key "
        r"anthropic does not accept\. Replace it\.",
        id="anthropic",
    ),
    pytest.param(
        lambda create: OpenAIClient(client=openai_sdk(create), spec=KEYED_OPENAI_SPEC),
        openai.APIStatusError,
        r"model 'gpt-5\.5' key was rejected by the provider: env UFO_OPENAI_API_KEY "
        r"\(or OPENAI_API_KEY\) or the workspace's 'openai_api_key' BYOK slot holds a key openai "
        r"does not accept\. Replace it\.",
        id="openai-chat",
    ),
    pytest.param(
        lambda create: OpenAIClient(
            client=SimpleNamespace(responses=SimpleNamespace(create=create)),
            spec=replace(KEYED_OPENAI_SPEC, api_surface="responses"),
        ),
        openai.APIStatusError,
        r"model 'gpt-5\.5' key was rejected by the provider: env UFO_OPENAI_API_KEY "
        r"\(or OPENAI_API_KEY\) or the workspace's 'openai_api_key' BYOK slot holds a key openai "
        r"does not accept\. Replace it\.",
        id="openai-responses",
    ),
]


@pytest.mark.parametrize(("build", "error_type", "message"), REJECTED_KEY_CLIENTS)
async def test_rejected_key_names_the_slot_and_env_it_came_from(
    build: Callable[[ScriptedCreate], AnthropicClient | OpenAIClient],
    error_type: type[Exception],
    message: str,
) -> None:
    """The #928 fix: a 401 is the provider's verdict on the key the registry resolved, so the round
    raises the credential fault naming both places that key can come from — where the SDK's own auth
    error left the turn recording `AuthenticationError: 401 Invalid bearer token`, which attributes
    the fault to neither and tells a member nothing to do. Deterministic per key, so one call."""
    create = ScriptedCreate(provider_error(error_type, 401), provider_error(error_type, 401))
    with pytest.raises(CredentialValueInvalid, match=message):
        await collect(build(create))
    assert create.calls == 1


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_retries_exhaust_after_max(
    harness: ProviderHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 the backoff cannot outlast is the account's limit, not the request's: the round raises
    the typed account fault, so a caller holding the member's other account moves the work onto it
    where the SDK's own status class named only the wire code."""
    zero_backoff(monkeypatch)
    errors = [provider_error(harness.error_type, 429) for _ in range(harness.max_retries + 1)]
    create = ScriptedCreate(*errors)
    with pytest.raises(ModelAccountRateLimited, match="no capacity left"):
        await collect(harness.build(create))
    assert create.calls == harness.max_retries + 1


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_server_error_exhaustion_stays_the_providers_own_fault(
    harness: ProviderHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a 429 names the account. A 5xx that outlives the retries is the provider's own outage,
    so moving the work onto the member's other account would spend it on a fault that account
    cannot fix."""
    zero_backoff(monkeypatch)
    errors = [provider_error(harness.error_type, 503) for _ in range(harness.max_retries + 1)]
    create = ScriptedCreate(*errors)
    with pytest.raises(harness.error_type):
        await collect(harness.build(create))
    assert create.calls == harness.max_retries + 1


async def test_a_turn_with_no_account_of_the_members_never_moves() -> None:
    """A turn the workspace or the deploy pays for holds no accounts to move between: a rate-limited
    round is the provider's fault like any other, and the holder stays on the model it began on."""
    client = SimpleNamespace()
    serving = ServingModel(model=ANTHROPIC_SPEC.id, spec=ANTHROPIC_SPEC, client=client)

    assert await serving.move() is False
    assert (serving.model, serving.spec, serving.client) == (
        ANTHROPIC_SPEC.id,
        ANTHROPIC_SPEC,
        client,
    )


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_a_status_fault_after_first_yield_interrupts_the_round(
    harness: ProviderHarness,
) -> None:
    create = ScriptedCreate(
        (harness.partial_events(), provider_error(harness.error_type, 500)),
        (harness.ok_events(), None),
    )
    received = []
    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in harness.build(create).complete(REQUEST):
            received.append(event)
    assert raised.value.kind == "stream_error"
    expected: list[ModelEvent] = [ModelStreamStart(), TextDelta(text="partial")]
    if harness.partial_usage is not None:
        expected.append(harness.partial_usage)
    assert received == expected
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
    error_logs = [
        record
        for record in caplog.records
        if record.getMessage() == "model.provider_transport_error"
    ]
    assert len(error_logs) == 1
    assert error_logs[0].ufo["error_class"] == type(first).__name__
    retry_logs = [
        record
        for record in caplog.records
        if record.getMessage() == "model.provider_transport_retry"
    ]
    assert len(retry_logs) == harness.max_retries
    assert {record.ufo["error_class"] for record in retry_logs} == {type(first).__name__}


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_a_timeout_after_first_yield_interrupts_the_round(
    harness: ProviderHarness,
) -> None:
    create = ScriptedCreate(
        (harness.partial_events(), provider_timeout(harness.timeout_type)),
        (harness.ok_events(), None),
    )
    received = []
    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in harness.build(create).complete(REQUEST):
            received.append(event)
    assert raised.value.kind == "stream_transport"
    expected: list[ModelEvent] = [ModelStreamStart(), TextDelta(text="partial")]
    if harness.partial_usage is not None:
        expected.append(harness.partial_usage)
    assert received == expected
    assert create.calls == 1


def remote_protocol_error() -> httpx.RemoteProtocolError:
    """The peer-closed-mid-stream fault: a raw httpx error neither SDK wraps once streaming has
    started, so it must ride the same retry clause as a timeout (#998)."""
    return httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body (incomplete chunked read)"
    )


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_remote_protocol_error_retries_then_succeeds(
    harness: ProviderHarness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    zero_backoff(monkeypatch)
    create = ScriptedCreate(remote_protocol_error(), (harness.ok_events(), None))
    with caplog.at_level(logging.INFO):
        events = await collect(harness.build(create))
    assert create.calls == 2
    assert events[0] == TextDelta(text="ok")
    assert isinstance(events[-1], Usage)
    retry = next(
        record
        for record in caplog.records
        if record.getMessage() == "model.provider_transport_retry"
    )
    assert retry.ufo["error_class"] == "RemoteProtocolError"


@pytest.mark.parametrize("harness", PROVIDERS)
async def test_a_disconnect_after_first_yield_interrupts_the_round(
    harness: ProviderHarness,
) -> None:
    create = ScriptedCreate(
        (harness.partial_events(), remote_protocol_error()),
        (harness.ok_events(), None),
    )
    received = []
    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in harness.build(create).complete(REQUEST):
            received.append(event)
    assert raised.value.kind == "stream_transport"
    assert "peer closed connection" in str(raised.value)
    expected: list[ModelEvent] = [ModelStreamStart(), TextDelta(text="partial")]
    if harness.partial_usage is not None:
        expected.append(harness.partial_usage)
    assert received == expected
    assert create.calls == 1


async def test_anthropic_iteration_remote_protocol_error_before_first_event_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact production shape (#998): the stream opens, then the peer closes the connection
    before a single event arrives — a raw httpx error during iteration, same as a timeout."""
    zero_backoff(monkeypatch)
    create = ScriptedCreate(
        ([], remote_protocol_error()),
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
    assert events == [
        TextDelta(text="ok"),
        Usage(input_tokens=1, output_tokens=1),
    ]


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
    assert events == [
        TextDelta(text="ok"),
        Usage(input_tokens=1, output_tokens=1),
    ]


async def test_openai_iteration_timeout_before_first_event_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """openai.APITimeoutError derives from APIConnectionError, not httpx, and the SDK wraps only
    the create call — a read timeout during iteration arrives as the raw httpx error."""
    zero_backoff(monkeypatch)
    create = ScriptedCreate(
        ([], stream_read_timeout()),
        ([openai_text("ok"), openai_usage(prompt=1, completion=1)], None),
    )
    events = await collect(OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC))
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
    assert events == [
        Usage(input_tokens=1),
        TextDelta(text="ok"),
        Usage(input_tokens=1, output_tokens=1),
    ]


async def test_anthropic_iteration_timeout_after_first_event_interrupts_the_round() -> None:
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
    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in AnthropicClient(
            client=anthropic_sdk(create), spec=ANTHROPIC_SPEC
        ).complete(REQUEST):
            received.append(event)
    assert raised.value.kind == "stream_transport"
    assert received == [
        ModelStreamStart(),
        TextDelta(text="partial"),
        Usage(input_tokens=1),
    ]
    assert create.calls == 1


async def test_an_anthropic_mid_stream_error_after_first_yield_interrupts_the_round() -> None:
    """The GLM idle-stall shape on the Anthropic wire: a 200-coded error event on the live stream
    after visible output. Not deterministic, so it interrupts the round instead of failing the
    turn."""
    create = ScriptedCreate(
        (
            [anthropic_message_start(input_tokens=1), anthropic_text("partial")],
            anthropic_mid_stream_error(),
        ),
    )
    received = []
    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in AnthropicClient(
            client=anthropic_sdk(create), spec=ANTHROPIC_SPEC
        ).complete(REQUEST):
            received.append(event)
    assert raised.value.kind == "stream_error"
    assert received == [
        ModelStreamStart(),
        TextDelta(text="partial"),
        Usage(input_tokens=1),
    ]
    assert create.calls == 1


async def test_an_openai_sse_injected_error_interrupts_the_round() -> None:
    """A mid-stream error frame: the SDK raises the exact APIError class when an SSE data chunk
    carries an error body on the already-200 stream."""
    sse_error = openai.APIError(
        "Upstream idle timeout exceeded",
        request=httpx.Request("POST", "https://provider.invalid/v1"),
        body=None,
    )
    create = ScriptedCreate(
        ([openai_text("partial")], sse_error),
        ([openai_text("ok"), openai_usage(prompt=1, completion=1)], None),
    )
    received = []
    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(
            REQUEST
        ):
            received.append(event)
    assert raised.value.kind == "stream_error"
    assert "Upstream idle timeout exceeded" in str(raised.value)
    assert received == [ModelStreamStart(), TextDelta(text="partial")]
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
    events = []
    with pytest.raises(ModelResponseTruncated):
        async for event in AnthropicClient(
            client=anthropic_sdk(create), spec=ANTHROPIC_SPEC
        ).complete(REQUEST):
            events.append(event)
    assert events[-1] == Usage(input_tokens=1, output_tokens=9)


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
    events = []
    with pytest.raises(ModelRefusal):
        async for event in AnthropicClient(
            client=anthropic_sdk(create), spec=ANTHROPIC_SPEC
        ).complete(REQUEST):
            events.append(event)
    assert events == [ModelStreamStart(), Usage(input_tokens=1, output_tokens=1)]
    assert create.calls == 1


async def test_openai_truncation_raises() -> None:
    create = ScriptedCreate(
        (
            [openai_text("cut of"), openai_finish("length"), openai_usage(prompt=1, completion=9)],
            None,
        )
    )
    events = []
    with pytest.raises(ModelResponseTruncated):
        async for event in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(
            REQUEST
        ):
            events.append(event)
    assert events[-1] == Usage(input_tokens=1, output_tokens=9)


async def test_openai_truncation_without_usage_still_raises_truncated() -> None:
    """A cut-off round that carries no usage chunk keeps its truncation class: the engine recovers
    on that class alone, so a no-usage RuntimeError would cost the round its salvaged output and
    its truncation feedback."""
    create = ScriptedCreate(([openai_text("cut of"), openai_finish("length")], None))
    events = []
    with pytest.raises(ModelResponseTruncated):
        async for event in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(
            REQUEST
        ):
            events.append(event)
    assert events == [ModelStreamStart(), TextDelta(text="cut of")]


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
        Usage(input_tokens=1, output_tokens=0),
        TextDelta(text="recovered"),
        Usage(input_tokens=2, output_tokens=3),
    ]


async def test_anthropic_reasoning_without_an_answer_is_an_empty_completion() -> None:
    create = ScriptedCreate(
        (
            [
                anthropic_message_start(input_tokens=1),
                *anthropic_thinking("thought about it", "sig-dropped"),
                anthropic_output(0, stop_reason="end_turn"),
            ],
            None,
        ),
        (
            [
                anthropic_message_start(input_tokens=2),
                *anthropic_thinking("thought again", "sig-kept"),
                anthropic_text("recovered"),
                anthropic_output(3, stop_reason="end_turn"),
            ],
            None,
        ),
    )
    events = await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))
    assert create.calls == 2
    assert events == [
        Usage(input_tokens=1, output_tokens=0),
        TextDelta(text="recovered"),
        ThinkingBlock(thinking="thought again", signature="sig-kept"),
        Usage(input_tokens=2, output_tokens=3),
    ]


async def test_anthropic_stream_dying_after_the_thinking_block_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zero_backoff(monkeypatch)
    create = ScriptedCreate(
        (
            [
                anthropic_message_start(input_tokens=1),
                *anthropic_thinking("abandoned", "sig-dropped"),
            ],
            stream_read_timeout(),
        ),
        (
            [
                anthropic_message_start(input_tokens=2),
                *anthropic_thinking("kept", "sig-kept"),
                anthropic_text("recovered"),
                anthropic_output(3),
            ],
            None,
        ),
    )
    events = await collect(AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC))
    assert create.calls == 2
    assert events == [
        Usage(input_tokens=1, output_tokens=0),
        TextDelta(text="recovered"),
        ThinkingBlock(thinking="kept", signature="sig-kept"),
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
        Usage(input_tokens=1, output_tokens=0),
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
    assert events == [Usage(input_tokens=1, output_tokens=0)] * (ANTHROPIC_MAX_EMPTY_RETRIES + 1)


async def test_openai_persistent_empty_degrades_to_empty() -> None:
    empty = ([openai_finish("stop"), openai_usage(prompt=1, completion=0)], None)
    create = ScriptedCreate(*([empty] * (OPENAI_MAX_EMPTY_RETRIES + 1)))
    events = await collect(OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC))
    assert create.calls == OPENAI_MAX_EMPTY_RETRIES + 1
    assert events == [Usage(input_tokens=1, output_tokens=0)] * (OPENAI_MAX_EMPTY_RETRIES + 1)


def test_sdk_client_factories_disable_sdk_retries() -> None:
    anthropic_sdk = anthropic_sdk_client("key")
    openai_sdk = openai_sdk_client("key")
    assert anthropic_sdk.max_retries == 0
    assert openai_sdk.max_retries == 0


async def test_anthropic_default_request_enables_adaptive_thinking_without_effort() -> None:
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC).complete(
        REQUEST
    ):
        pass
    assert create.kwargs["thinking"] == {"type": "adaptive"}
    assert "output_config" not in create.kwargs


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


async def test_anthropic_required_reasoning_clamps_off_to_minimum() -> None:
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    spec = replace(
        next(
            spec
            for spec in core_model_specs("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
            if spec.id == "claude-opus-5"
        ),
        reasoning=ReasoningSupport(
            supported=True, tools_with_reasoning=True, default_on=True, can_disable=False
        ),
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=spec).complete(
        REQUEST.model_copy(update={"model": spec.id, "reasoning": "off"})
    ):
        pass
    assert create.kwargs["thinking"] == {"type": "adaptive"}
    assert create.kwargs["output_config"] == {"effort": "low"}


REASONING_REQUEST = ModelRequest(
    model="claude-opus-4-8",
    system="be terse",
    max_tokens=64,
    conversation_cache_ttl="5m",
    messages=(
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content=(
                ThinkingBlock(thinking="weigh the options", signature="sig-1"),
                RedactedThinkingBlock(data="ZW5jcnlwdGVk"),
                ReasoningItemBlock(
                    id="rs_1", encrypted_content="Z3B0LWVuY3J5cHRlZA", summary=("weigh the item",)
                ),
                TextBlock(text="checking"),
                ToolUseBlock(id="t1", name="bash", input={"command": "ls"}),
            ),
        ),
        Message(role="user", content=(ToolResultBlock(tool_use_id="t1", content="chart.png"),)),
    ),
)


async def test_anthropic_request_echoes_its_own_reasoning_and_drops_the_openai_item() -> None:
    """Anthropic gets its thinking blocks back verbatim and never the OpenAI reasoning item: the
    whole rendered list is pinned, so an arm that starts carrying the other wire's block fails
    here."""
    create = CapturingCreate(
        ([anthropic_message_start(input_tokens=1), anthropic_text("ok"), anthropic_output(1)], None)
    )
    async for _ in AnthropicClient(client=anthropic_sdk(create), spec=ANTHROPIC_SPEC).complete(
        REASONING_REQUEST
    ):
        pass
    messages = create.kwargs["messages"]
    assert isinstance(messages, list)
    assert messages[1]["content"] == [
        {"type": "thinking", "thinking": "weigh the options", "signature": "sig-1"},
        {"type": "redacted_thinking", "data": "ZW5jcnlwdGVk"},
        {"type": "text", "text": "checking"},
        {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}},
    ]


def test_openai_messages_drop_every_reasoning_block() -> None:
    """The Chat Completions surface carries no reasoning at all — its assistant message has no field
    that holds any, whichever provider produced it — so the pinned list has none: neither the signed
    Anthropic blocks nor the reasoning item the Responses surface does replay."""
    assert openai_messages(REASONING_REQUEST.system, REASONING_REQUEST.messages) == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "chart.png"},
    ]


def test_responses_input_echoes_the_reasoning_item_and_drops_the_thinking_blocks() -> None:
    """The Responses surface replays its own reasoning item whole — ahead of the message and the
    function call the round chose — and drops the Anthropic blocks: the full item list is pinned, so
    a lost field or a moved item fails here."""
    assert responses_input(REASONING_REQUEST.messages) == [
        {"role": "user", "content": "hi"},
        {
            "type": "reasoning",
            "id": "rs_1",
            "encrypted_content": "Z3B0LWVuY3J5cHRlZA",
            "summary": [{"type": "summary_text", "text": "weigh the item"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "checking", "annotations": []}],
        },
        {
            "type": "function_call",
            "call_id": "t1",
            "name": "bash",
            "arguments": '{"command": "ls"}',
        },
        {"type": "function_call_output", "call_id": "t1", "output": "chart.png"},
    ]


async def test_openai_default_request_omits_reasoning_effort() -> None:
    create = CapturingCreate(([openai_text("ok"), openai_usage(prompt=1, completion=1)], None))
    async for _ in OpenAIClient(client=openai_sdk(create), spec=OPENAI_SPEC).complete(REQUEST):
        pass
    assert "reasoning_effort" not in create.kwargs


async def test_openai_reasoning_off_pins_reasoning_effort_none() -> None:
    """An absent `reasoning_effort` is the provider's own default effort, so `off` has to say
    `none`: max_tokens is reasoning-inclusive, and a caller that budgeted for the answer alone gets
    none of it back once the model reasons."""
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
    assert off.kwargs["reasoning_effort"] == "none"


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


def test_catalog_registers_gpt_5_6_sol_on_the_responses_surface(tmp_path: Path) -> None:
    """Sol shares the family's refusal of `tools` beside `reasoning_effort` on
    `/v1/chat/completions` (#568), so it is called on Responses. The family accepts 1,050,000
    tokens and bills a whole request over 272,000 at 2x input and 1.5x output, so the row carries
    the window its registered rate is true at."""
    registry = model_registry(_config(tmp_path), ())
    spec = registry.spec("gpt-5.6-sol")
    assert spec.provider == "openai"
    assert spec.api_surface == "responses"
    assert spec.context_window == 272_000
    assert spec.knowledge_cutoff == "2026-02"
    assert spec.price.input == 4_000_000
    assert spec.price.output == 20_000_000
    assert spec.price.cache_read == 400_000
    assert spec.price.cache_write_30m == 5_000_000


def test_registry_rejects_an_auto_model_no_spec_describes(tmp_path: Path) -> None:
    """Every agent that defers its model resolves through `auto_model` on every turn, so a knob
    naming no registered spec is a boot failure — not one mid-turn failure per workspace."""
    with pytest.raises(ValueError, match=r"models\.auto_model 'claude-opus-6' is not a registered"):
        model_registry(_config(tmp_path, ModelsConfig(auto_model="claude-opus-6")), ())


def test_registry_rejects_two_specs_for_one_id(tmp_path: Path) -> None:
    clash = core_model_specs("ANTHROPIC_API_KEY", "OPENAI_API_KEY")[0]
    dup = Manifest(name="dup", version="1", models=(clash,))
    with pytest.raises(ValueError, match="two model specs registered for id"):
        model_registry(_config(tmp_path), (dup,))


@pytest.mark.parametrize(
    ("model_id", "key_env", "key_slot"),
    (
        ("claude-opus-4-8", "ANTHROPIC_API_KEY", "anthropic_api_key"),
        ("gpt-5.4", "OPENAI_API_KEY", "openai_api_key"),
    ),
)
async def test_registry_rejects_non_ascii_core_provider_keys(
    model_id: str,
    key_env: str,
    key_slot: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(f"UFO_{key_env}", raising=False)
    monkeypatch.setenv(key_env, "—")
    registry = model_registry(_config(tmp_path), ())
    with ws(uuid4()):
        with pytest.raises(
            CredentialValueInvalid,
            match=(
                rf"model {model_id!r} key contains non-ASCII characters: env UFO_{key_env} \(or "
                rf"{key_env}\) or the workspace's {key_slot!r} BYOK slot holds a value "
                r"the provider wire cannot carry\."
            ),
        ):
            await registry.client_for(model_id)


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


def test_model_request_rejects_a_forced_choice_that_names_no_offered_tool() -> None:
    with pytest.raises(ValueError, match="names no offered tool"):
        ModelRequest(
            model="m",
            system="s",
            messages=(),
            max_tokens=1,
            conversation_cache_ttl="5m",
            tool_choice="finish",
            reasoning="off",
        )


def test_model_request_forced_choice_accepts_required_reasoning() -> None:
    tool = ToolSchema(name="finish", description="d", input_schema={"type": "object"})
    request = ModelRequest(
        model="m",
        system="s",
        messages=(),
        max_tokens=1,
        conversation_cache_ttl="5m",
        tools=(tool,),
        tool_choice="finish",
        reasoning="low",
    )
    assert request.reasoning == "low"
    forced = ModelRequest(
        model="m",
        system="s",
        messages=(),
        max_tokens=1,
        conversation_cache_ttl="5m",
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


def test_model_spec_rejects_an_empty_compaction_tail() -> None:
    with pytest.raises(ValueError, match="compaction_keep_messages must be positive"):
        ModelSpec(
            id="x",
            provider="openai",
            client=lambda spec, key: OpenAIClient(client=openai_sdk_client(key), spec=spec),
            price=_PRICE,
            knowledge_cutoff="2026-02",
            context_window=1,
            reasoning=_REASONS,
            api_surface="chat",
            compaction_keep_messages=0,
        )


def test_model_spec_rejects_a_nonpositive_compaction_trigger() -> None:
    with pytest.raises(ValueError, match="compaction_trigger_tokens must be positive"):
        ModelSpec(
            id="x",
            provider="openai",
            client=lambda spec, key: OpenAIClient(client=openai_sdk_client(key), spec=spec),
            price=_PRICE,
            knowledge_cutoff="2026-02",
            context_window=1,
            reasoning=_REASONS,
            api_surface="chat",
            compaction_trigger_tokens=0,
        )


def test_model_spec_rejects_a_compaction_trigger_at_the_context_limit() -> None:
    with pytest.raises(ValueError, match="compaction_trigger_tokens must be below"):
        ModelSpec(
            id="x",
            provider="openai",
            client=lambda spec, key: OpenAIClient(client=openai_sdk_client(key), spec=spec),
            price=_PRICE,
            knowledge_cutoff="2026-02",
            context_window=100,
            reasoning=_REASONS,
            api_surface="chat",
            compaction_trigger_tokens=100,
        )


@pytest.mark.parametrize(
    ("consecutive_turns", "trigger_percent", "message"),
    [
        (1, 30, "consecutive_turns must be at least 2"),
        (4, 0, "trigger_percent must be from 1 to 99"),
        (4, 100, "trigger_percent must be from 1 to 99"),
        (4, 101, "trigger_percent must be from 1 to 99"),
    ],
)
def test_repeated_tool_compaction_policy_rejects_invalid_values(
    consecutive_turns: int, trigger_percent: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RepeatedToolCompaction(
            consecutive_turns=consecutive_turns,
            trigger_percent=trigger_percent,
        )


def test_required_reasoning_uses_minimum_for_internal_and_stored_off_requests() -> None:
    support = ReasoningSupport(
        supported=True, tools_with_reasoning=True, default_on=True, can_disable=False
    )
    assert support.internal_effort() == "low"
    assert (
        ModelSpec(
            id="required-reasoning",
            provider="anthropic",
            client=lambda spec, key: AnthropicClient(client=anthropic_sdk_client(key), spec=spec),
            price=_PRICE,
            knowledge_cutoff="2026-01",
            context_window=1,
            reasoning=support,
            api_surface="chat",
        ).wire_reasoning("off", ())
        == "low"
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
