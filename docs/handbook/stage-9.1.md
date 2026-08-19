# Model catalogs and provider bridges  `stage-9.1`

This stage is shared behind-the-scenes support for choosing and using AI models. It is like a phone book plus a set of adapters: the rest of the system asks for a model by name, and this stage knows what it costs, how much text it can handle, what provider to call, and how to translate messages to and from that provider.

The model description shape lives in spec.py, which defines the official record for a model. catalog.py fills that shape with the built-in OpenAI and Anthropic models. registry.py combines these records into one master lookup table for the rest of the app. anthropic.py and openai.py are the bridges that actually send chat requests and turn streamed replies back into UFO’s common event format, including retries, errors, and token counting. The Bedrock extension adds Amazon Bedrock Mantle models and the right client style for each. The OpenRouter extension adds extra chat models plus image and video routes. The OpenAI embedding extension turns text into number lists so indexing and memory can compare meaning.

## Files in this stage

### Provider model catalogs
Built-in and extension catalogs declare the available chat models, pricing, limits, routing metadata, and provider-specific capabilities.

### `core/src/ufo/models/catalog.py`

`config` · `startup and model/pricing lookup`

This file is like the price sheet and address book for the system’s built-in AI models. When the rest of the program wants to use a model, it needs more than a name. It needs to know which provider owns it, how to create the right client for talking to it, how many tokens it can safely fit in one request, what its knowledge cutoff is, whether it supports reasoning, and how much usage should cost in the ledger.

The file defines small helper functions for making Anthropic and OpenAI model entries. Those helpers fill in repeated details, such as the provider name, the environment variable that should contain the API key, and the wrapper client to use. The main function, `core_model_specs`, then lists every model shipped by core and gives each one its pricing and limits.

A few details matter for correctness. Some OpenAI GPT-5.6 models must use the “Responses” API surface rather than the older chat-completions route because a certain mix of tools and reasoning settings is only legal there. Also, even if those models accept larger requests, the catalog records a smaller context window because that is the range where the listed price is accurate. At import time, the file builds default model specs, a price table, and a pricing digest so other parts of the system can refer to one shared source of truth.

#### Function details

##### `_anthropic_client`  (lines 27–28)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: Creates the Anthropic-specific client wrapper for one model. It is used when a `ModelSpec` needs a callable way to talk to Anthropic with the correct API key and model details.

**Data flow**: It receives a model specification and an API key string. It first creates the lower-level Anthropic software development kit client, then wraps that client together with the model specification in an `AnthropicClient`. The result is an object the rest of the system can use without caring about Anthropic’s raw library details.

**Call relations**: This function is attached to Anthropic `ModelSpec` entries by `_anthropic`. Later, when the system chooses one of those models and has an API key, the spec can call this factory to produce the actual Anthropic client used for requests.

*Call graph*: 2 external calls (__init__, anthropic_sdk_client).


##### `_openai_client`  (lines 31–32)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: Creates the OpenAI-specific client wrapper for one model. It gives the rest of the system a consistent client object while hiding the details of OpenAI’s SDK setup.

**Data flow**: It receives a model specification and an API key string. It builds the lower-level OpenAI SDK client from the key, then combines that with the model specification inside an `OpenAIClient`. The output is the ready-to-use client wrapper for that model.

**Call relations**: This function is attached to OpenAI `ModelSpec` entries by `_openai`. When a selected OpenAI model is actually used, the spec can call this factory to make the request client with the configured key.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 35–55)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS_WITH_TOOLS) -> ModelSpec
```

**Purpose**: Builds one catalog entry for an Anthropic model. It saves repeated Anthropic defaults in one place so the main catalog can stay readable and consistent.

**Data flow**: It receives the model id, price, knowledge cutoff, API-key environment variable name, and optional settings such as context window and reasoning support. It combines those with Anthropic defaults: provider name, client factory, chat API surface, key slot, and key environment variable. The output is a `ModelSpec`, which is the system’s complete record for that model.

**Call relations**: `core_model_specs` calls this helper once for each built-in Claude model. `_anthropic` does not contact Anthropic itself; it prepares the description that later lets the registry and request code create the right client and bill the right price.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 58–72)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: Builds one catalog entry for an OpenAI model. It centralizes OpenAI-specific defaults, including the provider name, context window, reasoning support, and API-key location.

**Data flow**: It receives the model id, price, knowledge cutoff, API-key environment variable name, and optionally which OpenAI API surface to use. It combines those inputs with OpenAI defaults and returns a `ModelSpec` containing everything the system needs to choose, call, and price that model.

**Call relations**: `core_model_specs` calls this helper once for each built-in GPT model. For models that need the newer Responses route, `core_model_specs` passes that choice in, and `_openai` records it in the finished spec.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 75–173)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: Returns the full list of model specifications that core ships with. This is the central place where built-in Anthropic and OpenAI models are named, priced, and described.

**Data flow**: It receives the environment variable names that should be used for Anthropic and OpenAI API keys. It creates `ModelPrice` records for each model, passes those prices and other facts into `_anthropic` or `_openai`, and returns a tuple of complete `ModelSpec` entries. Nothing is sent to the providers here; it only builds the catalog data.

**Call relations**: This function is used at module load time to create `CORE_MODEL_SPECS`, the default built-in model list. The module then derives `CORE_PRICES`, `CORE_PRICING`, and `PRICE_DIGEST` from that list so the rest of the system can look up model costs and verify the pricing table from one shared source.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `startup and provider discovery`

This extension is like a catalog card plus a set of connection instructions for Amazon Bedrock Mantle. UFO’s core system already knows how to talk to Anthropic-style and OpenAI-style APIs, so this file does not translate requests itself. Instead, it says: “these model IDs exist on Bedrock, here is what they cost, here is how much context they accept, here is which API style they use, and here is how to connect to them.”

The file expects a Bedrock API key from a named credential slot or from the environment variable AWS_BEARER_TOKEN_BEDROCK. It also requires an AWS region, read from AWS_REGION or AWS_DEFAULT_REGION. Without the region, the system cannot build the Bedrock Mantle endpoint URL, so it raises an error early rather than making a confusing failed network call later.

There are two client builders. Anthropic model IDs use Anthropic’s Bedrock Mantle client and are wrapped in UFO’s AnthropicClient. OpenAI-compatible model IDs use UFO’s OpenAIClient pointed at a Bedrock Mantle URL. The model list is built from small helper functions so each model has a consistent provider name, key location, price, context window, reasoning support, and API surface. Finally, manifest() exposes all of this to the plugin system so UFO can discover the Bedrock provider.

#### Function details

##### `bedrock_region`  (lines 47–53)

```
def bedrock_region() -> str
```

**Purpose**: This function finds the AWS region that Bedrock Mantle should use. It checks the usual environment variables and fails with a clear message if neither is set, because the region is needed to build the service endpoint.

**Data flow**: It reads AWS_REGION first, then AWS_DEFAULT_REGION if the first one is missing. If it finds a value, it returns that region string. If both are empty or unset, it raises a RuntimeError telling the user which environment variables to set.

**Call relations**: The Anthropic and OpenAI client builders both call this before creating their network clients. In the bigger flow, it acts as the shared gatekeeper that makes sure every Bedrock request is aimed at a real AWS region.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 56–68)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function creates the client used for Anthropic models served through Bedrock Mantle. It connects UFO’s common Anthropic wrapper to Anthropic’s Bedrock-specific asynchronous client.

**Data flow**: It receives a model specification and an API key. It asks bedrock_region for the AWS region, builds an Anthropic Bedrock Mantle client with the key, region, no automatic retries, and a 60-second timeout, then wraps that client together with the model specification in an AnthropicClient. The result is a ready-to-use UFO client for that model.

**Call relations**: Model specifications created for Anthropic IDs point to this function as their client factory. When UFO later needs to call one of those models, this function is the bridge from the saved model metadata to the actual Anthropic Bedrock connection.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 71–78)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function creates the client used for OpenAI-compatible models served through Bedrock Mantle. It chooses the correct Bedrock Mantle URL depending on whether the model uses OpenAI’s chat-style API or responses-style API.

**Data flow**: It receives a model specification and an API key. It reads the AWS region through bedrock_region, builds a base URL for Bedrock Mantle, creates an OpenAI SDK-compatible client for that URL and key, then wraps it in UFO’s OpenAIClient with the model specification. The output is a ready client that UFO can use through its normal OpenAI path.

**Call relations**: OpenAI-compatible model specifications point to this function as their client factory. When UFO sends a request to one of those models, this function supplies the correctly targeted OpenAI-style client instead of requiring custom Bedrock request code elsewhere.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 81–100)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS) -> ModelSpec
```

