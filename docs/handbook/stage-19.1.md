# Extension ABI, manifests, and execution context  `stage-19.1`

This stage is shared behind-the-scenes support for UFO’s extension system. It defines the “rules of the road” for outside code, so extensions can plug in safely without depending on private internals. The manifest file is the main menu: it describes what an extension or pack may declare, such as tools, routes, background jobs, credentials, agents, hooks, storage, and search providers. The contracts file defines the expected shape of data sent to and returned from spawned agents, using either built-in models or JSON Schema, so the core can validate both consistently.

The execution context file builds the limited toolbox an extension or job receives while running. It gives access only to approved things like workspace data, credentials, conversations, model calls, sources, and files. The SDK files are the public front doors for extension authors. They re-export context, manifest, and tool types from stable locations, so extensions can import supported names without reaching into the engine room.

## Files in this stage

### Agent I/O Contracts
Defines the schema-level contracts used to validate spawned-agent task inputs and result outputs.

### `core/src/ufo/contracts.py`

`domain_logic` · `contract selection and validation during schema writes, spawn setup, dispatch, and delivery`

When one part of the system sends work to another, both sides need to agree on the shape of the message. This file is that agreement point. By default, a task is a simple object with a text field called `task`, and a result is a simple object with a text field called `result`. But workspace agents can also declare their own JSON Schema, which is a standard way to describe allowed JSON data.

The important design choice here is that custom schemas are wrapped by `JsonContract` so they behave like Pydantic models. Pydantic is a Python validation library; many other parts of the system already expect its methods and its `ValidationError` error format. This wrapper means callers do not need separate code paths for “normal model” versus “raw JSON Schema.”

The file also protects the server from risky schemas. Before a declared schema is stored, `check_declared_schema` rejects schemas that are too large, are not top-level JSON objects, contain references such as `$ref`, or contain regular-expression features such as `pattern`. In plain terms: schemas must be self-contained, small, and predictable. That prevents a stored schema from trying to fetch outside resources or forcing expensive matching work on the server’s main loop.

#### Function details

##### `ValidatedJson.model_dump`  (lines 47–48)

```
def model_dump(self) -> object
```

**Purpose**: Returns the already-validated JSON-like data in its normal Python form. This makes a raw JSON Schema result act like a Pydantic model object when other code asks to dump it.

**Data flow**: It starts with a `ValidatedJson` object that holds some data which has already passed validation. It simply returns that stored data unchanged. Nothing else is read or modified.

**Call relations**: After `JsonContract.model_validate` accepts some input, it wraps that input in `ValidatedJson`. Later, any code that expects a Pydantic-style validated object can call this method to get the plain data back.


##### `ValidatedJson.model_dump_json`  (lines 50–51)

```
def model_dump_json(self) -> str
```

**Purpose**: Turns the validated data into a JSON text string. This mirrors the Pydantic method name so callers can serialize either kind of validated result the same way.

**Data flow**: It reads the stored validated data, passes it to `json.dumps`, and returns the resulting JSON string. The original data is not changed.

**Call relations**: This is used after validation, when something needs the payload as text rather than as Python objects. It sits beside `model_dump` as part of the small adapter that makes JSON-Schema-validated data look like Pydantic-validated data.

*Call graph*: 1 external calls (dumps).


##### `JsonContract.model_json_schema`  (lines 60–61)

```
def model_json_schema(self) -> dict[str, object]
```

**Purpose**: Returns the schema that this contract uses to judge data. It provides the same kind of schema-reporting method that a Pydantic model class provides.

**Data flow**: It reads the contract’s stored schema mapping, copies it into a plain dictionary, and returns that copy. The stored schema itself is not altered.

**Call relations**: Other parts of the system can ask a contract what shape it expects without caring whether the contract came from a Pydantic model or a raw JSON Schema. This method supplies that common view for raw JSON Schema contracts.


##### `JsonContract.model_validate`  (lines 63–98)

```
def model_validate(self, data: object) -> ValidatedJson
```

**Purpose**: Checks a Python value against this contract’s JSON Schema. If the value fits, it returns a `ValidatedJson` wrapper; if not, it raises a Pydantic `ValidationError` so callers see the same error style as they do for normal models.

**Data flow**: It takes incoming data and the contract’s schema. It builds a JSON Schema validator with an empty reference registry, meaning schema references cannot go fetch or resolve outside this contract. It collects all validation faults in path order. If the schema contains an unresolvable reference, or if the data breaks the schema rules, it converts those problems into Pydantic-style validation errors. If there are no faults, it returns a new `ValidatedJson` containing the original data.

**Call relations**: `JsonContract.model_validate_json` calls this after parsing JSON text. More broadly, this is the main bridge that lets raw JSON Schema contracts stand in for Pydantic model classes during spawn, dispatch, and delivery validation.

*Call graph*: called by 1 (model_validate_json); 6 external calls (__init__, from_exception_data, Draft202012Validator, InitErrorDetails, PydanticCustomError, Registry).


##### `JsonContract.model_validate_json`  (lines 100–116)

```
def model_validate_json(self, text: str) -> ValidatedJson
```

**Purpose**: Checks a JSON text string against this contract. It first makes sure the text is valid JSON, then reuses the normal object validation path.

**Data flow**: It receives a string. It tries to parse that string with `json.loads`. If parsing fails, it raises a Pydantic-style `ValidationError` saying the JSON text is invalid. If parsing succeeds, it sends the parsed data to `JsonContract.model_validate` and returns that result.

**Call relations**: This method is the text-input companion to `JsonContract.model_validate`. It hands off to `model_validate` so JSON strings and already-parsed Python data are checked by the same schema rules.

*Call graph*: calls 1 internal fn (model_validate); 4 external calls (from_exception_data, loads, InitErrorDetails, PydanticCustomError).


##### `input_contract`  (lines 122–123)

```
def input_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract used to validate task input. If no custom schema is supplied, it uses the default task model; otherwise it wraps the supplied JSON Schema in `JsonContract`.

**Data flow**: It receives either `None` or a schema mapping. With `None`, it returns the built-in `TaskInput` model class. With a schema, it creates and returns a `JsonContract` holding that schema.

**Call relations**: Code that prepares or validates spawned work can call this when it needs an input contract. The function hides the choice between the default Pydantic task shape and a workspace-declared raw JSON Schema.

*Call graph*: 1 external calls (__init__).


##### `output_contract`  (lines 126–127)

```
def output_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract used to validate result output. If no custom schema is supplied, it uses the default result model; otherwise it wraps the supplied JSON Schema in `JsonContract`.

**Data flow**: It receives either `None` or a schema mapping. With `None`, it returns the built-in `ResultOutput` model class. With a schema, it creates and returns a `JsonContract` holding that schema.

**Call relations**: Code that receives or checks an agent’s result can call this to get the right output contract. Like `input_contract`, it keeps callers from needing separate logic for default models and declared schemas.

*Call graph*: 1 external calls (__init__).


##### `check_declared_schema`  (lines 130–145)

```
def check_declared_schema(candidate: Mapping[str, object], field: str) -> None
```

**Purpose**: Checks whether a user-declared input or output schema is safe and acceptable before it is stored. It rejects schemas that are too big, not object-shaped at the top level, use forbidden reference or regular-expression features, or are not valid JSON Schema.

