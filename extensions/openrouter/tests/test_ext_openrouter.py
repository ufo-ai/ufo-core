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

from ufo.access.credentials import CredentialStore
from ufo.billing.accounting import IMAGES_DIMENSION, VIDEOS_DIMENSION
from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.models.interface import (
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
from ufo.models.pricing import ModelPrice
from ufo.models.registry import model_registry
from ufo.schema import tables
from ufo.schema.records import Agent, Turn, Usage
from ufo.sdk.audience import conversation_audience
from ufo.sdk.credentials import CredentialValueInvalid
from ufo.tools.context import ToolContext
from ufo.workspace import init_workspace_credentials, ws

OPENROUTER_KEY = "sk-or-v1-secret-0xfeedface"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n-one").decode()
SECOND_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n-two").decode()
MP4 = b"\x00\x00\x00\x18ftypmp42-frames-and-stereo-audio"
VIDEO_JOB = "vid-01JB7"

REQUEST = ModelRequest(
    model="google/gemini-2.5-pro",
    system="be terse",
    messages=(Message(role="user", content="hi"),),
    max_tokens=64,
    conversation_cache_ttl="5m",
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


def _client(
    create: ScriptedCreate,
    spec: openrouter.ModelSpec = openrouter.OPENROUTER_MODEL_SPECS[0],
) -> openrouter.OpenRouterModelClient:
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return openrouter.OpenRouterModelClient(client=sdk, spec=spec, key=OPENROUTER_KEY)


def test_openrouter_slug_maps_bare_ids_and_passes_slugs_through() -> None:
    assert openrouter.openrouter_slug("google/gemini-2.5-pro") == "google/gemini-2.5-pro"
    assert openrouter.openrouter_slug("gpt-5.4") == "openai/gpt-5.4"
    assert openrouter.openrouter_slug("claude-opus-4-8") == "anthropic/claude-opus-4-8"
    assert openrouter.openrouter_slug("grok-2") == "grok-2"


async def test_complete_streams_text_then_usage_without_an_auto_reasoning_budget() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(3, 2))]
    )
    events = [event async for event in _client(create).complete(REQUEST)]
    assert events[:2] == [ModelStreamStart(), TextDelta(text="ok")]
    assert events[-1] == Usage(input_tokens=3, output_tokens=2)
    kwargs = create.calls[0]
    assert kwargs["model"] == "google/gemini-2.5-pro"
    assert kwargs["extra_body"] == {}
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


async def test_second_gemini_flash_abort_reraises() -> None:
    second = _api_error()
    create = ScriptedCreate(_api_error(), second)

    with pytest.raises(openai.APIError) as raised:
        async for _ in _client(create).complete(GEMINI_FLASH_REQUEST):
            pass

    assert raised.value is second
    assert len(create.calls) == 2


async def test_gemini_flash_abort_after_text_does_not_retry() -> None:
    error = _api_error()
    create = ScriptedCreate(
        [_chunk(content="partial"), error],
        [_chunk(content="wrong"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    events = []

    with pytest.raises(openai.APIError) as raised:
        async for event in _client(create).complete(GEMINI_FLASH_REQUEST):
            events.append(event)

    assert raised.value is error
    assert events == [ModelStreamStart(), TextDelta(text="partial")]
    assert len(create.calls) == 1


async def test_gemini_flash_abort_after_tool_call_start_does_not_retry() -> None:
    error = _api_error()
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
            error,
        ],
        [_chunk(content="wrong"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))],
    )
    events = []

    with pytest.raises(openai.APIError) as raised:
        async for event in _client(create).complete(GEMINI_FLASH_REQUEST):
            events.append(event)

    assert raised.value is error
    assert events == [ModelStreamStart(), openrouter.ToolCallStart(id="call-1", name="inspect")]
    assert len(create.calls) == 1


