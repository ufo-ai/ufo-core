# Cross-cutting SDK, extension APIs, protocols, and type contracts  `stage-19` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It is the rulebook that lets UFO’s core, extensions, model providers, browser tools, sandboxes, search systems, memory systems, and iMessage code talk to each other without guessing each other’s data shapes.

The public SDK facade files act like stable front doors. Extension authors import approved tools for manifests, tools, jobs, auth, credentials, connectors, models, search, memory, browser control, logging, billing, feature flags, and administration without depending on fragile internal paths. The core extension contracts define what an extension may declare, what safe runtime context it receives, and what it may show in conversation panel slots.

The sandbox and browser contracts define the exact messages used to request tool actions, automate pages, and report browser errors. The generated iMessage protobuf and gRPC files define message, chat, attachment, event, and streaming records, plus the service “sockets” used by iMessage providers. The direct contract files add shared shapes for memory search, AI model calls, web search, object names, and spawned-agent inputs and outputs. Together, these pieces are the common language that keeps many replaceable parts working as one system.

## Sub-stages

- [Public SDK facades for extension capabilities](stage-19.1.md) `stage-19.1` — 19 files
- [Public SDK facades for platform utilities and administration](stage-19.2.md) `stage-19.2` — 15 files
- [Core extension API contracts](stage-19.3.md) `stage-19.3` — 3 files
- [Sandbox and browser wire contracts](stage-19.4.md) `stage-19.4` — 4 files
- [Generated iMessage protobuf message contracts](stage-19.5.md) `stage-19.5` — 13 files
- [iMessage service stubs and provider contract](stage-19.6.md) `stage-19.6` — 5 files

## Files in this stage

### Shared service contracts
Provider-facing interfaces define how memory, model, and search implementations plug into the core system.

### `core/src/ufo/memory.py`

`data_model` · `cross-cutting memory recall and browsing`

This file is a small contract between two sides of the system. One side stores or searches “memory” items. The other side wants to recall those items and show them to a user or inject them into context. Without this file, every memory provider could return different shapes of data or use different method names, and consumers would need custom code for each one.

The central result type is `MemoryMatch`, a plain record for one search hit. It carries the kind of memory, the text snippet, and optionally the durable object reference behind it. That reference matters because search only finds a possible hit; another part of the system can later open the actual object. It can also include when the item was created and what subject it belongs to, so callers can judge relevance and recency.

`MemorySearchProvider` is a protocol, meaning “anything with these methods counts.” It describes three abilities: search by query text, browse recent items for a set of readable subjects, and report which memory kinds can be listed.

`MemorySearch` is a thin wrapper around one selected provider. It does not search itself. It simply forwards calls to the provider, like a front desk that routes requests to the right specialist.

#### Function details

##### `MemorySearchProvider.search`  (lines 37–43)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This defines the search ability that every memory provider must offer. A caller gives one or more query strings and a source reader, and the provider is expected to return matching memory snippets.

**Data flow**: The inputs are search queries, a `SourceReader` that represents what sources or subjects may be read, and optional start and end times to limit the search window. A concrete provider uses those inputs to look through its memory store. The output is a tuple of `MemoryMatch` records, each describing one provider-neutral result.

**Call relations**: This method is the provider-side contract used by `MemorySearch.search`. The wrapper receives a search request from elsewhere, then hands the exact work to the selected provider through this method.


##### `MemorySearchProvider.list_recent`  (lines 45–51)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This defines how a memory provider lets callers browse recent memory items without typing a search query. It is for showing readable memory in newest-first pages, optionally narrowed to certain kinds of memory.

**Data flow**: The inputs are the readable subjects, a maximum number of items, an optional set of memory kinds, and an optional cursor that marks where the previous page ended. The provider uses those to fetch the next stable page of recent memory items. The output is a `ListingPage` containing `MemoryMatch` records and paging information.

**Call relations**: This method is the provider-side contract used by `MemorySearch.list_recent`. The wrapper does not decide how paging or filtering works; it passes the request to the selected provider, which knows its own storage.