**Data flow**: It receives a candidate schema and the name of the field being checked, for use in error messages. It serializes the schema to measure its size, checks that its top-level `type` is `object`, asks `_refused_keyword` whether it contains a forbidden keyword anywhere inside, and then asks the JSON Schema library whether the schema itself is well-formed. If any check fails, it raises `ValueError`; otherwise it returns nothing and leaves the schema unchanged.

**Call relations**: This function is meant to run when a declared schema is written or saved, not later when an agent is spawned. It calls `_refused_keyword` for the recursive safety scan and the JSON Schema library for standards validation, so later validation can rely on stored schemas being bounded and self-contained.

*Call graph*: calls 1 internal fn (_refused_keyword); 2 external calls (dumps, check_schema).


##### `_refused_keyword`  (lines 148–160)

```
def _refused_keyword(node: object) -> str | None
```

**Purpose**: Searches through a schema-like structure for the first keyword this system does not allow, such as `$ref` or `pattern`. It is a safety helper for `check_declared_schema`.

**Data flow**: It receives any nested value. If the value is a mapping, it checks each key and then searches each value. If the value is a list, it searches each item. It returns the first forbidden keyword it finds, or `None` if the whole structure is clean.

**Call relations**: `check_declared_schema` calls this before accepting a declared schema. This helper is deliberately recursive, like looking through every drawer inside a cabinet, because forbidden keywords could be hidden deep inside nested schema rules.

*Call graph*: called by 1 (check_declared_schema).


### Internal Extension Runtime
Describes the internal execution context and manifest declarations that govern what extensions and packs may access or provide.

### `core/src/ufo/ext/context.py`

`orchestration` · `cross-cutting`

Extensions and background jobs need useful powers: saving state, reading credentials, opening conversations, calling models, syncing sources, reading transcripts, and more. But they must not receive raw database or storage access, because that would let a bug or malicious extension reach another workspace or secret. This file solves that by wrapping each power in a narrow object, like giving a contractor only the keys for the rooms they need instead of the master key.

The central object is `ExtensionContext`. It is assembled by `context_for`, and most handlers receive it. It contains smaller capability objects: `ScopedStore` for the extension’s own saved JSON values, `CredentialAccess` for declared credential slots only, `TrajectoryCorpus` for read-only transcript access, `ConversationFiles` and `ConversationProbes` for conversation sandbox work, `ModelAccess` for metered model calls, and source/page helpers for synced content.

A key idea is “ambient workspace”: functions read the currently bound workspace from `ws_current()` rather than accepting a workspace id from the caller. That keeps every operation tied to the job or turn that is actually running. Many methods also check membership, audience, agent ownership, or declared permissions before doing anything. Without this file, extension code would either be too weak to do real work or too powerful to run safely.

#### Function details

##### `ScopedStore.workspace_id`  (lines 109–110)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace currently bound to this run. It lets the store always save and read data inside the right workspace without trusting callers to pass an id.

**Data flow**: It reads the active workspace scope, takes its workspace id, and returns that id. It does not change anything.

**Call relations**: Store methods call this implicitly when they build database queries, so every key-value operation is tied to the workspace that the job or turn is already running under.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 112–123)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one saved JSON value for this extension in the current workspace. Extensions use it to remember durable state, such as a browser run id or reply progress.

**Data flow**: A key goes in. The method opens a workspace-scoped database transaction, looks for a row matching this workspace, extension, and key, and returns the stored value or `None` if absent.

**Call relations**: Browser, Browserbase, Slack, and web surface code call this when they need previous extension state. It relies on the workspace transaction wrapper so callers never receive a raw unrestricted database handle.

*Call graph*: called by 4 (_start, _context, _slack_reply_progress, _own_web_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 125–140)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several saved values for this extension in one database query. It is useful when a caller already knows the exact keys it wants.

**Data flow**: A list of keys goes in. If the list is empty it returns an empty dictionary; otherwise it fetches matching rows for this workspace and extension and returns a key-to-value dictionary, omitting missing keys.

**Call relations**: It is the batched version of `ScopedStore.get`, using the same scoped database path but avoiding many separate reads.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 142–163)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Saves or replaces one JSON value for this extension in the current workspace. It gives extensions a durable place to keep small state.

**Data flow**: A key and JSON value go in. The method first tries to update an existing row; if no row exists, it inserts one with timestamps. Nothing is returned.

**Call relations**: Browser, Browserbase, and web surface code call it after creating or changing extension state. It is paired with `get`, `delete`, and `list` as the extension’s private key-value store.

*Call graph*: called by 3 (_start, _context, _open_conversation); 3 external calls (insert, update, workspace_tx).


##### `ScopedStore.put_if`  (lines 165–215)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Writes a value only if the stored value is still what the caller expected. This prevents one worker from accidentally overwriting another worker’s newer update.

**Data flow**: A key, new value, and expected old value go in. If the expected value is `None`, it tries to insert only if no row exists. Otherwise it locks the row, compares the current value, updates only on a match, and returns `true` or `false`.

**Call relations**: Slack reply checkpoint code uses this during progress updates, where two attempts may race. It builds on the same scoped store table but adds compare-before-write safety.

*Call graph*: called by 2 (_checkpoint_slack_reply, _slack_reply_progress); 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 217–225)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes one saved key for this extension in the current workspace. It is used when extension state is no longer valid.

**Data flow**: A key goes in. The method deletes the row matching this workspace, extension, and key. It returns nothing and does not complain if the key was already absent.

**Call relations**: The web surface calls it while opening conversations and clearing old extension-side state. It is the cleanup partner to `put`.

*Call graph*: called by 1 (_open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 227–240)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists saved key-value pairs for this extension, optionally under a prefix. It lets an extension find its own stored records without seeing anyone else’s.

**Data flow**: An optional prefix goes in. The method fetches matching rows for this workspace and extension, orders them by key, and returns a tuple of key-value pairs.

**Call relations**: Web audience code uses it to discover stored grants. It is still scoped by workspace and extension, so it is not a general database listing.

*Call graph*: called by 2 (_granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 256–257)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose credentials this access object can read. It anchors credential lookup to the active run.

**Data flow**: It reads the active workspace scope and returns its id. No secret is read here.

**Call relations**: Credential resolution and installation binding use this id when they need to fetch or seal a workspace-specific secret.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 259–265)

```
async def get(self, slot: str) -> str
```

**Purpose**: Returns a declared credential value, such as an API key. It refuses any slot the extension did not declare in its manifest.

**Data flow**: A credential slot name goes in. The method checks the declared-slot allowlist, then asks the active workspace for the credential value, returning the secret string or raising an error.

**Call relations**: This is the simplest credential path for handlers. It protects secrets by stopping undeclared slots before any workspace secret lookup happens.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.stored`  (lines 267–274)

```
async def stored(self, slot: str) -> bool
```

**Purpose**: Reports whether a declared credential is stored by this workspace rather than coming from the platform default. This matters for billing and responsibility.

**Data flow**: A slot name goes in. After checking the slot is declared, it asks the active workspace whether that credential is workspace-owned and returns a boolean.

**Call relations**: Code that spends provider credits can use this to tell whether the workspace brought its own key. Like `get`, it blocks undeclared slots first.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.resolve`  (lines 276–289)

