# Model request streaming and provider normalization  `stage-10.2`

This stage is shared behind-the-scenes support for talking to AI models. Its job is to hide the differences between providers, so the rest of UFO can ask for a model response in one standard way and receive one standard stream of events back. The empty __init__.py simply makes the models folder importable. interface.py defines the common “contract”: what a request looks like, how streamed text, tool calls, images, reasoning notes, and responses are represented, and how image-heavy conversations are trimmed to stay within provider limits. anthropic.py translates that common format to Anthropic Claude’s API and translates Claude’s live reply back into UFO events, including deciding which errors are worth retrying. openai.py does the same for OpenAI-style APIs, including both older chat streaming and newer response streaming paths. The OpenRouter extension plugs in another provider route, using an OpenAI-like interface while adding cost tracking, credential handling, retries, and image or video generation tools. Together, these parts act like adapters for different power outlets: each provider is different, but the rest of the system can plug in the same way.

## Files in this stage

### Shared model contract
Package setup and common data structures define the internal request, event, response, tool-call, image, and reasoning format used by all providers.

### `core/src/ufo/harness/models/__init__.py`

`other` · `import/package discovery`

This is an intentionally empty package file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Here, it means code elsewhere can refer to `ufo.harness.models` as a real module path.

Think of it like a label on a drawer: the drawer may contain useful items in other files, but this label is what lets the rest of the system find the drawer by name. Without this file, depending on the Python version and packaging setup, imports that expect `ufo.harness.models` to be a normal package could fail or behave differently.

Because the file is empty, it does not create classes, load data, run setup code, or change program state. Its value is structural: it keeps the project’s module layout clear and import-friendly.


### `core/src/ufo/harness/models/interface.py`

`data_model` · `request preparation and model streaming`

Different model providers have different APIs, but the rest of the project should not need to care about every provider’s private shape. This file acts like a shared contract: it defines the standard request object, the standard message and content blocks, and the stream of events that any model client must produce. In everyday terms, it is the form everyone fills out before sending work to a model, and the envelope everyone expects back.

The file includes data shapes for text, images, tool calls, tool results, and several kinds of reasoning blocks. Reasoning blocks are carried carefully because some providers require them to be sent back unchanged in later requests, like a sealed receipt that proves the conversation continued correctly. `ModelRequest` gathers everything needed for one model call: the chosen model, system instructions, messages, tools, token budget, reasoning setting, cache hints, and retry behavior.

It also defines `ModelClient`, a protocol, meaning a promise that any real client must provide a `complete` method that streams model events. Finally, the image helpers protect calls from failing when there are too many or too-large images. They either trim older images down to provider limits or replace all images when a text-only model is used, leaving a clear text note so the model knows something was omitted.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool`  (lines 165–170)

```
def _forced_choice_names_an_offered_tool(self) -> 'ModelRequest'
```

**Purpose**: This validation check makes sure that if a request forces the model to use one specific tool, that tool was actually offered in the request. It prevents sending an impossible instruction, such as telling the model to use a tool that is not available.

**Data flow**: It reads the completed `ModelRequest`, especially `tool_choice` and the list of `tools`. If no forced tool is named, it leaves the request unchanged. If a forced tool is named, it searches the offered tools by name; when it finds a match it returns the request, and when it does not it raises an error before the bad request can be sent.

**Call relations**: This runs automatically as part of building or validating a `ModelRequest`. It is an early guardrail before any `ModelClient.complete` implementation tries to send the request to a provider.


##### `ModelClient.complete`  (lines 224–224)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the required method that every concrete model client must provide. It takes one standardized `ModelRequest` and returns the model’s answer as a stream of events, such as text pieces, tool-call pieces, reasoning blocks, and usage information.

**Data flow**: A caller gives it a complete model request. A real implementation translates that request into the provider’s API format, sends it, and yields standardized `ModelEvent` objects as the provider responds. The protocol itself does not implement the behavior; it defines the shape that implementations must follow.

**Call relations**: Other parts of the system can call `complete` without knowing whether the backing provider is Anthropic, OpenAI, OpenRouter, or something else. Provider-specific clients fulfill this protocol and hand back events in the shared format defined in this file.


##### `trim_images`  (lines 232–263)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function reduces image content in messages so a request stays within provider image limits. It keeps the newest images first, because recent images are usually most relevant to the current turn, and replaces dropped images with a short text marker.

**Data flow**: It receives a tuple of messages. It finds every image, counts how many appear per message and across the full request, then applies a total byte budget by walking from newest kept image to oldest. Images outside those limits are marked for removal, and affected messages are copied with those images replaced by `[image omitted: over the provider image limit]`. If no images need removal, the original messages are returned.

**Call relations**: Before a model client sends messages to a provider, it can call this to avoid a request being rejected for too many images or too much image data. It relies on `_image_positions` to find images, `_image_data_len` to measure their encoded size, and `_trim_message` to produce the safe replacement messages.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `omit_images`  (lines 266–274)

```
def omit_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function removes all images from a message set for models that can only read text. It does not silently delete them; it replaces each image with a clear note saying the image was omitted because the model does not support images.

**Data flow**: It receives messages, finds every image position, and if there are none it returns the messages unchanged. If images are present, it copies the affected messages and replaces each image with `[image omitted: model accepts text input only]`.

**Call relations**: A text-only model path can call this during request preparation. It uses `_image_positions` to locate images and `_trim_message` to rewrite the messages while preserving the rest of the conversation.

*Call graph*: calls 2 internal fn (_image_positions, _trim_message).


##### `_image_data_len`  (lines 277–288)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This helper measures the size of one image’s base64 data string. That size is used as a practical budget number when deciding which images can stay in a request.

**Data flow**: It receives all messages plus one image position, which points either to a top-level image block or an image nested inside a tool result. It follows that position, reads the image source data, and returns the length of that data string. If the position does not actually point to an image, it raises an error because the caller’s bookkeeping is wrong.

**Call relations**: `trim_images` calls this while enforcing the request-wide image byte budget. It is not used by `omit_images`, because that path removes every image regardless of size.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 291–311)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This helper scans messages and records where every inline image lives. It understands both normal image blocks and images nested inside a tool result, such as a screenshot returned by a browser tool.

**Data flow**: It receives the full message tuple. It walks through messages from oldest to newest, skips plain string messages, and inspects structured content blocks. For each image it returns a position made of the message index, block index, and, when needed, the nested index inside a tool result.

**Call relations**: Both `trim_images` and `omit_images` start by calling this. It gives those functions a simple map of image locations so they can decide what to keep or replace without mixing that search logic into the trimming rules.

*Call graph*: called by 2 (omit_images, trim_images).