##### `MemorySearchProvider.listable_kinds`  (lines 53–53)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This defines how a memory provider tells callers which kinds of memory can be browsed. It helps user interfaces or consumers offer filters that actually match what the provider can return.

**Data flow**: There are no inputs beyond the provider itself. A concrete provider reports its closed list of listable memory categories. The output is a tuple of kind names as strings.

**Call relations**: This method is the provider-side contract used by `MemorySearch.listable_kinds`. Consumers ask the wrapper, and the wrapper asks the provider so the answer stays tied to the active memory implementation.


##### `MemorySearch.search`  (lines 62–69)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This forwards a memory search request to the selected provider. It gives the rest of the system one simple object to call, even though the real search engine may come from an extension.

**Data flow**: The caller supplies a `SourceReader`, query strings, and optional time bounds. `MemorySearch` passes those values unchanged to its provider’s `search` method. The result that comes back is returned unchanged as a tuple of `MemoryMatch` records.

**Call relations**: This is the consumer-facing side of the search flow. When code wants recall results, it calls `MemorySearch.search`; this method immediately delegates to `MemorySearchProvider.search` because the provider owns the actual search logic.


##### `MemorySearch.list_recent`  (lines 71–78)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This forwards a request to browse recent memory items to the selected provider. It keeps callers from needing to know which memory extension is currently active.

**Data flow**: The caller provides readable subjects, a page size, optional kind filters, and an optional cursor for continuing from a previous page. `MemorySearch` sends those values unchanged to the provider’s `list_recent` method. It returns the provider’s `ListingPage` unchanged.

**Call relations**: This sits between consumers and the provider’s browsing implementation. Callers use `MemorySearch.list_recent` as the stable entry point, and it hands the real work to `MemorySearchProvider.list_recent`.


##### `MemorySearch.listable_kinds`  (lines 80–81)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This asks the selected provider which memory kinds can be listed. It is useful before showing filters or deciding what categories can be browsed.

**Data flow**: There are no inputs beyond the wrapped provider. `MemorySearch` calls the provider’s `listable_kinds` method. The provider’s tuple of kind names is returned unchanged.

**Call relations**: This is the wrapper side of the listable-kinds flow. Consumers call it on `MemorySearch`, and it delegates directly to `MemorySearchProvider.listable_kinds` so the answer comes from the active provider.


### `core/src/ufo/models/interface.py`

`data_model` · `request preparation and model streaming`

This file is the project’s contract with language models. Different providers, such as Anthropic or OpenAI-style services, use different network formats, but the rest of the system needs one shared way to talk about messages, tools, images, reasoning, streamed text, and usage. The classes here are those shared shapes.

A request is represented by ModelRequest. It contains the model name, system instructions, conversation messages, available tools, token budget, reasoning setting, and cache hints. Messages can contain plain text, images, tool calls, tool results, and provider-specific reasoning blocks that must sometimes be echoed back unchanged. This is like keeping a sealed receipt from the provider: the system may not understand every byte, but it must preserve it so the next step is accepted.

The file also defines ModelClient, a protocol, meaning “anything with this complete method can be treated as a model client.” Its complete method streams back ModelEvent objects such as text chunks, tool call starts, reasoning blocks, and usage records.

