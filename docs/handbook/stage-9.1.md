# Model Round Streaming  `stage-9.1`

This stage is part of the main work loop: it is what happens when UFO asks a model for one answer and receives it piece by piece as it is being written. Different model companies speak slightly different “streaming” languages, so this stage acts like a set of adapters that turn them into UFO’s single standard stream of events.

The Anthropic adapter talks to Claude, manages credentials, retries, prompt caching, reasoning blocks, tool requests, and usage counts. The OpenAI adapter does the same for OpenAI-style services, including both Chat Completions and the newer Responses API. The OpenRouter extension adds another doorway to many hosted models, and can also expose image and video generation tools that save files and record cost.

While text is arriving, the replies helper watches for special hidden “reply-to” markup and prevents it from leaking to the user. Finally, the rounds runner is the coordinator. It starts one model reply, displays safe text as it streams in, gathers tool calls, reasoning, timing, and usage, then hands the rest of the system one clean result.

## Files in this stage

### Provider Stream Adapters
Provider-specific clients translate Anthropic, OpenAI-style, Responses API, and OpenRouter streams into UFO model events while handling credentials, retries, tools, usage, and provider quirks.

### `core/src/ufo/harness/models/anthropic.py`

`io_transport` · `request handling`

This file is the adapter between UFO's model harness and Anthropic's API. Without it, the rest of the project would need to know Anthropic's exact request format, streaming event names, error behavior, and special rules for tool use and reasoning. Instead, this file translates both directions: it turns a common ModelRequest into an Anthropic request, then turns Anthropic's live stream back into common ModelEvent objects the rest of the engine understands.

The file has three main jobs. First, it builds the correct Anthropic SDK client, choosing between an API key and an OAuth token. Second, it converts message content into Anthropic's wire format, including text, images, tool calls, tool results, and Claude's “thinking” blocks. Third, it runs the streamed request with careful retry rules. If the provider fails before any visible output, it can safely try again. If the stream breaks after text or a tool call has already been shown, it raises a special interruption so the wider engine can discard the partial turn and redo it cleanly.

A useful analogy is a translator at a live phone call. It prepares the right language before the call, translates each sentence as it arrives, keeps a tally of cost and usage, and knows when a dropped call can be redialed versus when the conversation must be restarted.

#### Function details

##### `anthropic_sdk_client`  (lines 56–72)

```
def anthropic_sdk_client(credential: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates an Anthropic asynchronous SDK client with the right kind of authentication. It turns off the SDK's built-in retries because this file has its own retry policy that is coordinated with streaming behavior.

**Data flow**: It receives one credential string. It checks whether that string looks like an Anthropic OAuth access token; if so, it builds a client that sends it as a bearer token with Anthropic's OAuth beta header. Otherwise, it builds a normal API-key client. In both cases, the result is a ready-to-use Anthropic client with a fixed timeout and no automatic SDK retries.

**Call relations**: This is the setup doorway for Anthropic access. It relies on is_oauth_credential to decide which authentication shape to use, then hands the credential to anthropic.AsyncAnthropic so later code, especially AnthropicClient.complete, can make actual streamed model requests.

*Call graph*: calls 1 internal fn (is_oauth_credential); 1 external calls (AsyncAnthropic).


##### `is_oauth_credential`  (lines 75–77)

```
def is_oauth_credential(credential: str) -> bool
```

**Purpose**: Checks whether a credential string is an Anthropic OAuth token rather than a normal API key. This matters because the two credential types are sent differently over the network.

**Data flow**: It receives a credential string, checks whether it begins with the known OAuth token prefix, and returns true or false. It does not change anything else.

**Call relations**: anthropic_sdk_client calls this early when constructing the SDK client. Its answer decides whether the client is configured for OAuth bearer-token authentication or normal API-key authentication.

*Call graph*: called by 1 (anthropic_sdk_client).


##### `_anthropic_image`  (lines 80–84)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts the project's image object into the image block format Anthropic expects. This keeps image formatting in one small place instead of repeating it throughout request-building code.

**Data flow**: It receives an ImageSource containing a media type and base64 image data. It wraps those values in Anthropic's nested dictionary shape and returns that dictionary. Nothing outside the returned value is changed.

**Call relations**: This helper is used whenever image content has to be placed on Anthropic's wire format. anthropic_content uses it for normal message images, and _anthropic_tool_result_part uses it for images returned by tools.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 87–92)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic's format. A tool result can contain plain text or an image, and this function handles those two cases.

**Data flow**: It receives one tool-result content block. If it is text, it returns an Anthropic text dictionary. If it is an image, it passes the image source to _anthropic_image and returns the resulting image dictionary.

**Call relations**: anthropic_content calls this while building a tool_result message for Anthropic. It delegates image formatting to _anthropic_image so tool-result images match normal message images.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 95–129)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Turns the project's standard message content into Anthropic content blocks. This is the central translation point for text, images, tool use, tool results, and Claude reasoning blocks.

**Data flow**: It receives either a plain string or a tuple of structured content blocks. A string is returned unchanged. Structured blocks are walked one by one and converted into Anthropic dictionaries. OpenAI-style reasoning items are skipped because Anthropic cannot use another provider's private reasoning format. The output is either the original string or a list of Anthropic-ready content dictionaries.

**Call relations**: AnthropicClient._request_kwargs calls this while preparing every message in a model request. It uses _anthropic_image for images and _anthropic_tool_result_part for multi-part tool results, then hands the converted content into the final API request.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (_request_kwargs).


##### `_AnthropicRetry.transport`  (lines 139–174)

```
async def transport(self, error: Exception, yielded: bool) -> _AnthropicRetry
```

**Purpose**: Decides what to do after a network-level streaming failure, such as a timeout or dropped connection. It retries only when it is still safe to do so.

**Data flow**: It receives the original error and a flag saying whether any visible model output has already been yielded. It increases the attempt count. If output was already yielded, or the retry limit is exhausted, it logs the failure and raises either a stream-interrupted error or the original error. Otherwise, it logs and counts the retry, waits for the current delay, and returns a new retry state with a larger delay.

**Call relations**: AnthropicClient.complete calls this when Anthropic or the HTTP layer fails during streaming. The method logs through the observability tools, sleeps before retrying, and returns an updated _AnthropicRetry object so the complete loop can issue the request again.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_AnthropicRetry.status`  (lines 176–226)

```
async def status(self, error: anthropic.APIStatusError, yielded: bool) -> _AnthropicRetry
```

**Purpose**: Decides what to do after Anthropic returns an API status error. It separates permanent request problems from temporary provider problems so the system does not retry hopeless requests.

**Data flow**: It receives Anthropic's status error and a flag saying whether visible output has already been yielded. A rejected credential is converted into the model spec's key-rejected error. Most deterministic 4xx client errors are raised immediately. Retryable errors are logged, counted, delayed using a retry-after header when present, and returned as an updated retry state. If the stream failed after visible output, it raises a stream interruption instead of quietly retrying.

**Call relations**: AnthropicClient.complete calls this when the Anthropic SDK reports a status error. It uses logging and metrics for visibility, may raise ModelStreamInterrupted for mid-stream failures, and returns the next retry plan when another request attempt is safe.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_AnthropicStream.__init__`  (lines 230–241)

```
def __init__(self) -> None
```

**Purpose**: Creates the scratchpad used to interpret one Anthropic streaming response. It starts with no emitted output, no remembered tool IDs, no reasoning blocks, and empty token-usage counters.

**Data flow**: It takes no input beyond the new object being created. It initializes fields that will be filled as Anthropic stream events arrive: tool-call IDs, thinking text fragments, reasoning blocks, token counts, output count, and stop reason.

**Call relations**: AnthropicClient.complete creates a fresh _AnthropicStream for each request attempt. The complete loop then feeds provider events into this state object so it can translate the stream consistently.

*Call graph*: called by 1 (complete).


##### `_AnthropicStream.accept`  (lines 243–287)

```
def accept(self, event: object) -> tuple[ModelEvent, ...]
```

**Purpose**: Consumes one raw Anthropic stream event and emits zero or more standard model events for the rest of the system. It is the live translator from Anthropic's event vocabulary into UFO's event vocabulary.

**Data flow**: It receives a single stream event from Anthropic. Depending on the event type, it may record input usage, remember a tool-use ID, emit text, emit tool-call start or tool-call JSON fragments, collect thinking text, store redacted thinking, close a finished thinking block, or record final output tokens and stop reason. It returns a tuple of standard ModelEvent objects, which may be empty if the event was only bookkeeping.

**Call relations**: AnthropicClient.complete calls this for each event in the provider stream. accept calls _record_input_usage when the stream starts with usage information and _close_thinking when a thinking block finishes. The events it returns are immediately yielded outward by complete.

*Call graph*: calls 2 internal fn (_close_thinking, _record_input_usage); 4 external calls (__init__, __init__, __init__, __init__).


##### `_AnthropicStream._record_input_usage`  (lines 289–297)

```
def _record_input_usage(self, usage: Any) -> None
```

**Purpose**: Copies token-use information from Anthropic's message-start event into the stream state. This lets the system later report how many input tokens were used and how many came from cache reads or writes.

**Data flow**: It receives Anthropic's usage object. It reads input tokens, cache-read tokens, and cache-creation details, then stores those numbers on the _AnthropicStream instance. It returns nothing; the changed stream state is the result.

**Call relations**: _AnthropicStream.accept calls this when it sees the stream's initial message-start event. Later, usage and has_usage read the stored counters so AnthropicClient.complete can yield accurate usage information even on some failures.

*Call graph*: called by 1 (accept).


##### `_AnthropicStream._close_thinking`  (lines 299–308)

```
def _close_thinking(self, index: int) -> None
```

**Purpose**: Finishes a Claude thinking block once Anthropic says that block is complete. It joins the collected thinking fragments and preserves Anthropic's signature, which is needed if the reasoning must be sent back later.

**Data flow**: It receives the stream index of the thinking block that just ended. It looks up the stored text fragments and signature for that index. If the signature is missing, it raises an error because the block would be invalid. Otherwise, it creates a ThinkingBlock, appends it to the ordered reasoning list, and removes the temporary pieces from the working dictionaries.

**Call relations**: _AnthropicStream.accept calls this when a content block stop event belongs to a thinking block. The completed reasoning block is later yielded by AnthropicClient.complete after the visible stream has ended and just before usage.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_AnthropicStream.has_usage`  (lines 310–316)

