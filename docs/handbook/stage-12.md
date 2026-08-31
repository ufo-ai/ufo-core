# Sandbox, terminal, browser, and document execution environments  `stage-12`

This stage provides the controlled “rooms” where risky or practical work happens: running commands, browsing the web, opening documents, moving files, and showing terminals. It is mostly behind-the-scenes support used during the main work loop whenever a tool needs to act outside the chat itself.

The conversation workspace file is the front door. It finds or creates the private sandbox for a conversation and keeps track of its location. The session layer is the safety wall: it offers one standard way to run commands, read files, write files, load skills, and reach services without leaving the allowed area. The execution-environment file prepares safe environment variables, giving tools placeholders instead of raw secret keys.

Below that, carrier and terminal code chooses where work runs: locally, in Docker, in a cloud sandbox, or through a user’s connected terminal. Browser adapters provide Chrome wherever it lives, while DevTools plumbing remotely controls tabs, JavaScript, dialogs, and downloads. The perception and interaction layer reads pages or rendered documents, finds buttons and fields, maps screen positions, and sends clicks, typing, uploads, scrolling, screenshots, and document actions.

## Sub-stages

- [Sandbox carrier implementations and terminal transports](stage-12.1.md) `stage-12.1` — 6 files
- [Browser provider adapters and sandbox Chrome launchers](stage-12.2.md) `stage-12.2` — 4 files
- [Chrome DevTools session plumbing](stage-12.3.md) `stage-12.3` — 8 files
- [Rendered content perception and browser interaction primitives](stage-12.4.md) `stage-12.4` — 9 files

## Files in this stage

### Sandbox workspace core
Conversation sandboxes are opened or reconnected, prepared with safe execution environments, and backed by a consistent session boundary for files, commands, skills, and services.

### `core/src/ufo/harness/sandbox/conversation.py`

`orchestration` · `request handling and turn setup`

A conversation’s `/workspace` is treated as the single real copy of its files. This file makes sure every write, browse, and tool run reaches that same place instead of creating stray copies. Think of it like a coat-check ticket: the database stores a sandbox handle, and future processes use that ticket to find the same sandbox again.

The main class, `ConversationSandbox`, can open a sandbox for active work, attach to an existing one for read-only style operations, bind a conversation to a user’s connected terminal, write files into the workspace, list files, read files, and prune old generated files. It also protects important boundaries. Reads never create a sandbox, because a harmless-looking file browse should not change system state. Off-turn writes use an unsigned run token, so they can do local file work without being allowed network access through the proxy. Local workspace directories are created carefully with containment checks, so a malicious or mistaken symbolic link cannot redirect the sandbox into the wrong part of the host filesystem.

A key detail is race handling. Two tasks may try to create the first sandbox at the same time, such as an attachment upload and a turn starting. The file uses a compare-and-swap database update, meaning “write this handle only if the old value is still what I saw.” The loser reopens the winner’s sandbox, so the conversation ends up with one durable workspace.

#### Function details

##### `ConversationSandbox._route`  (lines 105–116)

```
def _route(self, stored: str | None) -> tuple[Carrier, str, bool]
```

**Purpose**: Chooses which sandbox provider should be used for a stored sandbox handle. This matters because an old conversation may still live on a different backend, and opening it on the wrong backend would strand or overwrite its files.

**Data flow**: It takes the stored handle string, if there is one. It reads the backend name embedded in that handle and checks whether this deployment has a special resume backend for it. It returns the carrier to use, the backend name, and whether that backend is off-cluster; if no special route applies, it returns the deployment’s normal carrier settings.

**Call relations**: When `ConversationSandbox.opened` or `ConversationSandbox.existing` needs to reach a non-terminal sandbox, they call this helper first. It gives them the provider choice before they create or attach to the sandbox.

*Call graph*: called by 2 (_opened, existing); 1 external calls (sandbox_handle_backend).


##### `ConversationSandbox.open`  (lines 118–170)

```
async def open(self, conversation_id: UUID, turn_id: UUID | None, run_token: str, env: Mapping[str, str]) -> SandboxSession
```

**Purpose**: Creates or resumes the sandbox for a conversation and makes sure the database stores the durable handle for it. It is the main path used when a turn or an off-turn writer needs a workspace it can write to.

**Data flow**: It starts with a conversation id, optional turn id, run token, and environment variables. It reads the currently stored sandbox handle and sandbox size from the database, asks `_opened` to create or resume the sandbox, then tries to save the resulting handle. If another task saved a competing handle first, it adopts that winner and tries again. It returns a `SandboxSession`, which is the usable connection to the chosen sandbox.

**Call relations**: The turn queue uses this through `_open_sandbox`, and this file’s `write` and `write_runtime` use it before copying bytes into a workspace. It relies on `_binding` for the database read, `_opened` for the actual sandbox opening, and `_claim` to safely persist the handle.

*Call graph*: calls 3 internal fn (_binding, _claim, _opened); called by 3 (_open_sandbox, write, write_runtime); 1 external calls (__init__).


##### `ConversationSandbox.existing`  (lines 172–234)

```
async def existing(self, conversation_id: UUID) -> SandboxSession | None
```

**Purpose**: Attaches to a sandbox only if the conversation already has one that can be reached. It is the safe read path: it will not create a new workspace just because someone asked to browse or read files.

**Data flow**: It takes a conversation id and reads the stored handle. If there is no handle, it returns `None`. If the handle points to a client terminal, it tries to attach through a terminal carrier. Otherwise it routes to the right backend, checks the needed host directory without creating it, and asks the carrier to attach. It returns a `SandboxSession` when attachment succeeds, or `None` when there is no reachable sandbox.

**Call relations**: `entries`, `read`, `prune`, and `prune_runtime` all call this because they should only act on an already-existing workspace. It uses `_stored` to get the saved handle, `_route` to pick the carrier, and carrier attach calls to reconnect.

*Call graph*: calls 2 internal fn (_route, _stored); called by 4 (entries, prune, prune_runtime, read); 5 external calls (__init__, __init__, __init__, to_thread, sandbox_handle_id).


##### `ConversationSandbox.claim_terminal`  (lines 236–246)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds an unbound conversation to a user’s connected terminal directory. This lets the first later sandbox open use the user’s live terminal workspace instead of the default sandbox storage.

**Data flow**: It takes a conversation id and a current working directory from the terminal. It builds a `client:` style handle for that directory, checks whether the conversation already has any stored handle, and if not tries to save this terminal handle. It returns `True` only if this call successfully made the claim.

**Call relations**: No caller inside this file is listed; it is intended for the admission path while a member’s terminal connection is live. It uses `_stored` to avoid stealing an existing binding and `_claim` to save the terminal handle safely.

*Call graph*: calls 2 internal fn (_claim, _stored).


##### `ConversationSandbox.write`  (lines 248–259)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes an external byte payload, such as an uploaded attachment, into the conversation workspace. It enforces a maximum size so one write cannot consume too much memory.

**Data flow**: It receives a conversation id, a relative path, and bytes to write. It rejects content over the configured size limit, opens the conversation sandbox off-turn, writes the bytes to the requested workspace-relative path, and returns the `/workspace/...` path the agent can later use.

