# Serve Process Bootstrap and Runtime Supervision  `stage-5`

This stage is the service’s launch pad and safety watch. It runs when the long-lived UFO server starts, then continues working behind the scenes while the service is alive. The main entry point is serve.py. It brings the system’s major parts online: the web server, databases, background workers, extension jobs, sandboxed execution areas, credentials, storage, live update channels, and workspace boundaries that keep one workspace’s work from spilling into another.

The runtime package marker, __init__.py, is just a signpost for Python. It tells Python that the runtime folder can be imported as a package; it adds no behavior itself.

runtime_instance.py acts like a check-in desk and night watchman. It records that this serve process is alive so other processes can see it. It also runs cleanup loops that look for trouble: jobs left behind by dead processes, child work that should stop after a parent was cancelled, and running turns whose workflow has disappeared. Together, these pieces start the service and keep its work from getting stranded.

## Files in this stage

### Service Bootstrap and Supervision
The serve entrypoint initializes the long-running service and relies on the runtime package to register process liveness and supervise stuck or cancelled work.

### `core/src/ufo/serve.py`

`entrypoint` · `startup, main loop, background work, shutdown`

This is the service’s “control room.” A single running process can serve many workspaces, so the most important job here is to build all shared parts once, then make sure each request or background task is tied to the correct workspace before it touches data. Without this file, the system would have pieces such as models, connectors, sandboxes, jobs, and web routes, but no reliable way to assemble them into one working server.

At startup, `run` loads configuration, prepares logging and databases, loads extension manifests, checks required secrets, starts a heartbeat for this service instance, and builds core services such as blob storage, model registry, memory search, connector routing, and sandbox access. It then creates a FastAPI web app, mounts routes for extension pages and user-facing “surfaces,” registers background jobs, and starts DBOS, the durable workflow engine used for long-running work.

A recurring theme is “fail loudly at boot.” If two extensions claim the same name, a required backend is missing, a credential key is absent, or a public redirect URL is unusable, this file raises an error before users hit the broken path.

The file also guards workspace isolation. `WorkspaceScopeBoundary` clears the active workspace before and after each HTTP request, while mounted surface routes set it only after verifying the request. Think of it like checking a visitor’s badge before opening the correct filing cabinet, then locking the cabinet again afterward.

#### Function details

##### `_assert_no_reserved_routes`  (lines 201–217)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Checks that this service has not mounted web routes under URL prefixes reserved for the onboarding and sign-in gateway. This prevents a route from appearing to exist in the app while being silently hidden by the front-door routing layer.

**Data flow**: It reads the FastAPI app’s registered routes, compares each path with the reserved prefixes, and either returns quietly or raises a startup error listing the conflicting paths.

**Call relations**: Near the end of `run`, after all routes have been mounted, this function performs a final safety check before the server starts accepting traffic.

*Call graph*: called by 1 (run).


##### `run`  (lines 220–462)

```
def run() -> None
```

**Purpose**: Starts the shared fleet service process. It is the main assembly line that loads settings, creates shared services, mounts web routes, starts durable workers, and runs the HTTP server.

**Data flow**: It begins with environment variables and configuration files, then builds databases, credentials, extensions, storage, model access, sandboxes, connectors, jobs, and web routes. The result is a running FastAPI app served by Uvicorn; on shutdown it drains DBOS work and retires the service instance when safe.

**Call relations**: This is the top-level caller for almost every helper in the file. It calls setup helpers such as `_shared_owner_dsn`, `_select_hub`, `_connector_registry`, `_proxy_endpoint`, `_launch_jobs`, `_mount_ext_routes`, `_mount_shared_surfaces`, and finally `_assert_no_reserved_routes`; during shutdown it hands off to `_stop_executor`.

*Call graph*: calls 22 internal fn (from_env, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _preview_settings, _proxy_endpoint (+12 more)); 59 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `run.invoker_for`  (lines 285–286)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates an admission invoker for one workspace. An admission invoker is the object used to submit or resume user work while keeping it tied to that workspace.

**Data flow**: It receives a workspace ID, combines it with the already-built shared `Admission` object, and returns a workspace-specific `AdmissionInvoker`.

**Call relations**: Defined inside `run`, it is passed into runtime and job setup so later code can create workspace-scoped admission helpers whenever a job or request needs one.

*Call graph*: 1 external calls (__init__).


##### `_one_shot`  (lines 465–477)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: Runs one asynchronous setup or teardown operation to completion on a temporary event loop. It exists to safely perform database-touching startup steps without leaving database connections attached to a loop that is about to close.

**Data flow**: It receives a coroutine, wraps it in an inner cleanup step, runs it with `asyncio.run`, and returns the coroutine’s result after disposing database engines for that temporary loop.

