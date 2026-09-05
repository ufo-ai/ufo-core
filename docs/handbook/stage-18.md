# Model catalog, provider adapters, and billing accounting  `stage-18` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for any part of UFO that calls an AI model or charges for that work. It acts like a travel desk: it knows which “vehicles” are available, how to book each one, and how much the trip costs.

The model interface defines the common shape of requests and streaming replies, including tool calls, images, and reasoning text, so the main turn loop can talk to every provider the same way. The model spec, catalog, Bedrock extension, and OpenRouter extension list available models, their limits, prices, API style, and client setup. The registry is the lookup desk that turns a chosen model name into the right facts, credentials, and caller.

The Anthropic and OpenAI adapters translate UFO’s standard requests into each provider’s API and translate streamed answers back again, including retry decisions. Grants keep connected provider accounts usable by refreshing expired tokens.

The pricing, accounting, balance, and Metronome files form the money side. They price usage, check prepaid credit and spend limits, record usage, and export billing data to Metronome or Stripe.

## Files in this stage

### Model catalog and registry
Defines the model inventory from built-in and extension providers, then centralizes model facts and lookup behavior.

### `core/src/ufo/harness/models/catalog.py`

`config` · `startup and model/pricing lookup`

This file is like a menu and price list for the AI models shipped with the core system. Without it, the rest of the program would not know which built-in model IDs are valid, how much their input and output should cost, which environment variable holds the needed API key, or which provider-specific client should be used to contact the model.

The file starts by naming the key slots and environment variables for Anthropic and OpenAI credentials. It also defines shared limits, such as how many tokens fit in a model’s context window. A token is a small chunk of text used by AI model APIs for size and billing.

The helper functions build `ModelSpec` objects. A `ModelSpec` is the system’s compact fact sheet for one model: provider name, price, knowledge cutoff, context window, reasoning support, API surface, and client factory. The client factory is important because Anthropic and OpenAI need different setup steps, and OpenAI may use a Codex-style client when the credential points to a ChatGPT account.

The main function, `core_model_specs`, returns all built-in model specs. At the bottom, the module immediately builds the default catalog, extracts the price table, and creates a pricing digest. That digest gives the system a stable fingerprint of the pricing data, useful for ledgers or checks that need to know exactly which prices were in force.

#### Function details

##### `_anthropic_client`  (lines 32–35)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function creates the Anthropic model client for a specific model and credential. It hides the provider-specific setup so the rest of the system can simply ask the model spec to produce a usable client.

**Data flow**: It receives a model spec and an API key. It turns the key into an Anthropic SDK client, checks whether the key is an OAuth-style credential, then wraps both pieces together with the model spec in an `AnthropicClient`. The result is a ready-to-use client object for that model.

**Call relations**: This function is stored inside Anthropic `ModelSpec` entries by `_anthropic`. Later, when some other part of the system needs to call an Anthropic model, the spec can call this factory to build the actual client.

*Call graph*: 3 external calls (__init__, anthropic_sdk_client, is_oauth_credential).


##### `_openai_client`  (lines 38–42)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function creates the OpenAI model client for a specific model and credential. It also detects when the credential should use the Codex-style OpenAI path instead of the normal OpenAI SDK path.

**Data flow**: It receives a model spec and an API key. First it checks whether the key contains or maps to a ChatGPT account ID. If not, it builds a normal OpenAI SDK client and wraps it in an `OpenAIClient`. If an account ID is found, it builds a Codex SDK client for that account and marks the wrapper as Codex-backed. The output is the correct client object for the given key.

**Call relations**: This function is attached to OpenAI `ModelSpec` entries by `_openai`. When the system later wants to run an OpenAI model, the spec uses this function so callers do not need to know which OpenAI client variant is required.

*Call graph*: 4 external calls (__init__, chatgpt_account_id, codex_sdk_client, openai_sdk_client).


##### `_anthropic`  (lines 45–65)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS_WITH_TOOLS) -> ModelSpec
```

**Purpose**: This helper builds one complete `ModelSpec` for an Anthropic model. It keeps repeated Anthropic details in one place, so each model row only has to state what is different, such as price, model ID, cutoff date, or context size.

**Data flow**: It receives the model ID, pricing, knowledge cutoff, key environment variable name, and optional context or reasoning settings. It combines those with Anthropic defaults: the Anthropic provider name, Anthropic key slot, chat API surface, and the Anthropic client factory. It returns a finished `ModelSpec` for one Anthropic model.

**Call relations**: `core_model_specs` calls this helper once for each built-in Anthropic model. The helper hands back standardized specs, which are then collected into the core model catalog.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 68–82)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: This helper builds one complete `ModelSpec` for an OpenAI model. It avoids repeating the same OpenAI provider details for every model in the catalog.

**Data flow**: It receives the model ID, pricing, knowledge cutoff, key environment variable name, and optionally which OpenAI API surface to use. It fills in OpenAI defaults: the provider name, context window, reasoning support, key slot, and OpenAI client factory. It returns a finished `ModelSpec` for one OpenAI model.

**Call relations**: `core_model_specs` calls this helper for every built-in OpenAI model. Some calls choose the Responses API surface, which is the route needed for certain models and tool/reasoning combinations.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 85–199)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: This function builds the full list of core-shipped model specifications. It is the central source of truth for built-in model IDs, prices, knowledge cutoff dates, context limits, reasoning support, and which API surface each model should use.

**Data flow**: It receives the environment variable names that should be used for Anthropic and OpenAI keys. It creates many `ModelPrice` values, then passes them into `_anthropic` or `_openai` to make `ModelSpec` objects. It returns all of those specs as a tuple, ready to be indexed by model ID or used to build pricing tables.

**Call relations**: At module load time, this function is called to create `CORE_MODEL_SPECS`. The resulting specs feed `CORE_PRICES`, `CORE_PRICING`, and `PRICE_DIGEST`, so the catalog and the billing logic are based on the same model facts.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `extension discovery and model setup`

This extension is like a catalog card plus a set of connection instructions for Amazon Bedrock Mantle. UFO needs to know which model names exist, what they cost, how much text they can read at once, what kind of reasoning features they support, and which API style to use when talking to them. Without this file, the rest of the system would not know that these Bedrock models are available or how to connect to them safely.

The file supports two families of models. Anthropic model IDs are connected through Anthropic’s Bedrock Mantle client. OpenAI-style model IDs are connected through UFO’s OpenAI-compatible client, using a Bedrock Mantle web address. The file itself does not rewrite requests or translate messages; it only chooses the correct endpoint and client for each model.

Credentials come from a named secret slot, backed by the environment variable AWS_BEARER_TOKEN_BEDROCK. The AWS region is also required, because Bedrock endpoints are regional. The file looks for AWS_REGION first, then AWS_DEFAULT_REGION, and raises a clear error if neither is set.

At the bottom, `manifest` exposes all of this as a `Manifest`, which is the package of information UFO uses when loading the extension.

#### Function details

##### `bedrock_region`  (lines 46–52)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that should be used for Bedrock Mantle requests. This matters because Bedrock service addresses include the region, so the system cannot connect correctly without it.

**Data flow**: It reads the process environment, first checking AWS_REGION and then AWS_DEFAULT_REGION. If it finds a value, it returns that region string. If both are missing, it stops with an error explaining which environment variables must be set.

**Call relations**: When either kind of client is being created, `_anthropic_client` or `_openai_client` asks this function for the region. The returned region is then built into the Bedrock connection settings.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 55–67)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Builds a UFO Anthropic client for a Bedrock-hosted Anthropic model. Someone uses it indirectly when a model spec says, “this model should be reached through Anthropic’s Bedrock Mantle API.”

**Data flow**: It receives a `ModelSpec`, which describes the model, and an API key. It asks `bedrock_region` for the AWS region, creates an Anthropic Bedrock Mantle async client with that key, region, timeout, and no automatic retries, then wraps it in UFO’s `AnthropicClient` together with the model spec. The result is a ready-to-use client object.

**Call relations**: This function is stored inside Anthropic model specs created by `_anthropic`. Later, when UFO needs to call one of those models, the spec can use this function to build the actual connection. During that build, it hands off region lookup to `bedrock_region` and delegates the provider-specific connection to Anthropic’s Bedrock Mantle client.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 70–77)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Builds a UFO OpenAI-compatible client for a Bedrock-hosted OpenAI-style model. It chooses the correct Bedrock Mantle base address depending on which OpenAI API surface the model uses.

**Data flow**: It receives a `ModelSpec` and an API key. It reads the AWS region through `bedrock_region`, builds a Bedrock Mantle URL, and chooses between the `/openai/v1` path for the Responses API or `/v1` for the Chat Completions-style API. It then creates an OpenAI SDK client for that URL and wraps it in UFO’s `OpenAIClient`. The output is a ready-to-use client object.

**Call relations**: This function is stored inside OpenAI-style model specs created by `_openai`. When UFO later needs to send a request to one of those models, this function supplies the client. It relies on `bedrock_region` for the regional endpoint and on `openai_sdk_client` to make the lower-level OpenAI-compatible connection.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 80–99)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS) -> ModelSpec
```

**Purpose**: Creates a `ModelSpec` for an Anthropic model available through Bedrock. A model spec is the system’s record of a model’s name, price, limits, reasoning behavior, credential source, and client-building function.

**Data flow**: It takes a model ID, pricing information, a knowledge cutoff date, and optional context-window and reasoning settings. It fills in Bedrock-specific defaults, including the provider name, Anthropic client factory, chat API surface, credential slot, and API-key environment variable. It returns a complete `ModelSpec` ready to be included in the provider’s model list.

**Call relations**: The file uses this helper while building `BEDROCK_MODEL_SPECS`, so each Anthropic entry is created in a consistent way. The client field it places into the spec points to `_anthropic_client`, which is what UFO will call later when it needs a live connection.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 102–116)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Creates a `ModelSpec` for an OpenAI-compatible model available through Bedrock. It keeps the repeated Bedrock setup in one place so each OpenAI-style model entry only needs to state what is unique about that model.

**Data flow**: It takes a model ID, pricing information, knowledge cutoff, context-window size, and API style. It combines those with Bedrock defaults, including the provider name, OpenAI client factory, reasoning support, credential slot, and API-key environment variable. It returns a complete `ModelSpec` for the model catalog.

**Call relations**: The file uses this helper to build the OpenAI-compatible entries in `BEDROCK_MODEL_SPECS`. The spec it creates points to `_openai_client`, so later model calls can be routed to the right Bedrock Mantle OpenAI-compatible endpoint.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 204–215)

```
def manifest() -> Manifest
```

**Purpose**: Packages this extension’s public information into a `Manifest`, which is what UFO reads to discover the provider. It tells UFO the extension name, version, required credential, and available models.

**Data flow**: It creates a credential description for the Bedrock API key, combines it with the extension name, version, and the full `BEDROCK_MODEL_SPECS` list, and returns a `Manifest` object. It does not make network calls; it only reports what this extension offers and what secret it needs.

**Call relations**: The extension loader calls this function when it wants to learn about the Bedrock provider. The returned manifest hands UFO the credential slot created with `CredentialSlot` and the model catalog created earlier with `_anthropic` and `_openai`.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `request handling and tool execution`

OpenRouter is a service that sits in front of many AI model providers. This file is the adapter that makes that router look like a regular UFO model client. Without it, UFO could not use the OpenRouter model list, could not retry around broken upstream providers, and could not offer OpenRouter image or video generation as workspace tools.

For text models, the file translates UFO's internal request format into OpenAI-style chat-completion requests, because that is the network format OpenRouter speaks. It also maps friendly model names to OpenRouter slugs, attaches session IDs so prompt caching keeps working, sends reasoning settings, streams text and tool-call events back to UFO, and records token usage for billing. A lot of the code exists to make routed providers safer: if one upstream returns nothing, refuses a request that others may accept, stalls mid-stream, or delays usage reporting, the client retries or asks UFO to rerun the round instead of silently losing work.

For images and videos, the file defines two tools: generate_image and generate_video. These validate user-facing options, call OpenRouter's dedicated generation APIs, save finished files into the workspace, and meter the cost unless the workspace used its own OpenRouter key. The manifest at the bottom advertises all of this to the host system.

#### Function details

##### `openrouter_slug`  (lines 319–329)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Turns a UFO model name into the provider/model name OpenRouter expects. It adds OpenAI or Anthropic prefixes for common bare model names, while leaving already-prefixed names alone.

**Data flow**: It receives a model string. If the string already contains a slash, it returns it unchanged; if it looks like an OpenAI or Claude model, it adds the matching provider prefix; otherwise it returns the original string.

**Call relations**: The model client calls this whenever it prepares or tracks an OpenRouter request. Message conversion also uses it to detect Google models, which need a special workaround for some tool-result JSON.

*Call graph*: called by 4 (_create_kwargs, _stream, complete, _openrouter_messages).


##### `_chunk_provider`  (lines 332–337)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Extracts the name of the actual upstream provider that OpenRouter used for one streamed response chunk. This matters because a routed model may be served by several companies behind the scenes.

**Data flow**: It receives a streamed chat chunk, looks in the chunk's extra metadata for a provider field, and returns that provider name as text if present. If the chunk does not name a provider, it returns nothing.

**Call relations**: _OpenRouterStream.accept calls this while reading streamed chunks. The saved provider name can later be used by OpenRouterModelClient.complete to avoid a provider that stalled or returned an empty result.

*Call graph*: called by 1 (accept).


##### `_refused_upstream`  (lines 340–359)

```
def _refused_upstream(error: openai.APIStatusError) -> str | None
```

**Purpose**: Decides whether a 400 error came from one specific upstream provider that can be avoided, rather than from OpenRouter or from an impossible request. A 400 error means the request was rejected as bad in some way.

**Data flow**: It receives an OpenAI API status error. It checks whether the error body includes OpenRouter metadata naming an upstream provider, then filters out messages that suggest every provider would fail, such as context length, authentication, quota, or API key problems. It returns the upstream name only when retrying elsewhere might help.

**Call relations**: Retry logic calls this when OpenRouterModelClient.complete catches a status error, and _OpenRouterRetry.status uses it to decide whether to reroute immediately around a refusing provider.

*Call graph*: called by 2 (complete, status); 1 external calls (dumps).


##### `_usage_of`  (lines 362–383)

```
def _usage_of(usage: CompletionUsage, cache_write_30m_rate: int) -> Usage
```

**Purpose**: Converts OpenAI-style token usage into UFO's own Usage object for billing and accounting. It also separates normal input tokens from cached tokens and cache-write tokens.

**Data flow**: It receives usage data from the OpenAI SDK and a flag saying whether cache writes have a price. It reads total prompt, completion, cached, and cache-write token counts, checks that the numbers make sense, and returns a UFO Usage record.

**Call relations**: _OpenRouterStream.accept calls this when a streamed chunk includes usage information. The resulting Usage is later yielded by OpenRouterModelClient.complete as the final accounting for the model call.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_contains_json_reference`  (lines 398–410)

```
def _contains_json_reference(value: object) -> bool
```

**Purpose**: Checks whether a JSON-like value contains schema reference keys such as $ref or $dynamicRef. This is used because some Google-routed OpenRouter calls reject those references in tool-result messages.

**Data flow**: It receives any Python object. It walks through dictionaries and lists, looking for the special reference keys, and returns true if it finds one or false if it does not.

**Call relations**: _openrouter_messages uses this after parsing tool-result text. If a Google model would see a referenced JSON structure, the message is wrapped as plain text to avoid OpenRouter rejection.

*Call graph*: called by 1 (_openrouter_messages).


##### `_openrouter_messages`  (lines 413–450)

```
def _openrouter_messages(model: str, system: str, messages: tuple[Message, ...], accepts_image_input: bool) -> list[dict[str, object]]
```

**Purpose**: Builds the chat messages that will be sent to OpenRouter, including a special compatibility fix for Google models. It also removes image content when the target model cannot accept images.

**Data flow**: It receives the model name, system prompt, UFO messages, and whether image input is allowed. It optionally strips images, converts the conversation into OpenAI-style messages, and for Google models wraps certain tool-result JSON as plain text when it contains schema references.

**Call relations**: OpenRouterModelClient._create_kwargs calls this while building the request body. It relies on openrouter_slug, omit_images, openai_messages, JSON parsing, and _contains_json_reference before handing the finished messages to the OpenAI SDK.

*Call graph*: calls 2 internal fn (_contains_json_reference, openrouter_slug); called by 1 (_create_kwargs); 4 external calls (dumps, loads, omit_images, openai_messages).


##### `_OpenRouterRetry.status`  (lines 462–520)

```
async def status(self, error: openai.APIStatusError, yielded: bool, dead: set[str]) -> '_OpenRouterRetry'
```

**Purpose**: Chooses what to do after OpenRouter returns an HTTP status error. It can reroute around a refusing upstream, wait and retry rate-limit or server errors, or re-raise the error when retrying is unsafe.

**Data flow**: It receives the error, a flag saying whether any visible output was already streamed, and a set of providers to avoid. It may add a provider to that set, log and count a retry, sleep for a backoff delay, and return an updated retry state. If the error should not be retried, it raises it.

**Call relations**: OpenRouterModelClient.complete calls this inside its main retry loop. It uses _refused_upstream to distinguish reroutable upstream refusals from request-level failures.

*Call graph*: calls 1 internal fn (_refused_upstream); 4 external calls (sleep, replace, emit_metric, log).


##### `_OpenRouterRetry.stream_error`  (lines 522–545)

```
def stream_error(self, error: openai.APIError, yielded: bool) -> '_OpenRouterRetry'
```

**Purpose**: Handles an error injected into the live event stream. It has one narrow retry case for a known Gemini abort; otherwise it treats the stream as interrupted.

**Data flow**: It receives an OpenAI API error and whether output had already appeared. For the known Gemini abort before output, it returns a retry state marked as already retried. For other API stream errors, it raises ModelStreamInterrupted so the engine can discard partial output and rerun the round.

**Call relations**: OpenRouterModelClient.complete calls this after catching OpenAI stream errors. It logs and emits metrics for the special retry case, or hands interruption handling back to UFO's round engine.

*Call graph*: calls 1 internal fn (__init__); 3 external calls (replace, emit_metric, log).


##### `_OpenRouterStream.__init__`  (lines 549–556)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates a small state tracker for one streamed OpenRouter response. It remembers whether anything useful has been yielded, the provider used, tool-call IDs, usage, finish reason, and generation ID.

**Data flow**: It receives a flag saying whether 30-minute cache-write tokens should be counted. It initializes empty fields that will be filled as streamed chunks arrive.

**Call relations**: OpenRouterModelClient.complete creates one of these for each attempted model call. OpenRouterModelClient._stream then feeds chunks into its accept method.

*Call graph*: called by 1 (complete).


##### `_OpenRouterStream.accept`  (lines 558–588)

```
def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]
```

**Purpose**: Turns one raw streamed chat chunk into UFO model events, such as text pieces and tool-call pieces. It also captures usage and provider information as it goes.

**Data flow**: It receives a ChatCompletionChunk from the OpenAI SDK. It updates stored generation ID, provider, usage, and finish reason, then emits TextDelta, ToolCallStart, and ToolCallDelta events for any visible content in the chunk.

**Call relations**: OpenRouterModelClient._stream calls this for every chunk from OpenRouter. The events it returns are yielded upward to OpenRouterModelClient.complete and then to the rest of UFO.

*Call graph*: calls 2 internal fn (_chunk_provider, _usage_of); called by 1 (_stream); 3 external calls (__init__, __init__, __init__).


##### `OpenRouterModelClient.complete`  (lines 650–708)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one full streamed text-model request through OpenRouter and yields UFO model events. It is the main bridge between UFO's model interface and OpenRouter's chat-completions API.

**Data flow**: It receives a ModelRequest. It maps the model to an OpenRouter slug, streams events, retries selected failures, records or looks up usage, avoids bad upstream providers when possible, and finally yields a Usage record or raises a clear interruption/truncation error.

**Call relations**: This is called by UFO wherever a model client is asked to complete a turn. It delegates the actual network call to _stream, uses _finish_usage for accounting, uses _nowhere_left and _stalled_out to guide rerouting, and relies on _OpenRouterRetry for retry decisions.

*Call graph*: calls 8 internal fn (__init__, _finish_usage, _nowhere_left, _stalled_out, _stream, __init__, _refused_upstream, openrouter_slug); 4 external calls (__init__, __init__, __init__, emit_metric).


##### `OpenRouterModelClient._stream`  (lines 710–724)

```
async def _stream(self, request: ModelRequest, state: _OpenRouterStream, ignore_providers: set[str]) -> AsyncIterator[ModelEvent]
```

**Purpose**: Performs one actual streaming request to OpenRouter. It marks the stream as started and converts raw chunks into UFO events.

**Data flow**: It receives the request, the stream-state object, and providers to ignore. It builds request arguments, opens a streamed chat completion, yields ModelStreamStart when the first chunk arrives, and then yields events produced from each chunk.

**Call relations**: OpenRouterModelClient.complete calls this inside its retry loop. It uses _create_kwargs to prepare the API call, _excluded to decide provider exclusions, and _OpenRouterStream.accept to translate chunks.

*Call graph*: calls 4 internal fn (_create_kwargs, _excluded, accept, openrouter_slug); called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient._excluded`  (lines 726–733)

