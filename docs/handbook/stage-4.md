# Serve process startup, app lifespan, and fleet coordination  `stage-4`

This stage is the server’s “come alive and stay healthy” step. It happens at startup, then keeps running behind the scenes until shutdown. The main entry point, core/src/ufo/serve.py, assembles the service process. It starts the web server, attaches extension routes and user-facing surfaces, connects the background job engine, sets up workspace boundaries, credentials, sandbox networking, storage, and live update channels. In simple terms, it plugs all the main parts together so requests, jobs, files, and real-time updates can move through the system safely.

core/src/ufo/runtime_instance.py makes each running server process known to the wider fleet, meaning the group of server processes working together. It writes heartbeat-style records so other parts of the system can tell the process is alive. It also runs cleanup loops that find work left behind by crashed or stopped processes, and it spreads cancellations from parent tasks to child tasks. During shutdown, these pieces help stop background server work cleanly instead of leaving loose ends.

## Files in this stage

### Serve Runtime Lifecycle
Starts the shared service process, initializes integrations and local runtime state, then registers the process with fleet-wide heartbeat and cleanup coordination.

### `core/src/ufo/serve.py`

`entrypoint` · `startup, request handling, background work, shutdown`

Think of this file as the control room for one running UFO fleet process. A single process serves many workspaces, so the central problem is keeping each request, job, credential lookup, and database read tied to the correct workspace. Without this file, the system would have pieces like storage, extensions, jobs, sandboxes, and web routes, but nothing would assemble them safely into one service.

At startup, `run` loads configuration, opens databases, loads extension manifests, checks credentials, creates the shared runtime, starts DBOS workers, registers background jobs, mounts API routes, and finally starts Uvicorn, the web server. It also starts a heartbeat so other processes know this one is alive.

A major safety theme is “fail loudly at boot.” If an extension asks for a browser provider, search provider, connector auth backend, or credential key that is missing, this file stops startup instead of letting the first user request fail later.

Another important theme is workspace boundaries. Shared surface routes identify the caller’s workspace from the request, bind that workspace for the whole response, and clear it afterward. This is like checking a visitor’s badge at the door and making sure the badge is removed before the next visitor enters.

The file also decides whether sandbox internet traffic uses a local in-process proxy or an external proxy, so sandboxed agents can get controlled, metered, credential-aware network access.

#### Function details

##### `_assert_no_reserved_routes`  (lines 110–126)

```
def _assert_no_reserved_routes(app: FastAPI) -> None
```

**Purpose**: Checks that this service has not mounted web routes under URL prefixes reserved for the separate onboarding gateway. This prevents a silent routing conflict where the front door sends those paths somewhere else.

**Data flow**: It receives the FastAPI app, reads its registered routes, and looks for paths beginning with reserved prefixes such as `/login`, `/v1/onboard`, or `/ufo`. If it finds any, it raises an error; otherwise startup continues unchanged.

**Call relations**: `run` calls this after all routes have been mounted, just before starting the web server. It acts as the final guardrail that proves the service and gateway will not fight over the same paths.

*Call graph*: called by 1 (run).


##### `run`  (lines 129–253)

```
def run() -> None
```

**Purpose**: Starts the shared UFO service process. It builds every major dependency, installs web routes and background workers, and then runs the HTTP server.

**Data flow**: It begins by reading configuration and environment variables. From those it creates database connections, credential storage, extension objects, blob storage, model and memory backends, connector routing, sandbox support, the runtime object, DBOS workflow execution, FastAPI routes, and scheduled jobs. Its visible output is a running web server; it also starts background threads and records this process as an active fleet seat.

**Call relations**: This is the top-level entry point for the file. It calls the many helper functions in this file to choose backends, validate extension requirements, mount routes, launch jobs, set up proxy access, and shut down cleanly when Uvicorn exits.

*Call graph*: calls 16 internal fn (from_env, _assert_no_reserved_routes, _connect_flow, _connector_registry, _launch_jobs, _mount_ext_routes, _mount_shared_surfaces, _proxy_endpoint, _select_carrier, _select_cdp_provider (+6 more)); 39 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, run, Fernet, DBOS (+15 more)).


##### `_stop_executor`  (lines 256–270)

```
def _stop_executor(dbos: DBOS, heartbeat: Heartbeat, graceful_shutdown_seconds: int) -> None
```

