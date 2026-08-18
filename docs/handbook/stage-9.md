# Model request routing and streamed response normalization  `stage-9`

This stage is part of the main work loop, after the system has prepared a request for a language model and chosen where to send it. Its job is like using different power adapters: each provider has its own plug shape, but the rest of UFO expects one standard kind of output.

The Anthropic bridge sends requests to Anthropic’s Messages API, then converts Anthropic’s live streaming reply into UFO’s common events, such as text, reasoning, tool use, usage numbers, and errors. It also retries carefully when Anthropic fails before showing any user-visible output.

The OpenAI bridge does the same for OpenAI-style services. It can use either Chat Completions or Responses APIs, then normalizes their streamed chunks into the same UFO event stream.

The OpenRouter extension registers OpenRouter as another provider. It also adds image and video generation tools, while keeping produced files and costs tracked in the workspace. Together, these adapters let the engine swap providers without changing how the rest of the system reads model results.

## Files in this stage

### Core provider adapters
These files route prepared UFO model requests to Anthropic and OpenAI-compatible APIs and normalize their streamed replies into standard model events.

### `core/src/ufo/models/anthropic.py`

`io_transport` · `request handling`

This file lets the rest of the system talk to Anthropic models without knowing Anthropic's exact message format. Think of it as a translator at a service desk: UFO hands over its own standard request shape, and this file rewrites it into the form Anthropic expects; then it listens to Anthropic's streamed answer and rewrites each piece back into UFO's standard events.

The file covers several important details. It converts text, images, tool calls, tool results, and Anthropic-specific “thinking” blocks into the right wire format. “Thinking” here means provider-supplied reasoning data that may need to be echoed back later; the code preserves it in order and only releases it after the stream finishes, because sending partial reasoning from a failed attempt could corrupt a retried conversation.

The main class, `AnthropicClient`, opens a streaming request, yields text and tool-call events as they arrive, and ends with token usage information. It also treats provider failures carefully. Network timeouts, dropped connections, overloaded-provider errors, and other retryable failures are retried with backoff, but only until something visible has already been yielded. Once the outside world has seen part of an answer, retrying could create duplicated or mixed output, so errors are raised instead. Certain final states, such as truncation or refusal, become clear UFO-level exceptions.

#### Function details

##### `anthropic_sdk_client`  (lines 50–54)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: This creates the low-level Anthropic SDK client used to contact Anthropic's servers. It disables the SDK's built-in retries because this file has its own retry policy that knows when it is safe or unsafe to try again.

**Data flow**: It receives an API key. It builds an asynchronous Anthropic client with that key, a fixed timeout, and SDK retries turned off. It returns that ready-to-use client to whoever is setting up model access.

**Call relations**: This is the starting helper for creating the transport object that `AnthropicClient` will later use. It hands off actual network communication to `anthropic.AsyncAnthropic`, while keeping retry decisions out of the SDK and inside `AnthropicClient.complete`.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 57–61)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: This converts UFO's internal image description into the image block format Anthropic expects. It is used whenever an image is being sent either as normal message content or as part of a tool result.

**Data flow**: It receives an `ImageSource`, which contains the image media type and base64-encoded data. It wraps those fields in Anthropic's expected dictionary shape. The result is a plain data object ready to include in an API request.

**Call relations**: This helper sits underneath the broader content conversion flow. `anthropic_content` uses it for image message blocks, and `_anthropic_tool_result_part` uses it when a tool result contains an image.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 64–69)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: This converts one piece of a tool result into Anthropic's expected format. Tool results can contain text or images, and Anthropic needs each kind described differently.

**Data flow**: It receives one tool-result content block. If the block is text, it returns an Anthropic text dictionary. If the block is an image, it passes the image source to `_anthropic_image` and returns that converted image dictionary.

**Call relations**: This function is called while `anthropic_content` is building a full Anthropic message. It delegates image conversion to `_anthropic_image`, so the same image formatting rules are used everywhere.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 72–106)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: This turns UFO's standard message content into Anthropic message content. It is the main request-side translator for text, images, tool calls, tool results, and Anthropic reasoning blocks.

**Data flow**: It receives either a simple string or a tuple of structured content blocks. A string passes through unchanged. Structured blocks are inspected one by one and rewritten into Anthropic dictionaries; OpenAI-shaped reasoning items are skipped because Anthropic cannot reuse another provider's reasoning format. The output is either the original string or a list of Anthropic-ready content blocks.

