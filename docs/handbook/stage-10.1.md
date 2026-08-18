# Sandbox workspace, command execution, file access, and egress proxying  `stage-10.1`

This stage is the system’s workbench. During a conversation, it gives the agent one private workspace, keeps returning to it, and controls how commands, files, web access, and preview sites work. The conversation and session code are the front door: they find the right workspace, limit file access to `/workspace`, and expose safe actions like run, read, and write. Local, terminal, Docker, and E2B carriers are different “engines” for the same workbench: a host folder, the user’s terminal, a container, or a cloud sandbox.

Command work is made durable by task helpers, which save long-running output and results so later calls can reconnect instead of rerun. Workspace change tracking records what files changed, while file path limits keep stored change data bounded. The REPL extension adds persistent Python and JavaScript scratchpads.

Network access is guarded by the egress proxy. The proxy entrypoint runs the shared service, the proxy server approves destinations, injects only allowed credentials, and records usage. Cache settings allow controlled downloads, and ingress host labels safely route browser traffic to one sandbox port.

## Files in this stage

### Egress proxy service
These files start, configure, and implement the shared network gatekeeper that mediates sandbox outbound traffic and credential injection.

### `core/src/ufo/proxy_serve.py`

`entrypoint` · `startup and main loop`

A sandbox needs controlled access to the outside world: model providers, artifact storage, connector services, and sometimes a package cache. This file is the "front desk" for that shared network gatekeeper. It loads configuration, checks that required secrets are present, opens the database with an owner connection, builds the rules that say where each sandbox may connect, and starts the proxy server.

The important idea is that one proxy can serve every workspace. Each request carries a run token, which includes the workspace identity. The proxy uses that identity to look up only the rules and secrets for that workspace. That is why this file insists on an owner database connection: the proxy is trusted to bypass database row-level security, but then explicitly filters by workspace itself.

It also requires a stable certificate authority, or CA, which is like the master stamp used to sign temporary certificates for intercepted sandbox traffic. If this changed on every restart, sandboxes would stop trusting the proxy. Model provider keys come from environment variables. Workspace-specific connector credentials are decrypted only when a package declares that it needs them. If a required key is missing, the file fails early rather than running a proxy that silently blocks useful work.

#### Function details

##### `model_rule_base`  (lines 52–71)

```
def model_rule_base(config: Config) -> tuple[Rule, ...]
```

**Purpose**: Builds the basic network rules that allow sandboxes to talk to configured model providers, such as Anthropic or OpenAI. It only enables providers whose API key is actually present, so the proxy does not advertise a route it cannot safely use.

**Data flow**: It receives the loaded configuration and reads the provider key environment variable names from it. It checks the current process environment for those keys, asks the model-rule builder to turn each available key into proxy rules, combines the allowed host names into one host allow-list, and returns the final rules. If no provider key is set, it raises an error because model calls from the sandbox would have nowhere valid to go.

**Call relations**: During proxy startup, ProxyServe.serve calls this to create the shared model-provider rule set. The rules it returns become part of the base rules passed into PerAgentRules, which later resolves the full per-sandbox permissions for each turn.

*Call graph*: called by 1 (serve); 2 external calls (__init__, derive_model_rules).


##### `run`  (lines 74–95)

```
def run() -> None
```

**Purpose**: Boots the standalone shared proxy process. It gathers configuration, secrets, credentials, model pricing, and manifests, then creates a ProxyServe instance and runs it until shutdown.

**Data flow**: It starts by loading the project configuration and setting up telemetry logging. It loads extension manifests, reads the stable proxy certificate authority, finds the owner database connection string, opens a credential store if needed, and computes model pricing. These pieces are packed into a ProxyServe object, and asyncio.run starts its asynchronous serve loop. Nothing is returned; the process stays alive until the proxy shuts down or startup fails.

**Call relations**: This is the top-level entry for the proxy command. It calls helper functions in this file to validate required runtime secrets before handing control to ProxyServe.serve, which does the actual database setup, rule construction, proxy binding, and shutdown waiting.

*Call graph*: calls 3 internal fn (_credential_store, _egress_ca, owner_dsn); 9 external calls (__init__, Event, run, load_config, injecting_slots, load_manifests, model_registry, init_o11y, log).


##### `_egress_ca`  (lines 98–109)

```
def _egress_ca() -> tuple[str, str]
```

**Purpose**: Reads the shared certificate authority material used by the proxy to sign sandbox network certificates. This is necessary so sandboxes continue trusting the proxy across restarts.

**Data flow**: It reads two environment variables: one containing the CA certificate and one containing the CA private key, both in PEM text format. If both are present, it returns them as a pair. If either is missing, it raises an error before the proxy starts, because a throwaway CA would not match what sandboxes trust.

**Call relations**: run calls this during startup, before constructing ProxyServe. The returned certificate and key are later passed into EgressProxy by ProxyServe.serve so the proxy can sign certificates for outbound sandbox traffic.

*Call graph*: called by 1 (run).


##### `owner_dsn`  (lines 112–125)

```
def owner_dsn(config: Config) -> str
```

**Purpose**: Finds the database connection string the shared proxy should use. It deliberately uses an owner connection, because this one proxy serves many workspaces and must make workspace-scoped queries itself.

**Data flow**: It receives the loaded configuration, then looks first for the UFO_OWNER_DSN environment variable and next for the owner_url setting in the database config. If neither is present, it raises an error. If it finds a URL, it rewrites a plain PostgreSQL URL prefix to the async psycopg driver form expected by this service and returns the resulting string.

**Call relations**: run calls this during startup and passes the result into ProxyServe.serve through the ProxyServe object. ProxyServe.serve then uses it to initialize the database connection before the proxy begins accepting traffic.

*Call graph*: called by 1 (run).


##### `_credential_store`  (lines 128–142)

```
def _credential_store(config: Config, slots: tuple[CredentialSlot, ...]) -> CredentialStore | None
```

**Purpose**: Creates the encrypted credential store the proxy uses to fetch and decrypt workspace-specific connector secrets. It only requires this store when the active package declares credential slots that must be injected into outbound requests.

**Data flow**: It receives configuration and the list of credential slots that need injection. It reads the configured credential encryption key from the process environment. If the key exists, it builds a Fernet decryptor and wraps it in a CredentialStore. If the key is missing but credential-injecting slots exist, it raises an error because the proxy would be unable to add required secrets. If no such slots exist, it returns nothing because no credential store is needed.

**Call relations**: run calls this after loading manifests and identifying injecting slots. The returned CredentialStore, or None, is passed into ProxyServe and later into PerAgentRules and the cache credential callback so they can retrieve secrets for the correct workspace when needed.

*Call graph*: called by 1 (run); 2 external calls (__init__, Fernet).


##### `ProxyServe.serve`  (lines 160–202)

```
async def serve(self) -> None
```

**Purpose**: Runs the shared proxy service. It prepares the database, builds all network permission rules, starts the proxy listener, optionally starts the cache credential callback, waits for a shutdown signal, and then cleans up.

**Data flow**: It uses the fields stored on the ProxyServe object: configuration, manifests, database URL, certificate material, credential store, pricing, and shutdown event. First it registers signal handlers for Ctrl-C or termination, initializes and verifies the database, derives artifact-store rules, detects whether the sandbox cache daemon is enabled, and builds a PerAgentRules resolver. Then it creates an EgressProxy using that resolver, the CA material, run-token decoding, pricing, and cache settings. It starts the proxy on the configured port, optionally starts the credential callback server, waits until shutdown is requested, and finally closes the callback server if present and stops the proxy gracefully.

**Call relations**: run creates the ProxyServe object and starts this method. Inside the method, model_rule_base supplies model-provider rules, PerAgentRules supplies per-run authorization decisions, and EgressProxy becomes the actual network-facing proxy. If caching is enabled, this method calls ProxyServe._start_credential_callback so the cache daemon can request credentials safely.

*Call graph*: calls 3 internal fn (_start_credential_callback, model_rule_base, from_env); 14 external calls (__init__, __init__, __init__, get_running_loop, blob_store_for, init_db, verify_db_reachable, connector_clis, injecting_slots, log (+4 more)).


##### `ProxyServe._start_credential_callback`  (lines 204–220)

```
async def _start_credential_callback(self) -> asyncio.AbstractServer
```

**Purpose**: Starts a small local callback server used by the sandbox cache daemon to request credentials for operations such as fetching private Git packages. It protects that callback with a shared token so random local callers cannot ask for secrets.

**Data flow**: It reads the cache-control token from the process environment. If the token is missing, it raises an error because the cache daemon would be unable to authenticate its callback requests. If present, it creates a CredentialCallback with the proxy's credential store, the manifest credential slots, and the token, then binds it to the configured loopback host and port. It logs that the callback is listening and returns the server object so the caller can later close it.

**Call relations**: ProxyServe.serve calls this only when the sandbox cache daemon is enabled. The returned server stays alive beside the main EgressProxy until shutdown, when ProxyServe.serve closes it before stopping the proxy.

*Call graph*: called by 1 (serve); 3 external calls (__init__, injecting_slots, log).


### `core/src/ufo/sandbox/proxy/server.py`

`io_transport` · `startup, request handling, shutdown`

Sandboxed agents do not talk to the internet directly. They are pointed at this proxy, which acts like a guarded front desk: every outbound HTTPS connection must show a signed token, and the proxy checks that the token still represents a live turn or an unexpired probe. From that identity it builds a rule list: model endpoints, workspace credentials, OAuth grants, optional internet access, and cache services.

The proxy is deliberately default-deny. If a host is not allowed, the connection is refused. If broad internet access is allowed, the proxy still resolves the hostname itself and only permits public IPv4 addresses, so a sandbox cannot sneak into private network addresses.

For ordinary allowed hosts, it tunnels encrypted bytes without looking inside. For hosts where a secret must be swapped in, it performs TLS interception: it presents a temporary certificate trusted by the sandbox, reads the HTTP request, replaces a harmless sentinel value with the real credential, and then opens a verified TLS connection to the real service. For some OAuth-style grants, it does not put credentials on the wire at all; it forwards the request through a broker that owns the credential.

The file also meters egress requests and model token usage. Metering is queued and written in batches so network traffic is not slowed by database writes.

#### Function details

##### `_ContentDecoder.unconsumed_tail`  (lines 188–188)

```
def unconsumed_tail(self) -> bytes
```

**Purpose**: This protocol property describes the bytes a decompressor has not yet consumed. It lets the token-usage parser work with gzip or deflate streams without depending on one concrete decompressor class.

**Data flow**: A decompressor object exposes leftover compressed bytes through this property. The parser reads those bytes and feeds them back through the decoding loop until there is nothing useful left.

**Call relations**: It is part of the small interface used by HttpTokenUsage._decode when response bodies are compressed.


##### `_ContentDecoder.decompress`  (lines 190–190)

```
def decompress(self, data: bytes, max_length: int=0) -> bytes
```

**Purpose**: This protocol method describes how compressed bytes are turned into plain bytes. It allows the parser to limit how much decoded data it accepts.

**Data flow**: Compressed input bytes and an optional maximum decoded length go in. Plain decoded bytes come out, while any unused compressed data remains available through unconsumed_tail.

**Call relations**: HttpTokenUsage._decode relies on this shape when reading compressed model responses.


##### `_ContentDecoder.flush`  (lines 192–192)

```
def flush(self) -> bytes
```

**Purpose**: This protocol method finishes a compressed stream and returns any final decoded bytes. It matters because useful usage data can appear at the end of a response.

**Data flow**: The decoder's internal buffered state goes in implicitly. The remaining plain bytes come out, or an error is treated as an unreadable response.

**Call relations**: HttpTokenUsage._finish_decoder calls it before final token usage is reported.


##### `generate_ca`  (lines 198–221)

```
async def generate_ca() -> tuple[str, str]
```

**Purpose**: Creates the proxy's private certificate authority, which is the root certificate the sandbox trusts. Without this, the proxy could not safely inspect HTTPS requests that need credential injection.

**Data flow**: It creates a temporary directory, asks openssl to make a self-signed certificate and key, reads both files, and returns their text. The temporary files disappear afterward.

**Call relations**: It calls _openssl to do the certificate work. The resulting certificate and key are later supplied to EgressProxy.start and EgressProxy._leaf_context.

*Call graph*: calls 1 internal fn (_openssl); 2 external calls (Path, TemporaryDirectory).


##### `_openssl`  (lines 224–230)

```
async def _openssl(*argv: str) -> None
```

**Purpose**: Runs the openssl command-line tool and turns failures into Python errors. This keeps certificate generation in one place.

**Data flow**: OpenSSL arguments go in. The command runs with quiet standard output; if it succeeds nothing is returned, and if it fails an error with stderr text is raised.

**Call relations**: generate_ca uses it to make the root CA. EgressProxy.start uses it for the shared leaf key, and EgressProxy._leaf_context uses it to mint per-host certificates.

*Call graph*: called by 3 (_leaf_context, start, generate_ca); 1 external calls (create_subprocess_exec).


##### `PerAgentRules.resolve`  (lines 271–311)

```
async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: Builds the exact allow-and-inject rule list for the agent named by a token. This is what keeps one agent or workspace from using another agent's network access or secrets.

**Data flow**: A run token, probe token, or no token goes in. The function reads the relevant turn or conversation, enters the workspace and agent context, adds internet, cache, credential, grant, and CLI rules as appropriate, and returns a tuple of rules.

**Call relations**: EgressProxy gets rule sets through this resolver. It asks _turn_of or _conversation_of for identity, calls rule-derivation helpers for credentials and grants, and removes model-key injection for probes through _without_the_model_key.

*Call graph*: calls 3 internal fn (_conversation_of, _turn_of, _without_the_model_key); 6 external calls (__init__, agent, derive_cli_rules, derive_credential_rules, derive_grant_rules, ws).


##### `PerAgentRules._turn_of`  (lines 313–337)

```
async def _turn_of(self, run: RunToken) -> _Authority | None
```

**Purpose**: Looks up which agent owns a running turn token and whether that agent was allowed internet access. It gives rule building the identity it should use.

**Data flow**: A run token goes in. The database is queried for the matching turn and agent in the same workspace; either an _Authority object comes out or None if the turn is unknown.

**Call relations**: PerAgentRules.resolve calls this for run tokens before deriving per-agent rules.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._conversation_of`  (lines 339–368)

```
async def _conversation_of(self, probe: ProbeToken) -> _Authority | None
```

**Purpose**: Looks up the agent and internet policy for a probe token, which names a conversation instead of a turn. This lets background or probe work inherit the conversation's agent scope.

**Data flow**: A probe token goes in. The database is queried through the conversation table; the result is an _Authority with the token's acting member, or None if no matching conversation exists.

**Call relations**: PerAgentRules.resolve calls this for probe tokens before deriving rules.

*Call graph*: called by 1 (resolve); 3 external calls (__init__, select, workspace_tx).


##### `PerAgentRules._without_the_model_key`  (lines 370–381)

```
def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]
```

**Purpose**: Removes the deployment's model API key injection from a probe's rules. This prevents unattended probe work from spending the deployment's model budget.

**Data flow**: A tuple of rules goes in. The function filters out only injection rules whose sentinel is the model-key sentinel, and returns the remaining rules.

**Call relations**: PerAgentRules.resolve applies it only for ProbeToken principals.

*Call graph*: called by 1 (resolve).


##### `PerAgentRules.turn_live`  (lines 383–414)

```
async def turn_live(self, run: RunToken) -> int | None
```

**Purpose**: Checks whether a run token still points to a turn marked as running. It is the fresh safety check before a connection is allowed to use secrets.

**Data flow**: A run token goes in. The database is read for the turn status and workspace egress-rule generation; the generation number comes out if the turn is running, otherwise None.

**Call relations**: This is intended to be wired as EgressProxy's turn authorizer, which EgressProxy._authorized calls through _turn_authorized.

*Call graph*: 3 external calls (select, workspace_tx, ws).


##### `PerAgentRules.rules_generation`  (lines 416–427)

```
async def rules_generation(self, workspace_id: UUID) -> int
```

**Purpose**: Reads the workspace's current egress-rule generation number. That number lets cached rules be invalidated when grants or credentials change.

**Data flow**: A workspace id goes in. The database returns the workspace's egress_rules_generation integer.

**Call relations**: This is intended to be wired into EgressProxy for probe authorization, where there is no turn row to read.

*Call graph*: 3 external calls (select, workspace_tx, ws).


##### `EgressProxy.probe_tokens`  (lines 458–462)

```
def probe_tokens(self) -> ProbeTokenCodec
```

**Purpose**: Creates the decoder for probe tokens using the same secret as run tokens. This ensures both token types are signed by the same deployment authority.

**Data flow**: The proxy's run token codec is read. A ProbeTokenCodec with the same secret is returned.

**Call relations**: EgressProxy._principal uses this property when a Proxy-Authorization header is not a valid run token.

*Call graph*: 1 external calls (__init__).


##### `EgressProxy.start`  (lines 464–480)

```
async def start(self, bind_host: str=PROXY_BIND_HOST, port: int=0, public_url: str | None=None) -> ProxyEndpoint
```

**Purpose**: Starts listening for sandbox proxy connections and prepares the certificate files needed for HTTPS interception. It returns the endpoint information that sandboxes need to use the proxy.

**Data flow**: A bind host, port, and optional public URL go in. The proxy writes CA files, creates a leaf signing key, starts an asyncio TCP server, and returns a ProxyEndpoint with the chosen port and CA certificate.

**Call relations**: It uses _openssl for key creation and registers EgressProxy._handle as the per-connection handler.

*Call graph*: calls 1 internal fn (_openssl); 4 external calls (__init__, start_server, Path, TemporaryDirectory).


##### `EgressProxy.stop`  (lines 482–519)

```
async def stop(self, graceful_shutdown_seconds: int=0) -> None
```

**Purpose**: Shuts the proxy down without leaving sockets, temporary certificate files, or background metering work dangling. It tries to give active connections a grace period, then cancels what remains.

**Data flow**: A grace period in seconds goes in. The listener is closed, tracked connection and rule-resolution tasks are drained or cancelled, the meter worker is stopped, and the temporary work directory is cleaned up.

**Call relations**: It is the counterpart to EgressProxy.start and coordinates cleanup of tasks created by _handle, _rules_for, and _enqueue_meter.

*Call graph*: 3 external calls (gather, wait, monotonic).


##### `EgressProxy._handle`  (lines 521–638)

```
async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None
```

**Purpose**: Processes one incoming proxy connection from a sandbox. It authenticates the request, checks host rules, enforces connection limits, and chooses the right relay path.

**Data flow**: A stream reader and writer go in. The function reads the CONNECT request and token, turns the token into a principal, authorizes it, fetches rules, then either refuses, sends the request to the cache service, tunnels bytes, or performs HTTPS interception.

**Call relations**: asyncio.start_server calls this for each connection. It delegates identity to _principal, authorization to _authorized, rules to _rules_for, and traffic handling to _service, _tunnel, or _mitm.

*Call graph*: calls 8 internal fn (_authorized, _mitm, _principal, _rules_for, _service, _tunnel, _read_request_head, _respond); 3 external calls (__init__, close, current_task).


##### `EgressProxy._authorized`  (lines 640–655)

```
async def _authorized(self, principal: EgressPrincipal) -> int | None
```

**Purpose**: Decides whether a principal may make a new connection right now. It treats run tokens and probe tokens differently because runs depend on database turn state while probes depend on an expiry time.

**Data flow**: A principal goes in. For a probe, the expiry time is compared with the current time and the workspace generation is read; for a run, the turn authorizer is called. A generation number or None comes out.

**Call relations**: EgressProxy._handle calls this before any host rules are used.

*Call graph*: calls 1 internal fn (_turn_authorized); called by 1 (_handle); 1 external calls (now).


##### `EgressProxy._turn_authorized`  (lines 657–670)

```
async def _turn_authorized(self, run: RunToken) -> int | None
```

**Purpose**: Wraps the run-token authorization check and logs failures in one place. It keeps authorization errors visible while still letting the caller fail closed.

**Data flow**: A run token goes in. The configured authorizer returns a generation number or None; if it raises, the error is logged and raised again.

**Call relations**: EgressProxy._authorized calls this for RunToken principals.

*Call graph*: called by 1 (_authorized); 1 external calls (log_error).


##### `EgressProxy._rules_for`  (lines 672–698)

```
async def _rules_for(self, principal: EgressPrincipal | None, generation: int | None=None) -> tuple[Rule, ...]
```

**Purpose**: Returns the rule set for a principal, using a short-lived cache keyed by the principal and rule generation. This saves repeated database and secret-store work without keeping revoked access alive too long.

**Data flow**: A principal and optional generation go in. A valid cached rule tuple is returned if present; otherwise one shared background resolution is started or awaited, and its rules are returned.

**Call relations**: EgressProxy._handle calls it after authorization. It uses _rule_key and EgressProxy._resolve_rules, shielding shared work so one cancelled connection does not cancel everyone else's rule lookup.

*Call graph*: calls 2 internal fn (_resolve_rules, _rule_key); called by 1 (_handle); 3 external calls (create_task, shield, monotonic).


##### `EgressProxy._resolve_rules`  (lines 700–725)

```
async def _resolve_rules(self, key: _RuleKey, principal: EgressPrincipal, generation: int) -> tuple[Rule, ...]
```

**Purpose**: Performs the actual rule resolution and stores the result in the cache. It also logs rule-resolution errors once for all callers waiting on the same lookup.

**Data flow**: A cache key, principal, and generation go in. The configured resolver returns rules, which are cached with a time limit; errors are logged and re-raised.

**Call relations**: EgressProxy._rules_for creates tasks for this function when the cache misses.

*Call graph*: called by 1 (_rules_for); 4 external calls (__init__, current_task, monotonic, log_error).


##### `EgressProxy._principal`  (lines 727–740)

```
def _principal(self, proxy_auth: str) -> EgressPrincipal | None
```

**Purpose**: Turns the Proxy-Authorization header into a trusted identity, if possible. Invalid, missing, or forged tokens become no identity and therefore no network access.