**Call relations**: This function calls `open` because a write is allowed to create the workspace if none exists yet. It also uses `workspace_path` to turn the relative path into the path format visible inside the sandbox.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.write_runtime`  (lines 261–273)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output under a named runtime category, separate from ordinary member-visible workspace files. It is used for bounded system-produced files that still need to live with the conversation.

**Data flow**: It receives a conversation id, category, relative path, and bytes. It checks the size limit, opens the sandbox off-turn, prefixes the path with the category, writes the bytes into the runtime area, and returns a display path for that runtime file.

**Call relations**: Like `write`, it calls `open` because runtime output may need to create or resume the sandbox before writing. After opening, it hands the actual file placement and display-path calculation to the `SandboxSession`.

*Call graph*: calls 1 internal fn (open).


##### `ConversationSandbox.prune`  (lines 275–286)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files directly under a workspace subdirectory, keeping only a requested number of newest files. This prevents unattended off-turn writers, such as log appenders, from growing storage forever.

**Data flow**: It takes a conversation id, a relative directory prefix, and a keep count. It attaches only to an existing sandbox; if there is none, it does nothing. Inside the sandbox it runs a small Python pruning program against the workspace path. If that program reports failure, it raises an operating-system style error.

**Call relations**: It calls `existing` because pruning should not create a workspace. It uses `workspace_path` so the in-sandbox pruning program sees the same `/workspace` path the agent sees.

*Call graph*: calls 1 internal fn (existing); 1 external calls (workspace_path).


##### `ConversationSandbox.prune_runtime`  (lines 288–300)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files under one internal runtime directory, keeping only the newest requested number. It is the runtime-area version of workspace pruning.

**Data flow**: It takes a conversation id, runtime category, relative prefix, and keep count. It attaches to an existing sandbox, calculates the target runtime directory and its root through the session, runs the pruning program there, and raises an error if pruning fails.

**Call relations**: It calls `existing` for the same reason as `prune`: cleanup should not create a new sandbox. It then relies on the session to translate runtime-relative names into real paths before running the pruning script.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox.entries`  (lines 302–341)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists the member-visible files in a conversation workspace. It returns simple records with relative path, size, and last modified time, suitable for a file browser.

**Data flow**: It takes a conversation id and attaches to an existing sandbox. If none exists, it returns an empty tuple. It asks the sandbox to run `ufo fs glob`, a file-walking command, excluding names such as `.git`. It checks that the response contains a file list, warns if the list was truncated, converts absolute paths back to workspace-relative paths, and returns sorted `WorkspaceFile` objects.

**Call relations**: File browsing calls this path through `existing`, so browsing does not create storage. It calls `_workspace_rel` for each returned path to remove the workspace root safely before building `WorkspaceFile` records.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 3 external calls (__init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 343–347)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns an absolute path reported by a workspace walk into a path relative to the workspace root. This keeps file browser results from exposing container or host filesystem paths.

**Data flow**: It receives a sandbox handle and a path string. It checks whether the path starts with either the container workspace root or the host workspace path recorded in the handle. If so, it strips that root and returns the relative part. If not, it raises an error because the file walk returned something outside the workspace.

**Call relations**: `entries` calls this for each file returned by the sandbox’s file listing command. It acts as the final safety check before file paths are shown to callers.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 349–357)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Reads one file from an existing conversation workspace as a stream of byte chunks. It returns `None` instead of creating a workspace or pretending a missing file exists.

**Data flow**: It receives a conversation id and a relative file path. It attaches to an existing sandbox; if none is reachable, it returns `None`. It asks the session whether the file exists; if not, it returns `None`. Otherwise it returns an async byte iterator that yields the file content in chunks.

**Call relations**: It calls `existing` to preserve the no-side-effects rule for reads. Once a session is available, the actual existence check and byte streaming are delegated to the `SandboxSession`.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox._opened`  (lines 359–418)

```
async def _opened(self, conversation_id: UUID, turn_id: UUID | None, stored: str | None, run_token: str, env: Mapping[str, str], size: str) -> tuple[str, Carrier, SandboxHandle]
```

**Purpose**: Chooses where a sandbox open should happen and performs the actual create-or-resume call. This is the central decision point for terminal-bound conversations, resumed backends, and normal deployment-owned sandboxes.

**Data flow**: It receives the conversation id, turn id, stored handle, run token, environment variables, and sandbox size. It first checks whether the conversation is already bound to a client terminal or currently has a terminal workspace. If so, it creates a terminal-backed sandbox session. Otherwise it routes the stored handle to the right backend, prepares or chooses the host workspace directory, optionally changes ownership for the sandbox user when running as root, and asks the carrier to create or resume the sandbox. It returns the backend name, carrier, and handle.

**Call relations**: `open` calls this after reading the database binding. `_opened` calls `_route` for non-terminal backends and uses sandbox specification objects to hand the chosen settings to either a terminal carrier or the routed carrier.

*Call graph*: calls 1 internal fn (_route); called by 1 (open); 5 external calls (__init__, __init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._provisioned_dir`  (lines 420–435)

```
def _provisioned_dir(self, conversation_id: UUID) -> Path
```

**Purpose**: Creates and verifies the host directory used as a conversation’s workspace for local in-cluster sandboxes. It is careful to avoid being tricked by symbolic links inside the workspace root.

**Data flow**: It takes a conversation id. It makes sure the configured workspace root exists, resolves and validates that configured root, then creates or opens the conversation-specific child directory using containment checks. It returns the safe directory path.

**Call relations**: _opened calls this when a non-off-cluster sandbox needs a host workspace directory. It uses the containment helpers rather than plain path operations so the carrier later mounts a directory that is truly under the allowed root.

*Call graph*: 3 external calls (suppress, configured_root, contained_dir).


##### `ConversationSandbox._existing_dir`  (lines 437–448)

```
def _existing_dir(self, conversation_id: UUID) -> Path | None
```

**Purpose**: Finds the already-created host workspace directory for read-style operations without creating it. This preserves the rule that reads must not create new workspace state.

**Data flow**: It takes a conversation id. It validates the configured workspace root and tries to resolve the conversation directory within it. If the directory is missing, it returns `None`; if the path exists but violates containment rules, the containment helper raises an error. Otherwise it returns the safe directory path.

**Call relations**: `existing` calls this when attaching to a local in-cluster sandbox. It is the read-path counterpart to `_provisioned_dir`.

*Call graph*: 2 external calls (configured_root, contained_dir).


##### `ConversationSandbox._stored`  (lines 450–452)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Fetches only the stored sandbox handle for a conversation. It is a small convenience wrapper around the fuller binding lookup.

**Data flow**: It receives a conversation id, calls `_binding`, discards the sandbox size, and returns the handle string or `None`.

**Call relations**: `existing`, `claim_terminal`, and `_claim` use this when they only need to know what handle the conversation row currently stores. It delegates the actual database query to `_binding`.

*Call graph*: calls 1 internal fn (_binding); called by 3 (_claim, claim_terminal, existing).


##### `ConversationSandbox._binding`  (lines 454–475)

```
async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]
```

**Purpose**: Reads the conversation’s current sandbox handle and the owning agent’s requested sandbox size from the database. This gives sandbox opening enough information to resume the right place and size a new sandbox correctly.

**Data flow**: It receives a conversation id. Inside a workspace-scoped database transaction, it joins the conversation row to its agent row and selects the sandbox handle plus sandbox size for the current workspace. If no matching conversation exists, it raises an error. Otherwise it returns the handle, which may be `None`, and the sandbox size.

**Call relations**: `open` calls this at the start of the create-or-resume flow, and `_stored` calls it for handle-only reads. It uses `workspace_tx` for database access and `ws_current` so it only sees the active workspace.

*Call graph*: called by 2 (_stored, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 477–498)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Safely writes a sandbox handle into the conversation row only if the row still contains the value this caller previously saw. This is what prevents two simultaneous first opens from both winning.

**Data flow**: It receives a conversation id, the handle value the caller read earlier, and the new handle it wants to store. It runs a conditional database update: if the stored value still matches the earlier value, it saves the new handle and returns it. If not, it reads the current stored handle and returns that winner. If the handle somehow disappeared, it raises an error.

