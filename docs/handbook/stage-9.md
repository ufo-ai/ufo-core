# Conversation context, prompt construction, and model selection  `stage-9`

This stage is the “packing desk” before each model call in the main work loop. It gathers the conversation, prepares the instructions, chooses the right AI model, and formats the request so the chosen provider will accept it. The prompt package starts in ufo.loop.prompts: the package marker makes the prompt tools importable, and render.py fills the prompt template, checks that no blanks were left behind, and records a fingerprint so changes can be traced. governance.py adds safety around prompt edits by requiring proposed changes to be approved before they replace an agent’s instructions.

Long conversations are handled by compaction.py. It keeps recent messages as they are, summarizes older ones, and saves both versions so the system can inspect what changed.

The model files act like a travel guide and adapter kit. spec.py defines what facts are tracked for each model. catalog.py lists built-in models. registry.py lets the system look up a model and build the right client. interface.py defines the shared request shape and trims oversized images. openai.py, anthropic.py, and the OpenRouter extension translate that shared shape to each provider’s API and translate streamed replies back.

## Files in this stage

### Model definitions and lookup
These files define the model facts, built-in catalog entries, and registry lookup path used to select a model and construct the right client.

### `core/src/ufo/models/spec.py`

`data_model` · `model registry setup and per-request model selection`

This file is like a catalog card for every model the project can use. Without it, each part of the system might keep its own separate notes about a model: one table for billing, another for API calling, another for prompt wording, and so on. That would make mistakes easy, such as charging the wrong price or sending a feature to a model that cannot use it.

The main record is `ModelSpec`, a frozen data object, meaning its fields cannot be changed after creation. Each spec says the model's ID, provider, client builder, price, knowledge cutoff date, context window size, reasoning ability, API style, and where to find its API key. The smaller `ReasoningSupport` record describes whether a model supports extra reasoning, and whether that reasoning still works when tools are involved.

The file also protects the catalog from bad entries. When a `ModelSpec` is created, it checks that the knowledge cutoff looks like `YYYY-MM`, such as `2024-06`. It also rejects an impossible claim: saying tools can be used with reasoning while saying reasoning is not supported at all.

Finally, `ModelSpec.default_reasoning` decides what reasoning setting is actually safe to send for a request. If the requested model cannot reason, or cannot combine reasoning with tools, it quietly forces reasoning off before the call is made.

#### Function details

##### `ModelSpec.__post_init__`  (lines 54–62)

```
def __post_init__(self) -> None
```

**Purpose**: This function checks that a newly created model record is internally consistent. It catches bad model catalog entries early, before they cause confusing failures during an actual model call.

**Data flow**: A completed `ModelSpec` has just been created with fields such as `id`, `knowledge_cutoff`, and `reasoning`. The function reads those fields, verifies that the cutoff date is written as year and month, and checks that the reasoning flags do not contradict each other. If everything is valid, nothing changes; if something is wrong, it raises a `ValueError` with a message naming the bad model.

**Call relations**: This is run automatically by Python's dataclass machinery whenever a `ModelSpec` is constructed. It does not hand work off to other project functions; its role is to stop invalid model definitions at the door.


##### `ModelSpec.default_reasoning`  (lines 64–74)

```
def default_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort
```

**Purpose**: This function decides the reasoning level that should actually be sent to the model. It respects the user's requested reasoning effort only when the chosen model and request shape can safely support it.

**Data flow**: It receives a requested reasoning effort and the tools included in the current request. It looks at the model's reasoning support settings. If the model does not support reasoning, it returns `off`; if tools are present and this model cannot combine tools with reasoning, it also returns `off`; otherwise it returns the requested effort unchanged.

**Call relations**: This is used when preparing a model request, after a `ModelSpec` has been chosen from the registry. It acts as a safety filter between the caller's desired behavior and the model client's actual API call, so unsupported reasoning options are not sent downstream.


### `core/src/ufo/models/catalog.py`

`config` · `startup and model selection`

This file is like a menu for the system’s built-in AI backends. When the rest of the project needs to ask, “Which models exist, how much do they cost, how do I call them, and what key do I need?”, this is the single place for the core answers. Without it, the system could not reliably choose a supported Anthropic or OpenAI model, create the right client for it, or stamp usage with the right price.

The file defines shared constants first: environment variable names for API keys, default context windows, and a common statement that these models support reasoning with tools. A context window is the amount of text a model can consider at once.

It then provides small helper functions that build either Anthropic or OpenAI model specifications. A `ModelSpec` is a structured record describing one model: its ID, provider, pricing, key location, API surface, and client-building function.

The main function, `core_model_specs`, returns the full tuple of built-in models. Each entry includes exact prices and knowledge cutoff dates. One OpenAI model is marked to use the “responses” API surface because the usual chat API rejects one combination of tool use and reasoning settings. At import time, the file also builds `CORE_MODEL_SPECS`, a price lookup table, and a pricing digest so other parts of the system can compare or record the active pricing set.

#### Function details

##### `_anthropic_client`  (lines 24–25)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function creates a ready-to-use Anthropic client for one model. It combines the raw Anthropic software development kit client with the model’s own specification, so later code has both the connection and the facts about the model.

**Data flow**: It receives a `ModelSpec` and an API key string. It first creates the lower-level Anthropic SDK client using that key, then wraps it in this project’s `AnthropicClient` along with the model specification. The result is an Anthropic client object that knows which model it is meant to call.

**Call relations**: This function is used as the client-building recipe stored inside Anthropic `ModelSpec` objects created by `_anthropic`. When some later part of the system wants to actually talk to that Anthropic model, the stored recipe can build the correct client.

*Call graph*: 2 external calls (__init__, anthropic_sdk_client).


##### `_openai_client`  (lines 28–29)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function creates a ready-to-use OpenAI client for one model. It ties together the raw OpenAI SDK client and the project’s model specification so calls can be made with the right model context.

**Data flow**: It receives a `ModelSpec` and an API key string. It uses the key to create the lower-level OpenAI SDK client, then wraps that SDK client and the specification in an `OpenAIClient`. The output is an OpenAI client object prepared for that specific model.

**Call relations**: This function is stored as the client-building recipe inside OpenAI `ModelSpec` objects created by `_openai`. Later, when the system needs to send work to an OpenAI model, that recipe is what turns an API key into the right project-level client.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 32–51)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: This helper builds the complete catalog entry for one Anthropic model. It keeps all Anthropic-specific defaults in one place so each model entry only needs to state what is unique, such as its name, price, and knowledge cutoff.

**Data flow**: It receives a model ID, price, knowledge cutoff date, API-key environment variable name, and optionally a context window size. It combines those inputs with Anthropic defaults: the provider name, the Anthropic key slot, the chat API surface, reasoning support, and the `_anthropic_client` builder. It returns a `ModelSpec` describing that Anthropic model.

**Call relations**: `core_model_specs` calls this repeatedly while assembling the built-in catalog. `_anthropic` does the repetitive Anthropic setup work and hands back finished `ModelSpec` records for inclusion in the returned model list.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 54–68)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: This helper builds the complete catalog entry for one OpenAI model. It centralizes OpenAI-specific defaults, while still allowing a model to choose a different API surface when needed.

**Data flow**: It receives a model ID, price, knowledge cutoff date, API-key environment variable name, and optionally an API surface. It combines those inputs with OpenAI defaults: the provider name, the OpenAI key slot, the context window, reasoning support, and the `_openai_client` builder. It returns a `ModelSpec` describing that OpenAI model.

**Call relations**: `core_model_specs` calls this for each built-in OpenAI model. Most entries use the default chat API surface, while one entry passes a different surface so requests are formed in a way that OpenAI accepts.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 71–160)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: This function builds and returns the full list of core-supported model specifications. It is the central source for the built-in Anthropic and OpenAI model catalog, including prices and key environment variable names.

