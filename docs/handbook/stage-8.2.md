# Skill and per-turn toolbox loading  `stage-8.2`

This stage prepares the agent’s working kit for a single turn or job. It is shared behind-the-scenes support that happens before tools or extensions run. First, the skill runtime defines a “skill” as a folder of packaged instructions or code. It can read those folders, list what is available, and copy only the selected skill into the sandbox, which is the agent’s isolated work area. Next, the tool registry acts like a menu. It records each tool’s name, description, expected input, how to run it, and any safety labels. The tool context then gives each tool a controlled doorway to the outside world: files, browser sessions, connected accounts, credentials, search, artifacts, subagents, and cleanup tasks, but only where allowed. Finally, the extension context builds a similar limited toolbox for extensions and background jobs. It keeps them inside the current workspace and exposes only approved storage, transcripts, models, sources, scheduling, and credential access. Together, these pieces let the agent work with useful abilities while keeping each action boxed in and workspace-safe.

## Files in this stage

### Extension toolbox assembly
Builds the constrained runtime environment for extensions and background jobs, including loading and copying selected skills into the sandbox.

### `core/src/ufo/ext/context.py`

`orchestration` · `extension/job execution and request handling`

Extensions need useful powers, but they must not get unrestricted access to the database, secrets, files, or other workspaces. This file solves that by giving each handler a carefully scoped context object, like handing a contractor a keycard that opens only the rooms needed for the job. The current workspace is not passed around directly; instead, each method reads the workspace already bound to the running turn or job. That reduces the chance that code accidentally reads or writes someone else’s data.

The main object is ExtensionContext. It contains smaller access objects: ScopedStore for an extension’s own key-value data, CredentialAccess for declared secret slots, TrajectoryCorpus for read-only conversation transcripts, ModelAccess for billed model calls, and source/page helpers for synced content. Some pieces are optional, so a context can be small for simple extensions and richer for jobs that need models, schedules, or transcripts.

The file also includes guardrails. Asking for an undeclared credential raises an error. Missing transcript blobs are skipped, while corrupt transcripts are logged and skipped. Model calls are metered through the same billing path as normal turns. Source registration is idempotent, meaning repeating the same setup reuses the same source instead of creating duplicates.

#### Function details

##### `ScopedStore.workspace_id`  (lines 70–71)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id that is currently bound to this running job or turn. This lets the store stay tied to the active workspace without accepting a workspace id from extension code.

**Data flow**: It reads the ambient workspace context through ws_current() → takes its workspace_id → returns that id without changing anything.

**Call relations**: The store methods call on this property whenever they read or write extension data, so every database query is anchored to the currently active workspace.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 73–84)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one saved JSON-like value from this extension’s private storage area. It is used when an extension wants to remember small durable state between runs.

**Data flow**: It receives a key → opens a workspace-scoped database transaction → looks for a row matching the current workspace, this extension name, and the key → returns the stored value, or None if no row exists.

**Call relations**: Extension handlers use this through ExtensionContext.store. Internally it relies on workspace_tx for the safe database scope and SQLAlchemy select to fetch the row.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 86–107)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Saves or replaces one JSON-like value in this extension’s private storage area. It gives extensions a simple persistent notebook for their own data.

**Data flow**: It receives a key and value → opens a workspace-scoped transaction → tries to update an existing row for this workspace, extension, and key → if nothing was updated, inserts a new row.

**Call relations**: Extension handlers use this through ExtensionContext.store. It hands the actual database work to workspace_tx plus SQLAlchemy update/insert statements.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `ScopedStore.delete`  (lines 109–117)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes one key from this extension’s private store. This is how an extension forgets state it no longer needs.

**Data flow**: It receives a key → opens a workspace-scoped transaction → deletes the row matching the current workspace, this extension, and the key → returns nothing.

**Call relations**: Extension code reaches this through ExtensionContext.store. The function depends on workspace_tx so deletion stays inside the active workspace.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 119–132)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists saved key-value pairs in this extension’s private store, optionally only those whose keys begin with a given prefix. This is useful when an extension stores a group of related records under shared key names.

**Data flow**: It receives an optional prefix → opens a workspace-scoped transaction → selects matching rows for the current workspace and extension → returns an ordered tuple of key/value pairs.

**Call relations**: Extension handlers call this through ExtensionContext.store. It uses SQLAlchemy select inside workspace_tx to keep the query scoped and predictable.

*Call graph*: 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 146–147)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the current workspace id for credential access. It confirms that credential reads are tied to the workspace currently running the handler.

**Data flow**: It reads the ambient workspace with ws_current() → extracts workspace_id → returns it.

**Call relations**: CredentialAccess.get and rotate use the same workspace context so secrets come from the active workspace rather than from a caller-supplied id.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 149–155)

```
async def get(self, slot: str) -> str
```

**Purpose**: Fetches the value of a credential slot only if the extension declared that slot in its manifest. This prevents an extension from guessing secret names and reading secrets it was not granted.

**Data flow**: It receives a slot name → checks whether the slot is in the declared set → if not, raises UndeclaredCredentialSlot → otherwise asks the current workspace to resolve the credential and returns the secret string.

