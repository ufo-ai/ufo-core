# Server assembly, route mounting, and surface ingress  `stage-5`

This stage is where the service is put together and its outside doors are opened. It is part of startup, but it also shapes the main request path after the server is running. The central file, core/src/ufo/serve.py, is like the building manager: it starts the web server, connects databases and background workers, loads extensions, and makes sure each request is matched to the correct workspace.

Once the host is assembled, the authentication pieces act as the badge desk. They sign people in, finish OAuth account-linking flows, and create signed tokens, which are small tamper-proof passes that prove a request belongs to a trusted user, workspace, or browser entry point.

The mounted surfaces are the actual doors people and systems use. The web app, Slack, iMessage, terminal, debugger, and related tools each receive events in their own format. Shared surface code translates those events into the system’s normal records: workspace, member, conversation, and message turns. Together, these parts let outside activity safely enter UFO and become work the runtime can understand.

## Sub-stages

- [Authentication, login, OAuth, and signed ingress tokens](stage-5.1.md) `stage-5.1` — 12 files
- [Mounted user-facing and operator-facing surfaces](stage-5.2.md) `stage-5.2` — 19 files

## Files in this stage

### Server assembly, route mounting, and surface ingress
### `core/src/ufo/serve.py`

`entrypoint` · `startup, request handling, background work, shutdown`

This file is the place where many separate parts of the system are plugged together into one running service. Think of it like opening a theater for the day: it unlocks the doors, checks the stage equipment, assigns staff, starts background crews, and then lets the audience in. Here, the “audience” is HTTP requests, surface clients, background jobs, sandboxes, connectors, and extension routes.

The service is shared across many workspaces, so one of its most important jobs is safety: every request must be clearly linked to one workspace before it reads data or credentials. The file installs middleware to clear old workspace state, mounts surface routes that identify the caller, and builds per-workspace context objects for handlers.

At startup it loads configuration and extension manifests, checks databases, prepares encrypted credential storage, chooses optional backends such as search, browser control, feature flags, hubs, terminal transport, blob storage, and sandbox egress control, then registers DBOS workflow jobs. DBOS is the durable workflow system used to keep long-running work recoverable after crashes.

It also owns shutdown behavior. It tries to drain running workflows before retiring this process’s executor “seat,” so another process does not accidentally run the same workflow at the same time.

#### Function details

##### `_payload_digest`  (lines 216–218)

```
def _payload_digest(payload: object) -> str
```

**Purpose**: Creates a stable fingerprint for a JSON-like value. This is used to record exactly which configuration or sandbox settings a running service used.

**Data flow**: It receives a payload object, turns it into JSON with keys sorted so the same data always produces the same text, hashes that text with SHA-256, and returns a string starting with `sha256:`.

**Call relations**: It is called by `_runtime_identity` when building the runtime identity record, so configuration and sandbox details can be compared by digest instead of storing large raw data.

*Call graph*: called by 1 (_runtime_identity); 2 external calls (sha256, dumps).


##### `_runtime_identity`  (lines 221–239)

```
def _runtime_identity(config: Config, carrier: CarrierSpec) -> RuntimeIdentity
```

**Purpose**: Builds a compact identity record for this running service version. It captures the runtime revision, container image digest, main configuration digest, and sandbox setup digest.

**Data flow**: It reads runtime revision and image values from environment variables, checks that they are either both present or both absent, combines them with the loaded config and carrier details, and returns a `RuntimeIdentity` object.

**Call relations**: It is called during `run` after sandbox carriers are selected. It relies on `_payload_digest` to make stable fingerprints that are later exposed to surfaces as part of runtime information.

*Call graph*: calls 1 internal fn (_payload_digest); called by 1 (run); 3 external calls (__init__, model_dump, runtime_digest).


##### `_assert_no_reserved_routes`  (lines 242–258)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Protects URL paths that belong to the onboarding and sign-in gateway. Without this check, the service could mount a route that is silently hidden by the front-door routing layer.

