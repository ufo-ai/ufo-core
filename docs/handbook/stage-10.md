# Model Invocation, Streaming, and Spend Admission  `stage-10`

This stage is part of the main work loop, when UFO needs to ask a language model for the next answer or action. It is the system’s “translator and cashier.” It checks that a model can be used, prepares the request in the format each provider expects, streams the reply back in UFO’s own common event format, and records usage so cost can be calculated.

The models package marker simply makes this model code importable. The Anthropic bridge talks to Anthropic’s API and hides Anthropic-only details such as special content blocks, retry behavior, and usage reporting. The OpenAI bridge does the same for OpenAI-style services, including chat messages, tools, images, reasoning data, and streamed replies. The pricing code turns token counts into billable cost and records which price table was used.

OpenRouter is added as an extension provider, including image and video generation with proper saving and billing. The self-improvement model adapter gives that extension a small, safe way to call models. Its proposer asks for better agent prompts after failures, and its replay code tests a new prompt against an old conversation without rerunning tools.

## Files in this stage

### Core model transports
Built-in model package entry points and provider bridges translate Anthropic and OpenAI-style requests and streams into UFO's common model interface.

### `core/src/ufo/models/__init__.py`

`data_model` · `import time`

This is an empty package marker file. In Python, a folder can act as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to this folder using import paths such as `ufo.models`, instead of treating it as just a plain directory on disk.

Because the file is empty, it does not create any classes, functions, settings, or side effects. Its job is structural: it gives the project a stable place for model-related code to live. You can think of it like a label on a filing cabinet drawer. The label does not contain the documents, but it tells the rest of the system that this drawer exists and can be opened by name.

Without this file, depending on the Python version and packaging setup, imports that expect `ufo.models` to be a regular package might fail or behave differently. Keeping it here makes the package layout explicit and easier for both tools and developers to understand.


### `core/src/ufo/models/anthropic.py`

`io_transport` · `request handling`

This file is the Anthropic adapter for the model layer. The rest of the project wants to ask a model for an answer and receive a steady stream of simple events: text, tool calls, hidden reasoning records, and final token usage. Anthropic’s API speaks its own format, so this file acts like a translator at the border.

Before sending a request, it converts the project’s message blocks into Anthropic content blocks. Text stays text, images become Anthropic base64 image objects, tool calls and tool results are reshaped, and reasoning from other providers is dropped because Anthropic cannot safely reuse it.

The main class, `AnthropicClient`, opens a streaming request to Anthropic. As chunks arrive, it yields project events such as `TextDelta` for visible text and `ToolCallStart` or `ToolCallDelta` when the model asks to use a tool. It holds Anthropic “thinking” blocks until the stream is finished, because those blocks must be echoed back later in their exact order if tool results continue the same reasoning round.

The file also owns retry behavior. Temporary network failures, timeouts, overloads, and retryable status errors are tried again before any visible output has been yielded. Once the user-visible answer has started, errors are no longer retried because mixing two attempts would be unsafe. It always finishes a successful stream with usage information so callers know token costs.

#### Function details

##### `anthropic_sdk_client`  (lines 50–54)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the low-level Anthropic software client used to call the provider. It deliberately turns off the SDK’s built-in retries so this file’s own retry rules are the single source of truth.

**Data flow**: It receives an API key → builds an asynchronous Anthropic client with that key, a fixed timeout, and no automatic SDK retries → returns the ready-to-use client object.

**Call relations**: This is the setup helper for code that needs an Anthropic connection. It hands back the raw provider client that `AnthropicClient` stores and later uses when `complete` sends a streaming request.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 57–61)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts the project’s image description into the image shape Anthropic expects. This keeps image formatting in one small place instead of repeating it wherever images may appear.

**Data flow**: It receives an `ImageSource`, which contains the image media type and base64 data → wraps those fields in Anthropic’s required dictionary structure → returns that dictionary for inclusion in a request.

**Call relations**: It is used whenever higher-level conversion code finds an image. `_anthropic_tool_result_part` calls it for images inside tool results, and `anthropic_content` calls it for images in normal message content.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 64–69)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Turns one piece of a tool result into Anthropic’s request format. A tool result can contain plain text or an image, and this function converts either case.

**Data flow**: It receives one tool-result content block → if it is text, it returns an Anthropic text dictionary; if it is an image, it delegates the image wrapping to `_anthropic_image` → the converted part is ready to be placed inside an Anthropic tool result.

**Call relations**: It works as a helper inside `anthropic_content`. When a message includes a structured tool result made of multiple parts, `anthropic_content` calls this function for each part before sending the request.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 72–106)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Translates the project’s standard message content into the exact content format accepted by Anthropic. This is the main request-body translator for text, images, tool use, tool results, and Anthropic reasoning blocks.

**Data flow**: It receives either a plain string or a tuple of project content blocks → plain strings pass through unchanged; structured blocks are inspected one by one and converted into Anthropic dictionaries → it returns either the original string or a list of Anthropic-ready content blocks. It drops reasoning blocks that belong to another provider’s wire format, because Anthropic cannot reuse them.

**Call relations**: `AnthropicClient.complete` calls this while building the message list for Anthropic. During that conversion, this function calls `_anthropic_image` for normal image blocks and `_anthropic_tool_result_part` for image or text pieces inside tool results.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 114–442)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to Anthropic and streams back the project’s common model events. It is responsible for translating the request, interpreting Anthropic’s stream, retrying safe failures, preserving reasoning blocks, and ending with token usage.

**Data flow**: It receives a `ModelRequest` containing the model name, system prompt, conversation messages, tool definitions, token limit, caching settings, and reasoning preference → builds Anthropic request arguments, including converted messages and optional tool or reasoning settings → opens a streaming API call → converts each incoming Anthropic stream event into project events such as stream-start, text deltas, tool-call starts, tool-call JSON deltas, hidden reasoning blocks, and final usage. It may raise clear project errors if the model response is truncated, refused, missing usage, or if the provider failure cannot safely be retried.

**Call relations**: This is the central flow of the file. It calls `anthropic_content` before sending messages so Anthropic receives the right shape. While the stream runs, it creates and yields the project’s event objects for the rest of the engine to consume. If Anthropic reports temporary trouble before any visible output, it waits and tries again; if output has already begun, it lets the error surface so the caller does not accidentally combine two different model attempts.

*Call graph*: calls 1 internal fn (anthropic_content); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep (+3 more)).


### `core/src/ufo/models/openai.py`

`io_transport` · `request handling during streamed model calls`

This file solves a practical translation problem. The rest of the project speaks in its own common model language: messages can contain text, images, tool calls, tool results, and sometimes saved reasoning. OpenAI-style providers expect that information in very specific request formats, and they stream back many small events. This file converts both directions.