##### `_trim_message`  (lines 314–341)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]], replacement: str) -> Message
```

**Purpose**: This helper rewrites one message by replacing selected images with a text placeholder. It preserves everything else in the message, including non-image content and images that are not marked for removal.

**Data flow**: It receives one message, that message’s index, a set of image positions to drop, and the replacement text to use. If the message is plain text, it returns it unchanged. If it has structured blocks, it builds a new block list where selected top-level images become `TextBlock` placeholders and selected nested tool-result images are replaced inside a copied tool-result block. It returns a copied `Message` with updated content.

**Call relations**: `trim_images` calls this after choosing only the over-limit images to remove. `omit_images` calls it after marking every image for removal. It creates replacement `TextBlock` objects and uses the message model’s copy operation so the original message objects are not edited in place.

*Call graph*: called by 2 (omit_images, trim_images); 2 external calls (__init__, model_copy).


### Provider streaming adapters
Provider bridges translate UFO's internal model format to Anthropic, OpenAI-compatible, and OpenRouter APIs while normalizing streamed replies, retries, costs, and credentials.

### `core/src/ufo/harness/models/anthropic.py`

`io_transport` · `request handling`

This file lets the rest of the system talk to Anthropic without needing to know Anthropic's exact wire format. Think of it as a translator and traffic cop: it rewrites UFO's messages, tools, images, and reasoning blocks into the shape Claude expects, then reads Claude's live stream back and converts it into simple events such as text chunks, tool-call starts, tool-call JSON pieces, reasoning records, and final token usage.

It also protects the calling code from common provider failures. Network timeouts, dropped streams, rate limits, and temporary server errors are retried with backoff, meaning the wait time grows after each failed attempt. But once visible output has already been streamed, the file treats a failure differently: it raises a special interruption so the larger round logic can throw away the partial answer and try the whole round again safely.

A few provider-specific details matter here. OAuth credentials use different headers than API keys. Anthropic reasoning blocks must be saved and returned in the exact order the provider gave them, because later tool-result requests may need to echo them back unchanged. Empty end-turn responses are retried a few times because reasoning alone is not considered an answer.

#### Function details

##### `anthropic_sdk_client`  (lines 57–73)

```
def anthropic_sdk_client(credential: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the Anthropic software client used to make API calls. It chooses the right authentication style depending on whether the credential is an OAuth token or a normal API key.

**Data flow**: It receives one credential string. It checks the prefix to decide whether it is an OAuth token, then builds an Anthropic async client with retries turned off and a fixed timeout. The result is a ready-to-use client object; retry behavior is left to this file instead of the Anthropic SDK.

**Call relations**: This is the setup doorway for Anthropic access. It calls is_oauth_credential to classify the credential, then hands the chosen authentication settings to anthropic.AsyncAnthropic so later code can send requests through the official SDK.

*Call graph*: calls 1 internal fn (is_oauth_credential); 1 external calls (AsyncAnthropic).


##### `is_oauth_credential`  (lines 76–78)

```
def is_oauth_credential(credential: str) -> bool
```

**Purpose**: Tells whether a credential string looks like an Anthropic OAuth access token rather than an API key. This matters because Anthropic expects the two kinds of credentials to be sent differently.

**Data flow**: It receives a credential string and checks whether it starts with Anthropic's OAuth token prefix. It returns true for OAuth-style credentials and false otherwise.

**Call relations**: anthropic_sdk_client calls this before creating the SDK client, so authentication headers are chosen correctly at startup or client construction time.

*Call graph*: called by 1 (anthropic_sdk_client).


##### `_anthropic_image`  (lines 81–85)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts UFO's internal image description into the image block format Anthropic expects. This lets images travel alongside text in a model message.

**Data flow**: It receives an ImageSource containing a media type and base64 image data. It wraps those fields in Anthropic's image dictionary shape and returns that dictionary.

**Call relations**: anthropic_content uses this when preparing normal message content, and _anthropic_tool_result_part uses it when a tool result contains an image. It is the shared image-format adapter.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 88–93)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic's format. Tool results can be text or images, and Anthropic needs each piece labeled clearly.

**Data flow**: It receives one tool-result content block. If the block is text, it returns an Anthropic text dictionary. If the block is an image, it delegates to _anthropic_image and returns the resulting image dictionary.

**Call relations**: anthropic_content calls this while building tool-result messages. When an image appears inside a tool result, this function hands that image conversion to _anthropic_image.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 96–130)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Translates UFO's message content into Anthropic message content. It covers plain text, images, tool calls, tool results, and Anthropic reasoning blocks.

**Data flow**: It receives either a simple string or a tuple of structured content blocks. A string passes through unchanged. Structured blocks are examined one by one and converted into Anthropic dictionaries; OpenAI-style reasoning items are skipped because Anthropic cannot use another provider's reasoning format. The result is either the original string or a list of Anthropic-ready content blocks.

**Call relations**: AnthropicClient._request_kwargs calls this while building the final API request. It uses _anthropic_image for image blocks and _anthropic_tool_result_part for nested tool-result content.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (_request_kwargs).


##### `_AnthropicRetry.transport`  (lines 141–176)

```
async def transport(self, error: Exception, yielded: bool) -> _AnthropicRetry
```

**Purpose**: Decides what to do after a network-level streaming failure, such as a timeout or dropped connection. It retries safe failures before any output is shown, but marks mid-answer failures as interrupted.

**Data flow**: It receives the exception and a flag saying whether the stream had already produced visible output. It increases the attempt count. If output was already yielded or the retry budget is exhausted, it logs the failure and raises an error. Otherwise it logs and records a retry metric, waits for the current delay, then returns a new retry state with a longer delay.

**Call relations**: AnthropicClient.complete uses this inside its stream loop when transport errors happen. This method logs through the observability layer, sleeps before retrying, and may raise ModelStreamInterrupted so the larger round system knows the partial answer cannot be trusted.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_AnthropicRetry.status`  (lines 178–238)

```
async def status(self, error: anthropic.APIStatusError, yielded: bool) -> _AnthropicRetry
```

**Purpose**: Decides what to do after Anthropic reports an API status error, such as bad credentials, rate limiting, or a temporary provider problem. It separates errors that should never be retried from errors that may recover after waiting.

**Data flow**: It receives an Anthropic status error and a flag saying whether output was already yielded. It checks the HTTP status code, which is the web-style number describing the failure. Rejected keys become a clear credential error. Deterministic client mistakes are raised immediately. Retryable failures wait, using Anthropic's retry-after header when present, then return an updated retry state. Very long rate-limit waits can be deferred by raising ModelRetryAfter instead of sleeping.

**Call relations**: AnthropicClient.complete calls this when the provider or stream reports a status error. It logs failures and retries, emits retry metrics, may sleep before another request, and may hand special errors to the wider round logic for credential replacement, rate-limit handling, or stream interruption.

*Call graph*: calls 2 internal fn (__init__, __init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_AnthropicStream.__init__`  (lines 242–253)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh collector for one Anthropic streaming response. It starts with no emitted output, no usage counts, no tool-call mappings, and no saved reasoning blocks.

**Data flow**: It takes no outside data beyond the new object being created. It initializes dictionaries and counters that will be filled as stream events arrive. The result is a blank stream state object ready to accept Anthropic events.

**Call relations**: AnthropicClient.complete creates one of these for each request attempt. The object then receives every raw event through _AnthropicStream.accept and later provides final usage and reasoning information.

*Call graph*: called by 1 (complete).


##### `_AnthropicStream.accept`  (lines 255–299)

```
def accept(self, event: object) -> tuple[ModelEvent, ...]
```

**Purpose**: Consumes one raw Anthropic stream event and turns it into zero or more UFO model events. It is the main live translator for the streaming response.

**Data flow**: It receives one event from Anthropic. Depending on the event type, it may record input-token usage, remember a tool-call id, emit a text delta, emit a tool-call start or partial JSON update, collect hidden reasoning text, store redacted reasoning, or record the final stop reason and output-token count. It returns the UFO events produced by that one input event and updates the stream state.

**Call relations**: AnthropicClient.complete calls this for every item in the provider stream and yields any events it returns. accept delegates usage recording to _record_input_usage and finishes reasoning blocks through _close_thinking when Anthropic says a thinking block has ended.

*Call graph*: calls 2 internal fn (_close_thinking, _record_input_usage); 4 external calls (__init__, __init__, __init__, __init__).


##### `_AnthropicStream._record_input_usage`  (lines 301–309)

```
def _record_input_usage(self, usage: Any) -> None
```

**Purpose**: Saves input-token and cache-token counts reported at the start of an Anthropic message. These counts are needed for the final usage record.

**Data flow**: It receives Anthropic's usage object. It copies the input token count, cache read count, and cache write counts into the stream state, handling both older and newer Anthropic cache-report shapes. It returns nothing, but the stream object now has usage data stored.

**Call relations**: _AnthropicStream.accept calls this when it sees the stream's message-start event. Later, _AnthropicStream.has_usage and _AnthropicStream.usage read the values it stored.

*Call graph*: called by 1 (accept).


##### `_AnthropicStream._close_thinking`  (lines 311–320)

```
def _close_thinking(self, index: int) -> None
```

**Purpose**: Finishes one Anthropic thinking block by joining its streamed pieces and saving it with its required signature. The signature is important because Anthropic expects reasoning to be echoed back unchanged in later related requests.