**Purpose**: This helper builds a complete ModelSpec for an Anthropic model on Bedrock. A ModelSpec is the project’s model record: it says what the model is called, how to authenticate, what it costs, how much text it can accept, and which client should be used.

**Data flow**: It takes a model ID, pricing, a knowledge cutoff date, and optional context-window and reasoning settings. It combines those with fixed Bedrock details, such as the provider name, Bedrock credential slot, Bedrock API key environment variable, chat API surface, and the _anthropic_client factory. It returns a ModelSpec ready to be included in the provider’s model list.

**Call relations**: The Bedrock model catalog uses this helper to define Anthropic-hosted entries consistently. It hands off client creation to _anthropic_client by storing that function inside each returned ModelSpec.

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 103–118)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: This helper builds a complete ModelSpec for an OpenAI-compatible model on Bedrock. It keeps all OpenAI-style Bedrock models consistent, including their endpoint style and retention behavior.

**Data flow**: It takes a model ID, pricing, a knowledge cutoff date, a context-window size, and the API surface to use. It combines those with fixed Bedrock details, such as the provider name, credential slot, API key environment variable, reasoning support, and the _openai_client factory. It returns a ModelSpec, with retention_none set to false because Bedrock Mantle does not allow the gateway’s no-retention mode for these IDs.

**Call relations**: The Bedrock model catalog uses this helper to define OpenAI-compatible entries. It stores _openai_client as the factory that will later create the actual network client for each model.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 190–201)

```
def manifest() -> Manifest
```

**Purpose**: This function publishes the Bedrock extension to UFO’s plugin system. It returns the provider name, version, required credential, and full list of available Bedrock model specifications.

**Data flow**: It uses the file’s constants and model list to create a CredentialSlot describing the Bedrock API key, then creates and returns a Manifest containing the extension name, version, credential requirement, and models. It does not make network calls; it only describes what this extension offers.

**Call relations**: UFO calls this during extension discovery so it can learn that the Bedrock provider exists. The manifest hands the rest of the system the credential requirement and BEDROCK_MODEL_SPECS, which in turn contain the client factories used later when requests are made.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/openrouter/ufo_ext_openrouter.py`

`io_transport` · `model requests and tool execution`

OpenRouter is a service that sits in front of many AI model providers. This file teaches the project how to talk to it. For chat models, it makes OpenRouter look like any other model client in the system: a request goes in, streamed text or tool-call updates come back, and final token usage is recorded. It also knows OpenRouter-specific details, such as turning a friendly model id into an OpenRouter provider/model name, sending reasoning settings, and retrying when an upstream provider returns an empty answer.

The file also defines two agent tools: generate_image and generate_video. These are separate from chat models because their cost is not measured in text tokens. Images are billed per image, and videos are billed per output second. The tools validate requests before sending them, so the agent cannot ask a model for a size, duration, or aspect ratio that OpenRouter’s provider is known not to support. After a successful result, the file saves the generated media into the workspace and records the cost when the platform key was used.

Finally, manifest() announces this extension to the host system: which models exist, which tools are available, and which credential slot holds the OpenRouter API key.

#### Function details

##### `openrouter_slug`  (lines 253–263)

```
def openrouter_slug(model: str) -> str
```

**Purpose**: Turns the system’s model name into the form OpenRouter expects. It adds a provider prefix for known OpenAI and Anthropic model names, while leaving already-prefixed names alone.

**Data flow**: It receives a model id as text. If the id already contains a slash, it returns it unchanged; if it looks like an OpenAI or Claude model, it adds the right provider prefix; otherwise it passes the id through. The output is the model slug sent to OpenRouter.

**Call relations**: When OpenRouterModelClient._create_kwargs prepares a chat request, it calls this helper so the request names the model in OpenRouter’s language before the request is sent.

*Call graph*: called by 1 (_create_kwargs).


##### `_chunk_provider`  (lines 266–271)

```
def _chunk_provider(chunk: ChatCompletionChunk) -> str | None
```

**Purpose**: Reads which upstream provider OpenRouter used for a streamed response chunk. This matters because the client can avoid that provider if it produces an empty completion.

**Data flow**: It receives one streaming chunk from OpenRouter. It looks inside the chunk’s extra metadata for a provider field and returns it as text if present, or nothing if it is missing.

**Call relations**: OpenRouterModelClient.complete calls this while reading the stream. If the answer ends up empty, the provider name it found can be added to an ignore list for a retry.

*Call graph*: called by 1 (complete).


##### `_usage_of`  (lines 274–295)

```
def _usage_of(usage: CompletionUsage, cache_write_30m_rate: int) -> Usage
```

**Purpose**: Converts OpenRouter/OpenAI-style token usage into the project’s own Usage record. It separates normal input tokens, cached input tokens, cache-write tokens, and output tokens so billing can be accurate.

**Data flow**: It receives usage numbers from the provider and the model’s cache-write price setting. It checks that cached and cache-written tokens do not exceed the prompt total, then builds a Usage object with the adjusted token counts. If the provider reports impossible or badly typed cache data, it raises an error instead of recording bad accounting.

**Call relations**: OpenRouterModelClient.complete calls this when the stream includes final usage data. The resulting Usage event is yielded back to the rest of the model loop.

*Call graph*: called by 1 (complete); 1 external calls (__init__).


##### `OpenRouterModelClient.complete`  (lines 314–418)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs one streaming chat completion through OpenRouter. It yields events as the model speaks, reports tool calls as they arrive, records usage, retries temporary provider failures, and flags truncated answers.

**Data flow**: It receives a ModelRequest containing the model name, messages, tools, token limit, and reasoning preference. It builds request arguments, sends them to OpenRouter, then turns streamed chunks into system events such as stream start, text deltas, tool-call starts, tool-call argument deltas, and final usage. It may sleep and retry on rate limits or server errors before any visible output has appeared; it raises an error if the provider cuts the answer off at the token limit.

**Call relations**: This is the main chat path for the OpenRouter model backend. It calls _create_kwargs to prepare the request, _chunk_provider to learn which upstream answered, and _usage_of to translate final token accounting. It hands ModelStreamStart, TextDelta, ToolCallStart, ToolCallDelta, and Usage events to the caller consuming the model stream.

*Call graph*: calls 3 internal fn (_create_kwargs, _chunk_provider, _usage_of); 8 external calls (__init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenRouterModelClient._create_kwargs`  (lines 420–451)