```
async def resolve(self, slot: str) -> str
```

**Purpose**: Resolves a declared credential through its manifest-defined source when one exists, falling back to the normal workspace credential. This supports credentials that come from installed provider connections.

**Data flow**: A slot name goes in. The method checks declaration, looks for a configured source, tries to mint or retrieve a source-backed secret from the credential store, and otherwise returns the workspace credential.

**Call relations**: It connects manifest credential declarations to the credential store and workspace fallback path. It raises clearly if a source was declared but no store was wired.

*Call graph*: 3 external calls (__init__, slot_secret, ws_current).


##### `CredentialAccess.rotate`  (lines 291–296)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing declared credential only if its current value matches an expected value. This supports safe secret rotation after an external provider changes a key.

**Data flow**: A slot name, expected old secret, and new plaintext secret go in. The method checks the slot is declared, asks the workspace to rotate if the old value matches, and returns whether it succeeded.

**Call relations**: It is a credential-specific compare-and-swap operation. It delegates the actual secret write to the workspace credential layer.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 298–311)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores a sealed provider installation reference into a declared credential slot. It records that this workspace may use a verified installation without storing a plain installation id.

**Data flow**: A slot and installation id go in. The method checks the slot, seals the workspace, slot, and installation together with the deployment’s secret key, and writes that sealed value as the workspace credential.

**Call relations**: It uses installed credential request configuration and the sealing helper. This is the secure bridge between installation flows and later credential minting.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 344–345)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose conversation transcripts may be read. It keeps transcript reads tied to the active workspace.

**Data flow**: It reads the current workspace scope and returns the workspace id. It does not read any transcript itself.

**Call relations**: The corpus methods use this id to choose conversations before reading transcript blobs.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 347–354)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for evaluation or learning jobs. It avoids handing a job the workspace’s entire history.

**Data flow**: No explicit input is needed. It builds a query for the most recent conversations in the current workspace and passes that selection to `_read`, which returns transcript objects.

**Call relations**: It is the broad corpus read path. `ExtensionContext.trajectories` exposes it when a corpus has been wired.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus.conversations`  (lines 356–368)

```
async def conversations(self, conversation_ids: tuple[UUID, ...]) -> tuple[Trajectory, ...]
```

**Purpose**: Reads transcripts for exactly the conversation ids a caller names, still restricted to the current workspace. It is used when a job already knows its target conversations.

**Data flow**: A tuple of conversation ids goes in. The method filters them to conversations in this workspace, then delegates to `_read` and returns the decoded trajectories found.

**Call relations**: It shares the actual transcript decoding with `trajectories`, but skips the recent-history limit because the caller supplied exact ids.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus._read`  (lines 370–414)

```
async def _read(self, chosen: sa.ScalarSelect[UUID]) -> tuple[Trajectory, ...]
```

**Purpose**: Does the real work of turning selected conversation rows into decoded trajectory records. It skips missing or corrupt transcripts instead of failing the whole corpus read.

**Data flow**: A SQL selection of conversation ids goes in. The method fetches conversation, turn, and agent prompt data, reads each transcript blob, decodes it, computes the prompt digest, and returns `Trajectory` objects.

**Call relations**: `trajectories` and `conversations` both call this. It touches both the database and blob store, logs corrupt transcript skips, and builds the read-only data handed to jobs.

*Call graph*: called by 2 (conversations, trajectories); 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `ConversationFiles.write`  (lines 433–436)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s sandbox workspace, where the agent can see them later under `/workspace`. It is for off-turn code that needs to prepare files for an agent.

**Data flow**: A conversation id, relative path, and bytes go in. The sandbox layer validates and writes the file, then returns the path visible inside the agent workspace.

**Call relations**: This is a narrow wrapper over `ConversationSandbox.write`. It keeps file writes limited to conversation workspaces rather than exposing the sandbox object directly.


##### `ConversationFiles.prune`  (lines 438–444)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a sandbox path prefix, keeping only the newest set. It prevents unattended writers from filling a conversation workspace forever.

**Data flow**: A conversation id, path prefix, and keep count go in. The sandbox layer removes older matching files and returns nothing.

**Call relations**: It is the cleanup companion to `ConversationFiles.write`, delegating the actual filesystem work to the conversation sandbox service.


##### `conversation_agent_id`  (lines 447–460)

```
async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation belongs to, within a specific workspace. It returns nothing if the conversation id is not in that workspace.

**Data flow**: A workspace id and conversation id go in. The method queries the conversation table and returns the agent id or `None`.

**Call relations**: `ConversationProbes.run` uses it before opening a sandbox, and `ExtensionContext.conversation_agent` exposes it to handlers that need to resolve a conversation safely.

*Call graph*: called by 2 (run, conversation_agent); 2 external calls (select, workspace_tx).


##### `ConversationProbes.run`  (lines 499–549)

```
async def run(self, conversation_id: UUID, command: str, timeout_s: int=PROBE_TIMEOUT_SECONDS, acting_member_id: UUID | None=None) -> ExecResult
```

**Purpose**: Runs one bounded shell command inside a conversation’s sandbox while no turn is active. It lets background work inspect or test the same files an agent uses, without giving it unlimited runtime or model credentials.

**Data flow**: A conversation id, command, timeout, and optional acting member go in. The method checks the timeout, verifies the conversation belongs to the current workspace, creates a signed probe token, opens the sandbox under the conversation’s agent scope, runs the command, and returns stdout, stderr, and exit code.

**Call relations**: It calls `conversation_agent_id` to enforce workspace ownership, asks the injected environment builder for probe environment variables, and delegates command execution to the sandbox session.

*Call graph*: calls 1 internal fn (conversation_agent_id); 5 external calls (__init__, now, agent, ws_current, uuid4).


##### `trajectory_workspaces`  (lines 552–574)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for jobs that need conversation trajectories. It finds workspaces that have at least one conversation with at least one turn.

**Data flow**: No runtime data goes in. It creates a query-building inner function and wraps it as `WorkspaceCandidates`, which the job dispatcher can later evaluate.

**Call relations**: A trajectory-reading extension declares this as its candidate source. The dispatcher uses the returned selector before binding each workspace and running the job.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 560–572)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query for `trajectory_workspaces`. It selects workspaces that have turn-bearing conversations.

**Data flow**: No direct input goes in. It returns a SQL query using existence checks for conversations and turns.

**Call relations**: It is used inside `trajectory_workspaces` by the owner-candidate wrapper, keeping the core table knowledge inside this file.

*Call graph*: 2 external calls (exists, select).


##### `seated_member_workspaces`  (lines 577–587)

```
def seated_member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for jobs that should run only where at least one member has a seat. It is aimed at first-party member-related jobs.

**Data flow**: No runtime data goes in. It wraps an inner query builder and returns workspace candidates.

**Call relations**: The job dispatcher can use the returned candidates to avoid running member jobs in empty or unseated workspaces.

*Call graph*: 1 external calls (owner_candidates).


##### `seated_member_workspaces.with_a_seated_member`  (lines 580–585)

