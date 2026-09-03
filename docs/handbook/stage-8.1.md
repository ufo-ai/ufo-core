# Model provider request/stream adapters  `stage-8.1`

This stage is shared behind-the-scenes support for talking to AI model services. UFO has its own internal shape for a model request and for streamed reply events. These files translate between that common shape and the different outside providers, like plug adapters for different wall sockets.

The package file simply makes the models folder importable. The catalog is a built-in list of directly supported Anthropic and OpenAI models, including their names, limits, prices, and which API key setting to use. The registry is the main lookup desk: when the system needs a model, it finds the model ID, provider, price rules, and builds the right client.

The Anthropic adapter turns UFO messages into Claude Messages API calls, then converts Claude’s streaming output back into UFO events, including tool calls, images, reasoning text, retries, and usage counts. The OpenAI adapter does the same for OpenAI-style services, including Chat Completions, Responses, and Codex backends. The OpenRouter extension adds another provider bridge, including chat plus image and video generation tools.

## Files in this stage

### Model package catalog
Package scaffolding and the built-in model catalog establish the known direct Anthropic and OpenAI model definitions.

### `core/src/ufo/harness/models/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere in the project can refer to this folder using normal import paths, such as importing things from `ufo.harness.models` or its child modules.

Think of it like a label on a drawer: the drawer may hold useful files, but this label tells Python that the drawer is part of the organized code system. Without this file, some Python environments or tooling might not recognize the `models` directory as a package, which could make imports fail or behave differently.

Because the file is empty, it does not create objects, run setup steps, or re-export names. Its value is structural: it helps define the shape of the project’s module tree.


### `core/src/ufo/harness/models/catalog.py`

`config` · `startup / config load`

This catalog gives the rest of the system one reliable place to look up facts about built-in models. Without it, the program could pick the wrong network API, bill usage at the wrong price, or send a request shape that the provider rejects.

The file defines shared names for API key locations, default context window sizes, and whether a model supports “reasoning” features, meaning extra thinking controls that can be combined with tool use. It then provides two small builders: one for Anthropic models and one for OpenAI models. Each builder creates a ModelSpec, which is like a recipe card saying: this is the model id, this is the provider, this is the price, this is the knowledge cutoff, this is how large a conversation can get, and this is the function to use when making a live client.

The main function, core_model_specs, returns all built-in model recipe cards. It includes special details where they matter: for example, some GPT-5.6 models must use OpenAI’s “responses” API surface because the normal chat endpoint cannot accept one combination of options. At import time, the file also turns these specs into lookup tables for prices and a pricing digest, so accounting code can stamp charges consistently.

#### Function details

##### `_anthropic_client`  (lines 32–35)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Creates a ready-to-use Anthropic model client for a particular model spec and credential. It also notices whether the credential is an OAuth-style credential, so the Anthropic wrapper can treat it correctly.

**Data flow**: It receives a ModelSpec and a key string. It first builds the underlying Anthropic SDK client from the key, checks what kind of credential the key is, and then wraps both pieces together with the spec. The result is an AnthropicClient object that later code can use to talk to that model.

**Call relations**: This function is not used while merely listing the catalog; it is stored inside Anthropic ModelSpec objects as the way to create a live client later. When some later part of the system chooses an Anthropic model, the spec can call this factory, which in turn relies on anthropic_sdk_client and is_oauth_credential before constructing AnthropicClient.

*Call graph*: 3 external calls (__init__, anthropic_sdk_client, is_oauth_credential).


##### `_openai_client`  (lines 38–42)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Creates a ready-to-use OpenAI model client for a particular model spec and credential. It chooses between the normal OpenAI client and a Codex-style client depending on what kind of account information is found in the key.

**Data flow**: It receives a ModelSpec and a key string. It asks chatgpt_account_id whether the key contains a ChatGPT account id. If not, it builds a normal OpenAI SDK client and wraps it in OpenAIClient. If an account id is present, it builds a Codex SDK client for that account and marks the wrapper as codex-enabled. The output is an OpenAIClient ready for later requests.

**Call relations**: Like the Anthropic client factory, this function is saved inside OpenAI ModelSpec objects rather than called during catalog construction. Later, when the system needs to contact an OpenAI-backed model, the spec can call this function, which then hands off to openai_sdk_client or codex_sdk_client before creating OpenAIClient.

*Call graph*: 4 external calls (__init__, chatgpt_account_id, codex_sdk_client, openai_sdk_client).


##### `_anthropic`  (lines 45–65)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS_WITH_TOOLS) -> ModelSpec
```

**Purpose**: Builds the catalog entry for one Anthropic model. It keeps Anthropic-specific defaults in one place so each model row only needs to state what is different, such as its id, price, cutoff date, or larger context window.

**Data flow**: It receives the model id, pricing, knowledge cutoff, API key environment-variable name, and optional choices for context size and reasoning support. It combines those with Anthropic defaults: the provider name, the Anthropic client factory, the Anthropic key slot, and the chat API surface. The output is a ModelSpec describing exactly how this model should be used and billed.

**Call relations**: core_model_specs calls this helper once for each built-in Anthropic model. The helper does the repetitive ModelSpec construction, so the main catalog can stay readable and focus on the model facts.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 68–82)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: Builds the catalog entry for one OpenAI model. It applies OpenAI-specific defaults, including the provider name, key slot, context window, reasoning support, and which OpenAI API surface to use.

**Data flow**: It receives the model id, pricing, knowledge cutoff, API key environment-variable name, and optionally an API surface such as chat or responses. It combines those inputs with OpenAI defaults and returns a ModelSpec. That spec says how to create the client, how large the conversation may grow for billing-safe compaction, and how usage should be priced.

**Call relations**: core_model_specs calls this helper for each built-in OpenAI model. For models that need the responses API rather than the chat API, core_model_specs passes that choice in, and this helper stores it in the resulting ModelSpec.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 85–190)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: Returns the complete set of built-in model specifications for core. This is the central list that tells the system which Anthropic and OpenAI models exist by default and what their operational and pricing rules are.

**Data flow**: It receives the names of the environment variables that should hold the Anthropic and OpenAI keys. It creates ModelPrice objects for each model, then passes those prices and model facts into the Anthropic and OpenAI helper builders. The output is a tuple of ModelSpec objects. The module immediately uses that tuple to build CORE_MODEL_SPECS, price lookup data, a pricing table, and a digest that can identify the exact pricing set.

**Call relations**: This is the top-level catalog builder in the file. It calls _anthropic and _openai repeatedly, and those helpers produce the ModelSpec entries that the rest of the model registry and billing ledger can rely on.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### Direct provider adapters
Anthropic and OpenAI bridges translate UFO model requests into provider calls and convert streamed responses back into UFO events.

### `core/src/ufo/harness/models/anthropic.py`

`io_transport` · `request handling`

This file lets the rest of the system talk to Anthropic without needing to know Anthropic's exact wire format. Think of it like a translator at a live conversation: it rewrites outgoing messages into the provider's language, listens to the streamed answer piece by piece, and translates each piece back into the system's shared event format.

It covers three main jobs. First, it builds the Anthropic SDK client using either an API key or an OAuth access token, because those authenticate differently. Second, it converts UFO's message content, such as text, images, tool calls, tool results, and saved reasoning, into Anthropic content blocks. Third, it runs the streaming request and emits internal events for visible text, tool calls, stream start, hidden reasoning blocks, and final token usage.

A large part of the file is defensive behavior around provider failures. Before any visible output has been sent, temporary network or provider errors can be retried with increasing delays. After visible output has started, the file raises a special interruption error so the wider engine can throw away the partial answer and rerun the round safely. It also treats max-token cutoffs, refusals, rejected keys, rate limits, and empty responses in specific ways so callers get meaningful failures instead of vague SDK errors.

#### Function details

##### `anthropic_sdk_client`  (lines 56–72)

```
def anthropic_sdk_client(credential: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the Anthropic asynchronous SDK client that will make API calls. It chooses the right authentication style depending on whether the credential is an OAuth token or a normal API key, and it disables the SDK's built-in retries so this file's own retry rules stay in charge.

**Data flow**: It receives one credential string. It checks whether that string looks like an Anthropic OAuth token, then builds and returns an Anthropic client configured with either bearer-token OAuth headers or an API key, plus a shared timeout and no automatic SDK retries.

**Call relations**: This is the setup doorway for Anthropic access. It asks is_oauth_credential to classify the credential, then hands the chosen settings to Anthropic's SDK constructor so later code can use the returned client inside AnthropicClient.

*Call graph*: calls 1 internal fn (is_oauth_credential); 1 external calls (AsyncAnthropic).


##### `is_oauth_credential`  (lines 75–77)

```
def is_oauth_credential(credential: str) -> bool
```

**Purpose**: Decides whether a credential is an Anthropic OAuth access token rather than a standard API key. This matters because the same API uses different headers for those two cases.

**Data flow**: It receives a credential string, checks whether it starts with the known OAuth token prefix, and returns true or false.

**Call relations**: It is used by anthropic_sdk_client during client creation. Its answer controls which authentication path that setup function takes.

*Call graph*: called by 1 (anthropic_sdk_client).


##### `_anthropic_image`  (lines 80–84)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns UFO's internal image source into the image block shape Anthropic expects. It is a small conversion helper for messages and tool results that include images.

**Data flow**: It receives an image source containing a media type and base64 image data. It wraps those fields in a dictionary using Anthropic's expected names and returns that dictionary.

**Call relations**: It is called when full message content is converted by anthropic_content, and also when a tool result part is converted by _anthropic_tool_result_part. It keeps image formatting consistent in both places.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 87–92)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic's format. Tool results can include plain text or images, and this function handles those two supported cases.