@pytest.mark.parametrize(
    ("model_request", "error"),
    [
        pytest.param(REQUEST, _api_error(), id="other-model"),
        pytest.param(
            GEMINI_FLASH_REQUEST, _api_error("The operation was aborted."), id="near-message"
        ),
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
async def test_abort_retry_excludes_other_failures(
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


async def test_priced_cache_writes_are_a_disjoint_usage_class() -> None:
    create = ScriptedCreate(
        [
            _chunk(content="ok"),
            _chunk(finish="stop"),
            _chunk(usage=_usage(10, 2, cached=4, cache_write=3)),
        ]
    )
    client = _client(create)
    priced = replace(
        client.spec,
        price=ModelPrice(0, 0, 0, 0, 0, cache_write_30m=1),
    )
    events = [event async for event in replace(client, spec=priced).complete(REQUEST)]
    assert events[-1] == Usage(
        input_tokens=3,
        output_tokens=2,
        cache_read_tokens=4,
        cache_write_30m_tokens=3,
    )


async def test_reasoning_effort_rides_from_the_request() -> None:
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))]
    )
    async for _ in _client(create).complete(REQUEST.model_copy(update={"reasoning": "low"})):
        pass
    assert create.calls[0]["extra_body"] == {"reasoning": {"effort": "low"}}


async def test_reasoning_off_disables_the_reasoning_budget() -> None:
    """An omitted budget leaves the upstream model reasoning at its own default effort, and
    max_tokens is reasoning-inclusive, so `off` says so on the wire."""
    create = ScriptedCreate(
        [_chunk(content="ok"), _chunk(finish="stop"), _chunk(usage=_usage(1, 1))]
    )
    async for _ in _client(create).complete(REQUEST.model_copy(update={"reasoning": "off"})):
        pass
    assert create.calls[0]["extra_body"] == {"reasoning": {"enabled": False}}


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
    assert create.calls[0]["extra_body"] == {}


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
    assert create.calls[0]["extra_body"] == {}


def test_google_tool_result_with_json_reference_is_text_enveloped() -> None:
    result = json.dumps(
        {
            "$defs": {"Visibility": {"type": "string"}},
            "properties": {"visibility": {"$ref": "#/$defs/Visibility"}},
        }
    )
    request = REQUEST.model_copy(
        update={
            "model": "google/gemini-3.7-flash",
            "messages": (
                Message(role="user", content="inspect"),
                Message(
                    role="assistant",
                    content=(ToolUseBlock(id="c1", name="inspect", input={}),),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="c1", content=result),),
                ),
            ),
        }
    )
    kwargs = _client(ScriptedCreate([]))._create_kwargs(request, frozenset())
    messages = kwargs["messages"]
    assert isinstance(messages, list)
    assert json.loads(messages[-1]["content"]) == {"text": result}


def test_deep_google_tool_results_do_not_recurse() -> None:
    result = "[" * 600 + '{"$ref":"#/$defs/Value"}' + "]" * 600
    request = REQUEST.model_copy(
        update={
            "model": "google/gemini-3.7-flash",
            "messages": (
                Message(role="user", content="inspect"),
                Message(
                    role="assistant",
                    content=(ToolUseBlock(id="c1", name="inspect", input={}),),
                ),
                Message(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="c1", content=result),),
                ),
            ),
        }
    )

    messages = _client(ScriptedCreate([]))._create_kwargs(request, frozenset())["messages"]

    assert isinstance(messages, list)
    assert json.loads(messages[-1]["content"])["text"] == result


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


async def test_dead_provider_completion_reroutes_excluding_that_provider() -> None:
    dead = [_chunk(finish="stop", provider="deadco"), _chunk(usage=_usage(1, 0))]
    good = [_chunk(content="recovered"), _chunk(finish="stop"), _chunk(usage=_usage(2, 3))]
    create = ScriptedCreate(dead, good)
    events = [event async for event in _client(create).complete(REQUEST)]
    assert len(create.calls) == 2
    assert [event for event in events if isinstance(event, TextDelta)] == [
        TextDelta(text="recovered")
    ]
    assert [event for event in events if isinstance(event, Usage)] == [
        Usage(input_tokens=1, output_tokens=0),
        Usage(input_tokens=2, output_tokens=3),
    ]
    assert create.calls[1]["extra_body"]["provider"] == {"ignore": ["deadco"]}


