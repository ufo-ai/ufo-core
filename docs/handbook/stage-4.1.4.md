# Extension Provisioning and Connector Declarations  `stage-4.1.4`

This stage is shared setup support for extensions, which are add-on packages that give a workspace new abilities. It sits between “an extension is installed” and “the workspace can actually use it.” The provisioning file is the installer’s careful hand. It reads the agents promised by installed extensions and creates matching real agents inside the workspace. It also avoids trampling over local edits. If a member has changed an agent, an extension update should not blindly replace that work.

The keyed connectors file declares a special kind of connector: services reached with plain API keys. An API key is a secret text token used to prove access to another service. This file tells the system what keys are required, where those keys should be sent, and what safe instructions the resulting agent should follow. Together, these pieces let packaged extension capabilities become usable workspace agents, while keeping service access clear and user changes protected.

## Files in this stage

### Extension Agents and Keyed Connectors
Runtime provisioning materializes installed extension agents in workspaces, while keyed connector declarations define API-key-based services those extensions can package.

### `core/src/ufo/runtime/kinds/provisioning.py`

`domain_logic` · `workspace setup and first workspace activity after extensions are active`

An extension can ship a suggested agent, like a template that says “this workspace should have an agent with this name, prompt, tools, and setup.” This file applies those declarations to a specific workspace. Without it, shipped agents would either never appear, appear as duplicates, or accidentally overwrite agents that workspace members have edited.

The main rule is: once a shipped agent is created or adopted, it becomes an ordinary workspace agent. Most of its settings are not rewritten later, because the workspace member may have changed them. Two fields are treated specially. The extension’s setup is rewritten when the extension declaration changes, because that setup belongs to the extension. The purpose is filled in only if the workspace row has no purpose yet.

The file also prevents name fights. A shipped agent is tracked by the extension name plus the name declared in that extension, not just by the visible agent name. If the desired name is already taken, it tries a safe variant such as adding the extension name. Main agents are handled specially: a provision marked as the main agent can adopt the workspace’s existing main agent rather than replacing it. In short, this file is the careful “move-in clerk” for extension-provided agents: it adds them, labels where they came from, and avoids disturbing what is already there.

#### Function details

##### `AgentProvisioning.apply`  (lines 54–62)

```
async def apply(self, workspace_id: UUID) -> tuple[ProvisionOutcome, ...]
```

**Purpose**: Applies all agent declarations from all active extension manifests to one workspace. It is the public entry point for this file’s work: take every shipped agent description and make sure the workspace has the right row for it.

**Data flow**: It receives a workspace ID and reads the manifests stored on the AgentProvisioning object. It enters that workspace’s context, walks through every manifest and every agent provision inside it, and asks _one to apply each provision. It returns a tuple of ProvisionOutcome records saying, for each extension agent, whether it was created, adopted, or already present.

**Call relations**: This is the top-level loop for provisioning. It sets the workspace context through ufo.runtime.workspace.ws, then repeatedly calls AgentProvisioning._one so the detailed database decisions happen one provision at a time.

*Call graph*: calls 1 internal fn (_one); 1 external calls (ws).


##### `AgentProvisioning._one`  (lines 64–147)

```
async def _one(self, workspace_id: UUID, manifest: Manifest, provision: AgentProvision) -> ProvisionOutcome
```

**Purpose**: Applies one extension-declared agent to one workspace. It decides whether that agent is already present, should adopt an existing workspace agent, or needs to be created under a free name.

**Data flow**: It receives the workspace ID, the extension manifest, and one agent provision. Inside a workspace database transaction, it first looks for an existing row already linked to this extension and declared agent name. If it finds one, it may update only the extension-owned fields through _fill and returns that the agent is present. If a provision wants to be the main agent but the existing shipped row is not main, it detaches and archives that old shipped row so the main provision can use the workspace’s real main agent instead. If no shipped row is usable, it looks for an existing unprovisioned agent to adopt: either the current main agent for a main provision, or an identical same-name agent for a normal provision. If adoption is not possible, it asks _free_name for a safe visible name and then calls _create to insert a new row. The result is a ProvisionOutcome with the extension, final visible name, and result status.

**Call relations**: AgentProvisioning.apply calls this for each declared agent. This function is the decision-maker: it uses workspace_tx for the database transaction, calls _fill when a shipped row already exists, calls _identical when considering adoption, calls _free_name when a new name may be needed, and calls _create when a new agent row must be inserted.

*Call graph*: calls 4 internal fn (_create, _fill, _free_name, _identical); called by 1 (apply); 4 external calls (__init__, select, update, workspace_tx).


##### `AgentProvisioning._fill`  (lines 149–189)