```
def _create_kwargs(self, request: ModelRequest, ignore_providers: frozenset[str]) -> dict[str, Any]
```

**Purpose**: Builds the exact set of arguments sent to OpenRouter’s chat-completions endpoint. It adapts the project’s model request into the OpenAI-compatible request shape OpenRouter accepts.

**Data flow**: It receives a ModelRequest and a set of providers to avoid. It translates messages, maps the model id to an OpenRouter slug, adds streaming and usage options, includes reasoning settings when needed, includes provider ignores for reroutes, and serializes available tools into OpenAI-style function tools. The output is a dictionary passed to the OpenAI SDK client.

**Call relations**: OpenRouterModelClient.complete calls this at the start of each attempt. It uses openrouter_slug for model naming and openai_messages for message translation before the network request is made.

*Call graph*: calls 1 internal fn (openrouter_slug); called by 1 (complete); 1 external calls (openai_messages).


##### `_model_client`  (lines 454–458)

```
def _model_client(spec: ModelSpec, key: str) -> OpenRouterModelClient
```

**Purpose**: Creates an OpenRouterModelClient for a registered model spec and API key. It is the factory the model registry uses when it needs a working client.

**Data flow**: It receives a model specification and an API key. It creates an OpenAI-compatible SDK client pointed at OpenRouter’s base URL, wraps it with the model spec, and returns an OpenRouterModelClient.

