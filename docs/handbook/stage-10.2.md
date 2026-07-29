# Sandbox carriers, proxying, browser, and website automation  `stage-10.2`

This stage is the system’s “workshop and internet front door” for web-based tasks. It is mostly shared support used during the main work loop, after a conversation needs a safe place to run code, browse sites, or preview a web project.

The sandbox carriers provide that safe place. Docker runs a conversation’s workspace in a local container, while E2B can run it in a remote cloud sandbox. The sandbox build script keeps both environments made from the same recipe, so tools behave the same in either place. The proxy files act like a guarded doorway to the internet: sandbox traffic goes through them, allowed hosts are checked, credentials are added only when permitted, and usage is recorded. The proxy gate script tests that this doorway is reachable and trusted before deployment.

On top of that safe base, the browser stages provide Chrome sessions, connect to Chrome’s control channel, understand pages, and perform actions like clicking, typing, downloading, and uploading. The website tools build, preview, and publish projects, using the same sandbox and browser machinery to check that pages are actually live.

## Sub-stages

- [Browser providers and sandbox-hosted website automation adapters](stage-10.2.1.md) `stage-10.2.1` — 6 files
- [Chrome DevTools browser session and protocol plumbing](stage-10.2.2.md) `stage-10.2.2` — 8 files
- [Browser page understanding and action execution tools](stage-10.2.3.md) `stage-10.2.3` — 11 files

## Files in this stage

### Sandbox network proxy
Shared proxy entrypoint and server logic enforce outbound access, credential injection, TLS handling, auditing, and pricing for sandbox traffic.

### `core/src/ufo/proxy_serve.py`

`entrypoint` · `startup through shutdown`

A sandbox often needs to call outside services, such as model providers, artifact storage, or connector APIs. This file is the startup and wiring point for the shared egress proxy, which is the service that controls those outbound calls. Without it, sandboxes would either have no approved way to reach the internet, or they might reach services without the right workspace scoping and secret injection.

The proxy is shared across workspaces, so it cannot rely on a single workspace-specific database connection. Instead, it opens an owner database connection that can see all workspaces, then scopes each lookup using the workspace ID carried inside the run token. Think of the run token like a badge: every request shows its badge, and the proxy uses that badge to find only the matching workspace's rules and secrets.

At startup, the file loads configuration and extension manifests, reads a stable certificate authority from the environment, checks for model-provider API keys, opens the credential store if connector secrets are needed, and builds a pricing table for metering model use. The `ProxyServe` class then starts the actual proxy server. During each run, the proxy combines shared rules, pack-defined rules, artifact-store rules, granted permissions, and per-workspace credentials. It also shuts down cleanly when the process receives a stop signal.

#### Function details

##### `model_rule_base`  (lines 43–62)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the basic outbound-access rules for model providers such as Anthropic or OpenAI. It only enables providers whose API key environment variable is actually set, and it fails early if no model provider is usable.

**Data flow**: It takes the loaded configuration, reads the configured API key environment variable names, and checks the process environment for real keys. For each key it finds, it asks the proxy rule code to create rules that allow the right provider hosts and replace placeholder secrets with the real key on the wire. It returns a set of rules; if no provider key is found, it raises an error instead of starting a proxy that could not reach any model.

**Call relations**: When `ProxyServe.serve` is building the per-agent rule resolver, it calls `model_rule_base` to add the shared model-provider routes. `model_rule_base` delegates the provider-specific rule details to `derive_model_rules`, then wraps all allowed model hosts into a `ScopeRule` so the proxy knows which destinations are allowed.

*Call graph*: called by 1 (serve); 2 external calls (__init__, derive_model_rules).


##### `run`  (lines 65–86)

```
def run() -> None
```

**Purpose**: Starts the standalone shared proxy process. This is the top-level boot function that gathers configuration, secrets, manifests, telemetry settings, and pricing before handing control to the async server.

**Data flow**: It begins with no input from callers and reads configuration plus several environment variables. It loads pack manifests, gets the shared egress certificate authority, chooses the owner database address, opens the credential store when needed, builds the model pricing table, and creates a `ProxyServe` object. It then starts the async event loop and runs the proxy until it is shut down.

**Call relations**: `run` is the outer startup story for this file. It calls `_egress_ca`, `_owner_dsn`, and `_credential_store` to validate required secrets before the server starts, uses loader and registry helpers to prepare manifests and pricing, constructs `ProxyServe`, and finally hands execution to `ProxyServe.serve` through `asyncio.run`.

*Call graph*: calls 3 internal fn (_credential_store, _egress_ca, _owner_dsn); 9 external calls (__init__, Event, run, load_config, injecting_slots, load_manifests, model_registry, init_o11y, log).


##### `_egress_ca`  (lines 89–100)

```
def _egress_ca() -> tuple[str, str]
```

**Purpose**: Reads the shared certificate authority used by the proxy to sign temporary certificates for sandbox connections. It makes sure the proxy uses the same trusted signing material across restarts and workspaces.

**Data flow**: It reads the certificate and private key from the environment variables named by `EGRESS_CA_CERT_ENV` and `EGRESS_CA_KEY_ENV`. If both are present, it returns them as text. If either is missing, it raises an error because sandboxes would not trust certificates signed by a newly invented one-off authority.

**Call relations**: `run` calls `_egress_ca` during startup before creating `ProxyServe`. The returned certificate and key are later passed into `EgressProxy` inside `ProxyServe.serve`, where they are used for the proxy's encrypted connection interception and signing.

*Call graph*: called by 1 (run).


##### `_owner_dsn`  (lines 103–116)

```
def _owner_dsn(config: Config) -> str
```

**Purpose**: Chooses the database connection string for the shared proxy's owner-level database access. This lets one proxy process look up data for many workspaces while still filtering each query by the workspace from the run token.

**Data flow**: It reads the owner database URL first from the `UFO_OWNER_DSN` environment variable, then from the configuration if the environment does not provide one. If neither exists, it raises an error. Before returning the URL, it rewrites a plain PostgreSQL URL prefix to the async driver form expected by this service.

**Call relations**: `run` calls `_owner_dsn` while assembling the `ProxyServe` object. Later, `ProxyServe.serve` passes this value to `init_db`, so the proxy can use the database while resolving per-workspace rules and credentials.

*Call graph*: called by 1 (run).


##### `_credential_store`  (lines 119–133)

```
def _credential_store(config: Config, slots: tuple[CredentialSlot, ...]) -> CredentialStore | None
```

**Purpose**: Opens the encrypted credential store when the active pack needs provider secrets injected into outgoing requests. It also protects operators from starting a proxy that cannot decrypt required workspace secrets.

**Data flow**: It takes the loaded configuration and the credential slots declared by the active manifests. It reads the credential encryption key from the environment variable named in configuration. If the key exists, it builds a Fernet encryption helper and wraps it in a `CredentialStore`. If no key exists but injecting slots are required, it raises an error. If no secrets are needed, it returns nothing.

**Call relations**: `run` calls `_credential_store` after loading manifests and finding their injecting slots. The resulting store, if any, is passed into `ProxyServe`, and `ProxyServe.serve` gives it to `PerAgentRules` so per-workspace secrets can be decrypted and inserted into outbound connector calls.

*Call graph*: called by 1 (run); 2 external calls (__init__, Fernet).


##### `ProxyServe.serve`  (lines 151–181)

```
async def serve(self) -> None
```

**Purpose**: Runs the proxy server itself. It wires together database access, storage rules, model rules, manifest rules, run-token decoding, certificate signing, pricing, and graceful shutdown.

**Data flow**: It starts by registering operating-system signal handlers so Ctrl+C or a termination signal can request shutdown. It initializes the database using the owner connection string, derives rules for artifact storage, and builds a `PerAgentRules` resolver that can decide what each sandbox run is allowed to reach. It then creates an `EgressProxy`, starts it on the configured port and public URL, waits until shutdown is requested, and finally stops the proxy with the configured grace period.

**Call relations**: `run` creates a `ProxyServe` instance and then invokes this method through the async event loop. Inside, `ProxyServe.serve` calls `model_rule_base` for shared model-provider access, uses helpers such as `blob_store_for`, `derive_artifact_store_rules`, `derive_manifest_rules`, and `connector_transfer_hosts` to build the rest of the access picture, and hands the final resolver into `EgressProxy` so live sandbox traffic can be authorized and shaped.

*Call graph*: calls 2 internal fn (model_rule_base, from_env); 12 external calls (__init__, __init__, __init__, get_running_loop, blob_store_for, init_db, connector_clis, injecting_slots, log, connector_transfer_hosts (+2 more)).


### `core/src/ufo/sandbox/proxy/server.py`

`io_transport` · `startup, request handling, shutdown`

A sandboxed agent is not allowed to talk directly to the internet. Instead, its web traffic goes through this proxy, like a guarded reception desk for outgoing calls. The proxy reads a signed run token from the proxy authorization header, checks that the turn is still running, and builds rules for that exact workspace, agent, credentials, grants, and internet policy. If the token is missing, invalid, expired, or points to an ended turn, the request is refused.

For a plain allowed host, the proxy simply opens a tunnel and cannot see the encrypted traffic inside. For hosts that need a secret, it temporarily acts as the HTTPS server seen by the sandbox using a locally trusted certificate, then either swaps a harmless sentinel value for the real credential or sends the request through a broker that owns the credential. This means raw secrets do not live inside the sandbox.

The file also protects the system from overload and unsafe destinations: it caps headers, body sizes, total connections, and per-workspace connections; it blocks private or IPv6 internet targets; and it pins public DNS results before connecting. Finally, it emits metrics and writes ledger rows for egress requests, and it can parse model API responses to count token usage separately from ordinary host traffic.

#### Function details

##### `_ContentDecoder.unconsumed_tail`  (lines 138–138)

```
def unconsumed_tail(self) -> bytes
```

**Purpose**: This protocol property names the bytes a decompressor has not processed yet. It lets the token parser work with any gzip or deflate-like decoder that exposes this behavior.

**Data flow**: A decoder object already has some compressed input inside it → this property is read to find leftover bytes → the caller can continue decoding from the right place.

**Call relations**: The file uses this protocol as a promise for objects used by HttpTokenUsage. HttpTokenUsage._decode relies on this shape when it processes compressed model responses.


##### `_ContentDecoder.decompress`  (lines 140–140)

```
def decompress(self, data: bytes, max_length: int=0) -> bytes
```

**Purpose**: This protocol method describes how compressed bytes become plain bytes. It also supports a maximum output size so the proxy does not expand an untrusted response without limit.

