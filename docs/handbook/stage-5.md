# Serve runtime startup, liveness registration, and scheduler activation  `stage-5`

This stage is part of starting up the long-running UFO service. It is the moment when the process stops being just a program on disk and becomes a live worker in the system. The main entry point, core/src/ufo/serve.py, wires together the pieces the service needs: the web server, workflow runner, database access, extensions, credentials, sandboxes, connectors, live update hub, and background jobs. It also starts workspace-level schedulers, so each workspace can run its own timed or queued work.

The runtime_instance.py file acts like the service’s attendance sheet and cleanup crew. It registers that this process is alive, keeps that status fresh, and helps the rest of the system know which workers can still be trusted. It also runs repair loops in the background. These loops look for work left behind by crashes, cancelled tasks, or lost workflow attempts, then reclaim or clean it up. Together, these files turn on the service, announce it, start its background machinery, and prevent abandoned jobs from staying stuck forever.

## Files in this stage

### Serve Runtime Activation
Starts the shared serve process, registers the runtime as alive, and activates background repair loops for abandoned work.

### `core/src/ufo/serve.py`

`entrypoint` · `startup, main loop, background work, shutdown`

This is the service’s main assembly point. Its job is to turn configuration and installed extension manifests into a live server that can serve many workspaces safely from one process. Without this file, the parts of the system would exist, but they would not be connected: HTTP routes would not be mounted, background jobs would not run, sandboxes would not know how to reach the proxy, and requests might read data from the wrong workspace.

The file starts by loading configuration, setting up logging and health checks, opening the database, loading extensions, preparing encrypted credential storage, and registering this process as an active worker. It then chooses concrete backends for things that can vary by deployment, such as the live-message hub, browser control provider, search provider, blob storage, terminal transport, feature flags, and connector authentication.

A major theme is safe workspace scoping. One fleet serves every workspace, so each request or workflow must prove which workspace it belongs to before touching data. The `WorkspaceScopeBoundary` middleware acts like a doorman who clears the room before and after each visitor, preventing one request’s workspace identity from leaking into another.

The file also mounts extension routes and shared surface routes, starts DBOS workflow jobs, runs long-lived recovery and delivery loops, and shuts down carefully so unfinished workflows are not accidentally run twice by another process.

#### Function details

##### `_assert_no_reserved_routes`  (lines 202–218)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Checks that this service has not mounted web routes under paths reserved for the onboarding and login gateway. This prevents a route from appearing to exist in the app while being hidden by the front-door proxy.

**Data flow**: It reads the FastAPI app’s registered routes, compares each route path with the reserved prefixes, and either returns quietly or raises an error listing the conflicting paths.

**Call relations**: The main `run` function calls this near the end of startup, after routes have been mounted. It acts as a final safety inspection before the server begins accepting traffic.

*Call graph*: called by 1 (run).


##### `run`  (lines 221–464)

```
def run() -> None
```

**Purpose**: Starts the shared fleet process. It builds all major services, registers jobs and routes, launches DBOS workflows, runs the web server, and performs careful shutdown.

**Data flow**: It reads configuration, environment variables, extension manifests, database settings, credentials, and deployment options. From those inputs it creates stores, registries, runtime objects, route handlers, background workers, and finally a running Uvicorn web server; on exit it drains and retires the worker when safe.

**Call relations**: This is the central caller for almost every helper in the file. It calls selection helpers to choose backends, mounting helpers to expose routes, job helpers to start workflow work, and shutdown helpers when Uvicorn stops.

*Call graph*: calls 22 internal fn (from_env, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _one_shot, _preview_settings, _proxy_endpoint (+12 more)); 59 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `run.invoker_for`  (lines 286–287)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates an admission invoker for one workspace. An admission invoker is the object used to submit or resume work on behalf of that specific workspace.

**Data flow**: It receives a workspace ID, combines it with the shared admission service built by `run`, and returns a workspace-bound invoker.

**Call relations**: It is defined inside `run` because it depends on the admission object created during startup. `run` passes it into the runtime and job launch code so later jobs can admit work for the right workspace.

*Call graph*: 1 external calls (__init__).


##### `_one_shot`  (lines 467–479)

```
def _one_shot(coro: Coroutine[Any, Any, T]) -> T
```

**Purpose**: Runs a single asynchronous setup or cleanup task on a temporary event loop, then cleans up database engines tied to that loop. This avoids leaving database connections attached to a loop that has already been closed.

**Data flow**: It receives a coroutine, wraps it in a cleanup step, runs that wrapper with `asyncio.run`, and returns the coroutine’s result after loop-specific database resources are disposed.

**Call relations**: `run`, `_proxy_endpoint`, and `_stop_executor` use this for isolated database-touching actions during startup and shutdown. It delegates the actual awaited work to `_one_shot.step`.

*Call graph*: called by 3 (_proxy_endpoint, _stop_executor, run); 1 external calls (run).


##### `_one_shot.step`  (lines 473–477)

```
async def step() -> T
```

