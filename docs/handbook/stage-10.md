# Model dispatch, streaming response handling, and usage accounting  `stage-10`

This stage is part of the main work loop, after UFO has prepared a prompt and is ready to ask an AI model for an answer. It is like a switchboard: it sends the conversation to the chosen provider, listens as the answer arrives piece by piece, and converts everything into UFO’s own standard event format so the rest of the system does not need to know which provider was used.

The Anthropic and OpenAI files are the main provider bridges. They submit requests, read streamed text, notice when the model asks to call a tool, capture “reasoning” notes when the provider sends them, retry failures that are safe to retry, and report token usage so spending can be tracked. The OpenRouter extension adds another route to many OpenAI-style models, plus image and video tools, without making it part of the core code.

The replies file cleans special hidden reply tags from model output. It keeps the useful message text while preventing internal markup from leaking to users.

## Files in this stage

### Reply Extraction
Utilities strip hidden direct-reply markup from model output while preserving the intended reply content.

### `core/src/ufo/loop/replies.py`

`domain_logic` · `live response streaming and round completion`

The system lets a model mark part of its output as a reply to a particular message, using a tag like <reply-to message="...">words</reply-to>. That tag is useful to the program, but it should not appear in the text shown to people. This file is the filter and extractor for that hidden markup.

After a full round of model output is complete, marked_replies reads the whole text, finds every properly closed reply span, and turns each one into a MarkedReply: the target message id, if it is a valid UUID, plus the words inside the tag. It also returns a cleaned version of the full round text with the tags removed, so the conversation history keeps what was said without keeping instructions that might confuse the model later.

During live streaming, the problem is trickier because output arrives in small chunks. A tag might be split across chunks, like getting “<rep” now and “ly-to...” later. ReplyRedaction works like a careful curtain: it lets safe ordinary text through, hides everything inside a reply span, and waits when it sees the start of something that might become a tag. If the markup is malformed, it errs on the side of hiding it rather than showing private control text to a member.

#### Function details

##### `marked_replies`  (lines 42–51)

```
def marked_replies(text: str) -> tuple[tuple[MarkedReply, ...], str]
```

**Purpose**: This function reads a completed round of text and pulls out all properly closed reply sections. It also returns the same round text with the reply markup removed, so the visible conversation record stays clean.

**Data flow**: It takes the full text of a round as input. It scans for <reply-to message="...">...</reply-to> spans, strips any reply tags out of the words inside, ignores empty replies, converts the named message id when possible, and builds MarkedReply objects. It returns two things: the collected replies in order, and the original text with all reply openers and closers removed.

**Call relations**: This is used after the model has finished a round, when the system can safely inspect the whole answer at once. For each reply it finds, it calls _named_message to turn the tag's message value into a real UUID when possible, then packages the result as a MarkedReply.

*Call graph*: calls 1 internal fn (_named_message); 1 external calls (__init__).


##### `_named_message`  (lines 54–58)

```
def _named_message(named: str) -> UUID | None
```

**Purpose**: This helper checks whether the message value written in a reply tag is a valid UUID, meaning a standard unique message identifier. If it is not valid, the reply is still kept, but its target is recorded as unknown.

**Data flow**: It receives the raw text from the message="..." part of a reply tag. It trims extra spaces and tries to convert that text into a UUID. If conversion works, it returns the UUID; if conversion fails, it returns None.

**Call relations**: marked_replies calls this whenever it finds a closed reply span. This keeps the parsing rule in one small place: the main extractor does not need to know the details of what counts as a valid message id.

*Call graph*: called by 1 (marked_replies); 1 external calls (UUID).


##### `ReplyRedaction.feed`  (lines 77–99)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This method filters one incoming chunk of live model output and returns only the text that is safe to show immediately. It hides reply spans and reply markup so users do not see internal tags or duplicate reply text in the live stream.

**Data flow**: It receives the next piece of streamed text and adds it to text held from earlier chunks. If it is currently inside a reply span, it keeps withholding text until it finds the closing tag. If it is outside a span, it publishes ordinary text before an opener, enters hidden mode when it finds an opener, or publishes only the part that cannot still become a tag. It returns the safe text to display now, with any reply markup stripped out, and updates its own held text and inside/outside state for the next chunk.

**Call relations**: The live streaming path calls this repeatedly as chunks arrive. It uses _growing_suffix when waiting for a possible closing tag across chunk boundaries, and _settled_chars when deciding how much outside text is definitely ordinary prose rather than the start of a tag.

*Call graph*: calls 2 internal fn (_growing_suffix, _settled_chars); 1 external calls (find).


##### `_growing_suffix`  (lines 102–107)

```
def _growing_suffix(text: str, token: str) -> str
```

**Purpose**: This helper keeps only the tail end of text that might still grow into a known token when more streamed text arrives. In this file, it is used to avoid missing a closing reply tag that is split across chunks.

**Data flow**: It receives some current text and a target token such as </reply-to>. It checks the end of the text for the longest suffix that matches the beginning of that token. It returns that suffix, or an empty string if no ending could become the token.

**Call relations**: ReplyRedaction.feed calls this while hidden inside a reply span and no full closing tag has appeared yet. The helper tells feed what tiny piece to keep for the next chunk, so old hidden reply content can be dropped while a possible partial closer is still remembered.

*Call graph*: called by 1 (feed).


##### `_settled_chars`  (lines 110–119)

```
def _settled_chars(text: str) -> int
```

**Purpose**: This helper decides how much text is safe to publish when the stream is not currently inside a reply span. It prevents the system from showing a '<' or partial tag too early, because later characters might prove it was reply markup.