**Call relations**: _openrouter stores this function in each ModelSpec as the way to construct the client later. It uses the shared openai_sdk_client helper so OpenRouter can reuse the standard OpenAI wire format.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_openrouter`  (lines 461–479)

```
def _openrouter(id: str, price: ModelPrice, cutoff: str, context_window: int=OPENROUTER_CONTEXT_WINDOW, reasoning: ReasoningSupport=_REASONS) -> ModelSpec
```

**Purpose**: Defines one OpenRouter chat model for the registry. It packages the model id, price, context size, reasoning support, and credential details into a ModelSpec.

**Data flow**: It receives the model id, price, knowledge cutoff, and optional limits. It fills in OpenRouter-specific defaults such as provider name, API key slot, API key environment variable, and chat API surface. The output is a ModelSpec that the system can register.

**Call relations**: The file uses this helper to build OPENROUTER_MODEL_SPECS. Later, manifest() exposes those specs so the host system can offer these OpenRouter models like any other model.

*Call graph*: 1 external calls (__init__).


##### `GenerateImageInput._within_model_limits`  (lines 551–575)

```
def _within_model_limits(self) -> 'GenerateImageInput'
```

**Purpose**: Checks that an image-generation request fits the selected image model’s real limits. It catches unsupported combinations early, before OpenRouter returns a provider error.

**Data flow**: It reads the chosen image model, number of images, aspect ratio, and resolution from the input object. It compares them with the allowlisted limits for that model, raises clear validation errors for unsupported choices, and fills in a default resolution when the model requires a resolution tier but none was supplied. The validated input object comes out ready to send.

**Call relations**: This validator runs as part of building GenerateImageInput, before _generate_image and OpenRouterImages.generate use the request. It protects the later API call from known-bad parameters.


##### `_reported_cost_micro_usd`  (lines 583–595)

```
def _reported_cost_micro_usd(usage: object) -> int | None
```

**Purpose**: Extracts a media-generation cost reported by OpenRouter and converts it into micro-dollars, meaning millionths of a US dollar. It also understands the alternate cost field used when the workspace brings its own upstream provider key.

**Data flow**: It receives a usage object, usually from an image response or video job. It looks for a positive cost, first in usage.cost and then in cost_details.upstream_inference_cost, converts dollars to micro-USD, and returns that integer. If no positive cost is reported, it returns nothing so the caller can use a fallback list price.

**Call relations**: OpenRouterImages._charge calls it for image responses, and OpenRouterVideos._job calls it for video job usage. Both paths use its result to avoid guessing cost when OpenRouter already reported one.

*Call graph*: called by 2 (_charge, _job).


##### `OpenRouterImages.generate`  (lines 628–665)

```
async def generate(self, ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Runs a complete image-generation tool call. It sends the request to OpenRouter, saves returned images into the workspace, meters the cost when appropriate, and returns both file paths and image data to the agent.

**Data flow**: It receives the tool context and validated image arguments. It obtains the OpenRouter key, checks whether the workspace supplied its own key, posts the generation request, and either returns a clear tool error or decodes the response images. Successful images are written to the sandbox, cost is calculated, platform-key usage is metered, and a ToolResult comes out containing JSON file details plus image content.

**Call relations**: _generate_image creates an OpenRouterImages instance and calls this method. Inside the method, _refusal explains failed HTTP responses, _images decodes successful image data, _save writes files, and _charge determines the cost before ToolResult is returned.

*Call graph*: calls 5 internal fn (meter_images, _charge, _images, _refusal, _save); 6 external calls (__init__, __init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterImages._refusal`  (lines 667–682)

```
def _refusal(self, args: GenerateImageInput, response: httpx.Response) -> str
```

**Purpose**: Builds a readable error message when OpenRouter refuses or fails an image request. It preserves the provider’s reason so the agent can adjust the prompt or parameters.

**Data flow**: It receives the original image arguments and the HTTP response. If the response is JSON, it tries to read error.message; otherwise it uses the raw response text. It trims the detail to a safe length and returns one plain text error string.

**Call relations**: OpenRouterImages.generate calls this when the image POST returns an error status. The returned text becomes the content of an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterImages._images`  (lines 684–714)

```
def _images(self, args: GenerateImageInput, body: object) -> tuple[GeneratedImage, ...]
```

**Purpose**: Pulls usable images out of OpenRouter’s response. It decodes base64 image strings, assigns a media type, and refuses oversized or missing image data.

**Data flow**: It receives the validated image arguments and the parsed response body. It looks through body.data entries for base64 image data, decodes each one into bytes, checks the byte size, and wraps each valid image as a GeneratedImage. If no images are found, or an image is too large, it raises an OpenRouterImageError.

**Call relations**: OpenRouterImages.generate calls this after a successful HTTP response and before saving anything. Its all-or-nothing check prevents a half-written generation where only some returned images reach disk.

*Call graph*: called by 1 (generate); 3 external calls (__init__, __init__, b64decode).


##### `OpenRouterImages._save`  (lines 716–723)

```
async def _save(self, ctx: ToolContext, args: GenerateImageInput, index: int, image: GeneratedImage) -> str
```

**Purpose**: Writes one generated image file into the workspace. It chooses a file extension based on the image’s media type.

**Data flow**: It receives the tool context, original image arguments, the image’s index, and the decoded image. It builds a path like generated-images/name-1.png, writes the raw bytes through the sandbox, and returns the saved path.

**Call relations**: OpenRouterImages.generate calls this once for each decoded image. The paths it returns are included in the final ToolResult so the agent can refer to or share the files.

*Call graph*: called by 1 (generate).


##### `OpenRouterImages._charge`  (lines 725–732)

```
def _charge(self, body: object, args: GenerateImageInput, images: int) -> int
```

**Purpose**: Calculates what an image generation should cost in micro-USD. It uses OpenRouter’s reported cost when available and falls back to the model’s listed per-image rate otherwise.

**Data flow**: It receives the response body, the image arguments, and the number of images produced. It reads usage from the response, asks _reported_cost_micro_usd for any provider-reported charge, and returns that if present. If no reported cost exists, it multiplies the model’s fallback list price by the image count.

**Call relations**: OpenRouterImages.generate calls this after saving the images. Its result is used both in the returned JSON and, when the platform key was used, in ctx.meter_images.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 1 (generate).


##### `_generate_image`  (lines 735–740)

```
async def _generate_image(ctx: ToolContext, args: GenerateImageInput) -> ToolResult
```

**Purpose**: Acts as the registered handler for the generate_image tool. It connects the generic tool system to the OpenRouterImages implementation.

**Data flow**: It receives the tool context and validated GenerateImageInput. It requires the extension context to be present, then builds an OpenRouterImages object using extension credentials and the configured transport. It returns whatever ToolResult the image generator produces.

**Call relations**: GENERATE_IMAGE_TOOL points to this function as its handler. When an agent calls generate_image, the tool system invokes this wrapper, which then hands the work to OpenRouterImages.generate.

*Call graph*: 1 external calls (__init__).


##### `GenerateVideoInput._within_model_limits`  (lines 797–821)

```
def _within_model_limits(self) -> 'GenerateVideoInput'
```

**Purpose**: Checks that a video-generation request fits the selected video model’s allowed duration, aspect ratio, and resolution. It also fills in the default resolution for the chosen model.

**Data flow**: It reads the selected model, duration, aspect ratio, and resolution from the input object. It compares them with that model’s limits, raises clear validation errors for unsupported requests, and sets the model’s default resolution when none was given. The output is a validated input object ready for the API call and cost calculation.

**Call relations**: This validator runs while creating GenerateVideoInput, before _generate_video and OpenRouterVideos.generate use it. It keeps known-invalid video requests from reaching OpenRouter.


##### `OpenRouterVideos.generate`  (lines 863–904)

```
async def generate(self, ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Runs a complete video-generation tool call. It starts a video job, waits for it to finish, downloads the MP4, saves it into the workspace, meters cost when appropriate, and returns the saved file path.

**Data flow**: It receives the tool context and validated video arguments. It gets the OpenRouter key, checks whether the workspace supplied its own key, posts a video request, and handles immediate refusal if the POST fails. If accepted, it reads the job, polls until the job settles, returns a tool error for failed jobs, downloads the completed video, writes it to the sandbox, calculates cost, meters platform-key usage, and returns a ToolResult with JSON file details.

**Call relations**: _generate_video creates an OpenRouterVideos instance and calls this method. The method coordinates _refusal, _job, _settled, _failure, _download, _save, and _charge to turn OpenRouter’s asynchronous video API into one tool call.

*Call graph*: calls 8 internal fn (meter_videos, _charge, _download, _failure, _job, _refusal, _save, _settled); 5 external calls (__init__, __init__, model_dump, AsyncClient, dumps).


##### `OpenRouterVideos._refusal`  (lines 906–921)

```
def _refusal(self, args: GenerateVideoInput, response: httpx.Response) -> str
```

**Purpose**: Builds a readable error message when OpenRouter does not start a video job. It keeps the provider’s reason visible to the agent.

**Data flow**: It receives the original video arguments and the HTTP response. It tries to read a JSON error message, falls back to the response text, trims the detail to a safe length, and returns one plain text message.

**Call relations**: OpenRouterVideos.generate calls this when the initial video POST returns an error status. The text becomes the content of an error ToolResult.

*Call graph*: called by 1 (generate); 1 external calls (json).


##### `OpenRouterVideos._job`  (lines 923–938)

```
def _job(self, body: object) -> VideoJob
```

**Purpose**: Turns an OpenRouter video response or poll result into a VideoJob record. It refuses responses that do not name a job id and status, because such a job cannot be safely polled.

**Data flow**: It receives a parsed response body. It reads the job id, status, optional error message, and optional usage cost. It converts reported usage cost through _reported_cost_micro_usd and returns a VideoJob. If the id or status is missing or not text, it raises OpenRouterVideoError.

**Call relations**: OpenRouterVideos.generate calls this after the initial accepted response, and OpenRouterVideos._settled calls it after each poll. The VideoJob it returns is the shared description of where the external video job currently stands.

*Call graph*: calls 1 internal fn (_reported_cost_micro_usd); called by 2 (_settled, generate); 2 external calls (__init__, __init__).


##### `OpenRouterVideos._settled`  (lines 940–961)

```
async def _settled(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> VideoJob
```

**Purpose**: Waits for an OpenRouter video job to stop being pending or in progress. It prevents the tool call from hanging forever by enforcing a timeout.

**Data flow**: It receives the video arguments, an HTTP client, and the current VideoJob. While the status says the job is still running, it checks the deadline, sleeps for the poll interval, fetches the latest job state, and converts it with _job. It returns the first job state that is no longer pending or in progress, or raises OpenRouterVideoError on timeout or failed polling.

**Call relations**: OpenRouterVideos.generate calls this after starting a video job. It repeatedly hands poll responses to _job, then returns the settled job so generate can decide whether to download the video or report failure.

*Call graph*: calls 1 internal fn (_job); called by 1 (generate); 4 external calls (__init__, sleep, get, monotonic).


##### `OpenRouterVideos._failure`  (lines 963–967)

```
def _failure(self, args: GenerateVideoInput, job: VideoJob) -> str
```

**Purpose**: Explains why an accepted video job did not produce a completed video. It uses the provider’s error when available.

**Data flow**: It receives the original video arguments and the settled VideoJob. It chooses the job’s error text if present, otherwise describes the final status, trims the detail to a safe length, and returns one error message.

**Call relations**: OpenRouterVideos.generate calls this when _settled returns a job whose status is not completed. The message is returned to the agent in an error ToolResult.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._download`  (lines 969–988)

```
async def _download(self, args: GenerateVideoInput, http: httpx.AsyncClient, job: VideoJob) -> bytes
```

**Purpose**: Downloads the finished MP4 for a completed video job and checks that it is usable. It refuses empty or oversized downloads before they can be saved.

**Data flow**: It receives the video arguments, an HTTP client, and the completed VideoJob. It requests the job’s content from OpenRouter, raises OpenRouterVideoError on download errors, empty content, or content over the byte limit, and returns the raw video bytes on success.

**Call relations**: OpenRouterVideos.generate calls this only after a job reaches the completed status. The returned bytes are then passed to _save for workspace storage.

*Call graph*: called by 1 (generate); 2 external calls (__init__, get).


##### `OpenRouterVideos._save`  (lines 990–994)

```
async def _save(self, ctx: ToolContext, args: GenerateVideoInput, video: bytes) -> str
```

**Purpose**: Writes a downloaded video into the workspace as an MP4 file. It gives the file the user-requested name stem.

**Data flow**: It receives the tool context, video arguments, and raw MP4 bytes. It builds a path under generated-videos, writes the bytes through the sandbox, and returns the saved path.

**Call relations**: OpenRouterVideos.generate calls this after _download succeeds. The returned path is included in the final ToolResult so the agent can refer to or share the video.

*Call graph*: called by 1 (generate).


##### `OpenRouterVideos._charge`  (lines 996–1004)

```
def _charge(self, args: GenerateVideoInput, job: VideoJob) -> int
```

**Purpose**: Calculates what a video generation should cost in micro-USD. It trusts OpenRouter’s reported job cost when present and otherwise falls back to the model’s per-second price for the chosen resolution.

**Data flow**: It receives the video arguments and the final VideoJob. If the job already carries a reported cost, it returns that. Otherwise it looks up the selected model’s price for the resolved resolution and multiplies by the requested duration.

**Call relations**: OpenRouterVideos.generate calls this after saving the video. Its result is written into the returned JSON and, when the platform key was used, passed to ctx.meter_videos.

*Call graph*: called by 1 (generate).


##### `_generate_video`  (lines 1007–1012)

```
async def _generate_video(ctx: ToolContext, args: GenerateVideoInput) -> ToolResult
```

**Purpose**: Acts as the registered handler for the generate_video tool. It connects the tool system’s call to the OpenRouterVideos implementation.

**Data flow**: It receives the tool context and validated GenerateVideoInput. It requires the extension context, creates an OpenRouterVideos object using extension credentials and the configured transport, and returns the ToolResult from video generation.

**Call relations**: GENERATE_VIDEO_TOOL points to this function as its handler. When an agent calls generate_video, this wrapper is invoked and hands the work to OpenRouterVideos.generate.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1024–1040)

```
def manifest() -> Manifest
```

**Purpose**: Declares what this extension offers to the host system. It lists the OpenRouter chat models, the image and video tools, and the credential slot for the OpenRouter API key.

**Data flow**: It takes no input. It builds a Manifest containing the extension name and version, the model specs prepared earlier, the two tool definitions, and a CredentialSlot describing where the OpenRouter API key comes from. The returned Manifest is how the extension is registered.

**Call relations**: The host calls manifest() when loading the extension. The Manifest it returns is the bridge that makes the file’s model client factory, tools, and credential needs visible to the rest of the system.

*Call graph*: 2 external calls (__init__, __init__).


### Chat provider bridges
Anthropic-style and OpenAI-style clients translate UFO model requests and streamed responses to and from provider APIs.

### `core/src/ufo/models/anthropic.py`

`io_transport` · `request handling during model calls`

This file is the Anthropic adapter for the model layer. The rest of the project wants to send a generic model request and receive generic events such as text chunks, tool-call chunks, hidden reasoning blocks, and final token usage. Anthropic has its own request format and its own streaming event shapes, so this file acts like a translator at the border.

Before sending a request, it converts the project’s messages, images, tool calls, tool results, and reasoning blocks into Anthropic’s expected content blocks. It also adds cache settings, tool definitions, and reasoning options based on the selected model’s specification.

During a streamed response, `AnthropicClient.complete` listens to Anthropic’s events and yields the project’s events in order. Text is passed through as it arrives. Tool calls are split into a start event and later JSON fragments. Anthropic “thinking” blocks are collected quietly and only yielded after the visible answer finishes, because they must be echoed back exactly later but are not user-facing live output.

The file is also careful about failures. Timeouts, dropped connections, rate limits, and temporary provider errors are retried only before any visible output has been sent. Once text or a tool call has reached the caller, retrying could duplicate or confuse the answer, so errors are raised immediately. Final usage information is always treated as important bookkeeping.

#### Function details

##### `anthropic_sdk_client`  (lines 50–54)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the low-level Anthropic software client used to make API calls. It deliberately turns off the Anthropic SDK’s built-in retries so this project can apply one consistent retry policy itself.

**Data flow**: It receives an API key → builds an asynchronous Anthropic client with that key, a fixed timeout, and no SDK retries → returns that ready-to-use client object.

**Call relations**: This is the setup helper for Anthropic access. Later, an `AnthropicClient` uses the returned SDK client to open streaming message requests, while the retry decisions stay inside `AnthropicClient.complete` rather than being hidden inside the external SDK.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 57–61)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Turns the project’s image representation into the image shape Anthropic expects. This is needed whenever an image is sent either as part of a user message or as part of a tool result.

**Data flow**: It receives an `ImageSource`, which contains a media type and base64 image data → wraps those fields in Anthropic’s required nested dictionary format → returns that dictionary for inclusion in an API request.

**Call relations**: This helper is called by both `_anthropic_tool_result_part` and `anthropic_content`. It keeps image formatting in one place so every Anthropic request uses the same image wire format.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 64–69)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic’s format. A tool result can contain plain text or an image, and Anthropic needs those described differently.