**Call relations**: `run`, `_proxy_endpoint`, and `_stop_executor` use it for isolated async operations during startup and shutdown, outside the long-lived web server loop.

*Call graph*: called by 3 (_proxy_endpoint, _stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 471–475)

```
async def step() -> T
```

**Purpose**: Performs the actual awaited operation for `_one_shot` and guarantees cleanup afterward. It is the small inner routine that makes the temporary event loop safe to discard.

**Data flow**: It awaits the original coroutine, remembers its result or exception, and then calls database engine cleanup in a `finally` block before returning or re-raising.

**Call relations**: It is created and run only by `_one_shot`, which uses `asyncio.run` to execute it.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 480–494)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Shuts down DBOS workflow execution without accidentally allowing the same work to run twice. It retires this service instance only if no workflows are still active locally.

**Data flow**: It asks DBOS to drain work for a configured number of seconds, inspects the active workflow set, logs and keeps the fleet seat if work is still running, or retires the heartbeat seat if the executor is empty.

**Call relations**: `run` calls this in its `finally` block after Uvicorn stops. It uses `_one_shot` to retire the heartbeat asynchronously when it is safe.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 497–511)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string used for owner-level cross-workspace maintenance reads. This special connection bypasses row-level workspace filtering so sweep jobs can first find all affected workspaces, then re-enter each one safely.

**Data flow**: It reads `UFO_OWNER_DSN` from the environment or falls back to the configured owner database URL. It returns the string if present, otherwise raises a clear startup error.

**Call relations**: `run` calls this before initializing the owner database connection, because later recovery and sweep jobs depend on it.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 514–581)

```
def _launch_jobs(runtime: Runtime, invoker_for: InvokerFactory, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts background jobs for core and installed extensions. These jobs cover source syncing, turn dispatch, delivery cleanup, page changes, indexing, and optional previews.

**Data flow**: It reads the built runtime, admission factory, sync driver, and page feed; builds helper objects such as probes and page-change runners; combines core and extension job bindings; skips disabled jobs from configuration; and launches a `JobRunner`.

**Call relations**: `run` calls this after the runtime and sync driver are ready. It hands off to job-related classes such as `PageChangeRunner`, `DeliverySweep`, and `JobRunner` so DBOS can schedule and execute background work.

*Call graph*: called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, connector_clis (+3 more)).


##### `_source_backends`  (lines 584–598)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of available source-sync backends. A source backend knows how to read from a kind of content source, such as the built-in folder source or an extension-provided source.

**Data flow**: It starts with the core folder backend, then scans manifests for extension source providers. For each provider it creates credential access limited to that extension’s declared credential slots, adds the backend by name, and raises an error if two providers reuse the same backend name.

**Call relations**: `run` uses this when constructing the `SyncDriver`, so configured source rows can be matched to exactly one implementation.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 601–642)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Builds functions that can ask a surface who the current user is for source-sync identity purposes. This lets synced content be associated with the right user identity for a given workspace and surface.

**Data flow**: It scans manifests for surfaces that declare a self-user identity callback. For each one, it creates a resolver that will later bind the workspace, provide safe credential access, and call the surface’s identity handler.

**Call relations**: `run` passes the resulting resolver map to `SyncDriver`. The nested `resolve` and `credential` functions do the actual per-workspace work later.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 615–639)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Resolves a surface-specific user identity inside one workspace. It wraps the extension’s identity callback with the right workspace binding and helper context.

**Data flow**: It receives a workspace ID, creates a credential-reading helper limited to the surface’s extension, enters that workspace scope, builds a `SurfaceIdentityContext`, and returns the handler’s user ID result or `None`.

**Call relations**: Created by `_source_identity_resolvers` and later called by source-sync code through the `SyncDriver` when it needs a workspace’s surface identity.

*Call graph*: 2 external calls (__init__, ws).


##### `_source_identity_resolvers.resolve.credential`  (lines 621–630)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Reads one declared credential for a surface identity resolver. It prevents an extension from reading credential slots it did not declare.

**Data flow**: It receives a credential slot name, checks that the slot was declared, checks that a credential store exists, then fetches the secret for the current workspace.

**Call relations**: This helper is passed indirectly through `SurfaceIdentityContext` to the surface identity handler created in `_source_identity_resolvers.resolve`.


##### `_select_hub`  (lines 645–663)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live-update hub backend for this process. The hub is the channel used to send real-time frames or events between workflows and connected surfaces.

**Data flow**: It starts with the built-in in-process hub option, adds hub builders registered by extensions, checks for duplicate backend names, looks up the configured backend, and returns the built hub or raises an error.

**Call relations**: `run` calls this early and then passes the hub into admission, runtime, route mounting, tailing, stopping, and surface delivery components.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_terminal_transport`  (lines 666–708)

```
def _select_terminal_transport(config: Config, manifests: tuple[Manifest, ...], blob: FleetBlobStore) -> TerminalTransport
```