```
def has_usage(self) -> bool
```

**Purpose**: Reports whether this stream has recorded any input-side usage information. It is used to decide whether partial usage can still be reported when a provider error interrupts the stream.

**Data flow**: It reads the stream state's input and cache token counters. If any of them are nonzero, it returns true; otherwise, it returns false. It does not change the stream state.

**Call relations**: AnthropicClient.complete uses this around error paths and missing-usage cases. If usage data is present before a failure, complete can yield it before raising or retrying.


##### `_AnthropicStream.usage`  (lines 318–325)

```
def usage(self) -> Usage
```

**Purpose**: Builds the project's standard Usage record from the token counts gathered during the Anthropic stream. This gives the rest of the system one consistent usage shape regardless of provider.

**Data flow**: It reads input tokens, output tokens, cache-read tokens, and cache-write tokens from the stream state. If Anthropic never reported output tokens, it uses zero for output. It returns a Usage object and does not alter the stream state.

**Call relations**: AnthropicClient.complete calls this at the end of a normal stream, before raising truncation or refusal errors, and on some failure paths when partial usage is available. It constructs the Usage record consumed by the rest of the harness.

*Call graph*: 1 external calls (__init__).


##### `AnthropicClient._request_kwargs`  (lines 334–380)

```
def _request_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the full set of arguments needed to call Anthropic's message creation API. It translates a generic ModelRequest into Anthropic's exact request shape, including system prompt caching, tools, reasoning settings, and streaming.

**Data flow**: It receives a ModelRequest. It builds a dictionary containing the model name, system prompt, trimmed messages, max token budget, streaming flag, cache settings, optional reasoning controls, and optional tool definitions. Message content is converted through anthropic_content, and images may be trimmed before sending. The returned dictionary is ready to pass to the Anthropic SDK.

**Call relations**: AnthropicClient.complete calls this right before opening the provider stream. It depends on anthropic_content for per-message conversion and trim_images to keep image-heavy conversations within provider limits or project policy.

*Call graph*: calls 1 internal fn (anthropic_content); called by 1 (complete); 1 external calls (trim_images).


##### `AnthropicClient.complete`  (lines 382–469)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one streamed completion request to Anthropic and yields the project's standard model events as they arrive. It is the main method the rest of the model harness uses to get Claude text, tool calls, reasoning blocks, and final usage.

**Data flow**: It receives a ModelRequest. It creates retry state, builds Anthropic request arguments, opens a streamed API response, and feeds each raw provider event into a fresh _AnthropicStream. Visible events such as stream start, text deltas, and tool-call pieces are yielded immediately. At the end, it checks stop reasons, raises clear errors for truncation or refusal, retries empty answer turns a limited number of times, then yields saved reasoning blocks and final Usage before returning. On retryable provider failures before visible output, it retries; after visible output, it raises an interruption so the wider engine can restart cleanly.

**Call relations**: This method ties the whole file together. It calls _request_kwargs to prepare the API call, creates _AnthropicStream to translate incoming events, uses _AnthropicRetry through its retry methods when transport or status errors occur, emits metrics for empty retries, and yields ModelStreamStart plus all translated model events to its caller.

*Call graph*: calls 2 internal fn (_request_kwargs, __init__); 5 external calls (__init__, __init__, __init__, __init__, emit_metric).


### `core/src/ufo/harness/models/openai.py`

`io_transport` · `request handling`

UFO has its own neutral way to describe a model request: system instructions, user and assistant messages, images, tool calls, tool results, reasoning items, and token usage. OpenAI-like providers expect those same ideas in specific wire formats, and different OpenAI APIs expect different shapes. This file is the adapter that translates both directions.

On the way out, it turns UFO messages into either Chat Completions messages or Responses API input items. It also builds tool definitions, chooses whether to send a reasoning effort setting, and adds special headers when the credential is a ChatGPT account token that must use the Codex backend instead of the public OpenAI API.

On the way back, it reads streaming chunks from the provider. A stream is like receiving a sentence one word at a time: this file converts each piece into events such as text deltas, tool-call starts, tool-call argument deltas, reasoning blocks, and final token usage. It also detects refusals and truncated answers.

A major responsibility here is safe retry behavior. If the provider times out or returns a retryable status before any visible output is produced, the request is tried again. If the stream breaks after output has already been shown, the file raises a special interruption so the larger engine can discard the partial turn instead of trusting a half-answer.

#### Function details

##### `_cache_write_tokens`  (lines 112–120)

```
def _cache_write_tokens(details: PromptTokensDetails | InputTokensDetails | None) -> int
```

**Purpose**: Reads OpenAI's extra token accounting field for cache writes, when the provider reports it. This matters because cached prompt tokens and newly cached prompt tokens may be priced differently from ordinary input tokens.

**Data flow**: It receives an optional token-details object from OpenAI. It looks inside the provider's extra metadata for `cache_write_tokens`, treats a missing value as zero, checks that a present value is a real integer, and returns that integer.

**Call relations**: The usage-conversion functions call this helper while turning provider token reports into UFO's `Usage` record. It is the small shared checker that keeps both Chat Completions and Responses accounting consistent.

*Call graph*: called by 2 (_chat_usage, _responses_usage).


##### `_responses_usage`  (lines 123–137)

```
def _responses_usage(raw: ResponseUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: Converts token usage reported by OpenAI's Responses API into UFO's common usage format. It separates normal input tokens, cached-read tokens, cache-write tokens, and output tokens.

**Data flow**: It receives a raw Responses API usage object and a flag saying whether 30-minute cache writes should be counted as priced cache-write tokens. It reads total input, output, cached, and cache-write counts, checks that the parts do not exceed the total, and returns a `Usage` object with the adjusted numbers.

**Call relations**: The Responses stream parser calls this when a response completes, fails with usage, or ends incomplete with usage. It relies on `_cache_write_tokens` for the provider-specific extra field.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 2 (_record_incomplete, accept); 1 external calls (__init__).


##### `_chat_usage`  (lines 140–154)

```
def _chat_usage(raw: openai.types.CompletionUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: Converts token usage reported by OpenAI's Chat Completions API into UFO's common usage format. It performs the same accounting as `_responses_usage`, but for the older chat usage shape.

**Data flow**: It receives a raw Chat Completions usage object and a cache-pricing flag. It extracts prompt tokens, completion tokens, cached prompt tokens, and cache-write tokens, validates the totals, and returns a `Usage` object.

**Call relations**: The Chat stream parser calls this when a streamed chat chunk includes usage information. It shares `_cache_write_tokens` with the Responses path so both APIs are counted the same way.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 1 (accept); 1 external calls (__init__).


##### `openai_sdk_client`  (lines 157–170)

```
def openai_sdk_client(api_key: str, base_url: str | None=None, default_headers: dict[str, str] | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Creates the OpenAI Python SDK client used to send requests. It deliberately disables the SDK's own retries because this file has its own retry rules that understand streaming.

**Data flow**: It receives an API key, an optional base URL, and optional default headers. It builds and returns an asynchronous OpenAI client with a fixed timeout and no SDK-level retry attempts.

**Call relations**: Other setup code can use this directly for OpenAI-compatible services. `codex_sdk_client` also calls it after preparing the special URL and headers needed for ChatGPT account-token access.

*Call graph*: called by 1 (codex_sdk_client); 1 external calls (AsyncOpenAI).


##### `chatgpt_account_id`  (lines 173–186)

```
def chatgpt_account_id(credential: str) -> str | None
```

**Purpose**: Checks whether a credential looks like a ChatGPT account token and, if so, extracts the account id from it. This tells the system whether to use the ChatGPT Codex backend instead of the normal OpenAI API host.

**Data flow**: It receives a credential string. If the string is not a three-part JWT token, or if its middle part cannot be decoded as JSON, it returns `None`; otherwise it reads the expected ChatGPT account claim and returns that account id when present.

**Call relations**: This function is a credential classifier. It does not call the model service itself, but its result decides whether code should build a normal OpenAI client or a Codex client.

*Call graph*: 2 external calls (urlsafe_b64decode, loads).


##### `codex_sdk_client`  (lines 189–205)

```
def codex_sdk_client(credential: str, account: str) -> openai.AsyncOpenAI
```

**Purpose**: Creates an OpenAI SDK client pointed at the ChatGPT Codex backend for member account tokens. That backend needs extra headers on every request, not just a bearer token.

**Data flow**: It receives the credential and the ChatGPT account id. It prepares headers for the account id, calling app, Responses beta, and streaming accept type, then returns an SDK client aimed at the Codex base URL.

**Call relations**: It delegates the actual SDK-client construction to `openai_sdk_client`. It is used when the credential has been identified as a ChatGPT account token rather than a platform API key.

*Call graph*: calls 1 internal fn (openai_sdk_client).


##### `_status_retry_wait`  (lines 208–214)

```
def _status_retry_wait(error: openai.APIStatusError, delay: float) -> float
```

**Purpose**: Decides how long to wait before retrying after an OpenAI HTTP status error. It respects the provider's `retry-after` header when that asks for a longer pause.

**Data flow**: It receives an API status error and the current backoff delay. It tries to read `retry-after` from the response headers, falls back to the current delay if the header is missing or invalid, and returns the larger safe wait time.

**Call relations**: `_OpenAIRetry.status` calls this when handling rate limits or server errors. It keeps retry timing provider-aware without scattering header parsing through the retry logic.

*Call graph*: called by 1 (status).


##### `_openai_image`  (lines 217–221)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns UFO's in-memory image data into the image shape expected by OpenAI chat messages. It packages the image as a `data:` URL, which is a text form containing the media type and base64 image bytes.

**Data flow**: It receives an image source with a media type and base64 data. It returns a small dictionary describing an OpenAI `image_url` part whose URL embeds that image data.

**Call relations**: Message-conversion code calls this whenever an image needs to be sent through the Chat Completions format, including images inside tool results.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 224–240)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Splits a tool result into the parts OpenAI Chat Completions can accept. Chat tool-result messages are text-only, so images have to be separated for later delivery as user image content.

**Data flow**: It receives either a plain text tool result or a tuple of text and image blocks. It returns two things: the text to put in the tool message, and a list of image parts to send afterward.

**Call relations**: `openai_messages` calls this while translating UFO tool results into chat messages. It uses `_openai_image` for any image blocks it finds.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 243–302)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts UFO's canonical conversation history into the message list required by OpenAI's Chat Completions API. This is the outbound translator for the chat surface.