**Data flow**: Compressed bytes and an optional size limit go in → the decoder expands as much as it safely can → plain response bytes come out, with any remainder kept by the decoder.

**Call relations**: HttpTokenUsage._decode calls this through the protocol when model responses are gzip or deflate encoded. The protocol keeps the parser independent from the exact decoder class.


##### `_ContentDecoder.flush`  (lines 142–142)

```
def flush(self) -> bytes
```

**Purpose**: This protocol method finishes a compressed stream and returns any final decoded bytes. It is needed when the response ends but the decompressor still holds buffered data.

**Data flow**: A decoder near the end of a stream goes in → it releases its remaining decoded content → final plain bytes come out.

**Call relations**: HttpTokenUsage._finish_decoder calls this before calculating usage, so token data at the end of a compressed response is not missed.


##### `generate_ca`  (lines 148–171)

```
async def generate_ca() -> tuple[str, str]
```

**Purpose**: This creates the temporary certificate authority used by the proxy to make trusted per-host certificates. Without it, the proxy could not safely inspect HTTPS requests that need credential injection.

**Data flow**: No input is needed → it creates a temporary folder, asks openssl to make a self-signed certificate and key, then reads them → it returns the certificate text and private key text.

**Call relations**: Startup code calls this before creating an EgressProxy. It delegates the command execution to _openssl and later EgressProxy.start writes the returned materials into its working directory.

*Call graph*: calls 1 internal fn (_openssl); 2 external calls (Path, TemporaryDirectory).


##### `_openssl`  (lines 174–180)

```
async def _openssl(*argv: str) -> None
```

**Purpose**: This is the small helper that runs the openssl command-line tool and turns failures into Python errors. It keeps certificate creation in one place.

**Data flow**: OpenSSL command arguments go in → a subprocess runs with standard output hidden and error output captured → it returns nothing on success or raises an error with the command's message on failure.

**Call relations**: generate_ca uses it for the root certificate, EgressProxy.start uses it for the shared leaf key, and EgressProxy._leaf_context uses it when minting a certificate for a specific host.

*Call graph*: called by 3 (_leaf_context, start, generate_ca); 1 external calls (create_subprocess_exec).


##### `PerAgentRules.resolve`  (lines 206–230)