**Call relations**: `open` uses this to settle races between concurrent sandbox creation attempts, and `claim_terminal` uses it to bind only an unclaimed conversation to a terminal. When it loses the update race, it calls `_stored` to discover the winning handle.

*Call graph*: calls 1 internal fn (_stored); called by 2 (claim_terminal, open); 3 external calls (update, workspace_tx, ws_current).


### `core/src/ufo/harness/sandbox/exec_env.py`

`domain_logic` · `sandbox open for probe execution`

A sandbox is a controlled place where the system runs commands. Those commands often need to talk to outside services, such as Git hosts or connector command-line tools. This file decides which environment variables should be present when a probe opens that sandbox.

The key idea is that the sandbox never receives real passwords, API keys, or tokens. Instead, it receives sentinels: harmless marker strings. Think of a sentinel like a coat-check ticket. The sandbox carries the ticket, and the proxy at the door swaps it for the real credential only when making an approved network request.

The main class, ProbeEnv, combines several sources of information. It adds the conversation ID, Git configuration, connector CLI credentials, and keyed provider variables such as API-key environment variables. It checks whether each declared credential slot is actually set before exporting anything for it, so the sandbox does not see misleading half-configured variables. It also resolves provider hosts, because some services have different regional or account-specific hosts.

The file is careful about ambiguity. If more than one possible connector account could match a fixed environment variable, it logs the problem and exports nothing for that provider rather than silently choosing the wrong account. This protects probes from acting as an unintended user or account.

#### Function details

##### `ProbeEnv.exports`  (lines 60–74)

```
async def exports(self, conversation_id: UUID, probe_id: UUID, acting_member_id: UUID | None=None) -> dict[str, str]
```

**Purpose**: Builds the complete set of environment variables for a probe running inside a sandbox. It gathers safe placeholders for Git, connector CLIs, keyed providers, and the current conversation ID.

**Data flow**: It receives a conversation ID, a probe ID, and optionally the member the probe is acting as. It reads the current workspace ID, then asks helper functions to produce Git settings, grant-based CLI variables, and keyed provider variables. It returns one dictionary of environment variable names and string values for the sandbox to use.

**Call relations**: This is the top-level entry in this file. When a probe needs to open a sandbox, it calls this method, which then delegates the specialized pieces to _git_config_env, _git_credential_config, _grant_cli_env, and _keyed_provider_env. It also asks ws_current for the current workspace so every credential lookup is scoped to the right workspace.

*Call graph*: calls 4 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env); 1 external calls (ws_current).


##### `_git_config_env`  (lines 77–84)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns Git configuration settings into the special environment variable format that Git understands. This lets the sandbox configure Git without writing a Git config file.

**Data flow**: It receives a tuple of Git setting pairs, where each pair is a setting name and value. It numbers those settings and creates variables such as the total count, each key, and each value. It returns a dictionary ready to merge into the sandbox environment.

**Call relations**: ProbeEnv.exports calls this after collecting the Git settings. This function does not decide what Git should be told; it only packages already-chosen settings in the format Git expects.

*Call graph*: called by 1 (exports).


##### `_git_credential_config`  (lines 87–122)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Creates Git authentication configuration for credential slots that are actually available in the workspace. It uses sentinels instead of real secrets, so Git traffic can be authorized later by the proxy.

**Data flow**: It receives an optional credential store, the declared credential slots, and a workspace ID. For each slot that declares Git basic authentication, it checks whether the workspace has a stored value, resolves the correct host, and adds a Git extra header containing the slot sentinel. If a slot cannot be checked or its host cannot be resolved, it logs a warning and skips that slot. It returns Git setting pairs for the slots that are safe to export.

**Call relations**: ProbeEnv.exports calls this before passing its result into _git_config_env. Inside, it relies on slot_is_set to avoid exporting placeholders for missing credentials, credential_host to find the correct host, and warn to record recoverable problems without stopping the sandbox open.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_keyed_provider_env`  (lines 125–167)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Builds environment variables for external providers that use API keys or similar credentials. It exports only safe sentinels and resolved host names, never the actual secret values.

**Data flow**: It receives an optional credential store, declared credential slots, and a workspace ID. It scans the slots for provider environment variables or host environment variables, checks whether each slot is set, resolves the provider host, and then adds the declared sentinel and host values to the returned environment dictionary. If checking or host resolution fails, it warns and skips that slot.

**Call relations**: ProbeEnv.exports calls this as one part of the sandbox environment. This helper uses slot_is_set and credential_host to make sure exported variables match real, usable credentials, and uses warn when a declared credential cannot be safely turned into an environment variable.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_grant_cli_env`  (lines 170–212)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], acting_member_id: UUID | None, run_id: UUID) -> dict[str, str]
```

**Purpose**: Builds environment variables for connector command-line tools that need to authenticate as a granted account. It chooses the probe actor's private grant when possible, otherwise a shared workspace grant.

**Data flow**: It receives an optional grant store, the connector CLI declarations, the acting member ID, and the current run or probe ID. It reads active grants, groups them by provider, prefers a matching private account, falls back to shared accounts, and writes the connector's environment variable to a grant sentinel when exactly one account fits. If more than one account fits, it logs the ambiguity and exports nothing for that provider.

**Call relations**: ProbeEnv.exports calls this while assembling the sandbox environment. This helper asks GrantStore.active_grants for the current usable grants, uses grant_sentinel to create the safe placeholder for the chosen account, and logs ambiguous cases so the failure is visible instead of silently using the wrong account.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (exports); 2 external calls (grant_sentinel, log).


### `core/src/ufo/harness/sandbox/session.py`

`domain_logic` · `cross-cutting during sandbox open, command execution, file access, skill loading, and proxy authorization`

A sandbox is like a rented workshop for one conversation: tools can work on files in `/workspace`, run commands, and use installed skills, but they should not touch private records or host storage. This file is the rulebook and front desk for that workshop. It defines the `Carrier` interface, which is the small contract every backend must follow, whether the real sandbox is Docker, a remote provider, or something else. The rest of the system talks to `Sandbox`, not to Docker or any provider directly. The file also protects paths. User-supplied paths are normalized so `..` tricks or symlinks cannot steer reads and writes outside the intended area. It prepares proxy environment variables so network traffic from the sandbox can be measured and authorized with signed tokens. It also separates normal workspace files from UFO’s private runtime files, such as tool output and skill staging. There are two ways to hold a sandbox: `SandboxSession` wraps one that already exists, while `_LateSandbox` waits to create it until the first real operation. Both expose the same methods, so callers do not need to care when the container is actually opened.

#### Function details

##### `egress_proxy_env`  (lines 353–392)

```
def egress_proxy_env(proxy: 'ProxyEndpoint', run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables a sandbox command needs so its outbound internet traffic goes through UFO’s egress proxy. This lets the system meter and authorize network use instead of giving the sandbox open internet access.

**Data flow**: It receives a proxy endpoint and a signed run token. It checks that the public proxy URL exists and is HTTPS, builds proxy URLs containing the token as the basic-auth username, and returns a dictionary of proxy, certificate, no-proxy, and sentinel model-key variables.

**Call relations**: Carrier code uses this kind of environment when launching commands in off-cluster sandboxes. It relies on URL parsing to validate the proxy address before anything is handed to the sandbox.

*Call graph*: 1 external calls (urlsplit).


##### `_basic_username`  (lines 398–404)

```
def _basic_username(header: str) -> str
```

**Purpose**: Extracts the username from a `Proxy-Authorization: Basic ...` header. In this system, that username is where signed sandbox tokens travel.

**Data flow**: It receives an authorization header string, verifies that it uses Basic authentication, decodes the base64 user-and-password text, and returns only the username part before the colon.

**Call relations**: Both `RunTokenCodec.from_proxy_auth` and `ProbeTokenCodec.from_proxy_auth` call this first, then verify the signed token found in the username.

*Call graph*: called by 2 (from_proxy_auth, from_proxy_auth); 1 external calls (b64decode).


##### `RunTokenCodec.from_env`  (lines 423–427)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Creates a run-token signer from the deployment secret stored in the environment. Without this secret, UFO cannot mint tokens that the proxy will trust.

**Data flow**: It reads the token-secret environment variable. If missing, it raises an error; otherwise it encodes the value as bytes and returns a `RunTokenCodec` using it.

**Call relations**: `core/src/ufo/serve.run` calls this at service startup so later sandbox opens can sign per-turn proxy tokens.

*Call graph*: called by 1 (run).


##### `RunTokenCodec.encode`  (lines 429–432)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a run’s workspace, turn, and optional member identity into a signed token. The proxy later uses this token to know which turn a network request belongs to.

**Data flow**: It receives a `RunToken`, formats its IDs into a domain-specific payload, signs that payload with the codec secret, and returns the signed string.

**Call relations**: `core/src/ufo/loop/queue._open_sandbox` calls this when opening a sandbox, so commands launched for a turn carry the right proxy identity.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 434–446)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Reads and verifies a run token presented to the proxy. It refuses forged tokens and tokens from the wrong token family.

