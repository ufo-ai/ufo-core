# Cross-cutting security, credentials, network egress policy, and authorization boundaries  `stage-19` (cross-cutting infrastructure)

This stage is shared security plumbing that runs behind the scenes whenever work happens in a workspace. A workspace is a customer or project boundary. The code makes sure secrets, files, network calls, and charges stay inside that boundary.

workspace.py labels each job with the right workspace, while ext/context.py gives extensions a limited toolbox instead of direct access to raw storage or credentials. credentials.py encrypts saved secrets and checks whether a request may use them. credential_kind.py shows which secret slots exist without revealing values. token_signing.py creates signed strings that can be trusted later because changes are detectable.

The sandbox network proxy is the gate at the edge. proxy_serve.py starts the shared proxy, server.py enforces each outbound request, and rules.py turns grants, manifests, models, and credentials into allow-or-deny rules. It can add secrets to approved requests without putting them inside the sandbox. fs_creds.py similarly gives short-lived access only to allowed workspace files.

The extension files plug in real providers: GitHub App tokens, API-key connectors such as Datadog, and direct source-sync credentials. Together they let useful integrations work while keeping raw secrets hidden.

## Files in this stage

### Sandbox egress proxy
Shared proxy startup and request enforcement keep outbound sandbox traffic within workspace-specific network, credential, and billing rules.

### `core/src/ufo/proxy_serve.py`

`entrypoint` · `startup and main loop`

Sandboxes need controlled access to the outside world: model providers, connector services, and sometimes workspace-specific credential-backed APIs. This file is the “front desk” for that shared egress proxy. Without it, sandbox traffic would either have no safe route out, or it could accidentally use the wrong workspace's permissions or secrets.

At startup, the file loads the normal project configuration, reads the database owner connection string, reads a stable certificate authority (CA, a trusted signing identity) used to create per-host proxy certificates, and opens the credential decryption store if the active pack needs injected secrets. It also loads extension manifests, which describe what connectors and credential slots exist.

The important safety idea is that the proxy serves all workspaces from one process, but every request carries a run token. That token includes the workspace identity. The proxy uses that identity when resolving rules and credentials, like a clerk checking the name on a badge before opening the right drawer.

`ProxyServe` then builds the rule resolver, database access, model pricing table, filesystem credential refresher, and actual `EgressProxy`. It starts listening on the configured port and waits until the process receives a shutdown signal. On shutdown, it stops the proxy gracefully instead of cutting off active work immediately.

#### Function details

##### `model_rule_base`  (lines 42–61)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the basic network rules that allow sandboxes to reach configured model providers, such as Anthropic or OpenAI. It only enables a provider when that provider's API key is present in the process environment, so missing keys fail early instead of producing confusing sandbox network failures later.

**Data flow**: It receives the loaded configuration, reads the configured environment variable names for model API keys, then checks the real process environment for those keys. For each key it finds, it asks the model-rule builder to create proxy rules, gathers the allowed provider host names into one shared scope rule, and keeps any key-replacement rules. It returns the completed set of rules, or raises an error if no model provider key exists.

**Call relations**: During `ProxyServe.serve`, this function supplies the model-provider base rules for `PerAgentRules`. The helper `derive_model_rules` contributes provider-specific rules, and `ScopeRule` wraps the final list of allowed hosts so the proxy knows which outside destinations are permitted.

*Call graph*: called by 1 (serve); 2 external calls (__init__, derive_model_rules).


##### `run`  (lines 64–85)

```
def run() -> None
```

**Purpose**: Boots the standalone proxy service from scratch. This is the top-level path used when the shared proxy process is started, and it gathers all configuration, secrets, manifests, pricing, and shutdown wiring before entering the async server loop.

**Data flow**: It starts with no caller-supplied data. It loads configuration, initializes observability logging/telemetry, loads extension manifests, reads the shared certificate material, resolves the owner database connection string, opens the credential store if needed, builds a `ProxyServe` object, logs that startup is beginning, and hands control to `asyncio.run` so the async server can run until shutdown.

**Call relations**: This function is the composition point for the file. It calls `_egress_ca`, `_owner_dsn`, and `_credential_store` to validate required secrets, uses manifest and model-registry helpers from elsewhere in the system, constructs `ProxyServe`, and then delegates the long-running work to `ProxyServe.serve`.

*Call graph*: calls 3 internal fn (_credential_store, _egress_ca, _owner_dsn); 9 external calls (__init__, Event, run, load_config, injecting_slots, load_manifests, model_registry, init_o11y, log).


##### `_egress_ca`  (lines 88–99)

```
def _egress_ca() -> tuple[str, str]
```

**Purpose**: Reads the shared certificate authority certificate and private key from environment variables. The proxy needs these stable values so sandboxes keep trusting proxy-generated certificates across restarts.

**Data flow**: It reads two environment variables: one for the CA certificate and one for the CA key. If either value is missing, it raises an error explaining that the shared proxy cannot safely sign sandbox traffic certificates. If both exist, it returns them as a pair of strings.

**Call relations**: `run` calls this during startup before creating `ProxyServe`. The returned certificate and key are later passed into `EgressProxy`, which uses them to create trusted per-host certificates for intercepted sandbox egress traffic.

*Call graph*: called by 1 (run).


##### `_owner_dsn`  (lines 102–115)

