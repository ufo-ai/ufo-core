# Server Startup, Route Mounting, and Control Surfaces  `stage-5`

This stage is the front door for the Python service. It belongs to startup: the moment when the service reads its settings, connects to the things it depends on, and makes its web addresses available. The main file, `core/src/ufo/serve.py`, is the entry point, meaning it is the place the process starts from.

During startup, it loads configuration, opens database connections, and starts or connects to background workers that do jobs outside the main request flow. It then wires in extensions and mounts HTTP routes. A route is the rule that says, “when a request comes to this web address, send it to this code.” These routes make many surfaces reachable: the main web app, Slack and iMessage integrations, terminal and debugger tools, memory exploration, billing, artifact downloads, OAuth login callbacks, and private operator control APIs. In short, this stage assembles the service’s control panel and public doorways before normal traffic begins.

## Files in this stage

### Server Startup, Route Mounting, and Control Surfaces
### `core/src/ufo/serve.py`

`entrypoint` · `startup, request handling, background work, shutdown`

Think of this file as the control room for one running UFO fleet process. It does not implement every feature itself. Instead, it gathers all the parts the service needs: the database, encrypted credential storage, blob storage, model registry, sandbox runner, live message hub, extension routes, connector login flow, background jobs, and HTTP server.

The most important job here is safe sharing. One process can serve many workspaces, so every incoming request or background action must be tied to the correct workspace before it reads or writes data. The file installs a small boundary around HTTP requests to clear old workspace state, set the right one, and clear it again when the response is fully finished.

Startup is deliberately strict. If two extensions register the same backend name, if a required browser or search provider is missing, if credentials are needed but no encryption key is set, or if the public OAuth callback URL is unusable, the process fails immediately. That is safer than discovering the problem during a user action.

At the end, `run` launches DBOS workflow execution and Uvicorn, the web server. On shutdown it drains work carefully so another process does not accidentally run the same workflow at the same time.

#### Function details

##### `_payload_digest`  (lines 218–220)

```
def _payload_digest(payload: object) -> str
```

**Purpose**: Creates a stable fingerprint for a piece of JSON-like data. This lets the service record exactly which configuration or sandbox settings it booted with.

**Data flow**: It receives any payload that can be turned into JSON, writes it in a consistent key order, hashes those bytes with SHA-256, and returns a string like `sha256:...`.

**Call relations**: It is used by `_runtime_identity` when building the runtime identity reported for this service instance.

*Call graph*: called by 1 (_runtime_identity); 2 external calls (sha256, dumps).


##### `_runtime_identity`  (lines 223–241)

```
def _runtime_identity(config: Config, carrier: CarrierSpec) -> RuntimeIdentity
```

**Purpose**: Builds a compact identity card for the running runtime. It records the code revision, container image digest, configuration fingerprint, and sandbox fingerprint.

**Data flow**: It reads the runtime revision and image from environment variables, reads the config and carrier settings, hashes the relevant data, and returns a `RuntimeIdentity` object. If only one of revision or image is set, it raises an error because the pair would be misleading.

**Call relations**: `run` calls this during startup after selecting the sandbox carrier, then passes the result into shared surface contexts so clients can see what runtime they are talking to.

*Call graph*: calls 1 internal fn (_payload_digest); called by 1 (run); 3 external calls (__init__, model_dump, runtime_digest).


##### `_assert_no_reserved_routes`  (lines 244–260)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Protects routes that belong to the onboarding and sign-in gateway. It makes startup fail if this service accidentally registers paths such as `/login`, `/logout`, `/join`, `/v1/onboard`, or `/ufo`.

**Data flow**: It inspects the FastAPI app's registered routes, looks for any route path starting with a reserved prefix, and either returns silently or raises a clear startup error listing the conflicts.

**Call relations**: `run` calls it after all routes are mounted and before the web server starts, making route ownership a checked rule instead of an assumption.

*Call graph*: called by 1 (run).


##### `run`  (lines 263–519)

```
def run() -> None
```

**Purpose**: Starts the shared service process. It is the main assembly point that loads settings, creates all major runtime objects, mounts web routes, starts workflow workers, and runs the HTTP server.

**Data flow**: It reads configuration and environment variables, initializes logging, databases, credentials, extensions, storage, models, sandboxes, connectors, jobs, and FastAPI routes. It then starts Uvicorn and, when the server stops, drains workflow execution and retires the process heartbeat if safe.