**Data flow**: It receives the numeric stream index for a thinking block. It looks up the collected text pieces and signature for that index. If the signature is missing, it raises an error because the reasoning block would be invalid. Otherwise it joins the text, creates a ThinkingBlock, appends it to the saved reasoning list, and removes the temporary pieces.

**Call relations**: _AnthropicStream.accept calls this when Anthropic signals that a thinking block has stopped. The completed reasoning block is later yielded by AnthropicClient.complete after the visible stream has ended.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_AnthropicStream.has_usage`  (lines 322–328)

```
def has_usage(self) -> bool
```

**Purpose**: Reports whether this stream has any input or cache usage information saved. This is useful when an error happens before the normal final usage event.

**Data flow**: It reads the stream state's input and cache token counters. If any of them are nonzero, it returns true; otherwise it returns false. It does not change anything.

**Call relations**: AnthropicClient.complete uses this during error paths to decide whether it can still yield a partial usage record before retrying or raising an error.


##### `_AnthropicStream.usage`  (lines 330–337)

```
def usage(self) -> Usage
```

**Purpose**: Builds the final UFO Usage record from the token counts collected during the Anthropic stream. This gives the rest of the system a provider-independent accounting summary.

**Data flow**: It reads input tokens, output tokens, cache-read tokens, and cache-write tokens from the stream state. If output tokens were never set, it uses zero. It returns a Usage object and does not modify the stream state.

**Call relations**: AnthropicClient.complete yields this record at the end of successful streams and also in some failure paths where usage was already reported. The Usage object is the final accounting event for the round.

*Call graph*: 1 external calls (__init__).


##### `AnthropicClient._request_kwargs`  (lines 346–392)

```
def _request_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the exact set of arguments sent to Anthropic's Messages API for one model request. It translates UFO's request object into Anthropic's expected fields, including system text, messages, tools, reasoning settings, caching, and streaming.

**Data flow**: It receives a ModelRequest. It creates a dictionary containing the model name, system prompt, trimmed messages, maximum token budget, stream setting, cache settings, optional reasoning controls, and optional tool definitions. Message content is converted through anthropic_content, and image-heavy history is trimmed through trim_images. The returned dictionary is ready to pass into the Anthropic SDK call.

**Call relations**: AnthropicClient.complete calls this immediately before creating the provider stream. It is the final request-shaping step before the network call to Anthropic.

*Call graph*: calls 1 internal fn (anthropic_content); called by 1 (complete); 1 external calls (trim_images).


##### `AnthropicClient.complete`  (lines 394–489)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one request to Anthropic and streams back UFO model events until the answer is complete. It also owns the retry policy, special stop-condition handling, empty-response retries, and final usage reporting.

**Data flow**: It receives a ModelRequest. It builds Anthropic request arguments, opens a streaming response, converts each raw provider event through _AnthropicStream.accept, and yields UFO events such as stream start, text chunks, tool-call pieces, reasoning blocks, and final Usage. If provider failures happen before visible output, it may retry. If failures happen after visible output, it raises an interruption. If Anthropic stops because of max tokens or refusal, it yields usage and raises the corresponding clear error.

**Call relations**: This is the main method callers use to get an Anthropic completion. It creates _AnthropicRetry to control backoff, creates _AnthropicStream for each attempt, calls _request_kwargs before the SDK request, emits metrics for empty-response retries, and yields the translated event stream to the rest of the harness.

*Call graph*: calls 2 internal fn (_request_kwargs, __init__); 5 external calls (__init__, __init__, __init__, __init__, emit_metric).


### `core/src/ufo/harness/models/openai.py`

`io_transport` · `request handling / model streaming`

This file lets the rest of the system ask an OpenAI-compatible model for an answer without caring about the exact wire format OpenAI expects. Think of it like a travel adapter: UFO has its own standard shape for messages, images, tool calls, reasoning, and usage counts, while OpenAI has several different plug shapes. This file converts between them.

It supports two OpenAI API surfaces. Chat Completions is the older chat-shaped API. Responses is the newer API that can carry reasoning items and function-call outputs more directly. The choice comes from the model's declared specification, not from guessing based on the model name. There is also a special path for ChatGPT account tokens, which go to the Codex backend and must use Responses.

The file builds request bodies, streams back model output, turns provider stream chunks into UFO events such as text deltas and tool-call deltas, records token usage, and retries temporary provider failures. It is careful about when retrying is safe: if nothing visible has been produced yet, it can retry; if the stream already produced output and then breaks, it raises a special interruption so the wider engine can discard the partial round and try cleanly. It also preserves encrypted reasoning from Responses so later tool-result rounds can continue the model's reasoning.

#### Function details

##### `_cache_write_tokens`  (lines 112–120)

```
def _cache_write_tokens(details: PromptTokensDetails | InputTokensDetails | None) -> int
```

**Purpose**: Reads OpenAI's extra token accounting field for cache-write tokens, when the provider reports it. It protects billing and usage records from bad provider data by rejecting non-integer values.

**Data flow**: It receives optional token-detail data from OpenAI. It looks inside the provider's extra fields for cache_write_tokens, treats a missing value as zero, verifies a present value is a real integer, and returns that count.

**Call relations**: The usage-conversion helpers for both Responses and Chat Completions call this when turning OpenAI usage numbers into UFO's Usage record.

*Call graph*: called by 2 (_chat_usage, _responses_usage).


##### `_responses_usage`  (lines 123–137)