```
def _owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string for the database owner role, which can read across workspaces. This shared proxy needs that broad database access, but it still scopes each query by the workspace found in the run token.

**Data flow**: It receives the loaded configuration, then looks first in the `UFO_OWNER_DSN` environment variable and then in the configured database owner URL. If neither exists, it raises an error because the shared proxy cannot resolve workspace-specific rules. If it finds a value, it adjusts the PostgreSQL URL so the async database driver is used, and returns the adjusted string.

**Call relations**: `run` calls this during startup and passes the result into `ProxyServe`. Later, `ProxyServe.serve` uses that string with database initialization so rule and credential lookups can work for all workspaces.

*Call graph*: called by 1 (run).


##### `_credential_store`  (lines 118–132)

```
def _credential_store(config: Config, slots: tuple[CredentialSlot, ...]) -> CredentialStore | None
```

**Purpose**: Creates the encrypted credential store used to decrypt workspace-specific connector secrets when a sandbox request needs them. If the active pack declares credential-injection slots, this function makes sure the decryption key is present before the proxy starts.

**Data flow**: It receives the configuration and the credential slots declared by loaded manifests. It reads the credential encryption key from the configured environment variable. If the key exists, it builds a Fernet decryptor and wraps it in a `CredentialStore`. If the key is missing but credential slots are required, it raises an error. If no slots need injection, it returns `None` because no credential store is needed.

**Call relations**: `run` calls this after loading manifests and finding their injecting slots. The resulting store is passed into `ProxyServe`, and then into `PerAgentRules`, so later request handling can swap the right workspace secret onto outbound traffic when required.

*Call graph*: called by 1 (run); 2 external calls (__init__, Fernet).


##### `ProxyServe.serve`  (lines 150–181)

```
async def serve(self) -> None
```

**Purpose**: Runs the proxy service itself. It wires together shutdown signals, database access, per-workspace rule resolution, certificate signing, run-token decoding, usage pricing, and the actual network proxy listener.

**Data flow**: It uses the fields stored on the `ProxyServe` instance: configuration, manifests, owner database string, CA certificate and key, optional credential store, pricing, and shutdown event. It registers operating-system signal handlers, initializes the database, builds the per-agent rule resolver, prepares optional workspace filesystem credential refreshing, constructs the `EgressProxy`, starts it on the configured port, waits for the shutdown event, and finally stops the proxy with the configured grace period.

**Call relations**: `run` creates the `ProxyServe` object and then calls this method through `asyncio.run`. Inside, it calls `model_rule_base` for model-provider rules, uses manifest helpers for connector rules and command-line tools, uses `RunTokenCodec.from_env` so requests can be tied back to their workspace, and hands the finished resolver and authorization callbacks to `EgressProxy` for live request handling.

*Call graph*: calls 2 internal fn (model_rule_base, from_env); 11 external calls (__init__, __init__, __init__, get_running_loop, init_db, connector_clis, injecting_slots, log, sandbox_fs_minter, connector_transfer_hosts (+1 more)).


### `core/src/ufo/sandbox/proxy/server.py`

`io_transport` · `startup, request handling, shutdown`

A sandboxed agent is not allowed to reach the internet directly. Instead, its web traffic goes through this proxy, like a guarded front desk for outbound calls. The proxy reads a signed run token from the request, checks that the named turn is still running, and then builds a rule list for that exact workspace, agent, and member. If the destination is not allowed, the request is refused by default.

When a destination is allowed, the proxy chooses one of three paths. For plain allowed hosts, it opens an opaque tunnel and cannot see inside the encrypted traffic. For hosts that need a real API key, it briefly becomes the trusted middle point for TLS, replaces only the matching fake “sentinel” credential with the real one, and forwards the request over a verified secure connection. For some grant-backed requests, it does not put the credential on the wire at all; it sends the request through a broker that owns the credential.

The same listener also serves short-lived filesystem credentials for workspace mounts. Along the way, the proxy limits connection counts, resolves public internet hosts safely, emits metrics, writes ledger rows, and parses model responses for token usage.

#### Function details

##### `_ContentDecoder.unconsumed_tail`  (lines 146–146)

```
def unconsumed_tail(self) -> bytes
```

**Purpose**: This is part of a small protocol, meaning a promised shape for decompression objects. It represents compressed bytes that were not yet consumed by the decoder.

**Data flow**: A decoder object exposes this property → callers read the leftover bytes → the caller can continue decoding without losing data.

**Call relations**: It is not called as a normal function here. HttpTokenUsage relies on this promised property when it works with gzip or deflate decoders.


##### `_ContentDecoder.decompress`  (lines 148–148)

```
def decompress(self, data: bytes, max_length: int=0) -> bytes
```

**Purpose**: This protocol method describes how a decoder turns compressed bytes into plain bytes. It lets this file treat different compression formats in the same way.

**Data flow**: Compressed data and an optional size limit go in → the decoder expands as much as it safely can → decoded bytes come out, with leftovers available through unconsumed_tail.

**Call relations**: HttpTokenUsage uses this shape when reading compressed model responses. The actual implementation comes from zlib, not from this protocol.


##### `_ContentDecoder.flush`  (lines 150–150)

```
def flush(self) -> bytes
```

**Purpose**: This protocol method describes how to ask a decoder for any final decoded bytes at the end of a response. It prevents token usage data from being missed if it appears at the very end.

**Data flow**: A decoder that may still hold buffered data goes in → it finishes its stream → final plain bytes come out.

**Call relations**: HttpTokenUsage calls this through its decoder when usage() or chunk-ending logic finishes a compressed response.


##### `generate_ca`  (lines 156–179)

```
async def generate_ca() -> tuple[str, str]
```

**Purpose**: Creates a temporary certificate authority, which is a local root certificate used to sign per-host certificates for the proxy. This lets the proxy safely inspect approved HTTPS requests inside the sandbox trust boundary.

**Data flow**: No caller data is needed → it creates temporary files and asks OpenSSL to make a self-signed certificate and key → it returns the certificate text and private key text.

**Call relations**: Startup code calls this before constructing the proxy. It hands certificate material to EgressProxy.start and EgressProxy._leaf_context indirectly, because leaf certificates must be signed by this authority.

*Call graph*: calls 1 internal fn (_openssl); 2 external calls (Path, TemporaryDirectory).


##### `_openssl`  (lines 182–188)

```
async def _openssl(*argv: str) -> None
```

**Purpose**: Runs the OpenSSL command-line tool and turns failures into Python errors with a useful message. This avoids needing a separate cryptography library in this file.

**Data flow**: OpenSSL arguments go in → a subprocess runs with those arguments → the function returns nothing on success or raises an error containing OpenSSL's stderr on failure.

**Call relations**: generate_ca uses it to make the root certificate, EgressProxy.start uses it to make a reusable leaf key, and EgressProxy._leaf_context uses it to sign host-specific certificates.

*Call graph*: called by 3 (_leaf_context, start, generate_ca); 1 external calls (create_subprocess_exec).


##### `PerAgentRules.resolve`  (lines 214–234)

```
async def resolve(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: Builds the allow-list and credential rules for one agent turn. It keeps workspaces and agents separated, so one agent cannot borrow another agent's grants or secrets.

**Data flow**: A run token, or no token, goes in → it looks up the turn and combines base rules, optional internet rules, workspace credential rules, grant rules, and CLI rules → a tuple of rules comes out.

**Call relations**: EgressProxy receives this function as its rule resolver. It calls PerAgentRules._turn_of first, then hands off to rule-derivation helpers for credentials, grants, and CLI credentials.

*Call graph*: calls 1 internal fn (_turn_of); 3 external calls (derive_cli_rules, derive_credential_rules, derive_grant_rules).


##### `PerAgentRules._turn_of`  (lines 236–268)

```
async def _turn_of(self, run: RunToken) -> tuple[UUID, UUID | None, bool] | None
```

**Purpose**: Finds which agent and member are tied to a run token, and whether that agent is allowed general internet access. This is the identity check behind per-agent network permissions.

**Data flow**: A run token goes in → the database is queried for the matching turn and agent in the same workspace → it returns agent id, acting member id, and internet-access flag, or nothing if the turn is unknown.

**Call relations**: PerAgentRules.resolve calls this before adding agent-specific rules. It uses workspace_tx and SQL selection to read the needed database row.

*Call graph*: called by 1 (resolve); 2 external calls (select, workspace_tx).


##### `PerAgentRules.turn_live`  (lines 270–286)

```
async def turn_live(self, run: RunToken) -> bool
```

**Purpose**: Checks whether a run token still names a running turn. This stops old or ended turns from continuing to use network access or real credentials.

**Data flow**: A run token goes in → the database status for that turn is read fresh → the function returns true only when the status is RUNNING.

**Call relations**: This is meant to be supplied to EgressProxy as its authorizer. The proxy calls the authorizer before allowing CONNECT traffic or minting filesystem credentials.

*Call graph*: 2 external calls (select, workspace_tx).


##### `EgressProxy.start`  (lines 315–331)

```
async def start(self, bind_host: str=PROXY_BIND_HOST, port: int=0, public_url: str | None=None) -> ProxyEndpoint
```

**Purpose**: Starts listening for sandbox proxy connections and prepares certificate files needed for HTTPS interception. It returns the endpoint information that sandboxes need in order to use the proxy.

**Data flow**: Bind host, port, and optional public URL go in → a temporary work directory is created, CA files and a leaf key are written, and an asyncio server starts → a ProxyEndpoint with port, CA certificate, and public URL comes out.

**Call relations**: Called during setup. It uses _openssl for key generation and registers EgressProxy._handle as the per-connection entry point.

*Call graph*: calls 1 internal fn (_openssl); 4 external calls (__init__, start_server, Path, TemporaryDirectory).


##### `EgressProxy.stop`  (lines 333–361)

```
async def stop(self, graceful_shutdown_seconds: int=0) -> None
```

**Purpose**: Shuts the proxy down without hanging forever. It stops new connections, gives active ones a grace period, cancels leftovers, flushes metering, and removes temporary certificate files.

**Data flow**: An optional grace period goes in → the listener is closed, live connection tasks are waited on or cancelled, the meter worker is stopped, and the temporary directory is cleaned → no value is returned, but proxy resources are released.

**Call relations**: Called during teardown. It coordinates with tasks created by EgressProxy._handle and with the background metering loop started by EgressProxy._enqueue_meter.

*Call graph*: 2 external calls (gather, wait).


##### `EgressProxy._handle`  (lines 363–463)

```
async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None
```

**Purpose**: Processes one incoming client connection from a sandbox. It is the main traffic dispatcher: it rejects invalid requests, serves filesystem credentials, or routes approved CONNECT requests.

**Data flow**: A stream reader and writer go in → it reads the request header, checks limits and authorization, resolves rules, verifies destination permission, and chooses tunneling or inspected forwarding → it writes a response or relays traffic, then updates connection counters.

**Call relations**: asyncio.start_server calls this for each connection. It calls _read_request_head and _respond for basic HTTP handling, _run_token and _rules_for for authorization, _serve_workspace_credentials for GET credential requests, and either _tunnel or _mitm for allowed CONNECT traffic.

*Call graph*: calls 7 internal fn (_mitm, _rules_for, _run_token, _serve_workspace_credentials, _tunnel, _read_request_head, _respond); 3 external calls (__init__, close, current_task).


##### `EgressProxy._serve_workspace_credentials`  (lines 465–492)

```
async def _serve_workspace_credentials(self, writer: asyncio.StreamWriter, target: str) -> None
```

**Purpose**: Serves short-lived workspace filesystem credentials on a special local path. This lets a sandbox mount its workspace files without receiving broad or long-lived cloud credentials.

**Data flow**: A response writer and URL target go in → the target is checked for the credential path and token shape, then the configured credential minting function is called → a JSON credential response or an error response is written.

**Call relations**: EgressProxy._handle calls this when the incoming method is GET. It uses _respond for refusals and logs minting failures.

*Call graph*: calls 1 internal fn (_respond); called by 1 (_handle); 3 external calls (drain, write, log).


##### `EgressProxy._rules_for`  (lines 494–509)

```
async def _rules_for(self, run: RunToken | None) -> tuple[Rule, ...]
```

**Purpose**: Returns the current rule set for a run token while avoiding repeated expensive lookups. It caches briefly but refreshes often enough that short-lived credentials and permissions stay current.

**Data flow**: A run token, or no token, goes in → it checks a time-limited cache, shares any in-progress lookup for the same run, or starts a new lookup → the resolved tuple of rules comes out.

**Call relations**: EgressProxy._handle calls this after authorization. It delegates actual lookup to EgressProxy._resolve_rules and shields the shared task so one cancelled request does not cancel rule resolution for others.

*Call graph*: calls 1 internal fn (_resolve_rules); called by 1 (_handle); 3 external calls (create_task, shield, monotonic).


##### `EgressProxy._resolve_rules`  (lines 511–531)

```
async def _resolve_rules(self, run: RunToken) -> tuple[Rule, ...]
```

**Purpose**: Performs the actual rule resolution and stores the result in the short cache. If rule lookup fails, it fails closed by falling back to base rules rather than broadening access.

**Data flow**: A run token goes in → the configured resolver is called, errors are logged, the cache is bounded and updated on success → a rule tuple comes out.

**Call relations**: EgressProxy._rules_for creates and awaits this task. It calls the injected resolver and records cache entries used by later _rules_for calls.

*Call graph*: called by 1 (_rules_for); 4 external calls (__init__, current_task, monotonic, log).


##### `EgressProxy._run_token`  (lines 533–539)

```
def _run_token(self, proxy_auth: str) -> RunToken | None
```

**Purpose**: Extracts and verifies a run token from the Proxy-Authorization header. Bad or missing tokens become no token, which leads to denial.

**Data flow**: A header string goes in → the token codec tries to parse it → a RunToken comes out on success, otherwise None.

**Call relations**: EgressProxy._handle calls this before checking liveness or rules. It depends on the configured RunTokenCodec for the actual signature and format checks.

*Call graph*: called by 1 (_handle).


##### `EgressProxy._resolve_public_address`  (lines 541–573)

```
async def _resolve_public_address(self, host: str, port: int) -> str
```

**Purpose**: Resolves a general internet hostname to a safe public IPv4 address. It blocks private, multicast, IPv6, malformed, or unresolvable destinations so internet access cannot be used to reach internal services.

**Data flow**: A host and port go in → the host is parsed as IPv4 or looked up through DNS for A records → the first globally routable IPv4 address comes out, or an error signals refusal or reachability failure.

**Call relations**: EgressProxy._handle uses this as the default resolver when a host is allowed only by InternetRule rather than an exact ScopeRule.

*Call graph*: 1 external calls (IPv4Address).


##### `EgressProxy._tunnel`  (lines 575–602)

```
async def _tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, run: RunToken, rules: tuple[Rule, ...], connect_host: str) -> None
```

**Purpose**: Opens a plain byte tunnel to an approved host when the proxy does not need to inspect or change the HTTPS request. The sandbox and upstream server keep their own encrypted conversation.

**Data flow**: Client streams, destination host and port, run token, rules, and resolved connect host go in → it opens the upstream connection, sends CONNECT success, records metering, and relays bytes both ways → traffic flows until one side closes.

**Call relations**: EgressProxy._handle calls this for allowed hosts with no injection or forwarding rules. It uses _relay for byte copying, _meter for metrics, and _meter_ledger for accounting.

*Call graph*: calls 4 internal fn (_meter, _meter_ledger, _relay, _respond); called by 1 (_handle); 4 external calls (drain, write, open_connection, wait_for).


##### `EgressProxy._mitm`  (lines 604–660)

```
async def _mitm(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, injections: list[InjectionRule], forwards: list[ForwardRule], run: RunToken, rules: tuple[Rule,
```

**Purpose**: Handles approved HTTPS traffic that needs credential injection, broker forwarding, or token usage metering. “MITM” here means the proxy becomes a trusted middle point using a certificate the sandbox already trusts.

**Data flow**: Client streams, host, port, matching injection and forwarding rules, run token, and all rules go in → it starts TLS with the sandbox, reads one decrypted request, optionally forwards through a broker, otherwise connects upstream and rewrites credential headers → it streams the response back and may record token usage.

**Call relations**: EgressProxy._handle calls this when rules say the destination needs inspection. It uses _leaf_context and _start_tls_server for TLS, _forward_match and _forward_broker for grant-brokered requests, _inject for direct credential replacement, _relay for streaming, and metering helpers for accounting.

*Call graph*: calls 11 internal fn (_forward_broker, _leaf_context, _meter, _meter_ledger, _meter_tokens, _forward_match, _inject, _read_request_head, _relay, _respond (+1 more)); called by 1 (_handle); 3 external calls (__init__, open_connection, wait_for).


##### `EgressProxy._forward_broker`  (lines 662–706)

```
async def _forward_broker(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, rule: ForwardRule, request: tuple[bytes, list[bytes]], host: str, run: RunToken, rules: tuple[
```

**Purpose**: Sends a sentinel-marked request through a grant broker instead of sending the real credential through the proxy. This keeps the actual account credential on the broker side only.

**Data flow**: Client streams, a ForwardRule, the parsed request, host, run token, and rules go in → it reads a bounded request body, records metering, calls the broker with cleaned headers and full URL, and writes the broker's reconstructed HTTP response → the client receives one closed response.

**Call relations**: EgressProxy._mitm calls this after _forward_match finds a matching sentinel. It uses _read_request_body, _forward_headers, _forward_response_bytes, _respond, and _drain_refused_body to complete or safely refuse the broker path.

*Call graph*: calls 7 internal fn (_meter, _meter_ledger, _drain_refused_body, _forward_headers, _forward_response_bytes, _read_request_body, _respond); called by 1 (_mitm); 3 external calls (drain, write, log).


##### `EgressProxy._leaf_context`  (lines 708–753)

```
async def _leaf_context(self, host: str) -> ssl.SSLContext
```

**Purpose**: Creates or reuses a TLS server certificate for a specific host. This lets the proxy decrypt approved sandbox HTTPS traffic without using a certificate for the wrong name.

**Data flow**: A hostname goes in → the cache is checked, and if missing, OpenSSL creates a certificate signing request and signs it with the proxy CA → an SSLContext ready for server-side TLS comes out.

**Call relations**: EgressProxy._mitm calls this before upgrading the sandbox connection to TLS. It uses _openssl and the temporary files prepared by EgressProxy.start.

*Call graph*: calls 1 internal fn (_openssl); called by 1 (_mitm); 2 external calls (Path, SSLContext).


##### `EgressProxy._meter`  (lines 755–758)

```
def _meter(self, host: str, rules: tuple[Rule, ...]) -> None
```

**Purpose**: Emits immediate in-memory observability metrics for an allowed host. This gives operators a live count of sandbox egress without waiting for database ledger writes.

**Data flow**: A host and rule tuple go in → each matching MeterRule emits a sandbox_egress_total metric with its dimension → no value is returned.

**Call relations**: EgressProxy._tunnel, EgressProxy._mitm, and EgressProxy._forward_broker call this once an allowed connection or request is actually being sent.

*Call graph*: called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (emit_metric).


##### `EgressProxy._meter_ledger`  (lines 760–766)

```
async def _meter_ledger(self, host: str, run: RunToken, rules: tuple[Rule, ...]) -> None
```

**Purpose**: Queues durable accounting for a non-token egress request. It skips token-only metering because model token billing is recorded separately.

**Data flow**: A host, run token, and rules go in → it checks whether the host has a non-token MeterRule → if so, it enqueues an egress record for the background writer.

**Call relations**: Called by _tunnel, _mitm, and _forward_broker after metered traffic starts. It hands records to EgressProxy._enqueue_meter.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 3 (_forward_broker, _mitm, _tunnel); 1 external calls (__init__).


##### `EgressProxy._meter_tokens`  (lines 768–774)

```
async def _meter_tokens(self, run: RunToken, accumulator: 'HttpTokenUsage') -> None
```

**Purpose**: Turns parsed model-response usage into a queued accounting record. If the response did not contain recognizable usage data, it logs that fact instead of inventing numbers.

**Data flow**: A run token and HttpTokenUsage accumulator go in → usage() is asked for model and token counts → a token meter record is queued, or absence is logged.

**Call relations**: EgressProxy._mitm calls this after relaying a metered model response. It hands successful records to EgressProxy._enqueue_meter.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 1 (_mitm); 2 external calls (__init__, log).


##### `EgressProxy._enqueue_meter`  (lines 776–783)

```
async def _enqueue_meter(self, record: _MeterRecord) -> None
```

**Purpose**: Adds an accounting record to a background queue and starts the writer task if needed. This keeps network traffic from waiting on database writes.

**Data flow**: An egress or token meter record goes in → the meter worker is created or checked, then the record is placed on the queue → no direct result is returned.

**Call relations**: EgressProxy._meter_ledger and EgressProxy._meter_tokens call this. It starts EgressProxy._meter_loop, which later writes batches.

*Call graph*: calls 1 internal fn (_meter_loop); called by 2 (_meter_ledger, _meter_tokens); 1 external calls (create_task).


##### `EgressProxy._meter_loop`  (lines 785–817)

```
async def _meter_loop(self) -> None
```

**Purpose**: Runs in the background to batch accounting records. Batching reduces database overhead while still flushing records quickly.

**Data flow**: Records arrive through the queue → the loop waits a tiny window, gathers up to a batch size, writes the batch, marks queue items done, and exits when it receives a stop marker → database updates happen through _write_meter_batch.

**Call relations**: EgressProxy._enqueue_meter starts this task. EgressProxy.stop sends its stop marker and waits for it to finish.

*Call graph*: calls 1 internal fn (_write_meter_batch); called by 1 (_enqueue_meter); 2 external calls (sleep, log_error).


##### `EgressProxy._write_meter_batch`  (lines 819–862)

```
async def _write_meter_batch(self, records: list[_MeterRecord]) -> None
```

**Purpose**: Writes queued egress and sandbox model-token usage into the workspace ledger. It groups records by run so each turn is billed under the right workspace and turn.

**Data flow**: A list of meter records goes in → egress counts are summed and token usage is added per model → accounting rows are written inside the correct workspace context, while per-run failures are logged.

**Call relations**: EgressProxy._meter_loop calls this for each batch. It delegates durable writes to record_egress_request and record_sandbox_tokens.

*Call graph*: called by 1 (_meter_loop); 6 external calls (__init__, record_egress_request, record_sandbox_tokens, workspace_tx, log_error, ws).


##### `_start_tls_server`  (lines 865–881)

```
async def _start_tls_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]
```

**Purpose**: Turns an accepted CONNECT connection into a TLS server connection from the sandbox's point of view. This is the moment the proxy starts reading decrypted HTTP for approved inspected traffic.

**Data flow**: A reader, writer, and server SSL context go in → it sends CONNECT success, upgrades the existing transport to TLS, and patches the stream objects to use the TLS transport → the same reader and writer now carry decrypted data.

**Call relations**: EgressProxy._mitm calls this after getting a host-specific certificate from _leaf_context.

*Call graph*: called by 1 (_mitm); 3 external calls (drain, write, get_running_loop).


##### `_read_request_head`  (lines 884–917)

```
async def _read_request_head(reader: asyncio.StreamReader) -> tuple[bytes, list[bytes]] | _HeaderRefusal | None
```

**Purpose**: Reads an HTTP request line and headers with a timeout and size limit. This protects the proxy from clients that send endless, oversized, or stalled headers.

**Data flow**: A stream reader goes in → it reads until the blank line after headers, tracking bytes and time → it returns the request line plus header lines, None for a closed connection, or a refusal object explaining the error.

**Call relations**: EgressProxy._handle uses this for the initial proxy request. EgressProxy._mitm uses it again after TLS starts to read the inner HTTPS request.

*Call graph*: called by 2 (_handle, _mitm); 3 external calls (__init__, readline, timeout).


##### `_forward_match`  (lines 920–934)

```
def _forward_match(headers: list[bytes], candidates: list[ForwardRule]) -> ForwardRule | None
```

**Purpose**: Finds whether a decrypted request carries a grant sentinel header that should be broker-forwarded. It matches the exact sentinel so one account's marker cannot unlock another account's grant.

**Data flow**: Request headers and candidate ForwardRules go in → each header value is compared against each rule's expected header name and sentinel → the matching ForwardRule comes out, or None.

**Call relations**: EgressProxy._mitm calls this before deciding whether to use _forward_broker or direct upstream forwarding.

*Call graph*: called by 1 (_mitm).


##### `_read_request_body`  (lines 951–1004)

```
async def _read_request_body(reader: asyncio.StreamReader, headers: list[bytes]) -> bytes | _Refusal
```

**Purpose**: Reads the full body for a broker-forwarded request, but only when it is clearly bounded and small enough. The broker path accepts one complete request, not an open-ended stream.

**Data flow**: A stream reader and request headers go in → content-length and transfer-encoding are checked, the body is read exactly when allowed → bytes come out, or a refusal explains missing, chunked, too-large, negative, or truncated input.

**Call relations**: EgressProxy._forward_broker calls this before invoking the grant broker. On refusal, the broker path uses _respond and _drain_refused_body.

*Call graph*: called by 1 (_forward_broker); 2 external calls (__init__, readexactly).


##### `_drain_refused_body`  (lines 1007–1024)

```
async def _drain_refused_body(reader: asyncio.StreamReader, pending: int) -> None
```

**Purpose**: Discards leftover upload bytes after the proxy has already decided to refuse a broker-forwarded body. This helps the client finish sending and read the clear error response instead of seeing only a broken socket.

**Data flow**: A reader and maximum pending byte count go in → bytes are read and discarded until the cap, timeout, or client close → no data is returned.

**Call relations**: EgressProxy._forward_broker calls this after _read_request_body returns a refusal.

*Call graph*: called by 1 (_forward_broker); 2 external calls (read, timeout).


##### `_forward_headers`  (lines 1027–1039)

```
def _forward_headers(headers: list[bytes], rule: ForwardRule) -> dict[str, str]
```

**Purpose**: Builds the header set that is safe to send to a grant broker. It removes the sentinel credential header and connection-level headers that should be recreated by the broker's own HTTP client.

**Data flow**: Original request headers and a ForwardRule go in → disallowed headers are skipped and remaining names and values are decoded → a dictionary of forwarded headers comes out.

**Call relations**: EgressProxy._forward_broker calls this when preparing the broker.forward call.

*Call graph*: called by 1 (_forward_broker).


##### `_forward_response_bytes`  (lines 1042–1060)

```
def _forward_response_bytes(response: ForwardedResponse) -> bytes
```

**Purpose**: Converts a broker's response object back into a single HTTP/1.1 response for the sandbox. It also filters unsafe headers that could split or corrupt the response.

**Data flow**: A ForwardedResponse goes in → status, safe headers, measured content length, connection close, and body are assembled → raw HTTP response bytes come out.

**Call relations**: EgressProxy._forward_broker writes these bytes back to the client. It calls _has_crlf to reject header names or values containing line breaks.

*Call graph*: calls 1 internal fn (_has_crlf); called by 1 (_forward_broker); 1 external calls (HTTPStatus).


##### `_has_crlf`  (lines 1063–1064)

```
def _has_crlf(value: str) -> bool
```

**Purpose**: Checks whether a string contains carriage-return or newline characters. Those characters are dangerous inside HTTP headers because they can create fake extra headers or responses.

**Data flow**: A string goes in → it is scanned for '\r' or '\n' → a boolean comes out.

**Call relations**: _forward_response_bytes calls this while filtering broker-provided headers.

*Call graph*: called by 1 (_forward_response_bytes).


##### `_inject`  (lines 1067–1092)

```
def _inject(headers: list[bytes], candidates: list[InjectionRule]) -> bytes
```

**Purpose**: Rewrites request headers by replacing an exact fake sentinel credential with the matching real secret. It leaves unknown or foreign sentinels untouched, preventing accidental credential crossover.

**Data flow**: Original headers and candidate InjectionRules go in → connection headers are removed, matching sentinel headers are replaced with real credential values, and connection close is added → a rebuilt header block comes out.

**Call relations**: EgressProxy._mitm calls this when forwarding an inspected request directly to the upstream service.

*Call graph*: called by 1 (_mitm).


##### `_relay`  (lines 1095–1129)

```
async def _relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter, on_downstream: Callable[[bytes]
```

**Purpose**: Copies bytes in both directions between the sandbox and the upstream server. It keeps downloading a response even after the sandbox has finished uploading the request, as long as progress continues.

**Data flow**: Client streams, upstream streams, and an optional response-chunk callback go in → two pump tasks move bytes up and down, optionally reporting downstream chunks → the upstream writer is closed when relaying ends.

**Call relations**: EgressProxy._tunnel uses this for opaque tunnels. EgressProxy._mitm uses it for inspected requests, sometimes with HttpTokenUsage.feed as the downstream callback.

*Call graph*: calls 1 internal fn (_pump); called by 2 (_mitm, _tunnel); 7 external calls (Event, can_write_eof, close, write_eof, create_task, timeout, wait).


##### `_pump`  (lines 1132–1147)

```
async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, on_chunk: Callable[[bytes], None] | None=None, on_progress: Callable[[], None] | None=None) -> None
```

**Purpose**: Copies chunks from one stream reader to one stream writer. It is the simple one-way conveyor belt used by the two-way relay.

**Data flow**: A reader, writer, and optional callbacks go in → chunks are read, written, drained, and reported → copying stops on end-of-stream, cancellation, or socket error.

**Call relations**: _relay creates two _pump tasks: one for client-to-upstream traffic and one for upstream-to-client traffic.

*Call graph*: called by 1 (_relay); 3 external calls (read, drain, write).


##### `_int_field`  (lines 1150–1152)

```
def _int_field(usage: dict[str, object], name: str) -> int
```

**Purpose**: Safely reads an integer token-count field from a parsed JSON object. It treats missing values, non-integers, and booleans as zero.

**Data flow**: A usage dictionary and field name go in → the value is checked for being a real integer → that integer or zero comes out.

**Call relations**: HttpTokenUsage._absorb_anthropic and HttpTokenUsage._openai use this when turning provider usage JSON into the common Usage shape.

*Call graph*: called by 2 (_absorb_anthropic, _openai).


##### `HttpTokenUsage.feed`  (lines 1178–1215)

```
def feed(self, chunk: bytes) -> None
```

**Purpose**: Feeds raw HTTP response bytes into the token-usage parser. It understands response headers, chunked transfer, and common compression before looking for model usage data.

**Data flow**: A response byte chunk goes in → headers are accumulated until complete, body decoding is configured, then body bytes are passed onward → internal parser state is updated, with no immediate return value.

**Call relations**: EgressProxy._mitm passes this as the downstream callback to _relay for model hosts that need token metering. It calls _feed_wire_body once the HTTP headers are understood.

*Call graph*: calls 2 internal fn (_fail, _feed_wire_body); 1 external calls (decompressobj).


##### `HttpTokenUsage.usage`  (lines 1217–1228)

```
def usage(self) -> tuple[str, Usage] | None
```

**Purpose**: Returns the model name and token counts found in a response, if any. It finishes any pending decompression first so late-arriving usage data is not missed.

**Data flow**: The accumulator's stored response state is read → the decoder is flushed and any full JSON body is checked → it returns a model plus Usage object, or None if no usage was recognized.

**Call relations**: EgressProxy._meter_tokens calls this after relaying a model response.

*Call graph*: calls 2 internal fn (_finish_decoder, _maybe_json_body); 1 external calls (__init__).


##### `HttpTokenUsage._feed_wire_body`  (lines 1230–1276)

```
def _feed_wire_body(self, chunk: bytes) -> None
```

**Purpose**: Turns the response's wire-level body into payload bytes. It handles normal bodies and HTTP chunked transfer coding, where data is split into size-prefixed chunks.

**Data flow**: Body bytes from the network go in → chunk sizes and chunk boundaries are interpreted when needed → actual payload bytes are passed to _decode, or invalid framing triggers failure.

**Call relations**: HttpTokenUsage.feed calls this after headers are complete. It calls _decode for payload bytes and _finish_decoder when a final chunk is reached.

*Call graph*: calls 3 internal fn (_decode, _fail, _finish_decoder); called by 1 (feed).


##### `HttpTokenUsage._decode`  (lines 1278–1294)

```
def _decode(self, chunk: bytes) -> None
```

**Purpose**: Decompresses payload bytes when the response is gzip or deflate encoded. If the response is not compressed, it passes bytes through unchanged.

**Data flow**: Payload bytes go in → they are decompressed with a safety limit or passed directly → decoded body bytes are sent to _feed_body, while decompression errors mark parsing as failed.

**Call relations**: HttpTokenUsage._feed_wire_body calls this for each body payload segment.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 1 (_feed_wire_body).


##### `HttpTokenUsage._finish_decoder`  (lines 1296–1305)

```
def _finish_decoder(self) -> None
```

**Purpose**: Flushes the decompressor once the response body is complete. This captures any final plain bytes still buffered inside the decompression object.

**Data flow**: Current decoder state goes in → if not already finished, compressed tail data is flushed → final decoded bytes are passed to _feed_body, or failure is recorded.

**Call relations**: HttpTokenUsage.usage calls this before reporting results. HttpTokenUsage._feed_wire_body also calls it when chunked input reaches the terminating chunk.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 2 (_feed_wire_body, usage).


##### `HttpTokenUsage._feed_body`  (lines 1307–1318)

```
def _feed_body(self, chunk: bytes) -> None
```

**Purpose**: Accumulates decoded body text and sends complete lines to the usage parser. It also enforces a maximum buffer size so a huge response cannot consume unlimited memory.

**Data flow**: Decoded bytes go in → they are appended to an internal buffer, complete newline-delimited lines are consumed, and processed bytes are removed → parser state advances or fails on overflow.

**Call relations**: HttpTokenUsage._decode and _finish_decoder call this. It passes each complete line to HttpTokenUsage._consume.

*Call graph*: calls 2 internal fn (_consume, _fail); called by 2 (_decode, _finish_decoder).


##### `HttpTokenUsage._consume`  (lines 1320–1337)

```
def _consume(self, line: bytes) -> None
```

**Purpose**: Examines one decoded response line for provider usage data. It supports server-sent events, which are lines beginning with data:, and also tries plain JSON lines.

**Data flow**: One body line goes in → it is stripped, optionally parsed as JSON, and dispatched according to the provider host → internal model and token counters may be updated.

**Call relations**: HttpTokenUsage._feed_body calls this for each complete line. It delegates Anthropic events to _anthropic, OpenAI events to _openai, and plain JSON checks to _maybe_json_body.

*Call graph*: calls 3 internal fn (_anthropic, _maybe_json_body, _openai); called by 1 (_feed_body); 1 external calls (loads).


##### `HttpTokenUsage._maybe_json_body`  (lines 1339–1354)

```
def _maybe_json_body(self, payload: bytes) -> None
```

**Purpose**: Tries to parse a complete JSON response body for usage data. This covers non-streaming responses where usage appears in one JSON object instead of server-sent event lines.

**Data flow**: A byte payload goes in → if it looks like JSON, it is parsed and interpreted based on the host → internal usage fields may be filled.

**Call relations**: HttpTokenUsage.usage calls this as a final fallback, and HttpTokenUsage._consume calls it for non-SSE lines.

*Call graph*: calls 2 internal fn (_absorb_anthropic, _openai); called by 2 (_consume, usage); 1 external calls (loads).


##### `HttpTokenUsage._anthropic`  (lines 1356–1366)

```
def _anthropic(self, event: dict[str, object]) -> None
```

**Purpose**: Interprets Anthropic streaming events for model name and token usage. It knows which event types carry initial input counts and later output counts.

**Data flow**: A parsed Anthropic event dictionary goes in → message_start and message_delta events are examined → model and usage fields are updated through _absorb_anthropic.

**Call relations**: HttpTokenUsage._consume calls this when the metered host is Anthropic.

*Call graph*: calls 1 internal fn (_absorb_anthropic); called by 1 (_consume).


##### `HttpTokenUsage._absorb_anthropic`  (lines 1368–1378)

```
def _absorb_anthropic(self, usage: object, initial: bool) -> None
```

**Purpose**: Copies Anthropic usage fields into the accumulator's common token counters. It separates input, output, cache-read, and cache-write counts.

**Data flow**: An Anthropic usage object and a flag saying whether it is initial usage go in → integer fields are safely extracted → internal counters are set and the response is marked as having usage.

**Call relations**: HttpTokenUsage._anthropic and _maybe_json_body call this. It uses _int_field for safe integer extraction.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_anthropic, _maybe_json_body).