**Data flow**: It inspects the FastAPI app’s registered routes, finds any route whose path starts with a reserved prefix such as `/login` or `/ufo`, and raises an error if any conflicts are found.

**Call relations**: It is called near the end of `run`, after all routes have been mounted, as a final startup safety check before the server begins accepting traffic.

*Call graph*: called by 1 (run).


##### `run`  (lines 261–508)

```
def run() -> None
```

**Purpose**: Starts the shared UFO service process. It loads configuration, creates all major runtime objects, registers routes and jobs, launches DBOS workflows, then runs the HTTP server.

**Data flow**: It reads config and environment variables, initializes observability, databases, credentials, extensions, blob storage, sandboxes, connectors, models, memory, surfaces, jobs, and middleware. It then starts Uvicorn, and on exit drains DBOS work and retires the process seat when safe.

**Call relations**: This is the top-level entry used to bring the service alive. It calls most helper functions in this file to select backends, mount routes, launch background jobs, prepare sandbox proxy settings, and clean up on shutdown.

*Call graph*: calls 23 internal fn (from_env, from_skills, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _preview_settings (+13 more)); 59 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `run.invoker_for`  (lines 327–328)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates an admission helper for one workspace. Admission is the step that accepts a member action and turns it into durable work.

**Data flow**: It receives a workspace ID, combines it with the shared `Admission` object created by `run`, and returns an `AdmissionInvoker` bound to that workspace.

**Call relations**: It is defined inside `run` so it can close over the shared admission object. `run` passes it into runtime setup, site reports, source sync, job launch, and delivery-related components whenever they need workspace-specific admission.

*Call graph*: 1 external calls (__init__).


##### `_one_shot`  (lines 511–523)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: Runs one asynchronous database-related task on a temporary event loop and then cleans up that loop’s database connections. This avoids leaving pooled connections attached to a loop that has already been closed.

**Data flow**: It receives a coroutine, wraps it in a cleanup step, runs that step with `asyncio.run`, and returns the coroutine’s result.

**Call relations**: It is used by `run` for startup database checks and seat recording, by `_proxy_endpoint` when deriving artifact-store network rules, and by `_stop_executor` when retiring the heartbeat seat.

*Call graph*: called by 3 (_proxy_endpoint, _stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 517–521)

```
async def step() -> T
```

**Purpose**: Performs the actual one-time async operation and guarantees database engine cleanup afterward.

**Data flow**: It awaits the coroutine given to `_one_shot`; whether it succeeds or fails, it then calls database engine disposal for the current loop before returning or re-raising.

**Call relations**: It is an internal helper used only by `_one_shot`. Its job is to make the cleanup inseparable from the one-time async operation.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 526–540)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Shuts down DBOS execution carefully so durable workflows are not accidentally run twice. It retires this process’s executor seat only if no workflows are still active.

**Data flow**: It asks DBOS to drain and destroy workflow execution within the configured grace period, checks the active workflow set, logs and keeps the seat if work remains, or retires the heartbeat seat if the executor is empty.

**Call relations**: It is called in `run` after Uvicorn exits. It uses `_one_shot` to run the async heartbeat retirement safely during shutdown.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 543–557)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string for owner-level cross-workspace reads. This is needed for background sweeps that first list work across all workspaces and then re-enter each workspace safely.

**Data flow**: It reads the owner database URL from an environment variable or config. If neither is present, it raises a clear startup error; otherwise it returns the chosen connection string.

**Call relations**: It is called by `run` before initializing the owner database connection. Without it, shared-service background jobs that enumerate workspaces would fail or read under the wrong database security context.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 560–627)

