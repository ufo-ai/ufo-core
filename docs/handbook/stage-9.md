# Model selection, request construction, streaming, and accounting  `stage-9`

This stage is the turn engine’s connection to AI models during the main work loop. It decides which model to use, builds a neutral request that can include chat messages, tools, images, and metadata, sends it to the right provider, then converts the provider’s streamed answer into UFO’s standard event format.

The engine is the traffic controller. It claims a turn, gathers context, calls the model, runs any requested tools, records cost, and saves the final transcript in order. The model interface and spec files define the shared shapes for requests, streams, tool calls, images, errors, and model facts. The catalog, Bedrock plug-in, and OpenRouter plug-in list available models, prices, limits, credentials, and connection details. The registry combines these into one live lookup table.

Provider bridges for OpenAI-style APIs and Anthropic translate UFO’s request into each provider’s format and translate streamed replies back. Pricing turns token usage into recorded cost with a fingerprint of the price table. The catalog skill exposes the live model list to users. The package file simply makes these modules importable.

## Files in this stage

### Model catalog sources
Built-in and extension catalog files declare which models exist, what they cost, their limits, credentials, and how provider clients are created.

### `core/src/ufo/models/catalog.py`

`config` · `startup`

This file is like a menu and price list for the AI models shipped with the core system. Without it, the rest of the system would not know which Anthropic or OpenAI models are available by default, how much their token usage costs, which client code to use to talk to them, or which environment variable should hold the needed API key.

The file defines shared facts first: the names of the API key slots, the default environment variable names, context window sizes, and a shared statement that these models support reasoning with tools. A context window is the amount of text a model can consider at once.

Two small helper functions create live client wrappers: one for Anthropic and one for OpenAI. Two more helpers build `ModelSpec` objects, which are compact records describing a model and how the system should call it.

The main function, `core_model_specs`, returns the full tuple of built-in model specifications. Each entry includes the model id, its pricing, its knowledge cutoff, and provider-specific details. One important special case is `gpt-5.6-terra`, which is marked to use OpenAI's Responses API surface instead of the normal chat surface because a certain chat request shape would be rejected. At import time, the file also builds ready-to-use constants for the core specs, prices, pricing table, and price digest.

#### Function details

##### `_anthropic_client`  (lines 24–25)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function builds the Anthropic model client used later to send requests to Anthropic. It connects a model description with an SDK client that has been created using the provided API key.

**Data flow**: It receives a `ModelSpec`, which describes the model, and an API key string. It uses the key to create the underlying Anthropic SDK client, then wraps that SDK client together with the model spec in an `AnthropicClient`. The result is a ready wrapper object that knows both how to talk to Anthropic and which model it represents.

**Call relations**: This function is stored inside Anthropic model specifications as the recipe for creating the real client. When another part of the system later needs to call an Anthropic model, the spec can use this function to build the provider-specific client.

*Call graph*: 2 external calls (__init__, anthropic_sdk_client).


##### `_openai_client`  (lines 28–29)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function builds the OpenAI model client used later to send requests to OpenAI. It ties together the model description and an SDK client created with the user's API key.

**Data flow**: It receives a `ModelSpec` and an API key string. It turns the key into a lower-level OpenAI SDK client, then places that client and the spec inside an `OpenAIClient`. The output is a ready wrapper object for making OpenAI requests for that model.

**Call relations**: This function is placed into OpenAI model specifications as the client-building recipe. Later, when the system selects an OpenAI model, the spec can call this function to get the correct provider client.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 32–51)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: This helper creates a complete `ModelSpec` for an Anthropic model. It avoids repeating the same provider, key, context, reasoning, and API settings for every Anthropic entry in the catalog.

**Data flow**: It receives the model id, price, knowledge cutoff, API key environment variable name, and optionally a context window size. It fills in the Anthropic-specific defaults, including the provider name, client factory, key slot, chat API surface, and reasoning support. It returns one finished `ModelSpec` that the registry can treat as a catalog entry.

**Call relations**: The main catalog function `core_model_specs` calls this helper once for each built-in Anthropic model. The helper hands back standardized model records so the rest of the catalog can be written as a clear list of model facts rather than repeated setup code.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 54–68)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: This helper creates a complete `ModelSpec` for an OpenAI model. It keeps OpenAI-specific defaults in one place while still allowing a model to choose a different API surface when needed.

**Data flow**: It receives the model id, price, knowledge cutoff, API key environment variable name, and optionally the OpenAI API surface to use. It adds the OpenAI provider name, OpenAI client factory, default context window, key slot, and reasoning support. It returns a finished `ModelSpec` for that OpenAI model.

**Call relations**: The main catalog function `core_model_specs` calls this helper for each built-in OpenAI model. For most models it uses the default chat-style API surface, but for `gpt-5.6-terra` it passes the Responses surface so later request building uses the legal OpenAI endpoint.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 71–160)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: This function builds the full list of model specifications that core UFO ships with. It is the central source of truth for built-in model availability, pricing, context limits, knowledge cutoffs, and provider routing.

**Data flow**: It receives the environment variable names to use for Anthropic and OpenAI API keys. It creates `ModelPrice` values for each model, passes those prices and model facts into the Anthropic or OpenAI helper functions, and collects the resulting `ModelSpec` objects into a tuple. The output is an immutable-looking list of ready catalog entries that other code can register and use.

**Call relations**: At module load time, this function is called to create `CORE_MODEL_SPECS` using the default API key environment variable names. Its output is then used to build the core price map, the pricing table, and a price digest, so model selection and cost accounting share the same catalog facts.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `extension discovery and provider configuration`

This file is like a catalog card plus a set of connection instructions for Amazon Bedrock Mantle. Without it, the larger system would not know that these Bedrock-hosted Anthropic and OpenAI-compatible models exist, what they cost, what API style they use, or which environment variables are needed to reach them.

The file first names the provider and defines the important settings: the API key environment variable, the AWS region environment variables, timeouts, context-window sizes, and whether the models support reasoning. A context window is the amount of text a model can consider at once.

It then provides two small builders for clients. Anthropic model IDs are connected through Anthropic's Bedrock Mantle client. OpenAI-style model IDs are connected through the project's OpenAI client wrapper, using a Bedrock Mantle URL that changes depending on whether the model uses the chat API or the newer responses API.

Next, helper functions create ModelSpec objects. A ModelSpec is the system's standard description of one model: its ID, provider, price, knowledge cutoff date, maximum context size, API style, and where to find its key. Finally, manifest() returns the extension manifest, which is what the host system reads when it discovers this provider.

#### Function details

##### `bedrock_region`  (lines 42–48)

```
def bedrock_region() -> str
```

**Purpose**: Finds the AWS region that Bedrock Mantle should use. This matters because Bedrock endpoints are regional, so the system cannot build the correct service URL without a region.

**Data flow**: It reads the process environment, first looking for AWS_REGION and then AWS_DEFAULT_REGION. If it finds one, it returns that region string. If neither is set, it stops with a clear error telling the user which environment variables to set.

**Call relations**: When either Bedrock client builder needs to connect to Amazon, it calls this function first. The returned region is then used to create the Anthropic Bedrock client or to build the OpenAI-compatible Bedrock Mantle base URL.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 51–63)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Builds the client object used to call Anthropic models through Amazon Bedrock Mantle. It wraps the provider-specific Anthropic connection in the project's common AnthropicClient shape.

**Data flow**: It receives a model specification and an API key. It asks bedrock_region for the AWS region, creates an Anthropic Bedrock Mantle async client with that key, region, no automatic retries, and a fixed timeout, then packages that client together with the model specification. The result is an AnthropicClient the rest of the system can use uniformly.

**Call relations**: This function is attached to Anthropic ModelSpec entries as their client builder. Later, when the system needs to use one of those models, the spec can call on this builder to produce the real network client.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 66–73)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Builds the client object used to call Bedrock Mantle models that speak an OpenAI-compatible API. It hides the Bedrock-specific URL details behind the project's standard OpenAIClient wrapper.

**Data flow**: It receives a model specification and an API key. It gets the AWS region, chooses the correct Bedrock Mantle base URL based on the model's API surface, creates an OpenAI SDK client pointed at that URL, and returns an OpenAIClient paired with the model specification.

**Call relations**: This function is attached to OpenAI-style ModelSpec entries as their client builder. When one of those models is selected, the system uses this builder so the normal OpenAI request code can talk to Bedrock Mantle instead of the public OpenAI endpoint.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 76–94)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Creates the standard model description for one Anthropic model hosted on Bedrock Mantle. It avoids repeating the same provider, credential, reasoning, and API settings for every Anthropic model entry.

**Data flow**: It takes a model ID, price information, a knowledge cutoff date, and optionally a context-window size. It combines those with the Bedrock provider name, Anthropic client builder, shared credential slot, API-key environment variable, reasoning support, and chat API setting. The output is a ModelSpec ready to be included in the provider's model catalog.

**Call relations**: The file uses this helper while building BEDROCK_MODEL_SPECS. Each Anthropic entry produced here later becomes part of the manifest that the host system reads during provider discovery.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 97–111)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: Creates the standard model description for one OpenAI-compatible model hosted on Bedrock Mantle. It keeps the repeated Bedrock settings in one place while still allowing each model to declare its own price, context size, and API style.

**Data flow**: It takes a model ID, price information, knowledge cutoff date, context-window size, and API surface. It combines those with the Bedrock provider name, OpenAI client builder, shared credential slot, API-key environment variable, and reasoning support. The output is a ModelSpec for the provider catalog.

**Call relations**: The file uses this helper while building BEDROCK_MODEL_SPECS. Those OpenAI-compatible specs are then exposed through manifest(), so the rest of the system can list and use them like any other model.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 182–193)

```
def manifest() -> Manifest
```

**Purpose**: Returns the provider manifest that tells the host system what this Bedrock extension offers. The manifest includes the extension name and version, the credential it needs, and the full list of Bedrock model specifications.

**Data flow**: It creates a credential slot describing the Bedrock API key, then combines that with the provider name, version, and BEDROCK_MODEL_SPECS. The result is a Manifest object that the extension loader can read.

**Call relations**: This is the main handoff point from the file to the wider system. During extension discovery, the host calls manifest() to learn which credentials to ask for and which models this provider makes available.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `startup for manifest registration; request handling when streaming model responses`