```
def _responses_usage(raw: ResponseUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: Converts token usage reported by the Responses API into UFO's standard Usage format. It separates normal input tokens, cached input tokens, cache-write tokens, and output tokens so costs can be calculated correctly.

**Data flow**: It receives raw Responses usage data and a flag saying whether 30-minute cache writes are priced. It reads cached-token and cache-write counts, checks they do not exceed the total input tokens, and returns a Usage object with the adjusted totals.

**Call relations**: _ResponsesStream.accept calls this when a completed or failed Responses stream reports usage. _ResponsesStream._record_incomplete also calls it when an incomplete response still includes usage.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 2 (_record_incomplete, accept); 1 external calls (__init__).


##### `_chat_usage`  (lines 140–154)

```
def _chat_usage(raw: openai.types.CompletionUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: Converts token usage reported by the Chat Completions API into UFO's standard Usage format. This keeps usage accounting consistent even though Chat and Responses name their fields differently.

**Data flow**: It receives raw Chat Completions usage data and a cache-pricing flag. It extracts cached and cache-write prompt tokens, validates the totals, subtracts those from normal input tokens as needed, and returns a Usage object.

**Call relations**: _ChatStream.accept calls this when a chat stream chunk includes final usage information.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 1 (accept); 1 external calls (__init__).


##### `openai_sdk_client`  (lines 157–170)

```
def openai_sdk_client(api_key: str, base_url: str | None=None, default_headers: dict[str, str] | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Creates an OpenAI asynchronous SDK client with this project's own retry policy in charge. It can point either at OpenAI itself or at another OpenAI-compatible service by using a custom base URL and headers.

**Data flow**: It receives an API key, optional base URL, and optional default headers. It builds and returns an AsyncOpenAI client with SDK retries disabled and a fixed provider timeout.

**Call relations**: codex_sdk_client uses this helper after preparing the special Codex backend URL and headers. Other setup code can also use it to create a normal OpenAI-wire client.

*Call graph*: called by 1 (codex_sdk_client); 1 external calls (AsyncOpenAI).


##### `chatgpt_account_id`  (lines 173–186)

```
def chatgpt_account_id(credential: str) -> str | None
```

**Purpose**: Checks whether a credential is a ChatGPT account token and, if so, extracts the ChatGPT account id from it. This tells the system whether to use the special Codex backend instead of the normal OpenAI API.

**Data flow**: It receives a credential string. It tries to read it as a JWT, which is a three-part token with a base64-encoded JSON payload; if that fails or the expected claim is absent, it returns None. If the account claim is present and non-empty, it returns that account id.

**Call relations**: This helper is used during client setup to distinguish platform API keys from ChatGPT account tokens. It does not call project code, only JSON and base64 decoding.

*Call graph*: 2 external calls (urlsafe_b64decode, loads).


##### `codex_sdk_client`  (lines 189–205)

```
def codex_sdk_client(credential: str, account: str) -> openai.AsyncOpenAI
```

**Purpose**: Creates an OpenAI SDK client configured for the ChatGPT Codex backend. This is needed because ChatGPT account tokens are not accepted by api.openai.com and require extra headers.

**Data flow**: It receives the account credential and account id. It builds the required Codex headers, including account, originator, Responses beta, and stream accept headers, then returns an SDK client pointed at the Codex backend.

**Call relations**: It delegates the actual SDK construction to openai_sdk_client after adding the Codex-specific host and headers.

*Call graph*: calls 1 internal fn (openai_sdk_client).


##### `_status_retry_wait`  (lines 208–214)

```
def _status_retry_wait(error: openai.APIStatusError, delay: float) -> float
```

**Purpose**: Chooses how long to wait after an OpenAI HTTP status error before retrying. It honors the provider's Retry-After header when that asks for a longer wait.

**Data flow**: It receives an API status error and the current retry delay. It reads the retry-after response header, parses it if possible, and returns the larger of that value and the current delay.

**Call relations**: _OpenAIRetry.status uses this when it has decided a status error, such as rate limiting or a server error, is safe to retry.

*Call graph*: called by 1 (status).


##### `_openai_image`  (lines 217–221)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns UFO's internal image data into the image-url shape expected by OpenAI Chat Completions. The image is embedded as a data URL rather than uploaded separately.

**Data flow**: It receives an ImageSource containing a media type and base64 image data. It returns a small dictionary with OpenAI's image_url structure.

**Call relations**: openai_messages uses this for regular image message parts, and _openai_tool_result uses it for image results returned by tools.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 224–240)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Splits a tool result into text and images in the way Chat Completions requires. Chat tool-result messages can carry text, but images must be lifted into a following user message.

**Data flow**: It receives either a plain string result or a tuple of text and image blocks. It returns one combined text string plus a list of OpenAI image parts.

**Call relations**: openai_messages calls this while converting UFO tool-result blocks into OpenAI chat messages. It uses _openai_image for any image blocks it finds.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 243–302)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts UFO's canonical conversation messages into the message list accepted by OpenAI Chat Completions. It translates text, images, tool calls, and tool results while dropping reasoning blocks that Chat Completions cannot represent.

**Data flow**: It receives the system prompt and the conversation history. It trims images according to shared rules, walks each message block, builds OpenAI role/content/tool-call dictionaries, moves tool-result images into user messages when needed, and returns the final message list.

**Call relations**: OpenAIClient._chat_kwargs calls this when building a Chat Completions request. It relies on _openai_image and _openai_tool_result for image and tool-result conversion.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 305–406)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: Converts UFO messages into the input item format accepted by the OpenAI Responses API. Unlike Chat Completions, this format can preserve reasoning items and structured function-call outputs.

**Data flow**: It receives the conversation history. It trims images, then turns each block into Responses input items: text, images, reasoning records, function calls, or function-call outputs. It returns the ordered list that will be sent as the Responses input.

**Call relations**: responses_request calls this while building the full Responses request body. It constructs OpenAI SDK typed objects and JSON-encodes tool-call arguments where needed.

*Call graph*: called by 1 (responses_request); 13 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, ResponseOutputTextParam (+3 more)).


##### `responses_request`  (lines 409–448)

```
def responses_request(request: ModelRequest, effort: OpenAIEffort, codex: bool=False) -> dict[str, Any]
```

**Purpose**: Builds the full request body for OpenAI's Responses API. It includes the model, instructions, conversation input, streaming mode, reasoning setting, tools, and provider-specific differences for Codex.

**Data flow**: It receives a ModelRequest, the resolved reasoning effort, and a flag saying whether the target is Codex. It converts messages through responses_input, adds limits and tool settings, omits unsupported Codex fields, and returns keyword arguments for the SDK call.

**Call relations**: OpenAIClient._complete_responses calls this immediately before starting a Responses stream. It uses responses_input and OpenAI function-tool parameter objects.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `_OpenAIRetry.transport`  (lines 458–493)

```
async def transport(self, error: Exception, yielded: bool) -> _OpenAIRetry
```

**Purpose**: Decides what to do after a network-level streaming problem, such as a timeout or dropped connection. It retries only while it is still safe to do so.

**Data flow**: It receives the original error and a flag saying whether any visible model output has already been yielded. If output already appeared or the retry budget is exhausted, it logs and raises either a stream-interrupted error or the original error. Otherwise it logs, emits a retry metric, sleeps, and returns a new retry state with a larger delay.

**Call relations**: Both OpenAIClient._complete_chat and OpenAIClient._complete_responses use this inside their streaming loops when transport errors occur.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_OpenAIRetry.status`  (lines 495–543)

```
async def status(self, error: openai.APIStatusError, yielded: bool) -> _OpenAIRetry
```

**Purpose**: Decides what to do after an HTTP status error from the provider, such as unauthorized, rate limited, or server error. It turns key and rate-limit problems into project-specific errors that callers can understand.

**Data flow**: It receives an OpenAI status error and whether output has already been yielded. It immediately rejects bad credentials, retries rate limits and server errors when safe, uses Retry-After timing when available, and otherwise raises the appropriate credential, rate-limit, interruption, or raw provider error.

**Call relations**: The chat and Responses streaming loops call this when OpenAI raises an API status error. It uses _status_retry_wait to choose retry timing.

*Call graph*: calls 2 internal fn (_status_retry_wait, __init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_ChatStream.__init__`  (lines 547–552)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates the small state holder used while reading one Chat Completions stream. It remembers whether output has appeared, partial tool-call ids, final usage, and the finish reason.

**Data flow**: It receives a flag for whether cache-write tokens should be counted as priced. It initializes empty stream state that will be filled as chunks arrive.

**Call relations**: OpenAIClient._complete_chat creates one _ChatStream for each attempt before it starts reading Chat Completions chunks.

*Call graph*: called by 1 (_complete_chat).


##### `_ChatStream.accept`  (lines 554–582)

```
def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]
```

**Purpose**: Consumes one Chat Completions stream chunk and turns it into zero or more UFO model events. This is where streaming text and tool-call fragments become the project's standard event types.

**Data flow**: It receives one OpenAI chat chunk. It records usage if present, records the finish reason if present, turns text into TextDelta events, starts tool calls when first seen, emits tool-call argument fragments, updates whether anything visible was yielded, and returns the events.

**Call relations**: OpenAIClient._complete_chat calls this for every chunk in the stream and then yields the returned events to the rest of the engine. It calls _chat_usage when usage arrives.

*Call graph*: calls 1 internal fn (_chat_usage); 3 external calls (__init__, __init__, __init__).


##### `_ChatStream.finish`  (lines 584–599)

```
def finish(self) -> tuple[Usage, Exception | None]
```

**Purpose**: Finalizes a Chat Completions stream after it ends. It returns usage and reports whether the model stopped because it hit the token budget.

**Data flow**: It reads the stored finish reason and usage. If the finish reason was length, it prepares a truncation error. If usage is missing, it raises either the truncation error or a missing-usage error. Otherwise it returns the usage and optional terminal error.

**Call relations**: OpenAIClient._complete_chat calls this after the async stream closes, before deciding whether to yield usage, retry an empty answer, or raise a truncation error.

*Call graph*: 1 external calls (__init__).


##### `_ResponsesStream.__init__`  (lines 603–610)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates the state holder used while reading one Responses API stream. It tracks streamed output, tool-call ids, reasoning items, usage, and any final refusal or failure.

**Data flow**: It receives a flag for cache-write token pricing. It initializes empty dictionaries, sets, lists, and terminal state that will be filled by Responses stream events.

**Call relations**: OpenAIClient._complete_responses creates one _ResponsesStream for each Responses attempt.

*Call graph*: called by 1 (_complete_responses).


##### `_ResponsesStream.accept`  (lines 612–651)

```
def accept(self, event: ResponseStreamEvent) -> tuple[ModelEvent, ...]
```

**Purpose**: Consumes one Responses API stream event and turns it into UFO model events or saved final state. It understands text deltas, function calls, reasoning items, refusals, completion, incomplete responses, and failures.

**Data flow**: It receives one Responses stream event. Depending on the event kind, it may emit text or tool-call events, save encrypted reasoning for later, convert usage, or record a terminal error such as refusal, truncation, or provider failure. It returns only the events that should be streamed live.

**Call relations**: OpenAIClient._complete_responses calls this for every Responses stream event and yields any returned live events. It delegates reasoning capture to _record_reasoning and incomplete-response handling to _record_incomplete.

*Call graph*: calls 3 internal fn (_record_incomplete, _record_reasoning, _responses_usage); 4 external calls (__init__, __init__, __init__, __init__).


##### `_ResponsesStream._record_reasoning`  (lines 653–662)

```
def _record_reasoning(self, item: ResponseReasoningItem) -> None
```

**Purpose**: Stores a completed Responses reasoning item so UFO can replay it in a later round. This is important when a model made a tool call and needs its hidden reasoning context preserved across the tool result.

**Data flow**: It receives a completed OpenAI reasoning item. It requires encrypted content to be present, copies the item id, encrypted content, and summary text into a ReasoningItemBlock, and appends it to the stream state's reasoning list.

**Call relations**: _ResponsesStream.accept calls this when a reasoning output item is done. The outer Responses completion loop later yields these reasoning blocks just before usage.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_ResponsesStream._record_incomplete`  (lines 664–675)

```
def _record_incomplete(self, response: Any) -> None
```

**Purpose**: Records what happened when the Responses API says the response is incomplete. It distinguishes token-limit truncation, content-filter refusal, and other incomplete-provider cases.

**Data flow**: It receives the incomplete response object. It stores usage if present, checks the provider's incomplete reason, and saves an appropriate terminal error: truncated, refused, or generic incomplete response.

**Call relations**: _ResponsesStream.accept calls this when it receives a ResponseIncompleteEvent. It uses _responses_usage for any usage data included with the incomplete response.

*Call graph*: calls 1 internal fn (_responses_usage); called by 1 (accept); 2 external calls (__init__, __init__).


##### `_ResponsesStream.finish`  (lines 677–685)

```
def finish(self) -> tuple[Usage, Exception | None]
```

**Purpose**: Finalizes a Responses stream after it closes. It returns the final usage and any terminal error that should be raised after usage is reported.

**Data flow**: It checks whether usage was recorded. If usage is missing but a terminal error exists, it raises that error so the wider engine sees the meaningful cause. If usage is missing with no known cause, it raises a missing-usage error. Otherwise it returns usage plus the stored terminal error, if any.

**Call relations**: OpenAIClient._complete_responses calls this after the stream ends, before yielding reasoning blocks, usage, or raising a refusal/truncation/failure.


##### `OpenAIClient.complete`  (lines 700–703)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Chooses which OpenAI API surface to use for a model request. This gives callers one simple entry point even though the provider has multiple request formats.

**Data flow**: It receives a ModelRequest. It checks whether this client is for Codex or whether the model spec declares the Responses API, then returns the matching async stream iterator for Responses or Chat Completions.

**Call relations**: The wider model harness calls this to start a completion. It hands the request to either OpenAIClient._complete_responses or OpenAIClient._complete_chat.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._reasoning_effort`  (lines 705–724)

```
def _reasoning_effort(self, request: ModelRequest) -> OpenAIEffort
```

**Purpose**: Decides what reasoning-effort value, if any, should be sent to OpenAI for this request. It prevents unsafe cases where a model would silently use provider-default reasoning when the caller asked for reasoning to be off.

**Data flow**: It reads the model spec, the request's reasoning setting, and whether tools are present. It asks the spec what the wire supports, maps project values such as off to OpenAI's none, turns auto into an omitted parameter, and raises if the request cannot safely express reasoning-off.

**Call relations**: OpenAIClient._chat_kwargs uses this for Chat Completions request parameters. OpenAIClient._complete_responses uses it before building a Responses request.

*Call graph*: called by 2 (_chat_kwargs, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 726–755)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the keyword arguments for a Chat Completions streaming request. It packages messages, token budget, reasoning effort, and optional tool settings into the shape the SDK expects.

**Data flow**: It receives a ModelRequest. It converts messages with openai_messages, adds model and streaming settings, resolves reasoning effort, translates tool definitions and tool choice if present, and returns the request dictionary.

**Call relations**: OpenAIClient._complete_chat calls this right before asking the OpenAI SDK to create a chat completion stream.

*Call graph*: calls 2 internal fn (_reasoning_effort, openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 757–831)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a full Chat Completions streaming request and yields UFO model events as they arrive. It also applies retry rules, usage reporting, empty-response retries, and truncation handling.

**Data flow**: It receives a ModelRequest. It builds SDK arguments, starts the stream, yields a stream-start marker, converts each chunk through _ChatStream.accept, retries safe failures, yields usage when available, and either returns cleanly or raises a meaningful terminal error.

**Call relations**: OpenAIClient.complete uses this when the selected surface is Chat Completions. It creates _ChatStream state, uses _OpenAIRetry for transport and status failures, and calls _chat_kwargs to prepare each attempt.

*Call graph*: calls 3 internal fn (_chat_kwargs, __init__, __init__); called by 1 (complete); 3 external calls (__init__, __init__, emit_metric).


##### `OpenAIClient._complete_responses`  (lines 833–900)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a full Responses API streaming request and yields UFO model events. It is the Responses counterpart to the chat path, with extra support for encrypted reasoning items.

**Data flow**: It receives a ModelRequest. It resolves reasoning effort, builds a Responses request, starts the stream, yields live text and tool-call events, retries safe failures, stores reasoning until the attempt succeeds, yields reasoning blocks and usage at the end, and raises any final refusal, truncation, or provider failure.

**Call relations**: OpenAIClient.complete uses this for models declared as Responses or for Codex clients. It uses responses_request for request construction, _ResponsesStream for stream interpretation, and _OpenAIRetry for retry decisions.

*Call graph*: calls 4 internal fn (_reasoning_effort, __init__, responses_request, __init__); called by 1 (complete); 3 external calls (__init__, __init__, emit_metric).


### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `model request handling and tool execution`

OpenRouter is like a switchboard for AI providers: one request may be served by Google, OpenAI, Anthropic, or another upstream company behind the scenes. This file teaches the project how to use that switchboard without pretending it is a simple direct model connection. It translates the system's model requests into OpenRouter's OpenAI-style chat format, streams text and tool calls back as normal system events, records token usage, and reacts carefully when an upstream provider stalls, refuses a request, or returns no output.

A large part of the file is defensive behavior. If one upstream provider fails but another might work, the code retries while excluding the bad route. If the whole request is too large or the account is unauthorized, it does not waste retries. It also requires a session id so OpenRouter can keep routing related calls to the same provider, which matters for prompt caching.

The file also defines two side-effecting tools: generate_image and generate_video. These call OpenRouter's dedicated image and video APIs, save the returned media into the workspace, and charge the workspace ledger unless the workspace is using its own OpenRouter key. Finally, manifest() advertises the available chat models, tools, and credential slot so the extension can be registered with the rest of the system.

#### Function details

##### `openrouter_slug`  (lines 322–332)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Turns the system's model name into the provider/model name OpenRouter expects. It adds prefixes for common bare OpenAI and Anthropic model ids, while leaving already-prefixed names alone.

**Data flow**: It receives a model id string. It checks whether the id already contains a slash, or whether it looks like an OpenAI or Claude model, then returns the OpenRouter-facing slug string.

**Call relations**: The chat client uses this whenever it builds or sends a request. Message formatting also uses it to detect Google models, because they need a small compatibility workaround.

*Call graph*: called by 4 (_create_kwargs, _stream, complete, _openrouter_messages).


##### `_chunk_provider`  (lines 335–340)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Reads which upstream provider OpenRouter used for a streamed response chunk. This matters because a failed or empty stream can later be retried while avoiding that provider.

**Data flow**: It receives one streamed chat chunk. It looks inside OpenRouter's extra metadata for a provider name and returns that name as text, or returns nothing if no provider was reported.

**Call relations**: _OpenRouterStream.accept calls this while processing each chunk. The remembered provider is later used by OpenRouterModelClient.complete when deciding what route to avoid after trouble.

*Call graph*: called by 1 (accept).


##### `_named_upstream`  (lines 343–365)

```
def _named_upstream(error: openai.APIStatusError) -> str | None
```

**Purpose**: Decides whether an OpenRouter error was actually caused by one named upstream provider. If so, the system may be able to retry through a different upstream instead of failing immediately.

**Data flow**: It receives an OpenAI API status error. It inspects the status code and error body metadata, ignores errors that look like account, key, quota, or request-size problems, and returns the upstream provider name only when rerouting is sensible.

**Call relations**: Retry logic calls this when a request is refused or rate-limited. The main completion loop also uses it to remember providers that rejected the request.

*Call graph*: called by 2 (complete, status); 1 external calls (dumps).


##### `_usage_of`  (lines 368–389)

```
def _usage_of(usage: CompletionUsage, cache_write_30m_rate: int) -> Usage
```

**Purpose**: Converts token usage reported by the OpenAI-style SDK into the system's own Usage record. It separates normal input tokens, output tokens, cached tokens, and cache-write tokens so billing is accurate.

**Data flow**: It receives the SDK's usage object and a flag-like rate for cache writes. It validates token counts, subtracts cached portions from normal input, and returns a Usage object.

**Call relations**: _OpenRouterStream.accept calls this when a stream chunk includes usage information. The resulting Usage is later yielded by the model client as the final accounting event.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_contains_json_reference`  (lines 404–416)

```
def _contains_json_reference(value: object) -> bool
```

**Purpose**: Checks whether a JSON-like value contains schema reference keys such as $ref. This is used to avoid a known OpenRouter/Google problem with tool-result messages.

**Data flow**: It receives any nested object. It walks through dictionaries and lists looking for reference keys, then returns true if it finds one and false otherwise.

**Call relations**: _openrouter_messages uses this after parsing tool-result text. If a reference is found, the message is wrapped as plain text so Google-routed requests are accepted.

*Call graph*: called by 1 (_openrouter_messages).


##### `_openrouter_messages`  (lines 419–456)

```
def _openrouter_messages(model: str, system: str, messages: tuple[Message, ...], accepts_image_input: bool) -> list[dict[str, object]]
```

**Purpose**: Prepares chat messages in the exact shape OpenRouter should receive. It also removes images for models that cannot accept them and applies a Google-specific safety wrapper around certain JSON tool results.

**Data flow**: It receives the model name, system prompt, conversation messages, and whether the model accepts images. It optionally strips images, translates messages to OpenAI format, then rewrites fragile Google tool-result content when needed. It returns a list of request-message dictionaries.

**Call relations**: OpenRouterModelClient._create_kwargs calls this while assembling the outgoing chat request. It relies on openrouter_slug to identify Google models and on _contains_json_reference to spot JSON references.

*Call graph*: calls 2 internal fn (_contains_json_reference, openrouter_slug); called by 1 (_create_kwargs); 4 external calls (dumps, loads, omit_images, openai_messages).


##### `_OpenRouterRetry.status`  (lines 469–538)

```
async def status(self, error: openai.APIStatusError, yielded: bool, dead: set[str]) -> '_OpenRouterRetry'
```

**Purpose**: Chooses what to do after an HTTP status error from OpenRouter, such as 400, 429, or 500. It may retry immediately around a bad upstream, wait and retry, park the request for later, or raise the error.

**Data flow**: It receives the error, whether any output was already shown, and a set of providers to avoid. It may add an upstream to that set, log and count the retry, sleep for a backoff delay, or raise an exception. It returns an updated retry-state object when another attempt should happen.

**Call relations**: OpenRouterModelClient.complete calls this inside its retry loop after API status failures. It uses _named_upstream to decide whether the failure belongs to one reroutable provider.

*Call graph*: calls 2 internal fn (__init__, _named_upstream); 4 external calls (sleep, replace, emit_metric, log).


##### `_OpenRouterRetry.stream_error`  (lines 540–563)

```
def stream_error(self, error: openai.APIError, yielded: bool) -> '_OpenRouterRetry'
```

**Purpose**: Handles an error injected into a live streaming response. It has one special retry for a known Gemini abort case; otherwise it turns the problem into a stream interruption so the engine can redo the round cleanly.

**Data flow**: It receives the streaming error and whether any output had already appeared. It either returns an updated retry state for the one known safe retry, or raises a ModelStreamInterrupted error.

**Call relations**: OpenRouterModelClient.complete calls this when the SDK reports a streaming API error. It logs and emits retry metrics for the special Gemini case.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (replace, emit_metric, log).


##### `_OpenRouterStream.__init__`  (lines 567–574)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates a small state holder for one OpenRouter streaming attempt. It remembers whether anything was yielded, which tool calls have started, the usage record, finish reason, provider, and generation id.

**Data flow**: It receives whether cache-write tokens should count as priced. It initializes empty tracking fields that will be filled as chunks arrive.

**Call relations**: OpenRouterModelClient.complete creates one of these for each attempt. OpenRouterModelClient._stream then feeds chunks into it through accept.

*Call graph*: called by 1 (complete).


##### `_OpenRouterStream.accept`  (lines 576–606)

```
def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]
```

**Purpose**: Turns one streamed OpenRouter chat chunk into the system's stream events. Those events may be text, the start of a tool call, more tool-call JSON, or no visible event at all.

**Data flow**: It receives a chat completion chunk. It updates saved provider, generation id, usage, and finish reason; converts text and tool-call deltas into ModelEvent objects; marks whether visible output appeared; and returns the new events.

**Call relations**: OpenRouterModelClient._stream calls this for every incoming chunk. It calls _chunk_provider and _usage_of to preserve routing and billing information.

*Call graph*: calls 2 internal fn (_chunk_provider, _usage_of); called by 1 (_stream); 3 external calls (__init__, __init__, __init__).


##### `OpenRouterModelClient.complete`  (lines 672–735)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one full streamed chat completion through OpenRouter. It is the main bridge between the system's model interface and OpenRouter's API, including retries, rerouting, stream interruption handling, truncation detection, and final usage reporting.

**Data flow**: It receives a ModelRequest. It builds retry and routing state, repeatedly opens a stream, yields model events as they arrive, handles errors by retrying or raising, looks up missing usage if needed, and finally yields usage before returning.

**Call relations**: The rest of the system calls this when an agent asks a registered OpenRouter model to respond. It delegates one attempt to _stream, asks _finish_usage for accounting, records stalled providers through _stalled_out, and uses retry helpers when failures happen.

*Call graph*: calls 8 internal fn (__init__, _finish_usage, _nowhere_left, _stalled_out, _stream, __init__, _named_upstream, openrouter_slug); 4 external calls (__init__, __init__, __init__, emit_metric).


##### `OpenRouterModelClient._stream`  (lines 737–751)

```
async def _stream(self, request: ModelRequest, state: _OpenRouterStream, ignore_providers: set[str]) -> AsyncIterator[ModelEvent]
```

**Purpose**: Performs one actual streaming API call to OpenRouter. It opens the stream, announces when the first chunk arrives, and converts chunks into system events.

**Data flow**: It receives the model request, the stream-state object, and providers to ignore. It builds API arguments, starts the OpenRouter stream, yields a stream-start marker on the first chunk, and then yields events produced from each chunk.

**Call relations**: OpenRouterModelClient.complete calls this for each attempt. It uses _create_kwargs to build the request, _excluded to decide provider exclusions, and _OpenRouterStream.accept to translate chunks.

*Call graph*: calls 4 internal fn (_create_kwargs, _excluded, accept, openrouter_slug); called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient._excluded`  (lines 753–760)

```
def _excluded(self, slug: str, dead: set[str]) -> frozenset[str]
```

**Purpose**: Calculates which upstream providers should be avoided on the next call. This keeps the request from being sent back to a provider that just failed or returned empty.

**Data flow**: It receives the OpenRouter slug and the providers that failed in the current round. For models with a fixed provider order, it also considers previously stalled providers and trims the ignore list so at least one route remains. It returns a frozen set of provider names.

**Call relations**: OpenRouterModelClient._stream calls this just before building the request. The result is passed into _create_kwargs so OpenRouter receives it in the provider ignore list.

*Call graph*: called by 1 (_stream).


##### `OpenRouterModelClient._nowhere_left`  (lines 762–777)

```
def _nowhere_left(self, slug: str, model: str, error: openai.APIStatusError, dead: set[str]) -> bool
```

**Purpose**: Detects a special case where the client's own provider exclusions left an unpinned model with no available OpenRouter route. In that case, the code degrades to an empty result instead of treating the 404 as a hard turn failure.

**Data flow**: It receives the slug, public model name, status error, and ignored-provider set. It checks for a 404 caused after exclusions on an unpinned slug, logs it, and returns true or false.

**Call relations**: OpenRouterModelClient.complete calls this after API status errors. If it returns true, complete yields empty usage if needed and stops the round gracefully.

*Call graph*: called by 1 (complete); 1 external calls (log).


##### `OpenRouterModelClient._stalled_out`  (lines 779–801)

```
def _stalled_out(self, slug: str, upstream: str | None, kind: str) -> None
```

**Purpose**: Remembers an upstream provider that stalled or refused a request, but only for models where this file knows the allowed route list. This prevents the next retry in the same turn from landing on the same bad route.

**Data flow**: It receives the slug, upstream provider name, and reason kind. If the provider is known, relevant, and not already saved, it appends it to the stalled list and logs the event.

**Call relations**: OpenRouterModelClient.complete calls this after stream transport failures, stream errors, and some refusals. Later, _excluded reads the stalled list when preparing another call.

*Call graph*: called by 1 (complete); 1 external calls (log).


##### `OpenRouterModelClient._finish_usage`  (lines 803–809)

```
async def _finish_usage(self, state: _OpenRouterStream) -> Usage
```

**Purpose**: Ensures a completed stream has a Usage record. If the stream did not include usage directly, it tries OpenRouter's generation lookup endpoint as a fallback.

**Data flow**: It receives the stream state. It uses usage already captured from the stream when available, otherwise looks up usage by generation id and finish reason, then returns a Usage object or raises if none can be found.

**Call relations**: OpenRouterModelClient.complete calls this after a stream finishes normally. It delegates the fallback lookup to _generation_usage.

*Call graph*: calls 1 internal fn (_generation_usage); called by 1 (complete).


##### `OpenRouterModelClient._generation_usage`  (lines 811–868)

```
async def _generation_usage(self, generation_id: str, finish_reason: str) -> Usage | None
```

**Purpose**: Looks up token usage from OpenRouter's generation ledger when the streaming response did not include usage. This protects billing and accounting from missing stream metadata.

**Data flow**: It receives a generation id and finish reason. It calls OpenRouter's generation endpoint, retries brief 404 or transport problems, validates the returned generation data, and returns Usage if the ledger entry matches the completed stream.

**Call relations**: _finish_usage calls this only as a fallback. If OpenRouter never indexes the generation or the lookup keeps failing, it raises ModelStreamInterrupted so the engine can retry the round.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_finish_usage); 4 external calls (__init__, sleep, AsyncClient, emit_metric).


##### `OpenRouterModelClient._create_kwargs`  (lines 870–925)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Builds the keyword arguments sent to the OpenAI-compatible chat-completions SDK. It adds the model slug, messages, tools, reasoning settings, session id, and provider routing preferences.

**Data flow**: It receives the model request and provider ignore list. It rejects requests without a session id, translates messages, adds reasoning and provider settings to extra_body, includes tool definitions and tool choice when present, and returns the final request dictionary.

**Call relations**: OpenRouterModelClient._stream calls this immediately before starting a stream. It uses openrouter_slug and _openrouter_messages to shape the OpenRouter request correctly.

*Call graph*: calls 2 internal fn (_openrouter_messages, openrouter_slug); called by 1 (_stream).


##### `_model_client`  (lines 928–933)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: Creates an OpenRouterModelClient for a registered model spec and API key. This is the factory the model registry can call when it needs a working client.

**Data flow**: It receives a ModelSpec and key. It creates an OpenAI-compatible async SDK client pointed at OpenRouter's base URL, wraps it in OpenRouterModelClient, and returns that client.

**Call relations**: _openrouter stores this factory in each ModelSpec. Later, the registry uses it when a request needs to run through OpenRouter.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 936–962)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW, reasoning: ReasoningSupport=_REASONS, accepts_image_input: bool=True, compaction_keep_messages:
```

**Purpose**: Builds one ModelSpec entry for an OpenRouter chat model. A ModelSpec is the system's catalog card for a model: id, price, context size, reasoning support, credential slot, and client factory.

**Data flow**: It receives model metadata such as id, price, cutoff date, context window, and behavior flags. It fills in OpenRouter-specific defaults and returns a ModelSpec.

**Call relations**: The file uses this helper to create OPENROUTER_MODEL_SPECS. manifest later exposes those specs to the extension registry.

*Call graph*: 1 external calls (__init__).


##### `GenerateImageInput._within_model_limits`  (lines 1073–1097)

```
def _within_model_limits(self) -> 'GenerateImageInput'
```

**Purpose**: Validates that an image-generation request fits the selected model's real limits. This catches impossible combinations before sending them to OpenRouter.

**Data flow**: It reads the chosen image model, number of images, aspect ratio, and resolution. It raises a clear validation error for unsupported choices, fills in a default resolution when that model uses resolution tiers, and returns the cleaned input object.

**Call relations**: Pydantic calls this automatically after GenerateImageInput is built. OpenRouterImages.generate can then assume the request is within the allowlisted model limits.


##### `_reported_cost_micro_usd`  (lines 1105–1117)

```
def _reported_cost_micro_usd(usage: object) -> int | None
```

**Purpose**: Extracts a reported generation cost and converts it to micro-dollars, meaning millionths of a US dollar. It understands both normal OpenRouter billing and bring-your-own-key upstream billing fields.

**Data flow**: It receives a usage-like object. It checks for a positive cost or upstream inference cost in dollars, converts that amount to micro-USD, and returns it; if no positive cost is reported, it returns nothing.

**Call relations**: OpenRouterImages._charge and OpenRouterVideos._job call this. If it finds no cost, those callers fall back to this file's list prices.

*Call graph*: called by 2 (_charge, _job).


##### `OpenRouterImages.generate`  (lines 1150–1187)

```
async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Runs one image generation tool call from request to saved files. It sends the prompt to OpenRouter, decodes returned images, writes them into the workspace, meters the cost when appropriate, and returns both file paths and image content.

**Data flow**: It receives the tool context and validated image arguments. It fetches the OpenRouter key, posts the generation request, returns an error ToolResult on refusal, saves decoded images on success, calculates cost, optionally records image usage, and returns a ToolResult describing the files.

**Call relations**: _generate_image creates an OpenRouterImages instance and calls this. Inside the flow it uses _refusal, _images, _save, and _charge as the major steps.

*Call graph*: calls 5 internal fn (meter_images, _charge, _images, _refusal, _save); 6 external calls (__init__, __init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterImages._refusal`  (lines 1189–1204)

```
def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str
```

**Purpose**: Creates a short, useful error message when OpenRouter does not generate an image. It preserves the provider's reason while keeping the text bounded.

**Data flow**: It receives the original image arguments and HTTP response. It tries to read a JSON error message, otherwise uses response text, trims it to a safe length, and returns a human-readable string.

**Call relations**: OpenRouterImages.generate calls this when the image API response is an error. The returned text becomes the content of an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterImages._images`  (lines 1206–1236)

```
def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]
```

**Purpose**: Pulls usable images out of OpenRouter's response. It decodes base64 image data, assigns a media type, and rejects missing or oversized images.

**Data flow**: It receives the image arguments and response body. It scans the body data list, decodes each b64_json image into bytes, checks the byte limit, wraps each one as GeneratedImage, and returns all images as a tuple.

**Call relations**: OpenRouterImages.generate calls this after a successful HTTP response. The decoded images are then passed to _save and also returned to the model as ImageContent.

*Call graph*: called by 1 (generate); 3 external calls (__init__, __init__, b64decode).


##### `OpenRouterImages._save`  (lines 1238–1245)

```
async def _save(self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage) -> str
```

**Purpose**: Writes one generated image into the workspace under the generated-images directory. It chooses a file extension based on the image media type.

**Data flow**: It receives the tool context, image arguments, image index, and decoded image. It builds a path from the requested file name and index, writes the bytes through the sandbox workspace, and returns the path.

**Call relations**: OpenRouterImages.generate calls this once for each generated image. The collected paths are included in the tool result so the agent can share or refer to the files.

*Call graph*: called by 1 (generate).


##### `OpenRouterImages._charge`  (lines 1247–1254)

```
def _charge(self, body: object, args: GenerateImageInput, images: int) -> int
```

**Purpose**: Calculates the cost to record for an image generation. It prefers OpenRouter's reported charge and falls back to this file's list price per image.

**Data flow**: It receives the response body, image arguments, and number of images. It reads reported usage cost when present; otherwise it multiplies the model's fallback image price by the image count and returns micro-USD.

**Call relations**: OpenRouterImages.generate calls this after images are saved. The result is used both in the tool's JSON summary and, when the workspace is not using its own key, in image metering.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 1 (generate).


##### `_generate_image`  (lines 1257–1262)

```
async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Acts as the registered tool handler for generate_image. It connects the generic tool system to the OpenRouterImages implementation.

