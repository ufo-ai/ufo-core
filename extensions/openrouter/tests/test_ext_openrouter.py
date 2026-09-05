"""The OpenRouter model-provider extension: id->slug projection, the streaming ModelClient
(reasoning budget, dead-provider re-route, truncation), the registry seam that selects it and prices
its slugs, `generate_image` over the Image API and `generate_video` over the asynchronous Video API.
The client is driven against a scripted OpenAI-SDK stub — a fake stands in for the SDK; the
ModelEvents and recorded request kwargs are what the tests assert, never the stub. Both generation
tools run their real handlers over an `httpx.MockTransport` that records every request and answers
canned provider JSON — no live key or network — while the key comes from the REAL credential store
and the charge lands in the REAL ledger, so the host-side key read and the `images` and `videos`
metering seams are exercised end to end."""

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from uuid import UUID, uuid4

import httpx
import openai
import pytest
import sqlalchemy as sa
import ufo_ext_openrouter as openrouter
from cryptography.fernet import Fernet
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import (
    Choice,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from openai.types.completion_usage import CompletionUsage, PromptTokensDetails
from pydantic import ValidationError
from ufo_ext_openrouter import GenerateImageInput, GenerateVideoInput

from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import workspace_tx
from ufo.harness.models.interface import (
    IMAGE_UNSUPPORTED_TEXT,
    ImageBlock,
    ImageSource,
    Message,
    ModelRequest,
    ModelResponseTruncated,
    ModelStreamStart,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)
from ufo.harness.models.registry import model_registry
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.billing.accounting import IMAGES_DIMENSION, VIDEOS_DIMENSION
from ufo.runtime.ext.context import context_for
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn, Usage
from ufo.sdk.audience import conversation_audience
from ufo.sdk.credentials import CredentialValueInvalid
from ufo.sdk.models import ModelStreamInterrupted

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

OPENROUTER_KEY = "sk-or-v1-secret-0xfeedface"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n-one").decode()
SECOND_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n-two").decode()
MP4 = b"\x00\x00\x00\x18ftypmp42-frames-and-stereo-audio"
VIDEO_JOB = "vid-01JB7"

SESSION = "9a5b0c2e-conversation"
REQUEST = ModelRequest(
    model="google/gemini-2.5-pro",
    system="be terse",
    messages=(Message(role="user", content="hi"),),
    max_tokens=64,
    conversation_cache_ttl="5m",
    session_id=SESSION,
)

GEMINI_FLASH_REQUEST = REQUEST.model_copy(update={"model": "google/gemini-3.7-flash"})


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


def _usage(prompt: int, completion: int, cached: int = 0, cache_write: int = 0) -> CompletionUsage:
    return CompletionUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=PromptTokensDetails(
            cached_tokens=cached, cache_write_tokens=cache_write
        ),
    )


class ScriptedCreate:
    """Plays the SDK stream factory: one scripted chunk list per call, recording the kwargs sent."""

    def __init__(
        self, *streams: BaseException | Sequence[BaseException | ChatCompletionChunk]
    ) -> None:
        self.streams = list(streams)
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> AsyncIterator[ChatCompletionChunk]:
        self.calls.append(kwargs)
        stream = self.streams[len(self.calls) - 1]
        if isinstance(stream, BaseException):
            raise stream
        return _aiter(stream)


async def _aiter(
    chunks: Sequence[BaseException | ChatCompletionChunk],
) -> AsyncIterator[ChatCompletionChunk]:
    for chunk in chunks:
        if isinstance(chunk, BaseException):
            raise chunk
        yield chunk


def _api_error(message: str = "The operation was aborted") -> openai.APIError:
    return openai.APIError(
        message,
        request=httpx.Request("POST", "https://openrouter.invalid/v1/chat/completions"),
        body=None,
    )


def _status_error(status: int, message: str = "provider error") -> openai.APIStatusError:
    return openai.APIStatusError(
        message,
        response=httpx.Response(
            status,
            headers={"retry-after": "0"},
            request=httpx.Request("POST", "https://openrouter.invalid/v1/chat/completions"),
        ),
        body=None,
    )


def _routing_error() -> openai.NotFoundError:
    """OpenRouter's 404 for a call it found nowhere to route, as the SDK hands it over: the whole
    body, the `error` envelope unwrapped."""
    return openai.NotFoundError(
        "No allowed providers are available for the selected model.",
        response=httpx.Response(
            404,
            request=httpx.Request("POST", "https://openrouter.invalid/v1/chat/completions"),
        ),
        body={
            "message": "No allowed providers are available for the selected model.",
            "code": 404,
        },
    )


def _dead_attempt(provider: str) -> list[ChatCompletionChunk]:
    return [_chunk(finish="stop", provider=provider), _chunk(usage=_usage(1, 0))]


def _client(
    create: ScriptedCreate,
    spec: openrouter.ModelSpec = openrouter.OPENROUTER_MODEL_SPECS[0],
) -> openrouter.OpenRouterModelClient:
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return openrouter.OpenRouterModelClient(client=sdk, spec=spec, key=OPENROUTER_KEY)


def test_forced_tool_choice_compels_the_named_tool() -> None:
    request = REQUEST.model_copy(
        update={
            "tools": (
                ToolSchema(
                    name="finish",
                    description="Return the result.",
                    input_schema={"type": "object"},
                ),
            ),
            "tool_choice": "finish",
        }
    )

    kwargs = _client(ScriptedCreate())._create_kwargs(request, frozenset())

    assert kwargs["tool_choice"] == {
        "type": "function",
        "function": {"name": "finish"},
    }
    assert kwargs["parallel_tool_calls"] is False


def test_openrouter_slug_maps_bare_ids_and_passes_slugs_through() -> None:
    assert openrouter.openrouter_slug("google/gemini-2.5-pro") == "google/gemini-2.5-pro"
    assert openrouter.openrouter_slug("gpt-5.4") == "openai/gpt-5.4"
    assert openrouter.openrouter_slug("claude-opus-4-8") == "anthropic/claude-opus-4-8"
    assert openrouter.openrouter_slug("grok-2") == "grok-2"


def test_manifest_registers_fable_5_1() -> None:
    by_id = {spec.id: spec for spec in openrouter.manifest().models}
    fable = by_id["anthropic/claude-fable-5.1"]
    assert fable.provider == "openrouter"
    assert fable.api_surface == "chat"
    assert fable.price.input == 10_000_000
    assert fable.price.output == 50_000_000
    assert fable.price.cache_read == 250_000
    assert by_id["anthropic/claude-fable-5"].price.cache_read == 1_000_000
    assert fable.context_window == 1_000_000
    assert fable.knowledge_cutoff == "2026-01"
    assert fable.reasoning.supported


def test_manifest_compacts_glm_flash_before_its_observed_coherence_boundary() -> None:
    glm_flash = {spec.id: spec for spec in openrouter.manifest().models}["z-ai/glm-5.3-flash"]
    assert glm_flash.context_window == 1_048_576
    assert glm_flash.compaction_trigger_tokens is None
    assert glm_flash.compaction_keep_messages == 3
    assert glm_flash.repeated_tool_compaction is not None
    assert glm_flash.repeated_tool_compaction.consecutive_turns == 4
    assert glm_flash.repeated_tool_compaction.trigger_percent == 50


async def test_complete_streams_text_then_usage_without_an_auto_reasoning_budget() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(3, 2))]
    )
    events = [event async for event in _client(create).complete(REQUEST)]
    assert events[:2] == [ModelStreamStart(), TextDelta(text="ok")]
    assert events[-1] == Usage(input_tokens=3, output_tokens=2)
    kwargs = create.calls[0]
    assert kwargs["model"] == "google/gemini-2.5-pro"
    assert kwargs["extra_body"] == {"session_id": SESSION}
    assert kwargs["stream_options"] == {"include_usage": True}


async def test_gemini_flash_abort_retries_immediately_once(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    emitted: list[tuple[str, dict[str, str]]] = []

    async def unexpected_sleep(_: float) -> None:
        raise AssertionError("abort retry slept")

    def meter(name: str, **dimensions: str) -> None:
        emitted.append((name, dimensions))

    monkeypatch.setattr(openrouter.asyncio, "sleep", unexpected_sleep)
    monkeypatch.setattr(openrouter, "emit_metric", meter)
    create = ScriptedCreate(
        _api_error(),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(2, 1))],
    )

    with caplog.at_level(logging.INFO):
        events = [event async for event in _client(create).complete(GEMINI_FLASH_REQUEST)]

    assert events == [
        ModelStreamStart(),
        TextDelta(text="ok"),
        Usage(input_tokens=2, output_tokens=1),
    ]
    assert create.calls[0] == create.calls[1]
    assert emitted == [
        (
            "model_provider_retry_total",
            {"provider": "openrouter", "model": "google/gemini-3.7-flash", "kind": "abort"},
        )
    ]
    retry = next(
        record for record in caplog.records if record.getMessage() == "model.provider_abort_retry"
    )
    assert retry.ufo == {
        "provider": "openrouter",
        "model": "google/gemini-3.7-flash",
        "attempt": 1,
    }