async def test_length_finish_raises_truncated() -> None:
    create = ScriptedCreate(
        [_chunk(content="cut"), _chunk(finish="length"), _chunk(usage=_usage(1, 9))]
    )
    events = []
    with pytest.raises(ModelResponseTruncated):
        async for event in _client(create).complete(REQUEST):
            events.append(event)
    assert events[-1] == Usage(input_tokens=1, output_tokens=9)


def test_manifest_registers_slug_pinned_specs() -> None:
    manifest = openrouter.manifest()
    by_id = {spec.id: spec for spec in manifest.models}
    assert set(by_id) == {
        "google/gemini-3.7-flash",
        "google/gemini-2.5-pro",
        "z-ai/glm-5.2",
        "z-ai/glm-5.3",
        "z-ai/glm-5.3-flash",
        "moonshotai/kimi-k3",
        "anthropic/claude-fable-5",
        "openai/gpt-5.6-sol",
    }
    assert by_id["z-ai/glm-5.2"].price.output == 3_000_000
    assert by_id["z-ai/glm-5.2"].knowledge_cutoff == "2026-03"


def test_glm_53_spec_carries_its_route_price_window_and_required_reasoning() -> None:
    spec = {s.id: s for s in openrouter.manifest().models}["z-ai/glm-5.3"]
    assert openrouter.openrouter_slug(spec.id) == "z-ai/glm-5.3"
    assert spec.price.input == 1_400_000
    assert spec.price.output == 4_400_000
    assert spec.price.cache_read == 260_000
    assert spec.context_window == 1_048_576
    assert spec.reasoning.default_on
    assert not spec.reasoning.can_disable
    assert not spec.accepts_image_input
    assert spec.wire_reasoning("off", ()) == "low"
    assert spec.wire_reasoning("high", ()) == "high"


def test_gemini_37_flash_spec_carries_its_route_price_window_and_reasoning() -> None:
    spec = {s.id: s for s in openrouter.manifest().models}["google/gemini-3.7-flash"]
    assert openrouter.openrouter_slug(spec.id) == "google/gemini-3.7-flash"
    assert spec.price.input == 375_000
    assert spec.price.output == 1_875_000
    assert spec.price.cache_read == 37_500
    assert spec.context_window == 1_048_576
    assert spec.knowledge_cutoff == "2026-03"
    assert spec.reasoning.default_on
    assert not spec.reasoning.can_disable
    assert spec.accepts_image_input
    assert spec.wire_reasoning("off", ()) == "low"
    assert spec.wire_reasoning("high", ()) == "high"


def test_glm_53_flash_spec_carries_its_undiscounted_price_window_and_required_reasoning() -> None:
    """The rates are the ones a route bills without the 0.5 promotional discount the listing shows,
    which no route is held to, and the window is the 1,048,576 tokens every route but Cloudflare's
    serves. The model reasons on every call, so a row that let an agent write `off` would send a
    budget it refuses. Unlike `z-ai/glm-5.3`, this route takes image input."""
    spec = {s.id: s for s in openrouter.manifest().models}["z-ai/glm-5.3-flash"]
    assert openrouter.openrouter_slug(spec.id) == "z-ai/glm-5.3-flash"
    assert spec.price.input == 150_000
    assert spec.price.output == 500_000
    assert spec.price.cache_read == 30_000
    assert spec.context_window == 1_048_576
    assert spec.knowledge_cutoff == "2026-03"
    assert spec.reasoning.default_on
    assert not spec.reasoning.can_disable
    assert spec.accepts_image_input
    assert spec.wire_reasoning("off", ()) == "low"
    assert spec.wire_reasoning("high", ()) == "high"