```
def with_a_seated_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces with seated members. A seated member is a member currently holding an active seat.

**Data flow**: No direct input goes in. It selects distinct workspace ids from member rows where the seated timestamp is present.

**Call relations**: It is the database-specific part used by `seated_member_workspaces`.

*Call graph*: 1 external calls (select).


##### `connection_workspaces`  (lines 590–603)

```
def connection_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for connection-driven jobs. It finds workspaces whose main agent has at least one connector grant.

**Data flow**: No runtime data goes in. It wraps an inner query builder as candidates for the dispatcher.

**Call relations**: Connection-based background handlers use this so they do not run in workspaces with no relevant connected accounts.

*Call graph*: 1 external calls (owner_candidates).


##### `connection_workspaces.with_a_main_agent_connection`  (lines 595–601)

```
def with_a_main_agent_connection() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspaces where the main agent has connector access. It looks through connector grants joined to agents.

**Data flow**: No direct input goes in. It returns a SQL query selecting distinct workspace ids for main-agent connector grants.

**Call relations**: It is used only inside `connection_workspaces`, keeping the schema details out of extension manifests.

*Call graph*: 1 external calls (select).


##### `awaiting_a_title`  (lines 609–625)

```
def awaiting_a_title() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition for conversations that still need an automatic title summary. A conversation qualifies only after a member turn has completed.

**Data flow**: No direct input goes in. It returns a SQL boolean expression checking that the conversation has not been summarized and has a completed member-sourced turn.

**Call relations**: `untitled_conversation_workspaces` and `ExtensionContext.conversations_awaiting_title` both reuse this condition so candidate selection and actual work agree.

*Call graph*: called by 2 (conversations_awaiting_title, with_an_unsummarized_title); 3 external calls (and_, exists, select).


##### `untitled_conversation_workspaces`  (lines 628–637)

```
def untitled_conversation_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for the conversation-title summarizing job. It finds workspaces with at least one conversation still awaiting a title.

**Data flow**: No runtime data goes in. It wraps an inner query builder and returns workspace candidates.

**Call relations**: The title summarizer uses this to avoid waking up for workspaces where every relevant conversation is already named.

*Call graph*: 1 external calls (owner_candidates).


##### `untitled_conversation_workspaces.with_an_unsummarized_title`  (lines 634–635)

```
def with_an_unsummarized_title() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that selects workspaces with unsummarized conversations. It uses the shared title-needed condition.

**Data flow**: No direct input goes in. It returns a SQL query selecting distinct workspace ids from conversations that match `awaiting_a_title`.

**Call relations**: It sits inside `untitled_conversation_workspaces` and depends on `awaiting_a_title` for the exact work definition.

*Call graph*: calls 1 internal fn (awaiting_a_title); 1 external calls (select).


##### `unseeded_agent_workspaces`  (lines 640–665)

```
def unseeded_agent_workspaces(extension: str, prefix: str) -> WorkspaceCandidates
```

**Purpose**: Builds a candidate selector for sweep jobs that need to do once-per-agent setup. It finds workspaces where not every agent has a corresponding extension store key.

**Data flow**: An extension name and key prefix go in. It creates a query builder that compares the number of agents with the number of matching settled keys, then returns candidates.

**Call relations**: A setup sweep can declare this so the dispatcher runs it only for workspaces that still have agents needing seeding.

*Call graph*: 1 external calls (owner_candidates).


##### `unseeded_agent_workspaces.with_an_unsettled_agent`  (lines 648–663)

```
def with_an_unsettled_agent() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query used by `unseeded_agent_workspaces`. It counts agents and settled extension-store keys per workspace.

**Data flow**: It closes over the extension name and prefix. It returns a SQL query selecting workspaces where the agent count is greater than the settled-key count.

**Call relations**: It is the schema-aware inner function passed to the owner-candidate wrapper.

*Call graph*: 1 external calls (select).


##### `TurnInvoker.invoke`  (lines 672–684)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Defines the interface for starting an internal turn from background code. It is a protocol, meaning this file describes what an invoker must provide without implementing it here.

**Data flow**: Conversation, agent, message, idempotency key, and scheduling options go in. An implementation should admit or reuse a turn and return its id, or `None` when watermark rules say not to run.

**Call relations**: `ExtensionContext.invoke` calls whatever concrete invoker was injected. This avoids importing the full turn engine into this context module.


##### `ModelResolver.auto_model`  (lines 694–694)

```
def auto_model(self) -> str
```

**Purpose**: Defines the model id that background model access should use by default. It is part of the model resolver protocol.

**Data flow**: No input goes in. A concrete resolver returns the deployment’s default model name.

**Call relations**: `ModelAccess.model` and `ModelAccess.turn` read this property so all background completions use the configured default model.


##### `ModelResolver.pricing`  (lines 697–697)

```
def pricing(self) -> Pricing
```

**Purpose**: Defines where model price information comes from. It lets metered model calls calculate usage cost.

**Data flow**: No input goes in. A concrete resolver returns a pricing table.

**Call relations**: `ModelAccess.turn` passes this pricing information into workspace billing when usage arrives from the model stream.


##### `ModelResolver.client_for`  (lines 699–699)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Defines how to obtain a model client for a chosen model. The client is the object that actually talks to the model provider.

**Data flow**: A model id goes in. A concrete resolver returns an asynchronous model client for that model.

**Call relations**: `ModelAccess.turn` calls this before streaming a completion, keeping the model registry behind a small interface.


##### `ModelResolver.key_slot_for`  (lines 701–701)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Defines how to find the credential slot associated with a model, when one exists. This is used for BYOK, meaning “bring your own key,” labeling.

**Data flow**: A model id goes in. A concrete resolver returns a credential slot name or `None`.

**Call relations**: `context_for` exposes this through `ExtensionContext.key_slot_for`, and usage export code uses it to label billing exports correctly.


##### `ModelResolver.provider_for`  (lines 703–703)

```
def provider_for(self, model: str) -> str
```

**Purpose**: Defines how to identify the provider behind a model, such as the company or service serving it. This is used for metrics and billing labels.

**Data flow**: A model id goes in. A concrete resolver returns a provider name string.

**Call relations**: `ModelAccess.turn` uses it when emitting model latency and token metrics.


##### `ModelAccess.model`  (lines 727–729)

```
def model(self) -> str
```

**Purpose**: Returns the default model that this background model access will call and bill. It is read-only from the handler’s point of view.

**Data flow**: It reads the resolver’s default model and returns that string. No provider call happens.

**Call relations**: Handlers can inspect this before calling `complete` or `turn`; the actual model call still goes through the metered methods.


##### `ModelAccess.complete`  (lines 731–738)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one model completion and returns only the assistant’s text. It is a convenience wrapper for callers that do not need tool calls or reasoning blocks.

**Data flow**: A model request goes in. The method delegates to `turn`, then extracts text from the returned assistant message and returns a string.

**Call relations**: The memory extension’s summarizer calls this. It relies on `turn` for streaming, metering, billing, and error accounting.

*Call graph*: calls 1 internal fn (turn); called by 1 (_summarize).


##### `ModelAccess.turn`  (lines 740–834)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one full model turn for a background job, including text, tool calls, reasoning blocks, token usage, billing, and metrics. It keeps model spending visible and tied to the active workspace.

**Data flow**: A model request goes in. The method forces the configured default model, streams events from the model client, accumulates text and tool-call JSON, sums usage, records billable usage, emits timing and token metrics, and returns an assistant `Message`.

**Call relations**: `complete` calls this for text-only use. It depends on the model resolver, workspace billing context, and observability helpers so background model calls behave like normal turn model calls.

*Call graph*: called by 1 (complete); 10 external calls (__init__, __init__, __init__, __init__, model_copy, loads, monotonic, emit_histogram, emit_metric, ws_current).


##### `_source_readable`  (lines 892–919)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition for whether a synced content source is readable by a particular agent and member context. It centralizes source visibility rules.

**Data flow**: A workspace id and `SourceReader` go in. It returns a SQL expression requiring the source to be live, in an allowed subject, and either granted to the agent or owned by the requesting member through the main agent path.

**Call relations**: `readable_page_states`, `readable_source_ids`, and `source_pages` all use this helper so they enforce the same source-access rule.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `MemberContextRecord._aware_utc`  (lines 976–977)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a context record’s information date so it always has a timezone. If a date is missing timezone information, it treats it as UTC.

**Data flow**: A datetime goes in. The validator returns it unchanged if it is timezone-aware, or returns a copy marked as UTC if not.

**Call relations**: Pydantic calls this automatically when creating `MemberContextRecord` objects, including records built by member context methods.

*Call graph*: 1 external calls (replace).


##### `_member_blob_text`  (lines 983–1001)

```
async def _member_blob_text(blob: WorkspaceBlobStore, key: str) -> str
```

**Purpose**: Reads a bounded amount of text from blob storage for member context. It prevents large files from being loaded fully into memory.

**Data flow**: A blob store and blob key go in. The function streams chunks until a byte limit is reached, closes the stream, decodes valid text, trims a partial UTF-8 character if needed, and returns a string.

**Call relations**: `ExtensionContext.member_context` uses it when including text artifacts and synced pages in a scheduled member’s context.

*Call graph*: calls 1 internal fn (get_stream); called by 1 (member_context).


##### `ExtensionContext.workspace_id`  (lines 1028–1029)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id for this context. It is a convenient way for handlers to refer to the workspace they are already bound to.

**Data flow**: It reads the id from the context’s `ScopedStore` and returns it. It does not query the database itself.

**Call relations**: Many methods use this property to keep their reads and writes aligned with the store’s ambient workspace.


##### `ExtensionContext.seated_members`  (lines 1031–1056)

```
async def seated_members(self, *, cursor: UUID | None=None, limit: int=100) -> SeatedMemberPage
```

**Purpose**: Reads a paged list of seated members for first-party jobs that are allowed to inspect member context. It is permission-gated.

**Data flow**: An optional cursor and limit go in. The method checks permission and limit bounds, queries seated members in id order, returns member records, and includes a next cursor when more remain.

**Call relations**: The sweep extension calls it during scheduled member work. It only works when `context_for` enabled member-context reading.

*Call graph*: called by 1 (_tick); 4 external calls (__init__, __init__, select, workspace_tx).


##### `ExtensionContext.workspace_agents`  (lines 1058–1084)

```
async def workspace_agents(self) -> tuple[WorkspaceAgent, ...]
```

**Purpose**: Reads the workspace’s agents, including owners and tool lists, for trusted sweep/setup jobs. It is not available to ordinary extensions.

**Data flow**: No explicit input goes in. After a permission check, it queries agents in creation order and returns `WorkspaceAgent` records.

**Call relations**: The web extension’s homepage seeding uses it. The permission flag keeps the full agent roster out of untrusted contexts.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.earliest_seated_admin`  (lines 1086–1104)