**Data flow**: It receives the names of the environment variables that should hold Anthropic and OpenAI API keys. It creates `ModelPrice` values for each model, passes them into `_anthropic` or `_openai`, and collects the resulting `ModelSpec` objects into a tuple. The output is the complete immutable list of built-in model records.

**Call relations**: At module import time, this function is called to create `CORE_MODEL_SPECS` using the default API-key environment variable names. The module then derives price tables and a pricing digest from that list, so the rest of the system can route model calls and account for costs consistently.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `core/src/ufo/models/registry.py`

`domain_logic` · `startup and model request handling`

This file solves a coordination problem: many parts of the system need to agree on what a model name means. A model has an id, a provider, pricing information, and a way to create a client that can call it. Without one shared registry, mistakes could show up later as scattered failures: an unknown model during a user turn, a missing price during billing, or the wrong API key lookup.

The registry is built from two sources. First it loads the built-in model definitions. Then it adds model definitions contributed by extension manifests. It rejects duplicate ids immediately, so an extension cannot quietly replace a core model by accident.

The `ModelRegistry` also supports an `auto` model choice. That is a placeholder meaning “use the deployment’s configured default model.” This lets agents avoid naming a specific model, while still resolving to one real model before use.

When a model client is needed, the registry looks up the model’s spec and fetches the needed key from the current workspace. That may be a workspace-owned “bring your own key” secret, or a platform default environment variable. In everyday terms, this file is like the front desk for model access: it checks the map, confirms the destination exists, finds the right payment/key, and sends the request to the right provider.

#### Function details

##### `ModelRegistry.resolve`  (lines 31–34)

```
def resolve(self, model: str) -> str
```

**Purpose**: Turns the special `auto` model placeholder into the real default model configured for this deployment. If the caller already named a specific model, it leaves that name unchanged.

**Data flow**: It receives a model name. If that name is the shared `auto` marker, it returns the registry’s configured `auto_model`; otherwise it returns the original name. It does not change any stored data.

**Call relations**: This is used by `ModelRegistry.model_key_env` before checking which environment variable is needed. That way the system checks the key for the model that will actually run, not for the placeholder name.

*Call graph*: called by 1 (model_key_env).


##### `ModelRegistry.spec`  (lines 36–42)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: Looks up the full registered description for a model id. It fails clearly if the model id is unknown, so callers do not continue with missing or guessed model facts.

**Data flow**: It receives a model id and reads the registry’s `specs` table. If the id is present, it returns the matching `ModelSpec`; if not, it raises a `ValueError` explaining that no model is registered for that id.

**Call relations**: This is the main lookup point used before creating a client in `ModelRegistry.client_for` and before checking provider key requirements in `ModelRegistry.model_key_env`. It keeps those later steps from having to each invent their own error behavior for unknown models.

*Call graph*: called by 2 (client_for, model_key_env).


##### `ModelRegistry.client_for`  (lines 44–61)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Builds a usable model client for one model, using the correct API key for the current workspace. Someone uses this when the system is about to call an AI provider.

**Data flow**: It receives a model id, looks up that model’s spec, and checks whether the spec needs a key. If no key is needed, it builds the client with an empty key. If a key is needed, it asks the current workspace for the right credential, using either a stored workspace key slot or an environment variable name. If the key is missing, it raises a clear runtime error telling the operator what to set. On success, it returns a `ModelClient` ready to make calls.

**Call relations**: It first relies on `ModelRegistry.spec` to get the model’s facts. Then it asks `ws_current` for the active workspace so workspace-specific credentials can be honored. Finally it hands the spec and resolved key to the client factory stored on the spec.

*Call graph*: calls 1 internal fn (spec); 1 external calls (ws_current).


##### `ModelRegistry.key_slot_for`  (lines 63–70)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Answers which workspace credential slot would be used for a model, if any. This is useful for reporting or billing flows that need to label whether usage was paid for with a workspace key or by the platform.

**Data flow**: It receives a model id and checks the registry’s `specs` table without raising an error for missing models. If the model is unknown or has no workspace key slot, it returns `None`. If the model has a key slot, it returns that slot name.

**Call relations**: Unlike `ModelRegistry.spec`, this method is deliberately forgiving. It can be used in later accounting-style flows where old records may mention a model that is no longer registered, and those flows should keep going instead of crashing.


##### `ModelRegistry.model_key_env`  (lines 72–82)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: Finds the environment variable that onboarding should require before a model is used. It only does this eager check for known core providers; contributed models may resolve their keys later in their own way.

**Data flow**: It receives a model name and the system configuration. It first resolves `auto` to the real configured model, then looks up that model’s provider. If the provider is Anthropic, it returns the configured Anthropic key environment variable. If the provider is OpenAI, it returns the configured OpenAI key environment variable. For other providers, it returns `None`.

**Call relations**: It calls `ModelRegistry.resolve` so placeholder model choices are checked correctly, then calls `ModelRegistry.spec` to read the provider. This supports startup or onboarding checks that want to catch missing core provider keys before the first model turn.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 85–106)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: Builds the complete active model registry for a running system. It combines built-in models and extension-provided models, checks for configuration mistakes, and prepares pricing information.

**Data flow**: It receives the system configuration and a tuple of extension manifests. It asks the core catalog for built-in model specs, then walks through those plus every model spec supplied by the manifests. Each spec is inserted into a dictionary by id; if two specs claim the same id, it raises an error. It also verifies that the configured default `auto_model` is a real registered model. If all checks pass, it creates and returns a `ModelRegistry` containing the specs table, a merged pricing table, and the default model id.

**Call relations**: This is the construction point used when the application is setting up its model layer. It calls `core_model_specs` to get built-in definitions, passes all model prices to `pricing_from` to build the pricing helper, and finally creates the `ModelRegistry` object that the rest of the system uses.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### Provider client adapters
These files provide the shared model-provider interface and concrete adapters for OpenAI-compatible, Anthropic, and OpenRouter-backed models.

### `core/src/ufo/models/interface.py`

`data_model` · `request preparation and model interaction`

Different AI providers use different request formats, but the rest of this project needs one steady way to describe a conversation, tools, images, model output, and errors. This file is that shared contract. It defines small data shapes such as text blocks, image blocks, tool calls, tool results, messages, tool schemas, and full model requests. These are built with Pydantic, a validation library that checks data has the expected shape before it travels further.

The file also defines `ModelClient`, a protocol, meaning a promise that any real model client must offer a `complete` method. Provider-specific clients can then plug in behind the same doorway, whether they talk to Anthropic, OpenAI, or another service.