```
def _excluded(self, slug: str, dead: set[str]) -> frozenset[str]
```

**Purpose**: Builds the set of upstream providers that should be avoided for the next OpenRouter call. It keeps exclusions safe for models that have a fixed provider order.

**Data flow**: It receives the OpenRouter slug and the providers already considered dead in this round. For models with a configured provider order, it combines current dead providers with earlier stalled providers but leaves at least one route open. For other models, it returns only the current dead set.

**Call relations**: OpenRouterModelClient._stream calls this before making the network request. The returned set becomes OpenRouter's provider.ignore preference in _create_kwargs.

*Call graph*: called by 1 (_stream).


##### `OpenRouterModelClient._nowhere_left`  (lines 735–750)

```
def _nowhere_left(self, slug: str, model: str, error: openai.APIStatusError, dead: set[str]) -> bool
```

**Purpose**: Detects a special case where this client's own provider exclusions made an unpinned OpenRouter model have no route left. In that case, the turn should degrade to an empty result instead of failing hard.

**Data flow**: It receives the slug, public model name, HTTP error, and excluded providers. If there are exclusions, the error is 404, and the model is not one with a fixed provider order, it logs the exhaustion and returns true. Otherwise it returns false.

**Call relations**: OpenRouterModelClient.complete calls this after status errors. If it returns true, complete yields empty usage if needed and ends the request rather than continuing retries or raising the 404.

*Call graph*: called by 1 (complete); 1 external calls (log).


##### `OpenRouterModelClient._stalled_out`  (lines 752–774)

```
def _stalled_out(self, slug: str, upstream: str | None, kind: str) -> None
```

**Purpose**: Remembers an upstream provider that stalled or refused a request, so later calls in the same turn avoid it. This prevents retrying the same broken route under a sticky OpenRouter session.

**Data flow**: It receives the model slug, upstream provider name, and a reason label. If the model uses a configured provider order and the provider is known and not already remembered, it appends the provider to the stalled list and logs it.

**Call relations**: OpenRouterModelClient.complete calls this when streams die or upstream refusals are detected. _excluded later reads the stalled list to carry those avoidances into subsequent attempts.

*Call graph*: called by 1 (complete); 1 external calls (log).


##### `OpenRouterModelClient._finish_usage`  (lines 776–782)

```
async def _finish_usage(self, state: _OpenRouterStream) -> Usage
```

**Purpose**: Ensures a completed stream has a Usage record. If the stream did not include usage directly, it tries OpenRouter's generation lookup endpoint.

**Data flow**: It receives the stream state. It returns the usage already found in the stream, or asks _generation_usage for usage using the generation ID and finish reason. If no usage can be found, it raises an error.

**Call relations**: OpenRouterModelClient.complete calls this after a stream ends normally. It hands off to _generation_usage only when OpenRouter did not include usage inside the stream.

*Call graph*: calls 1 internal fn (_generation_usage); called by 1 (complete).


##### `OpenRouterModelClient._generation_usage`  (lines 784–841)

```
async def _generation_usage(self, generation_id: str, finish_reason: str) -> Usage | None
```

**Purpose**: Looks up missing token usage for a completed OpenRouter generation. This covers cases where the stream finished but usage was not included yet.

**Data flow**: It receives a generation ID and expected finish reason. It repeatedly GETs OpenRouter's generation endpoint, tolerating short-lived 404s and transport errors, then validates the returned usage data and converts it to a Usage object. If the lookup never becomes available, it raises ModelStreamInterrupted.

**Call relations**: OpenRouterModelClient._finish_usage calls this as a fallback. It emits retry metrics while waiting for OpenRouter's ledger to catch up.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_finish_usage); 4 external calls (__init__, sleep, AsyncClient, emit_metric).


##### `OpenRouterModelClient._create_kwargs`  (lines 843–898)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Builds the exact keyword arguments passed to the OpenAI SDK for an OpenRouter chat-completion request. This is where UFO's request becomes OpenRouter's wire-format request.

**Data flow**: It receives a ModelRequest and a set of providers to ignore. It requires a session ID, creates extra OpenRouter settings for routing and reasoning, converts messages, includes tools and tool-choice settings when present, and returns a dictionary ready for client.chat.completions.create.

**Call relations**: OpenRouterModelClient._stream calls this immediately before sending a request. It uses openrouter_slug and _openrouter_messages to prepare OpenRouter-specific model and message fields.

*Call graph*: calls 2 internal fn (_openrouter_messages, openrouter_slug); called by 1 (_stream).


##### `_model_client`  (lines 901–906)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: Constructs an OpenRouterModelClient for a specific model spec and API key. This is the factory that the model registry uses when it needs a live client.

**Data flow**: It receives a ModelSpec and key. It creates an OpenAI-compatible async client pointed at OpenRouter's base URL, wraps it in OpenRouterModelClient, and returns that client.

**Call relations**: _openrouter stores this factory inside each ModelSpec. Later, when UFO selects one of those specs, the registry can call this function to obtain the working provider client.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 909–935)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW, reasoning: ReasoningSupport=_REASONS, accepts_image_input: bool=True, compaction_keep_messages:
```

**Purpose**: Creates a ModelSpec entry for one OpenRouter text model. A ModelSpec is the registry record that tells UFO a model's price, limits, key slot, and client factory.

**Data flow**: It receives model metadata such as ID, price, knowledge cutoff, context window, reasoning support, and compaction settings. It returns a populated ModelSpec using OpenRouter's provider name, API key environment variable, and client factory.

**Call relations**: The file uses this helper to build OPENROUTER_MODEL_SPECS. The manifest then exposes those specs to the host system.

*Call graph*: 1 external calls (__init__).


##### `GenerateImageInput._within_model_limits`  (lines 1046–1070)

```
def _within_model_limits(self) -> 'GenerateImageInput'
```

**Purpose**: Validates image-generation arguments against what the selected image model can actually do. This catches unsupported combinations before sending a doomed request to OpenRouter.

**Data flow**: It reads the chosen model, image count, aspect ratio, and resolution from the input object. It raises a validation error for too many images or unsupported settings, and fills in a default resolution for models that use resolution tiers.

**Call relations**: Pydantic calls this automatically after building GenerateImageInput. OpenRouterImages.generate then receives only arguments that passed these model-specific checks.


##### `_reported_cost_micro_usd`  (lines 1078–1090)

```
def _reported_cost_micro_usd(usage: object) -> int | None
```

**Purpose**: Finds the cost OpenRouter reported for an image or video generation and converts it to micro-dollars. A micro-dollar here means one millionth of a US dollar, which is convenient for exact billing math.

**Data flow**: It receives a usage-like object. It checks usage.cost first, then cost_details.upstream_inference_cost for bring-your-own-key style billing, ignores zero or invalid values, and returns a rounded micro-USD amount or nothing.

**Call relations**: OpenRouterImages._charge uses this for image costs, and OpenRouterVideos._job uses it when reading completed video jobs. If it returns nothing, each tool falls back to its configured list price.

*Call graph*: called by 2 (_charge, _job).


##### `OpenRouterImages.generate`  (lines 1123–1160)

```
async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Runs one complete image-generation tool call. It sends the prompt to OpenRouter, saves returned images in the workspace, meters cost when appropriate, and returns file paths plus image content to the model.

**Data flow**: It receives a ToolContext and validated GenerateImageInput. It obtains the OpenRouter key, posts the request, converts errors into tool errors, decodes images, writes files, calculates cost, optionally records image billing, and returns a ToolResult containing JSON metadata and image attachments.

**Call relations**: _generate_image calls this as the registered tool handler. Inside, it uses _refusal, _images, _save, and _charge to break the job into readable steps.