```
async def earliest_seated_admin(self) -> UUID | None
```

**Purpose**: Finds the earliest seated admin in the workspace. This gives background work a deterministic member to act for when an agent has no owner.

**Data flow**: No explicit input goes in. After permission checking, it queries seated admins ordered by seating time and id, returning one member id or `None`.

**Call relations**: The web surface seeding flow calls it when choosing an on-behalf-of member for ownerless work.

*Call graph*: called by 1 (seed_homepages); 2 external calls (select, workspace_tx).


##### `ExtensionContext.invoke_agent_for_member`  (lines 1106–1196)

```
async def invoke_agent_for_member(self, *, agent_name: str, member_id: UUID, conversation_key: str, message: str, idempotency_key: str) -> ScheduledMemberTurn
```

**Purpose**: Opens or reuses a private extension conversation for a member and schedules an agent turn in it. It is used by trusted scheduled member jobs.

**Data flow**: Agent name, member id, conversation key, message, and idempotency key go in. The method checks permission, verifies the member is seated, finds the extension-provisioned agent, locks or creates the private conversation, invokes the turn, and returns the conversation and turn ids.

**Call relations**: The sweep extension calls this during member ticks. Internally it calls `ExtensionContext.invoke` after safely resolving the conversation and agent.

*Call graph*: calls 1 internal fn (invoke); called by 1 (_tick); 6 external calls (__init__, insert, select, conversation_audience, workspace_tx, uuid4).


##### `ExtensionContext.member_context`  (lines 1198–1341)

```
async def member_context(self, *, since: datetime, limit: int=200) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Builds a recent, bounded context bundle visible to a scheduled member. It gathers relevant conversations, artifacts, synced pages, memories, and objectives.

**Data flow**: A since timestamp and limit go in. The method checks that a scheduled member is bound, reads visible records from several tables and blobs, converts them into `MemberContextRecord` objects, sorts by date, and returns the newest records up to the limit.

**Call relations**: It calls `_member_blob_text` for blob-backed text and `_member_extension_records` for memory/objective records. It is designed for scheduled member agents that need context without reading everything.

*Call graph*: calls 2 internal fn (_member_extension_records, _member_blob_text); 5 external calls (__init__, exists, select, readable_audiences, workspace_tx).


##### `ExtensionContext._member_extension_records`  (lines 1343–1580)

```
async def _member_extension_records(self, member_id: UUID, audiences: tuple[str, ...], since: datetime, limit: int) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Adds extension-owned memory and open objective records to scheduled member context. It keeps task-like long-running state visible even if it was not recently updated.

**Data flow**: A member id, readable audiences, since timestamp, and limit go in. It queries memory and objective tables, inspects objective steps, events, and checks to decide which objectives are still open, then returns context records.

**Call relations**: `member_context` calls this as one part of its combined context result. The tables are declared locally to avoid importing extension schema objects.

*Call graph*: called by 1 (member_context); 8 external calls (__init__, sha256, DateTime, column, or_, select, table, workspace_tx).


##### `ExtensionContext.retitle_conversation`  (lines 1582–1585)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Sets a conversation title in the current workspace. It is used when a job or extension knows a better title.

**Data flow**: A conversation id and title go in. The method passes the current workspace id, conversation id, and title to the surface retitling helper. It returns nothing.

**Call relations**: It delegates the actual title write to the surface layer, keeping context callers on a small safe method.

*Call graph*: 1 external calls (retitle_conversation).


##### `ExtensionContext.conversations_awaiting_title`  (lines 1587–1611)

```
async def conversations_awaiting_title(self, limit: int) -> tuple[UUID, ...]
```

**Purpose**: Lists conversations in this workspace that still need automatic title summaries. It returns the newest work first.

**Data flow**: A limit goes in. The method queries conversations matching `awaiting_a_title`, orders them newest first, and returns their ids.