OpenRouter is a service that sits in front of many different AI model providers. This file is the adapter that makes OpenRouter look like a normal model backend to the rest of the project. Without it, the system could not list these OpenRouter models, create an OpenRouter client, or translate a model request into the OpenAI-style chat API that OpenRouter accepts.

The file does three main jobs. First, it defines how model names are converted into OpenRouter slugs, such as turning a plain OpenAI model name into an `openai/...` name. Second, it defines `OpenRouterModelClient`, which sends a request to OpenRouter and streams back small events: pieces of text, tool-call starts, tool-call argument fragments, and finally token usage. This is like a live transcript printer: it emits each new bit as soon as it arrives, instead of waiting for the whole answer.

It also adds provider-specific safety behavior. If OpenRouter sends an empty answer from one upstream provider, the client can retry while asking OpenRouter to avoid that provider. If OpenRouter says the response was cut off because the token limit was reached, the file raises a clear truncation error. Finally, it declares the exact OpenRouter models this extension offers, including price, context window, and API key location, and exposes them through a manifest.

#### Function details

##### `openrouter_slug`  (lines 53–63)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: This function turns the project’s model id into the model name format OpenRouter expects. It adds a provider prefix for common plain OpenAI or Anthropic model names, while leaving already-prefixed OpenRouter slugs alone.

**Data flow**: It receives a model name as text. If the name already contains `/`, it treats it as a complete OpenRouter slug and returns it unchanged. If it looks like an OpenAI model, it returns `openai/<model>`; if it looks like an Anthropic Claude model, it returns `anthropic/<model>`; otherwise it passes the name through unchanged.

**Call relations**: When `OpenRouterModelClient._create_kwargs` is building the request that will be sent to OpenRouter, it calls this helper so the `model` field is in the form OpenRouter understands.

*Call graph*: called by 1 (_create_kwargs).


##### `_chunk_provider`  (lines 66–71)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: This function reads which upstream provider OpenRouter used for a streamed response chunk. That matters because, if that provider returns an empty completion, the client can retry while excluding it.

**Data flow**: It receives one streamed chat chunk from the OpenAI-compatible API. It looks inside the chunk’s extra OpenRouter metadata for a `provider` value. If it finds one, it returns it as text; otherwise it returns nothing.

**Call relations**: `OpenRouterModelClient.complete` calls this while reading the stream. The provider name it extracts is later used if the response is empty and the client decides to retry with that provider ignored.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 74–83)

```
def _usage_of(usage: CompletionUsage) -> Usage
```

**Purpose**: This function converts OpenAI-style token usage information into the project’s own `Usage` object. It also checks that cached input tokens are not larger than the total input tokens, because that would be inconsistent accounting.

**Data flow**: It receives usage data from the OpenAI-compatible response. It reads total prompt tokens, completion tokens, and any cached prompt tokens. It subtracts cached prompt tokens from normal input tokens, keeps completion tokens as output tokens, records cached tokens separately, and returns a `Usage` result.

**Call relations**: `OpenRouterModelClient.complete` calls this when a stream chunk includes final usage accounting. The returned `Usage` object is yielded at the end of a successful model response so the rest of the system knows the token cost.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient.complete`  (lines 100–168)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the main streaming call for OpenRouter. It sends one model request to OpenRouter, yields response events as they arrive, retries some temporary provider errors, and reports final token usage.

**Data flow**: It receives a `ModelRequest` containing the model name, messages, tools, token limit, and reasoning setting. It builds OpenRouter API arguments, opens a streamed chat completion, then turns each incoming chunk into project events such as `TextDelta`, `ToolCallStart`, and `ToolCallDelta`. It tracks the upstream provider, final finish reason, and usage data. On success it yields all streamed events and finally a `Usage` object; on truncation or unrecoverable API failure it raises an error.

**Call relations**: The rest of the model system calls this when it needs an answer from an OpenRouter-backed model. Inside the flow it asks `_create_kwargs` to prepare the API request, uses `_chunk_provider` to remember which upstream served the response, and uses `_usage_of` to translate token accounting. If OpenRouter returns a temporary rate-limit or server error before any output has been yielded, it waits with `asyncio.sleep` and retries. If a provider gives a completely empty stopped response, it loops back and asks OpenRouter to avoid that provider.

*Call graph*: calls 3 internal fn (_create_kwargs, _chunk_provider, _usage_of); 5 external calls (__init__, __init__, __init__, __init__, sleep).


##### `OpenRouterModelClient._create_kwargs`  (lines 170–199)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: This function prepares the exact set of arguments sent to OpenRouter’s OpenAI-compatible chat completion API. It translates the project’s request shape into the wire format expected by the network client.

**Data flow**: It receives a `ModelRequest` and a set of providers to ignore. It converts the model id into an OpenRouter slug, converts the system and conversation messages into OpenAI-style messages, includes the token limit and streaming options, adds reasoning effort when enabled, and adds tool definitions when tools are available. It returns a dictionary of keyword arguments ready for the OpenAI SDK call.

**Call relations**: `OpenRouterModelClient.complete` calls this just before creating the streamed completion. This helper calls `openrouter_slug` for model naming and `openai_messages` for message translation, so the main streaming function can stay focused on reading and interpreting the response.

*Call graph*: calls 1 internal fn (openrouter_slug); called by 1 (complete); 1 external calls (openai_messages).


##### `_model_client`  (lines 202–206)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: This function builds an `OpenRouterModelClient` for one model specification and API key. It is the factory used by the model registry when it needs a live client.

**Data flow**: It receives a `ModelSpec` and an API key. It creates an OpenAI SDK client configured to talk to OpenRouter’s base URL, then wraps that SDK client and the model spec in an `OpenRouterModelClient`. The result is a ready-to-use backend client.

**Call relations**: Each model specification created by `_openrouter` points to this factory. Later, when the system wants to use that model, the registry can call this function to turn stored configuration and a key into the streaming client used by `complete`.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 209–226)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: This helper creates a complete model specification for one OpenRouter model. A model specification is the project’s record of what the model is called, what it costs, what key it needs, and what capabilities it has.

**Data flow**: It receives a model id, price information, knowledge cutoff date, and optionally a context window size. It fills in OpenRouter-specific defaults such as provider name, API key environment variable, reasoning support, API surface, and client factory. It returns a `ModelSpec` that can be published in the extension manifest.

**Call relations**: The file uses this helper repeatedly when defining `OPENROUTER_MODEL_SPECS`. Those specs are later bundled by `manifest`, so the rest of the system can discover these models in the same way it discovers core models.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 249–250)

```
def manifest() -> Manifest
```

**Purpose**: This function exposes the extension’s public description to the host system. It says the extension’s name, version, and which OpenRouter models it provides.

**Data flow**: It reads the file’s constants for extension name, version, and model specifications. It packages them into a `Manifest` object and returns it.

**Call relations**: The extension loader calls this when registering available extensions. The returned manifest is the handoff point from this file to the wider model registry, letting OpenRouter models appear as selectable model options.

*Call graph*: 1 external calls (__init__).


### Turn orchestration
The turn engine coordinates model selection, request assembly, streaming, tool execution, transcript commits, and cost recording.

### `core/src/ufo/loop/engine.py`

`orchestration` · `during each agent turn`

An agent turn is more than one model call. A user message may trigger several rounds: the model thinks, asks to use tools, sees the tool results, maybe receives new incoming messages, and finally answers. This file makes that whole journey reliable. It uses DBOS steps, which are recorded units of work, like checkpoints in a video game. If the worker crashes, completed model calls and tool calls are replayed from the record instead of being run again, so tokens are not re-spent and side-effecting tools do not repeat their actions.

The TurnEngine is the main machine. It first claims the turn so only one worker owns it. It loads the prior transcript, adds time and sender context, applies extension hooks, checks spending and seat limits, streams model text live, dispatches tools, drains newly arrived messages between rounds, compacts long context when needed, and finally commits a durable terminal frame. It also parks a turn when spending or seat rules say it must pause, and it preserves inbound messages on failures so the conversation is not lost.

The file also protects the model’s context window. Large tool results are trimmed or written to files, images are stored outside the DBOS step log, and untrusted tool output is wrapped so the model treats it as data rather than instructions.

#### Function details

##### `_claim_turn`  (lines 164–206)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> bool
```

**Purpose**: Claims a queued or parked turn for one workflow attempt, so only that attempt is allowed to run it. Without this, two workers could answer the same user message or run the same tools twice.

**Data flow**: It receives a turn id and an attempt id. It opens a database transaction, locks the conversation row, changes the turn to running if it is claimable by this attempt, clears its dispatch marker, and removes a one-time resume task. It returns true if the claim succeeded and false if another execution already owns or finished the turn.