The main class, OpenAIClient, chooses which OpenAI API shape to use based on the model’s specification, not by guessing from the model name. That matters because some models only accept certain combinations, such as tools plus reasoning, on the newer Responses API. Think of it like using the right shipping label for each carrier: the package may contain the same conversation, but the label format must match the service.

The helper functions build request payloads for chat or responses, encode images as data URLs, split tool results into text and image parts when OpenAI requires it, and calculate token usage for billing or tracking. The streaming methods then call the provider, yield UFO’s standard ModelEvent objects as text or tool-call pieces arrive, and finish with usage information. They also retry temporary failures before any visible output has been delivered, respect provider retry-after hints, turn rejected API keys into clearer project errors, and raise explicit errors for truncation or refusal.

#### Function details

##### `_cache_write_tokens`  (lines 93–101)

```
def _cache_write_tokens(details: PromptTokensDetails | InputTokensDetails | None) -> int
```

**Purpose**: This helper reads a provider-specific token count for prompt tokens newly written into the cache. It protects the rest of the code from missing or badly typed provider metadata.

**Data flow**: It receives token-detail metadata from OpenAI-style usage records. If the extra field is absent, it returns 0; if the field exists and is a real integer, it returns that number; if the value is not an integer, it raises an error so bad billing data is not silently accepted.

**Call relations**: The chat streaming path uses it while converting Chat Completions usage into UFO’s Usage record. _responses_usage also calls it so both OpenAI API surfaces count cache-write tokens in the same cautious way.

*Call graph*: called by 2 (_complete_chat, _responses_usage).


##### `_responses_usage`  (lines 104–118)

```
def _responses_usage(raw: ResponseUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: This converts OpenAI Responses API usage data into UFO’s standard Usage object. It separates normal input tokens, cached input tokens, output tokens, and optionally cache-write tokens.

**Data flow**: It receives a raw Responses API usage object and a flag saying whether 30-minute cache writes should be priced. It reads cached tokens and cache-write tokens, checks that those numbers do not exceed the total input tokens, then returns a Usage record with the totals split into UFO’s categories.

**Call relations**: OpenAIClient._complete_responses calls this whenever the Responses stream reports usage. It relies on _cache_write_tokens for provider-specific cache-write metadata and then hands the normalized Usage event back to the model engine.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 1 (_complete_responses); 1 external calls (__init__).


##### `openai_sdk_client`  (lines 121–127)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: This creates the underlying asynchronous OpenAI SDK client used to talk to OpenAI or an OpenAI-compatible provider. It disables the SDK’s own retries because this file has its own retry rules for streaming calls.

**Data flow**: It receives an API key and, optionally, a custom base URL for another provider that speaks the OpenAI protocol. It returns an openai.AsyncOpenAI client configured with a timeout and no built-in SDK retries.

**Call relations**: This is a construction helper used when setting up an OpenAIClient. The returned SDK client is later used by OpenAIClient._complete_chat and OpenAIClient._complete_responses to make actual provider requests.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_status_retry_wait`  (lines 130–136)

```
def _status_retry_wait(error: openai.APIStatusError, delay: float) -> float
```

**Purpose**: This decides how long to wait after an HTTP status error before retrying. It respects the provider’s retry-after header when present, but never waits less than the current backoff delay.

**Data flow**: It receives an API status error and the current retry delay. It tries to read the response’s retry-after header as a number of seconds, falls back to the existing delay if it is missing or invalid, and returns the larger wait time.

**Call relations**: Both streaming paths call this after retryable status errors such as rate limits or server errors. It feeds the sleep time used before the next provider attempt.

*Call graph*: called by 2 (_complete_chat, _complete_responses).


##### `_openai_image`  (lines 139–143)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: This turns UFO’s image data into the image-url format expected by OpenAI chat messages. The image is embedded directly as a base64 data URL.

**Data flow**: It receives an ImageSource containing a media type and base64 image data. It returns a small dictionary saying this is an image URL and placing the image data inside a data URL string.

**Call relations**: openai_messages uses it for ordinary image blocks in chat messages. _openai_tool_result uses it when a tool result contains images that must be lifted into a user message.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 146–162)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: This prepares a tool result for OpenAI’s Chat Completions format, where tool messages can carry text but not images. It separates text from images so the caller can send the images in a follow-up user message.

**Data flow**: It receives either a plain string tool result or a tuple of text and image blocks. For a string, it returns that text and no images. For blocks, it joins all text pieces with newlines and converts each image through _openai_image, returning both the text and the image parts.

**Call relations**: openai_messages calls this while translating UFO ToolResultBlock objects into chat messages. The text becomes the OpenAI tool message, while any images are handed back so openai_messages can place them where Chat Completions accepts images.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 165–224)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: This converts UFO’s conversation history into the message list required by OpenAI’s Chat Completions API. It keeps text, images, tool calls, and tool results, while dropping reasoning records because this API shape has no place to replay them.

**Data flow**: It receives the system prompt and a tuple of UFO Message objects. It trims images where needed, walks through each message, converts text and images into OpenAI content, serializes tool-call arguments as JSON, turns tool results into OpenAI tool messages, and returns a list of dictionaries ready for the chat completion request.

**Call relations**: OpenAIClient._chat_kwargs calls this when building the request payload for the Chat Completions path. It uses _openai_image and _openai_tool_result to keep image and tool-result conversion consistent.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 227–328)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: This converts UFO’s conversation history into the richer input-item format used by OpenAI’s Responses API. Unlike Chat Completions, this format can carry saved reasoning items, which helps the model resume a tool-using reasoning chain later.

**Data flow**: It receives UFO messages. It walks through text, images, reasoning items, tool calls, and tool outputs; drops reasoning formats from other providers that OpenAI cannot use; encodes tool arguments as JSON; prefixes errored tool text with a clear marker; and returns a list of Responses API input items.

**Call relations**: responses_request calls this when assembling a Responses API request. It is the main adapter that lets OpenAIClient._complete_responses send UFO’s full conversation state, including reasoning records, back to the provider.

*Call graph*: called by 1 (responses_request); 13 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, ResponseOutputTextParam (+3 more)).


##### `responses_request`  (lines 331–363)

```
def responses_request(request: ModelRequest, effort: OpenAIEffort) -> dict[str, Any]
```

**Purpose**: This builds the full request dictionary for OpenAI’s Responses API. It combines the model name, instructions, converted input, token limit, streaming setting, reasoning effort, and tool definitions.

**Data flow**: It receives a ModelRequest and the already-decided reasoning effort. It converts messages with responses_input, adds streaming and usage-related options, asks the provider to include encrypted reasoning content, adds tools and tool-choice rules when present, and returns keyword arguments for the SDK call.