**Data flow**: It receives the tool context and validated image arguments. It checks that extension context is available, creates OpenRouterImages with extension credentials and optional test transport, and returns the result of generate.

**Call relations**: GENERATE_IMAGE_TOOL names this as its handler. When an agent calls the generate_image tool, the tool runtime invokes this function.

*Call graph*: 1 external calls (__init__).


##### `GenerateVideoInput._within_model_limits`  (lines 1318–1342)

```
def _within_model_limits(self) -> 'GenerateVideoInput'
```

**Purpose**: Validates that a video-generation request fits the chosen model's duration, aspect-ratio, and resolution limits. It also fills in the default resolution used for billing.

**Data flow**: It reads the selected video model, duration, aspect ratio, and resolution. It raises clear validation errors for unsupported values, sets a default resolution if none was given, and returns the cleaned input object.

**Call relations**: Pydantic runs this automatically after GenerateVideoInput is created. OpenRouterVideos.generate then sends only requests that match the allowlisted model capabilities.


##### `OpenRouterVideos.generate`  (lines 1384–1425)

```
async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Runs one video generation tool call from start to finish. It starts an OpenRouter video job, waits for it, downloads the completed MP4, saves it, meters cost when appropriate, and returns the saved path.

**Data flow**: It receives the tool context and validated video arguments. It fetches the API key, posts a video request, handles immediate refusal, polls the returned job until settled, handles failed jobs, downloads the video, saves it, calculates cost, optionally records video usage, and returns a ToolResult summary.

**Call relations**: _generate_video creates an OpenRouterVideos instance and calls this. The method delegates specific steps to _refusal, _job, _settled, _failure, _download, _save, and _charge.

*Call graph*: calls 8 internal fn (meter_videos, _charge, _download, _failure, _job, _refusal, _save, _settled); 5 external calls (__init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterVideos._refusal`  (lines 1427–1442)