**Data flow**: It extracts the Basic-auth username, verifies the signature with the deployment secret, splits the payload into IDs, converts them to UUIDs, and returns a `RunToken`. Bad decoding, signatures, formats, or UUIDs become a clear invalid-token error.

**Call relations**: This is the inverse of `RunTokenCodec.encode`; proxy-side code uses it when a sandbox makes a proxied request.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `ProbeTokenCodec.encode`  (lines 481–487)

```
def encode(self, probe: ProbeToken) -> str
```

**Purpose**: Signs a token for a probe, which is a sandbox command that runs outside a normal turn. The token includes its own expiry time because there is no running turn row to authorize it.

**Data flow**: It receives a `ProbeToken`, formats the workspace, conversation, probe ID, member ID, and expiry into a payload, signs it, and returns the signed string.

**Call relations**: Probe-launching code mints these tokens before a probe command uses the egress proxy; `ProbeTokenCodec.from_proxy_auth` later checks them.

*Call graph*: 1 external calls (sign_token).


##### `ProbeTokenCodec.from_proxy_auth`  (lines 489–505)

```
def from_proxy_auth(self, header: str) -> ProbeToken
```

**Purpose**: Verifies a probe token received through proxy authentication. It makes sure the token was signed by this deployment and belongs to the probe-token family.

**Data flow**: It extracts the Basic-auth username, verifies the signed payload, parses workspace, conversation, probe, member, and expiry values, and returns a `ProbeToken`. Invalid input is wrapped as an invalid signed probe-token error.

**Call relations**: This mirrors `ProbeTokenCodec.encode` and is used by proxy-side authorization when off-turn probe traffic arrives.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `sandbox_handle_id`  (lines 593–598)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Pulls the raw sandbox ID out of a stored handle only if it belongs to the requested backend. This prevents one sandbox provider from trying to resume another provider’s container.

**Data flow**: It receives a backend name and a stored handle string like `backend:id`. If the prefix matches, it returns the ID after the separator; otherwise it returns `None`.

**Call relations**: Carrier-opening code uses this when deciding whether a persisted conversation sandbox can be resumed by the current backend.


##### `sandbox_handle_backend`  (lines 601–604)

```
def sandbox_handle_backend(value: str) -> str
```

**Purpose**: Finds which backend wrote a stored sandbox handle. This helps deployments route an existing conversation back to the right sandbox carrier.

**Data flow**: It receives a stored handle string and returns the part before the first separator.

**Call relations**: Higher-level sandbox routing can use this alongside `sandbox_handle_id` when more than one backend may still have live sandboxes.


##### `Carrier.create`  (lines 642–642)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the carrier operation for creating or attaching to a sandbox that may be newly provisioned. A concrete backend implements the actual Docker, local, or remote-provider work.

**Data flow**: It receives a `SandboxSpec` describing the conversation, image, workspace, proxy, token, and optional size or resume ID. The implementation returns a `SandboxHandle` that later operations use.

**Call relations**: The `Sandbox` abstraction depends on carriers offering this operation, but this file only states the contract; backend files provide the real behavior.


##### `Carrier.attach`  (lines 644–650)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines a read-only-style attach operation that should not create a new sandbox. It is used when the caller wants an existing sandbox if it is still reachable.

**Data flow**: It receives a `SandboxSpec`, usually containing a resume ID or conversation identity. The implementation returns a `SandboxHandle` if the sandbox exists, or `None` if it is gone.

**Call relations**: Late or read paths can call this through carrier implementations to avoid resurrecting a container just because someone looked for files.


##### `Carrier.exec`  (lines 652–654)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines how to run one command inside a sandbox. Each backend decides how command execution actually reaches its container or remote environment.

**Data flow**: It receives a sandbox handle, an argument list, and a timeout. The implementation runs the command and returns stdout, stderr, exit code, and timeout information in an `ExecResult`.

**Call relations**: `ufo_fs_file_op` calls this directly, and many `Sandbox` methods call it through a bound carrier to run shell, Python, file-test, and supervisor commands.

*Call graph*: called by 1 (ufo_fs_file_op).


##### `Carrier.write`  (lines 656–668)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how to copy bytes into a sandbox file safely. The contract requires the backend to respect path containment rather than relying on unsafe shell redirection.

**Data flow**: It receives a handle, a target path, and byte content. The implementation writes the bytes, creating parents when appropriate, and changes the sandbox filesystem.

**Call relations**: `Sandbox.write_file`, runtime-file helpers, and skill staging use this through concrete carriers.


##### `Carrier.read`  (lines 670–681)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how to stream a file out of the sandbox without loading the whole file into host memory. This matters for large files and for consistent file-access boundaries.

**Data flow**: It receives a handle and a path. The implementation yields chunks of bytes asynchronously, or raises an appropriate file/access error.

**Call relations**: `Sandbox.read_file`, runtime-read helpers, and lower-level private reads use this carrier method after path checks.


##### `Carrier.dial`  (lines 683–692)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Defines how outside code can reach a network port opened inside the sandbox. This supports things like browser debugging ports or preview servers.

**Data flow**: It receives a handle and an in-sandbox port number. The implementation returns a `DialTarget` with host, TLS choice, and required headers, or raises `SandboxUnreachable`.

**Call relations**: `Sandbox.dial` delegates to this, and browser-related extensions call `Sandbox.dial` when they need to connect to a service inside the sandbox.


##### `Carrier.file_op`  (lines 694–704)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Defines a bounded, structured file-operation interface for workspace tools. It lets reads, edits, globs, greps, and change lists run inside the sandbox instead of pulling whole files to the host.

**Data flow**: It receives a handle, an operation name, and JSON-like parameters. The implementation returns a JSON-like result object, or raises a recoverable value error for tool-level failures.

**Call relations**: `Sandbox.run_ufo_fs` prepares safe parameters and then calls this method on the active carrier.


##### `CommandStopping.stop_commands`  (lines 724–724)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines an optional carrier feature for stopping commands that may keep running after the original exec call is cancelled. Not every backend needs this.

**Data flow**: It receives a sandbox handle, including turn identity, and the implementation stops only the command groups launched for that turn.

**Call relations**: `Sandbox.stop_commands` and `_LateSandbox.stop_commands` call this only when the carrier declares that it supports command stopping.