**Call relations**: OpenAIClient._complete_responses calls this just before starting a Responses stream. It hands the SDK a legal provider-specific request while keeping the rest of the project insulated from that wire format.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `OpenAIClient.complete`  (lines 375–378)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the public entry point for asking this client to complete a model request. It chooses the correct OpenAI API surface for the model.

**Data flow**: It receives a ModelRequest. It checks the model specification’s api_surface field and returns the asynchronous stream from either the Responses path or the Chat Completions path.

**Call relations**: Callers use this instead of calling the private streaming methods directly. It dispatches to OpenAIClient._complete_responses for models configured for the Responses API, otherwise to OpenAIClient._complete_chat.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._reasoning_effort`  (lines 380–399)

```
def _reasoning_effort(self, request: ModelRequest) -> OpenAIEffort
```

**Purpose**: This decides what reasoning-effort setting, if any, should be sent to the provider. It also prevents unsafe cases where the caller asked to turn reasoning off but the model/provider combination cannot express that with tools.

**Data flow**: It reads the request’s desired reasoning mode, tool presence, and the model specification’s reasoning rules. It returns an OpenAI-compatible effort value, returns None when no parameter should be sent, translates UFO’s 'off' into OpenAI’s 'none', or raises an error when the request cannot be represented safely.

**Call relations**: OpenAIClient._chat_kwargs uses this before building a Chat Completions request. OpenAIClient._complete_responses uses it before building a Responses request, so both API paths apply the same reasoning rules.

*Call graph*: called by 2 (_chat_kwargs, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 401–430)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: This builds the keyword arguments for a Chat Completions streaming request. It packages the conversation, token limit, reasoning effort, and tool definitions in the exact shape the chat API expects.

**Data flow**: It receives a ModelRequest. It converts messages with openai_messages, asks _reasoning_effort whether to include a reasoning setting, translates UFO tool definitions into OpenAI function tools, applies tool-choice rules, and returns a dictionary for the SDK call.

**Call relations**: OpenAIClient._complete_chat calls this immediately before contacting the provider. It is the chat-side counterpart to responses_request.

*Call graph*: calls 2 internal fn (_reasoning_effort, openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 432–601)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This streams a response from OpenAI’s Chat Completions API and converts provider chunks into UFO ModelEvent objects. It also handles retries, usage accounting, truncation, empty responses, and key rejection errors.

**Data flow**: It receives a ModelRequest, builds provider arguments with _chat_kwargs, starts a streaming SDK request, and reads chunks as they arrive. Text chunks become TextDelta events, new tool calls become ToolCallStart events, tool-call argument pieces become ToolCallDelta events, and usage chunks become a final Usage event. Temporary failures before visible output are retried with backoff; failures after output are raised to avoid mixing partial attempts.

**Call relations**: OpenAIClient.complete calls this for models using the Chat Completions surface. Inside the stream it uses _cache_write_tokens to normalize usage and _status_retry_wait to honor retry timing from the provider.

*Call graph*: calls 3 internal fn (_chat_kwargs, _cache_write_tokens, _status_retry_wait); called by 1 (complete); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenAIClient._complete_responses`  (lines 603–801)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This streams a response from OpenAI’s Responses API and converts its richer event stream into UFO ModelEvent objects. It supports text, tool calls, refusals, truncation, usage, and saved reasoning items.

**Data flow**: It receives a ModelRequest, resolves reasoning effort, builds the request with responses_request, then reads provider events. Text deltas become TextDelta events, function-call events become ToolCallStart and ToolCallDelta events, completed reasoning items are saved and yielded near the end, and usage is normalized with _responses_usage. Retryable provider failures are retried only before visible output; terminal provider events become clear UFO errors such as ModelRefusal or ModelResponseTruncated.

**Call relations**: OpenAIClient.complete calls this for models whose specification selects the Responses API. It uses responses_request for outbound formatting, _responses_usage for accounting, and _status_retry_wait for status-error retry timing.

*Call graph*: calls 4 internal fn (_reasoning_effort, _responses_usage, _status_retry_wait, responses_request); called by 1 (complete); 10 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


### Usage pricing
Cost accounting converts provider usage into billable records tied to a stable pricing table fingerprint.

### `core/src/ufo/models/pricing.py`

`domain_logic` · `billing and usage recording`

This file is the project’s price list and calculator for language model usage. Models charge different rates for different kinds of tokens, such as input tokens, output tokens, and cached tokens. This file gives those rates a clear shape with `ModelPrice`, then uses them to calculate costs in micro-USD, meaning millionths of a US dollar.

The main idea is simple: usage records say how many tokens were used, and a price table says how much each kind of token costs per million tokens. The calculator multiplies each token count by its matching rate, adds everything together, then divides by one million to get the final micro-dollar cost. This is like a grocery receipt: each item category has a quantity and a unit price, and the total is the sum of all category totals.

The file also creates a digest, which is a cryptographic fingerprint of the price table. That matters because prices can change over time. By stamping billed usage with this digest, the system can later prove which exact price table was used. If a historical usage record refers to a model that is no longer in the table, the code logs a warning and returns zero instead of crashing.

#### Function details

##### `price_digest`  (lines 26–43)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable version stamp for a model price table. This lets billing records point back to the exact set of rates that were used, even if the table changes later.

**Data flow**: It receives a mapping from model names to `ModelPrice` values. It sorts the models, turns their rates into a compact JSON string, hashes that string with SHA-256, and returns the hash prefixed with `sha256:`. The original price table is not changed.

**Call relations**: When `pricing_from` builds a `Pricing` object, it calls this function to attach a trustworthy fingerprint to the copied price table. Internally, this function relies on JSON formatting and SHA-256 hashing to make the fingerprint deterministic.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 46–60)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one usage record for one model. It returns the cost in micro-USD, which is a very small money unit useful for precise billing.

**Data flow**: It receives a model name, a `Usage` record containing token counts, and the price table. It looks up the model’s prices, multiplies each token count by the matching rate, adds the pieces together, and divides by one million because the rates are per million tokens. If the model is missing from the table, it writes a warning log and returns zero.

**Call relations**: This is the real pricing calculator used by `Pricing.micro_usd`. It is kept as a separate function so the `Pricing` class can be a small wrapper around a price table while the arithmetic stays in one clear place.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 70–71)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the normal method callers use to price a usage record with this `Pricing` object’s table. It hides the detail of passing the price table around.

**Data flow**: It receives a model name and a `Usage` record. It forwards those, along with the `Pricing` object’s stored price table, to `usage_priced_micro_usd`, then returns the calculated micro-USD cost.

**Call relations**: Billing code calls this method when recording sandbox, turn, or workspace usage. In that larger flow, the accounting layer has token usage to record, and this method supplies the money amount using the current pricing table.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 74–77)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete immutable-looking `Pricing` value from a plain model price table. It pairs the table with its digest so later billing records can identify the exact rates used.