**Data flow**: The raw proxy authorization string goes in. The function first tries to decode it as a run token, then as a probe token, and returns the decoded token or None.

**Call relations**: EgressProxy._handle calls this early in every CONNECT request.

*Call graph*: called by 1 (_handle).


##### `EgressProxy._resolve_public_address`  (lines 742–774)

```
async def _resolve_public_address(self, host: str, port: int) -> str
```

**Purpose**: Resolves a hostname for broad internet access while blocking private networks and IPv6. This prevents a sandbox from using public internet permission to reach internal infrastructure.

**Data flow**: A host and port go in. The host is parsed or looked up in DNS for A records, all results are checked to be globally routable IPv4 addresses, and the first allowed IP address string is returned.

**Call relations**: EgressProxy._handle uses it when a host is not exactly scoped but InternetRule access is available.

*Call graph*: 1 external calls (IPv4Address).


##### `EgressProxy._tunnel`  (lines 776–804)

```
async def _tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, principal: EgressPrincipal, rules: tuple[Rule, ...], connect_host: str) -> None
```

**Purpose**: Relays an allowed HTTPS connection without decrypting it. This is used when no credential injection or broker forwarding is needed.

**Data flow**: Client streams, destination host and port, principal, rules, and resolved connect host go in. The proxy opens the upstream TCP connection, sends CONNECT success, records metering, and relays bytes both ways.

**Call relations**: EgressProxy._handle calls this for allowed hosts with no InjectionRule or ForwardRule. It uses _relay for the byte copying and metering helpers for accounting.

*Call graph*: calls 4 internal fn (_meter, _meter_ledger, _relay, _respond); called by 1 (_handle); 4 external calls (drain, write, open_connection, wait_for).


##### `EgressProxy._mitm`  (lines 806–866)

```
async def _mitm(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, port: int, injections: list[InjectionRule], forwards: list[ForwardRule], principal: EgressPrincipal, rules:
```

**Purpose**: Intercepts an allowed HTTPS request when the proxy must swap in a secret or route through a broker. The sandbox sees a trusted temporary certificate, while the upstream service still receives a normal verified TLS connection.

**Data flow**: Client streams, host, port, matching injection and forward rules, principal, and all rules go in. The proxy upgrades the client side to TLS, reads one HTTP request, chooses broker forwarding if a sentinel matches, otherwise opens upstream TLS, rewrites headers, relays the response, and may collect token usage.

**Call relations**: EgressProxy._handle calls it for hosts with injection or forwarding rules. It uses _leaf_context, _start_tls_server, _forward_match, _inject, _relay, _forward_broker, and metering helpers.

*Call graph*: calls 11 internal fn (_forward_broker, _leaf_context, _meter, _meter_ledger, _meter_tokens, _forward_match, _inject, _read_request_head, _relay, _respond (+1 more)); called by 1 (_handle); 3 external calls (__init__, open_connection, wait_for).


##### `EgressProxy._service`  (lines 868–928)

```
async def _service(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, principal: EgressPrincipal, rule: ServiceRule) -> None
```

**Purpose**: Routes an allowed request through the local cache daemon, while stamping trusted workspace and user identity. If the cache is unavailable, it can fall through to the real origin when safe.

**Data flow**: Client streams, host, principal, and a ServiceRule go in. The proxy decrypts one request, rewrites the target for cache routing if needed, strips untrusted identity headers, connects to the daemon, meters the real origin, and relays the response.

**Call relations**: EgressProxy._handle calls it for cache service rules. It uses _service_origin, _prefix_target, _service_headers, _service_direct, and _relay.

*Call graph*: calls 10 internal fn (_leaf_context, _meter_service, _service_direct, _prefix_target, _read_request_head, _relay, _respond, _service_headers, _service_origin, _start_tls_server); called by 1 (_handle); 2 external calls (open_connection, wait_for).


##### `EgressProxy._service_direct`  (lines 930–967)

```
async def _service_direct(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, fallthrough: tuple[str, bytes] | None, headers: list[bytes], principal: EgressPrincipal) -> No
```

**Purpose**: Sends a cache-routed request directly to its origin when the cache daemon is down. This keeps cache outages from breaking safe fetches.

**Data flow**: Client streams, an optional fallthrough origin, original headers, and principal go in. If the origin is safe, the proxy opens HTTPS to it, rewrites headers for direct use, meters the request, and relays bytes; otherwise it returns a cache-unavailable response.

**Call relations**: EgressProxy._service calls this only after it cannot connect to the cache daemon.

*Call graph*: calls 4 internal fn (_meter_service, _direct_headers, _relay, _respond); called by 1 (_service); 2 external calls (open_connection, wait_for).


##### `EgressProxy._forward_broker`  (lines 969–1013)

```
async def _forward_broker(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, rule: ForwardRule, request: tuple[bytes, list[bytes]], host: str, principal: EgressPrincipal,
```

**Purpose**: Executes a request through an OAuth grant broker instead of sending credentials through the sandbox or proxy-to-provider wire. This is like asking a trusted courier to make the request on the user's account.

**Data flow**: Client streams, a ForwardRule, request head, host, principal, and rules go in. The body is read with a size limit, headers are cleaned, the broker is called, metering is recorded, and the broker's response is written back as HTTP.

**Call relations**: EgressProxy._mitm calls it when _forward_match finds a grant sentinel in the request headers.

*Call graph*: calls 7 internal fn (_meter, _meter_ledger, _drain_refused_body, _forward_headers, _forward_response_bytes, _read_request_body, _respond); called by 1 (_mitm); 3 external calls (drain, write, log).


##### `EgressProxy._leaf_context`  (lines 1015–1072)

```
async def _leaf_context(self, host: str) -> ssl.SSLContext
```

**Purpose**: Creates or reuses a TLS server context with a certificate for the requested host. This is what lets the proxy decrypt sandbox traffic for approved MITM cases.

**Data flow**: A host name goes in. The function safely builds temporary certificate file names, uses openssl to create and sign a host certificate, loads it into an SSL context, caches it, and returns the context.

**Call relations**: EgressProxy._mitm and EgressProxy._service call it before starting TLS with the sandbox.

*Call graph*: calls 1 internal fn (_openssl); called by 2 (_mitm, _service); 4 external calls (Path, SSLContext, contained_file, contained_leaf).


##### `EgressProxy._meter_service`  (lines 1074–1082)

```
async def _meter_service(self, host: str, principal: EgressPrincipal) -> None
```

**Purpose**: Records one cache-routed request against the real upstream host rather than the cache daemon address. This keeps billing the same whether the cache is hit, missed, or bypassed.

**Data flow**: A billed host and principal go in. A request MeterRule is made, a metric is emitted, and a ledger record is queued.

**Call relations**: EgressProxy._service and EgressProxy._service_direct call it after choosing the real origin.

*Call graph*: calls 2 internal fn (_meter, _meter_ledger); called by 2 (_service, _service_direct); 1 external calls (__init__).


##### `EgressProxy._meter`  (lines 1084–1087)

```
def _meter(self, host: str, rules: tuple[Rule, ...]) -> None
```

**Purpose**: Emits immediate in-memory observability metrics for metered hosts. This is separate from database billing so dashboards can update quickly.

**Data flow**: A host and rule tuple go in. For every MeterRule matching that host, a sandbox_egress_total metric is emitted with the rule's dimension.

**Call relations**: _tunnel, _mitm, _forward_broker, and _meter_service call it when a metered request or connection is accepted.

*Call graph*: called by 4 (_forward_broker, _meter_service, _mitm, _tunnel); 1 external calls (emit_metric).


##### `EgressProxy._meter_ledger`  (lines 1089–1098)

```
async def _meter_ledger(self, host: str, principal: EgressPrincipal, rules: tuple[Rule, ...]) -> None
```

**Purpose**: Queues billable egress request records for durable accounting. It ignores token-only meter rules because model token billing is handled separately.

**Data flow**: A host, principal, and rules go in. If a non-token MeterRule applies, it creates an _EgressMeter for the workspace and optional turn and enqueues it.

**Call relations**: Traffic paths call it after a request or tunnel is accepted; it hands work to _enqueue_meter.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 4 (_forward_broker, _meter_service, _mitm, _tunnel); 1 external calls (__init__).


##### `EgressProxy._meter_tokens`  (lines 1100–1122)

```
async def _meter_tokens(self, principal: EgressPrincipal, accumulator: 'HttpTokenUsage') -> None
```

**Purpose**: Queues billable model token usage parsed from a model response. It refuses to bill probes for model usage because probes should not receive the deployment model key.

**Data flow**: A principal and HttpTokenUsage accumulator go in. If usage was parsed for a run token, a _TokenMeter is enqueued; missing usage is logged, and probe usage is warned about but not billed.

**Call relations**: EgressProxy._mitm calls it after relaying a model-host response through an HttpTokenUsage accumulator.

*Call graph*: calls 1 internal fn (_enqueue_meter); called by 1 (_mitm); 3 external calls (__init__, log, warn).


##### `EgressProxy._enqueue_meter`  (lines 1124–1131)

```
async def _enqueue_meter(self, record: _MeterRecord) -> None
```

**Purpose**: Adds a metering record to the background queue and starts the meter worker if needed. This keeps accounting writes off the network relay path.

**Data flow**: An egress or token meter record goes in. A background task is created if absent, and the record is put on the queue.

**Call relations**: _meter_ledger and _meter_tokens call it. It starts EgressProxy._meter_loop.

*Call graph*: calls 1 internal fn (_meter_loop); called by 2 (_meter_ledger, _meter_tokens); 1 external calls (create_task).


##### `EgressProxy._meter_loop`  (lines 1133–1165)

```
async def _meter_loop(self) -> None
```

**Purpose**: Consumes metering records in small batches and writes them to accounting. Batching reduces database overhead during busy proxy traffic.

**Data flow**: Records arrive from the internal queue. The loop waits briefly to collect more records, writes a batch, marks queue items done, and exits when it receives a stop marker.

**Call relations**: _enqueue_meter starts this loop. It delegates database work to _write_meter_batch and logs failures without stopping request handling.

*Call graph*: calls 1 internal fn (_write_meter_batch); called by 1 (_enqueue_meter); 2 external calls (sleep, log_error).


##### `EgressProxy._write_meter_batch`  (lines 1167–1209)

```
async def _write_meter_batch(self, records: list[_MeterRecord]) -> None
```

**Purpose**: Groups queued metering records by workspace and turn before writing them. This combines many small events into fewer accounting operations.

**Data flow**: A list of meter records goes in. Request counts and token usage are summed per billed identity, then each workspace is opened and _write_billed is called.

**Call relations**: _meter_loop calls it for each collected batch.

*Call graph*: calls 1 internal fn (_write_billed); called by 1 (_meter_loop); 4 external calls (__init__, workspace_tx, log_error, ws).


##### `EgressProxy._write_billed`  (lines 1211–1231)

```
async def _write_billed(self, connection: AsyncConnection, workspace_id: UUID, turn_id: UUID | None, requests: int | None, tokens: dict[str, Usage]) -> None
```

**Purpose**: Writes one workspace/turn accounting bundle to the ledger. It knows the difference between normal turn billing and probe billing.

**Data flow**: A database connection, workspace id, optional turn id, optional request count, and token usage map go in. Probe request counts are written as probe egress; turn request counts and sandbox token usage are written to their turn.

**Call relations**: _write_meter_batch calls it after grouping records. It hands final writes to accounting helpers.

*Call graph*: called by 1 (_write_meter_batch); 3 external calls (record_egress_request, record_probe_egress_request, record_sandbox_tokens).


##### `_rule_key`  (lines 1234–1246)

```
def _rule_key(principal: EgressPrincipal) -> _RuleKey
```

**Purpose**: Builds the cache key used for resolved rules. It avoids wasting cache entries for probe tokens that differ only by per-exec details that do not affect rules.

**Data flow**: A run or probe principal goes in. Run tokens return themselves as the key; probe tokens are reduced to workspace, conversation, and acting member.

**Call relations**: EgressProxy._rules_for calls it before checking or filling the rule cache.

*Call graph*: called by 1 (_rules_for); 1 external calls (__init__).


##### `_read_fault`  (lines 1249–1255)

```
def _read_fault(task: asyncio.Task[tuple[Rule, ...]]) -> None
```

**Purpose**: Retrieves an exception from a completed background rule-resolution task so asyncio does not log it as an unhandled task failure. This avoids noisy or poorly redacted logs.

**Data flow**: A completed task goes in. If it was not cancelled, its exception is read and discarded unless another waiter is already observing it.

**Call relations**: EgressProxy._rules_for attaches it as a done callback to shared rule-resolution tasks.


##### `_start_tls_server`  (lines 1258–1274)

```
async def _start_tls_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]
```

**Purpose**: Completes the CONNECT response and upgrades the existing client connection into server-side TLS. This lets later code read decrypted HTTP bytes from the same stream objects.

**Data flow**: A reader, writer, and SSL context go in. The proxy pauses plaintext reading, sends CONNECT success, starts TLS on the transport, updates the stream writer, and returns the same reader and writer.

**Call relations**: EgressProxy._mitm and EgressProxy._service call it before reading HTTPS requests from the sandbox.

*Call graph*: called by 2 (_mitm, _service); 3 external calls (drain, write, get_running_loop).


##### `_read_request_head`  (lines 1277–1310)

```
async def _read_request_head(reader: asyncio.StreamReader) -> tuple[bytes, list[bytes]] | _HeaderRefusal | None
```

**Purpose**: Reads an HTTP request line and headers with time and size limits. This protects the proxy from clients that never finish headers or send enormous headers.

**Data flow**: A stream reader goes in. The function returns a request line plus header lines, None for clean EOF, or a _HeaderRefusal explaining timeout or oversize headers.

**Call relations**: EgressProxy._handle uses it for CONNECT requests, and _mitm and _service use it after TLS starts.

*Call graph*: called by 3 (_handle, _mitm, _service); 3 external calls (__init__, readline, timeout).


##### `_forward_match`  (lines 1313–1327)

```
def _forward_match(headers: list[bytes], candidates: list[ForwardRule]) -> ForwardRule | None
```

**Purpose**: Finds which broker-forward rule matches a sentinel credential in the request headers. Exact sentinel matching keeps two accounts on the same host from being confused.

**Data flow**: Request headers and candidate ForwardRules go in. The function scans header values, allowing forms like Bearer sentinel or token sentinel, and returns the matching rule or None.

**Call relations**: EgressProxy._mitm calls it before deciding whether to use _forward_broker or direct upstream injection.

*Call graph*: called by 1 (_mitm).


##### `_read_request_body`  (lines 1344–1397)

```
async def _read_request_body(reader: asyncio.StreamReader, headers: list[bytes]) -> bytes | _Refusal
```

**Purpose**: Reads the full body for a broker-forwarded request, but only if it is safely bounded by Content-Length. The broker expects one complete request body, not an endless stream.

**Data flow**: A reader and headers go in. The declared body length is checked; valid bodies are read and returned as bytes, while chunked, missing, too-large, negative, or truncated bodies return a _Refusal.

**Call relations**: EgressProxy._forward_broker calls it before invoking the broker.

*Call graph*: called by 1 (_forward_broker); 2 external calls (__init__, readexactly).


##### `_drain_refused_body`  (lines 1400–1417)

```
async def _drain_refused_body(reader: asyncio.StreamReader, pending: int) -> None
```

**Purpose**: Discards unread upload bytes after the proxy has already decided to refuse a forwarded request. This gives the client a chance to finish writing and read the clear refusal response.

**Data flow**: A reader and maximum pending byte count go in. The function reads and drops bytes until the limit, a timeout, or EOF is reached.

**Call relations**: EgressProxy._forward_broker calls it after sending a refusal from _read_request_body.

*Call graph*: called by 1 (_forward_broker); 2 external calls (read, timeout).


##### `_forward_headers`  (lines 1420–1432)

```
def _forward_headers(headers: list[bytes], rule: ForwardRule) -> dict[str, str]
```

**Purpose**: Builds the header dictionary sent to the broker. It removes the sentinel credential header and connection-specific headers that the broker or its HTTP client should recreate.

**Data flow**: Original headers and the matching ForwardRule go in. A plain string dictionary of safe headers comes out.

**Call relations**: EgressProxy._forward_broker passes its output to the grant broker's forward call.

*Call graph*: called by 1 (_forward_broker).


##### `_forward_response_bytes`  (lines 1435–1453)

```
def _forward_response_bytes(response: ForwardedResponse) -> bytes
```

**Purpose**: Turns a broker response object back into a raw HTTP response for the sandbox. It also drops unsafe or misleading headers.

**Data flow**: A ForwardedResponse goes in. The function creates a status line, filters headers that could break framing or inject new lines, adds content-length and connection close, and appends the body.

**Call relations**: EgressProxy._forward_broker writes these bytes to the client after a successful broker call.

*Call graph*: calls 1 internal fn (_has_crlf); called by 1 (_forward_broker); 1 external calls (HTTPStatus).


##### `_has_crlf`  (lines 1456–1457)

```
def _has_crlf(value: str) -> bool
```

**Purpose**: Checks whether a string contains carriage return or newline characters. These characters are dangerous inside HTTP header names or values.

**Data flow**: A string goes in. A boolean comes out indicating whether it contains CR or LF.

**Call relations**: _forward_response_bytes uses it to reject broker-provided headers that could split the HTTP response.

*Call graph*: called by 1 (_forward_response_bytes).


##### `_service_headers`  (lines 1472–1486)

```
def _service_headers(headers: list[bytes], principal: EgressPrincipal) -> bytes
```

**Purpose**: Builds the headers sent to the cache daemon with trusted proxy-stamped identity. It strips any workspace or user identity the sandbox tried to claim for itself.

**Data flow**: Original headers and a principal go in. Unsafe headers are removed, then x-ufo-workspace, x-ufo-user, and connection close are added.

**Call relations**: EgressProxy._service sends its output to the cache daemon.

*Call graph*: called by 1 (_service).


##### `_prefix_target`  (lines 1489–1497)

```
def _prefix_target(request_line: bytes, prefix: str) -> bytes
```

**Purpose**: Rewrites a request path so a package registry request is addressed to the cache daemon's package route. It leaves malformed request lines unchanged.

**Data flow**: A raw request line and prefix go in. If the line has method, target, and version, the prefix is inserted before the target path and the new request line is returned.

**Call relations**: EgressProxy._service calls it for ServiceRules that represent package cache interception.

*Call graph*: called by 1 (_service).


##### `_service_origin`  (lines 1500–1515)

```
def _service_origin(request_line: bytes) -> tuple[str, bytes] | None
```

**Purpose**: Extracts the real origin host from a git cache request path. This is used both for billing and for safe direct fallback if the cache is down.

**Data flow**: A raw request line goes in. If it looks like /git/<host>/<rest>, the function returns the host and a rewritten request line for the origin; otherwise it returns None.

**Call relations**: EgressProxy._service calls it for git-style cache requests.

*Call graph*: called by 1 (_service).


##### `_direct_headers`  (lines 1531–1538)

```
def _direct_headers(headers: list[bytes], host: str) -> bytes
```

**Purpose**: Builds headers for direct cache-fallback requests to the real origin. It removes cache-only identity headers and sets the correct Host header.

**Data flow**: Original headers and the origin host go in. A raw header block comes out with unsafe headers stripped, host corrected, and connection close added.

**Call relations**: EgressProxy._service_direct writes these headers to the upstream origin.

*Call graph*: called by 1 (_service_direct).


##### `_inject`  (lines 1541–1579)

```
def _inject(headers: list[bytes], candidates: list[InjectionRule]) -> bytes
```

**Purpose**: Rewrites request headers by replacing approved sentinel values with real secrets. A sentinel is a harmless placeholder the sandbox can hold; the real key is inserted only inside the proxy.

**Data flow**: Original headers and candidate InjectionRules go in. Matching authorization-style header values are replaced with their real secret, connection headers are removed, and connection close is added.

**Call relations**: EgressProxy._mitm uses it before sending a request to the real upstream service.

*Call graph*: called by 1 (_mitm).


##### `_relay`  (lines 1582–1616)

```
async def _relay(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, upstream_reader: asyncio.StreamReader, upstream_writer: asyncio.StreamWriter, on_downstream: Callable[[bytes]
```

**Purpose**: Copies bytes in both directions between the sandbox and upstream connection. It has special care for the common case where the client finishes uploading but the server is still streaming a response.

**Data flow**: Client and upstream readers/writers go in, plus an optional callback for downstream chunks. Two pump tasks move data both ways; if upload ends first, the response side is kept alive while progress continues.

**Call relations**: _tunnel, _mitm, _service, and _service_direct all use it for the actual byte relay. It delegates each direction to _pump.

*Call graph*: calls 1 internal fn (_pump); called by 4 (_mitm, _service, _service_direct, _tunnel); 7 external calls (Event, can_write_eof, close, write_eof, create_task, timeout, wait).


##### `_pump`  (lines 1619–1634)

```
async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, on_chunk: Callable[[bytes], None] | None=None, on_progress: Callable[[], None] | None=None) -> None
```

**Purpose**: Copies chunks from one stream reader to one stream writer. It is the simple worker used by the bidirectional relay.

**Data flow**: A reader, writer, and optional callbacks go in. Chunks are read, written, flushed, and reported until EOF, cancellation, or socket error.

**Call relations**: _relay creates _pump tasks for upstream-to-client and client-to-upstream traffic.

*Call graph*: called by 1 (_relay); 3 external calls (read, drain, write).


##### `_int_field`  (lines 1637–1639)

```
def _int_field(usage: dict[str, object], name: str) -> int
```

**Purpose**: Safely reads an integer field from a JSON-like dictionary. It treats missing values, booleans, and non-integers as zero.

**Data flow**: A dictionary and field name go in. A clean integer count comes out.

**Call relations**: HttpTokenUsage's Anthropic and OpenAI parsers use it, and _cached_field builds on it.