*Call graph*: calls 5 internal fn (meter_images, _charge, _images, _refusal, _save); 6 external calls (__init__, __init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterImages._refusal`  (lines 1162–1177)

```
def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str
```

**Purpose**: Turns a failed image API response into a short message the model can read and react to. This might describe a rejected prompt, bad parameter, missing balance, or other provider refusal.

**Data flow**: It receives the original image arguments and HTTP response. It tries to read a JSON error message, falls back to raw response text, trims it to a safe length, and returns one readable sentence.

**Call relations**: OpenRouterImages.generate calls this when OpenRouter returns an error status. The returned text becomes the content of an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterImages._images`  (lines 1179–1209)

```
def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]
```

**Purpose**: Extracts usable images from OpenRouter's response. It refuses to save missing or oversized image data.

**Data flow**: It receives the original image arguments and response body. It looks for base64-encoded image entries, decodes each into bytes, applies the maximum byte limit, assigns a media type when missing, and returns GeneratedImage records. If none are usable, it raises OpenRouterImageError.

**Call relations**: OpenRouterImages.generate calls this after a successful HTTP response. The returned GeneratedImage objects are then saved by _save and included in the final ToolResult.

*Call graph*: called by 1 (generate); 3 external calls (__init__, __init__, b64decode).


##### `OpenRouterImages._save`  (lines 1211–1218)

```
async def _save(self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage) -> str
```

**Purpose**: Writes one generated image file into the workspace. It chooses a file extension based on the image's media type.

**Data flow**: It receives the tool context, input arguments, image index, and image data. It builds a path under generated-images, writes the raw bytes through the sandbox, and returns the saved path.

**Call relations**: OpenRouterImages.generate calls this once for each decoded image. The collected paths are included in the tool's JSON result so the agent can refer to or share the files.

*Call graph*: called by 1 (generate).


##### `OpenRouterImages._charge`  (lines 1220–1227)

```
def _charge(self, body: object, args: GenerateImageInput, images: int) -> int
```

**Purpose**: Calculates what an image generation should cost for metering. It uses OpenRouter's reported charge when available and otherwise uses the model's configured list price per image.

**Data flow**: It receives the response body, original arguments, and number of images. It reads usage cost through _reported_cost_micro_usd; if no positive reported cost exists, it multiplies the model's fallback price by the image count.

**Call relations**: OpenRouterImages.generate calls this after saving images. The result is written into the tool output and passed to ToolContext.meter_images when the platform key paid for the generation.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 1 (generate).


##### `_generate_image`  (lines 1230–1235)

```
async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Acts as the registered handler for the generate_image tool. It connects the generic tool system to the OpenRouterImages helper.

**Data flow**: It receives a ToolContext and validated image arguments. It checks that extension context is available, creates an OpenRouterImages runner with credentials and optional test transport, and returns the runner's ToolResult.

**Call relations**: GENERATE_IMAGE_TOOL points at this function. When an agent calls generate_image, the tool runtime invokes this handler.

*Call graph*: 1 external calls (__init__).


##### `GenerateVideoInput._within_model_limits`  (lines 1291–1315)

```
def _within_model_limits(self) -> 'GenerateVideoInput'
```

**Purpose**: Validates video-generation arguments against the selected video model's real limits. It also chooses the default resolution that will be used for billing.

**Data flow**: It reads the chosen model, duration, aspect ratio, and resolution from the input object. It rejects unsupported duration, aspect-ratio, or resolution choices, and fills in the model's default resolution when the caller omitted one.

**Call relations**: Pydantic runs this automatically after creating GenerateVideoInput. OpenRouterVideos.generate then works with arguments that match the allowlisted model's capabilities.


##### `OpenRouterVideos.generate`  (lines 1357–1398)

```
async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Runs one complete video-generation tool call. It starts the OpenRouter video job, waits for it to finish, downloads the MP4, saves it, meters cost when appropriate, and returns the saved path.

**Data flow**: It receives a ToolContext and validated GenerateVideoInput. It gets credentials, posts the video request, handles immediate refusal, polls the job until it settles, returns an error if generation failed, downloads the completed content, writes it to the workspace, calculates cost, optionally records video billing, and returns a ToolResult with metadata.

**Call relations**: _generate_video calls this as the registered tool handler. It coordinates _refusal, _job, _settled, _failure, _download, _save, and _charge.

*Call graph*: calls 8 internal fn (meter_videos, _charge, _download, _failure, _job, _refusal, _save, _settled); 5 external calls (__init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterVideos._refusal`  (lines 1400–1415)

```
def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str
```

**Purpose**: Turns an immediate failed video API response into readable tool error text. This gives the model enough information to change the prompt or settings.

**Data flow**: It receives the video arguments and HTTP response. It tries to read a JSON error message, falls back to response text, trims the detail to a safe length, and returns a short failure message.

**Call relations**: OpenRouterVideos.generate calls this when the initial POST request returns an error status. The message becomes the text of an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterVideos._job`  (lines 1417–1432)

```
def _job(self, body: object) -> VideoJob
```

**Purpose**: Parses OpenRouter's video job description into a small VideoJob record. A video job is the ticket used to poll and later download the generated file.

**Data flow**: It receives a response body. It reads the job ID, status, optional error message, and optional reported cost; if the ID or status is missing, it raises OpenRouterVideoError. Otherwise it returns a VideoJob.

**Call relations**: OpenRouterVideos.generate calls this after the initial POST, and OpenRouterVideos._settled calls it after each poll response. It uses _reported_cost_micro_usd to capture billing data when OpenRouter provides it.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 2 (_settled, generate); 2 external calls (__init__, __init__).


##### `OpenRouterVideos._settled`  (lines 1434–1455)

```
async def _settled(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> VideoJob
```

**Purpose**: Polls an OpenRouter video job until it is no longer pending or in progress. It places a time limit on the wait so a stuck outside job does not hold the turn forever.

**Data flow**: It receives the input arguments, an HTTP client, and the current VideoJob. While the job is pending or in progress, it sleeps, polls OpenRouter, parses the new job state, and stops when the status changes. If the deadline passes or polling fails, it raises OpenRouterVideoError.

**Call relations**: OpenRouterVideos.generate calls this after creating the job. It repeatedly hands poll bodies to _job and returns the final job state to generate.

*Call graph*: calls 1 internal fn (_job); called by 1 (generate); 4 external calls (__init__, sleep, get, monotonic).


##### `OpenRouterVideos._failure`  (lines 1457–1461)

```
def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str
```

**Purpose**: Builds a readable explanation for a video job that ended without producing a completed video. It prefers the provider's own error message when available.

**Data flow**: It receives the original video arguments and final VideoJob. It chooses the job's error text or a generic status message, trims it, and returns a short sentence.

**Call relations**: OpenRouterVideos.generate calls this when a settled job is not completed. The returned text becomes the content of an error ToolResult.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._download`  (lines 1463–1482)

```
async def _download(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> bytes
```

**Purpose**: Downloads the finished MP4 for a completed video job and checks that it is usable. It prevents empty or oversized video files from being saved.

**Data flow**: It receives the input arguments, HTTP client, and completed VideoJob. It GETs the job content, raises an error for failed downloads, empty content, or content over the byte limit, and returns the raw video bytes.

**Call relations**: OpenRouterVideos.generate calls this only after _settled reports a completed job. The bytes it returns are then passed to _save.

*Call graph*: called by 1 (generate); 2 external calls (__init__, get).


##### `OpenRouterVideos._save`  (lines 1484–1488)

```
async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str
```

**Purpose**: Writes the finished video into the workspace as an MP4 file.

**Data flow**: It receives the tool context, input arguments, and raw video bytes. It builds a path under generated-videos using the requested file name, writes the bytes through the sandbox, and returns the path.

**Call relations**: OpenRouterVideos.generate calls this after downloading the completed video. The returned path is included in the final tool result.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._charge`  (lines 1490–1498)

```
def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int
```

**Purpose**: Calculates the cost to meter for a video generation. It uses OpenRouter's reported cost when available and otherwise falls back to the configured per-second model rate for the chosen resolution.

**Data flow**: It receives the video arguments and final VideoJob. If the job already contains a reported micro-USD cost, it returns that; otherwise it multiplies the model's per-second price for the actual resolution by the requested duration.

**Call relations**: OpenRouterVideos.generate calls this after saving the video. The result is written into the tool output and passed to ToolContext.meter_videos when the platform key paid for the generation.

*Call graph*: called by 1 (generate).


##### `_generate_video`  (lines 1501–1506)

```
async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Acts as the registered handler for the generate_video tool. It connects the tool runtime to the OpenRouterVideos runner.

**Data flow**: It receives a ToolContext and validated video arguments. It checks that extension context exists, creates an OpenRouterVideos helper with credentials and optional test transport, and returns the helper's ToolResult.

**Call relations**: GENERATE_VIDEO_TOOL points at this function. When an agent calls generate_video, the tool runtime invokes this handler.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1519–1535)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO host system. It advertises the OpenRouter models, the image and video tools, and the credential slot needed for the API key.

**Data flow**: It takes no input. It returns a Manifest containing the extension name and version, all registered model specs, both tool definitions, and the OpenRouter API-key credential description.

**Call relations**: The extension loader calls this to discover what the file provides. The returned manifest is how the rest of UFO learns that OpenRouter models and generation tools are available.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/harness/models/spec.py`

`data_model` · `model registry setup and per-request model calling`

This file is the project’s model fact sheet. Each supported model gets a `ModelSpec`, which is a frozen data record: once created, its values cannot be changed by accident. That matters because many parts of the system need to agree on the same facts: which provider serves the model, how much it costs, whether it can use images, how large a conversation it can read, whether it supports “reasoning” mode, and where to find its API key.

Without this file, those facts would likely be scattered across prompt building, billing, client setup, error handling, and transcript cleanup. That would make mistakes easy: one place might think a model supports tools with reasoning while another place sends an invalid request.

The file also contains small guardrails. Dates must look like `YYYY-MM`; transcript compaction thresholds must make sense; reasoning options cannot contradict each other. Think of it like a checklist at a rental counter: before handing over a car, the system confirms the fuel type, license rules, and limits are all valid.

Two helper records sit beside `ModelSpec`. `ReasoningSupport` describes whether a model can do extra reasoning work and when that can be turned off. `RepeatedToolCompaction` describes when repeated tool-heavy conversations should be shortened to stay within the model’s memory limit.

#### Function details

##### `ReasoningSupport.internal_effort`  (lines 38–41)

```
def internal_effort(self) -> ReasoningEffort
```

**Purpose**: This chooses the system’s safest internal reasoning setting for a model. It answers: should the system treat reasoning as off, or must it use the model’s minimum allowed reasoning level?

**Data flow**: It reads the `ReasoningSupport` record: whether reasoning is supported, whether it can be disabled, and the minimum allowed effort. If the model does not support reasoning, or if reasoning can be turned off, it returns `"off"`. If reasoning is mandatory, it returns the model’s minimum reasoning effort.

**Call relations**: This is a small decision helper for code that needs a default internal reasoning value from a model’s capabilities. It does not call out to other code; it simply interprets the fields stored on the same `ReasoningSupport` record.


##### `RepeatedToolCompaction.__post_init__`  (lines 51–55)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that the repeated-tool transcript compaction rule is usable. It prevents settings that would trigger too early, never trigger sensibly, or represent an impossible percentage.

**Data flow**: It receives a newly created `RepeatedToolCompaction` object with `consecutive_turns` and `trigger_percent`. It verifies that repeated tool use means at least two consecutive turns, and that the trigger percentage is from 1 through 99. If the values are valid, creation continues unchanged; if not, it raises a `ValueError` with a clear message.

**Call relations**: This runs automatically when a `RepeatedToolCompaction` record is created. It does not hand work to another project function; its role is to stop bad configuration before any conversation-shortening logic relies on it.


##### `ModelSpec.__post_init__`  (lines 85–106)

```
def __post_init__(self) -> None
```

**Purpose**: This validates a model’s fact sheet as soon as it is created. It catches contradictory or unsafe model settings early, before a request reaches a provider and fails in a harder-to-understand way.

**Data flow**: It reads the new `ModelSpec` fields: the knowledge cutoff date, reasoning settings, compaction limits, and context window. It checks that the date is in `YYYY-MM` form, that reasoning-related flags do not contradict each other, and that compaction thresholds are positive and smaller than the model’s context window. Valid specs pass through unchanged; invalid specs raise `ValueError` with model-specific messages.

**Call relations**: This runs automatically during `ModelSpec` creation, usually as model entries are registered. It sits at the boundary between configuration and runtime use: later code can trust the spec because this method has already rejected impossible combinations.


##### `ModelSpec.key_rejected`  (lines 108–119)

```
def key_rejected(self) -> CredentialValueInvalid
```

**Purpose**: This turns a provider’s “bad key” response into the project’s own clear credential error. It tells the user that the API key for this model was rejected and points them toward the possible places that key came from.

**Data flow**: It reads the model id, provider name, environment-variable key name, and workspace key slot from the `ModelSpec`. It builds a human-readable message explaining that the provider rejected the key, then creates and returns a `CredentialValueInvalid` error object containing that message.

**Call relations**: This is used when a provider reports an authentication failure, such as an HTTP 401 status. Its only handoff is to `CredentialValueInvalid`, which wraps the message in the project’s standard credential-error type so callers do not have to understand each provider’s own error class.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.rate_limited`  (lines 121–129)

```
def rate_limited(self) -> ModelAccountRateLimited
```

**Purpose**: This turns a provider’s “too many requests” or “no capacity” response into the project’s own account-capacity error. It gives callers one consistent error type for rate limits across different model providers.

**Data flow**: It reads the model id and provider name from the `ModelSpec`. It writes a message saying that the account serving this model has no capacity right now, then creates and returns a `ModelAccountRateLimited` error object.

**Call relations**: This is used after the model client has exhausted its own retries and the provider is still rate limiting, commonly associated with an HTTP 429 response. It hands the final message to `ModelAccountRateLimited`, making rate-limit failures look the same to the rest of the system no matter which provider produced them.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.wire_reasoning`  (lines 131–145)

```
def wire_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort | None
```

**Purpose**: This decides what reasoning setting, if any, should actually be sent to the model provider for one request. It protects the system from sending reasoning options to models or request shapes that do not support them.

**Data flow**: It receives the user- or system-requested reasoning effort and the tools included in the request. It reads the model’s reasoning capabilities from the `ModelSpec`. If the model does not support reasoning, it returns `None`, meaning no reasoning parameter should be sent. If tools are present but this model cannot combine tools with reasoning, it also returns `None`. If reasoning was requested as `"off"` but the model has reasoning on by default and cannot disable it, it returns the model’s minimum effort instead. Otherwise, it returns the requested effort unchanged.

**Call relations**: This sits just before a request is converted into the provider’s wire format, meaning the exact data sent over the API. It does not call other functions; it acts as the model-specific rulekeeper so client code can ask one question: “what reasoning value is safe to send for this request?”


### `core/src/ufo/harness/models/registry.py`

`domain_logic` · `startup and model request handling`

This file solves a practical problem: many parts of the system need to know what a model is, who provides it, how much it costs, and which key should pay for it. Without one shared registry, a bad model name or missing key could show up much later as a confusing provider error, a broken bill, or a failed turn.

The registry works like a front desk for models. At startup, `model_registry` collects the built-in model definitions and any model definitions supplied by extensions. It refuses duplicate model IDs, so one model cannot quietly replace another. It also checks that configured default models really exist.

During a run, `ModelRegistry` answers questions such as “what provider serves this model?”, “which bring-your-own-key slot pays for it?”, and “what client should I call?” A client is the object that sends requests to the model provider. The registry builds clients only when needed, so changed or refreshed credentials can be picked up without restarting.

There is special care for member-owned accounts. If a provider rejects a token before any response has streamed back, `_RebuiltOnRejection` rebuilds the client once, giving refreshed credentials a chance. If a member has multiple connected accounts, `MemberAccounts` and `ServingModel` can move a turn to the next account, but only if billing still matches the account that the turn was supposed to use.

#### Function details

##### `_RebuiltOnRejection.complete`  (lines 57–72)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This runs a model request through a client that can be rebuilt once if the provider says the credential is invalid before any output has been delivered. It exists to survive short-lived access tokens expiring during a long turn, without replaying a stream the user has already started seeing.

**Data flow**: It receives a model request and starts yielding events from the already-built client. If events have already been yielded and the credential fails, it lets the error pass through because replaying would duplicate output. If no event has been delivered yet, it asks the registry for a fresh client for the same model, checks that the funding source and payer did not change, and then yields events from the rebuilt client. If the payer changed, it raises a funding-change error instead of silently charging someone else.

**Call relations**: This wrapper is created by `ModelRegistry.client_for` for member-routed calls. When normal model streaming hits an invalid credential early, this method does the one allowed rebuild; if the rebuild would change who pays, it hands off to `ModelFundingChanged` so the caller sees a clear failure.

*Call graph*: 1 external calls (__init__).


##### `MemberAccounts.next`  (lines 95–105)

```
async def next(self) -> tuple[ModelSpec, ModelClient]
```

**Purpose**: This chooses the next connected member account to try when the current account cannot continue. It keeps failover safe by making sure the new model call is still paid by the member account that the turn is allowed to use.

**Data flow**: It reads the remaining alternate model IDs. If none are left, it raises the stored “all accounts exhausted” error. Otherwise it removes the first alternate from the list, looks up that model’s specification, builds a client for it, and compares the resolved funding and payer with the current workspace member payer. If they match, it returns the model specification and client. If not, it raises a funding-change error.

**Call relations**: This is used by `ServingModel.move` when a turn needs to switch from one member account to another. It consults the current workspace through `ws_current` so the move cannot accidentally fall back to a workspace or platform key.

*Call graph*: 2 external calls (__init__, ws_current).


##### `ServingModel.move`  (lines 130–137)

```
async def move(self) -> bool
```

**Purpose**: This moves an active turn to the member’s next available account, if such account failover is allowed. It keeps the current model ID, model facts, and client together so later rounds all refer to the new model consistently.

**Data flow**: It starts with the serving model currently used by a turn. If there is no `MemberAccounts` object attached, it returns `false`, meaning this turn cannot move. If accounts are available, it asks for the next model specification and client, replaces its own stored specification, client, and model ID, and returns `true`.

**Call relations**: This method is the small switch lever used by turn-running code when a provider rejects a member account before producing output. It relies on `MemberAccounts.next` to choose and validate the next account before changing the live `ServingModel`.


##### `ModelRegistry.resolve`  (lines 151–154)

```
def resolve(self, model: str) -> str
```

**Purpose**: This converts the special model name `auto` into the concrete default model configured for this deployment. If the caller already gave a real model ID, it leaves it unchanged.

**Data flow**: It receives a model name. If that name is the automatic-model sentinel, it returns the registry’s configured default model ID. Otherwise it returns the original name.

**Call relations**: This is called by `ModelRegistry.key_slot_for` and `ModelRegistry.model_key_env` before they answer questions about keys. That way a stored setting of `auto` is treated as the real model that will actually run.

*Call graph*: called by 2 (key_slot_for, model_key_env).


##### `ModelRegistry.spec`  (lines 156–162)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This looks up the registered facts for a model ID. It gives the rest of the system one reliable place to fail if a model name is unknown.

**Data flow**: It receives a model ID and reads the registry’s model table. If the ID exists, it returns the corresponding `ModelSpec`, which describes things like provider, client builder, price, and key slot. If the ID is missing, it raises a clear error naming the unknown model.

**Call relations**: This lookup is used by `ModelRegistry.client_for`, `ModelRegistry.provider_for`, and `ModelRegistry.model_key_env`. Those callers all depend on it so mistakes in model names are caught at the registry boundary instead of later in unrelated code.

*Call graph*: called by 3 (client_for, model_key_env, provider_for).


##### `ModelRegistry.client_for`  (lines 164–199)

```
async def client_for(self, model: str) -> ResolvedModelClient
```

**Purpose**: This builds the actual client object used to call a model, with the right credential and payer attached. It also validates that the credential can safely be sent to the provider.

**Data flow**: It receives a model ID, looks up its specification, and checks whether that model needs a key. If no key is needed, it returns a platform-funded client. If a key is needed, it asks the current workspace for the right credential, reports a clear error if none is set, rejects non-ASCII key values because the provider connection cannot carry them, and builds the provider client. For member-routed calls, it wraps the client in `_RebuiltOnRejection` so one early credential rejection can trigger a safe rebuild. It returns the client together with the funding type and exact payer.

**Call relations**: This is the registry’s main handoff from model name to callable provider client. It calls `ModelRegistry.spec` for model facts, uses `ws_current` to find credentials in the active workspace, creates `ResolvedModelClient` as the result, and may create `_RebuiltOnRejection` for safer member-account calls.

*Call graph*: calls 1 internal fn (spec); 4 external calls (__init__, __init__, __init__, ws_current).


##### `ModelRegistry.provider_for`  (lines 201–205)

```
def provider_for(self, model: str) -> str
```

**Purpose**: This answers which provider, such as Anthropic or OpenAI, serves a given model. It is useful when calls or costs need to be grouped by backend provider.

**Data flow**: It receives a model ID, looks up the model specification, and returns the provider name stored there. If the model ID is unknown, the lookup raises the same clear registry error used elsewhere.

**Call relations**: It depends on `ModelRegistry.spec` so provider reporting uses the same model facts as client creation and key checks. Other parts of the system can call this when they need provider-level labels without building a full client.

*Call graph*: calls 1 internal fn (spec).


##### `ModelRegistry.key_slot_for`  (lines 207–218)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This tells which bring-your-own-key slot would pay for a model, if any. It is deliberately forgiving for old or missing model IDs so billing exports can label them as platform-served instead of crashing.

**Data flow**: It receives a model name, first resolving `auto` to the configured real model. It then checks the registry table directly. If there is no matching model or the model has no key slot, it returns `null`. Otherwise it returns the key slot name.

**Call relations**: It calls `ModelRegistry.resolve` so automatic model settings are interpreted as the model that would actually run. Unlike stricter paths such as `spec`, this method is shaped for reporting and historical records, where an unknown old model should not stop the whole export.

*Call graph*: calls 1 internal fn (resolve).


##### `ModelRegistry.model_key_env`  (lines 220–230)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding which environment variable should be set before a model’s first use. It only answers for the core providers whose key names the system knows how to check ahead of time.

**Data flow**: It receives a model name and the configuration object. It resolves `auto`, looks up the model specification, reads the provider, and then returns the configured Anthropic or OpenAI API-key environment variable name. For other providers, usually contributed by extensions, it returns `null` because their key lookup happens later.

**Call relations**: It calls `ModelRegistry.resolve` and then `ModelRegistry.spec` so onboarding checks the actual configured model, not just the placeholder `auto`. It is used before a turn to give users early feedback about missing core provider keys.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 233–266)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the complete `ModelRegistry` used by the running system. It combines built-in models with extension-provided models, checks that important configured models exist, and prepares the shared price table.

**Data flow**: It receives the system configuration and a set of extension manifests. It asks for the built-in model specifications, adds every manifest-contributed model, and stores them by model ID. If two models claim the same ID, it raises an error. It then checks that the configured automatic model, ambient reply model, and background job model are all registered. Finally it builds pricing from the registered model prices and returns a new `ModelRegistry`.

**Call relations**: This is the startup builder for the registry. It calls `core_model_specs` to get built-in models, `pricing_from` to make the merged pricing table, and constructs `ModelRegistry` as the object other runtime code will query during model calls.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### Provider adapters and credentials
Implements Anthropic and OpenAI-compatible streaming adapters and manages connected provider grants.

### `core/src/ufo/harness/models/anthropic.py`

`io_transport` · `request handling`

The rest of the system wants to talk to language models in one common format, no matter which company provides the model. Anthropic's API has its own request shape, authentication rules, streaming event types, tool-call format, image format, and error behavior. This file translates between those worlds.

On the way out, it builds Anthropic-compatible request data from a `ModelRequest`: system text, chat messages, tools, images, cache hints, and optional reasoning settings. It also chooses the right authentication style: normal API keys use one header, while Anthropic OAuth tokens use bearer-token authentication and a special beta header.

On the way back, `_AnthropicStream` reads Anthropic's stream like a live transcript. Text chunks become UFO text events, tool-call starts and JSON fragments become UFO tool-call events, and hidden reasoning blocks are saved until the end so they can be replayed correctly later. Token usage is collected along the way.

The file is also careful about failure. Before any visible output has been yielded, temporary network or provider errors can be retried with increasing waits. After visible output has begun, the partial answer is no longer safe to silently retry, so the stream is marked interrupted and the higher-level round logic can restart cleanly.

#### Function details

##### `anthropic_sdk_client`  (lines 57–73)

```
def anthropic_sdk_client(credential: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the Anthropic software client used to make API calls. It deliberately turns off the SDK's built-in retries because this file applies its own retry rules, which are aware of streaming and UFO's round logic.

**Data flow**: It takes one credential string. It checks whether the credential looks like an Anthropic OAuth token; if so, it creates a client that authenticates with a bearer token and beta headers. Otherwise, it creates a client that authenticates with an API key. The result is an `AsyncAnthropic` client ready for requests.

**Call relations**: This is the setup doorway for Anthropic access. It calls `is_oauth_credential` to decide which authentication path to use, then hands the chosen settings to Anthropic's SDK client constructor.

*Call graph*: calls 1 internal fn (is_oauth_credential); 1 external calls (AsyncAnthropic).


##### `is_oauth_credential`  (lines 76–78)

```
def is_oauth_credential(credential: str) -> bool
```

**Purpose**: Tells whether a credential is an Anthropic OAuth access token rather than a normal API key. This matters because the two are sent to Anthropic differently.

**Data flow**: It receives a credential string and checks its prefix. If the string starts with Anthropic's OAuth token prefix, it returns true; otherwise it returns false. It does not change anything else.

**Call relations**: `anthropic_sdk_client` calls this before building the Anthropic SDK client, so the client is configured with the correct kind of authentication.

*Call graph*: called by 1 (anthropic_sdk_client).


##### `_anthropic_image`  (lines 81–85)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts UFO's internal image representation into the image block shape Anthropic expects. It is a small translator for base64-encoded image data.

**Data flow**: It receives an `ImageSource`, which includes a media type such as PNG or JPEG and the base64 image data. It wraps those fields in Anthropic's nested dictionary format and returns that dictionary.

**Call relations**: This helper is used when outgoing messages or tool results include images. `anthropic_content` uses it for normal image blocks, and `_anthropic_tool_result_part` uses it for image pieces inside tool results.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 88–93)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of tool output into Anthropic's content format. Tool output can be text or an image, and Anthropic needs each piece described in its own wire format.

**Data flow**: It receives a tool-result content block. If the block is text, it returns a text dictionary. If the block is an image, it passes the image source to `_anthropic_image` and returns the converted image dictionary.

**Call relations**: `anthropic_content` calls this while building a tool result message that may contain multiple text and image parts.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 96–130)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Turns UFO's message content into the exact content shape Anthropic's API accepts. This lets the rest of the system keep using one common message model while this file handles Anthropic-specific formatting.

**Data flow**: It receives either plain text or a tuple of content blocks. Plain text passes through unchanged. Structured blocks are inspected one by one and converted into Anthropic dictionaries for text, images, tool calls, tool results, and Anthropic reasoning. OpenAI-style reasoning items are skipped because Anthropic cannot use them.

**Call relations**: `AnthropicClient._request_kwargs` calls this for every outgoing chat message. During conversion it delegates image formatting to `_anthropic_image` and tool-result piece formatting to `_anthropic_tool_result_part`.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (_request_kwargs).


##### `_AnthropicRetry.transport`  (lines 141–176)

```
async def transport(self, error: Exception, yielded: bool) -> _AnthropicRetry
```

**Purpose**: Decides what to do after a network-style streaming failure, such as a timeout or dropped connection. It protects users from brief provider glitches, while avoiding unsafe retries after visible output has already been shown.

**Data flow**: It receives the original error and a flag saying whether any text or tool-call output has already been yielded. If output was already yielded, it logs the failure and raises a stream-interrupted error. If no output was yielded and the retry budget remains, it logs and counts the retry, waits for the current delay, and returns a new retry state with a larger delay. If the retry budget is exhausted, it re-raises the original error.

**Call relations**: `AnthropicClient.complete` calls this when Anthropic's stream fails with transport errors. This method uses logging and metrics for observability, sleeps before retrying, and returns updated retry instructions to the main completion loop.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_AnthropicRetry.status`  (lines 178–238)

```
async def status(self, error: anthropic.APIStatusError, yielded: bool) -> _AnthropicRetry
```

**Purpose**: Decides what to do after Anthropic reports an API status error. It separates permanent problems, like a rejected key or bad request, from temporary problems that can be retried.

**Data flow**: It receives Anthropic's status error and whether visible output has already been yielded. A rejected key becomes UFO's credential error. Non-retryable client errors are raised immediately. Rate limits and temporary provider errors may be retried before output is visible, using `retry-after` if Anthropic supplied it. Long rate-limit waits can be handed back as `ModelRetryAfter` instead of sleeping inside this call.

**Call relations**: `AnthropicClient.complete` calls this when the stream raises an Anthropic status error. The method logs outcomes, emits retry metrics, may wait, and then either returns updated retry state or raises the right higher-level error for the round controller.

*Call graph*: calls 2 internal fn (__init__, __init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_AnthropicStream.__init__`  (lines 242–253)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh state tracker for one Anthropic streaming response. It starts with no emitted output, no known tool calls, no collected reasoning, and zero token usage.

**Data flow**: It takes no outside data besides the new object being created. It initializes dictionaries and counters that will be filled as stream events arrive. The result is an empty stream state ready for `accept` to update.

**Call relations**: `AnthropicClient.complete` creates a new `_AnthropicStream` for each request attempt, including retries. That keeps partial state from one attempt from leaking into the next.

*Call graph*: called by 1 (complete).


##### `_AnthropicStream.accept`  (lines 255–299)

```
def accept(self, event: object) -> tuple[ModelEvent, ...]
```

**Purpose**: Reads one raw Anthropic stream event and turns it into zero or more UFO model events. It is the main event translator for live responses.

**Data flow**: It receives one Anthropic event. Message-start events update token input usage. Text deltas become UFO text deltas. Tool-use starts and JSON fragments become UFO tool-call events. Thinking and redacted-thinking events are stored for later rather than streamed live. Message-delta events record output token counts and the stop reason. It returns the UFO events that should be yielded immediately, if any, and records whether visible output has begun.

**Call relations**: `AnthropicClient.complete` feeds every raw stream event into this method. Inside, it calls `_record_input_usage` when usage first appears and `_close_thinking` when a reasoning block finishes, then hands immediate text or tool events back to the completion loop.

*Call graph*: calls 2 internal fn (_close_thinking, _record_input_usage); 4 external calls (__init__, __init__, __init__, __init__).


##### `_AnthropicStream._record_input_usage`  (lines 301–309)

```
def _record_input_usage(self, usage: Any) -> None
```

**Purpose**: Stores the input-token accounting reported by Anthropic. This includes normal input tokens and cache-related token counts.

**Data flow**: It receives Anthropic's usage object. It copies input tokens, cache-read tokens, and cache-write tokens into the stream state, handling both older and newer Anthropic cache-reporting shapes. It returns nothing; the stream state's counters are updated.

**Call relations**: `_AnthropicStream.accept` calls this when the stream starts and Anthropic sends message-level usage. Later, `has_usage` and `usage` use these stored values to decide what accounting event to emit.

*Call graph*: called by 1 (accept).


##### `_AnthropicStream._close_thinking`  (lines 311–320)

```
def _close_thinking(self, index: int) -> None
```

**Purpose**: Finishes one Anthropic thinking block and saves it as a complete UFO reasoning block. A thinking block must include a signature, because Anthropic requires the signed reasoning to be echoed back exactly in later tool-result turns.

**Data flow**: It receives the index of the thinking block that just ended. It gathers all text fragments saved for that index, retrieves and removes the matching signature, and appends a complete `ThinkingBlock` to the stream's reasoning list. If the signature is missing or empty, it raises an error because the reasoning would be unusable.

**Call relations**: `_AnthropicStream.accept` calls this when Anthropic says a thinking content block has stopped. The completed reasoning is not yielded immediately; `AnthropicClient.complete` yields it near the end, just before usage.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_AnthropicStream.has_usage`  (lines 322–328)

```
def has_usage(self) -> bool
```

**Purpose**: Checks whether this stream has any token-usage information worth reporting. This is useful when a stream fails before it completes but still reported some accounting data.

**Data flow**: It reads the stream state's input and cache token counters. If any of them are nonzero, it returns true; otherwise it returns false. It does not change the stream state.

**Call relations**: `AnthropicClient.complete` uses this check in failure paths and unusual stream endings so it can still yield usage data when Anthropic provided it.


##### `_AnthropicStream.usage`  (lines 330–337)

```
def usage(self) -> Usage
```

**Purpose**: Builds UFO's standard usage record from the token counts collected during the Anthropic stream. This gives the rest of the system one consistent accounting format.

**Data flow**: It reads input tokens, output tokens, cache-read tokens, and cache-write tokens from the stream state. If output tokens were never set, it reports zero output tokens. It returns a `Usage` object and does not change the stream state.

**Call relations**: `AnthropicClient.complete` yields this result at the end of a successful stream and also before raising certain errors, so callers can still record token costs.

*Call graph*: 1 external calls (__init__).


##### `AnthropicClient._request_kwargs`  (lines 346–392)

```
def _request_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the dictionary of arguments passed to Anthropic's `messages.create` call. It is where UFO's model request is translated into Anthropic's request language.

**Data flow**: It receives a `ModelRequest`. It creates system-message blocks, converts chat messages after trimming images where needed, sets the model name, token limit, streaming flag, cache settings, reasoning settings, and tool definitions. If OAuth is being used, it adds Anthropic's Claude Code system prefix. The output is a dictionary ready to send to the Anthropic SDK.

**Call relations**: `AnthropicClient.complete` calls this right before starting a provider request. It relies on `anthropic_content` for message conversion and `trim_images` to keep image-bearing history within the supported shape.

*Call graph*: calls 1 internal fn (anthropic_content); called by 1 (complete); 1 external calls (trim_images).


##### `AnthropicClient.complete`  (lines 394–489)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one streaming completion request against Anthropic and yields UFO-standard model events. It is the main public behavior of this client: send the request, stream back text and tool calls, report reasoning and usage, and apply safe retry rules.

**Data flow**: It receives a `ModelRequest`. It creates retry state, builds Anthropic request arguments, opens a streaming response, and feeds each raw event through `_AnthropicStream.accept`. It yields a stream-start event, then text and tool-call events as they arrive. At the end it checks Anthropic's stop reason, raises clear UFO errors for truncation or refusal, retries empty answers a limited number of times, yields saved reasoning blocks, then yields final usage and returns. On retryable provider failures before visible output, it waits and tries again; after visible output, it raises an interrupted-stream error through the retry helper.

**Call relations**: This method is the coordinator for the whole file. It calls `_request_kwargs` to prepare the outgoing call, creates `_AnthropicStream` to translate incoming events, uses `_AnthropicRetry` for error decisions, emits metrics for empty-response retries, and yields the standardized events consumed by the rest of the harness.

*Call graph*: calls 2 internal fn (_request_kwargs, __init__); 5 external calls (__init__, __init__, __init__, __init__, emit_metric).


### `core/src/ufo/harness/models/openai.py`

`io_transport` · `request handling`

This file lets the rest of the system talk to OpenAI and OpenAI-compatible providers without caring about their exact wire format. A "wire format" is the shape of the HTTP request and streaming response a provider expects. Some models use OpenAI's older Chat Completions API, while others must use the newer Responses API, especially when reasoning data or certain tool settings are involved. This file chooses the right surface from the model specification, or forces the Responses path when the credential is a ChatGPT account token for the Codex backend.

The file does three main jobs. First, it builds SDK clients with the right host, headers, timeout, and retry settings. Second, it translates UFO's neutral message blocks, tools, images, tool results, reasoning items, and token limits into the exact request shape OpenAI expects. Third, it reads streaming events back from the provider and emits simple UFO events such as text chunks, tool-call starts, tool-call argument chunks, reasoning blocks, and final token usage.

It also protects the wider system from common provider failures. Before any visible output is produced, temporary network errors, rate limits, server errors, and empty responses can be retried. After output has started, a broken stream is treated as an interrupted round so the engine can discard the partial answer and try again safely. Without this file, the rest of the harness would need to know many provider-specific details and would be much more fragile.

#### Function details

##### `_cache_write_tokens`  (lines 112–120)

```
def _cache_write_tokens(details: PromptTokensDetails | InputTokensDetails | None) -> int
```

**Purpose**: Reads the provider's optional count of tokens written into a prompt cache. This matters for usage and billing, because cached tokens can be priced differently from normal input tokens.

**Data flow**: It receives token-detail data from an OpenAI usage object. It looks for a provider-specific extra field named cache_write_tokens, treats a missing value as zero, checks that any present value is a real integer, and returns that integer.

**Call relations**: Usage conversion helpers call this when they translate OpenAI usage into UFO's Usage record. It is shared by both the Chat Completions path and the Responses path so cache accounting stays consistent.

*Call graph*: called by 2 (_chat_usage, _responses_usage).


##### `_responses_usage`  (lines 123–137)

```
def _responses_usage(raw: ResponseUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: Turns usage data from the Responses API into UFO's standard Usage format. It separates normal input tokens, cached input tokens, cache-write tokens, and output tokens.

**Data flow**: It receives a raw Responses usage object and a flag saying whether cache writes should be counted as separately priced. It reads cached and cache-write token counts, checks that they do not exceed total input tokens, then returns a Usage object with the counts split into UFO's categories.

**Call relations**: _ResponsesStream.accept calls this when a completed or failed Responses stream reports usage. _ResponsesStream._record_incomplete also calls it when an incomplete response still includes usage.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 2 (_record_incomplete, accept); 1 external calls (__init__).


##### `_chat_usage`  (lines 140–154)

```
def _chat_usage(raw: openai.types.CompletionUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: Turns usage data from the Chat Completions API into UFO's standard Usage format. It performs the same accounting as the Responses path, but reads the older Chat usage fields.

**Data flow**: It receives a raw Chat Completions usage object and a cache-pricing flag. It extracts cached and cache-write prompt tokens, validates that the counts make sense, subtracts them from normal input tokens, and returns a Usage object.

**Call relations**: _ChatStream.accept calls this when a streamed chat chunk includes final usage information.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 1 (accept); 1 external calls (__init__).


##### `openai_sdk_client`  (lines 157–170)

```
def openai_sdk_client(api_key: str, base_url: str | None=None, default_headers: dict[str, str] | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Creates an asynchronous OpenAI SDK client with UFO's chosen timeout and with SDK-level retries turned off. UFO does its own retrying so it can make careful decisions around streamed partial output.

**Data flow**: It receives an API key, and optionally a base URL and default headers. It passes them into the OpenAI SDK along with a fixed timeout and max_retries set to zero, then returns the ready client.

**Call relations**: codex_sdk_client uses this to build the special ChatGPT Codex client. Other provider integrations can also use it when they speak OpenAI-compatible HTTP but live at a different base URL.

*Call graph*: called by 1 (codex_sdk_client); 1 external calls (AsyncOpenAI).


##### `chatgpt_account_id`  (lines 173–186)

```
def chatgpt_account_id(credential: str) -> str | None
```

**Purpose**: Detects whether a credential is a ChatGPT account token and, if so, extracts the ChatGPT account id from it. This decides whether UFO should use the ChatGPT Codex backend instead of api.openai.com.

**Data flow**: It receives a credential string. If the string does not look like a JWT, meaning a dot-separated signed token, it returns None. If it does look like one, it decodes the payload, reads the expected auth claims, and returns the account id only if it is a non-empty string.

**Call relations**: This function is a credential classifier. Code that builds clients can use its result to choose codex_sdk_client for ChatGPT account credentials, or the normal OpenAI client path for platform API keys.

*Call graph*: 2 external calls (urlsafe_b64decode, loads).


##### `codex_sdk_client`  (lines 189–205)

```
def codex_sdk_client(credential: str, account: str) -> openai.AsyncOpenAI
```

**Purpose**: Builds an OpenAI SDK client aimed at the ChatGPT Codex backend. This backend needs extra headers that identify the account, app origin, beta Responses support, and streaming response type.

**Data flow**: It receives the credential and the ChatGPT account id. It creates the required header set and passes the credential, Codex base URL, and headers to openai_sdk_client, returning the configured SDK client.

**Call relations**: It delegates the actual SDK construction to openai_sdk_client. It exists because ChatGPT account tokens are served by a different backend than ordinary OpenAI API keys.

*Call graph*: calls 1 internal fn (openai_sdk_client).


##### `_status_retry_wait`  (lines 208–214)

```
def _status_retry_wait(error: openai.APIStatusError, delay: float) -> float
```

**Purpose**: Chooses how long to wait before retrying an HTTP status error. It respects the provider's retry-after header when present, but never waits less than the current backoff delay.

**Data flow**: It receives an OpenAI status error and the current retry delay. It tries to parse the retry-after response header as a number of seconds, falls back to the current delay if parsing fails, and returns the larger wait time.

**Call relations**: _OpenAIRetry.status calls this when a rate limit or server error is retryable. This keeps retry timing aligned with provider guidance when the provider gives one.

*Call graph*: called by 1 (status).


##### `_openai_image`  (lines 217–221)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts UFO's image data into the image-url shape OpenAI accepts. The image is embedded as a data URL, which is like putting the image bytes directly inside the message.

**Data flow**: It receives an ImageSource containing a media type and base64 image data. It returns a small dictionary with OpenAI's image_url fields filled in.

**Call relations**: openai_messages uses this for image content in chat messages. _openai_tool_result uses it when a tool result contains images that must be moved into a user message.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 224–240)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Splits a UFO tool result into text and images for the Chat Completions API. This is needed because OpenAI tool messages can carry text, but images have to be sent separately as user message content.

**Data flow**: It receives either a plain string result or a tuple of text and image blocks. It gathers all text into one string, converts images with _openai_image, and returns both the text and the list of image parts.

**Call relations**: openai_messages calls this while translating prior tool results into chat messages. It gives openai_messages the pieces needed to create both the tool message and any follow-up image message.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 243–302)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Translates UFO's conversation history into the message list expected by OpenAI's Chat Completions API. It keeps text, images, tool calls, and tool results, but drops reasoning blocks because this API has nowhere to put them.

**Data flow**: It receives the system prompt and the stored UFO messages. It trims images as needed, walks each message block, converts images and tool calls to OpenAI shapes, turns tool results into tool messages, lifts tool-result images into user messages, and returns a list of OpenAI-style message dictionaries.

**Call relations**: OpenAIClient._chat_kwargs calls this when building a Chat Completions request. It is the main adapter from UFO's internal message model to the chat API's request body.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 305–406)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: Translates UFO's conversation history into the input-item format expected by OpenAI's Responses API. Unlike the chat path, it can preserve OpenAI reasoning items so the model can continue a tool round with its prior hidden reasoning context.

**Data flow**: It receives UFO messages. It trims images, then turns plain messages, text blocks, image blocks, tool calls, tool outputs, and reasoning items into the corresponding Responses API input items. It drops reasoning formats from other providers that this API cannot replay.

**Call relations**: responses_request calls this to fill the input field of a Responses request. It is the Responses API equivalent of openai_messages, but with extra support for reasoning and function-call output items.

*Call graph*: called by 1 (responses_request); 13 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, ResponseOutputTextParam (+3 more)).


##### `responses_request`  (lines 409–448)

```
def responses_request(request: ModelRequest, effort: OpenAIEffort, codex: bool=False) -> dict[str, Any]
```

**Purpose**: Builds the full request body for OpenAI's Responses API. It includes the model, instructions, input history, streaming choice, reasoning settings, tools, and tool-choice rules.

**Data flow**: It receives a UFO ModelRequest, a resolved reasoning effort, and a flag saying whether the target is the Codex backend. It converts messages through responses_input, adds max output tokens except for Codex, asks for encrypted reasoning content, disables provider-side storage, adds tools if present, and returns the request dictionary.

**Call relations**: OpenAIClient._complete_responses calls this just before sending the provider request. It is the final packing step for the Responses streaming path.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `_OpenAIRetry.transport`  (lines 458–493)

```
async def transport(self, error: Exception, yielded: bool) -> _OpenAIRetry
```

**Purpose**: Decides what to do after a network or transport failure, such as a timeout or dropped connection. It retries only when it is still safe to do so.

**Data flow**: It receives the exception and a flag saying whether any visible model output has already been yielded. If output has already appeared, it raises a stream-interrupted error. If not and retry attempts remain, it logs and counts the retry, sleeps, and returns a new retry state with a larger delay. If attempts are exhausted, it re-raises the original error.

**Call relations**: Both streaming completion methods use this in their transport-error catch blocks. It hands back updated retry state so the outer loop can try the same model request again.

*Call graph*: calls 1 internal fn (__init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_OpenAIRetry.status`  (lines 495–543)

```
async def status(self, error: openai.APIStatusError, yielded: bool) -> _OpenAIRetry
```

**Purpose**: Decides what to do after the provider returns an HTTP error status. It treats rejected keys, rate limits, server failures, and mid-stream failures differently so callers get useful errors.

**Data flow**: It receives an OpenAI status error and a flag saying whether output was already yielded. A rejected key becomes the model spec's credential error. Retryable rate limits or server errors are retried before output starts. Mid-stream retryable errors become stream interruptions. Exhausted rate limits become the spec's rate-limit error, and other non-retryable errors are raised.

**Call relations**: Both streaming paths call this after status errors. It uses _status_retry_wait to decide sleep time and returns updated retry state when another attempt should be made.

*Call graph*: calls 2 internal fn (_status_retry_wait, __init__); 4 external calls (sleep, replace, emit_metric, log).


##### `_ChatStream.__init__`  (lines 547–552)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates the state tracker for one Chat Completions stream. It remembers whether anything useful was emitted, maps tool-call indexes to ids, and stores final usage and finish reason.

**Data flow**: It receives a flag saying whether cache-write tokens should be priced separately. It initializes empty tracking fields for yielded output, tool calls, usage, and finish reason.

**Call relations**: OpenAIClient._complete_chat creates one _ChatStream for each provider attempt. The completion loop then feeds every incoming chat chunk into that state object.

*Call graph*: called by 1 (_complete_chat).


##### `_ChatStream.accept`  (lines 554–582)

```
def accept(self, chunk: ChatCompletionChunk) -> tuple[ModelEvent, ...]
```

**Purpose**: Consumes one streamed Chat Completions chunk and turns it into UFO model events. These events are the live pieces the rest of the harness understands.

**Data flow**: It receives one OpenAI chat chunk. It records usage if present, records the finish reason if present, converts text deltas into TextDelta events, starts tool calls when first seen, converts tool argument fragments into ToolCallDelta events, updates its yielded flag, and returns the events from that chunk.

**Call relations**: OpenAIClient._complete_chat calls this inside the stream loop. It uses _chat_usage for final token accounting and creates the event objects that are yielded to the caller.

*Call graph*: calls 1 internal fn (_chat_usage); 3 external calls (__init__, __init__, __init__).


##### `_ChatStream.finish`  (lines 584–599)

```
def finish(self) -> tuple[Usage, Exception | None]
```

**Purpose**: Finishes a Chat Completions stream by returning its final usage and any terminal error. It detects when OpenAI stopped only because the token budget was reached.

**Data flow**: It reads the stored finish reason and usage. If finish_reason is length, it prepares a ModelResponseTruncated error. If usage is missing, it raises either that truncation error or a missing-usage error. Otherwise it returns the usage plus the optional truncation error.

**Call relations**: OpenAIClient._complete_chat calls this after the async stream ends. The caller then yields usage and either returns normally or raises the terminal error.

*Call graph*: 1 external calls (__init__).


##### `_ResponsesStream.__init__`  (lines 603–610)

```
def __init__(self, cache_write_30m_priced: bool) -> None
```

**Purpose**: Creates the state tracker for one Responses API stream. It remembers emitted output, tool-call ids, reasoning items, final usage, and any terminal error.

**Data flow**: It receives a cache-pricing flag. It initializes empty collections for tool-call tracking and reasoning, clears usage and terminal error, and marks that no visible output has been yielded yet.

**Call relations**: OpenAIClient._complete_responses creates one _ResponsesStream for each provider attempt. The streaming loop then feeds all Responses events into it.

*Call graph*: called by 1 (_complete_responses).


##### `_ResponsesStream.accept`  (lines 612–651)

```
def accept(self, event: ResponseStreamEvent) -> tuple[ModelEvent, ...]
```

**Purpose**: Consumes one Responses API stream event and converts it into UFO events or stored final state. It understands text, tool calls, reasoning completion, refusal, completion, failure, and incomplete-response events.

**Data flow**: It receives a Responses stream event. Depending on the event type, it may emit a TextDelta, ToolCallStart, or ToolCallDelta; store reasoning; convert usage; or record a terminal refusal, truncation, or failure error. It updates whether visible output has been yielded and returns any emitted events.

**Call relations**: OpenAIClient._complete_responses calls this for each event from the provider. It delegates detailed reasoning storage to _record_reasoning and incomplete-response handling to _record_incomplete.

*Call graph*: calls 3 internal fn (_record_incomplete, _record_reasoning, _responses_usage); 4 external calls (__init__, __init__, __init__, __init__).


##### `_ResponsesStream._record_reasoning`  (lines 653–662)

```
def _record_reasoning(self, item: ResponseReasoningItem) -> None
```

**Purpose**: Stores a completed OpenAI reasoning item so it can be replayed in a future request. This is important when the model makes tool calls and later needs its encrypted reasoning context back.

**Data flow**: It receives a completed reasoning item from the Responses stream. It checks that encrypted content is present, copies the id, encrypted content, and summary text into a UFO ReasoningItemBlock, and appends it to the stream state's reasoning list.

**Call relations**: _ResponsesStream.accept calls this when it sees a reasoning item done event. OpenAIClient._complete_responses later yields the collected reasoning blocks just before final usage.

*Call graph*: called by 1 (accept); 1 external calls (__init__).


##### `_ResponsesStream._record_incomplete`  (lines 664–675)

```
def _record_incomplete(self, response: Any) -> None
```

**Purpose**: Records why a Responses API stream ended incompletely. It turns provider reasons into UFO errors such as truncation or refusal when possible.

**Data flow**: It receives an incomplete response object. If usage is present, it converts and stores it. It then reads the incomplete reason: max_output_tokens becomes ModelResponseTruncated, content_filter becomes ModelRefusal, and anything else becomes a general runtime error.

**Call relations**: _ResponsesStream.accept calls this when the provider sends an incomplete-response event. Its stored terminal error is later returned or raised by _ResponsesStream.finish.

*Call graph*: calls 1 internal fn (_responses_usage); called by 1 (accept); 2 external calls (__init__, __init__).


##### `_ResponsesStream.finish`  (lines 677–685)

```
def finish(self) -> tuple[Usage, Exception | None]
```

**Purpose**: Finishes a Responses API stream by returning final usage and any terminal error. It preserves special error types like refusal and truncation so the engine can recover or report them correctly.

**Data flow**: It reads the stored usage and terminal error. If usage is missing, it raises the terminal error if one exists, or a missing-usage error otherwise. If usage exists, it returns usage together with the optional terminal error.

**Call relations**: OpenAIClient._complete_responses calls this after the provider stream ends. The caller uses its result to decide whether to yield usage, yield reasoning, return, or raise an error.


##### `OpenAIClient.complete`  (lines 700–703)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Chooses which OpenAI-style API surface to use for a model request. It hides the difference between Chat Completions and Responses from the rest of the harness.

**Data flow**: It receives a ModelRequest. If this client is for Codex or the model spec says to use Responses, it returns the Responses streaming iterator; otherwise it returns the Chat Completions streaming iterator.

**Call relations**: This is the public entry on OpenAIClient. Callers ask it for streamed model events, and it delegates to _complete_chat or _complete_responses based on client and model configuration.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._reasoning_effort`  (lines 705–724)

```
def _reasoning_effort(self, request: ModelRequest) -> OpenAIEffort
```

**Purpose**: Figures out what reasoning setting, if any, should be sent to OpenAI for this request. Reasoning means the model may spend hidden work tokens before answering; this setting controls that behavior where the provider supports it.

**Data flow**: It receives a ModelRequest and reads the model spec's reasoning rules. It asks the spec for the wire-level reasoning value, converts UFO's off setting into OpenAI's none value, omits auto or unsupported values, and raises if the caller asked to turn reasoning off in a tool request where the provider gives no legal way to say that.

**Call relations**: _chat_kwargs uses this while building Chat Completions parameters. _complete_responses uses it before building a Responses request.

*Call graph*: called by 2 (_chat_kwargs, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 726–755)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the keyword arguments passed to the OpenAI Chat Completions create call. It packages the model, messages, token budget, streaming options, reasoning effort, tools, and tool-choice rule.

**Data flow**: It receives a ModelRequest. It converts messages with openai_messages, adds max completion tokens and usage-in-stream options, asks _reasoning_effort for any reasoning parameter, converts tools into OpenAI function definitions, and returns the finished argument dictionary.

**Call relations**: OpenAIClient._complete_chat calls this immediately before starting a chat stream. It is the final request-building step for the Chat Completions path.

*Call graph*: calls 2 internal fn (_reasoning_effort, openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 757–831)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a full streaming Chat Completions request and yields UFO model events as they arrive. It also retries safe provider failures and emits final usage as the last successful event.

**Data flow**: It receives a ModelRequest. It builds chat arguments, opens an OpenAI stream, yields a ModelStreamStart when the stream begins, feeds chunks into _ChatStream, yields text and tool-call events, handles retryable errors before visible output, treats mid-stream failures as interruptions, checks final stream state, retries empty completions a few times, and finally yields usage or raises the terminal error.

**Call relations**: OpenAIClient.complete calls this for models using Chat Completions. It relies on _chat_kwargs for request construction, _ChatStream for chunk translation, and _OpenAIRetry for retry decisions.

*Call graph*: calls 3 internal fn (_chat_kwargs, __init__, __init__); called by 1 (complete); 3 external calls (__init__, __init__, emit_metric).


##### `OpenAIClient._complete_responses`  (lines 833–900)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a full streaming Responses API request and yields UFO model events as they arrive. It is the Responses counterpart to the chat path, with extra support for preserving reasoning items.

**Data flow**: It receives a ModelRequest. It resolves reasoning effort, builds a Responses request, opens the provider stream, yields ModelStreamStart, feeds events into _ResponsesStream, yields live text and tool-call events, retries safe failures, treats mid-stream provider errors as interruptions, retries empty completions, then yields collected reasoning blocks followed by final usage.

**Call relations**: OpenAIClient.complete calls this for Codex clients or models whose spec selects the Responses API. It uses responses_request for request construction, _ResponsesStream for event translation, and _OpenAIRetry for retry behavior.

*Call graph*: calls 4 internal fn (_reasoning_effort, __init__, responses_request, __init__); called by 1 (complete); 3 external calls (__init__, __init__, emit_metric).


### `core/src/ufo/harness/models/grant.py`

`domain_logic` · `credential use and refresh during provider calls`

This file exists because a connected provider account is not the same as a permanent API key. An access token expires. The refresh token is the “ticket” used to buy the next access token. If the system stored only the access token, it might still look connected after it had stopped working.

The main object is `Grant`, a small data record containing the current access token, the refresh token, and the expiry time. It also records whether another task has temporarily claimed the right to refresh it, because refresh tokens may be single-use. This is like putting a “someone is at the counter renewing this pass” note on a shared membership card, so two people do not try to renew it at once.

The file also defines which OAuth client ID and token endpoint to use for OpenAI and Anthropic. OAuth is the common web sign-in system where one service grants another limited access. The client ID can come from the environment, so different deployments can identify themselves differently.

When a token response comes back from a provider, `granted` checks that it contains all required pieces before accepting it. When a stored value is read, `read_grant` decides whether it is really a grant JSON object or just a plain API key. When a grant needs renewal, `refreshed` calls the provider’s token endpoint and returns a completely new `Grant`, because providers can rotate both access and refresh tokens.

#### Function details

##### `openai_client_id`  (lines 36–39)

```
def openai_client_id() -> str
```

**Purpose**: Returns the client ID this deployment should present to OpenAI when refreshing or redeeming an OAuth grant. It uses a deployment-specific environment value if one is set, otherwise it falls back to the public OpenAI Codex client ID.

**Data flow**: It reads the `UFO_OPENAI_OAUTH_CLIENT_ID` environment variable. If that value exists, it returns it; if not, it returns the built-in public OpenAI client ID. It does not change anything.

**Call relations**: The OpenAI entry in `GRANT_CLIENTS` stores this function instead of a fixed string. Later, when `refreshed` needs to renew an OpenAI grant, it calls through that stored function so the refresh uses the same kind of client identity that the sign-in flow used.


##### `anthropic_client_id`  (lines 42–45)

```
def anthropic_client_id() -> str
```

**Purpose**: Returns the client ID this deployment should present to Anthropic when refreshing or redeeming an OAuth grant. It uses a deployment-specific environment value if one is set, otherwise it falls back to the public Claude client ID.

**Data flow**: It reads the `UFO_ANTHROPIC_OAUTH_CLIENT_ID` environment variable. If that value exists, it returns it; otherwise it returns the built-in Anthropic public client ID. It only reports the chosen value and does not modify state.

**Call relations**: The Anthropic entry in `GRANT_CLIENTS` points to this function. When `refreshed` renews an Anthropic grant, it asks this function for the client ID to send to Anthropic’s token endpoint.


##### `GrantRefusedRefresh.__init__`  (lines 71–73)

```
def __init__(self, slot: str) -> None
```

**Purpose**: Builds a clear error for the case where a provider will not exchange a refresh token for a new grant. This tells the rest of the system that the user’s connected account can no longer be repaired automatically and must be connected again.

**Data flow**: It receives the slot name, such as the OpenAI or Anthropic credential slot. It creates an exception message naming that slot and stores the slot on the exception object, so later code can know which connected account failed.

**Call relations**: `refreshed` raises this error whenever the provider cannot be reached, rejects the refresh, or returns an unusable answer. `WorkspaceScope._refreshed_credential` can also raise it when refreshing a workspace credential fails, so callers get one consistent signal for “this grant cannot be refreshed.”

*Call graph*: called by 2 (refreshed, _refreshed_credential).


##### `Grant.spent`  (lines 88–89)

```
def spent(self) -> bool
```

**Purpose**: Says whether this grant should be treated as used up. It does not wait until the exact expiry second; it marks the grant spent several minutes early so a long-running provider call does not die halfway through.

**Data flow**: It reads the grant’s `expires_at` time and the current clock time. It subtracts the safety margin from the expiry time, compares that with now, and returns `true` if the grant is close enough to expiry that it should be refreshed.

**Call relations**: This property is meant for code deciding whether it can safely use a grant or should refresh it first. Internally it depends only on the current time, so it can be checked whenever a credential is about to be spent.

*Call graph*: 1 external calls (time).


##### `Grant.claimed`  (lines 92–93)

```
def claimed(self) -> bool
```

**Purpose**: Says whether another caller currently has the right to refresh this grant. This helps prevent two tasks from spending the same one-time refresh token at the same time.

**Data flow**: It reads `refreshing_until` from the grant and compares it with the current clock time. If the current time is still before that lease deadline, it returns `true`; otherwise it returns `false`.

**Call relations**: This property is used by refresh coordination code to decide whether to wait, retry, or take over after an old refresh claim has expired. It is the simple clock check behind the “someone else is renewing this” marker.

*Call graph*: 1 external calls (time).


##### `Grant.stored`  (lines 95–96)

```
def stored(self) -> str
```

**Purpose**: Turns a `Grant` object into the string form that can be saved in a credential slot. This keeps the access token, refresh token, expiry time, and refresh claim together instead of storing only one piece.

**Data flow**: It takes the fields already present on the `Grant` object and serializes them as JSON text. The returned string can be written to storage; the object itself is not changed.

**Call relations**: Other parts of the system can call this before saving a connected account grant. Its counterpart in this file is `read_grant`, which tries to turn a stored JSON string back into a `Grant`.


##### `granted`  (lines 99–112)

```
def granted(payload: dict[str, object]) -> Grant | None
```

**Purpose**: Checks a provider token response and turns it into a valid `Grant` only if all required pieces are present. It refuses incomplete responses because an access token without a refresh token would work briefly and then leave the account stuck.

**Data flow**: It receives a dictionary decoded from a provider response. It looks for a non-empty `access_token`, a non-empty `refresh_token`, and a numeric `expires_in` value. If anything is missing or the wrong type, it returns `None`; otherwise it creates a `Grant` whose expiry time is the current time plus the provider’s lifetime value.

**Call relations**: `refreshed` calls this after receiving JSON from a provider’s token endpoint. `granted` is the gatekeeper that decides whether the provider’s answer is complete enough to store and use.

*Call graph*: called by 1 (refreshed); 2 external calls (__init__, time).


##### `read_grant`  (lines 115–127)

```
def read_grant(stored: str) -> Grant | None
```

**Purpose**: Tries to read a stored credential string as a `Grant`. If the string is not grant-shaped JSON, it returns `None`, which lets the system treat it as a plain API key instead.

**Data flow**: It receives a stored string. First it tries to parse it as JSON. If parsing fails, or the parsed value is not an object, or the object cannot be validated as a `Grant`, it returns `None`. If validation succeeds, it returns the `Grant` object.

**Call relations**: This function sits at the boundary between saved credential text and usable grant data. It pairs with `Grant.stored`: one writes the JSON form, the other safely recognizes and rebuilds it later.

*Call graph*: 1 external calls (loads).


##### `refreshed`  (lines 130–155)

```
async def refreshed(grant: Grant, slot: str) -> Grant
```

**Purpose**: Uses a grant’s refresh token to ask OpenAI or Anthropic for a new grant. This is what keeps a connected account working after the short-lived access token expires.

**Data flow**: It receives the old `Grant` and the credential slot name. From the slot it finds the right token endpoint and client-ID function. It sends an HTTP POST request containing the refresh token, the refresh-token grant type, and the client ID. If the provider cannot be reached, rejects the request, returns unreadable JSON, or omits required fields, it raises `GrantRefusedRefresh`. If everything is valid, it returns a new `Grant` containing the newly issued access and refresh tokens.

**Call relations**: This is the file’s main renewal path. It calls the slot-specific client-ID function through `GRANT_CLIENTS`, uses `httpx.AsyncClient` to contact the provider over HTTP, hands the provider’s JSON answer to `granted`, and raises `GrantRefusedRefresh` whenever the refresh cannot produce a usable replacement.

*Call graph*: calls 2 internal fn (__init__, granted); 1 external calls (AsyncClient).


### `core/src/ufo/harness/models/interface.py`

`data_model` · `request preparation and model streaming`

This file is the contract between the UFO harness and any large language model service it talks to. Without it, each provider client would invent its own request and response shapes, and the rest of the system would have to know provider-specific details. Instead, this file gives everyone one shared set of message types.

Most of the file is made of small data shapes. A user or assistant message can contain plain text, images, tool calls, tool results, or model reasoning blocks. The reasoning blocks are kept carefully because some providers require the exact same hidden reasoning data to be sent back on the next turn. Tool results can also carry images, such as screenshots, not just text.

`ModelRequest` is the main package sent to a model client: it includes the model name, system prompt, conversation messages, token budget, tools, optional forced tool choice, reasoning setting, and cache/session hints. `ModelClient` is a protocol, meaning any provider-specific client counts as a model client if it offers the expected `complete` method.

The file also protects provider limits around images. `trim_images` keeps the newest images while replacing older or oversized ones with a clear text note. `omit_images` replaces all images when the chosen model only accepts text. This is like packing photos into an email with strict limits: keep the most recent useful ones, and leave a note where the others were removed.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool`  (lines 160–165)

```
def _forced_choice_names_an_offered_tool(self) -> 'ModelRequest'
```

**Purpose**: This validation step makes sure a request cannot force the model to use a tool that was not actually offered. It prevents a confusing or impossible model request before it reaches a provider.

**Data flow**: It reads the `tool_choice` field and the list of offered `tools` inside the `ModelRequest`. If no tool is forced, it leaves the request unchanged. If a tool is forced, it checks that one offered tool has the same name; otherwise it raises an error instead of producing an invalid request.

**Call relations**: This runs automatically when a `ModelRequest` is created or validated by Pydantic, the library used here to check structured data. It does not call other project functions; it acts as a gatekeeper before any `ModelClient.complete` implementation receives the request.


##### `ModelClient.complete`  (lines 219–219)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the shared promise every model client must keep: given a `ModelRequest`, stream back model events. Provider-specific clients implement this so the rest of the system can ask for completions without caring whether the backend is Anthropic, OpenAI, or another service.

**Data flow**: A complete `ModelRequest` goes in. The implementation sends that request to its provider and yields a stream of events such as text pieces, tool-call starts, tool-call JSON fragments, reasoning blocks, usage information, or stream-start markers. The protocol itself only defines the shape; it does not do the network work.

**Call relations**: Other parts of the harness call `complete` when they need a model answer. This file defines the interface, while concrete provider clients elsewhere supply the real behavior behind it.


##### `trim_images`  (lines 227–258)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares messages for image-capable model providers by enforcing shared image limits. It keeps the newest images that fit the per-message, per-request, and byte-size budgets, and replaces removed images with a short note.

**Data flow**: It receives a tuple of conversation `Message` objects. First it asks `_image_positions` where all inline images are. It chooses which image positions survive based on provider count limits, then uses `_image_data_len` to apply the total image-data budget from newest to oldest. Finally it calls `_trim_message` for each message that may need replacements, returning a new tuple of messages where dropped images are replaced by text markers. If nothing needs changing, it returns the original messages.

**Call relations**: This function is used before a provider client translates the shared message format into that provider's own request format. It coordinates the helper functions: `_image_positions` finds images, `_image_data_len` measures them, and `_trim_message` rewrites the affected messages.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `omit_images`  (lines 261–269)

```
def omit_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares messages for a text-only model by replacing every image with an explicit text note. That way the model receives a valid text request and can still see that an image was present but unavailable.

**Data flow**: It receives a tuple of `Message` objects. It uses `_image_positions` to find every top-level or tool-result image. If there are no images, it returns the original messages. Otherwise it calls `_trim_message` for each message, replacing all found images with the text marker for unsupported images, and returns the rewritten tuple.

**Call relations**: Provider selection or request preparation can call this when the chosen model cannot accept image input. It shares the same image-finding and message-rewriting helpers as `trim_images`, but it drops all images instead of keeping some.

*Call graph*: calls 2 internal fn (_image_positions, _trim_message).


##### `_image_data_len`  (lines 272–283)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This helper tells `trim_images` how large one image's base64 data is. The size is used to stay under the provider's total image-data limit.

**Data flow**: It receives the full message tuple and one image position, which identifies a message, a content block, and possibly a nested item inside a tool result. It looks up that exact image and returns the length of its encoded image data. If the position does not point to an image, it raises an internal error because the caller gave it an impossible address.

**Call relations**: `trim_images` calls this while deciding which kept images still fit inside the request-wide byte budget. It relies on positions produced by `_image_positions`, so in normal use those positions should always point to real images.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 286–306)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This helper scans a conversation and records where every image lives. It understands both images placed directly in messages and images nested inside tool results.

**Data flow**: It receives a tuple of `Message` objects. For each message with structured content, it walks through the content blocks. When it finds a direct `ImageBlock`, it records its message and block location; when it finds a `ToolResultBlock` with multiple parts, it records the location of each image inside that result. It returns the list of positions from oldest message to newest.

**Call relations**: `trim_images` calls this to decide which images to keep or drop, and `omit_images` calls it to find all images that must be replaced. The returned positions are then passed to `_image_data_len` and `_trim_message`.

*Call graph*: called by 2 (omit_images, trim_images).


##### `_trim_message`  (lines 309–336)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]], replacement: str) -> Message
```

**Purpose**: This helper rewrites one message by replacing selected images with a plain text placeholder. It leaves all other content untouched.

**Data flow**: It receives a message index, one `Message`, a set of image positions to drop, and the replacement text to insert. If the message is plain text, it returns it unchanged. If the message has structured blocks, it walks through them: a dropped top-level image becomes a new `TextBlock`, and a dropped image inside a tool result becomes a `TextBlock` inside a copied tool-result block. It returns a copied `Message` with the updated content.

**Call relations**: `trim_images` and `omit_images` call this after they decide which image positions should disappear. It uses `TextBlock` to create the visible placeholder and `Message.model_copy` to preserve the original message while changing only its content.

*Call graph*: called by 2 (omit_images, trim_images); 2 external calls (__init__, model_copy).


### Usage pricing and ledgers
Turns model and runtime usage into billable cost while enforcing prepaid balance and spend constraints.

### `core/src/ufo/runtime/billing/accounting.py`

`domain_logic` · `cross-cutting: turn admission, per-round billing, background billing, reporting, and usage export`

This file answers a simple but important question: when UFO does work for a workspace, who pays, how much, and is the workspace still allowed to keep going? It writes usage into a ledger, which is like a bank statement for compute: token counts, media generations, network egress counts, prices, models, and timestamps all become durable rows in the database.

The file is careful about retries. A turn may be replayed after a crash, so token usage for a single run attempt is treated as cumulative: if the same attempt reports the same usage again, nothing is charged twice; if it reports more usage, only the extra cost is debited. Separate attempts add together.

It also distinguishes platform-paid work from bring-your-own-key work. If a workspace used its own provider key, the ledger can still record the value of the work, but the prepaid balance is not debited for that model call.

On top of recording, this file enforces gates. `SpendEvaluator` checks rolling spend caps for a workspace, member, or agent. `BalanceGate` checks whether prepaid balance is high enough to start or continue work. Finally, `SpendRollup` reads the ledger back into human reports, and the export functions freeze usage deltas for external billing consumers.

#### Function details

##### `OffTurnSpendRefused.__init__`  (lines 64–67)

```
def __init__(self, outcome: SpendOutcome, message: str, model: str) -> None
```

**Purpose**: Creates an error used when a model call made outside a normal turn is blocked by billing rules. It stores both the decision, such as park or reject, and the model that was refused.

**Data flow**: It receives a spend outcome, a message for the caller, and a model name. It saves the outcome and model on the error object, then passes the message to the normal exception machinery so it can be shown or logged.

**Call relations**: The off-turn model access path raises this when billing gates refuse a model call. The stored model matters because one model may be blocked while another is allowed if the workspace owns a key for only some providers.

*Call graph*: called by 1 (turn).


##### `applicable_caps_absent`  (lines 70–76)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: Quickly answers whether the system recently learned that no spend cap applies to a specific workspace/member/agent combination. This avoids unnecessary database reads in the common case where no caps are configured.

**Data flow**: It takes the workspace, optional member, and agent identifiers. It looks in a short-lived in-memory cache and returns true only if the matching entry exists and has not expired.

**Call relations**: This is a fast path that callers can use before doing cap enforcement. The matching cache entries are written by `SpendEvaluator.decide` through `_note_absent_caps` when a full database check finds no relevant caps.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 79–88)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID | None]) -> None
```

**Purpose**: Remembers for a few seconds that a particular workspace/member/agent combination has no spend caps. This keeps cap checking cheap without making the cache a source of permanent truth.

**Data flow**: It receives a cache key made from workspace, member, and agent identifiers. It removes expired entries if the cache is full, then stores a new expiry time for that key.

**Call relations**: `SpendEvaluator.decide` calls this after it has checked the database and found no applicable caps. Later, `applicable_caps_absent` can use the note to skip another database round trip.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `_total_tokens`  (lines 91–99)

```
def _total_tokens(usage: Usage) -> int
```

**Purpose**: Adds up all token categories in a usage record into one total. Billing needs this single total for ledger amounts and reports.

**Data flow**: It receives a `Usage` object containing input, output, cache-read, and cache-write token counts. It sums those fields and returns the combined token count.

**Call relations**: Token billing writers call this before deciding whether there is anything to record. It feeds `record_turn_usage`, `record_workspace_usage`, and `record_sandbox_tokens`.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `_prompt_tokens`  (lines 102–111)

```
def _prompt_tokens(usage: Usage) -> int
```

**Purpose**: Counts the tokens that made up the prompt the model read, including cached prompt tokens. This is used to calculate what share of a prompt came from cache.

**Data flow**: It receives a `Usage` object and adds input tokens plus cache-read and cache-write tokens, but not output tokens. It returns that prompt-side total.

**Call relations**: The token ledger writers store this value beside the total token count. Later reporting, especially `read_turn_cost`, can compute cache percentage from the same ledger data that holds the cost.

*Call graph*: called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `workspace_owns_the_key`  (lines 114–137)

```
async def workspace_owns_the_key(connection: AsyncConnection, workspace_id: UUID, key_slot: str | None) -> bool
```

**Purpose**: Checks whether a workspace has stored its own provider credential for a given key slot. This prevents the platform from charging prepaid balance when the workspace is already paying the provider directly.

**Data flow**: It receives a database connection, workspace id, and key slot name. If the slot is missing it returns false; otherwise it asks the credential table whether that workspace has a matching credential row.

**Call relations**: `BalanceGate._workspace_serves_itself` uses this as the final check for the own-key exemption. It deliberately checks workspace-owned credentials, not member credentials.

*Call graph*: called by 1 (_workspace_serves_itself); 3 external calls (exists, scalar, select).


##### `record_turn_usage`  (lines 140–275)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Records and charges token usage for one turn run attempt. It is built to survive workflow replay without double-charging the same attempt.

**Data flow**: It receives the workspace, turn, model, usage counters, attempt id, pricing table, and whether the call used the workspace's own key. It totals and prices the usage, compares it with any existing ledger row for the same attempt, debits only the new charge when appropriate, and inserts or updates the ledger row.

**Call relations**: Turn execution code calls this as model usage becomes known. It uses `_total_tokens` and `_prompt_tokens` for counts, pricing to compute cost, and balance debit to take money only after replay safety checks pass.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 7 external calls (__init__, execute, insert, select, update, debit, ledger_id_for).


##### `read_turn_cost`  (lines 289–319)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID, dimension: str) -> TurnCost | None
```

**Purpose**: Reads what a turn spent for a chosen ledger dimension, such as host-side model tokens or sandbox model tokens. It returns a compact cost summary for display or terminal reporting.

**Data flow**: It receives a connection, turn id, and dimension name. It sums matching ledger rows across all attempts, calculates cache percentage from prompt and cache-read tokens, and returns a `TurnCost` object or nothing if the turn has no such spend.

**Call relations**: This is a reader over rows written by functions such as `record_turn_usage` and `record_sandbox_tokens`. It lets later turn-summary code avoid reconstructing cost from in-memory state.

*Call graph*: 3 external calls (__init__, execute, select).


##### `record_workspace_usage`  (lines 322–371)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Records token usage for a workspace-level background job that is not attached to a turn. This lets scheduled or off-turn work count toward workspace spend without pretending it belongs to a member or agent.

**Data flow**: It receives workspace id, model, usage, pricing, and whether the workspace used its own key. It totals and prices the usage, debits the workspace unless own-key billing applies, and inserts a fresh ledger row with no turn id.

**Call relations**: Background job code uses this instead of `record_turn_usage`. It shares token counting and pricing behavior with turn billing, but intentionally leaves member and agent attribution empty.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 4 external calls (execute, insert, debit, uuid4).


##### `record_egress_request`  (lines 374–403)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox network egress requests for a turn. These requests are metered as counts, not charged as money.

**Data flow**: It receives workspace id, turn id, and a request count. It builds the stable ledger id for that turn's egress row, then inserts the row or atomically adds to the existing count.

**Call relations**: The sandbox egress proxy uses this when turn-attached sandbox traffic is flushed. It writes a separate `egress` dimension so it does not mix with token billing.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_probe_egress_request`  (lines 406–432)

```
async def record_probe_egress_request(connection: AsyncConnection, workspace_id: UUID, amount: int=1) -> None
```

**Purpose**: Counts sandbox network egress requests made by off-turn probes. Like turn egress, it records activity but does not charge money.

**Data flow**: It receives workspace id and a request count. It inserts a new ledger row with no turn id, priced at zero, so the count belongs only to the workspace.

**Call relations**: Probe or proxy code uses this for sandbox activity not tied to a turn. Because the row has no turn id, workspace reports can include it while member and agent reports naturally leave it out.

*Call graph*: 3 external calls (execute, insert, uuid4).


##### `record_sandbox_tokens`  (lines 435–511)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Records and charges model calls made from inside a sandbox through the egress proxy. These are separate from the host turn loop's own model calls.

**Data flow**: It receives workspace, turn, model, usage, and pricing. It totals and prices the tokens, debits the workspace, and inserts or atomically adds the usage and cost to the turn's `sandbox_tokens` ledger row.

**Call relations**: The sandbox proxy calls this when in-sandbox model usage is known. It uses the same token helpers and pricing style as host-side token billing, but accumulates under a different ledger dimension.

*Call graph*: calls 3 internal fn (micro_usd, _prompt_tokens, _total_tokens); 3 external calls (execute, debit, ledger_id_for).


##### `record_image_usage`  (lines 514–533)

```
async def record_image_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records and charges generated images for a turn. The caller supplies the price because image providers may bill in units other than text tokens.

**Data flow**: It receives workspace, turn, model, image count, and cost. It passes those values to the shared media writer with the image dimension.

**Call relations**: Provider extensions or image generation code call this after an image generation completes. It delegates the actual ledger update and debit to `_record_media_usage`.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `record_video_usage`  (lines 536–550)

```
async def record_video_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records and charges generated videos for a turn. It mirrors image billing, but under the video dimension.

**Data flow**: It receives workspace, turn, model, video count, and cost. It forwards those values to the shared media writer with the video dimension.

**Call relations**: Video generation code calls this after the provider reports the charge. `_record_media_usage` does the shared database and balance work.

*Call graph*: calls 1 internal fn (_record_media_usage).


##### `_record_media_usage`  (lines 553–594)

```
async def _record_media_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, dimension: str, model: str, amount: int, micro_usd: int) -> None
```

**Purpose**: Shared helper that writes image or video usage into the ledger and debits the workspace. It keeps media billing consistent across different media types.

**Data flow**: It receives the media dimension, amount, price, workspace, turn, and model. It debits the workspace for the new price, then inserts a per-turn ledger row or atomically adds the new amount and cost to the existing one.

**Call relations**: `record_image_usage` and `record_video_usage` both call this. It is the common path for media charges, while egress remains the special dimension that records counts without debiting money.

*Call graph*: called by 2 (record_image_usage, record_video_usage); 3 external calls (execute, debit, ledger_id_for).


##### `mint_usage_exports`  (lines 619–742)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: Freezes newly settled ledger growth into export-intent rows for an external billing consumer. This makes retries safe because the exported delta is saved before delivery.

**Data flow**: It receives a workspace, consumer name, backfill floor time, and a function that maps models to credential slots. It finds ledger rows that have grown beyond what was previously exported, decides whether each token charge was bring-your-own-key, and inserts one immutable export row per new delta.

**Call relations**: A usage export job calls this before reading pending exports. It depends on ledger rows written by the billing recorders and creates stable work for `read_pending_usage_exports` to deliver.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 745–790)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Reads frozen usage-export records that have not yet been acknowledged by an external consumer. It returns exactly what should be sent next.