**Data flow**: It receives the currently held text. If there is no '<', all text is safe. If the last '<' and following characters could still become a reply opener or closer, or look like an unfinished opener with attributes, it returns the position before that risky part. Otherwise, it says all text is safe to publish.

**Call relations**: ReplyRedaction.feed calls this whenever it has no complete opener to remove but may still be holding the beginning of one. This is the small decision point that lets normal prose stream promptly while holding back possible markup until it is clearly safe or complete.

*Call graph*: called by 1 (feed).


### Model Provider Adapters
Core and extension adapters send prepared conversations to providers, translate streamed responses into UFO events, and handle retries, tool calls, reasoning, usage, and provider-specific capabilities.

### `core/src/ufo/harness/models/anthropic.py`

`io_transport` · `request handling during model calls`

This file lets the rest of the project talk to Anthropic models without needing to know Anthropic's exact request and streaming format. It is like a travel adapter: UFO has its own shape for messages, images, tools, reasoning, and token usage, while Anthropic expects and returns a different shape. This file converts between the two.

Before sending a request, it translates UFO content blocks into Anthropic content blocks, including text, images, tool calls, and tool results. It also trims images from older messages where needed, adds cache hints, chooses whether reasoning should be enabled, and includes tool definitions when tools are available.

During the response, Anthropic sends many small stream events. The client turns those into UFO events such as stream start, text pieces, tool-call starts, tool-call JSON fragments, hidden reasoning blocks, and final token usage. Reasoning is treated specially: it is saved until the stream ends, then returned in the same order Anthropic produced it, because Anthropic may require that exact sequence in later tool-result turns.

The file also protects the system from temporary provider failures. Timeouts, dropped connections, rate limits, and transient server problems are retried with increasing waits, but only before visible output has been sent. Once the user or engine has seen part of an answer, retrying could mix two different attempts, so errors are raised instead.

#### Function details

##### `anthropic_sdk_client`  (lines 50–54)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: This creates the low-level Anthropic software client used to call the provider. It deliberately turns off the Anthropic SDK's own retries so this file can apply one clear retry policy in one place.

**Data flow**: It takes an API key as input. It builds an asynchronous Anthropic client with that key, a fixed timeout, and no built-in retries. It returns that ready-to-use client to whoever is setting up model access.

**Call relations**: This is used during setup when the project needs an Anthropic client object. The returned client is later stored inside AnthropicClient, whose complete method performs the actual streaming requests and retry decisions.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 57–61)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: This converts UFO's internal image description into the image format Anthropic expects. It keeps the image media type and base64 data, but wraps them with Anthropic's field names.

**Data flow**: It receives an ImageSource containing the image type and encoded data. It places those values into a dictionary shaped like an Anthropic image block. The returned dictionary can be placed inside an Anthropic message or tool result.

**Call relations**: This is a small helper used whenever image content must be sent to Anthropic. anthropic_content calls it for normal image blocks, and _anthropic_tool_result_part calls it when a tool result contains an image.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 64–69)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: This converts one piece of a tool result into Anthropic's format. Tool results can contain text or images, and Anthropic needs each piece written in its own content-block shape.

**Data flow**: It receives one tool-result content item. If the item is text, it returns a text dictionary. If the item is an image, it passes the image source to _anthropic_image and returns the resulting image dictionary.

**Call relations**: This helper is used by anthropic_content when a UFO tool result contains multiple rich content parts instead of just a plain string. It hands image conversion off to _anthropic_image so image formatting stays consistent.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 72–106)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: This translates UFO's message content into the content format Anthropic accepts. It is used before every Anthropic request so the provider receives text, images, tool calls, tool results, and Anthropic-compatible reasoning in the right shape.

**Data flow**: It receives either a plain string or a tuple of UFO content blocks. A plain string is returned unchanged. For structured blocks, it walks through each block and builds a list of Anthropic dictionaries: text becomes text, images become Anthropic images, tool uses and tool results get their provider field names, and Anthropic reasoning blocks are preserved. OpenAI-style reasoning blocks are skipped because Anthropic cannot reuse another provider's private reasoning format.

**Call relations**: AnthropicClient.complete calls this while building the messages for a request. This function uses _anthropic_image for image blocks and _anthropic_tool_result_part for rich tool-result contents, so complete can stay focused on the larger streaming conversation.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 114–442)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the main Anthropic model call. It sends a UFO model request to Anthropic, streams back UFO-standard events as the answer arrives, records token usage, preserves hidden reasoning for later turns, and applies safe retry rules for temporary failures.

**Data flow**: It receives a ModelRequest containing the model name, system instructions, conversation messages, tools, reasoning preference, token limit, and cache settings. It converts messages into Anthropic format, adds tool and reasoning options, and opens a streaming request. As Anthropic sends events, it yields UFO events: a stream-start marker, text chunks, tool-call starts, tool-call JSON fragments, saved reasoning blocks near the end, and finally a Usage record with token counts. It also changes behavior on special endings: max_tokens becomes a truncation error, refusal becomes a refusal error, missing usage becomes a runtime error, and an empty answer may be retried a few times.

**Call relations**: This method is called by the model-running part of the system when it needs an Anthropic completion. It relies on anthropic_content to prepare outgoing message content, then talks to the Anthropic SDK client. It hands downstream code a clean stream of UFO ModelEvent objects, while logging and emitting retry metrics when transport or provider-status failures happen before any visible answer has been produced.

*Call graph*: calls 1 internal fn (anthropic_content); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep (+3 more)).


### `core/src/ufo/harness/models/openai.py`

`io_transport` · `request handling`