**Data flow**: It receives a mapping of model names to `ModelPrice` entries. It copies that mapping into a regular dictionary, computes a digest for the copied table, and returns a new `Pricing` object containing both the copied prices and the digest.

**Call relations**: This is the construction point for pricing data in this file. It calls `price_digest` before creating `Pricing`, so every `Pricing` object is born with both usable rates and a version stamp.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### Extension model adapters
Extension adapters expose additional model access paths, including OpenRouter-backed media-capable models and a constrained self-improvement model facade.

### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `request handling`

OpenRouter is a service that routes one request to many possible AI providers. This file is the adapter that makes that service fit UFO's own model and tool system. For chat models, it translates UFO's internal request format into OpenRouter's OpenAI-style chat format, streams text and tool calls back as UFO events, records token usage, and retries when OpenRouter or an upstream provider has a temporary failure. It also knows small OpenRouter-specific quirks, such as adding provider prefixes to model names and wrapping some JSON tool results for Google models when OpenRouter would otherwise reject them.

The file also defines two side-effecting tools: `generate_image` and `generate_video`. These are not registered as chat models, because they produce files and are priced per image or per video second, not per token. Each tool checks that the requested model, size, count, and duration are actually supported before making the API call. Successful images or videos are written into the workspace, and the cost is attached to the current turn unless the workspace supplied its own OpenRouter key. In short, this file is both the bridge to OpenRouter's chat models and the safe wrapper around OpenRouter's media-generation APIs.

#### Function details

##### `openrouter_slug`  (lines 260–270)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Turns a model name into the provider/model name style OpenRouter expects. This lets callers use familiar bare names like an OpenAI or Claude model while still sending a valid OpenRouter request.

**Data flow**: It receives a model string. If the string already contains a slash, it leaves it alone; if it looks like an OpenAI or Anthropic model, it adds the matching provider prefix; otherwise it passes the name through unchanged. The output is the model slug used in OpenRouter API calls.

**Call relations**: When a chat request is being prepared, `OpenRouterModelClient._create_kwargs` uses this to choose the wire model name. `_openrouter_messages` also uses it to detect Google-routed models, because those need special message cleanup.

*Call graph*: called by 2 (_create_kwargs, _openrouter_messages).


##### `_chunk_provider`  (lines 273–278)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Extracts the actual upstream provider name from a streamed OpenRouter response chunk when OpenRouter includes it. This matters because a provider that returns an empty answer can be avoided on a retry.

**Data flow**: It receives one streaming chat chunk from the OpenAI-style SDK. It looks in the chunk's extra metadata for a `provider` value and returns it as text, or returns nothing if the value is missing.

**Call relations**: `OpenRouterModelClient.complete` calls this while reading the stream. If the answer later turns out to be empty, that provider name can be added to the ignore list for the next attempt.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 281–302)

```
def _usage_of(usage: CompletionUsage, cache_write_30m_rate: int) -> Usage
```

**Purpose**: Converts OpenRouter/OpenAI token usage into UFO's own usage record. It carefully separates normal input tokens, generated output tokens, cached reads, and cache writes so billing can be accurate.

**Data flow**: It receives the SDK's usage object and the model's cache-write price setting. It reads prompt tokens, completion tokens, cached tokens, and any reported cache-write tokens, checks that the counts make sense, and returns a `Usage` object. If OpenRouter reports impossible token counts, it raises an error instead of silently billing bad data.

**Call relations**: `OpenRouterModelClient.complete` calls this when usage arrives inside the model stream. The resulting `Usage` event is yielded back to the main model loop so the turn can be accounted for.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `_contains_json_reference`  (lines 317–329)

```
def _contains_json_reference(value: object) -> bool
```

**Purpose**: Checks whether a nested JSON-like value contains schema reference keys such as `$ref`. This is used to avoid a known OpenRouter problem with Google tool-result messages.

**Data flow**: It receives any Python value, such as a dictionary, list, string, or number. It walks through dictionaries and lists looking for `$ref` or `$dynamicRef`. It returns true as soon as it finds one, otherwise false.

**Call relations**: `_openrouter_messages` calls this after parsing a tool result as JSON. If a reference is found, the message is wrapped as plain text before being sent to OpenRouter.

*Call graph*: called by 1 (_openrouter_messages).


##### `_openrouter_messages`  (lines 332–369)

```
def _openrouter_messages(model: str, system: str, messages: tuple[Message, ...], accepts_image_input: bool) -> list[dict[str, object]]
```

**Purpose**: Builds the message list that OpenRouter should receive for a chat request. It also applies OpenRouter-specific cleanup so models that cannot see images or Google-routed models with fragile JSON handling still get usable input.

**Data flow**: It receives the model name, system prompt, prior conversation messages, and whether the model accepts image input. It may remove images, converts UFO messages into OpenAI-style messages, and for Google models wraps certain JSON tool results as text if they contain schema references. The output is a list of dictionaries ready for the OpenRouter chat API.

**Call relations**: `OpenRouterModelClient._create_kwargs` calls this while assembling the API request. It relies on `openrouter_slug` to recognize Google slugs and on `_contains_json_reference` to decide which tool-result messages need protective wrapping.

*Call graph*: calls 2 internal fn (_contains_json_reference, openrouter_slug); called by 1 (_create_kwargs); 4 external calls (dumps, loads, omit_images, openai_messages).


##### `OpenRouterModelClient.complete`  (lines 390–498)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one streaming chat completion through OpenRouter and yields UFO model events as they arrive. It is the main chat bridge: text, tool calls, usage, retries, and truncation errors all pass through here.

**Data flow**: It receives a `ModelRequest` containing the prompt, messages, tools, token limit, and reasoning choice. It builds API arguments, opens a streaming OpenRouter request, translates stream chunks into start, text, tool-call, and usage events, and yields those events to the caller. It may retry temporary provider errors before any output appears, reroute around a provider that returns an empty response, or raise an error if the model hit the token limit.

**Call relations**: The model runtime calls this when an agent uses an OpenRouter-backed chat model. Inside, it asks `_create_kwargs` to prepare the request, `_chunk_provider` to identify the routed provider, `_usage_of` to translate usage, and `_generation_usage` as a fallback when usage was not included in the stream.