**Call relations**: The web surface title summarizer calls this, then later calls `summarized_conversation_title` for each processed conversation.

*Call graph*: calls 1 internal fn (awaiting_a_title); called by 1 (summarize_chat_titles); 2 external calls (select, workspace_tx).


##### `ExtensionContext.summarized_conversation_title`  (lines 1613–1618)

```
async def summarized_conversation_title(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores a summarized conversation title and marks the title-summary attempt complete. This prevents the same conversation from being summarized repeatedly.

**Data flow**: A conversation id and title go in. The method delegates to the surface helper with the current workspace id. It returns nothing.

**Call relations**: The web title summarizer calls it after generating a title for ids returned by `conversations_awaiting_title`.

*Call graph*: called by 1 (summarize_chat_titles); 1 external calls (summarize_conversation_title).


##### `ExtensionContext.pending_usage_exports`  (lines 1620–1642)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns settled but not yet acknowledged usage-export records for this extension. It first mints new export intents so the returned list is up to date.

**Data flow**: A floor timestamp and limit go in. The method checks that model key-slot labeling is wired, opens a transaction, mints exports, reads pending exports for this workspace and extension, and returns them.

**Call relations**: It works with the accounting module. `ack_usage_exports` is the follow-up after an external receiver accepts the records.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 1644–1653)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered so they stop appearing in pending reads. It should be called only after the external destination has accepted them.

**Data flow**: A tuple of export records goes in. If empty, nothing happens; otherwise it opens a transaction and asks accounting to acknowledge them.

**Call relations**: It completes the export flow begun by `pending_usage_exports`. Leaving records unacknowledged makes them safely redeliverable.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 1656–1668)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides a workspace-scoped database transaction for an extension’s own tables and approved SDK operations. It is powerful and expects the extension to scope its own custom tables correctly.

**Data flow**: No explicit input goes in. The method opens a workspace transaction, yields the database connection to the caller, commits on normal exit, and rolls back on error.

**Call relations**: Memory, research, sweep, and web extension code call this for their own table work. It is the one place this context intentionally yields a raw connection.

*Call graph*: called by 8 (_item, _page, record_sources, _finalize, _prior_ledgers, _tick, _gate, web_audience); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 1670–1706)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Starts an internal turn through the injected turn invoker. It checks that an invoker exists and passes through safety options such as idempotency and member-arrival watermarks.

**Data flow**: Conversation id, agent id, message, idempotency key, and optional scheduling flags go in. The method delegates to the wired invoker and returns the new or reused turn id, or `None` when watermark conditions suppress the turn.

**Call relations**: `invoke_agent_for_member` and web homepage seeding use it. The actual turn admission logic lives behind the injected `TurnInvoker`.

*Call graph*: called by 2 (invoke_agent_for_member, seed_homepages).


##### `ExtensionContext.tail`  (lines 1708–1717)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for a turn until the turn ends. It is for side-channel work that wants to watch a turn it started or follows.

**Data flow**: A turn id and optional cursor string go in. The method checks that a tailer is wired and returns an async context manager that yields live frames.

**Call relations**: It delegates entirely to the injected `TurnTailer`. If no tailer was provided, it fails loudly rather than pretending there is no output.


##### `ExtensionContext.turn_is_terminal`  (lines 1719–1733)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a final state. Missing turns are treated as terminal because there is nothing left to watch.

**Data flow**: A turn id goes in. The method queries this workspace’s turn row and returns `true` if missing or if its status is one of the known terminal statuses.

**Call relations**: Handlers can use it with `tail` to decide whether it is still safe or useful to emit side-channel updates for a turn.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.conversation_agent`  (lines 1735–1739)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent bound to a conversation in this workspace, or `None` if the conversation is not here. It is a safe lookup for opaque conversation ids.

**Data flow**: A conversation id goes in. The method calls `conversation_agent_id` with the current workspace id and returns its result.

**Call relations**: It exposes the shared helper to extension handlers while preserving workspace isolation.

*Call graph*: calls 1 internal fn (conversation_agent_id).


##### `ExtensionContext.conversation_facts`  (lines 1741–1776)

```
async def conversation_facts(self, conversation_ids: tuple[UUID, ...]) -> dict[UUID, ConversationFacts]
```

**Purpose**: Reads visibility and origin facts for a batch of conversations. Member-facing listings use this to decide who may see a row and where it came from.

**Data flow**: A tuple of conversation ids goes in. If empty it returns an empty dictionary; otherwise it queries matching conversations in this workspace, parses their audience strings, and returns a map to `ConversationFacts`.

**Call relations**: It batches what would otherwise be many conversation reads. It intentionally omits ids outside the workspace instead of inventing defaults.

*Call graph*: 4 external calls (__init__, select, parse_audience, workspace_tx).


##### `ExtensionContext.conversation_arrival_seq`  (lines 1778–1805)

```
async def conversation_arrival_seq(self, conversation_id: UUID) -> int
```

**Purpose**: Returns the highest member-message arrival sequence for a conversation. This is a watermark used to tell whether a member spoke after some background work was armed.

**Data flow**: A conversation id goes in. The method queries inbound member messages for this workspace and conversation, returns the maximum sequence number, or 0 if none exist.

**Call relations**: The returned watermark can be passed later to `invoke` options that avoid waking work when a newer member arrival has already happened.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.turn_outcomes`  (lines 1807–1833)

```
async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]
```

**Purpose**: Reads final status information for a batch of turns. It supports status displays that need to show what happened last.

**Data flow**: A tuple of turn ids goes in. If empty it returns `{}`; otherwise it queries matching turns in this workspace and returns a map to `TurnOutcome` objects with status and terminal text.

**Call relations**: The web surface homepage seeding calls this. Like conversation facts, it batches reads and treats missing ids as absent.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.is_operator_workspace`  (lines 1835–1841)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Reports whether the current workspace belongs to the platform operator. It helps hide operator-only information in customer workspaces.

**Data flow**: No explicit input goes in. The method reads the workspace domain in a transaction and compares it to the operator email domain, returning a boolean.

**Call relations**: Rendering code can call this before showing sensitive operator-only links or spend details.

*Call graph*: 2 external calls (workspace_tx, workspace_domain).


##### `ExtensionContext.open_conversation`  (lines 1843–1907)

```
async def open_conversation(self, agent_id: UUID, key: str, member_id: UUID | None=None) -> UUID
```

**Purpose**: Gets or creates an extension-owned conversation for a workflow key and agent. It gives repeated events for the same subject one stable conversation history.

**Data flow**: An agent id, key, and optional member id go in. The method verifies the agent belongs to this workspace, inserts the conversation if missing using the extension name as the surface, and returns the conversation id.

**Call relations**: The web surface seeding code uses it before invoking turns. Actual turn creation is separate and happens through `invoke`.

*Call graph*: called by 1 (seed_homepages); 4 external calls (select, conversation_audience, workspace_tx, uuid4).


##### `ExtensionContext.agent_name`  (lines 1909–1922)

```
async def agent_name(self) -> str
```

**Purpose**: Returns the name of the currently bound agent. It lets agent-scoped extension objects link back to the agent they belong to.

**Data flow**: No explicit input goes in. The method reads the current agent scope, queries that agent in its workspace, and returns its name.