*Call graph*: called by 3 (_absorb_anthropic, _openai, _cached_field).


##### `_cached_field`  (lines 1642–1644)

```
def _cached_field(usage: dict[str, object], details_name: str) -> int
```

**Purpose**: Reads cached-token counts from nested usage-detail objects. This normalizes provider response shapes into the project's Usage model.

**Data flow**: A usage dictionary and nested detail-field name go in. If the nested object contains cached_tokens as an integer, that number comes out; otherwise zero comes out.

**Call relations**: HttpTokenUsage._openai calls it when parsing OpenAI token usage.

*Call graph*: calls 1 internal fn (_int_field); called by 1 (_openai).


##### `HttpTokenUsage.feed`  (lines 1671–1708)

```
def feed(self, chunk: bytes) -> None
```

**Purpose**: Accepts raw response bytes and starts extracting model token usage from them. It understands HTTP headers, chunked transfer encoding, and gzip or deflate compression.

**Data flow**: A response chunk goes in. The function collects headers until complete, configures body decoding, then passes body bytes into the wire-body parser; bad or oversized input marks parsing as failed.

**Call relations**: EgressProxy._mitm gives this method to _relay as the downstream-chunk callback for metered model hosts.

*Call graph*: calls 2 internal fn (_fail, _feed_wire_body); 1 external calls (decompressobj).


##### `HttpTokenUsage.usage`  (lines 1710–1722)

```
def usage(self) -> tuple[str, Usage] | None
```

**Purpose**: Returns the parsed model name and token counts, if any were found. It finalizes any decompression before deciding.

**Data flow**: The accumulator's buffered response state goes in implicitly. It flushes the decoder, tries to parse any remaining body as JSON, and returns a model plus Usage object or None.

**Call relations**: EgressProxy._meter_tokens calls it after the relayed model response finishes.

*Call graph*: calls 2 internal fn (_finish_decoder, _maybe_json_body); 1 external calls (__init__).


##### `HttpTokenUsage._feed_wire_body`  (lines 1724–1770)

```
def _feed_wire_body(self, chunk: bytes) -> None
```

**Purpose**: Turns the HTTP wire body into actual payload bytes, including chunked transfer decoding. It is the bridge between HTTP framing and JSON/SSE parsing.

**Data flow**: Raw body bytes go in. For non-chunked responses they are decoded directly; for chunked responses the chunk sizes and boundaries are parsed before payload bytes are decoded.

**Call relations**: HttpTokenUsage.feed calls it after headers are complete.

*Call graph*: calls 3 internal fn (_decode, _fail, _finish_decoder); called by 1 (feed).


##### `HttpTokenUsage._decode`  (lines 1772–1788)

```
def _decode(self, chunk: bytes) -> None
```

**Purpose**: Applies content decompression when needed and passes plain bytes onward. It limits decoded data so a compressed response cannot expand without bound.

**Data flow**: Body payload bytes go in. They are either passed through unchanged or decompressed in controlled pieces, then sent to _feed_body; decompression errors fail parsing.

**Call relations**: HttpTokenUsage._feed_wire_body calls it for each body payload segment.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 1 (_feed_wire_body).


##### `HttpTokenUsage._finish_decoder`  (lines 1790–1799)

```
def _finish_decoder(self) -> None
```

**Purpose**: Flushes the decompressor once, so final buffered bytes are not missed. It is safe to call multiple times.

**Data flow**: Internal decompressor state goes in implicitly. Any final decoded bytes are fed into _feed_body, or parsing is failed on decompression error.

**Call relations**: HttpTokenUsage.usage calls it before returning usage, and _feed_wire_body calls it when a chunked body reaches its final chunk.

*Call graph*: calls 2 internal fn (_fail, _feed_body); called by 2 (_feed_wire_body, usage).


##### `HttpTokenUsage._feed_body`  (lines 1801–1812)

```
def _feed_body(self, chunk: bytes) -> None
```

**Purpose**: Buffers decoded response body bytes and processes complete lines. This is useful for server-sent events, where each line may carry one JSON event.

**Data flow**: Decoded bytes go in. Complete newline-delimited lines are passed to _consume, processed bytes are removed from the buffer, and oversized buffers fail parsing.

**Call relations**: HttpTokenUsage._decode and _finish_decoder pass decoded data here.

*Call graph*: calls 2 internal fn (_consume, _fail); called by 2 (_decode, _finish_decoder).


##### `HttpTokenUsage._consume`  (lines 1814–1831)

```
def _consume(self, line: bytes) -> None
```

**Purpose**: Looks at one decoded body line and tries to treat it as model usage data. It supports server-sent event lines beginning with data: and plain JSON-looking lines.

**Data flow**: One line of bytes goes in. JSON payloads are parsed and routed to the Anthropic or OpenAI parser depending on the response host.

**Call relations**: HttpTokenUsage._feed_body calls it for every complete line.

*Call graph*: calls 3 internal fn (_anthropic, _maybe_json_body, _openai); called by 1 (_feed_body); 1 external calls (loads).


##### `HttpTokenUsage._maybe_json_body`  (lines 1833–1848)

```
def _maybe_json_body(self, payload: bytes) -> None
```

**Purpose**: Tries to parse a whole non-streaming JSON response body for usage data. This covers model APIs that return one JSON document instead of server-sent events.

**Data flow**: A possible JSON byte string goes in. If it is a recognized provider response, model and usage fields are absorbed into the accumulator.

**Call relations**: HttpTokenUsage._consume calls it for non-SSE lines, and HttpTokenUsage.usage calls it on leftover buffered body before giving up.

*Call graph*: calls 2 internal fn (_absorb_anthropic, _openai); called by 2 (_consume, usage); 1 external calls (loads).


##### `HttpTokenUsage._anthropic`  (lines 1850–1860)

```
def _anthropic(self, event: dict[str, object]) -> None
```

**Purpose**: Parses Anthropic streaming usage events. It handles the start event for model and input counts and delta events for output counts.

**Data flow**: An Anthropic event dictionary goes in. Relevant model and usage fields update the accumulator's model, input, output, and cache counters.

**Call relations**: HttpTokenUsage._consume calls it when the response host is Anthropic.

*Call graph*: calls 1 internal fn (_absorb_anthropic); called by 1 (_consume).


##### `HttpTokenUsage._absorb_anthropic`  (lines 1862–1877)

```
def _absorb_anthropic(self, usage: object, initial: bool) -> None
```

**Purpose**: Copies Anthropic usage fields into the normalized Usage counters. It understands both current cache-creation detail fields and older flattened fields.

**Data flow**: A usage object and a flag saying whether it is initial usage go in. Initial counts set input and cache counters; output counts update the output counter; a successful parse marks usage as seen.

**Call relations**: HttpTokenUsage._anthropic and _maybe_json_body call it for Anthropic responses.

*Call graph*: calls 1 internal fn (_int_field); called by 2 (_anthropic, _maybe_json_body).


##### `HttpTokenUsage._openai`  (lines 1879–1903)

```
def _openai(self, event: dict[str, object]) -> None
```

**Purpose**: Parses OpenAI usage blocks from both Chat Completions and Responses API shapes. It logs when a usage object has an unknown shape rather than silently billing zero.

**Data flow**: An OpenAI event dictionary goes in. The model is recorded if present, token fields are read from the recognized usage format, and normalized counts are passed to _absorb_openai.

**Call relations**: HttpTokenUsage._consume and _maybe_json_body call it for OpenAI responses.

*Call graph*: calls 3 internal fn (_absorb_openai, _cached_field, _int_field); called by 2 (_consume, _maybe_json_body); 1 external calls (log).


##### `HttpTokenUsage._absorb_openai`  (lines 1905–1920)

```
def _absorb_openai(self, prompt: int, output: int, cached: int) -> None
```

**Purpose**: Normalizes OpenAI token counts into fresh input, output, and cache-read counts. OpenAI prompt counts include cached tokens, so this subtracts the cached portion.

**Data flow**: Prompt/input count, output count, and cached count go in. Cached tokens are clamped if impossible, then internal input, output, and cache-read counters are updated and usage is marked seen.

**Call relations**: HttpTokenUsage._openai calls it after reading an OpenAI usage block.

*Call graph*: called by 1 (_openai); 1 external calls (log).


##### `HttpTokenUsage._fail`  (lines 1922–1926)

```
def _fail(self) -> None
```

**Purpose**: Marks token-usage parsing as failed and clears buffered data. This prevents partial or unsafe data from being billed as if it were reliable.

**Data flow**: The accumulator's internal buffers go in implicitly. The overflow/failure flag is set and header, body, and chunk buffers are emptied.

**Call relations**: HttpTokenUsage.feed, _feed_wire_body, _decode, _finish_decoder, and _feed_body call it when parsing cannot safely continue.

*Call graph*: called by 5 (_decode, _feed_body, _feed_wire_body, _finish_decoder, feed).


##### `_respond`  (lines 1929–1947)

```
async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None
```

**Purpose**: Writes a final HTTP error or refusal response to the client with a readable plain-text message. This gives callers a clear reason instead of just a status code.

**Data flow**: A stream writer, status code, and message go in. The function writes an HTTP/1.1 response with content-length and connection close, then drains the writer; vanished clients are ignored.

**Call relations**: The proxy's request paths call it whenever they must refuse or fail a connection, including _handle, _tunnel, _mitm, _service, _service_direct, and _forward_broker.

*Call graph*: called by 6 (_forward_broker, _handle, _mitm, _service, _service_direct, _tunnel); 3 external calls (drain, write, HTTPStatus).


### `core/src/ufo/sandbox/cache.py`

`config` · `startup and sandbox setup`

This file is a small but important piece of configuration for UFO's sandbox cache. The cache is a service that sits between a sandbox and the public internet. Its job is to speed up repeated downloads and, more importantly, make sure sandboxes can only fetch from approved public sources rather than being redirected toward private network addresses.

The file names the special internal cache host, `cache.ufo.internal`, which the proxy recognizes. For Git downloads, the sandbox needs explicit Git settings so that fetching from allowed hosts such as GitHub goes through the cache. The helper `cache_git_config` builds those settings. Pushes are kept direct, so the cache is only used for reading code, not publishing it.

For package tools like npm, pip, Cargo, and Go, the sandbox does not need special settings. The proxy can transparently catch traffic to known public package registries and route it through the cache. Those approved hostnames are listed here so the proxy and daemon can stay aligned.

The file also defines how the system contacts a local cache daemon for control callbacks, including the callback host, port, and environment variable name for the control token. Finally, `parse_cache_daemon` turns a deploy-time `host:port` string into a usable address and deliberately fails if the format is wrong, because a bad cache address is considered a deployment mistake.

#### Function details

##### `cache_git_config`  (lines 33–41)

```
def cache_git_config() -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the Git configuration entries that make allowed Git fetches go through UFO's cache service. It is used when a sandbox needs Git to download from cached public hosts without changing normal push behavior.

**Data flow**: It reads the fixed cache host and the list of Git hosts that are allowed to be cached. For each allowed host, it creates two Git settings: one that rewrites fetch URLs toward the cache, and one that keeps pushes aimed at the original host. It returns those settings as an immutable tuple of key-value pairs.

**Call relations**: When sandbox setup needs Git to use the cache, this function supplies the exact Git settings to export or install. It does not contact the cache itself; it only hands back configuration that Git will later obey when fetches or pushes happen.


##### `parse_cache_daemon`  (lines 44–53)

```
def parse_cache_daemon(value: str | None) -> tuple[str, int] | None
```

**Purpose**: Turns an optional cache daemon address into a structured host and port. It lets deployments say where the local cache daemon is, while treating malformed addresses as real configuration errors.

**Data flow**: It receives either no value or a string expected to look like `host:port`. If the value is missing, it returns `None`, meaning this deployment is not using a cache daemon. If a value is present, it splits it at the last colon, checks that a host exists, converts the port text to a number, and returns `(host, port)`. If the text is not shaped correctly, it raises an error instead of silently disabling the cache.

**Call relations**: This function belongs in the deployment or startup path, where configuration text is converted into usable settings. Later cache-control code can rely on its result being either a clean `(host, port)` pair or `None`, rather than having to re-check the raw string itself.


### Workspace session model
These files define the stable per-conversation workspace, the safe sandbox access interface, and the host labels used to expose sandbox ports.

### `core/src/ufo/sandbox/conversation.py`

`domain_logic` · `turn setup and workspace file operations`

A conversation’s files live inside a sandbox, which is an isolated place where code can run and read or write `/workspace`. This file decides how to open that sandbox, how to find it later, and how to safely read, write, list, or clean files inside it. The important rule is: reads must not create anything. If a conversation has no sandbox yet, browsing or reading simply returns nothing, because creating storage during a read would be a surprising side effect.

The main class, `ConversationSandbox`, acts like a front desk for workspaces. When a turn starts, or when an attachment needs to be copied in, it checks the database row for the conversation’s stored sandbox handle. That handle is like a claim ticket saying which backend owns the workspace. If no handle exists, this file creates a sandbox and saves the handle. If two callers race to create the first sandbox, it uses a compare-and-swap database update, meaning “save my handle only if the row still says what I saw earlier.” The loser adopts the winner’s sandbox.

It also supports workspaces served by a connected user terminal. Once a conversation is bound to a terminal path, it stays bound there. For ordinary in-cluster sandboxes, it creates or checks a safe directory under the configured workspace root, with extra protection against unsafe symbolic links.

#### Function details

##### `ConversationSandbox.open`  (lines 93–137)

```
async def open(self, conversation_id: UUID, turn_id: UUID | None, run_token: str, env: Mapping[str, str]) -> SandboxSession
```

**Purpose**: Opens the sandbox for a conversation, creating it if needed, and makes sure the database records the sandbox that should be reused later. This is used when a turn needs to run tools, and also when an off-turn write needs a workspace.

**Data flow**: It receives a conversation id, an optional turn id, a run token, and environment variables. It reads the current stored sandbox handle and sandbox size from the database, asks `_opened` to create or attach to the right sandbox, then tries to save the resulting handle. If another caller saved a different handle first, it retries using that winning handle. It returns a `SandboxSession`, which is the usable connection to the sandbox.

**Call relations**: The main turn-opening path calls this through `_open_sandbox`, and `write` calls it when it needs somewhere to place bytes. Inside, it depends on `_binding` for the database state, `_opened` for the actual sandbox choice, and `_claim` to safely persist the chosen handle.

*Call graph*: calls 3 internal fn (_binding, _claim, _opened); called by 2 (_open_sandbox, write); 1 external calls (__init__).


##### `ConversationSandbox.existing`  (lines 139–182)

```
async def existing(self, conversation_id: UUID) -> SandboxSession | None
```

**Purpose**: Looks for an already-existing sandbox without creating a new one. This is the safe read-side entry point for listing, pruning, or reading files.

**Data flow**: It receives a conversation id and reads the stored sandbox handle. If there is no handle, or if the handle belongs to another backend that this process cannot serve, it returns `None`. If the handle is reachable, it attaches to the matching terminal or carrier and returns a `SandboxSession`.

**Call relations**: `entries`, `prune`, and `read` all call this because they should only operate on a workspace that already exists. It uses `_stored` to fetch the saved handle, then builds a `SandboxSpec` and attaches through either a terminal carrier or the configured sandbox carrier.

*Call graph*: calls 1 internal fn (_stored); called by 3 (entries, prune, read); 5 external calls (__init__, __init__, __init__, to_thread, sandbox_handle_id).


##### `ConversationSandbox.claim_terminal`  (lines 184–194)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Tries to bind a conversation that has no sandbox yet to a connected terminal directory. This lets a user’s live terminal become the place where that conversation’s workspace lives.

**Data flow**: It receives a conversation id and a current working directory from the terminal. It checks whether the conversation already has a stored handle. If not, it writes a `client:` handle pointing at that directory. It returns `true` only when this call made the claim.

**Call relations**: Admission code can call this while a member’s terminal connection is live. It uses `_stored` to avoid overwriting an existing binding and `_claim` to make the binding safely, so a later `open` will trust the database row rather than a temporary connection state.

*Call graph*: calls 2 internal fn (_claim, _stored).


##### `ConversationSandbox.write`  (lines 196–207)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Copies a byte string into a file inside the conversation’s workspace and returns the `/workspace/...` path that an agent would use to read it. It also protects the server from very large in-memory writes.

**Data flow**: It receives a conversation id, a relative file path, and bytes to write. It first rejects content over the configured size limit. Then it opens the sandbox off-turn, writes the file through the session, and returns the workspace-style path.

**Call relations**: This is an off-turn writer, so it calls `open` with the unsigned off-turn token to get or create the sandbox. It then relies on the returned session to do the actual file write and uses `workspace_path` to report the path in the form the sandbox sees.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.prune`  (lines 209–220)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files under a workspace subdirectory, keeping only the newest requested number. This is useful for logs or generated files that grow over time without a person watching them.

**Data flow**: It receives a conversation id, a relative directory prefix, and a number to keep. It attaches only if the sandbox already exists. Then it runs a small Python program inside the sandbox to find regular files, sort them, and delete the older ones. If that program fails, it raises an error.

**Call relations**: This function starts by calling `existing`, because pruning should not create a workspace. It passes `PRUNE_PROG` into the sandbox session so the cleanup happens from the same file-system view the agent uses.

*Call graph*: calls 1 internal fn (existing); 1 external calls (workspace_path).


##### `ConversationSandbox.entries`  (lines 222–261)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Returns the visible files in a conversation’s workspace for a file browser. It hides excluded names such as `.git` before the listing limit is applied, so metadata does not crowd out user files.

**Data flow**: It receives a conversation id and attaches only to an existing sandbox. It asks the sandbox file tool to list files under `/workspace`, checks that the result is a file list, warns if the listing was truncated, converts each absolute path into a relative workspace path, and returns sorted `WorkspaceFile` records with size and modification time.

**Call relations**: File browsing calls this read-side path, so it first goes through `existing`. For every returned path it calls `_workspace_rel` to remove the correct workspace root, and it creates `WorkspaceFile` objects for the browser-facing result.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 3 external calls (__init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 263–267)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns an absolute path reported by a workspace scan into a path relative to the workspace root. This keeps file browser output from exposing container or host directory details.

**Data flow**: It receives a sandbox handle and a path string. It checks whether the path starts with either `/workspace/` or the handle’s host workspace path. If so, it strips that prefix and returns the relative path. If the path is outside both roots, it raises an error.

**Call relations**: `entries` calls this while converting raw file-listing results into member-visible `WorkspaceFile` records. It acts as a safety check between the sandbox file walker and the public listing response.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 269–277)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Returns a stream of bytes for one file in an existing workspace, or `None` if the workspace or file is missing. It deliberately avoids creating a sandbox just because someone asked to read.

**Data flow**: It receives a conversation id and a relative path. It attaches through `existing`; if no sandbox is reachable, it returns `None`. It then checks whether the file exists. If it does, it returns an asynchronous byte stream from the session.

**Call relations**: This is the single-file read path and is built on `existing` for the no-side-effects rule. Once a session is available, it asks the session whether the file exists and then hands back the session’s reader.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox._opened`  (lines 279–335)

```
async def _opened(self, conversation_id: UUID, turn_id: UUID | None, stored: str | None, run_token: str, env: Mapping[str, str], size: str) -> tuple[str, Carrier, SandboxHandle]
```

**Purpose**: Chooses where a conversation’s sandbox should run and opens it. This is the central decision point for terminal-backed workspaces versus the deployment’s normal sandbox backend.

**Data flow**: It receives the conversation id, turn id, stored handle, run token, environment, and requested sandbox size. It first checks whether the stored handle or a live terminal binding points to a terminal directory. If so, it creates a terminal-backed sandbox. Otherwise it prepares a host workspace path, possibly changes ownership for the sandbox user, and creates a sandbox through the configured carrier. It returns the backend name, carrier, and sandbox handle.

**Call relations**: `open` calls this after reading the conversation’s stored binding. `_opened` builds `SandboxSpec` objects for either `TerminalCarrier` or the normal carrier, and uses `sandbox_handle_id` to understand whether a stored handle belongs to the client terminal backend or this deployment’s backend.

*Call graph*: called by 1 (open); 5 external calls (__init__, __init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._provisioned_dir`  (lines 337–352)

```
def _provisioned_dir(self, conversation_id: UUID) -> Path
```

**Purpose**: Creates and verifies the host directory used as a conversation’s workspace for in-cluster sandboxes. Its main job is to make directory creation safe, especially around symbolic links.

**Data flow**: It receives a conversation id. It makes sure the workspace root exists, resolves the configured root, then creates the conversation’s directory under that root using containment checks. It returns the safe directory path.

**Call relations**: The sandbox-opening flow uses this when a normal in-cluster carrier needs a host directory for a conversation. It relies on `configured_root` and `contained_dir` so the carrier does not mount a path that escapes the configured workspace area.

*Call graph*: 3 external calls (suppress, configured_root, contained_dir).


##### `ConversationSandbox._existing_dir`  (lines 354–365)

```
def _existing_dir(self, conversation_id: UUID) -> Path | None
```

**Purpose**: Finds the already-created host directory for a conversation without making a new one. This supports read paths where asking a question must not create workspace storage.

**Data flow**: It receives a conversation id. It resolves the configured workspace root and checks whether the conversation directory exists safely inside it. If the directory is absent, it returns `None`; if the path is unsafe, the containment code raises an error.

**Call relations**: `existing` uses this when attaching to an already-stored normal sandbox handle. It is the read-side counterpart to `_provisioned_dir`: one creates safely, the other only verifies and returns what is already there.

*Call graph*: 2 external calls (configured_root, contained_dir).


##### `ConversationSandbox._stored`  (lines 367–369)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Fetches only the stored sandbox handle for a conversation. It is a small helper for callers that do not need the sandbox size.

**Data flow**: It receives a conversation id, calls `_binding`, ignores the size part of the result, and returns the handle string or `None`.

**Call relations**: `existing`, `claim_terminal`, and `_claim` use this when they need the current database handle. It keeps those callers from duplicating the fuller row-reading logic in `_binding`.

