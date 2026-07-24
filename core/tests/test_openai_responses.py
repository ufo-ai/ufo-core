"""The OpenAI client's Responses-surface path (`OpenAIClient._complete_responses`, selected by
`spec.api_surface == "responses"`) — the streaming/translation/retry/truncation/refusal/empty
contract for a model like `gpt-5.6-terra` that is called on `/v1/responses`."""

from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import httpx
import openai
import pytest
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseOutputItemAddedEvent,
    ResponseRefusalDeltaEvent,
    ResponseTextDeltaEvent,
)
from openai.types.responses.response import IncompleteDetails
from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
    ResponseUsage,
)

from ufo.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    ModelRefusal,
    ModelRequest,
    ModelResponseTruncated,
    TextBlock,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)
from ufo.models.openai import (
    MAX_EMPTY_PROVIDER_RETRIES,
    MAX_PROVIDER_RETRIES,
    OpenAIClient,
    responses_request,
)
from ufo.models.pricing import ModelPrice
from ufo.models.spec import ModelSpec, ReasoningSupport
from ufo.schema.records import Usage

RESPONSES_SPEC = ModelSpec(
    id="gpt-5.6-terra",
    provider="openai",
    client=lambda spec, key: OpenAIClient(client=cast(openai.AsyncOpenAI, None), spec=spec),
    price=ModelPrice(0, 0, 0, 0),
    knowledge_cutoff="2026-02",
    context_window=272_000,
    reasoning=ReasoningSupport(supported=True, tools_with_reasoning=True),
    api_surface="responses",
)

type ResponseOutcome = Exception | tuple[list[object], Exception | None]


class ScriptedResponses:
    def __init__(self, *outcomes: ResponseOutcome) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def create(self, **kwargs: Any) -> AsyncIterator[object]:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        events, tail = outcome
        return self._stream(events, tail)

    async def _stream(self, events: list[object], tail: Exception | None) -> AsyncIterator[object]:
        for event in events:
            yield event
        if tail is not None:
            raise tail


def _response(
    *,
    usage: ResponseUsage | None = None,
    incomplete: IncompleteDetails | None = None,
) -> Response:
    return Response(
        id="response-1",
        created_at=0,
        model="gpt-5.6-terra",
        object="response",
        output=[],
        parallel_tool_calls=True,
        tool_choice="auto",
        tools=[],
        usage=usage,
        incomplete_details=incomplete,
    )


def _responses_client(scripted: ScriptedResponses) -> OpenAIClient:
    sdk = cast(openai.AsyncOpenAI, SimpleNamespace(responses=scripted))
    return OpenAIClient(client=sdk, spec=RESPONSES_SPEC)


def _request() -> ModelRequest:
    return ModelRequest(
        model="gpt-5.6-terra",
        system="s",
        messages=(Message(role="user", content="hi"),),
        max_tokens=64,
    )


def _completed_events(
    text: str = "ok", input_tokens: int = 1, output_tokens: int = 1
) -> list[object]:
    events: list[object] = []
    if text:
        events.append(
            ResponseTextDeltaEvent(
                type="response.output_text.delta",
                item_id="message-1",
                output_index=0,
                content_index=0,
                sequence_number=0,
                delta=text,
                logprobs=[],
            )
        )
    events.append(
        ResponseCompletedEvent(
            type="response.completed",
            sequence_number=1,
            response=_response(
                usage=ResponseUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    input_tokens_details=InputTokensDetails(cached_tokens=0),
                    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
                )
            ),
        )
    )
    return events


def _provider_error(status: int, retry_after: str | None = "0") -> openai.APIStatusError:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    response = httpx.Response(
        status_code=status,
        headers=headers,
        request=httpx.Request("POST", "https://provider.invalid/v1/responses"),
    )
    return openai.APIStatusError("provider error", response=response, body=None)


def _provider_timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(
        request=httpx.Request("POST", "https://provider.invalid/v1/responses")
    )


