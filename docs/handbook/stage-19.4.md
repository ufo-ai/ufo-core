# Sandbox and browser wire contracts  `stage-19.4`

This stage is shared behind-the-scenes support. It defines the “contracts” that other parts of the system rely on when they talk to a sandbox or control a browser. A contract here means a clear agreed shape for messages, like a form that must be filled in the right way.

The sandbox bridge file defines the shared language for live tool use. It says which tool requests are allowed, what replies should look like, and how the system lists the tools a sandbox can offer. This keeps the tool bridge predictable.

The browser actions file does the same for browser automation. It names the actions an agent may request, such as click, type, scroll, wait, or take a screenshot, and gives each action a valid structure.

The browser wire file checks raw JSON messages arriving from Chrome DevTools Protocol, the browser’s control channel, before the rest of the engine uses them. The errors file gives browser code a specific way to report impossible AI outputs, such as referring to something that is not really on the page.

## Files in this stage

### Sandbox bridge contracts
Shared request, response, and tool-exposure contracts define how live-turn sandbox tools are invoked.

### `core/src/ufo/tools/bridge.py`

`data_model` · `live sandbox request setup and tool request handling`

The tool bridge is a controlled doorway between a running sandbox session and a set of tools. This file defines the contract for that doorway: what a caller may ask for, what shape the answer must have, and which tool names are part of the bridge. Without this file, different parts of the system could disagree about simple but important details, such as whether a request is asking to list tools, fetch a schema, or run a tool.

Most of the file is made of Pydantic models. Pydantic is a validation library: it checks that incoming data has the expected shape before the rest of the system trusts it. For example, `ToolBridgeRequest` requires a unique request ID, an action, an optional tool name, and a dictionary of arguments. It also enforces a rule that list requests must not include a tool name or arguments, while schema and execution requests must name a tool.

The file also separates success and failure responses into clear forms: either `ok` is true with a result, or `ok` is false with an error message. Finally, `bridge_tools` builds the actual callable set. It includes built-in object tools and selected unbound external connector tools whose names are approved bridge tool names. A registry is created to validate that the final tool set is usable, for example by catching naming problems.

#### Function details

##### `ToolBridgeRequest._matches_action`  (lines 52–58)

```
def _matches_action(self) -> 'ToolBridgeRequest'
```

**Purpose**: This function checks that a bridge request makes sense for the action it claims to perform. It prevents confusing requests, such as asking to list all tools while also naming one specific tool.

**Data flow**: It receives a `ToolBridgeRequest` after its basic fields have already been read. If the action is `list`, it checks that there is no tool name and no arguments. If the action is `get_schema` or `execute`, it checks that a tool name is present. If the request breaks those rules, it raises an error; otherwise, it returns the same request as valid.

**Call relations**: This is run automatically by Pydantic when a `ToolBridgeRequest` is created or validated. It acts like a gatekeeper before any later bridge code tries to interpret or carry out the request.


##### `ToolBridgeRequester.request`  (lines 92–92)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is the promised shape of an object that can send a tool-bridge request for one live sandbox run. It does not implement the work here; it defines what other code can rely on.

**Data flow**: It takes a signed run token, which identifies and authorizes the live run, plus a validated `ToolBridgeRequest`. An implementation is expected to use those inputs to contact or invoke the bridge and return either a success response with a result or a failure response with an error message.

**Call relations**: This method belongs to a `Protocol`, meaning it is an interface-like promise in Python: any class with a compatible `request` method can be used as a `ToolBridgeRequester`. Other parts of the system can depend on this shape without caring which concrete requester is being used.


##### `bridge_tools`  (lines 95–111)

```
def bridge_tools(manifests: tuple[Manifest, ...]) -> tuple[ToolDef, ...]
```

**Purpose**: This function builds the exact list of tools that the bridge is allowed to expose. It combines built-in object tools with approved external tools from manifests, while keeping bound object actions behind the single `object_action` doorway.

**Data flow**: It receives a tuple of manifests, where each manifest can describe tools and connector tools. It first creates the built-in object-verb tools from `ObjectVerbs({}).tools()`. Then it scans the manifests for tools that are not bound to a specific object and whose names are in the bridge-approved list. It combines those tool definitions, creates a `ToolRegistry` from them to validate the collection, and returns the final tuple of tools.

**Call relations**: When bridge setup needs to know what can be called, it uses this function to assemble that callable set. Inside, it calls `ObjectVerbs.__init__` to prepare the built-in object operations, and it calls `ToolRegistry.__init__` as a validation step before handing the tool list back.

*Call graph*: 2 external calls (__init__, __init__).


### Browser automation contracts
Browser action schemas, model-output errors, and wire-message validation define the browser edge contracts.

### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is like a form template for browser control. Instead of letting the rest of the system pass around vague instructions like “click over there” or “scroll a bit,” it defines exactly what an action may look like and what extra details are allowed or required.