**Purpose**: Stops DBOS workflow execution safely during shutdown. It only retires this process’s fleet seat if no workflows are still active.

**Data flow**: It receives the DBOS executor object, heartbeat object, and a shutdown time limit. It asks DBOS to drain work, checks whether any workflows are still active, logs and leaves the seat alive if work remains, or retires the heartbeat seat if the executor is empty.

**Call relations**: `run` calls this in its `finally` block after the web server stops. It hands off to DBOS for draining and to `Heartbeat.retire` only when it is safe for another process to recover abandoned work.

*Call graph*: calls 1 internal fn (retire); called by 1 (run); 3 external calls (run, destroy, log).


##### `_shared_owner_dsn`  (lines 273–287)

```
def _shared_owner_dsn(config: Config) -> str
```

**Purpose**: Finds the special database connection string used for cross-workspace owner-level reads. This is needed for jobs that must enumerate workspaces before rebinding each operation to one workspace.

**Data flow**: It reads either the owner database URL environment variable or the configured owner URL. If neither exists, it raises an error. If it gets a plain PostgreSQL URL, it rewrites it to use the async database driver expected by the service.

**Call relations**: `run` uses this before initializing the owner database connection. It supports later background sweeps that need to see across workspaces without accidentally running under an unset workspace.

*Call graph*: called by 1 (run).


##### `_launch_jobs`  (lines 290–333)

```
def _launch_jobs(runtime: Runtime, sync_driver: SyncDriver, page_feed: CorePageFeed) -> None
```

**Purpose**: Registers and starts the service’s background jobs. These include source syncing, turn dispatching, sandbox cleanup, page-change indexing, and extension-provided jobs.

**Data flow**: It receives the runtime, sync driver, and page feed. It builds an admission helper, creates job runners for page changes and core jobs, combines those with extension job bindings, and launches the `JobRunner`, which registers schedules and queued work with DBOS.

**Call relations**: `run` calls this after DBOS has launched and after the runtime is available. Inside it, `invoker_for` creates workspace-specific admission invokers so jobs can admit work under the right workspace.

*Call graph*: called by 1 (run); 8 external calls (__init__, __init__, __init__, __init__, __init__, durable_surfaces, bindings_from, core_jobs).


##### `_launch_jobs.invoker_for`  (lines 304–305)

```
def invoker_for(workspace_id: UUID) -> AdmissionInvoker
```

**Purpose**: Creates a small object that can admit work for one specific workspace. It is used when a background job needs to enqueue or re-admit turns for that workspace.

**Data flow**: It receives a workspace ID, combines it with the shared `Admission` object, and returns an `AdmissionInvoker` tied to that workspace.

**Call relations**: This helper is passed as a factory into job-related objects created by `_launch_jobs`. Those job objects call it when they need a workspace-scoped way to submit work.

*Call graph*: 1 external calls (__init__).


##### `_select_carrier`  (lines 336–376)

```
def _select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> Carrier
```

**Purpose**: Chooses the sandbox backend that will run agent code. A carrier is the component that actually starts and controls sandboxes, such as a local runner or an extension-provided remote runner.

**Data flow**: It starts with the built-in local carrier, adds carrier factories declared by extensions, checks for duplicate names, and looks up the configured backend. For remote backends, it also verifies that a public HTTPS proxy URL is configured. It returns one constructed carrier instance or raises a clear startup error.

**Call relations**: `run` calls this while building the runtime. The selected carrier is later used by turns and cleanup jobs to create and reap sandboxes.

*Call graph*: called by 1 (run); 2 external calls (__init__, urlparse).


##### `_source_backends`  (lines 379–393)

```
def _source_backends(manifests: tuple[Manifest, ...]) -> dict[str, SourceBackend]
```

**Purpose**: Builds the list of source-sync backends available to the sync driver. A source backend knows how to read pages or files from a particular kind of source.

**Data flow**: It begins with the built-in folder source. For each extension source provider, it creates a credential reader limited to that extension’s declared credential slots, builds the backend, and stores it by backend name. Duplicate backend names cause startup to fail.

**Call relations**: `run` uses this to construct the `SyncDriver`. The sync driver later uses the returned map to decide how to sync each configured source.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `_select_hub`  (lines 396–414)