**Purpose**: Performs the actual await inside `_one_shot` and guarantees cleanup afterward. It is the small inner routine that makes the temporary event loop safe to discard.

**Data flow**: It awaits the original coroutine and captures its result. Whether that coroutine succeeds or fails, it then asks the database layer to dispose engines for the current loop before returning or re-raising.

**Call relations**: It is only used by `_one_shot`. Its cleanup call protects callers such as `run` and `_stop_executor` from leaking unusable database connections.

*Call graph*: 1 external calls (dispose_loop_engines).


##### `_stop_executor`  (lines 482–496)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Stops DBOS workflow execution and retires this worker’s seat only if no workflows are still active. This prevents another process from picking up the same work while it is still running here.

**Data flow**: It receives the DBOS object, heartbeat object, and shutdown timeout. It asks DBOS to drain and destroy execution, checks the active workflow set, logs and keeps the seat if work remains, or retires the heartbeat seat if the process is empty.

**Call relations**: `run` calls this in its final shutdown block. It uses `_one_shot` to run the asynchronous heartbeat retirement safely.

*Call graph*: calls 2 internal fn (retire, _one_shot); called by 1 (run); 2 external calls (destroy, log).


##### `_shared_owner_dsn`  (lines 499–513)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string used for owner-level cross-workspace reads. This special connection can enumerate work across workspaces before each item is re-scoped safely.

**Data flow**: It reads the owner DSN from an environment variable or configuration. If neither is set, it raises a clear startup error; otherwise it returns the DSN string.

**Call relations**: `run` calls this before initializing the owner database engine. The value is needed by background sweeps that must first find work across all workspaces.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 516–583)

```
def _launch_jobs(runtime: Runtime, invoker_for: InvokerFactory, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts the system’s DBOS-backed background jobs. These include source syncing, turn dispatch, page-change handling, delivery cleanup, preview rendering, and extension-defined jobs.

**Data flow**: It receives the runtime, an invoker factory, a sync driver, and a page feed. It builds probe access, page-change and preview runners, combines core and extension job bindings, disables configured jobs, and launches a `JobRunner`.

**Call relations**: `run` calls this after the runtime is initialized and sources are configured. The launched job runner hands work to DBOS schedules and to handlers that use runtime services such as blobs, sandboxes, models, indexes, and credentials.

*Call graph*: called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, connector_clis (+3 more)).


##### `_source_backends`  (lines 586–600)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the map of source-sync backends that can import pages or files into UFO. It includes the built-in folder source and any source providers declared by extensions.

**Data flow**: It reads each manifest’s source providers and credential declarations. It creates a backend object for each unique backend name, raising an error if two extensions claim the same name, and returns the finished map.

**Call relations**: `run` passes this map into `SyncDriver`. The sync driver later uses the selected backend name from configured sources to know how to fetch content.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_source_identity_resolvers`  (lines 603–644)

```
def _source_identity_resolvers(manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore) -> dict[str, SourceIdentityResolver]
```

**Purpose**: Builds functions that can discover a surface’s current user identity for source syncing. This lets synced content be tied to the correct external account when a surface supports that lookup.

**Data flow**: It reads manifests, credential storage, and the workspace blob store. For each surface that declares a self-user lookup, it creates a resolver function and returns a map from surface name to resolver.

**Call relations**: `run` gives these resolvers to `SyncDriver`. The nested resolver functions later bind the correct workspace before calling extension-provided identity code.

*Call graph*: called by 1 (run).


##### `_source_identity_resolvers.resolve`  (lines 617–641)

```
async def resolve(workspace_id: UUID, handler=surface.self_user_id, slots=declared, store=credentials) -> str | None
```

**Purpose**: Runs one surface’s identity lookup inside a specific workspace. It lets an extension ask, “who is the current user for this surface in this workspace?”

**Data flow**: It receives a workspace ID, creates a credential-reading helper limited to the extension’s declared slots, binds the workspace, builds a `SurfaceIdentityContext`, and returns the handler’s user ID result or `None`.

**Call relations**: This function is created by `_source_identity_resolvers` and later used by source syncing. It hands credential access and blob access to the extension’s identity handler.

*Call graph*: 2 external calls (__init__, ws).


##### `_source_identity_resolvers.resolve.credential`  (lines 623–632)

```
async def credential(credential_slot: str) -> str
```

**Purpose**: Reads one credential slot for a surface identity lookup, while enforcing that the surface declared permission to use that slot.

**Data flow**: It receives a credential slot name, checks that the slot is declared, checks that a credential store exists, then retrieves the encrypted credential value for the current workspace.

**Call relations**: It is used only inside `_source_identity_resolvers.resolve`. It protects extension identity code from reading undeclared credentials.


##### `_select_hub`  (lines 647–665)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live-message hub backend for this process. The hub is how surfaces receive live updates, similar to a switchboard for real-time messages.

**Data flow**: It starts with the built-in in-process hub, adds hub builders from manifests, checks for duplicate backend names, looks up the configured backend, and returns a built hub or raises if the name is unknown.