**Call relations**: Handlers use this through ExtensionContext.credentials. It delegates the actual secret lookup to ws_current().credential after doing the declaration check.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.rotate`  (lines 157–162)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Updates a declared credential slot after an outside provider has rotated the secret, using a compare-and-swap check. Compare-and-swap means it only changes the stored value if the old value matches what the caller expected.

**Data flow**: It receives a slot name, expected old value, and new plaintext value → verifies the slot was declared → asks the current workspace to rotate the credential → returns True or False depending on whether the rotation succeeded.

**Call relations**: Handlers use this through ExtensionContext.credentials. Like get, it first enforces the manifest declaration, then hands the real secret update to the current workspace object.

*Call graph*: 2 external calls (__init__, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 195–196)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id whose transcripts this corpus is allowed to read. It keeps transcript reads bound to the current workspace.

**Data flow**: It reads ws_current() → takes the workspace_id → returns it.

**Call relations**: TrajectoryCorpus.trajectories uses this property when it chooses which conversations to load.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 198–249)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Builds a read-only set of recent conversation transcripts for the current workspace. This gives evaluation or improvement jobs examples of past conversations without exposing arbitrary blob storage.

**Data flow**: It finds recent conversations with turns in the database → for each one, loads the transcript blob by its transcript key → decodes it into messages → attaches the agent id, prompt, and prompt digest → returns a tuple of Trajectory objects. Missing blobs are ignored, and corrupt transcripts are logged and skipped.

**Call relations**: ExtensionContext.trajectories calls this when a handler asks for the corpus. It uses workspace_tx for database reads, transcript_key and decode for transcript blobs, prompt_digest for the agent prompt fingerprint, and log when a transcript cannot be decoded.

*Call graph*: 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `trajectory_workspaces`  (lines 252–274)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a workspace candidate selector for jobs that need conversation trajectories. In plain terms, it says: run this job only for workspaces that have at least one conversation with at least one turn.

**Data flow**: It defines a database query builder → wraps that query builder with owner_candidates → returns the resulting WorkspaceCandidates object.

**Call relations**: Extensions can declare this selector when they need transcript-reading jobs. The inner with_a_turn query supplies the condition, while owner_candidates turns it into the dispatcher-facing candidate source.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 260–272)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query used by trajectory_workspaces. It selects workspace ids where there is at least one conversation that contains at least one turn.

**Data flow**: It starts from the workspace table → uses nested exists checks to test for a matching conversation and turn → returns a SQL select statement for matching workspace ids.

**Call relations**: This helper is passed to owner_candidates by trajectory_workspaces, so the job dispatcher can discover which workspaces have useful transcript data.

*Call graph*: 2 external calls (exists, select).


##### `TurnInvoker.invoke`  (lines 281–283)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Defines the shape of something that can start an internal agent turn from background code. It is a protocol, meaning it describes the required method without implementing it here.

**Data flow**: An implementation receives a conversation id, agent id, message, and idempotency key → starts or reuses the matching turn → returns the resulting turn id.

**Call relations**: ExtensionContext.invoke calls whichever object implements this protocol when an extension needs to trigger an internal turn.


##### `ModelResolver.auto_model`  (lines 293–293)

```
def auto_model(self) -> str
```

**Purpose**: Names the default model that background model access should use. It is part of the protocol that lets this file avoid importing the full model registry directly.

**Data flow**: An implementation exposes a string property → callers read it → they use that model name for completion requests and billing.

**Call relations**: ModelAccess.model and ModelAccess.turn read this property so extension model calls use the deployment’s chosen default model.


##### `ModelResolver.pricing`  (lines 296–296)

```
def pricing(self) -> Pricing
```

**Purpose**: Provides the price table used to turn model token usage into billable usage. It is read-only from this context’s point of view.

**Data flow**: An implementation exposes Pricing data → ModelAccess reads it → billing code uses it when recording usage.

**Call relations**: ModelAccess.turn reads this property after the model stream finishes, so the usage it records can be priced correctly.


##### `ModelResolver.client_for`  (lines 298–298)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Returns the model client for a given model name. The client is the object that actually talks to the model provider, using the active workspace’s credentials when appropriate.

**Data flow**: It receives a model name → resolves the correct provider client for the current workspace → returns a ModelClient.

**Call relations**: ModelAccess.turn calls this before streaming a completion. The concrete resolver is supplied when context_for builds a context with model access.


##### `ModelResolver.key_slot_for`  (lines 300–300)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Tells which credential slot, if any, is used for a given model. This helps usage exports label whether usage was tied to a bring-your-own-key credential.

**Data flow**: It receives a model name → looks up the related credential slot → returns the slot name or None.

**Call relations**: context_for stores this function on ExtensionContext so pending_usage_exports can label exported usage correctly.


##### `ModelAccess.model`  (lines 316–318)

```
def model(self) -> str
```

**Purpose**: Returns the default model name that this model access object will call and bill. It makes the chosen model visible without letting handlers switch billing away from the actual model used.

**Data flow**: It reads auto_model from the resolver → returns that model name.

**Call relations**: Extension handlers can inspect this property, while complete and turn use the same resolver value internally for real model calls.


##### `ModelAccess.complete`  (lines 320–327)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one model completion and returns only the assistant’s text. It is the simple path for extensions that want text back and do not need to inspect tool calls.

**Data flow**: It receives a ModelRequest → calls ModelAccess.turn to perform the metered model interaction → if the response content is plain text, returns it → otherwise joins all text blocks and returns the combined text.

**Call relations**: Memory extension code calls this for fact extraction and summarization. It delegates the hard work, including streaming and billing, to ModelAccess.turn.

*Call graph*: calls 1 internal fn (turn); called by 2 (_extract, _summarize).


##### `ModelAccess.turn`  (lines 329–377)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one full model turn, including streamed text, optional tool calls, and billing. It returns an assistant Message in the same shape used by normal conversations.

**Data flow**: It receives a ModelRequest → replaces its model with the deployment default → gets the correct ModelClient → streams events from the provider → collects text pieces, tool-call JSON, and usage records → records combined usage in a billable workspace event → returns an assistant Message with text and any tool-use blocks.

**Call relations**: ModelAccess.complete calls this for text-only use, and the knowledge graph extension calls it when it needs tool-aware model output. It uses ws_current().billable_event so the same workspace that provides credentials is also billed.

*Call graph*: called by 2 (complete, _tier_b); 7 external calls (__init__, __init__, __init__, __init__, model_copy, loads, ws_current).


##### `ExtensionContext.pending_usage_exports`  (lines 428–450)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns settled usage records that this extension has not yet acknowledged as exported. It first freezes new export intents so repeated reads can safely deliver the same records until they are acknowledged.

**Data flow**: It receives a time floor and a limit → checks that model key-slot labeling is wired → opens a workspace transaction → mints export rows for eligible usage → reads up to the requested number of pending exports → returns them.

**Call relations**: Billing export extensions call this when sending usage to an outside system. It delegates ledger details to mint_usage_exports and read_pending_usage_exports inside workspace_tx.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 452–461)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered after an outside receiver has accepted them. This prevents successfully delivered records from being sent again.

**Data flow**: It receives a tuple of UsageExport records → if the tuple is empty, does nothing → otherwise opens a workspace transaction → records acknowledgements for this workspace and extension.

**Call relations**: A usage exporter calls this only after delivery succeeds. It hands the database update to ack_usage_exports inside workspace_tx.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 464–476)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Gives an extension a database transaction for its own tables and approved SDK operations. This is powerful because it is a raw database connection, so the extension is responsible for scoping its own queries correctly.

**Data flow**: It opens workspace_tx → yields the database connection to the caller → commits when the caller exits normally or rolls back if an error happens.

**Call relations**: Extension handlers use this when their own schema needs SQL work. It uses the same workspace transaction mechanism as the scoped store, but it does not itself restrict which tables the SQL can touch.

*Call graph*: 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 478–485)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Starts an internal agent turn from an extension or background job. It fails clearly if no turn invoker was connected when the context was built.

**Data flow**: It receives conversation id, agent id, message, and idempotency key → checks that an invoker exists → forwards the request to that invoker → returns the resulting turn id.

**Call relations**: Handlers call this through their context. The actual turn-starting behavior is supplied by an implementation of TurnInvoker, not by this file.


##### `ExtensionContext.register_source`  (lines 487–549)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None) -> UUID
```