```
def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str
```

**Purpose**: Creates a short error message when OpenRouter refuses to start a video generation. It gives the model enough information to revise the prompt or parameters.

**Data flow**: It receives the video arguments and HTTP response. It tries to extract a JSON error message, falls back to response text, trims it to a safe length, and returns a readable explanation.

**Call relations**: OpenRouterVideos.generate calls this when the initial POST fails. The returned text is placed in an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterVideos._job`  (lines 1444–1459)

```
def _job(self, body: object) -> VideoJob
```

**Purpose**: Parses OpenRouter's video job description into a local VideoJob object. It refuses to continue if there is no job id or status to poll.

**Data flow**: It receives a response body. It reads the id, status, optional error message, and optional usage cost, validates the required fields, and returns a VideoJob.

**Call relations**: OpenRouterVideos.generate calls this after the initial video request. OpenRouterVideos._settled also calls it for each polling response.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 2 (_settled, generate); 2 external calls (__init__, __init__).


##### `OpenRouterVideos._settled`  (lines 1461–1482)

```
async def _settled(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> VideoJob
```

**Purpose**: Waits for a video job to stop being pending or in progress. It polls OpenRouter at fixed intervals but gives up after a bounded timeout so a turn is not held forever.

**Data flow**: It receives the video arguments, HTTP client, and current VideoJob. While the job is pending, it checks the deadline, sleeps, fetches the latest job state, parses it, and finally returns the settled job.

**Call relations**: OpenRouterVideos.generate calls this after creating a job. It uses _job to interpret each poll response and raises OpenRouterVideoError if polling fails or takes too long.

*Call graph*: calls 1 internal fn (_job); called by 1 (generate); 4 external calls (__init__, sleep, get, monotonic).


##### `OpenRouterVideos._failure`  (lines 1484–1488)

```
def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str
```

**Purpose**: Turns a failed video job into a clear tool error message. It uses the provider's own reason when one is available.

**Data flow**: It receives the original video arguments and the settled job. It chooses the job error text or a generic status message, trims it, and returns a readable explanation.

**Call relations**: OpenRouterVideos.generate calls this when a settled job is not completed. The message becomes the content of an error ToolResult.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._download`  (lines 1490–1509)

```
async def _download(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> bytes
```

**Purpose**: Downloads the finished MP4 for a completed video job and checks that it is usable. It rejects empty, failed, or oversized downloads before anything is written to the workspace.

**Data flow**: It receives video arguments, the HTTP client, and the completed job. It fetches the content endpoint, checks for HTTP errors, reads bytes, enforces the size limit, and returns the raw video bytes.

**Call relations**: OpenRouterVideos.generate calls this only after _settled reports completion. The returned bytes are passed to _save.

*Call graph*: called by 1 (generate); 2 external calls (__init__, get).


##### `OpenRouterVideos._save`  (lines 1511–1515)

```
async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str
```

**Purpose**: Writes the completed video into the workspace under the generated-videos directory. It uses the requested file-name stem and the MP4 suffix.

**Data flow**: It receives the tool context, video arguments, and raw video bytes. It builds the workspace path, writes the file through the sandbox, and returns the path.

**Call relations**: OpenRouterVideos.generate calls this after downloading the video. The path is included in the final tool result.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._charge`  (lines 1517–1525)

```
def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int
```

**Purpose**: Calculates the cost to record for a video generation. It prefers the completed job's reported cost and otherwise uses the model's per-second fallback price for the chosen resolution.

**Data flow**: It receives the video arguments and final job. If the job includes a reported micro-USD cost, it returns that; otherwise it multiplies the configured per-second rate by the requested duration.

**Call relations**: OpenRouterVideos.generate calls this after saving the video. The result is included in the tool summary and used for video metering when the platform key paid for the generation.

*Call graph*: called by 1 (generate).


##### `_generate_video`  (lines 1528–1533)

```
async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Acts as the registered tool handler for generate_video. It connects the tool runtime to the OpenRouterVideos implementation.

**Data flow**: It receives the tool context and validated video arguments. It checks for extension context, creates OpenRouterVideos with credentials and optional test transport, and returns the result of generate.

**Call relations**: GENERATE_VIDEO_TOOL names this as its handler. When an agent calls generate_video, the tool runtime invokes this function.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1546–1562)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It lists the OpenRouter chat models, the image and video tools, and the credential slot used for the API key.

**Data flow**: It takes no input. It builds and returns a Manifest containing the extension name, version, model specs, tool definitions, and OpenRouter API-key credential description.

**Call relations**: The extension loader calls this to register OpenRouter. Without it, the host would not know which models, tools, or credentials this file provides.

*Call graph*: 2 external calls (__init__, __init__).
