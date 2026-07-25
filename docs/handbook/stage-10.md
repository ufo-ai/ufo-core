# Model selection, request dispatch, and streamed response normalization  `stage-10`

This stage is the system’s “call the right AI” layer. It sits in the main work loop, after the program has a prompt or tool request ready, and before the answer is handed back to the rest of the app. The registry is the switchboard: it looks up the requested model, chooses which provider can serve it, finds the needed key, and records pricing information.

The provider files are adapters, like plug shapes for different sockets. The OpenAI adapter sends chat messages to OpenAI or compatible services and converts the streaming reply into the project’s standard text, tool-call, and usage events. The Anthropic adapter does the same for Anthropic’s Messages API, including prompts, tools, and images. The OpenRouter extension routes OpenAI-style requests through OpenRouter’s many model providers. The Bedrock extension connects Amazon Bedrock Mantle models to the same common interface and defines their authentication and costs. The self-improvement extension uses this shared model doorway with extra limits and metering, so its experiments stay controlled and comparable.

## Files in this stage

### Bedrock and core transports
Bedrock Mantle plugs into the model system by routing requests through the shared Anthropic and OpenAI-compatible streaming adapters.

### `extensions/bedrock/ufo_ext_bedrock.py`

`io_transport` · `startup registration and model request handling`

This file is an adapter, like a travel plug between two different electrical sockets. The UFO system has its own way to describe a model request: messages, images, tools, tool results, token limits, and streaming output. Amazon Bedrock Mantle exposes several different model APIs, including Anthropic-style APIs and OpenAI-style APIs. This file translates between those worlds.

At startup, the `manifest` function tells UFO that there is a provider named `bedrock`, which credential it needs, which model names it can serve, and the pricing table for those models. When a model is actually requested, `bedrock_client` checks the model name and returns the right kind of client: Anthropic, OpenAI chat-compatible, or OpenAI Responses-compatible.

The OpenAI Responses path needs extra work, because UFO messages may contain text, images, tool calls, and tool outputs. `responses_input` rewrites those into the shape expected by OpenAI’s Responses API. `responses_request` then builds the full request, including system instructions, tools, reasoning effort, and streaming settings.

`BedrockResponsesClient.complete` sends the request, reads the streamed events as they arrive, and converts them back into UFO events such as text chunks, tool-call starts, tool-call argument chunks, and final token usage. It also retries early provider failures, reports refusals clearly, and detects truncated or failed responses.

#### Function details

##### `responses_input`  (lines 117–181)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: This function converts UFO’s internal message format into the list of input items expected by OpenAI’s Responses API. It is needed because a conversation may contain plain text, images, tool calls, and tool results, and each of those must be represented differently for the provider.

**Data flow**: It receives a tuple of UFO `Message` objects. It first trims images if needed, then walks through each message and each content block. Plain text becomes text input, images become base64 data URLs, tool calls become function-call records, and tool results become function-call-output records. The result is a list of OpenAI Responses API input items ready to send over the network.

**Call relations**: `responses_request` calls this when building the final provider request. In the larger flow, it is the message translator: it takes the conversation as UFO understands it and hands `responses_request` provider-shaped input that OpenAI-compatible Bedrock Mantle can accept.

*Call graph*: called by 1 (responses_request); 9 external calls (dumps, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, trim_images).


##### `responses_request`  (lines 184–209)

```
def responses_request(request: ModelRequest) -> dict[str, Any]
```

**Purpose**: This function builds the complete set of arguments for an OpenAI Responses API call. It combines the model name, system instructions, converted messages, token limits, reasoning settings, and tool definitions into one request package.

**Data flow**: It receives a `ModelRequest`, which is UFO’s full description of what the model should do. It calls `responses_input` to convert the conversation, adds streaming and storage settings, includes reasoning effort when enabled, and translates available tools into OpenAI function tools. It returns a dictionary of keyword arguments that can be passed directly to the OpenAI client.

**Call relations**: `BedrockResponsesClient.complete` calls this right before it contacts the provider. This function sits between the high-level UFO request and the low-level API call, making sure the provider receives the request in the exact shape it expects.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (complete); 1 external calls (FunctionToolParam).