**Call relations**: TurnEngine._mark_running calls this at the start of TurnEngine.run. _claim_turn_with_handoff also uses it before checking whether the next queued turn should be handed off.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 6 external calls (and_, delete, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 217–274)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[bool, _TurnHandoff | None]
```

**Purpose**: Claims the current turn and, if possible, marks the next queued turn in the same conversation as ready to dispatch. This helps keep a conversation moving without letting turns run out of order.

**Data flow**: It receives a turn id and attempt id. First it tries to claim the turn. If that works, it reads the turn’s workspace and conversation, locks the conversation, finds the next queued turn, stamps it as enqueued when appropriate, and returns both the claim result and an optional handoff record for that next turn.

**Call relations**: It builds on _claim_turn. When it finds a next turn that needs scheduling, it creates a _TurnHandoff with the ids and workflow id that a caller can use to start the next workflow.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `ModelStreamError.__init__`  (lines 363–364)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Creates an error object for a model stream that failed after already producing some output or usage. It preserves the model provider’s error name, message, and any partial text that may be salvageable.

**Data flow**: It receives an error class name, a human-readable message, and optional partial output. It stores all three inside the exception arguments so they survive persistence and replay. It does not return anything beyond the constructed exception object.

**Call relations**: TurnEngine._stream_recovering_overflow creates this after TurnEngine._stream_once reports a stream error in its recorded result. Later code reads its properties to decide whether recovery is possible and what to commit.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 366–368)

```
def __str__(self) -> str
```

**Purpose**: Turns the model stream error into a readable string that includes the model provider’s error class. This matters because context-overflow detection may look at the text of the error.

**Data flow**: It reads the stored error class and message from the exception. It formats them as one string and deliberately leaves out the partial output. The result is a safe message for logs or terminal records.

**Call relations**: It is used whenever Python needs the exception as text, including generic error handling paths that record or inspect failures.


##### `ModelStreamError.model_error_class`  (lines 371–373)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original model provider error class name. Callers use it to distinguish a recoverable truncation from other model failures.

**Data flow**: It reads the first stored exception argument and returns it unchanged. It does not modify anything.

**Call relations**: TurnEngine._model_round checks this after TurnEngine._stream_recovering_overflow raises the error. TurnEngine._commit_once also uses it when recording the terminal failure.


##### `ModelStreamError.partial_output`  (lines 376–378)

```
def partial_output(self) -> str
```

**Purpose**: Returns any text or raw tool-call fragments the model produced before the stream failed. This lets the engine save paid-for partial work instead of throwing it away.

**Data flow**: It reads the third stored exception argument and returns it. Nothing else changes.

**Call relations**: TurnEngine._model_round uses this when recovering from a response that was cut off, writing the partial output to a workspace file when possible.


##### `ModelStreamError.model_error_message`  (lines 381–383)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original message from the model provider. This keeps terminal error reporting close to what actually happened upstream.

**Data flow**: It reads the second stored exception argument and returns it. It has no side effects.

**Call relations**: TurnEngine._commit_once uses this when building a failed terminal frame for a model stream error.


##### `TurnParked.__init__`  (lines 390–392)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates the special exception used when a running turn must pause instead of finish. The message explains to the user-facing surface why the turn was parked.

**Data flow**: It receives a message, stores it in the normal exception data, and also saves it as a named field. The constructed exception is later caught by the main run loop.

**Call relations**: TurnEngine._enforce_spend raises this when a seat or spending gate blocks continued work. TurnEngine.run catches it and calls TurnEngine._park.

*Call graph*: called by 1 (_enforce_spend).


##### `_dispatch_segments`  (lines 395–419)

```
def _dispatch_segments(tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]) -> Iterator[tuple[ToolUseBlock, ...]]
```

**Purpose**: Splits a model’s tool calls into safe execution groups. Tools marked safe to run in parallel can run together, while other tools become barriers so reads and writes do not race each other.

**Data flow**: It receives the tool registry and the ordered tool calls from one model round. It checks each tool’s parallel-safety flag, groups consecutive safe calls up to the configured limit, and yields groups in the original order. Unknown tools are treated as not safe.

**Call relations**: TurnEngine._model_round uses these groups before dispatching tools. Each yielded group is then either run concurrently or, for one-call groups, effectively in sequence.

*Call graph*: calls 1 internal fn (get); called by 1 (_model_round).


##### `_parse_args`  (lines 422–424)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Turns streamed fragments of a tool-call JSON argument into a Python dictionary. If the model supplied no argument text, it treats that as an empty input object.

**Data flow**: It receives a list of partial JSON strings, joins them, and parses the result with JSON decoding. It returns a dictionary, or an empty dictionary when the joined text is blank.

**Call relations**: TurnEngine._stream_once uses this after the model stream finishes, when it converts accumulated tool-call deltas into ToolUseBlock objects.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 427–437)

```
def _context_tag(context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the small context header placed before a member’s message, including the message time and sender when known. This gives the model a stable sense of when and from whom a message arrived.

**Data flow**: It receives optional turn context and the admission time. It chooses the sender’s timezone when available, formats the time, adds the sender line if present, and returns a text block wrapped in a <context> tag.

**Call relations**: TranscriptRepair.load_messages uses it for the founding inbound message. TurnEngine._render_arrival uses it for queued messages absorbed during a running turn.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 440–445)

```
def _bounded(content: str) -> str
```

**Purpose**: Shortens overly large text to the maximum tool-result size and appends a notice saying how much was cut. This protects the model context from being flooded by one huge error or result.

**Data flow**: It receives text. If the text is already small enough, it returns it unchanged. Otherwise it returns the leading portion plus a truncation marker with the dropped character count.

**Call relations**: TurnEngine._dispatch_step uses it for large error text or failed offloads. TurnEngine._model_round uses it when turning a finish-tool validation problem into a tool error result.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_loaded_skill_closures`  (lines 448–490)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedSkill, ...]]
```

**Purpose**: Figures out which skills are already present in the current message window. This prevents the engine from re-injecting skill instructions that the model can still see.

**Data flow**: It receives the current messages and the skill registry. It looks for completed load_skill calls and their matching results, reads the requested skill name from the original tool input, asks the registry for that skill’s closure, and yields the loaded skill groups that really completed and were not truncated.

**Call relations**: TurnEngine._reseed_loaded_skills calls this before and after compaction. The result feeds the compaction and tool context tracker that decides which skill instructions should remain suppressed.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 493–511)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts a structured payload when a tool call was the successful final act of a round, such as asking the user or requesting credentials. It reads the handler’s result rather than the raw model input, so hook changes are honored.

**Data flow**: It receives the round’s tool calls, their results, the expected tool name, and a validation model. It checks that the last call and last result match the requested final tool, parses the JSON payload from the result text, validates it, and returns the structured object or None.

**Call relations**: TurnEngine._model_round calls this after tool dispatches. The extracted question, credential request, or connect request is passed up to TurnEngine.run and eventually into the terminal frame.

*Call graph*: called by 1 (_model_round); 1 external calls (loads).


##### `_total_usage`  (lines 514–520)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many token-usage events into one total usage record. Billing and live cost reporting both need this single summed view.

**Data flow**: It receives a list of usage records. It sums input tokens, output tokens, cache-read tokens, and cache-write tokens separately, then returns one Usage object with those totals.

**Call relations**: TurnEngine._publish_cost, TurnEngine._enforce_spend, TurnEngine._commit_once, TurnEngine._park, and TurnEngine._bill_cancelled all call this before pricing or recording usage.

*Call graph*: called by 5 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 535–559)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a terminal result for a turn that already finished but whose live notification may have been lost. This lets a waiting client receive the answer even after a crash at the wrong moment.

**Data flow**: It reads the turn’s stored terminal frame from the database. If there is no terminal yet, it returns None. If there is one, it persists the inbound transcript as a safety measure, publishes the terminal to the live hub, logs publish failures without raising, and returns the frame.

**Call relations**: TurnEngine._resolve_unclaimed reaches this path when TurnEngine.run cannot claim the turn. It uses TranscriptRepair.persist_inbound before publishing the Terminal frame.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 561–568)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the completed conversation after a successful answer. It adds the assistant’s final answer to the message list before saving.

**Data flow**: It receives the messages the model saw, the final answer, the system prompt, and any injected system text. It appends an assistant message, then passes the full conversation to write_conversation. It returns nothing.

**Call relations**: TurnEngine._persist_transcript delegates here after TurnEngine.run commits a done terminal. It hands off the actual durable write to TranscriptRepair.write_conversation.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 570–577)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=()) -> None
```

**Purpose**: Saves only the user-side messages when a turn ends without a normal final answer. This preserves what people said while avoiding durable storage of partial or failed assistant text.

**Data flow**: It optionally receives additional arrival messages. It loads the prior transcript plus the founding inbound, appends the arrivals, and writes that conversation. The durable transcript is advanced enough for the next turn to see the user messages.

**Call relations**: TranscriptRepair.resolve calls this as repair work. TurnEngine._persist_inbound delegates here on parked, failed, cancelled, or non-done terminal paths.

*Call graph*: calls 2 internal fn (load_messages, write_conversation); called by 1 (resolve).


##### `TranscriptRepair.load_messages`  (lines 579–587)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the conversation history that should be shown to the model for this turn. For normal member turns, it prefixes the inbound text with time and sender context.

**Data flow**: It reads the turn’s inbound text and prior transcript messages. If the turn is not a subagent turn, it adds a context tag before the inbound. It returns prior messages followed by the current user message.

**Call relations**: TranscriptRepair.persist_inbound uses this when saving user messages. TurnEngine._load_messages delegates to it before model work starts.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 589–595)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the stored transcript from before this turn. It avoids reading this turn’s own previous write during replay.

**Data flow**: It asks the transcript store for the current stored conversation. If there is none, or if the stored sequence is at or beyond this turn’s sequence, it returns an empty tuple. Otherwise it returns the stored messages.

**Call relations**: TranscriptRepair.load_messages calls this to assemble the model’s starting conversation window.

*Call graph*: called by 1 (load_messages).


##### `TranscriptRepair.write_conversation`  (lines 597–617)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a conversation snapshot to the transcript store, retrying a few times on failure. This makes transcript persistence more tolerant of temporary storage problems.

**Data flow**: It receives messages plus optional system and injected context. It builds a Conversation object and tries to write it. On failure it logs the attempt, sleeps briefly, and retries; if all attempts fail, it stops without raising further.

**Call relations**: TranscriptRepair.persist_transcript and TranscriptRepair.persist_inbound both hand their final conversation shape to this writer.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `TurnEngine.__post_init__`  (lines 653–660)

```
def __post_init__(self) -> None
```

**Purpose**: Checks a subagent configuration rule after the engine is built. If the subagent needs the special finish tool, the normal tool registry must not already contain a tool with that same name.

**Data flow**: It reads output_model and the tool registry. If there is no output model, or no conflicting finish tool, it does nothing. If a conflict exists, it raises a ValueError before the turn runs.

**Call relations**: This dataclass hook runs automatically when a TurnEngine instance is created. It protects later TurnEngine._stream_once and TurnEngine._force_finish logic from ambiguous tool definitions.


##### `TurnEngine.run`  (lines 662–812)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs the entire turn lifecycle. It is the main body that claims work, prepares context, drives model/tool rounds, commits the result, and cleans up.

**Data flow**: It starts metrics and logs, creates usage and arrival trackers, builds a ToolContext, claims the turn, applies prompt hooks, loads messages, then repeatedly calls _model_round until it can commit a terminal frame. On normal completion it writes the transcript; on parking, cancellation, or failure it bills what was used, releases or preserves arrivals as needed, publishes state, and drains cleanup tasks.

**Call relations**: This is the central caller of most TurnEngine helpers, including _mark_running, _load_messages, _model_round, _commit, _park, _bill_cancelled, _persist_transcript, and _persist_inbound. It is the workflow body described by the file’s header comments.

*Call graph*: calls 11 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _release_unabsorbed, _resolve_unclaimed (+1 more)); 7 external calls (__init__, __init__, __init__, __init__, emit_metric, log, turn_span).


##### `TurnEngine.run.rank_find`  (lines 675–692)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Provides the browser find tool with a host-side model call for ranking elements. It lets browser tooling ask the model for help without running that model call inside the sandbox.

**Data flow**: It receives a system prompt and user text. It builds a small ModelRequest, streams the model response, appends text deltas into a string, and records any usage events in the surrounding turn’s usage list. It returns the combined text.

**Call relations**: TurnEngine.run places this nested function into ToolContext as the find callback. Browser-related tools can call it during TurnEngine._dispatch_step through their handler context.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine._scheduled_system`  (lines 814–841)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. Scheduled work has no fresh user sitting there, so relevant remembered context can help the agent act sensibly.