```
def _launch_jobs(runtime: Runtime, invoker_for: InvokerFactory, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts durable background jobs. These jobs cover source syncing, turn dispatch, page-change work, delivery sweeps, previews, and extension-provided work.

**Data flow**: It receives the assembled runtime, admission factory, sync driver, and page feed. It builds probe access, page-change runners, optional preview rendering, combines core and extension job bindings, filters disabled jobs, and launches a `JobRunner`.

**Call relations**: It is called by `run` after runtime setup and DBOS launch. It hands DBOS the job definitions that later run independently of web requests.

*Call graph*: called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, connector_clis (+3 more)).


##### `_source_backends`  (lines 630–644)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of source-sync backends available in this deployment. A source backend knows how to read from a kind of source, such as a folder or an extension-defined provider.

**Data flow**: It starts with the built-in folder backend, then walks extension manifests and asks each source provider to build itself with access only to that extension’s declared credential slots. It returns a backend-name-to-backend map and fails if two providers reuse a name.

**Call relations**: It is called by `run` while creating the `SyncDriver`. The sync driver later uses this map to match configured sources to the code that can sync them.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 647–688)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Creates helpers that can ask surfaces who the current source-side user is. This lets sync code match external identities to the correct workspace user.

**Data flow**: It scans surfaces in extension manifests for a `self_user_id` handler, wraps each one with workspace binding and credential checks, and returns a map from surface name to resolver function.

**Call relations**: It is called by `run` when building the source sync driver. The resolver functions it creates are later called by source-sync code when identity information is needed.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 661–685)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Runs one surface’s identity lookup inside the correct workspace. It gives the surface a safe context with blob access and a controlled credential reader.

**Data flow**: It receives a workspace ID, binds that workspace, creates a `SurfaceIdentityContext`, calls the surface’s identity handler, and returns the user ID string or `None`.

**Call relations**: It is produced by `_source_identity_resolvers` for each eligible surface. Source syncing calls it when it needs to know the surface’s view of the current user.

*Call graph*: 2 external calls (__init__, ws).


##### `_source_identity_resolvers.resolve.credential`  (lines 667–676)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Reads one declared credential for a surface identity lookup. It prevents a surface from reading credential slots it did not declare.

**Data flow**: It receives a credential slot name, checks that the slot belongs to the surface’s extension, checks that a credential store exists, then reads and returns the credential for the current workspace.

**Call relations**: It is used inside `_source_identity_resolvers.resolve` as the credential callback passed to a surface identity handler.


##### `_select_hub`  (lines 691–709)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live message hub for this process. The hub is the shared channel that lets surfaces and workers exchange live updates.

**Data flow**: It creates a table of hub builders from the built-in in-process hub and extension-registered hubs, checks for duplicate backend names, selects the configured backend, and returns the built hub.

**Call relations**: It is called by `run` early in service assembly. The returned hub is then passed to admission, tailing, stopping, surfaces, and the runtime.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_terminal_transport`  (lines 712–754)

```
def _select_terminal_transport(config: Config, manifests: tuple[Manifest, ...], blob: FleetBlobStore) -> TerminalTransport
```

**Purpose**: Chooses how terminal sessions are connected between users and sandboxed work. It refuses unsafe combinations where a multi-process fleet would use a terminal transport that only works inside one process.

**Data flow**: It checks hub and terminal backend settings, builds a table of terminal transport builders from core and extensions, selects the configured transport, and returns it using the hub URL and fleet blob store.

**Call relations**: It is called by `run` while creating the conversation sandbox. The chosen transport is handed into sandbox setup so terminal traffic can rendezvous correctly.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 757–785)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses the browser-control provider, if one is installed and selected. CDP means Chrome DevTools Protocol, a way for software to drive a browser.

**Data flow**: It scans extension manifests for CDP providers, rejects duplicate names, finds the configured provider, checks credential availability when needed, and returns a built provider or `None`.

**Call relations**: It is called directly by `run` for runtime setup and by `_require_cdp_provider` during extension requirement checks.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 788–812)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that every active extension’s required system seam is actually available. A seam is an integration point, such as browser control or search.

**Data flow**: It walks all manifests, looks up each declared requirement in the built-in requirement-check table, runs the check, and raises a startup error that names the extension and missing seam if anything fails.