**Data flow**: It receives the system prompt and a tuple of UFO messages. It trims older images, drops reasoning blocks that Chat Completions has no place to carry, converts text, images, tool calls, and tool results into OpenAI dictionaries, and returns the full message list.

**Call relations**: `OpenAIClient._chat_kwargs` calls this when building a Chat Completions request. It uses `_openai_image` and `_openai_tool_result` for image and tool-result details.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 305–406)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: Converts UFO's canonical conversation history into the richer input-item format required by OpenAI's Responses API. Unlike Chat Completions, this format can carry reasoning items back to the provider.

**Data flow**: It receives a tuple of UFO messages. It trims images, walks through each message block, drops reasoning formats that belong to other providers, preserves OpenAI reasoning items, converts text and images, and turns tool calls and tool outputs into Responses API input items.

**Call relations**: `responses_request` calls this when building a Responses API request. It is the main reason a previous round's encrypted reasoning can be replayed so the model can continue after tool results.

*Call graph*: called by 1 (responses_request); 13 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, ResponseOutputTextParam (+3 more)).


##### `responses_request`  (lines 409–448)

```
def responses_request(request: ModelRequest, effort: OpenAIEffort, codex: bool=False) -> dict[str, Any]
```

**Purpose**: Builds the full request body for OpenAI's Responses API. It combines model choice, instructions, conversation input, streaming options, reasoning effort, token budget, and tool definitions.

**Data flow**: It receives a UFO model request, a resolved reasoning effort, and a flag saying whether the Codex backend is being used. It converts messages with `responses_input`, adds streaming and reasoning-related options, omits unsupported fields for Codex, adds tools when present, and returns keyword arguments for the SDK call.

**Call relations**: `OpenAIClient._complete_responses` calls this immediately before opening a Responses stream. It is the last translation step before network I/O.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `_OpenAIRetry.transport`  (lines 458–493)

```
async def transport(self, error: Exception, yielded: bool) -> _OpenAIRetry
```

**Purpose**: Applies the retry policy for transport failures, such as timeouts or dropped connections. It only retries safely before user-visible output has been produced.

**Data flow**: It receives the caught exception and a flag saying whether the stream already yielded visible events. It either logs and raises, raises a stream-interruption error for mid-stream failure, or waits, records a retry metric, and returns a new retry state with a higher attempt count and longer delay.

**Call relations**: Both streaming methods use this after transport errors. It hands control back to the caller with updated retry timing when another attempt is allowed.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_OpenAIRetry.status`  (lines 495–541)

```
async def status(self, error: openai.APIStatusError, yielded: bool) -> _OpenAIRetry
```

**Purpose**: Applies the retry policy for HTTP status errors from the provider. It treats rejected credentials specially, retries rate limits and server errors when safe, and avoids retrying ordinary request failures.

**Data flow**: It receives an OpenAI status error and a flag saying whether output has already been yielded. It may translate a key-rejection status into the model spec's credential error, wait and return updated retry state for retryable failures, raise a stream-interruption error for retryable mid-stream failures, or re-raise the original error.

**Call relations**: Both streaming methods use this after OpenAI status errors. It calls `_status_retry_wait` to honor provider retry timing and records logs and metrics for observability.

*Call graph*: calls 2 internal fn (_status_retry_wait, __init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_ChatStream.__init__`  (lines 545–550)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates the small state holder used while reading one Chat Completions stream. It remembers whether anything visible has been yielded, active tool-call ids, final usage, and the finish reason.

**Data flow**: It receives a flag saying whether cache-write tokens should be counted in priced usage. It initializes empty tracking fields that will be filled as chunks arrive.

**Call relations**: `OpenAIClient._complete_chat` creates one of these for each attempt. The object then collects facts from streamed chunks until the attempt finishes or fails.

*Call graph*: called by 1 (_complete_chat).


##### `_ChatStream.accept`  (lines 552–580)

```
def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]
```

**Purpose**: Consumes one streamed Chat Completions chunk and turns it into zero or more UFO model events. It recognizes text output, tool-call starts, tool-call argument fragments, usage reports, and finish reasons.

**Data flow**: It receives one OpenAI chat chunk. It updates stored usage and finish reason when present, converts content deltas into `TextDelta` events, converts tool-call deltas into `ToolCallStart` and `ToolCallDelta` events, marks whether visible output happened, and returns the events to emit.

**Call relations**: `OpenAIClient._complete_chat` calls this inside the async stream loop and yields the returned events to the rest of UFO. It calls `_chat_usage` when usage appears.

*Call graph*: calls 1 internal fn (_chat_usage); 3 external calls (__init__, __init__, __init__).


##### `_ChatStream.finish`  (lines 582–597)

```
def finish(self) -> tuple[Usage, Exception | None]
```

**Purpose**: Checks the final state of a Chat Completions stream and returns its usage plus any terminal problem. It turns a `length` finish reason into UFO's standard truncated-response error.

**Data flow**: It reads the stored finish reason and usage. If the answer hit the token limit, it prepares a truncation error; if usage is missing, it raises an error; otherwise it returns the usage and optional terminal error.

**Call relations**: `OpenAIClient._complete_chat` calls this after the provider stream ends. The caller then decides whether to yield usage, raise truncation, retry an empty answer, or finish normally.

*Call graph*: 1 external calls (__init__).


##### `_ResponsesStream.__init__`  (lines 601–608)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates the state holder used while reading one Responses API stream. It tracks visible output, tool-call ids, whether tool arguments arrived in deltas, reasoning items, final usage, and terminal errors.

**Data flow**: It receives a flag for cache-write pricing and initializes empty maps, sets, lists, and result fields. These fields are filled as Responses events arrive.

**Call relations**: `OpenAIClient._complete_responses` creates one of these for each Responses attempt. The object gathers streamed state until the request completes, fails, or must be retried.

*Call graph*: called by 1 (_complete_responses).


##### `_ResponsesStream.accept`  (lines 610–649)

```
def accept(self, event: ResponseStreamEvent) -> tuple[ModelEvent, ...]
```

**Purpose**: Consumes one Responses API stream event and turns it into zero or more UFO model events. It understands text deltas, tool calls, reasoning completion, refusals, completion, failure, and incomplete-response events.