def test_kimi_k3_spec_carries_its_price_cache_rate_and_million_token_window() -> None:
    spec = {s.id: s for s in openrouter.manifest().models}["moonshotai/kimi-k3"]
    assert spec.price.input == 3_000_000
    assert spec.price.output == 15_000_000
    assert spec.price.cache_read == 300_000
    assert spec.context_window == 1_000_000


def test_fable_5_route_carries_its_slug_price_window_and_required_reasoning() -> None:
    """The id is the slug OpenRouter's catalog publishes, `anthropic/claude-fable-5`; the router
    also resolves dated spellings onto it, so a wrong id here would pass a live call and hide the
    mistake. Cache writes carry no rate, so this router's one write channel leaves them priced as
    input, like every other row here. The model reasons on every call, so a row that let an agent
    write `off` would send a budget it refuses."""
    spec = {s.id: s for s in openrouter.manifest().models}["anthropic/claude-fable-5"]
    assert openrouter.openrouter_slug(spec.id) == "anthropic/claude-fable-5"
    assert spec.price.input == 10_000_000
    assert spec.price.output == 50_000_000
    assert spec.price.cache_read == 1_000_000
    assert spec.price.cache_write_30m == 0
    assert spec.context_window == 1_000_000
    assert spec.reasoning.default_on
    assert not spec.reasoning.can_disable
    assert spec.wire_reasoning("off", ()) == "low"


def test_gpt_56_sol_route_carries_its_slug_price_and_billed_window() -> None:
    """The route accepts 1,050,000 tokens and bills 2x input plus 1.5x output for a whole request
    over 272,000, which one rate per token class cannot express, so the row carries the window this
    rate is true at. The row holds the undiscounted slug rate, which equals the direct openai row:
    the listing halves it under a promotional discount the router applies to one route of seven, and
    a ledger that books the discount charges half of what the other six routes cost. Reasoning
    composes with tools here because this router sends its own normalised `reasoning` field rather
    than the `reasoning_effort` the model refuses beside tools on Chat Completions (#568)."""
    spec = {s.id: s for s in openrouter.manifest().models}["openai/gpt-5.6-sol"]
    assert openrouter.openrouter_slug(spec.id) == "openai/gpt-5.6-sol"
    assert spec.price.input == 4_000_000
    assert spec.price.output == 20_000_000
    assert spec.price.cache_read == 400_000
    assert spec.price.cache_write_30m == 5_000_000
    assert spec.context_window == 272_000
    assert spec.knowledge_cutoff == "2026-02"
    assert spec.accepts_image_input
    tools = (ToolSchema(name="search", description="Search", input_schema={"type": "object"}),)
    assert spec.wire_reasoning("high", tools) == "high"


async def test_model_client_requires_its_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv(openrouter.OPENROUTER_API_KEY_ENV, raising=False)
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )
    registry = model_registry(config, (openrouter.manifest(),))
    with ws(uuid4()), pytest.raises(RuntimeError, match=openrouter.OPENROUTER_API_KEY_ENV):
        await registry.client_for("google/gemini-2.5-pro")


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


async def test_registry_selects_openrouter_and_prices_its_slug(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV, "sk-openrouter-test")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///:memory:"),
        blob=BlobConfig(backend="filesystem", root=tmp_path),
    )
    registry = model_registry(config, (openrouter.manifest(),))
    with ws(uuid4()):
        client = await registry.client_for("google/gemini-2.5-pro")
    assert isinstance(client, openrouter.OpenRouterModelClient)
    assert client.spec is registry.spec("google/gemini-2.5-pro")
    priced = registry.pricing.micro_usd(
        "google/gemini-2.5-pro", Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    )
    assert priced == 11_000_000


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
            "name": "poster",
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


def test_manifest_publishes_the_generation_tools_and_the_key_slot() -> None:
    """The key slot is declared because a tool reads credentials only for slots its manifest names;
    the model specs resolve the same slot, so models, images and videos run on one key."""
    manifest = openrouter.manifest()
    assert [tool.name for tool in manifest.tools] == ["generate_image", "generate_video"]
    assert all(tool.side_effecting for tool in manifest.tools)
    (slot,) = manifest.credentials
    assert slot.name == openrouter.OPENROUTER_KEY_SLOT
    assert slot.injection is None
    assert {spec.key_slot for spec in manifest.models} == {openrouter.OPENROUTER_KEY_SLOT}