```
def _select_hub(config: Config, manifests: tuple[Manifest, ...]) -> Hub
```

**Purpose**: Chooses the live-update hub used to send frames or events inside the service. The built-in default is an in-process hub, but extensions can register other hub implementations.

**Data flow**: It builds a name-to-builder map from the built-in hub and extension hub specs. It checks for duplicate backend names, finds the configured backend, and calls its builder with the configured hub URL. It returns the constructed hub or raises an error if the chosen name is unknown.

**Call relations**: `run` calls this early when assembling shared runtime services. The resulting hub is used by live surfaces and tailing code to stream updates.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `_select_cdp_provider`  (lines 417–445)

```
def _select_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> CdpProvider | None
```

**Purpose**: Selects the browser automation provider, if one is installed and configured. CDP means Chrome DevTools Protocol, a way to control a browser programmatically.

**Data flow**: It collects browser provider specs from extensions, checks for duplicate backend names, and looks up the configured provider name. If none is registered under that name, it returns `None`. If the provider needs credentials but no credential store exists, it raises an error. Otherwise it builds and returns the provider with credential access limited to that extension’s declared slots.

**Call relations**: `run` calls this when building the runtime. `_require_cdp_provider` also calls it during requirement validation when an extension says browser support must be available.

*Call graph*: called by 2 (_require_cdp_provider, run); 1 external calls (__init__).


##### `_validate_requires`  (lines 448–472)

```
def _validate_requires(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Checks that every active extension’s declared required service is actually available. This catches missing browser, search, or memory support during startup instead of during a user action.

**Data flow**: It reads each manifest’s required seam names, finds the matching checker function, and runs it with the current configuration, manifests, and credentials. Unknown seam names or failed checks are wrapped in errors that name the extension and missing requirement.

**Call relations**: `run` calls this after loading manifests and credentials. It delegates the actual checks to functions such as `_require_cdp_provider`, `_require_search_provider`, and `_require_memory_search`.

*Call graph*: called by 1 (run).


##### `_require_cdp_provider`  (lines 475–489)

```
def _require_cdp_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a browser automation provider is present when an extension requires one. It turns an optional runtime feature into a mandatory startup check.

**Data flow**: It asks `_select_cdp_provider` to resolve the configured provider. If that returns `None`, it raises an error explaining that the required provider is not registered.

**Call relations**: _validate_requires calls this when a manifest lists the `cdp_providers` requirement. It reuses the normal provider-selection logic so validation matches runtime behavior.

*Call graph*: calls 1 internal fn (_select_cdp_provider).


##### `_select_search_provider`  (lines 492–527)

```
def _select_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> SearchProvider | None
```

**Purpose**: Chooses the web or research search backend for the process. Search is optional unless an extension declares that it requires it.

**Data flow**: It gathers search provider specs from extensions, checks for duplicate names, and examines the configured search provider. If the setting is absent, it returns `None`. If the configured name is unknown or credentials are missing, it raises an error. Otherwise it builds the provider with credential access for the declaring extension’s slots.

**Call relations**: `run` calls this while building the runtime. `_require_search_provider` calls it during startup validation when research tools need search to be available.

*Call graph*: called by 2 (_require_search_provider, run); 2 external calls (__init__, __init__).


##### `_require_search_provider`  (lines 530–543)