*Call graph*: calls 4 internal fn (_create_kwargs, _generation_usage, _chunk_provider, _usage_of); 8 external calls (__init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenRouterModelClient._generation_usage`  (lines 500–525)

```
async def _generation_usage(self, generation_id: str, finish_reason: str) -> Usage | None
```

**Purpose**: Fetches token usage from OpenRouter's generation lookup endpoint when the streaming response did not include usage. This is a backup path for keeping billing accurate.

**Data flow**: It receives a generation id and the finish reason seen in the stream. It makes a separate OpenRouter API request, validates the returned generation data, checks cached token counts, emits a metric, and returns a UFO `Usage` object. If the generation was cancelled or does not match the stream's finish reason, it returns nothing.

**Call relations**: `OpenRouterModelClient.complete` calls this only after a stream ends without usage but has enough information to look up the generation. The usage it returns is then yielded like normal stream usage.

*Call graph*: called by 1 (complete); 3 external calls (__init__, AsyncClient, emit_metric).


##### `OpenRouterModelClient._create_kwargs`  (lines 527–563)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Assembles the keyword arguments for OpenRouter's chat-completions API. It is where UFO's request settings become the exact payload the OpenAI-style SDK sends.

**Data flow**: It receives a `ModelRequest` and a set of providers to avoid. It chooses the OpenRouter model slug, renders messages, adds token and streaming options, includes reasoning settings when needed, includes provider-ignore settings for reroutes, and converts UFO tool definitions into OpenAI function-tool definitions. The output is a dictionary passed directly to the SDK.

**Call relations**: `OpenRouterModelClient.complete` calls this at the start of each attempt. It delegates model-name conversion to `openrouter_slug` and message conversion to `_openrouter_messages`.

*Call graph*: calls 2 internal fn (_openrouter_messages, openrouter_slug); called by 1 (complete).


##### `_model_client`  (lines 566–571)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: Creates an `OpenRouterModelClient` for one registered model spec and API key. It is the factory the model registry uses when a request needs this provider.

**Data flow**: It receives a model specification and an API key. It creates an OpenAI-compatible async client pointed at OpenRouter's base URL and wraps it with the spec and key in an `OpenRouterModelClient`. The result is ready to stream chat completions.

**Call relations**: Each `ModelSpec` built by `_openrouter` stores this factory. Later, when the system selects an OpenRouter model, the registry uses the factory to produce the live client.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 574–594)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW, reasoning: ReasoningSupport=_REASONS, accepts_image_input: bool=True) -> ModelSpec
```

**Purpose**: Builds one registered chat-model specification for OpenRouter. It keeps the repeated provider settings in one place so each listed model can be declared clearly.

**Data flow**: It receives the model id, price, knowledge cutoff, context window, reasoning support, and image-input support. It fills in shared OpenRouter details such as provider name, key slot, environment variable, API surface, and client factory. The output is a `ModelSpec` used by the registry.

**Call relations**: The file calls this while constructing `OPENROUTER_MODEL_SPECS`. Those specs are later returned by `manifest` so the host can discover the available OpenRouter chat models.

*Call graph*: 1 external calls (__init__).


##### `GenerateImageInput._within_model_limits`  (lines 698–722)

```
def _within_model_limits(self) -> 'GenerateImageInput'
```

**Purpose**: Validates an image-generation request against what the chosen image model can actually do. It prevents known-bad combinations from being sent to OpenRouter, where they would fail later with a less helpful error.

**Data flow**: It reads the already-parsed prompt options: model, image count, aspect ratio, and resolution. It checks them against that model's limits, fills in a default resolution when that model uses resolution tiers, and returns the adjusted input object. If the request asks for too many images or an unsupported size, it raises a clear validation error.

**Call relations**: Pydantic, the input-validation library, calls this automatically after a `GenerateImageInput` is created for the `generate_image` tool. Validated arguments then flow into `OpenRouterImages.generate`.


##### `_reported_cost_micro_usd`  (lines 730–742)

```
def _reported_cost_micro_usd(usage: object) -> int | None
```

**Purpose**: Reads a cost reported by OpenRouter and converts it into micro-dollars, which are millionths of a US dollar. It also understands the alternate cost field used when a customer brings their own upstream provider key.

**Data flow**: It receives a usage-like object. It looks first for `cost`, then for `cost_details.upstream_inference_cost`, accepts only positive numeric values, converts dollars to micro-USD, and returns that integer. If no usable cost is present, it returns nothing so callers can fall back to list prices.

**Call relations**: Image charging calls this through `OpenRouterImages._charge`. Video job parsing calls it through `OpenRouterVideos._job`, so completed video jobs can carry their reported cost forward.

*Call graph*: called by 2 (_charge, _job).


##### `OpenRouterImages.generate`  (lines 775–812)

```
async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Performs a full image-generation tool call: sends the request, saves returned images, records cost when appropriate, and returns both file paths and image content to the model. This is the main worker behind `generate_image`.

**Data flow**: It receives the tool context and validated image arguments. It gets the OpenRouter key, posts the generation request, returns a tool error if OpenRouter refuses, decodes and checks images, writes them into the workspace, calculates the charge, meters the images unless the workspace supplied its own key, and returns a `ToolResult` containing JSON metadata plus the generated images.

**Call relations**: `_generate_image` constructs an `OpenRouterImages` instance and calls this. During the flow it uses `_refusal` for failed HTTP responses, `_images` to decode response data, `_save` to write files, and `_charge` to price the call.

*Call graph*: calls 5 internal fn (meter_images, _charge, _images, _refusal, _save); 6 external calls (__init__, __init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterImages._refusal`  (lines 814–829)

```
def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str
```

**Purpose**: Builds a short, useful error message when OpenRouter rejects an image request. The goal is to give the model enough information to change the prompt or parameters and try again.

**Data flow**: It receives the attempted image arguments and the HTTP response. It tries to read a JSON error message, falls back to the raw response text, trims it to a safe length, and returns a sentence saying that no image was generated.

**Call relations**: `OpenRouterImages.generate` calls this when the image API response is an error. The returned text becomes the content of an error `ToolResult`.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterImages._images`  (lines 831–861)

```
def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]
```

**Purpose**: Extracts usable images from OpenRouter's response and rejects responses that are missing images or contain files that are too large. This protects the workspace from broken or oversized output.

**Data flow**: It receives the original arguments and the response body. It looks for base64-encoded image data, decodes each image into bytes, assigns a media type when missing, checks the byte-size limit, and returns `GeneratedImage` objects. If there are no images or one is too large, it raises `OpenRouterImageError`.

**Call relations**: `OpenRouterImages.generate` calls this after a successful HTTP response and before saving anything. Its output is handed to `_save` and also returned to the model as image content.

*Call graph*: called by 1 (generate); 3 external calls (__init__, __init__, b64decode).


##### `OpenRouterImages._save`  (lines 863–870)

```
async def _save(self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage) -> str
```

**Purpose**: Writes one generated image into the workspace using a safe generated path. It gives the rest of the tool flow a file name that can later be shared.

**Data flow**: It receives the tool context, image arguments, the image's index in the batch, and the image data. It chooses a file extension from the image media type, writes the raw bytes under `generated-images/`, and returns the saved path.

**Call relations**: `OpenRouterImages.generate` calls this once for each decoded image. The collected paths are included in the final tool result.

*Call graph*: called by 1 (generate).


##### `OpenRouterImages._charge`  (lines 872–879)

```
def _charge(self, body: object, args: GenerateImageInput, images: int) -> int
```

**Purpose**: Calculates the cost of an image-generation call in micro-USD. It uses OpenRouter's reported charge when available and otherwise falls back to the model's listed per-image price.

**Data flow**: It receives the response body, the image arguments, and the number of images produced. It asks `_reported_cost_micro_usd` for a reported cost. If none is found, it multiplies the chosen model's fallback image price by the image count and returns that total.

**Call relations**: `OpenRouterImages.generate` calls this after images have been successfully saved. The returned amount is used for metering and is included in the tool result metadata.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 1 (generate).


##### `_generate_image`  (lines 882–887)

```
async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Acts as the registered handler for the `generate_image` tool. It connects the generic tool system to the OpenRouter image generator.