**Data flow**: It receives workspace id, consumer name, and a limit. It selects unacknowledged export rows, joins their descriptive ledger fields, converts them into `UsageExport` objects, and returns them in mint order.

**Call relations**: The export delivery loop calls this after `mint_usage_exports`. If delivery fails before acknowledgement, the same frozen rows are read again for safe retry.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 793–817)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks exported usage deltas as acknowledged after an outside billing system has accepted them. This removes them from future pending reads.

**Data flow**: It receives the workspace, consumer, and the exact `UsageExport` objects that were delivered. It updates matching export rows by ledger id and starting amount, setting their acknowledged time.

**Call relations**: The export delivery loop calls this only after successful external delivery. It completes the flow started by `mint_usage_exports` and read by `read_pending_usage_exports`.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 820–823)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: Finds workspaces that have ever had ledger activity and are therefore candidates for usage export. It is intentionally broad and cheap.

**Data flow**: It builds a candidate source from distinct workspace ids in the ledger table. The result is a `WorkspaceCandidates` object that a job runner can iterate or schedule from.

**Call relations**: Usage-export orchestration uses this to decide which workspaces to check. Per-workspace reads can still be no-ops if everything is already exported.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 860–875)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: Decides whether proposed work is allowed under configured spend caps. The answer can be allow, park for later, or reject outright.