The practical helper logic is about images. Providers have limits on how many images, and how many image bytes, a request may include. trim_images keeps the newest images and replaces older or oversized ones with a short note. omit_images replaces all images for text-only models. Without these helpers, requests could fail at the provider boundary or silently lose context in a less understandable way.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool`  (lines 157–162)

```
def _forced_choice_names_an_offered_tool(self) -> 'ModelRequest'
```

**Purpose**: This validation check makes sure a request cannot force the model to use a tool that was not actually offered. It catches a bad request early, before it reaches a model provider.

**Data flow**: It reads the ModelRequest after its fields have been filled. If there is no forced tool choice, it leaves the request unchanged. If there is a forced tool name, it compares that name with the offered tools; a match lets the request continue, while no match turns into a clear error.

**Call relations**: This runs as part of building or validating a ModelRequest. It protects later model-client code from receiving an impossible instruction, so provider-specific clients can assume that any forced tool choice names a real offered tool.


##### `ModelClient.complete`  (lines 209–209)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the shared method every model client must provide. It says: give me a ModelRequest, and I will stream back model events as they arrive.

**Data flow**: A caller supplies a complete model request. An implementation sends that request to its provider and yields events over time, such as text pieces, tool-call pieces, reasoning blocks, usage information, or stream-start markers. The protocol itself does not do the work; it defines the shape that real clients must follow.

**Call relations**: Other parts of the system can call complete without caring which provider is behind it. Anthropic, OpenAI-style, router, or other clients fit into the same slot by implementing this method.


##### `trim_images`  (lines 217–248)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares messages for models that accept images but enforce strict image limits. It keeps the most recent images that fit the per-message, per-request, and byte-size limits, and replaces the rest with a clear text note.

**Data flow**: It receives the full message tuple. First it asks _image_positions where all images are, including images nested inside tool results. It chooses which images can stay based on count limits, then checks the total image data size from newest to oldest using _image_data_len. If any images must be removed, it calls _trim_message for each message to produce a new message tuple where dropped images are replaced with an omission marker.

**Call relations**: This is used before provider-specific translation so every model client starts from a safe, shared message shape. It depends on _image_positions to find images, _image_data_len to measure them, and _trim_message to rewrite only the messages that need image replacement.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `omit_images`  (lines 251–259)

```
def omit_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares messages for a model that only accepts text. Instead of sending images the model cannot read, it replaces each one with a short explanation.

**Data flow**: It receives the message tuple and uses _image_positions to find every image. If there are no images, it returns the original messages. If images exist, it calls _trim_message on each message so every image position becomes a text block saying the image was omitted because the model accepts text only.

**Call relations**: This is the text-only counterpart to trim_images. It shares the same image-finding and message-rewriting helpers, but it does not try to preserve any images because the target model cannot use them.

*Call graph*: calls 2 internal fn (_image_positions, _trim_message).


##### `_image_data_len`  (lines 262–273)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This helper reports how large one image is, using the length of its base64 data string. trim_images uses it to stay under the provider’s total image-size budget.

**Data flow**: It receives the full messages and a position that points to one image. It follows that position to either a top-level image block or an image inside a tool result, then returns the length of the stored image data. If the position does not actually point to an image, it raises an error because the caller’s bookkeeping is wrong.

**Call relations**: trim_images calls this while walking kept images from newest to oldest. Its answer decides when the image byte budget has been used up and older images must be replaced.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 276–296)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This helper finds every inline image in the conversation and records where each one lives. It treats images inside tool results as real request images too.

**Data flow**: It receives the message tuple and scans messages from oldest to newest. Plain string messages are skipped. Structured messages are inspected block by block; top-level image blocks are recorded directly, and image parts inside tuple-based tool results are recorded with an extra nested index. It returns a list of positions that later helpers can use to measure or replace images.

**Call relations**: Both trim_images and omit_images call this first. It is the shared mapmaker for image cleanup: once it has marked where the images are, the other helpers can decide which positions to keep or replace.

*Call graph*: called by 2 (omit_images, trim_images).


##### `_trim_message`  (lines 299–326)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]], replacement: str) -> Message
```

**Purpose**: This helper rewrites one message by replacing selected image positions with a text placeholder. It leaves all other content unchanged.

**Data flow**: It receives a message index, one message, a set of image positions to drop, and replacement text. If the message is plain text, it returns it unchanged. For structured content, it builds a new list of blocks: dropped top-level images become TextBlock placeholders, dropped nested images inside tool results become placeholder parts, and everything else is copied through. It returns a copied Message with updated content rather than mutating the original.

**Call relations**: trim_images and omit_images call this after deciding which images cannot be sent. It uses TextBlock to create the visible placeholder and Message.model_copy to return a revised message while preserving the rest of the message data.

*Call graph*: called by 2 (omit_images, trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/search.py`