*Call graph*: calls 1 internal fn (_binding); called by 3 (_claim, claim_terminal, existing).


##### `ConversationSandbox._binding`  (lines 371–392)

```
async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]
```

**Purpose**: Reads the conversation’s current sandbox handle and the owning agent’s requested sandbox size from the database. This gives sandbox creation the information it needs before it opens anything.

**Data flow**: It receives a conversation id and opens a workspace-scoped database transaction. It selects the conversation’s sandbox handle and the related agent’s sandbox size, limited to the current workspace. If no matching conversation exists, it raises an error. Otherwise it returns the handle and size.

**Call relations**: `open` calls this before trying to create or resume a sandbox, and `_stored` calls it when only the handle is needed. It uses the current workspace context so one workspace cannot read or change another workspace’s conversation.

*Call graph*: called by 2 (_stored, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 394–415)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Safely writes a sandbox handle to the conversation row only if the row still contains the value the caller previously saw. This is how the code avoids two racing creators both believing they own the conversation’s workspace.

**Data flow**: It receives a conversation id, the previously observed stored handle, and the new handle to save. It performs a conditional database update. If the update changes a row, it returns the new handle. If another caller won first, it reads the current stored handle and returns that instead. If the handle somehow disappeared, it raises an error.

**Call relations**: `open` uses this after creating or attaching to a sandbox, and `claim_terminal` uses it to bind a new conversation to a terminal path. When `_claim` loses a race, it calls `_stored` so the caller can adopt the winner rather than keep using an unreferenced sandbox.

*Call graph*: calls 1 internal fn (_stored); called by 2 (claim_terminal, open); 3 external calls (update, workspace_tx, ws_current).


### `core/src/ufo/sandbox/ingress_host.py`

`domain_logic` · `request routing and sandbox URL creation`

A browser treats different hostnames as different places. This file uses that rule to keep sandboxed sites apart: each conversation and port gets its own hostname label, so cookies, storage, redirects, and root-based assets for one site cannot accidentally mix with another. Think of the label like a room number printed on a sealed envelope: it tells the system where to route the request, and the seal proves the room number was made by this deployment.

The label contains three pieces packed into a compact base32 string: the conversation ID, the port number, and a short HMAC signature. An HMAC is a tamper-checking code made with a secret key. Here it is not the main permission check; it simply stops random guessed hostnames from even reaching conversation data. Real access is still checked later by tokens or cookies.

The file also protects against a subtle browser problem. Base32 encoding can allow several spellings to decode to the same bytes. Browsers would treat those spellings as different origins, meaning separate cookie jars and storage. So when reading a label, the code decodes it, re-encodes it in the one accepted lowercase spelling, and rejects anything else.

#### Function details

##### `site_label`  (lines 52–57)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: Builds the DNS label for a specific conversation and sandbox port. Callers use it when they need the stable hostname piece where that sandbox site should be served.

**Data flow**: It receives a conversation UUID and a port number. It first checks that the port is in the normal addressable TCP port range, then joins the UUID bytes and the two-byte port into one address. It signs that address, appends the signature, encodes the result as a short lowercase base32 string, and returns that string.

**Call relations**: This is the label-making side of the flow. It relies on _signature to add the deployment-specific tamper check, then passes the signed bytes to _encode so the result is safe to use as a DNS label.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 60–72)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: Reads a DNS label back into the conversation ID and port it names, but only if the label is well formed, signed by this deployment, and written in the one accepted spelling. It is used to stop malformed or guessed hostnames before the server treats them as real sandbox addresses.

**Data flow**: It receives a label string from a hostname. It tries to base32-decode it, accepting uppercase or lowercase because DNS is case-insensitive. It then re-encodes the bytes and compares that with the lowercase input to reject alternate spellings. Next it splits the decoded bytes into the address and signature, recomputes the expected signature, and compares them safely. If anything fails, it raises SiteLabelError. If everything matches, it returns the UUID and port.

**Call relations**: This is the label-checking side of the flow. It uses _encode to enforce the single canonical hostname spelling and _signature to verify that the address was minted with the same deploy secret. When the signature matches, it hands back the conversation and port so later request logic can continue with stronger token or cookie authorization.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 75–76)

```
def _encode(raw: bytes) -> str
```

**Purpose**: Turns raw bytes into the exact DNS-label spelling this system accepts. It keeps labels lowercase and removes base32 padding characters so the hostname part stays compact.

**Data flow**: It receives bytes, base32-encodes them, converts the result to text, strips trailing equals-sign padding, lowercases the text, and returns it. It does not change any outside state.

**Call relations**: site_label uses this when creating a hostname label. parse_site_label uses it again after decoding to make sure the incoming label is written in the one canonical form, not one of the alternate spellings that could otherwise create separate browser origins.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 79–81)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: Creates the short tamper-checking signature attached to a sandbox site address. This proves the conversation-and-port bytes were produced with this deployment's ingress secret.

**Data flow**: It receives the raw address bytes made from a conversation ID and port. It reads the current ingress secret, combines that secret with a fixed label identifying this kind of signature, runs an HMAC using SHA-256, keeps the first four bytes, and returns those bytes.

**Call relations**: site_label calls this to stamp newly created labels. parse_site_label calls it to recompute what the stamp should be for an incoming label, then compares that expected value with the label's included signature.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).


### `core/src/ufo/sandbox/session.py`

`domain_logic` · `per-turn sandbox use and file/tool operations`

A sandbox is like a locked workshop for one conversation: tools can work on files inside it, but they must not wander into the system’s private records or credentials. This file sets the rules for that workshop. It defines the shared interface, called a carrier, that different sandbox backends must follow. A carrier might be Docker, a remote sandbox provider, or something local, but the rest of the system can use it the same way.

The file also defines small value objects that describe sandbox requests, handles, command results, proxy targets, and signed tokens. The signed tokens matter because sandbox network traffic goes through an egress proxy, and the proxy needs to know which workspace, turn, or probe is allowed to make the request.

The `SandboxSession` class is the tool-facing wrapper. It runs shell or Python commands, writes and reads workspace files, checks paths before use, and asks the carrier how to dial a port exposed from inside the sandbox. A key safety theme is path containment: tool-supplied paths are normalized so `..` tricks cannot escape `/workspace`. Another important detail is that file operations go through guarded code rather than unsafe shell redirects, so a malicious symlink planted in the workspace cannot redirect writes somewhere unexpected.

#### Function details

##### `_basic_username`  (lines 74–80)

```
def _basic_username(header: str) -> str
```

**Purpose**: Extracts the username part from a `Proxy-Authorization: Basic ...` header. In this system, that username is where signed sandbox access tokens are carried.

**Data flow**: It receives an authorization header string. It checks that the header uses Basic authentication, decodes the base64 text, and takes everything before the first colon as the username. It returns that username, or raises an error if the header is not valid Basic auth.

**Call relations**: The token decoders for run tokens and probe tokens call this first. They use the extracted username as the signed token that will then be checked cryptographically.

*Call graph*: called by 2 (from_proxy_auth, from_proxy_auth); 1 external calls (b64decode).


##### `RunTokenCodec.from_env`  (lines 99–103)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Builds a run-token signer and verifier from the deployment secret stored in the environment. This is how the service gets the private key material needed to mint sandbox proxy identities.

**Data flow**: It reads the environment variable that should contain the token secret. If the value is missing, it stops with a clear runtime error. If present, it converts the value to bytes and returns a `RunTokenCodec` using it.

**Call relations**: Startup paths such as the main server run and proxy server call this when they need the shared signing secret. The resulting codec is later used to issue or verify per-turn sandbox proxy tokens.

*Call graph*: called by 2 (serve, run).


##### `RunTokenCodec.encode`  (lines 105–108)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a run identity into a signed token for proxy use. The token says which workspace and turn a sandbox process belongs to, and optionally which member it is acting for.

**Data flow**: It receives a `RunToken` containing workspace, turn, and optional member IDs. It formats those fields into a small text payload and signs that payload with the codec secret. It returns the signed token string.

**Call relations**: The sandbox-opening flow calls this when preparing a sandbox for a turn. The token is later embedded in proxy environment variables so network requests from the sandbox can be attributed and authorized.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 110–122)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Verifies a proxy authorization header and reconstructs the run identity it represents. It rejects forged, damaged, or wrong-kind tokens.

**Data flow**: It receives a proxy authorization header. It extracts the Basic-auth username, verifies the signature with the deployment secret, splits the verified payload into fields, converts ID strings into UUIDs, and returns a `RunToken`. If any part fails, it raises a simple invalid-token error.

**Call relations**: This is the receiving side of `RunTokenCodec.encode`. The proxy uses this style of decoding when sandbox traffic arrives and it must decide which turn and member the request belongs to.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `ProbeTokenCodec.encode`  (lines 157–163)

```
def encode(self, probe: ProbeToken) -> str
```

**Purpose**: Creates a signed token for an off-turn probe command. A probe is sandbox work that runs outside a normal assistant turn, so its token includes its own expiry time.

**Data flow**: It receives a `ProbeToken` containing workspace, conversation, probe, optional member, and expiry information. It formats those fields with a probe-specific kind marker, signs the payload with the codec secret, and returns the signed token string.

**Call relations**: This mirrors run-token encoding but for probes. The separate token kind prevents a normal run token from being accepted as a probe token, or the other way around.

*Call graph*: 1 external calls (sign_token).


##### `ProbeTokenCodec.from_proxy_auth`  (lines 165–181)

```
def from_proxy_auth(self, header: str) -> ProbeToken
```

**Purpose**: Verifies a probe proxy authorization header and rebuilds the probe identity. It is used to decide whether an off-turn sandbox request is still allowed.

**Data flow**: It receives a Basic proxy authorization header. It extracts the username, verifies the signed payload, checks that the payload is for a probe, converts IDs and the expiry timestamp, and returns a `ProbeToken`. Invalid signatures, malformed text, wrong token kinds, or bad IDs all become an invalid-token error.

**Call relations**: This is the verifier paired with `ProbeTokenCodec.encode`. The proxy can call it when a probe tries to make an outbound connection, then compare the token’s expiry and authority to current rules.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `sandbox_handle_id`  (lines 267–272)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Pulls the backend-specific sandbox ID out of a stored handle only if that handle belongs to the current backend. This prevents one sandbox provider from trying to resume another provider’s container ID.

**Data flow**: It receives a backend name and a stored handle string. If the string starts with the expected `backend:` prefix, it returns the part after the prefix. Otherwise it returns `None`.

**Call relations**: Carrier implementations can use this when deciding whether a saved sandbox reference is theirs to resume. It protects deployments that switch sandbox backends from misusing stale handles.


##### `Carrier.create`  (lines 310–310)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the contract for creating or attaching to a sandbox for a conversation. Concrete sandbox backends implement this method.

**Data flow**: It receives a `SandboxSpec`, which describes the desired sandbox, workspace, image, proxy, token, and optional size or resume details. An implementation uses that information to provision or attach to a container-like environment and returns a `SandboxHandle`.

**Call relations**: This is part of the carrier interface. Higher-level sandbox-opening code depends on this promise instead of depending directly on Docker, E2B, local execution, or any other backend.


##### `Carrier.attach`  (lines 312–318)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines how to reconnect to an existing sandbox without creating a new one. It is used for read-only or resume-style situations where silently provisioning a fresh sandbox would be wrong.

**Data flow**: It receives a `SandboxSpec`, especially a resume identity. An implementation checks whether that sandbox is reachable. It returns a `SandboxHandle` if it can attach, or `None` if the sandbox is gone or unavailable.

**Call relations**: This method belongs to the carrier interface. It lets callers ask, “is the old workshop still there?” without accidentally creating a new empty workshop.


##### `Carrier.exec`  (lines 320–322)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines how to run a command inside the sandbox and collect its result. Every backend must provide its own way to start the process and capture output.

**Data flow**: It receives a sandbox handle, an argument list for the command, and a timeout. The backend runs that command inside the sandbox and returns stdout, stderr, exit code, and timeout information in an `ExecResult`.

**Call relations**: `sbxfs_file_op` calls this to run the sandbox file-operation command. `SandboxSession` methods also rely on this carrier capability through the shared interface.

*Call graph*: called by 1 (sbxfs_file_op).


##### `Carrier.write`  (lines 324–336)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how to copy bytes into a file under the sandbox workspace. Implementations must do this safely, without relying on unsafe shell redirects.

**Data flow**: It receives a sandbox handle, an absolute workspace path, and raw bytes. The backend writes those bytes into the sandbox, creating parent directories when needed, and reports success by returning nothing. Refused or failed writes surface as errors.

**Call relations**: `SandboxSession.write_file` prepares a safe workspace path and then hands the write to this carrier method. Each backend supplies the transport that fits its environment.


##### `Carrier.read`  (lines 338–349)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how to stream a file out of the sandbox workspace. It is designed for large files, so the host process does not need to hold the whole file in memory.

**Data flow**: It receives a sandbox handle and an absolute workspace path. The backend opens the file inside the sandbox and yields chunks of bytes over time. Missing or invalid files raise appropriate errors instead of returning fake content.

**Call relations**: `SandboxSession.read_file` checks and normalizes the path, then returns this stream to its caller. Different backends implement the actual copy-out path differently.


##### `Carrier.dial`  (lines 351–360)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Defines how outside code can reach a service listening on a port inside the sandbox. This is needed for things like browser debugging ports or preview servers started by a command.

**Data flow**: It receives a sandbox handle and an internal port number. The backend maps that to an externally reachable host, TLS choice, and any required headers, returning a `DialTarget`. If the sandbox cannot be reached, it raises `SandboxUnreachable`.

**Call relations**: `SandboxSession.dial` passes requests through to this carrier method. Browser-related extensions call the session method when they need to connect to services inside the sandbox.


##### `Carrier.file_op`  (lines 362–372)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Defines a structured way to run file operations inside the sandbox, such as reading windows of text, editing, globbing, or grepping. The goal is to keep heavy file work inside the sandbox and return only bounded JSON results.

**Data flow**: It receives a sandbox handle, an operation name, and a dictionary of parameters. The backend runs the operation inside the workspace and returns a parsed JSON-like dictionary. Recoverable tool-facing failures are reported as `ValueError`; unexpected failures become runtime errors.

**Call relations**: `SandboxSession.run_sbxfs` prepares safe parameters and delegates to this method. Backends that include the `sbxfs` command can share the helper `sbxfs_file_op` as their implementation.


##### `CommandStopping.stop_commands`  (lines 392–392)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines an optional capability for backends whose commands can keep running after the original call is cancelled. It stops commands associated with one turn, not the whole shared sandbox.

**Data flow**: It receives a sandbox handle, including the turn identity. An implementation finds commands launched for that turn and stops them. It returns nothing once the stop request has been sent or completed.

**Call relations**: `SandboxSession.stop_commands` checks whether the carrier supports this protocol before calling it. This keeps simple backends from needing a stop feature they cannot use.


##### `sbxfs_file_op`  (lines 395–418)

```
async def sbxfs_file_op(carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Implements `Carrier.file_op` for backends that have the `sbxfs` command installed in the sandbox. It runs one file operation and turns the command’s JSON output into a Python dictionary.

**Data flow**: It receives a carrier, sandbox handle, operation name, and parameters. It serializes the parameters to compact JSON, runs `sbxfs` through `Carrier.exec`, trims stdout, parses it as JSON, checks that the result is an object, and raises clear errors for missing output, bad JSON, or reported file-operation failures. On success, it returns the parsed dictionary.

**Call relations**: Carrier implementations can delegate their file-operation method to this helper instead of duplicating the command call and parsing rules. It relies directly on `Carrier.exec` to do the actual in-sandbox work.

*Call graph*: calls 1 internal fn (exec); 2 external calls (dumps, loads).


##### `workspace_path`  (lines 421–429)

```
def workspace_path(path: str) -> str
```

**Purpose**: Turns a caller-provided path into a safe absolute path under `/workspace`. It blocks attempts to escape the workspace using absolute paths or `..` segments.

**Data flow**: It receives a path string from a tool or caller. If the path is relative, it treats it as relative to `/workspace`; then it normalizes `.` and `..` pieces and checks that the final path is still inside `/workspace`. It returns the safe path string or raises an error if the path escapes.

**Call relations**: File-related session methods call this before touching the carrier. It relies on `_resolve_parts` for the path cleanup and gives all reads, writes, existence checks, and structured file operations the same safety rule.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 4 (file_exists, read_file, run_sbxfs, write_file); 1 external calls (PurePosixPath).


##### `_resolve_parts`  (lines 432–441)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Simplifies the pieces of a path while detecting attempts to climb above the workspace root. It is the small path-cleaning engine behind `workspace_path`.

**Data flow**: It receives a tuple of path parts. It builds a stack, ignoring empty and `.` parts, popping one level for `..`, and refusing to pop past the protected root. It returns the cleaned list of path parts.

**Call relations**: `workspace_path` calls this after building a POSIX-style path. Keeping this logic separate makes the escape check explicit and reusable inside that path-normalization step.

*Call graph*: called by 1 (workspace_path).


##### `SandboxSession.authorize`  (lines 453–479)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new session view with a different run token and adjusted environment variables. This lets the same sandbox be reused while giving each execution the right network authority.

**Data flow**: It receives a new run token, a set of environment variable names to remove, and extra environment variables to add. It checks that the current handle has a token and that proxy environment variables contain it, replaces the old token with the new one in proxy settings, removes cleared variables, overlays the new environment, and returns a new `SandboxSession` with an updated handle.

**Call relations**: This method sits between sandbox reuse and per-turn authorization. Rather than mutating the existing session, it builds a fresh session object whose carrier is the same but whose handle carries the new proxy identity.

*Call graph*: 2 external calls (__init__, __init__).


##### `SandboxSession.bash`  (lines 481–486)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a shell command inside the sandbox using Bash. It is the convenient path for tool code that wants normal shell behavior.

**Data flow**: It receives a command string and an optional timeout. It sends `bash -lc <command>` to the carrier with either the given timeout or the default timeout. It returns the carrier’s `ExecResult` with output, error text, exit code, and timeout information.

**Call relations**: Browser sandbox extension code calls this when bringing up or diagnosing Chrome-related services. Internally it is a thin wrapper over the carrier’s command execution method.

*Call graph*: called by 2 (lease, _bring_up_failure).


##### `SandboxSession.sh`  (lines 488–496)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a POSIX shell script inside the sandbox while passing arguments safely as separate command arguments. This avoids quoting surprises where an argument becomes part of the script text.

**Data flow**: It receives a script string, zero or more argument strings, and an optional timeout. It calls the carrier with `sh -c`, the script, and each argument as its own argument. It returns the resulting `ExecResult`.

**Call relations**: This is a safer general shell helper for session users. Like `bash`, it delegates actual execution to the carrier but shapes the argument list to keep data separate from code.


##### `SandboxSession.python`  (lines 498–512)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Python program inside the sandbox with the containment guard importable and with Python isolated from workspace-planted modules. This is used when host-provided Python code must safely inspect or write workspace paths.

**Data flow**: It receives Python source text, optional arguments, and an optional timeout. It prepends bootstrap code that locates the sandbox guard beside `sbxfs`, runs `python3` in isolated mode, passes the program and arguments as command arguments, and returns the carrier’s `ExecResult`.

**Call relations**: Session users call this when they need a small Python helper inside the sandbox. It still uses the same carrier execution path, but hardens Python startup so imports cannot be hijacked by files written in the workspace.


##### `SandboxSession.stop_commands`  (lines 514–519)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands left running by this session’s turn, but only when the carrier supports that feature. It is used after a deliberate user or member cancellation.

**Data flow**: It reads the session’s carrier and handle. If the carrier implements the optional command-stopping protocol, it asks the carrier to stop commands for this handle’s turn. If not, it does nothing.

**Call relations**: This bridges the general session API and the optional `CommandStopping` interface. It avoids forcing every carrier to implement stopping while still giving long-running backends a cleanup hook.


##### `SandboxSession.write_file`  (lines 521–522)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a file in the sandbox workspace after first making the path safe. This is the standard session-level way to place content into `/workspace`.

**Data flow**: It receives a caller path and byte content. It converts the path to a checked `/workspace` path with `workspace_path`, then asks the carrier to write the bytes there. It returns nothing when the write succeeds.

**Call relations**: The skill runtime calls this when mounting skill files into the sandbox. It delegates safety checking to `workspace_path` and the actual byte transfer to `Carrier.write`.

*Call graph*: calls 1 internal fn (workspace_path); called by 1 (mount_skill).


##### `SandboxSession.ensure_tool_output_dir`  (lines 524–546)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine’s private `.tool-output` directory exists inside the workspace. If a file or broken link is squatting on that name, it removes it so later offloads do not fail forever.

**Data flow**: It runs a small shell script in the sandbox against the fixed tool-output path. The script exits if the directory already exists, removes a non-directory occupant if needed, creates the directory, and prints a marker if it reclaimed the name. The method raises an `OSError` if the command fails, otherwise returns `true` when something was reclaimed and `false` when it was already fine.

**Call relations**: This method uses the carrier’s execution path directly. Because the target path is fixed by the engine, its destructive cleanup cannot be pointed at arbitrary user files.


##### `SandboxSession.file_exists`  (lines 548–553)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the sandbox workspace. It is a small safe wrapper around the shell `test -f` check.

**Data flow**: It receives a caller path, converts it to a safe workspace path, then runs `test -f` inside the sandbox. It returns `true` if the command exits successfully and `false` otherwise.

**Call relations**: This method shares the same path guard as reads and writes by calling `workspace_path`. It uses the carrier execution interface for the actual filesystem check.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.run_sbxfs`  (lines 555–566)