**Call relations**: This function is called by `AnthropicClient.complete` while building the request sent to Anthropic. It uses `_anthropic_image` and `_anthropic_tool_result_part` for nested image and tool-result conversion, then hands the finished message content back to the request-building flow.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 114–375)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This sends one UFO model request to Anthropic and yields a stream of UFO model events as Anthropic responds. It is responsible for translating the live response, preserving reasoning blocks, reporting token usage, and deciding which provider failures can safely be retried.

**Data flow**: It receives a `ModelRequest` containing the model name, system prompt, conversation messages, tools, reasoning settings, token limit, and cache settings. It builds Anthropic API arguments, converting message content with `anthropic_content`, adding tool definitions when needed, and enabling Anthropic thinking when requested by the model spec. It opens a streaming API call, reads each provider event, and turns it into UFO events such as stream start, text deltas, tool-call starts, tool-call JSON fragments, reasoning blocks, and final usage counts. It also changes control flow by sleeping and retrying before any visible output, or by raising clear exceptions for refusals, truncation, rejected keys, non-retryable client errors, and exhausted retries.

**Call relations**: This is the central flow in the file. It calls `anthropic_content` before sending the request, then calls Anthropic's streaming API through the stored SDK client. As events arrive, it creates UFO event objects such as `ModelStreamStart`, `TextDelta`, `ToolCallStart`, `ToolCallDelta`, `ThinkingBlock`, and `RedactedThinkingBlock` so the rest of the engine can consume a provider-neutral stream. At the end it yields `Usage`; if Anthropic reports refusal or truncation, it hands off to the corresponding UFO exceptions instead of pretending the answer is normal.

*Call graph*: calls 1 internal fn (anthropic_content); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep (+3 more)).


### `core/src/ufo/models/openai.py`

`io_transport` · `request handling`

UFO speaks to different language models through one internal shape: messages, images, tool calls, reasoning blocks, text deltas, and token usage. OpenAI-compatible providers do not all accept that shape directly, and even OpenAI has two different API surfaces: Chat Completions and Responses. This file is the adapter that translates between those worlds.

At the start of a request, it chooses the correct wire format from the model's specification, not by guessing from the model name. That matters because some models only accept certain combinations, such as tools plus reasoning settings, on the Responses API. The file then builds the provider request: text and images become OpenAI message parts, tool definitions become OpenAI function tools, and tool results are reshaped into the places OpenAI allows them.

When the provider streams back output, the client yields UFO events as they arrive: stream start, text pieces, tool-call starts, tool-call argument pieces, reasoning items, and final usage. It also protects the rest of the system from provider rough edges. It retries temporary network errors and rate-limit/server errors before any visible output has been returned, treats rejected API keys as a clear credential problem, detects truncated responses, and reports refusals. In short, this file is the interpreter at the border: without it, the rest of UFO would need to understand every OpenAI wire detail and failure mode itself.

#### Function details

##### `openai_sdk_client`  (lines 89–95)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Creates the underlying asynchronous OpenAI SDK client used to send requests. It deliberately disables the SDK's own retries so this file's retry rules are the single source of truth.

**Data flow**: It receives an API key and, optionally, a base URL for an OpenAI-compatible service. It builds an OpenAI async client with a fixed timeout, no SDK retries, and the given endpoint. The result is a ready-to-use network client.