##### `BedrockResponsesClient.complete`  (lines 216–311)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This asynchronous method sends a UFO model request to Bedrock Mantle’s OpenAI Responses-compatible endpoint and streams the answer back as UFO events. Someone uses it when the chosen Bedrock model is served through the OpenAI Responses API rather than Anthropic or OpenAI chat APIs.

**Data flow**: It receives a `ModelRequest`. It turns that request into provider arguments with `responses_request`, sends it to the OpenAI client, then reads the provider’s stream event by event. Text deltas become `TextDelta` events, tool-call starts become `ToolCallStart`, tool-call argument pieces become `ToolCallDelta`, and the final provider token counts become a `Usage` event. If the provider refuses, truncates, fails, times out, or returns no usage, it raises an appropriate error instead of silently pretending the request succeeded.

**Call relations**: This is the main request-handling path for Bedrock models listed as OpenAI Responses models. `bedrock_client` creates a `BedrockResponsesClient` for those models. During completion, this method calls `responses_request`, then hands off to the OpenAI SDK client, converts SDK stream events into UFO stream events, and uses `asyncio.sleep` when retrying early retryable failures.

*Call graph*: calls 1 internal fn (responses_request); 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep).


##### `bedrock_region`  (lines 314–320)

```
def bedrock_region() -> str
```

**Purpose**: This function finds the AWS region that Bedrock Mantle should use. Bedrock endpoints are regional, so without a region the code cannot know where to send requests.

**Data flow**: It reads the `AWS_REGION` environment variable first, then `AWS_DEFAULT_REGION` if the first is not set. If it finds a region, it returns that string. If neither variable exists, it raises an error explaining which environment variables the user must set.

**Call relations**: `bedrock_client` calls this before creating any provider client. It is the small gatekeeper that makes sure the network client is pointed at a real AWS region before any model request can be made.

*Call graph*: called by 1 (bedrock_client).


##### `bedrock_client`  (lines 323–351)

```
def bedrock_client(model: str, key: str) -> ModelClient
```

**Purpose**: This function creates the right model client for a requested Bedrock Mantle model. It hides the fact that different Bedrock models use different underlying API styles.

**Data flow**: It receives a model name and an API key. It calls `bedrock_region` to find the AWS region, then checks which known model group the name belongs to. Anthropic models get an Anthropic Bedrock Mantle client, OpenAI chat-compatible models get an `OpenAIClient`, and OpenAI Responses-compatible models get a `BedrockResponsesClient`. If the model name is not in any supported group, it raises an error.

**Call relations**: The provider specification created by `manifest` points to this function as the client factory. When UFO decides a Bedrock model should serve a request, it calls `bedrock_client`, which then creates and returns the client object that will actually perform completions.

*Call graph*: calls 1 internal fn (bedrock_region); 6 external calls (__init__, __init__, __init__, AsyncAnthropicBedrockMantle, cast, openai_sdk_client).


##### `manifest`  (lines 354–378)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the Bedrock extension to the UFO plugin system. It says what the provider is called, what credential it needs, which models it supports, how to build clients, and what prices apply.

**Data flow**: It takes no input. It creates a credential slot for the Bedrock API key, creates a model provider specification that matches known Bedrock model IDs, connects that provider to `bedrock_client`, attaches the environment variable used for the API key, and includes the price table. It returns a `Manifest` object that UFO can read during extension loading.