**Call relations**: The skill creation extension calls this when building skill objects tied to an agent.

*Call graph*: called by 1 (_skill); 3 external calls (select, agent_current, workspace_tx).


##### `ExtensionContext.page_states`  (lines 1924–1949)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for live synced pages in this workspace. It returns subject, revision, digest, and blob reference for each page found.

**Data flow**: A tuple of page ids goes in. If empty it returns `{}`; otherwise it queries non-tombstoned pages in this workspace and returns a map to `PageState`.

**Call relations**: This is the raw workspace-scoped page-state read. `readable_page_states` adds source visibility checks on top.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 1951–1985)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads page states only for pages the given reader is allowed to see. It combines page liveness, subject checks, and source access rules.

**Data flow**: Page ids and a `SourceReader` go in. The method joins pages to sources, filters by workspace, subject, tombstone status, and `_source_readable`, then returns page states by id.

**Call relations**: Memory object code calls this before reading page-backed objects. It relies on `_source_readable` for consistent source permission rules.

*Call graph*: calls 1 internal fn (_source_readable); called by 2 (_item, _page); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 1987–1992)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the live source ids readable by a given reader. It is a compact permission-filtered source listing.

**Data flow**: A `SourceReader` goes in. The method builds a source query using `_source_readable`, executes it, and returns a frozen set of ids.

**Call relations**: It shares the same access predicate as page reads, so callers can compare or filter source references safely.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 1994–2176)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers or revives a content-sync source for the workspace and grants it to an agent. It ensures the same source identity is reused instead of creating duplicate feeds.

**Data flow**: Backend name, typed config, subject, owner, optional connection, and optional agent id go in. The method computes the deterministic source id, validates the target agent and connection, inserts or revives the source, rejects conflicting authority or requested fields, grants the source to the agent, and returns the source id.

**Call relations**: The sample extension setup calls it. It uses `source_id` to settle identity and prepares rows that the core sync driver later claims and syncs.

*Call graph*: calls 1 internal fn (source_id); called by 1 (_setup); 5 external calls (now, model_dump, select, update, workspace_tx).


##### `ExtensionContext.source_id`  (lines 2178–2196)

```
def source_id(self, backend: str, config: BaseModel, *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Computes the deterministic id that `register_source` would use for a source. This lets callers reason about a source before it exists.

**Data flow**: Backend, config, and optional connection id go in. The method dumps the config to JSON-like data, removes non-identity fields when applicable, and returns the derived UUID.

**Call relations**: `register_source` calls it before writing. It pairs with `removed_source_ids` for callers that need to know whether a source was deleted rather than never created.

*Call graph*: called by 1 (register_source); 2 external calls (model_dump, source_row_id).


##### `ExtensionContext.removed_source_ids`  (lines 2198–2221)

```
async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Reports which of the given source ids are explicitly removed in this workspace. Absence is not treated as removal.

**Data flow**: A tuple of source ids goes in. If empty it returns an empty frozen set; otherwise it queries rows with `removed_at` set and returns their ids.

**Call relations**: Callers can use this before deciding whether to recreate or ignore source-related state. It deliberately requires positive evidence of removal.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.sources`  (lines 2223–2263)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists live registered sources in this workspace, optionally for one backend. It is the read side of source registration.

**Data flow**: An optional backend filter goes in. The method queries non-removed source rows, orders them, and returns `SourceRecord` values.

**Call relations**: The sources extension uses this to build tool bindings from registered feeds.

*Call graph*: called by 1 (_bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 2265–2313)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages readable by a given agent/member reader. It exposes page metadata while keeping page body content in blob storage by reference.

**Data flow**: A `SourceReader` goes in. The method joins pages to sources, filters by workspace, liveness, subject, and `_source_readable`, then returns `PageRecord` objects.

**Call relations**: It is the page listing counterpart to `sources`, with visibility checks applied before data leaves the database.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 2315–2331)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten by tombstoning it. Downstream page-change processing can then remove derived index data.

**Data flow**: A page id goes in. The method updates the page only if it is live in this workspace; if no row was updated, it raises an error.

**Call relations**: It is the per-page cleanup path, similar in effect to source removal but limited to one page.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 2333–2370)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a live source and tombstones its live pages in one transaction. The source row stays as a historical reference but is no longer synced.

**Data flow**: A source id goes in. The method marks the source removed, clears sync claims, deletes grants, tombstones pages for that source, and raises if no live source matched.

**Call relations**: It works with the sync and page-change pipelines: the sync driver stops claiming the source, and tombstoned pages cause derived index state to be cleaned.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 2372–2398)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for live sources and their live pages together. This keeps source visibility and page visibility from disagreeing.

**Data flow**: Source ids and a new subject go in. The method updates matching live sources, raises if none matched, and restamps live pages with the new subject and update time.

**Call relations**: Page-change replay sees the updated pages and can re-index them under the new subject.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.rewindow_sources`  (lines 2400–2473)

```
async def rewindow_sources(self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID]=frozenset()) -> None
```

**Purpose**: Updates non-identity configuration fields for live sources, optionally forcing some to refetch from scratch. It refuses changes that would make the row’s id no longer match its identity.

**Data flow**: A map of source ids to new configs and an optional refetch set go in. The method validates every source is live here, recomputes each id from the new config, updates config fields, and clears cursor/claim state for refetched sources.

**Call relations**: It uses the same source-id derivation as registration to protect source identity. It prepares rows for the sync driver to pick up changed windows safely.