**Data flow**: It receives one tool-result content block. If it is text, it returns a text dictionary; if it is an image, it delegates the image conversion and returns the resulting image dictionary.

**Call relations**: It is used inside anthropic_content when a tool result contains multiple structured parts. For images, it hands off to _anthropic_image so image formatting is not duplicated.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 95–129)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Converts UFO's internal message content into the content format Anthropic's Messages API accepts. It preserves Anthropic-compatible text, images, tool calls, tool results, and reasoning blocks, while dropping reasoning blocks that belong to another provider's format.

**Data flow**: It receives either a plain string or a tuple of internal content blocks. A string is returned unchanged; structured blocks are walked one by one and turned into Anthropic dictionaries. Image blocks and image tool-result parts go through the shared image conversion helpers. The output is either the original string or a list of Anthropic-ready content dictionaries.

**Call relations**: AnthropicClient._request_kwargs calls this while building the outgoing API request. This function sits at the boundary between UFO's shared model representation and Anthropic's provider-specific message format.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (_request_kwargs).


##### `_AnthropicRetry.transport`  (lines 139–174)

```
async def transport(self, error: Exception, yielded: bool) -> _AnthropicRetry
```

**Purpose**: Decides what to do after a network-style streaming failure, such as a timeout or dropped connection. It retries if it is still safe, or raises a clear interruption/failure when retrying would be unsafe or exhausted.

**Data flow**: It receives the error and a flag saying whether any visible output was already yielded. It increases the attempt count, logs what happened, may emit a retry metric, waits for the current backoff delay, and returns an updated retry state with a longer next delay. If output had already been shown, or the retry budget is spent, it raises instead.

**Call relations**: AnthropicClient.complete calls this when the stream fails due to transport problems. If nothing user-visible has gone out yet, it gives complete a new retry state so the request can be sent again; if a partial answer escaped, it raises ModelStreamInterrupted so the wider round logic can discard and rerun the partial round.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_AnthropicRetry.status`  (lines 176–230)

```
async def status(self, error: anthropic.APIStatusError, yielded: bool) -> _AnthropicRetry
```

**Purpose**: Decides what to do after Anthropic reports an API status error. It separates rejected credentials, rate limits, retryable provider trouble, and deterministic client mistakes so each case gets the right response.

**Data flow**: It receives an Anthropic status error and a flag saying whether output was already yielded. It checks the HTTP status code, logs the outcome, may convert rejected keys or rate limits into project-specific errors, may wait using the provider's retry-after header, and returns an updated retry state when retrying is allowed. Otherwise it raises the appropriate error.

**Call relations**: AnthropicClient.complete calls this when the Anthropic stream or request reports a status failure. It uses ModelStreamInterrupted for unsafe mid-stream failures, and otherwise hands complete either a new retry state or a final exception.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_AnthropicStream.__init__`  (lines 234–245)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh state tracker for one Anthropic streaming response. It starts with no emitted output, no tool-call IDs, no reasoning blocks, and zero usage counts.

**Data flow**: It takes no outside data beyond the new object being created. It initializes dictionaries and counters that will be filled as Anthropic stream events arrive.

**Call relations**: AnthropicClient.complete creates one of these for each request attempt. The rest of the stream-processing methods update this object as events arrive and later use it to produce final reasoning and usage information.

*Call graph*: called by 1 (complete).


##### `_AnthropicStream.accept`  (lines 247–291)

```
def accept(self, event: object) -> tuple[ModelEvent, ...]
```

**Purpose**: Reads one raw Anthropic stream event and turns any user-visible part into UFO model events. It also quietly records hidden state such as token usage, tool-call IDs, reasoning text, reasoning signatures, and stop reasons.

**Data flow**: It receives one event from Anthropic's stream. Depending on the event kind, it may update usage counters, remember a tool-call ID, emit a text delta, emit a tool-call start or tool-call JSON fragment, collect thinking text, store redacted reasoning, or record the final stop reason. It returns a tuple of zero or more UFO events, and marks the stream as having yielded visible output when it emits something.

**Call relations**: AnthropicClient.complete feeds every raw stream event into this method. When the event starts usage tracking, accept calls _record_input_usage; when a thinking block ends, it calls _close_thinking. The events it returns are immediately yielded back to the model engine.

*Call graph*: calls 2 internal fn (_close_thinking, _record_input_usage); 4 external calls (__init__, __init__, __init__, __init__).


##### `_AnthropicStream._record_input_usage`  (lines 293–301)

```
def _record_input_usage(self, usage: Any) -> None
```

**Purpose**: Stores the input-side token counts reported by Anthropic. This includes normal input tokens and cache-related token counts, which matter for accounting and cost tracking.

**Data flow**: It receives Anthropic's usage object from the start of a message. It copies input token counts into the stream state, treating missing cache values as zero and supporting both older and newer Anthropic cache-reporting shapes.

**Call relations**: _AnthropicStream.accept calls this when it sees the message-start event. Later, has_usage and usage use these stored counts to decide whether and what usage record should be yielded.

*Call graph*: called by 1 (accept).


##### `_AnthropicStream._close_thinking`  (lines 303–312)

```
def _close_thinking(self, index: int) -> None
```

**Purpose**: Finalizes one Anthropic reasoning, or thinking, block after all its streamed pieces have arrived. It combines the pieces and stores them with the required signature so the reasoning can be echoed back correctly in later turns.

**Data flow**: It receives the index of a thinking block. It retrieves and removes the collected text pieces and signature for that index, checks that the signature is present, joins the text, creates a ThinkingBlock, and appends it to the ordered reasoning list.

**Call relations**: _AnthropicStream.accept calls this when Anthropic says a thinking block has stopped. The completed reasoning blocks are later yielded by AnthropicClient.complete after the stream closes, just before final usage.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_AnthropicStream.has_usage`  (lines 314–320)

```
def has_usage(self) -> bool
```

**Purpose**: Reports whether this stream has collected any input-side usage information yet. This is useful when an error happens before the normal final usage event can be produced.

**Data flow**: It reads the stored input and cache token counters. If any of them are nonzero, it returns true; otherwise it returns false.

**Call relations**: AnthropicClient.complete uses this during error paths and unusual endings to decide whether it can still yield a useful usage record before raising or retrying.


##### `_AnthropicStream.usage`  (lines 322–329)

```
def usage(self) -> Usage
```

**Purpose**: Builds UFO's standard usage record from the token counts collected during the Anthropic stream. This is the final accounting summary for the model call.

**Data flow**: It reads the stream state's input tokens, output tokens, and cache token counters. It creates and returns a Usage object, using zero for output tokens if Anthropic has not supplied them yet.

**Call relations**: AnthropicClient.complete yields this at the end of successful streams and sometimes before retrying or raising after failures. It packages the state collected by accept and _record_input_usage into the shared record format used elsewhere.

*Call graph*: 1 external calls (__init__).


##### `AnthropicClient._request_kwargs`  (lines 338–384)

```
def _request_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the keyword arguments for Anthropic's messages.create API call from UFO's ModelRequest. It is where model name, system prompt, messages, tools, cache settings, reasoning settings, and streaming mode are assembled into Anthropic's expected request shape.

**Data flow**: It receives a ModelRequest. It trims images as needed, converts each message's content with anthropic_content, adds the system prompt and optional OAuth system prefix, sets token and cache options, translates reasoning effort into Anthropic thinking settings, and adds tool definitions plus tool-choice rules when tools are available. It returns a dictionary ready to pass to the Anthropic SDK.

**Call relations**: AnthropicClient.complete calls this immediately before starting an API stream. It is the main outgoing translation step, using anthropic_content for message bodies and trim_images to keep image history within expected limits.

*Call graph*: calls 1 internal fn (anthropic_content); called by 1 (complete); 1 external calls (trim_images).


##### `AnthropicClient.complete`  (lines 386–476)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one streaming Anthropic completion and yields UFO model events as they happen. It also owns the provider-specific retry policy, final reasoning delivery, usage reporting, and special errors for truncation, refusal, rejected keys, rate limits, and interrupted streams.