##### `HttpTokenUsage._openai`  (lines 1380–1389)

```
def _openai(self, event: dict[str, object]) -> None
```

**Purpose**: Interprets OpenAI response JSON for model name and token usage. It maps OpenAI's prompt and completion counts into the system's common Usage fields.

**Data flow**: A parsed OpenAI event dictionary goes in → model and usage fields are read → internal input and output token counters are updated when usage exists.

**Call relations**: HttpTokenUsage._consume and _maybe_json_body call this when the metered host is OpenAI. It uses _int_field for safe numeric reads.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_consume, _maybe_json_body).


##### `HttpTokenUsage._fail`  (lines 1391–1395)

```
def _fail(self) -> None
```

**Purpose**: Stops token parsing after malformed, unsupported, or oversized response data. Failing closed here means the proxy keeps relaying traffic but does not record questionable token usage.

**Data flow**: Current parser state goes in → the overflow/failure flag is set and buffers are cleared → later feed calls ignore more data and usage will not report parsed values.

**Call relations**: HttpTokenUsage.feed, _feed_wire_body, _decode, _finish_decoder, and _feed_body call this when they detect unsafe or invalid response data.

*Call graph*: called by 5 (_decode, _feed_body, _feed_wire_body, _finish_decoder, feed).


##### `_respond`  (lines 1398–1416)

```
async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None
```

**Purpose**: Writes a clear HTTP error or refusal response to a client. The reason is placed in the body so ordinary HTTP clients can show the useful message.

**Data flow**: A stream writer, status code, and message go in → an HTTP/1.1 response with content length and connection close is written and drained → no value is returned, and disconnected clients are ignored.

**Call relations**: EgressProxy._handle, _serve_workspace_credentials, _tunnel, _mitm, and _forward_broker use this whenever the proxy must refuse or fail a request.

*Call graph*: called by 5 (_forward_broker, _handle, _mitm, _serve_workspace_credentials, _tunnel); 3 external calls (drain, write, HTTPStatus).


### `core/src/ufo/sandbox/proxy/__init__.py`

`other` · `import/package discovery`

This is an empty Python package file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly lets the rest of the project find and refer to them by name.

Here, the package is `ufo.sandbox.proxy`, which suggests that nearby files are related to proxy behavior inside the sandbox part of the system. This file does not define functions, classes, settings, or startup work. Its value is structural: without it, depending on the Python version and packaging setup, imports that expect `ufo.sandbox.proxy` to be a regular package could fail or behave differently.


### `core/src/ufo/sandbox/proxy/rules.py`

`domain_logic` · `turn setup before sandbox network requests`

A sandbox should not be able to call any website or see raw secrets by default. This file is the rule-maker for the egress proxy, which is the gate that outgoing sandbox traffic must pass through. Think of it like a security desk in an office building: it knows which doors a visitor may use, whether a badge must be swapped for a real key behind the desk, and which visits should be counted for billing.

The file defines a few small rule types. A scope rule says which exact hosts are allowed. An internet rule says public internet is allowed for live turns. An injection rule says: if the sandbox sends a harmless placeholder value, replace it on the wire with the real secret so the sandbox never sees that secret. A meter rule says requests to a host should be counted under a spending category. A forward rule says a request should be executed through the broker, a trusted server-side service, instead of sending a credential from this deployment.

The derivation functions build these rules from different sources: the selected AI model, extension manifests, stored workspace credentials, active connector grants, and command-line connector credentials. Important behavior: missing or failed credentials usually produce no rule for that host instead of crashing the whole run, so a turn can continue if it never needs that credential.

#### Function details

##### `provider_host`  (lines 91–95)

```
def provider_host(model: str) -> str
```

**Purpose**: Finds which provider host belongs to a model name, such as mapping OpenAI-style model names to OpenAI's API host. It prevents the proxy from guessing where model traffic should go.

**Data flow**: It receives a model name as text. It checks known model-name prefixes one by one, and when the model starts with a known prefix, it returns the matching API host. If no prefix matches, it raises an error because the system does not know which provider should receive that model's requests.

**Call relations**: This is used by derive_model_rules when building the network rules for the chosen model. It hands back the host that later becomes both an allowed destination and the target for secret injection and metering.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 98–113)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: Builds the proxy rules needed for the sandbox to call the selected model provider safely. It allows the provider host, swaps the sandbox's placeholder API key for the real key, and marks model traffic for token-based metering.