**Call relations**: This is the top-level story for the file. Almost every helper in this file exists to keep `run` readable: selecting backends, validating extension requirements, mounting routes, registering jobs, preparing proxy access, and shutting down safely.

*Call graph*: calls 23 internal fn (from_env, from_skills, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _preview_settings (+13 more)); 60 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `run.invoker_for`  (lines 329–330)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates an admission invoker tied to one workspace. This is used when background or runtime code needs to admit work for a specific workspace.

**Data flow**: It receives a workspace ID, combines it with the shared `Admission` object created by `run`, and returns an `AdmissionInvoker` scoped to that workspace.

**Call relations**: `run` defines it while assembling admission. It passes this factory into the runtime and job launch code so later work can be admitted under the correct workspace.

*Call graph*: 1 external calls (__init__).


##### `_one_shot`  (lines 522–534)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: Runs one asynchronous setup or cleanup step on a temporary event loop. It also makes sure database engines tied to that temporary loop are disposed before the loop disappears.

**Data flow**: It receives a coroutine, wraps it in a small cleanup coroutine, runs it with `asyncio.run`, and returns the coroutine's result. Any loop-local database resources are cleaned up afterward.

**Call relations**: `run`, `_proxy_endpoint`, and `_stop_executor` use it for one-time database-touching work outside the long-lived server loop.

*Call graph*: called by 3 (_proxy_endpoint, _stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 528–532)

```
async def step() -> T
```

**Purpose**: Performs the actual awaited work for `_one_shot` and guarantees cleanup. It is the small inner routine that makes the temporary loop safe.

**Data flow**: It awaits the original coroutine. Whether that succeeds or fails, it then calls the database cleanup routine for the current loop before returning or re-raising.

**Call relations**: It is created and run only by `_one_shot`; callers do not use it directly.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 537–551)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Shuts down DBOS workflow execution without creating duplicate work. It only retires this process's worker seat if no workflows are still active.

**Data flow**: It asks DBOS to drain workflows for a configured time. It then checks whether any workflows are still active; if so, it keeps the seat alive for safety, otherwise it retires the heartbeat record.

**Call relations**: `run` calls it in a `finally` block after Uvicorn exits. It uses `_one_shot` to retire the heartbeat through async database code.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 554–568)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the special database connection string used for cross-workspace owner-level reads. This is needed for sweep jobs that first enumerate workspaces and then re-enter each one safely.

**Data flow**: It reads `UFO_OWNER_DSN` from the environment or falls back to the configured owner database URL. If neither exists, it raises a startup error explaining why shared serving cannot continue.

**Call relations**: `run` calls it before initializing the owner database connection.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 571–638)

```
def _launch_jobs(runtime: Runtime, invoker_for: InvokerFactory, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts scheduled and queued background jobs. These include source syncing, turn dispatch, delivery cleanup, page indexing, preview rendering, and extension-provided jobs.

**Data flow**: It receives the assembled runtime, an admission factory, a sync driver, and a page feed. It builds helper objects for probes, page-change handling, preview rendering, and job bindings, then launches a `JobRunner`.

**Call relations**: `run` calls it after the runtime and source sync pieces exist. It hands work off to DBOS job machinery and runtime job runners.

*Call graph*: called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, connector_clis (+3 more)).


##### `_source_backends`  (lines 641–655)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of source-sync backends available to the service. A source backend is code that knows how to read pages or files from a particular kind of source.

**Data flow**: It starts with the built-in folder backend, then walks extension manifests and adds each declared source provider. It gives each provider credential access limited to that extension's declared credential slots and rejects duplicate backend names.

**Call relations**: `run` uses it when constructing the `SyncDriver` for configured sources.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 658–699)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Builds functions that can discover the current user identity for source syncing on each surface. This is useful when a synced source needs to know which account it is acting as.

**Data flow**: It scans surfaces in extension manifests. For each surface that declares a self-user lookup, it creates a resolver that will later bind a workspace, provide safe credential reads, and call the surface's identity handler.

**Call relations**: `run` passes the resulting resolver map into the `SyncDriver`.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 672–696)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Looks up a surface-specific user identity inside one workspace. It wraps an extension's identity handler with workspace scoping and safe credential access.

**Data flow**: It receives a workspace ID, creates a credential-reading helper, binds the workspace for database safety, builds a `SurfaceIdentityContext`, and returns the identity string or `None` from the handler.

**Call relations**: It is generated by `_source_identity_resolvers` and later called by source-sync code when it needs to resolve identity for a surface.

*Call graph*: 2 external calls (__init__, ws).


##### `_source_identity_resolvers.resolve.credential`  (lines 678–687)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Reads one credential slot for a source identity lookup, but only if that slot was declared by the extension. This prevents an extension from quietly reading secrets it did not ask for.

**Data flow**: It receives a credential slot name, checks that the slot is allowed and that a credential store exists, then reads the stored value for the current workspace.

**Call relations**: It is used inside the generated `resolve` function and handed to the surface identity handler through its context.


##### `_select_hub`  (lines 702–720)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live message hub for the process. The hub is the shared channel used to stream live updates between running work and connected clients.

**Data flow**: It builds a table of hub builders from the built-in in-process hub and any extension-provided hubs, rejects duplicate names, looks up the configured backend, and returns the built hub.

**Call relations**: `run` calls it during startup before creating tailers, admission, runtime, and surfaces that depend on live updates.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_terminal_transport`  (lines 723–765)