**Data flow**: It receives a ModelRequest. It builds Anthropic request arguments, opens a streaming API call, yields a stream-start marker when the first event arrives, sends each raw event through _AnthropicStream.accept, and yields the resulting text or tool-call events. After the stream ends, it checks the stop reason, yields hidden reasoning blocks in order, yields final usage, and returns. If temporary errors happen before visible output, it waits and retries; if errors happen after visible output, it raises an interruption so the partial round can be discarded.

**Call relations**: This is the main method other model-running code uses to talk to Anthropic. It creates _AnthropicStream state for each attempt, calls _request_kwargs to prepare the provider call, relies on _AnthropicRetry methods to decide retry behavior, emits metrics for empty retries, and yields the shared ModelEvent objects consumed by the rest of the harness.

*Call graph*: calls 2 internal fn (_request_kwargs, __init__); 5 external calls (__init__, __init__, __init__, __init__, emit_metric).


### `core/src/ufo/harness/models/openai.py`

`io_transport` · `request handling during model calls and streaming`

This file lets the rest of the system talk to OpenAI-like model providers without caring about each provider's exact wire format. UFO has its own plain internal shapes for messages, images, tool calls, tool results, reasoning, streamed text, and token usage. OpenAI has two different APIs for similar work: Chat Completions and Responses. This file translates between those worlds.

The main class, OpenAIClient, chooses the correct API surface for a model. It can also override that choice when the credential is a ChatGPT account token, because that token goes to the ChatGPT Codex backend rather than the normal OpenAI API. Think of this file like a travel adapter: the appliance is the same conversation, but the plug shape changes depending on the wall socket.

It also takes care of streaming. As text and tool calls arrive piece by piece, helper stream classes convert provider chunks into ModelEvent objects that the engine understands. At the end, they report token usage and raise clear errors for truncation, refusal, rate limits, rejected keys, or broken streams.

A major responsibility here is safe retry behavior. Before any visible output is yielded, temporary network or server problems can be retried. After output has already been shown, a broken stream becomes an interrupted round so the engine can discard partial output rather than mixing two attempts together.

#### Function details

##### `_cache_write_tokens`  (lines 112–120)

```
def _cache_write_tokens(details: PromptTokensDetails | InputTokensDetails | None) -> int
```

**Purpose**: Reads OpenAI's optional count of tokens written into the prompt cache. This matters because cached tokens can be priced differently, so usage accounting needs to split them out correctly.

**Data flow**: It receives token-detail data from an OpenAI usage object. It looks inside the extra provider fields for cache_write_tokens, treats missing data as zero, verifies the value is a real integer, and returns that integer. If the provider sends a malformed value, it raises an error instead of silently producing bad billing data.

**Call relations**: The chat and Responses usage converters both call this helper while turning provider-specific usage reports into UFO's shared Usage record.

*Call graph*: called by 2 (_chat_usage, _responses_usage).


##### `_responses_usage`  (lines 123–137)