**Data flow**: It takes a model name and the real provider API key. It first finds the provider host, then chooses the correct authentication header shape for that provider, such as a raw Anthropic key or an OpenAI Bearer token. It returns a small bundle of rules: allow that host, inject the real key in place of the sentinel value, and meter the host as token usage.

**Call relations**: This function calls provider_host to identify the destination. It then creates the rule objects the egress proxy will later read when sandbox model traffic is attempted.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 116–118)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: Decides whether extension manifests require general sandbox internet access. It only grants that broader internet rule when at least one manifest explicitly asks for it.

**Data flow**: It receives the manifests for the deployment. It looks for any manifest marked as needing sandbox internet. If one exists, it returns an InternetRule; otherwise it returns no rules.

**Call relations**: This function is part of the setup that turns extension declarations into proxy behavior. It creates an InternetRule only when the manifest data says live turns need public internet access.

*Call graph*: 1 external calls (__init__).


##### `derive_credential_rules`  (lines 121–173)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: Builds rules for workspace credentials declared by extensions, while keeping the real secret out of the sandbox. It allows the relevant host only when a usable credential and host are available.

**Data flow**: It receives credential slots, a workspace ID, and the credential store. For each slot that asks for header injection, it tries to fetch or mint the real secret for that workspace. If minting fails, no secret exists, or the chosen host cannot be resolved, it skips that slot and may log a warning. If the slot is usable, it creates an injection rule for the host and header. For Git Basic authentication, it formats the username and secret into the Basic header form. It groups rules by host, then returns allow rules, injection rules, and optional metering rules.

**Call relations**: This function talks to the credential system through slot_secret and credential_host. It logs warnings through warn when a credential cannot be used. The resulting rules are later read by the proxy so sandbox requests can reach credential-backed services without exposing raw credentials inside the sandbox.

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, credential_host, slot_secret, warn).


##### `derive_grant_rules`  (lines 176–193)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: Builds allow-and-meter rules for connector grants. A grant lets the sandbox reach the connector provider's host and any related file-transfer hosts, but it does not inject a secret because the broker keeps and uses that token server-side.

**Data flow**: It receives active grants and, optionally, a lookup object for connector transfer hosts. For each grant, it collects the provider host plus any extra file-store hosts. It removes empty and duplicate host values. For each grant with at least one host, it returns a scope rule allowing those hosts and meter rules counting requests to each host.

**Call relations**: This function may ask ConnectorTransferHosts.of for extra file-transfer hosts tied to a provider. It creates the rules that let granted connector traffic and connector file movement pass through the egress proxy while still being counted.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 196–216)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: Builds forwarding rules for connector command-line credentials. These rules send matching requests through the broker when the acting member is allowed to use the grant.

**Data flow**: It receives active grants, the acting member ID if there is one, and the available CLI credential declarations. For each grant with a matching CLI credential, it checks whether the grant is shared or belongs to the acting member. If allowed, it creates a forward rule using the grant's host, the CLI header, a sentinel value for that account, the account ID, and the broker forwarding function.

**Call relations**: This function uses grant_sentinel to create the placeholder value the sandbox will carry. It then creates ForwardRule objects so the proxy can recognize those requests and hand them to the broker instead of trying to inject a local secret.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 230–231)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Looks up which file-transfer hosts should be allowed for a connector provider. It uses provider-specific hosts when known, otherwise it falls back to the open connector namespace default.

**Data flow**: It receives a provider name. It checks the explicit provider-to-hosts mapping. If the provider is present, it returns that provider's tuple of hosts, even if it is empty. If the provider is not present, it returns the default hosts.

**Call relations**: derive_grant_rules calls this when adding extra file-store hosts for a grant. This keeps grant rule creation simple while preserving the distinction between registered connectors and open-namespace connectors.


##### `connector_transfer_hosts`  (lines 234–245)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: Builds the lookup table used to find connector file-transfer hosts. It gathers declared hosts from registered connectors and also records default hosts from the open connector namespace.

**Data flow**: It receives deployment manifests. It walks through every connector declared in those manifests and maps each connector provider to its declared transfer hosts. It then asks for the open connector namespace and, if one exists, uses its transfer hosts as the default. It returns a ConnectorTransferHosts object containing both pieces.

**Call relations**: This function calls open_connector_namespace to discover the fallback namespace. Its result is passed into derive_grant_rules so grant-derived network access includes the right broker file-store hosts.

*Call graph*: 2 external calls (__init__, open_connector_namespace).


### Workspace security boundaries
Workspace identity, extension execution context, file-scoped sandbox credentials, and signed tokens prevent cross-workspace or overbroad access.

### `core/src/ufo/workspace.py`

`orchestration` · `cross-cutting during workspace-bound request or job execution`

This file is a safety wrapper around anything that depends on a workspace. A workspace is like a named room: once code enters that room with `ws(workspace_id)`, every credential lookup, database transaction, and billable model call inside the block is tied to that same room. Without this, code would need to pass the workspace ID everywhere by hand, which is easy to forget and dangerous in a multi-customer system.

The file keeps a credential store installed at startup. When code asks for a secret, it must do so through the current workspace. The lookup first tries that workspace’s own stored key, often called BYOK, meaning “bring your own key.” If there is no workspace-specific key, it falls back to a platform-wide environment variable. If neither exists, it fails loudly instead of guessing.

It also records spending through `billable_event()`. Code collects usage during a successful block, and only writes the charges after the block finishes cleanly. If an error happens, the usage is discarded, so failed work is not billed.

The `ws()` context manager sets the active workspace for a block, and `ws_current()` retrieves it. If no workspace has been set, `ws_current()` raises an error. This is intentional: it prevents silent cross-workspace reads, unscoped database access, or unbilled work.

#### Function details

##### `init_workspace_credentials`  (lines 29–33)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that this process should use when looking up workspace-specific secrets. It is meant to be called once during startup so later workspace code can resolve customer-provided keys.

**Data flow**: It receives either a credential store object or `None`. It saves that value in this module’s shared `_store` variable. After that, credential lookups will use the store if one was provided, or fall back only to environment variables if not.

**Call relations**: This is part of application setup. Later, `WorkspaceScope.credential`, `WorkspaceScope.rotate_credential`, and `WorkspaceScope.put_credential` all consult the stored value that this function installed.


##### `BillableEvent.usage`  (lines 49–51)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: Adds one metered model call to a billable event. Code uses it while work is running to say, in effect, “this model was used this much, at this price.”

**Data flow**: It receives a model name, a usage record, and optionally a pricing table. It appends those three pieces to the event’s internal list. Nothing is written to the billing ledger yet; it is only remembered for later.

**Call relations**: This is used inside a `WorkspaceScope.billable_event` block. The billable event collects usage through this method, and when the block succeeds, `WorkspaceScope.billable_event` reads the collected entries and records them for the workspace.


##### `WorkspaceScope.credential`  (lines 60–74)

```
async def credential(self, slot: str, env: str | None=None) -> str
```

**Purpose**: Returns the secret value for a named credential slot in this workspace. It enforces the rule that secrets are always fetched through a bound workspace, never as loose global values.

**Data flow**: It takes a slot name, such as the name of an API key, and optionally the name of an environment variable to use as a fallback. It first asks the configured credential store for this workspace’s value. If that slot is not set, or no store exists, it reads the fallback environment variable. If no value is found or the value is empty, it raises `CredentialSlotUnset` instead of returning a fake or blank secret.

**Call relations**: Code reaches this through `ws_current().credential(...)` after a workspace has been bound by `ws(...)`. It may call the credential store, and when the slot is missing it uses `CredentialSlotUnset` to make the missing configuration explicit.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceScope.rotate_credential`  (lines 76–81)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing workspace credential only if the caller’s expected current value matches. This compare-and-swap style protects against overwriting a secret that changed underneath the caller.

**Data flow**: It receives a slot name, the expected existing secret, and the new plaintext secret. If no credential store is configured, it returns `false` because there is nowhere to rotate a workspace-specific key. If a store exists, it asks the store to rotate the credential for this workspace and returns whether that succeeded.

**Call relations**: This belongs to an already-bound `WorkspaceScope`, usually obtained from `ws(...)` or `ws_current()`. It delegates the actual storage and comparison work to the credential store installed by `init_workspace_credentials`.


##### `WorkspaceScope.put_credential`  (lines 83–87)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores an initial credential value for this workspace. It is used when an authorized owner adds a workspace-specific secret for the first time.

**Data flow**: It receives a slot name and a plaintext secret. If no credential store is configured, it raises an error because the system has no secure place to save the secret. Otherwise, it passes the workspace ID, slot, and plaintext secret to the credential store.

**Call relations**: This method is called on a `WorkspaceScope`, so the workspace ID is already known. It relies on the credential store previously installed through `init_workspace_credentials` to do the actual save.


##### `WorkspaceScope.billable_event`  (lines 90–100)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a safe billing block for this workspace. Usage can be collected while work happens, and charges are written only if the whole block finishes without an error.

**Data flow**: It creates an empty `BillableEvent` and gives it to the caller’s block. The caller adds usage records to that event. When the block exits successfully, it opens a workspace database transaction and records each collected usage item for this workspace. If no usage was added, it does nothing. If the block raises an exception, the code after the yield does not run, so nothing is billed.

**Call relations**: Callers use this as `async with ws_current().billable_event() as bill`. The event’s `usage` method gathers items, then this function opens `workspace_tx` and hands each item to `record_workspace_usage` so the spend is written to the workspace ledger.

*Call graph*: 3 external calls (__init__, record_workspace_usage, workspace_tx).


##### `ws`  (lines 104–112)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Binds a workspace ID to a block of code. Everything inside that block can find the active workspace without passing the ID through every function call.

**Data flow**: It receives a workspace ID and stores it in the current execution context. It yields a `WorkspaceScope` for that ID to the caller. When the block finishes, even if it exits because of an error, it restores the previous workspace context so the binding does not leak into later work.

**Call relations**: This is normally used at the boundary of a request, turn, or background job. Inside the block, `ws_current()` can recover the same workspace, database helpers can use the same context, and credential or billing operations stay tied to that workspace.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 115–121)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the workspace scope currently bound to this execution. It is the standard way for deeper code to find out which workspace it is operating under.

**Data flow**: It reads the current workspace ID from the shared execution context. If an ID is present, it wraps it in a `WorkspaceScope` and returns it. If no workspace has been bound, it raises `WorkspaceUnbound` with a clear message telling the caller to use `ws(workspace_id)`.

**Call relations**: Code calls this from inside a `ws(...)` block when it needs credentials, billing, or other workspace-scoped behavior. If someone calls it outside that block, it fails immediately so the system does not accidentally use the wrong workspace or proceed without one.

*Call graph*: 3 external calls (__init__, __init__, get).


### `core/src/ufo/ext/context.py`

`domain_logic` · `cross-cutting during extension jobs, background tasks, and handler execution`

Extensions need to read and write state, use credentials, call models, inspect synced content, and sometimes propose changes. This file gives them those powers through narrow doors instead of handing them the whole building key. The central idea is an ExtensionContext: a bundle of carefully scoped helpers tied to the workspace currently running. The current workspace is taken from ambient runtime state, so the same context object can be reused safely while the dispatcher binds each job or turn to the right workspace.

The file contains small access wrappers. ScopedStore is a private key-value shelf for one extension inside one workspace. CredentialAccess only opens credential slots the extension declared ahead of time. TrajectoryCorpus reads recent conversation transcripts, but only for the current workspace and only through a read-only path. ModelAccess lets background code call the configured language model while billing usage to the same workspace. ExtensionContext also exposes approved paths for usage export, source registration, synced page reads, source removal, page forgetting, scheduled turn invocation, and governed prompt-change proposals.

A useful analogy is a hotel key card: the handler does not receive the master key. It receives a card that opens only its room, only the facilities it booked, and only while it is checked into the current workspace.

#### Function details

##### `ScopedStore.workspace_id`  (lines 74–75)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id that is currently bound to this running job or handler. This lets the store stay tied to the active workspace without keeping a separate workspace id inside the object.

**Data flow**: It reads the current workspace from runtime context → takes that workspace's id → returns it as the id to use in store queries.

**Call relations**: Other ScopedStore methods call on this property whenever they read or write extension data, so every operation is automatically aimed at the workspace currently being served.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 77–88)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one saved JSON-like value from this extension's private store. It is used when an extension needs durable memory such as a checkpoint, setting, or small state record.

**Data flow**: It receives a key → opens a workspace-scoped database transaction → searches for a row matching the current workspace, this extension name, and that key → returns the saved value, or None if there is no row.

**Call relations**: Extension code reaches this through ExtensionContext.store. It relies on workspace_tx for the database boundary and on workspace_id so the lookup cannot drift into another workspace.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 90–111)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Saves or replaces one JSON-like value in this extension's private store. This is the basic durable write path for extension-local state.

**Data flow**: It receives a key and value → tries to update an existing row for the current workspace and extension → if no row exists, inserts a new one with timestamps → returns nothing after the database transaction commits.

**Call relations**: Extension handlers use it through ExtensionContext.store. It uses the same workspace transaction path as get, so reads and writes share the same workspace safety boundary.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `ScopedStore.delete`  (lines 113–121)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes one key from this extension's private store. It is used when an extension wants to clear state it no longer needs.

**Data flow**: It receives a key → opens a workspace-scoped database transaction → deletes the row matching the current workspace, extension, and key → returns nothing whether or not a row existed.

**Call relations**: This is the cleanup companion to ScopedStore.get and ScopedStore.put, and it follows the same current-workspace scoping rules.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 123–136)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists this extension's stored keys and values, optionally limited to keys that start with a prefix. This is useful for extensions that keep several related records under a naming pattern.

**Data flow**: It receives an optional prefix → queries the store table for rows in the current workspace and extension whose keys begin with that prefix → sorts them by key → returns a tuple of key/value pairs.

**Call relations**: Extension code calls it through ExtensionContext.store when it needs to scan its own saved state. The database access stays behind workspace_tx and the extension name.

*Call graph*: 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 150–151)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id currently in effect for credential operations. This keeps credential reads and writes tied to the workspace being handled.

**Data flow**: It reads the current workspace from runtime context → extracts the workspace id → returns it.

**Call relations**: CredentialAccess.bind_installation uses this id when sealing an installation credential, and all CredentialAccess methods rely on the same current-workspace idea.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 153–159)

```
async def get(self, slot: str) -> str
```

**Purpose**: Fetches the live secret for a declared credential slot. It refuses to even look up a secret if the extension did not declare that slot in its manifest.

**Data flow**: It receives a slot name → checks whether that slot is in the declared set → if not, raises UndeclaredCredentialSlot → otherwise asks the current workspace for the credential value → returns the secret string.

**Call relations**: Handlers use this through ExtensionContext.credentials. The method is the guardrail between extension code and workspace secrets.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.rotate`  (lines 161–166)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Updates a declared credential slot after an outside service rotates its secret, using a compare-and-swap check. Compare-and-swap means it only changes the secret if the currently stored value matches what the caller expected.

**Data flow**: It receives a slot name, the expected old value, and the new plaintext value → checks the slot was declared → asks the current workspace to rotate the credential only if the expected value matches → returns True or False for whether the rotation happened.