```
def _select_terminal_transport(config: Config, manifests: tuple[Manifest, ...], blob: FleetBlobStore) -> TerminalTransport
```

**Purpose**: Chooses how sandbox terminals connect back to users. It also prevents an unsafe mix where live messages are cross-process but terminal rendezvous is only local to one process.

**Data flow**: It checks the terminal and hub backend combination, builds a table of terminal transport builders, rejects duplicates, looks up the configured backend, and returns the chosen transport using the shared hub URL and blob store.

**Call relations**: `run` calls it while constructing `ConversationSandbox`, so sandboxes know how terminal input and output will travel.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 768–796)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Selects the browser automation provider, if one is installed and configured. CDP means Chrome DevTools Protocol, a way to control a browser programmatically.

**Data flow**: It scans extension manifests for CDP providers, rejects duplicate backend names, finds the configured provider, checks credential-key availability if needed, and returns the built provider or `None`.

**Call relations**: `run` uses it when creating the runtime. `_require_cdp_provider` calls it during extension requirement validation.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 799–823)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that every active extension's declared requirements are actually available. This turns missing backends into clear startup errors instead of later user-facing failures.

**Data flow**: It reads each manifest's required seam names, finds the matching check function, runs it, and wraps any failure with the extension name and the missing seam.

**Call relations**: `run` calls it soon after loading manifests and credentials, before assembling the rest of the service.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 826–840)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a browser extension has a usable browser automation provider. If no active extension registered the selected provider, startup fails.

**Data flow**: It calls `_select_cdp_provider`; if the result is `None`, it raises an error naming the configured provider.

**Call relations**: _validate_requires uses this check when an extension says it requires `cdp_providers`.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 843–878)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Selects the research search backend, if configured. This is the service that research tools use to search outside information.

**Data flow**: It scans manifests for search providers, rejects duplicate names, returns `None` if no search provider is configured, otherwise validates the selected name and credential key, then builds the provider with scoped credential access.

**Call relations**: `run` uses it when creating the runtime. `_require_search_provider` uses it to enforce extensions that require search.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 881–894)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that research tools have a configured and working search provider. It gives a clear startup error if the search setting is missing or invalid.

**Data flow**: It checks that the search provider setting is present, then delegates the detailed validation and construction check to `_select_search_provider`.

**Call relations**: _validate_requires calls it for extensions that declare the `search_providers` requirement.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_select_flag_provider`  (lines 897–924)

```
def _select_flag_provider(config: Config, manifests: tuple[Manifest, ...]) -> FeatureProvider | None
```

**Purpose**: Chooses the feature-flag provider, if configured. Feature flags are switches that let the service turn behavior on or off without changing code.

**Data flow**: It scans extension manifests for flag providers, rejects duplicate names, returns `None` if no backend is configured, otherwise builds the selected provider. If the provider cannot be built because it lacks a key, it logs a warning and returns `None`.

**Call relations**: `run` calls it before initializing the feature-flag system.

*Call graph*: called by 1 (run); 2 external calls (__init__, warn).


##### `_require_memory_search`  (lines 927–953)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that the default memory-search provider exists exactly once and can be used. Memory search is the part that retrieves stored knowledge from prior context.

**Data flow**: It looks through manifests for the default memory-search provider name, rejects none or more than one, then checks whether any declared credential slots require a configured credential store.

**Call relations**: _validate_requires calls it when an extension declares that memory search is required.


##### `_select_auth_proxy`  (lines 965–1002)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy for connector credentials. This is used when a connector does not have its own broker and needs the host to resolve credentials safely.

**Data flow**: It scans manifests for auth proxy backends, rejects duplicate names, chooses the configured backend or the only available backend, checks that credentials are configured, and builds the proxy with scoped credential access.

**Call relations**: _connector_registry calls it while building the registry used by connector tools and source syncing.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 1005–1053)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient, public_base_url: str | None) -> None
```