```
def _responses_usage(raw: ResponseUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: Converts token usage from the OpenAI Responses API into UFO's common Usage format. It separates normal input tokens, cache reads, cache writes, and output tokens.

**Data flow**: It receives a Responses API usage object and a flag saying whether 30-minute cache writes should count as separately priced. It reads total input, output, cached, and cache-write tokens, checks that the pieces do not add up to more than the total, and returns a Usage object. Bad provider totals cause a runtime error.

**Call relations**: _ResponsesStream.accept calls this when a completed or failed Responses stream reports usage, and _ResponsesStream._record_incomplete calls it when an incomplete response still includes usage.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 2 (_record_incomplete, accept); 1 external calls (__init__).


##### `_chat_usage`  (lines 140–154)

```
def _chat_usage(raw: openai.types.CompletionUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: Converts token usage from the OpenAI Chat Completions API into UFO's common Usage format. It gives the rest of the system one consistent way to count tokens no matter which OpenAI API was used.

**Data flow**: It receives a Chat Completions usage object and a cache-pricing flag. It extracts prompt tokens, completion tokens, cached prompt tokens, and cache-write tokens, validates the counts, and returns a Usage object with the prompt total adjusted to exclude separately tracked cache tokens.

**Call relations**: _ChatStream.accept calls this when a streaming chat chunk includes final usage information.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 1 (accept); 1 external calls (__init__).


##### `openai_sdk_client`  (lines 157–170)

```
def openai_sdk_client(api_key: str, base_url: str | None=None, default_headers: dict[str, str] | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Builds the OpenAI Python SDK client with UFO's chosen timeout and retry policy. The SDK's own retries are disabled because this file implements retries in a way that understands streaming and partial output.

**Data flow**: It receives an API key, an optional base URL, and optional default headers. It creates and returns an asynchronous OpenAI SDK client configured for that endpoint, with a fixed provider timeout and no SDK-level retries.

**Call relations**: codex_sdk_client uses this helper to make a Codex-specific client. Other OpenAI-compatible providers can also use the same construction pattern with their own base URL and headers.

*Call graph*: called by 1 (codex_sdk_client); 1 external calls (AsyncOpenAI).


##### `chatgpt_account_id`  (lines 173–186)

```
def chatgpt_account_id(credential: str) -> str | None
```

**Purpose**: Detects whether a credential is a ChatGPT account token and, if so, extracts the account id inside it. This lets the system distinguish a member's ChatGPT login token from a normal OpenAI platform API key.

**Data flow**: It receives a credential string. If it looks like a three-part JWT token, it decodes the middle payload, reads the ChatGPT auth claims, and returns the embedded account id. If the credential is not a JWT, cannot be decoded, or lacks the expected claim, it returns None.

**Call relations**: This helper is used when deciding whether a credential should talk to the normal OpenAI API or to the ChatGPT Codex backend.

*Call graph*: 2 external calls (urlsafe_b64decode, loads).


##### `codex_sdk_client`  (lines 189–205)

```
def codex_sdk_client(credential: str, account: str) -> openai.AsyncOpenAI
```

**Purpose**: Builds an OpenAI SDK client pointed at the ChatGPT Codex backend for account-token credentials. That backend needs special headers on every request, not just a bearer token.

**Data flow**: It receives the member credential and the ChatGPT account id. It prepares the required Codex headers, including account id, originator, beta flag, and streaming accept header, then returns an SDK client aimed at the Codex base URL.

**Call relations**: It delegates the actual SDK construction to openai_sdk_client, adding the Codex-specific endpoint and headers before doing so.

*Call graph*: calls 1 internal fn (openai_sdk_client).


##### `_status_retry_wait`  (lines 208–214)

```
def _status_retry_wait(error: openai.APIStatusError, delay: float) -> float
```

**Purpose**: Chooses how long to wait before retrying an HTTP status error. It respects the provider's retry-after header when present, but never waits less than the current backoff delay.

**Data flow**: It receives an OpenAI status error and the current planned delay. It tries to parse the response's retry-after header as seconds, falls back to the given delay if missing or invalid, and returns the larger safe wait time.

**Call relations**: _OpenAIRetry.status calls this when deciding the pause before another try after a rate limit or server error.

*Call graph*: called by 1 (status).


##### `_openai_image`  (lines 217–221)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns UFO's internal image data into the image-url shape expected by OpenAI chat messages. The image bytes are carried as a base64 data URL.

**Data flow**: It receives an ImageSource containing a media type and base64 data. It wraps those fields in OpenAI's expected image_url dictionary and returns that dictionary.

**Call relations**: openai_messages uses it for user images, and _openai_tool_result uses it when a tool result contains images that must be sent back to the model.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 224–240)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Splits a tool result into text and images for the Chat Completions API. This is needed because OpenAI chat tool messages are text-only, while images must be sent separately as user content.

**Data flow**: It receives either a plain string tool result or a tuple of content blocks. It gathers text blocks into one newline-joined string and converts image blocks into OpenAI image dictionaries. It returns both the text and the list of images.

**Call relations**: openai_messages calls this while translating internal ToolResultBlock values into OpenAI chat messages.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 243–302)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts UFO's conversation history into the message list required by OpenAI Chat Completions. It preserves text, images, tool calls, and tool results where that API can represent them.

**Data flow**: It receives the system prompt and prior UFO messages. It trims images as needed, drops reasoning blocks because Chat Completions has no place for them, converts text and images into OpenAI content parts, serializes tool-call arguments as JSON, and emits tool-result messages. Tool-result images are lifted into a following user message because chat tool messages cannot contain images.

**Call relations**: OpenAIClient._chat_kwargs calls this when building the request body for the Chat Completions streaming call.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 305–406)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: Converts UFO's conversation history into the richer input item list expected by OpenAI's Responses API. Unlike Chat Completions, this format can carry reasoning items back to the provider.

**Data flow**: It receives prior UFO messages. It trims images, converts ordinary text and images into Responses input parts, preserves OpenAI reasoning items with their encrypted content and summaries, serializes tool calls as function-call items, and converts tool results into function-call-output items. Anthropic-style thinking blocks are skipped because they are not the Responses wire format.

**Call relations**: responses_request calls this to fill the input field of a Responses API request.

*Call graph*: called by 1 (responses_request); 13 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, ResponseOutputTextParam (+3 more)).


##### `responses_request`  (lines 409–448)

```
def responses_request(request: ModelRequest, effort: OpenAIEffort, codex: bool=False) -> dict[str, Any]
```

**Purpose**: Builds the full request body for OpenAI's Responses API. It includes the model, instructions, conversation input, streaming choice, reasoning settings, tools, and tool-choice rules.

**Data flow**: It receives a ModelRequest, a resolved reasoning effort, and a flag saying whether the request is going to the Codex backend. It converts messages with responses_input, adds streaming and encrypted-reasoning options, includes token limits except for Codex, adds reasoning when allowed, and describes available tools if any. It returns a dictionary ready to pass to the SDK.

**Call relations**: OpenAIClient._complete_responses calls this immediately before starting a Responses stream.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `_OpenAIRetry.transport`  (lines 458–493)

```
async def transport(self, error: Exception, yielded: bool) -> _OpenAIRetry
```

**Purpose**: Decides what to do after a network-level streaming problem, such as a timeout or dropped connection. It retries only while it is still safe to do so.

**Data flow**: It receives the exception and a flag saying whether any visible model output has already been yielded. If output has already appeared, it raises ModelStreamInterrupted so the partial round can be discarded. If no output appeared and retries remain, it logs the retry, emits a metric, sleeps, and returns a new retry state with a larger delay. If retries are exhausted, it re-raises the original error.

**Call relations**: Both OpenAIClient._complete_chat and OpenAIClient._complete_responses use this retry helper when transport errors happen during provider streaming.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_OpenAIRetry.status`  (lines 495–543)

```
async def status(self, error: openai.APIStatusError, yielded: bool) -> _OpenAIRetry
```

**Purpose**: Decides what to do after the provider returns an HTTP error status. It turns common provider failures into clearer UFO errors and retries temporary failures when safe.

**Data flow**: It receives an OpenAI status error and a flag saying whether output was already yielded. A rejected key becomes the model spec's credential error immediately. Rate limits and server errors can be retried before visible output, using retry-after-aware backoff. If output was already yielded, retryable errors become ModelStreamInterrupted. Exhausted rate limits become the spec's rate-limit error; other non-retryable statuses are raised as-is.

**Call relations**: The chat and Responses streaming methods call this when the OpenAI SDK reports an API status error. It uses _status_retry_wait to choose the sleep time.

*Call graph*: calls 2 internal fn (_status_retry_wait, __init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_ChatStream.__init__`  (lines 547–552)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates a small state holder for one Chat Completions stream. It remembers partial tool-call ids, whether anything visible has been emitted, final usage, and the provider's finish reason.

**Data flow**: It receives a flag describing cache-write pricing. It initializes empty state for tool calls, usage, finish reason, and the yielded-output marker. Nothing is returned beyond the new object.

**Call relations**: OpenAIClient._complete_chat creates one _ChatStream for each attempt at a chat completion.

*Call graph*: called by 1 (_complete_chat).


##### `_ChatStream.accept`  (lines 554–582)

```
def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]
```

**Purpose**: Turns one streamed Chat Completions chunk into zero or more UFO model events. It is the parser for incremental chat text and incremental tool-call arguments.

**Data flow**: It receives a provider chunk. If the chunk carries usage, it converts and stores it. If it carries text, it emits a TextDelta. If it carries a new tool call, it emits ToolCallStart, then emits ToolCallDelta pieces as arguments arrive. It updates its own state and returns the events it found.

**Call relations**: OpenAIClient._complete_chat calls this for every chunk from the SDK stream, then yields the returned events to the rest of the engine.

*Call graph*: calls 1 internal fn (_chat_usage); 3 external calls (__init__, __init__, __init__).


##### `_ChatStream.finish`  (lines 584–599)

```
def finish(self) -> tuple[Usage, Exception | None]
```

**Purpose**: Finishes interpreting a Chat Completions stream and reports final usage plus any terminal problem. It treats max-token truncation as a recoverable model-response error.

**Data flow**: It reads the stream state accumulated by accept. If the finish reason was length, it prepares a ModelResponseTruncated error. If usage was never reported, it raises either that truncation error or a missing-usage runtime error. Otherwise it returns the Usage object and the optional terminal error.

**Call relations**: OpenAIClient._complete_chat calls this after the provider stream ends to decide whether to yield usage, raise truncation, retry an empty response, or finish normally.

*Call graph*: 1 external calls (__init__).


##### `_ResponsesStream.__init__`  (lines 603–610)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates a state holder for one Responses API stream. It tracks visible output, tool-call ids, completed reasoning items, usage, and any terminal error reported by the provider.

**Data flow**: It receives a cache-write pricing flag. It initializes empty maps and sets for tool calls, an empty reasoning list, no usage, no terminal error, and a false yielded-output marker. The new object stores state for one streaming attempt.

**Call relations**: OpenAIClient._complete_responses creates one _ResponsesStream for each attempt at a Responses API completion.

*Call graph*: called by 1 (_complete_responses).


##### `_ResponsesStream.accept`  (lines 612–651)

```
def accept(self, event: ResponseStreamEvent) -> tuple[ModelEvent, ...]
```

**Purpose**: Turns one OpenAI Responses stream event into UFO model events or stored final state. It understands text deltas, tool calls, reasoning items, refusals, completion, failure, and incomplete responses.

**Data flow**: It receives a Responses event. Text events become TextDelta objects. Function-call starts and argument deltas become ToolCallStart and ToolCallDelta objects. Finished reasoning items are recorded for later. Completed events store usage; refused, failed, errored, or incomplete events store an appropriate terminal error. It returns only the events that should be shown immediately.

**Call relations**: OpenAIClient._complete_responses calls this for every event from the provider stream. It hands reasoning and incomplete-response details to _record_reasoning and _record_incomplete when needed.

*Call graph*: calls 3 internal fn (_record_incomplete, _record_reasoning, _responses_usage); 4 external calls (__init__, __init__, __init__, __init__).


##### `_ResponsesStream._record_reasoning`  (lines 653–662)

```
def _record_reasoning(self, item: ResponseReasoningItem) -> None
```

**Purpose**: Stores a completed OpenAI reasoning item so it can be replayed in a future request. This is important because the Responses API may need encrypted reasoning content to continue a tool-using conversation correctly.

**Data flow**: It receives a provider reasoning item. It requires encrypted_content to be present, gathers the item id, encrypted body, and summary text, and appends a ReasoningItemBlock to the stream's reasoning list. If the encrypted body is missing, it raises an error.

**Call relations**: _ResponsesStream.accept calls this when it sees a completed reasoning output item.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_ResponsesStream._record_incomplete`  (lines 664–675)

```
def _record_incomplete(self, response: Any) -> None
```

**Purpose**: Interprets an incomplete Responses API result and turns its reason into a clear terminal error. It distinguishes truncation, content-filter refusal, and other incomplete failures.

**Data flow**: It receives the incomplete response object. If usage is present, it converts and stores it. It then reads the incomplete reason: max_output_tokens becomes ModelResponseTruncated, content_filter becomes ModelRefusal, and anything else becomes a generic runtime error.

**Call relations**: _ResponsesStream.accept calls this when the provider sends a ResponseIncompleteEvent.

*Call graph*: calls 1 internal fn (_responses_usage); called by 1 (accept); 2 external calls (__init__, __init__).


##### `_ResponsesStream.finish`  (lines 677–685)

```
def finish(self) -> tuple[Usage, Exception | None]
```

**Purpose**: Finishes interpreting a Responses stream and returns final usage plus any terminal error. It preserves the specific error class for truncation or refusal even when usage is missing.

**Data flow**: It reads the usage and terminal error collected during streaming. If usage is missing and a terminal error exists, it raises that terminal error. If both usage and terminal error are missing, it raises a missing-usage runtime error. Otherwise it returns the usage and optional terminal error.

**Call relations**: OpenAIClient._complete_responses calls this after the stream closes to decide whether to yield usage, raise a provider outcome, retry an empty response, or emit saved reasoning blocks.


##### `OpenAIClient.complete`  (lines 700–703)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Chooses which OpenAI-style API path to use for a model request. It hides the Chat Completions versus Responses split from the rest of the engine.

**Data flow**: It receives a ModelRequest. If this client is for Codex, or if the model spec says to use the Responses API, it returns the Responses streaming iterator. Otherwise it returns the Chat Completions streaming iterator.

**Call relations**: This is the public entry point on OpenAIClient. Callers ask complete for model events, and it routes the work to _complete_chat or _complete_responses.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._reasoning_effort`  (lines 705–724)

```
def _reasoning_effort(self, request: ModelRequest) -> OpenAIEffort
```

**Purpose**: Decides what reasoning-effort value, if any, should be sent to OpenAI. It protects the caller from accidentally letting a reasoning model use its default reasoning when the request intended reasoning to be off.

**Data flow**: It reads the request's reasoning setting, tools, and the model spec's reasoning rules. If the model should not receive a reasoning parameter, it returns None. If the request asks for off, it converts that to OpenAI's none value. If the spec says the provider cannot accept a needed off setting alongside tools, it raises an error rather than sending an unsafe request.

**Call relations**: OpenAIClient._chat_kwargs uses this for Chat Completions, and OpenAIClient._complete_responses uses it before building a Responses request.

*Call graph*: called by 2 (_chat_kwargs, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 726–755)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the request arguments for a Chat Completions streaming call. It translates UFO's request fields into the names and shapes that OpenAI's chat endpoint expects.

**Data flow**: It receives a ModelRequest. It converts conversation messages with openai_messages, adds model name, token budget, streaming options, and usage-in-stream options. It adds reasoning effort when appropriate, and converts available tools and tool-choice settings into OpenAI chat format. It returns the keyword-argument dictionary for the SDK call.

**Call relations**: OpenAIClient._complete_chat calls this immediately before starting the provider stream. It depends on _reasoning_effort and openai_messages for the tricky translations.

*Call graph*: calls 2 internal fn (_reasoning_effort, openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 757–831)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a full Chat Completions streaming request and yields UFO model events as they arrive. It also applies the file's retry, interruption, truncation, empty-output, and usage-reporting rules.

**Data flow**: It receives a ModelRequest. For each attempt, it builds chat request arguments, starts the SDK stream, yields ModelStreamStart when the stream begins, converts chunks through _ChatStream.accept, and yields text or tool-call events. If temporary errors happen before output, it retries with backoff. If errors happen after output, it raises ModelStreamInterrupted. When the stream ends, it yields Usage, raises terminal truncation if needed, retries limited empty completions, or returns normally.

**Call relations**: OpenAIClient.complete routes chat-surface requests here. This method uses _chat_kwargs to prepare the request, _ChatStream to parse the stream, and _OpenAIRetry to make retry decisions.

*Call graph*: calls 3 internal fn (_chat_kwargs, __init__, __init__); called by 1 (complete); 3 external calls (__init__, __init__, emit_metric).


##### `OpenAIClient._complete_responses`  (lines 833–900)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a full Responses API streaming request and yields UFO model events as they arrive. It is the Responses counterpart to the chat path, with added support for preserving OpenAI reasoning items.

**Data flow**: It receives a ModelRequest. It resolves reasoning effort, builds the Responses request, starts the SDK stream, yields ModelStreamStart, converts provider events through _ResponsesStream.accept, and yields live text or tool-call events. It retries safe temporary failures, interrupts unsafe mid-stream failures, and after a successful stream yields stored reasoning blocks followed by Usage. Empty visible output is retried a few times before being accepted.

**Call relations**: OpenAIClient.complete routes Responses-surface and Codex requests here. It uses responses_request to build the wire request, _ResponsesStream to parse events, _reasoning_effort for reasoning rules, and _OpenAIRetry for retry behavior.

*Call graph*: calls 4 internal fn (_reasoning_effort, __init__, responses_request, __init__); called by 1 (complete); 3 external calls (__init__, __init__, emit_metric).


### Model registry
The registry centralizes model lookup, pricing, provider selection, and client construction for runtime turns.

### `core/src/ufo/harness/models/registry.py`

`domain_logic` · `startup and model request handling`

The rest of the system should not have to guess what a model ID means. This file makes one reliable place to ask. It gathers the built-in model definitions and any model definitions contributed by extensions, checks that no two models claim the same ID, and refuses to start if important configured model names are unknown. That turns a typo into an early, clear boot error instead of a confusing failure halfway through a conversation.

It also connects model choice to credentials and billing. When code asks for a client for a model, the registry looks up the model’s facts, finds the right key from the current workspace or environment, checks that the key can be sent safely to the provider, and returns both the client and who is paying. This matters because some calls may be paid by the platform, while others use a member’s own connected account.

There are two safety mechanisms for member-owned accounts. If a provider rejects a token before any response is streamed, the registry can rebuild the client once, which lets a refreshed token be used. If a member connected multiple provider accounts, a serving turn can move to the next account when the current one is rate-limited, while keeping billing honest.

#### Function details

##### `_RebuiltOnRejection.complete`  (lines 57–72)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This method runs a model request through an already-built client, but gives it one careful second chance if the provider rejects the credential before any answer has started. It prevents long-running turns from dying just because a token expired at an unlucky moment.

**Data flow**: It receives a model request and starts streaming events from the existing client. If events have already been delivered, any credential failure is passed upward because replaying would duplicate part of the answer. If the credential is rejected before the first event, it asks the registry to build a fresh client for the same model, checks that the payer and funding type did not change, and then streams the retry’s events back out.

**Call relations**: This wrapper is created when ModelRegistry.client_for builds a client for a member-routed model call. If the retry would silently switch who pays, it raises ModelFundingChanged so the wider turn does not continue under different billing rules.

*Call graph*: 1 external calls (__init__).


##### `MemberAccounts.next`  (lines 95–105)

```
async def next(self) -> tuple[ModelSpec, ModelClient]
```

**Purpose**: This method chooses the next connected member account to try when the current one is no longer usable. It is the account failover step for a turn that is supposed to stay on the member’s own paid accounts.

**Data flow**: It reads the stored list of alternate model IDs. If the list is empty, it raises the prepared “all accounts are exhausted” error. Otherwise it removes the first alternate, looks up that model’s specification, builds a client for it, and verifies that the funding type and payer still match the member-owned route the turn was allowed to use. It returns the new model facts and client.

**Call relations**: ServingModel.move calls this when a turn is allowed to move to another member account. During the check it consults the current workspace through ws_current, and if the route changed to a different payer class it raises ModelFundingChanged rather than letting billing drift.

*Call graph*: 2 external calls (__init__, ws_current).


##### `ServingModel.move`  (lines 130–137)

```
async def move(self) -> bool
```

**Purpose**: This method moves an active turn from its current model client to the next eligible member account, if such a move is available. It gives the turn a clean way to continue after a provider-level refusal before any output was streamed.

**Data flow**: It starts with the serving model’s current model ID, model specification, client, and optional account failover state. If there are no member accounts attached, it returns false and changes nothing. If accounts exist, it asks them for the next model and client, replaces its own stored model facts with the new ones, and returns true.

**Call relations**: It relies on MemberAccounts.next to enforce the billing and payer rules. Code running a turn can call this after a rate-limit-style failure to decide whether to replay the round on another connected account or treat the failure as final.


##### `ModelRegistry.resolve`  (lines 151–154)

```
def resolve(self, model: str) -> str
```

**Purpose**: This method turns the special model name “auto” into the concrete default model configured for the deployment. If the model name is already specific, it leaves it unchanged.

**Data flow**: It receives a model ID string. If that string is the project’s automatic-model sentinel, it returns the registry’s configured auto model ID. Otherwise it returns the original string.

**Call relations**: ModelRegistry.key_slot_for and ModelRegistry.model_key_env call this before answering questions about credentials, because a stored “auto” choice must be interpreted as the real model that will run.

*Call graph*: called by 2 (key_slot_for, model_key_env).


##### `ModelRegistry.spec`  (lines 156–162)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This method retrieves the official facts for a model ID. It is the registry’s strict lookup point, so unknown models fail with one clear error.

**Data flow**: It receives a model ID and looks in the registry’s dictionary of model specifications. If it finds one, it returns that ModelSpec. If not, it raises a ValueError that names the missing ID.

**Call relations**: ModelRegistry.client_for uses it before building a provider client, ModelRegistry.provider_for uses it to report the serving provider, and ModelRegistry.model_key_env uses it to decide which key environment variable onboarding should require.

*Call graph*: called by 3 (client_for, model_key_env, provider_for).


##### `ModelRegistry.client_for`  (lines 164–199)

```
async def client_for(self, model: str) -> ResolvedModelClient
```

**Purpose**: This method builds a ready-to-use client for one model and records who is paying for it. It is the place where model choice, credentials, workspace billing, and provider client construction meet.

**Data flow**: It receives a model ID, looks up the model specification, and checks whether the model needs a credential. For keyless models it returns a client marked as platform-funded. For keyed models it asks the current workspace for the right credential, turns missing slots into a clear setup error, rejects non-ASCII keys that cannot safely travel over the provider connection, and builds the provider client. If the call is routed through a member’s own account, it wraps the client so one credential rejection can trigger a rebuild. The output is a ResolvedModelClient containing the client, funding class, and payer.

**Call relations**: Many model calls flow through this method when they need an actual provider client. It calls ModelRegistry.spec for the model facts, uses ws_current to read workspace credentials and routing rules, may create a _RebuiltOnRejection wrapper for member-routed calls, and returns the ResolvedModelClient used by the caller.

*Call graph*: calls 1 internal fn (spec); 4 external calls (__init__, __init__, __init__, ws_current).


##### `ModelRegistry.provider_for`  (lines 201–205)

```
def provider_for(self, model: str) -> str
```

**Purpose**: This method answers which provider, such as OpenAI or Anthropic, serves a model. It is useful for logging, metering, and splitting usage by backend.

**Data flow**: It receives a model ID, retrieves that model’s specification, and returns the provider field from it. If the model ID is unknown, the strict specification lookup raises an error.

**Call relations**: It is a small public doorway over ModelRegistry.spec. Callers that only need the provider do not have to inspect the full model specification themselves.

*Call graph*: calls 1 internal fn (spec).


##### `ModelRegistry.key_slot_for`  (lines 207–218)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This method tells which bring-your-own-key slot would pay for a model, if any. It is intentionally forgiving for old or unknown model names so reports can still label usage instead of crashing.

**Data flow**: It receives a model ID, first resolving “auto” to the configured concrete model. It then does a non-strict lookup in the registry. If the model is missing or has no key slot, it returns null. Otherwise it returns the key slot name.

**Call relations**: It calls ModelRegistry.resolve because billing and exports may see the stored value “auto” even though the actual key belongs to the concrete configured model. Unlike ModelRegistry.spec, this path avoids a loud failure so historical billing data remains readable.

*Call graph*: calls 1 internal fn (resolve).


##### `ModelRegistry.model_key_env`  (lines 220–230)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This method tells onboarding which environment variable should be set before a model’s first use. It only answers for the core providers whose key variable names are known in configuration.

**Data flow**: It receives a model ID and the project configuration. It resolves “auto,” looks up the model specification, reads the provider, and returns the configured Anthropic or OpenAI key environment variable when applicable. For contributed providers that resolve keys later, it returns null.

**Call relations**: It uses ModelRegistry.resolve so the check matches the model that will actually run, then uses ModelRegistry.spec for the provider facts. Startup or onboarding code can call it to give users an early, concrete setup instruction.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 233–266)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This function builds the project’s complete model registry at startup. It combines built-in models with extension-provided models, checks for conflicts and bad configuration, and prepares pricing data.

**Data flow**: It receives configuration and a group of manifests from extensions. It asks the core catalog for built-in model specifications, adds every contributed model specification, and rejects duplicate IDs. It then verifies that the configured automatic, ambient-reply, and background-job models all exist. Finally it builds a price table from the registered models and returns a ModelRegistry containing the specs, pricing, and configured auto model.

**Call relations**: This is the construction step for the whole file’s registry object. It calls core_model_specs to get built-in models, pricing_from to merge their prices into a pricing helper, and ModelRegistry.__init__ to create the registry used later by lookup and client-building methods.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### OpenRouter extension
The OpenRouter extension adds an alternate provider bridge plus image and video generation tools.

### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `model request handling and tool execution`

OpenRouter is a service that routes one API call to many possible AI model providers. This file makes that router look like a normal UFO model client, so the rest of the system can ask for text completions, tool calls, and usage costs in the same shape it expects from built-in providers. It also exposes image and video generation as tools, because those APIs do not behave like chat models and are billed by image or by video second instead of by token.

The file has three main jobs. First, it defines the OpenRouter model list, including prices, context windows, reasoning support, and credentials. Second, it translates UFO chat requests into OpenAI-style chat completion calls, because OpenRouter speaks that wire format. While streaming results back, it watches for text, tool calls, token usage, provider errors, truncated answers, and retryable failures. Third, it defines `generate_image` and `generate_video` tools. These validate model-specific limits, call OpenRouter, save returned files into the workspace, and record costs when the platform key paid for the work.

A useful analogy is a travel agent. UFO asks for a trip in its own language; this file books through OpenRouter, watches for cancellations or delays, records the bill, and hands back tickets in UFO's standard format.

#### Function details

##### `openrouter_slug`  (lines 279–289)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: This turns a model name into the provider/model name OpenRouter expects. It adds an OpenAI or Anthropic prefix when a familiar bare model name is used, and leaves already-qualified names alone.

**Data flow**: It receives a model string. It checks whether the string already contains a slash, starts like an OpenAI model, or starts like a Claude model. It returns the OpenRouter slug that should be sent over the network.

**Call relations**: When a chat request is prepared, `OpenRouterModelClient._create_kwargs` calls this so the API receives the right model id. `_openrouter_messages` also uses it to decide whether special Google-message cleanup is needed.

*Call graph*: called by 2 (_create_kwargs, _openrouter_messages).


##### `_chunk_provider`  (lines 292–297)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: This reads which upstream provider OpenRouter chose for a streamed response chunk. That matters because a provider that returns an empty answer can be excluded on a retry.

**Data flow**: It receives one streamed chat chunk. It looks in OpenRouter's extra metadata for a provider name. It returns that provider as text, or nothing if the chunk does not say.

**Call relations**: `_OpenRouterStream.accept` calls this while reading the live stream. The provider it extracts can later help `OpenRouterModelClient.complete` reroute away from a dead upstream.

*Call graph*: called by 1 (accept).


##### `_usage_of`  (lines 300–321)

```
def _usage_of(usage: CompletionUsage, cache_write_30m_rate: int) -> Usage
```

**Purpose**: This converts OpenRouter/OpenAI token usage into UFO's standard usage record. It separates normal input tokens, output tokens, cached input tokens, and cache-write tokens so billing can be accurate.

**Data flow**: It receives the API's usage object and a flag showing whether cache writes should count. It validates that cached and cache-write tokens do not exceed the prompt total. It returns a `Usage` object in the system's own accounting shape.

**Call relations**: `_OpenRouterStream.accept` calls this when a stream chunk includes usage. The resulting usage is later yielded by `OpenRouterModelClient.complete` as the final cost and token report.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_contains_json_reference`  (lines 336–348)

```
def _contains_json_reference(value: object) -> bool
```

**Purpose**: This checks whether a JSON-like value contains schema reference keys such as `$ref`. It exists because some Google models behind OpenRouter reject tool-result messages containing those references.

**Data flow**: It receives any nested value made of dictionaries, lists, and simple values. It walks through the nested structure until it finds `$ref` or `$dynamicRef`, or reaches the end. It returns true or false.

**Call relations**: `_openrouter_messages` calls this only after it has decoded a tool result as JSON. If references are found, that caller wraps the text differently before sending it to OpenRouter.

*Call graph*: called by 1 (_openrouter_messages).


##### `_openrouter_messages`  (lines 351–388)

```
def _openrouter_messages(model: str, system: str, messages: tuple[Message, ...], accepts_image_input: bool) -> list[dict[str, object]]
```

**Purpose**: This prepares chat messages for OpenRouter. It removes images when the chosen model cannot read them, translates messages into OpenAI format, and applies a Google-specific workaround for troublesome JSON tool results.

**Data flow**: It receives the model name, system prompt, conversation messages, and whether image input is allowed. It may strip images, renders the messages into OpenAI-style dictionaries, then rewrites certain Google tool-result messages if they contain JSON schema references. It returns the list of message dictionaries to send to the API.

**Call relations**: `OpenRouterModelClient._create_kwargs` calls this while building the network request. It relies on `openrouter_slug`, `omit_images`, `openai_messages`, JSON parsing, and `_contains_json_reference` to produce messages OpenRouter will accept.

*Call graph*: calls 2 internal fn (_contains_json_reference, openrouter_slug); called by 1 (_create_kwargs); 4 external calls (dumps, loads, omit_images, openai_messages).


##### `_OpenRouterRetry.status`  (lines 399–435)

```
async def status(self, error: openai.APIStatusError, yielded: bool) -> '_OpenRouterRetry'
```

**Purpose**: This decides whether to retry after OpenRouter returns an HTTP status error, such as rate limiting or a server error. It retries only before visible model output has been shown, so users do not see duplicated partial answers.

**Data flow**: It receives an API status error and whether any output has already been yielded. It checks the status code, retry count, and retry-after header. It may log, emit a metric, sleep, and return an updated retry state; otherwise it raises the original error.

**Call relations**: `OpenRouterModelClient.complete` uses this inside its streaming loop when the API rejects a request with a status error. This method hands back a new retry plan or stops the flow by raising.

*Call graph*: 4 external calls (sleep, replace, emit_metric, log).


##### `_OpenRouterRetry.stream_error`  (lines 437–460)

```
def stream_error(self, error: openai.APIError, yielded: bool) -> '_OpenRouterRetry'
```

**Purpose**: This handles errors injected into the live streaming response. It has one special retry for a known Gemini abort case, and otherwise turns stream failures into a standard interruption signal.

**Data flow**: It receives an OpenAI API error and whether output has already appeared. If the exact known Gemini abort happens before output and has not been retried, it records that and returns updated retry state. Otherwise it raises `ModelStreamInterrupted`, which tells the engine the round should be discarded and retried.

**Call relations**: `OpenRouterModelClient.complete` calls this when the streaming API raises an API error. The method either allows one more loop attempt or hands off failure handling to the wider engine through `ModelStreamInterrupted`.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (replace, emit_metric, log).


##### `_OpenRouterStream.__init__`  (lines 464–471)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: This creates a small state tracker for one streamed model response. It remembers whether anything useful has been yielded, which tool calls are in progress, usage totals, finish reason, provider, and generation id.

**Data flow**: It receives whether cache-write tokens should be priced. It initializes empty fields for stream progress and later accounting. It returns a fresh stream-state object.

**Call relations**: `OpenRouterModelClient.complete` creates one of these for each attempt to stream a response. As chunks arrive, `accept` fills in the state.

*Call graph*: called by 1 (complete).


##### `_OpenRouterStream.accept`  (lines 473–503)

```
def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]
```

**Purpose**: This turns one raw streamed OpenRouter chunk into UFO model events. It extracts text pieces, tool-call starts, tool-call argument fragments, usage, finish reason, provider, and generation id.

**Data flow**: It receives a chat completion chunk. It updates stored metadata, converts usage if present, and builds zero or more events such as text deltas or tool-call deltas. It returns those events and marks the stream as having yielded if any visible event appeared.

**Call relations**: `OpenRouterModelClient.complete` calls this for every chunk in the stream. It uses `_chunk_provider` and `_usage_of`, then hands standard `ModelEvent` objects back to the caller.

*Call graph*: calls 2 internal fn (_chunk_provider, _usage_of); 3 external calls (__init__, __init__, __init__).


##### `OpenRouterModelClient.complete`  (lines 540–595)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the main chat-completion path for OpenRouter. It sends the request, streams text and tool-call events back, reports usage, and deals carefully with retries, dead providers, truncation, and broken streams.

**Data flow**: It receives a `ModelRequest`. It builds OpenRouter request arguments, opens a streaming chat completion, converts chunks into standard events, and yields those events as they arrive. At the end it yields usage; if the stream was cut short, empty, truncated, or failed in a retryable way, it retries or raises the appropriate system error.

**Call relations**: The wider model engine calls this when an agent is using an OpenRouter-backed model. It delegates request building to `_create_kwargs`, chunk tracking to `_OpenRouterStream`, retry choices to `_OpenRouterRetry`, and final accounting to `_finish_usage`.

*Call graph*: calls 4 internal fn (__init__, _create_kwargs, _finish_usage, __init__); 4 external calls (__init__, __init__, __init__, emit_metric).


##### `OpenRouterModelClient._finish_usage`  (lines 597–603)

```
async def _finish_usage(self, state: _OpenRouterStream) -> Usage
```

**Purpose**: This makes sure a completed stream has a usable token usage record. If the stream itself did not include usage, it tries OpenRouter's generation lookup endpoint as a backup.

**Data flow**: It receives the stream state after streaming ends. It first uses usage already captured from chunks; if missing, it looks up the generation by id and finish reason. It returns a `Usage` object or raises if no usage can be found.

**Call relations**: `OpenRouterModelClient.complete` calls this after a stream finishes normally. It may call `_generation_usage` to recover accounting information that was not included in the stream.

*Call graph*: calls 1 internal fn (_generation_usage); called by 1 (complete).


##### `OpenRouterModelClient._generation_usage`  (lines 605–662)

```
async def _generation_usage(self, generation_id: str, finish_reason: str) -> Usage | None
```

**Purpose**: This asks OpenRouter's generation ledger for token usage after a stream ends. It exists because usage may be missing from the live stream, and the ledger may take a few seconds to index the generation.

**Data flow**: It receives a generation id and expected finish reason. It repeatedly GETs the generation endpoint, retrying short-lived 404s and transport faults. If the record matches and was not cancelled, it converts native token counts into `Usage`; if the lookup never becomes available, it raises a stream interruption.

**Call relations**: `_finish_usage` calls this as the fallback accounting path. It reports retry metrics while waiting and returns usage to `complete`, which then yields it to the engine.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_finish_usage); 4 external calls (__init__, sleep, AsyncClient, emit_metric).


