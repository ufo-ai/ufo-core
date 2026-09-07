# Process entry and service bootstrap  `stage-2`

This stage is the front door of the UFO process. It belongs to startup and setup, before the system can answer web requests or run background jobs. Its job is to gather the basic facts the rest of the service needs: deployment settings, database connections, rules about which model keys can be shown in sandboxed environments, product and build information, and the shared service objects later code will use.

There are two main entrances. The CLI stage, centered on `core/src/ufo/cli.py`, provides `ufoctl`, a terminal tool for operators. It lets a person initialize a workspace, inspect the installed product, run selected tasks, package things, or perform administration work. It is like a control panel used around the service, not the service’s main request loop.

`core/src/ufo/serve.py` is the service launcher. It assembles the running web service: reads configuration, connects storage and owner databases, installs routes and user-facing surfaces, starts background workers, and then hands control to the web server.

## Sub-stages

- [CLI, initialization, and product inspection commands](stage-2.1.md) `stage-2.1` — 1 files

## Files in this stage

### Process entry and service bootstrap
### `core/src/ufo/serve.py`

`entrypoint` · `startup, main loop, background work, shutdown`

This file is the service’s “control room.” A single running process serves many workspaces, so the file’s biggest job is to wire together every shared part while making sure each request and background job is tied to the right workspace. Without this file, the product would have pieces like databases, sandboxes, connectors, model access, background jobs, and web routes, but no safe way to start them together.

At startup, `run` loads config, turns on logging and health checks, opens database connections, loads extension manifests, sets up encrypted credentials, records this process as an active worker, and starts a heartbeat. It then chooses the configured backends for blobs, live updates, terminals, browser control, search, feature flags, connectors, and sandbox networking. Think of it like setting up a factory floor: every station must be present, named correctly, and connected before work begins.

The file also mounts HTTP routes. Extension routes live under `/ext/...`; shared user surfaces live under `/surface/...`. Because one process serves all workspaces, `WorkspaceScopeBoundary` and the route wrappers carefully bind the current workspace before anything reads data, then clear it after the whole response finishes streaming.

Finally, the file registers durable background jobs and starts uvicorn, the web server. On shutdown it drains workflow execution carefully so unfinished work is not accidentally run twice by another process.

#### Function details

##### `_payload_digest`  (lines 217–219)

```
def _payload_digest(payload: object) -> str
```

**Purpose**: Creates a stable fingerprint for a piece of JSON-like data. This is used to tell whether important runtime inputs, such as config, have changed.

**Data flow**: It receives any payload that can be written as JSON. It serializes it in a predictable order, hashes the bytes with SHA-256, and returns a string like `sha256:...`.

**Call relations**: It is a small helper used by `_runtime_identity` when building the identity record for this running service.

*Call graph*: called by 1 (_runtime_identity); 2 external calls (sha256, dumps).


##### `_runtime_identity`  (lines 222–240)

```
def _runtime_identity(config: Config, carrier: CarrierSpec) -> RuntimeIdentity
```

**Purpose**: Builds a record that describes exactly what runtime this process is running. This helps other parts of the system compare the deployed code, image, sandbox setup, and config.

**Data flow**: It reads deployment revision and image values from environment variables, reads config and carrier details, turns key pieces into digests, and returns a `RuntimeIdentity` object. If only one of revision or image is set, it raises an error because the identity would be incomplete.

**Call relations**: `run` calls this during startup after choosing the sandbox carrier. It uses `_payload_digest` to make compact fingerprints of the config and sandbox setup.

*Call graph*: calls 1 internal fn (_payload_digest); called by 1 (run); 3 external calls (__init__, model_dump, runtime_digest).


##### `_assert_no_reserved_routes`  (lines 243–259)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Protects web paths that belong to the onboarding and sign-in gateway. It makes startup fail if this service accidentally registers routes that would be hidden or conflicted by the shared ingress.