This file lets the rest of the system talk to OpenAI and OpenAI-compatible providers without caring about their exact wire format. The project has its own idea of a model request: messages, images, tool calls, tool results, reasoning blocks, and token usage. OpenAI has two different API shapes for this: Chat Completions and Responses. This file chooses the right shape based on the model’s specification, not by guessing from the model name.

Think of it like a travel adapter. The rest of the app plugs in one standard request. This file reshapes that request so it fits the provider’s socket, then reshapes the streamed answer back into the app’s standard events.

It also protects the caller from common provider problems. If the network times out or the provider returns a temporary error, it retries with increasing waits. If the API key is rejected, it raises the project’s clearer credential error. If the model stops because the token budget was too small, it reports truncation instead of pretending the answer is complete.

A major detail is reasoning support. Some models can return hidden reasoning items that must be sent back later so tool-use conversations can continue correctly. The Responses path preserves those items; the Chat Completions path drops them because that API has nowhere legal to put them.

#### Function details

##### `_cache_write_tokens`  (lines 93–101)

```
def _cache_write_tokens(details: PromptTokensDetails | InputTokensDetails | None) -> int
```

**Purpose**: This helper reads the provider’s extra token-count field for cache writes. It exists because OpenAI-style usage reports may include cache-write tokens in a provider-specific place, and the billing code needs a clean integer.

**Data flow**: It receives token-detail data from a provider usage report. If the detail object or its extra data is missing, it returns 0. If a cache-write value is present, it checks that it is truly an integer, then returns it; if the value is malformed, it raises an error rather than letting bad billing data pass through.

**Call relations**: The chat streaming path uses this when it sees usage information, and _responses_usage uses it for the Responses API. In both cases, it feeds the shared Usage record with accurate cache-write counts.

*Call graph*: called by 2 (_complete_chat, _responses_usage).


##### `_responses_usage`  (lines 104–118)

```
def _responses_usage(raw: ResponseUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: This converts OpenAI Responses API usage data into the project’s standard Usage record. It separates normal input tokens, cached-read tokens, cache-write tokens, and output tokens so later pricing can be correct.

**Data flow**: It receives the raw usage object from a Responses API stream and a flag saying whether 30-minute cache writes should be priced. It reads cached tokens and cache-write tokens, checks that those counts do not exceed the total input tokens, subtracts them from normal input tokens, and returns a Usage object.

**Call relations**: OpenAIClient._complete_responses calls this whenever the Responses stream reports completed, incomplete, or failed usage. It relies on _cache_write_tokens to read the extra cache-write field safely before handing normalized usage back to the event stream.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 1 (_complete_responses); 1 external calls (__init__).


##### `openai_sdk_client`  (lines 121–127)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: This creates the asynchronous OpenAI SDK client used to make network calls. It disables the SDK’s own retries because this file has its own retry rules that are tied to streaming behavior.

**Data flow**: It receives an API key and optionally a base URL for an OpenAI-compatible provider. It builds and returns an AsyncOpenAI client with a fixed timeout and no internal retries.

**Call relations**: This is a construction helper for code that sets up an OpenAIClient. It hands off to the OpenAI SDK, while the retry decisions later happen inside OpenAIClient._complete_chat and OpenAIClient._complete_responses.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_status_retry_wait`  (lines 130–136)

```
def _status_retry_wait(error: openai.APIStatusError, delay: float) -> float
```

**Purpose**: This decides how long to wait after a provider status error, such as rate limiting. It respects the provider’s Retry-After header when present, but never waits less than the caller’s current backoff delay.

**Data flow**: It receives an OpenAI status error and the current delay. It reads the retry-after response header, tries to parse it as seconds, falls back to the current delay if it is missing or invalid, and returns the wait time to use.

**Call relations**: Both streaming paths call this after retryable status errors. It gives the retry loop a provider-aware wait time before the code sleeps and tries the request again.

*Call graph*: called by 2 (_complete_chat, _complete_responses).


##### `_openai_image`  (lines 139–143)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: This turns the project’s internal image source into the image format OpenAI’s APIs expect. It packages base64 image data as a data URL with the correct media type.

**Data flow**: It receives an ImageSource containing a media type and base64 data. It returns a small dictionary shaped as an OpenAI image_url part.

**Call relations**: openai_messages uses this when converting normal image blocks for Chat Completions. _openai_tool_result also uses it when a tool result contains images that must be sent back in a separate user-style message.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 146–162)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: This splits a tool result into text and images for the Chat Completions API. It is needed because OpenAI tool messages can carry text, but images must be sent as image parts in a separate user message.

**Data flow**: It receives either a plain string tool result or a tuple of content blocks. A string passes through as text with no images. A block list is separated into joined text parts and converted image parts, then returned as a pair.

**Call relations**: openai_messages calls this while translating ToolResultBlock objects. It uses _openai_image for any image blocks, then gives openai_messages the pieces it needs to emit the legal OpenAI message sequence.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 165–224)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: This converts the project’s conversation history into Chat Completions messages. It makes sure text, images, tool calls, and tool results are represented in the shape that the chat API accepts.

**Data flow**: It receives the system prompt and a tuple of internal Message objects. It trims image history, drops reasoning blocks that Chat Completions cannot carry, converts text and images into content parts, serializes tool-call arguments as JSON, turns tool results into tool messages, and returns a list of OpenAI-style message dictionaries.

**Call relations**: OpenAIClient._chat_kwargs calls this when building a Chat Completions request. During conversion it uses _openai_image and _openai_tool_result, then hands the finished message list back to the chat request builder.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 227–328)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: This converts the project’s conversation history into input items for OpenAI’s Responses API. Unlike the chat format, this path can preserve reasoning items so the model can resume a tool-use conversation correctly.

