# Turn setup, context assembly, and prompt preparation  `stage-8`

This stage happens just before the agent starts working on a user’s next message. It takes a waiting turn from the queue, gathers everything needed, and turns it into a ready-to-run job. The queue code is the coordinator. It safely claims one queued turn, loads model settings, credentials, sandbox access, tool choices, member context, and subagent options, then either runs the turn or records a controlled failure if setup cannot finish.

The transcript and prompt part prepares the words the model will read. If the conversation is too long, it summarizes older parts while keeping recent messages exact. It also adds recalled memories and renders the final prompt from templates.

The skill and toolbox part prepares what the agent can do. It copies needed skill files into the safe workspace, builds the catalog of callable tools, and gives extensions a restricted way to act. Together, these pieces act like packing a workbench before a repair: instructions, tools, workspace, and permissions are all ready before the first model call.

## Sub-stages

- [Transcript compaction and prompt construction](stage-8.1.md) `stage-8.1` — 3 files
- [Skill and toolbox preparation](stage-8.2.md) `stage-8.2` — 7 files

## Files in this stage

### Turn setup, context assembly, and prompt preparation
### `core/src/ufo/loop/queue.py`

`orchestration` · `queued turn execution`

A “turn” is one unit of conversation work: a user or system message comes in, and the agent must think, call tools if needed, write to the transcript, and finish with a status. This file is the traffic controller for that work. It uses DBOS, a durable workflow system, so that if the process crashes halfway through, the turn can be replayed safely instead of being lost or run twice in a harmful way.

The file keeps a single process-wide Runtime object. That object is like the toolbox for turn execution: database access, model registry, blob storage, sandbox manager, connector registry, credentials, search, memory, and more. When DBOS starts the turn workflow, the code binds the correct workspace, finds the agent for the turn, claims the turn, loads its database record, opens or attaches to the conversation sandbox, prepares tools and skills, and builds a TurnEngine. The TurnEngine then does the actual model-and-tool loop.

A key safety feature is the failure backstop. If something goes wrong before or outside the engine, this file still writes a terminal “failed” result and publishes it, so anyone waiting for the turn is not left hanging forever. It also carefully avoids putting real secrets into the sandbox. Instead it passes short sentinel values that a proxy can swap for real credentials only when allowed.

#### Function details

##### `init_runtime`  (lines 113–117)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the process-wide Runtime object that turn execution needs. This is done once when the service is set up, so later workflow runs can find shared services such as the database client, sandbox manager, model registry, and credential store.

**Data flow**: A fully built Runtime goes in. The function checks whether one is already installed; if not, it saves it in the module-level runtime slot. Nothing is returned, but future turn workflows can read that saved runtime.

**Call relations**: This is the setup step before turns can run. Later, _execute_turn reads the installed runtime; without this earlier call, turn execution stops immediately with a clear error.


##### `reset_runtime`  (lines 120–125)

```
def reset_runtime() -> None
```

**Purpose**: Clears the saved Runtime so another one can be installed. This is mainly a testing seam: normal serving installs the runtime once, but tests often need to swap in a fake or fresh runtime.

**Data flow**: No input is needed. The saved runtime slot is set back to empty. Nothing is returned.

**Call relations**: This sits outside the normal turn path. It exists so tests can call reset_runtime, then init_runtime again, without breaking the one-runtime safety check.


##### `_execute_turn`  (lines 128–162)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs the outer shell of a turn workflow. It chooses the correct workspace and agent context, then delegates the real turn work to _run_turn, while making sure unexpected failures still become a visible failed terminal result.

**Data flow**: It receives workspace_id and turn_id as strings from the durable workflow. It turns them into UUIDs, looks up the agent for the turn in the database, enters the workspace and agent scopes, and calls _run_turn. It returns a status string such as completed, failed, parked, or superseded; if an error happens, it records failure first.

**Call relations**: turn_workflow calls this when DBOS starts or replays a queued turn. _execute_turn sets the correct workspace boundary with ws and database transaction helpers, then hands off to _run_turn. If _run_turn or setup fails unexpectedly, it calls _commit_failed_terminal so waiting clients get a final answer.

*Call graph*: calls 2 internal fn (_commit_failed_terminal, _run_turn); called by 1 (turn_workflow); 5 external calls (select, agent, workspace_tx, ws, UUID).