**Purpose**: Registers a content-sync source, such as an account or folder that should be periodically synced into the workspace. Re-registering the same backend and config reuses the same source instead of creating duplicates.

**Data flow**: It receives a backend name, typed config object, subject label, and optional owner member id → turns the config into JSON → computes a stable source id → checks whether that source already exists → returns it if live with the same subject, revives it if removed, or inserts a new source row.

**Call relations**: Sample and YC extension setup code call this during source onboarding. It uses source_row_id for stable identity and workspace_tx plus SQLAlchemy statements for the database changes.

*Call graph*: called by 2 (_setup, setup_sources); 7 external calls (now, model_dump, insert, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.sources`  (lines 551–585)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists the live registered content sources in the current workspace, optionally limited to one backend. Removed sources are left out.

**Data flow**: It receives an optional backend filter → builds a workspace-scoped query for live source rows → runs it in a transaction → converts each row into a SourceRecord → returns the records as a tuple.

**Call relations**: The sources extension uses this when turning registered sources into user-facing bindings. It reads from the same source table that register_source writes.

*Call graph*: called by 1 (_bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 587–624)

```
async def source_pages(self, subjects: frozenset[str] | None=None) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages in the current workspace, optionally limited to certain visibility subjects. It returns references to page bodies rather than loading the full body content.

**Data flow**: It receives an optional set of subject labels → queries non-tombstoned pages for the workspace, adding the subject filter if present → converts rows into PageRecord objects → returns them.

**Call relations**: Extensions use this as the approved way to inspect synced pages. It pairs with forget_page, remove_source, and set_source_subject, which change page visibility or lifecycle state.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 626–642)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten by setting its tombstone flag. A tombstone is a marker saying the page should no longer be treated as active, which lets downstream indexing cleanup remove derived data.

**Data flow**: It receives a page id → records the current time → updates the matching live page in the current workspace to tombstoned → if no row was changed, raises an error.

**Call relations**: Extensions call this after reading pages through source_pages when a particular page should be removed. It uses workspace_tx and a SQL update to make the lifecycle change.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 644–675)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a registered source and tombstones all of its live pages in the same transaction. This stops future sync claims while preserving the source row as the historical parent of existing pages.