**Data flow**: It receives the current system prompt. It searches memory for the turn’s conversation and inbound text within a short timeout. If search fails or finds nothing, it returns the original system prompt; otherwise it appends escaped recalled-memory lines in a dedicated tag.

**Call relations**: TurnEngine.run calls this only when the turn came from scheduled admission. It depends on the memory search service being wired for scheduled turns.

*Call graph*: called by 1 (run); 3 external calls (timeout, escape, log).


##### `TurnEngine._mark_running`  (lines 843–851)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn as running for the engine’s workflow attempt. It is the object-level wrapper around the database claim.

**Data flow**: It reads the engine’s turn id and attempt id, passes them to _claim_turn, and returns whether the claim succeeded. It does not otherwise change engine state.

**Call relations**: TurnEngine.run calls this near the start. If it returns false, the run switches to TurnEngine._resolve_unclaimed instead of doing model work.

*Call graph*: calls 1 internal fn (_claim_turn); called by 1 (run).


##### `TurnEngine._repair`  (lines 853–854)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Creates a small helper object for transcript and terminal repair work. This keeps repair behavior separate from the main engine body.

**Data flow**: It reads the engine’s turn, transcript store, and hub, and returns a TranscriptRepair containing those three things. It does not perform I/O itself.

**Call relations**: TurnEngine._load_messages, _persist_transcript, _persist_inbound, and _resolve_unclaimed all call this before delegating to TranscriptRepair methods.

*Call graph*: called by 4 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 856–857)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the starting message history for the turn. It is a convenience wrapper so the engine does not duplicate transcript-repair setup.

**Data flow**: It creates a TranscriptRepair with _repair and asks it to load messages. The returned tuple becomes the model’s initial conversation window.

**Call relations**: TurnEngine.run calls this when preparing normal and denied-prompt paths.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._model_round`  (lines 859–1010)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID]) -> tuple[tuple[Message,
```

**Purpose**: Drives the repeated model-and-tool loop until the model produces a final answer or the round limit is reached. This is where most turn intelligence is coordinated.

**Data flow**: It receives the current tool context, messages, usage list, system prompt, and arrival trackers. Each loop absorbs queued arrivals, checks spending, refreshes loaded-skill state, compacts context, streams one model round, publishes cost, dispatches tool calls in safe groups, and feeds results back as user messages. It returns the final message window, answer text, and any pending ask-user, credential, or connect request.

**Call relations**: TurnEngine.run calls this inside its main completion loop. It calls helpers such as _absorb_arrivals, _enforce_spend, _stream_recovering_overflow, _dispatch, _force_final, _force_finish, _publish_cost, _offload, and _reseed_loaded_skills.

*Call graph*: calls 12 internal fn (_absorb_arrivals, _dispatch, _enforce_spend, _force_final, _force_finish, _offload, _publish_cost, _reseed_loaded_skills, _stream_recovering_overflow, _bounded (+2 more)); called by 1 (run); 6 external calls (__init__, __init__, __init__, gather, emit_metric, log).


##### `TurnEngine._absorb_arrivals`  (lines 1012–1034)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID]) -> tuple[Message, ...]
```

**Purpose**: Adds new inbound messages that arrived while the turn was already running. This lets a live conversation respond to late user messages before closing.

**Data flow**: It receives the current messages plus mutable logs of arrivals and absorbed ids. For normal member turns, it claims queued arrivals, records each claimed id, turns each rendered arrival into a user message, appends it to the arrival log and message window, and returns the expanded messages. Subagent turns skip this entirely.

**Call relations**: TurnEngine._model_round calls this at the start of each round. It depends on the DBOS-recorded TurnEngine._claim_arrivals step.

*Call graph*: calls 1 internal fn (_claim_arrivals); called by 1 (_model_round); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 1036–1059)

```
async def _render_arrival(self, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> str | None
```

**Purpose**: Turns one queued inbound message into exactly the text the model should see. It applies the same prompt-submission hook rules as the founding user message.

**Data flow**: It receives the arrival body, context, speaker id, and creation time. It fires the user_prompt_submit hook; if denied, it returns None. Otherwise it prefixes a context tag, optionally adds injected context in its own fenced tag, and returns the rendered string.

**Call relations**: TurnEngine._claim_arrivals calls this for each claimed queue row. The returned string is stored in the Arrival object so workflow replay does not fire the hook again.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 1062–1104)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Claims pending inbound queue rows for this running turn in a replay-safe way. It ensures arrivals are consumed once, and replay sees the same batch.

**Data flow**: It receives the ids already absorbed by this execution. In a database transaction it stamps unconsumed or not-yet-absorbed rows with this turn id, returns their message fields, sorts them by sequence, renders each arrival, and returns Arrival records containing the id and rendered text or None.

**Call relations**: TurnEngine._absorb_arrivals calls this DBOS step each round. It calls TurnEngine._render_arrival inside the step so hook output is included in the recorded step result.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 6 external calls (__init__, model_validate, and_, or_, update, workspace_tx).


##### `TurnEngine._release_unabsorbed`  (lines 1106–1125)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns arrival rows that were stamped by this turn but not safely absorbed. This prevents messages from being stranded after a failure or cancellation.

**Data flow**: It receives the ids known to have been absorbed. It tries to clear consumed_turn_id for other rows stamped with this turn id. If the database update fails, it logs the problem and lets later recovery handle any leftovers.

**Call relations**: TurnEngine.run calls this in cancellation and failure paths. It complements TurnEngine._claim_arrivals.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 1127–1155)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Gets a closing answer when the agent has used up its allowed tool rounds. Rather than failing immediately, it asks the model for the best final response with no more tool use.

**Data flow**: It receives messages, usage events, and the system prompt. It records exhaustion metrics, checks spend, compacts if needed, then either forces a subagent finish call or appends a final-answer prompt and streams one no-tools model round. It publishes updated cost and returns the final messages and text.

**Call relations**: TurnEngine._model_round calls this when its loop reaches max_rounds. It may call TurnEngine._force_finish for subagents, or TurnEngine._stream_recovering_overflow for ordinary turns.

*Call graph*: calls 4 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_recovering_overflow); called by 1 (_model_round); 3 external calls (__init__, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 1157–1182)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to close with the structured finish tool. This keeps child-agent answers shaped like the schema the parent expects.

**Data flow**: It receives messages, usage events, and the system prompt. It streams one model round with only the finish tool offered and required. If the model returns one valid finish call, it validates and returns its JSON answer; otherwise it raises an error.

**Call relations**: TurnEngine._model_round calls this when a subagent answers in prose instead of using finish. TurnEngine._force_final calls it when a subagent runs out of rounds.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 1184–1222)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False) -> tuple[tuple[Message, ...], str,
```

**Purpose**: Runs one model round and retries once after forced compaction if the provider says the context is too large. It is the safety valve for context-window overflow.

**Data flow**: It receives messages, usage events, system prompt, and tool-offering options. It calls _stream_once, adds usage, and returns text and tool calls when successful. If the stream reports an error, it raises ModelStreamError; if the error looks like context overflow, it forces compaction, adds compaction usage, reseeds loaded skills, tries one more stream, and returns the compacted messages plus result.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish all call this instead of calling _stream_once directly. It calls _reseed_loaded_skills after forced compaction.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 3 external calls (is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 1224–1261)

```
async def _enforce_spend(self, usage_events: list[Usage]) -> None
```

**Purpose**: Checks whether the running turn is still allowed to spend more tokens or continue under seat rules. If not, it parks the turn so work already done is preserved.

**Data flow**: It receives the usage events accumulated so far. It may check seat admission for the relevant member, then skips accounting work if no caps apply. Otherwise it prices the in-flight usage, asks the spend evaluator for a decision, and raises TurnParked if the answer is not allow.

**Call relations**: TurnEngine._model_round calls this before each model round, and TurnEngine._force_final calls it before a forced close. TurnEngine.run catches TurnParked and hands control to _park.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 6 external calls (__init__, __init__, applicable_caps_absent, workspace_tx, gate_member, seat_gate_absent).


##### `TurnEngine._stream_once`  (lines 1264–1371)

```
async def _stream_once(self, messages: tuple[Message, ...], system: str, offer_tools: bool=True, force_finish: bool=False) -> StreamResult
```

**Purpose**: Performs one actual model streaming request as a DBOS-recorded step. It streams live text to the hub while also returning the full round result for replay.

**Data flow**: It receives messages, system prompt, and flags for offering or forcing tools. It builds the model request, collects text deltas, tool-call starts and JSON fragments, and usage events. It periodically flushes text to the live hub. On provider error it returns a StreamResult containing usage and partial output; on success it parses tool-call arguments and returns text, tool calls, and usage.

**Call relations**: TurnEngine._stream_recovering_overflow is its only direct caller. The step result feeds later tool dispatch and is replayed after crashes instead of calling the model again.

*Call graph*: calls 1 internal fn (_parse_args); called by 1 (_stream_recovering_overflow); 5 external calls (__init__, __init__, __init__, __init__, monotonic).


##### `TurnEngine._stream_once.flush`  (lines 1320–1326)

```
async def flush() -> None
```

**Purpose**: Publishes buffered model text to the live hub during a stream. It keeps users from waiting until the whole model response is done.

**Data flow**: It reads the local text buffer and pending byte count. If there is buffered text, it joins and publishes it as a TextDelta, clears the buffer, resets the count, and updates the last-flush time.

**Call relations**: This nested helper is used inside TurnEngine._stream_once while consuming model stream events and once more at the end of the stream.

*Call graph*: 2 external calls (__init__, monotonic).


##### `TurnEngine._publish_cost`  (lines 1373–1386)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes the current estimated cost and token count for the turn. This supports live cost meters while the turn is still running.

**Data flow**: It receives usage events, totals them, computes tokens and micro-dollar cost from the pricing table, wraps that in a CostTick frame, and publishes it. Publish failures are swallowed by _publish.

**Call relations**: TurnEngine._model_round calls it after model rounds. TurnEngine._force_final and _force_finish call it after forced closing rounds.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 1388–1399)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the tracker of skill instructions currently visible to the model. This avoids duplicate skill loading after compaction or transcript changes.