##### `SkillLoading.load_skills`  (lines 731–733)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Defines an optional native way for a carrier to load skills into the sandbox runtime. Some connected runtimes can do this more directly than running the staged Python loader.

**Data flow**: It receives a handle and a skill payload. The implementation installs or resolves the skills and returns an `ExecResult` whose stdout describes loaded roots.

**Call relations**: `Sandbox.load_skills` prefers this path when the active carrier implements `SkillLoading`; otherwise it falls back to staged skill loading.


##### `SkillExecuting.exec_skill`  (lines 740–742)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines an optional privileged command path for running server-provided skill installation programs. This is separate from normal member commands.

**Data flow**: It receives a handle, command arguments, and a timeout. The implementation runs the command with the authority needed to update the skill tree and returns an `ExecResult`.

**Call relations**: `Sandbox._exec_skill` calls this for staged skill load and system-skill sync operations.


##### `SystemSkillSeeding.seed_system_skills`  (lines 749–749)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Defines an optional hook for carriers whose runtime filesystem is prepared in the current process. It lets the process seed baked system skills directly.

**Data flow**: It receives skill archive bytes. The implementation stores or installs them in whatever runtime location that carrier controls.

**Call relations**: Carrier setup code can use this protocol when it detects a backend capable of local system-skill seeding.


##### `ufo_fs_file_op`  (lines 752–790)

```
async def ufo_fs_file_op(carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Implements the common carrier file-operation behavior for sandbox images that include the UFO file tool. It runs the file command inside the sandbox and parses its JSON answer.

**Data flow**: It receives a carrier, handle, operation name, and parameters. It chooses a longer timeout for document reads, executes `ufo fs` or `sbxfs`, trims stdout, parses JSON, turns reported tool errors into `ValueError`, and returns the parsed object.

**Call relations**: Concrete carriers can delegate their `file_op` implementation here instead of duplicating command construction, JSON parsing, and error handling.

*Call graph*: calls 1 internal fn (exec); 3 external calls (dumps, loads, PurePosixPath).


##### `host_argv`  (lines 798–810)

```
def host_argv(argv: tuple[str, ...], root: str) -> tuple[str, ...]
```

**Purpose**: Rewrites command arguments for carriers where `/workspace` is really a host directory. It carefully changes only real `/workspace` path segments, not similar text inside URLs or other names.

**Data flow**: It receives an argument tuple and a host root path. It scans each argument and replaces standalone `/workspace` segments with the host root, returning a new tuple.

**Call relations**: Host-path carriers use this before executing commands so tools can keep speaking in sandbox paths while the local backend runs against host files.


##### `workspace_path`  (lines 813–821)

```
def workspace_path(path: str) -> str
```

**Purpose**: Normalizes a user- or tool-supplied path and proves it stays under `/workspace`. This is one of the main safety gates against path traversal.

**Data flow**: It receives a path that may be absolute or relative. It anchors relative paths under `/workspace`, resolves `.` and `..` pieces through `_resolve_parts`, rejects escapes, and returns the safe absolute workspace path.

**Call relations**: File writes, file existence checks, scoped reads, `run_ufo_fs`, and `rooted_path` call this before handing paths to a carrier.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 5 (_read_scoped_file, file_exists, run_ufo_fs, write_file, rooted_path); 1 external calls (PurePosixPath).


##### `runtime_relative`  (lines 824–834)

```
def runtime_relative(path: str) -> PurePosixPath
```

**Purpose**: Checks that a runtime-internal relative path is safe and truly relative. Runtime files are private UFO files, so their names need containment too.

**Data flow**: It receives a path string, asks the containment helper to resolve it under a fake `/runtime` root, rejects anything changed or escaping, and returns a `PurePosixPath` relative to that root.

**Call relations**: `_runtime_path` and `_runtime_display_path` call this before constructing real or display paths for runtime files.

*Call graph*: called by 2 (_runtime_display_path, _runtime_path); 2 external calls (PurePosixPath, contained_relative).


##### `sandbox_runtime_root`  (lines 837–839)

```
def sandbox_runtime_root(conversation_id: UUID) -> str
```

**Purpose**: Builds the private runtime directory path for one conversation inside the sandbox. This keeps UFO’s internal files separate from the member workspace.

**Data flow**: It receives a conversation UUID and returns a path under `$UFO_HOME/runs/` using the UUID’s hex form.

**Call relations**: `_runtime_root` uses this as the default when a sandbox handle does not already carry a custom runtime root.

*Call graph*: called by 1 (_runtime_root).


##### `shell_path`  (lines 842–847)

```
def shell_path(path: str) -> str
```

**Purpose**: Quotes a sandbox path so it can be safely inserted into a shell command. It has special handling for paths beginning with `$UFO_HOME` so that variable can still expand inside the sandbox.

**Data flow**: It receives a path string. If it starts with `$UFO_HOME/`, it preserves that variable and quotes the remainder; otherwise it shell-quotes the whole path.

**Call relations**: Other code can use this utility when building shell snippets that need to mention sandbox paths safely.

*Call graph*: 1 external calls (quote).


##### `_runtime_root`  (lines 850–851)

```
def _runtime_root(handle: SandboxHandle) -> str
```

**Purpose**: Finds the actual runtime root for a sandbox handle. It uses an explicit handle value if present, otherwise derives the standard per-conversation path.

**Data flow**: It receives a `SandboxHandle`. It returns `handle.runtime_root` when set, or calls `sandbox_runtime_root` with the handle’s conversation ID.

**Call relations**: Runtime path construction, skill staging, scoped reads, writes, and file operations all use this so they agree on one private runtime location.

*Call graph*: calls 1 internal fn (sandbox_runtime_root); called by 7 (_read_scoped_file, _run_staged_skill_load, _sync_system_skills, run_ufo_fs, write_runtime_path, _runtime_display_path, _runtime_path).


##### `_runtime_path`  (lines 854–855)

```
def _runtime_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Turns a safe runtime-relative name into the real absolute path inside the sandbox. This is used for UFO-owned files rather than user workspace files.

**Data flow**: It receives a handle and relative path. It gets the runtime root, validates the relative path with `runtime_relative`, joins them as POSIX paths, and returns the string.

**Call relations**: Runtime-file methods, skill staging, skill sync, tool-output directory creation, and runtime existence checks use this before carrier reads or writes.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 8 (_read_runtime_file, _run_staged_skill_load, _sync_system_skills, ensure_tool_output_dir, runtime_file_exists, runtime_path, write_runtime_file, write_runtime_path); 1 external calls (PurePosixPath).


##### `_runtime_display_path`  (lines 858–862)