##### `OpenRouterModelClient._create_kwargs`  (lines 664–712)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: This builds the exact keyword arguments sent to the OpenAI-style chat completion API. It adds OpenRouter-specific settings such as session id, reasoning controls, and ignored providers.

**Data flow**: It receives a model request and any providers to avoid. It checks that the request has a session id, prepares reasoning and provider routing options, converts messages, adds tools and forced tool choice when needed, and returns a dictionary ready for the SDK call.

**Call relations**: `OpenRouterModelClient.complete` calls this before every API attempt. It uses `openrouter_slug` and `_openrouter_messages` to translate UFO's request into OpenRouter's expected wire format.

*Call graph*: calls 2 internal fn (_openrouter_messages, openrouter_slug); called by 1 (complete).


##### `_model_client`  (lines 715–720)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: This creates an `OpenRouterModelClient` for a model spec and API key. It is the factory the model registry can call when it needs a live client.

**Data flow**: It receives a `ModelSpec` and a key. It creates an OpenAI-compatible SDK client pointed at OpenRouter's base URL, wraps it with OpenRouter-specific behavior, and returns the model client.

**Call relations**: `_openrouter` stores this factory in each `ModelSpec`. Later, when the registry activates one of those specs, this function provides the concrete client.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 723–743)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW, reasoning: ReasoningSupport=_REASONS, accepts_image_input: bool=True) -> ModelSpec
```

**Purpose**: This is a helper for declaring one OpenRouter model in the manifest. It keeps repeated provider, credential, API surface, and capability fields consistent.

**Data flow**: It receives a model id, price, knowledge cutoff, and optional capability settings. It packages them into a `ModelSpec` that the rest of UFO can register and select. It returns that spec.

**Call relations**: The file uses this repeatedly to build `OPENROUTER_MODEL_SPECS`. Those specs are then returned by `manifest` so the extension can advertise its available chat models.

*Call graph*: 1 external calls (__init__).


##### `GenerateImageInput._within_model_limits`  (lines 855–879)

```
def _within_model_limits(self) -> 'GenerateImageInput'
```

**Purpose**: This validates that an image-generation request fits the chosen model's real limits. It catches bad combinations before sending them to OpenRouter, so the model can correct the request instead of receiving a remote API error.

**Data flow**: It reads the already-parsed image arguments: model, number of images, aspect ratio, and resolution. It compares them with the allowlisted limits for that model, fills in the default resolution when needed, and returns the updated input object or raises a clear validation error.

**Call relations**: Pydantic calls this automatically when building `GenerateImageInput`. `OpenRouterImages.generate` can then trust that the request is within the known model bounds.


##### `_reported_cost_micro_usd`  (lines 887–899)

```
def _reported_cost_micro_usd(usage: object) -> int | None
```

**Purpose**: This extracts a positive reported cost from OpenRouter usage data and converts it to micro-dollars. A micro-dollar is one millionth of a US dollar, used here for precise billing.

**Data flow**: It receives a usage-like object. It looks for `cost`, then for an upstream inference cost used by bring-your-own-key cases. If it finds a positive number, it converts dollars to micro-USD and returns it; otherwise it returns nothing.

**Call relations**: Image and video charging both use this. `OpenRouterImages._charge` uses it directly, and `OpenRouterVideos._job` stores its result on the video job for later billing.

*Call graph*: called by 2 (_charge, _job).


##### `OpenRouterImages.generate`  (lines 932–969)

```
async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: This runs one image-generation tool call from start to finish. It calls OpenRouter, saves the returned images, records cost when appropriate, and returns both file paths and image data to the agent.