```
async def _fill(self, connection: AsyncConnection, shipped: sa.Row, manifest: Manifest, provision: AgentProvision) -> None
```

**Purpose**: Refreshes only the parts of an already-shipped agent that still belong to the extension. It preserves the workspace member’s edits while still letting new extension setup information reach existing workspaces.

**Data flow**: It receives a database connection, the existing shipped row, the current manifest, and the provision declaration. It compares the stored setup with the setup now declared by the extension. If they differ, it prepares to rewrite setup. If the row has no purpose, it also prepares to fill in the provision’s purpose. If neither change is needed, it does nothing. If a change is needed, it updates the row, moves the recorded provision version to the current manifest version, and updates the timestamp.

**Call relations**: AgentProvisioning._one calls this after it finds that the provisioned agent already exists in the workspace. It uses the database connection already opened by _one and sends the final update through SQLAlchemy’s update and execute calls.

*Call graph*: called by 1 (_one); 2 external calls (execute, update).


##### `AgentProvisioning._free_name`  (lines 191–216)

```
async def _free_name(self, connection: AsyncConnection, workspace_id: UUID, extension: str, declared: str) -> str
```

**Purpose**: Finds a visible agent name that will not collide with an active agent already in the workspace. This protects both member-created agents and agents shipped by other extensions from being overwritten.

**Data flow**: It receives a database connection, workspace ID, extension name, and the name the extension wanted. It reads all non-archived agent names in that workspace. It first tries the declared name. If that is taken, it tries the declared name plus a version of the extension name, then numbered variants up to a fixed limit. It returns the first unused candidate, or raises an error if none can be found.

**Call relations**: AgentProvisioning._one calls this only when it has decided a new agent row must be created. The returned name is then passed to AgentProvisioning._create so the insert uses a safe, user-visible name.

*Call graph*: called by 1 (_one); 2 external calls (execute, select).


##### `AgentProvisioning._create`  (lines 218–275)

```
async def _create(self, connection: AsyncConnection, workspace_id: UUID, manifest: Manifest, provision: AgentProvision, name: str) -> None
```

**Purpose**: Creates a new ordinary agent row from an extension’s provision. The new agent records where it came from, but it does not inherit private grants, credentials, or other member-controlled connections.

**Data flow**: It receives a database connection, workspace ID, manifest, provision, and chosen visible name. It gathers the provision’s spec fields, such as prompt, model, reasoning setting, visibility, tools, and setup. If the provision did not specify an icon, it reads existing workspace icons and asks auto_agent_icon to pick one. It then inserts a new agent row with a fresh UUID, timestamps, the provision identity, and the declared configuration. If another concurrent run already inserted the same provisioned agent, the database conflict rule makes this insert do nothing instead of failing the member’s turn.

**Call relations**: AgentProvisioning._one calls this after _free_name chooses a name for a new shipped agent. This function talks directly to the database, uses uuid4 to create the row ID, uses auto_agent_icon when needed, and chooses the PostgreSQL or SQLite insert helper depending on the database in use.

*Call graph*: called by 1 (_one); 4 external calls (execute, select, auto_agent_icon, uuid4).


##### `AgentProvisioning._identical`  (lines 277–290)

```
def _identical(self, row: sa.Row, provision: AgentProvision) -> bool
```

**Purpose**: Checks whether an existing workspace agent already matches what an extension would create. If it matches, the system can adopt that row instead of making a duplicate.

**Data flow**: It receives a database row and an agent provision. It compares the row’s prompt, model, reasoning setting, internet access flag, sandbox size, visibility, and tools against the provision’s spec. It returns true only when all compared fields match.

**Call relations**: AgentProvisioning._one calls this while deciding whether a normal, unprovisioned workspace agent can be adopted by an extension provision. If this returns true, _one links the existing row to the extension instead of calling _free_name and _create.

*Call graph*: called by 1 (_one).


### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup / extension manifest loading`

Some outside services cannot be connected through a broker such as Composio or Pipedream because the user already owns an API key and the broker cannot create one for them. This file covers that case. It is like a locked mail slot: the agent can send a request through it, but it never gets to hold or read the real key.

The file defines a small table of supported “keyed providers” such as Datadog, PostHog, Mercury, Apollo, and PandaDoc. For each provider, it records the API host, the HTTP header where the key belongs, the environment variable name the sandbox will see, and the human-facing description used when asking a workspace owner to fill the credential. An environment variable here contains only a sentinel value, meaning a placeholder marker. The egress proxy, which is the outbound network gate, swaps that marker for the real secret only when the request is going to the approved host.