**Call relations**: Other setup code can call this when it needs an OpenAI-wire client. The returned SDK object is then stored in OpenAIClient, whose streaming methods use it to make actual Chat Completions or Responses requests.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_openai_image`  (lines 98–102)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts UFO's internal image data into the image format expected by OpenAI chat-style messages. It packages the image as a data URL, which is a self-contained string containing the media type and base64 image data.

**Data flow**: It receives an ImageSource containing a media type and base64 data. It wraps those fields in OpenAI's image_url dictionary shape. The output is a small object that can be inserted into an OpenAI message.

**Call relations**: openai_messages uses this when normal message content contains images. _openai_tool_result also uses it when a tool result includes images that need to be moved into a follow-up user message.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 105–121)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Splits a tool result into the parts OpenAI Chat Completions can accept. Chat tool-result messages are text-only, so any images must be separated and sent later as user image content.

**Data flow**: It receives either a plain text tool result or a tuple of content blocks. If it is text, it returns that text and no images. If it is mixed content, it joins all text blocks into one string and converts image blocks into OpenAI image objects. The result is text for the tool message plus a list of image parts for a later message.

**Call relations**: openai_messages calls this while translating UFO messages for the Chat Completions API. It delegates image conversion to _openai_image, then hands the split result back so openai_messages can place each part where OpenAI allows it.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 124–183)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns UFO's standard conversation history into the message list used by OpenAI's Chat Completions API. It also drops reasoning blocks because that API shape has no place to replay them.

**Data flow**: It receives a system prompt and a tuple of UFO messages. It trims images if needed, then walks through each message. Plain text stays plain; text and images become OpenAI content parts; tool uses become OpenAI function tool calls; tool results become tool messages, with image results lifted into a following user message. The output is a list of dictionaries ready for a chat completion request.

**Call relations**: OpenAIClient._chat_kwargs calls this while building the request body for the chat API. It relies on _openai_image and _openai_tool_result for image and tool-result translation, and on JSON encoding for tool-call arguments.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 186–269)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: Turns UFO's conversation history into the richer input item list required by OpenAI's Responses API. Unlike the chat path, this can preserve OpenAI reasoning items so the model can continue a previous reasoning-and-tool-use round.

**Data flow**: It receives UFO messages and trims images if needed. For each block, it keeps text and images as Responses input content, turns tool uses into function-call input items, turns tool results into function-call output items, and carries OpenAI reasoning items forward with their encrypted content and summaries. Anthropic-style thinking blocks are skipped because they belong to a different provider format. The output is a list of Responses API input items.

**Call relations**: responses_request calls this when assembling a Responses API request. It creates the specific OpenAI typed objects needed by the SDK so OpenAIClient._complete_responses can send a valid streaming request.

*Call graph*: called by 1 (responses_request); 11 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, Summary (+1 more)).


##### `responses_request`  (lines 272–309)

```
def responses_request(request: ModelRequest, effort: OpenAIEffort, retention_none: bool) -> dict[str, Any]
```

**Purpose**: Builds the full request body for OpenAI's Responses API. It combines the prompt, converted conversation, token limit, reasoning setting, data-retention setting, and tool definitions into one provider-ready object.

**Data flow**: It receives a ModelRequest, the already-decided reasoning effort, and a flag saying whether the provider supports no-retention mode. It converts messages through responses_input, adds model settings and streaming options, optionally asks OpenAI to include encrypted reasoning content, optionally disables provider-side storage, and adds tools or a forced tool choice if present. The output is a dictionary passed directly to the SDK.

**Call relations**: OpenAIClient._complete_responses calls this immediately before starting a Responses stream. It hands off message conversion to responses_input and builds tool definitions using OpenAI's function-tool shape.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `OpenAIClient.complete`  (lines 321–324)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Chooses which OpenAI-style API surface to use for a model request. Callers use this as the simple public entry point instead of deciding between Chat Completions and Responses themselves.

**Data flow**: It receives a UFO ModelRequest and reads the model specification stored on the client. If the spec says the model uses the Responses API, it returns the Responses stream. Otherwise, it returns the Chat Completions stream. The output is an asynchronous stream of UFO ModelEvent objects.

**Call relations**: Higher-level model-running code calls complete when it wants output from this provider. complete then routes the work to either OpenAIClient._complete_responses or OpenAIClient._complete_chat, depending on the model spec.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._reasoning_effort`  (lines 326–345)

```
def _reasoning_effort(self, request: ModelRequest) -> OpenAIEffort
```

**Purpose**: Decides what reasoning setting, if any, should be sent to OpenAI for this request. This is important because leaving the setting out is not always the same as turning reasoning off.

**Data flow**: It reads the request's requested reasoning mode, the tools on the request, and the model spec's rules. It asks the spec what the wire-compatible reasoning value is. It converts UFO's 'off' into OpenAI's 'none', turns 'auto' into no explicit parameter, and raises a clear error if the caller asks to turn reasoning off in a situation where the provider cannot express that safely. The result is an OpenAI reasoning effort value or no value.