```
def _runtime_display_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds a user-facing name for a runtime file using `$UFO_HOME` instead of the full absolute sandbox path. This gives agents a stable-looking path they can reuse.

**Data flow**: It receives a handle and a relative runtime path. It finds the run directory name, validates the relative path, and returns a path like `$UFO_HOME/runs/<id>/...`.

**Call relations**: `Sandbox.runtime_display_path` calls this when outside code wants to show or hand back a readable runtime path.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 1 (runtime_display_path); 1 external calls (PurePosixPath).


##### `rooted_path`  (lines 865–868)

```
def rooted_path(path: str, root: str) -> str
```

**Purpose**: Normalizes a path under a chosen root while still applying the workspace-style escape checks. It preserves the spelling of the root the caller supplied.

**Data flow**: It receives a path and a root string. It maps the suffix onto `/workspace`, validates it with `workspace_path`, then maps the normalized suffix back under the original root.

**Call relations**: `Sandbox.run_ufo_fs` and `_read_scoped_file` use this for allowed read-only roots such as the runtime directory or skill tree.

*Call graph*: calls 1 internal fn (workspace_path); called by 2 (_read_scoped_file, run_ufo_fs).


##### `_resolve_parts`  (lines 871–880)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Simplifies path pieces and rejects attempts to climb above the root. It is the small path-stack routine behind workspace containment.

**Data flow**: It receives path parts, skips empty and `.` pieces, pops one segment for `..`, and raises an error if `..` would escape above the starting root. It returns the cleaned list of path segments.

**Call relations**: `workspace_path` calls this before deciding whether the final path still sits under `/workspace`.

*Call graph*: called by 1 (workspace_path).


##### `Sandbox.conversation_id`  (lines 892–896)

```
def conversation_id(self) -> UUID
```

**Purpose**: Declares that every sandbox object can report which conversation it belongs to. This can be known even before a late sandbox has actually been created.

**Data flow**: As a base property, it takes no extra input and raises `NotImplementedError`; subclasses return the stored conversation UUID.

**Call relations**: `SandboxSession`, `_LateSandbox`, and `_AuthorizedSandbox` provide the real value so callers can identify the workspace consistently.


##### `Sandbox.created`  (lines 899–901)

```
def created(self) -> bool
```

**Purpose**: Declares that sandbox objects can say whether a real sandbox session already exists. This helps callers avoid accidentally creating one just to ask.

**Data flow**: As a base property, it has no output except raising `NotImplementedError`; subclasses return true or false from their own state.

**Call relations**: `SandboxSession` always reports true, while `_LateSandbox` and `_AuthorizedSandbox` report whether the late session has been opened.


##### `Sandbox.authorize`  (lines 903–911)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'Sandbox'
```

**Purpose**: Declares how to view the same sandbox with a different run token and environment. This is how the same container can act with the right member’s network authority.

**Data flow**: It receives a run token, names of environment variables to clear, and new environment values. The base method raises `NotImplementedError`; subclasses return an authorized sandbox wrapper or session.

**Call relations**: `SandboxSession.authorize`, `_LateSandbox.authorize`, and `_AuthorizedSandbox.authorize` implement this behavior for already-open and not-yet-open sandboxes.


##### `Sandbox._bound`  (lines 913–914)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Declares the internal step that turns any sandbox wrapper into a concrete `SandboxSession`. Most public methods use this so they do not care whether creation is early or late.

**Data flow**: It receives no extra input and, in the base class, raises `NotImplementedError`. Implementations return a live session with a carrier and handle.

**Call relations**: Almost every operation on `Sandbox` calls `_bound` before it delegates to carrier reads, writes, execs, or dials.

*Call graph*: called by 19 (_read_file, _read_runtime_file, _read_scoped_file, bash, bash_task, dial, ensure_tool_output_dir, file_exists, load_skills, python (+9 more)).


##### `Sandbox.runtime_path`  (lines 916–918)

```
async def runtime_path(self, relative: str) -> str
```

**Purpose**: Returns the real internal path for one UFO runtime file. Callers use it when they need the absolute sandbox location, not a workspace path.

**Data flow**: It receives a relative runtime name, binds the sandbox to get its handle, builds a contained runtime path, and returns the absolute string.