**Call relations**: This is another CredentialAccess gate used by handlers that maintain external provider credentials. It delegates the actual credential update to the current workspace object.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 168–181)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores a provider installation as a sealed credential value for a declared slot. Sealing means the installation id is wrapped so it only makes sense for this workspace and slot.

**Data flow**: It receives a slot and installation id → checks the slot was declared → obtains the installation sealing key → seals the workspace id, slot, and installation id together → writes that sealed value into the current workspace credential slot.

**Call relations**: Extension onboarding or installation flows use this after proving a member can access the provider installation. It combines credential declaration checks, installation sealing, and workspace credential storage.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 214–215)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the current workspace id for transcript-corpus reads. The corpus never carries its own workspace id; it follows the workspace bound to the running job.

**Data flow**: It reads the current workspace from runtime context → extracts the workspace id → returns it for transcript queries.

**Call relations**: TrajectoryCorpus.trajectories uses this property to find only conversations belonging to the active workspace.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 217–268)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for the current workspace and turns them into evaluation examples. It skips missing or corrupt transcripts instead of failing the whole read.

**Data flow**: It finds recent conversations in the database that have turns → joins each to its agent and prompt → for each conversation, fetches the transcript blob → decodes the transcript → builds Trajectory objects containing ids, the agent prompt, a prompt digest, and messages → returns all successfully decoded trajectories.

**Call relations**: ExtensionContext.trajectories delegates to this when a handler asks for transcripts. It uses database queries for metadata, blob storage for transcript bodies, transcript decoding for messages, and logging when a transcript is corrupt.

*Call graph*: 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `trajectory_workspaces`  (lines 271–293)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate-workspace selector for jobs that need transcript data. It identifies workspaces that actually have at least one conversation with at least one turn.

**Data flow**: It creates a nested query builder → wraps it with owner_candidates, which is the system's controlled way to enumerate workspaces from owner-level data → returns a WorkspaceCandidates object for dispatchers to use.

**Call relations**: Extensions that read trajectories can declare this as their workspace candidate source. The dispatcher later binds each candidate workspace, and TrajectoryCorpus then reads within that workspace scope.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 279–291)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Defines the database query used by trajectory_workspaces to find eligible workspaces. A workspace is eligible only if it has a conversation that has at least one turn.

**Data flow**: It starts from the workspace table → adds existence checks for a conversation in that workspace and a turn in that conversation → returns a SQL select statement that yields matching workspace ids.

**Call relations**: This inner query is handed to owner_candidates by trajectory_workspaces. It is intentionally kept in core code because core owns the conversation and turn tables.

*Call graph*: 2 external calls (exists, select).


##### `TurnInvoker.invoke`  (lines 300–302)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Describes the interface for starting an internal assistant turn from background code. It is a protocol, meaning it states what a compatible object must provide without implementing it here.

**Data flow**: A compatible invoker receives a conversation id, agent id, message, and idempotency key → it should admit or reuse a turn safely → it returns the new or existing turn id.

**Call relations**: ExtensionContext.invoke calls this protocol when an invoker has been wired in. The actual implementation lives outside this file.


##### `ModelResolver.auto_model`  (lines 312–312)

```
def auto_model(self) -> str
```

**Purpose**: Describes the property that tells ModelAccess which default model this deployment should use. It is part of the model registry protocol.

**Data flow**: A compatible resolver exposes a model name string → ModelAccess reads it → model calls are fixed to that name.

**Call relations**: ModelAccess.model and ModelAccess.turn depend on this property so background model calls use the deployment default rather than an arbitrary model chosen by the handler.


##### `ModelResolver.pricing`  (lines 315–315)

```
def pricing(self) -> Pricing
```

**Purpose**: Describes the property that provides the model price table used for billing. Pricing here means the rules for turning token usage into billable cost.

**Data flow**: A compatible resolver exposes Pricing data → ModelAccess.turn combines it with measured token usage → the workspace billing event records the cost basis.

**Call relations**: ModelAccess.turn reads this after a model stream finishes so the same model call can be billed consistently.


##### `ModelResolver.client_for`  (lines 317–317)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Describes how to obtain a model client for a named model. A model client is the object that actually talks to the language-model provider.

**Data flow**: A compatible resolver receives a model name → chooses the right provider client, usually using the current workspace's credential if available → returns that client.

**Call relations**: ModelAccess.turn calls this before streaming a completion. The implementation is supplied by the core model registry outside this file.


##### `ModelResolver.key_slot_for`  (lines 319–319)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Describes how to map a model name to the credential slot that supplies its bring-your-own-key secret, if any. Bring-your-own-key means the workspace provides its own provider API key instead of using the platform default.

**Data flow**: A compatible resolver receives a model name → returns a credential slot name or None → usage export code can label whether usage came from a workspace-owned key.

**Call relations**: context_for wires this function into ExtensionContext when a model resolver is available, and ExtensionContext.pending_usage_exports uses it while minting export records.


##### `ModelAccess.model`  (lines 335–337)

```
def model(self) -> str
```

**Purpose**: Returns the default model name that this ModelAccess will call and bill. It gives handlers a simple way to see which model is fixed for their background completions.

**Data flow**: It reads auto_model from the resolver → returns that model name unchanged.

**Call relations**: This property is a thin view over the ModelResolver. ModelAccess.complete and ModelAccess.turn use the same underlying default model.


##### `ModelAccess.complete`  (lines 339–346)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one language-model completion and returns only the assistant's text. It is convenient for extension code that does not care about tool-call blocks.

**Data flow**: It receives a ModelRequest → calls ModelAccess.turn to do the streamed, metered model call → if the response content is plain text, returns it → otherwise joins the text blocks and returns the combined text.

**Call relations**: Memory extension code calls this for fact extraction and summarization. It delegates all real model streaming and billing work to ModelAccess.turn.

*Call graph*: calls 1 internal fn (turn); called by 2 (_extract, _summarize).


##### `ModelAccess.turn`  (lines 348–396)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs a full assistant turn against the default model, preserving both text and tool calls, and records usage for billing. This is the safe background path for model access.

**Data flow**: It receives a ModelRequest → replaces its model with the deployment default → gets the provider client → streams events from the client → collects text pieces, tool-call names and JSON input, and usage records → requires at least one usage record → bills the summed token usage to the current workspace → returns an assistant Message containing either text alone or text plus tool-use blocks.

**Call relations**: ModelAccess.complete calls this when only text is needed, and the knowledge-graph extension calls it when it needs the richer assistant message. It ties together the resolver, provider stream, JSON parsing for tool inputs, and workspace billing.

*Call graph*: called by 2 (complete, _tier_b); 7 external calls (__init__, __init__, __init__, __init__, model_copy, loads, ws_current).


##### `ExtensionContext.pending_usage_exports`  (lines 453–475)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns this extension's settled but not-yet-acknowledged usage export records. Before reading, it mints any new export intents that are ready under the requested time floor.

**Data flow**: It receives a floor time and maximum count → verifies that model key-slot labeling was wired → opens a workspace transaction → mints export records for this workspace and extension → reads up to the requested limit of pending exports → returns them.

**Call relations**: Billing-export extensions call this to deliver usage to an outside receiver. It uses accounting helpers inside the same transaction so newly minted and read exports are consistent.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 477–486)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered after an external receiver has accepted them. Unacknowledged exports remain pending so they can be retried safely.

**Data flow**: It receives a tuple of exports → if the tuple is empty, does nothing → otherwise opens a workspace transaction → records acknowledgements for this workspace, extension, and those exports → returns nothing.

**Call relations**: This is the second half of the usage-export flow after pending_usage_exports. Exporter code should call it only after successful delivery.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 489–501)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides an explicit database transaction for an extension's own tables and approved SDK operations. It is powerful and therefore documented as not automatically restricted beyond the current workspace transaction setup.

**Data flow**: A caller enters the async context manager → the function opens workspace_tx and yields the database connection → caller runs its SQL → on normal exit the transaction commits, and on error it rolls back.

**Call relations**: Extension code uses this when key-value storage is not enough and it needs its own schema. It shares the same transaction mechanism used by ScopedStore.

*Call graph*: 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 503–512)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Starts an internal turn in a conversation through a wired TurnInvoker. It fails clearly if no invoker is available instead of silently dropping the request.

**Data flow**: It receives conversation id, agent id, message, and idempotency key → checks that an invoker is present → passes all four values to the invoker → returns the turn id produced or reused by that invoker.

**Call relations**: Background handlers call this through ExtensionContext when they need the system to continue a conversation. The real turn admission logic is supplied by the wired invoker.


##### `ExtensionContext.register_source`  (lines 514–576)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None) -> UUID
```

**Purpose**: Registers a live content-sync source for this workspace, such as a connected account or folder. It is idempotent for the same workspace, backend, and configuration, so repeated setup does not create duplicate sync rows.

**Data flow**: It receives a backend name, typed config object, visibility subject, and optional owner member id → serializes the config to JSON-like data → computes the stable source id → checks whether that source already exists → returns it if live with the same subject, revives it if removed, or inserts it if new → raises if the same source is live under a different subject.

**Call relations**: Sample and YC extension setup code call this during onboarding or source setup. The core sync driver later polls the registered source rows and writes synced pages.

*Call graph*: called by 2 (_setup, setup_sources); 7 external calls (now, model_dump, insert, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.sources`  (lines 578–616)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists this workspace's live registered content sources, optionally only for one backend. Removed sources are hidden.

**Data flow**: It receives an optional backend filter → builds a query scoped to the current workspace and non-removed sources → reads rows from the database → converts each row into a SourceRecord → returns the records as a tuple.

**Call relations**: The sources extension uses this to build bindings from current registrations. It is the read-side companion to register_source and remove_source.

*Call graph*: called by 1 (_bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 618–663)

```
async def source_pages(self, subjects: frozenset[str] | None=None) -> tuple[PageRecord, ...]
```

**Purpose**: Lists this workspace's live synced pages, optionally limited to specific visibility subjects. It returns page metadata and blob references, not the full page bodies.

**Data flow**: It receives an optional set of subjects → queries non-tombstoned pages in the current workspace → applies the subject filter if supplied → converts rows into PageRecord objects → returns them as a tuple.

**Call relations**: Extensions use this sanctioned read path when they need to inspect synced content. Later operations such as forget_page or set_source_subject can change which pages remain visible or how they are classified.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 665–681)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten by tombstoning it. Tombstoning means the row remains as a record, but downstream indexing can remove its derived searchable chunks.

**Data flow**: It receives a page id → records the current time → updates the matching non-tombstoned page in this workspace to tombstone=true → if no row was updated, raises an error → otherwise returns nothing.

**Call relations**: This is the write-side companion to source_pages for a single page. The page-change pipeline can notice the tombstone and clean up derived index data.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 683–714)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a registered source and tombstones all of its live pages in one transaction. This stops future sync claims while preserving the source row as the page reference.

**Data flow**: It receives a source id → records the current time → marks the live source in this workspace as removed and clears any sync claim → raises if no live source matched → tombstones all live pages belonging to that source → returns nothing.

**Call relations**: This is the source-level cleanup path paired with register_source and sources. Downstream page-change processing handles removal of indexed content after pages are tombstoned.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 716–742)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the visibility subject for one or more live sources and restamps their live pages to match. A subject is the disclosure label that says who should be allowed to see synced content.

**Data flow**: It receives source ids and a new subject → records the current time → updates matching live sources in this workspace → raises if none matched → updates live pages from those sources with the new subject and timestamp → returns nothing.

**Call relations**: Extensions use this when a binding's visibility changes. Updating sources and pages together avoids a half-changed state, and fresh page timestamps let indexing replay the visibility change.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 744–750)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Submits a governed proposal to change an agent prompt instead of directly editing it. Governance here means the change must go through approval and digest checks before it can be applied.

**Data flow**: It receives an AgentChange → creates a Governance object for the current workspace and this extension as proposer → asks it to open the proposal → returns the ProposalRef that identifies the proposal.

**Call relations**: The sample extension calls this during its tick flow. It is the safe path from extension-suggested agent changes into the system's approval process.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 752–757)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns this workspace's transcript corpus for evaluation or learning jobs. It raises an error if transcript access was not wired, so a missing corpus is not mistaken for an empty history.

**Data flow**: It checks whether a TrajectoryCorpus is present → if not, raises RuntimeError → otherwise calls the corpus to read trajectories → returns the resulting tuple.