**Data flow**: It receives a database connection and the cost expected for pending work. It loads applicable caps, caches the no-cap case, sums recent usage for each cap, compares usage plus pending cost to each limit, and returns a `SpendDecision` with a user-facing message if blocked.

**Call relations**: Admission and continuation code use this when they need cap enforcement. It coordinates `_applicable_caps`, `_used_micro_usd`, `_message`, and `_note_absent_caps`.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 877–905)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: Loads the spend caps that apply to this workspace, and optionally to its member and agent. It turns database rows into small `SpendCap` objects.

**Data flow**: It reads cap rows whose scope matches the workspace as a whole, the current member, or the current agent. It returns all matching caps as an immutable tuple.

**Call relations**: `SpendEvaluator.decide` calls this first. If it returns no caps, the decision can allow work and remember the no-cap result briefly.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 907–933)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: Calculates how much money has already been priced inside one cap's rolling time window. This is what gets compared to the cap limit.

**Data flow**: It receives a `SpendCap`, computes the cutoff time from its window length, and sums ledger `priced_micro_usd` rows that belong to the cap's scope. It returns the summed micro-dollar amount.

**Call relations**: `SpendEvaluator.decide` calls this once for each applicable cap. The result is combined with pending spend to decide whether the cap is breached.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 935–946)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: Builds the plain message shown when a spend cap blocks work. It names the tightest breached cap and whether the work was parked or declined.