**Data flow**: It receives one tool-result content block → if it is text, it returns an Anthropic text dictionary; if it is an image, it passes the image source to `_anthropic_image` → the returned dictionary becomes one part of a tool-result message.

**Call relations**: This function is used inside `anthropic_content` when a tool result is made of multiple content parts. It delegates image formatting to `_anthropic_image` so tool-result images match normal message images.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 72–106)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Converts the project’s general message content into the content format Anthropic’s Messages API accepts. It covers plain strings, text blocks, images, tool uses, tool results, and Anthropic reasoning blocks.

**Data flow**: It receives either a simple string or a tuple of structured content blocks → if it is already a string, it returns it unchanged; otherwise it walks through each block and builds the matching Anthropic dictionary list → unsupported cross-provider reasoning items are skipped because Anthropic cannot safely reuse another provider’s private reasoning format.

**Call relations**: This is the main request-body translator used by `AnthropicClient.complete` when building the outgoing message list. It calls `_anthropic_image` for images and `_anthropic_tool_result_part` for multi-part tool results, then hands the converted content to the Anthropic SDK request.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 114–437)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to Anthropic and yields the project’s standard stream of model events back to the caller. It is responsible for streaming text, streaming tool calls, preserving reasoning blocks, reporting usage, and deciding when provider failures should be retried.

**Data flow**: It receives a `ModelRequest` containing the model name, system prompt, messages, tools, token limit, cache settings, and reasoning preference → builds Anthropic-specific request arguments, opens a streaming API call, and translates each incoming Anthropic event into project events such as `ModelStreamStart`, `TextDelta`, `ToolCallStart`, and `ToolCallDelta` → collects hidden reasoning blocks until the stream ends → finally yields those reasoning blocks followed by one `Usage` record, or raises a clear exception for truncation, refusal, unrecoverable provider errors, or a malformed stream.

**Call relations**: This is the core runtime path for Anthropic model calls. It calls `anthropic_content` while preparing messages, then calls the external Anthropic streaming API. As events arrive, it constructs the project’s event objects for downstream engine code. If Anthropic times out, disconnects, rate-limits, or returns a temporary error before visible output has been yielded, this function waits and tries again; after visible output starts, it stops retrying and lets the error surface so the caller does not receive a mixed answer from multiple attempts.

*Call graph*: calls 1 internal fn (anthropic_content); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep (+3 more)).


### `core/src/ufo/models/openai.py`

`io_transport` · `request handling`

UFO talks to language models through its own common request and event types, so the rest of the system does not need to know each provider's exact wire format. This file is the adapter for providers that speak the OpenAI API shape, including OpenAI itself and OpenAI-compatible services.

Its main job is translation in both directions. Before a request goes out, it converts UFO messages into the format expected by the chosen OpenAI API surface. The older Chat Completions surface has no place to carry model reasoning, so reasoning blocks are dropped there. The newer Responses surface can carry reasoning items, tool calls, tool outputs, text, and images more faithfully.

The `OpenAIClient` then opens a streaming request. As chunks arrive, it yields simple UFO events such as stream start, text deltas, tool-call starts, tool-call argument pieces, reasoning items, refusals, truncation errors, and final usage counts. It also contains careful retry behavior: network failures and temporary provider errors are retried only before visible output has been delivered, so callers do not accidentally see two partial answers. Without this file, UFO would not be able to safely call OpenAI-style models while preserving tools, images, reasoning state, cost accounting, and error behavior.

#### Function details