**Purpose**: Chooses how terminal sessions are connected between user-facing requests and sandbox-running workflows. It prevents unsafe combinations where a multi-process fleet would use a terminal transport that only works inside one process.

**Data flow**: It reads terminal and hub configuration, rejects an in-process terminal transport when the hub is cross-process, registers built-in and extension terminal transport builders, selects the configured one, and returns it.

**Call relations**: `run` calls this while building the conversation sandbox. The returned transport is used by sandbox terminal features so a user connection can reach the workflow that owns the terminal.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 711–739)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses the browser automation provider, if one is installed and selected. CDP means Chrome DevTools Protocol, a way to drive a browser programmatically.

**Data flow**: It scans extension manifests for CDP providers, rejects duplicate names, looks up the configured provider, checks credential-key availability when needed, and returns a built provider or `None`.

**Call relations**: `run` uses it when building runtime browser access. `_require_cdp_provider` also calls it during extension requirement checks to turn a missing browser provider into a startup failure.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 742–766)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that every active extension’s declared required system seam is actually available. A seam is an integration point, such as browser automation, memory search, or web search.

**Data flow**: It reads each manifest’s `requires` list, finds the corresponding checker, runs it, and raises an error naming the extension and missing seam if anything fails.

**Call relations**: `run` calls this after loading manifests and credentials, before serving traffic, so broken extension requirements are found at startup.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 769–783)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a browser automation provider is available when an extension requires one. It turns an optional provider into a mandatory startup condition.

**Data flow**: It calls `_select_cdp_provider`; if the result is `None`, it raises an error explaining that the configured provider is required but not registered.

**Call relations**: _validate_requires calls this through the required-seam checker table when an extension lists `cdp_providers` as required.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 786–821)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the external search provider for research tools, if configured. It builds one shared provider that can still read per-workspace credentials when a search happens.

**Data flow**: It scans manifests for search providers, rejects duplicate backend names, returns `None` when no provider is configured, otherwise checks that the selected provider exists and credentials are available, then builds it.

**Call relations**: `run` calls this to put search access into the runtime. `_require_search_provider` calls it when an extension says search is required.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 824–837)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that research search is configured and usable. It prevents a research extension from being active while the actual search backend is missing.

**Data flow**: It checks that the search-provider setting is present, then delegates detailed lookup and credential validation to `_select_search_provider`.

**Call relations**: _validate_requires calls this through the required-seam checker table when an extension lists `search_providers` as required.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_select_flag_provider`  (lines 840–867)

```
def _select_flag_provider(config: Config, manifests: tuple[Manifest, ...]) -> FeatureProvider | None
```

**Purpose**: Chooses the feature-flag provider. Feature flags are switches that let deployments turn behavior on or off without changing code.

**Data flow**: It scans manifests for flag providers, rejects duplicate backend names, returns `None` if no backend is configured, builds the selected provider if registered, and warns if the provider cannot be keyed and will fall back to defaults.

**Call relations**: `run` calls this before initializing flags, so the rest of the service can ask for flag values through the configured provider or use defaults.

*Call graph*: called by 1 (run); 2 external calls (__init__, warn).


##### `_require_memory_search`  (lines 870–896)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that the default memory-search provider exists exactly once and can be used. Memory search lets the system retrieve stored context or knowledge for conversations.

**Data flow**: It scans manifests for the default memory-search provider name, raises an error if none or more than one is found, and checks that a credential key exists if that provider declares credential slots.

**Call relations**: _validate_requires calls this through the required-seam checker table when an extension lists `memory_search` as required.


##### `_select_auth_proxy`  (lines 908–945)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy for connectors that do not have their own broker. This proxy helps obtain or use credentials for external services.

**Data flow**: It scans manifests for auth-proxy backends, handles automatic selection when there is only one, rejects ambiguous or unknown choices, checks that credentials are available, and builds the selected proxy with limited credential access.

**Call relations**: _connector_registry calls this while building connector routing. Brokered connectors can bypass it, but unbrokered connector sync credentials depend on it.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 948–996)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient, public_base_url: str | None) -> None
```

**Purpose**: Adds web routes supplied by extensions under `/ext/<extension-name>/...`. It makes sure each request is identified and bound to a workspace before the extension handler runs.

**Data flow**: It scans manifests for routes, requires a credential key if routes exist, builds an extension context, and registers an endpoint for each route with FastAPI.

**Call relations**: `run` calls this after jobs are launched and before the server starts. The nested endpoint function performs the per-request authorization and workspace binding.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 980–990)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Serves one extension-defined HTTP route after verifying which workspace the request belongs to. Unauthorized requests are stopped before extension code can read data.

**Data flow**: It receives a web request, asks the route’s identify function for a workspace, returns a 401 response if none is found, otherwise enters that workspace scope and calls the extension handler.