**Call relations**: It uses `_bound` and `_runtime_path`, sharing the same validation as runtime reads, writes, and skill staging.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.runtime_display_path`  (lines 920–922)

```
async def runtime_display_path(self, relative: str) -> str
```

**Purpose**: Returns a readable `$UFO_HOME`-based path for a runtime file. This is useful when the path may be shown to or reused by an agent.

**Data flow**: It receives a relative runtime name, binds the sandbox, converts the relative name into a display path, and returns that string.

**Call relations**: It delegates to `_runtime_display_path`, so display paths line up with the same runtime root used by actual file operations.

*Call graph*: calls 2 internal fn (_bound, _runtime_display_path).


##### `Sandbox.write_runtime_file`  (lines 924–927)

```
async def write_runtime_file(self, relative: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a UFO-owned runtime file outside the user workspace. This is for internal files such as staged skill payloads or tool outputs.

**Data flow**: It receives a runtime-relative name and bytes. It binds the sandbox, turns the relative name into a safe runtime path, and asks the carrier to write the bytes there.

**Call relations**: It uses `_bound`, `_runtime_path`, and the carrier’s `write` method, avoiding workspace path handling because the target is in the private runtime area.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.write_runtime_path`  (lines 929–937)

```
async def write_runtime_path(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to an already-resolved path, but only if that path is inside this sandbox’s runtime root. This prevents callers from using absolute paths to escape.

**Data flow**: It receives an absolute path and bytes. It binds the sandbox, checks that the path is under the runtime root and not the root itself, converts it back to a relative name, and writes through the carrier.

**Call relations**: It combines `_bound`, `_runtime_root`, `_runtime_path`, and POSIX path checks to safely support callers that already have a runtime path.

*Call graph*: calls 3 internal fn (_bound, _runtime_path, _runtime_root); 1 external calls (PurePosixPath).


##### `Sandbox.runtime_file_exists`  (lines 939–946)

```
async def runtime_file_exists(self, relative: str) -> bool
```

**Purpose**: Checks whether a specific runtime-owned file exists and is a regular file. It is a small safe existence test for UFO’s private runtime area.

**Data flow**: It receives a runtime-relative name, binds the sandbox, builds the runtime path, runs `test -f` inside the sandbox with a short timeout, and returns true when the exit code is zero.

**Call relations**: It uses carrier `exec` after `_runtime_path`; callers do not need to run their own shell checks.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.read_runtime_file`  (lines 948–950)

```
def read_runtime_file(self, relative: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts streaming a UFO-owned runtime file. It returns an asynchronous stream so large files do not need to be loaded all at once.

**Data flow**: It receives a runtime-relative name and returns the async iterator produced by `_read_runtime_file`.

**Call relations**: This is the public wrapper; `_read_runtime_file` performs the binding, path construction, and carrier read.

*Call graph*: calls 1 internal fn (_read_runtime_file).


##### `Sandbox._read_runtime_file`  (lines 952–955)

```
async def _read_runtime_file(self, relative: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a private runtime file from the sandbox in chunks. It applies runtime path validation before reading.

**Data flow**: It receives a runtime-relative name, binds the sandbox, converts the name into an absolute runtime path, reads chunks through the carrier, and yields each chunk onward.

**Call relations**: `Sandbox.read_runtime_file` calls this to do the actual streaming work.

*Call graph*: calls 2 internal fn (_bound, _runtime_path); called by 1 (read_runtime_file).


##### `Sandbox.bash`  (lines 957–963)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Bash command inside the sandbox through UFO’s command supervisor. This is the simple entry point for shell work in the conversation workspace.

**Data flow**: It receives a command string and optional timeout. It binds the sandbox, runs `ufo run -- bash -lc <command>` through the carrier, and returns the command result.

**Call relations**: Browser sandbox extension code calls this when bringing up or diagnosing services; internally it relies on `_bound` and carrier `exec`.

*Call graph*: calls 1 internal fn (_bound); called by 2 (lease, _bring_up_failure).


##### `Sandbox.bash_task`  (lines 965–975)

```
async def bash_task(self, command: str, base: str, *, detach: bool, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs or reattaches to a journaled Bash task through the sandbox supervisor. Detached tasks can continue or be revisited by task name.

**Data flow**: It receives a command, task base name, detach flag, and optional timeout. It binds the sandbox, builds the `ufo run --task ...` command, and returns the exec result.

**Call relations**: Higher-level task-running code can use this when shell work needs UFO’s task journal rather than a one-off command.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.sh`  (lines 977–986)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a POSIX shell script with arguments passed as separate command-line values. This avoids unsafe string interpolation and lets host-path carriers rewrite workspace arguments.

**Data flow**: It receives a script, positional arguments, and optional timeout. It binds the sandbox, runs `sh -c` with the script and arguments through the carrier, and returns an `ExecResult`.

**Call relations**: Other sandbox operations can use this for portable shell snippets while still going through `_bound` and carrier execution.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.python`  (lines 988–1003)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Python program inside the sandbox with UFO’s containment guard injected. This lets small helper programs safely check paths using the exact guard shipped by the host process.

**Data flow**: It receives Python source, arguments, and optional timeout. It binds the sandbox, prepends the containment bootstrap, runs isolated `python3 -I -c ...`, and returns the exec result.

**Call relations**: Copying and skill-related helpers rely on this style of safe in-sandbox Python; the method delegates final execution to the carrier.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.stop_commands`  (lines 1005–1011)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands launched for this sandbox turn when the carrier supports stopping. It avoids killing sibling turns sharing the same container.

**Data flow**: It binds the sandbox. If the carrier implements `CommandStopping`, it calls `stop_commands` with the current handle; otherwise it does nothing.

**Call relations**: Cancellation handling calls this after it knows a member deliberately stopped work, not for every internal cancellation.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.write_file`  (lines 1013–1015)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the conversation workspace at a safe path. It is the main public workspace copy-in method.

**Data flow**: It receives a path and bytes. It binds the sandbox, normalizes and confines the path with `workspace_path`, and asks the carrier to write the content.

**Call relations**: File-upload or tool-output flows use this instead of calling carrier `write` directly, because it enforces workspace containment first.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.load_skills`  (lines 1017–1059)

```
async def load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Loads system and user skills into the sandbox’s skill directory and returns their installed roots. It supports both carrier-native loading and a portable staged loader.

**Data flow**: It receives a skill payload. It binds the sandbox, uses native carrier loading if available or stages and executes the loader, validates the JSON response, refreshes system skills if needed, and returns a name-to-root mapping.

**Call relations**: `core/src/ufo/runtime/skills/runtime.install_skill` and `core/src/ufo/runtime/skills/runtime.load_skills` call this when preparing skills for execution.

*Call graph*: calls 3 internal fn (_bound, _run_staged_skill_load, _sync_system_skills); called by 2 (install_skill, load_skills); 1 external calls (loads).


##### `Sandbox._run_staged_skill_load`  (lines 1061–1083)

```
async def _run_staged_skill_load(self, bound: 'SandboxSession', payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Implements the portable fallback for loading skills by writing a JSON payload into the runtime area and running the bundled loader program inside the sandbox.

**Data flow**: It receives a bound session and payload. It serializes the payload, writes it to a random staging path, computes its hash, and executes the skill loader with the path, roots, runtime root, and expected hash.

**Call relations**: `Sandbox.load_skills` calls this when the carrier lacks native skill loading, and again after system-skill refresh if needed.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 3 external calls (sha256, dumps, uuid4).


##### `Sandbox._sync_system_skills`  (lines 1085–1102)

```
async def _sync_system_skills(self, bound: 'SandboxSession') -> None
```

**Purpose**: Copies the current system-skill archive into the sandbox and installs it safely. This refreshes baked or cached system skills when the loader cannot find the expected ones.

**Data flow**: It receives a bound session. It writes the system-skill zip archive to a random runtime staging path, runs the bundled sync program with an expected hash, and raises an error if the command fails.

**Call relations**: `Sandbox.load_skills` calls this when missing system skill roots suggest the sandbox needs a refresh.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 2 external calls (sha256, uuid4).


##### `Sandbox._exec_skill`  (lines 1104–1109)

```
async def _exec_skill(self, bound: 'SandboxSession', argv: tuple[str, ...]) -> ExecResult
```

**Purpose**: Runs a privileged skill-management command through carriers that explicitly support it. It keeps skill installation separate from ordinary sandbox commands.

**Data flow**: It receives a bound session and command arguments. It verifies the carrier implements `SkillExecuting`, then calls `exec_skill` with the default timeout and returns the result.

**Call relations**: `Sandbox._run_staged_skill_load` and `Sandbox._sync_system_skills` use this to execute their Python loader programs.

*Call graph*: called by 2 (_run_staged_skill_load, _sync_system_skills).


##### `Sandbox.ensure_tool_output_dir`  (lines 1111–1135)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure UFO’s private tool-output directory exists in the runtime area. If a file or broken link is squatting on that fixed internal name, it removes it and recreates the directory.

**Data flow**: It binds the sandbox, builds the runtime path for the tool-output directory, runs a shell script that checks, removes squatters, and creates the directory, then returns whether cleanup happened.

**Call relations**: Tool offload code can call this before writing private outputs so one bad filesystem entry does not break later offloads.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.file_exists`  (lines 1137–1143)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a workspace path exists as a regular file. It applies workspace containment before running the check.

**Data flow**: It receives a path, normalizes it with `workspace_path`, binds the sandbox, runs `test -f` inside the sandbox, and returns true when the exit code is zero.

**Call relations**: Callers use this lightweight check instead of opening or reading a file just to learn whether it exists.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.run_ufo_fs`  (lines 1145–1186)

```
async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured file operation inside the sandbox after deciding which safe root the path belongs to. Workspace paths can be edited, while runtime and skill-tree paths are only allowed for read-like operations.

**Data flow**: It receives an operation name and argument object. It copies the arguments, normalizes any path to `/workspace`, the current runtime root, or the skills root, rejects disallowed escapes or writes outside workspace, sets the workspace root parameter, and calls carrier `file_op`.

**Call relations**: File tools use this as their main bridge to in-sandbox `ufo fs` behavior, relying on it for path policy before the carrier runs the operation.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); 1 external calls (PurePosixPath).


##### `Sandbox.read_file`  (lines 1188–1190)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a workspace file, or an allowed current-runtime file, in bounded chunks. It is the public read method for scoped sandbox files.

**Data flow**: It receives a path and returns the async iterator created by `_read_scoped_file`.

**Call relations**: Callers use this wrapper; `_read_scoped_file` performs binding, root selection, containment checks, and carrier streaming.

*Call graph*: calls 1 internal fn (_read_scoped_file).


##### `Sandbox._read_scoped_file`  (lines 1192–1205)

```
async def _read_scoped_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Does the actual safe streaming for `read_file`. It permits normal workspace reads and current-runtime reads, but normalizes both before touching the carrier.

**Data flow**: It receives a path, binds the sandbox, computes the runtime display path, chooses runtime or workspace handling, validates with `rooted_path` or `workspace_path`, then yields chunks from carrier `read`.

**Call relations**: `Sandbox.read_file` calls this whenever a caller wants file bytes without loading the entire file into memory.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); called by 1 (read_file); 1 external calls (PurePosixPath).


##### `Sandbox._read_file`  (lines 1207–1210)

```
async def _read_file(self, target: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file from an already-chosen target path through the carrier. It is a lower-level helper that assumes the caller has already chosen a safe target.

**Data flow**: It receives a target path, binds the sandbox, calls carrier `read`, and yields each chunk.

**Call relations**: Internal code can use this when path validation has happened elsewhere and only carrier streaming is needed.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.dial`  (lines 1212–1216)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Asks the carrier how to reach a port opened inside the sandbox from outside. This supports interactive services started by commands.

**Data flow**: It receives a port number, binds the sandbox, delegates to carrier `dial`, and returns the resulting host, TLS, and header information.

**Call relations**: The sandbox Chrome extension calls this after starting browser services, so it can connect to the sandbox-published endpoint.

*Call graph*: calls 1 internal fn (_bound); called by 1 (lease).


##### `SandboxSession.conversation_id`  (lines 1230–1231)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation ID stored on an already-open sandbox handle. For a session, this value is always immediately available.

**Data flow**: It reads `handle.conversation_id` from the session and returns it.

**Call relations**: This implements the base `Sandbox.conversation_id` property for concrete, bound sessions.


##### `SandboxSession.created`  (lines 1234–1235)

```
def created(self) -> bool
```

**Purpose**: Reports that a `SandboxSession` already has a real sandbox. Unlike a late sandbox, no creation is pending.

**Data flow**: It receives no input and returns `True`.

**Call relations**: This implements the base `Sandbox.created` property for already-bound sessions.


##### `SandboxSession._bound`  (lines 1237–1238)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Returns this session as the bound sandbox. Since it already contains a carrier and handle, no opening work is needed.

**Data flow**: It receives no input and returns `self`.

**Call relations**: All inherited `Sandbox` operations call `_bound`; on `SandboxSession`, that step is a no-op.


##### `SandboxSession.authorize`  (lines 1240–1268)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new view of the same sandbox with a different run token and environment. This lets a shared container execute as the correct member without changing the original session.

**Data flow**: It receives a new token, environment names to remove, and environment values to add. It replaces the old token inside proxy environment variables, verifies the old token was present, merges the new environment, and returns a new `SandboxSession` with an updated handle.

**Call relations**: _AuthorizedSandbox uses this after a late sandbox is opened, and direct callers can use it to re-scope network authority for a session.

*Call graph*: 2 external calls (__init__, __init__).


##### `_LateSandbox.__init__`  (lines 1272–1284)

```
def __init__(self, conversation_id: UUID, turn_id: UUID, open: Callable[[], Awaitable[SandboxSession]], existing: Callable[[], Awaitable[SandboxSession | None]]) -> None
```

**Purpose**: Creates a sandbox wrapper that delays opening the real sandbox until an operation actually needs it. This avoids provisioning work for code paths that never touch the sandbox.

**Data flow**: It receives the conversation ID, turn ID, an async open function, and an async existing-session lookup. It stores them, creates an async lock, and starts with no session.

**Call relations**: Higher-level sandbox orchestration constructs `_LateSandbox` for turns; inherited `Sandbox` methods later call `_bound` to force creation.

*Call graph*: 1 external calls (Lock).


##### `_LateSandbox.conversation_id`  (lines 1287–1288)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation ID known before the sandbox is opened. This lets callers identify the workspace without creating the sandbox.

**Data flow**: It reads the stored `_conversation_id` and returns it.

**Call relations**: This implements `Sandbox.conversation_id` for delayed sandboxes and is reused by `_AuthorizedSandbox` through its late sandbox.


##### `_LateSandbox.created`  (lines 1291–1292)

```
def created(self) -> bool
```

**Purpose**: Reports whether this late sandbox has already opened a real session. It does not create anything while answering.

**Data flow**: It checks whether `_session` is no longer `None` and returns that boolean.

**Call relations**: This implements `Sandbox.created`; `_AuthorizedSandbox.created` delegates to it.


##### `_LateSandbox.authorize`  (lines 1294–1300)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Builds an authorized wrapper around this not-yet-open sandbox. The authorization will be applied after the real session is opened.

**Data flow**: It receives a run token, names of environment variables to clear, and new environment values, then returns an `_AuthorizedSandbox` carrying those values.

**Call relations**: Calls to `authorize` on delayed sandboxes do not force creation; `_AuthorizedSandbox._bound` applies the authorization later.

*Call graph*: 1 external calls (__init__).


##### `_LateSandbox._bound`  (lines 1302–1307)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens the sandbox on first use and returns the resulting session. A lock makes sure two concurrent first operations do not open two sandboxes.

**Data flow**: It checks whether `_session` exists. If not, it enters an async lock, checks again, awaits the stored open function, saves the session, and returns it.

**Call relations**: All inherited `Sandbox` operations eventually call this when used on a late sandbox.


##### `_LateSandbox.stop_commands`  (lines 1309–1312)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands for this turn even if the late sandbox was not opened through this wrapper yet. It can attach to an existing session just for stopping.

**Data flow**: It uses the cached session if present, otherwise awaits the existing-session lookup. If it gets a session with a stoppable carrier, it replaces the handle’s turn ID with this late sandbox’s turn ID and calls carrier stop.

**Call relations**: Cancellation flows use this so a deliberate stop can reach already-running work without accidentally creating a fresh sandbox.

*Call graph*: 1 external calls (replace).


##### `_AuthorizedSandbox.conversation_id`  (lines 1323–1324)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation ID of the wrapped late sandbox. Authorization does not change which conversation the sandbox belongs to.

**Data flow**: It reads `late.conversation_id` and returns it.

**Call relations**: This implements the base property for authorized late wrappers.


##### `_AuthorizedSandbox.created`  (lines 1327–1328)

```
def created(self) -> bool
```

**Purpose**: Reports whether the wrapped late sandbox has been created. The authorized wrapper itself does not own a separate session.

**Data flow**: It reads `late.created` and returns that boolean.

**Call relations**: This keeps creation-state checks consistent after calling `_LateSandbox.authorize`.


##### `_AuthorizedSandbox.authorize`  (lines 1330–1336)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Re-authorizes the same late sandbox with a new token and environment. The newest authorization replaces the wrapper rather than stacking wrappers.

**Data flow**: It receives new authorization inputs and forwards them to the underlying late sandbox’s `authorize` method, returning the resulting sandbox.

**Call relations**: This avoids nested authorization layers; later `_bound` will apply only the current authorization to the opened session.


##### `_AuthorizedSandbox._bound`  (lines 1338–1339)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens or retrieves the underlying late sandbox, then applies this wrapper’s authorization to the resulting session. This is where delayed authorization becomes a concrete session.

**Data flow**: It awaits `late._bound()` to get a `SandboxSession`, calls that session’s `authorize` with the stored token and environment changes, and returns the authorized session.

**Call relations**: Inherited `Sandbox` operations call this on authorized late sandboxes before executing commands, reads, writes, or dials.

## 📊 State Registers Touched

- `reg-deployment-config` — The merged deployment settings that tell the system what product, services, addresses, databases, sandboxes, and safety defaults to use.
- `reg-cancellation-state` — The shared brake state that marks work as stopping or cancelled so model calls, tools, workflows, and retries do not continue stale work.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-egress-network-policy` — The outbound network permission state that decides which external hosts, proxies, and secret injections are allowed for a workspace or agent.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-execution-environment` — The controlled runtime environment given to commands, files, terminals, browsers, and documents, including safe environment variables and containment rules.
- `reg-tool-catalog-policy` — The current tool catalog and allowlist rules that say which built-in, extension, connector, MCP, and sandbox tools may be called.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-skill-store` — The saved and selected skills that can be provisioned by packs, loaded into agent sandboxes, or created by users inside a workspace.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-request-actor-scope` — Context-local current workspace, member, acting agent, and object/action scope carried through authorization, database boundaries, object APIs, tools, and egress checks.
- `reg-turn-resource-budget` — In-flight per-turn resource budget and usage accumulator for spend, model tokens, cache reads, sandbox tokens, egress, retries, and stop conditions before final ledger reconciliation.
- `reg-conversation-workspace-changes` — Durable git-like summaries of files added, edited, or removed inside a conversation workspace during a turn.
- `reg-backend-provider-registry` — Process-local registry mapping provider names to active backend implementations for models, search, embeddings, memory, connectors, browser access, auth, billing, and feature services.
- `reg-sandbox-runtime-cache` — Built sandbox client/runtime image and reusable sandbox cache artifacts used when launching isolated execution environments.
