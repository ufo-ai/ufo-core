"""The OpenAI client's Responses-surface path (`OpenAIClient._complete_responses`, selected by
`spec.api_surface == "responses"`) — the streaming/translation/retry/truncation/refusal/empty
contract for a model like `gpt-5.6-terra` that is called on `/v1/responses`."""

import logging
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
    ResponseOutputItemDoneEvent,
    ResponseReasoningItem,
    ResponseRefusalDeltaEvent,
    ResponseTextDeltaEvent,
)
from openai.types.responses.response import IncompleteDetails
from openai.types.responses.response_reasoning_item import Summary as ReasoningSummary
from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
    ResponseUsage,
)

from ufo.harness.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    ModelRefusal,
    ModelRequest,
    ModelResponseTruncated,
    ModelStreamStart,
    ReasoningItemBlock,
    TextBlock,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)
from ufo.harness.models.openai import (
    MAX_EMPTY_PROVIDER_RETRIES,
    MAX_PROVIDER_RETRIES,
    OpenAIClient,
)
from ufo.harness.models.pricing import ModelPrice
from ufo.harness.models.spec import ModelSpec, ReasoningSupport
from ufo.harness.rounds import ModelStreamInterrupted
from ufo.schema.records import Usage

RESPONSES_SPEC = ModelSpec(
    id="gpt-5.6-terra",
    provider="openai",
    client=lambda spec, key: OpenAIClient(client=cast(openai.AsyncOpenAI, None), spec=spec),
    price=ModelPrice(0, 0, 0, 0, 0, cache_write_30m=1),
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
        conversation_cache_ttl="5m",
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


def _reasoning_done(
    item_id: str,
    encrypted: str | None = "ZW5jcnlwdGVk",
    summary: tuple[str, ...] = ("weighing it",),
    output_index: int = 0,
) -> ResponseOutputItemDoneEvent:
    """One whole reasoning item as a stateless response hands it back: the summary parts the model
    chose to show and the encrypted body that carries the reasoning itself."""
    return ResponseOutputItemDoneEvent(
        type="response.output_item.done",
        output_index=output_index,
        sequence_number=0,
        item=ResponseReasoningItem(
            type="reasoning",
            id=item_id,
            encrypted_content=encrypted,
            summary=[ReasoningSummary(type="summary_text", text=text) for text in summary],
        ),
    )


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


def _remote_protocol_error() -> httpx.RemoteProtocolError:
    return httpx.RemoteProtocolError("peer closed incomplete response")


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
                            input_tokens_details=InputTokensDetails(
                                cached_tokens=5, cache_write_tokens=3
                            ),
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
        conversation_cache_ttl="5m",
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
        ModelStreamStart(),
        ToolCallStart(id="call-1", name="read"),
        ToolCallDelta(id="call-1", partial_json='{"path":"a.png"}'),
        TextDelta(text="done"),
        Usage(
            input_tokens=4,
            output_tokens=4,
            cache_read_tokens=5,
            cache_write_30m_tokens=3,
        ),
    ]


async def test_responses_path_yields_the_reasoning_items_once_the_stream_closes() -> None:
    """The round's reasoning items arrive whole on their done events and are held to the end, in the
    provider's own output order and just ahead of the Usage, so the engine can send that sequence
    back on the message carrying this round's function calls. An item whose request asked for no
    summary is still real — the encrypted body is what the next request resolves on."""
    scripted = ScriptedResponses(
        (
            [
                _reasoning_done("rs_1"),
                _reasoning_done("rs_2", encrypted="bW9yZQ", summary=(), output_index=1),
                *_completed_events(text="done", input_tokens=3, output_tokens=4),
            ],
            None,
        )
    )
    events = [event async for event in _responses_client(scripted).complete(_request())]
    assert events == [
        ModelStreamStart(),
        TextDelta(text="done"),
        ReasoningItemBlock(id="rs_1", encrypted_content="ZW5jcnlwdGVk", summary=("weighing it",)),
        ReasoningItemBlock(id="rs_2", encrypted_content="bW9yZQ"),
        Usage(input_tokens=3, output_tokens=4),
    ]