##### `_enqueue_handoff`  (lines 165–200)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: Queues a follow-up turn for the same conversation partition when the current claim discovers that another turn should take over next. This keeps conversation turns ordered, like keeping all jobs for one customer in the same checkout lane.

**Data flow**: It receives the DBOS client, workspace ID, turn ID, conversation ID, and desired workflow ID. It builds queue options that target the turn workflow and partition by conversation ID, then asks DBOS to enqueue the work. If enqueueing is cancelled or fails, it clears the turn’s dispatch marker in the database so it can be retried later.

**Call relations**: _run_turn calls this after claiming a turn when the claim process reports a handoff. It hands the next piece of work to DBOS. On failure it logs or resets database state rather than pretending the handoff was successfully queued.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 203–379)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds everything needed for one agent turn and runs the TurnEngine. This is the main orchestration function: it claims the turn, loads records, prepares tools, skills, prompts, sandbox access, credentials, subagents, compaction, and model clients.

**Data flow**: It receives the Runtime and turn_id. It claims the turn so only the right workflow attempt runs it, repairs transcript state if the turn was superseded, optionally enqueues a handoff, loads the turn and agent, prepares subagent support, filters tools, builds the system prompt, opens the sandbox with safe environment variables, mounts preload skills, creates a TurnEngine, and awaits engine.run. It returns the resulting terminal status, or parked/superseded/failed when special conditions occur.

**Call relations**: _execute_turn calls this inside the proper workspace and agent scope. _run_turn calls helpers in this file for loading the turn, opening the sandbox, enqueueing handoffs, and committing backstop failures. It then hands the prepared world to TurnEngine, which performs the actual model rounds and tool dispatch.

*Call graph*: calls 4 internal fn (_commit_failed_terminal, _enqueue_handoff, _load_turn, _open_sandbox); called by 1 (_execute_turn); 24 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+14 more)).


##### `_run_turn.subagents_for`  (lines 231–235)

```
def subagents_for(acting_member_id: UUID | None) -> tuple[Spawn, Subagents]
```

**Purpose**: Creates an authorized subagent view for a specific acting member. It lets the main turn give subagent spawning rights that match who is acting, instead of using one blanket permission set.

**Data flow**: It receives an optional acting_member_id. It asks the Subagents object to authorize that member, then returns both the spawn function and the authorized Subagents wrapper. Nothing is written directly; it packages a permission-filtered interface.

**Call relations**: This helper is defined inside _run_turn because it depends on the Subagents object built for that specific turn. _run_turn passes it into TurnEngine, so the engine can ask for member-specific subagent access when a tool or subagent flow needs it.


##### `_commit_failed_terminal`  (lines 382–414)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: Records a final failed result for a turn and publishes it to listeners, even if the normal engine path did not get far enough to do so. This prevents clients from waiting forever after a crash or setup error.

**Data flow**: It receives a Hub, a turn ID, and the error that occurred. It creates a TerminalFrame containing the error type and a shortened message, updates the database if the turn is still queued or running, then publishes a terminal event. If this fails, it waits and retries with a growing delay until it succeeds.

**Call relations**: _execute_turn and _run_turn call this as a backstop when unexpected exceptions escape. It uses workspace_tx for the database write and Hub.publish to notify watchers that the turn has ended.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 6 external calls (__init__, __init__, sleep, update, workspace_tx, log).


##### `turn_workflow`  (lines 418–419)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Exposes turn execution as a DBOS workflow. A workflow is durable work that DBOS can queue, run, and replay after failures.

**Data flow**: It receives workspace_id and turn_id from DBOS. It immediately passes both to _execute_turn and returns the status string that _execute_turn produces.

**Call relations**: DBOS calls this function when an item from the turn queue is ready to run. It is intentionally thin: all real setup, context binding, and failure handling live in _execute_turn.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 422–479)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: Loads the database information needed to run a turn and converts it into typed application objects. It also derives the conversation audience, which controls who the agent is speaking to and which shared permissions may apply.

**Data flow**: It receives a turn UUID. It queries the turn, its agent, and its conversation in one database read. It turns raw database columns into a Turn object, an Agent object, and an Audience object, validating stored context and terminal data if present. It returns those three objects.