**Data flow**: It receives internal Message objects. It trims images, turns normal strings into simple input messages, converts text and images into Responses content parts, keeps reasoning items with their encrypted content and summaries, serializes tool calls, and converts tool results into function-call outputs. It returns a list of Responses API input items.

**Call relations**: responses_request calls this while assembling the full Responses API request. It is the main translator that lets OpenAIClient._complete_responses send prior reasoning, tool calls, and tool results back in the form the provider expects.

*Call graph*: called by 1 (responses_request); 13 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, ResponseOutputTextParam (+3 more)).


##### `responses_request`  (lines 331–363)

```
def responses_request(request: ModelRequest, effort: OpenAIEffort) -> dict[str, Any]
```

**Purpose**: This builds the full request body for the Responses API. It includes the model, instructions, conversation input, token limit, streaming flag, reasoning setting, and tool definitions.

**Data flow**: It receives a ModelRequest and the resolved reasoning effort. It converts messages through responses_input, adds fixed options such as streaming and store=false, asks the provider to include encrypted reasoning content, adds reasoning when allowed, and adds tools and tool-choice rules when present. It returns a dictionary ready to pass to the SDK.

**Call relations**: OpenAIClient._complete_responses calls this just before starting the provider stream. It depends on responses_input for the conversation portion and supplies the exact keyword arguments used by the OpenAI SDK.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `OpenAIClient.complete`  (lines 375–378)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the public streaming entry point for the OpenAI client. It chooses whether to use Chat Completions or Responses based on the model specification.

**Data flow**: It receives a ModelRequest. It checks the model spec’s api_surface field, then returns the async stream from either the Responses implementation or the Chat Completions implementation. The output is a stream of shared ModelEvent objects.

**Call relations**: Higher-level model code calls this when it wants a completion from this backend. complete does not translate the request itself; it dispatches to OpenAIClient._complete_responses or OpenAIClient._complete_chat, which do the real network streaming.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._reasoning_effort`  (lines 380–399)

```
def _reasoning_effort(self, request: ModelRequest) -> OpenAIEffort
```

**Purpose**: This decides what reasoning setting, if any, should be sent to OpenAI. It protects callers from accidentally letting a reasoning model spend answer tokens on hidden reasoning when they asked for reasoning to be off.

**Data flow**: It receives the current ModelRequest and reads the model specification. It asks the spec what reasoning value is legal for this request and its tools. It converts the project’s 'off' setting into OpenAI’s 'none', omits 'auto', and raises an error if the caller asked to turn reasoning off but the model/API combination cannot legally express that.

**Call relations**: OpenAIClient._chat_kwargs uses this when building Chat Completions arguments, and OpenAIClient._complete_responses uses it before building a Responses request. This keeps both API surfaces following the same reasoning rules.

*Call graph*: called by 2 (_chat_kwargs, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 401–430)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: This builds the keyword arguments for a Chat Completions streaming request. It is the chat-side request assembler.

**Data flow**: It receives a ModelRequest. It converts the system prompt and messages with openai_messages, adds the model name, token limit, streaming options, and usage reporting, then adds reasoning settings and tool definitions when appropriate. It returns a dictionary of arguments for the OpenAI SDK call.

**Call relations**: OpenAIClient._complete_chat calls this immediately before opening the chat stream. It relies on OpenAIClient._reasoning_effort for reasoning behavior and openai_messages for conversation translation.

*Call graph*: calls 2 internal fn (_reasoning_effort, openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 432–601)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This sends a Chat Completions request and turns the streamed provider chunks into the project’s ModelEvent stream. It also applies the project’s retry, usage, truncation, and credential-error rules for the chat API.

**Data flow**: It receives a ModelRequest. It builds SDK arguments, opens a streaming request, yields a stream-start marker, then yields text deltas and tool-call events as chunks arrive. It records usage data, separates cached-token counts, retries temporary failures before any visible output has been produced, reports key rejection clearly, raises truncation on length stops, and finally yields exactly one Usage event.

**Call relations**: OpenAIClient.complete calls this when the model spec says to use Chat Completions. It calls _chat_kwargs to prepare the request, _cache_write_tokens to normalize usage, and _status_retry_wait when a retryable HTTP status tells it how long to pause.

*Call graph*: calls 3 internal fn (_chat_kwargs, _cache_write_tokens, _status_retry_wait); called by 1 (complete); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenAIClient._complete_responses`  (lines 603–801)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This sends a Responses API request and turns its streamed events into the project’s ModelEvent stream. It is the Responses twin of the chat path, with extra support for preserving reasoning items.

**Data flow**: It receives a ModelRequest. It resolves reasoning effort, builds the Responses request, opens the stream, yields start, text, and tool-call events as they arrive, collects completed reasoning items for later replay, converts usage reports, and records terminal errors such as refusals, truncation, incomplete responses, or provider failures. It retries safe empty or temporary failures before visible output, then yields reasoning blocks followed by the final Usage event.

**Call relations**: OpenAIClient.complete calls this when the model spec chooses the Responses API. It uses OpenAIClient._reasoning_effort and responses_request before the network call, _responses_usage for token accounting, and _status_retry_wait during retry handling.

*Call graph*: calls 4 internal fn (_reasoning_effort, _responses_usage, _status_retry_wait, responses_request); called by 1 (complete); 10 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `request handling`

OpenRouter is a gateway: instead of talking directly to one model company, the system sends requests to OpenRouter, and OpenRouter routes them to the chosen provider. This file is the adapter that makes that gateway look like the project’s normal model client. It translates the project’s internal messages into OpenAI-style chat messages, streams text and tool-call updates back in the project’s event format, records token use, and retries when a provider is temporarily broken.