```
async def resolve(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: This builds the set of hosts and credential behaviors allowed for one run token. It is the policy step that makes sure one agent only sees its own workspace rules and grants.

**Data flow**: A run token, or no token, goes in → it looks up the turn, enters the right workspace and agent context, adds base rules, optional internet rules, credential rules, grant rules, and CLI rules → it returns the complete rule tuple for that request.

**Call relations**: EgressProxy asks this through its resolver callback when it needs rules. It calls PerAgentRules._turn_of to learn the agent and internet setting, then hands off to rule-derivation helpers for credentials, grants, and CLIs.

*Call graph*: calls 1 internal fn (_turn_of); 5 external calls (agent, derive_cli_rules, derive_credential_rules, derive_grant_rules, ws).


##### `PerAgentRules._turn_of`  (lines 232–256)

```
async def _turn_of(self, run: RunToken) -> tuple[UUID, bool] | None
```

**Purpose**: This finds which agent owns a turn and whether that agent was allowed internet access. It avoids guessing policy from the token alone.

**Data flow**: A run token goes in → it queries the workspace database for the matching turn and agent row → it returns the agent id and internet flag, or nothing if the turn is unknown.

**Call relations**: PerAgentRules.resolve calls this before deriving per-agent rules. If it returns nothing, resolution falls back to the base rule set only.

*Call graph*: called by 1 (resolve); 2 external calls (select, workspace_tx).


##### `PerAgentRules.turn_live`  (lines 258–275)

```
async def turn_live(self, run: RunToken) -> bool
```

**Purpose**: This checks whether a run token still names a turn marked as running. It is the last-minute safety gate before any outgoing connection can use policy or secrets.

**Data flow**: A run token goes in → the workspace database is queried for that turn's current status → the function returns true only if the status is RUNNING.

**Call relations**: An EgressProxy is commonly wired to use this as its authorizer. EgressProxy._handle calls its authorization callback before allowing a CONNECT request to continue.

*Call graph*: 3 external calls (select, workspace_tx, ws).


##### `EgressProxy.start`  (lines 303–319)

```
async def start(self, bind_host: str=PROXY_BIND_HOST, port: int=0, public_url: str | None=None) -> ProxyEndpoint
```

**Purpose**: This starts the proxy listener and prepares the certificate files it needs. It returns the connection details that sandboxes will be told to use.

**Data flow**: A bind host, optional port, and optional public URL go in → it creates a work directory, writes the CA certificate and key, creates a leaf private key, and starts an asyncio TCP server → it returns a ProxyEndpoint containing the bound port, CA certificate, and public URL.

**Call relations**: This is called during proxy startup. It uses _openssl for key generation and arranges for incoming connections to be sent to EgressProxy._handle.

*Call graph*: calls 1 internal fn (_openssl); 4 external calls (__init__, start_server, Path, TemporaryDirectory).


##### `EgressProxy.stop`  (lines 321–358)

```
async def stop(self, graceful_shutdown_seconds: int=0) -> None
```

**Purpose**: This shuts the proxy down in a bounded way. It stops accepting new connections, gives existing work a chance to finish, cancels what remains, drains metering, and removes temporary certificate files.

**Data flow**: A grace period in seconds goes in → the listener is closed, tracked connection and rule-resolution tasks are waited on or cancelled, the metering worker is stopped, and the temp directory is cleaned → no value is returned, but the proxy is no longer serving.

**Call relations**: Shutdown code calls this when the proxy process or service is stopping. It coordinates with the connection task set, rule task map, and meter queue that are filled during normal request handling.

*Call graph*: 3 external calls (gather, wait, monotonic).


##### `EgressProxy._handle`  (lines 360–466)

```
async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None
```

**Purpose**: This is the main per-connection decision maker. It accepts only HTTP CONNECT requests, authenticates the run token, applies rules, and chooses whether to tunnel bytes or inspect HTTPS for credential work.

**Data flow**: A client stream reader and writer go in → it reads the CONNECT request, validates method, host, port, token, turn liveness, connection limits, and rules → it either writes an HTTP refusal, opens an opaque tunnel, or starts the inspected MITM path, while updating connection counters.

**Call relations**: asyncio.start_server calls this for each accepted socket. It calls _read_request_head, _run_token, _turn_authorized, _rules_for, _resolve_public_address when needed, then hands off to EgressProxy._tunnel or EgressProxy._mitm.

*Call graph*: calls 7 internal fn (_mitm, _rules_for, _run_token, _tunnel, _turn_authorized, _read_request_head, _respond); 3 external calls (__init__, close, current_task).


##### `EgressProxy._turn_authorized`  (lines 468–481)

```
async def _turn_authorized(self, run: RunToken) -> bool
```

**Purpose**: This wraps the turn-liveness check so failures are logged once and then treated as unavailable authorization. It keeps database or authorization errors from looking like allowed traffic.

**Data flow**: A run token goes in → the configured authorizer is called → it returns the authorizer's true or false answer, or logs and re-raises if the authorizer fails.

**Call relations**: EgressProxy._handle calls this before resolving rules. If it raises, _handle sends a service-unavailable response instead of continuing.

*Call graph*: called by 1 (_handle); 1 external calls (log_error).


##### `EgressProxy._rules_for`  (lines 483–500)

```
async def _rules_for(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: This returns the policy rules for a run, using a short cache and sharing one in-flight lookup among simultaneous connections. It reduces repeated work without keeping rules forever.

**Data flow**: A run token, or none, goes in → it checks the cache, starts or reuses a rule-resolution task if needed, and shields that task from caller cancellation → it returns the resolved tuple of rules.

**Call relations**: EgressProxy._handle calls this after authorization. On a cache miss it starts EgressProxy._resolve_rules and attaches _read_fault so abandoned errors do not leak through asyncio logging.

*Call graph*: calls 1 internal fn (_resolve_rules); called by 1 (_handle); 3 external calls (create_task, shield, monotonic).


##### `EgressProxy._resolve_rules`  (lines 502–523)

```
async def _resolve_rules(self, run: RunToken) -> tuple[Rule, ...]
```

**Purpose**: This performs the actual rule resolution and stores the result in the short-lived cache. It logs resolution failures at the point where they happen.

**Data flow**: A run token goes in → the configured resolver is called, errors are logged, successful results are cached with an expiry time, and old cache entries may be evicted → the rule tuple comes out.

**Call relations**: EgressProxy._rules_for creates this as a shared task. EgressProxy._handle receives its result indirectly and uses those rules to decide whether and how to connect.

*Call graph*: called by 1 (_rules_for); 4 external calls (__init__, current_task, monotonic, log_error).


##### `EgressProxy._run_token`  (lines 525–531)

```
def _run_token(self, proxy_auth: str) -> RunToken | None
```

**Purpose**: This extracts and verifies the run token from the Proxy-Authorization header text. It turns missing or invalid authorization into a simple absence.

**Data flow**: A header value goes in → the configured RunTokenCodec tries to decode it → a RunToken comes out on success, or None comes out for missing or invalid text.

**Call relations**: EgressProxy._handle calls this early in CONNECT processing. A None result causes _handle to refuse egress before any host connection is attempted.

*Call graph*: called by 1 (_handle).


##### `EgressProxy._resolve_public_address`  (lines 533–565)

```
async def _resolve_public_address(self, host: str, port: int) -> str
```

**Purpose**: This resolves an internet hostname to a safe public IPv4 address. It blocks private, multicast, IPv6, malformed, and unresolvable targets so broad internet access cannot reach internal networks.

**Data flow**: A host and port go in → if the host is already an IPv4 address it is checked, otherwise DNS A records are looked up; all addresses must be globally routable → the first public IPv4 address string comes out, or an error refuses or reports failure.

**Call relations**: EgressProxy._handle uses this when a host is allowed by an InternetRule rather than an exact scope rule. Tests or deployments can replace it through resolve_public.

*Call graph*: 1 external calls (IPv4Address).


##### `EgressProxy._tunnel`  (lines 567–594)

```
async def _tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, run: RunToken, rules: tuple[Rule, ...], connect_host: str) -> None
```

**Purpose**: This opens an opaque byte tunnel to an allowed host when no credential injection or broker forwarding is needed. The proxy cannot see the HTTPS requests inside; it only relays bytes.

**Data flow**: Client streams, host, port, run token, rules, and resolved connect host go in → it connects upstream, replies with CONNECT success, records metering if applicable, and copies bytes both ways → no value is returned; the connection ends when one side closes or stalls.

**Call relations**: EgressProxy._handle calls this for admitted hosts without InjectionRule or ForwardRule. It uses _relay for byte copying and calls _meter and _meter_ledger once the tunnel is established.

*Call graph*: calls 4 internal fn (_meter, _meter_ledger, _relay, _respond); called by 1 (_handle); 4 external calls (drain, write, open_connection, wait_for).


##### `EgressProxy._mitm`  (lines 596–656)

```
async def _mitm(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, injections: list[InjectionRule], forwards: list[ForwardRule], run: RunToken, rules: tuple[Rule,
```

**Purpose**: This handles allowed HTTPS traffic that must be inspected so credentials can be injected or broker forwarding can happen. It creates a trusted temporary server certificate for the requested host and then processes the decrypted request.

**Data flow**: Client streams, host, port, matching injection and forward rules, run token, and all rules go in → it upgrades the client side to TLS, reads one HTTP request, either forwards through a broker, injects a real credential into headers, or relays the upstream response; it may also parse token usage → no value is returned.

**Call relations**: EgressProxy._handle calls this when rules say the host needs credential-related behavior. It depends on _leaf_context, _start_tls_server, _read_request_head, _forward_match, _inject, _relay, and the metering helpers.

*Call graph*: calls 11 internal fn (_forward_broker, _leaf_context, _meter, _meter_ledger, _meter_tokens, _forward_match, _inject, _read_request_head, _relay, _respond (+1 more)); called by 1 (_handle); 3 external calls (__init__, open_connection, wait_for).


##### `EgressProxy._forward_broker`  (lines 658–702)

```
async def _forward_broker(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, rule: ForwardRule, request: tuple[bytes, list[bytes]], host: str, run: RunToken, rules: tuple[
```

**Purpose**: This sends a sentinel-marked request through a grant broker instead of sending it directly to the host. The real credential stays broker-side, outside the sandbox and outside the proxy's outbound request headers.

**Data flow**: Client streams, a ForwardRule, the request head, host, run token, and rules go in → it reads a bounded body, strips proxy-owned and sentinel headers, calls the broker, and writes a reconstructed HTTP response back → on bad body or broker failure it writes a clear refusal or 502 response.

**Call relations**: EgressProxy._mitm calls this when _forward_match finds a matching grant sentinel. It uses _read_request_body, _forward_headers, the rule's broker, _forward_response_bytes, and metering helpers.

*Call graph*: calls 7 internal fn (_meter, _meter_ledger, _drain_refused_body, _forward_headers, _forward_response_bytes, _read_request_body, _respond); called by 1 (_mitm); 3 external calls (drain, write, log).


##### `EgressProxy._leaf_context`  (lines 704–749)

```
async def _leaf_context(self, host: str) -> ssl.SSLContext
```

**Purpose**: This creates or reuses a TLS server context for pretending, to the sandbox, to be a specific HTTPS host. The sandbox trusts these certificates because they are signed by the proxy's local CA.

**Data flow**: A hostname goes in → the cache is checked; if absent, openssl creates a certificate signing request and signed leaf certificate, then an SSL context is built with the leaf certificate and shared key → the SSL context comes out.

**Call relations**: EgressProxy._mitm calls this before upgrading the client connection to TLS. It uses _openssl and a lock so two requests for the same new host do not mint duplicate certificates at once.

*Call graph*: calls 1 internal fn (_openssl); called by 1 (_mitm); 2 external calls (Path, SSLContext).


##### `EgressProxy._meter`  (lines 751–754)

```
def _meter(self, host: str, rules: tuple[Rule, ...]) -> None
```

**Purpose**: This emits live metrics for an allowed egress event. Metrics are lightweight counters used for monitoring rather than durable billing records.

**Data flow**: A host and rules go in → each MeterRule for that host causes a sandbox_egress_total metric with its dimension → nothing is returned.

**Call relations**: EgressProxy._tunnel, EgressProxy._mitm, and EgressProxy._forward_broker call this once traffic is actually admitted. Durable ledger writes are handled separately by _meter_ledger and _meter_tokens.

*Call graph*: called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (emit_metric).


##### `EgressProxy._meter_ledger`  (lines 756–762)

```
async def _meter_ledger(self, host: str, run: RunToken, rules: tuple[Rule, ...]) -> None
```

**Purpose**: This queues a durable egress request record when the rules say this host should be billed or audited as egress. Token-only metering is deliberately excluded here.

**Data flow**: A host, run token, and rules go in → if a non-token MeterRule matches, an egress meter record is placed on the meter queue → no direct database write happens in this function.

**Call relations**: The tunnel, direct MITM, and broker paths call this after admitting traffic. It hands records to _enqueue_meter so request handling does not wait on ledger writes.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (__init__).


##### `EgressProxy._meter_tokens`  (lines 764–770)

```
async def _meter_tokens(self, run: RunToken, accumulator: 'HttpTokenUsage') -> None
```

**Purpose**: This turns parsed model response usage into a queued token billing record. It logs when the model response did not contain usable usage information.

**Data flow**: A run token and HttpTokenUsage accumulator go in → usage is extracted; if present, a token meter record is queued with model name and token counts → nothing is returned.

**Call relations**: EgressProxy._mitm calls this after relaying a metered model response. It relies on HttpTokenUsage.usage and then hands the record to _enqueue_meter.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 1 (_mitm); 2 external calls (__init__, log).


##### `EgressProxy._enqueue_meter`  (lines 772–779)

```
async def _enqueue_meter(self, record: _MeterRecord) -> None
```

**Purpose**: This places egress or token usage records onto the background metering queue and starts the worker if needed. It keeps the network relay path from doing database work inline.

**Data flow**: One meter record goes in → a meter worker task is created if absent, completed workers are surfaced, and the record is put into the queue → the record will later be written in a batch.

**Call relations**: _meter_ledger and _meter_tokens call this. It starts EgressProxy._meter_loop, which drains the queue and writes batches.

*Call graph*: calls 1 internal fn (_meter_loop); called by 2 (_meter_ledger, _meter_tokens); 1 external calls (create_task).


##### `EgressProxy._meter_loop`  (lines 781–813)

```
async def _meter_loop(self) -> None
```

**Purpose**: This background worker groups meter records into small batches and writes them to the ledger. Batching reduces database overhead during busy proxy traffic.

**Data flow**: Records arrive through the internal queue → the loop waits briefly, gathers more records up to a limit, writes them as one batch, marks queue items done, and stops when it receives a sentinel None → no value is returned.

**Call relations**: EgressProxy._enqueue_meter starts this task. It calls EgressProxy._write_meter_batch and logs batch-level failures without crashing request handling.

*Call graph*: calls 1 internal fn (_write_meter_batch); called by 1 (_enqueue_meter); 2 external calls (sleep, log_error).


##### `EgressProxy._write_meter_batch`  (lines 815–858)

```
async def _write_meter_batch(self, records: list[_MeterRecord]) -> None
```

**Purpose**: This writes queued metering records into workspace ledgers. It combines repeated records for the same run so accounting sees totals rather than many tiny writes.

**Data flow**: A list of egress and token records goes in → records are grouped by run, egress counts are summed, token usage is summed by model, and each run is written inside its workspace transaction → no value is returned; failures are logged per run.

**Call relations**: EgressProxy._meter_loop calls this after collecting a batch. It hands durable work to record_egress_request and record_sandbox_tokens.

*Call graph*: called by 1 (_meter_loop); 6 external calls (__init__, record_egress_request, record_sandbox_tokens, workspace_tx, log_error, ws).


##### `_read_fault`  (lines 861–867)

```
def _read_fault(task: asyncio.Task[tuple[Rule, ...]]) -> None
```

**Purpose**: This consumes an exception from a background rule-resolution task if nobody is left waiting for it. That prevents asyncio from logging raw exception text on its own.

**Data flow**: A completed rule-resolution task goes in → if it was not cancelled, its exception is read → nothing is returned, but any stored fault is marked as observed.

**Call relations**: EgressProxy._rules_for attaches this as a done callback to shared rule-resolution tasks. Callers that still await the task get the same exception normally.


##### `_start_tls_server`  (lines 870–886)

```
async def _start_tls_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]
```

**Purpose**: This replies to CONNECT and upgrades the existing client connection into server-side TLS. It lets the proxy read decrypted HTTPS requests for allowed MITM cases.

**Data flow**: A stream reader, writer, and SSL context go in → reading is paused, a CONNECT success is written, the transport is wrapped in TLS, and the reader/writer are updated → the same reader and writer come back, now speaking decrypted TLS.

**Call relations**: EgressProxy._mitm calls this after obtaining a host certificate from _leaf_context. It prepares the stream for _read_request_head to read the HTTPS request inside the tunnel.

*Call graph*: called by 1 (_mitm); 3 external calls (drain, write, get_running_loop).


##### `_read_request_head`  (lines 889–922)

```
async def _read_request_head(reader: asyncio.StreamReader) -> tuple[bytes, list[bytes]] | _HeaderRefusal | None
```

**Purpose**: This reads an HTTP request line and headers with a timeout and size cap. It protects the proxy from clients that send headers too slowly or too large.

**Data flow**: A stream reader goes in → it reads the first line and header lines until the blank separator, while counting bytes and watching the timeout → it returns the request head, None for clean EOF, or a refusal object with an HTTP status and message.

**Call relations**: EgressProxy._handle uses this for the initial proxy CONNECT. EgressProxy._mitm uses it again after TLS starts to read the inner HTTPS request.

*Call graph*: called by 2 (_handle, _mitm); 3 external calls (__init__, readline, timeout).


##### `_forward_match`  (lines 925–939)

```
def _forward_match(headers: list[bytes], candidates: list[ForwardRule]) -> ForwardRule | None
```

**Purpose**: This checks whether a request carries a broker-forwarding sentinel in the expected header. It chooses the matching account by exact sentinel value.

**Data flow**: Request headers and candidate ForwardRules go in → each header is compared with each rule's header name and sentinel, allowing common auth schemes like Bearer → the matching ForwardRule comes out, or None if no sentinel matches.

**Call relations**: EgressProxy._mitm calls this after reading the decrypted request. A match sends the flow to EgressProxy._forward_broker; no match continues to direct upstream credential injection.

*Call graph*: called by 1 (_mitm).


##### `_read_request_body`  (lines 956–1009)

```
async def _read_request_body(reader: asyncio.StreamReader, headers: list[bytes]) -> bytes | _Refusal
```

**Purpose**: This reads the full body for a broker-forwarded request, but only when it is clearly sized and within the limit. The broker path accepts one complete request, not a streaming upload.

**Data flow**: A stream reader and request headers go in → content length and transfer encoding are checked, then the exact number of bytes is read if allowed → it returns body bytes or a refusal explaining chunked, missing, oversized, negative, malformed, or truncated input.

**Call relations**: EgressProxy._forward_broker calls this before contacting the broker. If it returns a refusal, the broker is not called and the remaining client upload may be drained by _drain_refused_body.

*Call graph*: called by 1 (_forward_broker); 2 external calls (__init__, readexactly).


##### `_drain_refused_body`  (lines 1012–1029)

```
async def _drain_refused_body(reader: asyncio.StreamReader, pending: int) -> None
```

**Purpose**: This discards the rest of a refused upload for a short, bounded time. It helps the client finish sending and then read the proxy's useful error response instead of just seeing a closed socket.

**Data flow**: A stream reader and maximum pending byte count go in → the function reads and throws away chunks until the limit, timeout, or client close is reached → nothing is returned.

**Call relations**: EgressProxy._forward_broker calls this after writing a refusal for a bad forwarded body. It runs only to improve client behavior; the refusal has already been sent.

*Call graph*: called by 1 (_forward_broker); 2 external calls (read, timeout).


##### `_forward_headers`  (lines 1032–1044)

```
def _forward_headers(headers: list[bytes], rule: ForwardRule) -> dict[str, str]
```

**Purpose**: This prepares safe request headers for the broker call. It removes the sentinel credential marker and headers that belong to the proxy or should be recalculated by the broker client.

**Data flow**: Original request headers and the matching ForwardRule go in → connection, proxy connection, host, content-length, and sentinel-bearing headers are dropped; the rest are decoded into strings → a dictionary of headers comes out.

**Call relations**: EgressProxy._forward_broker calls this when making the broker request. The broker then injects the real credential on its side.

*Call graph*: called by 1 (_forward_broker).


##### `_forward_response_bytes`  (lines 1047–1065)

```
def _forward_response_bytes(response: ForwardedResponse) -> bytes
```

**Purpose**: This turns the broker's response object back into a simple HTTP/1.1 response for the sandbox. It also filters unsafe headers that could smuggle extra response lines.

**Data flow**: A ForwardedResponse goes in → status, safe headers, measured content length, connection close, and body are assembled → raw HTTP response bytes come out.

**Call relations**: EgressProxy._forward_broker writes these bytes to the client after a successful broker call. It calls _has_crlf to reject header names or values containing line breaks.

*Call graph*: calls 1 internal fn (_has_crlf); called by 1 (_forward_broker); 1 external calls (HTTPStatus).


##### `_has_crlf`  (lines 1068–1069)

```
def _has_crlf(value: str) -> bool
```

**Purpose**: This checks whether a string contains carriage return or newline characters. Those characters are unsafe inside HTTP header names or values because they can split headers.

**Data flow**: A string goes in → it is scanned for \r or \n → true or false comes out.

**Call relations**: _forward_response_bytes calls this while rebuilding broker responses. Headers that fail this check are dropped.

*Call graph*: called by 1 (_forward_response_bytes).


##### `_inject`  (lines 1072–1097)

```
def _inject(headers: list[bytes], candidates: list[InjectionRule]) -> bytes
```

**Purpose**: This rewrites request headers by replacing an allowed sentinel value with the real secret for that exact account. Unknown or mismatched sentinels pass through unchanged, so one account cannot accidentally receive another account's token.

**Data flow**: Original request headers and candidate InjectionRules go in → connection headers are removed, matching sentinel header values are replaced with real credentials, and connection close is added → a rebuilt header block in bytes comes out.

**Call relations**: EgressProxy._mitm calls this before sending a direct upstream request. The upstream receives the real credential over verified TLS, while the sandbox only ever sent a sentinel.

*Call graph*: called by 1 (_mitm).


##### `_relay`  (lines 1100–1134)

```
async def _relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter, on_downstream: Callable[[bytes]
```

**Purpose**: This copies bytes between the sandbox and the upstream server in both directions. It keeps a response alive after the request side finishes, as long as the upstream keeps making progress.

**Data flow**: Client and upstream readers/writers, plus an optional downstream chunk callback, go in → two pump tasks move bytes in opposite directions, optional response chunks are reported, and idle or completed flows are closed → no value is returned.

**Call relations**: EgressProxy._tunnel uses this for opaque CONNECT traffic. EgressProxy._mitm uses it for direct upstream traffic and may pass HttpTokenUsage.feed to observe model response chunks.

*Call graph*: calls 1 internal fn (_pump); called by 2 (_mitm, _tunnel); 7 external calls (Event, can_write_eof, close, write_eof, create_task, timeout, wait).


##### `_pump`  (lines 1137–1152)

```
async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, on_chunk: Callable[[bytes], None] | None=None, on_progress: Callable[[], None] | None=None) -> None
```

**Purpose**: This is the one-way byte copier used by the relay. It reads chunks from one stream and writes them to another.

**Data flow**: A reader, writer, and optional callbacks go in → chunks are read, written, drained, and then callbacks are notified → it stops on end of stream, I/O error, or cancellation.

**Call relations**: _relay creates two _pump tasks: one client-to-upstream and one upstream-to-client. The upstream-to-client pump can report chunks to the token usage parser.

*Call graph*: called by 1 (_relay); 3 external calls (read, drain, write).


##### `_int_field`  (lines 1155–1157)

```
def _int_field(usage: dict[str, object], name: str) -> int
```

**Purpose**: This safely extracts an integer token count from a JSON-like dictionary. It treats missing values, booleans, and non-integers as zero.

**Data flow**: A dictionary and field name go in → the named value is checked for being a real integer but not a boolean → that integer or zero comes out.

**Call relations**: HttpTokenUsage._absorb_anthropic and HttpTokenUsage._openai use this to read provider usage fields without trusting their shape blindly.

*Call graph*: called by 2 (_absorb_anthropic, _openai).


##### `HttpTokenUsage.feed`  (lines 1183–1220)

```
def feed(self, chunk: bytes) -> None
```

**Purpose**: This accepts raw bytes from an HTTP response and starts turning them into a body that can be searched for model token usage. It understands headers, chunked transfer, and gzip or deflate compression.

**Data flow**: A response byte chunk goes in → until headers are complete it buffers and parses them; then it configures chunking and decompression and sends body bytes onward → internal parser state is updated, with no immediate return value.

**Call relations**: EgressProxy._mitm passes this as the downstream callback to _relay for token-metered model hosts. It feeds later stages such as _feed_wire_body and _decode.

*Call graph*: calls 2 internal fn (_fail, _feed_wire_body); 1 external calls (decompressobj).


##### `HttpTokenUsage.usage`  (lines 1222–1233)

```
def usage(self) -> tuple[str, Usage] | None
```

**Purpose**: This returns the model name and token counts found so far, if any. It is called after the response stream has been relayed.

**Data flow**: The accumulator's internal buffered response state is used → any decompressor is finished, a final non-stream JSON body is checked if needed, and stored token counts are packaged → it returns a model and Usage object, or None if no usage was found.

**Call relations**: EgressProxy._meter_tokens calls this after relay completion. It may call _finish_decoder and _maybe_json_body before producing the accounting record.

*Call graph*: calls 2 internal fn (_finish_decoder, _maybe_json_body); 1 external calls (__init__).


##### `HttpTokenUsage._feed_wire_body`  (lines 1235–1281)

```
def _feed_wire_body(self, chunk: bytes) -> None
```

**Purpose**: This interprets the HTTP body as it appears on the wire, especially chunked transfer encoding. It removes chunk framing before body content is decoded.

**Data flow**: Wire-format body bytes go in → if not chunked they go straight to decoding; if chunked, size lines and chunk endings are parsed and payload bytes are extracted → clean payload bytes are passed to _decode or parsing is failed.

**Call relations**: HttpTokenUsage.feed calls this after headers are parsed. It hands extracted content to _decode and calls _finish_decoder when a zero-size final chunk is seen.

*Call graph*: calls 3 internal fn (_decode, _fail, _finish_decoder); called by 1 (feed).


##### `HttpTokenUsage._decode`  (lines 1283–1299)

```
def _decode(self, chunk: bytes) -> None
```

**Purpose**: This turns possibly compressed body bytes into plain body bytes. It also enforces the parser's size limit while decompressing.

**Data flow**: Body payload bytes go in → if no decompressor is active they are passed through; otherwise gzip or deflate data is expanded in bounded pieces → decoded bytes are sent to _feed_body, or parsing is failed on invalid compression.

**Call relations**: HttpTokenUsage._feed_wire_body calls this for each body payload. It uses the _ContentDecoder-style decompressor and forwards text-ready bytes to _feed_body.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 1 (_feed_wire_body).


##### `HttpTokenUsage._finish_decoder`  (lines 1301–1310)

```
def _finish_decoder(self) -> None
```

**Purpose**: This finalizes decompression exactly once. It makes sure any bytes still buffered by the decompressor are included before usage is calculated.

**Data flow**: The accumulator's decompressor state goes in → if not already finished, flush is called and final bytes are passed to _feed_body → internal state records that decoding is finished.

**Call relations**: HttpTokenUsage._feed_wire_body calls this at the end of chunked bodies, and HttpTokenUsage.usage calls it as a final cleanup before reading counts.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 2 (_feed_wire_body, usage).


##### `HttpTokenUsage._feed_body`  (lines 1312–1323)

```
def _feed_body(self, chunk: bytes) -> None
```

**Purpose**: This buffers decoded body text and splits it into lines for event parsing. It also enforces the maximum buffer size.

**Data flow**: Decoded body bytes go in → bytes are appended, complete newline-delimited lines are sent to _consume, consumed bytes are removed, and oversized leftover data fails parsing → no value is returned.

**Call relations**: HttpTokenUsage._decode and _finish_decoder call this. It drives _consume, which recognizes server-sent events and JSON payloads.

*Call graph*: calls 2 internal fn (_consume, _fail); called by 2 (_decode, _finish_decoder).


##### `HttpTokenUsage._consume`  (lines 1325–1342)

```
def _consume(self, line: bytes) -> None
```

**Purpose**: This examines one decoded response line and extracts JSON events when possible. It understands server-sent events, where useful data lines start with data:.

**Data flow**: One line of bytes goes in → non-SSE JSON-looking lines may be checked as whole-body JSON; SSE data lines containing JSON are parsed → provider-specific handlers update stored usage counts.

**Call relations**: HttpTokenUsage._feed_body calls this for each complete line. It dispatches to _anthropic or _openai depending on the response host, or to _maybe_json_body for plain JSON.

*Call graph*: calls 3 internal fn (_anthropic, _maybe_json_body, _openai); called by 1 (_feed_body); 1 external calls (loads).


##### `HttpTokenUsage._maybe_json_body`  (lines 1344–1359)

```
def _maybe_json_body(self, payload: bytes) -> None
```

**Purpose**: This tries to read token usage from a complete JSON response body rather than a streaming event. It covers non-SSE model responses.

**Data flow**: A possible JSON byte payload goes in → if usage has not already been seen and the payload parses as an object, provider-specific fields are read → internal model and token counters may be updated.

**Call relations**: HttpTokenUsage._consume calls this for non-SSE lines, and HttpTokenUsage.usage calls it on any final buffered body. It delegates to _absorb_anthropic or _openai.

*Call graph*: calls 2 internal fn (_absorb_anthropic, _openai); called by 2 (_consume, usage); 1 external calls (loads).


##### `HttpTokenUsage._anthropic`  (lines 1361–1371)

```
def _anthropic(self, event: dict[str, object]) -> None
```

**Purpose**: This reads Anthropic streaming events and updates model and token usage state. It knows which event types carry initial and incremental usage.

**Data flow**: A parsed Anthropic event dictionary goes in → message_start may set the model and initial token counts, while message_delta may update output usage → internal counters are changed.

**Call relations**: HttpTokenUsage._consume calls this when the response host is Anthropic. It delegates the actual usage-field reading to _absorb_anthropic.

*Call graph*: calls 1 internal fn (_absorb_anthropic); called by 1 (_consume).


##### `HttpTokenUsage._absorb_anthropic`  (lines 1373–1383)

```
def _absorb_anthropic(self, usage: object, initial: bool) -> None
```

**Purpose**: This copies Anthropic usage fields into the accumulator. It handles input tokens, cache read/write tokens, and output tokens.

**Data flow**: A usage object and a flag saying whether it is initial usage go in → valid integer fields are extracted and stored; output tokens are updated when present → the accumulator is marked as having seen usage.

**Call relations**: HttpTokenUsage._anthropic and _maybe_json_body call this. It uses _int_field to avoid accepting malformed token values.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_anthropic, _maybe_json_body).