Some providers have several possible API hosts, such as Datadog regions. For those, the file creates an extra credential slot so the owner chooses one of the published hosts. This avoids letting arbitrary user-written hostnames receive secrets.

Finally, the file builds a manifest. The manifest is the extension’s public declaration: these are the credential slots, and this is the prompt text explaining how agents should use them safely.

#### Function details

##### `KeyedSecret.__post_init__`  (lines 56–61)

```
def __post_init__(self) -> None
```

**Purpose**: This checks each declared secret right after it is created. It prevents the file from claiming the proxy can swap an authorization style that the proxy does not actually support.

**Data flow**: A KeyedSecret is created with a key name, header name, environment variable, description, and optional authorization scheme such as Bearer. This method reads the scheme. If there is no scheme, or if it is one of the allowed schemes, nothing changes. If the scheme is unknown, it raises an error so the bad provider declaration fails early.

**Call relations**: This runs automatically when a KeyedSecret row is constructed in the provider table. It does not hand work to other local functions; its job is to protect later manifest creation from unsafe or impossible secret-swapping rules.


##### `KeyedProvider.__post_init__`  (lines 79–88)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider has a clear, safe host rule. A provider must either have one fixed API host or a closed list of allowed site hosts, but not both and not neither.

**Data flow**: A KeyedProvider is created with its name, label, secrets, and either a fixed host or a list of possible sites. This method inspects those fields. If the host setup is invalid, or if a multi-site provider is missing the environment variable and description needed for the user’s choice, it raises an error. Otherwise the provider definition is accepted unchanged.

**Call relations**: This runs automatically while the KEYED_PROVIDERS table is being built. Later functions such as KeyedProvider.target_host and KeyedProvider.slots rely on this validation, because they assume every provider has exactly one safe way to decide where requests may go.


##### `KeyedProvider.target_host`  (lines 91–100)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This returns the host rule for a provider. For a simple provider it returns the fixed hostname; for a regional provider it returns a controlled host choice that can only be one of the declared options.

**Data flow**: It reads the provider’s fixed host and list of sites. If there are no sites, it outputs the fixed host string. If there are sites, it builds and outputs a HostChoice object containing the credential slot name, the explanation shown to the user, the allowed hosts, the default host, and the environment variable that will expose the chosen host in the sandbox.

**Call relations**: KeyedProvider.slots uses this to decide what host each secret may be injected into. KeyedProvider.usage also uses it to show the agent whether to call a literal host or a host supplied through an environment variable. When a choice is needed, this function calls HostChoice.__init__ to package the allowed options.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 102–120)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This turns one provider declaration into the credential slots the rest of the system understands. Each slot says what secret is needed and exactly where the proxy may inject it into an outgoing request.

**Data flow**: It starts with a provider and asks for its target host. For every secret belonging to that provider, it creates a CredentialSlot with a name, a user-facing description, and an InjectionTarget. The injection target records the approved host, HTTP header, sentinel placeholder, sandbox environment variable, and request-counting dimension. If the provider uses a host choice, it also adds a separate slot for the chosen host. The result is a tuple of credential slot declarations.

**Call relations**: The top-level manifest function gathers the output of KeyedProvider.slots for every provider in KEYED_PROVIDERS. Inside this function, CredentialSlot.__init__ and InjectionTarget.__init__ are called to build the manifest objects that tell the proxy and credential system what to do.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 122–135)

```
def usage(self) -> str
```

**Purpose**: This creates a short instruction line showing how an agent should call the provider’s REST API with curl. It is used in the prompt text so agents know the right host, headers, and environment variables without seeing the real secrets.

**Data flow**: It reads the provider’s secrets, header names, schemes, environment variable names, and host rule. It formats those into a human-readable bullet: the provider name, the credential slot names, and an example curl command. If the provider has a selectable host, it includes the host-choice slot and uses the host environment variable in the example. The output is plain text.

**Call relations**: This is used while SECTION_BODY is assembled. Each provider contributes one usage line to the prompt section that later goes into the manifest.


##### `manifest`  (lines 268–274)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s public entry point for the host system. It returns a Manifest object describing the extension name, version, credential slots, and prompt text.

**Data flow**: It reads the constants, the provider table, and the prepared prompt body. It asks every provider for its credential slots, flattens those into one tuple, wraps the prompt text in a PromptSection, and returns a Manifest containing all of that. It does not contact any outside service or read any real secret.

**Call relations**: The UFO extension loader is expected to call this when it discovers the extension. This function calls Manifest.__init__ and PromptSection.__init__ to package the configuration, and it relies on KeyedProvider.slots to supply the detailed credential declarations.

*Call graph*: 2 external calls (__init__, __init__).