**Data flow**: It receives the current message window. It derives loaded skill closures from those messages, combines them with preloaded skills, and reseeds the compaction loaded-skills tracker. It returns nothing.

**Call relations**: TurnEngine._model_round calls it before and after compaction. TurnEngine._stream_recovering_overflow calls it after forced overflow compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 2 (_model_round, _stream_recovering_overflow).


##### `TurnEngine._dispatch`  (lines 1401–1428)

```
async def _dispatch(self, context: ToolContext, call: ToolUseBlock) -> ToolResultBlock
```

**Purpose**: Turns one model tool call into the ToolResultBlock that will be fed back to the model. It rehydrates any images that were stored outside the recorded step log.

**Data flow**: It receives a ToolContext and ToolUseBlock. It calls the recorded _dispatch_step, and if there are no image references, returns a text result. If images were offloaded, it reads each blob, rebuilds image blocks, combines them with any text, and returns the full tool result.

**Call relations**: TurnEngine._model_round calls this for each tool call segment. It delegates the actual tool execution to TurnEngine._dispatch_step.

*Call graph*: calls 1 internal fn (_dispatch_step); called by 1 (_model_round); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._offload`  (lines 1430–1455)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text content into the sandbox’s tool-output directory and returns the file path. This keeps huge tool results or salvaged partial model output out of the prompt.

**Data flow**: It receives a filename and text content. It ensures the output directory exists, writes the bytes to the sandbox path, and returns that path. If anything fails, it logs and emits a metric, then returns None so callers can degrade gracefully.

**Call relations**: TurnEngine._dispatch_step uses it for oversized tool output. TurnEngine._model_round uses it to save truncated model partial output during recovery.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._dispatch_step`  (lines 1458–1587)

```
async def _dispatch_step(self, context: ToolContext, call: ToolUseBlock) -> DispatchResult
```

**Purpose**: Runs one tool call end to end as a DBOS-recorded step. Recording it prevents side-effecting tools from running twice after a crash.

**Data flow**: It receives the tool context and model tool call. It publishes activity, validates the tool name and arguments, fires pre-tool hooks, runs the tool handler with an idempotency key when needed, gathers text and images, bounds or offloads large text, wraps untrusted content, fires post-tool hooks, shrinks large images, stores image bytes in the blob store, and returns a DispatchResult with text, error status, and image references.

**Call relations**: TurnEngine._dispatch calls this and then reassembles the model-facing ToolResultBlock. It calls _publish_activity, _offload, _bounded_image, and _bounded as part of tool execution.

*Call graph*: calls 4 internal fn (_bounded_image, _offload, _publish_activity, _bounded); called by 1 (_dispatch); 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, replace).


##### `TurnEngine._bounded_image`  (lines 1589–1617)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks a tool-result image if its edges are too large for safe model input. This avoids wasting context and avoids provider image-size failures.

**Data flow**: It receives an ImageBlock containing base64 image data. It decodes and opens the image in a worker thread, returns it unchanged if already small, otherwise thumbnails it, saves it in an appropriate format, re-encodes it, and returns a new ImageBlock. If decoding or resizing fails, it logs and returns the original image.

**Call relations**: TurnEngine._dispatch_step calls this before writing successful tool images to the blob store.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._publish_activity`  (lines 1619–1638)

```
async def _publish_activity(self, call: ToolUseBlock) -> None
```

**Purpose**: Publishes a live activity frame when a tool call begins. This lets user interfaces show what the agent is doing during long turns.

**Data flow**: It receives a tool call. For load_skill it publishes the skill name. For other tools it builds a short JSON preview of the input and uses the model-provided user description when present, then publishes a ToolCall frame.

**Call relations**: TurnEngine._dispatch_step calls this before validating and running the tool. It uses TurnEngine._publish so hub failures do not fail the turn.

*Call graph*: calls 1 internal fn (_publish); called by 1 (_dispatch_step); 3 external calls (__init__, __init__, dumps).


##### `TurnEngine._commit`  (lines 1640–1690)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request: CredentialRequest |
```

**Purpose**: Durably commits the turn’s terminal state and publishes it live. It retries database commit failures so a final answer is not lost during a temporary outage.

**Data flow**: It receives the desired status, usage events, answer or error details, optional request payloads, and arrival-safety options. It repeatedly calls _commit_once with backoff until it gets a result. If _commit_once returns None because unseen arrivals exist, it returns None; otherwise it publishes the terminal frame, emits metrics, logs, and returns the frame.

**Call relations**: TurnEngine.run calls this for done, denied, and failed endings. It hands the durable write to TurnEngine._commit_once and the live notification to TurnEngine._publish.

*Call graph*: calls 2 internal fn (_commit_once, _publish); called by 1 (run); 4 external calls (__init__, sleep, emit_metric, log).


##### `TurnEngine._commit_once`  (lines 1692–1790)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction that records usage and stores the terminal frame. This is the authoritative end state of a turn.

**Data flow**: It receives status, usage, answer, error, optional question or connection requests, and arrival guards. It totals usage, optionally locks the conversation and checks for unabsorbed arrivals, records billing, reads the final cost, builds a TerminalFrame, updates the turn if still non-terminal, and returns either that frame or the already-stored terminal if another path committed first.

**Call relations**: TurnEngine._commit calls this inside its retry loop. It uses accounting functions and database locks to keep billing, arrival safety, and terminal state consistent.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 9 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx).


##### `TurnEngine._park`  (lines 1792–1829)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Moves a running turn into a parked, resumable state when spending or seat rules stop it. Parking preserves work already done instead of failing the turn.

**Data flow**: It receives a human-readable message and usage events. In one database transaction it marks the turn parked, records consumed usage for this attempt, and releases inbound messages claimed by the turn. If the update succeeded, it publishes a Parked frame and logs metrics.

**Call relations**: TurnEngine.run calls this after catching TurnParked from _enforce_spend. A later resume workflow can re-run the turn from its durable state.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 1831–1840)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes a live frame without letting publishing problems break the turn. Durable database state remains the source of truth.

**Data flow**: It receives a live frame such as text, cost, activity, parked, or terminal. It tries to publish it to the hub for this turn id. If publishing fails, it logs the error and returns normally.

**Call relations**: TurnEngine._commit, _park, _publish_activity, and _publish_cost all use this shared safe-publish path.

*Call graph*: called by 4 (_commit, _park, _publish_activity, _publish_cost); 1 external calls (log).


##### `TurnEngine._bill_cancelled`  (lines 1842–1861)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for a turn that was cancelled after spending tokens. Even cancelled work should count toward usage.

**Data flow**: It receives usage events, totals them, and tries to record usage in the database for this turn and attempt. If billing fails, it logs the failure but does not block cancellation.

**Call relations**: TurnEngine.run calls this when DBOS or asyncio cancellation interrupts the turn.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 1863–1868)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles the case where this engine instance could not claim the turn. It either republishes an already committed terminal or steps aside while another execution continues.

**Data flow**: It creates a TranscriptRepair helper and asks it to resolve the turn. The result is a TerminalFrame when the turn is already finished, or None when there is nothing this duplicate should do.