**Call relations**: This is the file’s registration point. The wider system calls `manifest` when loading the extension, then later uses the returned provider specification to match model names and call `bedrock_client` when a Bedrock-backed model is needed.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/models/anthropic.py`

`io_transport` · `request handling`

This file lets the rest of the project talk to Anthropic models without needing to know Anthropic's exact request and streaming formats. Think of it like a travel adapter: UFO has its own plug shape for messages, images, tool calls, and usage counts, while Anthropic expects a different shape. This file converts between the two.

First, it builds an Anthropic SDK client with the project's own retry policy, rather than relying on the SDK's built-in retries. Then it translates UFO content blocks into Anthropic content blocks. Plain text stays plain text. Images become base64 image objects. Tool-use and tool-result messages are reshaped into the format Anthropic expects.

The main piece is `AnthropicClient.complete`. It sends a model request as a streaming Anthropic request. As chunks arrive, it yields UFO events: text pieces, tool-call starts, and tool-call JSON fragments. At the end, it yields exactly one `Usage` record showing token counts, including cache reads and writes.

The file is careful about failures. Timeouts and temporary provider errors are retried before any output has been yielded. Once the caller has started receiving output, failures are raised immediately, because retrying could duplicate partial answers. It also treats truncation and model refusal as clear errors instead of pretending they are normal completions.

#### Function details

##### `anthropic_sdk_client`  (lines 40–44)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the Anthropic asynchronous client used to make API calls. It turns off the SDK's own retry behavior so this file can apply one consistent retry policy itself.

**Data flow**: It receives an API key → builds an `anthropic.AsyncAnthropic` client with a fixed timeout and zero SDK retries → returns that ready-to-use client object.

**Call relations**: This is the setup doorway for Anthropic access. It calls Anthropic's SDK constructor, and the resulting client is meant to be stored inside `AnthropicClient`, whose `complete` method later uses it to send model requests.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 47–51)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts UFO's internal image description into the image shape Anthropic's API expects. It is a small helper for keeping image conversion consistent in normal messages and tool results.

**Data flow**: It receives an `ImageSource`, which contains the image media type and base64 data → wraps those fields in Anthropic's image-content dictionary format → returns that dictionary.

**Call relations**: This helper is used whenever higher-level conversion code finds an image. `anthropic_content` uses it for images in regular message content, and `_anthropic_tool_result_part` uses it for images returned by tools.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 54–59)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic's expected format. A tool result may contain text or images, and this function knows how to translate each kind.

**Data flow**: It receives one tool-result content block → if it is text, it creates an Anthropic text dictionary; if it is an image, it delegates image formatting to `_anthropic_image` → returns the converted dictionary.

**Call relations**: This function is called by `anthropic_content` when a message includes a tool result with multiple content parts. It hands image work off to `_anthropic_image` so image formatting stays in one place.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 62–87)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Turns UFO message content into content Anthropic can accept. It supports plain strings, text blocks, image blocks, tool-use blocks, and tool-result blocks.

**Data flow**: It receives either a simple string or a tuple of UFO content blocks → strings pass through unchanged; structured blocks are inspected one by one and converted into Anthropic dictionaries → returns either the original string or a list of converted content dictionaries.

**Call relations**: `AnthropicClient.complete` calls this while building the outgoing Anthropic request. When it encounters images, it uses `_anthropic_image`; when it encounters structured tool-result parts, it uses `_anthropic_tool_result_part`.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 94–245)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one UFO model request to Anthropic and streams the answer back as UFO model events. It is responsible for request formatting, streaming conversion, retry behavior, refusal handling, truncation detection, and final usage reporting.

**Data flow**: It receives a `ModelRequest` containing the model name, system prompt, conversation messages, token limit, reasoning setting, and optional tools → trims images from the message history as needed, converts message content with `anthropic_content`, adds Anthropic-specific options, and opens a streaming API call → as Anthropic sends events, it yields UFO events such as `TextDelta`, `ToolCallStart`, and `ToolCallDelta` → after the stream finishes normally, it yields one `Usage` record with token counts and then returns. If Anthropic times out or reports a retryable problem before anything has been yielded, it waits and retries. If the model refuses, runs out of token budget, or the stream is malformed, it raises a clear error instead.

**Call relations**: This is the main runtime path for the file. Callers use it when they need an Anthropic completion. It calls `anthropic_content` to prepare messages, calls the Anthropic SDK to create a stream, constructs UFO event objects from incoming stream chunks, sleeps between retry attempts when needed, and raises `ModelResponseTruncated` or `ModelRefusal` for special stop reasons.

*Call graph*: calls 1 internal fn (anthropic_content); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, trim_images, log).


### `core/src/ufo/models/openai.py`

`io_transport` · `request handling during a model call`

This file solves a translation problem. The rest of the system talks in UFO's own message and event types, but OpenAI expects a particular JSON shape and sends back a stream in its own format. Without this file, UFO could not reliably ask OpenAI-style models for answers, pass images and tool calls to them, or understand their streamed responses.

The flow is like using an interpreter between two people who speak different dialects. First, helper functions convert UFO messages into OpenAI messages. Plain text becomes normal chat content. Images become OpenAI image URL parts using base64 data. Tool requests become OpenAI function calls. Tool results are split carefully because OpenAI only accepts tool-result text in a tool message; any images from a tool result are moved into a following user message.

The main piece, OpenAIClient.complete, sends the converted request with streaming turned on. As chunks arrive, it yields small UFO events: text pieces, the start of a tool call, tool-call argument fragments, and finally token usage. It also contains the safety behavior around unreliable providers: it retries timeouts and temporary server errors before any output has been yielded, waits when the provider asks it to slow down, detects truncated responses, and treats completely empty replies as retryable a few times.

#### Function details

##### `openai_sdk_client`  (lines 37–43)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Creates the low-level asynchronous OpenAI SDK client used to talk to the provider. It deliberately turns off the SDK's own retries so this file's OpenAIClient can apply one clear retry policy.

**Data flow**: It receives an API key and, optionally, a base URL for an OpenAI-compatible service. It builds an AsyncOpenAI client with a fixed timeout, no SDK retries, and the given connection details, then returns that client for later model requests.

**Call relations**: This is the setup doorway for the transport layer. Other code can call it when constructing an OpenAIClient, and the returned SDK object is what OpenAIClient.complete later uses to create streaming chat completions.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_openai_image`  (lines 46–50)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns UFO's internal image data into the image format OpenAI expects. It packages a base64 image as a data URL, which is a text form of an embedded image.