**Data flow**: It receives a source id → records the current time → marks the live source as removed and unclaimed → if no live source matched, raises an error → tombstones all live pages from that source.

**Call relations**: Extensions call this when a connected source is disconnected or should stop syncing. The page tombstones feed the normal page-change cleanup path.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 677–703)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the visibility subject for one or more live sources and restamps their live pages to match. This is how a source can move from one disclosure group to another without partial updates.

**Data flow**: It receives source ids and a new subject → records the current time → updates matching live source rows in this workspace → if none matched, raises an error → updates live pages from those sources with the new subject and timestamp.

**Call relations**: Extensions use this when a source binding’s audience changes. Updating pages at the same time causes downstream page-change replay to re-index them under the new subject.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 705–711)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Creates a governed proposal to change an agent prompt instead of editing the agent directly. Governance means the change must go through an approval/checking path before it is applied.

**Data flow**: It receives an AgentChange → builds a Governance object for the current workspace and extension → submits the change → returns a ProposalRef identifying the proposal.

**Call relations**: The sample extension calls this during its tick flow. This function hands the actual proposal work to Governance.propose_change while stamping the extension as the proposer.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 713–718)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns the current workspace’s transcript corpus for handlers that were given corpus access. It raises an error if no corpus was wired, so a missing permission or setup problem is not mistaken for an empty dataset.

**Data flow**: It checks whether self.corpus exists → if not, raises RuntimeError → otherwise calls the corpus object’s trajectories method → returns the resulting Trajectory tuple.