It also exposes two agent tools: generate_image and generate_video. These are not normal chat models, so they are registered as tools rather than as models an agent can be “pinned” to. The image tool sends a prompt to OpenRouter’s image API, decodes the returned image data, saves files in the workspace, and records the cost. The video tool starts an asynchronous video job, polls until it finishes, downloads the MP4, saves it, and records the cost.

A lot of this file is guardrails. It checks that requested image or video settings are ones the chosen provider actually supports, caps file sizes, preserves prompt caching by requiring a session id, and avoids double-billing workspaces that bring their own OpenRouter key.

#### Function details

##### `openrouter_slug`  (lines 270–280)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Turns the project’s model id into the provider/model name OpenRouter expects. It adds an OpenAI or Anthropic prefix for common bare model names, while leaving already-prefixed names alone.

**Data flow**: It receives a model name as text. If the name already contains a slash, it returns it unchanged; if it looks like an OpenAI or Claude model, it adds the matching provider prefix; otherwise it passes the name through. The output is the model slug sent to OpenRouter.

**Call relations**: When a request is prepared, OpenRouterModelClient._create_kwargs uses this to fill the model field. _openrouter_messages also uses it to decide whether Google-specific message cleanup is needed.

*Call graph*: called by 2 (_create_kwargs, _openrouter_messages).


##### `_chunk_provider`  (lines 283–288)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Extracts the upstream provider name from one streamed OpenRouter response chunk. This matters because OpenRouter can route the same model slug to different providers, and a bad provider can be skipped on a retry.

**Data flow**: It receives one streaming response chunk. It looks in OpenRouter’s extra metadata for a provider value and returns it as text if present; otherwise it returns nothing.

**Call relations**: OpenRouterModelClient.complete calls this while reading the stream. If the stream produces no useful output, the client can remember that provider and ask OpenRouter to avoid it on the next attempt.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 291–312)

```
def _usage_of(usage: CompletionUsage, cache_write_30m_rate: int) -> Usage
```

**Purpose**: Converts OpenAI SDK token usage into the project’s Usage record. It separates normal prompt tokens, cached prompt tokens, cache-write tokens, and output tokens so billing can be accurate.

**Data flow**: It receives the SDK’s usage object and the model’s cache-write price setting. It reads total prompt tokens, completion tokens, cached tokens, and OpenRouter’s cache-write extension. It validates that the parts do not exceed the total, then returns a Usage object with the token counts split into the project’s categories.

**Call relations**: OpenRouterModelClient.complete calls this whenever a streaming chunk includes usage information. The resulting Usage object is yielded back to the rest of the model loop as the final accounting for that call.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `_contains_json_reference`  (lines 327–339)

```
def _contains_json_reference(value: object) -> bool
```

**Purpose**: Checks whether a nested JSON-like value contains schema reference keys such as $ref. This protects against a specific OpenRouter/Google issue where such JSON in tool results can be rejected.

**Data flow**: It receives any Python value. It walks through dictionaries and lists, looking for keys that mean “this JSON points to another schema.” It returns true as soon as it finds one, or false if the whole value is safe.

**Call relations**: _openrouter_messages uses this after parsing tool-result text. If a Google-routed model would see JSON references, the message is wrapped as plain text instead.

*Call graph*: called by 1 (_openrouter_messages).


##### `_openrouter_messages`  (lines 342–379)

```
def _openrouter_messages(model: str, system: str, messages: tuple[Message, ...], accepts_image_input: bool) -> list[dict[str, object]]
```

**Purpose**: Builds the chat messages that will be sent to OpenRouter. It also applies OpenRouter-specific fixes, such as removing images for text-only models and wrapping troublesome Google tool-result JSON as text.

**Data flow**: It receives the model name, system prompt, conversation messages, and whether the model accepts image input. It may strip images, converts messages to OpenAI chat format, and for Google models inspects tool-result JSON for schema references. It returns the final list of message dictionaries sent over the wire.

**Call relations**: OpenRouterModelClient._create_kwargs calls this while assembling the API request. Inside, it relies on openrouter_slug to recognize Google routes, openai_messages for normal translation, omit_images when needed, and _contains_json_reference for the Google workaround.

*Call graph*: calls 2 internal fn (_contains_json_reference, openrouter_slug); called by 1 (_create_kwargs); 4 external calls (dumps, loads, omit_images, openai_messages).


##### `OpenRouterModelClient.complete`  (lines 411–545)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one streamed chat completion through OpenRouter and yields the project’s standard model events. It is the main bridge between OpenRouter’s streaming API and the rest of the agent system.

**Data flow**: It receives a ModelRequest containing the model, messages, tools, token limit, reasoning setting, and session id. It builds OpenRouter request arguments, opens a streaming completion, converts streamed text into TextDelta events, converts streamed function-call pieces into tool-call events, tracks usage, and finally yields Usage. It may retry temporary provider failures, reroute away from an empty provider, or raise an error if the model hit its token limit.

**Call relations**: The broader model runtime calls this when an agent needs a response from an OpenRouter-backed model. It hands request construction to _create_kwargs, reads provider metadata through _chunk_provider, converts token accounting through _usage_of, and asks _generation_usage for fallback usage if the stream did not include it.