**Call relations**: Created inside `_mount_ext_routes` and registered with FastAPI. It hands authorized requests to the extension’s own route handler.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 1017–1025)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace state at the beginning and end of each HTTP request. This is a safety fence that prevents one request’s workspace from leaking into another request.

**Data flow**: It receives the low-level ASGI request scope, receive function, and send function. For non-HTTP traffic it simply passes through; for HTTP traffic it sets the current workspace to `None`, calls the downstream app, and always resets it to `None` afterward.

**Call relations**: _mount_shared_surfaces installs `WorkspaceScopeBoundary` as middleware. Surface endpoints set the workspace after authentication, and this boundary guarantees cleanup after the full response finishes.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 1028–1219)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClie
```

**Purpose**: Mounts user-facing shared surface routes, such as browser or chat surfaces, in a way that works for many workspaces in one process. It also starts surface listeners and durable delivery pollers when needed.

**Data flow**: It receives the app and many shared services, installs the workspace boundary middleware, builds per-request context helpers, scans manifests for surfaces, registers their routes, records listeners, mounts the home redirect, and creates writeback and mid-turn reply pollers for durable surfaces.

**Call relations**: `run` calls this after core runtime pieces are ready. It calls helpers such as `_connector_entries`, `home_surface`, and `_mount_home`, and creates nested `context_for` and endpoint functions used by mounted surface routes.

*Call graph*: calls 5 internal fn (_connector_entries, _mount_home, home_surface, bundled_skills, from_skills); called by 1 (run); 24 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+14 more)).


##### `_mount_shared_surfaces.context_for`  (lines 1121–1154)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the full `SurfaceContext` for one workspace and one surface. This context is the toolbox a surface handler uses to admit turns, read blobs, access credentials, list skills, stop work, and more.

**Data flow**: It receives a workspace ID and surface name, combines them with shared services captured from `_mount_shared_surfaces`, creates workspace-bound admission and model helpers, and returns a `SurfaceContext`.

**Call relations**: Surface endpoints and listener runners created by `_mount_shared_surfaces` call this whenever they need to run surface code for a specific workspace.

*Call graph*: calls 1 internal fn (home_surface); 2 external calls (__init__, __init__).


##### `_mount_shared_surfaces.endpoint`  (lines 1182–1196)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Serves one surface-defined HTTP route after authenticating the request and binding the correct workspace. It supports both normal unauthorized responses and custom response objects returned by the surface’s identity check.

**Data flow**: It receives a request, calls the surface’s identify function with surface authentication helpers, returns the identify response directly if it is already a response, returns 401 if no workspace is resolved, otherwise sets the current workspace and calls the route handler with a fresh `SurfaceContext`.

**Call relations**: Created inside `_mount_shared_surfaces` for each surface route and registered with FastAPI. It is the point where browser requests enter workspace-scoped surface code.

*Call graph*: 2 external calls (Response, set).


##### `home_surface`  (lines 1222–1229)

```
def home_surface(manifests: tuple[Manifest, ...]) -> str | None
```

**Purpose**: Finds which installed surface is the browser home page. It ensures there is at most one such default entry point.

**Data flow**: It scans all manifests for surfaces marked as `home`, raises an error if more than one is found, and returns the single home surface name or `None`.

**Call relations**: `run`, `_mount_ext_routes`, `_mount_shared_surfaces`, `_mount_shared_surfaces.context_for`, and `_mount_home` use this to build return links and the root redirect.

*Call graph*: called by 5 (_mount_ext_routes, _mount_home, _mount_shared_surfaces, context_for, run).


##### `_mount_home`  (lines 1232–1244)

```
def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None
```

**Purpose**: Adds a simple `GET /` route that redirects visitors to the configured home surface. This makes the bare service URL useful instead of returning a missing-page response.

**Data flow**: It asks `home_surface` for the default surface, does nothing if there is none, otherwise registers a small redirect route on the FastAPI app.

**Call relations**: _mount_shared_surfaces calls this after mounting all surface routes, so the root URL points to an existing surface path.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (_mount_shared_surfaces); 1 external calls (add_route).


##### `_mount_home.home`  (lines 1241–1242)

```
async def home(_request: Request) -> Response
```

**Purpose**: Returns the actual redirect response for `GET /`. It sends the browser to the chosen surface path using a temporary redirect.

**Data flow**: It ignores the request details and returns a `RedirectResponse` pointing to `/surface/<home-surface>`.

**Call relations**: Created inside `_mount_home` and registered as the handler for the root route.

*Call graph*: 1 external calls (RedirectResponse).


##### `_serve_lifespan`  (lines 1248–1277)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs background tasks tied to the FastAPI app’s lifetime. These tasks recover stranded workflows, reconcile cancellations, process durable surface writebacks, and run surface listeners.

**Data flow**: When the app starts, it registers configured sources, opens an async task group, creates recovery and poller/listener tasks, yields control while the server runs, then cancels those tasks during shutdown.

**Call relations**: `run` passes this function as the FastAPI lifespan handler. It uses app state populated during startup, such as DBOS client, configured sources, pollers, and listeners.

*Call graph*: 5 external calls (__init__, __init__, __init__, TaskGroup, register_sources).


##### `_preview_settings`  (lines 1280–1289)

```
def _preview_settings(config: Config) -> tuple[tuple[str, int], str] | None
```

**Purpose**: Reads and validates sandbox preview-service settings. The preview service can render documents or sites for sandbox-related features.

**Data flow**: It parses the configured preview service address, returns `None` if disabled, otherwise reads the preview token from the environment and returns the address plus token, raising an error if the token is missing.

**Call relations**: `run` calls this while building renderers and site previewing. `_proxy_endpoint` also calls it so egress rules can include the preview token when previewing is enabled.

*Call graph*: called by 2 (_proxy_endpoint, run); 1 external calls (parse_preview_service).


##### `_proxy_endpoint`  (lines 1292–1370)

```
def _proxy_endpoint(app: FastAPI, config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: FilesystemBlobStore | S3BlobS
```

**Purpose**: Sets up sandbox outbound network control. Sandboxes do not get free internet access; they connect through an egress proxy that asks this service what each agent is allowed to reach.

**Data flow**: It reads proxy, certificate, cache, preview, credential, model, and connector settings; creates policy rules; mounts egress-control routes on the FastAPI app; and returns a `ProxyEndpoint` containing the proxy port, public URL, and trusted certificate for sandboxes.

**Call relations**: `run` calls this while building `ConversationSandbox`. It calls `_preview_settings`, `_one_shot`, and `_ephemeral_egress_ca`, and hands policy to `EgressControl` and `PerAgentRules`.

*Call graph*: calls 3 internal fn (_ephemeral_egress_ca, _one_shot, _preview_settings); called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, include_router, token_urlsafe, connector_transfer_hosts, derive_artifact_store_rules, derive_manifest_rules, connector_clis (+3 more)).


##### `_ephemeral_egress_ca`  (lines 1373–1392)

```
def _ephemeral_egress_ca() -> str
```

**Purpose**: Creates a temporary certificate authority certificate for local runs without a shared egress proxy certificate. A certificate authority is a trusted signer for secure network certificates.

**Data flow**: It generates a private key, builds a self-signed certificate marked as a certificate authority, serializes the certificate as PEM text, and returns that text. The signing key is not returned.

**Call relations**: _proxy_endpoint calls this only for local-style boots when no egress CA certificate is supplied by the environment.

*Call graph*: called by 1 (_proxy_endpoint); 9 external calls (generate_private_key, SHA256, BasicConstraints, CertificateBuilder, Name, NameAttribute, random_serial_number, now, timedelta).


##### `_connector_registry`  (lines 1398–1412)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the central registry for external-service connectors. Connectors are integrations such as OAuth-backed services that tools or sync jobs can use.

**Data flow**: It gathers connector entries from manifests, builds the namespace resolver, selects a fallback auth proxy if needed, and returns a `ConnectorRegistry`.

**Call relations**: `run` calls this before creating runtime and sync services. It delegates entry collection to `_connector_entries` and fallback proxy selection to `_select_auth_proxy`.

*Call graph*: calls 2 internal fn (_connector_entries, _select_auth_proxy); called by 1 (run); 2 external calls (__init__, open_connector_namespace).


##### `_connector_entries`  (lines 1415–1425)

```
def _connector_entries(manifests: tuple[Manifest, ...]) -> dict[str, ConnectorEntry]
```

**Purpose**: Collects connector metadata from all installed extensions. It ensures each OAuth provider name is owned by only one connector.

**Data flow**: It scans every manifest connector, reads its provider name, label, and broker, adds a `ConnectorEntry` to a dictionary, and raises an error on duplicate provider names.

**Call relations**: _connector_registry calls this to build the main connector registry. `_mount_shared_surfaces` also uses it when it needs a default connector registry.

*Call graph*: called by 2 (_connector_registry, _mount_shared_surfaces); 1 external calls (__init__).


##### `_connect_flow`  (lines 1428–1466)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...], index: IndexBackend | None=None, embed: EmbedClient | None=None, resumption: ConnectResume | Non
```

**Purpose**: Builds the OAuth connect flow used to authorize external connectors and store grants. OAuth is the common browser-based sign-in process where a user approves access to another service.

**Data flow**: It returns `None` if no credential store exists. Otherwise it gathers connector OAuth providers, checks for duplicate provider names, computes the redirect URI, builds connection hooks, and returns a `ConnectFlow` with encryption and grant storage.

**Call relations**: `run` calls this and installs the result globally with `install_connect_flow`. It relies on `_connect_redirect_uri` to produce the callback URL used by OAuth providers.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 4 external calls (__init__, __init__, connection_hooks, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 1469–1495)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL. This URL must be something the user’s browser can actually open after approving a connector.

**Data flow**: It reads `connect.public_base_url`, allows an empty value only when no connector providers exist, parses and validates the scheme and host, rejects wildcard bind addresses such as `0.0.0.0`, and returns the base URL plus the callback path.

**Call relations**: _connect_flow calls this while constructing the connect flow, so bad public URL configuration fails before any OAuth handoff begins.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).