**Call relations**: It is called by `run` before the service starts serving. It delegates to requirement helpers such as `_require_cdp_provider`, `_require_search_provider`, and `_require_memory_search`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 815–829)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that browser control is available when an extension says it needs it.

**Data flow**: It asks `_select_cdp_provider` to resolve the configured provider. If no provider is returned, it raises a startup error.

**Call relations**: It is used through `_REQUIRED_SEAM_CHECKS` by `_validate_requires`. It turns a missing optional browser backend into a hard failure only for extensions that require it.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 832–867)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the search provider for research tools. If no search provider is configured, it can return `None` so deployments without research support can still start.

**Data flow**: It scans manifests for search providers, rejects duplicate names, checks the configured name, verifies credential storage exists, builds the provider with scoped credential access, and returns it.

**Call relations**: It is called by `run` to install the provider into the runtime, and by `_require_search_provider` when an extension declares search as mandatory.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 870–883)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a usable search provider is configured when research tools require one.

**Data flow**: It first checks that the search-provider config value is set, then calls `_select_search_provider` so unknown names, duplicate providers, or missing credentials fail immediately.

**Call relations**: It is used through `_REQUIRED_SEAM_CHECKS` by `_validate_requires`. This keeps research extensions from failing later during a user action.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_select_flag_provider`  (lines 886–913)

```
def _select_flag_provider(config: Config, manifests: tuple[Manifest, ...]) -> FeatureProvider | None
```

**Purpose**: Chooses the feature-flag provider, if configured. Feature flags are runtime switches that let code ask whether a feature should be on or off.

**Data flow**: It scans extension manifests for flag providers, rejects duplicate backend names, returns `None` if no backend is configured, otherwise builds the selected provider and warns if the provider cannot be keyed.

**Call relations**: It is called by `run`, and its result is passed to flag initialization so the rest of the service can evaluate flags consistently.

*Call graph*: called by 1 (run); 2 external calls (__init__, warn).


##### `_require_memory_search`  (lines 916–942)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that exactly one default memory-search provider is installed and usable. Memory search is the feature that lets the system look up stored contextual information.

**Data flow**: It scans manifests for the default memory-search provider name, fails if none or more than one is found, then checks whether declared credential slots require a credential store.

**Call relations**: It is used through `_REQUIRED_SEAM_CHECKS` by `_validate_requires` when an extension declares that memory search is required.


##### `_select_auth_proxy`  (lines 954–991)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy for connectors. This proxy helps connector sync code obtain credentials when a connector does not use its own broker.

**Data flow**: It scans extension manifests for auth-proxy backends, handles automatic selection when there is exactly one, validates the configured backend, checks credential storage, and returns the built proxy or `None`.

**Call relations**: It is called by `_connector_registry`, which includes the selected fallback proxy in the connector registry used by tools and sync jobs.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 994–1042)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient, public_base_url: str | None) -> None
```

**Purpose**: Adds extension-owned HTTP routes under `/ext/<extension>/...`. Each route must identify a workspace before it can run.

**Data flow**: It walks extension manifests, builds an extension context with credential and indexing access, wraps each route handler with workspace identification and binding, and registers the route on the FastAPI app.

**Call relations**: It is called by `run` after core setup. The nested endpoint it creates is later called by FastAPI whenever a browser or provider callback hits an extension route.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 1026–1036)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Processes one request to an extension route after checking which workspace it belongs to.

**Data flow**: It receives a web request, calls the route’s identify function, returns `401 unauthorized` if identification fails, otherwise binds the identified workspace and calls the extension route handler.

**Call relations**: It is registered by `_mount_ext_routes` with FastAPI. FastAPI calls it during request handling, and it hands control to the extension’s handler only after workspace safety is established.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 1063–1071)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace state at the start and end of every HTTP request. This prevents one request’s workspace identity from leaking into another request.

**Data flow**: It receives the raw ASGI request scope, receive function, and send function. For non-HTTP traffic it simply forwards the call; for HTTP it clears `current_workspace`, runs the downstream app, and clears it again in a final cleanup step.