**Data flow**: It receives a single Responses stream event. Depending on the event type, it emits text or tool-call events, stores reasoning, converts usage, records refusal or failure errors, or records incomplete-response details. It returns only the events that should be streamed live.

**Call relations**: `OpenAIClient._complete_responses` calls this inside the Responses stream loop and yields its live events. It delegates reasoning and incomplete-response details to `_record_reasoning` and `_record_incomplete`.

*Call graph*: calls 3 internal fn (_record_incomplete, _record_reasoning, _responses_usage); 4 external calls (__init__, __init__, __init__, __init__).


##### `_ResponsesStream._record_reasoning`  (lines 651–660)

```
def _record_reasoning(self, item: ResponseReasoningItem) -> None
```

**Purpose**: Stores a completed OpenAI reasoning item so it can be replayed in a later request. The encrypted content is required because the provider needs it to reconnect future tool results with the model's prior reasoning.

**Data flow**: It receives a completed reasoning item from the Responses stream. It checks that encrypted content is present, extracts the id, encrypted content, and summary text, and appends a `ReasoningItemBlock` to the stream state.

**Call relations**: `_ResponsesStream.accept` calls this when a reasoning output item is done. The completed stream later yields these reasoning blocks just before final usage.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_ResponsesStream._record_incomplete`  (lines 662–673)

```
def _record_incomplete(self, response: Any) -> None
```

**Purpose**: Records why a Responses API request ended incomplete. It turns provider reasons such as token limit or content filtering into UFO's standard truncation or refusal errors.

**Data flow**: It receives the incomplete response object. If usage is present, it converts and stores it; then it reads the incomplete reason and stores the right terminal error: truncated, refusal, or a generic incomplete-response error.

**Call relations**: `_ResponsesStream.accept` calls this when OpenAI sends an incomplete-response event. It uses `_responses_usage` for any usage data attached to that incomplete response.

*Call graph*: calls 1 internal fn (_responses_usage); called by 1 (accept); 2 external calls (__init__, __init__).


##### `_ResponsesStream.finish`  (lines 675–683)

```
def finish(self) -> tuple[Usage, Exception | None]
```

**Purpose**: Checks the final state of a Responses API stream and returns its usage plus any terminal problem. It preserves refusals and truncations even when no usage was reported.

**Data flow**: It reads the stored usage and terminal error. If usage is missing but a terminal error exists, it raises that error; if usage is missing without explanation, it raises a missing-usage error; otherwise it returns the usage and optional terminal error.

**Call relations**: `OpenAIClient._complete_responses` calls this after the stream closes. The caller then decides whether to yield usage, raise the terminal error, retry an empty answer, or yield saved reasoning and finish.


##### `OpenAIClient.complete`  (lines 698–701)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Chooses which OpenAI-style API surface to use for a model request. This lets one client support both Chat Completions and Responses without guessing from the model name.

**Data flow**: It receives a UFO model request. It checks whether this client is using the Codex backend or whether the model spec declares the Responses API; based on that, it returns the matching async event stream.

**Call relations**: This is the public entry point for the rest of the model harness. It hands the request to either `_complete_responses` or `_complete_chat`.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._reasoning_effort`  (lines 703–722)

```
def _reasoning_effort(self, request: ModelRequest) -> OpenAIEffort
```

**Purpose**: Decides what reasoning-effort value, if any, should be sent to OpenAI for this request. It protects against cases where a model would reason by default even though the caller asked reasoning to be off.

**Data flow**: It receives the model request and consults the model spec. It maps UFO's reasoning choices to OpenAI's wire values, turns `auto` into no explicit parameter, turns `off` into OpenAI's `none`, and raises if the provider surface cannot express a required off setting with tools.

**Call relations**: `_chat_kwargs` and `_complete_responses` call this before sending requests. It centralizes the tricky compatibility rules between model capabilities, tools, and provider API surfaces.

*Call graph*: called by 2 (_chat_kwargs, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 724–753)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the keyword arguments for a Chat Completions streaming request. It turns a UFO request into the exact fields the OpenAI SDK expects for the chat endpoint.

**Data flow**: It receives a UFO model request. It converts messages with `openai_messages`, adds model name, token budget, streaming usage options, optional reasoning effort, and optional tools or forced tool choice, then returns the request dictionary.

**Call relations**: `_complete_chat` calls this immediately before starting the Chat Completions stream. It calls `_reasoning_effort` to decide whether a reasoning parameter is legal and needed.

*Call graph*: calls 2 internal fn (_reasoning_effort, openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 755–826)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one Chat Completions streaming request and yields UFO model events as they arrive, ending with usage. It also retries safe failures, detects empty provider responses, and reports truncation or mid-stream interruption correctly.

**Data flow**: It receives a UFO model request. It builds chat request arguments, opens the OpenAI stream, yields a stream-start event, converts chunks through `_ChatStream`, retries before visible output on transport or retryable status errors, yields any available usage before failures, and finally yields usage or raises the appropriate terminal error.

**Call relations**: `OpenAIClient.complete` calls this for models using the Chat Completions surface. It depends on `_chat_kwargs`, `_ChatStream`, and `_OpenAIRetry` to separate request building, stream parsing, and retry policy.

*Call graph*: calls 3 internal fn (_chat_kwargs, __init__, __init__); called by 1 (complete); 3 external calls (__init__, __init__, emit_metric).


##### `OpenAIClient._complete_responses`  (lines 828–895)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one Responses API streaming request and yields UFO model events as they arrive, ending with saved reasoning blocks and usage. It is the Responses counterpart to `_complete_chat`.

**Data flow**: It receives a UFO model request. It resolves reasoning effort, builds a Responses request, opens the stream, yields a stream-start event, converts stream events through `_ResponsesStream`, retries safe failures, handles provider-injected stream errors, retries empty responses, then yields collected reasoning blocks followed by final usage.

**Call relations**: `OpenAIClient.complete` calls this for Responses models and all Codex account-token clients. It uses `responses_request`, `_ResponsesStream`, `_reasoning_effort`, and `_OpenAIRetry` to translate, stream, and recover from provider problems.

*Call graph*: calls 4 internal fn (_reasoning_effort, __init__, responses_request, __init__); called by 1 (complete); 3 external calls (__init__, __init__, emit_metric).


### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `model calls and tool execution`

OpenRouter is a routing service: one API key and one OpenAI-style interface can reach many different model providers. This file is the adapter that makes that service look like the project's normal `ModelClient`, which is the common interface the rest of the system uses for chat models. Without it, OpenRouter models would not appear in the model registry, streaming replies would not be translated into the system's events, and usage costs could not be counted reliably.

The file also defines two tools: `generate_image` and `generate_video`. These are kept separate from chat models because their billing is not token-based. An image is charged per generated image, and a video is charged per output second. The tools send requests to OpenRouter, wait for results when needed, save files under workspace folders, and add the charge to the turn's ledger when the platform key paid for it.

A lot of the code is defensive. It checks model-specific limits before sending requests, retries provider failures before any visible output has been shown, works around OpenRouter quirks, and verifies downloaded media before writing it. Think of it as a travel adapter: it lets the rest of the system plug into OpenRouter safely, while hiding differences in plugs, voltage, receipts, and failure modes.

#### Function details

##### `openrouter_slug`  (lines 279–289)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Turns a model name into the provider/model form OpenRouter expects. It adds common provider prefixes for bare OpenAI and Anthropic model ids, while leaving already-qualified names alone.

**Data flow**: It receives a model string. If the string already contains a slash, it returns it unchanged; if it looks like an OpenAI or Claude name, it prefixes it; otherwise it passes the name through. The output is the model slug sent to OpenRouter.

**Call relations**: When the client builds an OpenRouter request, `OpenRouterModelClient._create_kwargs` calls this to choose the API model name. `_openrouter_messages` also calls it to detect Google models, because those need a special message workaround.

*Call graph*: called by 2 (_create_kwargs, _openrouter_messages).


##### `_chunk_provider`  (lines 292–297)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Reads which upstream provider OpenRouter chose for a streamed response chunk. This matters because a silent or broken provider can be excluded on a retry.

**Data flow**: It receives one streaming chunk from OpenRouter. It looks in the chunk's extra metadata for a `provider` value and returns it as text if present, otherwise returns nothing.

**Call relations**: `_OpenRouterStream.accept` calls this while reading each chunk. The saved provider name later helps `OpenRouterModelClient.complete` decide whether to retry an empty response while ignoring that provider.

*Call graph*: called by 1 (accept).


##### `_usage_of`  (lines 300–321)

```
def _usage_of(usage: CompletionUsage, cache_write_30m_rate: int) -> Usage
```

**Purpose**: Converts OpenRouter/OpenAI token accounting into the system's own `Usage` object. It separates normal prompt tokens, output tokens, cached prompt reads, and cache writes so billing can be correct.

**Data flow**: It receives the provider's usage report plus a flag-like rate telling whether 30-minute cache writes are priced. It validates that cached and cache-write counts do not exceed the total prompt tokens, subtracts them from normal input tokens, and returns a `Usage` record.