```
def _require_search_provider(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Enforces that a configured and usable search provider exists. This protects research extensions from failing only when the first search request arrives.

**Data flow**: It checks that the search provider setting is present, then calls `_select_search_provider` to verify the named backend can be built. It returns nothing if the requirement is satisfied, or raises a startup error if not.

**Call relations**: _validate_requires calls this for extensions that list the `search_providers` requirement. It delegates backend resolution to `_select_search_provider`.

*Call graph*: calls 1 internal fn (_select_search_provider).


##### `_require_memory_search`  (lines 546–572)

```
def _require_memory_search(_config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> None
```

**Purpose**: Verifies that exactly one default memory-search provider is available. Memory search is the feature that lets the system look up stored knowledge or indexed past content.

**Data flow**: It scans all manifests for providers with the default memory-search name. If none exist, more than one exists, or the chosen provider declares credentials without a credential store, it raises an error. Otherwise it simply confirms the requirement.

**Call relations**: _validate_requires uses this when an extension requires memory search. Unlike some other requirements, it performs the check directly instead of calling a separate selector in this file.


##### `_select_auth_proxy`  (lines 584–621)

```
def _select_auth_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> AuthProxy | None
```

**Purpose**: Chooses the fallback authentication proxy used by connector feed sync when a connector does not have its own broker. An auth proxy is a host-side service that supplies or exchanges credentials without exposing them directly to sandbox code.

**Data flow**: It collects auth proxy specs from extensions and checks for duplicate backend names. If no backend is explicitly configured, it accepts the only registered backend or fails if several choices exist. It verifies the selected backend exists and that credentials are available, then builds the proxy with access limited to the declaring extension’s credential slots.

**Call relations**: _connector_registry calls this while creating the connector registry. The returned proxy becomes the fallback path for connector credential resolution.

*Call graph*: called by 1 (_connector_registry); 2 external calls (__init__, __init__).


##### `_mount_ext_routes`  (lines 624–662)

```
def _mount_ext_routes(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, index: IndexBackend, embed: EmbedClient) -> None
```

**Purpose**: Adds HTTP routes provided by extensions under `/ext/<extension-name>/...`. Each route must first identify the workspace for the incoming request before the extension handler is allowed to run.

**Data flow**: It loops through manifests with routes, verifies credentials are available when needed, creates an extension context, and adds each route to the FastAPI app. The route wrapper rejects unidentified requests and runs identified ones inside the correct workspace binding.

**Call relations**: `run` calls this after jobs are launched and before shared surfaces are mounted. It creates the nested `endpoint` function used as the actual FastAPI handler for each extension route.

*Call graph*: called by 1 (run); 2 external calls (add_route, context_for).


##### `_mount_ext_routes.endpoint`  (lines 646–656)

```
async def endpoint(request: Request, handler=spec.handler, identify=spec.identify, extension_context=context) -> Response
```

**Purpose**: Acts as the safety wrapper around an extension HTTP route. It makes sure the request belongs to a workspace before calling extension code.

**Data flow**: It receives a web request, calls the route’s identify function, and returns a 401 response if identification fails. If identification succeeds, it temporarily binds that workspace and calls the extension’s handler with the prepared extension context and request. The handler’s response is returned to the client.

**Call relations**: This function is registered with FastAPI by `_mount_ext_routes`. It hands off to the extension’s own handler only after the workspace has been resolved.

*Call graph*: 2 external calls (Response, ws).


##### `WorkspaceScopeBoundary.__call__`  (lines 683–691)

```
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

**Purpose**: Middleware that clears the current workspace before and after every HTTP request. This prevents one request’s workspace from leaking into another request.

**Data flow**: It receives the ASGI request scope plus receive and send callables. For non-HTTP traffic, it simply passes through. For HTTP traffic, it sets the ambient workspace to `None`, runs the downstream app, and then sets it back to `None` in a final cleanup step even if an error occurs.

**Call relations**: _mount_shared_surfaces installs this as middleware. Shared surface endpoints set the workspace during a request, and this boundary guarantees that binding is cleaned up after the full response, including streamed responses.

*Call graph*: 1 external calls (set).


##### `_mount_shared_surfaces`  (lines 694–775)

```
def _mount_shared_surfaces(app: FastAPI, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, blob: BlobStore, hub: Hub, dbos_client: DBOSClient, artifact_secret: str, public_base_url
```

**Purpose**: Mounts shared surface routes, such as product-facing integrations, so one service can safely serve many workspaces. A surface is an external interface that can admit turns, stream updates, and sometimes post durable writebacks.

**Data flow**: It adds the workspace boundary middleware, builds shared admission and hub-tailing helpers, and then loops through surface specs from manifests. For each route, it creates authentication helpers, registers a FastAPI endpoint, and records durable surfaces that need writeback polling. If durable posting is available, it creates a shared `WritebackPoller` and stores it on the app.

**Call relations**: `run` calls this after extension routes are mounted. It creates both the nested `context_for` helper, which builds per-workspace surface contexts, and the nested `endpoint` wrapper, which authenticates each request.

*Call graph*: called by 1 (run); 10 external calls (__init__, __init__, __init__, __init__, add_middleware, add_route, durable_surfaces, writeback_workspaces, log, uuid4).


##### `_mount_shared_surfaces.context_for`  (lines 721–731)

```
def context_for(workspace_id: UUID, surface: str) -> SurfaceContext
```

**Purpose**: Builds the per-request context object that a surface handler uses. This context carries the workspace ID and the tools needed to admit work, access blobs, stream updates, read credentials, and create artifact links.

**Data flow**: It receives a workspace ID and surface name. It combines them with shared services such as blob storage, admission, hub tailing, credentials, artifact token secret, and public base URL, then returns a `SurfaceContext` tied to that workspace.

**Call relations**: The surface endpoint wrapper calls this after identifying the request’s workspace. The writeback poller also uses it so durable background delivery runs under the correct workspace.

*Call graph*: 2 external calls (__init__, __init__).


##### `_mount_shared_surfaces.endpoint`  (lines 747–761)

```
async def endpoint(request: Request, handler=route.handler, identify=resolver, surface=spec.name, surface_auth=auth) -> Response
```

**Purpose**: Wraps a shared surface route with authentication and workspace binding. It makes sure every surface request is tied to exactly one workspace before route code runs.

**Data flow**: It receives a request, calls the surface’s identify function with surface authentication, and handles three outcomes: a ready-made response, no identity, or a workspace ID. No identity becomes a 401 response. A workspace ID is stored as the current workspace, then the real surface route handler is called with a workspace-specific context.

**Call relations**: _mount_shared_surfaces registers this function with FastAPI for each surface route. It hands off to the surface’s handler only after identification and workspace binding.

*Call graph*: 3 external calls (Response, set, context_for).


##### `_serve_lifespan`  (lines 779–802)

```
async def _serve_lifespan(app: FastAPI) -> AsyncIterator[None]
```

**Purpose**: Runs app-level background tasks for as long as the web app is alive. These tasks recover work from dead executors, reconcile cancellations, and optionally deliver durable surface writebacks.

**Data flow**: When the FastAPI lifespan starts, it creates an async task group and starts executor recovery and cancellation reconciliation. If a writeback poller exists, it starts that too. When the app is shutting down, it cancels those tasks.

**Call relations**: `run` passes this as the FastAPI lifespan function. It is separate from the heartbeat thread because heartbeat liveness must begin earlier and survive event-loop stalls.

*Call graph*: 3 external calls (__init__, __init__, TaskGroup).


##### `_proxy_endpoint`  (lines 805–835)

```
def _proxy_endpoint(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, workspace_fs: SandboxFsCredentialMinter | None=No
```

**Purpose**: Chooses how sandboxes will reach the egress proxy, the controlled gateway for outbound internet traffic. It supports either a local in-process proxy or an external shared proxy.

**Data flow**: It reads sandbox proxy configuration. If no public proxy URL is set, it starts a local egress proxy and returns its endpoint. If a public proxy URL is set, it reads the shared proxy certificate from the environment and returns a `ProxyEndpoint` pointing at the external proxy. Missing certificate data causes startup to fail.

**Call relations**: `run` calls this while building the runtime. If local proxying is needed, it delegates to `_local_egress_proxy`.

*Call graph*: calls 1 internal fn (_local_egress_proxy); called by 1 (run); 1 external calls (__init__).


##### `_local_egress_proxy`  (lines 838–877)

```
def _local_egress_proxy(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None, pricing: Pricing, run_tokens: RunTokenCodec, workspace_fs: SandboxFsCredentialMinter | Non
```

**Purpose**: Starts an in-process egress proxy for single-node or local deployments. This proxy gives sandboxes a controlled way to access the network with policy checks, credentials, and metering.

**Data flow**: It builds a rule resolver from model rules, grants, credentials, extension-declared injection slots, internet rules, transfer hosts, and connector command-line tools. It creates a new event loop in a daemon thread, starts the proxy boot coroutine there, and waits for a `ProxyEndpoint` result within the startup timeout.

**Call relations**: _proxy_endpoint calls this when the deployment does not use an external proxy. Its nested `_boot` function performs the asynchronous certificate generation and proxy startup.

*Call graph*: called by 1 (_proxy_endpoint); 10 external calls (__init__, __init__, new_event_loop, run_coroutine_threadsafe, Thread, connector_clis, injecting_slots, model_rule_base, connector_transfer_hosts, derive_manifest_rules).


##### `_local_egress_proxy._boot`  (lines 865–875)

```
async def _boot() -> ProxyEndpoint
```

**Purpose**: Performs the asynchronous startup steps for the local egress proxy. It creates temporary certificate authority material and starts the proxy server.

**Data flow**: It generates a certificate authority certificate and key, builds an `EgressProxy` with rule resolution, live-turn authorization, run-token checking, pricing, and optional workspace filesystem credential refresh. It starts the proxy on the configured port and returns the resulting endpoint.

**Call relations**: _local_egress_proxy schedules this coroutine on the proxy’s dedicated event loop. It hands the finished endpoint back to `_local_egress_proxy`, which then returns it to `_proxy_endpoint`.

*Call graph*: 2 external calls (__init__, generate_ca).


##### `_connector_registry`  (lines 883–906)

```
def _connector_registry(config: Config, manifests: tuple[Manifest, ...], credentials: CredentialStore | None) -> ConnectorRegistry
```

**Purpose**: Builds the registry that knows about all installed connector providers. Connectors are integrations that may use OAuth or brokered authentication to access external services.

**Data flow**: It scans manifests for connector definitions, checks that no two extensions register the same provider name, and creates registry entries with labels and broker information. It also builds the connector namespace resolver and selects the fallback auth proxy. It returns a `ConnectorRegistry`.

**Call relations**: `run` calls this during runtime construction. It calls `_select_auth_proxy` for fallback credential resolution, and the resulting registry is used by connector tools and source sync.

*Call graph*: calls 1 internal fn (_select_auth_proxy); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_flow`  (lines 909–934)

```
def _connect_flow(credentials: CredentialStore | None, config: Config, manifests: tuple[Manifest, ...]) -> ConnectFlow | None
```

**Purpose**: Creates the OAuth connect flow used to start and finish member authorization for connectors. OAuth is the common browser-based permission handoff used by many third-party services.

**Data flow**: If no credential store exists, it returns `None` because grants cannot be securely stored. Otherwise it collects OAuth providers from connector manifests, checks for duplicate provider names, computes the callback URL, and returns a `ConnectFlow` with encryption, grant storage, provider data, and namespace resolution.

**Call relations**: `run` installs the returned connect flow globally. This flow is then used by tools, surfaces, and the OAuth callback route; it calls `_connect_redirect_uri` to validate and build the external callback address.

*Call graph*: calls 1 internal fn (_connect_redirect_uri); called by 1 (run); 3 external calls (__init__, __init__, open_connector_namespace).


##### `_connect_redirect_uri`  (lines 937–961)

```
def _connect_redirect_uri(config: Config, providers: Mapping[str, OAuthProvider]) -> str
```

**Purpose**: Builds and validates the public OAuth callback URL for connector authorization. This URL must be reachable by third-party providers after a user approves access.

**Data flow**: It reads `connect.public_base_url` from configuration and the set of registered OAuth providers. If no providers exist, it returns an empty or best-effort callback URL. If providers exist, it requires a base URL with an HTTP or HTTPS scheme and a real hostname, rejects bind-only addresses like `127.0.0.1`, and returns the base URL plus the callback path.

**Call relations**: _connect_flow calls this while constructing the OAuth flow. Its validation prevents a connector from registering with a callback address that an outside provider cannot reach.

*Call graph*: called by 1 (_connect_flow); 1 external calls (urlparse).


### `core/src/ufo/runtime_instance.py`

`orchestration` · `main loop`

Think of each serve process as a worker in a shared workshop. This file makes every worker regularly sign a time sheet, so the others know it is still alive. That sign-in is stored in the `runtime_instance` table as a heartbeat: a timestamp that is refreshed every few seconds.

That heartbeat matters because DBOS, the durable workflow system used here, records which executor started a queued workflow. If a workflow is still marked pending under an executor whose heartbeat has gone stale, the original process probably died. The `ExecutorRecovery` loop finds those stranded workflows and asks DBOS to recover them, so another live process can continue the work instead of leaving it frozen.

The file also has a `CancelReconciler`. Cancelling one turn is a local action, but turns can spawn child turns and grandchildren. This reconciler periodically searches for still-running turns that sit underneath a cancelled ancestor, then cancels them too. This makes cancellation eventually flow down the whole tree, even after crashes or restarts.

All three background jobs are deliberately tolerant of temporary database or DBOS failures. They log the failed tick and keep going, because one bad moment should not kill the long-running safety loops.

#### Function details

##### `record_fleet_seat`  (lines 38–53)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: This records that a serve process exists before it starts doing durable DBOS work. It creates the process's row in the shared `runtime_instance` table, which other processes later use as proof that this executor is alive.

**Data flow**: It receives an `instance_id`, opens a database transaction, and inserts a new runtime-instance row with no workspace attached and the current time as its heartbeat. After the row is written, it logs that the fleet seat was recorded. The outside world now has a fresh liveness marker for this process.

**Call relations**: This is the first part of the liveness story. Later, `Heartbeat.beat` keeps the row fresh, `Heartbeat.retire` removes it on graceful shutdown, and `ExecutorRecovery._live_executors` reads it to decide which executors must not be recovered.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 66–76)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending loop that keeps one process's heartbeat fresh. It protects a healthy process from being mistaken for a dead one.

**Data flow**: It repeatedly calls `Heartbeat.beat` to update the database timestamp for this instance. If a database error happens on one tick, it logs the failure instead of stopping. Then it waits for the heartbeat interval and tries again.

**Call relations**: This loop drives `Heartbeat.beat` over and over. Its updates are later read by `ExecutorRecovery._live_executors`, which uses them to avoid recovering work that is still being run by a live process.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 78–88)

```
async def beat(self) -> None
```

**Purpose**: This writes one fresh heartbeat timestamp for the current process. It is the small database update that says, in effect, “I am still here.”

**Data flow**: It reads the instance id from the `Heartbeat` object, opens a database transaction, and updates that row's `heartbeat_at` and `updated_at` fields to the current database time. It returns nothing, but the stored liveness timestamp becomes fresh.

**Call relations**: `Heartbeat.run` calls this on every heartbeat tick. The timestamp it writes is later compared against a cutoff by `ExecutorRecovery._live_executors` to decide whether this executor is alive.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 90–96)

```
async def retire(self) -> None
```

**Purpose**: This removes the process's liveness row when the process shuts down cleanly. That lets peers see immediately that the seat is gone, rather than waiting for the heartbeat to become stale.

**Data flow**: It reads the instance id from the `Heartbeat` object, opens a database transaction, and deletes the matching row from `runtime_instance`. It returns nothing, but the shared table no longer claims this process is alive.

**Call relations**: The serve shutdown path calls this through `ufo.serve._stop_executor`. It is the graceful counterpart to the recovery sweep: if a process exits normally, it clears its own marker instead of making others infer death from an old timestamp.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 116–122)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending background loop that looks for work abandoned by dead processes. It keeps queued workflows from staying stuck under an executor that will never come back.

**Data flow**: It waits for the recovery interval, calls `ExecutorRecovery.sweep`, and catches database or DBOS errors. On failure it logs the error class and continues looping, so temporary trouble delays recovery but does not permanently disable it.

**Call relations**: This loop is the scheduler for `ExecutorRecovery.sweep`. Every serve process can run it, so any surviving process can notice and recover work left behind by a crashed peer.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 124–132)

```
async def sweep(self) -> None
```

**Purpose**: This performs one recovery pass. It compares executors that still have pending workflows with executors that still have fresh heartbeats, and recovers the ones that look dead.

**Data flow**: It asks `_pending_executors` for executor ids attached to pending workflows. It asks `_live_executors` for executor ids with recent heartbeat rows. It subtracts the live set from the pending set, then for each remaining stranded executor it calls DBOS recovery in a worker thread and logs how many workflows were recovered.

**Call relations**: `ExecutorRecovery.run` calls this on each interval. It delegates the two facts it needs to `_pending_executors` and `_live_executors`, then hands each dead executor id to DBOS so DBOS can re-dispatch its pending workflows safely.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 134–146)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: This finds executor ids that currently have pending DBOS workflows. These are candidates for recovery, but only if their executor is no longer alive.

**Data flow**: It asks DBOS for pending workflows, up to a fixed scan limit, without loading the full workflow inputs or outputs. If the result reaches the limit, it logs that the scan may have hit the cap. It returns a set of executor id strings found on those pending workflow records.

**Call relations**: `ExecutorRecovery.sweep` calls this first to find where pending work is sitting. The sweep then compares the result with `_live_executors`, because pending work alone is not a problem if its executor is still alive.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 148–158)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: This finds executor ids that have recently heartbeated and should be treated as alive. It prevents the recovery loop from starting a second copy of work that a live process is already running.

**Data flow**: It calculates a cutoff time by subtracting the stale window from the current time. Then it reads `runtime_instance` rows whose heartbeat is newer than that cutoff. It returns those row ids as strings, one per live executor.

**Call relations**: `ExecutorRecovery.sweep` uses this as the safety check before recovery. Its answer comes from rows created by `record_fleet_seat` and refreshed by `Heartbeat.beat`.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 182–188)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending loop that makes cancellation flow down from cancelled parent turns to still-running descendants. It turns cancellation from a one-turn action into an eventually consistent tree-wide cleanup.

**Data flow**: It waits for the reconciliation interval, calls `CancelReconciler.sweep`, and catches database or DBOS errors. If one pass fails, it logs the error and keeps looping so the next tick can try again.

**Call relations**: This loop drives `CancelReconciler.sweep` periodically. Every serve process can run it, so a surviving process can finish cancellation cleanup even if the process that started the cancellation crashes.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 190–197)

```
async def sweep(self) -> None
```

**Purpose**: This performs one cancellation cleanup pass. It finds live turns that have a cancelled ancestor and cancels each of them through the normal cancellation path.

**Data flow**: It builds and runs `_orphans_query` inside a database transaction to get descendant turns that should no longer be running. For each result, it enters that turn's workspace context, calls `cancel_one_turn` with the DBOS client and turn id, and logs the turn if it was actually cancelled.

**Call relations**: `CancelReconciler.run` calls this on each interval. It relies on `_orphans_query` to find the right turns, then hands each turn to `cancel_one_turn`, which performs the actual workflow cancellation and terminal status update.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (cancel_one_turn, owner_tx, log, ws).


##### `CancelReconciler._orphans_query`  (lines 199–234)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: This builds the database query that finds non-finished turns living under a cancelled ancestor. It is how the reconciler catches not just direct children, but grandchildren and deeper descendants too.

**Data flow**: It starts from every turn whose status is still non-terminal, meaning not finished yet. Using a recursive database query, it walks upward through each turn's `parent_turn_id` chain until it either finds a cancelled ancestor or runs out of parents. It returns a selectable query that yields the live descendant turn id and workspace id for each match.

**Call relations**: `CancelReconciler.sweep` calls this to know which turns need cancellation. The query only identifies the targets; the sweep then uses `cancel_one_turn` to do the actual cancellation work in the correct workspace.

*Call graph*: called by 1 (sweep); 1 external calls (select).

## 📊 State Registers Touched

- `reg-config` — The effective deployment settings that tell the system how to start, what services to use, and what safety rules are enabled.
- `reg-extension-set` — The saved and loaded set of extensions, packs, manifests, and contributed capabilities available to the runtime.
- `reg-workspace-directory` — The shared record of workspaces, members, owners, agents, and workspace boundaries.
- `reg-surface-installations` — The stored links between outside surfaces, workspaces, channels, conversations, and agents.
- `reg-live-stream` — The live feed of turn updates, text chunks, tool events, costs, and final frames that clients and debuggers can watch.
- `reg-runtime-fleet` — The shared heartbeat and ownership records that show which server processes are alive and which work they are responsible for.
- `reg-cancellation-state` — The shared stop signal state used to cancel active turns, child tasks, tools, and abandoned work safely.
- `reg-egress-policy` — The network access and proxy state that decides which sandbox traffic is allowed, audited, billed, or given injected secrets.
- `reg-subagent-tree` — The shared parent-child work structure for delegated agents, including child turns, messages, waits, and cancellations.
- `reg-observability-context` — The shared tracing, metrics, structured logs, and trace-parent links used to understand work across processes and turns.
- `reg-db-schema-version` — The applied core, control-plane, and extension migration revisions that determine which persisted schema the runtime may safely use.
- `reg-db-connection-pool` — The process-global database engine, sessions, transactions, and connection pool shared by requests, workers, jobs, and shutdown cleanup.
- `reg-redis-backplane-pool` — The optional Redis client/backplane connection state used to share live stream infrastructure across server processes.
- `reg-update-check-cache` — Cached version/update-check metadata used by CLI or startup paths to avoid repeated remote checks and notify operators about newer releases.