**Data flow**: It inspects the FastAPI app’s registered routes, checks whether any path starts with reserved prefixes such as login, logout, join, onboarding, or `/ufo`, and raises an error if it finds conflicts. Otherwise it changes nothing.

**Call relations**: `run` calls it after all routes are mounted and before starting the web server, so route conflicts are caught immediately rather than becoming confusing production behavior.

*Call graph*: called by 1 (run).


##### `run`  (lines 262–522)

```
def run() -> None
```

**Purpose**: Starts the shared UFO fleet process. It is the top-level startup path that connects all services, mounts routes, launches workers, and runs the HTTP server.

**Data flow**: It reads configuration and environment variables, initializes databases and observability, loads extensions, creates shared objects like blob stores, sandboxes, hubs, model registries, connector registries, and background job runners, then starts uvicorn. On exit, it shuts down workflow execution safely.

**Call relations**: This is the main coordinator for the file. It calls nearly every helper here to choose backends, validate requirements, mount routes, register jobs, configure sandbox egress, and clean up through `_stop_executor`.

*Call graph*: calls 23 internal fn (from_env, from_skills, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _preview_settings (+13 more)); 66 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `run.invoker_for`  (lines 328–329)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates a workspace-specific admission helper. Code uses it when it needs to admit work, such as a turn or surface action, for one particular workspace.

**Data flow**: It takes a workspace ID, combines it with the shared `Admission` object created by `run`, and returns an `AdmissionInvoker` bound to that workspace.

**Call relations**: It is defined inside `run` so it can close over the shared admission system. `run` passes it into runtime setup, job launching, site reports, and surface context creation.

*Call graph*: 1 external calls (__init__).


##### `_one_shot`  (lines 525–537)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: Runs one asynchronous database-related operation on a temporary event loop and then cleans up that loop’s database engines. This prevents database connections from being stranded on a loop that has already closed.

**Data flow**: It receives a coroutine, wraps it in a cleanup step, runs it to completion with `asyncio.run`, and returns the coroutine’s result. After the coroutine finishes or fails, the inner step disposes loop-bound database engines.

**Call relations**: `run`, `_proxy_endpoint`, and `_stop_executor` use it for startup or shutdown database work that happens outside the long-lived server loop.

*Call graph*: called by 3 (_proxy_endpoint, _stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 531–535)

```
async def step() -> T
```

**Purpose**: Performs the actual awaited operation for `_one_shot` and guarantees cleanup afterward.

**Data flow**: It awaits the coroutine given to `_one_shot`. Whether that coroutine succeeds or raises an error, it then disposes database engines tied to this temporary loop.

**Call relations**: This nested helper is only used by `_one_shot`; it contains the cleanup rule that makes `_one_shot` safe for short-lived event loops.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 540–554)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Shuts down workflow execution without letting unfinished work run twice. It retires this process’s worker seat only if no workflows are still active.

**Data flow**: It asks DBOS to drain and destroy workflow execution within the configured grace period. It then checks active workflows; if any remain, it logs that the seat is kept. If none remain, it retires the heartbeat seat through `_one_shot`.

**Call relations**: `run` calls this in its `finally` block after uvicorn exits. It hands off to DBOS for workflow shutdown and to the heartbeat object for safe retirement.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 557–571)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string for privileged cross-workspace reads. This is needed for sweep jobs that first list work across all workspaces and then process each workspace safely.

**Data flow**: It looks for the owner database URL in an environment variable first and then in config. If neither exists, it raises a clear startup error; otherwise it returns the URL.

**Call relations**: `run` calls it before initializing the owner database connection, so shared-fleet jobs have the special database access they need.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 574–642)