##### `_cache_write_tokens`  (lines 91–99)

```
def _cache_write_tokens(details: PromptTokensDetails | InputTokensDetails | None) -> int
```

**Purpose**: This helper reads an optional provider-specific token count for prompt tokens written into cache. It protects the rest of the code from missing data or wrongly typed data in the OpenAI usage details.

**Data flow**: It receives token-detail data from the OpenAI SDK. If the detail object or extra provider data is missing, it returns zero. If `cache_write_tokens` is present, it checks that it is a real integer and returns it; otherwise it raises an error because usage accounting would be unsafe.

**Call relations**: Usage conversion calls this whenever OpenAI reports token details. The chat streaming path uses it directly, and the Responses path uses it through `_responses_usage`, so both API surfaces calculate cache-write tokens the same way.

*Call graph*: called by 2 (_complete_chat, _responses_usage).


##### `_responses_usage`  (lines 102–116)

```
def _responses_usage(raw: ResponseUsage, cache_write_30m_priced: bool) -> Usage
```

**Purpose**: This converts OpenAI Responses API usage numbers into UFO's own `Usage` record. It separates normal input tokens, output tokens, cached-read tokens, and cache-write tokens so billing and reporting stay accurate.

**Data flow**: It receives raw Responses API usage plus a flag saying whether 30-minute cache writes should be priced. It reads cached-token counts and cache-write counts, checks that they do not exceed the total input tokens, then returns a `Usage` object with the totals split into UFO's categories.

**Call relations**: The Responses streaming code calls this when it receives completed, incomplete, or failed response usage data. It relies on `_cache_write_tokens` for the provider-specific cache-write field and hands the final normalized usage event back to the caller.

*Call graph*: calls 1 internal fn (_cache_write_tokens); called by 1 (_complete_responses); 1 external calls (__init__).


##### `openai_sdk_client`  (lines 119–125)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: This builds the actual asynchronous OpenAI SDK client used to talk over the network. It disables the SDK's built-in retries because this file has its own retry rules for streamed output.

**Data flow**: It receives an API key and, optionally, a custom base URL for an OpenAI-compatible provider. It creates and returns an `AsyncOpenAI` client with a fixed timeout and no SDK-level retries.

**Call relations**: This is the setup helper for code that wants an OpenAI-wire client. The returned SDK object is later stored inside `OpenAIClient`, whose streaming methods decide when and how to retry.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_openai_image`  (lines 128–132)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: This turns UFO's image data into the small image object expected by the Chat Completions message format. It packages the image as a data URL, meaning the image bytes travel inside the request text as base64 data.

**Data flow**: It receives an `ImageSource` containing a media type and base64 image data. It returns a dictionary shaped like OpenAI's `image_url` content part.

**Call relations**: Message conversion helpers call this whenever they need to place an image into an OpenAI chat-style message. `_openai_tool_result` uses it for images returned by tools, and `openai_messages` uses it for images in normal message content.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 135–151)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: This splits a tool result into text and images in a way the Chat Completions API can accept. This matters because OpenAI tool messages are text-only, so images from a tool result must be moved into a separate user-style image message.

**Data flow**: It receives either a plain string tool result or a tuple of text and image blocks. A string comes back as text with no images. A mixed result is separated into joined text and a list of OpenAI image parts.

**Call relations**: `openai_messages` calls this while converting UFO tool result blocks for the Chat Completions API. It delegates each image conversion to `_openai_image`, then gives `openai_messages` the pieces needed to emit a text tool message and, if needed, a follow-up image message.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 154–213)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: This converts UFO's conversation history into the message list required by OpenAI's Chat Completions API. It keeps text, images, tool calls, and tool results, while dropping reasoning blocks because that API surface has nowhere valid to send them.

**Data flow**: It receives the system instruction and a tuple of UFO messages. It first trims images through the shared image-trimming helper, then walks each message block by block. Text becomes chat content, images become OpenAI image parts, tool uses become OpenAI function calls, and tool results become tool messages, with any result images lifted into a later user message.

**Call relations**: `OpenAIClient._chat_kwargs` calls this when preparing a Chat Completions request. It uses `_openai_image` and `_openai_tool_result` for image and tool-result conversion, and uses JSON encoding for tool-call arguments.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 216–299)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: This converts UFO's conversation history into input items for OpenAI's newer Responses API. Unlike the chat format, this can preserve OpenAI reasoning items so the model can continue a tool-using reasoning chain later.

**Data flow**: It receives UFO messages, trims images, then walks through text, images, reasoning items, tool calls, and tool results. Text and images are grouped into message content; tool calls and tool outputs become their own response input items; reasoning items are replayed with their encrypted content and summaries. Anthropic-style thinking blocks are ignored because they are not the Responses API's format.

**Call relations**: `responses_request` calls this while building the full Responses API request. It constructs the OpenAI SDK's typed input objects and JSON-encodes tool-call arguments so `_complete_responses` can send a legal streamed request.

*Call graph*: called by 1 (responses_request); 11 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, Summary (+1 more)).


##### `responses_request`  (lines 302–339)

```
def responses_request(request: ModelRequest, effort: OpenAIEffort, retention_none: bool) -> dict[str, Any]
```

**Purpose**: This builds the full argument dictionary for a streamed OpenAI Responses API call. It combines the model name, instructions, converted conversation, reasoning setting, tool definitions, token limit, and retention options.

**Data flow**: It receives a `ModelRequest`, the already-decided reasoning effort, and a flag saying whether the provider accepts `store=False` retention. It converts messages with `responses_input`, adds streaming and token settings, optionally asks for encrypted reasoning content, and includes tools and tool-choice rules when tools are available. It returns the keyword arguments that can be passed directly to the OpenAI SDK.

**Call relations**: `OpenAIClient._complete_responses` calls this right before opening the provider stream. It relies on `responses_input` for the conversation body and creates OpenAI function-tool definitions for any UFO tools in the request.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `OpenAIClient.complete`  (lines 351–354)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the public streaming entry point for this client. It chooses the correct OpenAI API surface based on the model specification rather than guessing from the model name.

**Data flow**: It receives a UFO `ModelRequest`. It checks `self.spec.api_surface`; if the model says it uses the Responses API, it returns the Responses stream, otherwise it returns the Chat Completions stream. The output is an asynchronous stream of UFO `ModelEvent` objects.

**Call relations**: Higher-level model code calls this when it wants a completion from an OpenAI-wire model. This method dispatches to `_complete_responses` or `_complete_chat`, which do the actual request conversion, network streaming, retries, and event translation.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._reasoning_effort`  (lines 356–375)

```
def _reasoning_effort(self, request: ModelRequest) -> OpenAIEffort
```

**Purpose**: This decides what reasoning setting, if any, should be sent to OpenAI for one request. It is careful about the difference between omitting a reasoning setting and explicitly saying reasoning should be off.

**Data flow**: It reads the request's desired reasoning mode, the request's tools, and the model specification's reasoning rules. It returns an OpenAI reasoning effort value, returns `None` when no parameter should be sent, maps UFO's `off` mode to OpenAI's `none`, and raises an error if the caller asked to turn reasoning off in a situation where the provider will not accept that parameter.

**Call relations**: Both request-building paths use this decision. `_chat_kwargs` uses it for Chat Completions, and `_complete_responses` computes it before building the Responses request, so both surfaces obey the same model-specific reasoning rules.

*Call graph*: called by 2 (_chat_kwargs, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 377–406)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: This builds the argument dictionary for a streamed Chat Completions API call. It translates UFO's request into the older OpenAI chat shape, including messages, token limit, reasoning effort, and tools.