**Call relations**: `_OpenRouterStream.accept` calls this when a stream chunk includes usage. The resulting `Usage` is later yielded by `OpenRouterModelClient.complete` as the final accounting for the model call.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_contains_json_reference`  (lines 336–348)

```
def _contains_json_reference(value: object) -> bool
```

**Purpose**: Checks whether a JSON-like value contains schema reference keys such as `$ref`. This is used to avoid a known OpenRouter/Google problem with tool-result messages containing those references.

**Data flow**: It receives any nested Python value. It walks through dictionaries and lists, looking for special reference keys, and returns true as soon as it finds one; otherwise it returns false.

**Call relations**: `_openrouter_messages` calls this after parsing a tool result as JSON. If a Google-routed request contains those reference keys, the message is wrapped as plain text before being sent.

*Call graph*: called by 1 (_openrouter_messages).


##### `_openrouter_messages`  (lines 351–388)

```
def _openrouter_messages(model: str, system: str, messages: tuple[Message, ...], accepts_image_input: bool) -> list[dict[str, object]]
```

**Purpose**: Prepares the conversation messages for OpenRouter. It uses the system's OpenAI message translation, optionally removes images for models that cannot accept them, and applies a Google-specific JSON workaround.

**Data flow**: It receives the model name, system prompt, message history, and whether the model accepts image input. It may strip images, converts messages into OpenAI-style dictionaries, and for Google models wraps certain tool-result JSON as text so OpenRouter will accept it. It returns the list of API-ready messages.

**Call relations**: `OpenRouterModelClient._create_kwargs` calls this while building the chat-completion request. Inside, it relies on `omit_images`, `openai_messages`, `openrouter_slug`, JSON parsing/encoding, and `_contains_json_reference`.

*Call graph*: calls 2 internal fn (_contains_json_reference, openrouter_slug); called by 1 (_create_kwargs); 4 external calls (dumps, loads, omit_images, openai_messages).


##### `_OpenRouterRetry.status`  (lines 399–435)

```
async def status(self, error: openai.APIStatusError, yielded: bool) -> '_OpenRouterRetry'
```

**Purpose**: Decides what to do after OpenRouter returns an HTTP status error, such as rate limiting or a server error. It retries only when it is safe: before any visible model output has been yielded.

**Data flow**: It receives the API error and whether the stream has already produced user-visible events. If the error is retryable and retry limits are not exhausted, it waits using either `retry-after` or exponential backoff, logs and counts the retry, and returns an updated retry state. Otherwise it re-raises the original error.

**Call relations**: `OpenRouterModelClient.complete` calls this when the OpenAI SDK reports an API status error. This method uses logging, metrics, sleeping, and dataclass replacement to carry the next retry state back to the main streaming loop.

*Call graph*: 4 external calls (sleep, replace, emit_metric, log).


##### `_OpenRouterRetry.stream_error`  (lines 437–460)

```
def stream_error(self, error: openai.APIError, yielded: bool) -> '_OpenRouterRetry'
```

**Purpose**: Decides what to do when OpenRouter injects an error into the live streaming response. It has one special retry for a known Gemini abort, and otherwise turns the fault into a stream interruption.

**Data flow**: It receives the API error and whether output has already appeared. If it is the exact known Gemini abort before output and has not been retried yet, it logs and returns updated retry state. Otherwise it raises `ModelStreamInterrupted`, telling the engine that the current round should be discarded and retried at a higher level.

**Call relations**: `OpenRouterModelClient.complete` calls this when a streaming API error occurs. It either hands back a retry state for another attempt or hands off failure control by raising the system's stream-interruption exception.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (replace, emit_metric, log).


##### `_OpenRouterStream.__init__`  (lines 464–471)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates the small state tracker used while one OpenRouter stream is being read. It remembers whether output appeared, tool-call ids, usage, finish reason, provider, and generation id.

**Data flow**: It receives whether cache-write tokens should be counted as priced. It initializes empty state fields that will be filled as stream chunks arrive.

**Call relations**: `OpenRouterModelClient.complete` creates a fresh `_OpenRouterStream` for each attempt. Later, `_OpenRouterStream.accept` updates this state chunk by chunk.

*Call graph*: called by 1 (complete).


##### `_OpenRouterStream.accept`  (lines 473–503)

```
def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]
```

**Purpose**: Translates one OpenRouter streaming chunk into the system's model events. These events are the pieces the rest of the engine understands: text, tool-call starts, tool-call argument fragments, and usage.

**Data flow**: It receives one chat-completion chunk. It records generation id, provider, usage, and finish reason when present; then it turns content text into `TextDelta` events and tool-call fragments into `ToolCallStart` and `ToolCallDelta` events. It returns the events produced by that chunk and marks the stream as having yielded output if any appeared.

**Call relations**: `OpenRouterModelClient.complete` calls this for every streamed chunk from OpenRouter. It uses `_chunk_provider` and `_usage_of`, and its returned events are yielded directly to the model engine.

*Call graph*: calls 2 internal fn (_chunk_provider, _usage_of); 3 external calls (__init__, __init__, __init__).


##### `OpenRouterModelClient.complete`  (lines 540–595)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one chat-model request through OpenRouter and streams the answer back as system model events. It also enforces retries, interruption rules, empty-provider rerouting, truncation detection, and final usage reporting.

**Data flow**: It receives a `ModelRequest` containing the model, prompt, messages, tools, token budget, reasoning setting, and session id. It builds API arguments, opens a streaming request, yields a stream-start event, yields text and tool-call deltas as they arrive, then yields final usage. If certain failures happen, it retries, raises an interruption, or raises a truncation error.

**Call relations**: This is the main method the engine uses when an OpenRouter model is selected. It creates `_OpenRouterRetry` and `_OpenRouterStream`, calls `_create_kwargs` before each API attempt, feeds chunks through `_OpenRouterStream.accept`, and calls `_finish_usage` at the end.

*Call graph*: calls 4 internal fn (__init__, _create_kwargs, _finish_usage, __init__); 4 external calls (__init__, __init__, __init__, emit_metric).


##### `OpenRouterModelClient._finish_usage`  (lines 597–603)

```
async def _finish_usage(self, state: _OpenRouterStream) -> Usage
```

**Purpose**: Finds the final token usage for a completed stream. It uses usage included in the stream when available, and otherwise asks OpenRouter's generation endpoint for it.

**Data flow**: It receives the stream state after the response ends. If usage is already present, it returns it; if not, but there is a generation id and finish reason, it calls `_generation_usage`; if no usable accounting can be found, it raises an error.

**Call relations**: `OpenRouterModelClient.complete` calls this after a stream ends normally. When stream chunks did not include usage, this method hands off to `_generation_usage` for a follow-up lookup.

*Call graph*: calls 1 internal fn (_generation_usage); called by 1 (complete).


##### `OpenRouterModelClient._generation_usage`  (lines 605–662)

```
async def _generation_usage(self, generation_id: str, finish_reason: str) -> Usage | None
```

**Purpose**: Looks up token usage from OpenRouter's generation ledger when the stream did not include it. It retries short-lived missing or transport failures because OpenRouter's ledger can lag behind the finished stream.

**Data flow**: It receives a generation id and the finish reason seen in the stream. It repeatedly GETs the generation endpoint, tolerating temporary 404 or transport errors within a bounded retry window, validates the returned data, and returns a `Usage` object if the ledger matches the finished generation. It may return nothing for cancelled or mismatched generations, or raise a stream interruption if lookup never becomes reliable.

**Call relations**: `OpenRouterModelClient._finish_usage` calls this as a fallback. It uses HTTP transport, sleeps between retries, emits metrics for lookup retries, and raises `ModelStreamInterrupted` when the engine should rerun the round.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_finish_usage); 4 external calls (__init__, sleep, AsyncClient, emit_metric).


##### `OpenRouterModelClient._create_kwargs`  (lines 664–706)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Builds the keyword arguments sent to OpenRouter's chat-completions API. It is where model name, messages, tools, session pinning, reasoning settings, and ignored providers become one request.

**Data flow**: It receives the system's model request and a set of providers to avoid. It requires a session id, prepares OpenRouter's `extra_body`, maps reasoning options, converts messages, attaches tools if present, and returns a dictionary ready for the OpenAI SDK call.

**Call relations**: `OpenRouterModelClient.complete` calls this before each streaming attempt. It uses `openrouter_slug` and `_openrouter_messages`, and its output is passed straight into the OpenAI-compatible client.

*Call graph*: calls 2 internal fn (_openrouter_messages, openrouter_slug); called by 1 (complete).


##### `_model_client`  (lines 709–714)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: Creates an `OpenRouterModelClient` for one registered model spec and API key. It is the factory the model registry can call when it needs a live client.

**Data flow**: It receives a model specification and key. It builds an OpenAI SDK client pointed at OpenRouter's base URL, wraps it with the spec and key, and returns the ready OpenRouter client.

**Call relations**: `_openrouter` stores this function in each `ModelSpec`. Later, outside this file, the registry can call it to turn a listed model into a usable backend.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 717–737)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW, reasoning: ReasoningSupport=_REASONS, accepts_image_input: bool=True) -> ModelSpec
```