**Data flow**: It receives an ImageSource containing a media type, such as image/png, and base64 image data. It wraps those fields into an OpenAI image_url dictionary and returns that dictionary.

**Call relations**: This small converter is used whenever images need to cross the boundary into OpenAI's message format. openai_messages uses it for images in normal messages, and _openai_tool_result uses it for images returned by tools.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 53–69)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Splits a tool result into the parts OpenAI can accept in different places. OpenAI tool messages are text-only, so image results must be separated and sent later as user image content.

**Data flow**: It receives either a plain string result or a tuple of result blocks. If the result is text, it returns that text and no images. If the result contains several blocks, it collects text blocks into one newline-joined string, converts image blocks with _openai_image, and returns both the text and the image list.

**Call relations**: openai_messages calls this while converting UFO tool-result blocks. It hands back text for an OpenAI tool message and image parts that openai_messages lifts into a following user message so the model can still see them.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 72–125)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts UFO's conversation history into the list of messages expected by the OpenAI Chat Completions API. This includes system instructions, text, images, tool calls, and tool results.

**Data flow**: It receives the system prompt and a tuple of UFO Message objects. It first adds the system message, then walks through the conversation after trim_images has reduced image history as needed. For each message, it converts plain text directly, converts image blocks into OpenAI image parts, serializes tool-call inputs into JSON text, turns tool results into OpenAI tool messages, and moves any tool-result images into a follow-up user message. It returns a list of OpenAI-shaped dictionaries ready to send over the API.

**Call relations**: OpenAIClient.complete calls this just before making the provider request. Inside the conversion, it relies on _openai_image for image formatting, _openai_tool_result for tool outputs, json.dumps for tool arguments, and trim_images to keep image-heavy histories within practical limits.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (complete); 2 external calls (dumps, trim_images).


##### `OpenAIClient.complete`  (lines 132–261)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to OpenAI and streams the answer back as UFO ModelEvent objects. It is the main runtime path for getting text, tool calls, and token usage from an OpenAI-style provider.

**Data flow**: It receives a ModelRequest containing the model name, system prompt, prior messages, token limit, reasoning setting, and available tools. It converts the messages with openai_messages, builds the provider request, and starts a streaming chat completion. As provider chunks arrive, it turns text chunks into TextDelta events, tool-call beginnings into ToolCallStart events, tool-call argument fragments into ToolCallDelta events, and the final usage report into a Usage event. It also changes behavior on errors: before any output is yielded, it retries timeouts and retryable HTTP status errors with delays; after output has started, it raises failures immediately. If the provider says the reply was cut off by the token limit, it raises ModelResponseTruncated. If the stream has no usage data, it raises an error. If the provider returns an empty stop response, it retries a few times before yielding usage and ending.