**Call relations**: The sample extension calls this in its tick flow. This method is a small guard and delegation layer over TrajectoryCorpus.trajectories.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 760–789)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: BlobStore | None=None, invoker: TurnInvoke
```

**Purpose**: Builds the ExtensionContext object that a handler receives. It assembles only the capabilities that were declared or wired for this extension.

**Data flow**: It receives the extension name, declared credential slots, optional index/embed/page/blob/model/invoker/scheduler/surface pieces → creates a ScopedStore and CredentialAccess → creates optional corpus and model access when their backing services are provided → creates installation and scheduling access → returns a fully assembled ExtensionContext.

**Call relations**: The dispatcher or serving layer calls this when preparing extension and core-job handlers. It is the factory that turns system services into the narrow, workspace-scoped toolbox used throughout this file.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `core/src/ufo/sandbox/fs_creds.py`

`domain_logic` · `sandbox mount setup and credential refresh`

A sandbox needs file access, but it should not receive broad storage keys. This file solves that by minting temporary S3-style credentials that are locked to one narrow folder-like prefix: `conversations/<conversation_id>/workspace/`. Think of it like issuing a hotel key card that opens only one room, and only for a short time.

The flow has two layers. First, the server creates a signed token for a live sandbox turn. The token is opaque to the sandbox and can later be presented to a proxy when the mounted filesystem needs fresh credentials. Second, when that token is redeemed, `SandboxFsCredentialMinter` checks that it is authentic and still allowed, then asks STS, meaning Security Token Service, for temporary credentials with an inline policy that limits what those credentials can do.

The file also supports a short deploy-time “gate” token, used for setup checks rather than a normal agent turn. It creates a marker object for an empty workspace prefix because the mounted filesystem can fail if the prefix has no object at all. The important security idea is that every credential is scoped to the workspace prefix only, so even a leaked credential cannot reach the framework-owned records above it.

#### Function details

##### `SandboxFsCredentials.ecs_json`  (lines 48–58)

```
def ecs_json(self) -> bytes
```

**Purpose**: Turns the temporary storage credentials into the JSON shape expected by an ECS-style credentials endpoint. ECS here means Amazon Elastic Container Service; tools such as filesystem mounters know how to read credentials in this format.

**Data flow**: It starts with a `SandboxFsCredentials` object containing an access key, secret key, session token, expiration time, and role name. It formats those fields with the names expected by the credential consumer, converts the expiration time to a UTC-looking string ending in `Z`, serializes everything as compact JSON, and returns the bytes to send over an HTTP response or similar channel.

**Call relations**: After credentials have been minted, this method is the final packaging step for consumers that expect ECS-compatible credential JSON. It does not mint or validate anything itself; it only converts already-approved credentials into the wire format.

*Call graph*: 1 external calls (dumps).


##### `StsClient.assume_role`  (lines 86–88)

```
async def assume_role(self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int) -> Any
```

**Purpose**: Defines the small interface needed from any STS client: ask for temporary credentials for a role, with a policy and duration. It lets the rest of the file work with AWS STS, MinIO STS, or a test double in the same way.

**Data flow**: A caller provides a role identifier, a session name, a policy describing allowed storage access, and how long the credentials should last. An implementation sends that request to an STS service and returns the service’s response.

**Call relations**: This protocol is what `SandboxFsCredentialMinter._mint` relies on when it needs credentials. `AwsStsClient.assume_role` is the real implementation used in production, while tests or other deployments can provide another object with the same method.


##### `workspace_key_prefix`  (lines 91–96)

```
def workspace_key_prefix(conversation_id: UUID) -> str
```

**Purpose**: Builds the exact blob-storage prefix that represents one conversation’s sandbox workspace. This is the safe area the sandbox is allowed to mount and modify.

**Data flow**: It takes a conversation UUID and turns it into a string like `conversations/<id>/workspace`. That string is then used as the base path for marker objects and for the access policy that limits temporary credentials.

**Call relations**: When `ensure_workspace_marker` needs to create the workspace marker, it calls this to find the right prefix. When `SandboxFsCredentialMinter._mint` creates a scoped credential, it calls this so the STS policy covers only that workspace.

*Call graph*: called by 2 (_mint, ensure_workspace_marker).


##### `ensure_workspace_marker`  (lines 99–108)

```
async def ensure_workspace_marker(blob: BlobStore, conversation_id: UUID) -> str
```

**Purpose**: Creates an empty marker object for a conversation’s workspace prefix before the sandbox filesystem mounts it. This avoids a mount startup failure that can happen when the prefix is completely empty.

**Data flow**: It receives a blob store connection and a conversation ID. It builds the workspace prefix, appends a trailing slash to make a directory-like marker key, writes empty bytes at that key, and returns the key it wrote so the caller can later remove it if needed.

**Call relations**: This function uses `workspace_key_prefix` to stay consistent with the credential policy. It writes through `BlobStore.put`, so it is part of setup before the sandbox filesystem tries to use the prefix.

*Call graph*: calls 2 internal fn (put, workspace_key_prefix).


##### `workspace_prefix_policy`  (lines 111–140)

```
def workspace_prefix_policy(bucket: str, key_prefix: str) -> str
```

**Purpose**: Creates the STS policy that confines a temporary credential to one workspace prefix in one bucket. This is the core safety barrier that keeps a sandbox from seeing files outside its own workspace.

**Data flow**: It takes a bucket name and key prefix. It produces a compact JSON policy that allows reading, writing, and deleting objects only under that prefix, and allows bucket listing only when the requested listing is for that same prefix.

**Call relations**: `SandboxFsCredentialMinter._mint` calls this right before asking STS for credentials. The generated policy is handed to STS so the returned keys are limited from the moment they are issued.

*Call graph*: called by 1 (_mint); 1 external calls (dumps).


##### `issue_sandbox_fs_gate_token`  (lines 143–150)

```
def issue_sandbox_fs_gate_token(conversation_id: UUID, token_secret: bytes, expires_at: datetime) -> str
```

**Purpose**: Creates a short-lived signed token for deploy or setup checks that need to redeem sandbox filesystem credentials without a normal live turn. It is a controlled exception path with its own expiration time.

**Data flow**: It receives a conversation ID, a signing secret, and an expiration time. It builds gate claims containing the conversation and expiration timestamp, signs those claims with the secret, and returns the resulting token string.

**Call relations**: Later, `SandboxFsCredentialMinter.refresh` can accept this kind of token. Instead of checking a live run, refresh checks whether the gate token has expired before minting credentials.

*Call graph*: 3 external calls (__init__, timestamp, sign_token).


##### `_utc_now`  (lines 153–154)

```
def _utc_now() -> datetime
```

**Purpose**: Returns the current time in UTC, meaning the standard global time zone. It exists so time checks can be injected or replaced in tests.

**Data flow**: It reads the system clock, asks for the current UTC time, and returns a timezone-aware `datetime` value.

**Call relations**: `SandboxFsCredentialMinter` uses this as its default clock. `SandboxFsCredentialMinter.refresh` relies on that clock when deciding whether a deploy gate token is still valid.

*Call graph*: 1 external calls (now).


##### `AwsStsClient.assume_role`  (lines 166–175)

```
async def assume_role(self, *, RoleArn: str, RoleSessionName: str, Policy: str, DurationSeconds: int) -> Any
```

**Purpose**: Implements the real STS request that asks AWS or MinIO for temporary credentials. It wraps the network client so the minter does not need to know the details of talking to STS.

**Data flow**: It receives a role, session name, access policy, and duration. It opens an STS client, sends an `assume_role` request with those values, waits for the service response, and returns that response to the caller.

**Call relations**: This is the production implementation of the `StsClient` protocol. `SandboxFsCredentialMinter._mint` calls through this interface when it needs fresh scoped credentials, and this method uses `AwsStsClient._client` to create the actual service client.

*Call graph*: calls 1 internal fn (_client).


##### `AwsStsClient._client`  (lines 177–180)

```
def _client(self) -> ClientCreatorContext
```

**Purpose**: Creates an async STS client configured for the deployment’s endpoint and region. This is the small factory that knows how to connect to AWS STS or a MinIO-compatible STS endpoint.

**Data flow**: It reads the client object’s endpoint URL and region. It asks `aiobotocore` for a session and creates an STS client, defaulting to `us-east-1` if no region was configured.

**Call relations**: `AwsStsClient.assume_role` calls this each time it needs to contact STS. Keeping client creation here keeps the request code simple and centralizes endpoint and region choices.

*Call graph*: called by 1 (assume_role); 1 external calls (get_session).


##### `SandboxFsCredentialMinter.issue`  (lines 201–211)

```
def issue(self, conversation_id: UUID, run: RunToken) -> str
```

**Purpose**: Creates the signed token that represents permission to refresh filesystem credentials for one live sandbox turn. The token names the conversation, workspace, and turn, but cannot be forged without the secret.

**Data flow**: It receives a conversation ID and a `RunToken`, which identifies the current workspace and turn. It packages those values into turn claims, signs the JSON claims with the configured secret, and returns the signed token string.

**Call relations**: The workspace mount setup code calls this when preparing a sandbox. Later, when the mounted filesystem needs credentials, `SandboxFsCredentialMinter.refresh` verifies and redeems this token.

*Call graph*: called by 1 (_workspace_mount); 2 external calls (__init__, sign_token).


##### `SandboxFsCredentialMinter.refresh`  (lines 213–228)

```
async def refresh(self, token: str, authorize: Callable[[RunToken], Awaitable[bool]]) -> SandboxFsCredentials
```

**Purpose**: Verifies a sandbox filesystem token and, if it is still allowed, turns it into fresh temporary storage credentials. This is the main checkpoint between an untrusted sandbox and real blob storage access.

**Data flow**: It receives a token string and an authorization callback. It verifies the token signature and parses its claims. For a normal turn token, it rebuilds the `RunToken` and asks the callback whether that run is still live. For a deploy gate token, it checks the expiration time. If the token fails any check, it raises `InvalidSandboxFsToken`; otherwise it calls `_mint` and returns scoped credentials.

**Call relations**: This function is called when credentials are being redeemed or refreshed. It performs the trust decision first, then hands off to `SandboxFsCredentialMinter._mint` only after the token has been accepted.

*Call graph*: calls 1 internal fn (_mint); 3 external calls (__init__, __init__, verify_token).


##### `SandboxFsCredentialMinter._mint`  (lines 230–244)

```
async def _mint(self, conversation_id: UUID) -> SandboxFsCredentials
```

**Purpose**: Asks STS for new temporary credentials that are restricted to one conversation’s workspace prefix. This is where an approved token becomes usable, limited storage access.

**Data flow**: It receives a conversation ID. It builds the workspace prefix, builds the prefix-only access policy, sends an STS assume-role request with the configured role and credential lifetime, then extracts the returned keys and expiration time into a `SandboxFsCredentials` object.

**Call relations**: `SandboxFsCredentialMinter.refresh` calls this after token validation succeeds. This method depends on `workspace_key_prefix` and `workspace_prefix_policy` to make sure the STS request is scoped to the right storage path.

*Call graph*: calls 2 internal fn (workspace_key_prefix, workspace_prefix_policy); called by 1 (refresh); 1 external calls (__init__).


##### `sandbox_fs_minter`  (lines 247–265)

```
def sandbox_fs_minter(blob: BlobConfig) -> SandboxFsCredentialMinter | None
```

**Purpose**: Builds a ready-to-use `SandboxFsCredentialMinter` from blob-storage configuration. It also enforces that the required S3 and signing settings are present before sandbox filesystem credentials can be used.

**Data flow**: It receives a `BlobConfig`. If the blob backend is not S3, it returns `None` because this feature is not needed. For S3, it checks for the bucket, S3 URL, STS role ARN, and signing secret environment variable. If anything required is missing, it raises an error; otherwise it creates an `AwsStsClient` and returns a configured `SandboxFsCredentialMinter`.

**Call relations**: This is the setup bridge between configuration and runtime credential minting. Startup or sandbox setup code can call it once, then use the returned minter to issue and refresh sandbox filesystem tokens.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/token_signing.py`

`util` · `cross-cutting`

This file solves a common trust problem: the system may need to give a client a token, then later accept it back without storing every token in a database. To make that safe, the token is signed. A signature here means a short proof made with a secret key that only the server knows. If even one character of the token body is changed, the proof no longer matches.

The token has two parts separated by a dot. The first part is the payload, converted into base64url text. Base64url is a way to turn raw bytes into characters that are safe to put in URLs. The second part is an HMAC signature. HMAC is a standard way to make a keyed fingerprint: it combines the secret key and the token body using SHA-256, a common hash algorithm.

The payload is called “opaque” because this file does not interpret it. It simply signs bytes and later returns bytes. Other parts of the system decide what those bytes mean.

When verifying, the file is careful to reject malformed tokens, tokens with the wrong signature, and tokens whose payload cannot be decoded. It uses a safe comparison function for signatures so attackers cannot learn the correct signature by timing tiny differences in string comparison.

#### Function details

##### `sign_token`  (lines 12–16)

```
def sign_token(secret: bytes, payload: bytes) -> str
```

**Purpose**: Creates a signed token string from a secret key and a byte payload. Someone would use this when they want to send data out and later detect whether it was altered.

**Data flow**: It receives a secret key as bytes and a payload as bytes. It turns the payload into URL-safe base64 text, makes an HMAC-SHA256 signature over that text using the secret, turns the signature into URL-safe base64 text, and returns one string shaped like `payload.signature`. It does not change any outside state.

**Call relations**: This is the token-making half of the pair. It relies on the standard base64 encoder to make URL-safe text and on HMAC to produce the tamper-proof signature. Later, `verify_token` can read the returned string and check that it was made with the same secret.

*Call graph*: 2 external calls (urlsafe_b64encode, new).


##### `verify_token`  (lines 19–30)

```
def verify_token(token: str, secret: bytes) -> bytes
```

**Purpose**: Checks that a signed token is well-formed and has a valid signature, then returns the original payload bytes. Someone would use this when a token comes back from an untrusted place, such as a browser or API client.

**Data flow**: It receives a token string and the secret key. First it splits the token into body and signature at the dot. If either part is missing, it raises `SignedTokenError`. Then it recomputes the expected signature from the body and secret, compares it safely with the provided signature, and rejects the token if they differ. Finally it decodes the body back into bytes and returns those bytes; if decoding fails, it raises `SignedTokenError` instead.

**Call relations**: This is the checking half that matches `sign_token`. It uses the same base64 and HMAC steps to rebuild what the signature should be, then uses a timing-safe comparison before trusting the payload. When anything is wrong, it reports the problem through `SignedTokenError` so callers can treat the token as invalid.

*Call graph*: 5 external calls (__init__, b64decode, urlsafe_b64encode, compare_digest, new).


### Credential declarations and exchange
Credential manifests, sealed storage, and provider-specific exchanges let workspaces grant narrowly scoped secrets without exposing raw values.

### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential lookup during git access`

This file solves a security-sensitive problem: how to let a workspace clone or access GitHub repositories through a GitHub App, without asking users to paste powerful personal tokens. A workspace stores only a sealed installation value. “Sealed” means it is encrypted and tied to the workspace and credential slot, so someone cannot just type another organization’s installation number and gain access.

When the system needs a Git credential, GitHubAppTokens first checks whether the workspace has a readable App installation binding. If it does, it opens the sealed value, builds a short-lived JSON Web Token, or JWT (a signed proof that this server owns the GitHub App), and sends it to GitHub. GitHub replies with an installation access token, which is the actual credential used for that organization’s allowed repositories.

The file also caches each installation token until shortly before it expires. This avoids asking GitHub for a new token every time during a conversation. If several tasks ask for the same token at once, they share the same in-progress minting task, like several people waiting for one fresh key to be cut instead of each ordering their own.

If minting fails, the code raises an error rather than silently falling back to a user token. That matters because using the wrong identity could access repositories under permissions the organization did not grant.

#### Function details

##### `_segment`  (lines 47–48)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: Turns one part of a JWT into the compact encoded text format GitHub expects. It is used for the JWT header and body before they are signed.

**Data flow**: It receives a small dictionary of values, converts it to compact JSON text, encodes that text using URL-safe base64, and removes padding characters. The result is a byte string ready to be joined into a JWT.

**Call relations**: GitHubAppTokens._jwt calls this helper while building the signed proof that the server owns the GitHub App. It prepares the pieces that later get signed with the private key.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 71–84)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a workspace has a valid GitHub App installation binding. It does not mint a token; it only answers whether the stored installation seal can be read safely.

**Data flow**: It takes a workspace ID and a credential store. It asks the store for the installation slot. If the slot is missing, it returns false. If the slot exists, it tries to open the sealed installation value. If opening fails, it logs a warning and returns false. If opening succeeds, it returns true.

**Call relations**: Other credential-selection code can call this when deciding whether GitHub App credentials are available for a workspace. It relies on CredentialStore.get to read the stored value and open_installation to prove the value really belongs to this workspace and slot.

*Call graph*: calls 1 internal fn (get); 2 external calls (open_installation, warn).


##### `GitHubAppTokens.secret`  (lines 86–102)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Returns the GitHub installation access token for a workspace, minting one if needed. If the workspace has no App installation binding, it returns none so another credential source can be used.

**Data flow**: It receives a workspace ID and credential store. It reads the stored installation seal. If no value is stored, it returns none. If a value is present, it opens the seal to get the installation identifier, checks whether a still-fresh token is already cached, and returns it if possible. If not, it starts or joins a shared minting task, waits for it, and returns the new token.

**Call relations**: This is the main function callers use when they need the secret value for Git access. It hands actual minting to GitHubAppTokens._mint, and uses asyncio task sharing so simultaneous requests for the same workspace and installation do not all contact GitHub separately.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 104–112)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: Runs one token-minting operation and stores the result in the cache. It also cleans up the “minting in progress” record when the attempt finishes.

**Data flow**: It receives the cache key and the GitHub installation identifier. It asks GitHubAppTokens._installation_token to fetch a fresh token and expiry time from GitHub, saves that pair in the cache, and returns it. Whether the request succeeds or fails, it removes its own pending task marker if it is still the current one.

**Call relations**: GitHubAppTokens.secret creates this as an asynchronous task when no fresh cached token exists. This function then delegates the network exchange to GitHubAppTokens._installation_token and reports the result back to all callers waiting on the shared task.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 114–146)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: Contacts GitHub to exchange the App’s signed proof for an installation access token. It raises a clear credential-minting error if GitHub cannot be reached, refuses the request, or returns an unreadable answer.