```
async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured sandbox file operation with safe workspace parameters. This is the session-level entry to operations such as reads, edits, searches, and listings.

**Data flow**: It receives an operation name and argument dictionary. It copies the arguments, rewrites a string `path` argument through `workspace_path` if present, adds the fixed workspace root, and asks the carrier to run the file operation. It returns the parsed dictionary result from the carrier.

**Call relations**: This method prepares safe, consistent parameters before handing off to `Carrier.file_op`. Backends may fulfill that request through `sbxfs_file_op` or another carrier-specific mechanism.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.read_file`  (lines 568–571)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox workspace after checking that the requested path stays under `/workspace`. It is meant for files that may be too large to load all at once.

**Data flow**: It receives a caller path. It converts that path to a safe workspace path and returns the carrier’s byte stream for that file. The caller then consumes chunks from the async iterator.

**Call relations**: This is the read-side partner to `write_file`. It uses `workspace_path` for containment and delegates the actual streaming to `Carrier.read`.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.dial`  (lines 573–576)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Gets the outside address needed to reach a service running on a port inside the sandbox. Callers use it instead of guessing how a given backend exposes ports.

**Data flow**: It receives an internal port number. It asks the carrier to translate that port for this sandbox handle into a `DialTarget` containing host, TLS choice, and any required headers. It returns that target or lets the carrier raise an unreachable error.

**Call relations**: Sandbox Chrome integration calls this when it needs to connect to a browser service inside the sandbox. The method is a simple session-level pass-through to the carrier’s backend-specific dialing logic.

*Call graph*: called by 1 (lease).


### Sandbox carriers
These files provide the concrete local, terminal-stream, Docker, and E2B backends that create or resume workspaces and execute sandbox operations.

### `core/src/ufo/sandbox/local.py`

`io_transport` · `request handling`

This file is the simplest way the project can run sandboxed work: it creates or reuses a host directory as the conversation workspace, then runs commands inside that directory. It is meant for development and low-friction use, not strong security isolation. Think of it like giving the agent a clearly marked desk in your house: the tools are told to stay on that desk, but there is no locked room around it.

The main class, LocalCarrier, builds a safe-enough working environment for each command. It creates a scratch area for helper programs, a fake HOME directory, and a PATH that includes the sandbox tools sbx and sbxfs. It also avoids inheriting the server process’s private environment, because that may contain deployment secrets.

When a sandbox is created, the file prepares the workspace directory, writes the proxy certificate, and returns a SandboxHandle describing how commands should run. Network requests from those commands are pointed at the local proxy, with sentinel API keys so model calls can be swapped and measured consistently.

File reads and writes go through containment checks, which means a requested /workspace path is carefully mapped into the real host workspace and checked so symbolic links or .. paths cannot escape. Command execution rewrites /workspace paths to the real host path, starts a subprocess, and kills the whole process group on timeout or cancellation so stray child processes do not keep running forever.

#### Function details

##### `_provision_scratch`  (lines 60–78)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates a temporary support area used by the local sandbox for its whole lifetime. This area holds a fake home directory and copies of helper command-line tools that sandbox commands need.

**Data flow**: It starts with no input, asks the operating system for a new temporary directory, creates home and bin folders inside it, copies the sbx and sbxfs helper scripts plus their needed module into bin, marks the scripts executable, and returns the path to this scratch directory.

**Call relations**: This is used as the default setup for LocalCarrier’s _scratch field when a LocalCarrier is constructed. Later, create, attach, and _base_env rely on that scratch directory to build the command environment.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `LocalCarrier.create`  (lines 85–116)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Starts a local sandbox workspace for a conversation. It makes sure the workspace folder exists and prepares the environment that future commands will inherit.

**Data flow**: It receives a SandboxSpec containing the conversation ID, workspace path, proxy information, run token, and extra environment values. It creates the host workspace directory, writes the proxy certificate into the scratch area, builds proxy-related environment variables, combines them with the base command environment and the spec’s own environment, and returns a SandboxHandle describing the ready sandbox.

**Call relations**: This is called when the system needs a new local sandbox. It calls LocalCarrier._base_env to get a deliberately limited environment, then packages everything into a SandboxHandle that later operations such as exec, read, write, and file_op use.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier._base_env`  (lines 118–140)

```
def _base_env(self) -> dict[str, str]
```

**Purpose**: Builds the basic environment variables every local sandbox command should receive. It prevents commands from seeing the server’s secret environment while still giving them essentials like PATH, HOME, temporary-directory settings, and locale settings.

**Data flow**: It reads only a small allow-list from the host environment, such as TMPDIR and locale variables. It adds a scratch HOME, a PATH containing the sandbox helper tools and Python’s directory, and Git settings that disable host credential helpers and interactive prompts. It returns this as a dictionary of environment variables.

**Call relations**: LocalCarrier.create uses this when making a full command environment with proxy access. LocalCarrier.attach uses it when reconnecting to an existing workspace for local file operations without creating a new workspace.

*Call graph*: called by 2 (attach, create); 1 external calls (Path).


##### `LocalCarrier.attach`  (lines 142–155)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an existing local workspace without creating it. This is useful for browsing or reading a conversation’s workspace only if that workspace already exists.

**Data flow**: It receives a SandboxSpec, checks whether the specified host workspace path is already a directory, and returns None if it is not. If the directory exists, it builds a SandboxHandle using the base environment and returns it.

**Call relations**: This is the read-only counterpart to LocalCarrier.create. It calls LocalCarrier._base_env so attached operations still have access to the local helper tools, but it does not create the workspace folder or set up the full proxy environment.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 157–199)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command in the local workspace as a host subprocess. It makes /workspace-style paths work locally and enforces a timeout.

**Data flow**: It receives a SandboxHandle, the command arguments, and a timeout. It finds the real host workspace path, rewrites any /workspace text in the arguments to that real path, starts the command with the workspace as its current folder and the handle’s environment, waits for it to finish, and returns an ExecResult containing stdout, stderr, exit code, and timeout information if relevant. If the command times out or the task is cancelled, it kills the command’s whole process group.

**Call relations**: This is the main command runner for the local carrier. It calls _root to locate the workspace and _kill_process_group when a command must be forcibly stopped, then hands the result back to whichever higher-level sandbox tool requested execution.

*Call graph*: calls 2 internal fn (_kill_process_group, _root); 3 external calls (__init__, create_subprocess_exec, wait_for).


##### `LocalCarrier.write`  (lines 201–224)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into a file inside the local workspace. It keeps the event loop responsive by doing the actual disk work in a background thread.

**Data flow**: It receives a sandbox handle, a logical workspace path, and the bytes to write. It sends the blocking filesystem work to LocalCarrier._write_contained in another thread, then returns when the write has completed.

**Call relations**: Higher-level upload or tool code calls this when a file must appear inside the sandbox workspace. This function is the async wrapper; LocalCarrier._write_contained performs the guarded write.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._write_contained`  (lines 226–228)

```
def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Performs the actual safe file write inside the workspace. It uses the containment guard so a path cannot escape the workspace through tricks like symbolic links.

**Data flow**: It receives the sandbox handle, the logical file path, and the bytes. It turns the logical /workspace path into a relative workspace name, finds the real workspace root, opens a guarded target location with parent creation allowed, and replaces the target file’s contents with the supplied bytes using the intended write mode.

**Call relations**: LocalCarrier.write calls this in a background thread. It depends on _workspace_name to translate the logical path and _root to find the host directory, then delegates the safety-sensitive path opening to contained_file.

*Call graph*: calls 2 internal fn (_root, _workspace_name); 1 external calls (contained_file).


##### `LocalCarrier.read`  (lines 230–241)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the local workspace in chunks. It avoids blocking the async event loop while reading from the host filesystem.

**Data flow**: It receives a sandbox handle and a logical path. It opens a safe, contained file source in a background thread, repeatedly reads up to one megabyte at a time, yields each chunk to the caller, and finally closes the file even if the caller stops early.

**Call relations**: Higher-level download or file-viewing code uses this to read workspace files. It calls LocalCarrier._contained_source to do the guarded open, then handles the async chunk-by-chunk streaming.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._contained_source`  (lines 243–252)

```
def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader
```

**Purpose**: Safely opens a workspace file for reading. It makes sure the requested path exists and is inside the workspace before returning a file object.

**Data flow**: It receives the sandbox handle and logical path, converts the path into a workspace-relative name, finds the real workspace root, and asks the containment guard to open the target. If the path is missing or cannot be safely reached, it raises FileNotFoundError. If it is valid, it returns a binary reader for the file.

**Call relations**: LocalCarrier.read calls this before streaming file bytes. It uses _workspace_name and _root for path translation, and contained_file for the actual safety check against filesystem escape.

*Call graph*: calls 2 internal fn (_root, _workspace_name); 1 external calls (contained_file).


##### `LocalCarrier.file_op`  (lines 254–259)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured file operation through the sbxfs helper tool. This lets local mode use the same file-tool behavior as other sandbox carriers.

**Data flow**: It receives a sandbox handle, an operation name, and operation parameters. It passes those to sbxfs_file_op, which runs the helper command against the workspace, and returns the resulting dictionary.

**Call relations**: Higher-level file tools call this when they need operations beyond simple read and write. This function is a thin bridge from the LocalCarrier interface to the shared sbxfs_file_op helper.

*Call graph*: 1 external calls (sbxfs_file_op).


##### `LocalCarrier.dial`  (lines 261–269)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Rejects attempts to connect to a service port inside the local sandbox. Local mode does not provide a separate network-addressable sandbox host.

**Data flow**: It receives a sandbox handle and a port number, but does not use them to create a connection target. Instead, it raises SandboxUnreachable with a message explaining that a remote carrier is needed for this kind of access.

**Call relations**: Code that wants to reach an in-sandbox web server or browser endpoint may call this through the carrier interface. In local mode, the story ends here with a clear error instead of pretending such routing exists.

*Call graph*: 1 external calls (__init__).


##### `_kill_process_group`  (lines 272–277)

```
async def _kill_process_group(process: asyncio.subprocess.Process) -> None
```

**Purpose**: Forcefully stops a command and any child processes it started. This prevents timed-out or cancelled commands from leaving runaway processes behind.

**Data flow**: It receives an asyncio subprocess object, sends SIGKILL to the process group whose ID matches the child process ID, ignores the case where the process is already gone, and then waits for the process to finish being cleaned up.

**Call relations**: LocalCarrier.exec calls this when a command times out or when execution is interrupted. It is deliberately aimed at the whole process group, not just the direct child, because shell commands often start their own child processes.

*Call graph*: called by 1 (exec); 2 external calls (wait, killpg).


##### `_root`  (lines 280–283)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Returns the real host directory that backs /workspace for a local sandbox. It also catches the configuration error where no host workspace path was provided.

**Data flow**: It receives a SandboxHandle, checks that workspace_host_path is present, converts that path string into a Path object, and returns it. If the handle has no workspace path, it raises a RuntimeError explaining the problem.

**Call relations**: LocalCarrier.exec, LocalCarrier._write_contained, and LocalCarrier._contained_source call this whenever they need to turn sandbox work into host filesystem work.

*Call graph*: called by 3 (_contained_source, _write_contained, exec); 1 external calls (Path).


##### `_workspace_name`  (lines 286–290)

```
def _workspace_name(path: str) -> PurePosixPath
```

**Purpose**: Converts a logical /workspace path into the relative name used under the host workspace directory. It only performs the simple mapping; real safety checks happen later in the containment guard.

**Data flow**: It receives a path string such as /workspace/example.txt, treats it as a POSIX-style path, removes the /workspace prefix, and returns the remaining relative path. If the path is not under /workspace, the path conversion fails.

**Call relations**: LocalCarrier._write_contained and LocalCarrier._contained_source call this before asking contained_file to open a path. It is the translation step between tool-facing sandbox paths and host-facing workspace paths.

*Call graph*: called by 2 (_contained_source, _write_contained); 1 external calls (PurePosixPath).


### `core/src/ufo/sandbox/terminal.py`

`io_transport` · `request handling`

Most sandboxes can be reached by dialing a server or container. A user’s own terminal is different: the server cannot directly open a connection into someone’s laptop. So this file builds a rendezvous point, like a front desk where one side drops off a request and the other side later brings back the answer.

The `Terminals` class keeps the live state for each conversation: whether a terminal is connected, what directory it is in, which operation is currently waiting, and who is waiting for the result. This matters because the client connection is expected to come and go. A request may be made while the terminal is briefly reconnecting, so the code waits a short grace period instead of failing immediately.

Only one terminal operation runs at a time for a conversation. Extra requests wait in line. This prevents two commands or file copies from racing through the same client stream.

`TerminalCarrier` is the sandbox-facing wrapper. It makes terminal operations look like ordinary sandbox operations: create a handle, run a command, read a file, write a file, or perform a file-system operation. It also rewrites `/workspace/...` paths into the real directory where the user launched the client. The terminal client receives names and data, not arbitrary server-side code to run.

#### Function details

##### `TerminalTransport.connect`  (lines 218–218)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Defines the contract for announcing that a terminal connection is now available for a conversation. Implementations use it when the user's client stream arrives.

**Data flow**: It receives a conversation id, the terminal's current working directory, and the member identity. The implementation records that binding so later sandbox work can find the terminal. It returns nothing, but changes the transport's shared state.

**Call relations**: This is part of the `TerminalTransport` protocol, so callers can use either the in-process `Terminals` implementation or another backend without changing the rest of the code.


##### `TerminalTransport.disconnect`  (lines 220–220)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Defines the contract for announcing that a terminal connection has gone away. This lets the transport stop offering a terminal that is no longer connected.

**Data flow**: It receives a conversation id. The implementation reduces or removes the recorded connection for that conversation. It returns nothing.

**Call relations**: Surface routes call this through the transport interface when a held client stream ends; concrete implementations decide how to clean up.


##### `TerminalTransport.workspace`  (lines 222–222)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Defines how code can ask where a conversation's connected terminal is currently rooted. This is a quick lookup, not a wait.

**Data flow**: It receives a conversation id and reads the transport's known binding. It returns a `TerminalWorkspace` if a terminal is connected, or `None` if not.

**Call relations**: This protocol method lets higher-level code inspect the terminal binding without knowing whether the data is stored in memory or somewhere else.


##### `TerminalTransport.arrived`  (lines 224–224)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Defines how code can wait for a terminal to show up. This is important because the client stream often disconnects and reconnects between turns.

**Data flow**: It receives a conversation id and a grace period in seconds. The implementation either returns the terminal workspace when it is present, or `None` after the wait expires.

**Call relations**: Sandbox opening and operation sending use this protocol method so they can tolerate normal reconnect gaps.


##### `TerminalTransport.send`  (lines 226–235)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Defines how the server asks the user's terminal to perform one operation and waits for the answer. Operations include running commands, reading files, writing files, and file-browser actions.

**Data flow**: It receives the conversation id, operation kind, timeout, optional operation name, path argument, JSON parameters, and optional bytes to stage. The implementation delivers the request to the terminal and returns reply bytes, or raises an error if the terminal cannot answer.

**Call relations**: The `TerminalCarrier` uses this method for almost all real work, while the terminal-facing routes use companion methods such as `next_op`, `staged`, and `resolve` to complete the other half.


##### `TerminalTransport.next_op`  (lines 237–239)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Defines how a connected terminal asks, "What should I do next?" It waits until there is a pending operation for that conversation.

**Data flow**: It receives a conversation id and optionally an operation id to skip. It reads the pending operation state and returns a `TerminalOp` for the client to run.

**Call relations**: Terminal client streams call this side of the rendezvous, while `send` creates the operations that it returns.


##### `TerminalTransport.staged`  (lines 241–243)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Defines how the terminal client fetches staged bytes for an operation, such as file contents for a write. The bytes are kept separate from the small directive message.

**Data flow**: It receives the conversation id, operation id, and optional member id. The implementation checks that the operation is still current and authorized, then returns the stored bytes or `None`.

**Call relations**: This supports write-style operations after `send` has staged a body and after `next_op` has told the client which operation to run.


##### `TerminalTransport.resolve`  (lines 245–252)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Defines how the terminal client reports that an operation finished. It can report either successful reply bytes or a failure message.

**Data flow**: It receives the conversation id, operation id, reply bytes, optional failure text, and optional member id. The implementation matches this to the waiting operation and wakes the server-side sender. It returns `true` if the reply was accepted and `false` if it was stale or unauthorized.

**Call relations**: This is the answer half of the request started by `send`; terminal-facing routes call it after running the operation locally.


##### `TerminalTransport.in_flight`  (lines 254–254)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Defines how code can inspect the operation currently waiting for a reply. This is mainly useful for tests or operational visibility.

**Data flow**: It receives a conversation id and reads the transport's current slot. It returns the pending `TerminalOp` if one exists, otherwise `None`.

**Call relations**: This protocol method gives observers a safe view of the same operation state used by `send`, `next_op`, and `resolve`.


##### `_wake`  (lines 257–266)

```
def _wake(waiter: _Waiter, answer: object) -> None
```

**Purpose**: Wakes an asynchronous waiter safely, even when the waking code is running on a different thread. This matters because the workflow side and the web-connection side use different event loops.

**Data flow**: It receives a stored future plus the event loop that owns it, and an answer value. It schedules a small callback on that loop so the future is completed in the right place. It returns nothing, but causes the waiting task to resume.

**Call relations**: `Terminals.connect`, `Terminals.send`, and `Terminals.resolve` call this whenever one side of the rendezvous needs to wake the other side without touching an asyncio future from the wrong thread.

*Call graph*: called by 3 (connect, resolve, send).


##### `_wake._set`  (lines 262–264)

```
def _set() -> None
```

**Purpose**: Completes the waiting future if it has not already been completed. It is the tiny callback that actually runs on the future's own event loop.

**Data flow**: It closes over the future and the answer passed to `_wake`. When the event loop runs it, it checks whether the future is still pending and, if so, stores the answer in it. It produces no direct return value.

**Call relations**: `_wake` schedules this callback; separating it keeps the thread-sensitive future update inside the correct event loop.


##### `Terminals.connect`  (lines 282–295)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Records that a user's terminal is connected for a conversation. If someone was waiting for the terminal to arrive, it wakes them.

**Data flow**: It receives the conversation id, current directory, and member id. Under a lock, it creates or updates that conversation's slot, increments the connection count, and collects arrival waiters. After releasing the lock, it wakes those waiters.

**Call relations**: This is called from the surface side when a client stream connects. It uses `_wake` to resume tasks waiting in `Terminals.arrived`.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `Terminals.disconnect`  (lines 297–304)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Records that one terminal connection for a conversation has ended. If no connection remains and no operation is waiting, it removes the slot.

**Data flow**: It receives a conversation id, finds the slot, and lowers its connection count. If the slot is idle and the count reaches zero, it deletes the stored state. It returns nothing.

**Call relations**: This is the counterpart to `Terminals.connect`, used when the surface sees the user's held stream close.


##### `Terminals.workspace`  (lines 306–311)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the current bound directory and member for a connected terminal, if one is known. It is a simple snapshot of the connection state.

**Data flow**: It receives a conversation id and reads the matching slot under a lock. If present, it builds a `TerminalWorkspace` from the slot's directory and member id; otherwise it returns `None`.

**Call relations**: Other code can use this to check the terminal binding without waiting. It mirrors the protocol's `workspace` method.

*Call graph*: 1 external calls (__init__).


##### `Terminals.arrived`  (lines 313–335)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal to be connected to a conversation. This avoids false failures during the normal short gap while the client reconnects.

**Data flow**: It receives a conversation id and grace period. It checks for an existing slot; if none exists, it registers a future to be woken by `connect` and waits until either the terminal arrives or time runs out. It returns a `TerminalWorkspace` or `None`.

**Call relations**: `Terminals.send` calls this before sending an operation. If the wait times out, it asks `_drop_arrival` to remove its unused waiter.

*Call graph*: calls 1 internal fn (_drop_arrival); called by 1 (send); 3 external calls (__init__, get_running_loop, wait_for).


##### `Terminals._drop_arrival`  (lines 337–342)

```
def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None
```

**Purpose**: Removes a no-longer-needed arrival waiter. This prevents timed-out waits from staying in memory and being woken later by mistake.

**Data flow**: It receives a conversation id and the future that should be removed. Under a lock, it filters that future out of the arrival-waiter list and deletes the list if it becomes empty. It returns nothing.

**Call relations**: `Terminals.arrived` calls this when its wait expires, so `Terminals.connect` will not later wake a task that has already given up.

*Call graph*: called by 1 (arrived).


##### `Terminals.send`  (lines 344–419)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one operation to the connected terminal and waits for its reply. It is the main server-side half of the terminal rendezvous.

**Data flow**: It receives the conversation id, operation details, timeout, and optional body bytes. It waits for a terminal, waits for the conversation's single-operation turn, creates a unique operation id, stores the operation in the slot, wakes any terminal stream waiting in `next_op`, then waits for `resolve` to provide reply bytes. It returns those bytes, or raises if the terminal disappears, times out, or reports failure.

**Call relations**: `TerminalCarrier` methods rely on this to run commands and file operations. It calls `arrived` first, `_take_turn` to serialize operations, and `_wake` to notify a waiting terminal stream or queued senders.

*Call graph*: calls 3 internal fn (_take_turn, arrived, _wake); 5 external calls (__init__, __init__, get_running_loop, wait_for, uuid4).


##### `Terminals._take_turn`  (lines 421–452)

```
async def _take_turn(self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int) -> None
```

**Purpose**: Makes sure only one operation at a time is active for a conversation. Later operations wait in a queue instead of colliding with the current one.

**Data flow**: It receives the conversation id, the caller's event loop, and the operation timeout. If the slot is free, it marks it busy and returns. If another operation is running, it queues a future and waits up to the allowed time. On timeout, it removes its queue entry and raises `TerminalGone`.

**Call relations**: `Terminals.send` calls this before installing a new operation. When `send` finishes, it wakes queued waiters so they can compete for the next turn.

*Call graph*: called by 1 (send); 5 external calls (__init__, create_future, time, wait_for, deque).


##### `Terminals.next_op`  (lines 454–479)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Lets the connected terminal stream wait for the next operation it should run. It returns an already-waiting operation immediately when possible.

**Data flow**: It receives a conversation id and optionally an operation id to exclude. It checks the slot: if there is an undelivered matching operation, it marks it delivered and returns it. Otherwise it stores a watcher future and waits until `send` wakes it with a new operation.

**Call relations**: The terminal-facing route uses this after connecting or after posting a result. It pairs with `Terminals.send`, which creates operations, and `Terminals.resolve`, which answers them.

*Call graph*: 2 external calls (__init__, get_running_loop).


##### `Terminals.staged`  (lines 481–493)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Returns the bytes attached to the current operation, such as the contents of a file being written. It only serves bytes for the exact operation that is still in flight.

**Data flow**: It receives a conversation id, operation id, and optional member id. It checks that the slot, operation id, and member match. If they do, it returns the stored bytes; otherwise it returns `None`.

**Call relations**: A terminal client uses this after `next_op` tells it about an operation that needs a body. The bytes were originally placed there by `Terminals.send`.


##### `Terminals.in_flight`  (lines 495–500)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports the operation currently waiting for a terminal reply. This is a read-only inspection helper.

**Data flow**: It receives a conversation id and reads the slot under a lock. It returns the current `TerminalOp` or `None` if there is no slot or no active operation.

**Call relations**: Tests or operator tools can use this to see what `send` is waiting on without participating in the normal `next_op` and `resolve` flow.


##### `Terminals.resolve`  (lines 502–531)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts the terminal client's answer for an in-flight operation and wakes the server-side sender. It ignores stale, duplicate, or unauthorized answers.

**Data flow**: It receives the conversation id, operation id, reply bytes, optional failure text, and optional member id. Under a lock, it verifies that the reply matches the current operation and has not already been resolved. It then wakes the waiting sender with either the bytes or a `TerminalOpFailed` marker. It returns whether the answer was accepted.

**Call relations**: The terminal-facing route calls this after the user-side client finishes an operation. It uses `_wake` to resume the `Terminals.send` call that is waiting for the result.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `TerminalCarrier.create`  (lines 546–588)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a sandbox handle for a conversation whose sandbox is the user's connected terminal. It verifies that the connected terminal is in the expected workspace directory.

**Data flow**: It receives a `SandboxSpec`, waits for the terminal binding, checks that the bound directory matches the requested workspace, builds proxy environment variables using the run token, and returns a `SandboxHandle`. If no matching terminal is connected, it raises `TerminalGone`.

**Call relations**: Sandbox setup calls this when choosing the terminal-backed carrier. The returned handle is later used by `exec`, `read`, `write`, and `file_op`.

*Call graph*: 3 external calls (__init__, __init__, urlsplit).


##### `TerminalCarrier.attach`  (lines 590–603)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reattaches to an existing terminal-backed sandbox outside the main turn, such as for file browsing or background writes. It only succeeds if the terminal is currently bound to the requested directory.

**Data flow**: It receives a `SandboxSpec`, checks for an arrived terminal with no grace wait, and compares its directory to the resume id. If they match, it returns a `SandboxHandle`; otherwise it returns `None`.

**Call relations**: This is used for off-turn access. It depends on the transport's `arrived` method so it can work even when a different process or backend is holding the terminal connection.

*Call graph*: 1 external calls (__init__).


##### `TerminalCarrier.exec`  (lines 605–618)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command in the user's terminal-backed workspace. It converts logical `/workspace` paths into the user's real directory before sending the command.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It finds the real workspace root, rewrites each argument from `/workspace` to that root, and passes the rewritten command to `_exec`. It returns an `ExecResult`.

**Call relations**: Higher-level sandbox code calls this for normal command execution. It delegates the actual terminal request and reply parsing to `TerminalCarrier._exec`.

*Call graph*: calls 2 internal fn (_exec, _root).


##### `TerminalCarrier._exec`  (lines 620–655)

```
async def _exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command whose paths have already been prepared for the user's machine. It is the lower-level command runner used when callers must avoid a second path rewrite.