**Call relations**: TurnEngine.run calls this immediately after _mark_running returns false. It delegates to TranscriptRepair.resolve through _repair.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._persist_transcript`  (lines 1870–1873)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Saves the full successful transcript through the TranscriptRepair helper. It keeps the engine’s main run method from knowing transcript-write details.

**Data flow**: It receives messages, answer, system prompt, and injected context. It creates a repair helper and delegates persistence. It returns nothing.

**Call relations**: TurnEngine.run calls this after a done terminal is committed. TranscriptRepair.persist_transcript performs the actual conversation write.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._persist_inbound`  (lines 1875–1876)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=()) -> None
```

**Purpose**: Saves user messages when the turn does not produce a normal completed assistant answer. This protects conversation continuity after errors, parking, or certain non-done terminals.

**Data flow**: It receives optional arrival messages, creates a TranscriptRepair helper, and delegates inbound-only persistence. It returns nothing.

**Call relations**: TurnEngine.run calls this on failure, cancellation, parking-related paths, and non-done terminal paths. TranscriptRepair.persist_inbound does the actual transcript assembly and write.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


### Provider stream adapters
Provider adapters translate UFO model requests into vendor APIs and convert streamed provider responses back into standard UFO model events.

### `core/src/ufo/models/anthropic.py`

`io_transport` · `request handling`

This file lets the rest of the project talk to Anthropic without needing to know Anthropic's exact request and streaming formats. UFO has its own plain model types, such as text blocks, image blocks, tool calls, and usage records. Anthropic expects similar information, but shaped differently. This file translates between the two.

The small helper functions turn UFO content into Anthropic content. Text stays text. Images become base64 image objects. Tool uses and tool results are reshaped into the structures Anthropic's API accepts. This is like repacking a suitcase for a different airline: the same items are inside, but the labels and compartments must match the carrier's rules.

The main piece is `AnthropicClient.complete`. It builds the API request, including the system prompt, messages, optional tools, reasoning settings, cache hints, and token limit. It then opens a streaming response. As Anthropic sends pieces back, this method yields UFO events: text fragments, tool-call starts, tool-call argument fragments, and finally one usage record.

It is careful about failures. Timeouts and temporary provider errors are retried before anything has been streamed. Once output has started, errors are raised immediately so callers do not accidentally mix partial old output with a new retry. It also treats refusals and max-token cutoffs as explicit errors, while allowing a few retries for completely empty responses.

#### Function details

##### `anthropic_sdk_client`  (lines 41–45)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the official Anthropic asynchronous client used to make API calls. It deliberately turns off the SDK's built-in retries, because this file has its own retry rules that match UFO's streaming behavior.

**Data flow**: It receives an Anthropic API key. It puts that key, a fixed timeout, and zero SDK retries into Anthropic's client constructor. It returns a ready-to-use asynchronous Anthropic client object.

**Call relations**: This function is a setup helper for code that needs an Anthropic client. It hands off to Anthropic's official SDK constructor, while leaving retry decisions to `AnthropicClient.complete` later during actual requests.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 48–52)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts UFO's image representation into the shape Anthropic expects. It is used whenever an image appears in a message or inside a tool result.

**Data flow**: It receives an `ImageSource`, which contains the image's media type and base64 data. It wraps those fields in Anthropic's image-content dictionary format. The returned value is ready to be placed in an Anthropic message.

**Call relations**: `anthropic_content` calls this when normal message content includes an image. `_anthropic_tool_result_part` also calls it when a tool result includes an image, so image formatting stays consistent in both paths.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 55–60)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic's format. Tool results can contain text or images, and this helper turns each part into the correct API-ready object.

**Data flow**: It receives one tool-result content block. If the block is text, it returns a text dictionary. If the block is an image, it sends the image source through `_anthropic_image` and returns the formatted image dictionary.

**Call relations**: `anthropic_content` uses this helper while converting a full tool-result block. When it sees image content, this helper delegates to `_anthropic_image` so image conversion is not duplicated.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 63–88)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Translates UFO message content into Anthropic message content. It supports plain strings as well as structured content containing text, images, tool uses, and tool results.

**Data flow**: It receives either a simple string or a tuple of UFO content blocks. A string is returned unchanged. Structured blocks are walked one by one and converted into Anthropic dictionaries; images and tool-result parts are passed to the smaller conversion helpers. The result is either the original string or a list Anthropic can accept in a message.

**Call relations**: `AnthropicClient.complete` calls this while building the outgoing Anthropic request. This function, in turn, calls `_anthropic_image` and `_anthropic_tool_result_part` so each nested kind of content is translated correctly before the request is sent.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 96–248)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to Anthropic and streams back UFO model events. It is the main runtime path for Anthropic completions, including text output, tool calls, retry behavior, refusal handling, truncation detection, and final token usage.

**Data flow**: It receives a `ModelRequest` containing the model name, system prompt, messages, tools, reasoning preference, and token limit. It trims images where needed, converts message content with `anthropic_content`, adds tool and reasoning settings, and starts an Anthropic streaming request. As stream events arrive, it turns them into UFO events: tool-call starts, text deltas, tool-call argument deltas, and finally a `Usage` record. It may instead raise an error if Anthropic refuses, truncates at the token limit, times out after retries, or returns an unusable stream.

**Call relations**: This method is called when the rest of UFO wants an Anthropic model response. It relies on `anthropic_content` to prepare outgoing messages, then talks to Anthropic's streaming API. During the stream it creates UFO event objects such as `TextDelta`, `ToolCallStart`, and `ToolCallDelta` for callers to consume immediately. At the end it creates `Usage` so the caller knows how many tokens were used. If temporary failures happen before any event is yielded, it waits with `asyncio.sleep` and retries; after output has begun, it raises the failure instead of hiding it.

*Call graph*: calls 1 internal fn (anthropic_content); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, trim_images, log).


### `core/src/ufo/models/openai.py`

`io_transport` · `request handling`

This file lets the rest of the project talk to OpenAI and OpenAI-compatible services without caring about their exact wire format. A “wire format” is the shape of the data sent over the network. UFO has its own common message types for text, images, tool calls, tool results, refusals, and token usage. OpenAI has different shapes for the older Chat Completions API and the newer Responses API. This file translates between those worlds.

The main class, OpenAIClient, is given an OpenAI SDK client and a ModelSpec, which says which API surface the model expects. That matters because some models only accept certain combinations of options, such as tools and reasoning settings, on one API but not the other.

Before sending a request, helper functions convert UFO messages into OpenAI-compatible messages or Responses input items. They also turn inline images into data URLs and split tool results into the forms OpenAI accepts. During streaming, the client yields small events as soon as they arrive: text chunks, tool-call starts, tool-call argument chunks, and finally usage information. It also retries temporary provider failures before any output has been produced, treats truncation and refusals as explicit errors, and guards against missing usage data.

#### Function details

##### `openai_sdk_client`  (lines 71–77)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: This creates the actual asynchronous OpenAI SDK client used to make network requests. It deliberately turns off the SDK's built-in retries so this file's own retry rules are the single source of truth.

**Data flow**: It receives an API key and optionally a base URL for an OpenAI-compatible service. It passes those into OpenAI's AsyncOpenAI constructor, along with this file's timeout and no SDK retries. The result is a ready-to-use client object for later model calls.

**Call relations**: This helper sits at setup time, before requests are streamed. Its one direct handoff is to OpenAI's AsyncOpenAI constructor, which builds the network client that OpenAIClient will later use.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_openai_image`  (lines 80–84)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: This turns UFO's image source into the image shape expected by OpenAI's Chat Completions API. It packages the image as a data URL, which is like putting the image bytes directly inside the message instead of pointing to a separate web address.

**Data flow**: It receives an ImageSource containing a media type and base64 image data. It combines those into a data URL and wraps it in OpenAI's image_url message format. The output is a small dictionary ready to place inside an OpenAI message.

**Call relations**: It is used by _openai_tool_result when tool output contains images, and by openai_messages when ordinary user or assistant messages contain images. In both cases, it is the small adapter that makes an internal image block acceptable to OpenAI.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 87–103)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: This prepares a tool result for OpenAI chat messages, where tool messages can carry text but not images. If a tool returned images, it separates them so the caller can send them in a following user message.

**Data flow**: It receives either a plain string result or a tuple of result blocks such as text and images. Plain text passes through unchanged with no images. Mixed content is split into one joined text string and a list of OpenAI image parts. The output is that text plus the lifted-out images.

**Call relations**: openai_messages calls this while translating a conversation for the Chat Completions API. When this helper finds images, it calls _openai_image to convert them, then hands both text and image pieces back so openai_messages can place them where OpenAI allows them.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 106–159)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: This converts UFO's conversation format into the message list used by OpenAI's Chat Completions API. It covers ordinary text, images, tool calls, and tool results.

**Data flow**: It receives the system instruction and a tuple of UFO messages. It first trims images using the shared trim_images helper, then walks through each message. Text becomes simple content, images become OpenAI image parts, tool uses become OpenAI function tool calls, and tool results become tool messages. If a tool result contains images, those images are moved into a trailing user message. The output is a list of dictionaries ready for chat.completions.create.

**Call relations**: OpenAIClient._chat_kwargs calls this when building a Chat Completions request. This function relies on _openai_image for image conversion, _openai_tool_result for tool outputs, json.dumps to serialize tool arguments, and trim_images to keep image history within the project's limits.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 162–228)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: This converts UFO messages into the item format required by OpenAI's Responses API. It performs the same basic translation as openai_messages, but into the newer Responses API shapes.

**Data flow**: It receives a tuple of UFO messages and reads their roles and content blocks after image trimming. Text and images become typed Responses input content. Tool calls become function_call items with JSON arguments. Tool results become function_call_output items, preserving text and image content when possible and marking tool errors in the text. The output is a list of Responses API input items.

**Call relations**: responses_request calls this while assembling the full Responses API request. Inside, it creates the typed OpenAI Responses objects and uses json.dumps when a tool call's structured arguments need to become a JSON string.

*Call graph*: called by 1 (responses_request); 9 external calls (dumps, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, trim_images).


##### `responses_request`  (lines 231–256)

```
def responses_request(request: ModelRequest) -> dict[str, Any]
```

**Purpose**: This builds the full request body for OpenAI's Responses API from UFO's ModelRequest. It adds the model name, instructions, input messages, token limit, streaming flag, reasoning setting, and available tools.

**Data flow**: It receives a ModelRequest. It converts the message history with responses_input, copies request-level settings such as model and max tokens, and conditionally adds reasoning and tool configuration. The output is a dictionary of keyword arguments that can be passed directly to client.responses.create.

**Call relations**: OpenAIClient._complete_responses calls this immediately before making a Responses API streaming request. It hands message conversion to responses_input and wraps each UFO tool definition as an OpenAI FunctionToolParam.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `OpenAIClient.complete`  (lines 268–271)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the public entry point for asking this client to complete a model request. It chooses the correct OpenAI API style based on the model specification.

**Data flow**: It receives a ModelRequest. It reads self.spec.api_surface to decide whether this model should use the Responses API or the Chat Completions API. It returns an asynchronous stream of ModelEvent objects from the chosen private method.

**Call relations**: Callers use this method instead of choosing an OpenAI endpoint themselves. It dispatches to OpenAIClient._complete_responses when the spec says responses, otherwise to OpenAIClient._complete_chat.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 273–302)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: This prepares the keyword arguments for a Chat Completions request. It is the chat-specific packaging step before the network call is made.

**Data flow**: It receives a ModelRequest and reads model name, system text, messages, token limit, requested reasoning level, tools, and tool choice. It converts messages with openai_messages, applies the model spec's default reasoning rules, and adds tool definitions if present. The result is a dictionary suitable for client.chat.completions.create.

**Call relations**: OpenAIClient._complete_chat calls this just before opening the OpenAI stream. This helper keeps request construction separate from the streaming and retry loop, while openai_messages handles the detailed conversation translation.

*Call graph*: calls 1 internal fn (openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 304–406)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This sends a streaming request to OpenAI's Chat Completions API and turns the streamed chunks into UFO model events. It also enforces important safety rules around retries, empty replies, truncation, and usage reporting.

**Data flow**: It receives a ModelRequest. It builds chat request arguments with _chat_kwargs, opens a streaming API call, and reads chunks as they arrive. Text deltas become TextDelta events. New tool calls become ToolCallStart events, and partial tool arguments become ToolCallDelta events. Usage data is converted into UFO's Usage record, separating cached input tokens from newly billed input tokens. If temporary network or provider errors happen before any event is yielded, it waits and retries. At the end, it yields usage as the final event or raises a clear error for truncation, missing usage, or unrecoverable failures.

**Call relations**: OpenAIClient.complete routes chat-surface requests here. During the flow, this method calls _chat_kwargs for request building, uses the OpenAI stream as its source of truth, yields TextDelta, ToolCallStart, ToolCallDelta, and Usage objects to the caller, sleeps between retries, and logs timeout retry information through ufo.o11y.log.