**Data flow**: It receives the final outcome and the list of breached caps. It picks the cap with the smallest limit, converts micro-dollars to dollars, and returns a readable sentence.

**Call relations**: `SpendEvaluator.decide` calls this only after it has found at least one breach. The returned text travels inside the `SpendDecision`.

*Call graph*: called by 1 (decide).


##### `_token_sum`  (lines 1052–1060)

```
def _token_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a reusable database expression for summing token amounts only from token-related ledger dimensions. Non-token dimensions, such as images or egress, count as zero.

**Data flow**: It takes no runtime input. It returns a SQL expression that adds ledger amounts when the dimension is `tokens` or `sandbox_tokens` and otherwise adds zero.

**Call relations**: Report builders use this inside larger database queries. It is shared by `_usage_details`, `SpendRollup.read`, and `SpendRollup._by_origin`.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_token_cost_sum`  (lines 1063–1075)

```
def _token_cost_sum() -> sa.ColumnElement[int]
```

**Purpose**: Builds a reusable database expression for summing money spent only on token-related dimensions. This separates token cost from total cost.

**Data flow**: It takes no runtime input. It returns a SQL expression that adds `priced_micro_usd` for `tokens` and `sandbox_tokens`, while treating other dimensions as zero.

**Call relations**: Usage report queries call this beside `_token_sum`. It supports token-specific totals in workspace, member, and origin reports.

*Call graph*: called by 3 (_by_origin, read, _usage_details); 1 external calls (case).


##### `_usage_details`  (lines 1078–1202)

```
async def _usage_details(connection: AsyncConnection, source: sa.FromClause, scope: sa.ColumnElement[bool], cutoff: datetime | None, now: datetime) -> UsageDetails
```

**Purpose**: Builds the detailed usage section shared by workspace and member reports. It includes selected-range totals, all-time totals, daily history, model breakdowns, execution breakdowns, and comparison tokens for the previous period.

**Data flow**: It receives a database connection, a table/join source, a scope filter, an optional cutoff, and the current time. It runs several aggregate queries, fills missing daily rows with zeros, normalizes the first-use timestamp, and returns a `UsageDetails` object.

**Call relations**: `SpendRollup.read` and `SpendRollup.read_member` both call this so their reports tell the same story. It relies on `_token_sum` and `_token_cost_sum` to keep token totals consistent.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 2 (read, read_member); 9 external calls (__init__, __init__, __init__, __init__, fromisoformat, date, timedelta, execute, select).


##### `SpendRollup.read`  (lines 1213–1317)