**Call relations**: It is installed by `_mount_shared_surfaces` as middleware. It surrounds all mounted shared-surface request handling and works with surface endpoints that set `current_workspace` after authenticating the request.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 1074–1262)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClie
```

**Purpose**: Mounts all shared-fleet surface routes and background surface helpers. A surface is a user-facing integration point, such as a browser UI or chat-like interface.

**Data flow**: It installs workspace-scope middleware, builds shared objects such as admission, tailing, stopping, skills, connector access, object schemas, and conversation slots, then registers each surface route with workspace identification. It also creates listener runners and durable writeback pollers when needed.

**Call relations**: It is called by `run` after runtime, credentials, sandboxes, hub, and models are ready. It calls helper functions such as `_connector_entries`, `home_surface`, and `_mount_home`, and the routes it registers are later invoked by FastAPI.

*Call graph*: calls 5 internal fn (bundled_skills, from_skills, _connector_entries, _mount_home, home_surface); called by 1 (run); 23 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+13 more)).


##### `_mount_shared_surfaces.context_for`  (lines 1156–1196)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the per-request `SurfaceContext` given to a surface handler. This context is the surface’s toolbox for the current workspace.

**Data flow**: It receives a workspace ID and surface name, combines them with shared services such as blob storage, sandboxes, admission, credentials, skills, memory, models, connectors, runtime identity, preview settings, and object metadata, then returns a `SurfaceContext`.

**Call relations**: It is defined inside `_mount_shared_surfaces` so it can capture all assembled service pieces. Surface endpoints, listeners, and pollers use it whenever they need to run surface code for a specific workspace.

*Call graph*: calls 1 internal fn (home_surface); 3 external calls (__init__, __init__, frame_admissible).


##### `_mount_shared_surfaces.endpoint`  (lines 1225–1239)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Processes one request to a shared surface route. It authenticates or identifies the workspace before allowing the surface handler to run.

**Data flow**: It receives a web request, asks the surface’s identify function to resolve it, returns a response directly if identify provides one, returns `401` if unresolved, otherwise sets the current workspace and calls the route handler with a fresh `SurfaceContext`.

**Call relations**: It is registered by `_mount_shared_surfaces` for each surface route. It is called by FastAPI during request handling and is protected by `WorkspaceScopeBoundary` cleanup around the whole response.

*Call graph*: 2 external calls (Response, set).


##### `home_surface`  (lines 1265–1272)

```
def home_surface(manifests: tuple[Manifest, ...]) -> str | None
```

**Purpose**: Finds the one surface marked as the browser home. This lets the bare service URL redirect users to the right front door.

**Data flow**: It scans all manifest surfaces for the `home` marker, raises an error if more than one surface claims that role, and returns the home surface name or `None`.

**Call relations**: It is used by `run`, `_mount_ext_routes`, `_mount_shared_surfaces`, `_mount_shared_surfaces.context_for`, and `_mount_home` whenever code needs to know the deploy’s main browser-facing surface.

*Call graph*: called by 5 (_mount_ext_routes, _mount_home, _mount_shared_surfaces, context_for, run).


##### `_mount_home`  (lines 1275–1287)

```
def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None
```

**Purpose**: Adds a simple `GET /` route that redirects to the configured home surface. This makes the service root useful instead of returning a 404.

**Data flow**: It calls `home_surface`; if there is no home surface it does nothing. Otherwise it creates a small handler that redirects to `/surface/<home>` and registers it on the app.

**Call relations**: It is called by `_mount_shared_surfaces` after surface routes are mounted. Its nested `home` handler is later called by FastAPI when someone visits the bare host.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (_mount_shared_surfaces); 1 external calls (add_route).


##### `_mount_home.home`  (lines 1284–1285)

```
async def home(_request: Request) -> Response
```

**Purpose**: Redirects a browser from `/` to the home surface route.

**Data flow**: It receives the request but does not need to inspect it. It returns a `303` redirect response pointing at the selected surface path.

**Call relations**: It is created by `_mount_home` and registered with FastAPI as the service’s root route.

*Call graph*: 1 external calls (RedirectResponse).


##### `_serve_lifespan`  (lines 1291–1320)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs app-loop background tasks while the web server is alive. These tasks recover stranded work, reconcile cancellations, run surface writebacks, and start listeners.

**Data flow**: On startup it registers configured sources, creates a task group, starts recovery and reconciliation tasks plus any pollers and surface listeners, yields control while the app runs, then cancels those tasks during shutdown.

**Call relations**: It is passed to the FastAPI app by `run` as the lifespan manager. FastAPI enters it when Uvicorn starts serving and exits it during shutdown.

*Call graph*: 5 external calls (__init__, __init__, __init__, TaskGroup, register_sources).


##### `_preview_settings`  (lines 1323–1332)

```
def _preview_settings(config: Config) -> tuple[tuple[str, int], str] | None
```

**Purpose**: Reads and validates sandbox preview-service settings. The preview service is an optional helper that renders or previews content from sandbox work.

**Data flow**: It parses the configured preview service address. If previewing is disabled it returns `None`; if enabled it requires a preview token from the environment and returns the service address plus token.

**Call relations**: It is called by `run` when setting up document rendering and site previewing, and by `_proxy_endpoint` so sandbox egress rules can include preview access.

*Call graph*: called by 2 (_proxy_endpoint, run); 1 external calls (parse_preview_service).


##### `_proxy_endpoint`  (lines 1335–1412)

```
def _proxy_endpoint(app: FastAPI, config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: FilesystemBlobStore | S3BlobS
```

**Purpose**: Sets up sandbox network egress control. Egress means outbound network access from sandboxes, and this function gives sandboxes the proxy details while mounting the server-side policy API.

**Data flow**: It reads required certificates and control tokens from config or environment, creates local defaults when allowed, validates cache and preview settings, builds per-agent network rules, creates `EgressControl`, mounts its routers on the app, and returns a `ProxyEndpoint` for sandbox setup.

**Call relations**: It is called by `run` while creating the conversation sandbox. It uses `_preview_settings`, `_ephemeral_egress_ca`, and `_one_shot`, and it wires the returned endpoint into sandbox carrier configuration.

*Call graph*: calls 3 internal fn (_ephemeral_egress_ca, _one_shot, _preview_settings); called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, include_router, token_urlsafe, parse_cache_daemon, connector_clis, injecting_slots, model_rule_base (+3 more)).


##### `_ephemeral_egress_ca`  (lines 1415–1434)

```
def _ephemeral_egress_ca() -> str
```

**Purpose**: Creates a temporary certificate authority certificate for local egress setup when no shared certificate is supplied. A certificate authority is the trust anchor used to verify proxy certificates.

**Data flow**: It generates a private key, builds a self-signed CA certificate named `ufo-egress-local`, valid for a long period, and returns the certificate text in PEM format. The signing key is not returned.

**Call relations**: It is called by `_proxy_endpoint` only for local boots without a configured egress CA. Hosted deployments must provide a real shared certificate instead.

*Call graph*: called by 1 (_proxy_endpoint); 9 external calls (generate_private_key, SHA256, BasicConstraints, CertificateBuilder, Name, NameAttribute, random_serial_number, now, timedelta).


##### `_connector_registry`  (lines 1440–1454)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the central connector registry. Connectors are integrations with outside services, and the registry tells tools and sync jobs which providers exist and how authentication should be resolved.

**Data flow**: It gathers connector entries from manifests, opens the connector namespace resolver, selects a fallback auth proxy, and returns a `ConnectorRegistry`.

**Call relations**: It is called by `run` during startup. It delegates to `_connector_entries` and `_select_auth_proxy`, and the resulting registry is passed into the runtime and shared surfaces.

*Call graph*: calls 2 internal fn (_connector_entries, _select_auth_proxy); called by 1 (run); 2 external calls (__init__, open_connector_namespace).


##### `_connector_entries`  (lines 1457–1467)

```
def _connector_entries(manifests: tuple[Manifest, ...]) -> dict[str, ConnectorEntry]
```

**Purpose**: Extracts connector provider records from extension manifests. It guarantees that two extensions do not claim the same OAuth provider name.

**Data flow**: It scans each manifest connector, checks for duplicate provider names, creates a `ConnectorEntry` with provider, label, and broker information, and returns a provider-name map.

**Call relations**: It is called by `_connector_registry` and by `_mount_shared_surfaces` when a registry needs to be built from manifests.

*Call graph*: called by 2 (_connector_registry, _mount_shared_surfaces); 1 external calls (__init__).


##### `_connect_flow`  (lines 1470–1508)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...], index: IndexBackend | None=None, embed: EmbedClient | None=None, resumption: ConnectResume | Non
```

**Purpose**: Creates the OAuth connect flow used to authorize external connectors. OAuth is the browser-based handoff where a user grants this service access to another service.

**Data flow**: If there is no credential store it returns `None`. Otherwise it collects OAuth providers from manifests, rejects duplicate provider names, builds the external redirect URI, installs connection hooks and labels, and returns a `ConnectFlow`.

**Call relations**: It is called by `run`, and the result is installed globally with `install_connect_flow`. It uses `_connect_redirect_uri` to validate the callback URL.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 4 external calls (__init__, __init__, connection_hooks, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 1511–1537)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL. This must be a real URL a user’s browser can open after an external provider redirects back.

**Data flow**: It reads `connect.public_base_url`, allows an empty value only when no providers exist, parses the URL, rejects missing scheme or host and wildcard bind addresses, then appends the connector callback path.

**Call relations**: It is called by `_connect_flow` while building connector authorization support. Its checks prevent connector setup from failing later in a user’s browser.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).