*Call graph*: calls 4 internal fn (_create_kwargs, _generation_usage, _chunk_provider, _usage_of); 8 external calls (__init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenRouterModelClient._generation_usage`  (lines 547–572)

```
async def _generation_usage(self, generation_id: str, finish_reason: str) -> Usage | None
```

**Purpose**: Fetches token usage from OpenRouter’s generation lookup endpoint when the streaming response did not include usage. It is a fallback so the system can still bill and report a completed call.

**Data flow**: It receives a generation id and the finish reason seen in the stream. It makes a separate HTTP request to OpenRouter, validates that the returned generation matches and was not cancelled, then converts native token counts into a Usage object. If the lookup does not match the completed stream, it returns nothing.

**Call relations**: OpenRouterModelClient.complete calls this only after a stream ends without usage but has enough information to look up the generation. It emits a metric so operators can see when this fallback path is used.

*Call graph*: called by 1 (complete); 3 external calls (__init__, AsyncClient, emit_metric).


##### `OpenRouterModelClient._create_kwargs`  (lines 574–616)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Assembles the exact keyword arguments sent to the OpenAI-compatible SDK for an OpenRouter chat request. It also enforces that router requests name a session, because that is needed for reliable prompt caching.

**Data flow**: It receives a ModelRequest and a set of providers to ignore. It checks for a session id, prepares routing and reasoning options, converts the model name and messages, adds streaming and usage options, and includes tool definitions if any are available. It returns a dictionary ready for the SDK call.

**Call relations**: OpenRouterModelClient.complete calls this before each attempt, including retries that exclude dead providers. It delegates model-name mapping to openrouter_slug and message conversion to _openrouter_messages.

*Call graph*: calls 2 internal fn (_openrouter_messages, openrouter_slug); called by 1 (complete).


##### `_model_client`  (lines 619–624)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: Creates an OpenRouterModelClient for a particular model spec and API key. This is the factory the model registry can call when it needs a live client.

**Data flow**: It receives a ModelSpec and secret key. It builds an OpenAI-compatible async SDK client pointed at OpenRouter’s base URL, wraps it with the spec and key, and returns the OpenRouterModelClient.

**Call relations**: _openrouter stores this factory in each ModelSpec. Later, when the registry instantiates a model provider, this function supplies the concrete client object.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 627–647)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW, reasoning: ReasoningSupport=_REASONS, accepts_image_input: bool=True) -> ModelSpec
```

**Purpose**: Builds one ModelSpec entry for an OpenRouter chat model. A ModelSpec is the project’s description of a model: its id, price, context size, reasoning support, key slot, and client factory.

**Data flow**: It receives the model id, price, knowledge cutoff, and optional capability settings. It fills in the OpenRouter provider name, API key information, chat API surface, and client factory. It returns a ModelSpec ready to be listed in the extension manifest.

**Call relations**: The file uses this helper repeatedly to define OPENROUTER_MODEL_SPECS. Those specs are later exposed by manifest so the system can register these models.

*Call graph*: 1 external calls (__init__).


##### `GenerateImageInput._within_model_limits`  (lines 752–776)

```
def _within_model_limits(self) -> 'GenerateImageInput'
```

**Purpose**: Validates an image-generation request against the chosen model’s real limits. It catches unsupported combinations early, before OpenRouter returns a harder-to-use HTTP error.

**Data flow**: It receives the already-parsed image input object. It checks the requested image count, aspect ratio, and resolution against the allowlist for that model, fills in a default resolution when that model needs one, and returns the updated input object. Invalid choices become validation errors with plain explanations.

**Call relations**: Pydantic calls this automatically when tool arguments for generate_image are parsed. OpenRouterImages.generate then receives inputs that are already narrowed to what the selected image model can serve.


##### `_reported_cost_micro_usd`  (lines 784–796)

```
def _reported_cost_micro_usd(usage: object) -> int | None
```

**Purpose**: Reads a generation cost reported by OpenRouter and converts it into micro-dollars, where one US dollar is one million micro-dollars. It also understands the alternate cost field used when a customer brings their own upstream provider key.

**Data flow**: It receives a usage-like object, usually a dictionary from an image or video response. It looks first for a positive cost, then for a positive upstream inference cost inside cost details. If it finds one, it converts dollars to micro-dollars and returns an integer; otherwise it returns nothing.

**Call relations**: OpenRouterImages._charge and OpenRouterVideos._job call this before falling back to built-in list prices. This keeps metering aligned with what OpenRouter actually charged when that information is available.

*Call graph*: called by 2 (_charge, _job).


##### `OpenRouterImages.generate`  (lines 829–866)

```
async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Runs an image-generation tool call from request to saved workspace files. It sends the prompt to OpenRouter, stores returned images, and reports both file paths and cost back to the model.

**Data flow**: It receives the tool context and validated image arguments. It obtains the OpenRouter key, posts the generation request, turns provider errors into tool errors, decodes and checks returned images, writes them into the workspace, computes cost, meters the call when the platform key was used, and returns a ToolResult containing JSON file information plus inline image content.

**Call relations**: The generate_image tool handler calls this for actual work. It uses _refusal for failed HTTP responses, _images to decode the response, _save to write each file, _charge to price the call, and ToolContext.meter_images to record platform-billed usage.

*Call graph*: calls 5 internal fn (meter_images, _charge, _images, _refusal, _save); 6 external calls (__init__, __init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterImages._refusal`  (lines 868–883)

```
def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str
```

**Purpose**: Turns a failed image API response into a short explanation the model can read. This helps the model adjust the prompt, parameters, or account assumptions and try again.

**Data flow**: It receives the original image arguments and the HTTP response. It tries to read a JSON error message; if none is available, it uses the raw response text. It returns a bounded text message containing the model, status code, and provider explanation.

**Call relations**: OpenRouterImages.generate calls this when OpenRouter returns an error status for an image request. The returned text becomes the content of an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterImages._images`  (lines 885–915)

```
def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]
```

**Purpose**: Extracts usable images from OpenRouter’s response. It refuses empty or oversized image data so the workspace is not left with broken or unsafe files.

**Data flow**: It receives the image arguments and the decoded response body. It walks the response data entries, reads base64 image strings, decodes them to bytes, checks their size, assigns a media type, and returns GeneratedImage objects. If no usable image is present, or one is too large, it raises an OpenRouterImageError.

**Call relations**: OpenRouterImages.generate calls this after a successful HTTP response and before saving anything. Its output is passed to _save for disk writing and also returned inline as ImageContent.

*Call graph*: called by 1 (generate); 3 external calls (__init__, __init__, b64decode).


##### `OpenRouterImages._save`  (lines 917–924)

```
async def _save(self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage) -> str
```

**Purpose**: Writes one generated image into the workspace under the generated-images folder. It chooses the file extension from the image media type.

**Data flow**: It receives the tool context, image arguments, image index, and decoded image object. It builds a path from the requested file-name stem plus an index and extension, writes the raw bytes through the workspace sandbox, and returns the saved path.

**Call relations**: OpenRouterImages.generate calls this once for each decoded image. The collected paths are included in the tool result so the agent can later share or refer to the files.

*Call graph*: called by 1 (generate).


##### `OpenRouterImages._charge`  (lines 926–933)

```
def _charge(self, body: object, args: GenerateImageInput, images: int) -> int
```

**Purpose**: Computes the cost of an image generation in micro-dollars. It prefers OpenRouter’s reported charge and falls back to the file’s built-in list price per image.

**Data flow**: It receives the response body, image arguments, and number of images produced. It reads usage cost through _reported_cost_micro_usd; if that is missing, it multiplies the selected model’s fallback image price by the image count. It returns the cost as an integer.

**Call relations**: OpenRouterImages.generate calls this after images have been saved. The result is both written into the tool-result JSON and, when appropriate, passed to ToolContext.meter_images.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 1 (generate).


##### `_generate_image`  (lines 936–941)

```
async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Small tool-handler wrapper for generate_image. It connects the generic tool system to the OpenRouterImages class.