**Purpose**: Creates a complete model specification for one OpenRouter chat model. A model specification tells the system the model id, price, context size, reasoning support, credential slot, and client factory.

**Data flow**: It receives the model id, price, knowledge cutoff, and optional limits or capabilities. It fills in OpenRouter-specific defaults and returns a `ModelSpec` record.

**Call relations**: The file uses this helper to build `OPENROUTER_MODEL_SPECS`. The manifest later exposes those specs so the wider system can offer exactly these OpenRouter models.

*Call graph*: 1 external calls (__init__).


##### `GenerateImageInput._within_model_limits`  (lines 842–866)

```
def _within_model_limits(self) -> 'GenerateImageInput'
```

**Purpose**: Validates an image-generation request against the selected model's real limits. It catches impossible combinations before sending them to OpenRouter.

**Data flow**: It reads the requested image model, count, aspect ratio, and resolution. It rejects too many images, unsupported aspect ratios, and unsupported resolution tiers; if a model needs a resolution and none was supplied, it fills in the default. It returns the cleaned input object.

**Call relations**: Pydantic calls this validator when building `GenerateImageInput` for the `generate_image` tool. Because validation happens before `OpenRouterImages.generate`, the tool usually avoids provider-side 400 errors for known-bad combinations.


##### `_reported_cost_micro_usd`  (lines 874–886)

```
def _reported_cost_micro_usd(usage: object) -> int | None
```

**Purpose**: Extracts the cost OpenRouter reported and converts it to micro-dollars, where one dollar is one million micro-dollars. It also understands the alternate cost field used when the workspace brings its own upstream key.

**Data flow**: It receives a usage-like object. It looks first for `cost`, then for `cost_details.upstream_inference_cost`; if it finds a positive number, it converts dollars to micro-USD and returns it. If no positive cost is reported, it returns nothing so callers can use list pricing instead.

**Call relations**: Image charging calls this from `OpenRouterImages._charge`. Video job parsing calls it from `OpenRouterVideos._job`, so completed jobs can carry their reported cost forward.

*Call graph*: called by 2 (_charge, _job).


##### `OpenRouterImages.generate`  (lines 919–956)

```
async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Runs one image-generation tool call from request to saved workspace files. It sends the prompt to OpenRouter, decodes returned images, saves them, meters the cost when appropriate, and returns both file paths and image data to the model.

**Data flow**: It receives a tool context and validated image arguments. It gets the OpenRouter key, posts the generation request, turns provider errors into tool errors, decodes and checks images, writes each image into the sandbox, calculates cost, meters platform-paid usage, and returns a `ToolResult` containing JSON metadata plus image content.

**Call relations**: `_generate_image` creates an `OpenRouterImages` instance and calls this. During the flow it delegates refusal text to `_refusal`, image extraction to `_images`, file writing to `_save`, and pricing to `_charge`.

*Call graph*: calls 5 internal fn (meter_images, _charge, _images, _refusal, _save); 6 external calls (__init__, __init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterImages._refusal`  (lines 958–973)

```
def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str
```

**Purpose**: Builds a clear tool-error message when OpenRouter refuses or fails an image request. It preserves the provider's reason in a bounded form so the model can adjust and try again.

**Data flow**: It receives the image arguments and the HTTP response. It tries to read a JSON error message, falls back to raw response text, trims it to a safe length, and returns a sentence saying no image was generated.

**Call relations**: `OpenRouterImages.generate` calls this when the image POST returns an error status. The returned text is placed inside an error `ToolResult`.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterImages._images`  (lines 975–1005)

```
def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]
```

**Purpose**: Extracts, decodes, and size-checks images from an OpenRouter response. It prevents empty or oversized provider output from being saved.

**Data flow**: It receives the image request arguments and the decoded response body. It scans the response's data entries for base64 image strings, decodes them to bytes, applies a maximum byte limit, assigns a media type when missing, and returns `GeneratedImage` records. If no usable image exists, it raises `OpenRouterImageError`.

**Call relations**: `OpenRouterImages.generate` calls this after a successful HTTP response. The resulting image records are then passed to `_save` and also returned as `ImageContent`.

*Call graph*: called by 1 (generate); 3 external calls (__init__, __init__, b64decode).


##### `OpenRouterImages._save`  (lines 1007–1014)

```
async def _save(self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage) -> str
```

**Purpose**: Writes one generated image into the workspace using the right file extension. This makes the generated artifact available as a normal workspace file.

**Data flow**: It receives the tool context, original image arguments, the image index, and decoded image data. It chooses a suffix from the media type, builds a path under `generated-images/`, writes the bytes through the sandbox, and returns the saved path.

**Call relations**: `OpenRouterImages.generate` calls this once for each decoded image. The returned paths are included in the tool result so the model can tell the user which files were created.

*Call graph*: called by 1 (generate).


##### `OpenRouterImages._charge`  (lines 1016–1023)

```
def _charge(self, body: object, args: GenerateImageInput, images: int) -> int
```

**Purpose**: Computes the charge for an image generation in micro-USD. It trusts OpenRouter's reported cost when present and otherwise falls back to the known list price per image.

**Data flow**: It receives the response body, image arguments, and number of images. It tries `_reported_cost_micro_usd` on the response usage field; if that gives a value, it returns it. Otherwise it multiplies the selected model's fallback list price by the number of images.

**Call relations**: `OpenRouterImages.generate` calls this after images have been successfully saved. Its result is used both in the returned metadata and, when the workspace is not using its own key, in `ToolContext.meter_images`.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 1 (generate).


##### `_generate_image`  (lines 1026–1031)

```
async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Connects the registered `generate_image` tool to the OpenRouter image implementation. It ensures the tool is running inside the extension context so credentials are available.

**Data flow**: It receives the tool context and validated image arguments. It checks for extension context, creates `OpenRouterImages` with extension credentials and optional test transport, and returns the result of its `generate` method.

**Call relations**: The `GENERATE_IMAGE_TOOL` definition uses this as its handler. When an agent calls the tool, the tool system invokes this function, which hands the real work to `OpenRouterImages.generate`.

*Call graph*: 1 external calls (__init__).


##### `GenerateVideoInput._within_model_limits`  (lines 1087–1111)

```
def _within_model_limits(self) -> 'GenerateVideoInput'
```

**Purpose**: Validates a video-generation request against the chosen model's allowed duration, aspect ratios, and resolutions. It also fills in the default resolution used for billing.

**Data flow**: It reads the requested video model, duration, aspect ratio, and resolution. It rejects unsupported durations and options, fills the model's default resolution when none was supplied, and returns the cleaned input object.

**Call relations**: Pydantic calls this validator when creating `GenerateVideoInput` for the `generate_video` tool. That means `OpenRouterVideos.generate` receives an already-bounded request.


##### `OpenRouterVideos.generate`  (lines 1153–1194)

```
async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Runs one video-generation tool call from start to saved MP4 file. It starts the job, waits for completion, downloads the result, saves it, meters cost when appropriate, and returns file metadata.

**Data flow**: It receives a tool context and validated video arguments. It gets the OpenRouter key, posts a video request, converts immediate errors into tool errors, parses the returned job, polls until the job settles, reports provider failure if it did not complete, downloads the MP4, writes it to the workspace, calculates cost, meters platform-paid usage, and returns a `ToolResult` with the saved path and cost.

**Call relations**: `_generate_video` creates an `OpenRouterVideos` instance and calls this. The method delegates pieces of the work to `_refusal`, `_job`, `_settled`, `_failure`, `_download`, `_save`, and `_charge`.

*Call graph*: calls 8 internal fn (meter_videos, _charge, _download, _failure, _job, _refusal, _save, _settled); 5 external calls (__init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterVideos._refusal`  (lines 1196–1211)

```
def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str
```

**Purpose**: Builds a clear tool-error message when OpenRouter refuses to start a video job. It carries the provider's explanation back to the model in a bounded form.

**Data flow**: It receives the video arguments and the HTTP response. It tries to read a JSON error message, falls back to raw response text, trims it, and returns a sentence saying no video was generated.

