# Model selection, provider invocation, streaming, and accounting  `stage-9`

This stage is the system’s gateway to AI services during the main work loop. When the engine needs a model to answer, summarize, embed text, or process media, this stage decides which model record to use, finds the right provider and credentials, sends the request, and turns the provider’s streaming reply into UFO’s own standard event format.

The model catalogs and provider bridges act like a directory and a set of plug adapters. The catalogs describe available models, their limits, and prices. The registry makes them easy to look up. Provider clients for OpenAI, Anthropic, Bedrock, and OpenRouter translate UFO requests into each company’s expected format, then translate streamed responses, errors, retries, and token counts back into a common form. Embedding providers do similar work for turning text into number lists used for search and memory.

interface.py defines that common request-and-response language, including safeguards for image limits. pricing.py turns reported token usage into cost records and tags them with the exact price table used.

## Sub-stages

- [Model catalogs and provider bridges](stage-9.1.md) `stage-9.1` — 8 files

## Files in this stage

### Model Interfaces and Accounting
Defines the shared provider-facing model request language, safeguards image inputs, and converts token usage into auditable cost records.

### `core/src/ufo/models/interface.py`

`data_model` · `request preparation and model streaming`

This file is the contract between UFO and the model services it can use, such as Anthropic or OpenAI-style providers. Instead of every part of the project knowing each provider's exact message format, the system uses the shared shapes defined here: messages, text blocks, image blocks, tool calls, tool results, reasoning blocks, requests, and streamed response events. Think of it like a standard plug shape: different wall sockets may exist behind the scenes, but the rest of the program plugs into one familiar interface.

The central request type is ModelRequest. It contains the model name, system instructions, conversation messages, available tools, token budget, caching preference, and reasoning setting. The ModelClient protocol says that any model client must offer a complete method that takes one of these requests and streams back ModelEvent objects, such as text chunks, tool-call updates, reasoning items, usage records, or errors.

The file also deals with a practical provider limit: images are expensive and capped. trim_images scans conversation messages for inline images, keeps the newest ones first, enforces per-message and whole-request image counts, then enforces a total byte budget. Dropped images are not silently erased; they become a short text note saying the image was omitted, so the model still knows something used to be there.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool`  (lines 148–153)

```
def _forced_choice_names_an_offered_tool(self) -> 'ModelRequest'
```

**Purpose**: This validation step makes sure a request cannot force the model to use a tool that was never offered. It prevents a confusing situation where the request says “you must use tool X” but tool X is not in the tool list.

**Data flow**: A ModelRequest is being built or checked. The function reads its tool_choice field and its tools list. If no forced tool was requested, it leaves the request unchanged. If a forced tool was requested, it checks that one offered tool has the same name. If not, it raises an error; otherwise, the validated request comes out unchanged.

**Call relations**: This runs as part of Pydantic's model validation, meaning it is automatically called when a ModelRequest is created or validated. It does not call other project functions; it acts as a gatekeeper before the request is passed to a ModelClient.


##### `ModelClient.complete`  (lines 200–200)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the required method for any AI model client implementation. It says: given a standard ModelRequest, stream back standard ModelEvent objects.

**Data flow**: A caller provides a ModelRequest containing the conversation, tools, model choice, and limits. A concrete provider client, such as one for Anthropic or OpenAI-style APIs, turns that into the provider's own format and yields events as they arrive. The output is an asynchronous stream, meaning results can arrive piece by piece instead of all at once.

**Call relations**: This method is only a protocol declaration here, like an interface or promise. Other files implement it for real providers, while orchestration code can call complete without caring which provider is behind it.


##### `trim_images`  (lines 208–236)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function reduces the number and total size of inline images before a model request is sent. It keeps the newest images because they are usually most relevant, and replaces removed images with a short text marker so the conversation still makes sense.

**Data flow**: It receives a tuple of Message objects. It first asks _image_positions to find every image, including images nested inside tool results. It then decides which images may stay based on three limits: per message, per request, and total base64 data size. It uses _image_data_len to count image data toward the byte budget. If nothing must be removed, it returns the original messages. If some images exceed the limits, it calls _trim_message for each message and returns a new tuple where dropped images have become placeholder text.

**Call relations**: This is the main public helper for image trimming in this file. Request-building code can call it before handing messages to a provider client. It coordinates the smaller helper functions: _image_positions finds candidates, _image_data_len measures them, and _trim_message rewrites affected messages.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `_image_data_len`  (lines 239–250)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This helper measures the stored data size of one image at a known position in the message list. It is used so trim_images can enforce the provider's total image byte limit.

**Data flow**: It receives the full message tuple and a position made of message index, block index, and optional nested index. It looks up that exact spot. If the spot contains a top-level image, it returns the length of that image's base64 data string. If the spot points to an image inside a tool result, it returns that nested image's data length. If the position does not actually point to an image, it raises an error because the caller's bookkeeping is wrong.

**Call relations**: trim_images calls this while walking backward through the images it tentatively wants to keep. The result tells trim_images whether keeping that image would push the request over the byte budget.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 253–273)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This helper finds every inline image in the conversation and records where each one lives. It includes both normal image blocks and images tucked inside a tool result.

**Data flow**: It receives the tuple of Message objects. It skips plain string messages because they cannot contain image blocks. For structured messages, it walks through each content block. When it finds a top-level ImageBlock, it records its message and block location. When it finds a ToolResultBlock whose content is a tuple, it checks each part and records any nested ImageBlock locations. It returns a list of positions from oldest to newest.

**Call relations**: trim_images calls this first. The returned positions are the map that lets trim_images decide which images to keep, measure, or replace later.

*Call graph*: called by 1 (trim_images).


##### `_trim_message`  (lines 276–300)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]]) -> Message
```