def test_the_payload_is_bounded_at_the_tool_boundary() -> None:
    common = {"prompt": "p", "name": "poster"}
    with pytest.raises(ValidationError):
        GenerateImageInput(**common, n=openrouter.MAX_IMAGES_PER_CALL + 1)
    with pytest.raises(ValidationError):
        GenerateImageInput(**{**common, "prompt": "x" * (openrouter.MAX_IMAGE_PROMPT_CHARS + 1)})
    with pytest.raises(ValidationError):
        GenerateImageInput(**{**common, "name": "../escape"})
    with pytest.raises(ValidationError):
        GenerateImageInput(**common, model="stability/whatever")


def test_the_offered_resolution_tiers_are_the_ones_seedream_draws() -> None:
    """Seed's parameter list names `1K`, and Seed then refuses to render it: it draws at least
    3,686,400 output pixels and 1K is 1,048,576 at every aspect ratio. What the field offers is
    what came back as an image, so `1K` is not a tier here however the parameter list reads."""
    common = {"prompt": "p", "name": "poster"}
    tiers, _none = get_args(GenerateImageInput.model_fields["resolution"].annotation)
    assert set(get_args(tiers)) == {"2K", "4K"}
    assert openrouter.IMAGE_MODELS[openrouter.DEFAULT_IMAGE_MODEL].resolutions == set(
        get_args(tiers)
    )
    for below in ("1K", "512"):
        with pytest.raises(ValidationError):
            GenerateImageInput(**common, resolution=below)


def test_an_unasked_resolution_settles_on_the_cheapest_tier_that_draws() -> None:
    """A call that names no tier draws at 2K rather than whatever the provider would pick, and 4K
    stays reachable for the member who wants it."""
    common = {"prompt": "p", "name": "poster"}
    assert GenerateImageInput(**common).resolution == openrouter.DEFAULT_RESOLUTION
    assert openrouter.DEFAULT_RESOLUTION == "2K"
    for tier in ("2K", "4K"):
        assert GenerateImageInput(**common, resolution=tier).resolution == tier


def test_a_model_that_sizes_its_own_output_is_sent_no_tier() -> None:
    """Only seedream takes a resolution, so the default is never applied to the others and naming
    one for them is refused rather than sent as a parameter their providers do not serve."""
    common = {"prompt": "p", "name": "poster"}
    for model in ("openai/gpt-image-2", "black-forest-labs/flux.2-pro", "recraft/recraft-v4.1"):
        assert GenerateImageInput(**common, model=model).resolution is None
        with pytest.raises(ValidationError, match="takes no resolution tier"):
            GenerateImageInput(**common, model=model, resolution="2K")


def test_a_model_that_draws_one_image_refuses_a_batch() -> None:
    """The flux.2 models are `n: 1-1` upstream, so a batch the schema's own cap allows is refused
    here rather than spent on a 400 mid-turn."""
    common = {"prompt": "p", "name": "poster"}
    for model in ("black-forest-labs/flux.2-pro", "black-forest-labs/flux.2-klein-4b"):
        assert GenerateImageInput(**common, model=model, n=1).n == 1
        with pytest.raises(ValidationError, match="at most 1 image"):
            GenerateImageInput(**common, model=model, n=2)
    assert GenerateImageInput(**common, model="bytedance-seed/seedream-4.5", n=2).n == 2


def test_an_aspect_ratio_the_chosen_model_does_not_serve_is_refused() -> None:
    """recraft serves five of the eight ratios the field offers; the other three are a 400 from
    OpenRouter, so they are caught where the model can read why and pick one it serves."""
    common = {"prompt": "p", "name": "poster"}
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