async def test_second_gemini_flash_abort_interrupts_the_round() -> None:
    create = ScriptedCreate(_api_error(), _api_error())

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for _ in _client(create).complete(GEMINI_FLASH_REQUEST):
            pass

    assert raised.value.kind == "stream_error"
    assert len(create.calls) == 2


async def test_gemini_flash_abort_after_text_interrupts_the_round() -> None:
    create = ScriptedCreate(
        [_chunk(content="partial"), _api_error()],
        [_chunk(content="wrong"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    events = []

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in _client(create).complete(GEMINI_FLASH_REQUEST):
            events.append(event)

    assert raised.value.kind == "stream_error"
    assert events == [ModelStreamStart(), TextDelta(text="partial")]
    assert len(create.calls) == 1


async def test_gemini_flash_abort_after_tool_call_start_interrupts_the_round() -> None:
    create = ScriptedCreate(
        [
            ChatCompletionChunk(
                id="c",
                object="chat.completion.chunk",
                created=0,
                model="x",
                choices=[
                    Choice(
                        index=0,
                        finish_reason=None,
                        delta=ChoiceDelta(
                            tool_calls=[
                                ChoiceDeltaToolCall(
                                    index=0,
                                    id="call-1",
                                    function=ChoiceDeltaToolCallFunction(
                                        name="inspect", arguments=""
                                    ),
                                    type="function",
                                )
                            ]
                        ),
                    )
                ],
            ),
            _api_error(),
        ],
        [_chunk(content="wrong"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    events = []

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in _client(create).complete(GEMINI_FLASH_REQUEST):
            events.append(event)

    assert raised.value.kind == "stream_error"
    assert events == [ModelStreamStart(), openrouter.ToolCallStart(id="call-1", name="inspect")]
    assert len(create.calls) == 1


@pytest.mark.parametrize(
    ("model_request", "error"),
    [
        pytest.param(
            GEMINI_FLASH_REQUEST,
            _status_error(400, "The operation was aborted"),
            id="status-subclass",
        ),
        pytest.param(
            GEMINI_FLASH_REQUEST,
            openai.APITimeoutError(
                request=httpx.Request("POST", "https://openrouter.invalid/v1/chat/completions")
            ),
            id="timeout-subclass",
        ),
        pytest.param(
            GEMINI_FLASH_REQUEST,
            openai.APIConnectionError(
                message="The operation was aborted",
                request=httpx.Request("POST", "https://openrouter.invalid/v1/chat/completions"),
            ),
            id="connection-subclass",
        ),
    ],
)
async def test_api_error_subclasses_keep_raising_raw(
    model_request: ModelRequest, error: openai.APIError
) -> None:
    create = ScriptedCreate(
        error,
        [_chunk(content="wrong"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )

    with pytest.raises(type(error)) as raised:
        async for _ in _client(create).complete(model_request):
            pass

    assert raised.value is error
    assert len(create.calls) == 1


@pytest.mark.parametrize(
    "message", ["Upstream idle timeout exceeded", "JSON error injected into SSE stream"]
)
async def test_an_error_injected_mid_stream_interrupts_the_round(message: str) -> None:
    """OpenRouter reports an upstream stall or fault as an error frame on the live stream — the
    exact APIError class — after output already yielded. The client raises the typed interruption
    so the engine discards the partial round and re-runs it once."""
    create = ScriptedCreate([_chunk(content="partial"), _api_error(message)])
    events = []

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in _client(create).complete(REQUEST):
            events.append(event)

    assert raised.value.kind == "stream_error"
    assert message in str(raised.value)
    assert events == [ModelStreamStart(), TextDelta(text="partial")]
    assert len(create.calls) == 1


async def test_an_interrupted_stream_still_yields_its_consumed_usage() -> None:
    create = ScriptedCreate([_chunk(content="partial"), _chunk(usage=_usage(7, 1)), _api_error()])
    events = []

    with pytest.raises(ModelStreamInterrupted):
        async for event in _client(create).complete(REQUEST):
            events.append(event)

    assert events == [
        ModelStreamStart(),
        TextDelta(text="partial"),
        Usage(input_tokens=7, output_tokens=1),
    ]


async def test_a_peer_disconnect_mid_stream_interrupts_the_round() -> None:
    create = ScriptedCreate(
        [
            _chunk(content="partial"),
            httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            ),
        ]
    )
    events = []

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in _client(create).complete(REQUEST):
            events.append(event)

    assert raised.value.kind == "stream_transport"
    assert "peer closed connection" in str(raised.value)
    assert events == [ModelStreamStart(), TextDelta(text="partial")]
    assert len(create.calls) == 1


async def test_cancelled_gemini_flash_stream_does_not_retry() -> None:
    error = asyncio.CancelledError()
    create = ScriptedCreate(
        error,
        [_chunk(content="wrong"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        async for _ in _client(create).complete(GEMINI_FLASH_REQUEST):
            pass

    assert raised.value is error
    assert len(create.calls) == 1


async def test_gemini_flash_abort_preserves_attempt_usage_in_order() -> None:
    create = ScriptedCreate(
        [_chunk(usage=_usage(7, 0)), _api_error()],
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(11, 3))],
    )

    events = [event async for event in _client(create).complete(GEMINI_FLASH_REQUEST)]

    assert [event for event in events if isinstance(event, Usage)] == [
        Usage(input_tokens=7, output_tokens=0),
        Usage(input_tokens=11, output_tokens=3),
    ]


async def test_status_retry_keeps_its_existing_path() -> None:
    create = ScriptedCreate(
        _status_error(500),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(2, 1))],
    )

    events = [event async for event in _client(create).complete(GEMINI_FLASH_REQUEST)]

    assert len(create.calls) == 2
    assert events == [
        ModelStreamStart(),
        TextDelta(text="ok"),
        Usage(input_tokens=2, output_tokens=1),
    ]


async def test_complete_recovers_missing_final_usage_from_the_generation() -> None:
    requests: list[httpx.Request] = []

    def generation(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "cancelled": False,
                    "finish_reason": "tool_calls",
                    "native_tokens_prompt": 13,
                    "native_tokens_completion": 5,
                    "native_tokens_cached": 8,
                }
            },
        )

    create = ScriptedCreate([_chunk(content="ok"), _chunk(finish="tool_calls")])
    client = replace(
        _client(create),
        key=OPENROUTER_KEY,
        generation_transport=httpx.MockTransport(generation),
    )

    events = [event async for event in client.complete(REQUEST)]

    assert events[-1] == Usage(input_tokens=5, output_tokens=5, cache_read_tokens=8)
    assert len(requests) == 1
    assert requests[0].url == "https://openrouter.ai/api/v1/generation?id=c"
    assert requests[0].headers["authorization"] == f"Bearer {OPENROUTER_KEY}"


async def test_generation_recovery_retries_past_the_ledgers_indexing_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(openrouter, "GENERATION_404_RETRY_SECONDS", 0.0)
    requests: list[httpx.Request] = []

    def generation(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "data": {
                    "cancelled": False,
                    "finish_reason": "tool_calls",
                    "native_tokens_prompt": 13,
                    "native_tokens_completion": 5,
                    "native_tokens_cached": 8,
                }
            },
        )

    create = ScriptedCreate([_chunk(content="ok"), _chunk(finish="tool_calls")])
    client = replace(
        _client(create),
        key=OPENROUTER_KEY,
        generation_transport=httpx.MockTransport(generation),
    )

    events = [event async for event in client.complete(REQUEST)]

    assert events[-1] == Usage(input_tokens=5, output_tokens=5, cache_read_tokens=8)
    assert len(requests) == 3


async def test_an_exhausted_generation_lookup_interrupts_the_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 that survives the whole indexing window is a generation the upstream never finalized:
    the round is interrupted for the engine's single whole-round retry instead of failing the turn
    on raise_for_status. A second exhaustion exceeds the engine's budget and fails loud."""
    monkeypatch.setattr(openrouter, "GENERATION_404_RETRY_SECONDS", 0.0)
    requests: list[httpx.Request] = []

    def generation(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    create = ScriptedCreate([_chunk(content="ok"), _chunk(finish="tool_calls")])
    client = replace(
        _client(create),
        key=OPENROUTER_KEY,
        generation_transport=httpx.MockTransport(generation),
    )

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for _ in client.complete(REQUEST):
            pass

    assert raised.value.kind == "generation_missing"
    assert "never indexed generation c" in str(raised.value)
    assert len(requests) == openrouter.GENERATION_404_RETRIES + 1


async def test_a_non_404_generation_error_still_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(openrouter, "GENERATION_404_RETRY_SECONDS", 0.0)

    def generation(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    create = ScriptedCreate([_chunk(content="ok"), _chunk(finish="tool_calls")])
    client = replace(
        _client(create),
        key=OPENROUTER_KEY,
        generation_transport=httpx.MockTransport(generation),
    )

    with pytest.raises(httpx.HTTPStatusError, match="403"):
        async for _ in client.complete(REQUEST):
            pass


async def test_a_transport_fault_on_the_generation_lookup_retries_within_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lookup GET is cheap and a whole re-run of the round is not, so a dropped connection on
    the ledger endpoint retries in place on the 404 window before anything reaches the engine."""
    monkeypatch.setattr(openrouter, "GENERATION_404_RETRY_SECONDS", 0.0)
    attempts: list[httpx.Request] = []

    def generation(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "cancelled": False,
                    "finish_reason": "tool_calls",
                    "native_tokens_prompt": 13,
                    "native_tokens_completion": 5,
                    "native_tokens_cached": 8,
                }
            },
        )

    create = ScriptedCreate([_chunk(content="ok"), _chunk(finish="tool_calls")])
    client = replace(
        _client(create),
        key=OPENROUTER_KEY,
        generation_transport=httpx.MockTransport(generation),
    )

    events = [event async for event in client.complete(REQUEST)]

    assert events[-1] == Usage(input_tokens=5, output_tokens=5, cache_read_tokens=8)
    assert len(attempts) == 2


async def test_an_exhausted_generation_lookup_transport_fault_interrupts_the_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live gap the SWE-bench wave exposed: the ledger endpoint dropping every lookup
    connection killed the turn with the raw httpx error, because the GET sat outside the
    classified stream faults. Exhausting the window now interrupts the round for the engine's
    single whole-round retry; a second exhaustion fails loud on the typed class."""
    monkeypatch.setattr(openrouter, "GENERATION_404_RETRY_SECONDS", 0.0)
    attempts: list[httpx.Request] = []

    def generation(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body (incomplete chunked read)"
        )

    create = ScriptedCreate([_chunk(content="ok"), _chunk(finish="tool_calls")])
    client = replace(
        _client(create),
        key=OPENROUTER_KEY,
        generation_transport=httpx.MockTransport(generation),
    )

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for _ in client.complete(REQUEST):
            pass

    assert raised.value.kind == "generation_lookup"
    assert "peer closed connection" in str(raised.value)
    assert len(attempts) == openrouter.GENERATION_404_RETRIES + 1


async def test_a_read_timeout_mid_stream_interrupts_the_round() -> None:
    create = ScriptedCreate(
        [
            _chunk(content="partial"),
            httpx.ReadTimeout("read timed out"),
        ]
    )
    events = []

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in _client(create).complete(REQUEST):
            events.append(event)

    assert raised.value.kind == "stream_transport"
    assert "ReadTimeout" in str(raised.value)
    assert events == [ModelStreamStart(), TextDelta(text="partial")]
    assert len(create.calls) == 1


async def test_complete_does_not_recover_an_unfinished_stream() -> None:
    requests: list[httpx.Request] = []

    def generation(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = replace(
        _client(ScriptedCreate([_chunk(content="partial")])),
        key=OPENROUTER_KEY,
        generation_transport=httpx.MockTransport(generation),
    )

    with pytest.raises(RuntimeError, match="model stream produced no usage"):
        async for _ in client.complete(REQUEST):
            pass

    assert requests == []


async def test_unpriced_cache_writes_remain_fresh_input() -> None:
    create = ScriptedCreate(
        [
            _chunk(content="ok"),
            _chunk(finish="stop"),
            _chunk(usage=_usage(10, 2, cached=4, cache_write=3)),
        ]
    )
    events = [event async for event in _client(create).complete(REQUEST)]
    assert events[-1] == Usage(
        input_tokens=6,
        output_tokens=2,
        cache_read_tokens=4,
    )


async def test_a_request_naming_no_session_is_refused_before_the_call() -> None:
    """A direct client holds one cache and never reads the field, so an unnamed series only means
    something here — and here it means a caller reached a router without saying what its prompt
    prefix belongs to. Refused before the request goes out: the alternative is a cache that quietly
    never hits, which nothing surfaces."""
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))]
    )

    with pytest.raises(RuntimeError, match="names no session_id"):
        async for _ in _client(create).complete(REQUEST.model_copy(update={"session_id": None})):
            pass

    assert create.calls == []


async def test_model_without_reasoning_omits_the_reasoning_budget() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))]
    )
    spec = replace(
        openrouter.OPENROUTER_MODEL_SPECS[0],
        reasoning=openrouter.ReasoningSupport(supported=False, tools_with_reasoning=False),
    )
    async for _ in _client(create, spec).complete(REQUEST):
        pass
    assert create.calls[0]["extra_body"] == {"session_id": SESSION}


