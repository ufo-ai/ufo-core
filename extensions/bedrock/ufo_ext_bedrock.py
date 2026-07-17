"""Amazon Bedrock Mantle model provider over Anthropic and OpenAI-compatible APIs."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import anthropic
import openai
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseOutputItemAddedEvent,
    ResponseRefusalDeltaEvent,
    ResponseTextDeltaEvent,
)
from openai.types.responses.easy_input_message_param import EasyInputMessageParam
from openai.types.responses.function_tool_param import FunctionToolParam
from openai.types.responses.response_function_call_output_item_list_param import (
    ResponseFunctionCallOutputItemParam,
)
from openai.types.responses.response_function_tool_call_param import (
    ResponseFunctionToolCallParam,
)
from openai.types.responses.response_input_image_content_param import ResponseInputImageContentParam
from openai.types.responses.response_input_image_param import ResponseInputImageParam
from openai.types.responses.response_input_message_content_list_param import (
    ResponseInputContentParam,
)
from openai.types.responses.response_input_param import FunctionCallOutput, ResponseInputItemParam
from openai.types.responses.response_input_text_content_param import ResponseInputTextContentParam
from openai.types.responses.response_input_text_param import ResponseInputTextParam

from ufo.sdk.manifest import CredentialSlot, Manifest, ModelProviderSpec
from ufo.sdk.models import (
    AnthropicClient,
    ImageBlock,
    Message,
    ModelClient,
    ModelEvent,
    ModelPrice,
    ModelRefusal,
    ModelRequest,
    ModelResponseTruncated,
    OpenAIClient,
    TextBlock,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    openai_sdk_client,
    trim_images,
)

NAME = "bedrock"
VERSION = "0.1.0"
PROVIDER_NAME = "bedrock"
BEDROCK_API_KEY_ENV = "AWS_BEARER_TOKEN_BEDROCK"
BEDROCK_KEY_SLOT = "bedrock_api_key"
AWS_REGION_ENV = "AWS_REGION"
AWS_DEFAULT_REGION_ENV = "AWS_DEFAULT_REGION"
PROVIDER_TIMEOUT_SECONDS = 60.0
MAX_PROVIDER_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0
MAX_EMPTY_PROVIDER_RETRIES = 3

ANTHROPIC_PRICES: tuple[tuple[str, ModelPrice], ...] = (
    (
        "anthropic.claude-fable-5",
        ModelPrice(10_000_000, 50_000_000, 1_000_000, 12_500_000),
    ),
    (
        "anthropic.claude-opus-4-8",
        ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
    ),
    (
        "anthropic.claude-opus-4-7",
        ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
    ),
    (
        "anthropic.claude-opus-4-6-v1",
        ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
    ),
    (
        "anthropic.claude-sonnet-5",
        ModelPrice(3_000_000, 15_000_000, 300_000, 3_750_000),
    ),
    (
        "anthropic.claude-sonnet-4-6",
        ModelPrice(3_000_000, 15_000_000, 300_000, 3_750_000),
    ),
)
OPENAI_CHAT_PRICES: tuple[tuple[str, ModelPrice], ...] = (
    ("openai.gpt-oss-20b", ModelPrice(70_000, 300_000, 0, 0)),
    ("openai.gpt-oss-120b", ModelPrice(150_000, 600_000, 0, 0)),
)
OPENAI_RESPONSES_PRICES: tuple[tuple[str, ModelPrice], ...] = (
    ("openai.gpt-5.4", ModelPrice(2_500_000, 15_000_000, 250_000, 2_500_000)),
    ("openai.gpt-5.5", ModelPrice(5_000_000, 30_000_000, 500_000, 5_000_000)),
)
BEDROCK_PRICES = (*ANTHROPIC_PRICES, *OPENAI_CHAT_PRICES, *OPENAI_RESPONSES_PRICES)
ANTHROPIC_MODEL_IDS = frozenset(model for model, _ in ANTHROPIC_PRICES)
OPENAI_CHAT_MODEL_IDS = frozenset(model for model, _ in OPENAI_CHAT_PRICES)
OPENAI_RESPONSES_MODEL_IDS = frozenset(model for model, _ in OPENAI_RESPONSES_PRICES)


def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]:
    items: list[ResponseInputItemParam] = []
    for message in trim_images(messages):
        if isinstance(message.content, str):
            items.append(EasyInputMessageParam(role=message.role, content=message.content))
            continue
        content: list[ResponseInputContentParam] = []
        for block in message.content:
            match block:
                case TextBlock(text=text):
                    content.append(ResponseInputTextParam(type="input_text", text=text))
                case ImageBlock(source=source):
                    content.append(
                        ResponseInputImageParam(
                            type="input_image",
                            detail="auto",
                            image_url=f"data:{source.media_type};base64,{source.data}",
                        )
                    )
                case ToolUseBlock(id=call_id, name=name, input=arguments):
                    if content:
                        items.append(EasyInputMessageParam(role=message.role, content=content))
                        content = []
                    items.append(
                        ResponseFunctionToolCallParam(
                            type="function_call",
                            call_id=call_id,
                            name=name,
                            arguments=json.dumps(arguments),
                        )
                    )
                case ToolResultBlock(tool_use_id=call_id, content=result, is_error=is_error):
                    if content:
                        items.append(EasyInputMessageParam(role=message.role, content=content))
                        content = []
                    if isinstance(result, str):
                        output: str | list[ResponseFunctionCallOutputItemParam] = (
                            f"[tool error] {result}" if is_error else result
                        )
                    else:
                        output = [
                            ResponseInputTextContentParam(
                                type="input_text",
                                text=f"[tool error] {part.text}" if is_error else part.text,
                            )
                            if isinstance(part, TextBlock)
                            else ResponseInputImageContentParam(
                                type="input_image",
                                detail="auto",
                                image_url=(
                                    f"data:{part.source.media_type};base64,{part.source.data}"
                                ),
                            )
                            for part in result
                        ]
                    items.append(
                        FunctionCallOutput(
                            type="function_call_output",
                            call_id=call_id,
                            output=output,
                        )
                    )
        if content:
            items.append(EasyInputMessageParam(role=message.role, content=content))
    return items


def responses_request(request: ModelRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": request.model,
        "instructions": request.system,
        "input": responses_input(request.messages),
        "max_output_tokens": request.max_tokens,
        "store": False,
        "stream": True,
    }
    if request.reasoning != "off":
        kwargs["reasoning"] = {"effort": request.reasoning}
    if request.tools:
        kwargs["tools"] = [
            FunctionToolParam(
                type="function",
                name=tool.name,
                description=tool.description,
                parameters=tool.input_schema,
                strict=False,
            )
            for tool in request.tools
        ]
        kwargs["parallel_tool_calls"] = request.tool_choice is None
        if request.tool_choice is not None:
            kwargs["tool_choice"] = {"type": "function", "name": request.tool_choice}
    return kwargs


@dataclass(frozen=True)
class BedrockResponsesClient:
    client: openai.AsyncOpenAI

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        delay = INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        empty_attempt = 0
        while True:
            yielded = False
            tool_call_ids: dict[str, str] = {}
            tool_call_arguments: set[str] = set()
            usage: Usage | None = None
            try:
                stream = await self.client.responses.create(**responses_request(request))
                async for event in stream:
                    match event:
                        case ResponseTextDeltaEvent(delta=text):
                            yielded = True
                            yield TextDelta(text=text)
                        case ResponseOutputItemAddedEvent(
                            item=ResponseFunctionToolCall(id=item_id, call_id=call_id, name=name)
                        ):
                            if item_id is None:
                                raise RuntimeError("OpenAI function call has no item id")
                            tool_call_ids[item_id] = call_id
                            yielded = True
                            yield ToolCallStart(id=call_id, name=name)
                        case ResponseFunctionCallArgumentsDeltaEvent(
                            item_id=item_id, delta=partial_json
                        ):
                            tool_call_arguments.add(item_id)
                            yielded = True
                            yield ToolCallDelta(
                                id=tool_call_ids[item_id], partial_json=partial_json
                            )
                        case ResponseFunctionCallArgumentsDoneEvent(
                            item_id=item_id, arguments=arguments
                        ) if item_id not in tool_call_arguments:
                            yielded = True
                            yield ToolCallDelta(id=tool_call_ids[item_id], partial_json=arguments)
                        case ResponseRefusalDeltaEvent(delta=refusal):
                            raise ModelRefusal(f"OpenAI declined the completion: {refusal}")
                        case ResponseCompletedEvent(response=response):
                            raw = response.usage
                            if raw is None:
                                raise RuntimeError("model stream produced no usage")
                            cached_tokens = raw.input_tokens_details.cached_tokens
                            if cached_tokens > raw.input_tokens:
                                raise RuntimeError(
                                    "cached prompt tokens exceed total prompt tokens"
                                )
                            usage = Usage(
                                input_tokens=raw.input_tokens - cached_tokens,
                                output_tokens=raw.output_tokens,
                                cache_read_tokens=cached_tokens,
                            )
                        case ResponseIncompleteEvent(response=response):
                            reason = response.incomplete_details
                            if reason is not None and reason.reason == "max_output_tokens":
                                raise ModelResponseTruncated(
                                    "OpenAI response truncated at the max_output_tokens budget"
                                )
                            if reason is not None and reason.reason == "content_filter":
                                raise ModelRefusal(
                                    "OpenAI declined the completion (content_filter)"
                                )
                            raise RuntimeError("OpenAI returned an incomplete response")
                        case ResponseFailedEvent(response=response):
                            message = response.error.message if response.error else "unknown error"
                            raise RuntimeError(f"OpenAI response failed: {message}")
                        case ResponseErrorEvent(message=message):
                            raise RuntimeError(f"OpenAI response failed: {message}")
            except openai.APITimeoutError:
                attempt += 1
                if yielded or attempt > MAX_PROVIDER_RETRIES:
                    raise
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)
                continue
            except openai.APIStatusError as error:
                attempt += 1
                retryable = error.status_code == 429 or error.status_code >= 500
                if yielded or not retryable or attempt > MAX_PROVIDER_RETRIES:
                    raise
                header = error.response.headers.get("retry-after")
                try:
                    wait = max(float(header), 0.0) if header is not None else delay
                except ValueError:
                    wait = delay
                await asyncio.sleep(wait)
                delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)
                continue
            if usage is None:
                raise RuntimeError("model stream produced no usage")
            if not yielded and empty_attempt < MAX_EMPTY_PROVIDER_RETRIES:
                empty_attempt += 1
                continue
            yield usage
            return


def bedrock_region() -> str:
    region = os.environ.get(AWS_REGION_ENV) or os.environ.get(AWS_DEFAULT_REGION_ENV)
    if not region:
        raise RuntimeError(
            f"Bedrock needs a region: set {AWS_REGION_ENV} or {AWS_DEFAULT_REGION_ENV}"
        )
    return region


def bedrock_client(model: str, key: str) -> ModelClient:
    region = bedrock_region()
    if model in ANTHROPIC_MODEL_IDS:
        return AnthropicClient(
            client=cast(
                anthropic.AsyncAnthropic,
                anthropic.AsyncAnthropicBedrockMantle(
                    api_key=key,
                    aws_region=region,
                    max_retries=0,
                    timeout=PROVIDER_TIMEOUT_SECONDS,
                ),
            )
        )
    if model in OPENAI_CHAT_MODEL_IDS:
        return OpenAIClient(
            client=openai_sdk_client(
                key,
                f"https://bedrock-mantle.{region}.api.aws/v1",
            )
        )
    if model in OPENAI_RESPONSES_MODEL_IDS:
        return BedrockResponsesClient(
            client=openai_sdk_client(
                key,
                f"https://bedrock-mantle.{region}.api.aws/openai/v1",
            )
        )
    raise ValueError(f"Bedrock Mantle does not serve model {model!r}")


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=BEDROCK_KEY_SLOT,
                description="Amazon Bedrock API key used by Mantle model APIs.",
            ),
        ),
        models=(
            ModelProviderSpec(
                name=PROVIDER_NAME,
                matches=lambda model: (
                    model in ANTHROPIC_MODEL_IDS
                    or model in OPENAI_CHAT_MODEL_IDS
                    or model in OPENAI_RESPONSES_MODEL_IDS
                ),
                client=bedrock_client,
                key_slot=BEDROCK_KEY_SLOT,
                key_env=BEDROCK_API_KEY_ENV,
                prices=BEDROCK_PRICES,
            ),
        ),
    )