One important practical job here is image trimming. Model providers set limits on how many images can appear in one message, in one full request, and in total request size. `trim_images` walks through all messages, finds inline images, keeps the newest ones first, and replaces dropped images with a short text note. This is like packing a suitcase with a strict airline weight limit: the newest and most relevant items stay, older ones are swapped for a note saying they were left behind.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool_with_reasoning_off`  (lines 87–94)

```
def _forced_choice_names_an_offered_tool_with_reasoning_off(self) -> 'ModelRequest'
```

**Purpose**: This validation step checks that a request forcing the model to use one specific tool is safe and valid. It makes sure the named tool was actually offered, and that extra reasoning mode is turned off because at least one provider rejects forced tool use with extended reasoning enabled.

**Data flow**: A `ModelRequest` is being created or validated. The function reads `tool_choice`, the list of offered `tools`, and the `reasoning` setting. If there is no forced tool, it leaves the request unchanged. If the forced tool name is missing from the offered tools, or reasoning is not `off`, it raises an error; otherwise the same request comes out approved.

**Call relations**: This runs automatically as part of Pydantic model validation when a `ModelRequest` is built. It acts as an early gatekeeper before any provider-specific client tries to send the request, so invalid combinations fail close to where the request is formed.


##### `ModelClient.complete`  (lines 127–127)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This defines the one method every model client must provide: take a model request and stream back model events. It is a contract rather than an implementation, so provider-specific clients can be swapped in while the rest of the code calls them the same way.

**Data flow**: A completed `ModelRequest` goes in. A real implementation is expected to send it to a model provider and yield events over time, such as text pieces, tool-call starts, tool-call JSON fragments, or usage information. This protocol method itself does not change data or return a final object; it describes the expected streaming shape.

**Call relations**: Other parts of the system depend on this method when they need a model completion. Concrete clients for specific providers implement this contract, and orchestration code can call `complete` without needing to know which provider is behind it.


##### `trim_images`  (lines 135–163)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares conversation messages so they stay within provider image limits. It keeps the newest images that fit the per-message, per-request, and byte-size limits, and replaces removed images with a clear text placeholder.

**Data flow**: A tuple of messages goes in. The function first asks `_image_positions` where all inline images are. It chooses images to keep based on request-wide count, per-message count, and total base64 data size, using `_image_data_len` to measure image data. If nothing must be dropped, the original messages come out unchanged. If images must be removed, it calls `_trim_message` for each message and returns a new tuple where dropped images have become `[image omitted: over the provider image limit]` text blocks.

**Call relations**: This is the main public helper for image limiting in this file. It coordinates three smaller helpers: one to find images, one to measure them, and one to rewrite affected messages. It should be used before provider-specific clients translate and send model requests, so both Anthropic-like and OpenAI-like clients start from the same trimmed message set.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `_image_data_len`  (lines 166–177)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This measures the stored data length of one image identified by its position inside the messages. It is used to enforce the total image-size budget for a request.

**Data flow**: The function receives all messages plus a position made of message index, block index, and optionally a nested image index inside a tool result. It looks up that exact image and returns the length of its base64 data string. If the position does not actually point to an image, it raises an error instead of guessing.

**Call relations**: `trim_images` calls this while deciding which kept images still fit inside the overall byte budget. It does not choose what to keep by itself; it supplies the size information that lets `trim_images` make that decision.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 180–200)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This finds every inline image in the conversation and records where each one lives. It includes both normal image blocks and images nested inside tool results.

**Data flow**: A tuple of messages goes in. The function skips plain string messages, then walks through structured content blocks. For each image it finds, it records a position as message number, block number, and either `None` for a top-level image or a sub-index for an image inside a tool result. It returns the list in oldest-first order.

**Call relations**: `trim_images` calls this first because it needs a map of all images before it can decide which older ones to drop and which newer ones to preserve. This helper only observes the messages; it does not modify them.

*Call graph*: called by 1 (trim_images).


##### `_trim_message`  (lines 203–227)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]]) -> Message
```

**Purpose**: This rewrites one message after `trim_images` has decided which image positions must be removed. It preserves the message structure but swaps dropped images for a text note so the model can still tell something was omitted.

**Data flow**: The function receives one message, its message index, and a set of image positions to drop. If the message is plain text, it returns it unchanged. For structured content, it walks each block: top-level dropped images become a new `TextBlock`, and dropped nested images inside a `ToolResultBlock` are replaced inside a copied tool result. The output is a copied `Message` with updated content, while the original message is left untouched.

**Call relations**: `trim_images` calls this after it has built the drop set. `_trim_message` performs the actual safe rewrite, using `TextBlock` to create omission notes and Pydantic's `model_copy` to make updated copies rather than mutating existing message objects.

*Call graph*: called by 1 (trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/models/anthropic.py`

`io_transport` · `request handling`

This file is the adapter between UFO's internal model interface and Anthropic's API. Without it, the rest of the project would need to know Anthropic's exact request shape, streaming event names, image format, tool-call format, and error behavior.

The file does two main jobs. First, it converts UFO's own content blocks into Anthropic's expected format. A plain text message stays plain text, while richer content such as images, tool requests, and tool results is translated into the dictionaries Anthropic expects. This is like translating a restaurant order from the project's house language into the kitchen's exact ticket format.

Second, `AnthropicClient.complete` sends a request, reads Anthropic's streamed response piece by piece, and yields UFO-standard events such as text chunks, tool-call starts, tool-call JSON fragments, and final token usage. It also decides what to do when things go wrong. Timeouts and temporary provider failures are retried before any output has been sent. Once output has started, errors are raised immediately so the caller does not unknowingly mix pieces from different attempts. Special Anthropic stop reasons are mapped to clearer project errors: hitting the token limit becomes a truncated-response error, and a refusal becomes a refusal error. Empty responses are retried a few times before being accepted as empty so the higher-level turn loop can decide what to do next.

#### Function details

##### `anthropic_sdk_client`  (lines 41–45)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the official Anthropic asynchronous API client with project-chosen timeout settings. It deliberately disables the SDK's built-in retries so this file's own retry policy is the single source of truth.

**Data flow**: It takes an Anthropic API key as input. It builds an `AsyncAnthropic` client configured with that key, a fixed request timeout, and zero SDK retries. It returns that ready-to-use client without sending any request yet.

**Call relations**: This is used when setting up access to Anthropic. The returned SDK client is later stored inside `AnthropicClient`, whose `complete` method uses it to make streaming message requests.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 48–52)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns the project's image representation into the image shape Anthropic expects. It is a small translator for base64 image data and its media type, such as PNG or JPEG.

**Data flow**: It receives an `ImageSource`, which contains the image's media type and base64-encoded data. It wraps those values in a dictionary with Anthropic's required `type`, `source`, `media_type`, and `data` fields. It returns that dictionary for inclusion in a message.

**Call relations**: This helper is called whenever richer content contains an image. `anthropic_content` uses it for normal image blocks, and `_anthropic_tool_result_part` uses it when a tool result includes an image.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 55–60)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic's format. Tool results can contain text or images, so this function chooses the right conversion for each piece.

**Data flow**: It receives one `ToolResultContent` item. If the item is text, it returns an Anthropic-style text dictionary. If the item is an image, it passes the image source to `_anthropic_image` and returns the resulting image dictionary.

**Call relations**: This function is used inside `anthropic_content` when a tool result contains multiple rich parts instead of a single plain string. It delegates image formatting to `_anthropic_image` so the image encoding rules stay in one place.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 63–88)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Converts UFO message content into Anthropic message content. It lets the rest of the project use its own clean content types while this adapter handles Anthropic's exact wire format.

**Data flow**: It receives either a plain string or a tuple of content blocks. A string is returned unchanged. For block content, it walks through each block and converts text, images, tool-use requests, and tool results into Anthropic-style dictionaries. It returns either the original string or a list of converted dictionaries.

**Call relations**: This is called by `AnthropicClient.complete` while building the outgoing request. It uses `_anthropic_image` for image blocks and `_anthropic_tool_result_part` for rich tool-result pieces, then hands the converted content to Anthropic's API request.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 96–248)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to Anthropic and streams back the response as the project's standard events. It also applies the project's retry, timeout, refusal, truncation, tool-call, and token-usage rules.

**Data flow**: It receives a `ModelRequest` containing the model name, system prompt, conversation messages, token limit, tool definitions, optional forced tool choice, and reasoning preference. It builds Anthropic request arguments, converting messages through `anthropic_content`, adding tools when present, and enabling Anthropic reasoning settings when requested. It opens a streaming API call, reads Anthropic events as they arrive, and turns them into `TextDelta`, `ToolCallStart`, and `ToolCallDelta` events. It tracks token usage from the stream and finally yields one `Usage` record. If Anthropic reports truncation or refusal, it raises project-specific errors instead of returning a normal result. If the provider times out or returns retryable failures before anything has been yielded, it waits and retries; after output has started, it raises the error immediately.

**Call relations**: This is the main method callers use when they want an Anthropic completion. It relies on `anthropic_content` to prepare outgoing messages, then calls the Anthropic SDK to create a stream. As stream events arrive, it hands standard model events back to the caller. When retrying, it uses `asyncio.sleep` for backoff delays; when finishing successfully, it hands off final accounting through a `Usage` event.

*Call graph*: calls 1 internal fn (anthropic_content); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, trim_images, log).


### `core/src/ufo/models/openai.py`

`io_transport` · `model request handling`

This file solves a translation problem. The rest of the project talks in its own common language: messages, text blocks, images, tool calls, tool results, and usage counts. OpenAI has two different web API shapes for similar work: Chat Completions and Responses. This file knows how to speak both, so the rest of the system does not have to care which one a particular model needs.

Think of it like a travel adapter. UFO plugs in one standard kind of request, and this file reshapes it to fit the provider's socket. For chat-style models, it builds OpenAI chat messages, including special handling for images and tool results. For Responses-style models, it builds the newer input item format. It also declares tools in the exact form the provider expects.

The central class, OpenAIClient, chooses the right API surface based on the model specification, not by guessing from the model name. That matters because some models only accept certain combinations, such as reasoning settings and tools, on one API surface.

When a model streams back a reply, this file converts provider-specific stream chunks into UFO events: text pieces, tool-call starts, tool-call argument pieces, refusal errors, truncation errors, and final token usage. It also retries temporary provider failures before any output has been produced, but once output has started it avoids retrying because that could duplicate or corrupt a partial answer.

#### Function details

##### `openai_sdk_client`  (lines 71–77)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Creates the underlying asynchronous OpenAI SDK client used to talk to OpenAI or an OpenAI-compatible service. It disables the SDK's own retries so this file can apply one consistent retry policy itself.

**Data flow**: It receives an API key and, optionally, a custom base URL for a compatible provider. It uses those values to build an OpenAI SDK client with a fixed timeout and no built-in retries. The result is a ready-to-use network client.

**Call relations**: This is the setup doorway for the lower-level SDK client. Other code can use it before constructing an OpenAIClient, so OpenAIClient can focus on request translation and streaming behavior rather than SDK configuration.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_openai_image`  (lines 80–84)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns UFO's internal image representation into the image format expected by the OpenAI Chat Completions API. It packages the image as a data URL, which means the image bytes are embedded directly in the request text as base64 data.

**Data flow**: It receives an ImageSource containing a media type and base64 image data. It wraps those values in the nested dictionary OpenAI expects for an image URL content part. The output is a small dictionary that can be inserted into an OpenAI message.

**Call relations**: This helper is used when building chat messages directly and when splitting tool results into text and image pieces. It keeps image formatting consistent wherever chat-style OpenAI messages need images.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 87–103)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Splits a tool result into text and images because the chat API can only put plain text in a tool message. Any images are separated so the caller can send them in a follow-up user message.