##### `HttpTokenUsage._openai`  (lines 1385–1394)

```
def _openai(self, event: dict[str, object]) -> None
```

**Purpose**: This reads OpenAI-style model and usage fields from a parsed event or JSON response. It stores prompt and completion token counts.

**Data flow**: A parsed OpenAI event dictionary goes in → model is stored if present, usage is checked, prompt tokens and completion tokens are extracted → internal counters are updated and marked as seen.

**Call relations**: HttpTokenUsage._consume and _maybe_json_body call this for OpenAI hosts. It uses _int_field for safe numeric extraction.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_consume, _maybe_json_body).


##### `HttpTokenUsage._fail`  (lines 1396–1400)

```
def _fail(self) -> None
```

**Purpose**: This permanently stops token parsing for a response after malformed, unsupported, or oversized data is detected. It fails closed for metering rather than risking unbounded memory or wrong parsing.

**Data flow**: No external input is needed → overflow/failure state is set and buffered header, body, and chunk data are cleared → future feed operations ignore more data.

**Call relations**: HttpTokenUsage.feed, _feed_wire_body, _decode, _finish_decoder, and _feed_body call this when they hit unsafe or invalid response data. Later usage() will return None unless usage was already seen.

*Call graph*: called by 5 (_decode, _feed_body, _feed_wire_body, _finish_decoder, feed).