```
def _launch_jobs(runtime: Runtime, invoker_for: InvokerFactory, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts the durable background jobs this service is responsible for. These jobs include source syncing, turn dispatch, page-change work, delivery cleanup, and extension-provided jobs.

**Data flow**: It receives the runtime, an admission-invoker factory, a sync driver, and a page feed. It builds probes, page-change handling, optional preview rendering, combines core and extension job bindings, filters disabled jobs, and launches a `JobRunner`.

**Call relations**: `run` calls this after the runtime and sync driver are ready. It connects DBOS-backed job scheduling to runtime services like sandboxes, blob storage, models, indexing, and admission.

*Call graph*: called by 1 (run); 14 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, connector_clis (+4 more)).


##### `_source_backends`  (lines 645–659)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of source-sync backends available to the service. A source backend is code that knows how to read from a kind of source, such as a folder or an extension-defined system.

**Data flow**: It starts with the built-in folder backend. Then it scans extension manifests, creates credential access limited to each extension’s declared credential slots, builds each provider, and returns a name-to-backend map. Duplicate backend names cause startup to fail.

**Call relations**: `run` uses this when creating the `SyncDriver`, so configured sources can be matched to exactly one backend implementation.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 662–703)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Builds helpers that can ask a surface who the current user is for source syncing. This lets synced data be tied to the right external identity.

**Data flow**: It scans surface definitions in manifests. For each surface that declares a self-user lookup, it creates an async resolver that binds the workspace, exposes only declared credentials, calls the surface’s handler, and returns the user ID or `None`.

**Call relations**: `run` passes the returned resolver map into the `SyncDriver`. The nested `resolve` function is the actual callback later used during source-sync identity checks.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 676–700)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Resolves one surface user identity inside one workspace. It wraps an extension-provided identity handler with workspace scoping and safe credential access.

**Data flow**: It receives a workspace ID. It creates a restricted credential reader, binds the workspace with `ws(...)`, builds a `SurfaceIdentityContext`, calls the surface’s identity handler, and returns that handler’s user ID result.

**Call relations**: This function is produced by `_source_identity_resolvers` and later used by source syncing. It delegates credential reads to its nested `credential` helper.

*Call graph*: 2 external calls (__init__, ws).


##### `_source_identity_resolvers.resolve.credential`  (lines 682–691)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Reads one credential slot for a surface identity lookup, but only if the surface’s extension declared that slot. This prevents extensions from reading unrelated secrets.

**Data flow**: It receives a credential slot name, checks that the slot is allowed, checks that a credential store exists, then reads and returns the credential for the current workspace.

**Call relations**: It is used only inside `_source_identity_resolvers.resolve` when the surface identity handler asks for a credential.


##### `_select_hub`  (lines 706–724)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live-update hub backend for this process. The hub is the shared channel used to publish and tail live conversation or surface updates.

**Data flow**: It starts with the built-in in-process hub option, adds hub builders from extension manifests, rejects duplicate backend names, then builds the configured backend. If the configured name is unknown, it raises an error.

**Call relations**: `run` calls this during startup before creating tailers, admission, runtime services, and shared surfaces that depend on live updates.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_terminal_transport`  (lines 727–769)

```
def _select_terminal_transport(config: Config, manifests: tuple[Manifest, ...], blob: FleetBlobStore) -> TerminalTransport
```

**Purpose**: Chooses how terminal sessions are connected between browsers, workflows, and sandboxes. It also blocks unsafe combinations where multi-process live updates are used with a terminal transport that only works inside one process.

**Data flow**: It checks config for an invalid in-process terminal plus cross-process hub combination. It builds a set of terminal transport builders from core and extensions, rejects duplicates, and returns the configured transport using the hub URL and blob store.

**Call relations**: `run` calls this while creating the `ConversationSandbox`, so terminal copy-in, copy-out, and session routing are ready before sandboxes are used.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 772–800)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses the browser-control provider, if one is installed and selected. CDP means Chrome DevTools Protocol, a way to control a browser programmatically.

**Data flow**: It scans manifests for CDP provider specs, rejects duplicate backend names, looks up the configured provider, and returns `None` if not found. If found, it checks whether required credentials can be read, then builds the provider with restricted credential access.