### `core/src/ufo/runtime/__init__.py`

`other` · `import/package discovery`

In Python, an `__init__.py` file is like a label on a folder saying, “this folder is part of the program and can be imported.” This particular file is empty, so it does not run setup code, expose shortcuts, or define shared names for the `ufo.runtime` package. Its main value is structural: it lets Python and tooling treat `core/src/ufo/runtime` as a package location where runtime-related code can live. Without it, some import styles or older Python tooling might not recognize the folder in the expected way. Think of it as a blank cover page for a chapter: it does not contain the chapter’s content, but it helps the rest of the book know the chapter exists.


### `core/src/ufo/runtime/runtime_instance.py`

`orchestration` · `startup and background loops during serving`

This file is the fleet’s housekeeping crew. Each serve process writes a small database row saying “I am here,” then refreshes that row every few seconds. Other processes use those fresh rows as proof that a process is still alive. Think of it like workers clocking in and tapping a badge reader: if a badge has not been tapped recently, the system assumes that worker has left and someone else can pick up the abandoned work.

The file also defines three repeating background sweeps. Executor recovery looks for DBOS workflows that are still pending under an executor id whose process no longer has a fresh heartbeat, then asks DBOS to recover them. DBOS is the durable workflow system here: it records workflow progress so work can resume after failure.