##### `_respond`  (lines 1403–1421)

```
async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None
```

**Purpose**: This writes a complete HTTP error or refusal response to the client. It includes the proxy's explanation in the body so callers can see why the request failed.

**Data flow**: A stream writer, status code, and message go in → an HTTP/1.1 response with content type, content length, connection close, and body is written and drained → no value is returned, and vanished peers are ignored.

**Call relations**: EgressProxy._handle uses this for rejected CONNECT requests, _tunnel uses it for upstream connection failures, _mitm uses it for inner request refusals, and _forward_broker uses it for body or broker errors.

*Call graph*: called by 4 (_forward_broker, _handle, _mitm, _tunnel); 3 external calls (drain, write, HTTPStatus).


### Sandbox carriers
Docker and E2B backends provide local and remote execution environments while routing sandbox networking through UFO controls.

### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox creation and command/file access during conversation turns`

This file is the Docker version of a “carrier”: the part of the system that provides a safe workspace where commands can run. Instead of running tools directly on the host machine, it puts each conversation in its own Docker container, like giving every conversation its own small rented workshop. The workshop’s files live in a host-mounted `/workspace`, so the container can be stopped to save memory without losing the conversation’s files.

The file is careful about two important things. First, it reuses containers when possible. If a conversation already has a running container, it attaches to it. If the container was stopped because it sat idle, it starts it again. If nothing exists, it creates a new one. Second, it never stores a turn’s network credentials inside the container. Instead, every command gets a fresh proxy environment for that turn, so internet requests are counted and filtered correctly, and real API keys never enter the sandbox.

It also cleans up idle running containers by stopping them, not deleting them. This frees memory while keeping the workspace available. Reads, writes, and command execution all “touch” the container so active work is not reclaimed by mistake. The file also installs the current proxy certificate into reused containers, because the proxy’s certificate can change when the UFO process restarts.

#### Function details

##### `_docker`  (lines 52–66)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the `docker` command-line tool asynchronously and returns its exit code, output, and error text. It is the shared doorway through which this file asks Docker to create containers, inspect them, execute commands, and change networks.

**Data flow**: It receives Docker command arguments, optional input bytes, and a timeout. It starts a Docker subprocess, sends the input to it, waits for it to finish, and returns a three-part result: numeric exit code, standard output bytes, and standard error bytes. If the command takes too long, it kills the process and returns a timeout-style result.

**Call relations**: Most DockerCarrier methods use this helper whenever they need Docker to do something. It keeps timeout and subprocess behavior in one place so higher-level methods can focus on sandbox meaning rather than process mechanics.

*Call graph*: called by 9 (_ensure_network, _install_ca, _reclaim_idle, _revive, _running_id, _stopped_id, _write_started, create, exec); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 76–172)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or reconnects to the Docker container for one conversation. It also prepares the per-turn proxy environment so commands from this turn use the right network token and never inherit an old one.

**Data flow**: It receives a sandbox specification containing the conversation ID, image, workspace path, proxy details, run token, and environment variables. It first stops other idle containers if needed, then looks for an existing running or stopped container with the conversation’s fixed name. It installs the current proxy certificate and returns a SandboxHandle pointing at the container. If no usable container exists, it creates a Docker network, starts a new container with the workspace mounted, installs the certificate, and returns the handle. If creation partly succeeds and then fails, it removes the new container and network.

**Call relations**: This is the main open-the-sandbox path. It calls the smaller helpers that check container state, revive stopped containers, create networks, install certificates, and run Docker commands. Later operations such as exec, read, and write use the handle it returns.

*Call graph*: calls 8 internal fn (_ensure_network, _install_ca, _network_name, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 1 external calls (__init__).


##### `DockerCarrier.attach`  (lines 174–190)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Finds an existing container for a conversation without creating a new one. It is useful for read-only or resume-style operations that should use a sandbox if it exists but should not start a fresh workspace.

**Data flow**: It receives a sandbox specification and builds the expected Docker container name. If the container is already running, it returns a SandboxHandle for it. If it is stopped, it tries to start it again and then returns a handle. If there is no container, or revival fails, it returns `None`.

**Call relations**: This method relies on the running/stopped lookup helpers and the revive helper. Unlike create, it does not install a proxy environment into the handle because the intended uses do not need outbound network access.

*Call graph*: calls 3 internal fn (_revive, _running_id, _stopped_id); 1 external calls (__init__).


##### `DockerCarrier._reclaim_idle`  (lines 192–248)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops containers that have been idle long enough, freeing memory while preserving their files and Docker identity. It is a safety valve for hosts that may accumulate many conversation containers.

**Data flow**: It receives the conversation ID currently being opened and marks it as recently touched. It asks Docker for running UFO sandbox containers, records any it did not already know about, and finds containers that have not been touched for the idle limit and have no command currently running. For each still-stale container, it stops it, disconnects its conversation network, and removes that network. If stopping fails, it keeps the old touch record so the container can be retried later.

**Call relations**: Create calls this before opening a sandbox. The method uses Docker lookups and network naming helpers, and it respects the in-flight counter updated by exec, read, and write so it does not stop a container while work is happening.

*Call graph*: calls 3 internal fn (_network_name, _running_id, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 250–280)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside a conversation’s Docker container and returns its text output, error output, and exit code. It pins the container as active while the command runs so idle cleanup does not stop it mid-command.

**Data flow**: It receives a SandboxHandle, a command argument tuple, and a timeout. It increments the in-flight count, records a fresh touch time, turns the handle’s proxy environment into Docker `--env` arguments, and runs `docker exec`. If Docker says the container is not running, it tries to revive the container and run the command once more. It returns an ExecResult and then marks the command as finished and updates the touch time.

**Call relations**: This is the command execution path used after create has produced a handle. It uses the common Docker subprocess helper and may call revive if reclaim or another outside action stopped the container.

*Call graph*: calls 2 internal fn (_revive, _docker); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 282–296)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the container’s workspace. It sends the file contents through standard input rather than putting them on the command line, which is safer and works for arbitrary bytes.

**Data flow**: It receives a SandboxHandle, a target path, and byte content. It marks the container as active, asks `_write_started` to create parent directories and stream the bytes into the file, and retries once after reviving the container if Docker reports it was stopped. If the write still fails, it raises an OSError. Finally it lowers the in-flight count and refreshes the touch time.

**Call relations**: This is the public file-write operation for the Docker carrier. It delegates the actual Docker command to `_write_started` and shares the same revive-on-stopped behavior used by exec and read.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 298–313)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Performs one actual write attempt inside the container. It creates the destination directory if needed and streams the given bytes into the requested file.

**Data flow**: It receives a SandboxHandle, path, and content bytes. It runs `docker exec -i` with a small shell command that makes the parent directory and writes standard input into the target path. It returns the Docker exit code and error bytes so the caller can decide whether to retry or raise an error.

**Call relations**: DockerCarrier.write calls this helper for the first attempt and possibly a second attempt after reviving a stopped container. This helper does not decide policy; it only performs the one Docker write command.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write).