**Call relations**: _run_turn calls this after claiming a turn, and also when a turn was not claimed so transcript repair can still use the correct conversation details. It relies on workspace_tx for the database read and parse_audience to interpret the stored audience value.

*Call graph*: called by 1 (_run_turn); 7 external calls (__init__, __init__, model_validate, model_validate, select, parse_audience, workspace_tx).


##### `_open_sandbox`  (lines 482–515)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: Opens the conversation sandbox for this turn with the right run token and safe environment variables. The sandbox is where tools and code run, but real secrets are not placed inside it.

**Data flow**: It receives the sandbox service, run-token encoder, turn, optional grant store, connector CLI declarations, optional credential store, and credential slots. It creates a signed run token for this turn, builds environment variables for Git, connector CLI grants, and keyed providers, then asks ConversationSandbox.open for a SandboxHandle. The result is a handle to the sandbox session.

**Call relations**: _run_turn calls this before creating the SandboxSession and TurnEngine. _open_sandbox delegates environment construction to _git_config_env, _git_credential_config, _grant_cli_env, and _keyed_provider_env, then hands the final token and environment to the sandbox layer.

*Call graph*: calls 6 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env, open, encode); called by 1 (_run_turn); 1 external calls (__init__).


##### `_git_config_env`  (lines 518–525)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns Git configuration settings into environment variables that Git understands. This lets the turn configure Git behavior without writing a Git config file inside the sandbox.

**Data flow**: It receives a tuple of key/value Git settings. It creates GIT_CONFIG_COUNT plus numbered GIT_CONFIG_KEY_n and GIT_CONFIG_VALUE_n variables. It returns the environment dictionary.

**Call relations**: _open_sandbox calls this after collecting base Git proxy settings and any credential-related Git headers. Its output becomes part of the environment passed into ConversationSandbox.open.

*Call graph*: called by 1 (_open_sandbox).


##### `_git_credential_config`  (lines 528–563)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds Git-specific authentication headers for credential slots that are actually filled. It uses sentinel values, not real secrets, so the sandbox can ask for access while a proxy performs the safe secret swap later.

**Data flow**: It receives the credential store, declared credential slots, and workspace ID. For each slot that supports Git basic authentication, it checks whether the workspace has a stored value, resolves the host, and adds a Git extraheader setting containing the slot’s sentinel. Missing credentials, invalid host choices, or lookup errors produce warnings and are skipped. It returns a tuple of Git config key/value pairs.

**Call relations**: _open_sandbox calls this while preparing sandbox startup environment. It uses credential_host and slot_is_set from the credential system, and its returned settings are converted to environment variables by _git_config_env.

*Call graph*: called by 1 (_open_sandbox); 3 external calls (credential_host, slot_is_set, warn).


##### `_keyed_provider_env`  (lines 566–608)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Exports environment variables for provider credentials that are selected by key, while still avoiding real secret exposure. The sandbox sees sentinel values and resolved host names, not the credential itself.

**Data flow**: It receives the credential store, credential slots, and workspace ID. It scans slots with environment-variable or host-variable injection, checks that a secret is stored, resolves the chosen host, and writes the declared variable names into an environment dictionary. If a slot is unset, broken, or has no available host, it is skipped and may log a warning. The environment dictionary is returned.

**Call relations**: _open_sandbox calls this when constructing the sandbox environment. It works alongside _git_credential_config and _grant_cli_env: all three contribute different kinds of safe credential hints for processes inside the sandbox.

*Call graph*: called by 1 (_open_sandbox); 3 external calls (credential_host, slot_is_set, warn).


##### `SandboxAuthorizer.authorize`  (lines 619–631)

```
async def authorize(self, acting_member_id: UUID | None) -> SandboxSession
```

**Purpose**: Creates a member-specific authorized view of an existing sandbox session. This is used when work inside a turn needs to act as a particular member, with that member’s run token and allowed connector CLI grants.

**Data flow**: It receives an optional acting_member_id. It encodes a new run token containing the workspace, turn, and acting member, gathers grant-backed CLI environment variables for that member, and calls sandbox.authorize. It returns a SandboxSession that carries those updated authorization details.

**Call relations**: _run_turn creates a SandboxAuthorizer and passes its authorize method into TurnEngine as sandbox_for. When the engine needs sandbox access for a specific actor, this method refreshes the run token and grant environment; it uses _grant_cli_env to decide which CLI credentials may be exposed.