**Call relations**: The sample extension calls this when it wants to inspect conversation history. It delegates the actual database and blob reads to TrajectoryCorpus.trajectories.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 721–750)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: BlobStore | None=None, invoker: TurnInvoke
```

**Purpose**: Builds the ExtensionContext object handed to an extension or core job. It assembles only the capabilities that were declared or wired for that run.

**Data flow**: It receives the extension name, declared credential slots, optional backends, optional blob store, optional invoker, optional model resolver, schedule invoker, and declared surfaces → constructs the store, credential gate, installation gate, optional corpus, scheduler, and model access → returns one ExtensionContext.

**Call relations**: Dispatcher or setup code calls this before running a handler. It is the central assembly point that connects this file’s smaller access objects to the rest of the system without exposing raw global resources.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `core/src/ufo/skills/runtime.py`

`domain_logic` · `startup and skill loading`

A skill is a small folder of instructions and supporting files. Its main file, SKILL.md, starts with a YAML frontmatter block, which is a machine-readable header, followed by normal Markdown instructions for the agent. This file turns those folders into RuntimeSkill objects, checks that they are well formed, and builds a SkillRegistry so the rest of the system can ask for skills by name.

The file also understands nested skills. If a skill folder contains a child folder with its own SKILL.md, that child becomes a separate skill named like parent/child. Loading the child also loads the parent first, so shared files from the parent come along. A skill can also declare dependencies, meaning other skills that should be loaded with it.

At startup, the built-in core skills are discovered from disk. Later, registries can be combined with user-saved skills, while refusing user skills that try to replace built-in or pack-provided ones. When a skill is actually loaded for a conversation, mount_skill writes its SKILL.md and asset files into .skills/<name>/ inside the sandbox workspace. In plain terms, this file is the librarian and copier for skills: it catalogs them, follows their “bring these too” rules, and places their instructions where the agent is allowed to read them.

#### Function details

##### `RuntimeSkill.mounted_files`  (lines 49–50)

```
def mounted_files(self) -> dict[str, bytes]
```

**Purpose**: Returns the complete set of files that should be placed into the sandbox for this skill. It includes the original SKILL.md plus any extra bundled files.

**Data flow**: It reads the skill’s stored raw SKILL.md text and its list of asset files. It turns SKILL.md into bytes, combines it with the asset files, and returns a dictionary from file path to file contents. It does not change the skill.

**Call relations**: When mount_skill is ready to copy a skill into the sandbox, it asks this method for the exact files to write. This keeps the mounting step simple: it does not need to know how a skill stores its own files.

*Call graph*: called by 1 (mount_skill).


##### `RuntimeSkill.mount_root`  (lines 52–53)

```
def mount_root(self) -> str
```

**Purpose**: Builds the sandbox folder path where this skill should appear. This gives every skill a predictable home under the workspace’s .skills directory.

**Data flow**: It reads the skill’s name and joins it with the standard skills mount directory. The result is a string such as a folder path for that skill. Nothing is written or changed.

**Call relations**: RuntimeSkill.prompt_body uses this path when telling the agent where bundled files will be found. mount_skill uses the same path when actually writing those files, so the prompt and the filesystem agree.

*Call graph*: called by 2 (prompt_body, mount_skill).


##### `RuntimeSkill.prompt_body`  (lines 55–60)

```
def prompt_body(self) -> str
```

**Purpose**: Creates the text shown to the agent when a skill is loaded. It combines the skill name, description, instructions, and, if present, a list of bundled files.

**Data flow**: It reads the skill’s name, description, Markdown instructions, and asset list. If assets exist, it asks mount_root where the skill will be mounted and writes file references using that path. It returns one readable text block for the model.

**Call relations**: This method is the bridge between the stored skill data and the agent-facing instruction text. It relies on RuntimeSkill.mount_root so that any file paths mentioned to the agent match where mount_skill will later place the files.

*Call graph*: calls 1 internal fn (mount_root).


##### `_split_frontmatter`  (lines 63–69)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates the machine-readable header from the human-readable instructions in SKILL.md. It also rejects files that do not use the required frontmatter format.

**Data flow**: It receives the full text of a SKILL.md file. It checks that the file starts with the expected opening line, finds the matching closing line, and returns two pieces: the YAML metadata text and the Markdown body. If the format is missing or unfinished, it raises an error.

**Call relations**: parse_skill_content calls this before trying to understand a skill. This function acts like checking that a form has the required header section before the rest of the system trusts what is inside.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 72–77)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds the immediate subfolders inside a skill folder that are themselves separate child skills. A child skill is recognized by having its own SKILL.md file.

**Data flow**: It receives a folder path, looks only at its direct children, keeps the children that are directories containing SKILL.md, sorts them, and returns those paths. It does not inspect every nested file deeply.

**Call relations**: parse_skill uses this to avoid treating child skill files as part of the parent’s bundled assets. discover_skills uses it to recursively register each child skill under the parent’s name.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 80–113)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Builds a RuntimeSkill from files already loaded in memory. It validates the SKILL.md, reads its metadata, gathers assets, and produces the project’s standard skill object.

**Data flow**: It receives the claimed folder name, a mapping of file paths to bytes, and optional registry and parent names. It finds SKILL.md, decodes it, splits its frontmatter from its body, parses the YAML metadata, checks that the frontmatter name matches the folder name, collects non-SKILL.md files as assets, and returns a RuntimeSkill. Bad or missing data becomes a clear error.

**Call relations**: parse_skill calls this after reading files from disk. Because this function works from bytes rather than directly from the filesystem, the same validation can also be used for skills that came from another source, such as a sandbox or saved record.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 116–125)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads a skill folder from disk and turns it into a RuntimeSkill. It deliberately leaves out any nested child skill folders, because those are registered separately.

**Data flow**: It receives a filesystem path for one skill folder. It first finds child skill directories, then reads all regular files under the folder except files belonging to those child skill directories. It passes the collected file bytes to parse_skill_content and returns the resulting RuntimeSkill.

**Call relations**: discover_skills uses this for each parent or child folder it discovers. parse_skill is the disk-reading wrapper around parse_skill_content’s validation and object creation.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 128–146)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Finds one skill and all of its nested child skills, then returns them in a flat lookup table by registry name. This makes nested folders easy to load by name later.

**Data flow**: It receives a skill folder and optional naming context. It parses that folder as a skill, stores it under its registry name, then looks for immediate child skill folders. For each child, it calls itself again using a path-like name such as parent/child and records the parent relationship. It returns a dictionary of all discovered skills.

**Call relations**: _load_core_skills uses this to collect all built-in skills. Inside its own recursion, discover_skills calls parse_skill for each folder and _child_skill_dirs to find the next level of child skills.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 149–156)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the built-in skills that ship with the core package. These become available before any pack or user-provided skills are added.

**Data flow**: It receives the root folder that contains core skill folders. It lists visible, normal-looking subdirectories, discovers skills in each one, and merges the results into one dictionary keyed by skill name. The output becomes the core skill collection used to build the default registry.

**Call relations**: This runs while the module is being imported, so core skills are cataloged early in startup. It hands each skill directory to discover_skills, which handles parsing and nested children.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.named`  (lines 174–179)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up a skill by name and gives a helpful error if it is not available. This is the safe doorway for turning a requested skill name into the actual RuntimeSkill object.

**Data flow**: It receives a skill name and checks the registry’s by_name dictionary. If the name exists, it returns the matching RuntimeSkill. If not, it builds an error message listing available names and raises a ValueError.

**Call relations**: SkillRegistry.tree and its inner add step call this whenever they need to resolve a parent or dependency name. That keeps missing-skill failures consistent and understandable.

*Call graph*: called by 2 (tree, add).


##### `SkillRegistry.tree`  (lines 181–202)