**Data flow**: It receives the tool context and validated image arguments. It checks that extension context is available, creates an `OpenRouterImages` helper using the extension credentials and optional test transport, and returns the result of running the image generation.

**Call relations**: The `GENERATE_IMAGE_TOOL` definition points to this function. When an agent calls the tool, the tool runtime enters here and hands the real work to `OpenRouterImages.generate`.

*Call graph*: 1 external calls (__init__).


##### `GenerateVideoInput._within_model_limits`  (lines 941–965)

```
def _within_model_limits(self) -> 'GenerateVideoInput'
```

**Purpose**: Validates a video-generation request against the chosen model's real limits. It catches unsupported durations, resolutions, and aspect ratios before starting a long external job.

**Data flow**: It reads the model, duration, resolution, and aspect ratio from the input object. It checks the duration range and allowed aspect ratios, fills in the model's default resolution if none was provided, and rejects unsupported resolution tiers. The output is the same input object, possibly with resolution filled in.

**Call relations**: Pydantic calls this automatically after `GenerateVideoInput` is built for the `generate_video` tool. Only validated arguments are passed to `OpenRouterVideos.generate`.


##### `OpenRouterVideos.generate`  (lines 1007–1048)

```
async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Performs a full video-generation tool call: starts a video job, waits for it to finish, downloads the MP4, saves it, meters cost, and returns the saved file path. This is the main worker behind `generate_video`.

**Data flow**: It receives the tool context and validated video arguments. It gets the OpenRouter key, posts a video-generation request, returns a tool error if no job starts, polls the job until it settles, reports provider failure if the job does not complete, downloads the finished video, saves it into the workspace, calculates cost, meters the video unless the workspace supplied its own key, and returns JSON metadata in a `ToolResult`.

**Call relations**: `_generate_video` constructs an `OpenRouterVideos` instance and calls this. The method uses `_refusal`, `_job`, `_settled`, `_failure`, `_download`, `_save`, and `_charge` as the separate steps of the video pipeline.

*Call graph*: calls 8 internal fn (meter_videos, _charge, _download, _failure, _job, _refusal, _save, _settled); 5 external calls (__init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterVideos._refusal`  (lines 1050–1065)

```
def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str
```

**Purpose**: Builds a short, useful error message when OpenRouter refuses to start a video job. This lets the model see whether the problem was the prompt, parameters, balance, or another provider response.

**Data flow**: It receives the attempted video arguments and the HTTP response. It tries to read a JSON error message, falls back to response text, trims it to a safe length, and returns a sentence saying that no video was generated.

**Call relations**: `OpenRouterVideos.generate` calls this when the initial video request returns an HTTP error. The text becomes the content of an error `ToolResult`.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterVideos._job`  (lines 1067–1082)

```
def _job(self, body: object) -> VideoJob
```

**Purpose**: Parses OpenRouter's video job description into a small internal record. It refuses to continue if the response does not name a job and status, because there would be nothing reliable to poll.

**Data flow**: It receives a response body from either job creation or polling. It reads the job id, status, optional error message, and optional reported cost, then returns a `VideoJob`. If the id or status is missing or not text, it raises `OpenRouterVideoError`.

**Call relations**: `OpenRouterVideos.generate` calls this after the initial successful request. `_settled` calls it again after each poll response to update the job state.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 2 (_settled, generate); 2 external calls (__init__, __init__).


##### `OpenRouterVideos._settled`  (lines 1084–1105)

```
async def _settled(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> VideoJob
```

**Purpose**: Polls a video job until it is no longer pending or in progress. It sets a time limit so a stuck external job does not hold the agent's turn open forever.

**Data flow**: It receives the video arguments, an HTTP client, and the current `VideoJob`. While the job is still pending or running, it waits, asks OpenRouter for the latest status, parses the new job state, and repeats. It returns the final job state, or raises an error if polling fails or the timeout is reached.

**Call relations**: `OpenRouterVideos.generate` calls this after creating a job. It uses `_job` to interpret each poll response and hands the settled job back so generation can either report failure or download the completed video.

*Call graph*: calls 1 internal fn (_job); called by 1 (generate); 4 external calls (__init__, sleep, get, monotonic).


##### `OpenRouterVideos._failure`  (lines 1107–1111)

```
def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str
```

**Purpose**: Turns a completed-but-unsuccessful video job into readable error text. It prefers the provider's own message when one is available.

**Data flow**: It receives the video arguments and the final job record. It chooses the job's error message or a generic status description, trims it to a safe length, and returns a sentence explaining that no video was generated.

**Call relations**: `OpenRouterVideos.generate` calls this when polling finishes but the job status is not `completed`. The returned text becomes the tool error shown to the model.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._download`  (lines 1113–1132)

```
async def _download(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> bytes
```

**Purpose**: Downloads the finished MP4 for a completed video job and checks that it is present and not too large. This prevents saving empty or oversized media into the workspace.

**Data flow**: It receives the video arguments, HTTP client, and completed job. It requests the job's first content item, rejects HTTP errors, rejects empty content, checks the byte-size limit, and returns the raw video bytes.

**Call relations**: `OpenRouterVideos.generate` calls this only after `_settled` reports a completed job. The returned bytes are passed to `_save`.

*Call graph*: called by 1 (generate); 2 external calls (__init__, get).


##### `OpenRouterVideos._save`  (lines 1134–1138)

```
async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str
```

**Purpose**: Writes the finished video into the workspace under a predictable generated path. It returns the path that the agent can later share.

**Data flow**: It receives the tool context, video arguments, and raw MP4 bytes. It writes the bytes under `generated-videos/` using the requested file-name stem and returns that path.

**Call relations**: `OpenRouterVideos.generate` calls this after downloading the completed video. The saved path is included in the final tool result.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._charge`  (lines 1140–1148)

```
def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int
```

**Purpose**: Calculates the cost of a video-generation job in micro-USD. It uses OpenRouter's reported charge when present and otherwise prices the video by model, resolution, and duration.

**Data flow**: It receives the video arguments and final job record. If the job already contains a reported cost, it returns that. Otherwise it looks up the chosen model's per-second rate for the final resolution, multiplies by requested duration, and returns the total.

**Call relations**: `OpenRouterVideos.generate` calls this after the video has been saved. The amount is used for video metering and included in the result metadata.

*Call graph*: called by 1 (generate).


##### `_generate_video`  (lines 1151–1156)

```
async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Acts as the registered handler for the `generate_video` tool. It connects the generic tool system to the OpenRouter video generator.

**Data flow**: It receives the tool context and validated video arguments. It checks that extension context is available, creates an `OpenRouterVideos` helper using the extension credentials and optional test transport, and returns the result of running video generation.

**Call relations**: The `GENERATE_VIDEO_TOOL` definition points to this function. When an agent calls the tool, the tool runtime enters here and hands the real work to `OpenRouterVideos.generate`.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1168–1184)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host application. It tells the system which OpenRouter chat models, tools, and credential slot this extension provides.