*Call graph*: 5 external calls (now, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.schedule_source_sync`  (lines 2475–2492)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Requests an immediate sync for live sources by moving their next-sync time to now. It is the approved way to ask the sync driver to rescan sources.

**Data flow**: Source ids go in. The method updates matching live sources in this workspace and raises if none matched.

**Call relations**: The sync driver later notices the updated schedule and claims the source, while its lease logic still prevents concurrent syncs of the same source.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 2494–2500)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Creates a governed proposal to change an agent prompt instead of writing the change directly. This keeps agent changes reviewable and safe.

**Data flow**: An `AgentChange` goes in. The method creates a `Governance` object for this workspace and extension, submits the change, and returns a proposal reference.

**Call relations**: The sample extension calls this during its tick. Approval and compare-and-swap application happen in the governance layer, not here.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 2502–2507)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns this workspace’s trajectory corpus through the context. It fails clearly if transcript reading was not wired for this handler.

**Data flow**: No explicit input goes in. The method checks that `corpus` exists, calls its `trajectories` method, and returns the trajectory tuple.

**Call relations**: The sample extension calls this during its tick. It is a safe front door to `TrajectoryCorpus`.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 2510–2574)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: WorkspaceBlobStore | None=None, sandboxes:
```

**Purpose**: Builds the `ExtensionContext` object handed to an extension or core job. It wires only the capabilities that this handler is supposed to have.

**Data flow**: Extension name, declared credential slots, optional services, model resolver, surfaces, audience, and permission flags go in. The function validates model attribution, constructs scoped helper objects, leaves unavailable capabilities as `None`, and returns an `ExtensionContext`.

**Call relations**: This is the factory that makes core jobs and extensions receive the same shaped context. It connects stores, credentials, model access, transcripts, files, probes, tailing, and member context without exposing raw global services.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `core/src/ufo/ext/manifest.py`

`data_model` · `startup and extension loading`

Think of this file as the product catalog form that every extension fills out. The core system does not ask extensions to directly register themselves by running setup code all over the application. Instead, an extension returns a frozen Manifest object, and the loader reads that object to decide what to install into the running system.

Most of the file is made of small frozen data classes. A data class is a simple named bundle of fields. “Frozen” means it is meant to be read as a fixed declaration, not changed later. For example, a CredentialSlot describes a secret an extension needs, a JobSpec describes background work, a RouteSpec describes an HTTP endpoint, and a HookSpec describes code that reacts to lifecycle events such as a tool being used or a user submitting a prompt.

This matters because extensions touch sensitive and central parts of the system: credentials, network access, agent tools, browser backends, memory search, and scheduled jobs. Without a clear manifest format, core would not know what an extension contributes, how to validate it, or how to fail early when two extensions claim the same global space.

The few functions near the end perform shared validation and collection. They turn many manifests into safe global views, such as all declared credential slots or the single allowed open connector namespace.

#### Function details

##### `AgentProvision.__post_init__`  (lines 531–543)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that an extension-provided agent is valid as soon as it is created. It protects the system from shipping an agent with an invalid name, no instructions, or setup requirements it cannot actually perform.

**Data flow**: It starts with the fields already placed on the AgentProvision object: the agent name, its specification, its allowed tools, and any setup it says it needs. It checks the name against the allowed object-name pattern, checks that the agent has a non-empty prompt, and then checks whether an agent that must set up connector access has the basic tools needed to do that. If something is wrong, it raises an error immediately. If everything is valid, it changes nothing and the object remains available for the loader to use.

**Call relations**: This runs automatically when an AgentProvision is constructed, before the manifest is accepted by the rest of the system. Later startup code can trust that any provisioned agent has a usable name and prompt, and that an agent asked to obtain its own access grants has the tool verbs needed to do so.


##### `conversation_slot_declarations`  (lines 676–705)

```
def conversation_slot_declarations(manifests: tuple[Manifest, ...]) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]
```

**Purpose**: This gathers all conversation-slot providers declared by active manifests and checks that they form one clean global namespace. A conversation slot is a typed piece of extra conversation data that an extension can read and summarize for the UI or agent context.

**Data flow**: It receives the active manifests. For each manifest, it looks through its conversation slot providers and validates each provider’s id, label, icon, callbacks, and payload type. It also remembers which extension owns each id, so two extensions cannot accidentally claim the same slot. If any declaration is invalid or duplicated, it raises an error. Otherwise, it returns a tuple of pairs, each pairing the owning manifest with its provider.

**Call relations**: The loader or startup assembly calls this when it needs the deploy-wide list of conversation slots. The function does not run the providers themselves; it only proves that the declarations are safe and unambiguous before later UI or conversation code relies on them.


##### `open_connector_namespace`  (lines 708–720)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: This finds the one optional catch-all connector namespace declared by installed extensions. It prevents ambiguity when a connector provider was not registered one by one but can still be resolved by a broader broker.

**Data flow**: It receives the active manifests and scans for a manifest with a connector_resolver. If it finds none, it returns None. If it finds exactly one, it returns that resolver. If it finds more than one, it raises an error because the system would not know which namespace should own an unregistered connector name.

**Call relations**: Startup code uses this when building connector and OAuth resolution. Later connect flows and connector registries can then ask this single resolver about provider slugs that were not explicitly declared. The function’s early check keeps those later paths from making inconsistent routing choices.


##### `declared_slots`  (lines 741–754)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: This turns extension credential declarations into the simpler credential-slot records used by shared parts of the system, such as the credential object type and the portal credentials panel. In plain terms, it builds the list of secrets the workspace may need to provide.

**Data flow**: It receives the active manifests. It walks through every CredentialSlot declared by every manifest and creates a DeclaredSlot record containing the slot name, description, owning extension, whether a member is allowed to fill it, and the target host if the credential is injected onto network requests. It returns all of those records as one tuple and does not modify the manifests.

**Call relations**: Code that needs a deploy-wide view of credentials calls this instead of separately inspecting each manifest. Inside the transformation it creates DeclaredSlot objects, handing off the manifest and slot information in the smaller shape expected by credential-facing features.

*Call graph*: 1 external calls (__init__).


### Public SDK Facades
Re-exports stable extension-author APIs for runtime context, manifest definitions, and tool integration.

### `core/src/ufo/sdk/context.py`

`other` · `cross-cutting import-time SDK access`

This file does not create new behavior of its own. Instead, it acts like a clearly labeled front desk for the SDK: extension code can import context-related tools from `ufo.sdk.context` without needing to know where each tool lives inside the larger project.

That matters because the internal layout of a codebase can change over time. If extension authors imported directly from many internal modules, even a small reorganization could break their code. By re-exporting the useful public names here, the project gives outsiders a simpler and more stable path.

The names exposed here cover several ideas: the current agent identity, credential access and credential errors, conversation facts, page and source records, scheduled member turns, trajectory information, model access, surface installation access, and record types such as agent changes and proposal references. In plain terms, these are the objects an extension handler may need in order to understand “who is acting,” “what conversation or page are we in,” “what data can I read or write,” and “what happened or should happen next.”

The repeated `as Name` imports are intentional public re-exports. They make it explicit that these imported names are meant to be available from this module.


### `core/src/ufo/sdk/manifest.py`

`data_model` · `import time and extension loading`

This file exists to keep the project’s public extension API stable and easy to find. A manifest is the structured description of what an extension provides: agents, tools, hooks, prompts, credentials, conversation slots, and related limits. The real definitions live in several internal modules, but extension authors should not have to know that internal layout. Instead, this file gathers the approved names into one public place.

It works like a front desk in a large building. Visitors ask at the desk for the thing they need, rather than wandering through private offices. Here, the “visitors” are extensions, and the “front desk” is `ufo.sdk.manifest`.

The file does not create new behavior. It imports and immediately re-exports selected classes, constants, and functions under the same names. That includes manifest schema objects, hook payload and outcome types, credential and agent setup types, conversation slot payload types, image preview limits, and workspace change types. The repeated `as SameName` style makes the re-export explicit, which helps tools and readers see that these names are intentionally part of the public surface.

Without this file, extension code would likely import from internal modules directly. That would make extensions more fragile, because an internal refactor could break them even if the public API was meant to stay the same.


### `core/src/ufo/sdk/tools.py`

`other` · `cross-cutting; active when extensions or tool handlers import the public SDK`

This file does not implement new behavior. Its job is to give outside code a stable and friendly import path for tool-related building blocks. In plain terms, it is like a front desk: instead of sending extension authors through many back-office rooms, it gathers the official names they are allowed to use in one place.

The module exposes types used when writing tool handlers, such as text or image content, a tool context, and a tool result. It also exposes tool registration pieces, file-change limits, task-running helpers, and timeout-related constants. One important export is `run_task`, which lets long-running commands continue in a detached task journal. That matters because a command may take longer than the caller can wait, but the system still needs a consistent way to track it and report back using the same task handles.

The top comment explains a project rule: package `__init__.py` files are kept empty, so public API names live in explicit modules like this one. Without this file, extensions would either have to import from internal locations that may change, or duplicate knowledge about where each tool concept lives.