## 📊 State Registers Touched

- `reg-config-stack` — The merged settings that tell the whole service how to start, connect, and behave.
- `reg-feature-flags` — The shared on/off switches and rollout choices that let operators change behavior without redeploying.
- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-database-schema` — The durable database layout and connection layer used to store and retrieve system records safely.
- `reg-workspace-directory` — The saved list of workspaces, members, agents, admins, and workspace-level settings.
- `reg-auth-sessions` — The sign-in state and signed tokens that prove who a web, surface, or API request belongs to.
- `reg-acting-authority` — The shared record of whether work is acting as a member, an agent, or only the workspace.
- `reg-credentials-connections` — The stored secrets, connected accounts, grants, and refreshable permissions used to call outside services.
- `reg-access-subjects` — The shared visibility rules that say which members or audiences may read conversations, sources, and objects.
- `reg-conversation-transcript` — The saved conversation history, turns, compactions, titles, audiences, and generated references.
- `reg-turn-queue` — The durable queue of conversation turns waiting, running, parked, resumed, or blocked as duplicates.
- `reg-surface-routing` — The saved routing state that maps web, Slack, iMessage, terminal, and other surfaces to workspaces and agents.
- `reg-inbound-message-queue` — The durable inbox of external messages waiting to be admitted into conversations exactly once.
- `reg-runtime-instances` — The shared record of which server processes are alive and which background or surface duties they have claimed.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
- `reg-conversation-slots-ui` — The shared side-panel and workspace UI state for artifacts, sources, tasks, sites, automations, and app home screens.
- `reg-surface-outbox` — The durable outbound delivery buffer and writeback markers for replies or mid-turn messages that must be sent to external surfaces exactly once.
- `reg-runtime-message-bus` — Shared Redis/pub-sub or message-hub connection state used to coordinate live updates, workers, and cross-process runtime events.
- `reg-model-client-pools` — Shared outbound model/provider client sessions, connection pools, retry state, and provider-side rate-limit/backoff buckets used across turns.