Cancel reconciliation spreads cancellation down a tree of turns. A “turn” is one unit of conversation or agent work. If a parent turn is cancelled, this sweep finds live dependent descendants and cancels them too.

Stranded turn reconciliation fixes a different stuck state: a turn marked running, but whose workflow is no longer advancing or no longer exists. After a grace period, it cancels that turn so the conversation does not wait forever on work that cannot continue.

#### Function details

##### `record_fleet_seat`  (lines 42–57)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: Creates this process’s presence row in the shared runtime table before the durable workflow engine starts. This makes the process visible as alive so recovery logic does not accidentally treat its own new work as abandoned.

**Data flow**: It receives an instance id. It opens an owner database transaction, inserts a runtime_instance row with that id, no workspace, and current timestamps, then writes a log entry saying the fleet seat was recorded. It returns nothing, but the database now contains the process’s liveness marker.

**Call relations**: This is the first “I am alive” mark for a serve process. It uses the database transaction helper to write the row, then hands observability information to the logger so operators can see that the seat exists.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 70–80)

```
async def run(self) -> None
```

**Purpose**: Runs forever, periodically refreshing this process’s liveness row. It keeps going even if one database update fails, because one missed heartbeat should not make a healthy process look dead.

**Data flow**: It reads the instance id stored on the Heartbeat object. On each cycle it calls Heartbeat.beat to update the database, logs any SQL database error, then sleeps for the configured heartbeat interval before trying again. It produces no final result because it is a long-running loop.

**Call relations**: This loop is the driver for Heartbeat.beat. It calls beat on every tick, uses asyncio.sleep to wait between ticks, and uses the logger when a tick fails so the loop can continue instead of stopping.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 82–92)

```
async def beat(self) -> None
```

**Purpose**: Writes one fresh heartbeat timestamp for this process. This is the small database update that says “this instance is still alive right now.”

**Data flow**: It takes the Heartbeat object’s instance id, opens an owner database transaction, and updates the matching runtime_instance row with current heartbeat and update timestamps. It returns nothing, but the row becomes fresh again.

**Call relations**: Heartbeat.run calls this on every heartbeat cycle. The function does the actual database update through the transaction helper and SQLAlchemy’s update builder.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 94–100)

```
async def retire(self) -> None
```

**Purpose**: Removes this process’s liveness row during a graceful shutdown. This lets other processes see immediately that the seat is gone instead of waiting for the heartbeat to become stale.

**Data flow**: It reads the Heartbeat object’s instance id, opens an owner database transaction, and deletes the matching runtime_instance row. It returns nothing, but the database no longer advertises this process as alive.

**Call relations**: The serve shutdown path calls this when stopping the executor. It uses the same owner transaction helper as heartbeat writes, but deletes the row instead of refreshing it.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 120–126)

```
async def run(self) -> None
```