async def test_model_without_tools_with_reasoning_omits_the_reasoning_budget() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))]
    )
    spec = replace(
        openrouter.OPENROUTER_MODEL_SPECS[0],
        reasoning=openrouter.ReasoningSupport(supported=True, tools_with_reasoning=False),
    )
    request = REQUEST.model_copy(
        update={
            "tools": (
                ToolSchema(name="search", description="Search", input_schema={"type": "object"}),
            )
        }
    )
    async for _ in _client(create, spec).complete(request):
        pass
    assert create.calls[0]["extra_body"] == {"session_id": SESSION}


def test_every_ordered_id_is_one_the_manifest_serves() -> None:
    """A key that names no registered id orders nothing and says so nowhere, so the table is held
    to the ids it claims to route."""
    served = {spec.id for spec in openrouter.manifest().models}
    assert set(openrouter.PROVIDER_ORDER) <= served


@pytest.mark.parametrize("model", ["z-ai/glm-5.3", "z-ai/glm-5.3-flash"])
def test_an_ordered_id_prioritizes_fast_providers_and_limits_fallbacks(model: str) -> None:
    spec = {spec.id: spec for spec in openrouter.OPENROUTER_MODEL_SPECS}[model]
    request = REQUEST.model_copy(update={"model": model})

    kwargs = _client(ScriptedCreate(), spec)._create_kwargs(request, frozenset())

    assert kwargs["extra_body"]["provider"] == {
        "order": ["fireworks", "baseten", "morph"],
        "allow_fallbacks": False,
    }


def test_an_id_off_the_order_sends_no_provider_preference() -> None:
    kwargs = _client(ScriptedCreate())._create_kwargs(REQUEST, frozenset())

    assert "provider" not in kwargs["extra_body"]


def test_an_id_off_the_order_sends_its_exclusions_and_no_pin() -> None:
    """The pin belongs to the table, the exclusion to the re-route: an id with no `only` of its own
    still carries `ignore`, because a re-issue without it repeats the call the dead upstream
    answered empty."""
    kwargs = _client(ScriptedCreate())._create_kwargs(
        REQUEST, frozenset({"Google", "Google AI Studio"})
    )

    assert kwargs["extra_body"]["provider"] == {"ignore": ["Google", "Google AI Studio"]}


async def test_a_dead_upstream_is_excluded_inside_the_ordered_providers() -> None:
    """The re-route and order ride one `provider` object: the empty completion's provider joins
    `ignore` while the order still holds, so the retry lands on another of the four."""
    spec = {spec.id: spec for spec in openrouter.OPENROUTER_MODEL_SPECS}["z-ai/glm-5.3-flash"]
    create = ScriptedCreate(
        [_chunk(finish="stop", provider="Morph"), _chunk(usage=_usage(1, 0))],
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )

    events = [
        event
        async for event in _client(create, spec).complete(
            REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
        )
    ]

    assert TextDelta(text="ok") in events
    assert create.calls[0]["extra_body"]["provider"] == {
        "order": ["fireworks", "baseten", "morph"],
        "allow_fallbacks": False,
    }
    assert create.calls[1]["extra_body"]["provider"] == {
        "order": ["fireworks", "baseten", "morph"],
        "allow_fallbacks": False,
        "ignore": ["Morph"],
    }


async def test_a_dead_upstream_off_the_order_is_excluded_from_the_re_issue() -> None:
    """An unpinned id carries no `only`, so the re-route's `ignore` is its whole provider
    preference — and it has to carry one: the re-issue keeps the session_id that pins the series to
    the upstream of its first answered call, so nothing else moves it off the dead one."""
    create = ScriptedCreate(
        _dead_attempt("Google"),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )

    events = [event async for event in _client(create).complete(REQUEST)]

    assert TextDelta(text="ok") in events
    assert "provider" not in create.calls[0]["extra_body"]
    assert create.calls[1]["extra_body"]["provider"] == {"ignore": ["Google"]}
    assert create.calls[1]["extra_body"]["session_id"] == SESSION


async def test_exhausted_exclusions_degrade_to_the_empty_result() -> None:
    """Every Gemini id serves from two providers, so two dead upstreams cover the slug before the
    re-route's retries run out and the next call is refused. That refusal is the client's own
    exclusions, so the round ends as the empty result the turn loop nudges on — not a 404 that
    fails the turn."""
    create = ScriptedCreate(
        _dead_attempt("Google"),
        _dead_attempt("Google AI Studio"),
        _routing_error(),
    )

    events = [event async for event in _client(create).complete(REQUEST)]

    assert [event for event in events if isinstance(event, TextDelta)] == []
    assert events[-1] == Usage(input_tokens=1, output_tokens=0)
    assert create.calls[2]["extra_body"]["provider"] == {"ignore": ["Google", "Google AI Studio"]}