*Call graph*: calls 1 internal fn (_chat_kwargs); called by 1 (complete); 7 external calls (__init__, __init__, __init__, __init__, __init__, sleep, log).


##### `OpenAIClient._complete_responses`  (lines 408–516)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the Responses API version of the streaming completion loop. It translates OpenAI Responses streaming events into UFO's standard event stream, while applying the same retry and error behavior as the chat path.

**Data flow**: It receives a ModelRequest, adjusts the requested reasoning setting through the model spec, and builds the Responses API request with responses_request. As OpenAI events arrive, text deltas become TextDelta events, function-call starts become ToolCallStart events, and function-call argument pieces become ToolCallDelta events. Completed response usage is converted into a Usage record. Refusals, content-filter stops, max-token truncation, failed responses, missing usage, and bad provider errors become explicit exceptions. Temporary failures before any output is yielded are retried with delays.

**Call relations**: OpenAIClient.complete routes responses-surface models here. This method hands request construction to responses_request, then interprets the OpenAI Responses event stream and emits the same UFO event types that the chat path emits, so callers do not need to know which OpenAI API was used underneath.

*Call graph*: calls 1 internal fn (responses_request); called by 1 (complete); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, model_copy, log).


### Model contracts and accounting
Shared package definitions describe model requests, specs, registries, user-facing catalog access, and price-based cost calculation.

### `core/src/ufo/models/__init__.py`

`data_model` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to modules inside this directory using names like `ufo.models.something`, rather than needing to know the folder layout directly. Think of it like a label on a drawer: the label does not store the tools itself, but it lets the rest of the workshop find the drawer reliably. Because this file is empty, it does not run setup code, expose shortcut imports, or define shared model classes. Its value is structural: without it, depending on the Python version and packaging setup, imports involving `ufo.models` could fail or behave less predictably.


### `core/src/ufo/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a trust and upkeep problem: people need to know which models are available, but a hand-written list can easily become outdated. Instead of maintaining a separate document, this file turns the live model registry into a readable skill at startup. The model registry is the system’s source of truth for model facts such as provider, price, knowledge cutoff, context window, reasoning support, and API style.

The main function walks through every registered model, sorts them by model ID, and writes one row per model into a Markdown table. Markdown is plain text formatting often used for readable documents. The table includes prices in dollars per million tokens, where a token is a small chunk of text used for billing and context limits. A tiny helper converts the internal price unit, micro-dollars, into normal dollars.

The result is wrapped as a RuntimeSkill, meaning the rest of the system can load it like any other skill. An everyday analogy: instead of printing a restaurant menu from last month’s spreadsheet, the restaurant prints today’s menu from the same kitchen inventory system that cooks the food. That way, the menu and reality stay in sync.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This small helper turns an internal price number into a human-readable dollar amount per million tokens. It exists so the catalog can show prices like “$1.25” instead of a raw accounting unit.

**Data flow**: It receives a price measured in micro-dollars per million tokens. It divides that number by the constant that says how many micro-dollars make one dollar, formats the result with two decimal places, and returns a string such as “$0.50”. It does not change any outside data.

**Call relations**: The catalog-building function calls this helper while writing each model’s input and output price into the table. It is a formatting step in the larger story of turning registry facts into readable catalog text.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function builds the complete model catalog as a RuntimeSkill. Someone would use it during startup to create a skill that users or agents can load when they need to compare available models.

**Data flow**: It receives a ModelRegistry, which contains the live set of model specifications. It sorts those specifications, turns each one into a Markdown table row, formats prices through _per_mtok, builds a full Markdown document with a name and description, and returns a RuntimeSkill containing that catalog text. The registry is read but not changed.

**Call relations**: This is the file’s main piece. During boot, it is meant to be called alongside registry setup so the generated skill comes from the same model records used for routing, pricing, and prompting. Inside that flow it calls _per_mtok for readable prices, then hands the finished name, description, instructions, and raw Markdown to RuntimeSkill.__init__ to create the skill object the rest of the runtime can use.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `core/src/ufo/models/interface.py`

`data_model` · `request preparation and model streaming`

This file is the contract between UFO and any large language model service it uses. Without it, each provider client would invent its own message shapes, tool formats, and error meanings, and the rest of the system would have to know provider-specific details.

The file defines small data shapes for the pieces that can travel to and from a model: plain text, images encoded as base64 text, tool calls requested by the model, and tool results sent back to the model. A `ModelRequest` gathers the full prompt: the model name, system instruction, conversation messages, token budget, available tools, optional forced tool choice, and reasoning setting. It also checks an important rule: if the caller forces the model to use one tool, that tool must actually be offered, and extra reasoning must be turned off because some providers reject that combination.

`ModelClient` is a protocol, meaning a promise about what provider clients must offer: a `complete` method that streams back model events. Those events can be text, tool-call fragments, or usage information.

The file also protects requests that contain many images. Providers have limits on image count and total image size. `trim_images` keeps the newest images, replaces removed ones with a clear placeholder, and preserves the rest of the conversation. This is like packing a suitcase with a strict weight limit: the newest, most relevant items stay, and older bulky items are left behind with a note saying they were omitted.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool_with_reasoning_off`  (lines 87–94)

```
def _forced_choice_names_an_offered_tool_with_reasoning_off(self) -> 'ModelRequest'
```

**Purpose**: This checks that a model request is internally consistent when the caller forces the model to use a specific tool. It prevents sending requests that the provider would reject or that name a tool the model was never given.

**Data flow**: It reads the `tool_choice`, the list of offered `tools`, and the `reasoning` setting from the request being built. If no tool is forced, it leaves the request unchanged. If a tool is forced, it confirms the tool name exists and that reasoning is set to `off`; otherwise it raises an error instead of allowing a bad request to continue.

**Call relations**: This validator runs as part of creating or validating a `ModelRequest`. It acts before any provider client receives the request, so later model-calling code can trust that a forced tool choice is valid and compatible with the reasoning setting.


##### `ModelClient.complete`  (lines 127–127)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the shared method every model provider client must implement. It takes one normalized model request and returns a stream of model events, such as text, tool-call updates, or usage data.

**Data flow**: A `ModelRequest` goes in. A provider-specific client turns that request into the provider's real API call, then yields `ModelEvent` items as the response arrives. The method itself is only a protocol declaration here, so it describes the expected shape but does not perform the work in this file.

**Call relations**: Other code can call `complete` without caring whether the backing provider is Anthropic, OpenAI, or another service. Concrete provider clients supply the actual behavior while matching this shared interface.


##### `trim_images`  (lines 135–163)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares conversation messages so they stay within provider image limits. It removes older or oversized inline images and replaces them with a short text note, so the model still knows something was omitted.

**Data flow**: It receives a tuple of messages. First it asks `_image_positions` where all images are, including images nested inside tool results. It keeps only the newest images allowed per message and per whole request, then uses `_image_data_len` to enforce the total base64 image-size budget from newest to oldest. If anything must be removed, it calls `_trim_message` for each message and returns a new tuple with placeholders where dropped images used to be; otherwise it returns the original messages.

**Call relations**: This is the main public helper for image trimming in this file. It coordinates the smaller helper functions: one finds images, one measures image data size, and one rebuilds messages with omitted-image markers.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `_image_data_len`  (lines 166–177)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This finds the size of one specific image's encoded data. It is used to decide whether keeping that image would exceed the request-wide image byte budget.

**Data flow**: It receives the full message list and a position that points to an image. The position identifies the message, the content block, and optionally an image nested inside a tool result. It looks up that image and returns the length of its base64 data string. If the position does not actually point to an image, it raises an error because the caller's bookkeeping is wrong.

**Call relations**: This helper is called by `trim_images` while it walks backward through the images it might keep. It supplies the size numbers that let `trim_images` stop before the provider's total image-size limit is crossed.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 180–200)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This scans the conversation and records where every inline image appears. It gives later trimming code a simple map of image locations, ordered from oldest to newest.

**Data flow**: It receives the tuple of messages. It skips messages whose content is just plain text, then looks through structured content blocks. It records top-level image blocks and image blocks nested inside tool-result content, returning a list of positions that point back into the original message structure.

**Call relations**: This is the first helper used by `trim_images`. Its output becomes the working list that `trim_images` uses to enforce per-message, per-request, and byte-size limits.

*Call graph*: called by 1 (trim_images).


##### `_trim_message`  (lines 203–227)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]]) -> Message
```

**Purpose**: This rebuilds one message after the trimming decision has been made. It replaces only the images marked for removal, leaving the rest of the message unchanged.

**Data flow**: It receives a message index, the message itself, and a set of image positions to drop. If the message is plain text, it returns it unchanged. For structured messages, it walks each block: dropped top-level images become a `TextBlock` containing the omitted-image note, and dropped nested images inside tool results are replaced the same way inside a copied tool-result block. It returns a copied message with updated content.

**Call relations**: This helper is called by `trim_images` after the full drop set is known. It performs the final message rewriting, using `TextBlock` for placeholders and `Message.model_copy` to return updated data without mutating the original message object.