**Call relations**: `OpenRouterVideos.generate` calls this when the initial video POST returns an error. The text becomes the content of an error `ToolResult`.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterVideos._job`  (lines 1213–1228)

```
def _job(self, body: object) -> VideoJob
```

**Purpose**: Parses OpenRouter's video job response into a small `VideoJob` record. It refuses to continue if there is no job id or status, because such a response cannot be polled safely.

**Data flow**: It receives a decoded response body. It extracts the job id, status, optional error message, and optional reported cost, converting the cost through `_reported_cost_micro_usd`. It returns a `VideoJob`, or raises `OpenRouterVideoError` if the response does not identify a job.

**Call relations**: `OpenRouterVideos.generate` calls this after starting a job. `_settled` also calls it after each poll response to update the current job state.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 2 (_settled, generate); 2 external calls (__init__, __init__).


##### `OpenRouterVideos._settled`  (lines 1230–1251)

```
async def _settled(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> VideoJob
```

**Purpose**: Polls a video job until it stops being pending or in progress. It puts a firm time limit on waiting so a stuck external job does not hold a conversation forever.

**Data flow**: It receives the video arguments, an HTTP client, and the current job. While the status is pending or in progress, it checks the deadline, sleeps for the poll interval, fetches the latest job state, and parses it with `_job`. It returns the final job state or raises `OpenRouterVideoError` on timeout or poll failure.

**Call relations**: `OpenRouterVideos.generate` calls this after the initial job is created. Its output determines whether generation continues to download or returns a provider failure message.

*Call graph*: calls 1 internal fn (_job); called by 1 (generate); 4 external calls (__init__, sleep, get, monotonic).


##### `OpenRouterVideos._failure`  (lines 1253–1257)

```
def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str
```

**Purpose**: Turns a finished-but-unsuccessful video job into a readable error message. It uses the provider's reason when one is available.

**Data flow**: It receives the original video arguments and the final job. It chooses the job's error text or a generic status message, trims it to the allowed length, and returns a sentence saying no video was generated.

**Call relations**: `OpenRouterVideos.generate` calls this when `_settled` returns a job whose status is not completed. The text is returned to the model as an error tool result.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._download`  (lines 1259–1278)

```
async def _download(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> bytes
```

**Purpose**: Downloads the completed MP4 and checks that it is present and not too large. This protects the workspace from empty or oversized provider output.

**Data flow**: It receives the video arguments, HTTP client, and completed job. It GETs the job content, raises a video error on HTTP failure, verifies the bytes are non-empty and below the size cap, and returns the raw MP4 bytes.

**Call relations**: `OpenRouterVideos.generate` calls this only after a job reaches the completed status. The returned bytes are then passed to `_save`.

*Call graph*: called by 1 (generate); 2 external calls (__init__, get).


##### `OpenRouterVideos._save`  (lines 1280–1284)

```
async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str
```

**Purpose**: Writes the finished video into the workspace as an MP4 file. This turns the remote generation result into a local artifact the user can share or inspect.

**Data flow**: It receives the tool context, video arguments, and raw video bytes. It builds a path under `generated-videos/`, writes the bytes through the sandbox, and returns the saved path.

**Call relations**: `OpenRouterVideos.generate` calls this after downloading the MP4. The returned path is included in the final tool result.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._charge`  (lines 1286–1294)

```
def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int
```

**Purpose**: Computes the charge for a video generation in micro-USD. It uses OpenRouter's reported job cost when present, otherwise it calculates a fallback from the model, resolution, and duration.

**Data flow**: It receives the video arguments and final job. If the job already carries a reported cost, it returns that; otherwise it looks up the selected model's per-second rate for the chosen resolution and multiplies by the requested duration.

**Call relations**: `OpenRouterVideos.generate` calls this after the video has been saved. The returned cost appears in the tool result and is passed to `ToolContext.meter_videos` when the platform key paid for the job.

*Call graph*: called by 1 (generate).


##### `_generate_video`  (lines 1297–1302)

```
async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Connects the registered `generate_video` tool to the OpenRouter video implementation. It ensures extension credentials are available before starting the real work.

**Data flow**: It receives the tool context and validated video arguments. It checks for extension context, creates `OpenRouterVideos` with credentials and optional test transport, and returns the result of its `generate` method.

**Call relations**: The `GENERATE_VIDEO_TOOL` definition uses this as its handler. When an agent calls the tool, the tool system invokes this function and it hands off to `OpenRouterVideos.generate`.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1315–1331)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It lists the OpenRouter chat models, the image and video tools, and the credential slot used for the API key.

**Data flow**: It takes no input. It builds and returns a `Manifest` containing the extension name, version, model specs, tool definitions, and an `openrouter_api_key` credential description.

**Call relations**: The extension loader calls this to discover what the file provides. The returned manifest is how the rest of the system learns which models, tools, and credentials belong to OpenRouter.

*Call graph*: 2 external calls (__init__, __init__).


### Reply Text Filtering
Reply helpers detect hidden reply-to markup and prevent private spans from leaking during live streaming or final display.

### `core/src/ufo/harness/replies.py`

`domain_logic` · `live streaming and round completion`

The project lets a model mark part of its output as a direct reply to a specific message, using tags like `<reply-to message="..."> ... </reply-to>`. Those tags are instructions for the system, not words a person should see. This file is the safety layer that separates the two.

After a round is complete, `marked_replies` scans the full text, pulls out every properly closed reply span, and records who it was meant for by reading the message id in the tag. It also returns a cleaned version of the full text with the tags removed, so the conversation history keeps the spoken words but not the control markup.

While the model is still streaming text chunk by chunk, `ReplyRedaction` does a harder job. A tag may be split across chunks, like receiving “<rep” now and “ly-to...” later. So it temporarily holds back uncertain text until it knows whether it is ordinary prose or part of a reply tag. Anything inside a reply span is withheld from the live stream, because it will be delivered later as its own member-visible reply. Broken or unfinished markup is not shown. In short, this file acts like a mail sorter: it removes routing labels before delivery and prevents half-printed labels from leaking onto the envelope.

#### Function details

##### `marked_replies`  (lines 42–51)

```
def marked_replies(text: str) -> tuple[tuple[MarkedReply, ...], str]
```

**Purpose**: This function reads a completed block of model text and extracts the reply sections that were wrapped in `<reply-to ...>` tags. It also produces the same text with the reply markup removed, so the saved conversation does not contain instructions meant only for the system.

**Data flow**: It takes the full text of a round as input. It searches for complete opener-and-closer reply spans, strips any reply tags from the spoken words inside them, turns each non-empty span into a `MarkedReply`, and uses `_named_message` to turn the named message id into a real UUID when possible. It returns two things: the collected replies in order, and the original text with all reply-to opening and closing tags removed.

**Call relations**: This is the completed-round counterpart to the live redaction flow. When the round is done, callers use it to discover which words should be delivered as marked replies. During that extraction, it asks `_named_message` to interpret the message id, then builds `MarkedReply` records for the rest of the system to use.

*Call graph*: calls 1 internal fn (_named_message); 1 external calls (__init__).


##### `_named_message`  (lines 54–58)

```
def _named_message(named: str) -> UUID | None
```

**Purpose**: This helper checks whether the message value written in a reply tag is a valid UUID, which is a standard unique identifier. If it is not valid, the reply is still kept, but it is marked as not pointing to a known message id.

**Data flow**: It receives the raw string from the tag’s `message` field. It trims surrounding whitespace and tries to convert it into a UUID. If that works, it returns the UUID; if conversion fails, it returns `None`.

**Call relations**: It is called by `marked_replies` while reading each completed reply span. Its small job keeps identifier validation separate from the larger job of scanning and cleaning the text.

*Call graph*: called by 1 (marked_replies); 1 external calls (UUID).


##### `ReplyRedaction.feed`  (lines 77–99)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This method filters one new chunk of live model output and returns only the part that is safe to show immediately. It hides reply spans and their tags, even when the tag or closer is split across multiple streamed chunks.

**Data flow**: It receives the next text chunk from the stream and adds it to text already being held back. If it is currently inside a reply span, it looks for the closing `</reply-to>` tag; until that closer appears, it keeps only the possible tail that might become the closer and publishes nothing from inside. If it is outside a reply span, it publishes ordinary text before an opener, enters hidden mode when it sees a valid opener, and otherwise uses `_settled_chars` to decide how much text is definitely not part of a future tag. It returns the cleaned text that can be shown now, with any reply markup removed, and updates its own stored state for the next chunk.

**Call relations**: This method is used during live streaming, before the whole round is available. It relies on `_growing_suffix` when waiting for a closing tag and `_settled_chars` when deciding whether a trailing `<` might still turn into markup. Later, once the round is finished, `marked_replies` can extract the withheld reply content from the complete text.

*Call graph*: calls 2 internal fn (_growing_suffix, _settled_chars); 1 external calls (find).


##### `_growing_suffix`  (lines 102–107)

```
def _growing_suffix(text: str, token: str) -> str
```

**Purpose**: This helper keeps just enough trailing text to recognize a token that may be split across stream chunks. In this file, it helps notice a closing reply tag that starts in one chunk and finishes in the next.

**Data flow**: It receives some held text and a target token, such as `</reply-to>`. It checks the end of the text for the longest suffix that could be the beginning of that token. It returns that suffix, or an empty string if no ending characters could grow into the token.

**Call relations**: It is called by `ReplyRedaction.feed` while the stream is inside a hidden reply span and no full closing tag has appeared yet. By keeping only the possible beginning of the closer, it lets the redactor drop hidden reply content while still recognizing the closer when later chunks arrive.

*Call graph*: called by 1 (feed).


##### `_settled_chars`  (lines 110–119)

```
def _settled_chars(text: str) -> int
```