**Data flow**: It receives either a simple string result or a tuple of content blocks. If the result is text, it returns that text and no images. If the result has blocks, it gathers text blocks into one newline-separated string and converts image blocks into OpenAI image parts. The output is a pair: text for the tool message and image parts for a later message.

**Call relations**: openai_messages calls this when it sees a ToolResultBlock. This helper does the awkward conversion needed because OpenAI's chat format cannot carry image content inside the tool-result message itself.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 106–159)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts UFO's conversation history into the message list required by OpenAI's Chat Completions API. It preserves text, images, tool calls, and tool results while reshaping them into OpenAI's roles and fields.

**Data flow**: It receives a system instruction string and a tuple of UFO messages. It first trims images as needed, then walks through each message. Plain text becomes a normal chat message. Image blocks become OpenAI image content parts. Tool-use blocks become OpenAI function-call records. Tool-result blocks become tool messages, with any images lifted into a following user message. The output is a list of dictionaries ready to send to chat completions.

**Call relations**: OpenAIClient._chat_kwargs calls this while building the chat API request. It is the main translator for the older chat-style surface, and it relies on _openai_image and _openai_tool_result for the trickier image and tool-result cases.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 162–228)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: Converts UFO's conversation history into the input item format required by OpenAI's Responses API. This is the newer API shape, where messages, function calls, function-call outputs, text, and images are represented as typed input items.

**Data flow**: It receives UFO messages and trims images where needed. For plain text, it creates a simple input message. For block content, it groups text and images into message content until it reaches a tool call or tool result, then emits separate Responses API items for those. Tool-call arguments are serialized as JSON, and tool results may become either text output or a list containing text and image output parts. The output is a list of typed input items accepted by the Responses API.

**Call relations**: responses_request calls this to fill the request's input field. It is the Responses API counterpart to openai_messages, but it uses OpenAI SDK typed parameter objects instead of plain chat-message dictionaries.

*Call graph*: called by 1 (responses_request); 9 external calls (dumps, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, trim_images).


##### `responses_request`  (lines 231–256)