**Purpose**: Runs the executor recovery sweep on a timer. Its job is to keep abandoned pending workflows from staying stuck after their original process dies.

**Data flow**: It repeatedly sleeps for its configured interval, then calls ExecutorRecovery.sweep. If the sweep hits a database error or DBOS workflow error, it logs the error class and continues looping. It does not return during normal operation.

**Call relations**: This is the scheduler for ExecutorRecovery.sweep. It waits between recovery attempts with asyncio.sleep and reports failed ticks through the logger rather than letting the background loop die.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 128–136)

```
async def sweep(self) -> None
```

**Purpose**: Finds pending workflows whose executor no longer appears alive, then asks DBOS to recover them. This prevents work from being stranded under a dead process id.

**Data flow**: It asks _pending_executors for executor ids that still own pending workflows, and asks _live_executors for executor ids with fresh heartbeat rows. It subtracts live ids from pending ids; for each remaining stranded executor, it calls DBOS recovery in a worker thread and logs how many workflows were recovered. The outcome is changed workflow state inside DBOS, not a returned value.

**Call relations**: ExecutorRecovery.run calls this on each timer tick. This function combines the two helper sets, then hands each dead executor id to DBOS._recover_pending_workflows through asyncio.to_thread so the blocking recovery call does not block the async loop.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 138–150)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: Looks in DBOS for executor ids that currently hold pending workflows. These ids are possible recovery targets, but only if they are not also alive.

**Data flow**: It asks DBOS to list pending workflows, without loading their inputs or outputs. If the result reaches the scan limit, it logs that the scan may be capped. It returns a set of executor id strings found on those pending workflow records.

**Call relations**: ExecutorRecovery.sweep calls this before comparing against live executors. The DBOS listing is run through asyncio.to_thread because the DBOS call is synchronous, and the logger records when the scan reaches its configured limit.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 152–162)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: Reads the database for process ids whose heartbeat is recent enough to count as alive. These ids must not be recovered, because their workflows may still be actively running.

**Data flow**: It calculates a cutoff time from the current UTC time minus the stale-after window. It selects runtime_instance rows whose heartbeat_at is newer than that cutoff, then returns their ids as strings in a set. It does not change the database.

**Call relations**: ExecutorRecovery.sweep calls this alongside _pending_executors. The result is used as the “do not touch” list before recovery is attempted.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 186–192)

```
async def run(self) -> None
```

**Purpose**: Runs the cancellation reconciliation sweep on a timer. It makes sure cancellation eventually spreads from a cancelled turn to dependent live descendants.

**Data flow**: It repeatedly sleeps for its configured interval, then calls CancelReconciler.sweep. If a database or DBOS error happens, it logs the error class and continues. It has no normal final output because it is a background loop.

**Call relations**: This is the scheduler for CancelReconciler.sweep. It uses asyncio.sleep for pacing and the logger for failed sweep attempts.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 194–201)

```
async def sweep(self) -> None
```

**Purpose**: Finds live turns that sit underneath a cancelled ancestor and cancels them. This is what turns one local cancellation into a full cleanup of dependent work.

**Data flow**: It builds and runs the orphan query, getting turn ids and workspace ids for live descendants of cancelled turns. For each row, it enters that workspace context and calls cancel_one_turn. If that call actually cancels the turn, it logs the reconciled turn id. It returns nothing, but some turns may become cancelled.

**Call relations**: CancelReconciler.run calls this on every interval. This function relies on _orphans_query to identify candidates, uses owner_tx to read them, switches into each turn’s workspace with ws, and delegates the actual cancellation to cancel_one_turn.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `CancelReconciler._orphans_query`  (lines 203–243)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: Builds the database query that finds non-finished turns with a cancelled dependent ancestor. It captures indirect descendants too, not only immediate children.

**Data flow**: It starts from live, non-terminal turns and walks upward through parent_turn_id links, but only across relationships that count as dependency links. When the climb reaches a cancelled ancestor, the original live turn is selected with its workspace id. The function returns a SQL query object; it does not run the query itself.

**Call relations**: CancelReconciler.sweep calls this to get the query it will execute. The query uses _dependent_parent to decide which parent links should be followed while walking the turn tree.

*Call graph*: calls 1 internal fn (_dependent_parent); called by 1 (sweep); 1 external calls (select).


##### `CancelReconciler._dependent_parent`  (lines 245–255)

```
def _dependent_parent(self, turn: sa.Table | sa.FromClause) -> sa.ColumnElement
```

**Purpose**: Defines which parent link should count when cancellation moves upward through a turn tree. It follows normal dependent child links, but deliberately avoids crossing into independent spawned-agent work.

**Data flow**: It receives a turn table-like object. It returns a SQL expression: use parent_turn_id when the turn has a subagent profile or came from intent admission, otherwise use null so the recursive climb stops. It produces a query fragment rather than reading or writing data itself.