**Call relations**: `run` calls this during startup. The selected hub is later shared with admissions, tailers, surfaces, and runtime components.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_terminal_transport`  (lines 668–710)

```
def _select_terminal_transport(config: Config, manifests: tuple[Manifest, ...], blob: FleetBlobStore) -> TerminalTransport
```

**Purpose**: Chooses how terminal sessions communicate between browser connections and running workflows. It refuses unsafe combinations where a multi-process fleet would use a process-local terminal channel.

**Data flow**: It reads hub and terminal configuration, validates that an in-process terminal is not paired with a cross-process hub, gathers extension-provided terminal transport builders, and returns the selected transport.

**Call relations**: `run` uses this when constructing sandbox support. The chosen transport is passed into `ConversationSandbox` so terminal operations can reach the right running environment.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 713–741)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Chooses the browser automation provider, if one is configured and installed. CDP means Chrome DevTools Protocol, a way to control a browser programmatically.

**Data flow**: It gathers CDP provider specs from manifests, checks for duplicate names, finds the configured provider, validates credential key availability when needed, and returns a built provider or `None`.

**Call relations**: `run` calls this to give browser capability to the runtime. `_require_cdp_provider` also calls it when an extension declares that browser control is mandatory.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 744–768)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks each extension’s declared required seams before the server starts. A seam is a pluggable capability, such as search or browser control, that an extension depends on.

**Data flow**: It reads every manifest’s required seam names, finds the matching checker, runs it, and wraps any failure in an error that names the extension and missing capability.

**Call relations**: `run` calls this during startup after validating extension tools. It delegates specific checks to functions such as `_require_cdp_provider`, `_require_search_provider`, and `_require_memory_search`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 771–785)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that browser automation is actually available when an extension requires it. It turns a missing optional browser backend into a clear startup failure.

**Data flow**: It calls `_select_cdp_provider` with the current configuration and manifests. If selection returns `None`, it raises an error naming the configured provider.

**Call relations**: _validate_requires uses this when a manifest lists the `cdp_providers` seam. It relies on `_select_cdp_provider` for the detailed backend and credential checks.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 788–823)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the external search backend for research features. It can return no provider when search is not configured, unless another check requires it.

**Data flow**: It gathers search provider specs from manifests, checks for duplicate backend names, reads the configured search provider, validates that credentials are available, and builds the selected provider.

**Call relations**: `run` calls this to install search capability into the runtime. `_require_search_provider` calls it when an extension says search is required.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 826–839)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a search backend is configured and usable when research tools require one.

**Data flow**: It checks that the search provider setting is present, then calls `_select_search_provider` to verify the named backend exists and can be built.

**Call relations**: _validate_requires uses this for the `search_providers` seam. It turns missing or broken research search setup into a startup error.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_select_flag_provider`  (lines 842–869)

```
def _select_flag_provider(config: Config, manifests: tuple[Manifest, ...]) -> FeatureProvider | None
```

**Purpose**: Chooses the feature-flag provider, if configured. Feature flags are deployment switches that let code choose between enabled and disabled behavior.

**Data flow**: It gathers flag provider specs from manifests, rejects duplicate backend names, returns `None` if no backend is configured, builds the selected provider, and warns if the provider cannot be keyed.

**Call relations**: `run` calls this before initializing flags. Its output is passed to the flag system, which then answers feature checks during runtime.

*Call graph*: called by 1 (run); 2 external calls (__init__, warn).


##### `_require_memory_search`  (lines 872–898)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that exactly one usable default memory-search provider is installed when an extension requires memory search.

**Data flow**: It scans manifests for providers with the default memory-search name, raises if none or more than one are found, then checks that credentials are available if that provider declares credential slots.

**Call relations**: _validate_requires calls this for the `memory_search` seam. It does not build the provider itself; it verifies that the installed extension set and credential setup can support it.


##### `_select_auth_proxy`  (lines 910–947)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy for connector credentials. This is used when a connector does not have its own broker and needs host-side credential access.

**Data flow**: It gathers auth proxy specs from manifests, applies the configured backend or auto-selects when only one exists, validates credentials, builds the selected proxy, and returns it or `None`.

**Call relations**: _connector_registry calls this while building connector routing. The selected proxy becomes the fallback path for connector feed-sync credential resolution.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 950–998)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient, public_base_url: str | None) -> None
```

**Purpose**: Adds extension-defined HTTP routes under `/ext/<extension>/...`. Each route must identify the workspace before extension code can run.

**Data flow**: It reads manifests and their route specs, creates an extension context with limited credential and index access, wraps each route in an endpoint that checks authorization and binds the workspace, then adds the route to FastAPI.

**Call relations**: `run` calls this after core jobs and before shared surfaces are mounted. The nested endpoint hands verified requests to extension handlers.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 982–992)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Processes one request to an extension route. It refuses unidentified requests and runs authorized extension code inside the identified workspace.

**Data flow**: It receives a web request, asks the route’s identify function for a workspace, returns a 401 response if none is found, or binds that workspace and awaits the extension handler’s response.

**Call relations**: This endpoint is created by `_mount_ext_routes` for each extension route. It hands control to the extension handler only after workspace scoping is established.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 1019–1027)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Clears workspace identity before and after each HTTP request. This prevents accidental leakage of one request’s workspace into another request handled by the same process.

**Data flow**: It receives the ASGI request scope, receive channel, and send channel. Non-HTTP traffic passes through unchanged; HTTP traffic has `current_workspace` set to `None`, then the downstream app runs, and finally the workspace is cleared again.

**Call relations**: _mount_shared_surfaces installs this as middleware. Surface endpoints set the workspace during request handling, and this boundary guarantees cleanup after the full response has been sent.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 1030–1225)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: WorkspaceBlobStore, sandboxes: ConversationSandbox, hub: Hub, dbos_client: DBOSClie
```