**Call relations**: This method is called by the higher-level model-running code when UFO needs a response from an OpenAI-compatible model. It hands message conversion to openai_messages, uses the OpenAI SDK stream as the outside source of truth, emits UFO event objects for the rest of the system to consume, logs timeout retries through ufo.o11y.log, waits between retries with asyncio.sleep, and raises ModelResponseTruncated when the provider stopped because the requested token budget was exhausted.

*Call graph*: calls 1 internal fn (openai_messages); 7 external calls (__init__, __init__, __init__, __init__, __init__, sleep, log).


### Model registry
The registry resolves requested models to available providers, credentials, and pricing metadata.

### `core/src/ufo/models/registry.py`

`orchestration` · `startup to turn/request handling`

This file is the switchboard for AI model backends. The project can talk directly to Anthropic and OpenAI, and extensions can add more model providers through manifests. The registry combines all of these into one ordered table, with the built-in providers first. That order matters: if a normal OpenAI or Anthropic model name is requested, the direct built-in client wins before any extension gets a chance.

The registry also joins together pricing information, so the same source of truth can answer both “who serves this model?” and “how should this model be priced?” Without this file, model selection would be scattered around the codebase, and unknown models might be guessed at instead of failing clearly.

A key idea here is that API keys are resolved late, when a model is actually used. A workspace may bring its own key, often called BYOK (“bring your own key”), or the system may fall back to a platform key from an environment variable. This means one missing provider key does not break the whole server at startup; it only fails if someone tries to use that provider. The registry also supports an `auto` model setting, which acts like saying “use the deployment’s default model” until runtime resolves it to a real model name.

#### Function details

##### `ModelRegistry.resolve`  (lines 44–48)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special `auto` model choice into the concrete default model configured for this deployment. If the caller already gave a specific model name, it leaves it alone.

**Data flow**: It receives a model name. If that name is the project’s `auto` marker, it replaces it with the registry’s configured default model; otherwise it returns the original name unchanged. It does not change any stored state.

**Call relations**: This is used before the system chooses a provider or prices a run, so later steps work with a real model name instead of the generic `auto` shortcut.


##### `ModelRegistry.client_for`  (lines 50–67)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: This creates the actual model client that should serve a given model, using the right provider and the right API key for the current workspace. Someone uses it when they are ready to make a model call.

**Data flow**: It receives a model name, finds the first provider that claims it, then decides whether that provider needs an API key. If no key is needed, it builds the client with an empty key. If a key is needed, it asks the current workspace for the workspace’s saved key first, or the configured environment variable fallback. If no usable key exists, it raises a clear error. On success, it returns a ready-to-use model client.

**Call relations**: It asks `ModelRegistry._provider_for` to choose the provider, then asks `ws_current` for the workspace tied to the current operation so the call can use that workspace’s credentials. This keeps provider choice and key lookup in one place before the turn loop sends work to the model.

*Call graph*: calls 1 internal fn (_provider_for); 1 external calls (ws_current).


##### `ModelRegistry.key_slot_for`  (lines 69–77)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This answers which workspace BYOK credential slot would be used for a model, if any. It is useful for reporting or billing code that needs to label whether a model call used a workspace-owned key or a platform-served provider.

**Data flow**: It receives a model name and scans the registry’s providers for the first one that matches. If no provider matches, or the matching provider does not use a workspace key slot, it returns `None`. Otherwise it returns the provider’s key slot name, such as the Anthropic or OpenAI key slot.

**Call relations**: Unlike `client_for`, this is deliberately quiet rather than strict. It does not raise an error for old or unknown model names, which lets accounting-style flows keep moving even if a historical model provider has since been removed.


##### `ModelRegistry.model_key_env`  (lines 79–88)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding code which environment variable should be checked before the first use of a built-in model provider. It only knows how to eagerly check the core Anthropic and OpenAI providers.

**Data flow**: It receives a model name and the project configuration. It finds the provider for that model, looks at the provider’s name, and returns the configured environment variable name for Anthropic or OpenAI. For extension-provided models, it returns `None` because those providers resolve their keys later in their own way.