**Call relations**: `run` uses this to give the runtime browser-control capability. `_require_cdp_provider` calls it during requirement validation when an extension says browser control is mandatory.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 803–827)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks extension-declared requirements at startup. This makes missing browser, search, or memory backends fail early instead of failing during a user action.

**Data flow**: It reads each manifest’s required seam names, finds the corresponding checker, and runs it against the current config, manifests, and credentials. Unknown seams or failed checks are wrapped in errors that name the extension and missing capability.

**Call relations**: `run` calls this after validating extension tools and before building the rest of the runtime, so the process never starts with an extension whose declared dependencies are unavailable.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 830–844)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a selected browser-control provider is actually available. It is used when an active extension says CDP support is required.

**Data flow**: It calls `_select_cdp_provider`. If that returns `None`, it raises an error naming the missing configured provider.

**Call relations**: _validate_requires calls this through the required-seam checker table when a manifest requires `cdp_providers`.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 847–882)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the web or research search backend for the process. If search is not configured, it can return `None` unless an extension requires search.

**Data flow**: It scans manifests for search provider specs, rejects duplicate backend names, returns `None` if config leaves search unset, or builds the selected provider with restricted credential access. Unknown provider names and missing credential setup raise startup errors.

**Call relations**: `run` calls it to give the runtime optional search ability. `_require_search_provider` calls it when requirement validation says search must exist.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 885–898)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that research search is configured and usable. This prevents research tools from being installed without a real search backend.

**Data flow**: It checks whether the search-provider config value is set. If not, it raises an error; if it is set, it calls `_select_search_provider` to validate and build it.

**Call relations**: _validate_requires calls this through the required-seam checker table when a manifest requires `search_providers`.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_select_flag_provider`  (lines 901–929)

```
def _select_flag_provider(config: Config, manifests: tuple[Manifest, ...]) -> FeatureProvider | None
```

**Purpose**: Chooses the feature-flag provider. Feature flags are runtime switches that let code ask whether a feature should be on or off.

**Data flow**: It gathers flag provider specs from manifests, rejects duplicate backend names, returns `None` when flags are not configured, or builds the selected provider with the known flag declarations. If the selected provider cannot be built because it is unkeyed, it logs a warning and returns `None`.

**Call relations**: `run` calls this before `init_flags`, so feature-flag lookups across the service use the chosen provider or fall back to call-site defaults.

*Call graph*: called by 1 (run); 2 external calls (__init__, warn).


##### `_require_memory_search`  (lines 932–958)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that the default memory-search provider exists and is usable. Memory search lets the system retrieve saved memory-like information through an extension-provided backend.

**Data flow**: It scans manifests for providers with the default memory-search name. It raises an error if none exist, if more than one exists, or if the chosen provider declares credential slots but no credential store is configured.

**Call relations**: _validate_requires calls this through the required-seam checker table when a manifest requires `memory_search`.


##### `_select_auth_proxy`  (lines 970–1007)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy used by connector feed sync when a connector does not have its own broker. This lets host-side code obtain external-service credentials safely.

**Data flow**: It scans manifests for auth-proxy backends, rejects duplicates, chooses the configured backend or the only available backend, checks credentials are configured, and builds the proxy with restricted credential access. Ambiguous or unknown choices raise startup errors.

**Call relations**: _connector_registry calls this while building the connector routing object used by runtime tools and source syncing.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 1010–1058)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient, public_base_url: str | None) -> None
```

**Purpose**: Adds extension-provided web routes under `/ext/<extension>/...`. Each route must first identify the workspace so the handler cannot accidentally read or write data outside that workspace.

**Data flow**: It scans manifests for routes, requires credentials when routes exist, builds an extension context, and adds FastAPI routes. Each route endpoint checks the request identity, binds the workspace with `ws(...)`, and then calls the extension handler.

**Call relations**: `run` calls this after job launch and before mounting shared surfaces. Its nested `endpoint` function is what FastAPI later runs for each extension route request.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 1042–1052)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Serves one extension route request after verifying which workspace it belongs to. Unauthorized requests are stopped before the extension handler runs.