*Call graph*: called by 1 (trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/models/pricing.py`

`domain_logic` · `accounting and usage recording`

This file is the project’s price list and calculator for model usage. Models are billed by tokens, which are small chunks of text. Different token types can cost different amounts: input tokens, output tokens, cached reads, and cached writes. The file stores those rates in `ModelPrice`, using micro-USD, meaning millionths of a US dollar, so the system can do exact integer math instead of fragile floating-point money math.

The main job is simple: take a usage record, look up the model’s rates, multiply each token count by its matching rate, and divide by one million because rates are expressed per million tokens. If the model is not in the price table, the code logs a warning and returns zero. That is important for old or historical records: the system can keep running even if it sees a model name it no longer knows how to price.

The file also creates a digest, which is like a tamper-evident label on a sealed envelope. It sorts the price table, turns it into compact JSON, and hashes it with SHA-256. The resulting stamp lets later accounting records prove which exact price table was used when a cost was calculated.

#### Function details

##### `price_digest`  (lines 24–39)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable version stamp for a whole model price table. This lets the system record not just a cost, but also which exact set of prices produced that cost.

**Data flow**: It receives a mapping from model names to their prices. It sorts the models, converts each price into a compact JSON form, then runs that text through SHA-256, a standard one-way fingerprinting algorithm. It returns a string beginning with `sha256:` followed by the fingerprint.

**Call relations**: When a new `Pricing` object is built, `pricing_from` calls this function so the price table and its fingerprint are created together. It relies on JSON formatting and SHA-256 hashing to make the same input always produce the same stamp.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 42–54)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one usage record for one model, in micro-USD. It is the core arithmetic that turns token counts into a billable amount.

**Data flow**: It receives a model name, a usage record with token counts, and a price table. It looks up that model’s rates, multiplies each token count by the matching rate, adds the results, and divides by one million because the rates are per million tokens. If the model is missing, it writes a log message and returns zero instead of failing.

**Call relations**: `Pricing.micro_usd` calls this function when other accounting code asks for the cost of model usage. If the model is unknown, it hands off to the project logger so the situation is visible without interrupting the accounting flow.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 64–65)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the convenient method people use to price usage from a `Pricing` object. It hides the detail that the actual calculation is done by a helper function.

**Data flow**: It receives a model name and a usage record. It combines those with the price table stored inside the `Pricing` object, passes everything to `usage_priced_micro_usd`, and returns the computed micro-USD amount.

**Call relations**: The accounting layer calls this method when recording sandbox tokens, turn usage, or workspace usage. This method then delegates to `usage_priced_micro_usd`, keeping the public interface small while reusing the shared pricing calculation.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 68–71)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete `Pricing` object from a raw model price table. It packages the prices together with the digest that identifies them.

**Data flow**: It receives a mapping of model names to `ModelPrice` values. It copies that mapping into a normal dictionary, computes a digest for the copied table, and returns a new `Pricing` object containing both the table and the digest.

**Call relations**: This is the setup function for pricing data. It calls `price_digest` before constructing `Pricing`, so any code that later uses the `Pricing` object has both the rates needed for calculations and the version stamp needed for traceability.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### `core/src/ufo/models/registry.py`

`domain_logic` · `startup and per-model request handling`

This file is the model directory for the system. Like a restaurant menu that lists every dish by its exact name, it keeps one record for each available AI model, keyed by the model’s id. Those records come from two places: the built-in models shipped with the core app, and extra models contributed by extension manifests. If two models try to use the same id, startup fails immediately, because silently picking one would send requests, pricing, or credentials to the wrong place.

The central type is `ModelRegistry`. It stores the model specifications, the merged pricing table, and the configured fallback model used when code asks for the special `auto` model. A model specification contains the facts needed to use a model, such as its provider, price, credential location, and how to build its client.

The registry also keeps credential lookup in one place. When a model is actually used, `client_for` fetches the right API key from the current workspace if available, or from the platform environment. This means a missing key only breaks the model that needs it, not the whole server. The file also supports onboarding checks by saying which environment variable a core provider needs before the first turn.

#### Function details

##### `ModelRegistry.resolve`  (lines 31–34)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special model name `auto` into the real model id configured for this deployment. If the caller already named a specific model, it leaves that name unchanged.

**Data flow**: It receives a model name. If that name is the shared `AUTO_MODEL` marker, it returns the registry’s configured concrete model; otherwise it returns the original name. Nothing else is changed.

**Call relations**: This is used by `ModelRegistry.model_key_env` before checking credentials, so the system checks the key for the model that will actually run rather than the placeholder name `auto`.

*Call graph*: called by 1 (model_key_env).


##### `ModelRegistry.spec`  (lines 36–42)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This retrieves the registered facts for one exact model id. It is the loud, central checkpoint that catches unknown model names early with a clear error.

**Data flow**: It receives a model id and looks it up in the registry’s `specs` table. If found, it returns the matching `ModelSpec`; if not, it raises a `ValueError` explaining that no model is registered for that id.

**Call relations**: Both `ModelRegistry.client_for` and `ModelRegistry.model_key_env` call this before they can do their work. That keeps model validation in one place instead of letting different parts of the system fail later in different ways.

*Call graph*: called by 2 (client_for, model_key_env).


##### `ModelRegistry.client_for`  (lines 44–61)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: This creates the actual model client used to talk to a selected AI provider. It also finds the right API key at the moment the client is needed, so workspace-specific keys and rotated platform keys are respected.

**Data flow**: It receives a model id, gets that model’s specification, and checks whether the model needs a key. If no key is needed, it builds the client with an empty key. If a key is needed, it asks the current workspace for the credential, using the model’s bring-your-own-key slot or environment variable fallback. If no usable key exists, it raises a clear runtime error. Otherwise, it returns a ready-to-use `ModelClient` built from the spec and key.

**Call relations**: This function first calls `ModelRegistry.spec` to get the model’s instructions. It then calls `ws_current` to access the active workspace’s credentials. It is the point where a registered model becomes a usable network client for an actual turn.

*Call graph*: calls 1 internal fn (spec); 1 external calls (ws_current).


##### `ModelRegistry.key_slot_for`  (lines 63–70)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This answers which workspace credential slot would be used for a model, if any. It is deliberately forgiving, so reporting code can keep working even if it sees an old or removed model id.

**Data flow**: It receives a model id and looks directly in the registry’s table. If the model is unknown, or if the model does not use a workspace key slot, it returns `None`. If the model has a slot, it returns that slot name.

**Call relations**: Unlike `ModelRegistry.spec`, this does not fail on unknown models. That makes it suitable for background or accounting flows, such as labeling billing rows as workspace-keyed or platform-served without crashing on historical data.


##### `ModelRegistry.model_key_env`  (lines 72–82)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding code which environment variable must be set before a model can run, when the model belongs to a built-in provider. For extension-provided models, it returns `None` because the core app may not know how those models obtain their keys.

**Data flow**: It receives a model name and the app configuration. It first resolves `auto` to the actual configured model id, then looks up that model’s specification and reads its provider. If the provider is Anthropic, it returns the configured Anthropic key environment variable. If the provider is OpenAI, it returns the configured OpenAI key environment variable. For other providers, it returns `None`.

**Call relations**: This function calls `ModelRegistry.resolve` so placeholder model choices are checked against the real model. It then calls `ModelRegistry.spec` to read the provider. It is used when the system wants to warn early about missing core-provider keys before a first model turn happens.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 85–106)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the active `ModelRegistry` at startup from built-in model definitions and extension manifests. It also creates the shared pricing table and rejects configuration mistakes before the server starts serving model turns.

**Data flow**: It receives the app configuration and a tuple of manifests. It asks `core_model_specs` for the built-in model specs, then adds every model spec contributed by the manifests. As it builds the id-to-spec table, it raises an error if two specs claim the same id. It also checks that the configured automatic model is a real registered id. Finally, it builds pricing data from every spec’s price and returns a frozen `ModelRegistry` containing the specs, pricing, and auto-model choice.

**Call relations**: This is the construction point for the whole registry. It calls `core_model_specs` to get built-in models, `pricing_from` to merge their prices into a usable pricing object, and then creates `ModelRegistry`. Other parts of the system can rely on the returned registry because duplicate ids and a bad `auto_model` have already been rejected.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### `core/src/ufo/models/spec.py`

`data_model` · `config load and model selection`

This file is like a passport for every AI model the system knows about. Each model gets a single `ModelSpec`, a frozen record that says who provides the model, how to build a client for it, what its price is, how much text it can accept, what date its knowledge stops at, whether it supports extra “reasoning” mode, and whether it uses a chat-style or responses-style API.

The important idea is centralization. Instead of one part of the system remembering prices, another remembering API behavior, and another remembering prompt details, every part asks the registry for the model’s `ModelSpec` and reads the same facts. Without this, a missing or outdated model entry could cause confusing failures later, such as a bad API request, a broken prompt, or incorrect billing.

The file also defines `ReasoningSupport`, which describes whether a model can use extended reasoning and whether that reasoning still works when tools are involved. “Tools” here means external functions the model can call, such as search or code execution. Some models can reason alone but not while using tools, so `ModelSpec.default_reasoning` safely turns reasoning off in those cases.

There is a small validation step too: knowledge cutoff dates must look like `YYYY-MM`, and a model cannot claim tool-compatible reasoning unless it supports reasoning at all.

#### Function details

##### `ModelSpec.__post_init__`  (lines 54–62)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a newly created model record is internally sensible. It catches bad model definitions early, before the system tries to call the model or show its details to a user.

**Data flow**: A freshly built `ModelSpec` comes in with fields such as `id`, `knowledge_cutoff`, and `reasoning`. The function checks that the knowledge cutoff is written as a year and month, like `2024-06`, and checks that tool-compatible reasoning is not claimed for a model that does not support reasoning. If everything is valid, nothing changes; if something is wrong, it raises an error explaining the bad model entry.

**Call relations**: This runs automatically when a `ModelSpec` is created. It acts as the gatekeeper for the registry’s model facts, so later code can trust that basic model metadata is well formed instead of rediscovering the problem during a model call.


##### `ModelSpec.default_reasoning`  (lines 64–74)

```
def default_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort
```

**Purpose**: This decides what reasoning setting should actually be sent to a model for one request. It protects callers from asking for reasoning in situations where the chosen model cannot support it.

**Data flow**: The function receives the reasoning level someone requested and the tools included in the request. It looks at the model’s declared reasoning support. If the model does not support reasoning, it returns `off`; if tools are present and this model cannot combine tools with reasoning, it also returns `off`; otherwise it returns the requested reasoning level unchanged.

**Call relations**: Code preparing a model request can call this after choosing a `ModelSpec`. It turns the model’s stored capability facts into a safe request setting, so the later API call does not ask the provider for an unsupported combination.

## 📊 State Registers Touched

- `reg-effective-config` — The running service’s merged settings, such as required keys, enabled backends, safety options, and service behavior.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-spend-ledger-billing` — The shared usage ledger, prices, caps, exports, and billing records used to track and limit spending.
- `reg-tool-catalog` — The live list of tools the model can call, including their names, descriptions, schemas, and dispatch targets.
- `reg-model-catalog-pricing` — The shared list of available AI models, provider details, limits, credentials, and prices.
- `reg-live-stream-hub` — The live stream of turn updates that clients can watch and replay after reconnecting.
- `reg-transcript-compaction-store` — The saved conversation transcript and compacted summaries used to rebuild context and inspect past turns.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
- `reg-turn-context-token-budget` — The per-turn context-window and token-budget state used to compact history, construct model requests, and constrain model/tool work.