async def test_the_providers_reported_cost_meters_onto_the_turn(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter reports what it charged for this generation whatever unit the upstream billed in,
    so that number is the ledger's, and the row counts images rather than tokens. A default call
    also sends only the fields every allowlisted model accepts — GPT Image 2 takes no
    `resolution`."""
    api = _ImageApi(usage={"prompt_tokens": 0, "completion_tokens": 4175, "cost": 0.1234})
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path, model="openai/gpt-image-2")
    assert api.sent() == {
        "model": "openai/gpt-image-2",
        "prompt": "a red panda astronaut, studio lighting",
        "n": 1,
    }
    assert await _images_ledger(turn_id) == (1, 123_400, "openai/gpt-image-2", None)


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


async def test_a_byok_generation_meters_the_upstream_charge_it_reports(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace running its own upstream key is charged nothing by OpenRouter, which reports
    `cost` as zero and states the real spend under `cost_details`. That is the same money one hop
    further out, so it is what the turn is metered — a reported zero is not a free image."""
    api = _ImageApi(
        usage={
            "prompt_tokens": 12,
            "completion_tokens": 229,
            "cost": 0,
            "is_byok": True,
            "cost_details": {"upstream_inference_cost": 0.00693},
        }
    )
    _wire(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path, model="openai/gpt-image-2")
    assert await _images_ledger(turn_id) == (1, 6_930, "openai/gpt-image-2", None)


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


async def test_a_zero_cost_carrying_no_upstream_charge_falls_back_to_the_list_rate(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, _ImageApi(usage={"prompt_tokens": 0, "completion_tokens": 4175, "cost": 0}))
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path)
    list_rate = openrouter.IMAGE_MODELS["bytedance-seed/seedream-4.5"].list_micro_usd
    assert await _images_ledger(turn_id) == (1, list_rate, "bytedance-seed/seedream-4.5", None)


async def test_two_generations_on_one_turn_accumulate_into_one_images_row(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, _ImageApi())
    workspace_id, turn_id = await _keyed_turn()
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path)
    await _generate(workspace_id, turn_id, _Sandbox(), tmp_path, name="second")
    assert await _images_ledger(turn_id) == (2, 160_000, "bytedance-seed/seedream-4.5", None)


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
            "name": "teaser",
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
    common = {"prompt": "p", "name": "teaser"}
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
    common = {"prompt": "p", "name": "teaser"}
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
    common = {"prompt": "p", "name": "teaser"}
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
    common = {"prompt": "p", "name": "teaser"}
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
    common = {"prompt": "p", "name": "teaser"}
    with pytest.raises(ValidationError):
        GenerateVideoInput(**{**common, "prompt": "x" * (openrouter.MAX_VIDEO_PROMPT_CHARS + 1)})
    with pytest.raises(ValidationError):
        GenerateVideoInput(**{**common, "name": "../escape"})
    with pytest.raises(ValidationError):
        GenerateVideoInput(**common, model="minimax/hailuo-2.3")