async def test_responses_path_translates_images_tools_and_usage() -> None:
    scripted = ScriptedResponses(
        (
            [
                ResponseOutputItemAddedEvent(
                    type="response.output_item.added",
                    output_index=0,
                    sequence_number=0,
                    item=ResponseFunctionToolCall(
                        type="function_call",
                        id="item-1",
                        call_id="call-1",
                        name="read",
                        arguments="",
                    ),
                ),
                ResponseFunctionCallArgumentsDeltaEvent(
                    type="response.function_call_arguments.delta",
                    item_id="item-1",
                    output_index=0,
                    sequence_number=1,
                    delta='{"path":"a.png"}',
                ),
                ResponseTextDeltaEvent(
                    type="response.output_text.delta",
                    item_id="message-1",
                    output_index=1,
                    content_index=0,
                    sequence_number=2,
                    delta="done",
                    logprobs=[],
                ),
                ResponseCompletedEvent(
                    type="response.completed",
                    sequence_number=3,
                    response=_response(
                        usage=ResponseUsage(
                            input_tokens=12,
                            output_tokens=4,
                            total_tokens=16,
                            input_tokens_details=InputTokensDetails(cached_tokens=5),
                            output_tokens_details=OutputTokensDetails(reasoning_tokens=2),
                        )
                    ),
                ),
            ],
            None,
        )
    )
    request = ModelRequest(
        model="gpt-5.6-terra",
        system="be terse",
        max_tokens=128,
        tools=(ToolSchema(name="read", description="read it", input_schema={"type": "object"}),),
        messages=(
            Message(
                role="user",
                content=(
                    TextBlock(text="look"),
                    ImageBlock(source=ImageSource(media_type="image/png", data="QUJD")),
                ),
            ),
            Message(
                role="assistant",
                content=(ToolUseBlock(id="old-call", name="read", input={"path": "old"}),),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="old-call", content="missing", is_error=True),
                ),
            ),
        ),
    )
    events = [event async for event in _responses_client(scripted).complete(request)]
    assert events == [
        ToolCallStart(id="call-1", name="read"),
        ToolCallDelta(id="call-1", partial_json='{"path":"a.png"}'),
        TextDelta(text="done"),
        Usage(input_tokens=7, output_tokens=4, cache_read_tokens=5),
    ]


def test_responses_request_preserves_input_controls_and_disables_storage() -> None:
    request = ModelRequest(
        model="gpt-5.6-terra",
        system="be terse",
        max_tokens=128,
        tools=(ToolSchema(name="read", description="read it", input_schema={"type": "object"}),),
        messages=(
            Message(
                role="user",
                content=(
                    TextBlock(text="look"),
                    ImageBlock(source=ImageSource(media_type="image/png", data="QUJD")),
                ),
            ),
            Message(
                role="assistant",
                content=(ToolUseBlock(id="old-call", name="read", input={"path": "old"}),),
            ),
            Message(
                role="user",
                content=(
                    ToolResultBlock(tool_use_id="old-call", content="missing", is_error=True),
                ),
            ),
        ),
    )
    kwargs = responses_request(request)
    assert kwargs["instructions"] == "be terse"
    assert kwargs["store"] is False
    assert kwargs["reasoning"] == {"effort": "high"}
    assert kwargs["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": "data:image/png;base64,QUJD",
                },
            ],
        },
        {
            "type": "function_call",
            "call_id": "old-call",
            "name": "read",
            "arguments": '{"path": "old"}',
        },
        {
            "type": "function_call_output",
            "call_id": "old-call",
            "output": "[tool error] missing",
        },
    ]


async def test_responses_path_fails_loud_on_truncation_and_refusal() -> None:
    truncated = ScriptedResponses(
        (
            [
                ResponseIncompleteEvent(
                    type="response.incomplete",
                    sequence_number=0,
                    response=_response(incomplete=IncompleteDetails(reason="max_output_tokens")),
                )
            ],
            None,
        )
    )
    with pytest.raises(ModelResponseTruncated):
        [event async for event in _responses_client(truncated).complete(_request())]

    refused = ScriptedResponses(
        (
            [
                ResponseRefusalDeltaEvent(
                    type="response.refusal.delta",
                    item_id="message-1",
                    output_index=0,
                    content_index=0,
                    sequence_number=0,
                    delta="no",
                )
            ],
            None,
        )
    )
    with pytest.raises(ModelRefusal):
        [event async for event in _responses_client(refused).complete(_request())]