```
def tree(self, name: str) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Calculates the full set of skills that must be loaded when one skill is requested. It includes parents first, then dependencies, then the requested skill, without loading the same skill twice.

**Data flow**: It receives the requested skill name. It creates an ordered collection of loaded skills and a temporary visiting set to spot dependency loops. It resolves the requested skill, walks through parents and dependencies, skips anything already loaded or currently being visited, and returns the final ordered tuple of RuntimeSkill objects.

**Call relations**: Code that wants to load a skill can call this to get the whole bundle to mount. It uses SkillRegistry.named to resolve names, and its inner SkillRegistry.tree.add function does the recursive walking.

*Call graph*: calls 1 internal fn (named).


##### `SkillRegistry.tree.add`  (lines 191–199)

```
def add(skill: RuntimeSkill) -> None
```

**Purpose**: Adds one skill and everything it requires to the tree being built. It is careful not to get stuck if skills depend on each other in a circle.

**Data flow**: It receives a RuntimeSkill. If that skill is already loaded or is currently being explored, it stops. Otherwise, it marks the skill as being explored, adds the parent’s required skills first, then each dependency’s required skills, and finally records the skill itself as loaded.

**Call relations**: This is the working step inside SkillRegistry.tree. It calls SkillRegistry.named when a parent or dependency is named as text and needs to be turned into a RuntimeSkill.

*Call graph*: calls 1 internal fn (named).


##### `SkillRegistry.index`  (lines 204–212)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Creates the public list of skills that should appear in the system prompt’s skill index. It includes only top-level skills, not child skills.

**Data flow**: It reads the registry in registration order. For each skill with no parent, it takes the skill name and description and returns them as a tuple of pairs. It leaves out child skills because those are meant to be discovered through their parent’s instructions.

**Call relations**: This supports the part of the system prompt that tells the agent what skills can be loaded directly. It does not call other project functions because it only filters and formats registry data.


##### `SkillRegistry.merged_with`  (lines 214–226)

```
def merged_with(self, user_skills: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that includes user-saved skills after the base core and pack skills. It protects built-in and pack skills from being overwritten by user-controlled skill names.

**Data flow**: It copies the current registry’s name-to-skill dictionary. Then it checks each user skill: if its name is already taken, it logs that the shadowing attempt was refused and skips it; otherwise, it adds the user skill. It returns a new SkillRegistry containing the merged result.

**Call relations**: This is used when a workspace’s saved user skills need to be made available alongside the normal registry. It logs refused collisions through the observability logger, then constructs a fresh SkillRegistry so callers get an updated but safe view.

*Call graph*: 2 external calls (__init__, log).


##### `mount_skill`  (lines 232–235)

```
async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None
```

**Purpose**: Copies a skill’s files into the sandbox workspace so the agent can read them during a conversation. The sandbox is the controlled file area where the agent is allowed to work.

**Data flow**: It receives a SandboxSession and a RuntimeSkill. It asks the skill for its mount root, asks for all files that should be mounted, and writes each file’s bytes into the sandbox under that root path. The result is side effect only: files appear in the workspace.

**Call relations**: After a caller has chosen a skill, usually using SkillRegistry.tree to include parents and dependencies, this function performs the actual file placement. It relies on RuntimeSkill.mount_root for the destination folder, RuntimeSkill.mounted_files for the contents, and SandboxSession.write_file for the sandbox write operation.

*Call graph*: calls 3 internal fn (write_file, mount_root, mounted_files).


### Tool access boundary
Defines the safe per-tool context and the registry of callable tools available to the agent.

### `core/src/ufo/tools/context.py`

`domain_logic` · `tool execution and turn cleanup`

A tool in this system is not allowed to freely reach into everything the server knows. Instead, it gets a ToolContext: a carefully packed bag of only the powers it needs for this turn. Think of it like a visitor badge. The badge does not just identify the visitor; it also says which doors they can open.

The file also defines the shapes of tool results. A tool can return text or an image, and it can mark the result as an error or as untrusted content, meaning the system should treat it as outside data rather than instructions for the model.

For longer work, the context can spawn subagents. A subagent is a child task with a named profile and typed input and output. Background subagents can later be waited on, cancelled, or messaged.

ToolContext also contains important safety checks. It decides which member the turn is acting for, whether the speaker is the workspace owner, whether an extension may request or store a credential, and which connected external accounts a tool may use. Private accounts stay private unless explicitly shared.

Finally, TurnCleanup gives tools a per-turn place to register resources that must be closed, such as browser connections. At the end of the turn, cleanup runs in reverse order so temporary resources do not leak.

#### Function details

##### `Spawn.__call__`  (lines 120–126)

```
async def __call__(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: This is the interface for starting a child subagent task. A tool uses it when a job is big enough or specialized enough to delegate to another configured agent profile.

**Data flow**: It receives the subagent profile name, a payload of input data, a flag saying whether to run in the background, and an optional deduplication key. The implementation, supplied elsewhere, validates the payload, starts or reconnects to the child turn, and returns a SpawnResult containing the child turn id and, for foreground work, the validated output.

**Call relations**: No direct call facts are listed here because this file only defines the contract. Tool code receives a Spawn through ToolContext and calls it when it wants the subagent workflow outside this file to create or resume a child turn.


##### `SubagentControl.wait`  (lines 135–135)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: This is the interface for waiting until one or more background subagents finish. A tool uses it after previously spawning child work in the background.

**Data flow**: It receives the ids of child turns to wait for. The implementation, supplied elsewhere, watches those child turns until they reach a final state and returns one SubagentStatus for each, including its status and final text.

**Call relations**: This protocol method is part of the subagent control surface placed on ToolContext. The actual waiting is done by the subagent workflow outside this file.


##### `SubagentControl.cancel`  (lines 137–137)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: This is the interface for cancelling a background subagent that is still running. A tool uses it when child work is no longer needed or should be stopped.

**Data flow**: It receives one child turn id. The implementation, supplied elsewhere, asks that child turn to stop and returns a SubagentStatus describing how it ended.

**Call relations**: This file only defines the method a controller must provide. Tool handlers call it through ToolContext.subagents when they need lifecycle control over already spawned child turns.


##### `SubagentControl.message`  (lines 139–139)

```
async def message(self, turn_id: UUID, text: str) -> SubagentStatus
```

**Purpose**: This is the interface for sending a follow-up message to an already spawned background subagent. It lets a parent tool continue a child task instead of starting over.

**Data flow**: It receives the child turn id and the text to send. The implementation, supplied elsewhere, schedules that message as the child’s next turn and returns the resulting SubagentStatus.

**Call relations**: The subagent workflow implements this behavior. This context file exposes the shape of the operation so tools can use it without knowing the lower-level scheduling details.


##### `TurnCleanup.register`  (lines 153–154)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: This records a cleanup action that should run when the current turn ends. Tools use it after opening something that must later be closed, such as a browser connection.

**Data flow**: It receives an async close function. It appends that function to the turn’s private cleanup list and returns nothing; the only change is that the close function is now waiting to be run later.

**Call relations**: Tools register close actions during a turn. Later, the turn loop calls TurnCleanup.drain, which consumes the registered actions and actually closes the resources.


##### `TurnCleanup.drain`  (lines 156–162)

```
async def drain(self) -> None
```

**Purpose**: This runs all registered cleanup actions for a turn. It protects the system from leaked connections or leases when a turn finishes, fails, or is cancelled.

**Data flow**: It reads the stored list of async close functions. It pops them one by one in reverse order, awaits each close, and logs any exception instead of letting one failed cleanup stop the rest.

**Call relations**: This is the second half of the cleanup story started by TurnCleanup.register. When the turn is ending, the surrounding loop drains this registry; if a close action fails, it hands the failure details to the logging system through ufo.o11y.log.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 191–204)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: This chooses which workspace member the turn is acting for when using private resources. It matters because connected accounts and other private capabilities should follow the right human, even in scheduled jobs or delegated subagents.

**Data flow**: It reads speaker_member_id first. If there is a current speaker, that member is returned; otherwise it falls back to on_behalf_of_member_id. If neither exists, it returns None, meaning the turn has no private member identity.

**Call relations**: Other ToolContext methods, especially connector_accounts, use this property to decide which private grants are visible. It is a small but important identity rule shared across tool authorization.


##### `ToolContext.speaker_is_owner`  (lines 206–215)

```
async def speaker_is_owner(self) -> bool
```

**Purpose**: This checks whether the current speaking member is the workspace owner. It is used to guard workspace-wide actions that should not be available to ordinary joined members.

**Data flow**: It first returns false if there is no speaking member. Otherwise it opens a workspace database transaction, asks for the owner member id of the current workspace, and compares that id with speaker_member_id.

**Call relations**: Several object and credential flows call this before allowing sensitive operations, including agent object changes, member-owned object access, credential deletion, and built-in credential-request tooling. Inside this function, the database transaction comes from workspace_tx and the owner lookup comes from owner_member_id.

*Call graph*: called by 14 (apply, delete, apply, delete, get, list, status, request_credentials_handler, _credential_authorization, _owner_seats (+4 more)); 2 external calls (workspace_tx, owner_member_id).


##### `ToolContext.begin_credential_authorization`  (lines 217–219)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: This starts the process of authorizing an extension credential slot, such as beginning an OAuth-style connection flow. It only works after the shared credential safety checks pass.

**Data flow**: It receives a credential slot name and a payload string. It asks _credential_authorization to verify the speaker, audience, extension declaration, configured secret storage, and owner permission; then it calls the credential request service to create an authorization token or link string.

**Call relations**: The Slack extension’s OAuth link flow calls this when it needs to begin authorization. This method delegates all permission checking to _credential_authorization, then hands the approved request to the configured CredentialRequests object.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 1 (_oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 221–223)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: This reopens or verifies an existing sealed credential authorization request. It is used when the system needs to inspect or continue an authorization that was previously started.

**Data flow**: It receives the credential slot and a sealed authorization string. It runs the same _credential_authorization checks, then asks the credential request service to open the sealed authorization for this workspace, member, and slot.

**Call relations**: No external caller is listed in the call facts, but within this file it follows the same pattern as beginning and fulfilling credential authorization. It relies on _credential_authorization to make sure only the right owner, in the right private audience, for a declared extension slot, can proceed.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext.fulfill_credential_authorization`  (lines 225–230)

```
async def fulfill_credential_authorization(self, slot: str, sealed: str, plaintext: str) -> None
```

**Purpose**: This completes a credential authorization by storing the provided secret value. It is the step that turns an approved request into an actual saved credential.

**Data flow**: It receives the slot name, sealed authorization string, and plaintext secret. It verifies permission through _credential_authorization, opens the sealed authorization to confirm it matches, then writes the plaintext credential into the current workspace store.

**Call relations**: This method combines the credential request system with workspace storage. It calls _credential_authorization for safety, then uses ws_current to reach the active workspace and save the credential.

*Call graph*: calls 1 internal fn (_credential_authorization); 1 external calls (ws_current).


##### `ToolContext._credential_authorization`  (lines 232–243)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: This is the shared gatekeeper for credential authorization. It prevents extensions from requesting undeclared secrets, prevents non-owners from filling workspace-wide credential slots, and prevents credential work outside the speaker’s private audience.

**Data flow**: It reads the current speaker, audience, extension context, requestable credential service, and workspace ownership. If any required condition is missing or wrong, it raises a clear error. If everything is valid, it returns the credential request service and the speaker’s member id.

**Call relations**: begin_credential_authorization, open_credential_authorization, and fulfill_credential_authorization all call this before doing their specific work. It calls speaker_is_owner as the final ownership check.

*Call graph*: calls 1 internal fn (speaker_is_owner); called by 3 (begin_credential_authorization, fulfill_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 245–270)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: This chooses the exact connected external account a connector tool should use. It protects against accidentally using the wrong account when none, one, or several accounts are available.

**Data flow**: It receives a provider name, such as a connector service, and optionally a specific account id. It asks connector_accounts for all accounts this turn may use for that provider. If an account id was requested, it returns it only if available; otherwise it requires that exactly one account be available and returns that account id.

**Call relations**: Connector extension tools call this before asking the external broker to execute work. This method builds on connector_accounts, turning the list of allowed accounts into one unambiguous account id or a clear error the caller can surface.

*Call graph*: calls 1 internal fn (connector_accounts); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_accounts`  (lines 272–292)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: This lists the connected account ids that the current turn is allowed to use for one provider. It is the runtime rule that keeps connections private unless they were shared.

**Data flow**: It receives a provider name. It requires a configured GrantStore, figures out the acting member, reads active grants for this workspace and agent, filters them to the requested provider, and keeps only grants that are shared or owned by the acting member. It returns the allowed account ids sorted as a tuple.

**Call relations**: connector_account calls this when it needs one usable account, and source extension code calls it when resolving an account. If no grant system is configured, it raises ConnectUnavailable to make clear that connector use is not available in this deployment.

*Call graph*: called by 2 (connector_account, _resolved_account); 1 external calls (__init__).


### `core/src/ufo/tools/registry.py`

`data_model` · `startup and tool dispatch`

An AI agent cannot safely call arbitrary code. It needs a clear, fixed list of tools, with names, descriptions, input rules, and the function that actually runs each tool. This file provides that list in a structured way.

`ToolDef` is one tool card. It stores the tool name, a human-readable description, the Pydantic input model (a Python class that checks and describes valid input), and the async handler function that performs the work. It also carries important safety flags. For example, `untrusted` means the tool may return attacker-controlled text, such as a web page or search result, so the engine should treat that result as data, not as instructions. `side_effecting` means the tool can write or send something outside the system, so the engine may add an idempotency key, which is like a receipt number used to avoid doing the same external action twice after a retry.

`ToolRegistry` is the frozen collection of these tool cards. At creation time it rejects duplicate tool names, because two tools with the same name would make dispatch ambiguous. Later, the engine can ask it for the schemas to show the model, or ask for one tool by name when the model requests a call.

#### Function details

##### `ToolDef.schema`  (lines 37–42)

```
def schema(self) -> ToolSchema
```

**Purpose**: Turns one internal tool definition into the public tool description that can be sent to the model client. This tells the model the tool name, what it is for, and what input format it must use.

**Data flow**: It reads the tool's stored name, description, and Pydantic input model. It asks the input model to produce a JSON schema, which is a standard machine-readable description of allowed input fields. It returns a `ToolSchema` object containing those three pieces.

**Call relations**: This is used by `ToolRegistry.schemas` when the system needs to build the full menu of tools available to the model. It hands its output to `ToolSchema.__init__`, which packages the wire-facing schema object.

*Call graph*: 1 external calls (__init__).


##### `ToolRegistry.__post_init__`  (lines 49–53)

```
def __post_init__(self) -> None
```

**Purpose**: Checks the registry right after it is created to make sure no two tools share the same name. This prevents the engine from later having to guess which tool a model meant to call.

**Data flow**: It reads all tool names from the registry's tuple of tools. It counts repeated names, sorts any duplicates, and if any exist it raises a `ValueError`. If every name is unique, it changes nothing and the registry is ready to use.

**Call relations**: This runs automatically after a `ToolRegistry` dataclass is constructed. It protects later lookup and dispatch code, especially `ToolRegistry.get`, from ambiguous tool names.


##### `ToolRegistry.schemas`  (lines 55–56)

```
def schemas(self) -> tuple[ToolSchema, ...]
```

**Purpose**: Builds the complete tool menu that can be shown to the model. Each item explains one tool and the input shape the model must follow to call it.

**Data flow**: It takes the registry's stored tuple of tool definitions. For each one, it calls `ToolDef.schema` to convert the internal definition into a wire-facing schema. It returns those schemas as an immutable tuple.

**Call relations**: This is the batch version of `ToolDef.schema`. It is used when the engine or model interface needs to advertise all available tools at once, rather than looking up a single tool to run.


##### `ToolRegistry.get`  (lines 58–62)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the tool definition with a given name so the engine can run the correct handler. If the name is not known, it fails loudly instead of silently doing the wrong thing.

**Data flow**: It receives a tool name string. It scans the registry's stored tools until it finds a matching `ToolDef`, then returns that definition. If no match exists, it raises a `KeyError` saying the tool is unknown.

**Call relations**: The engine's `_dispatch_segments` flow calls this when the model has requested a tool by name. Once `get` returns the matching `ToolDef`, the engine can use that definition's input model, safety flags, and handler to validate and execute the call.

*Call graph*: called by 1 (_dispatch_segments).