**Data flow**: It takes no input. It packages the extension name, version, registered model specs, image and video tools, and the OpenRouter API-key credential description into a `Manifest`. The output is the manifest object the host reads when loading the extension.

**Call relations**: The extension loader calls this when discovering available extensions. The returned manifest makes the OpenRouter models and media tools visible to the rest of the system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `during model calls in proposing, replay, and grading`

This file is like a standard plug for the extension’s model use. The rest of the self-improvement code should not need to know every detail of the SDK’s model system. It only needs two abilities: ask for a plain text completion, or ask the model to take a turn that may involve tools.

To make that possible, the file defines two small protocols. A protocol is a promise about what methods an object must have. `ModelLeg` promises a `complete` method that returns text. `ReplayLeg` promises a `turn` method that returns a full model message, including possible tool-related behavior.

`ModelAccessLeg` is the real adapter. It wraps the SDK’s `ModelAccess`, which is the project’s metered model connection, meaning usage can be tracked and charged or limited by workspace. When code calls this adapter, it builds a `ModelRequest` with shared defaults: a maximum of 2048 output tokens, a short conversation cache lifetime of five minutes, and reasoning turned off. For tool replay, it also includes the available tool definitions.

Without this file, different parts of the extension might call the model in inconsistent ways, with different limits or settings. This centralizes that choice so model behavior is easier to understand, test, and control.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This defines the shape of a simple text-only model call. Anything that claims to be a `ModelLeg` must accept a system instruction and conversation messages, then return the model’s text answer.

**Data flow**: The caller provides a system prompt, which is the high-level instruction for the model, and a tuple of messages, which is the conversation so far. An implementing object sends those to a model and returns a string. This protocol method itself only states the promise; it does not perform the work here.

**Call relations**: Other self-improvement code can depend on this narrow promise instead of depending directly on the full SDK model object. `ModelAccessLeg.complete` is the concrete version in this file that fulfills the promise.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This defines the shape of a model call used when replaying a turn with tools available. It returns a full `Message`, not just plain text, because the model’s response may need to include tool-use information.

**Data flow**: The caller provides a system prompt, the conversation messages so far, and a tuple of tool schemas, which describe tools the model is allowed to use. An implementing object uses those inputs to produce the model’s next message. This protocol method only defines that contract; it does not run the model itself.

**Call relations**: Replay-related code can ask for this ability without caring which model backend is underneath. `ModelAccessLeg.turn` is the concrete adapter method that carries out this contract using the SDK.


##### `ModelAccessLeg.complete`  (lines 28–38)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a text-only request to the underlying SDK model using the extension’s standard model settings. It is used when the extension needs a plain answer rather than a tool-aware model turn.

**Data flow**: It receives a system instruction and conversation messages. It wraps them in a `ModelRequest`, adding the selected model name, the shared 2048-token output limit, a five-minute conversation cache setting, and `reasoning` set to off. It sends that request through `self.model.complete` and returns the resulting text string.

**Call relations**: This is the concrete implementation of the `ModelLeg.complete` promise. When higher-level self-improvement steps need a simple model completion, they call this adapter, which builds the SDK request object and hands it to the metered `ModelAccess` connection.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 40–53)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a tool-aware model request through the SDK and returns the model’s next message. It is meant for replay flows where the model may need to see or choose from available tools.

**Data flow**: It receives a system instruction, conversation messages, and tool descriptions. It creates a `ModelRequest` containing those values plus the configured model name, the 2048-token output limit, the five-minute cache setting, and `reasoning` set to off. It passes that request to `self.model.turn` and returns the `Message` that comes back.

**Call relations**: This is the concrete implementation of the `ReplayLeg.turn` promise. Replay code calls it when it needs the SDK model to produce a full conversational turn, and this method handles packaging the request in the one standard way used by the extension.

*Call graph*: 1 external calls (__init__).


### Self-improvement workflows
Self-improvement workflows use model calls to propose prompt changes and replay prior conversations for evaluation.

### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is one step in a self-improvement loop. Imagine a coach reviewing moments where an assistant got stuck, then writing a small update to the assistant’s instructions so it does better next time. That is what this code does for system prompts, which are the hidden instructions that shape an AI agent’s behavior.

The main class, PromptProposer, receives the agent’s current prompt and a TaskClass, which is a group of similar tasks where problems were found. If that task class has no mined examples, there is nothing to learn from, so it stops immediately. Otherwise it builds a careful request for a model: here is the current prompt, here is the task class, and here are short examples showing the user request and what went wrong. The model is told to return only the full revised prompt, not an explanation.

After the model replies, the file cleans up common formatting noise, such as accidental Markdown code fences. It then rejects empty replies and rejects replies that are exactly the same as the current prompt. This matters because later parts of the system likely judge or apply candidates, and an unchanged prompt would be a wasted no-op. If the reply looks like a real change, it is wrapped as a PromptCandidate with the task name and proposed prompt text.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main action in the file: it tries to produce a revised system prompt for a task class where the agent has had trouble. Someone would use it when they have collected examples of failures and want a model-generated improvement candidate.

**Data flow**: It receives the current prompt text and a task class. If the task class has no examples, it returns nothing. Otherwise it builds a user-facing prompt, sends it with the fixed proposer instruction to the model, cleans the model’s answer, checks that the answer is not empty and not identical to the old prompt, and finally returns a PromptCandidate containing the task name and new prompt text.

**Call relations**: This function is the coordinator for the proposal step. It calls PromptProposer._prompt to prepare the material shown to the model, creates a Message for that model request, calls _clean to normalize the model’s answer, and creates a PromptCandidate only when the answer looks like a real proposed change.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This function writes the actual instruction packet that will be shown to the model. It combines the task class name, the current system prompt, and a limited set of failure examples into one clear request.