**Purpose**: Mounts all shared-fleet-capable surface routes and starts surface-related delivery helpers. A surface is a user-facing interface, such as a browser UI or integration endpoint.

**Data flow**: It receives the app plus runtime services such as credentials, blobs, sandboxes, hub, DBOS client, models, skills, memory, and connectors. It builds common surface context factories, adds middleware, registers routes and listeners, mounts the home redirect, and creates writeback pollers when surfaces can post back.

**Call relations**: `run` calls this after extension routes are mounted. It uses `_connector_entries`, `_mount_home`, `home_surface`, and the nested `context_for` and `endpoint` functions to connect incoming surface requests to workspace-scoped runtime services.

*Call graph*: calls 5 internal fn (_connector_entries, _mount_home, home_surface, bundled_skills, from_skills); called by 1 (run); 24 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+14 more)).


##### `_mount_shared_surfaces.context_for`  (lines 1123–1160)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the full `SurfaceContext` for one workspace and one surface. This context is the toolbox a surface handler uses to admit turns, read blobs, access skills, stop work, use credentials, and describe deployment capabilities.

**Data flow**: It receives a workspace ID and surface name, combines them with the shared services prepared by `_mount_shared_surfaces`, computes items such as home surface and admissible frames, and returns a ready-to-use context object.

**Call relations**: Surface endpoints, listeners, and pollers call this when they need to run surface code. It creates workspace-bound admission and passes along shared runtime objects in a controlled shape.

*Call graph*: calls 1 internal fn (home_surface); 3 external calls (__init__, __init__, frame_admissible).


##### `_mount_shared_surfaces.endpoint`  (lines 1188–1202)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Processes one request to a mounted surface route. It verifies the request, binds the workspace, and calls the surface’s route handler with a workspace-specific context.

**Data flow**: It receives a request, asks the surface’s identify function to resolve it using surface authentication, returns an immediate response or 401 when appropriate, or sets the current workspace and awaits the surface handler.

**Call relations**: This endpoint is created inside `_mount_shared_surfaces` for each surface route. It is protected by `WorkspaceScopeBoundary`, which cleans up the workspace after the response finishes.

*Call graph*: 2 external calls (Response, set).


##### `home_surface`  (lines 1228–1235)

```
def home_surface(manifests: tuple[Manifest, ...]) -> str | None
```

**Purpose**: Finds the single surface marked as the browser home. This lets the bare service URL redirect users to the right user interface.

**Data flow**: It scans all manifests for surfaces marked as home. It raises if more than one claims that role, returns the one name if found, or returns `None` when there is no browser home.

**Call relations**: `run`, `_mount_ext_routes`, `_mount_shared_surfaces`, `_mount_shared_surfaces.context_for`, and `_mount_home` all use this to build URLs and contexts that point users back to the main surface.

*Call graph*: called by 5 (_mount_ext_routes, _mount_home, _mount_shared_surfaces, context_for, run).


##### `_mount_home`  (lines 1238–1250)

```
def _mount_home(app: FastAPI, manifests: tuple[Manifest, ...]) -> None
```

**Purpose**: Adds a simple `GET /` route that redirects to the configured home surface. This turns the service root into a useful front door instead of a dead end.

**Data flow**: It asks `home_surface` for the home surface name. If one exists, it creates a redirect endpoint and adds it to the FastAPI app; otherwise it does nothing.

**Call relations**: _mount_shared_surfaces calls this after mounting surface routes. The nested `home` function performs the actual redirect when a browser visits `/`.

*Call graph*: calls 1 internal fn (home_surface); called by 1 (_mount_shared_surfaces); 1 external calls (add_route).


##### `_mount_home.home`  (lines 1247–1248)

```
async def home(_request: Request) -> Response
```

**Purpose**: Redirects a browser from `/` to the selected home surface path.

**Data flow**: It ignores the request details and returns a 303 redirect response pointing at `/surface/<home-surface>`.

**Call relations**: This endpoint is registered by `_mount_home`. It relies on `_mount_home` having already chosen a valid home surface.

*Call graph*: 1 external calls (RedirectResponse).


##### `_serve_lifespan`  (lines 1254–1283)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs app-loop background tasks for as long as the web server is alive. These tasks recover abandoned workflows, reconcile cancellations, handle stranded turns, run surface delivery pollers, and run surface listeners.