**Call relations**: OpenAIClient._chat_kwargs calls this when preparing a Chat Completions request. OpenAIClient._complete_responses calls it before building a Responses request. This keeps both API paths using the same reasoning rules.

*Call graph*: called by 2 (_chat_kwargs, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 347–376)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the request body for OpenAI's Chat Completions API. It gathers the translated messages, token limit, streaming options, reasoning setting, and any tool definitions.

**Data flow**: It receives a ModelRequest. It converts UFO messages with openai_messages, calculates the reasoning setting with _reasoning_effort, and adds tool definitions plus tool-choice rules when tools are available. The output is a dictionary of keyword arguments passed to the OpenAI SDK's chat completion create call.

**Call relations**: OpenAIClient._complete_chat calls this just before opening the provider stream. It is the chat-side counterpart to responses_request, focused on the older Chat Completions wire shape.

*Call graph*: calls 2 internal fn (_reasoning_effort, openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 378–533)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a streaming Chat Completions request and converts the provider's stream into UFO's standard event stream. It also applies the retry and error rules that keep temporary provider problems from leaking into the rest of the system too early.

**Data flow**: It receives a ModelRequest, builds chat request arguments, and starts an OpenAI streaming call. As chunks arrive, it yields a stream-start event, text deltas, tool-call starts, tool-call argument deltas, and finally token usage. It records finish reasons, checks usage math, retries temporary network or server problems only before visible output has been yielded, turns rejected keys into the model spec's credential error, retries a few empty completions, and raises a truncation error if the model stopped because the token budget was exhausted.

**Call relations**: OpenAIClient.complete calls this when the model spec uses the Chat Completions surface. It calls OpenAIClient._chat_kwargs for the request body, sends the request through the stored SDK client, logs and emits metrics for retries, and yields UFO events to the higher-level engine.

*Call graph*: calls 1 internal fn (_chat_kwargs); called by 1 (complete); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenAIClient._complete_responses`  (lines 535–725)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a streaming Responses API request and converts its richer event stream into UFO's standard model events. It is the Responses equivalent of the chat streaming path, with added support for replayable reasoning items.

**Data flow**: It receives a ModelRequest, resolves the reasoning effort, builds a Responses request, and starts the provider stream. Text events become text deltas; function-call events become tool-call starts and argument deltas; completed reasoning items are collected with their encrypted content; completed usage becomes UFO Usage. It raises clear errors for refusals, filtered or incomplete responses, failed responses, missing reasoning content, missing usage, truncation, bad credentials, and non-retryable provider failures. If the stream ends without visible output, it retries a few times; after a successful stream, it yields saved reasoning blocks just before final usage.

**Call relations**: OpenAIClient.complete calls this when the model spec says to use the Responses API. It calls OpenAIClient._reasoning_effort and responses_request before opening the stream, then translates OpenAI response events into the same ModelEvent language used by the rest of UFO.

*Call graph*: calls 2 internal fn (_reasoning_effort, responses_request); called by 1 (complete); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep, emit_metric (+1 more)).


### OpenRouter extension
This file registers OpenRouter as an additional provider and normalizes its chat, image, video, cost, and file-tracking behavior into UFO's model workflow.

### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `model request handling and tool execution`

OpenRouter is a service that routes one API key to many different AI model providers. This file is the adapter between that service and the rest of the project. For text chat, it makes OpenRouter look like the system’s normal model client: requests come in as the project’s standard model request, are translated into OpenAI-style chat messages, streamed back as text or tool-call events, and finally reported with token usage for billing and limits.

The file also adds two user-facing tools: generate_image and generate_video. These are kept separate from normal chat models because their costs are not token based. An image costs per image, and a video costs per output second. The tools validate requests before sending them, so the model does not ask OpenRouter for combinations a provider cannot make. They then call OpenRouter, save returned media into the workspace, and record the cost unless the workspace is using its own OpenRouter key.

A useful analogy is a travel desk: the rest of the system asks for “a model trip” or “an image trip,” and this file knows which OpenRouter counter to visit, what paperwork to fill out, how to retry bad routes, where to put the souvenirs, and how to write the receipt.

#### Function details

##### `openrouter_slug`  (lines 250–260)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Turns the project’s model name into the provider/model name OpenRouter expects. It adds an OpenAI or Anthropic prefix when a familiar bare model name is used, while leaving already-prefixed names alone.

**Data flow**: It receives a model id as text. If the id already contains a slash, it returns it unchanged; if it looks like an OpenAI or Claude model, it adds the matching provider prefix; otherwise it passes the text through. The output is the model slug sent to OpenRouter.

**Call relations**: When OpenRouterModelClient._create_kwargs builds the outgoing chat request, it calls this helper so the request uses OpenRouter’s naming style before it leaves the system.

*Call graph*: called by 1 (_create_kwargs).


##### `_chunk_provider`  (lines 263–268)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Reads which upstream provider OpenRouter used for one streamed response chunk. This matters because the client may avoid that provider if it returns an empty, useless completion.

**Data flow**: It receives one OpenAI-style streaming chunk. It looks in the chunk’s extra OpenRouter metadata for a provider name and returns that name as text, or returns nothing if no provider is named.

**Call relations**: OpenRouterModelClient.complete uses this while reading the stream. If a stream ends with no useful output, the remembered provider can be excluded on a retry.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 271–280)

```
def _usage_of(usage: CompletionUsage) -> Usage
```

**Purpose**: Converts OpenAI SDK token usage into the project’s own Usage object. It also separates cached prompt tokens from newly billed input tokens.

**Data flow**: It receives a usage report from the OpenAI-style API. It reads prompt tokens, completion tokens, and cached prompt tokens, checks that cached tokens are not more than total prompt tokens, and returns a Usage record with input, output, and cache-read counts.

**Call relations**: OpenRouterModelClient.complete calls this when the stream includes final usage information, then yields the resulting Usage object back to the main model flow.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient.complete`  (lines 299–398)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one streaming chat completion through OpenRouter and turns the result into the project’s standard model events. It also retries temporary provider failures and reroutes around providers that return an empty answer.