`data_model` · `startup and request handling`

This file is the boundary between the main system and any web-search backend. The core code does not talk directly to Google, Bing, Exa, Tavily, or any other service. Instead, it speaks through a common shape called a SearchProvider. This is like having a standard wall socket: many devices can plug in, but the house wiring only needs to know the socket shape.

The file defines the plain data objects that travel across this boundary. SearchQuery describes what the user wants to search for, how many results to return, optional date limits, allowed domains, and an optional category such as academic or image search. SearchResults returns ranked SearchHit items, and may also include a direct answer if the backend can synthesize one. FetchRequest describes a request to read one web page, and FetchedPage returns the extracted text and possibly a summary.

The SearchProvider protocol is the promise every search backend must keep. It must say whether it can fetch pages, it must be able to run a search, and, if supported, fetch a URL. This keeps API keys and network calls inside the host process and out of the sandbox, which matters for security and clean separation.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 82–82)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells callers whether this search provider can fetch and extract the contents of a web page, not just return search links. Tools use it to avoid asking a backend to do something it does not support.

**Data flow**: A caller reads this property from a concrete search provider. The provider returns a true-or-false answer. Nothing else is changed; the result simply guides the caller's next choice.

**Call relations**: The research fetch tool checks this before trying to fetch a URL. If it is false, the tool can stop or report that fetching is unavailable instead of calling SearchProvider.fetch and failing later.


##### `SearchProvider.search`  (lines 84–84)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This method is the standard way to ask any configured backend to run a web search. It takes a SearchQuery and returns SearchResults in the core system's common format.

**Data flow**: A caller provides a SearchQuery containing the search sentence, result count, and optional filters such as dates or allowed domains. The concrete provider translates that into whatever its external service expects, waits for the result, and returns SearchResults made of SearchHit entries plus an optional direct answer.

**Call relations**: Research tools call this through the turn's tool context, without caring which backend was selected at startup. The concrete provider may hand the request off to an outside search API, but the rest of the core only sees the shared SearchResults shape.


##### `SearchProvider.fetch`  (lines 86–86)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This method is the standard way to ask a provider to read one web page and return usable text from it. It is only meant to be called when supports_fetch says the provider can do this.

**Data flow**: A caller provides a FetchRequest with the URL, optional extraction prompt, optional maximum text length, and a flag saying whether to bypass cached content. The concrete provider retrieves or reprocesses the page and returns a FetchedPage containing the URL, extracted text, and possibly a summary.

**Call relations**: The research fetch_url tool checks SearchProvider.supports_fetch first, then calls this method when page fetching is available. The selected backend performs the actual network or API work, while core receives a provider-neutral FetchedPage.


### Object identity
Common object naming rules provide stable references that other contracts can safely share.

### `core/src/ufo/object_name.py`

`data_model` · `cross-cutting`

This file is the naming rulebook for objects. In this project, an object is identified by a kind, such as what category it belongs to, and a name, which is the specific item inside that category. Without one strict rulebook, one part of the system might save an object under a name that another part cannot read, link to, or display later.

The file defines two name patterns. A kind must start with a lowercase letter and then use lowercase letters, numbers, or underscores. An object name can use lowercase letters, numbers, and hyphens, must start and end with a letter or number, and cannot be longer than 64 characters. This is like agreeing that every library book label must follow the same format before anyone shelves or searches for it.

The standalone `validate_object_name` function checks caller-supplied object names before they are stored. If a name is bad, it raises `InvalidName`, a clear error type for this exact problem.

The `ObjectRef` class is the main data shape here. It is a frozen Pydantic model, meaning it is validated when created and then cannot be changed. Pydantic is a library that checks data against declared rules. `ObjectRef` stores `kind`, `name`, and an optional `agent`, keeping the agent separate rather than hiding it inside the name.

#### Function details

##### `validate_object_name`  (lines 22–30)

```
def validate_object_name(name: str) -> None
```