**Data flow**: It receives a `ModelRequest`. It converts the conversation with `openai_messages`, adds model name, max completion tokens, streaming options, and usage reporting. It asks `_reasoning_effort` whether to include a reasoning setting, then adds tool definitions and tool-choice rules if the request includes tools. It returns the SDK-ready keyword arguments.

**Call relations**: `OpenAIClient._complete_chat` calls this immediately before starting the chat stream. This helper keeps request construction separate from the streaming loop that reads events and handles failures.

*Call graph*: calls 2 internal fn (_reasoning_effort, openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 408–581)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This sends a request to the Chat Completions API and yields UFO events as the streamed answer arrives. It turns OpenAI chunks into text deltas, tool-call events, and final usage, while applying careful retry and error rules.

**Data flow**: It receives a `ModelRequest`, builds OpenAI chat arguments, and opens a streamed SDK call. As chunks arrive, it emits a stream-start event once, then emits text pieces and tool-call pieces. It records usage when OpenAI sends it, checks cache-token accounting, raises a truncation error if the model hit the token limit, retries temporary failures only before visible output, and finally yields one `Usage` event.

**Call relations**: `OpenAIClient.complete` uses this path when the model spec selects the Chat Completions surface. It depends on `_chat_kwargs` for request construction and `_cache_write_tokens` for usage accounting, and it produces the common event stream consumed by the rest of UFO.

*Call graph*: calls 2 internal fn (_chat_kwargs, _cache_write_tokens); called by 1 (complete); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


##### `OpenAIClient._complete_responses`  (lines 583–787)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This sends a request to the Responses API and yields UFO events as the streamed response arrives. It supports the richer Responses event types, including preserved reasoning items, refusals, incomplete responses, tool calls, text, and usage.

**Data flow**: It receives a `ModelRequest`, decides the reasoning effort, builds the Responses request, and opens a streamed SDK call. It translates text deltas into `TextDelta`, function-call starts and argument pieces into tool-call events, stores completed reasoning items until the stream is safely finished, converts usage into UFO's `Usage`, and turns provider refusals or truncation into clear errors. Temporary network and status failures are retried only before user-visible output has been yielded.

**Call relations**: `OpenAIClient.complete` uses this path when the model spec selects the Responses surface. It calls `_reasoning_effort`, `responses_request`, and `_responses_usage`, then hands the rest of the system the same kind of `ModelEvent` stream as the chat path, with reasoning blocks yielded near the end so future tool rounds can replay them.

*Call graph*: calls 3 internal fn (_reasoning_effort, _responses_usage, responses_request); called by 1 (complete); 10 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep, emit_metric, log).


### Model registry and specification
The registry and shared model specification provide one reliable source for model facts, routing behavior, pricing, and client lookup.

### `core/src/ufo/models/registry.py`

`domain_logic` · `startup and model request handling`

This file solves a coordination problem: many parts of the system need to know which AI models exist, how to call them, what they cost, and which secret key they need. Without one shared registry, a typo or missing model could fail later in several different ways: a provider error, a billing mistake, or a broken render. The registry makes those problems fail early and clearly.

The main piece is `ModelRegistry`, a frozen data object that stores model specifications by exact model id. A model specification is the “fact sheet” for a model: who provides it, how to create a client for it, what key it needs, and what it costs. The registry also knows which real model should be used when code asks for the special `auto` model.

The file also defines `model_registry`, the builder function. It starts with the built-in model list, adds any model definitions contributed by extension manifests, rejects duplicate ids, checks that configured default models really exist, and builds a merged pricing table.

One important behavior is that API keys are resolved only when a model client is actually requested. That means the server can start even if a key for an unused provider is missing, but the first attempt to use that provider will fail with a clear message. It is like keeping a catalog of tools, but only checking whether the right battery is installed when someone actually picks up a tool.

#### Function details

##### `ModelRegistry.resolve`  (lines 31–34)

```
def resolve(self, model: str) -> str
```

**Purpose**: This turns the special model name `auto` into the concrete default model configured for this deployment. If the caller already named a specific model, it leaves that name unchanged.

**Data flow**: It receives a model name. If that name is the shared `auto` placeholder, it returns the registry’s configured `auto_model`; otherwise it returns the original name. It does not change the registry.

**Call relations**: Other methods use this when they need the real model behind `auto`. `key_slot_for` uses it before asking which workspace key slot applies, and `model_key_env` uses it before deciding which environment variable should be checked.

*Call graph*: called by 2 (key_slot_for, model_key_env).


##### `ModelRegistry.spec`  (lines 36–42)

```
def spec(self, model: str) -> ModelSpec
```

**Purpose**: This looks up the stored fact sheet for a model id. It deliberately fails with a clear error if the id is unknown, so mistakes are caught at the registry instead of much later in a provider call or billing step.

**Data flow**: It receives a model id and reads the registry’s `specs` table. If the id is present, it returns the matching `ModelSpec`; if not, it raises a `ValueError` explaining that no model is registered for that id.

**Call relations**: This is the main doorway for reading model facts. `client_for` uses it before building a provider client, `provider_for` uses it to report the backend provider, and `model_key_env` uses it after resolving `auto` to inspect the configured provider.

*Call graph*: called by 3 (client_for, model_key_env, provider_for).


##### `ModelRegistry.client_for`  (lines 44–69)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: This creates the actual client object used to talk to the provider for a chosen model. It also finds the right API key, either from the current workspace’s bring-your-own-key setting or from the platform environment.

**Data flow**: It receives a model id, looks up that model’s specification, and checks whether the model needs a key. If no key is required, it builds the client with an empty key. If a key is required, it asks the current workspace for the credential, turns missing credentials into a clear runtime error, rejects keys containing non-ASCII characters because provider wire protocols cannot carry them safely, and finally returns a ready-to-use model client.

**Call relations**: When some part of the system is ready to make a model call, it comes here to get the provider-specific client. This method first relies on `ModelRegistry.spec` for the model’s facts, then asks `ws_current` for the active workspace so workspace-specific credentials can be honored at call time.

*Call graph*: calls 1 internal fn (spec); 2 external calls (__init__, ws_current).


##### `ModelRegistry.provider_for`  (lines 71–75)

```
def provider_for(self, model: str) -> str
```

**Purpose**: This tells the caller which provider serves a model, such as OpenAI or Anthropic. That is useful for things like metering, reporting, and separating usage by backend.

**Data flow**: It receives a model id, looks up the model specification through the registry, and returns the provider name stored in that specification. If the model id is unknown, the lookup fails clearly.

**Call relations**: This is a small read path built on top of `ModelRegistry.spec`. Code that needs provider identity without creating a client can call this and get the same authoritative answer the rest of the registry uses.

*Call graph*: calls 1 internal fn (spec).


##### `ModelRegistry.key_slot_for`  (lines 77–88)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: This answers which workspace BYOK slot, if any, would supply the key for a model. BYOK means “bring your own key”: a workspace can store its own provider secret instead of using the platform’s default key.

**Data flow**: It receives a model name, first resolving `auto` to the real configured model. It then checks the registry table directly. If the model no longer exists or does not use a workspace key slot, it returns `None`; otherwise it returns the slot name.

**Call relations**: This method is designed to be forgiving because it may be used for reporting or billing records that mention old models. It calls `ModelRegistry.resolve` but intentionally does not use the louder `spec` lookup, so historical or keyless entries can be labeled without crashing the export.