**Data flow**: It receives a GitHub installation identifier. It creates an HTTP client, builds an authorization header using GitHubAppTokens._jwt, and sends a POST request to GitHub’s installation-token endpoint. If GitHub returns success, it reads the token and expiry timestamp from the JSON response and returns them. If the request fails or the response is malformed, it raises CredentialMintFailed.

**Call relations**: GitHubAppTokens._mint calls this when a fresh token is required. This function is the bridge between the local credential logic and GitHub’s API, and it depends on GitHubAppTokens._jwt to prove this server is allowed to request installation tokens.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 3 external calls (__init__, fromisoformat, AsyncClient).


##### `GitHubAppTokens._jwt`  (lines 148–155)

```
def _jwt(self) -> str
```

**Purpose**: Creates the short-lived signed JWT that identifies this server as the registered GitHub App. GitHub requires this signed proof before it will issue an installation access token.

**Data flow**: It reads the current time, builds a JWT header and body containing the signing algorithm, issue time, expiry time, and App ID, encodes those pieces, signs them with the App’s RSA private key, and returns the complete JWT as text.

**Call relations**: GitHubAppTokens._installation_token calls this just before making the GitHub API request. It uses _segment to encode the JWT header and body, then adds the private-key signature that GitHub can verify.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `app_tokens`  (lines 158–169)

```
def app_tokens(installation_slot: str) -> GitHubAppTokens
```

**Purpose**: Builds a GitHubAppTokens object from deployment environment variables. This is the setup function that loads the GitHub App ID and private key configured for the running service.

**Data flow**: It receives the name of the credential slot that stores installation bindings. It reads GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY from the process environment, parses the private key, verifies that it is an RSA key, and returns a configured GitHubAppTokens instance. If the key is the wrong kind, it raises an error immediately.

**Call relations**: Startup or extension setup code can call this to create the token minter used later during credential lookup. It hands the loaded App ID, private key, and installation slot into GitHubAppTokens so later calls can mint installation tokens.

*Call graph*: 2 external calls (__init__, load_pem_private_key).


### `extensions/keyed_connectors/ufo_ext_keyed_connectors.py`

`config` · `startup/config load`

Many external services do not use a brokered login flow. Instead, they expect the caller to send an API key in an HTTP header. This file describes those services in a structured way so the agent can use them safely. Think of it like a mailroom rulebook: for each provider, it says which address is allowed, which envelope label carries the secret, and what placeholder the sandbox is allowed to see.

The file defines small records for a provider and its secrets. A provider can have one fixed API host, or a closed list of possible hosts when the correct host depends on the customer’s region or account. Datadog is declared this way, because different Datadog sites use different API hostnames.

For each secret, the file creates a credential slot. A credential slot is a named place where the workspace owner can privately provide a value. The sandbox receives only a sentinel, meaning a harmless placeholder string. When the sandbox makes an outbound request, an egress proxy replaces that sentinel with the real secret only for the approved host and header. This prevents the agent from reading, printing, or saving the actual key.

The final manifest advertises these credential slots and adds prompt text explaining how an agent should use keyed providers.

#### Function details

##### `KeyedProvider.__post_init__`  (lines 69–78)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that each provider declaration is valid as soon as it is created. It prevents unclear or unsafe provider records, such as one that names both a fixed host and a list of selectable hosts.

**Data flow**: It reads the provider’s stored fields after construction. If the provider has neither a fixed host nor site choices, or has both, it raises an error. If the provider uses site choices but does not say how to ask for and expose that choice, it also raises an error. Otherwise, the provider object is left unchanged and accepted.

**Call relations**: This runs automatically when a KeyedProvider row is created in the provider table. It acts as an early guardrail, so later code can build credential slots and usage instructions without having to defend against malformed provider definitions.


##### `KeyedProvider.target_host`  (lines 81–90)

```
def target_host(self) -> str | HostChoice
```

**Purpose**: This answers the question, “Which API host should requests be allowed to reach for this provider?” For fixed-host providers it returns the hostname directly; for region-based providers it builds a safe host-choice object from the declared list.

**Data flow**: It reads the provider’s host and sites fields. If there is a single fixed host, that hostname comes out. If there are selectable sites, it creates a HostChoice containing the credential slot name for the choice, the allowed hostnames, the default host, and the environment variable that will carry the selected host into the sandbox.

**Call relations**: The credential-slot builder and usage-text builder both call this when they need to know where the provider’s secrets may be sent. When a choice is needed, it hands off to HostChoice so the larger manifest can represent the allowed host list in a standard form.

*Call graph*: 1 external calls (__init__).


##### `KeyedProvider.slots`  (lines 92–110)

```
def slots(self) -> tuple[CredentialSlot, ...]
```

**Purpose**: This turns one provider declaration into the credential slots the workspace owner can fill. Each slot says what secret is needed and exactly where that secret may be injected into an outgoing web request.

**Data flow**: It starts with the provider’s secrets and target host. For each secret, it creates a CredentialSlot with a human description and an InjectionTarget that names the approved host, HTTP header, sentinel placeholder, environment variable, and request dimension. If the provider has a selectable host, it also adds a separate slot for that host choice. The result is a tuple of credential-slot declarations.

**Call relations**: The top-level manifest function calls this for every keyed provider when assembling the extension manifest. Inside this function, CredentialSlot and InjectionTarget are created so the broader UFO system knows both what to ask the owner for and how the proxy should replace sentinels on outbound requests.

*Call graph*: 2 external calls (__init__, __init__).


##### `KeyedProvider.usage`  (lines 112–122)

```
def usage(self) -> str
```

**Purpose**: This writes a short instruction line showing an agent how to call the provider’s REST API from the sandbox. It names the needed slots and gives a curl-style example using environment variables rather than raw secrets.

**Data flow**: It reads the provider name, label, secrets, headers, environment variable names, and target host. It formats those pieces into one readable bullet point. The output is plain text that becomes part of the prompt guidance shown for keyed providers.

**Call relations**: The section body for the manifest includes one usage line per provider by calling this method while the module is loaded. That text later travels through the manifest so agents know that these services are reached directly through their own APIs, not through connected-account tools.


##### `manifest`  (lines 188–194)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s public declaration. It packages the provider credential slots and the human guidance text into a Manifest object that UFO can load.

**Data flow**: It reads the extension name, version, provider table, and prepared prompt section body. It gathers all credential slots from all keyed providers, creates a PromptSection containing the instructions, and returns a Manifest containing both the machine-readable credential rules and the human-readable guidance.

**Call relations**: The UFO extension loader calls this when it needs to discover what this extension offers. It delegates slot creation to each KeyedProvider and hands the finished pieces to Manifest and PromptSection so the rest of the system can request credentials, expose safe environment variables, and teach the agent how to use them.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/credential_kind.py`

`domain_logic` · `request handling for credential object list, read, status, and delete operations`

Some installed extensions need private secrets, such as API keys. Those secrets are declared by the extension as BYOK slots, meaning “bring your own key” or credential supplied by the workspace. This file turns those declarations into a readable object kind called `credential`.

The important safety rule is that reading a credential object never reveals the stored secret, or even a digest of it. A read shows only the slot’s declaration: its name, description, declaring extension, and any host choice tied to where the secret will be used. The status says whether the slot is filled or empty. Think of it like a locked mailbox label: you can see that a mailbox exists and whether something is inside, but you cannot see the contents.

Filling or rotating a credential is deliberately not done here. Create and update are refused because adding a secret needs a private handoff through `request_credentials`. Deleting is allowed, but only for the workspace owner, and it means “clear the stored value.” The slot still appears afterward because the slot comes from the extension manifest, not from the database row. The file also gives each slot a safe object name, adding a short hash when two slot names would otherwise collide.

#### Function details

##### `_slug`  (lines 73–74)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a raw credential slot name into a simple object name that is easier and safer to use in URLs or commands. It lowercases the text and replaces non-letter-or-number runs with dashes.

**Data flow**: It receives a raw string, cleans it into a lowercase dash-separated name, trims extra dashes from the ends, and returns that cleaned name.

**Call relations**: CredentialObjects._named uses this helper when building the public names for declared credential slots. It is the first step before checking whether two slots would end up with the same visible name.

*Call graph*: called by 1 (_named); 1 external calls (sub).


##### `CredentialObjects.list`  (lines 86–99)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists every credential slot declared by active extensions and says whether each one is filled or empty. It gives users an overview without exposing any secret values.

**Data flow**: It receives the tool context and a list query, builds the public slot names, reads which slots have stored credential rows, creates one summary row per declared slot, and returns a paged result that matches the query.

**Call relations**: When the object system asks for a list of credential objects, this method calls _named to map declarations to object names and _filled_slots to find which slots currently have stored values. It then hands the rows to object_page so normal object listing rules, such as paging, can be applied.

*Call graph*: calls 2 internal fn (_filled_slots, _named); 2 external calls (__init__, object_page).


##### `CredentialObjects.get`  (lines 101–125)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Shows the declaration for one credential slot, plus its database timestamps if it has been filled. It never reads or returns the secret value.

**Data flow**: It receives a public object name, looks up the matching declared slot, and returns nothing if the name is unknown. If the slot exists, it checks the workspace credential table for that slot’s creation and update times, builds a CredentialSpec describing the slot and any host information, and returns an ObjectDetail with those timestamps.

**Call relations**: The object system calls this when someone reads a single credential object. It uses _named to translate the public name back to the real slot declaration, opens a workspace database transaction to check for a matching stored row, and packages the result as an object detail.

*Call graph*: calls 1 internal fn (_named); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects.status`  (lines 127–145)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status report for one credential slot, mainly whether it is currently filled. If the slot is tied to a host choice, it can also report the resolved host.

**Data flow**: It receives a public object name, finds the declared slot, and returns nothing if the name is unknown. It then checks the workspace credential table for a row for that slot, sets `filled` to true or false, optionally asks the credential store for the selected host, and returns that status dictionary.

**Call relations**: The object system calls this when it needs live state rather than the full declaration. It uses _named for name lookup, the workspace database to test whether a value exists, and credential_host when host information must be resolved from the credential store.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, credential_host, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 147–150)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None) -> None
```

**Purpose**: Refuses attempts to create or update credential objects through the normal object write path. This protects secrets by forcing fills and rotations to go through the private `request_credentials` flow instead.

**Data flow**: It receives the requested name, new spec, and old spec, but does not use them to change anything. It immediately raises a VerbNotSupported error explaining that credentials must be filled or rotated elsewhere.

**Call relations**: The object system would call this for create or update operations. Instead of handing off to any database write, it stops the flow with a clear refusal message.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 152–162)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Clears the stored secret value for a credential slot. Only the workspace owner can do this, and deleting the value does not remove the slot declaration itself.

**Data flow**: It receives the tool context and public slot name, checks whether the current speaker is the workspace owner, and raises an OwnerRequired error if not. If allowed, it maps the public name to the declared slot and deletes the matching row from the workspace credential table, leaving the declared slot to appear as empty afterward.

**Call relations**: The object system calls this when someone deletes a credential object. It asks ToolContext whether the speaker is the owner, uses _named to find the real slot, and performs a workspace-scoped database delete for that slot.

*Call graph*: calls 2 internal fn (_named, speaker_is_owner); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 164–176)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Builds the public object names for all declared credential slots. It also prevents name collisions when two different extensions declare slots whose names clean up to the same slug.

**Data flow**: It reads the object’s declared slots, converts each slot name with _slug, groups slots that would share the same public name, and returns a dictionary from public name to slot declaration. If a group has more than one slot, it adds a short hash based on extension and slot name so each public name is unique.

**Call relations**: The list, get, status, and delete methods all call this before they can work with a slot by name. It relies on _slug for readable names and on a SHA-256 hash for short, stable disambiguation when names clash.

*Call graph*: calls 1 internal fn (_slug); called by 4 (delete, get, list, status); 1 external calls (sha256).


##### `CredentialObjects._filled_slots`  (lines 178–187)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in the current workspace. It returns only slot names, never the credential contents.

**Data flow**: It opens a workspace database transaction, selects all credential slot names for the current workspace, and returns them as an immutable set. The before state is the credential table; the after result is just the set of filled slot identifiers.

**Call relations**: CredentialObjects.list calls this to decide whether each declared slot summary should say `filled` or `empty`. It uses the current workspace ID so one workspace’s credential state cannot leak into another.

*Call graph*: called by 1 (list); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/credentials.py`

`domain_logic` · `cross-cutting`

This file is the project’s locked cabinet for credentials. A “credential slot” is a named place where a workspace may store a secret, such as an API token. The secret is encrypted with Fernet, a symmetric encryption tool where the same private key locks and unlocks the value, before it is written to the database. When something later needs the secret, this file decrypts it inside the server process and returns it to the trusted caller.

It also supports a safer way to ask users for credentials. Instead of asking a user to paste a secret into chat, the system creates a sealed request. That seal says which workspace, member, and slot the request belongs to, and it expires after a short time. A private surface can collect the secret and fulfill the request only if the seal still matches. This is like a claim ticket at a coat check: it does not contain the coat, but it proves exactly which coat may be picked up.

Some credentials are not stored directly. A provider can mint a short-lived secret from a bound installation, so this file defines the shared rules for asking either the provider or the stored database value. It also restricts account-specific host choices to a declared list, so user-entered text cannot trick the proxy into connecting to an unsafe internal address.

#### Function details

##### `seal_credential_request`  (lines 61–62)

```
def seal_credential_request(fernet: Fernet, state: CredentialRequestState) -> str
```

**Purpose**: This turns a credential request state into an encrypted, portable string. Other parts of the system can hand this string around without exposing the details inside or letting someone edit them.

**Data flow**: It receives a Fernet encryption object and a CredentialRequestState containing facts such as workspace, member, slot, purpose, and optional payload. It converts the state to JSON, encrypts that text, and returns the encrypted text as a string.

**Call relations**: CredentialRequests.seal and CredentialRequests.authorize use this when creating member credential requests, and seal_installation uses it when binding a provider installation. It is the common envelope-maker for all sealed credential actions.

*Call graph*: called by 3 (authorize, seal, seal_installation); 2 external calls (model_dump_json, encrypt).


##### `open_credential_request`  (lines 65–91)

```
def open_credential_request(fernet: Fernet, sealed: str, *, purpose: str, ttl: int | None=CREDENTIAL_REQUEST_TTL_SECONDS) -> CredentialRequestState
```

**Purpose**: This opens and checks a sealed credential request. It makes sure the seal was made by this deployment, has not expired when expiry is required, is shaped like a valid request, and was made for the expected purpose.

**Data flow**: It receives a Fernet object, an encrypted string, the expected purpose, and an optional time limit. It tries to decrypt the string, parse the JSON into a CredentialRequestState, and compare the embedded purpose with the requested one. It returns the validated state, or raises CredentialRequestInvalid if anything is wrong.

**Call relations**: CredentialRequests.open_authorization, authorized_slot_workspace, and open_installation call this before trusting a sealed value. It centralizes the dangerous part: callers get either a verified state or a clear failure, not half-trusted decoded data.

*Call graph*: called by 3 (open_authorization, authorized_slot_workspace, open_installation); 2 external calls (__init__, decrypt).


##### `CredentialRequests.seal`  (lines 103–110)

```
def seal(self, workspace_id: UUID, member_id: UUID, slots: tuple[str, ...]) -> str
```