**Purpose**: This function checks whether a plain string is allowed to be used as an object name. It is useful before saving a caller-provided name, so bad names are rejected at the doorway instead of causing trouble later.

**Data flow**: It receives a proposed name as text. It checks the length and compares the text to the allowed object-name pattern. If the name is acceptable, nothing is returned and execution continues; if it is too long or uses the wrong shape, it raises `InvalidName` with a message explaining the rule.

**Call relations**: This is the standalone check used by code that needs to validate a name before building a full `ObjectRef`. When the check fails, it creates an `InvalidName` error so the caller can treat this as a naming problem, not as some unrelated failure.

*Call graph*: 1 external calls (__init__).


##### `ObjectRef.validate_kind`  (lines 46–49)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: This validates the `kind` part of an `ObjectRef`. It makes sure every object category name follows the shared grammar, so references are predictable and easy to parse.

**Data flow**: It receives the proposed `kind` value while an `ObjectRef` is being created. It compares that value to the allowed kind pattern. If it matches, the same value is passed onward into the model; if not, it raises a `ValueError` explaining the expected format.

**Call relations**: This method is called automatically by Pydantic during `ObjectRef` creation. It acts as the gatekeeper for the `kind` field before the reference object is accepted.


##### `ObjectRef.validate_name`  (lines 53–59)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: This validates the `name` part of an `ObjectRef`. It prevents references from containing names that are too long or shaped differently from the names the rest of the system expects.

**Data flow**: It receives the proposed object name while an `ObjectRef` is being built. It checks the name length and pattern. A valid name is returned unchanged; an invalid one causes a `ValueError` with the naming rule in the message.

**Call relations**: Pydantic calls this automatically when code creates an `ObjectRef`. It mirrors the standalone object-name rule, but applies it inside the full reference object.


##### `ObjectRef.__str__`  (lines 61–62)

```
def __str__(self) -> str
```

**Purpose**: This turns an `ObjectRef` into a short human-readable string. It shows the object as `kind/name`, which is useful in logs, messages, and places where a compact label is needed.

**Data flow**: It reads the already-validated `kind` and `name` fields from the reference. It joins them with a slash and returns that text. It does not change the object.

**Call relations**: This is used whenever Python needs the text form of an `ObjectRef`, for example through `str(ref)`. It does not call other project logic; it simply presents the validated identity in a standard display form.


### Subagent contracts
Spawned-agent input and output schemas define the validated data boundary for delegated work.

### `core/src/ufo/turns/contracts.py`

`domain_logic` · `spawn, dispatch, delivery, and schema write-time validation`

When one agent asks another agent to do work, both sides need a clear agreement about the message shape. This file is that agreement layer. Without it, bad task inputs or malformed results could travel through the system and fail later in harder-to-understand places.

There are two kinds of contracts here. The default ones are Pydantic models, which are Python classes that check whether data has the expected fields and types. For example, the default task input is an object with a text field called `task`, and the default result is an object with a text field called `result`. The other kind is `JsonContract`, which wraps a raw JSON Schema, meaning a data-shape rule written as JSON-like data rather than as a Python model.

The important design choice is that both kinds answer the same questions: “What is your schema?”, “Does this Python value fit?”, and “Does this JSON text fit?” If validation fails, `JsonContract` deliberately raises Pydantic-style `ValidationError`s so the rest of the system can catch one error shape.

The file also protects the server before a declared schema is stored. It rejects overly large schemas, schemas that are not top-level objects, outside references, and regular-expression-based rules. That avoids surprising network lookups and expensive pattern matching in the serving loop.

#### Function details

##### `ValidatedJson.model_dump`  (lines 55–56)

```
def model_dump(self) -> object
```

**Purpose**: Returns the already-validated data in the same style as a Pydantic model would. This lets raw JSON Schema validation results be used by code that expects model-like objects.

**Data flow**: It starts with a `ValidatedJson` object holding some data that has already passed validation. It reads that stored data and returns it unchanged. Nothing else is modified.