##### `DockerCarrier.read`  (lines 315–337)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the container in chunks. This avoids loading the whole file into memory and ensures the caller sees the file as the sandbox sees it.

**Data flow**: It receives a SandboxHandle and path. It marks the container as active, starts a `cat` command inside the container, and yields byte chunks as they arrive. After the stream ends, it checks whether an error was recorded. If the error says the container was not running, it revives the container and streams again from the beginning. If another error remains, it raises FileNotFoundError. It always updates the in-flight and touch records afterward.

**Call relations**: This is the public file-read operation. It uses `_read_started` to keep one streaming attempt separate from retry logic, and it uses `_revive` if the stopped-container case appears before any successful read.

*Call graph*: calls 2 internal fn (_read_started, _revive).


##### `DockerCarrier._read_started`  (lines 339–373)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncIterator[bytes], list[str]]
```

**Purpose**: Sets up one read attempt and returns both the byte stream and a place where any final error will be recorded. This separation lets the outer read method retry cleanly without mixing retry logic into the stream itself.

**Data flow**: It receives a SandboxHandle and path. It creates an empty failure list and an async stream function that will run `cat` in the container. It returns the stream iterator plus the failure list; the list stays empty on success, or receives the error message after the stream has finished.

**Call relations**: DockerCarrier.read calls this each time it wants to try reading a file. The nested stream performs the actual Docker subprocess work, while read decides whether an error means retry or failure.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 347–371)

```
async def stream() -> AsyncIterator[bytes]
```

**Purpose**: Runs `cat` inside the container and yields the file’s bytes in bounded chunks. It is the actual streaming engine behind DockerCarrier.read.

**Data flow**: It starts a Docker exec subprocess with standard output and standard error captured. It repeatedly reads up to the configured chunk size from standard output and yields each chunk. After output ends, it reads the error text, kills the subprocess if needed, waits for it to exit, and records an error message if Docker returned a non-zero exit code.

**Call relations**: This nested function is produced by `_read_started` and consumed by DockerCarrier.read. It does the low-level pipe reading, while the outer methods interpret errors and perform retry behavior.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier.host`  (lines 375–382)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Reports that this Docker carrier cannot expose an in-container port as an external host URL. It tells callers to use a different carrier when they need to reach a service running inside the sandbox from outside.

**Data flow**: It receives a SandboxHandle and port number, but does not use them to create any route. Instead, it raises a RuntimeError explaining that per-port hosting is not available for this Docker backend.

**Call relations**: This method satisfies the carrier interface but deliberately refuses the operation. Other carriers, such as a remote sandbox provider, may implement this feature; the Docker carrier does not.


##### `DockerCarrier._revive`  (lines 384–398)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Starts a stopped container again and reconnects it to its per-conversation network. This is the shared recovery step used when a previously reclaimed container is touched again.

**Data flow**: It receives a conversation ID and container ID. It computes the network name, ensures that network exists, connects the container to it, and then asks Docker to start the container. It returns `True` if the start succeeds and `False` if the reconnect or start cannot be completed.

**Call relations**: Create, attach, exec, write, and read all call this when they find or encounter a stopped container. It uses the network helper and Docker subprocess helper so callers do not need to know the details of reconnecting a reclaimed sandbox.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (attach, create, exec, read, write).


##### `DockerCarrier._stopped_id`  (lines 400–409)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Looks up whether a named Docker container exists in the stopped, exited state. This is how the carrier finds containers that were paused by idle reclaim but can still be reused.

**Data flow**: It receives a Docker container name. It runs `docker ps` with filters for that exact name and exited status. If Docker reports an error, it raises RuntimeError. Otherwise it returns the found container ID as a string, or `None` if no stopped container matches.

**Call relations**: Create and attach use this after checking for a running container. If it returns an ID, they can try `_revive` instead of creating a brand-new container.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 411–422)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Looks up whether a named Docker container is currently running. It distinguishes a true “not found” result from a Docker command failure, so temporary Docker problems are not mistaken for missing containers.

**Data flow**: It receives a Docker container name. It runs `docker ps` with filters for that exact name and running status. If Docker itself fails, it raises RuntimeError. If the command succeeds, it returns the matching container ID or `None` if there is no running match.

**Call relations**: Create, attach, and idle reclaim call this before deciding what to do next. Its answer controls whether the carrier reuses a live container, revives a stopped one, or considers making a new one.

*Call graph*: calls 1 internal fn (_docker); called by 3 (_reclaim_idle, attach, create).


##### `DockerCarrier._network_name`  (lines 424–425)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Docker network name for a conversation. A separate network name keeps each conversation’s container connection organized and predictable.

**Data flow**: It receives a conversation UUID. It combines the carrier’s network prefix with the UUID in hexadecimal form and returns that string.

**Call relations**: Create uses this before making a new container network, revive uses it before reconnecting a stopped container, and reclaim uses it when removing the network for an idle stopped container.

*Call graph*: called by 3 (_reclaim_idle, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 427–438)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, creating it if needed. It treats “someone else just created it” as success, which matters when two create operations race at the same time.

**Data flow**: It receives a network name. It first asks Docker whether a network with that exact name already exists. If it does, it returns. If not, it asks Docker to create it. A normal create failure raises RuntimeError, but an already-exists response is accepted because the desired result has still been achieved.

**Call relations**: Create calls this before starting a new container, and revive calls it before reconnecting a stopped one. It uses the shared Docker helper for both the lookup and creation commands.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 440–453)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current proxy certificate authority inside a container so HTTPS requests through the UFO proxy are trusted. This is needed even for reused containers because the local proxy may have a new certificate after a process restart.

**Data flow**: It receives a container ID and certificate text. It runs a root-level Docker exec command that writes the certificate into the container’s trusted certificate directory and updates the certificate store. If Docker reports failure, it raises RuntimeError with the error text.

**Call relations**: Create calls this whenever it returns a container handle, whether the container was newly created, already running, or revived from stopped. This keeps network access working for the turn without baking turn-specific credentials into the container.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `manifest`  (lines 456–461)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO plugin system. It says that the extension provides a carrier named `docker` and that DockerCarrier is the factory for creating it.

**Data flow**: It takes no input. It builds and returns a Manifest object containing the extension name, version, and carrier specification.

**Call relations**: The wider system calls this when loading extensions. The returned manifest is how the core discovers that `[sandbox] backend = "docker"` can be served by DockerCarrier.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `startup and sandbox request handling`

A sandbox is the safe working computer where a conversation’s tools run and where its files live. This file teaches the system how to use E2B for that job. Without it, a deployment that chooses the `e2b` sandbox backend would have no way to open a remote workspace, keep it alive, run commands, or recover it after a pause.

The main class, `E2BCarrier`, is the adapter between UFO’s generic sandbox interface and E2B’s SDK. It opens a sandbox for a conversation, reconnects to an existing one when possible, and keeps a small in-process record of “leases” — time windows during which E2B promises the sandbox will stay active. Think of the lease like paying a parking meter: before doing work, the carrier checks whether enough time remains, and renews if needed.

The file also prepares the sandbox for safe internet access. Because E2B runs outside the cluster, every command is given proxy settings that route outbound network calls through UFO’s egress proxy. That proxy can meter requests and replace placeholder model API keys with real ones. The file installs the proxy’s certificate into the sandbox so HTTPS connections still work.

It also streams files in and out, maps command failures into normal execution results, and registers itself in the manifest as the `e2b` carrier.

#### Function details

##### `_egress_env`  (lines 101–136)

```
def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside the remote sandbox send outbound internet traffic through UFO’s egress proxy. This is important because the proxy meters usage and keeps real model API keys out of the sandbox.

**Data flow**: It receives a proxy description and a run token. It checks that the proxy has a public HTTPS URL, then creates proxy URLs that include the run token as the username. It returns a dictionary of environment variables for HTTP proxying, no-proxy exceptions, placeholder model keys, and certificate settings.

**Call relations**: When `E2BCarrier.create` prepares a sandbox handle for a turn, it calls this first so later commands run with safe, metered network access.

*Call graph*: called by 1 (create); 1 external calls (urlsplit).


##### `_live_id`  (lines 139–140)

```
def _live_id(lease: '_Lease | None') -> str | None
```

**Purpose**: Returns the sandbox id from a cached lease, or nothing if there is no cached lease. It is a tiny helper for choosing whether a conversation already has a known live sandbox.

**Data flow**: It receives either a lease object or `None`. If there is a lease, it reads the sandbox id from it; otherwise it returns `None`.

**Call relations**: `E2BCarrier.create` and `E2BCarrier.attach` use this when no durable resume id was supplied, so they can fall back to the sandbox this process already remembers.

*Call graph*: called by 2 (attach, create).


##### `E2BCommands.run`  (lines 153–161)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None) -> E2BCommandResult
```

**Purpose**: Describes the E2B SDK method used to run a shell command inside a sandbox. This is a protocol declaration, meaning it states what shape the SDK object must have rather than implementing the command itself.

**Data flow**: A command string, optional working directory, environment variables, user, and timeout go in. The SDK runs the command remotely and returns stdout, stderr, and an exit code, or raises an SDK exception on certain failures.

**Call relations**: `E2BCarrier.exec`, `_install_ca`, and `_ensure_workspace` rely on this SDK capability to perform work inside the sandbox.


##### `E2BFileStream.__aiter__`  (lines 168–168)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: Describes how an E2B file stream can be read piece by piece using asynchronous iteration. This lets large files be transferred without loading the whole file into memory.

**Data flow**: It starts from an open remote file stream and produces chunks of bytes over time. The caller receives each chunk as it becomes available.

**Call relations**: `E2BCarrier.read` uses this behavior after opening a streamed file read, yielding each chunk onward to its own caller.


##### `E2BFileStream.aclose`  (lines 170–170)

```
async def aclose(self) -> None
```

**Purpose**: Describes the SDK method for closing an open streamed file connection. Closing matters because the remote connection is otherwise still held open.

**Data flow**: It receives the stream object and releases the underlying remote read connection. It returns no file contents; its effect is cleanup.

**Call relations**: `E2BCarrier.read` calls this in a final cleanup step, even if the reader stops early or an error occurs.


##### `E2BFiles.write`  (lines 174–174)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: Describes the E2B SDK method for writing bytes or text to a file inside the sandbox. It is the path used when content must be uploaded as data rather than squeezed into a shell command.

**Data flow**: A target path, file content, and optional user go in. The SDK sends the content to the remote sandbox filesystem and returns an SDK-specific result object.