**Data flow**: It receives a handle, already-resolved command arguments, and a timeout. It sends an `exec` operation through the terminal transport, parses the JSON reply, decodes base64 stdout and stderr, normalizes timeout exit codes, and returns an `ExecResult`.

**Call relations**: `TerminalCarrier.exec` calls this for ordinary commands, and `TerminalCarrier.file_op` calls it to run workspace enumeration commands before certain file operations.

*Call graph*: calls 2 internal fn (_reply_object, _reply_stream); called by 2 (exec, file_op); 2 external calls (__init__, dumps).


##### `TerminalCarrier.write`  (lines 657–670)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file in the user's terminal workspace. The file content is staged separately instead of being squeezed into the directive message.

**Data flow**: It receives a handle, logical path, and bytes. It maps the path to the user's real workspace, sends a write operation with the bytes as staged body, and returns nothing on success. If the terminal reports failure, it raises `OSError`.

**Call relations**: File-writing code calls this through the sandbox carrier interface. It uses `_client_path` for safe path mapping and the transport's `send` method to deliver the write request.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.read`  (lines 672–688)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a file from the user's terminal workspace and yields it in chunks. It translates missing-file failures into the same kind of error other sandbox carriers use.

**Data flow**: It receives a handle and logical path. It maps the path to the real workspace, sends a read operation, receives the whole file as reply bytes, and yields those bytes in one-megabyte chunks. If the terminal says the file is missing, it raises `FileNotFoundError`.

**Call relations**: File-reading code calls this through the sandbox carrier interface. It relies on `_client_path` for path mapping and the transport's `send` method for the actual client request.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.file_op`  (lines 690–739)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level file-system operation, such as search, globbing, or change listing, on the user's machine. For tree walks, it first creates a server-controlled listing so the client does not choose a different set of files.

**Data flow**: It receives a handle, operation name, and parameter dictionary. It rewrites only parameters that are workspace paths, may run an enumeration shell command through `_exec`, sends the file operation to the terminal client, parses the JSON reply, and returns the result dictionary. If the client reports an operation error, it raises an exception.

**Call relations**: The file-browser and sandbox file APIs use this for structured file work. It calls `_root` and `_under_root` for path safety, `_exec` for pre-walk enumeration, and `_reply_object` for result parsing.

*Call graph*: calls 4 internal fn (_exec, _reply_object, _root, _under_root); 2 external calls (dumps, quote).


##### `TerminalCarrier.dial`  (lines 741–745)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Refuses attempts to expose a network port from a terminal-backed sandbox. A user's terminal is not a remote container with per-port service routing.

**Data flow**: It receives a sandbox handle and port number but does not use them to build a target. It raises `SandboxUnreachable` explaining that this carrier cannot provide external per-port access.

**Call relations**: Code that expects carriers to support dialing may call this, but for terminal-backed sandboxes it deliberately stops the flow and tells callers to use a remote carrier instead.

*Call graph*: 1 external calls (__init__).


##### `_reply_stream`  (lines 748–756)

```
def _reply_stream(result: dict[str, object], name: str) -> bytes
```

**Purpose**: Extracts one captured command stream, such as stdout or stderr, from a terminal exec reply. The client sends these streams as base64 text, which is a safe text form for arbitrary bytes.

**Data flow**: It receives a parsed reply dictionary and a stream name. It looks for the matching base64 field, validates and decodes it, and returns raw bytes. If the field is missing or malformed, it raises an error rather than pretending the stream was empty.

**Call relations**: `TerminalCarrier._exec` calls this twice, once for stdout and once for stderr, after `_reply_object` has parsed the reply JSON.

*Call graph*: called by 1 (_exec); 1 external calls (b64decode).


##### `_reply_object`  (lines 759–766)

```
def _reply_object(reply: bytes, op: str) -> dict[str, object]
```

**Purpose**: Parses a terminal reply as a JSON object. JSON is the text format used here for structured replies such as command results and file-operation results.

**Data flow**: It receives raw reply bytes and the operation name for error messages. It decodes the bytes as UTF-8, parses JSON, verifies that the result is a dictionary-like object, and returns it. Bad JSON or the wrong shape raises `RuntimeError`.

**Call relations**: `TerminalCarrier._exec` uses this for exec replies, and `TerminalCarrier.file_op` uses it for file-operation replies.

*Call graph*: called by 2 (_exec, file_op); 1 external calls (loads).


##### `_root`  (lines 769–772)

```
def _root(handle: SandboxHandle) -> str
```

**Purpose**: Returns the real directory on the user's machine that backs `/workspace`. It ensures terminal-backed handles really have such a directory.

**Data flow**: It receives a sandbox handle. If the handle has a workspace host path, it returns that string; otherwise it raises an error because terminal sandboxes must be tied to a local directory.

**Call relations**: `TerminalCarrier.exec`, `TerminalCarrier.file_op`, and `_client_path` use this before rewriting paths.

*Call graph*: called by 3 (exec, file_op, _client_path).


##### `_client_path`  (lines 775–781)

```
def _client_path(handle: SandboxHandle, path: str) -> str
```

**Purpose**: Maps a logical `/workspace/...` path into the user's real workspace directory. It strips only the leading `/workspace` prefix, not every occurrence of the word.

**Data flow**: It receives a sandbox handle and a path. It gets the workspace root with `_root`, then passes the root and path to `_under_root`. It returns the mapped path string.

**Call relations**: `TerminalCarrier.read` and `TerminalCarrier.write` call this before asking the terminal client to touch a file.

*Call graph*: calls 2 internal fn (_root, _under_root); called by 2 (read, write).


##### `_under_root`  (lines 784–789)

```
def _under_root(root: str, path: str) -> str
```

**Purpose**: Performs the actual safe path mapping from `/workspace` to the real root directory. Paths outside `/workspace` are left unchanged.

**Data flow**: It receives a root directory and a path string. It treats the path as a POSIX-style path, checks whether it is under `/workspace`, and if so joins the relative part onto the real root. It returns the original path or the rewritten path.

**Call relations**: `_client_path` uses this for read and write paths, and `TerminalCarrier.file_op` uses it for selected file-operation parameters.

*Call graph*: called by 2 (file_op, _client_path); 1 external calls (PurePosixPath).


### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox creation and command/file access during request handling`

This file is the Docker version of the sandbox “carrier,” meaning the part that gives a conversation a safe place to run code. Instead of running tools directly on the host machine, it starts a Docker container named after the conversation. The workspace directory is mounted into that container, so files survive even if the container is stopped and later restarted.

Its main job is to make containers feel persistent while still saving scarce host resources. Docker networks and memory can run out, so before creating a new sandbox it looks for old idle containers and stops them. It does not delete them, because stopping is like turning off a sleeping laptop: the files are still there, and the next command can wake it back up.

Every command runs with a fresh per-turn proxy environment. That matters because the container may outlive one model turn, but network requests must be charged and authorized for the current turn only. The proxy token is passed to each `docker exec` call, not stored permanently inside the container.

The file also installs the proxy’s certificate authority into containers so HTTPS tools trust the local proxy, streams file reads and writes safely through Docker, and refuses port dialing because this Docker carrier does not expose sandbox services to the outside world.

#### Function details

##### `_docker`  (lines 73–87)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and returns its exit code plus captured output. It gives the rest of the file one common way to talk to Docker, including a clear result when Docker takes too long.

**Data flow**: It receives Docker arguments, optional bytes for standard input, and a timeout. It starts `docker ...`, feeds the input, waits for output, and returns `(exit_code, stdout_bytes, stderr_bytes)`. If the timeout expires, it kills the Docker process and returns a special timeout code with `timed out` as the error text.

**Call relations**: Almost every Docker-facing method in this file goes through this helper, including container lookup, creation, execution, network setup, certificate installation, reclaim, revive, and inspection. It is the small doorway between the Python carrier code and the Docker daemon.

*Call graph*: called by 12 (_death_report, _ensure_network, _held_id, _install_ca, _reclaim_idle, _release, _revive, _running_id, _stopped_id, _write_started (+2 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 100–203)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or reconnects to the Docker container for a conversation. It also builds the per-turn proxy environment so commands inside the sandbox use the right network token and fake API key placeholders.

**Data flow**: It receives a sandbox specification containing the conversation id, image, workspace path, proxy details, token, and environment. It first reclaims idle containers, then checks whether this conversation already has a running or stopped container. If one exists, it starts or reuses it and installs the current proxy certificate. If not, it creates the per-conversation Docker network, starts a new container with the workspace mounted, installs the certificate, and returns a `SandboxHandle` describing the usable sandbox.

**Call relations**: This is the main open-the-sandbox path used by the core system when a conversation needs a Docker sandbox. It relies on the lookup helpers to find existing containers, `_revive` to wake stopped ones, `_ensure_network` and `_network_name` to prepare networking, `_install_ca` to keep HTTPS working through the proxy, `_reclaim_idle` to free old resources, and `_docker` to run the actual Docker commands.

*Call graph*: calls 8 internal fn (_ensure_network, _install_ca, _network_name, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 1 external calls (__init__).


##### `DockerCarrier.attach`  (lines 205–229)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Finds an existing sandbox container for a conversation without creating a new one. It is useful for read-style access where absence should simply mean “there is no sandbox to attach to.”

**Data flow**: It receives a sandbox specification and builds the expected container name. If the container is running, it returns a `SandboxHandle`. If it is stopped, it tries to revive it and then returns a handle. If no container exists, or revival fails in the allowed ways, it returns `None`.

**Call relations**: This is the gentler companion to `create`: it uses `_running_id` and `_stopped_id` to look for a container and `_revive` to restart one if possible. Unlike `create`, it never calls Docker run and never makes a fresh sandbox.

*Call graph*: calls 3 internal fn (_revive, _running_id, _stopped_id); 1 external calls (__init__).


##### `DockerCarrier._reclaim_idle`  (lines 231–299)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops old, idle sandbox containers so the host gets memory and Docker network space back. It preserves the container and workspace so a later command can restart the sandbox instead of losing work.

**Data flow**: It receives the conversation currently being opened and marks it as freshly touched. It asks Docker for known UFO containers and networks, records any it did not already know about, then finds conversations that have been idle long enough and have no command currently running. For each stale conversation, it removes the local touch record, stops the container, removes its network, and restores the touch record if the release did not fully succeed.

**Call relations**: `create` calls this before opening a sandbox, making new activity the trigger for cleanup. It uses `_held_id` to find a container in any state and `_release` to perform the stop-and-network-removal sequence, while `_docker` supplies the Docker listings.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 301–333)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside a conversation’s Docker container. It pins the container as active while the command runs, so cleanup will not stop it mid-command.

**Data flow**: It receives a sandbox handle, a command argument tuple, and a timeout. It marks the conversation as in flight, turns the handle’s proxy environment into Docker `--env` arguments, and runs `docker exec`. If Docker says the container is not running, it tries to revive the container and run the command once more. It returns an `ExecResult` with decoded output, error text, exit code, and timeout information.

**Call relations**: The rest of the sandbox system uses this as the normal command execution path. It delegates Docker work to `_docker`, uses `_revive` if reclaim stopped the container, and returns the standard execution result expected by callers.

*Call graph*: calls 2 internal fn (_revive, _docker); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 335–353)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the sandbox workspace. It streams the content through Docker standard input instead of putting file contents on a command line.

**Data flow**: It receives a sandbox handle, a target path, and bytes to write. It marks the container as busy, calls `_write_started` to perform the actual copy, and retries after `_revive` if Docker reports the container was stopped. If the write still fails, it raises an `OSError`; otherwise it returns nothing after the file has been written.

**Call relations**: This is the public write path for the Docker carrier. It uses `_write_started` for the low-level Docker exec call and `_revive` to recover from an idle reclaim that happened before the write began.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 355–371)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Performs one actual write attempt into the container. It uses a sandbox-side copy program that is designed to write safely within the workspace.

**Data flow**: It receives the sandbox handle, path, and content bytes. It runs `docker exec -i` with Python inside the container, feeds the content as standard input, and tells the sandbox copy program the target path and workspace root. It returns the Docker exit code and error bytes.

**Call relations**: `write` calls this helper for the first write attempt and, if needed, the retry after revival. `_write_started` itself uses `_docker` to run the Docker command.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write).


##### `DockerCarrier.read`  (lines 373–407)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. This avoids loading the entire file into host memory and reads the file as the container sees it.

**Data flow**: It receives a sandbox handle and path. It marks the conversation active, starts a streaming `cat` command through `_read_started`, yields each byte chunk to the caller, and then checks whether the command failed. If the failure means the container was stopped before data was read, it revives and retries from the start. If `cat` reports a normal file error, it raises a matching `OSError`; otherwise it raises a runtime error with extra container state.

**Call relations**: This is the public read path for sandbox files. It uses `_read_started` to create the stream, `_revive` to recover from stopped containers, and `_death_report` when a failed read gives no useful error text.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 409–451)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Sets up one file-read attempt and returns both the byte stream and a place where any final failure will be recorded. It separates starting a read from retry logic so the outer `read` method can safely restart from the beginning if needed.

**Data flow**: It receives a sandbox handle and path. It creates an initially empty failure list and an async generator that will run `docker exec cat path`. It returns the generator plus the list; while the generator is consumed, bytes come out, and after it finishes, a non-zero exit code may be added to the failure list.

**Call relations**: `read` calls this helper for the first read and possibly for a second read after revival. The nested `stream` generator does the actual subprocess work.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 424–449)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs `cat` inside the container and yields the file contents as chunks. It also cleans up the Docker exec process if the caller stops reading early.

**Data flow**: It starts a Docker subprocess with stdout and stderr pipes. It repeatedly reads up to the configured chunk size from stdout and yields each chunk. After stdout ends, it reads stderr, waits for the process exit code, and records failure details if the command failed. If the generator is abandoned while the process is still running, it kills the process and drains its pipes.

**Call relations**: This generator is created by `_read_started` and consumed by `read`. It is the only part of the read path that directly starts the streaming Docker subprocess.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 453–471)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Adds useful Docker container state to a mysterious read failure. It helps explain whether the container was still running, exited, killed for memory, or missing.

**Data flow**: It receives a sandbox handle and runs `docker inspect` for that container. If inspection succeeds, it returns a text snippet with the container status, exit code, and whether Docker says it was out-of-memory killed. If inspection fails, it returns a snippet saying inspection itself failed and includes Docker’s error text.

**Call relations**: `read` calls this only when a `cat` command dies without helpful stderr. `_death_report` uses `_docker` to ask Docker for the container’s state.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.file_op`  (lines 473–478)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured sandbox file operation, such as one provided by the project’s `sbxfs` file-tool layer. It lets higher-level code perform file actions through the same Docker sandbox boundary.

**Data flow**: It receives a sandbox handle, an operation name, and operation parameters. It passes those to `sbxfs_file_op`, using this carrier as the executor. The result is a dictionary describing the file operation’s outcome.

**Call relations**: This method is the bridge between DockerCarrier and the shared sandbox file-operation helper. The helper will use the carrier’s execution behavior so the container is pinned and revivable like any other command.

*Call graph*: 1 external calls (sbxfs_file_op).


##### `DockerCarrier.dial`  (lines 480–487)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Clearly says that this Docker carrier cannot expose a service running inside the sandbox to the outside world. For example, it cannot provide an external URL for a dev server or browser debugging port inside the container.

**Data flow**: It receives a sandbox handle and port number, but does not try to connect. It immediately raises `SandboxUnreachable` with an explanation.

**Call relations**: Callers use `dial` when they need to reach an in-sandbox network service. This implementation stops that flow early and points users toward a remote carrier that supports external per-port routing.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 489–503)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a container and removes its per-conversation Docker network. This is how idle reclaim gives scarce Docker resources back to the host without deleting the workspace.

**Data flow**: It receives a conversation id and optionally a container id. If a container id is present, it asks Docker to stop it. Then it removes the conversation’s network. It returns `true` when the stop and network removal reached the desired state, including the case where the network was already gone; otherwise it returns `false` so cleanup can be retried later.

**Call relations**: `_reclaim_idle` calls this while holding the conversation’s lifecycle lock. It uses `_network_name` to compute the network name and `_docker` to stop and remove Docker resources.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 505–525)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Starts a stopped sandbox container again. It recreates and reconnects the conversation’s Docker network first, because reclaim may have removed that network when it stopped the container.

**Data flow**: It receives a conversation id and container id. Under a lifecycle lock, it marks the conversation as touched, ensures the network exists, connects the container to it, and starts the container. It returns `true` if the container is running again, `false` if Docker refuses the connect or start in a recoverable way, and raises if the network cannot be ensured.

**Call relations**: This is the shared wake-up path used by `create`, `attach`, `exec`, `write`, and `read`. It depends on `_network_name`, `_ensure_network`, and `_docker`, and it is ordered against `_release` by the same per-conversation lock.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (attach, create, exec, read, write).


##### `DockerCarrier._held_id`  (lines 527–535)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds a container with the given name no matter whether it is running, paused, or exited. Reclaim needs this because any existing container may still be tied to resources that should be released.

**Data flow**: It receives a Docker container name. It asks Docker for any matching container id, including stopped ones. It returns the id as text if found, `None` if not found, and raises if Docker itself fails.

**Call relations**: `_reclaim_idle` calls this before releasing a stale conversation. `_held_id` uses `_docker` to query Docker.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 537–546)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds a stopped container with the given name. This lets the carrier recognize a sandbox that was reclaimed earlier and can be restarted instead of recreated.

**Data flow**: It receives a Docker container name. It asks Docker for an exited container with that exact name. It returns the container id if present, `None` if no stopped match exists, and raises if the Docker query fails.

**Call relations**: `create` uses this to decide whether to revive or replace an old container, and `attach` uses it to reconnect to an existing stopped sandbox. The Docker query goes through `_docker`.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 548–559)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds a running container with the given name. It treats Docker command failures as real errors, not as “not found,” so callers do not make misleading decisions during Docker hiccups.

**Data flow**: It receives a Docker container name. It asks Docker for a running container with that exact name. It returns the container id if found, `None` if the query succeeds with no match, and raises if Docker cannot answer properly.

**Call relations**: `create` and `attach` use this as their first check for an already-usable sandbox. It delegates the Docker lookup to `_docker`.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 561–562)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the deterministic Docker network name for a conversation. A predictable name lets separate calls find the same network again after restarts or races.

**Data flow**: It receives a conversation UUID. It combines the carrier’s network prefix with the UUID in compact hexadecimal form and returns that string.

**Call relations**: `create`, `_revive`, and `_release` call this whenever they need to create, reconnect, or remove the per-conversation network.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 564–575)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, without failing if another concurrent call created it first. This is important when two requests for the same conversation arrive at nearly the same time.

**Data flow**: It receives a network name. It asks Docker whether a matching network already exists; if so, it returns. If not, it asks Docker to create it. If Docker says the network already exists, that is treated as success; other Docker errors become runtime errors.

**Call relations**: `create` calls this before starting a fresh container, and `_revive` calls it before reconnecting a stopped container. All Docker communication goes through `_docker`.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 577–590)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current egress proxy certificate into a container so HTTPS tools inside it trust the proxy. This must happen even for reused containers because the proxy certificate can change when the host process restarts.

**Data flow**: It receives a container id and certificate text. It runs a root command inside the container, writes the certificate file through standard input, and updates the container’s certificate store. It returns nothing on success and raises a runtime error if the install command fails.

**Call relations**: `create` calls this on every successful path: newly created containers, already running containers, revived containers, and containers won after a name race. It uses `_docker` to run the root-level Docker exec.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `manifest`  (lines 593–598)

```
def manifest() -> Manifest
```

**Purpose**: Advertises this extension to the UFO plugin system. It says that the carrier named `docker` is provided by the `DockerCarrier` class.

**Data flow**: It takes no input. It builds a `Manifest` containing the extension name, version, and a carrier specification whose factory is `DockerCarrier`, then returns that manifest.

**Call relations**: The extension loader calls this when discovering available carriers. The returned manifest is how the core system learns that setting the sandbox backend to Docker should instantiate `DockerCarrier`.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `request handling and sandbox operation`

A sandbox is the isolated computer where a conversation’s commands and files live. This file is the bridge between UFO’s generic sandbox interface and E2B’s remote cloud sandboxes. Without it, a deployment that chooses the `e2b` backend could not create a workspace, run shell commands, upload or read files, or expose an in-sandbox service to the outside world.

The main class, `E2BCarrier`, keeps track of live E2B sandboxes by conversation. It can open a fresh sandbox, reconnect to one that was paused, or reuse one this process already knows about. Because E2B pauses sandboxes after a timeout, this file treats each sandbox like a borrowed room with a lease: before doing work, it checks whether the lease is long enough, and reconnects to extend it when needed.

It also prepares each sandbox so it can work in UFO’s environment. It installs the egress proxy certificate, creates `/workspace`, and sets environment variables so network traffic goes through the metered proxy instead of carrying real model API keys inside the sandbox.

Command execution has extra care. Commands are launched in their own process group so timeouts and user stops can kill the whole tree of child processes, not just the shell. File reads stream data in chunks. Dials return a public E2B host and access token for services started inside the sandbox.

#### Function details

##### `_egress_env`  (lines 141–176)

```
def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside the remote sandbox send outbound network traffic through UFO’s egress proxy. It also uses placeholder model keys, so real API keys do not have to be placed inside the sandbox.