*Call graph*: calls 1 internal fn (_grant_cli_env); 1 external calls (__init__).


##### `_grant_cli_env`  (lines 634–673)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], acting_member_id: UUID | None, turn_id: UUID) -> dict[str, str]
```

**Purpose**: Chooses connector CLI environment variables based on active account grants. It prefers a member’s private grant, falls back to shared grants, and avoids guessing when more than one account could match.

**Data flow**: It receives an optional GrantStore, connector CLI declarations, an optional acting member ID, and the turn ID for logging. It reads active grants, groups them by connector provider, chooses either one private account or one shared account, and sets the CLI’s environment variable to a grant sentinel. If multiple accounts are possible, it logs an ambiguity and exports nothing for that provider. It returns the environment dictionary.

**Call relations**: _open_sandbox calls this when the sandbox is first opened, and SandboxAuthorizer.authorize calls it again when creating member-specific sandbox access. It reads grants from GrantStore.active_grants and turns selected account IDs into sentinel values using grant_sentinel.

*Call graph*: calls 1 internal fn (active_grants); called by 2 (authorize, _open_sandbox); 2 external calls (grant_sentinel, log).

## 📊 State Registers Touched

- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-effective-configuration` — The chosen runtime settings that tell the service how this deployment should behave.
- `reg-pack-selection` — The selected product pack that decides which bundle of extensions, skills, and infrastructure is enabled.
- `reg-extension-inventory` — The installed extension set and their declared capabilities, such as tools, routes, jobs, skills, and storage.
- `reg-model-catalog` — The shared list of available AI models, their limits, features, provider names, and calling rules.
- `reg-model-provider-adapters` — The shared provider clients that translate internal model requests into Anthropic, OpenAI, OpenRouter, or similar APIs.
- `reg-pricing-table` — The shared price list used to turn model and service usage into cost records.
- `reg-credential-store` — The encrypted store of API keys, service secrets, and owner-provided credentials.
- `reg-auth-session` — The signed login and identity state that proves which member or operator is using the system.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-audience-policy` — The saved visibility rules that decide which people may see or use a conversation or agent.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-inbound-message-state` — The saved incoming messages and surface-provided context waiting to be admitted into a conversation turn.
- `reg-transcript-state` — The stored conversation transcript, including exact recent messages and compact summaries of older content.
- `reg-tool-catalog` — The shared catalog of tools the model is allowed to see and call during a turn.
- `reg-skill-store` — The shared set of built-in, extension-provided, and user-created skills available to agents.
- `reg-sandbox-session` — The saved or live sandbox workspace where an agent can run commands and keep files across tool calls.
- `reg-egress-policy` — The network access rules that decide which outside sites sandboxed work may contact and which secrets may be injected.
- `reg-connector-connections` — The saved external accounts, OAuth connections, and agent grants that let tools use outside services safely.
- `reg-search-index` — The shared searchable index and chunk store built from synced or fetched content.
- `reg-memory-store` — The long-term memory store of remembered facts and recall results used to inform later responses.
- `reg-fleet-presence` — The shared record of live runtime processes used for supervision, cancellation, and recovery after crashes.
- `reg-accounting-ledger` — The shared usage ledger that records tokens, egress, sandbox usage, billing exports, and spend-limit checks.
- `reg-observability-context` — The shared logging, metrics, tracing, and trace-link state used to understand work across requests and subagents.
- `reg-proposal-governance` — The saved proposals and safety checks used to govern prompt or system improvements before applying them.
- `reg-subagent-delegation-state` — The parent-child turn and conversation links plus in-flight child-task tracking used to coordinate delegated subagents, cancellation, and result collection.
- `reg-prompt-template-state` — The canonical prompt and instruction templates, versions, and digests used to assemble model prompts and guard prompt-improvement proposals against stale edits.
- `reg-acting-principal-scope` — The current acting principal context—member, agent, on-behalf-of member, and object/agent scope—used to authorize actions, attribute turns, choose grants, and keep tool work tied to the right actor.
- `reg-model-token-budget` — The per-turn model budget state derived from model context/output limits and spend policy, used while assembling prompts, truncating or summarizing context, and tracking remaining usage during model calls.