**Call relations**: `E2BCarrier.write` uses this for normal file uploads, and `_install_ca` uses it to place the proxy certificate into the sandbox before installing it.


##### `E2BFiles.read`  (lines 176–176)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: Describes the E2B SDK method for opening a file read from the sandbox. In this file it is used in streaming mode so large files can be copied out safely.

**Data flow**: A path and requested format go in. The SDK opens the remote file and returns a stream object that can yield bytes over time.

**Call relations**: `E2BCarrier.read` calls this after making sure the sandbox lease is valid, then passes the stream’s chunks back to the rest of the system.


##### `E2BSandbox.get_host`  (lines 185–185)

```
def get_host(self, port: int) -> str
```

**Purpose**: Describes the SDK method that turns a sandbox port into an externally reachable host name. This is used when something running inside the sandbox, such as a development server, needs to be reached from outside.

**Data flow**: A port number goes in. The sandbox object formats and returns the public host name for that port.

**Call relations**: `E2BCarrier.host` calls this after ensuring the sandbox will stay alive long enough for the caller to use the address.


##### `E2BSdk.create`  (lines 189–197)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the SDK method for creating a new E2B sandbox from a template. This is the fresh-start path when no usable previous sandbox exists.

**Data flow**: A template name, timeout span, metadata, lifecycle settings, and API key go in. E2B starts a new remote sandbox and returns an object that can run commands, access files, and expose ports.

**Call relations**: `E2BCarrier._resume_or_open` calls this when there is no resume id or when the named sandbox no longer exists.


##### `E2BSdk.connect`  (lines 199–205)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the SDK method for reconnecting to an existing E2B sandbox. In E2B, this also resumes a paused sandbox and sets its active lease.

**Data flow**: A sandbox id, timeout span, and API key go in. If E2B still has that sandbox, it returns a usable sandbox object; if not, it raises a not-found error.

**Call relations**: `E2BCarrier.attach`, `_resume_or_open`, and `_sandbox` use this as the safe way to resume and renew a sandbox before work continues.


##### `E2BCarrier.create`  (lines 232–258)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Opens or resumes the sandbox for a conversation and returns a generic sandbox handle the rest of UFO can use. It also prepares the sandbox with proxy settings, a trusted certificate, and the expected workspace directory.

**Data flow**: It receives a sandbox specification containing the conversation id, optional resume id, run token, proxy data, and extra environment variables. It builds egress environment variables, clears expired cached leases, chooses whether to resume or open a sandbox, records a new lease, installs the proxy certificate, ensures `/workspace` exists, and returns a `SandboxHandle` containing ids, tokens, and command environment.

**Call relations**: This is the main creation path called by the core sandbox system. It delegates environment setup to `_egress_env`, cache lookup to `_live_id`, sandbox acquisition to `_resume_or_open`, and remote preparation to `_install_ca` and `_ensure_workspace`.

*Call graph*: calls 6 internal fn (_ensure_workspace, _evict_expired, _install_ca, _resume_or_open, _egress_env, _live_id); 2 external calls (__init__, __init__).


##### `E2BCarrier.attach`  (lines 260–289)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an already-known sandbox without creating a new one. It is used when the caller wants to read or inspect an existing conversation sandbox and should get `None` if that sandbox is gone.

**Data flow**: It receives a sandbox specification and looks for a resume id, falling back to the cached live id if needed. If no id exists, it returns `None`. If E2B can reconnect, it records a fresh lease and returns a handle; if E2B says the sandbox is missing, it forgets the cache and returns `None`.

**Call relations**: This path uses `_live_id` only as a fallback. Unlike `create`, it never calls `_resume_or_open` and never opens a replacement sandbox, because absence should remain visible to the caller.

*Call graph*: calls 1 internal fn (_live_id); 2 external calls (__init__, __init__).


##### `E2BCarrier._evict_expired`  (lines 291–303)

```
def _evict_expired(self) -> None
```

**Purpose**: Removes cached lease records whose time has run out. This does not delete the real sandbox; it only forgets local bookkeeping that can no longer be trusted.

**Data flow**: It reads the current clock and scans the carrier’s in-memory conversation-to-lease map. Any lease whose stored expiry time is in the past is removed from that map.

**Call relations**: `E2BCarrier.create` calls this before choosing a sandbox, so stale local entries do not grow forever or mislead later work.

*Call graph*: called by 1 (create).


##### `E2BCarrier._resume_or_open`  (lines 305–329)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: Chooses between reconnecting to an existing sandbox and creating a fresh one. It treats a missing old sandbox as recoverable by opening a new empty sandbox, while logging that the resume failed.

**Data flow**: It receives the sandbox specification and an optional sandbox id to resume. If an id is present, it asks E2B to connect and returns the sandbox if successful. If E2B says the sandbox is gone, it logs that fact. With no usable old sandbox, it asks E2B to create a new sandbox from the configured template and returns it.

**Call relations**: `E2BCarrier.create` calls this after deciding which id, if any, should win. This helper is where the carrier crosses from local decision-making into E2B’s create/connect API.

*Call graph*: called by 1 (create); 1 external calls (log).


##### `E2BCarrier._install_ca`  (lines 331–339)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Installs the egress proxy certificate inside the sandbox so programs there trust HTTPS connections that pass through the proxy. Without this, many secure network calls from the sandbox would fail certificate checks.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path as root, then runs a root command that installs it into the system certificate store. If that command fails, it raises a clear runtime error with the command’s output.

**Call relations**: `E2BCarrier.create` calls this after acquiring a sandbox and before returning it for normal command execution.

*Call graph*: called by 1 (create).


##### `E2BCarrier._ensure_workspace`  (lines 341–352)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: Makes sure `/workspace` exists inside the sandbox and belongs to the normal sandbox user. This gives every conversation a predictable place for files and commands.

**Data flow**: It receives a sandbox, then runs a root command that creates the workspace directory and changes its owner. If the command fails, it raises a runtime error with the available command output.

**Call relations**: `E2BCarrier.create` calls this on every fresh or resumed sandbox, because the process reconnecting to a sandbox may not be the one that originally prepared it.

*Call graph*: called by 1 (create).


##### `E2BCarrier.exec`  (lines 354–389)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the conversation’s sandbox and returns its output in UFO’s standard execution-result format. It also makes sure the sandbox lease is long enough for the command to finish.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It renews or retrieves the sandbox with enough lease time, quotes the argument list into a shell command, runs it in `/workspace` with proxy and tool environment variables, and returns stdout, stderr, and exit code. Normal non-zero command exits become `ExecResult`; command timeouts become exit code 124; unexpected provider failures drop the cached lease and are re-raised.

**Call relations**: Tool execution flows through this method. It relies on `_sandbox` to supply a live sandbox, uses `shlex.join` to form the command string, and calls `_drop` if the provider connection fails in a way that makes the lease untrustworthy.

*Call graph*: calls 2 internal fn (_drop, _sandbox); 2 external calls (__init__, join).


##### `E2BCarrier.write`  (lines 391–402)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Uploads bytes to a file inside the sandbox. This is the safe data path for file contents, since command execution only accepts a shell string and has no standard input channel here.

**Data flow**: It receives a sandbox handle, destination path, and byte content. It gets a live sandbox through `_sandbox`, writes the bytes through E2B’s filesystem API, and returns nothing. If the write fails unexpectedly, it drops the cached lease and lets the error continue upward.

**Call relations**: The rest of the sandbox system calls this when it needs to place files in the remote workspace. It depends on `_sandbox` for lease safety and `_drop` for cleanup after provider failures.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 404–422)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. This allows large generated files to be downloaded without keeping the entire file in memory.

**Data flow**: It receives a sandbox handle and file path. It gets a live sandbox, asks E2B to open the file as a stream, converts E2B’s file-not-found error into Python’s standard `FileNotFoundError`, yields each byte chunk to the caller, and always closes the stream at the end.

**Call relations**: File download flows through this method. It uses `_sandbox` before opening the stream, `_drop` if the provider call fails unexpectedly, and the stream’s `aclose` cleanup path after iteration.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.host`  (lines 424–432)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Returns the public host name for a port exposed by something running inside the sandbox. This supports use cases like reaching a browser debugging endpoint or a preview server started by a tool.

**Data flow**: It receives a sandbox handle and port number. It makes sure the sandbox has enough lease time, asks the sandbox object to format the host for that port, and returns the host string.

**Call relations**: Callers use this when they need an outside address for an in-sandbox service. It goes through `_sandbox` first because an address is not useful if the sandbox pauses before anyone can connect.

*Call graph*: calls 1 internal fn (_sandbox).


##### `E2BCarrier._sandbox`  (lines 434–468)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int) -> E2BSandbox
```

**Purpose**: Returns a sandbox object whose lease lasts long enough for the work about to happen. It is the carrier’s central safety check before command, file, or host operations.

**Data flow**: It receives a sandbox handle and the number of seconds the caller needs. It checks the cached lease for the conversation. If enough time remains, it returns the cached sandbox. Otherwise it removes the old cache entry, reconnects to E2B with a large enough timeout, records a new lease, logs the renewal, and returns the reconnected sandbox. If E2B says the sandbox is gone, it drops the lease record and raises the error.

**Call relations**: `E2BCarrier.exec`, `write`, `read`, and `host` all call this before touching the remote sandbox. It calls `_drop` on not-found failure and logs successful renewals so lease behavior can be observed.

*Call graph*: calls 1 internal fn (_drop); called by 4 (exec, host, read, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 470–475)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: Forgets the cached lease for a conversation after a provider call fails. This prevents later code from trusting a sandbox connection or expiry time that may no longer be valid.

**Data flow**: It receives a conversation id and a short label naming what operation was happening. It removes that conversation from the local lease map and writes a log event.

**Call relations**: `exec`, `write`, `read`, and `_sandbox` call this when E2B communication fails in ways that make the cached lease unsafe.

*Call graph*: called by 4 (_sandbox, exec, read, write); 1 external calls (log).


##### `build_e2b_carrier`  (lines 478–487)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: Constructs the E2B carrier selected by configuration. It fails during startup if the required E2B API key is missing, instead of waiting for the first user request to fail.

**Data flow**: It reads the `E2B_API_KEY` environment variable. If it is absent or empty, it raises a runtime error. If present, it creates and returns an `E2BCarrier` using that key and the configured E2B template name.

**Call relations**: The manifest registers this as the factory for the `e2b` carrier, so the server calls it when the deployment chooses `[sandbox] backend = "e2b"`.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 490–495)

```
def manifest() -> Manifest
```