**Data flow**: It receives a proxy endpoint and a run token. It checks that the proxy has a public HTTPS URL, turns that URL into proxy settings that include the run token as the username, adds no-proxy and certificate settings, and returns a dictionary of environment variables. If the proxy URL is missing or unsafe, it raises an error instead of creating an unmetered sandbox environment.

**Call relations**: When `E2BCarrier.create` builds a `SandboxHandle`, it calls this first so every later command can inherit the right proxy and certificate settings.

*Call graph*: called by 1 (create); 1 external calls (urlsplit).


##### `E2BCommandHandle.wait`  (lines 192–192)

```
async def wait(self) -> E2BCommandResult
```

**Purpose**: Describes the SDK call used to wait for a command that was started in the background. It exists so this file can type-check the E2B SDK behavior it relies on.

**Data flow**: The command has already been started and has a process id. Calling `wait` waits until it finishes, then produces stdout, stderr, and an exit code.

**Call relations**: This is part of the protocol used by `E2BCarrier.exec` after it starts a command in the background so it can learn the process id before waiting for the result.


##### `E2BCommands.run`  (lines 213–222)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None, background: Literal[False]=False) -> E2BCommandResult
```

**Purpose**: Describes the SDK method for running a shell command inside an E2B sandbox. It can either return the finished command result or a handle to a still-running background command.

**Data flow**: It receives a command string plus optional working directory, environment variables, user, timeout, and background choice. It sends that command to the sandbox and returns either the command’s final output or a running-command handle.

**Call relations**: The carrier uses this protocol throughout preparation, command execution, stop signaling, and health probes. The real implementation is supplied by the E2B SDK.


##### `E2BFileStream.__aiter__`  (lines 229–229)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: Describes how a streamed file read produces chunks of bytes asynchronously. This lets large files be read without loading the whole file at once.

**Data flow**: It starts from an open file stream and yields byte chunks one at a time as the caller asks for them.

**Call relations**: The stream returned by `E2BFiles.read` is consumed by `E2BCarrier.read`, which passes each chunk onward to its caller.


##### `E2BFileStream.aclose`  (lines 231–231)

```
async def aclose(self) -> None
```

**Purpose**: Describes how to close an open streamed file read. This matters because an unfinished stream still holds a network connection.

**Data flow**: It receives the open stream object, closes the underlying remote read, and returns nothing.

**Call relations**: `E2BCarrier.read` calls this in a cleanup block so the connection is released even if the caller stops reading partway through.


##### `E2BFiles.write`  (lines 235–235)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: Describes the SDK method for writing data into a file inside the sandbox. It is the safe path for sending raw bytes, since command execution only accepts shell text.

**Data flow**: It receives a remote path, text or bytes, and optionally a user. It sends that content to the sandbox filesystem and returns the SDK’s write result.

**Call relations**: `E2BCarrier.write` uses this for normal file uploads, and `_install_ca` uses it to place the proxy certificate in the sandbox.


##### `E2BFiles.read`  (lines 237–237)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: Describes the SDK method for reading a file from the sandbox. In this file it is used in streaming mode so large outputs can be transferred gradually.

**Data flow**: It receives a path and a format such as `stream`. It opens a remote read and returns an async byte stream.

**Call relations**: `E2BCarrier.read` calls this, translates E2B’s missing-file error into Python’s `FileNotFoundError`, and then yields the stream’s chunks.


##### `E2BSandbox.get_host`  (lines 246–246)

```
def get_host(self, port: int) -> str
```

**Purpose**: Describes how to turn a port inside the sandbox into an externally reachable hostname. This is needed when a tool starts a web server or browser debugging endpoint inside the sandbox.

**Data flow**: It receives an internal port number and returns the host name that E2B uses to route public traffic to that port.

**Call relations**: `E2BCarrier.dial` calls this after renewing the sandbox lease, then wraps the host with TLS and access-token information.


##### `E2BSdk.create`  (lines 250–259)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the SDK call that creates a brand-new E2B sandbox from a template. UFO uses it when there is no usable existing sandbox for a conversation.

**Data flow**: It receives the template name, timeout lease, metadata, lifecycle rules, network rules, and API key. It asks E2B to create the sandbox and returns an object that can run commands, read files, and expose ports.

**Call relations**: `E2BCarrier._resume_or_open` calls this only after it has decided it cannot reconnect to an existing sandbox.


##### `E2BSdk.connect`  (lines 261–267)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the SDK call that reconnects to an existing sandbox by id. In E2B, this can also wake a paused sandbox and extend its timeout.

**Data flow**: It receives a sandbox id, a lease span, and the API key. It asks E2B for that sandbox and returns the live sandbox object, or raises if E2B no longer has it.

**Call relations**: All reconnects go through `E2BCarrier._connected`, which wraps this call with retries, logging, and a total time limit.


##### `E2BCarrier.create`  (lines 313–389)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Opens the sandbox that a conversation should use and returns a generic UFO sandbox handle. It may resume a durable sandbox, reuse this process’s current one, or create a fresh E2B sandbox.

**Data flow**: It receives a `SandboxSpec` containing the conversation id, possible resume id, proxy details, run token, size, and environment. It builds the proxy environment, chooses which sandbox id to resume if any, opens or reconnects the sandbox, prepares its certificate and workspace, stores a lease in memory, and returns a `SandboxHandle` with the container id and command environment.

**Call relations**: This is the main setup path for the carrier. It calls `_egress_env`, checks `_leased`, delegates opening to `_resume_or_open`, prepares through `_prepare` or `_prepare_strictly`, then hands the ready sandbox back to UFO as a `SandboxHandle`.

*Call graph*: calls 5 internal fn (_leased, _prepare, _prepare_strictly, _resume_or_open, _egress_env); 5 external calls (__init__, __init__, timeout, emit_metric, log).


##### `E2BCarrier.attach`  (lines 391–415)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an already-known sandbox without creating a replacement. It is used for read-style access where missing should mean missing, not silently opening an empty new workspace.

**Data flow**: It receives a `SandboxSpec`. If there is no resume id, it returns `None`. If there is one, it tries to connect and renew the sandbox lease; on success it caches the lease and returns a `SandboxHandle`, and on E2B not-found it forgets any cached entry and returns `None`.

**Call relations**: Unlike `create`, this function calls `_connected` directly and never calls `_resume_or_open`, because its job is only to attach to the exact stored sandbox if it still exists.

*Call graph*: calls 1 internal fn (_connected); 2 external calls (__init__, __init__).


##### `E2BCarrier._resume_or_open`  (lines 417–457)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: Chooses between reconnecting to an existing sandbox and creating a new one. This keeps the public `create` method focused on setup while this function handles the provider choice.

**Data flow**: It receives the sandbox spec and an optional sandbox id. If an id is present, it tries `_connected`; if E2B says the sandbox is gone, it logs that fact and continues. It then finds the configured template for the requested size, creates a new sandbox through the SDK, verifies that E2B returned a traffic access token, and returns the sandbox.

**Call relations**: `E2BCarrier.create` calls this after deciding what id, if any, should be resumed. This function uses `_connected` for safe reconnects and the SDK’s create call for new sandboxes.

*Call graph*: calls 1 internal fn (_connected); called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare_strictly`  (lines 459–475)

```
async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None
```

**Purpose**: Prepares a newly created or otherwise unproven sandbox, retrying only the kind of network drop that may be temporary. If preparation cannot be trusted, the sandbox is not cached as usable.

**Data flow**: It receives the sandbox and the original spec. It calls `_prepare`; if a transport problem occurs and retry attempts remain, it logs and waits before trying again. If attempts run out or the error is not a retryable transport error, it drops the cached lease for the conversation and raises the failure.

**Call relations**: `E2BCarrier.create` uses this whenever it cannot safely assume the sandbox was prepared before. It calls `_prepare`, `_drop`, logging, metrics, and sleep as part of the retry story.

*Call graph*: calls 2 internal fn (_drop, _prepare); called by 1 (create); 3 external calls (sleep, emit_metric, log).


##### `E2BCarrier._connected`  (lines 477–539)

```
async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox
```

**Purpose**: Reconnects to an E2B sandbox with bounded retries. It is the single safe doorway for E2B `connect` calls because provider network failures can leave the caller unsure whether the request succeeded.

**Data flow**: It receives a conversation id, sandbox id, and desired lease span. It repeatedly calls the SDK’s `connect`, retrying transport errors with backoff, but not retrying a clear not-found response. The whole process is capped by a total timeout; if that cap is reached, it raises the last transport error or a read-timeout error.

**Call relations**: `_resume_or_open`, `_sandbox`, and `attach` all use this when they need to resume or renew a sandbox. It centralizes logging and retry behavior so every reconnect behaves the same way.

*Call graph*: called by 3 (_resume_or_open, _sandbox, attach); 4 external calls (sleep, timeout, ReadTimeout, log).


##### `E2BCarrier._prepare`  (lines 541–548)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Makes a sandbox usable for UFO work by installing the proxy certificate and ensuring the workspace directory exists. It is the common preparation routine for both fresh and resumed sandboxes.

**Data flow**: It receives a sandbox object and the proxy certificate text. It writes and installs the certificate, then creates and fixes ownership of `/workspace`. It returns nothing if both steps succeed, or lets an error bubble up if either step fails.

**Call relations**: `E2BCarrier.create` may call this with a short timeout for already-known resumed sandboxes, while `_prepare_strictly` calls it for sandboxes that must be proven ready.

*Call graph*: calls 2 internal fn (_ensure_workspace, _install_ca); called by 2 (_prepare_strictly, create).


##### `E2BCarrier._leased`  (lines 550–565)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: Looks up the in-memory lease for a conversation and cleans out expired lease records. This prevents the process from keeping references to every sandbox it has ever touched.

**Data flow**: It receives a conversation id. It reads the current lease map, removes entries whose recorded expiry time has passed, and returns the lease that was associated with the requested conversation if one was present.

**Call relations**: `E2BCarrier.create` uses it to see whether this process already has a sandbox to reuse. `_sandbox` uses it before deciding whether it must reconnect and renew.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 567–575)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Installs UFO’s egress proxy certificate into the sandbox trust store. This lets commands inside the sandbox trust HTTPS connections that pass through the proxy.

**Data flow**: It receives the sandbox and certificate text. It writes the certificate to a staging path as root, runs the install-and-update command, and returns nothing on success. If the command exits with an error, it raises a clearer runtime error with the command output.

**Call relations**: `_prepare` calls this before workspace setup because network access through the proxy depends on the certificate being trusted.

*Call graph*: called by 1 (_prepare).


##### `E2BCarrier._ensure_workspace`  (lines 577–586)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: Creates `/workspace` inside the sandbox and gives ownership to the normal sandbox user. This directory is where the conversation’s files live.

**Data flow**: It receives a sandbox. It runs a root command to create the directory if needed and change its owner. On command failure, it raises a runtime error with the useful output.

**Call relations**: `_prepare` calls this after installing the certificate, so every opened sandbox has the expected working directory for later commands and file operations.

*Call graph*: called by 1 (_prepare).


##### `E2BCarrier.exec`  (lines 588–674)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the sandbox and returns its stdout, stderr, exit code, and timeout information in UFO’s standard format. It also makes timeouts meaningful by trying to stop the whole command process group, not just stop waiting for output.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It renews the sandbox lease, checks whether a previously silent sandbox is still responsive, quotes the arguments into a shell command, starts it in the background to learn its process id, records that process group under the turn, waits for completion, and converts SDK results or exceptions into an `ExecResult`. On timeout it emits a metric and tries to kill the process group; on cancellation or unexpected provider errors it drops the cached lease.

**Call relations**: This is the main command-running path used by UFO tools. It relies on `_sandbox` for a live lease, `_still_there` for safety after prior silence, `_stop_group` for cleanup after timeouts, `_forget_group` when a command is no longer running, and `_drop` when the provider state can no longer be trusted.

*Call graph*: calls 5 internal fn (_drop, _forget_group, _sandbox, _still_there, _stop_group); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.stop_commands`  (lines 676–698)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Stops commands that are still running for one turn in one sandbox. This is used when a user or member stop is known to be a real stop, not just an executor retry.

**Data flow**: It receives a sandbox handle. It removes the set of recorded process ids for that container and turn; if the set is empty, it returns without contacting E2B. Otherwise it renews the sandbox briefly and sends a stop signal to each recorded process group.

**Call relations**: `exec` records process groups that are running and deliberately leaves them alive on generic cancellation. Later, this function is called from the layer that knows the cancellation means “stop this turn,” and it delegates the actual signal to `_stop_group`.

*Call graph*: calls 2 internal fn (_sandbox, _stop_group).


##### `E2BCarrier._forget_group`  (lines 700–710)

```
def _forget_group(self, handle: SandboxHandle, pid: int) -> None
```

**Purpose**: Removes a process group from the carrier’s record once it has ended or has been stopped. This keeps the stop list limited to work that may still be running.

**Data flow**: It receives a sandbox handle and process id. It finds the set for that container and turn, removes the process id, and deletes the whole entry if no process ids remain.

**Call relations**: `E2BCarrier.exec` calls this in its cleanup path whenever a started command is no longer intentionally left running.

*Call graph*: called by 1 (exec).


##### `E2BCarrier._stop_group`  (lines 712–733)

```
async def _stop_group(self, sandbox: E2BSandbox, container_id: str, pid: int) -> None
```

**Purpose**: Sends a hard kill signal to a command’s whole process group inside the sandbox. This is how the carrier tries to stop child processes as well as the shell that launched them.

**Data flow**: It receives the sandbox, container id, and process id. It runs `kill -9` against the negative process id, which means the group rather than one process. If the stop command times out or fails, it records a metric; on timeout it also marks the container as silent so the next command probes it first.

**Call relations**: `exec` calls this after a command timeout. `stop_commands` calls it when an entire turn is being stopped.

*Call graph*: called by 2 (exec, stop_commands); 1 external calls (emit_metric).


##### `E2BCarrier._still_there`  (lines 735–753)

```
async def _still_there(self, sandbox: E2BSandbox, container_id: str) -> None
```

**Purpose**: Checks whether a sandbox previously marked as silent can answer commands again. It avoids spending a full user command timeout on a sandbox that is already wedged.

**Data flow**: It receives a sandbox and container id. If the container is not marked silent, it returns immediately. If it is marked silent, it runs a tiny `true` command with a short timeout; success clears the mark, while failure raises `SandboxUnreachable` and emits a metric.

**Call relations**: `E2BCarrier.exec` calls this before launching real work. `_stop_group` and a launch timeout can mark a container as silent, and this function later decides whether it has recovered.

*Call graph*: called by 1 (exec); 2 external calls (__init__, emit_metric).


##### `E2BCarrier.write`  (lines 755–766)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Uploads bytes into a file inside the sandbox. It uses the filesystem API rather than shell commands so arbitrary binary content does not have to be squeezed through command-line text.

**Data flow**: It receives a sandbox handle, destination path, and bytes. It renews the lease long enough for the upload, writes the content through E2B’s file API, and returns nothing. If the write fails, it drops the cached lease before raising the error.

**Call relations**: This is the carrier’s file-upload path. It depends on `_sandbox` for a live sandbox and `_drop` when the provider call makes the lease untrustworthy.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 768–788)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. This lets large generated files be downloaded without storing the entire file in the UFO process at once.

**Data flow**: It receives a sandbox handle and path. It renews the sandbox for a full autosuspend span, opens a streaming read, translates E2B’s file-not-found exception into `FileNotFoundError`, yields chunks to the caller, and always closes the stream afterward.

**Call relations**: This is the carrier’s file-download path. It uses `_sandbox` before opening the stream, `_drop` if the provider read fails unexpectedly, and the stream’s `aclose` method for cleanup.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.file_op`  (lines 790–795)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level filesystem operation using UFO’s shared `sbxfs` helper. This gives E2B the same structured file-operation behavior as other sandbox carriers.

**Data flow**: It receives a sandbox handle, operation name, and parameter dictionary. It passes those values plus this carrier object to `sbxfs_file_op`, which performs the operation through the carrier’s command execution path, and returns the resulting dictionary.

**Call relations**: This function is a thin adapter. It hands off to the shared sandbox filesystem helper rather than duplicating that logic here.

*Call graph*: 1 external calls (sbxfs_file_op).


##### `E2BCarrier.dial`  (lines 797–818)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns the outside address for a service listening on a port inside the sandbox. This is used for things like browser debugging ports or preview web servers started by a command.

**Data flow**: It receives a sandbox handle and port. It renews the sandbox lease long enough for an off-carrier connection, asks E2B for the host name for that port, includes the traffic access token as a header if one exists, and returns a `DialTarget`. If the sandbox is gone, it raises `SandboxUnreachable`.