```
def responses_request(request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the full request body for the OpenAI Responses API from a UFO ModelRequest. It adds the model name, instructions, converted input, token limit, streaming flag, reasoning settings, and tool definitions.

**Data flow**: It receives a ModelRequest. It converts the message history through responses_input, copies over system instructions and token limits, and sets the request to stream without storing the response. If reasoning is enabled, it adds the requested reasoning effort. If tools are available, it converts each one into a Responses API function tool and sets tool-choice behavior. The output is a dictionary of keyword arguments for the SDK call.

**Call relations**: OpenAIClient._complete_responses calls this immediately before contacting the provider. It gathers all Responses-specific request-building rules in one place so the streaming method can focus on reading events and handling errors.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `OpenAIClient.complete`  (lines 268–271)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Chooses which OpenAI-compatible API surface to use for a model request. It is the public entry point for asking this client to produce streamed model events.

**Data flow**: It receives a ModelRequest. It checks the model specification attached to the client. If the specification says to use the Responses API, it returns the Responses streaming generator; otherwise it returns the Chat Completions streaming generator. The output is an asynchronous stream of UFO model events.

**Call relations**: Higher-level model code calls complete when it wants a model answer. complete then delegates to either _complete_responses or _complete_chat based on the model spec, making the API choice explicit and reliable.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 273–302)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the request body for the Chat Completions API. It decides exactly which chat fields to send, including messages, token limits, streaming options, reasoning effort, tools, and tool-choice rules.

**Data flow**: It receives a ModelRequest. It converts UFO messages into OpenAI chat messages, adds the model name and maximum completion tokens, and asks the model spec what reasoning effort is safe for this request. If tools are present, it converts them into OpenAI function tool declarations and configures whether the model may call tools in parallel or must choose a specific one. The output is a dictionary of keyword arguments for the chat completions SDK call.

**Call relations**: OpenAIClient._complete_chat calls this before starting a chat stream. This keeps request construction separate from stream reading, so the chat streaming loop can stay focused on chunks, retries, and final usage.

*Call graph*: calls 1 internal fn (openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 304–406)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends a request to the Chat Completions API and yields UFO events as the provider streams back text, tool calls, and final usage. It also protects the caller from common provider problems such as timeouts, temporary server errors, empty responses, and truncated completions.

**Data flow**: It receives a ModelRequest. It builds chat request arguments, opens a streaming provider call, and reads chunks as they arrive. Text chunks become TextDelta events. New tool calls become ToolCallStart events, and streamed tool arguments become ToolCallDelta events. Usage information is saved until the end, then yielded as the final event. If the provider times out or returns retryable status errors before any output has been yielded, it waits and retries. If the reply is cut off by the token limit, it raises ModelResponseTruncated instead of pretending the answer is complete.

**Call relations**: OpenAIClient.complete calls this for models that use the Chat Completions surface. It depends on _chat_kwargs to build the outgoing request and hands back standard model events to the rest of UFO, hiding the provider's chunk format.

*Call graph*: calls 1 internal fn (_chat_kwargs); called by 1 (complete); 7 external calls (__init__, __init__, __init__, __init__, __init__, sleep, log).


##### `OpenAIClient._complete_responses`  (lines 408–516)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends a request to the OpenAI Responses API and turns its streamed events into UFO's standard model events. It mirrors the chat path but understands the newer Responses event types, including refusals and incomplete responses.

**Data flow**: It receives a ModelRequest, adjusts the requested reasoning effort according to the model spec, builds the Responses API request, and starts a streaming call. Text deltas become TextDelta events. Function-call item events become ToolCallStart events. Function-call argument deltas become ToolCallDelta events. Completed response usage becomes a final Usage event. Refusal, content-filter, truncation, failed-response, and stream-error events are turned into clear exceptions. Temporary provider failures are retried only before any output has been yielded.

**Call relations**: OpenAIClient.complete calls this for models whose spec says they need the Responses API. It uses responses_request for outgoing request construction, then translates Responses-specific stream events into the same event language used by the rest of the system.

*Call graph*: calls 1 internal fn (responses_request); called by 1 (complete); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, model_copy, log).


### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `extension discovery and model request handling`

OpenRouter is a service that routes one chat request to many possible model companies behind the scenes. This file is the adapter that makes that service fit UFO’s model interface. Without it, UFO could not discover the listed OpenRouter models, send them requests, stream their answers back, or understand their token usage and pricing.

The file does three main jobs. First, it defines the OpenRouter model list, including each model’s id, price, knowledge cutoff, context size, and API key location. Second, it builds an OpenAI-compatible client pointed at OpenRouter’s API address, because OpenRouter uses the same basic chat format as OpenAI. Third, it translates a live streaming response into UFO’s own event stream: text pieces, tool-call starts, tool-call argument pieces, and final usage numbers.

A notable safety feature is its retry behavior. If OpenRouter returns rate-limit or server errors before any answer has started, the client waits and tries again. If OpenRouter routes to an upstream provider that returns an empty answer, the client can retry while asking OpenRouter to avoid that provider, like asking a dispatcher not to send the same broken taxi twice. If the model stops because it hit the token limit, the file raises a clear truncation error instead of pretending the answer finished normally.

#### Function details

##### `openrouter_slug`  (lines 53–63)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Turns a model id into the provider/model name format OpenRouter expects. This lets callers use familiar bare names like an OpenAI or Claude model id while still sending OpenRouter a routeable model slug.

**Data flow**: It receives a model name as text. If the name already contains a slash, it is treated as an OpenRouter slug and returned unchanged. If it looks like an OpenAI or Anthropic Claude model, the function adds the matching provider prefix; otherwise it leaves the name as-is and lets OpenRouter decide what to do with it.

**Call relations**: When OpenRouterModelClient._create_kwargs is preparing the request body for OpenRouter, it asks this function to produce the exact model string to send over the wire.

*Call graph*: called by 1 (_create_kwargs).


##### `_chunk_provider`  (lines 66–71)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Reads which upstream provider OpenRouter used for a streamed response chunk. This matters because an empty answer from one upstream can trigger a retry that excludes that provider.

**Data flow**: It receives one streaming chat chunk from the OpenAI-style response. It looks in the chunk’s extra OpenRouter-specific metadata for a provider field. It returns that provider name as text if present, or nothing if the chunk does not say.

**Call relations**: OpenRouterModelClient.complete calls this while reading the stream. If the completion later turns out to be empty, the remembered provider can be added to the ignore list for a retry.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 74–83)

```
def _usage_of(usage: CompletionUsage) -> Usage
```

**Purpose**: Converts OpenAI-style token usage information into UFO’s own Usage object. It also separates cached input tokens from newly processed input tokens so costs can be counted correctly.

**Data flow**: It receives a CompletionUsage object from the API. It reads total prompt tokens, completion tokens, and any cached prompt-token detail. It checks that cached tokens are not greater than total prompt tokens, then returns a Usage value with input tokens, output tokens, and cache-read tokens split out.

**Call relations**: OpenRouterModelClient.complete calls this when the stream includes final usage data. The returned Usage object is yielded as the final event of a successful model response.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient.complete`  (lines 100–168)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to OpenRouter and streams the answer back in UFO’s standard event format. It is the main bridge between OpenRouter’s OpenAI-compatible streaming API and the rest of the system.

**Data flow**: It receives a ModelRequest containing the model name, messages, tools, reasoning setting, and token budget. It builds API arguments, opens a streaming chat completion, and reads each chunk as it arrives. Text becomes TextDelta events, tool-call starts become ToolCallStart events, tool-call argument fragments become ToolCallDelta events, and final token accounting becomes a Usage event. It may also sleep and retry on temporary provider errors, retry empty upstream responses while excluding a bad provider, or raise an error if the response was cut off by the token limit.

**Call relations**: This is called by the model layer when UFO needs an OpenRouter-backed model to answer. It delegates request-shaping to OpenRouterModelClient._create_kwargs, provider detection to _chunk_provider, and token conversion to _usage_of. It hands the rest of the system a clean stream of ModelEvent objects instead of raw OpenRouter chunks.

*Call graph*: calls 3 internal fn (_create_kwargs, _chunk_provider, _usage_of); 5 external calls (__init__, __init__, __init__, __init__, sleep).


##### `OpenRouterModelClient._create_kwargs`  (lines 170–199)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Builds the exact set of arguments sent to OpenRouter’s chat-completions API. It gathers the request’s messages, model name, token budget, tools, reasoning effort, and any providers to avoid into the format the OpenAI-style SDK expects.

**Data flow**: It receives a ModelRequest and a frozen set of provider names to ignore. It converts the model id with openrouter_slug, converts UFO messages into OpenAI-style messages, adds streaming and usage options, includes reasoning settings when enabled, adds provider exclusions when needed, and formats tool definitions if tools are present. It returns a dictionary of keyword arguments ready for the SDK call.

**Call relations**: OpenRouterModelClient.complete calls this right before starting each API attempt. This function uses openrouter_slug for model naming and openai_messages for message translation, then hands the finished request shape back to complete.

*Call graph*: calls 1 internal fn (openrouter_slug); called by 1 (complete); 1 external calls (openai_messages).


##### `_model_client`  (lines 202–206)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: Creates an OpenRouterModelClient for a specific model spec and API key. This is the factory the model registry can use when someone selects an OpenRouter model.

**Data flow**: It receives a ModelSpec and an API key string. It creates an OpenAI-compatible asynchronous SDK client pointed at OpenRouter’s base URL, then wraps that SDK client and the model spec in an OpenRouterModelClient.