**Purpose**: This creates a sealed request saying that a specific member in a specific workspace may fill specific credential slots. It refuses slots that no installed extension declared.

**Data flow**: It receives a workspace ID, member ID, and tuple of slot names. It compares the requested slots with the known declared slots, builds a CredentialRequestState if they are allowed, and returns an encrypted seal string.

**Call relations**: This is used when the system asks a member to provide credentials privately. It hands the actual sealing work to seal_credential_request after checking that the request only names real installed slots.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.authorize`  (lines 112–125)

```
def authorize(self, workspace_id: UUID, member_id: UUID, slot: str, payload: str) -> str
```

**Purpose**: This creates a sealed authorization request for one credential slot and a provider-specific state value. It is used when a credential will be completed through an outside provider flow, such as an OAuth-style authorization.

**Data flow**: It receives the workspace ID, member ID, slot name, and provider payload. It checks that the slot is declared and that the payload is not empty, then seals those facts into an encrypted string and returns it.

**Call relations**: Provider authorization setup calls this to create a trustworthy sealed value that can later come back through a browser redirect. It uses seal_credential_request to produce the encrypted handoff.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `CredentialRequests.open_authorization`  (lines 127–141)

```
def open_authorization(self, sealed: str, workspace_id: UUID, member_id: UUID, slot: str) -> str
```

**Purpose**: This verifies that a sealed provider authorization really belongs to the expected workspace, member, and slot. If it passes, it extracts the provider state payload.

**Data flow**: It receives a sealed string plus the workspace, member, and slot the caller expects. It opens the seal, compares each important field, checks the slot is still declared, and returns the payload. If the seal names anything else or lacks a payload, it raises an error.

**Call relations**: This is the matching reader for seals created by CredentialRequests.authorize. It relies on open_credential_request for decryption and then performs the extra identity checks that bind the authorization to the current member action.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `seal_installation`  (lines 144–158)

```
def seal_installation(fernet: Fernet, workspace_id: UUID, slot: str, installation_id: str) -> str
```

**Purpose**: This seals a provider installation ID so it cannot be copied or guessed and used by another workspace. The stored value is the sealed binding, not the bare installation ID.

**Data flow**: It receives a Fernet object, workspace ID, slot name, and installation ID. It builds a CredentialRequestState marked with the special installation-binding purpose, includes the installation ID as payload, encrypts it, and returns the sealed string.

**Call relations**: Provider installation flows use this when saving a long-lived binding. It reuses seal_credential_request, but with a different purpose so an ordinary credential request seal cannot impersonate an installation binding.

*Call graph*: calls 1 internal fn (seal_credential_request); 1 external calls (__init__).


##### `open_installation`  (lines 161–172)

```
def open_installation(fernet: Fernet, workspace_id: UUID, slot: str, sealed: str) -> str
```

**Purpose**: This opens a sealed provider installation binding and confirms it belongs to the expected workspace and slot. It rejects forged values, values for other workspaces, and plain unsealed IDs.

**Data flow**: It receives a Fernet object, workspace ID, slot name, and sealed string. It opens the seal with no expiry, verifies the workspace and slot, checks that an installation ID payload is present, and returns that ID.

**Call relations**: Provider code calls this when it needs the installation ID behind a stored binding. It depends on open_credential_request for the encrypted envelope and then checks that the binding is for the exact workspace and slot being used.

*Call graph*: calls 1 internal fn (open_credential_request); 1 external calls (__init__).


##### `install_credential_requests`  (lines 178–184)

```
def install_credential_requests(requests: CredentialRequests | None) -> None
```

**Purpose**: This installs the process-wide credential request authority. It lets routes that do not have normal request context, such as provider callback routes, still verify credential authorization seals.

**Data flow**: It receives either a CredentialRequests object or None. It stores that value in a module-level variable, changing what later calls see as the installed credential request setup.

**Call relations**: Startup code calls this once when the server is configured. Later, installed_credential_requests and authorized_slot_workspace read the installed value when a provider callback or authorization check needs the encryption key and declared slots.


##### `installed_credential_requests`  (lines 187–190)

```
def installed_credential_requests() -> CredentialRequests
```

**Purpose**: This returns the currently installed credential request authority. It fails clearly if no credential key was configured.

**Data flow**: It reads the module-level installed credential request object. If one is present, it returns it; if not, it raises a runtime error explaining that credential authorization is unavailable.

**Call relations**: Code that needs the global credential request setup calls this instead of reaching into the module variable directly. It protects callers from accidentally continuing when credential authorization was never configured.


##### `authorized_slot_workspace`  (lines 193–209)

```
def authorized_slot_workspace(sealed: str, slot: str, payload: str) -> UUID | None
```

**Purpose**: This finds which workspace a provider authorization seal belongs to, but only if the seal is valid for one exact slot and payload. It is useful when a browser callback arrives without a normal workspace session.

**Data flow**: It receives a sealed string, a slot name, and a payload value. It opens the seal using the installed credential request key, then checks that the seal names exactly that slot and payload. It returns the workspace ID if everything matches, or None if anything is missing or invalid.

**Call relations**: Provider callback routes use this to recover the workspace from the seal itself. It calls open_credential_request and deliberately turns invalid seals into None, because callbacks should simply not resolve to a workspace when the handoff is wrong.

*Call graph*: calls 1 internal fn (open_credential_request).


##### `CredentialStore.put`  (lines 216–238)

```
async def put(self, workspace_id: UUID, slot: str, plaintext: str) -> None
```

**Purpose**: This saves a plaintext credential for a workspace and slot after encrypting it. It either updates the existing slot or creates a new database row.

**Data flow**: It receives a workspace ID, slot name, and plaintext secret. It rejects an empty secret, encrypts the text, opens a workspace database transaction, tries to update the matching credential row, and inserts a new row if no row existed. It returns nothing, but the database now contains the encrypted value.

**Call relations**: Credential fulfillment code uses this when a member or provider supplies a credential value. It is the write side of CredentialStore; later, CredentialStore.get reads and decrypts what this saved.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `CredentialStore.get`  (lines 240–252)

```
async def get(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: This retrieves and decrypts the credential stored for one workspace and slot. It signals clearly when the slot has not been set.

**Data flow**: It receives a workspace ID and slot name. It queries the credential table for the encrypted value, raises CredentialSlotUnset if no row exists, decrypts the ciphertext if found, and returns the plaintext secret.

**Call relations**: slot_secret, slot_is_set, and credential_host call this as the shared way to read stored credential slots. GitHub app token code also calls it when checking or minting from stored provider bindings.

*Call graph*: called by 5 (credential_host, slot_is_set, slot_secret, bound, secret); 3 external calls (__init__, select, workspace_tx).


##### `CredentialStore.rotate`  (lines 254–285)

```
async def rotate(self, workspace_id: UUID, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: This safely replaces a stored credential only if it still contains an expected old value. It prevents two refresh operations from overwriting each other by accident.

**Data flow**: It receives a workspace ID, slot name, expected current plaintext, and replacement plaintext. It rejects an empty replacement, reads the current encrypted row, decrypts it, compares it with the expected value, and only then writes a newly encrypted replacement. It returns true if exactly one row was updated, otherwise false.

**Call relations**: Provider token refresh code can use this after receiving a new token from an outside service. The function keeps the read-compare-write sequence inside one database transaction so callers can tell whether their refresh was still current.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `HostChoice.__post_init__`  (lines 309–314)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a host-choice declaration is internally consistent. The default host must be one of the allowed hosts.

**Data flow**: It reads the HostChoice object's default value and allowed host tuple after the object is created. If the default is not in the allowed list, it raises a ValueError; otherwise, the object remains usable.

**Call relations**: This runs automatically when a HostChoice is constructed. It catches bad extension or manifest declarations early, before credential_host relies on the default as a safe fallback.


##### `HostChoice.resolve`  (lines 316–321)

```
def resolve(self, selected: str) -> str | None
```

**Purpose**: This turns a stored host selection into one of the declared safe host strings. It accepts different letter casing from the stored value but returns the canonical declared spelling.

**Data flow**: It receives a selected string, trims surrounding spaces, and compares it case-insensitively with each allowed host. It returns the matching declared host, or None if the selection is not in the allowed list.

**Call relations**: credential_host calls this when a workspace has stored a choice for a variable provider host. It is the gate that prevents arbitrary stored text from becoming a proxy destination.


##### `CredentialSource.secret`  (lines 329–329)

```
async def secret(self, workspace_id: UUID, store: 'CredentialStore') -> str | None
```

**Purpose**: This protocol method describes how provider-backed credential sources should produce a secret for a workspace. A source can return a freshly minted token instead of relying on a member-stored plaintext value.

**Data flow**: An implementation receives a workspace ID and CredentialStore. It may read stored binding information, contact a provider, and return a secret string, or return None if it cannot mint one for that workspace.

**Call relations**: slot_secret calls this when a slot declares a CredentialSource. Concrete provider classes, such as extension code, supply the real behavior behind this protocol.

*Call graph*: called by 1 (slot_secret).


##### `CredentialSource.bound`  (lines 331–335)

```
async def bound(self, workspace_id: UUID, store: 'CredentialStore') -> bool
```

**Purpose**: This protocol method describes how provider-backed credential sources should answer whether a workspace is connected, without minting a secret. It is a cheaper yes-or-no check for setup decisions.

**Data flow**: An implementation receives a workspace ID and CredentialStore. It checks whatever stored binding or marker proves the workspace can mint a secret, and returns true or false without producing the actual credential.

**Call relations**: slot_is_set calls this before deciding whether a slot is available. Provider implementations use it so routine sandbox setup can avoid an unnecessary provider network call.

*Call graph*: called by 1 (slot_is_set).


##### `slot_secret`  (lines 338–353)

```
async def slot_secret(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: This answers the main question: what secret should this credential slot provide for this workspace? It prefers a provider-minted secret when a source exists, otherwise it falls back to the stored credential value.

**Data flow**: It receives a slot name, optional CredentialSource, workspace ID, and CredentialStore. If a source exists, it asks the source for a secret and returns it if present. If not, it tries to read the stored slot from the database and returns that value, or None if the slot is unset.

**Call relations**: Consumers such as proxy rule creation, sandbox environment export, and configuration code should resolve credentials through this one function. It calls CredentialSource.secret for provider-backed slots and CredentialStore.get for ordinary stored slots.

*Call graph*: calls 2 internal fn (secret, get).


##### `slot_is_set`  (lines 356–368)

```
async def slot_is_set(name: str, source: CredentialSource | None, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: This checks whether a credential slot would provide a secret, without actually producing one. It is meant for frequent setup checks where minting a token would be too expensive or unnecessary.

**Data flow**: It receives a slot name, optional CredentialSource, workspace ID, and CredentialStore. If a source exists and says it is bound, it returns true. Otherwise it tries to read the stored slot; it returns false if the slot is missing and true if a stored value exists.

**Call relations**: Sandbox-opening logic can use this to decide whether to configure clients or environment variables. It calls CredentialSource.bound for provider-backed slots and CredentialStore.get for stored slots, avoiding CredentialSource.secret and its possible provider round trip.

*Call graph*: calls 2 internal fn (bound, get).


##### `credential_host`  (lines 371–388)

```
async def credential_host(store: CredentialStore, workspace_id: UUID, host: str | HostChoice) -> str | None
```

**Purpose**: This decides which provider host a credential may be used with for a workspace. It returns either a fixed declared host or a workspace-selected host from a closed, safe list.

**Data flow**: It receives a CredentialStore, workspace ID, and either a plain host string or a HostChoice. If given a plain string, it returns it directly. If given a HostChoice, it reads the workspace's stored selection, falls back to the default when no selection exists, and returns the resolved declared host or None when the stored selection is not allowed.

**Call relations**: The egress proxy and sandbox setup both use this so they agree on the same allowed host. It calls CredentialStore.get to read the workspace selection and uses HostChoice.resolve indirectly through the HostChoice object to keep the result inside the declared safe set.

*Call graph*: calls 1 internal fn (get).


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `source sync authentication`

Some data providers cannot get credentials through an installed broker, or a deployment may want to keep the provider key under its own control. In those cases, a member adds an API key directly. This file is the small bridge that retrieves that key when a source sync needs it.

The main idea is simple: the key is stored encrypted in a workspace credential store under the provider’s name. When a sync run is routed through the special direct account path, `DirectAuthProxy` is asked for credentials. It uses `CredentialAccess`, which is the controlled interface for reading allowed credential slots, to fetch the secret for that provider. It then wraps the secret as a `Credential` with a bearer token, meaning it will be used as the “Bearer ...” authentication value in provider HTTP requests.

The important safety rule is that this happens on the host side, inside the sync job. The raw secret is not sent into the sandbox or exposed to an agent-facing surface. Like a locked cabinet with a narrow service window, this file only takes out the one named key needed for the provider and hands back a credential object for the immediate job.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This function retrieves the stored API key for a provider and returns it in the standard credential shape used by the sync system. It is used when a direct, member-supplied key is the way to authenticate to the provider.

**Data flow**: It receives a workspace identifier, a provider name, and an account handle. The provider name is used to read the matching secret from `CredentialAccess`; the account handle is only part of the routing path and does not supply the key. The fetched secret is wrapped into a `Credential` as a bearer token and returned, without logging or exposing the raw key elsewhere.

**Call relations**: When the source sync has been routed to the direct authentication backend, this method is called to produce the credential needed for provider HTTP calls. After reading the secret from the credential store, it hands the value to `Credential.__init__` so the rest of the sync code can use a normal credential object instead of dealing with the raw stored secret directly.

*Call graph*: 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-effective-config` — The running service’s merged settings, such as required keys, enabled backends, safety options, and service behavior.
- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-member-session-auth` — The signed-in person’s identity and session proof used to decide who is making a request.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-authorization-grants` — The saved permissions showing which user-approved outside accounts an agent may use.
- `reg-tool-catalog` — The live list of tools the model can call, including their names, descriptions, schemas, and dispatch targets.
- `reg-model-catalog-pricing` — The shared list of available AI models, provider details, limits, credentials, and prices.
- `reg-sandbox-workspace-handle` — The saved handle and lease for the safe workspace where a conversation can run commands and keep files.
- `reg-blob-storage-backend` — The shared large-file storage used for workspace files, transcripts, source snapshots, and artifacts.
- `reg-network-egress-policy` — The allow-or-deny rules for outbound network calls, including when approved secrets may be attached.
- `reg-connector-broker-catalog` — The known external service brokers and provider actions that let agents use connected services safely.
- `reg-mcp-server-connections` — The configured MCP tool-server connections used to discover and call extra provider tools.
- `reg-artifact-download-tokens` — The short-lived signed passes that let private files produced by a turn be downloaded safely.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
- `reg-row-level-security-context` — The database safety context that keeps each workspace’s rows separated even when code uses shared tables.
- `reg-sandbox-image-artifact-state` — The validated sandbox/workroom image identity and preflight health result used later when creating sandbox runtimes.
- `reg-extension-request-context-envelope` — The request-time workspace, member, conversation, turn, capability, and cleanup context passed from core into extension handlers and tools.
- `reg-crypto-signing-encryption-keyring` — Stable secret key material used to sign/verify session, OAuth/state, artifact, and filesystem tokens and to encrypt/decrypt stored credentials.
- `reg-workspace-file-access-leases` — Short-lived scoped credentials or signed grants that let sandboxes mount or access only approved workspace blob/file paths.