**Data flow**: It receives a ModelRequest containing the model, messages, tools, token limit, and reasoning preference. It builds OpenRouter request arguments, opens a streaming chat request, yields start, text, and tool-call events as chunks arrive, records usage, and finally yields usage. If the provider reports truncation, it raises a clear truncation error; if retryable errors happen before any output, it waits and tries again.

**Call relations**: This is the main chat path for OpenRouter model specs. It relies on _create_kwargs to prepare the request, _chunk_provider to identify bad upstreams, and _usage_of to translate final billing data before handing events back to the caller.

*Call graph*: calls 3 internal fn (_create_kwargs, _chunk_provider, _usage_of); 8 external calls (__init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenRouterModelClient._create_kwargs`  (lines 400–431)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Builds the exact keyword arguments sent to the OpenAI-compatible OpenRouter chat API. It is the place where project-level options become wire-level API fields.

**Data flow**: It receives a model request and a set of providers to ignore. It translates messages, converts the model id to an OpenRouter slug, adds streaming and usage options, attaches tool schemas if present, and adds OpenRouter-specific reasoning or provider-ignore settings. It returns a dictionary ready for the SDK call.

**Call relations**: OpenRouterModelClient.complete calls this right before contacting OpenRouter. It calls openrouter_slug and openai_messages so the outgoing request matches OpenRouter’s expected format.

*Call graph*: calls 1 internal fn (openrouter_slug); called by 1 (complete); 1 external calls (openai_messages).


##### `_model_client`  (lines 434–438)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: Creates an OpenRouterModelClient for a registered model spec and API key. It is the factory used by the model registry when it needs a live client.

**Data flow**: It receives a ModelSpec and an API key. It builds an OpenAI SDK client pointed at OpenRouter’s base URL, wraps it with the spec in an OpenRouterModelClient, and returns that ready-to-use client.

**Call relations**: _openrouter stores this factory inside each ModelSpec, so later model execution can create a client without knowing the construction details.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 441–458)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Creates a ModelSpec entry for one OpenRouter chat model. A ModelSpec is the project’s catalog card for a model: price, context size, key slot, and capabilities.

**Data flow**: It receives an id, price, knowledge cutoff, and optional context window. It fills in OpenRouter-specific provider settings, reasoning support, API key information, and the client factory, then returns a ModelSpec.

**Call relations**: The file uses this helper to build the OPENROUTER_MODEL_SPECS list that manifest later exposes to the extension registry.

*Call graph*: 1 external calls (__init__).


##### `GenerateImageInput._within_model_limits`  (lines 523–547)

```
def _within_model_limits(self) -> 'GenerateImageInput'
```

**Purpose**: Checks that an image-generation request is possible for the chosen model before the network call is made. This gives the agent a useful local error instead of a vague provider rejection.

**Data flow**: It reads the already-parsed image arguments: model, image count, aspect ratio, and resolution. It compares them with the allowlisted limits for that model, raises a validation error for unsupported choices, and fills in a default resolution when the model needs one. The output is the same input object, now confirmed and possibly completed.

**Call relations**: Pydantic, the validation library used for tool inputs, calls this after parsing GenerateImageInput. OpenRouterImages.generate then receives only inputs that should fit the selected model.


##### `_reported_cost_micro_usd`  (lines 555–567)

```
def _reported_cost_micro_usd(usage: object) -> int | None
```

**Purpose**: Extracts OpenRouter’s reported cost and converts it into micro-dollars, where one US dollar is one million micro-dollars. It understands both normal OpenRouter billing and bring-your-own-key upstream billing reports.

**Data flow**: It receives a usage-like object, usually a dictionary from OpenRouter. It looks first for a positive cost, then for a positive upstream inference cost, converts dollars to micro-dollars, and returns that number. If no positive cost is found, it returns nothing so the caller can use a fallback list price.

**Call relations**: Image charging calls this through OpenRouterImages._charge. Video job parsing calls it through OpenRouterVideos._job so the completed job can carry its reported cost forward.

*Call graph*: called by 2 (_charge, _job).


##### `OpenRouterImages.generate`  (lines 600–637)

```
async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Runs one complete image-generation tool call. It sends the prompt to OpenRouter, saves the returned images, records cost when appropriate, and returns both file names and inline image content to the agent.

**Data flow**: It receives a tool context and validated image arguments. It gets the OpenRouter key, posts the request, turns provider errors into tool errors, decodes returned images, writes them into the workspace, calculates cost, and meters the image usage unless the workspace supplied its own key. The result is a ToolResult containing JSON file information plus the generated images.

**Call relations**: The tool wrapper _generate_image constructs OpenRouterImages and calls this. Inside, this method delegates refusal wording, response decoding, saving, and charging to smaller helpers, then returns the finished tool result to the agent loop.

*Call graph*: calls 5 internal fn (meter_images, _charge, _images, _refusal, _save); 6 external calls (__init__, __init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterImages._refusal`  (lines 639–654)

```
def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str
```

**Purpose**: Turns a failed image API response into a short, readable error message. This helps the agent understand whether to change the prompt, parameters, or account setup.

**Data flow**: It receives the original image arguments and the HTTP response. It tries to read a JSON error message; if that is not available, it uses the response text, trims it to a safe length, and returns a sentence saying the model generated no image.

**Call relations**: OpenRouterImages.generate calls this when OpenRouter returns an error status, then places the returned text into an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterImages._images`  (lines 656–686)

```
def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]
```

**Purpose**: Pulls usable images out of OpenRouter’s response. It refuses responses with no image data or images that are too large to save safely.

**Data flow**: It receives the image arguments and the decoded response body. It scans the response data list for base64 image strings, decodes each one to bytes, checks the byte size, assigns a media type such as image/png when missing, and returns GeneratedImage records. If nothing usable is found, it raises an image error.

**Call relations**: OpenRouterImages.generate calls this after a successful HTTP response and before saving. Its output is passed to _save and also included as ImageContent in the tool result.

*Call graph*: called by 1 (generate); 3 external calls (__init__, __init__, b64decode).


##### `OpenRouterImages._save`  (lines 688–695)

```
async def _save(self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage) -> str
```

**Purpose**: Writes one generated image file into the workspace. It chooses a file extension from the image’s media type.

**Data flow**: It receives the tool context, the original image arguments, an index number, and one GeneratedImage. It builds a path like generated-images/name-1.png, writes the raw bytes through the sandbox file API, and returns the saved path.

**Call relations**: OpenRouterImages.generate calls this once for each returned image after _images has decoded and checked them.

*Call graph*: called by 1 (generate).


##### `OpenRouterImages._charge`  (lines 697–704)

```
def _charge(self, body: object, args: GenerateImageInput, images: int) -> int
```

**Purpose**: Decides how much an image-generation call cost. It prefers OpenRouter’s actual reported charge and falls back to the model’s listed per-image price.

**Data flow**: It receives the response body, the image arguments, and the number of images returned. It reads usage cost through _reported_cost_micro_usd; if a real cost is present, it returns that. Otherwise it multiplies the chosen model’s list price by the image count.

**Call relations**: OpenRouterImages.generate calls this after images are saved, then uses the returned amount for workspace metering and for the JSON summary shown to the agent.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 1 (generate).


##### `_generate_image`  (lines 707–712)

```
async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: This is the registered handler behind the generate_image tool. It connects the generic tool system to the OpenRouterImages implementation.

**Data flow**: It receives the tool context and parsed image arguments. It checks that extension context is available, creates an OpenRouterImages helper with credentials and optional test transport, and returns that helper’s generate result.

**Call relations**: GENERATE_IMAGE_TOOL points to this function as its handler. When an agent calls generate_image, the tool system enters here and this function hands the work to OpenRouterImages.generate.

*Call graph*: 1 external calls (__init__).


##### `GenerateVideoInput._within_model_limits`  (lines 769–793)

```
def _within_model_limits(self) -> 'GenerateVideoInput'
```

**Purpose**: Checks that a video-generation request fits the chosen video model. It catches unsupported durations, aspect ratios, and resolutions before sending anything to OpenRouter.

**Data flow**: It reads the parsed video arguments: model, duration, aspect ratio, and resolution. It compares them with the model’s limits, raises a validation error for unsupported settings, and fills in the model’s default resolution when none was provided. The returned object is the validated and completed request.

**Call relations**: Pydantic calls this after parsing GenerateVideoInput. OpenRouterVideos.generate then works with arguments that already match the allowlisted model capabilities.


##### `OpenRouterVideos.generate`  (lines 835–876)

```
async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Runs one complete video-generation tool call. Because video generation takes time, it starts a job, waits for it to finish, downloads the MP4, saves it, records cost when appropriate, and returns the saved file path.

**Data flow**: It receives a tool context and validated video arguments. It gets the OpenRouter key, posts a video job request, turns immediate API errors into tool errors, parses the job, polls until it settles, reports failed jobs as tool errors, downloads completed video bytes, saves them into the workspace, calculates cost, and meters the video unless the workspace supplied its own key. The output is a ToolResult with JSON describing the generated file and cost.

**Call relations**: The tool wrapper _generate_video constructs OpenRouterVideos and calls this. This method coordinates the smaller helpers for refusal messages, job parsing, polling, failure text, downloading, saving, and charging.

*Call graph*: calls 8 internal fn (meter_videos, _charge, _download, _failure, _job, _refusal, _save, _settled); 5 external calls (__init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterVideos._refusal`  (lines 878–893)

```
def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str
```

**Purpose**: Turns an immediate failed video API response into a short message the agent can act on. It preserves the provider’s reason when available.

**Data flow**: It receives the video arguments and the HTTP response. It tries to read a JSON error message, otherwise falls back to response text, trims it, and returns a sentence saying no video was generated.

**Call relations**: OpenRouterVideos.generate calls this when the initial video request is rejected before a job starts, then returns the text in an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterVideos._job`  (lines 895–910)

```
def _job(self, body: object) -> VideoJob
```

**Purpose**: Parses OpenRouter’s video job information into a small local record. A job record tells the tool what to poll, whether it is done, why it failed, and what it cost if known.

**Data flow**: It receives a decoded response body from either the initial job creation or a later poll. It requires an id and status, extracts an optional error message, reads any reported cost, and returns a VideoJob. If the body cannot identify a job, it raises a video error.

**Call relations**: OpenRouterVideos.generate calls this for the first accepted response. OpenRouterVideos._settled calls it again for each poll response so the latest job status replaces the old one.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 2 (_settled, generate); 2 external calls (__init__, __init__).


##### `OpenRouterVideos._settled`  (lines 912–933)

```
async def _settled(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> VideoJob
```

**Purpose**: Waits for an OpenRouter video job to stop being pending or in progress. It keeps polling, but only up to a fixed timeout so a turn is not held open forever.

**Data flow**: It receives the video arguments, an HTTP client, and the current VideoJob. While the status is still pending or in progress, it checks the deadline, sleeps for the poll interval, requests the latest job state, and parses it. It returns the final job when the status is no longer pending, or raises an error if polling fails or takes too long.

**Call relations**: OpenRouterVideos.generate calls this after the initial job is accepted. It calls _job to interpret each poll response before handing the settled job back to the main video flow.

*Call graph*: calls 1 internal fn (_job); called by 1 (generate); 4 external calls (__init__, sleep, get, monotonic).


##### `OpenRouterVideos._failure`  (lines 935–939)

```
def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str
```

**Purpose**: Creates the error text for a video job that was accepted but did not complete. It gives the agent the provider’s reason when one exists.

**Data flow**: It receives the original video arguments and the final VideoJob. It chooses the job’s error message if present, otherwise describes the final status, trims the detail, and returns a sentence saying no video was generated.

**Call relations**: OpenRouterVideos.generate calls this after _settled returns a job whose status is not completed, then returns the message as an error ToolResult.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._download`  (lines 941–960)

```
async def _download(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> bytes
```

**Purpose**: Downloads the finished video file and checks that it is safe to save. It refuses empty or oversized downloads.

**Data flow**: It receives the video arguments, HTTP client, and completed job. It requests the job’s video content, raises a video error for HTTP failures, reads the response bytes, checks that bytes exist and are under the size limit, and returns the raw MP4 bytes.

**Call relations**: OpenRouterVideos.generate calls this only after _settled reports a completed job. The returned bytes are then passed to _save.

*Call graph*: called by 1 (generate); 2 external calls (__init__, get).


##### `OpenRouterVideos._save`  (lines 962–966)

```
async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str
```

**Purpose**: Writes the downloaded MP4 video into the workspace. It uses the requested name and the standard generated-videos folder.

**Data flow**: It receives the tool context, video arguments, and raw video bytes. It builds a path like generated-videos/name.mp4, writes the bytes through the sandbox file API, and returns the saved path.

**Call relations**: OpenRouterVideos.generate calls this after _download returns valid video bytes, then includes the path in the tool result.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._charge`  (lines 968–976)

```
def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int
```

**Purpose**: Decides how much a video-generation job cost. It prefers OpenRouter’s reported charge and otherwise estimates from the selected model, resolution, and duration.

**Data flow**: It receives the video arguments and final VideoJob. If the job already carries a reported cost, it returns that. Otherwise it looks up the per-second price for the chosen model and resolution, multiplies by the requested duration, and returns the cost in micro-dollars.

**Call relations**: OpenRouterVideos.generate calls this after saving the video, then uses the amount for workspace metering and for the JSON summary returned to the agent.

*Call graph*: called by 1 (generate).


##### `_generate_video`  (lines 979–984)

```
async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: This is the registered handler behind the generate_video tool. It connects the generic tool system to the OpenRouterVideos implementation.

**Data flow**: It receives the tool context and parsed video arguments. It checks that extension context is available, creates an OpenRouterVideos helper with credentials and optional test transport, and returns that helper’s generate result.

**Call relations**: GENERATE_VIDEO_TOOL points to this function as its handler. When an agent calls generate_video, the tool system enters here and this function hands the work to OpenRouterVideos.generate.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 996–1012)

```
def manifest() -> Manifest
```

**Purpose**: Declares what this extension contributes to the host system: its name and version, its OpenRouter chat models, its image and video tools, and the credential slot for the OpenRouter API key.

**Data flow**: It takes no input. It builds a Manifest containing the model specs, tool definitions, and credential description, then returns it to the extension loader.

**Call relations**: The host calls this when loading the extension. The returned manifest is how the registry learns that OpenRouter models and the generate_image and generate_video tools exist.

*Call graph*: 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-model-catalog` — The shared catalog of AI models, providers, routing rules, reasoning modes, key lookup rules, and usage shapes.
- `reg-usage-ledger` — The shared cost and usage records for model calls, tools, sandboxes, connectors, network use, and generated media.
- `reg-spend-controls` — The spend caps, prepaid balances, price table fingerprints, and checks that decide whether work may continue.
- `reg-observability` — The shared logs, traces, metrics, trace links, and sanitized diagnostic records used to understand system behavior.
- `reg-prompt-context-budget` — The per-turn context-window and token-allocation state used to pack history, compact old context, reserve output/reasoning room, and pass normalized usage expectations to model calls.