**Data flow**: At startup it registers configured sources, creates an async task group, starts recovery, reconciliation, poller, and listener tasks, then yields control to FastAPI. On shutdown it cancels all tasks it started.

**Call relations**: `run` gives this function to FastAPI as the app lifespan manager. It uses state values that `run` and `_mount_shared_surfaces` placed on the app.

*Call graph*: 5 external calls (__init__, __init__, __init__, TaskGroup, register_sources).


##### `_preview_settings`  (lines 1286–1295)

```
def _preview_settings(config: Config) -> tuple[tuple[str, int], str] | None
```

**Purpose**: Reads and validates sandbox preview-service settings. The preview service renders documents or sites for sandbox-related features.

**Data flow**: It parses the configured preview service address. If disabled, it returns `None`; if enabled, it requires a preview token from the environment and returns the service address plus token.

**Call relations**: `run` calls this when building renderers and site preview support. `_proxy_endpoint` also calls it so egress policy can allow preview traffic with the right token.

*Call graph*: called by 2 (_proxy_endpoint, run); 1 external calls (parse_preview_service).


##### `_proxy_endpoint`  (lines 1298–1376)

```
def _proxy_endpoint(app: FastAPI, config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, blob: FilesystemBlobStore | S3BlobS
```

**Purpose**: Sets up sandbox egress control and returns the proxy information that sandboxes need. Egress means outbound network access from a sandbox, which must be filtered, authorized, and metered.

**Data flow**: It reads proxy, certificate, token, cache, preview, connector, model, artifact, and credential settings. It builds per-agent network rules, mounts control and git-credential routes on the FastAPI app, and returns a `ProxyEndpoint` containing the proxy port, public URL, and trusted certificate.

**Call relations**: `run` calls this while constructing `ConversationSandbox`. It uses `_preview_settings`, `_ephemeral_egress_ca`, and `_one_shot`, then hands routing rules to `EgressControl` so the separate egress proxy can ask serve what to allow.

*Call graph*: calls 3 internal fn (_ephemeral_egress_ca, _one_shot, _preview_settings); called by 1 (run); 13 external calls (__init__, __init__, __init__, __init__, include_router, token_urlsafe, connector_transfer_hosts, derive_artifact_store_rules, derive_manifest_rules, connector_clis (+3 more)).


##### `_ephemeral_egress_ca`  (lines 1379–1398)

```
def _ephemeral_egress_ca() -> str
```

**Purpose**: Creates a temporary certificate authority certificate for local runs without a shared egress proxy certificate. A certificate authority is a trust anchor used to decide whether proxy-made certificates should be trusted.

**Data flow**: It generates a private key, builds a self-signed CA certificate named for local UFO egress, serializes only the certificate to PEM text, and returns that text.

**Call relations**: _proxy_endpoint calls this only when no hosted proxy URL is configured and no shared CA certificate is supplied. In that local default, the certificate is well-formed even though no real proxy may be running.

*Call graph*: called by 1 (_proxy_endpoint); 9 external calls (generate_private_key, SHA256, BasicConstraints, CertificateBuilder, Name, NameAttribute, random_serial_number, now, timedelta).


##### `_connector_registry`  (lines 1404–1418)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the registry that routes connector-related work. Connectors are integrations with outside services, often using OAuth, a browser-based permission flow.

**Data flow**: It reads connector declarations from manifests, builds connector entries, selects a fallback auth proxy, opens the connector namespace resolver, and returns a `ConnectorRegistry`.

**Call relations**: `run` calls this during startup. `_mount_shared_surfaces`, runtime tools, and source syncing use the resulting registry to understand available connector providers.

*Call graph*: calls 2 internal fn (_connector_entries, _select_auth_proxy); called by 1 (run); 2 external calls (__init__, open_connector_namespace).


##### `_connector_entries`  (lines 1421–1431)

```
def _connector_entries(manifests: tuple[Manifest, ...]) -> dict[str, ConnectorEntry]
```

**Purpose**: Collects connector provider metadata from all manifests and checks that no provider name is registered twice.

**Data flow**: It scans each manifest’s connectors, extracts the OAuth provider name, label, and broker, creates a `ConnectorEntry` for each, and returns a provider-to-entry map.

**Call relations**: _connector_registry uses this for the main registry. `_mount_shared_surfaces` also uses it when it needs a default connector registry.

*Call graph*: called by 2 (_connector_registry, _mount_shared_surfaces); 1 external calls (__init__).