**Data flow**: It receives a tool context and validated image arguments. It gets the OpenRouter key, posts the request, turns provider errors into tool errors, decodes returned images, writes them into the workspace, calculates cost, optionally meters that cost, and returns a `ToolResult` containing JSON metadata plus image content.

**Call relations**: The `_generate_image` tool handler calls this. Inside, it relies on `_refusal`, `_images`, `_save`, and `_charge` to break the work into understandable steps.

*Call graph*: calls 5 internal fn (meter_images, _charge, _images, _refusal, _save); 6 external calls (__init__, __init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterImages._refusal`  (lines 971–986)

```
def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str
```

**Purpose**: This creates a clear error message when OpenRouter refuses or fails an image request. It keeps the provider's explanation but trims it so an oversized response does not flood the tool result.

**Data flow**: It receives the original image arguments and an HTTP response. It tries to read a JSON error message, otherwise falls back to the response text. It returns a short string saying the model produced no image and why.

**Call relations**: `OpenRouterImages.generate` calls this when the image POST returns an error status. The returned text becomes an error `ToolResult` that the agent can react to.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterImages._images`  (lines 988–1018)

```
def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]
```

**Purpose**: This extracts usable images from OpenRouter's response. It decodes base64 image data, assigns a media type, and rejects images that are too large to save safely.

**Data flow**: It receives the image arguments and the parsed response body. It scans the `data` list for base64 image strings, decodes each into bytes, checks its size, and returns `GeneratedImage` objects. If no valid image appears, or an image is too large, it raises an image error.

**Call relations**: `OpenRouterImages.generate` calls this after a successful HTTP response. Its output is then passed to `_save` for disk writing and included as image content in the tool result.

*Call graph*: called by 1 (generate); 3 external calls (__init__, __init__, b64decode).


##### `OpenRouterImages._save`  (lines 1020–1027)

```
async def _save(self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage) -> str
```

**Purpose**: This writes one generated image into the workspace. It chooses a file extension based on the image media type.

**Data flow**: It receives the tool context, original arguments, an image index, and the decoded image. It builds a path under `generated-images/`, writes the bytes through the workspace sandbox, and returns the saved path.

**Call relations**: `OpenRouterImages.generate` calls this once for each decoded image. The returned paths are reported back to the agent so files can later be shared.

*Call graph*: called by 1 (generate).


##### `OpenRouterImages._charge`  (lines 1029–1036)

```
def _charge(self, body: object, args: GenerateImageInput, images: int) -> int
```

**Purpose**: This calculates what an image generation should cost in micro-USD. It prefers the actual cost reported by OpenRouter, and falls back to the model's list price per image.

**Data flow**: It receives the response body, image arguments, and number of images. It looks for usage cost with `_reported_cost_micro_usd`; if none is available, it multiplies the model's fallback image price by the image count. It returns the final charge.

**Call relations**: `OpenRouterImages.generate` calls this after images are successfully saved. The result is included in the tool result and may be sent to `ctx.meter_images`.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 1 (generate).


##### `_generate_image`  (lines 1039–1044)

```
async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: This is the registered tool handler for `generate_image`. It connects the generic tool system to the OpenRouter image generator.

**Data flow**: It receives a tool context and validated image arguments. It checks that extension context exists, builds an `OpenRouterImages` helper with credentials and optional test transport, and returns that helper's generated `ToolResult`.

**Call relations**: The `GENERATE_IMAGE_TOOL` definition points to this function. When an agent calls the tool, the tool runtime invokes this handler, which delegates the real work to `OpenRouterImages.generate`.

*Call graph*: 1 external calls (__init__).


##### `GenerateVideoInput._within_model_limits`  (lines 1100–1124)

```
def _within_model_limits(self) -> 'GenerateVideoInput'
```

**Purpose**: This validates that a video-generation request fits the selected video model. It also chooses the default resolution so billing and generation use the same explicit tier.

**Data flow**: It reads the parsed video arguments: model, duration, aspect ratio, and resolution. It checks the duration and aspect ratio against the model's limits, fills in the model's default resolution if missing, and rejects unsupported resolutions. It returns the updated input object or raises a clear validation error.

**Call relations**: Pydantic runs this while creating `GenerateVideoInput`. `OpenRouterVideos.generate` receives only requests that match known provider limits.


##### `OpenRouterVideos.generate`  (lines 1166–1207)

```
async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: This runs one video-generation tool call from request to saved MP4. Because video generation is asynchronous, it starts a job, waits for completion, downloads the result, saves it, and records cost.

**Data flow**: It receives a tool context and validated video arguments. It gets credentials, posts the video request, returns a tool error if OpenRouter rejects it, polls the job until it settles, returns a tool error if generation failed, downloads the MP4, saves it in the workspace, computes cost, optionally meters it, and returns metadata with the saved file path.

**Call relations**: The `_generate_video` tool handler calls this. It coordinates `_refusal`, `_job`, `_settled`, `_failure`, `_download`, `_save`, and `_charge` to cover the whole lifecycle of a video job.

*Call graph*: calls 8 internal fn (meter_videos, _charge, _download, _failure, _job, _refusal, _save, _settled); 5 external calls (__init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterVideos._refusal`  (lines 1209–1224)

```
def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str
```

**Purpose**: This creates a readable error message when OpenRouter refuses to start a video job. It preserves the provider's reason while keeping the text bounded.

**Data flow**: It receives the video arguments and HTTP response. It tries to read a JSON error message, otherwise uses the response text, trims it, and returns a sentence explaining that no video was generated.

**Call relations**: `OpenRouterVideos.generate` calls this when the initial video POST returns an error status. The text becomes the content of an error tool result.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterVideos._job`  (lines 1226–1241)

```
def _job(self, body: object) -> VideoJob
```

**Purpose**: This turns an OpenRouter video job response into the file's internal `VideoJob` record. It verifies that there is a job id and a status, because without those the job cannot be polled or downloaded.

**Data flow**: It receives a parsed response body. It reads the job id, status, optional error message, and optional reported cost. It returns a `VideoJob`, or raises a video error if the response does not describe a usable job.

**Call relations**: `OpenRouterVideos.generate` calls this after the initial accepted response, and `_settled` calls it after each poll. It uses `_reported_cost_micro_usd` so completed jobs can carry their actual cost forward.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 2 (_settled, generate); 2 external calls (__init__, __init__).


##### `OpenRouterVideos._settled`  (lines 1243–1264)

```
async def _settled(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> VideoJob
```

**Purpose**: This waits for an asynchronous video job to stop being pending or in progress. It prevents the tool call from waiting forever by enforcing a timeout.

**Data flow**: It receives video arguments, an HTTP client, and the current job. While the job is still pending or running, it sleeps, polls OpenRouter, validates the poll response, and updates the job. It returns the final job state or raises an error if polling fails or takes too long.

**Call relations**: `OpenRouterVideos.generate` calls this after creating a job. It repeatedly calls `_job` to interpret each poll response, then hands the settled job back so generation can either report failure or download the file.

*Call graph*: calls 1 internal fn (_job); called by 1 (generate); 4 external calls (__init__, sleep, get, monotonic).


##### `OpenRouterVideos._failure`  (lines 1266–1270)

```
def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str
```

**Purpose**: This turns a settled but unsuccessful video job into a message the agent can understand. It uses the provider's own error when available.

**Data flow**: It receives the original video arguments and the final job. It chooses the job's error message or a generic status explanation, trims it, and returns a short failure string.

**Call relations**: `OpenRouterVideos.generate` calls this when polling ends with a status other than completed. The returned text becomes an error `ToolResult`.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._download`  (lines 1272–1291)

```
async def _download(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> bytes
```

**Purpose**: This downloads the finished MP4 for a completed video job and checks that it is safe to save. It rejects empty or oversized content.

**Data flow**: It receives video arguments, an HTTP client, and a completed job. It GETs the job's content endpoint, checks for HTTP errors, reads the bytes, ensures they are non-empty and under the size cap, and returns the raw video bytes.

**Call relations**: `OpenRouterVideos.generate` calls this only after `_settled` reports completion. The returned bytes are then passed to `_save`.

*Call graph*: called by 1 (generate); 2 external calls (__init__, get).


##### `OpenRouterVideos._save`  (lines 1293–1297)

```
async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str
```

**Purpose**: This writes a generated video file into the workspace. It always saves the result as an MP4 under the generated-videos directory.

**Data flow**: It receives the tool context, video arguments, and raw video bytes. It builds the workspace path, writes the bytes through the sandbox, and returns the saved path.

**Call relations**: `OpenRouterVideos.generate` calls this after downloading the completed video. The returned path is included in the tool result so the agent can refer to or share the file.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._charge`  (lines 1299–1307)

```
def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int
```

**Purpose**: This calculates the cost of a video generation in micro-USD. It uses OpenRouter's reported job cost when available, otherwise multiplies the model's per-second list rate by the requested duration.

**Data flow**: It receives the video arguments and final job. If the job already carries a reported cost, it returns that. Otherwise it looks up the model and resolution rate, multiplies by duration, and returns the fallback charge.

**Call relations**: `OpenRouterVideos.generate` calls this after saving the video. The charge is reported in the tool output and may be recorded with `ctx.meter_videos`.

*Call graph*: called by 1 (generate).


##### `_generate_video`  (lines 1310–1315)

```
async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: This is the registered tool handler for `generate_video`. It connects the tool runtime to the OpenRouter video workflow.

**Data flow**: It receives a tool context and validated video arguments. It checks that extension context is present, creates an `OpenRouterVideos` helper with credentials and optional transport, and returns the helper's result.

**Call relations**: The `GENERATE_VIDEO_TOOL` definition points to this function. When an agent calls the video tool, this handler delegates the actual network, polling, saving, and billing work to `OpenRouterVideos.generate`.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1328–1344)

```
def manifest() -> Manifest
```

**Purpose**: This describes the extension to the UFO plugin system. It advertises the OpenRouter models, image and video tools, and the credential slot used for the API key.

**Data flow**: It takes no input. It builds a `Manifest` containing the extension name and version, registered model specs, registered tools, and credential description. It returns that manifest to the host system.

**Call relations**: The extension loader calls this to discover what the file provides. The returned manifest is how OpenRouter chat models and generation tools become available to the rest of the application.

*Call graph*: 2 external calls (__init__, __init__).