**Call relations**: _openrouter stores this function inside each ModelSpec as the way to build a live client. When the wider system needs to use one of these specs, it can call this factory to get a ready OpenRouter client.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 209–226)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW) -> ModelSpec
```

**Purpose**: Creates a complete model description for one OpenRouter model. The description tells UFO how to identify the model, what it costs, how large its context window is, what key to use, and which client factory can run it.

**Data flow**: It receives a model id, price information, a knowledge cutoff date, and optionally a context-window size. It fills in shared OpenRouter settings such as provider name, API key environment variable, reasoning support, chat API surface, and the _model_client factory. It returns a ModelSpec used by the registry.

**Call relations**: The file calls this repeatedly while building OPENROUTER_MODEL_SPECS. Those specs are later included in the extension manifest so the rest of UFO can discover them.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 249–250)

```
def manifest() -> Manifest
```

**Purpose**: Publishes this extension’s name, version, and available OpenRouter models to UFO. It is the discovery hook that tells the host application what this extension offers.

**Data flow**: It reads the module’s extension name, version, and prepared model specs. It packages them into a Manifest object and returns it.

**Call relations**: The extension-loading system calls this when scanning available extensions. The returned Manifest lets the registry treat the listed OpenRouter models like any other supported model.

*Call graph*: 1 external calls (__init__).


### Conversation context compaction
This file prepares oversized conversation histories by preserving recent context and replacing older turns with a structured summary.

### `core/src/ufo/loop/compaction.py`

`domain_logic` · `request handling, before a model call or after a context-overflow retry`

AI models can only read so much text at once. As a conversation grows, old tool results, images, repeated output, and chat messages can push it past that limit. This file is the system’s safety valve: when the transcript gets too large, it compresses the older part into a clear summary and keeps the newest part exactly as it was.

The main object, Compaction, first estimates how large the current conversation is. If it is still small enough, nothing happens. If it is too large, the code splits the transcript into whole conversation rounds, like keeping complete pages of a notebook instead of tearing a sentence in half. Older rounds become the “head” to summarize, and recent rounds become the “tail” to keep verbatim.

The head is rendered into plain text for a summarizing model call. Images become markers, tool calls are written out, and huge repeated runs of text are folded down to one copy plus a count. The model must return a structured JSON summary, which this file validates before using. If the summarizing request is itself too large, the oldest part is dropped and retried a few times.

Finally, the file builds one replacement message called “Compacted context,” adds durable file references, stores before/after/summary snapshots in compressed blob storage, and fires hooks so observers can notice that compaction happened.

#### Function details

##### `is_context_overflow`  (lines 77–83)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: This helper decides whether an error means the model provider refused a request because the input was too large. It lets the system recover by shrinking the prompt instead of treating every error as fatal.

**Data flow**: It receives an exception, combines the exception type and message into lowercase text, and looks for phrases such as “too long” or “context length.” It returns true when those clues are found, otherwise false.

**Call relations**: Compaction._summarize uses this after a failed summarizing call. If the error looks like a size problem, the compaction flow tries again with less old history; if not, the error is allowed to stop the turn.

*Call graph*: called by 1 (_summarize).


##### `Compaction.maybe_compact`  (lines 111–128)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: This is the public gatekeeper for compaction. It checks whether the current message window is large enough to need shrinking, unless compaction is being forced after a model overflow.

**Data flow**: It receives the current messages and a force flag. It reads the configured window size, summary reserve, buffer, and keep-message count, estimates the message size, and either returns the messages unchanged with no usage records or sends them into the full compaction process.

**Call relations**: The engine calls this when preparing the conversation for the model. It uses Compaction._tokens to estimate size and calls Compaction._compact only when the transcript should actually be compressed.

*Call graph*: calls 2 internal fn (_compact, _tokens).


##### `Compaction._compact`  (lines 131–170)

```
async def _compact(self, messages: tuple[Message, ...], reason: Literal['auto', 'force']) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: This runs the full compaction job once the decision has been made. It chooses what to summarize, asks for the summary, builds the replacement message, saves evidence, and reports the token usage.

**Data flow**: It receives the full message window and the reason for compaction. It splits the window, counts its size, fires a pre-compaction hook, finds the next storage index, summarizes the older messages, gathers durable file references, creates the compacted replacement message, stores before/after/summary records, fires a post-compaction hook, and returns the new message window plus model usage.

**Call relations**: Compaction.maybe_compact hands control here when compaction is needed. This method coordinates the smaller helpers: selection, summarization, rendering, persistence, and token counting. It is wrapped as a DBOS step, meaning crash recovery can replay the recorded result instead of spending tokens or writing duplicate records again.

*Call graph*: calls 7 internal fn (_next_index, _persist, _references, _render, _select, _summarize, _tokens); called by 1 (maybe_compact); 3 external calls (__init__, __init__, __init__).


##### `Compaction._select`  (lines 172–188)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: This chooses the boundary between old messages to summarize and recent messages to keep exactly. It avoids cutting through a tool call and its answer.

**Data flow**: It receives all messages, groups them into complete rounds, then keeps enough newest rounds to cover the configured number of recent messages. If no older rounds remain, it returns nothing; otherwise it returns the older grouped rounds and the flattened recent tail.

**Call relations**: Compaction._compact calls this before doing any expensive work. It relies on Compaction._rounds to create safe groups so later steps can summarize only the older head.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 190–204)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: This groups messages into conversation rounds that should stay together. A round starts around assistant activity and includes the user or tool-result messages that follow it.

**Data flow**: It receives a sequence of messages and walks through them in order. When it sees a new assistant message after an existing group, it closes the current group and starts another. It returns a tuple of message groups.

**Call relations**: Compaction._select calls this so it can keep or summarize whole rounds. That matters because separating a tool request from its result would make the remaining transcript confusing.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 206–228)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: This asks the model to summarize the old part of the transcript, with a controlled retry path if that summarization prompt is still too large.

**Data flow**: It starts with the selected old rounds. It tries to summarize them once; if the provider says the prompt is too large, it drops the oldest slice and tries again, up to the configured retry limit. On success it returns the structured summary and usage record; on repeated failure it raises an error.

**Call relations**: Compaction._compact calls this after choosing the head. This method calls Compaction._summarize_once for each attempt, uses is_context_overflow to recognize size failures, and uses Compaction._drop_oldest to shrink the retry input.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 230–250)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: This performs one actual model call to turn rendered old transcript text into a validated compaction summary.

**Data flow**: It receives grouped old rounds, renders them into a single user message, builds a model request with the compaction system prompt and token limit, streams back text chunks and usage information, joins the chunks, validates the JSON summary, and returns the summary plus usage.

**Call relations**: Compaction._summarize calls this for each attempt. It delegates transcript rendering to Compaction._prepare and output validation to Compaction._parse_summary.

*Call graph*: calls 2 internal fn (_parse_summary, _prepare); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 252–271)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...]) -> str
```

**Purpose**: This turns the old transcript rounds into text that the summarizing model can read. It also reminds the model at the end to answer with only the required JSON object.

**Data flow**: It receives grouped rounds, renders each message as a role plus readable content, joins the messages, folds huge repeated text runs, appends the format reminder, and returns the finished prompt text.

**Call relations**: Compaction._summarize_once uses this while building the model request. It calls Compaction._text to render each message and Compaction._fold_repeated_runs to prevent repeated spam or loops from overwhelming the summarizer.

*Call graph*: calls 2 internal fn (_fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 273–286)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: This compresses long stretches of the exact same short phrase repeated many times. It keeps the information that repetition happened without sending every copy to the summarizing model.

**Data flow**: It receives rendered transcript text, searches for repeated word sequences, and replaces each run with one copy followed by a marker such as “repeated 20 times.” It returns the shortened text.

**Call relations**: Compaction._prepare calls this before sending the transcript head to the model. Its nested fold function builds the replacement text for each repeated run found by the regular expression.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 281–284)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: This small inner helper creates the replacement text for one repeated run. It calculates how many times the repeated unit appeared.

**Data flow**: It receives one regex match, extracts the repeated unit, estimates the repeat count from the matched text length, and returns the unit plus a count marker.

**Call relations**: It is used only inside Compaction._fold_repeated_runs as the replacement callback for each repeated-text match.


##### `Compaction._drop_oldest`  (lines 288–293)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: This shrinks a too-large summarization input by removing the oldest portion. It is the retry strategy when the model says the compaction prompt itself is too big.

**Data flow**: It receives the grouped old rounds, removes the oldest fifth or at least one round, and returns the remaining newer rounds.

**Call relations**: Compaction._summarize calls this between retry attempts after is_context_overflow confirms a size-related failure.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 295–336)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: This turns the model’s raw text response into a trusted CompactionSummary object. It refuses to use empty, malformed, or schema-invalid summaries.

**Data flow**: It receives raw text from the model, finds the first balanced JSON object inside it, validates that JSON against the expected summary shape, checks that the intent is not blank, and returns the typed summary. If any check fails, it raises a RuntimeError.

**Call relations**: Compaction._summarize_once calls this after collecting streamed text from the model. It protects the rest of the compaction pipeline from replacing real history with an unusable summary.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 338–355)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: This finds durable tool-output files mentioned in the old transcript and carries a few of them into the compacted context. These references let the model re-open important large outputs instead of relying only on the summary.

**Data flow**: It receives the summarized head and the kept tail. It scans message text for .tool-output text-file paths, ignores paths already visible in the kept tail, removes duplicates, keeps only the most recent limited set, and returns those paths.

**Call relations**: Compaction._compact calls this after summarization and before rendering the replacement message. It uses Compaction._text so it can scan all supported message content shapes.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 357–388)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...]) -> str
```