##### `_connect_flow`  (lines 1434–1472)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...], index: IndexBackend | None=None, embed: EmbedClient | None=None, resumption: ConnectResume | Non
```

**Purpose**: Creates the OAuth connect flow for installed connectors. This flow starts authorization, protects state, stores grants, and completes browser callbacks.

**Data flow**: It receives the credential store, config, manifests, and optional index, embed, and resume support. If credentials are absent it returns `None`; otherwise it gathers OAuth providers, validates the redirect URI, builds connection hooks, and returns a `ConnectFlow`.

**Call relations**: `run` calls this and installs the result as the global connect flow. It delegates redirect URL validation to `_connect_redirect_uri` and uses extension connection hooks to publish newly completed connections.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 4 external calls (__init__, __init__, connection_hooks, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 1475–1501)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL for connector login flows. This must be a real browser-openable URL because outside providers redirect the user back to it.

**Data flow**: It reads `connect.public_base_url` from config and the set of registered providers. If there are no providers it returns an inert or derived callback; otherwise it requires a scheme, host, and non-wildcard hostname, then appends the callback path.

**Call relations**: _connect_flow calls this while building the OAuth flow. Its result is shared by both halves of the connector authorization handoff: the initial request and the provider callback.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).


### `core/src/ufo/runtime/runtime_instance.py`

`orchestration` · `background during serve runtime`

This file is the shared fleet’s safety crew. Each serve process records a “seat” in the database and refreshes it every few seconds, like tapping a card reader to say “I am still here.” Other processes use that heartbeat to decide whether work owned by that process is still alive or has been abandoned.

There are three repair loops. ExecutorRecovery looks at DBOS workflows, where DBOS is the durable workflow system that remembers queued work across crashes. If a workflow is still pending under an executor id whose heartbeat has gone stale, it asks DBOS to recover that work so another live process can continue it. CancelReconciler spreads cancellation downward through a tree of turns: cancelling a parent turn only stops that one row at first, so this sweep finds still-live descendant turns and cancels them too. StrandedTurnReconciler fixes a narrower problem: a turn marked running may point to a workflow attempt that DBOS no longer knows how to advance. After a grace period, that turn is cancelled so the conversation no longer treats it as active work.

All loops tolerate temporary database or DBOS errors by logging the failure and trying again later. That is important because these loops are guardians; one bad tick should not stop the whole safety system.

#### Function details

##### `record_fleet_seat`  (lines 42–57)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: Creates the database row that says this serve process exists. This is done before durable workflow execution starts, so other repair loops do not mistake this new process’s work for abandoned work.

**Data flow**: It receives this process’s unique instance id. It opens an owner database transaction, inserts a runtime_instance row with no workspace attached, stamps the current time as its heartbeat and creation time, then logs that the fleet seat was recorded. Nothing is returned; the database row is the result.

**Call relations**: This is the first visible sign of a serve process joining the shared fleet. Later, Heartbeat.beat keeps the same row fresh, ExecutorRecovery._live_executors reads these rows to decide which executors are alive, and Heartbeat.retire removes the row during graceful shutdown.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 70–80)

```
async def run(self) -> None
```

**Purpose**: Runs the endless heartbeat loop for one serve process. It repeatedly refreshes the process’s liveness row so peers know the process is still alive.

**Data flow**: It uses the instance id stored on the Heartbeat object. Each cycle it calls Heartbeat.beat, logs a database error if that one refresh fails, then sleeps for the heartbeat interval before trying again. It normally does not return because it is a background loop.

**Call relations**: This loop is the driver for Heartbeat.beat. It is meant to run for the lifetime of the serve process, so ExecutorRecovery can safely treat a fresh heartbeat as proof that an executor should not be recovered by another process.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 82–92)

```
async def beat(self) -> None
```

**Purpose**: Writes one fresh liveness timestamp for this process. This small update is what tells the rest of the fleet, “do not recover my work; I am still here.”

**Data flow**: It reads the instance id from the Heartbeat object. It opens an owner database transaction, updates the matching runtime_instance row, and sets heartbeat_at and updated_at to the database’s current time. It returns nothing; the changed timestamp is the output.

**Call relations**: Heartbeat.run calls this once per loop cycle. ExecutorRecovery._live_executors later reads the timestamps this function writes, and uses them to avoid recovering workflows that belong to still-running processes.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 94–100)

```
async def retire(self) -> None
```

**Purpose**: Removes this process’s liveness row during a clean shutdown. This lets other processes see immediately that the seat is gone instead of waiting for the heartbeat to become stale.

**Data flow**: It reads the instance id from the Heartbeat object. It opens an owner database transaction and deletes the matching runtime_instance row. It returns nothing; the database no longer lists this process as live.

**Call relations**: The serve shutdown path calls this when stopping the executor. It complements Heartbeat.run: the run loop keeps the row fresh while alive, and retire removes it when the process exits normally.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 120–126)

```
async def run(self) -> None
```

**Purpose**: Runs the repeating sweep that rescues pending workflows left behind by dead serve processes. It keeps the durable workflow queue from getting stuck after a crash.

**Data flow**: It reads the interval settings from the ExecutorRecovery object. Each cycle it sleeps, calls ExecutorRecovery.sweep, logs database or DBOS workflow errors if the sweep fails, and then continues. It does not produce a normal return value because it is a long-running background loop.

**Call relations**: This is the scheduler for ExecutorRecovery.sweep. Every serve process can run it, so any surviving process can notice and recover work that belonged to a crashed peer.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 128–136)

```
async def sweep(self) -> None
```

**Purpose**: Finds executor ids that still have pending DBOS workflows but no fresh process heartbeat, then asks DBOS to recover those workflows. This is how abandoned durable work gets re-dispatched.

**Data flow**: It asks ExecutorRecovery._pending_executors for executors with pending workflows, and ExecutorRecovery._live_executors for executors whose heartbeat is still fresh. It subtracts live executors from pending executors. For each remaining dead-looking executor, it calls DBOS recovery in a worker thread and logs how many workflows were recovered.

**Call relations**: ExecutorRecovery.run calls this on a timer. It depends on heartbeat rows maintained by Heartbeat.beat and on pending workflow information from DBOS, then hands stranded executor ids back to DBOS recovery.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 138–150)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: Reads DBOS to find which executor ids currently own pending workflows. These are candidates for recovery, but only if their executor is no longer alive.

**Data flow**: It asks DBOS for up to a fixed limit of workflows in PENDING status, without loading large inputs or outputs. If the result hits the limit, it logs that the scan may be capped. It returns a set of executor id strings taken from the workflow statuses.

**Call relations**: ExecutorRecovery.sweep uses this as the “work that might be stranded” side of its comparison. The result is later checked against ExecutorRecovery._live_executors so live executors are not recovered by mistake.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 152–162)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: Reads the runtime_instance table to find which executors have refreshed their heartbeat recently. These executors are treated as alive and are protected from recovery.

**Data flow**: It calculates a cutoff time by subtracting the stale window from the current time. It opens an owner database transaction, selects runtime_instance ids whose heartbeat_at is newer than that cutoff, and returns those ids as strings in a set.

**Call relations**: ExecutorRecovery.sweep uses this as the “still alive” side of its comparison. The timestamps it reads are written by Heartbeat.beat and initially created by record_fleet_seat.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 186–192)

```
async def run(self) -> None
```

**Purpose**: Runs the repeating sweep that carries cancellation from a cancelled turn down to its still-live dependent descendants. This makes cancellation eventually cover the whole dependent turn tree.

**Data flow**: It reads the DBOS client and interval from the CancelReconciler object. Each cycle it sleeps, calls CancelReconciler.sweep, logs database or DBOS errors if the sweep fails, and continues running. It normally has no final output because it is a background loop.

**Call relations**: This is the timer-driven wrapper around CancelReconciler.sweep. It runs in each serve process so a cancellation left half-finished by a crash can still be completed by another process.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 194–201)

```
async def sweep(self) -> None
```

**Purpose**: Finds live turns that sit underneath a cancelled ancestor, then cancels each one. This prevents child or grandchild work from continuing after the work it depends on has been cancelled.

**Data flow**: It builds and runs the query from CancelReconciler._orphans_query to get affected turn ids and their workspaces. For each row, it enters that workspace context and calls cancel_one_turn with the DBOS client and turn id. If a turn was actually cancelled, it logs the reconciliation.

**Call relations**: CancelReconciler.run calls this periodically. It uses CancelReconciler._orphans_query to find the targets, then hands each target to the shared cancellation primitive, cancel_one_turn, rather than editing the turn directly.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `CancelReconciler._orphans_query`  (lines 203–243)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: Builds the database query that identifies non-terminal turns with a cancelled dependent ancestor. In plain terms, it asks, “which still-live turns are below something cancelled?”

**Data flow**: It starts from live, non-terminal turns and recursively walks upward through parent_turn_id links that count as dependent parent links. If the walk reaches a cancelled ancestor, the original turn is selected along with its workspace id. The output is a SQLAlchemy Select object, which is a database query description rather than the query results themselves.

**Call relations**: CancelReconciler.sweep calls this before reading the database. The query uses CancelReconciler._dependent_parent to decide when a parent link should be followed, because not every parent relationship means cancellation should spread.

*Call graph*: calls 1 internal fn (_dependent_parent); called by 1 (sweep); 1 external calls (select).


##### `CancelReconciler._dependent_parent`  (lines 245–255)

```
def _dependent_parent(self, turn: sa.Table | sa.FromClause) -> sa.ColumnElement
```

**Purpose**: Describes which parent link counts as a cancellation dependency. It lets cancellation climb through ordinary dependent turns, but not into independent spawned agents.

**Data flow**: It receives a turn table or table-like alias. It returns a SQL expression: use parent_turn_id when the turn has a subagent profile or came from an intent admission, otherwise treat the parent as null so the upward search stops there.

**Call relations**: CancelReconciler._orphans_query calls this while building its recursive parent walk. This small rule is what keeps the cancellation sweep from crossing boundaries into independent agent work.

*Call graph*: called by 1 (_orphans_query); 3 external calls (case, null, or_).


##### `StrandedTurnReconciler.run`  (lines 289–295)

```
async def run(self) -> None
```

**Purpose**: Runs the repeating sweep that fixes running turns whose workflow attempt can no longer move them forward. It prevents conversations from being blocked by turns that look active but are actually unreachable.

**Data flow**: It reads the DBOS client, interval, and grace settings from the StrandedTurnReconciler object. Each cycle it sleeps, calls StrandedTurnReconciler.sweep, logs database or DBOS errors if the sweep fails, and keeps going. It normally does not return.

**Call relations**: This is the timer for StrandedTurnReconciler.sweep. It runs alongside the heartbeat, executor recovery, and cancellation reconciliation loops as part of the serve process’s background maintenance.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `StrandedTurnReconciler.sweep`  (lines 297–313)

```
async def sweep(self) -> None
```

**Purpose**: Cancels running turns whose recorded workflow attempt is no longer pending, enqueued, or delayed in DBOS. This clears rows that would otherwise remain running forever.

**Data flow**: It queries old enough RUNNING turns using StrandedTurnReconciler._claimed_query. If the scan reaches its limit, it logs that fact. It then asks StrandedTurnReconciler._advancing_attempts which recorded workflow attempts are still carried by DBOS. For any claimed turn whose attempt is not advancing, it enters the turn’s workspace, calls cancel_one_turn, and logs if cancellation happened.

**Call relations**: StrandedTurnReconciler.run calls this on a timer. It uses _claimed_query to find possible stranded rows, _advancing_attempts to separate live workflow attempts from lost ones, and cancel_one_turn to safely terminalize only the turns that are still eligible.

*Call graph*: calls 2 internal fn (_advancing_attempts, _claimed_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `StrandedTurnReconciler._claimed_query`  (lines 315–332)

```
def _claimed_query(self) -> sa.Select
```

**Purpose**: Builds the database query for RUNNING turns that have held the same workflow claim longer than the grace period. These are possible stranded turns, not yet proven stranded.

**Data flow**: It calculates a cutoff time by subtracting the grace window from the current time. It returns a SQL query selecting turn id, workspace id, and running_attempt for turns that are RUNNING, have a running_attempt, were last updated before the cutoff, and are among the oldest rows up to the scan limit.

**Call relations**: StrandedTurnReconciler.sweep calls this before reading candidate rows from the database. The query deliberately includes only old RUNNING claims so a brand-new claim is not mistaken for abandoned work during a race.

*Call graph*: called by 1 (sweep); 3 external calls (now, timedelta, select).


##### `StrandedTurnReconciler._advancing_attempts`  (lines 334–345)

```
async def _advancing_attempts(self, attempts: list[str]) -> set[str]
```

**Purpose**: Asks DBOS which workflow attempts from a given list are still in a status that can advance. This is the final check before deciding that a running turn is stranded.

**Data flow**: It receives a list of workflow attempt ids. If the list is empty, it returns an empty set immediately, avoiding a broad DBOS query. Otherwise it asks the DBOS client for those workflow ids with advancing statuses only, and returns the workflow ids DBOS still reports as carried.

**Call relations**: StrandedTurnReconciler.sweep calls this after collecting candidate running turns. Its result tells the sweep which turns to skip because their workflow is still alive, and which turns can be handed to cancel_one_turn as stranded.

*Call graph*: called by 1 (sweep).

## 📊 State Registers Touched

- `reg-deployment-config` — The merged deployment settings that tell the system what product, services, addresses, databases, sandboxes, and safety defaults to use.
- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-database-schema-version` — The database migration state that records which durable tables and columns the running code can rely on.
- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-surface-routing` — The shared mapping from external surfaces such as Slack, iMessage, web, terminal, and hosted sites to the right workspace, agent, and conversation.
- `reg-live-update-hub` — The live activity stream that carries turn progress, tool status, cancellations, mid-turn replies, and final updates to connected viewers.
- `reg-runtime-fleet-liveness` — The heartbeat and listener-claim records that show which long-running service instances are alive and what work they currently own.
- `reg-workflow-claims` — The workflow attempt and run-claim state that prevents two workers from running the same turn, scheduled task, listener, or cleanup job at once.
- `reg-egress-network-policy` — The outbound network permission state that decides which external hosts, proxies, and secret injections are allowed for a workspace or agent.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-scheduled-jobs` — The durable background-job state for scheduled tasks, pauses, monitor checks, report writing, thumbnail repair, product metrics, and self-improvement runs.
- `reg-service-connection-pools` — Process-local shared connection/client pools for database, Redis/pubsub, HTTP/provider calls, and other long-lived service clients reused by requests, workers, tools, and jobs.
- `reg-background-runner-handles` — Process-local async task handles, wakeup queues, and scheduler loop state for live background workers distinct from their durable job records.
- `reg-http-route-registry` — Process-local mounted HTTP, WebSocket, callback, and extension route dispatch table used by the running web server.
- `reg-backend-provider-registry` — Process-local registry mapping provider names to active backend implementations for models, search, embeddings, memory, connectors, browser access, auth, billing, and feature services.
- `reg-durable-workflow-store` — Serialized durable workflow/checkpoint objects used to reload or resume long-running turns, jobs, and recovery work after crashes or code changes.
- `reg-sandbox-runtime-cache` — Built sandbox client/runtime image and reusable sandbox cache artifacts used when launching isolated execution environments.