**Call relations**: It relies on `ModelRegistry._provider_for` to identify who serves the model. This function is part of the early setup or onboarding path, while `client_for` is the later runtime path that actually obtains the key and builds the client.

*Call graph*: calls 1 internal fn (_provider_for).


##### `ModelRegistry._provider_for`  (lines 90–94)

```
def _provider_for(self, model: str) -> ModelProviderSpec
```

**Purpose**: This is the registry’s private lookup helper. It finds the first provider in the ordered table that says it can serve a given model, and fails clearly if none can.

**Data flow**: It receives a model name and checks each provider’s matching rule in order. If one matches, it returns that provider specification. If none match, it raises a `ValueError` saying that no provider serves the model.

**Call relations**: Both `ModelRegistry.client_for` and `ModelRegistry.model_key_env` call this so they make provider decisions in exactly the same way. It is the central gatekeeper that prevents the system from guessing a backend for an unknown model.

*Call graph*: called by 2 (client_for, model_key_env).


##### `model_registry`  (lines 97–123)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the active `ModelRegistry` from configuration and extension manifests. It is the setup function that gathers built-in providers, adds contributed providers, and prepares the combined pricing table.

**Data flow**: It receives the loaded configuration and a tuple of manifests from extensions. It creates built-in provider entries for Anthropic and OpenAI, including their model-name matching rules, client builders, key slots, and configured key environment variables. It then appends all model providers contributed by manifests, collects their price entries, merges those prices with the core pricing table, and returns a frozen `ModelRegistry` containing the providers, pricing, and configured default `auto` model.

**Call relations**: This is called during setup to create the registry used later by model selection, credential lookup, and accounting. It constructs `ModelProviderSpec` entries, passes contributed prices through `pricing_with`, and returns the `ModelRegistry` object that the rest of the system consults during model runs.

*Call graph*: 3 external calls (__init__, __init__, pricing_with).


### Extension model gateways
OpenRouter and the self-improvement extension add higher-level model access paths that reuse the system’s common request and metering conventions.

### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `request handling`

OpenRouter speaks the same basic “chat completions” protocol as OpenAI, but it can send a request onward to many different upstream providers. This file is the adapter that makes that look like a normal model client to the rest of the system. Without it, the project could only use the built-in direct providers, and OpenRouter-only model names would have nowhere to go.

The file does a few OpenRouter-specific jobs. First, it turns model names into OpenRouter slugs, such as changing a bare OpenAI model name into `openai/...` or a Claude model into `anthropic/...`. Second, it builds the request OpenRouter expects, including streamed output, tool definitions, usage reporting, and optional “reasoning” effort. Third, it reads the streamed response piece by piece and converts it into the project’s own events: text chunks, tool-call starts, tool-call argument chunks, and final token usage.

It also protects the caller from some common service problems. Temporary rate-limit or server errors are retried before any output has been produced. If OpenRouter returns an empty answer from one upstream provider, the client can try again while asking OpenRouter to avoid that provider, like asking a dispatcher to try a different driver after one arrives with an empty package.

#### Function details

##### `openrouter_slug`  (lines 58–68)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Turns the model name used by the rest of the system into the model name format OpenRouter expects. If the name already names a provider, it leaves it alone; otherwise it adds known provider prefixes for OpenAI and Anthropic-style models.

**Data flow**: It receives a model string. It checks whether the string already contains a slash, whether it starts like an OpenAI model, or whether it starts like a Claude model. It returns the original string or a provider-prefixed version that can be sent to OpenRouter.

**Call relations**: When `OpenRouterModelClient._create_kwargs` prepares an API request, it calls this function so the outgoing request uses the right OpenRouter model slug.

*Call graph*: called by 1 (_create_kwargs).


##### `_chunk_provider`  (lines 71–76)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Finds which upstream provider OpenRouter used for a streamed response chunk, when OpenRouter includes that extra detail. This matters because the client may ask OpenRouter to avoid that provider if it produces an empty completion.

**Data flow**: It receives one streamed chat chunk from the OpenAI-compatible API. It looks in the chunk’s extra metadata for a `provider` value. It returns that provider name as text, or returns nothing if the chunk does not say.