**Call relations**: This is part of the adapter that makes `JsonContract` look like a Pydantic model contract. After `JsonContract.model_validate` creates a `ValidatedJson`, later code can ask it for its plain data using this method.


##### `ValidatedJson.model_dump_json`  (lines 58–59)

```
def model_dump_json(self) -> str
```

**Purpose**: Turns the validated data back into JSON text. This is useful when callers want the same kind of JSON output they would get from a Pydantic model.

**Data flow**: It reads the stored validated data, passes it to JSON serialization, and returns a string of JSON text. The stored data is not changed.

**Call relations**: This supports the model-like interface that `JsonContract` promises. Once validation has produced a `ValidatedJson`, this method hands the value off to `json.dumps` so other parts of the system can send or store it as JSON text.

*Call graph*: 1 external calls (dumps).


##### `JsonContract.model_json_schema`  (lines 68–69)

```
def model_json_schema(self) -> dict[str, object]
```

**Purpose**: Returns the raw JSON Schema behind this contract. Callers use it to inspect or advertise the expected message shape.

**Data flow**: It reads the schema stored inside the `JsonContract`, copies it into a regular dictionary, and returns that copy. The original schema is left alone.

**Call relations**: This mirrors the `model_json_schema` method provided by Pydantic model classes. Because both raw schemas and Pydantic models offer this method, higher-level spawn and delivery code can ask either kind of contract what shape it expects without caring which kind it is.


##### `JsonContract.model_validate`  (lines 71–106)

```
def model_validate(self, data: object) -> ValidatedJson
```

**Purpose**: Checks whether a Python value fits this contract’s JSON Schema. If it fits, it wraps the value as validated data; if it does not, it raises a Pydantic-style validation error.

**Data flow**: It receives any Python value, such as a dictionary decoded from JSON. It builds a JSON Schema validator with an empty reference registry, checks the value, sorts any problems into a stable order, and turns those problems into `ValidationError` entries. If the schema tries to resolve a reference, that is reported as a validation error too. If there are no problems, it returns a `ValidatedJson` holding the original value.

**Call relations**: This is the core of the raw-schema path. `JsonContract.model_validate_json` calls it after parsing JSON text. More broadly, it gives raw JSON Schemas the same validation behavior expected from Pydantic model contracts, including the same general error type for callers to catch.

*Call graph*: called by 1 (model_validate_json); 6 external calls (__init__, from_exception_data, Draft202012Validator, InitErrorDetails, PydanticCustomError, Registry).


##### `JsonContract.model_validate_json`  (lines 108–124)

```
def model_validate_json(self, text: str) -> ValidatedJson
```

**Purpose**: Checks whether a JSON string is valid JSON and also fits this contract’s schema. It is the text-input version of `model_validate`.

**Data flow**: It receives a string. First it tries to parse the string as JSON. If parsing fails, it raises a Pydantic-style validation error saying the JSON itself is invalid. If parsing succeeds, it sends the decoded value to `JsonContract.model_validate` and returns that method’s validated result.

**Call relations**: This method is the doorway for callers that still have message bodies as text. It hands off the real shape checking to `JsonContract.model_validate`, so JSON parsing and schema validation produce one consistent kind of result or error.

*Call graph*: calls 1 internal fn (model_validate); 4 external calls (from_exception_data, loads, InitErrorDetails, PydanticCustomError).


##### `freeform_result_contract`  (lines 130–137)

```
def freeform_result_contract(contract: Contract) -> bool
```

**Purpose**: Detects whether a contract is the simple built-in shape `result: str`. The system can use this to recognize the default freeform result handoff.

**Data flow**: It receives a contract. If the contract is a Pydantic model class, it looks at the model’s fields and returns `true` only when there is exactly one field named `result` and that field is a string. Raw `JsonContract` values, and any other shape, return `false`.

**Call relations**: This is a small classifier used by higher-level code that needs to treat the default freeform result differently from more specific structured outputs. It does not call other local helpers; it simply inspects the contract it is given.