**Data flow**: It receives a web request, asks the route’s identify function for a workspace ID, returns a 401 response if none is found, otherwise binds that workspace and calls the extension handler with its context and request.

**Call relations**: This function is created inside `_mount_ext_routes` for each extension route and handed to FastAPI as the actual request handler.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 1079–1087)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace state at the start and end of every HTTP request. This is a safety guard for a process that serves many workspaces.

**Data flow**: It receives an ASGI request scope plus receive/send callables. For non-HTTP traffic it simply passes through. For HTTP, it sets the current workspace to `None`, calls the wrapped app, and then sets it back to `None` even if an error occurs.

**Call relations**: _mount_shared_surfaces installs this middleware. Surface route handlers set the current workspace during a request, and this boundary guarantees that value does not leak into another request.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 1090–1280)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClie
```

**Purpose**: Mounts the main user-facing surfaces under `/surface/...` for a shared fleet. A surface is a front door such as a chat UI, browser UI, or integration-specific interface.

**Data flow**: It installs workspace-scope middleware, builds shared helpers such as admission, tailing, stopping, durable turn steps, connector access, skill bundles, object schemas, conversation slots, and preview settings. It then scans surface specs, adds routes that identify and bind workspaces per request, starts listeners, mounts the home redirect, and creates durable writeback pollers when needed.

**Call relations**: `run` calls this after runtime and probes are ready. It uses helpers like `_connector_entries`, `home_surface`, `_mount_home`, and its nested `context_for` and `endpoint` functions to connect user requests to runtime services safely.

*Call graph*: calls 5 internal fn (bundled_skills, from_skills, _connector_entries, _mount_home, home_surface); called by 1 (run); 23 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+13 more)).


##### `_mount_shared_surfaces.context_for`  (lines 1173–1214)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the full `SurfaceContext` for one workspace and one surface. This context is the package of safe tools a surface handler uses to admit turns, read blobs, access credentials, list models, use skills, and more.

**Data flow**: It receives a workspace ID and surface name. It combines shared objects captured by `_mount_shared_surfaces` with workspace-specific wrappers like `MemberAdmission`, then returns a `SurfaceContext` loaded with capabilities and metadata.

**Call relations**: Surface endpoints, listeners, writeback pollers, and mid-turn reply pollers use this function whenever they need to run surface code for a specific workspace.

*Call graph*: calls 1 internal fn (home_surface); 3 external calls (__init__, __init__, frame_admissible).


##### `_mount_shared_surfaces.endpoint`  (lines 1243–1257)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Serves one shared-surface HTTP route after resolving the workspace from the request. It is the per-request bridge from a browser call to a workspace-scoped surface handler.

**Data flow**: It receives a request, calls the surface’s identify function with surface auth, returns an early response if identify produced one, returns 401 if identity is missing, otherwise sets the current workspace and calls the route handler with a freshly built `SurfaceContext`.

**Call relations**: This nested function is created for each surface route inside `_mount_shared_surfaces` and registered with FastAPI.

*Call graph*: 2 external calls (Response, set).


##### `home_surface`  (lines 1283–1290)

```
def home_surface(manifests: tuple[Manifest, ...]) -> str | None
```

**Purpose**: Finds the one surface marked as the browser home page. This lets the bare service URL redirect users to the right front door.

**Data flow**: It scans all surface specs in all manifests for the `home` marker. If more than one surface claims to be home, it raises an error; if exactly one does, it returns its name; otherwise it returns `None`.

**Call relations**: `run`, `_mount_shared_surfaces`, `_mount_ext_routes`, `_mount_home`, `_connect_flow`, and surface context creation use it whenever they need the deploy’s default browser destination.

*Call graph*: called by 6 (_connect_flow, _mount_ext_routes, _mount_home, _mount_shared_surfaces, context_for, run).


##### `_mount_home`  (lines 1293–1305)

```
def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None
```

**Purpose**: Adds a simple `GET /` route that redirects to the configured home surface. This makes the bare host useful instead of returning a missing-page response.

**Data flow**: It asks `home_surface` for the home surface name. If there is none, it does nothing. If there is one, it registers a FastAPI route whose handler redirects to `/surface/<name>`.

**Call relations**: _mount_shared_surfaces calls this after mounting surface routes, so the home redirect points to a route that already exists.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (_mount_shared_surfaces); 1 external calls (add_route).


##### `_mount_home.home`  (lines 1302–1303)

```
async def home(_request: Request) -> Response
```

**Purpose**: Redirects a browser from `/` to the chosen home surface. It is intentionally small because the surface itself decides what the user should see next.

**Data flow**: It ignores the request body and returns a 303 redirect response to the home surface path.

**Call relations**: This nested handler is registered by `_mount_home` as the `GET /` endpoint.

*Call graph*: 1 external calls (RedirectResponse).


##### `_serve_lifespan`  (lines 1309–1338)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs app-loop background tasks for as long as the web server is alive. These tasks recover stranded work, reconcile cancellations, run surface writebacks, and start surface listeners.

**Data flow**: When the app starts, it registers configured sources and creates a task group. It starts recovery, cancellation, stranded-turn, poller, and listener tasks. When the app shuts down, it cancels those tasks.

**Call relations**: `run` passes this as the FastAPI lifespan function. It uses objects stored on `app.state` during startup, such as DBOS client, pollers, listeners, and configured sources.

*Call graph*: 5 external calls (__init__, __init__, __init__, TaskGroup, register_sources).


##### `_preview_settings`  (lines 1341–1350)

```
def _preview_settings(config: Config) -> tuple[tuple[str, int], str] | None
```

**Purpose**: Reads and validates sandbox preview-service settings. The preview service renders or previews content produced inside sandboxes.

**Data flow**: It parses the preview-service setting from config. If preview is disabled, it returns `None`. If enabled, it requires a preview token from the environment and returns the parsed host/port plus token.

**Call relations**: `run` uses it to configure document rendering and site previewing. `_proxy_endpoint` uses it so sandbox egress rules can include the preview token when previewing is enabled.

*Call graph*: called by 2 (_proxy_endpoint, run); 1 external calls (parse_preview_service).


##### `_proxy_endpoint`  (lines 1353–1430)

```
def _proxy_endpoint(app: FastAPI, config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: FilesystemBlobStore | S3BlobS
```

**Purpose**: Sets up sandbox network egress control and returns the proxy details that sandboxes need. Egress means outbound network traffic from a sandbox.

**Data flow**: It checks whether the deployment uses a hosted proxy or local/default proxy setup, obtains or creates a certificate authority certificate and control token, validates optional cache settings, builds per-agent network rules, creates an `EgressControl` router, mounts its routes on the app, and returns a `ProxyEndpoint` with port, certificate, and public URL.

**Call relations**: `run` calls this while creating the `ConversationSandbox`. It calls `_preview_settings`, `_one_shot`, and `_ephemeral_egress_ca`, and it installs routers that the separate egress proxy process calls back into.

*Call graph*: calls 3 internal fn (_ephemeral_egress_ca, _one_shot, _preview_settings); called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, include_router, token_urlsafe, parse_cache_daemon, connector_clis, injecting_slots, model_rule_base (+3 more)).


##### `_ephemeral_egress_ca`  (lines 1433–1452)

```
def _ephemeral_egress_ca() -> str
```

**Purpose**: Creates a temporary certificate authority certificate for local runs that do not provide a shared egress certificate. A certificate authority is a trust anchor used to decide whether generated TLS certificates should be trusted.

**Data flow**: It generates a private key, builds a self-signed certificate named for local egress, marks it as a CA certificate, signs it, and returns the certificate text in PEM format. It does not return the signing key.

**Call relations**: _proxy_endpoint calls it only when no hosted proxy certificate is configured and no local shared certificate was supplied.

*Call graph*: called by 1 (_proxy_endpoint); 9 external calls (generate_private_key, SHA256, BasicConstraints, CertificateBuilder, Name, NameAttribute, random_serial_number, now, timedelta).


##### `_connector_registry`  (lines 1458–1472)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the central connector registry. Connectors are integrations with outside services, and the registry lets tools and sync jobs find the right connector provider.

**Data flow**: It gathers connector entries from manifests, creates a namespace resolver, selects a fallback auth proxy if needed, and returns a `ConnectorRegistry`.

**Call relations**: `run` calls this before creating runtime services. It relies on `_connector_entries` for provider metadata and `_select_auth_proxy` for fallback credential access.

*Call graph*: calls 2 internal fn (_connector_entries, _select_auth_proxy); called by 1 (run); 2 external calls (__init__, open_connector_namespace).


##### `_connector_entries`  (lines 1475–1485)

```
def _connector_entries(manifests: tuple[Manifest, ...]) -> dict[str, ConnectorEntry]
```

**Purpose**: Collects connector provider metadata from extension manifests. It makes sure no two extensions claim the same provider name.

**Data flow**: It scans each connector declaration, reads its OAuth provider name, label, and broker, creates a `ConnectorEntry`, and returns a provider-name map. Duplicate provider names raise an error.

**Call relations**: _connector_registry uses it to build the main connector registry, and `_mount_shared_surfaces` uses it when it needs a default registry for surface contexts.

*Call graph*: called by 2 (_connector_registry, _mount_shared_surfaces); 1 external calls (__init__).


##### `_connect_flow`  (lines 1488–1527)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...], index: IndexBackend | None=None, embed: EmbedClient | None=None, resumption: ConnectResume | Non
```

**Purpose**: Builds the OAuth connection flow for connectors. OAuth is the common browser-based sign-in process used to grant an app access to another service.

**Data flow**: If no credential store exists, it returns `None` because grants cannot be safely stored. Otherwise it collects OAuth providers from manifests, rejects duplicate provider names, builds callback and portal URLs, attaches connection hooks, and returns a `ConnectFlow` that can validate, authorize, and complete connector setup.

**Call relations**: `run` passes its result to `install_connect_flow`. It uses `_connect_redirect_uri` and `home_surface` to create URLs that both the member’s browser and external providers can use.

*Call graph*: calls 2 internal fn (_connect_redirect_uri, home_surface); called by 1 (run); 5 external calls (__init__, __init__, connection_hooks, open_connector_namespace, portal_url).


##### `_connect_redirect_uri`  (lines 1530–1556)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL for connector providers. This URL must be something a real browser can open.

**Data flow**: It reads `connect.public_base_url` from config. If no providers are installed, it returns a best-effort callback path or an empty string. If providers exist, it requires a proper `http` or `https` URL with a host, rejects wildcard bind addresses, and returns the callback URL.

**Call relations**: _connect_flow calls this while constructing the connector OAuth flow, so provider handoffs use one trusted callback URL.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).

## 📊 State Registers Touched

- `reg-effective-config` — The merged service settings that tell the process how this deployment should run.
- `reg-schema-version` — The database upgrade position that says which schema changes have already been applied.
- `reg-persistence-handles` — The shared database and blob-storage connections used to read and save durable system data.
- `reg-egress-sandbox-policy` — The shared rules for sandbox network access, proxy behavior, internet permission, and exposed secrets.
- `reg-runtime-fleet-liveness` — The shared record of running service and worker instances, heartbeats, listener claims, and stuck work.
- `reg-telemetry-context` — The shared trace, metric, log, health, and redaction context used to observe work across the system.
- `reg-build-metadata` — The product, package, version, and build identity exposed to CLI/admin surfaces, health checks, and telemetry.
- `reg-shared-infra-clients` — Long-lived non-database infrastructure clients and connection pools such as Redis, HTTP, provider, and service clients shared by workers and request handlers.