The main idea is the `ComputerAction` model. It describes one browser action: the action name, an optional screen coordinate, optional text to type, optional scroll details, an optional wait time, an optional drag starting point, and an optional element reference. An element reference is a short ID, such as `e5`, that comes from earlier page-reading tools and points to something on the page without needing raw pixel coordinates.

The file also defines `ScrollParameters`, which says which direction to scroll and how far. The distance can be a number of screen heights or the special value `max`, meaning “jump as far as possible.”

These models use Pydantic, a Python library that checks data against declared rules. That matters because browser automation is easy to get wrong: a misspelled action name, an invalid scroll amount, or an unsafe wait time could cause confusing failures later. This file catches those problems early and gives the rest of the browser extension a shared action vocabulary.


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `request handling`

This file is small, but it names an important failure case. In browser automation, an AI model may be asked to choose or refer to something on a page, such as a button, link, or stored element reference. Sometimes the model may “hallucinate,” meaning it invents a value that looks plausible but is not real. For example, it might point to an element reference that was never sent to it or no longer exists.

The file defines `HallucinationError`, a special kind of `ValidationError`. A validation error means “the supplied data did not pass the rules we require.” By making hallucination its own error type, the system can treat this case differently from other invalid inputs if needed. For example, it could explain to the model that the chosen element is not available, retry with better context, or stop safely instead of clicking the wrong thing.

Think of it like a receptionist checking an appointment list. If someone asks for “Room 900” in a building with only five floors, that is not just a normal scheduling problem; it is an impossible request. This error gives that impossible request a clear label.


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `cross-cutting during CDP message parsing`

The browser engine talks to Chrome using the Chrome DevTools Protocol, often shortened to CDP. CDP is a JSON-based protocol, meaning messages arrive as plain nested data: strings, numbers, lists, dictionaries, booleans, or null. This file gives that loose JSON world a safer doorway into the rest of the code.

It first names the allowed JSON shapes with type aliases such as `Json` and `JsonDict`. These do not transform data by themselves; they are shared vocabulary for saying “this value came from the wire.”

The more important work is done by four small narrowing functions: `as_map`, `as_str`, `as_int`, and `as_list`. Each one checks a value before the engine uses it. For example, if the code expects a dictionary-like object but receives a string, `as_map` raises `ValidationError` instead of letting confusing errors appear later. This is like checking a delivery package at the front desk before sending it through the building.

A key detail is that `None` is accepted as an empty dictionary or empty list in the places where that is useful. But required strings must be present and non-empty, and integers must really be integers. Bad shapes become `ValidationError`, which is meant to be reported as a recoverable tool error rather than crashing the whole system.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a dictionary with string keys. It is used when incoming CDP data should contain named fields, and it turns a missing value into an empty object when that is acceptable.

**Data flow**: It receives a JSON value, or `None`, plus a text path that says where the value came from. If the value is `None`, it returns an empty dictionary; if it is already a dictionary, it returns it unchanged. If it is anything else, it raises `ValidationError` with a message naming the bad path.

**Call relations**: Code that reads Chrome protocol data calls `as_map` before treating a value like a set of named fields. When the value is wrong, this function stops the flow immediately by creating a `ValidationError`, so later code does not accidentally work with the wrong kind of data.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a real, non-empty string. It is useful for required text fields from CDP, such as identifiers or names, where an empty or missing value would not be meaningful.

**Data flow**: It receives a JSON value, or `None`, plus a path describing the field being checked. If the value is a non-empty string, it returns that string. For `None`, an empty string, or any non-string value, it raises `ValidationError` explaining that the path must contain a non-empty string.

**Call relations**: Callers use `as_str` at the boundary between raw Chrome JSON and typed engine logic. If the field is valid, the caller gets a safe string to use; if not, `as_str` hands off to `ValidationError` so the bad protocol shape is reported clearly.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer. It protects code that needs a whole number from accidentally receiving text, a list, a missing value, or some other JSON shape.

**Data flow**: It receives a JSON value, or `None`, and a path naming the value being checked. If the value is an integer, it returns it unchanged. Otherwise, it raises `ValidationError` saying that the named path must be an integer.

**Call relations**: Callers use `as_int` when a Chrome message field needs to become a trusted number inside the browser engine. If the value does not match, the function creates a `ValidationError` before the incorrect data can travel deeper into the system.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list. It is used for CDP fields that contain repeated items, and it treats a missing value as an empty list when that is safe.

**Data flow**: It receives a JSON value, or `None`, along with a path describing the field. If the value is a list, it returns that list unchanged; if the value is `None`, it returns an empty list. If the value is anything else, it raises `ValidationError` with a clear message about the expected list shape.

**Call relations**: Callers use `as_list` before looping over values from a Chrome protocol message. It either gives them a list they can safely iterate over, or it creates a `ValidationError` so malformed wire data is caught at the parsing boundary.

*Call graph*: 1 external calls (__init__).