async def test_generate_video_posts_the_job_polls_it_and_saves_the_download(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole asynchronous flow on one key: the accepted job is polled until it completes, the
    finished MP4 is downloaded from the job's content route and written into the workspace, and the
    provider's reported charge is what the turn is metered."""
    api = _VideoApi()
    _wire_video(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _film(workspace_id, turn_id, sandbox, tmp_path, duration=10, aspect_ratio="16:9")

    assert {request.headers["authorization"] for request in api.requests} == {
        f"Bearer {OPENROUTER_KEY}"
    }
    assert api.sent() == {
        "model": "minimax/hailuo-3",
        "prompt": "a red panda astronaut drifting down a station corridor",
        "duration": 10,
        "resolution": "2K",
        "aspect_ratio": "16:9",
        "generate_audio": True,
    }
    assert api.polls() == 2
    assert api.requests[-1].url.params["index"] == "0"
    assert sandbox.writes == {"generated-videos/teaser.mp4": MP4}
    assert json.loads(result.content[0].text) == {
        "model": "minimax/hailuo-3",
        "files": ["generated-videos/teaser.mp4"],
        "duration_seconds": 10,
        "machine_generated": True,
        "cost_micro_usd": 650_000,
    }
    assert not result.is_error
    assert await _videos_ledger(turn_id) == (1, 650_000, "minimax/hailuo-3", None)


async def test_a_job_that_prices_nothing_meters_the_list_rate_per_second(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H3 lists at $0.13 per output second at its one 2K tier, so an unpriced job is metered over
    the seconds asked for rather than at nothing."""
    _wire_video(monkeypatch, _VideoApi(statuses=[{"status": "completed"}]))
    workspace_id, turn_id = await _keyed_turn()
    await _film(workspace_id, turn_id, _Sandbox(), tmp_path, duration=15)
    assert await _videos_ledger(turn_id) == (1, 15 * 130_000, "minimax/hailuo-3", None)


async def test_seedance_films_its_own_length_and_tier_and_is_metered_at_that_tiers_rate(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A take H3 cannot film — 20 seconds at 480p — goes out on Seedance's own bounds, and an
    unpriced job is metered at that tier's rate, not at H3's flat $0.13 per second."""
    api = _VideoApi(statuses=[{"status": "completed"}])
    _wire_video(monkeypatch, api)
    workspace_id, turn_id = await _keyed_turn()
    sandbox = _Sandbox()
    result = await _film(
        workspace_id,
        turn_id,
        sandbox,
        tmp_path,
        model="bytedance/seedance-2.5",
        duration=20,
        resolution="480p",
    )
    assert api.sent() == {
        "model": "bytedance/seedance-2.5",
        "prompt": "a red panda astronaut drifting down a station corridor",
        "duration": 20,
        "resolution": "480p",
        "generate_audio": True,
    }
    assert sandbox.writes == {"generated-videos/teaser.mp4": MP4}
    assert json.loads(result.content[0].text)["cost_micro_usd"] == 20 * 107_471
    assert await _videos_ledger(turn_id) == (
        1,
        20 * 107_471,
        "bytedance/seedance-2.5",
        None,
    )


async def test_a_bigger_frame_on_a_token_billed_model_meters_more_per_second(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seedance's charge is per video token, so the same 6-second take costs more at 720p than at
    480p; the fallback rate is read per tier and the ledger carries the difference."""
    _wire_video(monkeypatch, _VideoApi(statuses=[{"status": "completed"}]))
    workspace_id, turn_id = await _keyed_turn()
    await _film(
        workspace_id,
        turn_id,
        _Sandbox(),
        tmp_path,
        model="bytedance/seedance-2.5",
        duration=6,
    )
    assert await _videos_ledger(turn_id) == (1, 6 * 232_577, "bytedance/seedance-2.5", None)


async def test_a_byok_video_meters_the_upstream_charge_it_reports(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_video(
        monkeypatch,
        _VideoApi(
            statuses=[
                {
                    "status": "completed",
                    "usage": {
                        "cost": 0,
                        "is_byok": True,
                        "cost_details": {"upstream_inference_cost": 0.78},
                    },
                }
            ]
        ),
    )
    workspace_id, turn_id = await _keyed_turn()
    await _film(workspace_id, turn_id, _Sandbox(), tmp_path, duration=6)
    assert await _videos_ledger(turn_id) == (1, 780_000, "minimax/hailuo-3", None)


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


async def test_two_generations_on_one_turn_accumulate_into_one_videos_row(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_video(monkeypatch, _VideoApi(statuses=[{"status": "completed", "usage": {"cost": 0.65}}]))
    workspace_id, turn_id = await _keyed_turn()
    await _film(workspace_id, turn_id, _Sandbox(), tmp_path)
    await _film(workspace_id, turn_id, _Sandbox(), tmp_path, name="second")
    assert await _videos_ledger(turn_id) == (2, 1_300_000, "minimax/hailuo-3", None)


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