@pytest.mark.parametrize("status", [429, 500])
async def test_responses_path_retries_status_then_succeeds(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ufo.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    scripted = ScriptedResponses(_provider_error(status), (_completed_events(), None))
    events = [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == 2
    assert events == [TextDelta(text="ok"), Usage(input_tokens=1, output_tokens=1)]


async def test_responses_path_honors_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr("ufo.models.openai.asyncio.sleep", sleep)
    scripted = ScriptedResponses(
        _provider_error(429, retry_after="0.25"), (_completed_events(), None)
    )
    [event async for event in _responses_client(scripted).complete(_request())]
    assert waits == [0.25]


async def test_responses_path_does_not_retry_client_error() -> None:
    scripted = ScriptedResponses(_provider_error(400), (_completed_events(), None))
    with pytest.raises(openai.APIStatusError):
        [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == 1


@pytest.mark.parametrize("error", [_provider_error(429), _provider_timeout()])
async def test_responses_path_exhausts_retries(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ufo.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    scripted = ScriptedResponses(*([error] * (MAX_PROVIDER_RETRIES + 1)))
    with pytest.raises(type(error)):
        [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == MAX_PROVIDER_RETRIES + 1


async def test_responses_path_retries_timeout_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ufo.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    scripted = ScriptedResponses(_provider_timeout(), (_completed_events(), None))
    events = [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == 2
    assert events == [TextDelta(text="ok"), Usage(input_tokens=1, output_tokens=1)]


@pytest.mark.parametrize("tail", [_provider_error(500), _provider_timeout()])
async def test_responses_path_does_not_retry_after_first_yield(tail: Exception) -> None:
    scripted = ScriptedResponses(
        (_completed_events(text="partial")[:1], tail), (_completed_events(), None)
    )
    received = []
    with pytest.raises(type(tail)):
        async for event in _responses_client(scripted).complete(_request()):
            received.append(event)
    assert received == [TextDelta(text="partial")]
    assert scripted.calls == 1


async def test_responses_path_persistent_empty_degrades_to_usage() -> None:
    empty = (_completed_events(text="", input_tokens=1, output_tokens=0), None)
    scripted = ScriptedResponses(*([empty] * (MAX_EMPTY_PROVIDER_RETRIES + 1)))
    events = [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == MAX_EMPTY_PROVIDER_RETRIES + 1
    assert events == [Usage(input_tokens=1, output_tokens=0)]


async def test_responses_path_omits_reasoning_when_the_model_does_not_support_it() -> None:
    seen: dict[str, Any] = {}
    scripted = ScriptedResponses((_completed_events(), None))

    class Capturing:
        async def create(self, **kwargs: Any) -> AsyncIterator[object]:
            seen.update(kwargs)
            return await scripted.create(**kwargs)

    spec = replace(
        RESPONSES_SPEC, reasoning=ReasoningSupport(supported=False, tools_with_reasoning=False)
    )
    client = OpenAIClient(
        client=cast(openai.AsyncOpenAI, SimpleNamespace(responses=Capturing())), spec=spec
    )
    request = _request().model_copy(update={"reasoning": "high"})
    [event async for event in client.complete(request)]
    assert "reasoning" not in seen


async def test_responses_path_omits_reasoning_when_tools_forbid_the_pair() -> None:
    seen: dict[str, Any] = {}
    scripted = ScriptedResponses((_completed_events(), None))

    class Capturing:
        async def create(self, **kwargs: Any) -> AsyncIterator[object]:
            seen.update(kwargs)
            return await scripted.create(**kwargs)

    spec = replace(
        RESPONSES_SPEC,
        reasoning=ReasoningSupport(supported=True, tools_with_reasoning=False),
    )
    client = OpenAIClient(
        client=cast(openai.AsyncOpenAI, SimpleNamespace(responses=Capturing())), spec=spec
    )
    request = _request().model_copy(
        update={
            "tools": (
                ToolSchema(name="search", description="Search", input_schema={"type": "object"}),
            )
        }
    )
    [event async for event in client.complete(request)]
    assert "tools" in seen
    assert "reasoning" not in seen