**Data flow**: It receives the tool context and parsed image arguments. It checks that the OpenRouter extension context is available, creates an OpenRouterImages helper using extension credentials and the configured test transport, and returns the result of its generate method.

**Call relations**: GENERATE_IMAGE_TOOL names this function as its handler. When an agent calls the generate_image tool, the tool runtime enters here and this wrapper hands the work to OpenRouterImages.generate.

*Call graph*: 1 external calls (__init__).


##### `GenerateVideoInput._within_model_limits`  (lines 997–1021)

```
def _within_model_limits(self) -> 'GenerateVideoInput'
```

**Purpose**: Validates a video-generation request against the selected video model’s supported duration, aspect ratios, and resolutions. It also fills in the default resolution used for pricing.

**Data flow**: It receives the parsed video input object. It checks that the duration is within the chosen model’s range, verifies the aspect ratio if one was requested, fills in the model’s default resolution if missing, and rejects unsupported resolution choices. It returns the updated input object.

**Call relations**: Pydantic runs this when arguments for generate_video are parsed. OpenRouterVideos.generate can then assume the request matches one of the known provider-supported combinations.


##### `OpenRouterVideos.generate`  (lines 1063–1104)

```
async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Runs a video-generation tool call from prompt to saved MP4 file. It starts an OpenRouter video job, waits for it to finish, downloads the result, records cost, and returns the file path.

**Data flow**: It receives the tool context and validated video arguments. It gets the OpenRouter key, posts a video request, turns immediate provider errors into tool errors, parses the job, polls until the job settles, reports failed jobs as tool errors, downloads the finished video, saves it into the workspace, computes cost, meters platform-billed usage, and returns a ToolResult containing file and cost details.

**Call relations**: The generate_video tool handler calls this for the real work. It uses _refusal for rejected starts, _job and _settled for job polling, _failure for completed-but-failed jobs, _download for the MP4 bytes, _save for workspace storage, and _charge for metering.

*Call graph*: calls 8 internal fn (meter_videos, _charge, _download, _failure, _job, _refusal, _save, _settled); 5 external calls (__init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterVideos._refusal`  (lines 1106–1121)

```
def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str
```

**Purpose**: Turns a failed video-start response into a short explanation for the model. This covers cases such as rejected prompts, invalid parameters, or account problems.

**Data flow**: It receives the video arguments and HTTP response. It tries to extract a JSON error message, otherwise uses the raw response body, trims it to a safe length, and returns a readable message with the model and status code.