**Call relations**: Callers use this after starting a service in the sandbox. It uses `_sandbox` to make sure the sandbox will stay awake and then calls the sandbox’s `get_host` to format the route.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 820–862)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int, span_floor: int=SANDBOX_LEASE_SECONDS) -> E2BSandbox
```

**Purpose**: Returns a live sandbox object whose lease is long enough for the next operation. It is the carrier’s central lease-renewal gate.

**Data flow**: It receives a sandbox handle, the number of seconds the upcoming work needs, and an optional minimum lease span. It checks the cached lease for the conversation and container id; if it is fresh enough, it returns the cached sandbox. Otherwise it forgets the cache, reconnects through `_connected` with a long enough span, records the new lease, logs the renewal, and returns the sandbox.

**Call relations**: `exec`, `write`, `read`, `dial`, and `stop_commands` all call this before touching E2B. It uses `_leased` for cache lookup and `_connected` when renewal is needed.

*Call graph*: calls 2 internal fn (_connected, _leased); called by 5 (dial, exec, read, stop_commands, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 864–869)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: Forgets a cached lease after a provider call fails. The sandbox may still exist, but this process no longer trusts its local expiry record.

**Data flow**: It receives a conversation id and a short label saying what was happening. It removes the conversation’s lease from the in-memory map and logs the drop.

**Call relations**: `_prepare_strictly`, `exec`, `write`, and `read` call this when an error means the next operation should reconnect instead of relying on the old cached sandbox object.

*Call graph*: called by 4 (_prepare_strictly, exec, read, write); 1 external calls (log).


##### `sandbox_templates`  (lines 872–889)

```
def sandbox_templates(value: str) -> dict[str, str]
```

**Purpose**: Parses the `E2B_TEMPLATES` environment variable into a mapping from UFO sandbox size to E2B template reference. It also validates that every supported size is present and no unexpected size is listed.

**Data flow**: It receives a comma-separated string such as `small=...,medium=...,large=...`. It splits each item into size and template, builds a dictionary, checks that the keys exactly match the supported sandbox sizes, and returns the dictionary. Bad formatting or missing sizes raise runtime errors.

**Call relations**: `build_e2b_carrier` calls this during carrier construction after reading the environment variable.

*Call graph*: called by 1 (build_e2b_carrier).


##### `build_e2b_carrier`  (lines 892–899)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: Constructs an `E2BCarrier` from process environment variables. This is the factory used when a deployment selects the E2B sandbox backend.

**Data flow**: It reads `E2B_API_KEY` and `E2B_TEMPLATES` from the environment. If either is missing, it raises a clear configuration error. Otherwise it parses the templates and returns a new `E2BCarrier` with the API key and template map.

**Call relations**: `manifest` registers this function as the carrier factory, so the wider UFO system calls it when loading the E2B extension.

*Call graph*: calls 1 internal fn (sandbox_templates); 1 external calls (__init__).


##### `manifest`  (lines 902–914)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to UFO’s plugin system. It says that this file provides a carrier named `e2b` and tells UFO how to build it.

**Data flow**: It creates a `Manifest` containing the carrier name, version, factory function, off-cluster flag, and supported sizes, then returns it.

**Call relations**: This is the registration point for the extension. The core system reads the manifest, then later calls `build_e2b_carrier` when it needs the E2B carrier.

*Call graph*: 2 external calls (__init__, __init__).


### Command and change helpers
These files support durable command execution, persistent REPL sessions, file-change limits, and saved summaries of workspace modifications.

### `core/src/ufo/tools/file_changes.py`

`config` · `cross-cutting`

This small file exists to give the rest of the system one clear rule about how long a recorded file path is allowed to be. The constant `FILE_CHANGE_PATH_MAX_CHARS` is set to 4,096 characters. That means any code concerned with file changes can use the same limit instead of each part of the project inventing its own number.

In plain terms, it is like putting a maximum-size label on a filing cabinet drawer: everyone knows how long a file name or path can be before it is considered too large. This matters because file paths can come from outside the program, from users, tools, or the operating system. Having a shared limit helps avoid unexpected memory use, oversized database fields, or inconsistent validation behavior.

There are no functions or classes here. The file is purely a shared setting for other code to import and follow.


### `core/src/ufo/tools/tasks.py`

`orchestration` · `tool execution and timeout handling`

This file solves a practical problem: a tool may ask the sandbox to run a command, but the command might outlive the tool call, the process waiting for it, or even a crash and retry. Without this file, long commands could be killed too early, repeated by accident, or leave the caller with no reliable way to check what happened.

It treats every command like a named job with a small paper trail in the workspace: a log file for output, a pid file for the wrapper process, and an exit file for the final exit code. Think of it like leaving a numbered claim ticket at a repair shop. If you come back later with the same ticket, you get the same job status instead of starting a second repair.

The main flow is: choose a task id, create file paths for that task, ask the sandbox to launch a shell wrapper, and wait only for the allowed foreground time. If the command finishes in time, the caller receives the command output and exit code as usual. If it is still running when the wait expires, the code checks whether the background wrapper is alive and returns instructions for reading the log, watching for completion, or stopping the task. If the sandbox timed out but no task can be found, it records diagnostic information such as load, memory, and disk use, because that points to a sandbox execution problem rather than simply slow work.

#### Function details

##### `run_task`  (lines 117–147)

```
async def run_task(ctx: ToolContext, command: str, timeout_ms: int | None) -> TaskRun
```

**Purpose**: Runs one shell command through the detached task journal and waits only as long as the caller allowed. It returns a TaskRun that says what happened, including whether the command is still alive in the background.

**Data flow**: It receives a tool context, a command string, and an optional timeout in milliseconds. It turns the timeout into seconds, caps it at the maximum allowed value, derives a stable task id, and asks the sandbox to launch and wait through the shell journal scripts. If the command finishes before the wait expires, it returns the sandbox result with no background pid. If the wait expires, it probes the task files to see whether the command is still running or has completed, records extra timeout diagnostics if nothing is alive, and returns the task id, result, requested timeout, and any pid it found.

**Call relations**: This is the main entry point in the file for other tool code. It calls task_id to decide what this run should be called, task_base to pass the task's workspace path into the shell scripts, and _record_exec_timeout only when a sandbox timeout looks suspicious because no detached task can be found.

*Call graph*: calls 3 internal fn (_record_exec_timeout, task_base, task_id); 1 external calls (__init__).


##### `task_id`  (lines 150–157)

```
def task_id(ctx: ToolContext) -> str
```

**Purpose**: Chooses the short name used for a task's journal files. When the tool context has an idempotency key, meaning a retry should represent the same attempted action, it produces the same task id again so the command is not relaunched.

**Data flow**: It reads the idempotency key from the tool context. If there is no key, it makes a fresh random id. If there is a key, it hashes that key and takes a short prefix, producing a stable id that hides the original key but repeats for the same retry.

**Call relations**: run_task calls this before launching anything. That choice controls whether a later retry reconnects to an existing task's files or starts a completely new task.

*Call graph*: called by 1 (run_task); 2 external calls (sha256, uuid4).


##### `task_base`  (lines 160–164)

```
def task_base(task: str) -> str
```

**Purpose**: Builds the common absolute file path prefix for a task's journal files. It gives all shell scripts and user-facing handles one shared place to find the log, pid, and exit files.

**Data flow**: It receives a task id string. It combines the workspace directory, the hidden tasks directory, and the task id into a path like a base filename; callers then add .log, .pid, or .exit as needed.

**Call relations**: run_task uses this path when telling the sandbox where to write the journal files. task_handles uses the same path when telling a caller how to inspect or stop a detached task, so the launch side and the user-facing instructions stay in sync.

*Call graph*: called by 2 (run_task, task_handles).


##### `task_handles`  (lines 167–187)

```
def task_handles(task: str, pid: str, applied_s: int | None=None, note: str='') -> str
```

**Purpose**: Creates the human-readable message and machine-readable JSON handles for a command that is running detached. A caller uses this text to know where to read output, how to watch for completion, and how to stop the task.

**Data flow**: It receives the task id, the wrapper process id, an optional number of seconds that expired, and an optional note from the calling tool. It chooses the right lead sentence depending on whether the task was detached from the start or moved to the background after a timeout. It then builds paths and commands for reading the log, watching the exit file, and stopping the wrapper, and returns one combined text block with a JSON payload.

**Call relations**: This function is used after a task is known to be detached, so the rest of the system can give consistent instructions no matter which tool started the command. It calls task_base to ensure the advertised paths match the actual journal location, then uses JSON formatting so the handles are easy for another program or agent to parse.

*Call graph*: calls 1 internal fn (task_base); 1 external calls (dumps).


##### `timeout_notice`  (lines 190–206)

```
def timeout_notice(applied_s: int, requested_s: int | None) -> str
```

**Purpose**: Writes a clear explanation for a command timeout. It tells the caller whether the timeout was the default, the requested value, or a capped maximum.

**Data flow**: It receives the number of seconds that actually applied and the number of seconds the caller originally requested, if any. It compares those values and returns a sentence explaining why the sandbox stopped waiting and, when relevant, that a larger requested timeout was reduced to the configured maximum.

**Call relations**: This helper is meant for user-facing timeout reporting around task execution. It does not launch or inspect tasks itself; it turns timeout numbers into wording that prevents confusion between a caller's requested deadline and the sandbox's enforced cap.


##### `_record_exec_timeout`  (lines 209–242)

```
async def _record_exec_timeout(ctx: ToolContext, command: str, applied_s: int, requested_s: int | None) -> None
```

**Purpose**: Records diagnostic information when a sandbox command times out in a way that does not look like a healthy detached task continuing in the background. This helps distinguish 'the work is still running' from 'the sandbox command channel stopped responding.'

**Data flow**: It receives the tool context, the command, the applied timeout, and the requested timeout. It briefly asks the sandbox for basic system information: load, memory, and workspace disk usage. Whether that probe succeeds or fails, it writes an observation log entry with the profile, timeout details, a shortened copy of the command, whether vitals were reachable, and the vitals text if available. It intentionally does not change the command result returned to the caller.

**Call relations**: run_task calls this only after the foreground wait expired and a probe could not find a live or completed detached task. Inside, it uses a short asyncio timeout so the diagnostic probe cannot hang for long, and it sends the final record through the project's observability logging functions.

*Call graph*: called by 1 (run_task); 3 external calls (timeout, log, turn_profile).


### `core/src/ufo/workspace_changes.py`

`domain_logic` · `turn-end refresh and later workspace-change reads`

This file solves a subtle bookkeeping problem: a shared workspace can be changed by many things, but the product needs one clear, stored answer about the files that are currently changed in that workspace. Rather than trying to remember every tool call or every message, it asks the sandbox's filesystem service for a git-style scan of changed files. Think of it like taking a snapshot of a messy desk at the end of a work session, then filing that snapshot so others can inspect it later.

The file defines small data shapes for that snapshot. A single WorkspaceChange records a path, a patch text showing the change, and whether the patch had to be shortened. WorkspaceChanges wraps a whole scan and records whether the full list was shortened. Limits are enforced so huge paths, huge patches, or too many changed files do not overwhelm storage or the portal.

WorkspaceChangeRecorder is used after a turn ends. It asks the sandbox for its current changes, validates that the answer has the expected shape, and stores it in the database. If scanning fails, it logs the failure but does not break the already-finished turn; the previous saved scan remains in place. The helper recorded_workspace_changes reads the latest saved scan, resolving subagents back to the parent conversation that owns the shared workspace.

#### Function details

##### `WorkspaceChangeRecorder.record`  (lines 67–76)

```
async def record(self) -> None
```

**Purpose**: This is the safe top-level action for refreshing the saved workspace-change snapshot. It tries to scan the sandbox and store the result, but if anything goes wrong it logs the problem instead of failing the completed turn.

**Data flow**: It starts with the recorder's sandbox, workspace id, and conversation id. It asks _scan for the current changed-file snapshot, passes that snapshot to _store, and produces no returned value. If scanning or storing raises an error, it turns that failure into a log entry containing the conversation id and error details, leaving any older database record unchanged.

**Call relations**: This method is the public entry point for the recorder's work. It calls _scan first to get the sandbox's answer, then _store to save that answer. When either step fails, it hands the failure details to the logging system so the issue is visible without interrupting the larger turn flow.

*Call graph*: calls 2 internal fn (_scan, _store); 1 external calls (log).


##### `WorkspaceChangeRecorder._scan`  (lines 78–83)

```
async def _scan(self) -> WorkspaceChanges
```

**Purpose**: This asks the sandbox's filesystem service for the current list of changed files and checks that the reply matches the expected format. It protects the rest of the system from trusting a malformed sandbox response.

**Data flow**: It sends a simple "changes" request to the sandbox. The sandbox returns raw data, which this function validates as a WorkspaceChanges object. If the data fits, the validated snapshot comes out; if it does not, the function raises a clearer runtime error saying the scan was malformed.

**Call relations**: It is called by WorkspaceChangeRecorder.record during the refresh. Its output is meant to go straight into _store, so validation here ensures the database only receives a well-shaped change snapshot.

*Call graph*: called by 1 (record).


##### `WorkspaceChangeRecorder._store`  (lines 85–98)

```
async def _store(self, scanned: WorkspaceChanges) -> None
```

**Purpose**: This saves the latest validated workspace-change snapshot in the database. If a snapshot already exists for the same workspace and conversation, it replaces it with the new one.

**Data flow**: It receives a WorkspaceChanges object. It opens a workspace database transaction, converts the snapshot into plain JSON-friendly data, and writes it into the conversation_change table with the workspace id and conversation id. The result is a database row that is either newly inserted or updated in place; the function returns no value.

**Call relations**: It is called by WorkspaceChangeRecorder.record after _scan succeeds. It relies on workspace_tx to get a database connection and chooses the correct insert style for PostgreSQL or SQLite, so the same logical save works in different database backends.

*Call graph*: called by 1 (record); 2 external calls (model_dump, workspace_tx).


##### `recorded_workspace_changes`  (lines 101–125)

```
async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: This reads the most recently saved workspace-change snapshot for a conversation. If the conversation is a subagent sharing a parent's workspace, it reads the parent's saved snapshot instead.

**Data flow**: It receives a conversation id. It opens a database transaction, looks up the conversation that actually owns the sandbox workspace, then looks for that owner's saved change scan. If there is no conversation, no owner, or no saved scan, it returns the shared NOTHING_CHANGED value; otherwise it validates the stored scan and returns it as a WorkspaceChanges object.

**Call relations**: This is the read side that complements WorkspaceChangeRecorder._store. Other parts of the system can call it when they need to show or use the current saved workspace changes, without talking to the sandbox directly.

*Call graph*: 2 external calls (select, workspace_tx).


### `extensions/repl/ufo_ext_repl/manifest.py`

`domain_logic` · `extension load and tool request handling`

This file is the extension package for a small, stateful coding workspace. A REPL, short for “read-eval-print loop,” is like a scratchpad where each successful note stays on the page for the next note. Here there are two scratchpads: `js_repl` for Node.js, often useful for browser automation and image output, and `xlsx_repl` for Python spreadsheet work with openpyxl.

The important promise is safe persistence. Before each run, the tool combines the previously successful code with the new code. If the new run exits successfully, that combined code becomes the saved state. If it fails or times out, the saved state is left untouched. Without this, one bad experiment could poison every later call.

The JavaScript tool also prepares a small `emitImage` helper so code can return images inline. It writes each call’s images to a separate file, which matters because timed-out JavaScript may keep running in the background. The Python spreadsheet tool appends a footer that prints a JSON version of a variable named `result`, if the user set one.

Both tools run inside the project sandbox, so file access and network access follow the sandbox’s rules. The file also registers these tools and related data-analysis skills in the extension manifest.

#### Function details

##### `_meter_run`  (lines 74–90)

```
def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None
```

**Purpose**: Records one monitoring count for a REPL run, including which tool ran and how the process ended. This helps operators tell the difference between user code failing and the interpreter itself being missing or broken.

**Data flow**: It receives the tool context, the tool name, and the process exit code. It turns the current subagent profile into a metric label, folds unusual exit codes into a general “other” bucket, and sends the count to the observability system. It does not return anything; its effect is the emitted metric.

**Call relations**: `js_repl` and `xlsx_repl` call this after the sandbox task finishes. It hands the final reporting work to the external metric helpers, so the main REPL flow can continue producing the tool result.

*Call graph*: called by 2 (js_repl, xlsx_repl); 2 external calls (emit_metric, turn_profile).


##### `global_modules_link`  (lines 93–111)

```
def global_modules_link(roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds the shell command that makes globally installed Node.js packages importable from the JavaScript REPL. This is needed because ES modules do not automatically look in the usual global package paths.

**Data flow**: It takes a tuple of possible global package roots, or uses the defaults. It returns a shell script string that creates `.repl/node_modules`, removes an outdated symlink if needed, and symlinks each package from the global roots into that directory. It only returns text; the command is run later.

**Call relations**: `js_repl` calls this before starting Node.js. The returned command is executed in the sandbox so packages such as Playwright can be imported by normal package name inside the user’s JavaScript code.

*Call graph*: called by 1 (js_repl).


##### `js_emit_relative`  (lines 118–126)

```
def js_emit_relative(call: str) -> str
```

**Purpose**: Creates the workspace-relative file path where one JavaScript REPL call should write emitted images. Each call gets its own image file so background work from an older timed-out call cannot overwrite or confuse a later call’s images.

**Data flow**: It receives a short call identifier. It inserts that identifier into a `.repl/js-emit-...jsonl` filename and returns the relative path as text. It does not touch the filesystem itself.

**Call relations**: `js_repl` calls this when preparing a JavaScript run. The result is passed into `js_emit_prelude`, and later `js_repl` uses the matching workspace path when reading images back through `_emitted_images`.

*Call graph*: called by 1 (js_repl).


##### `js_emit_prelude`  (lines 129–166)

```
def js_emit_prelude(emit_relative: str) -> str
```

**Purpose**: Generates the JavaScript setup code that defines a global `emitImage` function for user code. This lets JavaScript return screenshots or other image data as part of the tool result instead of only printing text.

**Data flow**: It receives the image output path for this call. It returns JavaScript source code that imports file-writing helpers, defines size and count limits, accepts image bytes or base64 text, and writes recent image entries as JSON lines to the output file. It uses JSON quoting so the path is safely embedded in JavaScript.

**Call relations**: `js_repl` calls this while building the temporary JavaScript run file. The user’s code runs after this prelude, and `_emitted_images` later reads the file that this generated `emitImage` function wrote.

*Call graph*: called by 1 (js_repl); 1 external calls (dumps).


##### `_candidate_source`  (lines 236–242)

```
async def _candidate_source(ctx: ToolContext, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should be tried for the next REPL run. It is the gatekeeper for the “only successful code becomes state” rule.

**Data flow**: It receives the sandbox context, the saved-state file path, the new code, and whether to reset. If reset is true, it deletes the saved state. If there is no saved state, it returns just the new code plus a newline. Otherwise, it reads the saved code and appends the new code. The returned text is a candidate state, not yet committed.

**Call relations**: Both `js_repl` and `xlsx_repl` call this before writing their temporary run files. They only save the candidate back to the persistent state file after the interpreter exits successfully.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (quote).


##### `_repl_result`  (lines 245–257)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Turns a completed interpreter run into the standard tool result returned to the caller. It includes stdout, stderr, and the exit code, and it clearly warns when failed code was not saved.

**Data flow**: It receives captured stdout, captured stderr, an exit code, and optionally images. It builds a JSON text payload. If the exit code is nonzero, it adds a notice that the REPL state did not advance and marks the tool result as an error. It returns a `ToolResult` containing the text and any images.

**Call relations**: `js_repl` and `xlsx_repl` call this after a run finishes without a foreground timeout. For JavaScript, `js_repl` may first collect images with `_emitted_images` and then pass them into this function.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_expired_result`  (lines 260–270)

```
def _expired_result(run: TaskRun, applied_s: int) -> ToolResult
```

**Purpose**: Builds the tool response for a run that exceeded the caller’s waiting budget. It explains that the saved REPL state did not change and, when possible, gives handles for checking the still-running background task.

**Data flow**: It receives the task run record and the timeout that was actually applied. If there is no process id, it returns an error message saying the wait expired and state is unchanged. If the process is still known, it returns task id, log path, pid, and the same state warning, so the caller can follow up later.

**Call relations**: `js_repl` and `xlsx_repl` call this when `run_task` reports a timeout. It uses the shared timeout and task-handle helpers so REPL timeouts behave like other long-running sandbox commands.

*Call graph*: called by 2 (js_repl, xlsx_repl); 4 external calls (__init__, __init__, task_handles, timeout_notice).


##### `_emitted_images`  (lines 278–290)

```
async def _emitted_images(ctx: ToolContext, emit_path: str) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript code sent through `emitImage` and converts them into tool-result image objects. It also cleans up the per-call image file afterward.

**Data flow**: It receives the sandbox context and the full image file path. If the file does not exist, it returns no images. If it exists, it reads the JSON-lines content, deletes the file, validates up to the most recent allowed entries, skips malformed lines, and returns a tuple of image content objects.

**Call relations**: `js_repl` calls this after a JavaScript run finishes. It completes the image path started by `js_emit_relative` and `js_emit_prelude`: the prelude writes images, and this function folds them into the final `_repl_result`.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, quote).


##### `js_repl`  (lines 293–313)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs one JavaScript REPL call in the sandbox while preserving successful state across calls. It supports Node.js ES modules, global package imports, timeouts that continue in the background, and inline image output.

**Data flow**: It receives the tool context and validated JavaScript input. It builds candidate source from saved state plus new code, creates a unique image output path, writes a temporary `.mjs` run file with the image prelude, links global Node packages, and starts Node through the shared task runner. After the run, it records a metric. If the run timed out, it returns timeout information and does not save state. If it exited successfully, it commits the candidate source as the new REPL state. Finally, it returns stdout, stderr, exit code, and any emitted images.

**Call relations**: This is the handler registered for the `js_repl` tool in `manifest`. It orchestrates the helper functions in order: `_candidate_source` prepares the code, `js_emit_relative` and `js_emit_prelude` prepare image support, `global_modules_link` prepares imports, `_meter_run` records the outcome, `_expired_result` covers timeouts, `_emitted_images` collects image output, and `_repl_result` formats the completed response.

*Call graph*: calls 8 internal fn (_candidate_source, _emitted_images, _expired_result, _meter_run, _repl_result, global_modules_link, js_emit_prelude, js_emit_relative); 3 external calls (quote, run_task, uuid4).


##### `xlsx_repl`  (lines 316–325)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs one Python REPL call for spreadsheet work in the sandbox while preserving successful state across calls. It is designed so users can set a variable named `result` and get its JSON form back in stdout.

**Data flow**: It receives the tool context and validated Python input. It builds candidate source from saved state plus new code, writes a temporary Python run file with a small footer that prints `result` if it exists, and starts Python through the shared task runner. It records a metric, returns a special timeout response if the wait expired, commits the candidate source only when the process exits successfully, and then returns stdout, stderr, and exit code.

**Call relations**: This is the handler registered for the `xlsx_repl` tool in `manifest`. It uses the same state and result helpers as `js_repl`: `_candidate_source` prepares the candidate program, `_meter_run` reports the run, `_expired_result` explains timeouts, and `_repl_result` formats normal completion.

*Call graph*: calls 4 internal fn (_candidate_source, _expired_result, _meter_run, _repl_result); 2 external calls (quote, run_task).


##### `manifest`  (lines 328–348)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the larger system: its name, version, tools, skills, and sandbox internet setting. Without this, the REPL tools and data skills would not be discoverable by the host application.

**Data flow**: It takes no input. It constructs a manifest containing two tool definitions, each tied to its input model and handler function, and adds skill specifications for the data-related skill folders. It returns the finished manifest object to the extension loader.

**Call relations**: The host system calls this when loading the extension. The manifest it returns points tool requests to `js_repl` and `xlsx_repl`, and it exposes the related skill packs so they can be loaded when needed.

*Call graph*: 3 external calls (__init__, __init__, __init__).