async def test_a_404_before_any_exclusion_still_fails_loud() -> None:
    """The first call of a round narrowed nothing of ours, so its 404 is the account's own routing
    policy — nothing of ours to degrade on, and it raises."""
    create = ScriptedCreate(_routing_error())

    with pytest.raises(openai.NotFoundError):
        async for _ in _client(create).complete(REQUEST):
            pass

    assert "provider" not in create.calls[0]["extra_body"]


async def test_a_404_on_a_narrowed_call_still_fails_loud() -> None:
    """An ordered slug holds four routes against three exclusions at most, so `ignore` cannot
    exhaust the order. A 404 under narrowing is a refusal the client did not cause."""
    spec = {spec.id: spec for spec in openrouter.OPENROUTER_MODEL_SPECS}["z-ai/glm-5.3-flash"]
    create = ScriptedCreate(_dead_attempt("Morph"), _routing_error())
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})

    with pytest.raises(openai.NotFoundError):
        async for _ in _client(create, spec).complete(request):
            pass

    assert create.calls[1]["extra_body"]["provider"] == {
        "order": ["fireworks", "baseten", "morph"],
        "allow_fallbacks": False,
        "ignore": ["Morph"],
    }


GLM_FLASH_ROUTING = {
    "order": ["fireworks", "baseten", "morph"],
    "allow_fallbacks": False,
}


def _glm_flash_spec() -> openrouter.ModelSpec:
    return {spec.id: spec for spec in openrouter.OPENROUTER_MODEL_SPECS}["z-ai/glm-5.3-flash"]


def _stalled_stream(upstream: str) -> list[BaseException | ChatCompletionChunk]:
    return [
        _chunk(content="partial", provider=upstream),
        _api_error("Upstream idle timeout exceeded"),
    ]


async def test_every_glm_slug_is_pinned_and_none_of_them_name_a_dropped_route() -> None:
    """An id left out of the table routes across all two dozen upstreams, which is the only way
    Together or Modal ever took a call. So the gate is that every GLM slug the manifest offers is
    named here, not just the two that were measured, and that no ordered set names either route."""
    glm = [spec.id for spec in openrouter.OPENROUTER_MODEL_SPECS if spec.id.startswith("z-ai/")]

    assert glm
    assert all(slug in openrouter.PROVIDER_ORDER for slug in glm)
    for routes in openrouter.PROVIDER_ORDER.values():
        assert "together" not in routes
        assert "modal" not in routes
        assert len(routes) >= 2