async def test_responses_reasoning_without_an_answer_is_an_empty_completion() -> None:
    """Reasoning alone is not an answer: the round is re-issued, and the abandoned attempt's item is
    never delivered beside the new attempt's."""
    scripted = ScriptedResponses(
        (
            [
                _reasoning_done("rs_dropped"),
                *_completed_events(text="", input_tokens=1, output_tokens=0),
            ],
            None,
        ),
        (
            [
                _reasoning_done("rs_kept"),
                *_completed_events(text="recovered", input_tokens=2, output_tokens=3),
            ],
            None,
        ),
    )
    events = [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == 2
    assert events == [
        ModelStreamStart(),
        Usage(input_tokens=1, output_tokens=0),
        ModelStreamStart(),
        TextDelta(text="recovered"),
        ReasoningItemBlock(
            id="rs_kept", encrypted_content="ZW5jcnlwdGVk", summary=("weighing it",)
        ),
        Usage(input_tokens=2, output_tokens=3),
    ]


async def test_responses_stream_dying_after_a_reasoning_item_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ufo.harness.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    scripted = ScriptedResponses(
        ([_reasoning_done("rs_abandoned")], _provider_timeout()),
        (
            [
                _reasoning_done("rs_kept"),
                *_completed_events(text="recovered", input_tokens=2, output_tokens=3),
            ],
            None,
        ),
    )
    events = [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == 2
    assert events == [
        ModelStreamStart(),
        ModelStreamStart(),
        TextDelta(text="recovered"),
        ReasoningItemBlock(
            id="rs_kept", encrypted_content="ZW5jcnlwdGVk", summary=("weighing it",)
        ),
        Usage(input_tokens=2, output_tokens=3),
    ]


async def test_responses_reasoning_item_without_encrypted_content_fails_loud() -> None:
    """The request asks for `encrypted_content`, so an item arriving without it is a provider that
    did not honour `include`. Such an item cannot be replayed — echoing the bare id would resolve
    against state a `store=False` request left nowhere — so the round fails here, naming the missing
    field, rather than on the next request's 400."""
    scripted = ScriptedResponses(
        ([_reasoning_done("rs_1", encrypted=None), *_completed_events()], None)
    )
    with pytest.raises(RuntimeError, match="no encrypted content"):
        [event async for event in _responses_client(scripted).complete(_request())]


async def test_responses_path_fails_loud_on_truncation_and_refusal() -> None:
    usage = ResponseUsage(
        input_tokens=3,
        output_tokens=7,
        total_tokens=10,
        input_tokens_details=InputTokensDetails(cached_tokens=0),
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
    )
    truncated = ScriptedResponses(
        (
            [
                ResponseIncompleteEvent(
                    type="response.incomplete",
                    sequence_number=0,
                    response=_response(
                        usage=usage,
                        incomplete=IncompleteDetails(reason="max_output_tokens"),
                    ),
                )
            ],
            None,
        )
    )
    truncated_events = []
    with pytest.raises(ModelResponseTruncated):
        async for event in _responses_client(truncated).complete(_request()):
            truncated_events.append(event)
    assert truncated_events == [ModelStreamStart(), Usage(input_tokens=3, output_tokens=7)]

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
                ),
                *_completed_events(text="", input_tokens=3, output_tokens=7),
            ],
            None,
        )
    )
    refused_events = []
    with pytest.raises(ModelRefusal):
        async for event in _responses_client(refused).complete(_request()):
            refused_events.append(event)
    assert refused_events == [ModelStreamStart(), Usage(input_tokens=3, output_tokens=7)]


async def test_responses_path_keeps_the_terminal_class_without_usage() -> None:
    """An incomplete or refused round that reports no usage keeps its own error class. The engine
    recovers a truncated round on ModelResponseTruncated alone, and a refusal recorded as a plain
    RuntimeError would read as an internal fault."""
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
    truncated_events = []
    with pytest.raises(ModelResponseTruncated):
        async for event in _responses_client(truncated).complete(_request()):
            truncated_events.append(event)
    assert truncated_events == [ModelStreamStart()]

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
    refused_events = []
    with pytest.raises(ModelRefusal):
        async for event in _responses_client(refused).complete(_request()):
            refused_events.append(event)
    assert refused_events == [ModelStreamStart()]