**Purpose**: Adds extension-owned HTTP routes under `/ext/<extension>/...`. Each route must first identify a workspace so the handler cannot touch shared data without a workspace scope.

**Data flow**: It scans manifests for route specs, builds an extension context, creates a FastAPI endpoint for each route, and registers it. If routes need credentials but no credential key exists, startup fails.

**Call relations**: `run` calls it after jobs are launched and before shared surfaces are mounted. The generated endpoints call extension handlers after authorization and workspace binding.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 1037–1047)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Serves one extension route after checking which workspace the request belongs to. Unauthorized requests are stopped before the extension handler runs.

**Data flow**: It receives a web request, asks the route's identify function for a workspace ID, returns a 401 response if identification fails, otherwise binds that workspace and awaits the extension handler.

**Call relations**: It is created inside `_mount_ext_routes` for each extension route and invoked by FastAPI when that URL is requested.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 1074–1082)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace state at the start and end of every HTTP request. This prevents one workspace's context from leaking into another request in the shared fleet.

**Data flow**: It receives the ASGI request scope, receive function, and send function. For non-HTTP traffic it passes through; for HTTP it clears the current workspace, runs the downstream app, and clears the workspace again in a `finally` block.

**Call relations**: _mount_shared_surfaces installs this as middleware. It surrounds all later HTTP route handling and protects surface streaming responses too.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 1085–1284)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClie
```

**Purpose**: Mounts member-facing shared surface routes, such as chat or browser surfaces, in a way that resolves the workspace on every request. It also starts pollers and listeners for durable surface delivery.

**Data flow**: It installs the workspace boundary middleware, prepares shared helpers such as admission, tailing, stopping, skills, connector registry, object schemas, and conversation slots, then registers each surface route. It also creates writeback and mid-turn reply pollers when surfaces support durable posting.

**Call relations**: `run` calls it after building the runtime and extension routes. It uses `_connector_entries`, `home_surface`, `_mount_home`, and nested helpers to build per-workspace surface contexts.

*Call graph*: calls 5 internal fn (bundled_skills, from_skills, _connector_entries, _mount_home, home_surface); called by 1 (run); 24 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+14 more)).


##### `_mount_shared_surfaces.context_for`  (lines 1179–1219)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the full surface context for one workspace and one surface. This context is the toolbox a surface handler uses to admit turns, read blobs, access credentials, list skills, stop runs, and more.

**Data flow**: It receives a workspace ID and surface name, combines them with shared objects prepared by `_mount_shared_surfaces`, and returns a `SurfaceContext` filled with workspace-scoped services and deployment metadata.

**Call relations**: Surface endpoints, listeners, writeback pollers, and mid-turn reply pollers use this helper whenever they need to run surface code for a particular workspace.

*Call graph*: calls 1 internal fn (home_surface); 3 external calls (__init__, __init__, frame_admissible).


##### `_mount_shared_surfaces.endpoint`  (lines 1247–1261)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Serves one shared surface route after authenticating the request and binding the correct workspace. This is the main request wrapper for member-facing surfaces.

**Data flow**: It receives a web request, asks the surface's identify function to resolve it, returns a response directly if identification does so, returns 401 if unresolved, otherwise sets the current workspace and calls the route handler with a freshly built surface context.

**Call relations**: It is created inside `_mount_shared_surfaces` for each surface route and called by FastAPI when members interact with those surface URLs.

*Call graph*: 2 external calls (Response, set).


##### `home_surface`  (lines 1287–1294)

```
def home_surface(manifests: tuple[Manifest, ...]) -> str | None
```

**Purpose**: Finds which installed surface should be treated as the browser home page. It makes sure there is at most one such home surface.

**Data flow**: It scans all manifests for surfaces marked as home, raises an error if more than one is found, and returns the single home surface name or `None`.

**Call relations**: `run`, `_mount_ext_routes`, `_mount_shared_surfaces`, `_mount_shared_surfaces.context_for`, and `_mount_home` use it whenever they need the browser's default landing surface.

*Call graph*: called by 5 (_mount_ext_routes, _mount_home, _mount_shared_surfaces, context_for, run).


##### `_mount_home`  (lines 1297–1309)

```
def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None
```

**Purpose**: Turns the bare root URL `/` into a redirect to the configured home surface. Without this, visiting the service host directly would likely be a dead end.

**Data flow**: It calls `home_surface`; if there is no home surface it does nothing. Otherwise it registers a GET route for `/` that redirects to `/surface/<home>`.

**Call relations**: _mount_shared_surfaces calls it after mounting all surface routes.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (_mount_shared_surfaces); 1 external calls (add_route).


##### `_mount_home.home`  (lines 1306–1307)

```
async def home(_request: Request) -> Response
```

**Purpose**: Responds to `GET /` by redirecting the browser to the home surface. It uses a 303 redirect, which tells the browser to fetch the target page with GET.

**Data flow**: It ignores the request body and returns a redirect response pointing at the selected surface path.

**Call relations**: It is created and registered by `_mount_home`, then invoked by FastAPI for root-path browser visits.

*Call graph*: 1 external calls (RedirectResponse).


##### `_serve_lifespan`  (lines 1313–1342)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs background tasks that should live for the same lifetime as the web app loop. These include recovery, cancellation cleanup, stranded-turn cleanup, surface writeback polling, and surface listeners.

**Data flow**: On startup it registers configured sources, then opens an asyncio task group and starts the long-running background tasks. On shutdown it cancels those tasks.

**Call relations**: `run` passes it as the FastAPI lifespan handler when creating the app, so Uvicorn activates it while the server is running.

*Call graph*: 5 external calls (__init__, __init__, __init__, TaskGroup, register_sources).


##### `_preview_settings`  (lines 1345–1354)

```
def _preview_settings(config: Config) -> tuple[tuple[str, int], str] | None
```

**Purpose**: Reads and validates sandbox preview-service settings. The preview service renders sandbox content for viewing outside the sandbox.

**Data flow**: It parses the configured preview service address. If preview is disabled it returns `None`; if enabled, it requires a preview token from the environment and returns the address plus token.

**Call relations**: `run` uses it when setting up document rendering and site previewing. `_proxy_endpoint` uses it so egress rules can allow preview access.

*Call graph*: called by 2 (_proxy_endpoint, run); 1 external calls (parse_preview_service).


##### `_proxy_endpoint`  (lines 1357–1434)

```
def _proxy_endpoint(app: FastAPI, config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: FilesystemBlobStore | S3BlobS
```

**Purpose**: Sets up the sandbox egress proxy connection and the server-side policy endpoint that proxy calls. Egress here means network traffic leaving a sandbox.

**Data flow**: It determines the certificate authority and control token, validates cache and preview settings, builds per-agent egress rules from models, artifacts, credentials, grants, manifests, and connector tools, mounts egress-control routers on the FastAPI app, and returns the proxy endpoint details given to sandboxes.

**Call relations**: `run` calls it while constructing `ConversationSandbox`. It uses `_ephemeral_egress_ca`, `_preview_settings`, and `_one_shot`, then hands off enforcement to `EgressControl`.

*Call graph*: calls 3 internal fn (_ephemeral_egress_ca, _one_shot, _preview_settings); called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, include_router, token_urlsafe, parse_cache_daemon, connector_clis, injecting_slots, model_rule_base (+3 more)).


##### `_ephemeral_egress_ca`  (lines 1437–1456)

```
def _ephemeral_egress_ca() -> str
```

**Purpose**: Creates a temporary certificate authority certificate for local development when no shared egress proxy certificate is provided. A certificate authority is a trust anchor used to verify proxy-made certificates.

**Data flow**: It generates a private key, creates a self-signed CA certificate named `ufo-egress-local`, valid for a long period, and returns the certificate text in PEM format. The signing key is not returned.

**Call relations**: _proxy_endpoint calls it only for local boots that do not provide a shared egress CA.

*Call graph*: called by 1 (_proxy_endpoint); 9 external calls (generate_private_key, SHA256, BasicConstraints, CertificateBuilder, Name, NameAttribute, random_serial_number, now, timedelta).


##### `_connector_registry`  (lines 1462–1476)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the central connector registry. Connectors are integrations, often OAuth-based, that let UFO access outside services on a member's behalf.

**Data flow**: It gathers connector entries from manifests, builds a namespace resolver, selects any fallback auth proxy, and returns a `ConnectorRegistry`.

**Call relations**: `run` calls it during startup. `_mount_shared_surfaces` may also build a default registry using `_connector_entries` if one is not supplied.

*Call graph*: calls 2 internal fn (_connector_entries, _select_auth_proxy); called by 1 (run); 2 external calls (__init__, open_connector_namespace).


##### `_connector_entries`  (lines 1479–1489)

```
def _connector_entries(manifests: tuple[Manifest, ...]) -> dict[str, ConnectorEntry]
```

**Purpose**: Collects connector provider metadata from extension manifests. It ensures each provider name belongs to only one extension.

**Data flow**: It scans all manifest connectors, rejects duplicate OAuth provider names, and returns a dictionary of provider names to `ConnectorEntry` objects containing provider, label, and broker information.

**Call relations**: _connector_registry uses it to build the main registry. `_mount_shared_surfaces` uses it when it needs to create a fallback registry.

*Call graph*: called by 2 (_connector_registry, _mount_shared_surfaces); 1 external calls (__init__).


##### `_connect_flow`  (lines 1492–1530)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...], index: IndexBackend | None=None, embed: EmbedClient | None=None, resumption: ConnectResume | Non
```

**Purpose**: Creates the OAuth connection flow used to authorize connectors and store grants. OAuth is the common browser-based sign-in handoff used by many external services.

**Data flow**: If there is no credential store, it returns `None` because tokens cannot be safely encrypted. Otherwise it gathers OAuth providers, checks duplicates, computes the redirect URI, builds connection hooks and labels, and returns a `ConnectFlow`.

**Call relations**: `run` calls it and installs the result with the global connect-flow installer, so tools, private surface authorization, and OAuth callbacks share the same flow.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 4 external calls (__init__, __init__, connection_hooks, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 1533–1559)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL. This URL must be reachable by the member's browser after an external provider redirects back.

**Data flow**: It reads `connect.public_base_url`, returns an inert value if no providers exist, otherwise requires a real HTTP or HTTPS URL with a host that is not a wildcard bind, and appends the callback path.

**Call relations**: _connect_flow calls it while creating the connector authorization flow.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system how it should run in this deployment.
- `reg-selected-pack-services` — The chosen product pack and the shared service objects it wires up for the rest of the app.
- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-extension-install-store` — The saved record of which extensions are installed, removed, or holding extension-specific data.
- `reg-surface-routing` — The shared routing state that maps browser, Slack, iMessage, terminal, site, and object requests to the right workspace, agent, and conversation.
- `reg-auth-identity-sessions` — The current proof of who a person, operator, shared-link visitor, or external service caller is.
- `reg-egress-policy-proxy` — The network allowlist and proxy state that decide which outside hosts sandboxed work may contact.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-model-catalog-providers` — The shared catalog of available AI models, their prices and limits, and the provider clients used to call them.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-inbound-message-queue` — The saved queue of incoming external messages waiting to be rendered, ordered, deduplicated, and admitted as turns.
- `reg-live-turn-stream` — The live event feed that lets clients and other processes watch a running turn and learn how it ended.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-blob-artifact-store` — The shared file, blob, artifact, preview, download, and hosted media storage used by turns and surfaces.
- `reg-background-jobs` — The shared job schedule, due-work candidates, claims, retries, and worker state for background and autonomous work.
- `reg-runtime-fleet-heartbeats` — The fleet-wide record of which runtime processes are alive and what work they may be responsible for.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-schema-migration-version` — The Alembic/database schema version state that records which migrations have been applied and gates safe startup against the expected database shape.
- `reg-database-connection-pool` — The shared SQLAlchemy engine/session and connection-pool state used by requests, turns, workers, migrations, and persistence helpers to access the database safely.
- `reg-surface-listener-leases` — The stored claims/leases that coordinate which runtime instance is allowed to listen on a shared surface installation or address, avoiding duplicate external listeners.
- `reg-redis-service-pool` — The shared Redis client/connection and stream/cache coordination state used by live turn streaming, workers, and Redis-backed extension stores.
- `reg-extension-catalog-cache` — The extension app-store/catalog metadata and update availability state used when discovering, installing, removing, or bundling extensions.
- `reg-server-route-mounts` — The in-process ASGI route, middleware, static mount, and extension route table assembled at startup and used to dispatch later HTTP/webhook/control requests.