async def test_a_stalled_upstream_leaves_the_rounds_re_run() -> None:
    """The live failure this closes: a round died on an injected idle timeout, the engine re-ran it,
    and it died the same way three minutes later. A re-run carries the same messages and the same
    sticky session_id, so it is the call that just stalled and OpenRouter pins it back to the
    upstream that stalled it. The upstream a stream died on joins `ignore` for the rest of the
    turn, so the re-run is served somewhere else."""
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
    create = ScriptedCreate(
        _stalled_stream("Morph"),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    client = _client(create, _glm_flash_spec())

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for _ in client.complete(request):
            pass
    events = [event async for event in client.complete(request)]

    assert raised.value.kind == "stream_error"
    assert TextDelta(text="ok") in events
    assert create.calls[0]["extra_body"]["provider"] == GLM_FLASH_ROUTING
    assert create.calls[1]["extra_body"]["provider"] == {
        **GLM_FLASH_ROUTING,
        "ignore": ["Morph"],
    }
    assert create.calls[1]["extra_body"]["session_id"] == SESSION


async def test_a_transport_fault_mid_stream_leaves_its_upstream_too() -> None:
    """A connection dropped mid-body is the same wedged route as an error frame OpenRouter injects
    when its upstream stalls, and the re-run has to land elsewhere for the same reason."""
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
    create = ScriptedCreate(
        [
            _chunk(content="partial", provider="Baseten"),
            httpx.RemoteProtocolError("peer closed connection without sending complete body"),
        ],
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    client = _client(create, _glm_flash_spec())

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for _ in client.complete(request):
            pass
    events = [event async for event in client.complete(request)]

    assert raised.value.kind == "stream_transport"
    assert TextDelta(text="ok") in events
    assert create.calls[1]["extra_body"]["provider"] == {
        **GLM_FLASH_ROUTING,
        "ignore": ["Baseten"],
    }


async def test_stalled_upstreams_never_leave_a_pinned_slug_served_nowhere() -> None:
    """`ignore` subtracts from the ordered routes, so a turn that stalled on all three would ask
    for a slug served nowhere and 404 every round after it. Two exclusions ride at most — the bound
    the dead-provider re-route already holds — so a route always stays open."""
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
    create = ScriptedCreate(
        *(_stalled_stream(upstream) for upstream in ("Baseten", "Fireworks", "Morph")),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    client = _client(create, _glm_flash_spec())

    for _ in range(3):
        with pytest.raises(ModelStreamInterrupted):
            async for _ in client.complete(request):
                pass
    events = [event async for event in client.complete(request)]

    assert TextDelta(text="ok") in events
    assert create.calls[3]["extra_body"]["provider"] == {
        **GLM_FLASH_ROUTING,
        "ignore": ["Baseten", "Fireworks"],
    }


async def test_a_stall_off_the_order_narrows_nothing() -> None:
    """`ignore` is the whole provider preference for an unpinned slug, and how many routes that
    slug has is OpenRouter's to know — narrowing one away across rounds can leave it served
    nowhere, and that 404 lands on a round holding no usage to degrade on. A stall is remembered
    only where `order` names the routes it subtracts from."""
    create = ScriptedCreate(
        _stalled_stream("Google"),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    client = _client(create)

    with pytest.raises(ModelStreamInterrupted):
        async for _ in client.complete(REQUEST):
            pass
    events = [event async for event in client.complete(REQUEST)]

    assert TextDelta(text="ok") in events
    assert "provider" not in create.calls[1]["extra_body"]


def _relayed_refusal(
    upstream: str = "Morph",
    raw: str = "morph-glm53flash accepts text parts only",
) -> openai.BadRequestError:
    """A 400 an upstream raised and OpenRouter relayed, as the SDK hands it over: the `error`
    envelope unwrapped, the upstream named beside the upstream's own body."""
    return openai.BadRequestError(
        "Provider returned error",
        response=httpx.Response(
            400,
            request=httpx.Request("POST", "https://openrouter.invalid/v1/chat/completions"),
        ),
        body={
            "message": "Provider returned error",
            "code": 400,
            "metadata": {
                "provider_name": upstream,
                "raw": json.dumps({"error": {"message": raw, "type": "invalid_request_error"}}),
            },
        },
    )


async def test_a_relayed_refusal_reroutes_to_another_upstream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The live failure this closes: two routes answered 429, the third refused a content part it
    alone rejects, and the whole request died on that 400 while a route it never asked was still
    open. The refusing upstream joins `ignore` and the call is re-issued."""
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
    create = ScriptedCreate(
        _relayed_refusal(),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )

    with caplog.at_level(logging.INFO):
        events = [event async for event in _client(create, _glm_flash_spec()).complete(request)]

    assert TextDelta(text="ok") in events
    assert create.calls[0]["extra_body"]["provider"] == GLM_FLASH_ROUTING
    assert create.calls[1]["extra_body"]["provider"] == {**GLM_FLASH_ROUTING, "ignore": ["Morph"]}
    assert create.calls[1]["extra_body"]["session_id"] == SESSION
    refused = next(
        record for record in caplog.records if record.getMessage() == "model.provider_refused_retry"
    )
    assert refused.ufo == {
        "provider": "openrouter",
        "model": "z-ai/glm-5.3-flash",
        "upstream": "Morph",
        "attempt": 1,
    }


async def test_a_relayed_refusal_off_the_order_reroutes_on_its_exclusion_alone() -> None:
    """An unpinned id carries no `only`, so the re-route's `ignore` is its whole provider
    preference here too — and it has to carry one, or the sticky session_id pins the re-issue back
    to the upstream that just refused it."""
    create = ScriptedCreate(
        _relayed_refusal("Google", "unsupported content part in messages[4]"),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )

    events = [event async for event in _client(create).complete(REQUEST)]

    assert TextDelta(text="ok") in events
    assert create.calls[1]["extra_body"]["provider"] == {"ignore": ["Google"]}


@pytest.mark.parametrize(
    "raw",
    [
        "This endpoint's maximum context length is 131072 tokens",
        "No auth credentials found: the API key is invalid",
        "Insufficient credit to serve this request",
    ],
)
async def test_a_refusal_every_route_repeats_still_fails_loud(raw: str) -> None:
    """A relayed 400 naming the request's size, the key or the account is the request being
    unservable, not one route's own limit: another route answers it identically, so the error
    reaches the caller on the first refusal."""
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
    create = ScriptedCreate(
        _relayed_refusal("Morph", raw),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )

    with pytest.raises(openai.BadRequestError):
        async for _ in _client(create, _glm_flash_spec()).complete(request):
            pass

    assert len(create.calls) == 1


async def test_a_400_openrouter_raised_itself_still_fails_loud() -> None:
    """A 400 with no `metadata.provider_name` is the router refusing the call it read, so there is
    no upstream to route around and nothing a re-issue would change."""
    error = openai.BadRequestError(
        "messages: expected an array",
        response=httpx.Response(
            400,
            request=httpx.Request("POST", "https://openrouter.invalid/v1/chat/completions"),
        ),
        body={"message": "messages: expected an array", "code": 400},
    )
    create = ScriptedCreate(
        error,
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )

    with pytest.raises(openai.BadRequestError) as raised:
        async for _ in _client(create).complete(REQUEST):
            pass

    assert raised.value is error
    assert len(create.calls) == 1


async def test_exhausted_refusal_reroutes_raise_the_last_refusal() -> None:
    """The bound preserves the old behavior at the end of the routes: when every upstream the
    re-route reaches refuses the messages, the caller still gets the BadRequestError."""
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
    last = _relayed_refusal("Fireworks")
    create = ScriptedCreate(_relayed_refusal("Morph"), _relayed_refusal("Baseten"), last)

    with pytest.raises(openai.BadRequestError) as raised:
        async for _ in _client(create, _glm_flash_spec()).complete(request):
            pass

    assert raised.value is last
    assert len(create.calls) == openrouter.MAX_REFUSED_PROVIDER_RETRIES + 1


async def test_exhausted_refusal_exclusions_degrade_to_the_empty_result() -> None:
    """An unpinned slug sends every refusing upstream as its whole `ignore`, so the re-route can
    cover the slug and the call after it is refused with a 404. A refusal streams no usage of its
    own, so the degrade ends the round on a zero Usage — a round that yields nothing at all fails
    the turn instead of degrading to the empty result."""
    create = ScriptedCreate(
        _relayed_refusal("Google", "unsupported content part in messages[4]"),
        _relayed_refusal("Google AI Studio", "unsupported content part in messages[4]"),
        _routing_error(),
    )

    events = [event async for event in _client(create).complete(REQUEST)]

    assert events == [Usage()]
    assert len(create.calls) == openrouter.MAX_REFUSED_PROVIDER_RETRIES + 1
    assert create.calls[2]["extra_body"]["provider"] == {"ignore": ["Google", "Google AI Studio"]}


async def test_a_refusal_after_output_yielded_keeps_its_plain_raise() -> None:
    """Output already reached the engine, so a re-issue would stream the round's opening twice."""
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
    create = ScriptedCreate(
        [_chunk(content="partial", provider="Morph"), _relayed_refusal()],
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    events = []

    with pytest.raises(openai.BadRequestError):
        async for event in _client(create, _glm_flash_spec()).complete(request):
            events.append(event)

    assert events == [ModelStreamStart(), TextDelta(text="partial")]
    assert len(create.calls) == 1


async def test_a_refusing_upstream_leaves_the_rest_of_the_turn() -> None:
    """The content part the upstream refused stays in the messages for every later round, and the
    sticky session_id pins the next round back to that upstream, so the exclusion outlives the
    round exactly as a stall's does."""
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
    create = ScriptedCreate(
        _relayed_refusal(),
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
        [_chunk(content="again"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    client = _client(create, _glm_flash_spec())

    async for _ in client.complete(request):
        pass
    events = [event async for event in client.complete(request)]

    assert TextDelta(text="again") in events
    assert create.calls[2]["extra_body"]["provider"] == {**GLM_FLASH_ROUTING, "ignore": ["Morph"]}


async def test_the_gemini_flash_abort_retry_keeps_its_identical_call() -> None:
    """The abort is the model's own no-output fault, not a wedged route: it clears on an immediate
    identical retry, so it is the one mid-stream fault that leaves no upstream behind."""
    create = ScriptedCreate(
        [_chunk(provider="Google"), _api_error()],
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(2, 1))],
    )

    events = [event async for event in _client(create).complete(GEMINI_FLASH_REQUEST)]

    assert TextDelta(text="ok") in events
    assert create.calls[0] == create.calls[1]


async def test_a_generation_lookup_fault_leaves_no_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stream completed and only the usage ledger failed, so the upstream served the round
    exactly as asked and the re-run keeps every route."""
    monkeypatch.setattr(openrouter, "GENERATION_404_RETRY_SECONDS", 0.0)
    request = REQUEST.model_copy(update={"model": "z-ai/glm-5.3-flash"})
    create = ScriptedCreate(
        [_chunk(content="ok", provider="Morph"), _chunk(finish="stop")],
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    client = replace(
        _client(create, _glm_flash_spec()),
        generation_transport=httpx.MockTransport(lambda _: httpx.Response(404)),
    )

    with pytest.raises(ModelStreamInterrupted) as raised:
        async for _ in client.complete(request):
            pass
    events = [event async for event in client.complete(request)]

    assert raised.value.kind == "generation_missing"
    assert TextDelta(text="ok") in events
    assert create.calls[1]["extra_body"]["provider"] == GLM_FLASH_ROUTING


def test_google_tool_results_survive_json_parser_value_refusal() -> None:
    result = '{"$value":' + "1" * 5_000 + "}"
    messages = (
        Message(role="user", content="inspect"),
        Message(role="assistant", content=(ToolUseBlock(id="c1", name="inspect", input={}),)),
        Message(role="user", content=(ToolResultBlock(tool_use_id="c1", content=result),)),
    )
    request = REQUEST.model_copy(update={"model": "google/gemini-3.7-flash", "messages": messages})

    rendered = _client(ScriptedCreate([]))._create_kwargs(request, frozenset())["messages"]

    assert isinstance(rendered, list)
    assert json.loads(rendered[-1]["content"])["text"] == result


def test_google_error_results_and_dynamic_references_are_text_enveloped() -> None:
    error_result = '{"$ref":"#/$defs/Value"}'
    dynamic_result = '{"$dynamicRef":"#value"}'
    messages = (
        Message(role="user", content="inspect"),
        Message(
            role="assistant",
            content=(
                ToolUseBlock(id="c1", name="inspect", input={}),
                ToolUseBlock(id="c2", name="inspect", input={}),
            ),
        ),
        Message(
            role="user",
            content=(
                ToolResultBlock(tool_use_id="c1", content=error_result, is_error=True),
                ToolResultBlock(tool_use_id="c2", content=dynamic_result),
            ),
        ),
    )
    request = REQUEST.model_copy(update={"model": "google/gemini-3.7-flash", "messages": messages})

    rendered = _client(ScriptedCreate([]))._create_kwargs(request, frozenset())["messages"]

    assert isinstance(rendered, list)
    assert json.loads(rendered[-2]["content"])["text"] == f"[tool error] {error_result}"
    assert json.loads(rendered[-1]["content"])["text"] == dynamic_result


def test_non_google_and_reference_free_tool_results_keep_the_standard_shape() -> None:
    result = '{"answer": 5}'
    messages = (
        Message(role="user", content="add"),
        Message(role="assistant", content=(ToolUseBlock(id="c1", name="add", input={}),)),
        Message(role="user", content=(ToolResultBlock(tool_use_id="c1", content=result),)),
    )
    google = REQUEST.model_copy(update={"model": "google/gemini-3.7-flash", "messages": messages})
    anthropic = REQUEST.model_copy(
        update={"model": "anthropic/claude-fable-5", "messages": messages}
    )
    client = _client(ScriptedCreate([]))
    assert client._create_kwargs(google, frozenset())["messages"][-1]["content"] == result
    assert client._create_kwargs(anthropic, frozenset())["messages"][-1]["content"] == result


def test_text_only_model_omits_tool_result_images_before_provider_call() -> None:
    spec = {item.id: item for item in openrouter.OPENROUTER_MODEL_SPECS}["z-ai/glm-5.3"]
    request = REQUEST.model_copy(
        update={
            "model": spec.id,
            "messages": (
                Message(role="user", content="inspect"),
                Message(
                    role="assistant",
                    content=(ToolUseBlock(id="c1", name="browser", input={}),),
                ),
                Message(
                    role="user",
                    content=(
                        ToolResultBlock(
                            tool_use_id="c1",
                            content=(
                                TextBlock(text="audit passed"),
                                ImageBlock(source=ImageSource(media_type="image/png", data=PNG)),
                            ),
                        ),
                    ),
                ),
            ),
        }
    )

    messages = _client(ScriptedCreate([]), spec)._create_kwargs(request, frozenset())["messages"]

    assert messages[-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": f"audit passed\n{IMAGE_UNSUPPORTED_TEXT}",
    }


async def test_length_finish_without_stream_usage_truncates_before_any_generation_lookup() -> None:
    """The engine recovers a truncated round on the ModelResponseTruncated class alone, so a round
    cut at the budget raises it whether or not the stream carried usage: a generation lookup here
    would replace it with ModelStreamInterrupted and cost the round its salvage and its feedback."""
    requests: list[httpx.Request] = []

    def generation(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    create = ScriptedCreate([_chunk(content="cut"), _chunk(finish="length")])
    client = replace(
        _client(create),
        key=OPENROUTER_KEY,
        generation_transport=httpx.MockTransport(generation),
    )
    events = []

    with pytest.raises(ModelResponseTruncated):
        async for event in client.complete(REQUEST):
            events.append(event)

    assert events == [ModelStreamStart(), TextDelta(text="cut")]
    assert requests == []


async def test_registry_rejects_a_non_ascii_openrouter_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV, "—")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )
    registry = model_registry(config, (openrouter.manifest(),))
    with ws(uuid4()):
        with pytest.raises(
            CredentialValueInvalid,
            match=(
                r"model 'google/gemini-2\.5-pro' key contains non-ASCII characters: "
                rf"env UFO_{openrouter.OPENROUTER_API_KEY_ENV} "
                rf"\(or {openrouter.OPENROUTER_API_KEY_ENV}\) or the workspace's "
                rf"{openrouter.OPENROUTER_KEY_SLOT!r} BYOK slot holds a value "
                r"the provider wire cannot carry\."
            ),
        ):
            await registry.client_for("google/gemini-2.5-pro")


@dataclass
class _ImageApi:
    """Answers `POST /api/v1/images` with canned Image API JSON and records every request."""

    images: list[dict[str, object]] = field(
        default_factory=lambda: [{"b64_json": PNG, "media_type": "image/png"}]
    )
    usage: dict[str, object] | None = field(default_factory=lambda: {"cost": 0.08})
    status: int = 200
    error_body: dict[str, object] | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path != "/api/v1/images":
            raise AssertionError(f"unscripted request: {request.method} {request.url}")
        if self.error_body is not None:
            return httpx.Response(self.status, json=self.error_body)
        body: dict[str, object] = {"created": 1748372400, "data": self.images}
        if self.usage is not None:
            body["usage"] = self.usage
        return httpx.Response(self.status, json=body)

    def sent(self) -> dict[str, object]:
        (request,) = self.requests
        return json.loads(request.content)


@dataclass
class _Sandbox:
    """Captures the bytes the image tool writes into the workspace."""

    writes: dict[str, bytes] = field(default_factory=dict)

    async def write_file(self, path: str, content: bytes) -> None:
        self.writes[path] = content


async def _keyed_turn(stored: bool = False) -> tuple[UUID, UUID]:
    """One running turn to meter against, keyed either way: `stored` puts the workspace's own
    OpenRouter key in the credential store (BYOK — OpenRouter bills the workspace directly), and the
    default leaves the platform's `OPENROUTER_API_KEY` env to serve it."""
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="draw me a poster",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    if stored:
        await store.put(workspace_id, openrouter.OPENROUTER_KEY_SLOT, OPENROUTER_KEY)
    return workspace_id, turn_id


def _context(workspace_id: UUID, turn_id: UUID, sandbox: _Sandbox, tmp_path: Path) -> ToolContext:
    return ToolContext(
        sandbox=sandbox,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="draw me a poster",
            created_at=datetime(2026, 8, 6, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        idempotency_key="turn/generate_image/call-1",
        ext=context_for(openrouter.NAME, frozenset({openrouter.OPENROUTER_KEY_SLOT})),
    )


def _wire(monkeypatch: pytest.MonkeyPatch, api: _ImageApi) -> None:
    monkeypatch.setattr(openrouter, "IMAGE_TRANSPORT", httpx.MockTransport(api.handle))
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV, OPENROUTER_KEY)


async def _images_ledger(turn_id: UUID) -> tuple[int, int, str, str | None] | None:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                    tables.ledger.c.price_digest,
                ).where(
                    (tables.ledger.c.turn_id == turn_id)
                    & (tables.ledger.c.dimension == IMAGES_DIMENSION)
                )
            )
        ).one_or_none()
    if row is None:
        return None
    return int(row.amount), int(row.priced_micro_usd), row.model, row.price_digest


async def _generate(
    workspace_id: UUID, turn_id: UUID, sandbox: _Sandbox, tmp_path: Path, **overrides: object
):
    args = GenerateImageInput(
        **{
            "prompt": "a red panda astronaut, studio lighting",
            "file_name": "poster",
            **overrides,
        }
    )
    with ws(workspace_id):
        return await openrouter.GENERATE_IMAGE_TOOL.handler(
            _context(workspace_id, turn_id, sandbox, tmp_path), args
        )


def test_the_image_allowlist_and_its_limits_name_the_same_models() -> None:
    """The wire schema's model enum and the limits table are one allowlist: a model the agent can
    ask for that has no entry would price a cost-less response at a KeyError."""
    assert set(get_args(openrouter.ImageModel)) == set(openrouter.IMAGE_MODELS)
    assert openrouter.DEFAULT_IMAGE_MODEL == "bytedance-seed/seedream-4.5"
    assert "openai/gpt-image-2" in openrouter.IMAGE_MODELS


def test_every_advertised_aspect_ratio_is_served_by_the_default_model() -> None:
    """The field offers one ratio enum across every model, so the default must serve all of it; a
    model serving less narrows the call in `_within_model_limits`, never the schema."""
    limits = openrouter.IMAGE_MODELS[openrouter.DEFAULT_IMAGE_MODEL]
    assert set(get_args(openrouter.AspectRatio)) == limits.aspect_ratios
    assert openrouter.IMAGE_MODELS["recraft/recraft-v4.1"].aspect_ratios < limits.aspect_ratios


def test_the_payload_is_bounded_at_the_tool_boundary() -> None:
    common = {"prompt": "p", "file_name": "poster"}
    with pytest.raises(ValidationError):
        GenerateImageInput(**common, n=openrouter.MAX_IMAGES_PER_CALL + 1)
    with pytest.raises(ValidationError):
        GenerateImageInput(**{**common, "prompt": "x" * (openrouter.MAX_IMAGE_PROMPT_CHARS + 1)})
    with pytest.raises(ValidationError):
        GenerateImageInput(**{**common, "file_name": "../escape"})
    with pytest.raises(ValidationError):
        GenerateImageInput(**common, model="stability/whatever")


def test_generation_inputs_refuse_an_extra_key() -> None:
    common = {"prompt": "p", "file_name": "poster"}
    for model in (GenerateImageInput, GenerateVideoInput):
        model.model_validate(common)
        with pytest.raises(ValidationError, match="unexpected_key"):
            model.model_validate({**common, "unexpected_key": "x"})


def test_a_model_that_sizes_its_own_output_is_sent_no_tier() -> None:
    """Only seedream takes a resolution, so the default is never applied to the others and naming
    one for them is refused rather than sent as a parameter their providers do not serve."""
    common = {"prompt": "p", "file_name": "poster"}
    for model in ("openai/gpt-image-2", "black-forest-labs/flux.2-pro", "recraft/recraft-v4.1"):
        assert GenerateImageInput(**common, model=model).resolution is None
        with pytest.raises(ValidationError, match="takes no resolution tier"):
            GenerateImageInput(**common, model=model, resolution="2K")


def test_a_model_that_draws_one_image_refuses_a_batch() -> None:
    """The flux.2 models are `n: 1-1` upstream, so a batch the schema's own cap allows is refused
    here rather than spent on a 400 mid-turn."""
    common = {"prompt": "p", "file_name": "poster"}
    for model in ("black-forest-labs/flux.2-pro", "black-forest-labs/flux.2-klein-4b"):
        assert GenerateImageInput(**common, model=model, n=1).n == 1
        with pytest.raises(ValidationError, match="at most 1 image"):
            GenerateImageInput(**common, model=model, n=2)
    assert GenerateImageInput(**common, model="bytedance-seed/seedream-4.5", n=2).n == 2


def test_an_aspect_ratio_the_chosen_model_does_not_serve_is_refused() -> None:
    """recraft serves five of the eight ratios the field offers; the other three are a 400 from
    OpenRouter, so they are caught where the model can read why and pick one it serves."""
    common = {"prompt": "p", "file_name": "poster"}
    for ratio in ("3:2", "2:3", "21:9"):
        with pytest.raises(ValidationError, match="does not take aspect_ratio"):
            GenerateImageInput(**common, model="recraft/recraft-v4.1", aspect_ratio=ratio)
        assert (
            GenerateImageInput(
                **common, model="bytedance-seed/seedream-4.5", aspect_ratio=ratio
            ).aspect_ratio
            == ratio
        )
    assert (
        GenerateImageInput(**common, model="recraft/recraft-v4.1", aspect_ratio="16:9").aspect_ratio
        == "16:9"
    )


async def test_generate_image_posts_the_bounded_request_and_saves_every_image(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _ImageApi(
        images=[
            {"b64_json": PNG, "media_type": "image/png"},
            {"b64_json": SECOND_PNG, "media_type": "image/webp"},
        ]
    )
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _generate(
        workspace_id,
        turn_id,
        sandbox,
        tmp_path,
        n=2,
        resolution="2K",
        aspect_ratio="16:9",
        quality="high",
    )

    (request,) = api.requests
    assert request.headers["authorization"] == f"Bearer {OPENROUTER_KEY}"
    assert api.sent() == {
        "model": "bytedance-seed/seedream-4.5",
        "prompt": "a red panda astronaut, studio lighting",
        "n": 2,
        "resolution": "2K",
        "aspect_ratio": "16:9",
        "quality": "high",
    }
    assert sandbox.writes == {
        "generated-images/poster-1.png": base64.b64decode(PNG),
        "generated-images/poster-2.webp": base64.b64decode(SECOND_PNG),
    }
    assert json.loads(result.content[0].text) == {
        "model": "bytedance-seed/seedream-4.5",
        "files": ["generated-images/poster-1.png", "generated-images/poster-2.webp"],
        "machine_generated": True,
        "cost_micro_usd": 80_000,
    }
    assert [(block.type, block.media_type) for block in result.content[1:]] == [
        ("image", "image/png"),
        ("image", "image/webp"),
    ]
    assert not result.is_error


async def test_a_response_without_a_cost_meters_the_models_list_rate(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _ImageApi(
        images=[{"b64_json": PNG}, {"b64_json": SECOND_PNG}],
        usage={"prompt_tokens": 0, "completion_tokens": 4175},
    )
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _generate(workspace_id, turn_id, sandbox, tmp_path, n=2)
    list_rate = openrouter.IMAGE_MODELS["bytedance-seed/seedream-4.5"].list_micro_usd
    assert await _images_ledger(turn_id) == (
        2,
        2 * list_rate,
        "bytedance-seed/seedream-4.5",
        None,
    )
    assert sorted(sandbox.writes) == [
        "generated-images/poster-1.png",
        "generated-images/poster-2.png",
    ]
    assert [block.media_type for block in result.content[1:]] == ["image/png", "image/png"]


async def test_a_default_seedream_call_puts_the_cheap_tier_on_the_wire(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settled default is a real request field, not merely a validated value: a call naming no
    tier reaches OpenRouter asking for 2K."""
    api = _ImageApi()
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path)
    assert api.sent() == {
        "model": "bytedance-seed/seedream-4.5",
        "prompt": "a red panda astronaut, studio lighting",
        "n": 1,
        "resolution": "2K",
    }


async def test_a_workspace_on_its_own_key_is_not_metered_for_its_own_spend(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter bills a stored `openrouter_api_key` directly, and an `images` export carries no
    byok label to hold the consumer off, so metering that call would charge the workspace a second
    time for spend it has already paid. The images are still generated and saved."""
    api = _ImageApi(usage={"prompt_tokens": 0, "completion_tokens": 4175, "cost": 0.1234})
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn(stored=True)
    sandbox = _Sandbox()
    result = await _generate(workspace_id, turn_id, sandbox, tmp_path)

    (request,) = api.requests
    assert request.headers["authorization"] == f"Bearer {OPENROUTER_KEY}"
    assert not result.is_error
    assert sorted(sandbox.writes) == ["generated-images/poster-1.png"]
    assert await _images_ledger(turn_id) is None


async def test_a_provider_refusal_returns_its_message_and_bills_nothing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generation OpenRouter refused is not billed by OpenRouter, so it is not metered here, and
    the refusal reaches the model as tool-result text rather than a raised failure."""
    api = _ImageApi(
        status=400, error_body={"error": {"message": "prompt rejected by the safety system"}}
    )
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _generate(workspace_id, turn_id, sandbox, tmp_path)
    assert result.is_error
    assert "prompt rejected by the safety system" in result.content[0].text
    assert "bytedance-seed/seedream-4.5" in result.content[0].text
    assert sandbox.writes == {}
    assert await _images_ledger(turn_id) is None


async def test_an_image_over_the_byte_cap_is_never_written(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, _ImageApi())
    monkeypatch.setattr(openrouter, "MAX_IMAGE_BYTES", 4)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    with pytest.raises(openrouter.OpenRouterImageError, match="saves at most 4"):
        await _generate(workspace_id, turn_id, sandbox, tmp_path)
    assert sandbox.writes == {}
    assert await _images_ledger(turn_id) is None


async def test_one_oversized_image_leaves_none_of_the_generation_on_disk(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap is met on every image before any of them lands, so a batch whose second image is
    too large writes nothing rather than half a generation the member would have to sort out."""
    oversized = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 64).decode()
    _wire(
        monkeypatch,
        _ImageApi(images=[{"b64_json": PNG}, {"b64_json": oversized}]),
    )
    monkeypatch.setattr(openrouter, "MAX_IMAGE_BYTES", 32)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    with pytest.raises(openrouter.OpenRouterImageError, match="saves at most 32"):
        await _generate(workspace_id, turn_id, sandbox, tmp_path, n=2)
    assert sandbox.writes == {}
    assert await _images_ledger(turn_id) is None


async def test_a_success_carrying_no_image_fails_loud(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, _ImageApi(images=[]))
    workspace_id, turn_id = await _keyed_turn()
    with pytest.raises(openrouter.OpenRouterImageError, match="no image data"):
        await _generate(workspace_id, turn_id, _Sandbox(), tmp_path)
    assert await _images_ledger(turn_id) is None


@dataclass
class _VideoApi:
    """Plays OpenRouter's asynchronous Video API: the POST accepts a job, each poll answers the next
    scripted status (the last one repeating), and the content route serves the MP4 bytes."""

    statuses: list[dict[str, object]] = field(
        default_factory=lambda: [
            {"status": "in_progress"},
            {"status": "completed", "usage": {"cost": 0.65, "is_byok": False}},
        ]
    )
    video: bytes = MP4
    accept_status: int = 202
    error_body: dict[str, object] | None = None
    content_status: int = 200
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/videos":
            if self.error_body is not None:
                return httpx.Response(self.accept_status, json=self.error_body)
            return httpx.Response(
                self.accept_status,
                json={
                    "id": VIDEO_JOB,
                    "status": "pending",
                    "polling_url": f"https://openrouter.ai/api/v1/videos/{VIDEO_JOB}",
                },
            )
        if path == f"/api/v1/videos/{VIDEO_JOB}/content":
            return httpx.Response(self.content_status, content=self.video)
        if path == f"/api/v1/videos/{VIDEO_JOB}":
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"id": VIDEO_JOB, **status})
        raise AssertionError(f"unscripted request: {request.method} {request.url}")

    def sent(self) -> dict[str, object]:
        return json.loads(self.requests[0].content)

    def polls(self) -> int:
        return len([r for r in self.requests if r.url.path == f"/api/v1/videos/{VIDEO_JOB}"])


def _wire_video(monkeypatch: pytest.MonkeyPatch, api: _VideoApi) -> None:
    monkeypatch.setattr(openrouter, "VIDEO_TRANSPORT", httpx.MockTransport(api.handle))
    monkeypatch.setattr(openrouter, "VIDEO_POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV, OPENROUTER_KEY)


async def _videos_ledger(turn_id: UUID) -> tuple[int, int, str, str | None] | None:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                    tables.ledger.c.price_digest,
                ).where(
                    (tables.ledger.c.turn_id == turn_id)
                    & (tables.ledger.c.dimension == VIDEOS_DIMENSION)
                )
            )
        ).one_or_none()
    if row is None:
        return None
    return int(row.amount), int(row.priced_micro_usd), row.model, row.price_digest


async def _film(
    workspace_id: UUID, turn_id: UUID, sandbox: _Sandbox, tmp_path: Path, **overrides: object
):
    args = GenerateVideoInput(
        **{
            "prompt": "a red panda astronaut drifting down a station corridor",
            "file_name": "teaser",
            **overrides,
        }
    )
    with ws(workspace_id):
        return await openrouter.GENERATE_VIDEO_TOOL.handler(
            _context(workspace_id, turn_id, sandbox, tmp_path), args
        )


def test_the_video_allowlist_and_its_limits_name_the_same_models() -> None:
    """The wire schema's model enum and the limits table are one allowlist: a model the agent can
    ask for that has no entry would price a cost-less job at a KeyError. Every model the tool offers
    is named in the tool's own description, since that is where the agent reads what to pick."""
    assert set(get_args(openrouter.VideoModel)) == set(openrouter.VIDEO_MODELS)
    assert openrouter.DEFAULT_VIDEO_MODEL == "minimax/hailuo-3"
    assert set(openrouter.VIDEO_MODELS) == {"minimax/hailuo-3", "bytedance/seedance-2.5"}
    for model in openrouter.VIDEO_MODELS:
        assert model in openrouter.GENERATE_VIDEO_DESCRIPTION


def test_the_video_field_bounds_span_every_model_the_tool_offers() -> None:
    """OpenRouter's video model listing gives H3 durations 5-15s and Seedance 4-30s, and both take
    the same six aspect ratios; the field offers one range and one ratio enum across every model, so
    the range is the widest any of them films and each model narrows it at the boundary."""
    common = {"prompt": "p", "file_name": "teaser"}
    ratios, _none = get_args(GenerateVideoInput.model_fields["aspect_ratio"].annotation)
    assert set(get_args(ratios)) == {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
    for limits in openrouter.VIDEO_MODELS.values():
        assert limits.aspect_ratios == set(get_args(ratios))
    assert (openrouter.MIN_VIDEO_SECONDS, openrouter.MAX_VIDEO_SECONDS) == (
        min(limits.min_seconds for limits in openrouter.VIDEO_MODELS.values()),
        max(limits.max_seconds for limits in openrouter.VIDEO_MODELS.values()),
    )
    assert GenerateVideoInput(**common).duration == openrouter.DEFAULT_VIDEO_SECONDS
    for refused in (openrouter.MIN_VIDEO_SECONDS - 1, openrouter.MAX_VIDEO_SECONDS + 1):
        with pytest.raises(ValidationError):
            GenerateVideoInput(**common, duration=refused)
    with pytest.raises(ValidationError):
        GenerateVideoInput(**common, aspect_ratio="5:4")


def test_each_video_model_films_only_its_own_durations() -> None:
    """H3's 5-15s and Seedance's 4-30s are one field, so a take the field allows and the chosen
    model does not is refused here, where the model reads why and can ask again, rather than
    spending minutes of generation on a 400."""
    common = {"prompt": "p", "file_name": "teaser"}
    for model, (low, high) in (
        ("minimax/hailuo-3", (5, 15)),
        ("bytedance/seedance-2.5", (4, 30)),
    ):
        for seconds in range(low, high + 1):
            assert GenerateVideoInput(**common, model=model, duration=seconds).duration == seconds
        for refused in (low - 1, high + 1):
            if not openrouter.MIN_VIDEO_SECONDS <= refused <= openrouter.MAX_VIDEO_SECONDS:
                continue
            with pytest.raises(ValidationError, match=f"films between {low} and {high} seconds"):
                GenerateVideoInput(**common, model=model, duration=refused)


def test_a_video_model_serving_less_than_the_field_offers_is_narrowed_at_the_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model whose provider serves a shorter take or fewer ratios than the field offers is held to
    its own `VIDEO_MODELS` row, where the model reads why and can ask again, rather than spending
    minutes of generation on a 400."""
    common = {"prompt": "p", "file_name": "teaser"}
    monkeypatch.setitem(
        openrouter.VIDEO_MODELS,
        openrouter.DEFAULT_VIDEO_MODEL,
        openrouter.VideoModelLimits(
            min_seconds=6,
            max_seconds=10,
            aspect_ratios=frozenset({"16:9", "9:16"}),
            default_resolution="2K",
            second_micro_usd={"2K": 130_000},
        ),
    )
    assert GenerateVideoInput(**common, duration=10, aspect_ratio="16:9").duration == 10
    for refused in (5, 15):
        with pytest.raises(ValidationError, match="films between 6 and 10 seconds"):
            GenerateVideoInput(**common, duration=refused)
    with pytest.raises(ValidationError, match="does not take aspect_ratio"):
        GenerateVideoInput(**common, duration=6, aspect_ratio="4:3")


def test_each_video_model_films_only_the_tiers_it_serves() -> None:
    """H3 films 2K and nothing else, Seedance films 480p or 720p and has no 2K tier at all, so a
    tier valid for one model is a 400 for the other and is refused where the model can pick again. A
    call naming no tier settles on the model's own default rather than the provider's."""
    common = {"prompt": "p", "file_name": "teaser"}
    tiers, _none = get_args(GenerateVideoInput.model_fields["resolution"].annotation)
    assert set(get_args(tiers)) == {"480p", "720p", "2K"}
    assert GenerateVideoInput(**common).resolution == "2K"
    assert GenerateVideoInput(**common, model="bytedance/seedance-2.5").resolution == "720p"
    for tier in ("480p", "720p"):
        assert (
            GenerateVideoInput(**common, model="bytedance/seedance-2.5", resolution=tier).resolution
            == tier
        )
        with pytest.raises(ValidationError, match="does not film at"):
            GenerateVideoInput(**common, resolution=tier)
    with pytest.raises(ValidationError, match="does not film at 2K; it films 480p, 720p"):
        GenerateVideoInput(**common, model="bytedance/seedance-2.5", resolution="2K")


def test_a_per_second_rate_is_a_rate_at_one_frame_size() -> None:
    """H3 bills a flat $0.13 per output second; Seedance bills $0.0000107 per video token, tokens
    being `width * height * seconds * 24 / 1024`, so its per-second rate rises with the frame and
    one constant cannot price both of its tiers. Each tier's rate is taken at the largest frame it
    serves — 992x432 and 1112x834 — so a list charge never trails what OpenRouter bills, whatever
    aspect ratio the provider frames inside the tier."""
    assert openrouter.VIDEO_MODELS["minimax/hailuo-3"].second_micro_usd == {"2K": 130_000}
    rates = openrouter.VIDEO_MODELS["bytedance/seedance-2.5"].second_micro_usd
    assert rates == {
        "480p": round(992 * 432 * 24 / 1024 * 0.0000107 * 1_000_000),
        "720p": round(1112 * 834 * 24 / 1024 * 0.0000107 * 1_000_000),
    }
    assert rates["720p"] > openrouter.VIDEO_MODELS["minimax/hailuo-3"].second_micro_usd["2K"]
    for model, limits in openrouter.VIDEO_MODELS.items():
        assert limits.default_resolution in limits.second_micro_usd, model


def test_the_video_payload_is_bounded_at_the_tool_boundary() -> None:
    common = {"prompt": "p", "file_name": "teaser"}
    with pytest.raises(ValidationError):
        GenerateVideoInput(**{**common, "prompt": "x" * (openrouter.MAX_VIDEO_PROMPT_CHARS + 1)})
    with pytest.raises(ValidationError):
        GenerateVideoInput(**{**common, "file_name": "../escape"})
    with pytest.raises(ValidationError):
        GenerateVideoInput(**common, model="minimax/hailuo-2.3")


async def test_a_workspace_on_its_own_key_is_not_metered_for_its_own_video(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter bills a stored `openrouter_api_key` directly and a `videos` export carries no byok
    label to hold the consumer off, so metering it would charge the workspace twice. The video is
    still generated and saved."""
    _wire_video(monkeypatch, _VideoApi())
    workspace_id, turn_id = await _keyed_turn(stored=True)
    sandbox = _Sandbox()
    result = await _film(workspace_id, turn_id, sandbox, tmp_path)
    assert not result.is_error
    assert sorted(sandbox.writes) == ["generated-videos/teaser.mp4"]
    assert await _videos_ledger(turn_id) is None


async def test_a_rejected_video_request_returns_the_providers_message_and_bills_nothing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _VideoApi(
        accept_status=400, error_body={"error": {"message": "prompt rejected by the safety system"}}
    )
    _wire_video(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _film(workspace_id, turn_id, sandbox, tmp_path)
    assert result.is_error
    assert "prompt rejected by the safety system" in result.content[0].text
    assert "minimax/hailuo-3" in result.content[0].text
    assert api.polls() == 0
    assert sandbox.writes == {}
    assert await _videos_ledger(turn_id) is None


async def test_a_job_that_fails_after_acceptance_reports_its_reason_and_bills_nothing(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generation OpenRouter accepted and then failed is the provider's refusal, so it reaches the
    model as tool-result text it can act on rather than a raised failure, and nothing is downloaded
    or metered."""
    api = _VideoApi(
        statuses=[{"status": "failed", "error": {"message": "sensitive content in frame 2"}}]
    )
    _wire_video(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _film(workspace_id, turn_id, sandbox, tmp_path)
    assert result.is_error
    assert "sensitive content in frame 2" in result.content[0].text
    assert sandbox.writes == {}
    assert await _videos_ledger(turn_id) is None


async def test_a_generation_that_never_settles_gives_up_at_the_bound(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tool holds a turn open only for its own bound: a job left pending past
    `VIDEO_POLL_TIMEOUT_SECONDS` fails loud instead of polling forever."""
    _wire_video(monkeypatch, _VideoApi(statuses=[{"status": "pending"}]))
    monkeypatch.setattr(openrouter, "VIDEO_POLL_TIMEOUT_SECONDS", 0.0)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    with pytest.raises(openrouter.OpenRouterVideoError, match="left vid-01JB7 pending"):
        await _film(workspace_id, turn_id, sandbox, tmp_path)
    assert sandbox.writes == {}
    assert await _videos_ledger(turn_id) is None


async def test_a_video_over_the_byte_cap_is_never_written(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_video(monkeypatch, _VideoApi())
    monkeypatch.setattr(openrouter, "MAX_VIDEO_BYTES", 8)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    with pytest.raises(openrouter.OpenRouterVideoError, match="saves at most 8"):
        await _film(workspace_id, turn_id, sandbox, tmp_path)
    assert sandbox.writes == {}
    assert await _videos_ledger(turn_id) is None


async def test_a_completed_job_carrying_no_content_fails_loud(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_video(monkeypatch, _VideoApi(video=b""))
    workspace_id, turn_id = await _keyed_turn()
    with pytest.raises(openrouter.OpenRouterVideoError, match="no video data"):
        await _film(workspace_id, turn_id, _Sandbox(), tmp_path)
    assert await _videos_ledger(turn_id) is None


async def test_a_silent_take_asks_the_provider_to_drop_the_audio_track(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _VideoApi()
    _wire_video(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    await _film(workspace_id, turn_id, _Sandbox(), tmp_path, generate_audio=False)
    assert api.sent() == {
        "model": "minimax/hailuo-3",
        "prompt": "a red panda astronaut drifting down a station corridor",
        "duration": 5,
        "resolution": "2K",
        "generate_audio": False,
    }