**Purpose**: Registers this extension with UFO’s plugin manifest system under the carrier name `e2b`. This is how the core system discovers that the E2B sandbox backend exists.

**Data flow**: It creates a manifest object containing the extension name, version, and a carrier specification. That carrier specification points to `build_e2b_carrier` and marks the carrier as off-cluster.

**Call relations**: At startup, the extension loading system reads this manifest. When configuration asks for the `e2b` carrier, the registered factory is used to build the actual `E2BCarrier`.

*Call graph*: 2 external calls (__init__, __init__).


### Sandbox image and proxy validation
Build and deployment checks keep sandbox images consistent across providers and verify that fresh sandboxes can trust and reach the HTTPS proxy.

### `sandbox/build_template.py`

`entrypoint` · `build and deployment time`

This script solves a practical release problem: the sandbox needs many command-line tools, Python packages, Node packages, browser files, and UFO helper scripts already installed before any real work starts. If the hosted E2B version and the Docker version were built separately, one could have a tool that the other lacks, causing bugs that only appear in one runtime. This file avoids that by defining the shared build steps once, then applying them to two different bases: an E2B template for hosted sandboxes, and a Docker image for local/container use.

Think of it like one packing checklist used for two suitcases. The suitcases are different, but the contents must match.

The script installs system tools such as PDF utilities, Chromium, LibreOffice, GitHub CLI, and OCR support. It also installs Python and Node libraries used by document, browser, media, and office-file skills. It copies sandbox helper commands into the image, sets environment variables, removes sudo, switches back to a non-root runtime user, and sets a simple keep-alive start command.

A key safety feature is the build digest: a fingerprint of the build recipe and script contents. The script bakes that fingerprint into the image, then later uses it to check whether the live published template is stale. It can also boot a newly published sandbox and run a readiness probe so missing tools are caught immediately.

#### Function details

##### `build_definition_digest`  (lines 154–179)

```
def build_definition_digest() -> str
```

**Purpose**: Creates a fingerprint of everything important that goes into the sandbox image. This lets the project tell whether the live published template was built from the current recipe or from an older one.

**Data flow**: It reads the build constants in this file, the sandbox environment values, and the contents of each bundled sandbox script. It turns that information into a stable JSON string, hashes it with SHA-256, and returns a text digest like a tamper-evident seal.

**Call relations**: When the image layers are being applied, apply_layers calls this to write the digest into the image. Later, check_published_template calls it again and compares the current source digest with the digest found inside the live sandbox.

*Call graph*: called by 2 (apply_layers, check_published_template); 2 external calls (sha256, dumps).


##### `apply_layers`  (lines 182–208)

```
def apply_layers(builder: object) -> object
```

**Purpose**: Adds the shared UFO sandbox contents onto whichever base image is being built. This is the central recipe that keeps E2B and Docker sandboxes consistent.

**Data flow**: It receives a builder object from the E2B SDK, switches to the build user, installs system, Python, and Node tools, installs Playwright’s browser, creates UFO directories, writes the build digest, copies helper scripts, sets environment variables, switches to the runtime user, and returns the updated builder.

**Call relations**: Both e2b_template and pod_dockerfile call this function so they share the same package installs, files, environment, and start command. Inside the flow it calls build_definition_digest so the finished image carries proof of the exact recipe used.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 2 (e2b_template, pod_dockerfile).


##### `e2b_template`  (lines 211–213)

```
def e2b_template() -> object
```

**Purpose**: Builds the in-memory definition for the hosted E2B sandbox template. E2B is the service that runs remote sandbox environments for the project.

**Data flow**: It starts with the E2B base template, points the build context at the repository root, then passes that builder through apply_layers. The result is a complete E2B template definition ready to publish.

**Call relations**: main calls this when no special command-line option is given. The returned definition is handed to the E2B SDK’s template build operation, which publishes the hosted sandbox.

*Call graph*: calls 1 internal fn (apply_layers); called by 1 (main); 1 external calls (Template).


##### `pod_dockerfile`  (lines 216–218)

```
def pod_dockerfile() -> str
```

**Purpose**: Produces a Dockerfile for the local Docker version of the sandbox image. This lets Docker users build the same sandbox contents without needing an E2B account.

**Data flow**: It starts from the public Docker base image, applies the shared sandbox layers, and asks the E2B SDK to render those steps as Dockerfile text. The output is the full Dockerfile as a string.

**Call relations**: main calls this when the user asks to print the Dockerfile. build_docker_image calls it when it needs Dockerfile text to feed directly into docker build.

*Call graph*: calls 1 internal fn (apply_layers); called by 2 (build_docker_image, main); 2 external calls (Template, to_dockerfile).


##### `build_docker_image`  (lines 221–232)

```
def build_docker_image() -> None
```

**Purpose**: Builds the Docker carrier’s sandbox image on the local machine. It is for deployments that use Docker instead of publishing to E2B.

**Data flow**: It asks pod_dockerfile for Dockerfile text, sends that text into the local docker build command, uses the repository root as the build context, and tags the result with the expected sandbox image name. If Docker reports failure, it stops the script with an error; otherwise it prints the image tag.

**Call relations**: main calls this when the --build-docker option is used. It relies on pod_dockerfile so the Docker build is generated from the same shared recipe as the E2B template.

*Call graph*: calls 1 internal fn (pod_dockerfile); called by 1 (main); 1 external calls (run).


##### `verify_published_template`  (lines 235–250)

```
def verify_published_template(name: str) -> None
```

**Purpose**: Checks that a newly published E2B template actually works before the build is treated as successful. It catches missing baked-in tools right away.

**Data flow**: It receives the template name, starts a temporary E2B sandbox from that template, runs the readiness command inside it, and then kills the sandbox. If the command fails or exits with a non-zero status, it raises an error saying the published template is missing runtime tools.

**Call relations**: main calls this immediately after publishing the E2B template. It uses E2B’s Sandbox.create call to boot the template and then runs the same readiness probe that the template itself uses.

*Call graph*: called by 1 (main); 1 external calls (create).


##### `check_published_template`  (lines 253–274)

```
def check_published_template(name: str) -> None
```

**Purpose**: Checks whether the already published E2B template matches the current source recipe, without publishing anything. This is a safety gate for continuous integration or release checks.

**Data flow**: It computes the digest from the current source, starts a temporary sandbox from the live template, reads the digest file baked into that sandbox, and compares the two. If the file is missing or the values differ, it raises an error telling the user to republish the sandbox template.

**Call relations**: main calls this when the --check option is used. It calls build_definition_digest for the expected value, then uses E2B’s sandbox API to read the actual value from the live template.

*Call graph*: calls 1 internal fn (build_definition_digest); called by 1 (main); 1 external calls (create).


##### `main`  (lines 277–308)

```
def main() -> None
```

**Purpose**: Provides the command-line interface for this build script. It decides whether to print a Dockerfile, build a Docker image, check an existing E2B template, or publish and verify a new E2B template.

**Data flow**: It reads command-line arguments, chooses exactly one action, and then calls the matching helper. With no option, it builds and publishes the E2B template, verifies it by booting a sandbox, and prints the published template id.

**Call relations**: This is the top-level driver when the file is run as a script. It routes user requests to pod_dockerfile, build_docker_image, check_published_template, e2b_template, and verify_published_template, and uses the E2B SDK when publishing is needed.

*Call graph*: calls 5 internal fn (build_docker_image, check_published_template, e2b_template, pod_dockerfile, verify_published_template); 2 external calls (ArgumentParser, build).


### `sandbox/proxy_gate.py`

`entrypoint` · `deployment validation`

This script acts like a gate at deployment time: it refuses to pass unless the sandbox can use the configured HTTPS proxy correctly. The real-world problem is certificate trust. A sandbox is a separate temporary machine, and it will not automatically trust a custom certificate authority, which is a certificate used to say “this proxy is legitimate.” If that trust setup is wrong, later network calls from the sandbox could fail in confusing ways.

The script takes a public proxy URL, reads the proxy certificate from an environment variable, starts a fresh E2B sandbox, copies the certificate into it, and runs the command that installs the certificate. Then it repeatedly runs `curl` inside the sandbox through the proxy to a known HTTPS API endpoint. It intentionally uses an invalid run token, so success does not mean the API request works. Success means the proxy accepted the TLS connection far enough to return the expected CONNECT response, a 403 authorization failure. That is the sign that TLS and routing are working, but credentials are not accepted, which is exactly what this probe expects.

If the proxy is still starting up, some temporary curl failures are treated as “not ready yet,” and the script waits and tries again. Anything unexpected, malformed, or timed out becomes a clear deployment error. The sandbox is always killed at the end, like cleaning up a temporary workbench after testing a tool.

#### Function details

##### `ProxyTlsGate.run`  (lines 45–109)

```
def run(self) -> None
```

**Purpose**: Runs the actual proxy readiness test inside a fresh sandbox. It checks that the proxy URL is HTTPS, installs the provided certificate in the sandbox, probes the proxy until it either sees the expected authorization failure or decides something is wrong.

**Data flow**: It starts with two stored values: the public proxy URL and the certificate text. It turns the URL into a proxy address with a deliberately invalid token, builds a safe `curl` command, creates a temporary sandbox, writes and installs the certificate there, then runs the probe command repeatedly. The output from curl is reduced to two facts: the HTTPS CONNECT status and curl’s exit code. If the status is the expected 403, the function prints a pass message and returns. If the result is malformed, unexpected, or never becomes ready before the deadline, it raises an error. In all cases, it kills the temporary sandbox before leaving.

**Call relations**: This is the worker that `main` sets up and then uses to perform the gate. Inside the flow it relies on URL parsing to reject non-HTTPS proxy addresses, shell quoting to build the curl command safely, E2B sandbox creation to get a clean test machine, monotonic time to measure the retry deadline reliably, and short sleeps between retries so the proxy has time to become ready.

*Call graph*: 5 external calls (create, join, monotonic, sleep, urlsplit).


##### `main`  (lines 112–119)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the proxy gate. It collects the proxy URL from the command line and the certificate from the environment, then starts the gate check.

**Data flow**: It reads `--proxy-url` from command-line arguments and reads the certificate from the environment variable named by `EGRESS_CA_CERT_ENV`. If the certificate is missing, it stops with a clear error. If both inputs are present, it creates a `ProxyTlsGate` object containing those values and runs the gate.

**Call relations**: This is the small front door to the file. It uses Python’s argument parser to turn command-line text into a usable proxy URL, constructs the gate object with that URL and the certificate, and hands control to the gate’s run logic for the real sandbox test.

*Call graph*: 2 external calls (__init__, ArgumentParser).