@pytest.mark.parametrize("status", [429, 500])
async def test_responses_path_retries_status_then_succeeds(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ufo.harness.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    scripted = ScriptedResponses(_provider_error(status), (_completed_events(), None))
    events = [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == 2
    assert events == [
        ModelStreamStart(),
        TextDelta(text="ok"),
        Usage(input_tokens=1, output_tokens=1),
    ]


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    (("0.25", 2.0), ("42", 42.0), ("invalid", 2.0)),
)
async def test_responses_path_uses_exponential_floor_for_retry_after(
    retry_after: str, expected: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr("ufo.harness.models.openai.asyncio.sleep", sleep)
    scripted = ScriptedResponses(
        _provider_error(429, retry_after=retry_after), (_completed_events(), None)
    )
    [event async for event in _responses_client(scripted).complete(_request())]
    assert waits == [expected]


async def test_responses_path_does_not_retry_client_error() -> None:
    scripted = ScriptedResponses(_provider_error(400), (_completed_events(), None))
    with pytest.raises(openai.APIStatusError):
        [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == 1


@pytest.mark.parametrize("error", [_provider_error(429), _provider_timeout()])
async def test_responses_path_exhausts_retries(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ufo.harness.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    scripted = ScriptedResponses(*([error] * (MAX_PROVIDER_RETRIES + 1)))
    with pytest.raises(type(error)):
        [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == MAX_PROVIDER_RETRIES + 1


async def test_responses_path_retries_timeout_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ufo.harness.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    scripted = ScriptedResponses(_provider_timeout(), (_completed_events(), None))
    events = [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == 2
    assert events == [
        ModelStreamStart(),
        TextDelta(text="ok"),
        Usage(input_tokens=1, output_tokens=1),
    ]


async def test_responses_path_retries_disconnect_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("ufo.harness.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    scripted = ScriptedResponses(_remote_protocol_error(), (_completed_events(), None))
    with caplog.at_level(logging.INFO):
        events = [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == 2
    assert events == [
        ModelStreamStart(),
        TextDelta(text="ok"),
        Usage(input_tokens=1, output_tokens=1),
    ]
    retry = next(
        record
        for record in caplog.records
        if record.getMessage() == "model.provider_transport_retry"
    )
    assert retry.ufo["error_class"] == "RemoteProtocolError"


async def test_responses_path_exhausted_disconnect_logs_error_class(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("ufo.harness.models.openai.INITIAL_RETRY_DELAY_SECONDS", 0.0)
    scripted = ScriptedResponses(
        *(_remote_protocol_error() for _ in range(MAX_PROVIDER_RETRIES + 1))
    )
    with caplog.at_level(logging.INFO), pytest.raises(httpx.RemoteProtocolError):
        [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == MAX_PROVIDER_RETRIES + 1
    failure = next(
        record
        for record in caplog.records
        if record.getMessage() == "model.provider_transport_error"
    )
    assert failure.ufo["error_class"] == "RemoteProtocolError"


@pytest.mark.parametrize(
    ("tail", "kind"),
    [(_provider_error(500), "stream_error"), (_provider_timeout(), "stream_transport")],
)
async def test_responses_path_fault_after_first_yield_interrupts_the_round(
    tail: Exception, kind: str
) -> None:
    scripted = ScriptedResponses(
        (_completed_events(text="partial")[:1], tail), (_completed_events(), None)
    )
    received = []
    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in _responses_client(scripted).complete(_request()):
            received.append(event)
    assert raised.value.kind == kind
    assert received == [ModelStreamStart(), TextDelta(text="partial")]
    assert scripted.calls == 1


async def test_responses_path_sse_injected_error_interrupts_the_round() -> None:
    """An error frame on the live stream surfaces as the exact APIError class and interrupts the
    round whatever the stream yielded so far."""
    error = openai.APIError(
        "Upstream idle timeout exceeded",
        request=httpx.Request("POST", "https://provider.invalid/v1"),
        body=None,
    )
    scripted = ScriptedResponses(
        (_completed_events(text="partial")[:1], error), (_completed_events(), None)
    )
    received = []
    with pytest.raises(ModelStreamInterrupted) as raised:
        async for event in _responses_client(scripted).complete(_request()):
            received.append(event)
    assert raised.value.kind == "stream_error"
    assert received == [ModelStreamStart(), TextDelta(text="partial")]
    assert scripted.calls == 1


async def test_responses_path_persistent_empty_degrades_to_usage() -> None:
    empty = (_completed_events(text="", input_tokens=1, output_tokens=0), None)
    scripted = ScriptedResponses(*([empty] * (MAX_EMPTY_PROVIDER_RETRIES + 1)))
    events = [event async for event in _responses_client(scripted).complete(_request())]
    assert scripted.calls == MAX_EMPTY_PROVIDER_RETRIES + 1
    assert events == [
        event
        for _ in range(MAX_EMPTY_PROVIDER_RETRIES + 1)
        for event in (ModelStreamStart(), Usage(input_tokens=1, output_tokens=0))
    ]


async def test_responses_path_omits_reasoning_when_the_model_does_not_support_it() -> None:
    """A model that does not reason takes no reasoning parameter, for `off` as much as for `high`:
    there is nothing to switch off, and `none` is a value its api does not know."""
    seen: dict[str, Any] = {}
    scripted = ScriptedResponses((_completed_events(), None), (_completed_events(), None))

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
    seen.clear()
    [event async for event in client.complete(request.model_copy(update={"reasoning": "off"}))]
    assert "reasoning" not in seen


async def test_responses_path_refuses_an_off_request_no_reasoning_parameter_can_state() -> None:
    """A model that reasons but refuses the parameter alongside tools cannot be told to stop, so the
    request fails loud instead of running at the provider's default effort on a budget sized for the
    answer alone."""
    scripted = ScriptedResponses((_completed_events(), None))
    spec = replace(
        RESPONSES_SPEC,
        reasoning=ReasoningSupport(supported=True, tools_with_reasoning=False),
    )
    client = OpenAIClient(
        client=cast(openai.AsyncOpenAI, SimpleNamespace(responses=scripted)), spec=spec
    )
    tool = ToolSchema(name="finish", description="finish", input_schema={"type": "object"})
    request = _request().model_copy(
        update={"tools": (tool,), "tool_choice": "finish", "reasoning": "off"}
    )
    with pytest.raises(RuntimeError, match="cannot switch its reasoning off"):
        [event async for event in client.complete(request)]
    assert scripted.calls == 0