**Call relations**: CancelReconciler._orphans_query uses this helper while building its recursive query. The helper supplies the rule that keeps cancellation from crossing boundaries into independent agent children.

*Call graph*: called by 1 (_orphans_query); 3 external calls (case, null, or_).


##### `StrandedTurnReconciler.run`  (lines 289–295)

```
async def run(self) -> None
```

**Purpose**: Runs the stranded-turn cleanup sweep on a timer. It looks for turns marked running even though their workflow is no longer able to move them forward.

**Data flow**: It repeatedly sleeps for the configured interval and calls StrandedTurnReconciler.sweep. If the sweep fails because of a database or DBOS error, it logs the error class and keeps looping. It does not return in normal service.

**Call relations**: This is the timed driver for StrandedTurnReconciler.sweep. It spaces out checks with asyncio.sleep and protects the background loop by logging failed ticks instead of crashing.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `StrandedTurnReconciler.sweep`  (lines 297–313)

```
async def sweep(self) -> None
```

**Purpose**: Cancels running turns whose recorded workflow attempt is no longer present or no longer advancing. This prevents conversations from treating impossible work as still alive forever.

**Data flow**: It queries old enough running turns that have a running_attempt value. If the scan reaches the limit, it logs that. It asks _advancing_attempts which of those workflow attempts are still pending, enqueued, or delayed in DBOS. For each claimed turn whose attempt is not advancing, it enters the turn’s workspace and calls cancel_one_turn; successful cancellations are logged. It returns nothing, but stranded turns may become cancelled.

**Call relations**: StrandedTurnReconciler.run calls this periodically. This function uses _claimed_query to find possible stuck rows, _advancing_attempts to avoid cancelling work DBOS still carries, ws to set the correct workspace, and cancel_one_turn to do the safe final cancellation.

*Call graph*: calls 2 internal fn (_advancing_attempts, _claimed_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `StrandedTurnReconciler._claimed_query`  (lines 315–332)

```
def _claimed_query(self) -> sa.Select
```

**Purpose**: Builds the database query for old running turns that have a claimed workflow attempt. The age grace period avoids mistaking a freshly claimed turn for a stuck one.

**Data flow**: It calculates a cutoff time from the current UTC time minus the grace window. It returns a SQL query selecting running turns with a non-empty running_attempt and updated_at older than that cutoff, ordered oldest first and capped by the scan limit. It does not execute the query.

**Call relations**: StrandedTurnReconciler.sweep calls this before reading candidate rows. The returned query supplies the first filter in the stranded-turn cleanup process.

*Call graph*: called by 1 (sweep); 3 external calls (now, timedelta, select).


##### `StrandedTurnReconciler._advancing_attempts`  (lines 334–345)

```
async def _advancing_attempts(self, attempts: list[str]) -> set[str]
```

**Purpose**: Checks which workflow attempts are still carried by DBOS in a state that can move forward. Those attempts are considered alive and must not be cancelled as stranded.

**Data flow**: It receives a list of workflow attempt ids. If the list is empty, it immediately returns an empty set so it does not accidentally ask DBOS for everything. Otherwise it asks the DBOS client for workflows with those ids whose status is pending, enqueued, or delayed, and returns the workflow ids it found as a set.

**Call relations**: StrandedTurnReconciler.sweep calls this after collecting claimed running turns. Its result tells the sweep which rows to skip, so only turns whose workflow is absent or no longer advancing are passed on to cancel_one_turn.

*Call graph*: called by 1 (sweep).

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-pack-and-feature-selection` — The chosen product packs and feature switches that decide which parts of the system are enabled.
- `reg-extension-installation-lock` — The saved list of installed extensions and exact versions that should be loaded again consistently.
- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-database-schema-version` — The record of which database upgrades have already been applied and what storage shape the system expects.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-workspace-directory` — The durable list of workspaces and their core ownership, admin, billing, and setup state.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-live-update-stream` — The temporary live feed of progress messages that open clients and other server processes can follow.
- `reg-cancellation-flags` — The shared stop signals and cleanup markers used to cancel turns, child work, sandboxes, and stuck jobs safely.
- `reg-runtime-instance-fleet` — The record of which server processes are alive and which shared listeners or jobs they currently own.
- `reg-background-job-queue` — The shared pool of delayed or recurring work that workers claim, run, retry, and clean up.
- `reg-egress-policy` — The shared network exit rules that decide which outside addresses sandboxes may contact and when secrets may be added.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-extension-catalog-update-cache` — Cached public extension catalog and update/compatibility check metadata used when selecting, installing, bundling, or refreshing extensions.
- `reg-runtime-connection-pools` — Live pooled connections and reusable clients for shared services such as the database, Redis/live hub, blob storage, model providers, connector APIs, and sandbox/browser providers.