```
async def read(self, connection: AsyncConnection, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads a full workspace spend report for a selected time window or for all time. It turns raw ledger rows into totals by dimension, member, agent, origin, pricing table, and usage detail.

**Data flow**: It receives a connection and optional window length. It computes the time cutoff, runs grouped ledger queries for the workspace, asks `_by_origin` for origin totals, asks `_usage_details` for usage charts, and returns a `SpendReport`.

**Call relations**: Billing dashboards or APIs use this when showing workspace-level spend. It is the main reporting entry point for ledger data.

*Call graph*: calls 4 internal fn (_by_origin, _token_cost_sum, _token_sum, _usage_details); 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


##### `SpendRollup._by_origin`  (lines 1319–1384)

```
async def _by_origin(self, connection: AsyncConnection, window: sa.ColumnElement[bool]) -> tuple[OriginTotal, ...]
```

**Purpose**: Groups token spend by the conversation origin a member actually started, even when subagents created child turns. This makes fan-out work appear under the surface that caused it.

**Data flow**: It receives a connection and window filter. It builds a recursive database query that walks parent turns up to their root conversation, then sums token counts and token costs by that conversation's readable surface label.

**Call relations**: `SpendRollup.read` calls this as one section of the workspace report. It uses `_token_sum` and `_token_cost_sum` so origin totals match other token reports.

*Call graph*: calls 2 internal fn (_token_cost_sum, _token_sum); called by 1 (read); 4 external calls (__init__, desc, execute, select).


##### `SpendRollup.read_member`  (lines 1386–1444)

```
async def read_member(self, connection: AsyncConnection, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads a spend report for one member only. It includes that member's selected usage, all-time usage, dimensions, and member-specific spend caps.

**Data flow**: It receives a connection, member id, and optional window length. It joins ledger rows through turns and conversations to that member, aggregates spend by dimension, loads that member's caps, builds shared usage details, and returns a `MemberSpendReport`.

**Call relations**: Member-facing billing views or admin tools use this for a single person's usage. It reuses `_usage_details` so member reports match workspace report calculations.

*Call graph*: calls 1 internal fn (_usage_details); 7 external calls (__init__, __init__, __init__, now, timedelta, execute, select).


##### `BalanceGate.admits`  (lines 1476–1514)

```
async def admits(self, connection: AsyncConnection, agent_id: UUID | None=None, key_slot_for: Callable[[str], str | None] | None=None, turn_id: UUID | None=None, model: str | None=None) -> SpendDecisi
```

**Purpose**: Decides whether a turn may start, resume, or be folded into a live run based on prepaid balance. Starting requires reserve headroom unless the workspace is using its own provider key and is still above zero.

**Data flow**: It receives a connection and optional agent, key-slot resolver, turn id, and model. It reads the workspace balance headroom, checks reserve and grace rules, checks whether the turn already debited money, optionally checks whether the workspace serves the model with its own key, and returns a `SpendDecision`.

**Call relations**: Turn admission and resume paths call this before allowing work to begin. It delegates own-key checks to `_workspace_serves_itself` and prior-charge checks to `_turn_has_debited`.

*Call graph*: calls 2 internal fn (_turn_has_debited, _workspace_serves_itself); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._workspace_serves_itself`  (lines 1516–1538)

```
async def _workspace_serves_itself(self, connection: AsyncConnection, agent_id: UUID | None, key_slot_for: Callable[[str], str | None] | None, model: str | None=None) -> bool
```

**Purpose**: Determines whether the workspace's own credential will serve the model being run. This supports the balance-gate exemption for bring-your-own-key model calls.

**Data flow**: It receives an optional agent id, optional model, and a function that maps model names to credential slots. If the model is not provided, it reads the agent's model from the database; then it checks whether the workspace owns the needed key slot.

**Call relations**: `BalanceGate.admits` calls this only when balance is below the normal starting reserve but still positive. It finishes by calling `workspace_owns_the_key`.

*Call graph*: calls 1 internal fn (workspace_owns_the_key); called by 1 (admits); 2 external calls (execute, select).


##### `BalanceGate.sustains`  (lines 1540–1561)

```
async def sustains(self, connection: AsyncConnection, pending_micro_usd: int, turn_id: UUID | None=None) -> SpendDecision
```

**Purpose**: Decides whether a running turn may continue into another round. Unlike admission, continuation stops at zero balance, not at the reserve line.

**Data flow**: It receives a connection, the pending cost not yet billed, and an optional turn id. It reads balance headroom, subtracts pending spend, applies grace, and either allows the turn or rejects it if the turn would actually debit money while over the limit.

**Call relations**: The turn loop calls this between rounds or before more spend is taken. It uses `_turn_has_debited` to avoid parking work that costs the balance nothing and therefore could never resume by itself.

*Call graph*: calls 1 internal fn (_turn_has_debited); 4 external calls (__init__, _forget_absent_balance, balance_refusal_message, read_headroom).


##### `BalanceGate._turn_has_debited`  (lines 1563–1586)

```
async def _turn_has_debited(self, connection: AsyncConnection, turn_id: UUID | None) -> bool
```

**Purpose**: Checks whether a turn has already taken real money from the workspace balance. This is different from asking whether the work had a listed price, because own-key token work can be priced but not debited.

**Data flow**: It receives a connection and optional turn id. If there is no turn id it returns false; otherwise it looks for any ledger row for that turn with a positive debited amount and returns whether one exists.

**Call relations**: `BalanceGate.admits` and `BalanceGate.sustains` use this to avoid bad loops around balance limits. It grounds the decision in what the ledger actually charged.

*Call graph*: called by 2 (admits, sustains); 2 external calls (execute, select).


### `core/src/ufo/harness/models/pricing.py`

`domain_logic` · `billing/accounting`

This file is the project’s price list and calculator for language model usage. Models charge different rates for different kinds of tokens, such as input tokens, output tokens, and cached tokens. The ModelPrice data class stores those rates in micro-USD per million tokens. A micro-USD is one millionth of a US dollar, which lets the code use whole numbers instead of floating-point money values.

The main job is simple: take a model name, take a Usage record that says how many tokens were used in each category, look up that model’s rates, multiply the counts by the matching rates, and return the total cost in micro-USD. If the model is not in the price table, the code logs a warning and returns zero. That is important for old or historical records: billing can continue without crashing, but the missing price is still visible in logs.

The file also builds a digest, which is a cryptographic fingerprint, of the price table. Like a tamper-evident label on a receipt, this digest makes it possible to tell exactly which set of prices produced a billed amount. The Pricing class bundles the price table with that digest and exposes a small method for calculating costs.

#### Function details

##### `price_digest`  (lines 27–44)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable version stamp for a table of model prices. This helps later readers know exactly which prices were used when a usage record was billed.

**Data flow**: It receives a mapping from model names to ModelPrice values. It turns that table into sorted, compact JSON so the same prices always produce the same text, then runs SHA-256, a standard fingerprinting algorithm, over that text. It returns a string starting with "sha256:" followed by the fingerprint.

**Call relations**: When a new Pricing object is built, pricing_from calls this function to stamp the price table. Internally it relies on json.dumps to make the deterministic text form and hashlib.sha256 to make the fingerprint.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 47–61)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one usage record for one model. It is the core calculator that turns token counts into a money amount measured in micro-USD.

**Data flow**: It receives a model name, a Usage object containing token counts, and a price table. It looks up the model’s rates, multiplies each token category by its matching rate, adds the results, and divides by one million because the rates are per million tokens. If the model is missing from the table, it logs that fact and returns 0 instead of stopping the program.

**Call relations**: Pricing.micro_usd calls this helper whenever billing code asks for a cost. If the model is unknown, this function hands the problem to the observability logger so operators can notice the missing price.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 71–72)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the simple public way to ask a Pricing object, “What did this model usage cost?” It keeps callers from needing to know the details of the price table lookup and token math.

**Data flow**: It receives a model name and a Usage record. It uses the Pricing object’s stored price table and passes everything to usage_priced_micro_usd. The result is returned as an integer number of micro-USD.

**Call relations**: Billing code calls this method when recording sandbox, turn, or workspace usage. This method is a small wrapper that keeps those accounting paths focused on recording charges while usage_priced_micro_usd does the actual calculation.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 75–78)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete Pricing object from a raw model price table. It makes a copy of the table and attaches the digest that identifies that exact set of rates.

**Data flow**: It receives a mapping of model names to ModelPrice values. It copies that mapping into a normal dictionary, calculates the price digest for the copied table, and returns a new Pricing object containing both the table and the digest.

**Call relations**: This is the setup step for pricing data. It calls price_digest so every Pricing object carries its own version stamp, then constructs the Pricing instance that later billing code can use.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### `core/src/ufo/runtime/billing/balance.py`

`domain_logic` · `request handling and billing background jobs`

This file solves a practical billing problem: the system needs to check credit before model work starts, and that check happens often. Instead of recalculating a lifetime total from every purchase each time, it keeps a current balance row in the database, like a running bank balance. The individual purchases are still stored separately so the balance can be audited later.

The file separates several jobs. It can quickly read only the numbers needed to decide whether a workspace has enough “headroom” to start more work. It can read the fuller balance picture for an admin screen, including lifetime credit granted and money charged. It can add credit exactly once per payment reference, so a repeated payment notification does not double-credit the workspace. It can subtract usage from the balance in the same transaction as the usage record, so the money trail stays consistent.

It also supports automatic top-ups. A workspace may say, “when my balance drops below this threshold, charge this amount.” The file can find workspaces that crossed that line, store those settings, and mark that a workspace has successfully paid before. That successful payment earns a small grace allowance, so a workspace is not stopped during the short delay between running low and the refill completing.

A small in-memory cache remembers workspaces that recently had no balance row. This saves repeated database reads for self-hosted or unpaid setups, but it is only an optimization; adding credit clears the cache entry.

#### Function details

##### `balance_absent`  (lines 40–46)

```
def balance_absent(workspace_id: UUID) -> bool
```

**Purpose**: This is a quick check that answers whether a workspace was recently found to have no balance row. It is used as a shortcut so the system can avoid repeated database reads for workspaces that are known, for a few seconds, to have no billing balance.

**Data flow**: It receives a workspace ID. It looks in a small in-memory map for an expiry time, compares that time with the current monotonic clock, and returns true only if the “no balance” note is still fresh. It does not change the database or the balance.

**Call relations**: This function stands at the front of the fast path. Other code can ask it before doing a heavier balance read. The notes it checks are created by _note_absent_balance when read_balance or read_headroom fail to find a database row, and they are cleared by _forget_absent_balance after credit creates or updates a balance.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_balance`  (lines 49–56)

```
def _note_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This records, briefly, that a workspace has no balance row. It exists to make repeated checks cheaper without treating the cache as the source of truth.

**Data flow**: It receives a workspace ID, reads the current monotonic time, and stores an expiry time a few seconds in the future. If the cache has reached its maximum size, it first removes entries whose expiry time has already passed. The result is an updated in-memory cache entry, not a database change.

**Call relations**: read_balance and read_headroom call this when the database says the workspace has no balance row. Later, balance_absent can use the note to skip work for a short time. If credit later adds money to the workspace, _forget_absent_balance removes the note so the shortcut cannot hide a real balance.

*Call graph*: called by 2 (read_balance, read_headroom); 1 external calls (monotonic).


##### `_forget_absent_balance`  (lines 59–62)

```
def _forget_absent_balance(workspace_id: UUID) -> None
```

**Purpose**: This removes the short-lived “no balance” note for a workspace. It is needed when a workspace has just been credited, because the old shortcut would now be wrong.

**Data flow**: It receives a workspace ID and deletes that ID from the in-memory absence cache if it is present. Nothing is returned, and no database row is changed.

**Call relations**: credit calls this after successfully adding money to a workspace balance. That keeps the fast-path cache in step with the real database state, so future balance checks do not incorrectly assume the workspace still lacks a balance.

*Call graph*: called by 1 (credit).


##### `billing_screen_url`  (lines 79–88)

```
def billing_screen_url(public_base_url: str | None, home_surface: str | None) -> str | None
```

**Purpose**: This builds the link to the workspace billing screen, if this deployment has a public web address and a browser surface to show it on. If there is nowhere useful to send a user, it returns no link.

**Data flow**: It takes a public base URL and the name of the home surface. If either is missing, it returns None. Otherwise it trims any trailing slash from the base URL, appends the surface path and billing fragment, and returns the completed URL string.

**Call relations**: This is used when preparing messages or configuration for billing gates. Its result can be handed to balance_refusal_message so a user who is blocked for lack of credit can be told exactly where an admin can fix it.


##### `balance_refusal_message`  (lines 91–100)

```
def balance_refusal_message(billing_url: str | None) -> str
```

**Purpose**: This writes the human-facing message shown when a workspace is out of credit. It explains the problem and, when possible, includes the billing page link.

**Data flow**: It takes an optional billing URL. If the URL is missing, it returns a plain sentence saying the workspace is out of credit and an admin can set up refills. If the URL exists, it returns the same warning with the link included.

**Call relations**: This function sits near the point where billing decisions become user-facing text. It commonly follows billing_screen_url: first build the link if there is one, then use this function to produce the refusal message.


##### `read_auto_topup`  (lines 112–134)

```
async def read_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This checks whether a workspace both has automatic top-up configured and has fallen low enough to need one now. It answers the refill job’s question: “Should we try to charge this workspace on this tick?”

**Data flow**: It receives a database connection and workspace ID. It reads the workspace’s balance, top-up amount, and top-up threshold from the balance table. If there is no row, no top-up amount, or the balance is still above the threshold, it returns None. Otherwise it returns an AutoTopup object containing the amount to charge and the threshold that triggered it.

**Call relations**: A billing extension or refill worker can call this before attempting payment. The function keeps the “is this workspace short?” decision in core billing logic, while the payment extension can focus on how to charge the card.

*Call graph*: 3 external calls (__init__, execute, select).


##### `topping_up_workspaces`  (lines 137–151)

```
def topping_up_workspaces() -> WorkspaceCandidates
```

**Purpose**: This prepares a fleet-wide search for workspaces that currently need automatic refill. It avoids opening transactions for every workspace by first narrowing the list to only those with top-up enabled and a balance at or below the threshold.

**Data flow**: It creates a candidate source based on a nested database query. The query selects workspace IDs whose auto-top-up amount is set and whose balance has reached the configured threshold. The function returns a WorkspaceCandidates object that can be used by a background job to visit only likely refill targets.

**Call relations**: A periodic refill job uses this as its starting list. Inside it, short_of_its_line builds the exact database selection, and owner_candidates wraps that selection into the project’s workspace-candidate system.

*Call graph*: 1 external calls (owner_candidates).


##### `topping_up_workspaces.short_of_its_line`  (lines 144–149)

```
def short_of_its_line() -> sa.Select[tuple[UUID]]
```

**Purpose**: This is the database query builder used by topping_up_workspaces. It describes which workspaces are low enough to be considered for automatic top-up.

**Data flow**: It takes no direct arguments, but closes over the balance table definitions. It builds a SQL select statement that returns workspace IDs where an auto-top-up amount exists and the current balance is less than or equal to the threshold. The output is a query object, not the query results themselves.

**Call relations**: topping_up_workspaces hands this query-building function to owner_candidates. That lets the wider candidate system run the query at the right time and route refill work by workspace owner.

*Call graph*: 1 external calls (select).


##### `set_auto_topup`  (lines 154–174)

```
async def set_auto_topup(connection: AsyncConnection, workspace_id: UUID, amount_micro_usd: int | None, threshold_micro_usd: int | None) -> bool
```

**Purpose**: This turns automatic refill on or off for a workspace that already has a balance. It prevents half-configured settings by requiring both the refill amount and the trigger threshold, or neither.

**Data flow**: It receives a database connection, workspace ID, optional top-up amount, and optional threshold. If only one of the two numbers is provided, it raises a ValueError. Otherwise it updates the workspace balance row with the new settings and timestamp. It returns true if exactly one balance row was updated, or false if the workspace had no balance row to update.

**Call relations**: Admin-facing billing code can call this when someone changes refill settings. The later refill flow reads these fields through read_auto_topup, configured_auto_topup, and topping_up_workspaces.

*Call graph*: 2 external calls (execute, update).


##### `mark_topup_verified`  (lines 177–191)

```
async def mark_topup_verified(connection: AsyncConnection, workspace_id: UUID) -> None
```

**Purpose**: This records that a workspace has successfully paid for a top-up at least once. That proof earns the workspace a fixed grace allowance when deciding whether work may continue while a refill is in progress.

**Data flow**: It receives a database connection and workspace ID. It updates the workspace balance row only if the verification timestamp is still empty, setting that timestamp and the updated time to now. It returns nothing.

**Call relations**: Payment or refill code calls this after a card charge has settled. Later, read_headroom sees the verification timestamp and includes the fixed top-up grace amount in the numbers used by billing gates.

*Call graph*: 2 external calls (execute, update).


##### `configured_auto_topup`  (lines 203–224)

```
async def configured_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None
```

**Purpose**: This reads the automatic top-up settings exactly as configured, whether or not the workspace is currently low on credit. It is useful for showing an admin what rule is saved.

**Data flow**: It receives a database connection and workspace ID. It reads the top-up amount and threshold from the workspace balance row. If there is no row or no top-up amount, it returns None. Otherwise it returns an AutoTopup object with the saved amount and threshold.

**Call relations**: This differs from read_auto_topup, which only speaks up when the balance has crossed the threshold. Admin screens or settings APIs can call configured_auto_topup when they need to display the rule itself.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_headroom`  (lines 227–246)

```
async def read_headroom(connection: AsyncConnection, workspace_id: UUID) -> Headroom | None
```

**Purpose**: This reads the small set of balance numbers needed before starting model work. It is deliberately lighter than the full balance read because it may run before every model round.

**Data flow**: It receives a database connection and workspace ID. It reads the current balance, reserve amount, and top-up verification timestamp. If no balance row exists, it notes that absence in the short-lived cache and returns None. If a row exists, it returns a Headroom object containing the balance, required reserve, and either zero grace or the fixed grace amount for verified top-up workspaces.

**Call relations**: Billing gates call this when deciding whether a workspace may begin more work. If no row exists, it calls _note_absent_balance so later checks can use balance_absent. Its returned Headroom values are the fast decision-making version of the fuller data read by read_balance.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `recent_purchases`  (lines 260–293)

```
async def recent_purchases(connection: AsyncConnection, workspace_id: UUID, limit: int) -> tuple[Purchase, ...]
```

**Purpose**: This returns the newest balance credits for a workspace, up to a caller-specified limit. It supports screens or reports that explain where a balance came from.

**Data flow**: It receives a database connection, workspace ID, and maximum number of purchases to return. It queries purchase rows for that workspace, newest first, using the row ID to make the order stable when timestamps match. It converts each row into a Purchase object and returns them as a tuple.

**Call relations**: Admin or operator views can call this alongside read_balance. read_balance gives the lifetime totals, while recent_purchases gives the visible recent entries behind those totals without loading an unbounded purchase history.

*Call graph*: 3 external calls (__init__, execute, select).


##### `read_balance`  (lines 296–326)

```
async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None
```

**Purpose**: This reads the full billing picture for a workspace: current balance, reserve, total credit ever granted, total money ever charged, and the latest purchase time. It is meant for admin or operator views rather than the frequent pre-work gate.

**Data flow**: It receives a database connection and workspace ID. First it reads the workspace balance row. If none exists, it records that absence in the short-lived cache and returns None. If the row exists, it separately sums all purchase rows for that workspace and finds the most recent purchase timestamp. It returns a Balance object combining the current row and lifetime purchase totals.

**Call relations**: Admin-facing code calls this when it needs the whole story. It calls _note_absent_balance on a missing row, just like read_headroom, so the fast absence shortcut stays informed.

*Call graph*: calls 1 internal fn (_note_absent_balance); 3 external calls (__init__, execute, select).


##### `credit`  (lines 329–387)

```
async def credit(connection: AsyncConnection, workspace_id: UUID, granted_micro_usd: int, charged_micro_usd: int, reference: str) -> bool
```

**Purpose**: This adds credit to a workspace balance exactly once for a given reference, such as a payment ID. It protects against duplicate delivery of the same payment event by refusing to apply the same workspace-reference pair twice.

**Data flow**: It receives a database connection, workspace ID, granted amount, charged amount, and reference string. It inserts a purchase row with a new UUID; if a row with the same workspace and reference already exists, it does nothing and returns false. If the purchase is new, it inserts or updates the workspace balance by adding the granted amount, clears any cached “no balance” note, and returns true.

**Call relations**: Payment fulfilment code calls this inside its own database transaction, so recording the payment source and crediting the balance succeed or fail together. If it returns true and the caller’s transaction commits, the caller can then call count_charge to emit the billing metric.

*Call graph*: calls 1 internal fn (_forget_absent_balance); 2 external calls (execute, uuid4).


##### `count_charge`  (lines 390–407)

```
def count_charge(charged_micro_usd: int) -> None
```

**Purpose**: This reports newly charged money to the metrics system after the database transaction has safely committed. It only counts positive charges, because counters can go up but cannot reliably be undone for refunds or corrections.

**Data flow**: It receives a charged amount in micro-USD. If the amount is zero or negative, it returns without doing anything. If the amount is positive, it emits a metric named balance_charged_micro_usd_total with that amount. It does not change the database.

**Call relations**: Callers use this after credit has returned true and after their transaction has committed. Keeping it outside credit avoids reporting a charge that later rolls back, and avoids double-counting when a payment event is retried.

*Call graph*: 1 external calls (emit_metric).


##### `debit`  (lines 410–430)

```
async def debit(connection: AsyncConnection, workspace_id: UUID, micro_usd: int) -> int
```

**Purpose**: This subtracts spent credit from a workspace balance. It records what was actually taken, and it allows the balance to go negative so the system does not lose the record of money already spent.

**Data flow**: It receives a database connection, workspace ID, and amount to subtract. If the amount is zero, it immediately returns zero. Otherwise it updates the workspace balance row by reducing the balance and refreshing the timestamp. If one row was updated, it returns the requested amount; if the workspace has no balance row, it returns zero.

**Call relations**: Usage-recording code calls this in the same transaction as the ledger entry for the work that burned credit. That way, the usage record and balance movement stay together: both are saved, or neither is.

*Call graph*: 2 external calls (execute, update).


##### `set_reserve`  (lines 433–444)

```
async def set_reserve(connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int) -> bool
```

**Purpose**: This sets the reserve amount a workspace must keep before new work may start. The reserve acts like a safety buffer so a nearly empty workspace does not begin work it cannot meaningfully continue.

**Data flow**: It receives a database connection, workspace ID, and reserve amount in micro-USD. It updates the existing workspace balance row with the new reserve and timestamp. It returns true if one row was updated, or false if the workspace had no balance row.

**Call relations**: Admin or billing configuration code calls this when changing the workspace’s required buffer. Later, read_headroom includes this reserve in the numbers used by admission gates before model work begins.

*Call graph*: 2 external calls (execute, update).


### Billing integrations
Connects internal usage and balance data to external Metronome reporting and Stripe prepaid payments.

### `extensions/metronome/ufo_ext_metronome.py`

`domain_logic` · `scheduled jobs, chat tool calls, and billing page requests`

This extension is the bridge between UFO's internal money and usage records and two outside services. Metronome receives usage events so humans can see rated usage statements. Stripe stores cards, opens the customer billing portal, and charges saved cards when automatic top-ups are needed. Without this file, settled usage would not be shipped to Metronome, admins could not arrange cards through the product, and workspaces with automatic refill enabled would not be charged and credited.

The file has three main jobs. First, UsageShipper drains frozen usage export records from core. It turns each record into a Metronome event with a stable transaction id, sends the batch, and only then marks those records as acknowledged. This is like mailing numbered receipts: if the process crashes, the same receipt can be mailed again without counting twice.

Second, the manage_billing tool lets a workspace admin ask in chat for billing status, a Stripe portal link, or automatic refill settings. It checks that the speaker is an admin before doing anything.

Third, BalanceTopup runs on a schedule. When core says a workspace balance is low enough to refill, it finds the saved card, creates a Stripe charge, and credits the workspace only after Stripe says the payment succeeded. The file also defines a billing route for a web page that shows balance, reserve, saved card details, autopay settings, and recent purchases.

#### Function details

##### `StripeError.__init__`  (lines 189–191)

```
def __init__(self, message: str, status: int=0) -> None
```

**Purpose**: This builds an error object for a failed Stripe call and remembers the HTTP status code. The status matters because later code treats a declined card differently from a broken provider call.

**Data flow**: It receives a message and an optional status code. It stores the message in the normal exception machinery and keeps the numeric status on the error object for callers to inspect.

**Call relations**: The shared Stripe request helper creates this error when Stripe returns a non-success response. Charging code later reads the status to decide whether to wait, report a decline, or fail loudly.

*Call graph*: called by 1 (_stripe).


##### `UsageShipper.run`  (lines 221–247)

```
async def run(self) -> None
```

**Purpose**: This sends one workspace's settled usage records to Metronome in safe batches. It is careful to mark records as sent only after Metronome accepts them, so a crash causes a harmless retry rather than lost usage.

**Data flow**: It reads the Metronome bearer token from the environment, finds the workspace's fixed backfill floor, repeatedly asks core for pending usage exports, converts them to event payloads, ensures the Metronome customer alias exists, posts the events, logs success, and acknowledges the exports. It stops when there is no more work or the final batch is smaller than the batch size.

**Call relations**: The scheduled _ship wrapper creates a UsageShipper and calls this method. During the run it relies on _floor for the time boundary, _note_usage_aging_out for warnings, _events for the Metronome payload, _ensure_metronome_customer before first send, and _ingest for the actual HTTP post.

*Call graph*: calls 6 internal fn (_events, _floor, _note_usage_aging_out, _ensure_metronome_customer, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 249–259)

```
async def _floor(self) -> datetime
```

**Purpose**: This returns the earliest usage time this workspace is allowed to ship. On the first run it records a floor a few days in the past, which prevents an accidental unlimited historical backfill.

**Data flow**: It reads a stored timestamp from the extension store. If none exists, it creates one based on the current time minus the backfill window, saves it, and returns it; otherwise it parses and returns the stored value.

**Call relations**: UsageShipper.run calls this before reading pending usage. That means core mints export intents only for usage on or after this stable floor.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._note_usage_aging_out`  (lines 261–282)

```
def _note_usage_aging_out(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: This warns operators when pending usage is getting too old for Metronome's backdating window. It does not fix the data; it makes a silent billing risk visible.

**Data flow**: It receives a batch of usage exports, finds the oldest occurrence time, compares it with the allowed backfill window, and emits a warning if the batch contains usage older than that window.

**Call relations**: UsageShipper.run calls this after it reads a batch and before sending it. It uses _rfc3339 to format the timestamp in the warning.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 3 external calls (now, timedelta, warn).


##### `UsageShipper._events`  (lines 284–303)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: This turns UFO usage export records into the exact event dictionaries Metronome expects. It preserves important labels, including whether the workspace used its own provider key.

**Data flow**: It receives usage exports and reads the workspace id from the context. For each export it builds an event with a stable transaction id, customer id, event type, timestamp, and usage properties, then returns the list of events.

**Call relations**: UsageShipper.run calls this immediately before _ingest. It uses _rfc3339 so event timestamps are sent in a standard text form.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 306–307)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job entry for shipping usage. It adapts the job system's context into a UsageShipper run.

**Data flow**: It receives an extension context, creates a UsageShipper with the configured test or production transport, and awaits its run. It returns nothing except any error raised by the shipper.

**Call relations**: manifest registers this as the handler for the usage shipping job. The real work is handed to UsageShipper.run.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 324–339)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: This reads the Stripe settings required for billing work. It fails before any Stripe object is created if the deployment is missing a needed setting.

**Data flow**: It reads the Stripe secret key and billing portal configuration id from environment variables. If either is missing, it raises an error naming all missing settings; otherwise it returns a validated BillingConfig object.

**Call relations**: Billing tool actions, billing projection, and top-up jobs call this before talking to Stripe. Usage shipping does not use it because Metronome usage reporting has its own token.


##### `_billing_record`  (lines 352–354)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: This reads the saved Stripe customer id for a workspace, if one has already been provisioned. It is the local pointer from UFO's workspace to Stripe's customer record.

**Data flow**: It asks the extension store for the billing record. If nothing is stored it returns None; otherwise it validates the stored data and returns a BillingRecord.

**Call relations**: Billing status, portal creation, autopay setup, the billing web page, and balance top-up all call this when they need to know whether the workspace already has a Stripe customer.

*Call graph*: called by 5 (run, _billing_autopay, _billing_portal, _billing_projection, _billing_status).


##### `manage_billing`  (lines 377–386)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: This is the chat-facing billing action. It lets an admin request status, get a Stripe portal link, or set automatic refills.

**Data flow**: It receives the tool context and parsed user arguments. It first checks admin permission, then reads billing configuration, then dispatches to the status, portal, or autopay helper based on the requested operation, returning a tool result.

**Call relations**: The tool definition registered in manifest points to this function. It delegates permission checking to _admin_billing and the actual work to _billing_status, _billing_portal, or _billing_autopay.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_autopay, _billing_portal, _billing_status).


##### `_billing_autopay`  (lines 389–420)

```
async def _billing_autopay(ext: ExtensionContext, config: BillingConfig, args: ManageBillingInput) -> ToolResult
```

**Purpose**: This sets or stops automatic balance refills for a workspace. It requires a saved payment method before enabling refills because future charges happen when no admin is present.

**Data flow**: It receives the extension context, Stripe config, and autopay arguments. It validates that both dollar amounts are supplied together or neither is supplied, checks for a saved card when enabling, converts dollars to micro-dollars, writes the auto-top-up rule in a transaction, clears old refusal state, bumps the attempt marker, logs the change, and returns the new setting as JSON text.

**Call relations**: manage_billing calls this for the autopay operation. It uses _billing_record and _default_payment_method to confirm payment setup, set_auto_topup to write the rule, and _text_result to return the answer.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 2 external calls (set_auto_topup, log).


##### `_admin_billing`  (lines 423–429)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This enforces that billing actions are only done by a speaking workspace admin. It protects billing controls from ordinary members and anonymous tool use.

**Data flow**: It reads the speaker member id and admin status from the tool context. If there is no speaker it raises SpeakerRequired; if the speaker is not an admin it raises an error; otherwise it returns the extension context.

**Call relations**: manage_billing calls this before any billing operation. The helpers that actually talk to Stripe or core only receive an extension context after this permission gate passes.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing); 1 external calls (__init__).


##### `_billing_status`  (lines 432–456)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: This reports what balance the workspace has and whether Stripe currently has a default payment method for it. It answers the admin's question without changing anything.

**Data flow**: It reads the balance from core inside a transaction, reads any stored Stripe customer record, checks Stripe for a default payment method if there is a customer, and returns a JSON text result with card-present status and balance fields.

**Call relations**: manage_billing calls this for the status operation. It depends on _billing_record, _default_payment_method, read_balance, and _text_result.

*Call graph*: calls 4 internal fn (transaction, _billing_record, _default_payment_method, _text_result); called by 1 (manage_billing); 1 external calls (read_balance).


##### `_billing_portal`  (lines 459–481)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: This creates a short-lived Stripe Customer Portal link where an admin can save or update a payment method and view billing details. If the workspace has no Stripe customer yet, it creates one first.

**Data flow**: It reads the workspace id and local billing record. If no record exists, it creates a Stripe customer and stores the returned id. It then creates a portal session with a return URL back to the workspace billing screen, logs the action, and returns the portal URL and customer id as JSON text.

**Call relations**: manage_billing calls this for the portal operation. It uses _stripe_customer for first-time provisioning, _portal_session for the link, and _text_result for the tool response.

*Call graph*: calls 5 internal fn (home_url, _billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 2 external calls (__init__, log).


##### `_text_result`  (lines 484–485)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This wraps a small dictionary as a text ToolResult. It gives chat tools a consistent way to return machine-readable JSON as plain text content.

**Data flow**: It receives a payload dictionary, converts it to a JSON string, puts that string in a TextContent object, and returns a ToolResult containing it.

**Call relations**: The billing status, portal, and autopay helpers all call this when returning data to the chat tool caller.

*Call graph*: called by 3 (_billing_autopay, _billing_portal, _billing_status); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 499–503)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads a required environment variable and raises a clear error if it is missing. It prevents silent operation with missing credentials.

**Data flow**: It receives an environment variable name, reads its value, and returns the value if present. If the value is missing or empty, it raises a RuntimeError explaining that the Metronome extension needs it.

**Call relations**: UsageShipper.run calls this before touching the usage export seam, so a deployment without a Metronome token fails before creating pending export work.

*Call graph*: called by 1 (run).


##### `_stripe_customer`  (lines 506–523)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: This creates or reuses the one Stripe Customer for a workspace. It uses a stable idempotency key, meaning repeated create attempts settle on the same customer instead of making duplicates.

**Data flow**: It receives billing config, a workspace id, and an optional HTTP transport. It posts customer details and workspace metadata to Stripe, then extracts and returns the customer id from Stripe's response.

**Call relations**: _billing_portal calls this when a workspace asks for the billing portal before a Stripe customer record exists. It sends the HTTP request through _stripe and validates the returned id with _as_str.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_portal_session`  (lines 526–551)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None, return_url: str | None=None) -> str
```

**Purpose**: This asks Stripe for a Customer Portal session URL. The URL lets an admin manage payment methods and billing details under the deployment's portal configuration.

**Data flow**: It receives billing config, a Stripe customer id, an optional portal flow type, an optional transport, and an optional return URL. It builds the Stripe request data, posts it, extracts the session URL, and returns it.

**Call relations**: _billing_portal calls this to create the link shown to admins. It relies on _stripe for the provider call and _as_str to ensure Stripe actually returned a URL.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_portal).


##### `_default_payment_method`  (lines 554–566)

```
async def _default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: This checks whether a Stripe customer has a default payment method and returns its id. That id is needed for off-session charges, because the charge must name the card to use.