**Call relations**: OpenRouterVideos.generate calls this when the initial POST to the video API returns an error. The text is placed into an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterVideos._job`  (lines 1123–1138)

```
def _job(self, body: object) -> VideoJob
```

**Purpose**: Parses OpenRouter’s video job description into the project’s VideoJob record. It makes sure there is a job id and status before the code tries to poll or download anything.

**Data flow**: It receives a decoded response body from either the initial request or a poll. It reads the job id, status, optional error message, and optional reported cost. If id or status is missing, it raises an OpenRouterVideoError. Otherwise it returns a VideoJob object.

**Call relations**: OpenRouterVideos.generate calls this after starting a video request, and OpenRouterVideos._settled calls it after each poll response. It uses _reported_cost_micro_usd so completed jobs can carry exact pricing forward.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 2 (_settled, generate); 2 external calls (__init__, __init__).


##### `OpenRouterVideos._settled`  (lines 1140–1161)

```
async def _settled(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> VideoJob
```

**Purpose**: Waits for an asynchronous video job to stop being pending or in progress. It prevents the agent turn from waiting forever by enforcing a timeout.

**Data flow**: It receives the video arguments, an HTTP client, and the current VideoJob. While the job is still pending or running, it checks the deadline, sleeps for the polling interval, fetches the latest job state, and parses it with _job. It returns the final non-pending VideoJob, or raises an error if polling fails or times out.

**Call relations**: OpenRouterVideos.generate calls this after parsing the initial job. The settled job then decides whether generation succeeded, failed, or should be reported as an error.

*Call graph*: calls 1 internal fn (_job); called by 1 (generate); 4 external calls (__init__, sleep, get, monotonic).


##### `OpenRouterVideos._failure`  (lines 1163–1167)

```
def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str
```

**Purpose**: Builds the error text for a video job that OpenRouter accepted but did not complete successfully. It preserves the provider’s own reason when available.

**Data flow**: It receives the original video arguments and the final VideoJob. It chooses the job’s error message if one exists, otherwise describes the ending status, trims the text, and returns a readable failure string.

**Call relations**: OpenRouterVideos.generate calls this when _settled returns a job whose status is not completed. The returned message becomes an error ToolResult.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._download`  (lines 1169–1188)

```
async def _download(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> bytes
```

**Purpose**: Downloads the completed video file and checks that it is present and not too large. This protects the workspace from empty downloads and oversized files.

**Data flow**: It receives the video arguments, HTTP client, and completed VideoJob. It requests the first video content item, checks for HTTP errors, reads the bytes, rejects empty content, rejects files over the size cap, and returns the raw MP4 bytes.

**Call relations**: OpenRouterVideos.generate calls this only after a job reaches the completed status. Its output is handed to _save so the MP4 can be written into the workspace.

*Call graph*: called by 1 (generate); 2 external calls (__init__, get).


##### `OpenRouterVideos._save`  (lines 1190–1194)

```
async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str
```

**Purpose**: Writes the downloaded MP4 into the workspace under the generated-videos folder. It uses the file-name stem chosen in the tool arguments.

**Data flow**: It receives the tool context, video arguments, and raw video bytes. It builds a path ending in .mp4, writes the bytes through the workspace sandbox, and returns the saved path.

**Call relations**: OpenRouterVideos.generate calls this after downloading the completed video. The returned path is included in the final tool result so the agent can reference or share the file.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._charge`  (lines 1196–1204)

```
def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int
```

**Purpose**: Computes the video generation cost in micro-dollars. It uses OpenRouter’s reported cost when present, otherwise falls back to the selected model’s per-second price for the chosen resolution.

**Data flow**: It receives the video arguments and final VideoJob. If the job already carries a reported cost, it returns that. Otherwise it looks up the chosen model’s price for the actual resolution and multiplies by the requested duration.

**Call relations**: OpenRouterVideos.generate calls this after the video is saved. The cost is included in the tool-result JSON and, when the platform key was used, recorded through ToolContext.meter_videos.

*Call graph*: called by 1 (generate).


##### `_generate_video`  (lines 1207–1212)

```
async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Small tool-handler wrapper for generate_video. It connects the project’s generic tool runtime to the OpenRouterVideos implementation.

**Data flow**: It receives the tool context and parsed video arguments. It checks that the OpenRouter extension context exists, creates an OpenRouterVideos helper with extension credentials and the configured transport, and returns the result of its generate method.

**Call relations**: GENERATE_VIDEO_TOOL names this function as its handler. When an agent calls generate_video, the tool runtime enters here and this wrapper delegates to OpenRouterVideos.generate.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1225–1241)

```
def manifest() -> Manifest
```

**Purpose**: Declares what this extension offers to the host system: its name and version, its OpenRouter chat models, its image and video tools, and the credential slot for the API key.

**Data flow**: It takes no input. It builds a Manifest object containing model specs, tool definitions, and a CredentialSlot describing the OpenRouter API key. The returned manifest is what the extension loader reads.

**Call relations**: The extension system calls this during extension discovery or startup. The manifest hands the host everything needed to register OpenRouter models, expose the generation tools, and know which secret key may be supplied by the workspace.

*Call graph*: 2 external calls (__init__, __init__).

## 📊 State Registers Touched

- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-transcript-history` — The saved conversation transcript, including compacted summaries and durable final results that later stages read instead of relying on memory.
- `reg-live-update-hub` — The live activity stream that carries turn progress, tool status, cancellations, mid-turn replies, and final updates to connected viewers.
- `reg-cancellation-state` — The shared brake state that marks work as stopping or cancelled so model calls, tools, workflows, and retries do not continue stale work.
- `reg-model-catalog` — The shared AI model catalog that records available providers, model names, prices, limits, API routes, key sources, and reasoning support.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-prompt-and-delivery-policy` — The prompt, delivery-rule, compaction, and prompt-change proposal state that controls what instructions are rendered and how replies should be shaped.
- `reg-service-connection-pools` — Process-local shared connection/client pools for database, Redis/pubsub, HTTP/provider calls, and other long-lived service clients reused by requests, workers, tools, and jobs.
- `reg-turn-resource-budget` — In-flight per-turn resource budget and usage accumulator for spend, model tokens, cache reads, sandbox tokens, egress, retries, and stop conditions before final ledger reconciliation.
- `reg-surface-delivery-outbox` — Durable outgoing surface delivery state for final replies, mid-turn replies, writebacks, claims, retries, and de-duplication across Slack, iMessage, web, terminal, and other surfaces.
- `reg-backend-provider-registry` — Process-local registry mapping provider names to active backend implementations for models, search, embeddings, memory, connectors, browser access, auth, billing, and feature services.
- `reg-rendered-prompt-audit` — Per-turn rendered prompt metadata, slot validation results, and prompt digests used to trace or replay the exact model prompt later.