**Call relations**: `OpenRouterModelClient.complete` calls this while reading the stream. The provider name it returns can later be added to an ignore list if the response ended cleanly but produced no useful text or tool calls.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 79–88)

```
def _usage_of(usage: CompletionUsage) -> Usage
```

**Purpose**: Converts OpenAI-style token usage numbers into the project’s own `Usage` object. It separates normal input tokens from cached input tokens so costs can be counted correctly.

**Data flow**: It receives the usage record from the API. It reads total prompt tokens, completion tokens, and any cached prompt tokens. If cached tokens are impossibly larger than total prompt tokens, it raises an error; otherwise it returns a `Usage` value with input, output, and cache-read token counts.

**Call relations**: `OpenRouterModelClient.complete` calls this when a streamed chunk includes final usage information. The returned `Usage` object is yielded at the end of a successful model response.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient.complete`  (lines 104–172)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to OpenRouter and streams the answer back in the project’s standard event format. It is the main runtime path for using OpenRouter as a model backend.

**Data flow**: It receives a `ModelRequest` containing the model name, messages, token limit, tools, and reasoning setting. It builds OpenRouter API arguments, opens a streaming request, then reads chunks as they arrive. Text becomes `TextDelta` events, tool calls become `ToolCallStart` and `ToolCallDelta` events, and final token counts become a `Usage` event. It may also sleep and retry on temporary API errors, raise a truncation error if the model hit the token limit, or retry with a provider excluded if the first provider returned an empty result.

**Call relations**: The rest of the system calls this when it wants a response from an OpenRouter-backed model. Inside, it hands request-building to `OpenRouterModelClient._create_kwargs`, uses `_chunk_provider` to remember which upstream provider answered, uses `_usage_of` to translate token accounting, and emits the project’s standard model events for downstream turn-processing code.

*Call graph*: calls 3 internal fn (_create_kwargs, _chunk_provider, _usage_of); 5 external calls (__init__, __init__, __init__, __init__, sleep).


##### `OpenRouterModelClient._create_kwargs`  (lines 174–202)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Builds the exact set of arguments sent to OpenRouter’s OpenAI-compatible chat API. This keeps request-shaping separate from the streaming and retry logic.

**Data flow**: It receives a model request and a set of provider names to avoid. It converts the model name with `openrouter_slug`, converts the conversation into OpenAI-style messages, adds token limits, streaming options, optional reasoning settings, optional ignored providers, and optional tool definitions. It returns a dictionary of API arguments.

**Call relations**: `OpenRouterModelClient.complete` calls this right before making each OpenRouter request. If an empty response forces a retry, `complete` calls it again with a larger ignore list so OpenRouter can choose a different upstream provider.

*Call graph*: calls 1 internal fn (openrouter_slug); called by 1 (complete); 1 external calls (openai_messages).


##### `_model_client`  (lines 205–206)

```
def _model_client(model: str, key: str) -> OpenRouterModelClient
```

**Purpose**: Creates an `OpenRouterModelClient` using the supplied API key. It is the factory function the manifest gives to the host system when a model should be served through OpenRouter.

**Data flow**: It receives a model name and an API key. The model name is not needed at this construction step; the key is used to create an asynchronous OpenAI-compatible SDK client pointed at OpenRouter’s base URL. It returns an `OpenRouterModelClient` wrapping that SDK client.

**Call relations**: The provider specification created by `manifest` refers to this function as its client builder. When the host chooses the OpenRouter provider, it calls this function to get the client that will later run `complete`.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `manifest`  (lines 209–223)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the wider system: its name, version, how to match models, how to create a client, where to find the API key, and known prices. This is how the extension is discovered and registered.

**Data flow**: It takes no input. It creates a `Manifest` containing one model provider specification for OpenRouter. That specification matches any model not claimed earlier by more specific providers, points to `_model_client`, names the API key environment variable and key slot, and includes pinned price data for known OpenRouter slugs.

**Call relations**: The extension loader calls this when loading the OpenRouter extension. The returned manifest tells the host system to use `_model_client` when it needs an OpenRouter-backed client, which then produces an `OpenRouterModelClient` for actual requests.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `cross-cutting model calls`

The self-improvement extension needs to ask a language model for two kinds of help. Sometimes it wants plain text back, such as a proposal or evaluation. Other times it wants a full model “turn,” which may include tool use information as part of a conversation. This file defines those two narrow needs and then adapts them to the project’s wider model access system.

Think of it like a ticket counter at a train station. The rest of the extension does not need to know how the whole rail network works. It only needs to say where it wants to go and receive the right kind of ticket. Here, `ModelAccessLeg` is that counter: it takes a system instruction, conversation messages, and optionally tool descriptions, then builds a `ModelRequest` for the SDK’s `ModelAccess` object.

A key detail is that every request is capped at `MAX_OUTPUT_TOKENS`, currently 2048, and has reasoning turned off. That makes calls more predictable and keeps usage under control. The file also defines two protocol classes, `ModelLeg` and `ReplayLeg`. A protocol is like a promise about what methods an object must provide. This lets other code depend on a simple shape instead of a specific implementation, which makes testing and swapping model backends easier.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This describes the simplest kind of model call the extension needs: send instructions and prior messages, then get text back. It is a protocol method, so it defines an expected behavior rather than doing the work itself.

**Data flow**: The caller provides a system instruction and a tuple of conversation messages. Any object that claims to be a `ModelLeg` must turn that input into a final text string. This protocol method itself has no body, so it changes nothing directly.

**Call relations**: Other self-improvement code can ask for a `ModelLeg` when it only needs text completion. A real implementation, such as `ModelAccessLeg.complete`, supplies the actual connection to the SDK model service.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This describes a fuller model conversation step where the model can see available tools and return a complete message. It is used for replay-style interactions where the response may need more structure than plain text.

**Data flow**: The caller provides a system instruction, existing conversation messages, and tool descriptions. Any object matching this protocol must produce one model message in response. Since this is only a protocol declaration, it does not perform the call itself or modify anything.

**Call relations**: Replay or simulation code can depend on this small interface without caring which model backend is underneath. `ModelAccessLeg.turn` is the concrete version in this file that forwards the request to the SDK.


##### `ModelAccessLeg.complete`  (lines 28–37)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This performs a plain text model completion through the SDK’s metered model access layer. It is used when the extension wants a textual answer and does not need tool-aware conversation behavior.

**Data flow**: It receives a system instruction and conversation messages. It wraps them in a `ModelRequest`, adds the selected model name from `ModelAccess`, caps the answer length at 2048 tokens, and turns reasoning off. It then sends that request to `self.model.complete` and returns the resulting text.

**Call relations**: This is the concrete worker behind the `ModelLeg.complete` promise. Code that only knows it needs a text completion can call this adapter, and the adapter hands the properly shaped request to the SDK by constructing a `ModelRequest` first.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 39–51)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This performs a tool-aware model turn through the SDK’s metered model access layer. It is used when the extension needs the model to respond as part of a conversation that may include available tools.

**Data flow**: It receives a system instruction, conversation messages, and tool schemas, which are descriptions of tools the model may use. It packages those into a `ModelRequest`, includes the configured model name, limits the output to 2048 tokens, and disables reasoning. It sends the request to `self.model.turn` and returns the resulting model message.

**Call relations**: This is the concrete worker behind the `ReplayLeg.turn` promise. Replay-style code calls it when it needs a full message back, and this adapter translates that need into the SDK’s standard `ModelRequest` format before passing it onward.

*Call graph*: 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-effective-config` — The combined settings that tell the service which features, adapters, limits, and deployment options to use.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-model-catalog` — The model switchboard that maps model names to providers, credentials, request formats, and prices.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-observability-trace-state` — The logs, metrics, traces, traceparent links, and redaction rules used to monitor work safely.
- `reg-adapter-implementation-registry` — Process-wide mapping from configured backend/provider names to implementation adapters for Redis hubs, sandboxes, browsers, models, search, sources, and related services.
- `reg-http-client-pools` — Shared outbound HTTP client/session pools and retry-capable transport state used for provider APIs, OAuth/credential bridges, connectors, model calls, billing, email, and other integrations.