**Data flow**: It receives billing config, a customer id, and an optional transport. It fetches the customer from Stripe, looks inside invoice settings for a default payment method, and returns that method id or None.

**Call relations**: Autopay setup, billing status, balance top-up, and card display all call this before deciding whether a workspace has a usable saved payment method.

*Call graph*: calls 1 internal fn (_stripe); called by 4 (run, _billing_autopay, _billing_status, _card_on_file).


##### `_card_on_file`  (lines 581–596)

```
async def _card_on_file(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> CardOnFile | None
```

**Purpose**: This reads friendly card details for the billing page: brand and last four digits. It returns None when there is no default card or the default payment method is not a card.

**Data flow**: It receives billing config, a customer id, and an optional transport. It first finds the default payment method id, then fetches that payment method from Stripe, extracts card brand and last four digits if present, and returns a CardOnFile object.

**Call relations**: _billing_projection calls this when rendering the billing page. It builds on _default_payment_method and uses _stripe for the second Stripe lookup.

*Call graph*: calls 2 internal fn (_default_payment_method, _stripe); called by 1 (_billing_projection); 1 external calls (__init__).


##### `_stripe`  (lines 599–620)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: This is the shared low-level Stripe HTTP helper. It adds authentication, pins the Stripe API version, sends the request, and turns failed responses into StripeError.

**Data flow**: It receives billing config, HTTP method, Stripe path, optional form data, optional idempotency key, and optional transport. It sends the request to Stripe, raises StripeError for non-success responses, and returns the parsed JSON body for successful responses.

**Call relations**: All Stripe-specific helpers use this: customer creation, portal sessions, payment method reads, card reads, and top-up charges. StripeError.__init__ is used here to preserve the failed status code.

*Call graph*: calls 1 internal fn (__init__); called by 5 (_charge, _card_on_file, _default_payment_method, _portal_session, _stripe_customer); 1 external calls (AsyncClient).


##### `_as_str`  (lines 623–627)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: This validates that a provider response field is a non-empty string. It catches malformed or unexpected Stripe responses close to where they are read.

**Data flow**: It receives a value and a human-readable field name. If the value is a non-empty string it returns it; otherwise it raises a ValueError naming the missing field.

**Call relations**: _stripe_customer uses it for customer ids, and _portal_session uses it for portal URLs.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_metronome_fault`  (lines 630–641)

```
def _metronome_fault(call: str, response: httpx.Response) -> str
```

**Purpose**: This creates a safe, concise error message for a failed Metronome call. It includes the call name, status code, and Metronome's message field without dumping the whole response body.

**Data flow**: It receives a call label and an HTTP response. It tries to parse JSON, reads a message if one exists, and returns a short text fault string.

**Call relations**: Customer lookup, customer creation, and ingest posting use this when they need to raise a MetronomeError. This keeps job failure records useful without exposing full request or response data.

*Call graph*: called by 3 (_customer_by_alias, _ensure_metronome_customer, _ingest); 1 external calls (json).


##### `_ensure_metronome_customer`  (lines 644–693)

```
async def _ensure_metronome_customer(ctx: ExtensionContext, token: str, transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: This makes sure Metronome has a live customer whose ingest alias is the workspace UUID. Without that alias, Metronome may accept usage events but fail to attach them to the right customer.

**Data flow**: It receives an extension context, Metronome token, and optional transport. It looks up a customer by alias; if one exists it returns. If not, it tries to create a customer with that alias, handles alias conflicts by re-reading, raises clear errors for missing customer permissions or failed responses, and logs successful creation.

**Call relations**: UsageShipper.run calls this once before sending the first batch in a pass. It depends on _customer_by_alias for lookups and _metronome_fault for readable provider errors.

*Call graph*: calls 2 internal fn (_customer_by_alias, _metronome_fault); called by 1 (run); 4 external calls (__init__, __init__, AsyncClient, log).


##### `_customer_by_alias`  (lines 703–718)

```
async def _customer_by_alias(http: httpx.AsyncClient, headers: dict[str, str], alias: str) -> str | None
```

**Purpose**: This looks up the live Metronome customer that owns a given ingest alias. It returns None if no visible customer currently has the alias.

**Data flow**: It receives an HTTP client, headers, and an alias string. It calls Metronome's customer list endpoint with the alias filter, raises if the token lacks permission or the response fails, and returns the first customer id found or None.

**Call relations**: _ensure_metronome_customer calls this before creating a customer and again after a conflict. That second read distinguishes a harmless race from an alias held by something the token cannot see.

*Call graph*: calls 1 internal fn (_metronome_fault); called by 1 (_ensure_metronome_customer); 3 external calls (__init__, __init__, get).


##### `BalanceTopup.run`  (lines 737–820)

```
async def run(self) -> None
```

**Purpose**: This performs an automatic prepaid balance refill for one workspace when core says the balance is low. It charges the saved Stripe payment method and credits the workspace only after the charge succeeds.

**Data flow**: It reads the desired auto-top-up rule, skips if none exists, observes short retry pauses for missing cards and longer pauses for card refusals, loads Stripe config, finds the workspace's Stripe customer and default payment method, checks current charged totals, attempts a charge, records refusal state if declined, and on success credits the workspace and logs the top-up.

**Call relations**: The scheduled _top_up wrapper creates BalanceTopup and calls this method. It uses _billing_record and _default_payment_method to find payment setup, _charge to ask Stripe for money, and core balance functions to credit and verify the refill.

*Call graph*: calls 3 internal fn (_charge, _billing_record, _default_payment_method); 9 external calls (fromisoformat, now, count_charge, credit, mark_topup_verified, read_auto_topup, read_balance, log, warn).


##### `BalanceTopup._charge`  (lines 822–879)

```
async def _charge(self, config: BillingConfig, customer_id: str, payment_method: str, wanted: AutoTopup, workspace_id: UUID, attempt: str) -> str | None
```

**Purpose**: This creates and confirms a Stripe PaymentIntent for an automatic top-up. It returns the payment intent id only when money has actually moved.

**Data flow**: It receives Stripe config, customer id, payment method id, the desired top-up amount, workspace id, and an attempt key. It converts micro-dollars to cents, posts a confirmed off-session payment intent to Stripe with a stable idempotency key, returns the intent id if succeeded, returns None for declined or non-succeeded payments, and raises a special in-flight error for Stripe conflicts.

**Call relations**: BalanceTopup.run calls this when it is time to refill. It uses _stripe for the request, and its result tells the caller whether to credit the balance, wait, or mark a refusal.

*Call graph*: calls 1 internal fn (_stripe); called by 1 (run); 2 external calls (__init__, warn).


##### `_top_up`  (lines 882–883)

```
async def _top_up(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job entry for automatic balance refills. It adapts the job system's context into a BalanceTopup run.

**Data flow**: It receives an extension context, creates a BalanceTopup with the configured test or production transport, and awaits its run. It returns nothing unless the top-up logic raises an error.

**Call relations**: manifest registers this as the handler for the balance top-up job. The actual decision-making is in BalanceTopup.run.

*Call graph*: 1 external calls (__init__).


##### `_ingest`  (lines 886–894)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: This posts a batch of usage events to Metronome's ingest API. It raises a job fault if Metronome refuses the batch.

**Data flow**: It receives a bearer token, a list of event dictionaries, and an optional transport. It sends the events as JSON with the token in the Authorization header, returns on success, and raises MetronomeError with a formatted fault on failure.

**Call relations**: UsageShipper.run calls this after preparing events and confirming the customer alias. It uses _metronome_fault to produce the error text for failed sends.

*Call graph*: calls 1 internal fn (_metronome_fault); called by 1 (run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 897–899)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: This formats a datetime for provider-facing text fields. If the time has no timezone, it treats it as UTC so the timestamp is not ambiguous.

**Data flow**: It receives a datetime. It leaves timezone-aware values alone, adds UTC to naive values, and returns the ISO-formatted timestamp string.

**Call relations**: UsageShipper._events uses this for Metronome event timestamps, and UsageShipper._note_usage_aging_out uses it in warning logs.

*Call graph*: called by 2 (_events, _note_usage_aging_out); 1 external calls (replace).


##### `_billing_request_workspace`  (lines 908–913)

```
def _billing_request_workspace(request: Request) -> UUID | None
```

**Purpose**: This identifies which workspace a billing page request belongs to by reading the signed session cookie. If it cannot find a workspace claim, the route should not proceed.

**Data flow**: It receives an HTTP request, reads the session cookie, asks the bearer-token helper for the workspace claim, and returns a workspace UUID or None.

**Call relations**: manifest registers this as the identify function for the billing route. Core uses its answer to bind the route request to a workspace before _billing_projection runs.

*Call graph*: 1 external calls (workspace_claim).


##### `_billing_projection`  (lines 916–983)

```
async def _billing_projection(ext: ExtensionContext, request: Request) -> Response
```

**Purpose**: This serves the billing status page data. It shows balance limits, card details if readable, automatic refill settings, and recent purchases without charging anything or changing settings.

**Data flow**: It receives an extension context and HTTP request. It verifies the session cookie for the bound workspace, checks that the email belongs to an admin member, reads headroom, balance, autopay, and purchase history from core, optionally reads card details from Stripe, and returns a JSON response. If the user is not signed in or not an admin, it returns an error response.

**Call relations**: The billing route registered in manifest calls this after _billing_request_workspace identifies the workspace. It uses core balance and seat helpers for local facts, _billing_record and _card_on_file for Stripe card display, and deliberately keeps the page usable even if Stripe cannot be read.

*Call graph*: calls 3 internal fn (transaction, _billing_record, _card_on_file); 9 external calls (configured_auto_topup, read_balance, read_headroom, recent_purchases, verify_token, JSONResponse, warn, member_by_email, member_is_admin).


##### `manifest`  (lines 986–1024)

```
def manifest() -> Manifest
```

**Purpose**: This tells the UFO extension system what this extension provides. It registers the chat tool, scheduled jobs, billing route, prompt guidance, and one credential slot.

**Data flow**: It builds and returns a Manifest containing the extension name and version, the manage_billing tool, the usage shipping and balance top-up job specs, the billing HTTP route, the billing prompt section, and the Anthropic bring-your-own-key credential slot.

**Call relations**: The extension loader calls this to discover the file's capabilities. The handlers it names are _ship for usage shipping, _top_up for balance refills, _billing_projection for the billing route, and manage_billing through the tool definition.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, metered_workspaces, topping_up_workspaces).

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the service how to start, where storage is, and which runtime options are enabled.
- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-model-provider-catalog` — The shared list of available AI models and providers, including limits, prices, credentials, and adapter rules.
- `reg-agent-records` — The saved assistant profiles, including their model choice, tools policy, setup needs, visibility, reasoning level, and spawn contracts.
- `reg-workspace-member-seat-state` — The shared record of workspaces, members, admins, invitations, seats, and workspace-level limits.
- `reg-egress-proxy-policy` — The network access rules and proxy state that decide which outside hosts can be reached and when secrets may be attached.
- `reg-credential-vault-connections` — The lockbox of account connections, OAuth grants, API keys, BYOK attempts, and agent permissions to use outside services.
- `reg-billing-ledger-balance` — The shared money and usage record, including spend caps, model costs, sandbox and egress usage, prepaid balances, and export progress.
- `reg-product-census-telemetry` — Derived product analytics/census state summarizing workspace activity, onboarding progress, tool connections, and payment funnel status for dashboards.
- `reg-external-client-connection-pools` — Process-global HTTP/gRPC client sessions, proxy clients, DNS/TLS state, and connection pools used for model providers, connectors, cloud storage, and sandbox services.
- `reg-provider-rate-limit-backoff` — Shared throttling, retry-after, backoff, and concurrency state for AI providers and external connector APIs, separate from billing spend caps.
- `reg-turn-assembly-snapshot` — The resolved per-turn host package handed into execution, including selected agent/model, effective prompts, allowed tools/spawn menu, seeded file digests, skills, and routing choices.
- `reg-turn-token-budget-state` — Per-turn context and token budget state used to trim history, set completion limits, manage prompt-cache assumptions, and reconcile model usage with billing.