**Purpose**: This helper rewrites one message by replacing selected images with a standard omitted-image text block. It preserves the rest of the message exactly as it was.

**Data flow**: It receives one message, that message's index, and a set of image positions that should be dropped. If the message content is plain text, it returns the message unchanged. If the message has structured blocks, it builds a new block list. A top-level image marked for removal becomes a TextBlock containing the omitted-image note. A nested image inside a tool result is replaced in the tool result's content tuple. All other blocks are copied through. It returns a new Message object with updated content rather than modifying the original in place.

**Call relations**: trim_images calls this for each message after it has decided which image positions must be removed. This helper creates the actual cleaned messages that are safe to send onward to model clients.

*Call graph*: called by 1 (trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/models/pricing.py`

`domain_logic` · `request handling and accounting`

This file is the pricing rulebook for model usage. When the system records that a model used some number of tokens, it needs to know how much that usage costs. Tokens are small pieces of text processed by a model, and different kinds of tokens can have different prices: input, output, cache reads, and cache writes. Without this file, the accounting code could record usage counts, but it would not be able to turn those counts into a money amount in a consistent way.

The file represents each model's prices with `ModelPrice`, using micro-USD per million tokens. A micro-USD is one millionth of a US dollar, which lets the system avoid floating-point rounding problems. Think of it like counting in cents instead of dollars, but at a much smaller scale.

It also builds a digest, which is a cryptographic fingerprint of the whole price table. If the table changes, the fingerprint changes. This matters because old billing records can be tied back to the exact prices that produced them.

The main `Pricing` object bundles the price table with that digest. Accounting code asks it for a cost, and it delegates the arithmetic to a helper. If an old or unknown model name appears, the system logs that fact and returns zero instead of crashing.

#### Function details

##### `price_digest`  (lines 26–43)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable version stamp for a model price table. This lets the system later prove which exact set of prices was used when billing was calculated.

**Data flow**: It receives a mapping from model names to their prices. It sorts the models, converts the price data into compact JSON text in a predictable order, then runs that text through SHA-256, a standard fingerprinting algorithm. It returns a string starting with `sha256:` followed by the fingerprint.

**Call relations**: When a new `Pricing` object is built, `pricing_from` calls this function to stamp the price table. It relies on JSON formatting and SHA-256 hashing so the same price table always produces the same digest.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 46–60)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one usage record for one model. It multiplies each kind of token usage by that model's matching rate and returns the total in micro-USD.

**Data flow**: It takes a model name, a `Usage` record containing token counts, and a table of model prices. It looks up the model's price, multiplies each token count by its rate, adds the pieces together, and divides by one million because rates are stored per million tokens. If the model is not found, it writes a log message and returns zero.

**Call relations**: `Pricing.micro_usd` calls this function whenever accounting code needs a cost. If the model is unknown, it hands off to the logging system with the event name `pricing.unknown_model` so the problem is visible without stopping the accounting flow.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 70–71)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the main, simple way to ask a `Pricing` object for the cost of a usage record. Callers do not need to know the details of the price table or the arithmetic.

**Data flow**: It receives a model name and a usage record. It uses the `prices` stored inside the `Pricing` object, passes them to `usage_priced_micro_usd`, and returns the calculated micro-USD amount.

**Call relations**: The accounting layer calls this method when recording sandbox, turn, or workspace usage. This method is the small doorway between accounting records and the pricing calculation helper.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 74–77)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete `Pricing` object from a raw model-to-price table. It makes sure the table and its digest are created together.

**Data flow**: It receives a mapping of model names to `ModelPrice` values. It copies that mapping into a regular dictionary, computes the digest for that copied table, and returns a frozen `Pricing` object containing both the prices and the digest.

**Call relations**: Setup code can use this function when it has loaded or defined a price table. It calls `price_digest` first, then creates the `Pricing` object that the rest of the accounting system will use.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the process how to run, which services to use, and which safety options are enabled.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-model-catalog` — The shared directory of available AI models, their providers, limits, prices, key requirements, and routing behavior.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-spend-controls` — The workspace spending caps, prepaid balances, top-up settings, BYOK flags, and billing export state.
- `reg-search-index` — The shared keyword and embedding indexes that let conversations, tools, and background jobs find relevant stored documents.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-provider-runtime-controls` — The in-process model-provider client pools, retry/backoff state, rate-limit buckets, and request-budget guards shared by model calls and background work.
- `reg-prompt-render-audit` — Rendered-prompt fingerprints, template provenance, and compaction/prompt hashes used to trace or reproduce the exact context sent to models.