**Purpose**: This turns the validated structured summary into the single readable message that replaces the old transcript head. It includes only sections that actually have content.

**Data flow**: It receives a CompactionSummary and durable reference paths. It builds labeled sections such as intent, files, errors, pending tasks, current work, and next step, formats lists as bullets, adds any durable references, and returns one text block starting with “Compacted context.”

**Call relations**: Compaction._compact calls this to create the replacement user message. It uses Compaction._bullets for simple list formatting, and the same rendered text is also sent to the post-compaction hook.

*Call graph*: calls 1 internal fn (_bullets); called by 1 (_compact).


##### `Compaction._bullets`  (lines 390–391)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: This formats a list of strings as Markdown-style bullet lines. It is a small presentation helper for compacted summaries.

**Data flow**: It receives a tuple of strings and returns one string where each item is prefixed with “- ” and separated by newlines.

**Call relations**: Compaction._render calls this whenever a summary section is naturally a list, such as concepts, errors, decisions, pending tasks, or references.

*Call graph*: called by 1 (_render).


##### `Compaction._persist`  (lines 393–404)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: This saves the evidence of a compaction: the original window, the replacement window, and the structured summary. That makes the compression auditable later.

**Data flow**: It receives an index, the before messages, the after messages, and the summary. It writes the before and after windows through Compaction._write, serializes the summary as JSON, compresses it, and stores it in the blob store under the right key.

**Call relations**: Compaction._compact calls this after building the compacted window. It uses Compaction._key to address storage and Compaction._write for message windows.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 406–410)

```
async def _next_index(self) -> int
```

**Purpose**: This finds the next numbered slot for storing a compaction record in the conversation. It prevents a new compaction from overwriting an older one.

**Data flow**: It starts at index 1, checks whether an “after” record already exists for that index, increments until it finds a free slot, and returns that number.

**Call relations**: Compaction._compact calls this just before persistence. It uses Compaction._key to check the blob paths that correspond to each possible index.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 412–419)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: This reads a saved compaction record back from storage. It is useful for inspection, evaluation, or debugging.

**Data flow**: It receives a compaction index, tries to load the compressed before, after, and summary blobs, and returns none if any are missing. If all are present, it decodes them into a CompactionRecord.

**Call relations**: External readers can call this when they need to inspect a past compaction. It uses Compaction._key to locate the blobs and hands the raw stored data to decode_compaction.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 421–429)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: This stores either the pre-compaction or post-compaction message window. It serializes messages in a stable JSON form and compresses them before writing.

**Data flow**: It receives an index, a label of “before” or “after,” and the messages. It wraps the messages in a CompactionWindow, converts that to sorted compact JSON, compresses the bytes with LZ4, and writes them to the blob store.

**Call relations**: Compaction._persist calls this twice, once for the original window and once for the compacted window. It uses Compaction._key to choose the storage location.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 431–432)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: This builds the storage key for one part of a compaction record. It keeps all compaction blobs in the expected conversation-specific location.

**Data flow**: It receives a compaction index and a part name such as before, after, or summary. It combines those with the conversation ID through the shared compaction-key helper and returns the resulting path-like key.

**Call relations**: Storage-related methods call this whenever they need to read, write, or check a compaction blob: Compaction._next_index, Compaction._persist, Compaction._write, and Compaction.read_record.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 434–450)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: This estimates how much of the model’s reading window the current messages will use. It deliberately counts images with a fixed cost so image-heavy conversations still trigger compaction.

**Data flow**: It receives messages, renders their text, counts role and text length using a measured characters-per-token ratio, adds a fixed token estimate for each image, and returns the total estimated token count.

**Call relations**: Compaction.maybe_compact uses this to decide whether compaction is needed. Compaction._compact uses it to report before and after sizes to hooks. It relies on Compaction._text and Compaction._image_count.

*Call graph*: calls 2 internal fn (_image_count, _text); called by 2 (_compact, maybe_compact).


##### `Compaction._image_count`  (lines 452–462)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: This counts images inside a message, including images nested inside tool results. It supports the token estimate used to trigger compaction.

**Data flow**: It receives one message. If the content is plain text, it returns zero. Otherwise it walks through content blocks, counts direct image blocks and image parts inside tool-result blocks, and returns the total.

**Call relations**: Compaction._tokens calls this for each message so the size estimate reflects image content as well as text.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 464–482)

```
def _text(self, message: Message) -> str
```

**Purpose**: This renders any supported message content into readable text. It gives the compaction code a consistent way to inspect messages, even when they contain blocks, images, tool results, or tool calls.

**Data flow**: It receives one message. If the content is already a string, it returns it. Otherwise it walks the content blocks: text stays text, images become “[image],” tool results become their text or image markers, and tool uses become a function-like name plus JSON arguments. It returns the pieces joined with newlines.

**Call relations**: Compaction._prepare uses this to build the summarizer input, Compaction._references uses it to find durable file paths, and Compaction._tokens uses it to estimate message size.

*Call graph*: called by 3 (_prepare, _references, _tokens); 1 external calls (dumps).


### Prompt governance and rendering
These files organize prompt modules, govern approved prompt changes, and render the final fingerprinted system prompt sent to the model.

### `core/src/ufo/governance.py`

`domain_logic` · `request handling`

This file is a safety gate for changing an agent’s prompt. A prompt is the text that guides how an agent behaves, so changing it can change the agent’s behavior in important ways. Instead of letting code overwrite that prompt directly, this file creates a proposal, records what the prompt looked like at that moment, and later approves the proposal only if nothing has changed underneath it.

The key idea is a “digest,” which is a short fingerprint made from the prompt text. If even one character changes, the fingerprint changes. When a proposal is opened, the system stores the old prompt’s digest and the new prompt text. When someone approves the proposal, the system locks and rereads the current agent prompt, checks its digest, and compares it with the stored one. This is like checking that a document is still on the same revision before applying edits.

If the prompt still matches, the new prompt is written to the agent and the proposal is marked approved. If the prompt has changed since the proposal was made, the proposal is marked rejected instead. This prevents stale approvals from accidentally overwriting newer work. All database actions happen inside a workspace transaction, so the checks and writes are kept together safely.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: Creates a stable fingerprint for a prompt string. The rest of the file uses this fingerprint to tell whether a prompt has changed without comparing stored proposal state by hand.