##### `input_contract`  (lines 140–141)

```
def input_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the input contract for an agent task. It uses the default task shape when no custom schema was declared, or wraps the declared schema when one exists.

**Data flow**: It receives either `None` or a mapping that represents a JSON Schema. If the input is `None`, it returns the built-in `TaskInput` model. If a schema is present, it creates and returns a `JsonContract` around that schema.

**Call relations**: This is the small bridge between stored contract data and runtime validation. Spawn or dispatch code can call it to get one contract object, then use that object without caring whether it came from the default Pydantic model or a declared JSON Schema.

*Call graph*: 1 external calls (__init__).


##### `output_contract`  (lines 144–145)

```
def output_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the output contract for an agent result. It uses the default result shape when no custom schema was declared, or wraps the declared schema when one exists.

**Data flow**: It receives either `None` or a mapping that represents a JSON Schema. If there is no schema, it returns the built-in `AgentResultOutput` model. If there is a schema, it creates and returns a `JsonContract` around it.

**Call relations**: This mirrors `input_contract` for the result side of the exchange. Delivery code can call it to obtain a single contract interface, then validate returned data through either a Pydantic model or `JsonContract` in the same way.

*Call graph*: 1 external calls (__init__).


##### `check_declared_schema`  (lines 148–163)

```
def check_declared_schema(candidate: Mapping[str, object], field: str) -> None
```

**Purpose**: Checks a user-declared input or output schema before it is stored. It rejects schemas that are too large, not object-shaped, invalid as JSON Schema, or risky for the server to evaluate later.

**Data flow**: It receives a candidate schema and the name of the field being checked. It measures the schema’s JSON text size, verifies the top-level type is `object`, searches for refused keywords such as references and patterns, and asks the JSON Schema library to compile-check it. If any check fails, it raises `ValueError` with a clear message. If all checks pass, it returns nothing and leaves the schema unchanged.

**Call relations**: This function runs at write time, before later spawn or delivery code reads the contract. It calls `_refused_keyword` to find banned features, uses JSON serialization for the size limit, and uses the JSON Schema validator’s schema checker to confirm the schema is structurally valid.

*Call graph*: calls 1 internal fn (_refused_keyword); 2 external calls (dumps, check_schema).


##### `_refused_keyword`  (lines 166–178)

```
def _refused_keyword(node: object) -> str | None
```

**Purpose**: Searches through a schema-like object for keywords this system does not allow. It exists to keep declared contracts self-contained and safe to evaluate.

**Data flow**: It receives any nested value. If the value is a mapping, it checks each key and then searches each child value. If the value is a list, it searches each item. It returns the first refused keyword it finds, or `None` when the whole structure is clean.

**Call relations**: This is a private helper for `check_declared_schema`. That public checker uses it before accepting a declared schema, so banned features are rejected early rather than causing slow, unsafe, or confusing behavior during agent execution.

*Call graph*: called by 1 (check_declared_schema).

## 📊 State Registers Touched

- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-auth-tokens` — The signed login, surface, artifact, and SDK tokens used to prove that a caller or link is allowed to act.
- `reg-execution-environment` — The controlled runtime environment given to commands, files, terminals, browsers, and documents, including safe environment variables and containment rules.
- `reg-tool-catalog-policy` — The current tool catalog and allowlist rules that say which built-in, extension, connector, MCP, and sandbox tools may be called.
- `reg-model-catalog` — The shared AI model catalog that records available providers, model names, prices, limits, API routes, key sources, and reasoning support.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-index-memory-store` — The searchable index and long-term memory store built from synced pages, embeddings, recalled facts, and deduplicated notes.
- `reg-extension-data-store` — The per-workspace extension storage area where optional features save their own small durable JSON state.
- `reg-object-kind-action-registry` — Process-local registry of built-in and extension object kinds, schemas, actions, visibility rules, and handlers used by the portal object APIs.
- `reg-conversation-slot-provider-registry` — Registered providers that summarize and read extension conversation slots such as artifacts, sources, sites, automations, and task panels.