**Data flow**: It receives the current prompt and the task class. It takes up to the allowed number of examples, trims each request and problem description to the allowed character limit, formats them as numbered examples, and returns one complete text block asking for the full revised system prompt.

**Call relations**: PromptProposer.propose calls this just before contacting the model. Its output becomes the user message in the model conversation, giving the model the context it needs to suggest a focused prompt improvement.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This helper removes simple wrapping that the model might add around its answer, especially Markdown code fences. It helps turn the model’s reply into plain prompt text that can be compared and stored.

**Data flow**: It receives raw text from the model. It trims whitespace, removes an opening and closing triple-backtick block if present, trims again, and returns the cleaned text. It does not change anything outside that returned string.

**Call relations**: PromptProposer.propose calls this after the model responds. The cleaned result is then checked for emptiness or sameness with the current prompt before a PromptCandidate is created.

*Call graph*: called by 1 (propose).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation`

This file is a safety wrapper for testing prompt changes against archived conversations. In a real agent run, the model may ask to use tools, such as search or code execution. Re-running those tools during evaluation could be slow, costly, unsafe, or simply produce different results. Instead, this replay system treats the archive like a recorded play: the model can ask for the same tool calls, and the system hands back the same recorded tool results.

The main idea is to isolate the effect of the system prompt. The old final answer is removed, but the earlier user messages and tool history are kept. Reasoning-only blocks are also removed because they belong to the original model run and may be rejected or misleading when sent back to the provider.

The replay then gives the new prompt, the trimmed conversation, and a small tool catalog to the model. If the model asks for a tool call that matches one from the archive, it receives the archived answer. If it asks for something different, the replay stops and grades whatever text the model has produced so far. A round limit prevents endless tool loops. This matters because prompt candidates can be compared without touching live systems or changing the outside world.

#### Function details

##### `_canonical_input`  (lines 40–41)

```
def _canonical_input(value: object) -> str
```

**Purpose**: Turns a tool input into a stable text form so two inputs can be compared reliably. This is needed because the same JSON-like data can be written with keys in different orders.

**Data flow**: It receives any value used as a tool input. It converts that value to compact JSON text with keys sorted. The result is a consistent string that can be used as part of a lookup key.

**Call relations**: When the archive is indexed, archived_tool_results uses this to label each recorded tool call. Later, _feed_archived uses the same conversion on a replayed tool call so it can find the matching archived result.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 44–60)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Builds the starting conversation for a replay by removing the original final answer. It keeps the parts needed to recreate the task context, especially the earlier tool-use rounds.

**Data flow**: It receives the full archived message list. It removes trailing assistant messages that are final answers rather than tool requests, then cleans each remaining message of reasoning-only blocks. It returns the trimmed, safe-to-replay message tuple.

**Call relations**: ReplayEvaluation.replay calls this at the start of a replay. replay_head delegates the message cleanup to _without_reasoning so the model is not given old hidden reasoning material.

*Call graph*: calls 1 internal fn (_without_reasoning); called by 1 (replay).


##### `_without_reasoning`  (lines 63–71)

```
def _without_reasoning(message: Message) -> Message
```

**Purpose**: Removes reasoning blocks from one message while leaving normal text, tool calls, and tool results intact. This avoids sending provider-specific or stale internal thinking back into a new model request.

**Data flow**: It receives one message. If the message is plain text, it returns it unchanged. If the message contains structured blocks, it filters out thinking and reasoning blocks and returns a new message with the remaining content.

**Call relations**: replay_head calls this for every message that remains in the replay context. Its output becomes part of the conversation sent by ReplayEvaluation.replay to the model leg.

*Call graph*: called by 1 (replay_head); 1 external calls (__init__).


##### `archived_tool_results`  (lines 74–96)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: Creates a lookup table of recorded tool answers from the archived conversation. This lets replay answer tool requests from history instead of actually running tools.

**Data flow**: It reads the archived messages twice. First it gathers tool result blocks by their tool-use id. Then it finds tool-use blocks and pairs each one with its recorded result, using the tool name and canonicalized input as the lookup key. It returns a dictionary from that key to the archived result block.

**Call relations**: ReplayEvaluation.replay calls this before the replay loop begins. _feed_archived later uses the resulting lookup table to answer the model’s replayed tool calls.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 99–116)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: Builds the small list of tools that the replay model is allowed to call. The list is based only on tools that appeared in the archive, not on any live tool registry.

**Data flow**: It scans the archived messages for tool-use blocks and records each distinct tool name once. For each name, it creates a permissive tool schema that allows object-shaped inputs. It returns those schemas as a tuple.

**Call relations**: ReplayEvaluation.replay calls this during setup, then passes the returned tool schemas into each model turn. This gives the model enough information to reproduce archived calls without giving it access to unrelated live tools.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 119–135)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: Answers the model’s current tool calls using recorded tool results. If any requested call does not match the archive, it signals that the replay has left the recorded path.

**Data flow**: It receives the tool calls requested in one replay round and the archived-result lookup table. For each call, it searches by tool name and canonicalized input. If all calls are found, it creates a user message containing copied tool-result blocks with the new call ids. If any call is missing, it returns None.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks to use tools. A returned message is added to the replay conversation; None tells the replay loop to stop because the model requested a tool path the archive cannot safely answer.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 148–169)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: Runs the full counterfactual replay for one archived task and one candidate system prompt. It produces the final text that this prompt would likely have generated under the recorded tool history.

**Data flow**: It receives archived messages and a system prompt. It builds the archived tool-result lookup, the replay tool list, and the trimmed conversation head. Then it repeatedly asks the replay model for the next assistant message. If the model gives a final answer, that text is returned. If it asks for tools, the method feeds back archived results when possible. If the model diverges or the round limit is reached, it returns the last useful text seen.

**Call relations**: This is the main flow in the file. It calls archived_tool_results, replay_tools, and replay_head to prepare the replay, then uses _feed_archived inside the loop to keep the model on the recorded tool path. It wraps the outcome in ReplayResult for the caller that is comparing prompt candidates.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-model-catalog` — The shared list of available AI models, their abilities, prices, limits, and required credentials.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-proposal-state` — Durable proposed-change records, including pending, approved, or rejected prompt/config/self-improvement proposals and their before/after payloads.
- `reg-evaluation-run-store` — Durable evaluation test cases, replay runs, comparison results, and self-improvement validation state used to accept or reject changes.
- `reg-runtime-connection-pools` — Live pooled connections and reusable clients for shared services such as the database, Redis/live hub, blob storage, model providers, connector APIs, and sandbox/browser providers.