*Call graph*: calls 1 internal fn (resolve).


##### `ModelRegistry.model_key_env`  (lines 90–100)

```
def model_key_env(self, model: str, config: Config) -> str | None
```

**Purpose**: This tells onboarding code which environment variable should be set before a model is used, but only for the built-in providers whose key names the core system knows. For extension-provided models, it returns `None` because those may resolve keys in their own way later.

**Data flow**: It receives a model name and the global configuration. It resolves `auto`, looks up the model’s provider, and compares it with the known Anthropic and OpenAI provider names. For those, it returns the configured environment variable name; for any other provider, it returns `None`.

**Call relations**: Startup or onboarding checks can call this before the first turn to warn about missing keys. It uses `ModelRegistry.resolve` so `auto` points at the model that will actually run, then uses `ModelRegistry.spec` to read the provider from the registered model facts.

*Call graph*: calls 2 internal fn (resolve, spec).


##### `model_registry`  (lines 103–136)

```
def model_registry(config: Config, manifests: tuple[Manifest, ...]) -> ModelRegistry
```

**Purpose**: This builds the active `ModelRegistry` for a running deployment. It combines built-in models with models supplied by extension manifests, rejects conflicting ids, checks important configured model choices, and prepares pricing data.

**Data flow**: It receives the global configuration and a set of manifests. It asks `core_model_specs` for the built-in model specifications, walks through those plus all manifest-provided specifications, and stores them in a dictionary keyed by model id. If two specs claim the same id, it raises an error. It then verifies that the configured automatic model, ambient reply model, and background jobs model all exist. Finally, it builds a pricing table from the model prices and returns a new `ModelRegistry`.

**Call relations**: This is the setup step that creates the registry other code will depend on later. During startup it gathers model definitions from the core catalog and extension manifests, hands the collected price information to `pricing_from`, and returns the registry object used by later lookup, key, provider, and client-building paths.

*Call graph*: 3 external calls (__init__, core_model_specs, pricing_from).


### `core/src/ufo/models/spec.py`

`data_model` · `model registry setup and per-request model preparation`

This file is like a catalog card for every model. Instead of scattering facts such as “which API should call this model,” “how much does it cost,” “does it support reasoning,” or “where is its key stored” across the codebase, the project stores them in a single frozen object called ModelSpec. “Frozen” means the record cannot be changed after it is created, which helps keep model behavior predictable.

The file also defines ReasoningSupport, a smaller record that says whether a model can do extended reasoning, whether that reasoning can be used together with tools, whether it is on by default, and whether it can be turned off. This matters because different model providers interpret missing or explicit reasoning settings differently. The code is careful not to accidentally request reasoning where it is unsupported, or accidentally omit an “off” setting when the provider would treat omission as “use your default.”

ModelSpec validates important facts early, such as requiring the knowledge cutoff date to look like YYYY-MM. That means a bad model entry fails when the registry is used, not later in the middle of a user turn. It also turns provider key rejection into a clear credential error that tells the user which model key needs replacing.

#### Function details

##### `ReasoningSupport.internal_effort`  (lines 36–39)

```
def internal_effort(self) -> ReasoningEffort
```

**Purpose**: This returns the safest internal reasoning setting for a model before a user request is turned into an API call. It says “off” when reasoning is unsupported or can be disabled, and otherwise returns the model’s minimum required reasoning level.

**Data flow**: It reads the ReasoningSupport record’s flags: whether reasoning is supported, whether it can be disabled, and what the minimum effort is. If reasoning is not available, or if the model allows reasoning to be turned off, the result is "off". If the model always requires reasoning, the result is its configured minimum effort.

**Call relations**: This is a small helper on the ReasoningSupport record. Other model-routing or request-building code can ask it for a safe baseline reasoning value instead of repeating the same rule in several places.


##### `ModelSpec.__post_init__`  (lines 68–78)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a newly created model description is internally consistent. It catches mistakes in the model catalog early, before a bad entry causes confusing failures during an actual model call.

**Data flow**: It receives a completed ModelSpec after the dataclass has filled in its fields. It checks that the knowledge cutoff is written as a year and month, like "2024-06". It also checks that the model does not claim tool-compatible reasoning or default-on reasoning unless reasoning itself is supported. If any check fails, it raises a ValueError; otherwise the ModelSpec remains valid and ready to use.

**Call relations**: This runs automatically when a ModelSpec is constructed. It does not hand work to other project functions; its job is to guard the catalog entry at creation time so later registry lookups and request builders can trust the facts on the spec.


##### `ModelSpec.key_rejected`  (lines 80–90)

```
def key_rejected(self) -> CredentialValueInvalid
```

**Purpose**: This builds a clear, typed error for the case where a provider rejects the API key for this model. It turns a generic provider authentication failure into a message that tells the user what key source to fix.

**Data flow**: It reads the model id, provider name, environment-variable key name, and workspace BYOK slot name. From those facts it writes a human-readable explanation saying that the provider did not accept the key and that the user should replace it. It returns a CredentialValueInvalid error object containing that explanation.

**Call relations**: When provider communication gets a key-rejected response, this method supplies the project-specific credential error. It calls CredentialValueInvalid.__init__ to create that error, so higher-level code can report a stable credential problem instead of an unclear stream or provider failure.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.wire_reasoning`  (lines 92–106)

```
def wire_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort | None
```

**Purpose**: This decides what reasoning setting, if any, should be sent on an actual model API request. It protects the system from sending unsupported reasoning options, especially when tools are also being used.

**Data flow**: It takes the user-requested reasoning effort and the tools planned for the request. It first reads the model’s reasoning rules. If the model does not support reasoning, it returns None, meaning “send no reasoning setting.” If tools are present but this model cannot combine tools with reasoning, it also returns None. If the caller requested "off" but this model has reasoning on by default and cannot disable it, the function returns the model’s minimum effort instead. In all other supported cases, it returns the requested effort unchanged.

**Call relations**: Request-building code uses this method when preparing the final provider call. It is the gatekeeper between the project’s internal reasoning choice and the exact setting that is safe to put on the wire, meaning the outgoing API request.


### Embedding provider bridge
The OpenAI embedding extension converts text into vectors for indexing, retrieval, and memory workflows.

### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration and embedding request handling`

This extension is the bridge between the project and OpenAI's embeddings API. An embedding is like giving a sentence a location on a map of meaning: texts with similar meanings end up near each other. The rest of the system can use those vectors for search, matching, or retrieval.

The file does three main jobs. First, it prepares text safely for the outside service. OpenAI requests have practical size limits, so `plan_embed_batches` clips overly long text items and groups them into batches that stay under item-count and character-count ceilings. This is like packing boxes for shipping: each box has a maximum weight and maximum number of items.

Second, `OpenAIEmbedClient.embed` performs the actual network call. It reads the OpenAI API key from the environment only when embedding is requested, not when the app starts. That means development can boot without a key, but any real embedding attempt fails clearly if the key is missing.

Finally, `build` and `manifest` make this file discoverable as an extension. They tell the host system: this extension provides an embedding backend, and it needs the `OPENAI_API_KEY` deploy-time secret to do real work.

#### Function details

##### `plan_embed_batches`  (lines 32–48)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 61–73)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

*Call graph*: calls 1 internal fn (plan_embed_batches); 1 external calls (AsyncOpenAI).


##### `build`  (lines 76–81)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 84–90)

```
def manifest() -> Manifest
```

*Call graph*: 2 external calls (__init__, __init__).