**Purpose**: This helper decides how much currently held text is safe to publish because it cannot still become reply markup. It protects against showing a partial tag that begins at the end of a chunk.

**Data flow**: It receives held text from the live stream. It looks for the last `<`, because that is the only place a tag could begin. If there is no `<`, all text is safe. If the tail could still grow into `<reply-to...` or `</reply-to>`, it reports that only the text before that tail is safe. Otherwise it reports that the whole text can be published.

**Call relations**: It is called by `ReplyRedaction.feed` when the stream is not currently inside a reply span and no full opener has been found. Its answer tells `feed` what can be released now and what must be held for the next chunk.

*Call graph*: called by 1 (feed).


### Round Orchestration
The round runner consumes standardized provider events, displays safe streamed output, and assembles the final result with tool calls, reasoning, usage, and timing.

### `core/src/ufo/harness/rounds.py`

`orchestration` · `request handling during one model round`

A model provider does not usually return a whole answer all at once. It sends a stream of small events: text fragments, tool-call fragments, usage data, and sometimes errors. This file turns that stream into something the engine can trust.

The main piece is ModelRoundRunner. It starts a provider request, reads each event, and gives the work to _RoundState, which is like a clipboard for the current round. Text is stored for the final record, but it is also buffered and passed through a TextFilter before being published live. That filter is important because some text may be meant for internal use and must not be shown to the user.

The runner also flushes buffered text on a timer, not just when enough bytes arrive. This keeps live output feeling responsive without publishing every tiny fragment separately. Tool calls are assembled from their streamed JSON pieces, then turned into real tool-call objects only after the stream finishes successfully.

A key rule is that a round is only committed when the stream completes. If the provider connection dies partway through, the code returns a CollectedRound marked with error details and partial output, so the caller can discard and retry it instead of treating a half-answer as final.

#### Function details

##### `TextFilter.feed`  (lines 23–23)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This is the expected interface for a text filter that receives streamed text a little at a time and returns only the part that is safe to show live. Different callers can provide different filters, as long as they follow this shape.

**Data flow**: A text chunk goes in. The filter updates whatever internal memory it needs, removes or delays text that should not be shown, and returns the visible text that may be published now.

**Call relations**: _RoundState.flush calls this whenever buffered model text is ready to be shown. The runner does not know the filtering rules itself; it relies on this interface so the caller can decide what is safe.


##### `ModelStreamInterrupted.__init__`  (lines 34–36)

```
def __init__(self, kind: str, message: str) -> None
```

**Purpose**: This creates a special error for cases where the model stream stopped because of a temporary provider or network problem. The extra kind value tells the caller what sort of interruption happened, so it can decide whether to retry.

**Data flow**: An interruption kind and a human-readable message go in. The message becomes the normal exception text, and the kind is stored on the error object for later inspection.

**Call relations**: Model clients such as the OpenAI, Anthropic, and OpenRouter integrations raise this when a live stream fails in a retryable way. Later, _RoundState.result recognizes this exact error type and records its kind in the collected round.

*Call graph*: called by 9 (status, transport, _complete_chat, _complete_responses, status, transport, _generation_usage, complete, stream_error).


##### `ModelRoundRunner.run`  (lines 97–128)

```
async def run(self, request: RequestT, text_filter: TextFilter) -> CollectedRound[ToolCallT, ReasoningT, UsageT]
```

**Purpose**: This runs one complete model-provider stream from start to finish. It publishes safe live text along the way and returns a single CollectedRound describing either the completed response or the interrupted partial attempt.

**Data flow**: A model request and a text filter go in. The function creates a fresh round state, starts a small background timer for flushing text, reads provider events one by one, records any error, flushes any remaining text, measures total time, and returns the final collected round.

**Call relations**: This is the public entry point for the file’s main behavior. It creates _RoundState to do the event-by-event collection, starts the inner pace task to keep live output moving, calls state.accept for every provider event, and finally asks state.result to package the outcome.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (Event, create_task).


##### `ModelRoundRunner.run.pace`  (lines 106–111)

```
async def pace() -> None
```

**Purpose**: This small background task makes sure streamed text is published regularly, even if the buffer has not reached its size limit. Without it, a slow trickle of text might sit unseen until much later.

**Data flow**: It watches a stop signal. Until that signal is set, it waits for the configured flush interval, then asks the round state to flush any buffered text.

**Call relations**: ModelRoundRunner.run starts this task before reading the provider stream and stops it when the stream ends or errors. It works alongside state.accept: accept flushes when there is a lot of text, while pace flushes when enough time has passed.

*Call graph*: 1 external calls (wait_for).


##### `_RoundState.__init__`  (lines 132–151)

```
def __init__(self, runner: ModelRoundRunner[RequestT, ToolCallT, ReasoningT, UsageT], text_filter: TextFilter, started: float) -> None
```

**Purpose**: This prepares the scratch space for one model round. It stores the runner settings, the text filter, timing start point, buffers, tool-call assembly areas, reasoning records, usage records, and timing markers.

**Data flow**: The runner, text filter, and start time go in. A new state object comes out with empty lists and dictionaries ready to collect text, tool calls, reasoning, usage, and timing information.

**Call relations**: ModelRoundRunner.run creates one _RoundState at the start of every round. The rest of the file’s work happens through this state object as events arrive, text is flushed, and the final result is built.

*Call graph*: called by 1 (run); 1 external calls (Lock).


##### `_RoundState.accept`  (lines 153–187)

```
async def accept(self, event: object) -> None
```

**Purpose**: This reads one event from the provider stream and puts its information in the right place. It is the sorter for the stream, deciding whether an event means text, a tool call, reasoning, usage, or a timing milestone.

**Data flow**: One raw provider event goes in. The function checks what kind of event it is, then updates the state: text is appended and maybe flushed, tool-call IDs and JSON fragments are saved, reasoning and usage are collected, and timing milestones are recorded. If the event type is unknown, it raises an error.

**Call relations**: ModelRoundRunner.run calls this for every event produced by the provider. It calls _mark_visible when the first user-visible thing appears, _elapsed_ms for timing, and flush when the text buffer has grown large enough.

*Call graph*: calls 3 internal fn (_elapsed_ms, _mark_visible, flush); 1 external calls (cast).


##### `_RoundState.flush`  (lines 189–199)

```
async def flush(self) -> None
```

**Purpose**: This publishes any buffered text that is ready to be shown to the live stream. It uses a lock, which is a safeguard that stops two flushes from changing the same buffer at the same time.

**Data flow**: The current text buffer is read. Its contents are joined, passed through the text filter, then the buffer and byte count are cleared. If the filter returns visible text, that text is sent to the runner’s publish_text callback; if publishing returns an awaitable task, the function waits for it to finish.

**Call relations**: _RoundState.accept calls this when enough text has accumulated, and the runner’s background pacing task also calls it on a timer. This is the point where collected model text becomes visible outside the runner.

*Call graph*: called by 1 (accept); 1 external calls (isawaitable).


##### `_RoundState.result`  (lines 201–244)

```
def result(self, error: Exception | None, wall_ms: int) -> CollectedRound[ToolCallT, ReasoningT, UsageT]
```

**Purpose**: This turns the scratch state for a round into the final CollectedRound object. It has two paths: one for failed or interrupted streams, and one for streams that completed successfully.

**Data flow**: An optional error and the total wall-clock time go in. If there was an error, the function returns a collected round with error details, any usage already seen, and partial output. If there was no error, it requires usage data, parses accumulated tool-call JSON, builds tool-call objects, and returns the completed text, tool calls, reasoning, usage, and timing.

**Call relations**: ModelRoundRunner.run calls this after the provider stream has ended and all text has been flushed. It also recognizes ModelStreamInterrupted so the caller can tell a retryable stream interruption apart from other errors.

*Call graph*: 3 external calls (__init__, loads, cast).


##### `_RoundState._mark_visible`  (lines 246–251)

```
def _mark_visible(self) -> None
```

**Purpose**: This records when the first visible event in the round happened. That can mean the first text fragment or the start of a tool call, and it helps measure how quickly the model began producing something useful.

**Data flow**: It reads the current state. If the first-visible timestamp is still empty, it calculates elapsed milliseconds, stores that value, and optionally reports a milestone named first_visible_event.

**Call relations**: _RoundState.accept calls this when it sees text or a tool-start event. It calls _elapsed_ms to compute the timing and uses the runner’s milestone callback if one was supplied.

*Call graph*: calls 1 internal fn (_elapsed_ms); called by 1 (accept).


##### `_RoundState._elapsed_ms`  (lines 253–254)

```
def _elapsed_ms(self) -> int
```

**Purpose**: This calculates how many milliseconds have passed since the round began. It gives the rest of the file a consistent way to record timing markers.

**Data flow**: It reads the runner’s monotonic clock and the stored start time. It subtracts start from now, converts seconds to milliseconds, and returns that integer.

**Call relations**: _RoundState.accept uses this to mark when the provider stream started, and _mark_visible uses it to mark when the first visible output appeared.

*Call graph*: called by 2 (_mark_visible, accept).