**Data flow**: It receives prompt text, turns that text into bytes, and runs it through SHA-256, a standard hashing method that produces a fixed-length fingerprint. It returns that fingerprint as a hexadecimal string.

**Call relations**: When a change is proposed, Governance.propose_change uses this to record the fingerprint of the proposed new prompt. When a proposal is approved, Governance.approve_proposal uses it again to check whether the current agent prompt still matches the prompt version the proposal was based on.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Opens a new proposal to change an agent’s prompt inside one workspace. It records the requested new prompt and the prompt fingerprint the change expects to start from, but it does not modify the agent yet.

**Data flow**: It receives an AgentChange, which includes the target agent, the expected old prompt fingerprint, and the new prompt text. It creates a new proposal ID, checks in the database that the agent exists in this workspace, stores a pending proposal with the old fingerprint, the new prompt, and the new prompt’s fingerprint, then returns a ProposalRef pointing to the new proposal. If the agent is not found in the workspace, it raises an error instead.

**Call relations**: This is the first half of the governed-change flow. It opens a database transaction through workspace_tx, uses SQLAlchemy to read and insert rows, calls prompt_digest to fingerprint the new prompt, and returns a ProposalRef so later code can refer to this proposal when approval is requested.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: Attempts to apply a pending proposal to an agent. It only updates the agent prompt if the current prompt still matches the fingerprint recorded when the proposal was created.

**Data flow**: It receives a proposal ID and looks up that proposal inside the current workspace. If the proposal does not exist or is no longer pending, it raises an error. It then locks the matching agent row, reads the current prompt, fingerprints it, and compares that fingerprint with the proposal’s expected starting fingerprint. If they differ, it marks the proposal rejected and logs that rejection. If they match, it writes the proposed prompt into the agent row, marks the proposal approved, and logs the approval.

**Call relations**: This is the second half of the governed-change flow. It relies on workspace_tx so the check, possible agent update, and proposal status update happen as one safe database operation. It calls prompt_digest for the compare-and-swap check, uses SQLAlchemy to read and update the database, and sends approval or rejection events to the logging system.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.loop.prompts` using normal Python import paths.

There is no program logic in this file. It does not build prompts, load templates, configure anything, or run any startup code. Its value is structural: it gives the prompt-related code in this directory a clear place in the project’s module tree. A useful analogy is a label on a filing cabinet drawer. The label does not contain the documents, but it lets people and tools know where that drawer belongs and how to find what is inside.

If this file were removed, modern Python may still recognize the folder as a package in some situations, but keeping it avoids ambiguity and preserves compatibility with tools or code that expect an explicit package marker.


### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `turn setup before sending a model request`

A system prompt is like a form letter: the project ships a shared shell, and each run fills in the blanks with the agent’s instructions, available skills, capability sections, citation rules, and the model’s knowledge cutoff date. This file is the renderer for that form letter.

It reads the core prompt text files from disk when the module is loaded. Then, when asked to render a prompt, it replaces known slots such as the agent prompt, the skill list, and shared citation text. It also turns the machine-style knowledge cutoff date, such as "2026-02", into a human-friendly form like "February 2026".

The most important behavior is strict validation. If the agent prompt says it needs a variable, the caller must supply it. If the caller supplies an extra variable the prompt never declared, that is also an error. After filling everything, the renderer checks for any leftover {{name}} placeholder. This prevents accidental raw braces from being sent to the model, which would mean the model received an incomplete or broken instruction.

Finally, the rendered prompt is cleaned up by collapsing excessive blank lines and is packaged with a SHA-256 digest, a content fingerprint used for observability and debugging.

#### Function details

##### `rendered_prompt`  (lines 49–50)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This function wraps finished prompt text in a small result object and adds a digest, which is a stable fingerprint of the exact text. The digest makes it easy to notice and trace prompt changes without comparing the full prompt every time.

**Data flow**: It receives the final prompt content as text. It converts that text into bytes, computes a SHA-256 hash from it, prefixes the hash with "sha256:", and returns a RenderedPrompt containing both the digest and the original content. It does not change any outside state.

**Call relations**: After render_template has filled and cleaned the prompt, it calls rendered_prompt as the final packaging step. rendered_prompt hands back the object that the rest of the system can send to the model and record for observability.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 53–70)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This is the main convenience function for building the normal agent system prompt. It combines the shared shell prompt, the agent’s own instructions, optional skills, contributed sections, and the model’s knowledge cutoff.

**Data flow**: It receives the agent prompt, a list of named sections, an optional list of skills, and a required knowledge cutoff in YYYY-MM form. It turns the cutoff into a readable month and year, inserts that into the knowledge-cutoff block, places that block into the shared shell template, and then passes the prepared template to render_template. The result is a fully rendered prompt with a digest.

**Call relations**: This function is the higher-level entry into the renderer for the main agent prompt. It does the knowledge-cutoff preparation itself, then relies on render_template to do the general slot filling, validation, cleanup, and digest creation.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 73–91)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This function fills a prompt template and makes sure the result is complete. It is the central assembly line for prompt rendering.

**Data flow**: It receives a template, an agent prompt, variable values for that agent prompt, a list of skills, and a list of sections. First it asks _substitute_vars to fill variables inside the agent prompt. If there is an agent prompt but the outer template has no place for it, it raises an error. Then it replaces the skill, citation, section, and agent-prompt slots. After that it scans the whole result for any unresolved {{name}} placeholders and fails loudly if any remain. Finally it trims repeated blank lines and returns a RenderedPrompt through rendered_prompt.

**Call relations**: render_system_prompt calls this after preparing the shared shell template. Inside the rendering flow, render_template delegates smaller jobs to _substitute_vars for agent-prompt variables, render_skill_index for the available-skills block, and rendered_prompt for final packaging.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 94–103)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This function turns the list of loadable skills into the text block that will be inserted into the prompt. If there are no skills, it returns an empty string so the prompt does not contain an empty skill section.

**Data flow**: It receives a sequence of skill name and description pairs. With no skills, it outputs empty text. With skills, it creates an <available_skills> block where each skill appears as a bullet with its description, then returns that block as a string.

**Call relations**: render_template calls this while filling the skill slot in the prompt. Its output becomes the model-visible list of skills available to the agent.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 106–113)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This helper fills named variables inside an agent prompt, while checking both sides for mistakes. It prevents missing values and accidental extra values from silently producing a bad prompt.

**Data flow**: It receives a prompt template and a mapping of variable names to replacement text. It scans the template for declared {{variable}} names, compares them with the supplied variable names, and raises an error if any declared variable is missing or any supplied variable was not declared. If the names match exactly, it replaces each placeholder with its supplied value and returns the filled text.

**Call relations**: render_template calls this before inserting the agent prompt into the larger shell. This means variable problems are caught early, before the full prompt is assembled and sent onward.

*Call graph*: called by 1 (render_template).

## 📊 State Registers Touched

- `reg-conversation-transcript` — The stored conversation history, messages, files, speakers, and outcomes that later stages read and append to.
- `reg-model-catalog` — The shared list of available AI models, providers, limits, prices, and client adapters.
- `reg-prompt-state` — The agent instructions, rendered prompt templates, fingerprints, and governed prompt-change proposals.
- `reg-compaction-state` — The saved summaries and reduced conversation versions used when a conversation is too large for a model call.
- `reg-self-improvement-evals` — The mined failure examples, replay results, grades, and statistical evidence used by self-improvement jobs before proposing prompt changes.
- `reg-agent-runtime-settings` — Persistent non-prompt agent configuration such as selected runtime profile, workflow/tool policy, internet-access setting, and conversation or surface agent bindings.
